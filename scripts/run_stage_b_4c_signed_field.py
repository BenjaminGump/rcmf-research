from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import time
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.training.addressing_4b import (
    bootstrap_metric_ci,
    distribution,
    evaluate_scores,
    mean_std,
    per_state_metric_values,
    rows_by_split,
)
from rcmf.training.addressing_only import (
    baseline_frozen_qwen_cosine,
    baseline_random,
    rows_to_tensors,
)
from rcmf.training.signed_residual_field import (
    ReferenceSignedTwoTower,
    SIGNED_FIELD_VERSION,
    SignedResidualField,
    StateOnlyResidualHeadWithGate,
    build_fold_rows,
    copy_reference_weights_to_core,
    deterministic_task_folds,
    field_algebra_validation,
    residual_stats,
    summarize_runs,
    train_memory_prior,
    train_prior_head,
    train_signed_model,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
)


RUN_VERSION = "stage_b_signed_residual_field_4c_v1"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _load_representation_cache(
    path: Path,
    *,
    expected_count: int,
    expected_source_path: Path | None,
    model_name: str,
    accepted_formats: set[str],
) -> tuple[torch.Tensor, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu")
    fmt = str(payload.get("format"))
    if fmt not in accepted_formats:
        raise ValueError(f"Unsupported representation cache format {fmt} at {path}")
    representations = payload.get("representations")
    if not isinstance(representations, torch.Tensor) or representations.dim() != 2:
        raise ValueError(f"Representation cache missing 2D representations tensor: {path}")
    if representations.shape[0] != expected_count:
        raise ValueError(f"Representation count {representations.shape[0]} != {expected_count}: {path}")
    if payload.get("model_name") != model_name:
        raise ValueError(f"Representation model mismatch: {payload.get('model_name')} != {model_name}")
    if expected_source_path is not None and expected_source_path.exists():
        expected_hash = sha256_file(expected_source_path)
        if payload.get("source_sha256") != expected_hash:
            raise ValueError(f"Representation source hash mismatch for {path}")
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"representations", "chunk_representations", "owner_indices", "chunk_token_counts"}
    }
    metadata["path"] = str(path)
    metadata["shape"] = list(representations.shape)
    return representations.to(torch.float32), metadata


def _metric_mean(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, dict) and value.get("mean") is not None:
        return float(value["mean"])
    return 0.0


def _summary_mean(summary: dict[str, Any], key: str) -> float:
    value = summary.get(key)
    if isinstance(value, dict) and value.get("mean") is not None:
        return float(value["mean"])
    return 0.0


def _global_prior_eval(mu: torch.Tensor, rows: list[dict[str, Any]], *, device: torch.device) -> dict[str, Any]:
    labels = rows_to_tensors(rows, device=device)
    scores = mu.to(device=device, dtype=torch.float32).unsqueeze(0).repeat(len(rows), 1)
    residual = torch.zeros_like(scores)
    return {
        "full_score": evaluate_scores(scores, labels),
        "residual_only": evaluate_scores(residual, labels),
        "residual_stats": residual_stats(residual, mu.to(device), labels),
        "per_state_full_score": per_state_metric_values(scores, labels),
        "per_state_residual_only": per_state_metric_values(residual, labels),
    }


def _baseline_suite(
    *,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    mu: torch.Tensor,
    device: torch.device,
    seed: int,
) -> dict[str, Any]:
    train_labels = rows_to_tensors(train_rows, device=device)
    validation_labels = rows_to_tensors(validation_rows, device=device)
    validation_state = state_representations[
        torch.tensor([int(row["state_index"]) for row in validation_rows], dtype=torch.long)
    ].to(device=device, dtype=torch.float32)
    memory = memory_representations.to(device=device, dtype=torch.float32)
    with torch.no_grad():
        global_scores = mu.to(device=device, dtype=torch.float32).unsqueeze(0).repeat(len(validation_rows), 1)
        cosine_scores = baseline_frozen_qwen_cosine(validation_state, memory)
        random_scores = baseline_random(tuple(global_scores.shape), seed=seed, device=device)
    return {
        "global_memory_prior": _global_prior_eval(mu, validation_rows, device=device),
        "frozen_qwen_hidden_cosine": {
            "full_score": evaluate_scores(cosine_scores, validation_labels),
            "per_state_full_score": per_state_metric_values(cosine_scores, validation_labels),
        },
        "deterministic_random": {
            "seed": seed,
            "full_score": evaluate_scores(random_scores, validation_labels),
            "per_state_full_score": per_state_metric_values(random_scores, validation_labels),
        },
        "train_label_counts": _label_counts(train_labels),
        "validation_label_counts": _label_counts(validation_labels),
    }


def _label_counts(labels: dict[str, torch.Tensor]) -> dict[str, int]:
    valid = labels["valid_mask"]
    utility = labels["utility"]
    return {
        "states": int(valid.shape[0]),
        "memories": int(valid.shape[1]),
        "valid_pairs": int(valid.sum().item()),
        "missing_pairs": int((~valid).sum().item()),
        "positive_pairs": int((valid & (utility > 0.01)).sum().item()),
        "neutral_pairs": int((valid & (utility.abs() <= 0.01)).sum().item()),
        "negative_pairs": int((valid & (utility < -0.01)).sum().item()),
        "positive_gain_states": int((labels["positive_gain"].sum(dim=1) > 0).sum().item()),
        "no_positive_states": int((labels["no_positive_state"] & ~labels["all_missing_state"]).sum().item()),
        "all_missing_states": int(labels["all_missing_state"].sum().item()),
    }


def _reference_reproduction(state_dim: int, memory_dim: int, *, device: torch.device) -> dict[str, Any]:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(20260806)
    state = torch.randn(7, state_dim, generator=generator, dtype=torch.float32).to(device)
    memory = torch.randn(5, memory_dim, generator=generator, dtype=torch.float32).to(device)
    torch.manual_seed(20260806)
    reference = ReferenceSignedTwoTower(state_dim, memory_dim, tower_dim=128, hidden_dim=256, dropout=0.0).to(device).eval()
    core = SignedResidualField(state_dim, memory_dim, rank=128, hidden_dim=256, dropout=0.0).to(device).eval()
    copy_reference_weights_to_core(reference, core)
    with torch.no_grad():
        ref = reference(state, memory)
        copied = core(state, memory)
    residual_error = float((ref["residual"] - copied["residual"]).abs().max().detach().cpu().item())
    gate_error = float((ref["gate"] - copied["gate"]).abs().max().detach().cpu().item())
    q_error = float((ref["q"] - copied["q"]).abs().max().detach().cpu().item())
    k_error = float((ref["k"] - copied["k"]).abs().max().detach().cpu().item())
    return {
        "format": "signed_reference_core_equivalence_v1",
        "residual_max_abs_error": residual_error,
        "gate_max_abs_error": gate_error,
        "q_max_abs_error": q_error,
        "k_max_abs_error": k_error,
        "passed": residual_error <= 1.0e-7 and gate_error <= 1.0e-7 and q_error <= 1.0e-7 and k_error <= 1.0e-7,
    }


def _make_model(variant: str, state_dim: int, memory_dim: int, memory_count: int) -> torch.nn.Module:
    if variant == "state_only_residual_upper_bound":
        return StateOnlyResidualHeadWithGate(state_dim, memory_count, hidden_dim=256, dropout=0.05)
    if variant == "signed_two_tower_reference_r128":
        return ReferenceSignedTwoTower(state_dim, memory_dim, tower_dim=128, hidden_dim=256, dropout=0.05)
    if variant == "signed_core_field_r128":
        return SignedResidualField(state_dim, memory_dim, rank=128, hidden_dim=256, dropout=0.05)
    if variant == "signed_core_field_r64":
        return SignedResidualField(state_dim, memory_dim, rank=64, hidden_dim=256, dropout=0.05)
    if variant == "normalized_signed_core_field_r128":
        return SignedResidualField(
            state_dim,
            memory_dim,
            rank=128,
            hidden_dim=256,
            dropout=0.05,
            normalize_qk=True,
            learned_temperature=True,
        )
    raise ValueError(f"Unknown variant: {variant}")


def _save_checkpoint(
    checkpoint_dir: Path,
    *,
    variant: str,
    seed: int,
    run: dict[str, Any],
    prior_kind: str,
) -> str:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state_dict = run.pop("state_dict")
    path = checkpoint_dir / f"{variant}_{prior_kind}_seed_{seed}.pt"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    torch.save(
        {
            "format": RUN_VERSION,
            "model_kind": variant,
            "prior_kind": prior_kind,
            "seed": seed,
            "best_epoch": run.get("best_epoch"),
            "epochs_ran": run.get("epochs_ran"),
            "state_dict": state_dict,
            "source_commit": maybe_git_commit(),
        },
        tmp_path,
    )
    tmp_path.replace(path)
    run["checkpoint"] = str(path)
    return str(path)


def _train_variant_runs(
    *,
    variant: str,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    mu: torch.Tensor,
    seeds: list[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    prior_kind: str,
) -> dict[str, Any]:
    runs = []
    checkpoints = []
    state_dim = int(state_representations.shape[1])
    memory_dim = int(memory_representations.shape[1])
    memory_count = int(memory_representations.shape[0])
    for seed in seeds:
        print(f"training {variant} prior={prior_kind} seed={seed}", flush=True)
        torch.manual_seed(seed)
        random.seed(seed)
        model = _make_model(variant, state_dim, memory_dim, memory_count)
        run = train_signed_model(
            model=model,
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            seed=seed,
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
        )
        checkpoints.append(
            _save_checkpoint(
                output_dir / "checkpoints",
                variant=variant,
                seed=seed,
                run=run,
                prior_kind=prior_kind,
            )
        )
        runs.append(run)
    summary = summarize_runs(runs)
    return {"variant": variant, "prior_kind": prior_kind, "runs": runs, "summary": summary, "checkpoints": checkpoints}


def _attach_bootstrap_vs_global(payload: dict[str, Any], global_eval: dict[str, Any]) -> None:
    by_seed = {}
    lower_bounds = []
    for index, run in enumerate(payload["runs"]):
        rows = {
            "correct": run["validation"]["per_state_full_score"]["rows"],
            "shuffled": run["controls"]["shuffled_state"]["per_state_full_score"]["rows"],
            "global": global_eval["per_state_full_score"]["rows"],
        }
        ci = bootstrap_metric_ci(rows)
        by_seed[f"seed_{index}"] = ci
        bound = (
            ci.get("ndcg@4", {})
            .get("correct_minus_global_bootstrap_ci95", {})
            .get("lo")
        )
        if bound is not None:
            lower_bounds.append(float(bound))
    payload["summary"]["bootstrap_ci_vs_global"] = by_seed
    payload["summary"]["ndcg@4_minus_global_ci95_lower_min"] = min(lower_bounds) if lower_bounds else None


def _run_learned_prior_ablation(
    *,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    empirical_mu: torch.Tensor,
    seeds: list[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    prior_epochs: int,
) -> dict[str, Any]:
    runs = []
    prior_reports = []
    checkpoints = []
    for seed in seeds:
        print(f"training learned memory prior seed={seed}", flush=True)
        prior_head, prior_report = train_prior_head(
            memory_representations,
            empirical_mu,
            seed=seed,
            epochs=prior_epochs,
            device=device,
        )
        with torch.no_grad():
            mu_hat = prior_head(memory_representations.to(device=device, dtype=torch.float32)).detach().cpu()
        prior_path = output_dir / "checkpoints" / f"memory_prior_head_seed_{seed}.pt"
        prior_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "format": RUN_VERSION,
                "model_kind": "memory_prior_head",
                "seed": seed,
                "state_dict": prior_head.state_dict(),
                "empirical_mu": empirical_mu,
                "mu_hat": mu_hat,
                "report": prior_report,
                "source_commit": maybe_git_commit(),
            },
            prior_path,
        )
        prior_report["checkpoint"] = str(prior_path)
        prior_reports.append(prior_report)
        torch.manual_seed(seed)
        random.seed(seed)
        model = SignedResidualField(
            int(state_representations.shape[1]),
            int(memory_representations.shape[1]),
            rank=128,
            hidden_dim=256,
            dropout=0.05,
        )
        run = train_signed_model(
            model=model,
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu_hat,
            seed=seed,
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
        )
        checkpoints.append(
            _save_checkpoint(
                output_dir / "checkpoints",
                variant="signed_core_field_r128",
                seed=seed,
                run=run,
                prior_kind="learned_memory_prior",
            )
        )
        runs.append(run)
    return {
        "variant": "signed_core_field_r128",
        "prior_kind": "learned_memory_prior",
        "prior_reports": prior_reports,
        "runs": runs,
        "summary": summarize_runs(runs),
        "checkpoints": checkpoints,
    }


def _run_continuity(
    *,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    seeds: list[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    prior_epochs: int,
) -> dict[str, Any]:
    mu = train_memory_prior(train_rows, memory_count=int(memory_representations.shape[0]))
    baselines = _baseline_suite(
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_representations,
        memory_representations=memory_representations,
        mu=mu,
        device=device,
        seed=seeds[0],
    )
    variants = [
        "state_only_residual_upper_bound",
        "signed_two_tower_reference_r128",
        "signed_core_field_r128",
        "signed_core_field_r64",
        "normalized_signed_core_field_r128",
    ]
    trained = {}
    for variant in variants:
        trained[variant] = _train_variant_runs(
            variant=variant,
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            seeds=seeds,
            output_dir=output_dir,
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
            prior_kind="empirical_train_mu",
        )
        _attach_bootstrap_vs_global(trained[variant], baselines["global_memory_prior"])
        atomic_write_json(output_dir / f"{variant}_continuity_partial.json", trained[variant])

    learned_prior = _run_learned_prior_ablation(
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_representations,
        memory_representations=memory_representations,
        empirical_mu=mu,
        seeds=seeds,
        output_dir=output_dir,
        device=device,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        weight_decay=weight_decay,
        patience=patience,
        prior_epochs=prior_epochs,
    )
    _attach_bootstrap_vs_global(learned_prior, baselines["global_memory_prior"])
    trained["signed_core_field_r128_learned_prior"] = learned_prior
    return {
        "format": "stage_b_signed_field_continuity_v1",
        "mu_distribution": distribution(mu.tolist()),
        "baselines": baselines,
        "variants": trained,
    }


def _run_cross_validation(
    *,
    rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    memory_representations: torch.Tensor,
    state_representations: torch.Tensor,
    seeds: list[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    fold_seed: int,
) -> dict[str, Any]:
    tasks = sorted({str(row["task_id"]) for row in rows})
    folds = deterministic_task_folds(tasks, folds=5, seed=fold_seed)
    fold_outputs = []
    for fold in folds:
        fold_index = int(fold["fold"])
        print(f"running CV fold {fold_index}", flush=True)
        payload = build_fold_rows(rows, memory_bank, fold)
        if not payload["validation"]["passed"]:
            raise ValueError(f"Fold leakage validation failed: {payload['validation']}")
        fold_memory_reps = memory_representations[payload["stage_indices"]]
        mu = train_memory_prior(payload["train_rows"], memory_count=int(fold_memory_reps.shape[0]))
        baselines = _baseline_suite(
            train_rows=payload["train_rows"],
            validation_rows=payload["validation_rows"],
            state_representations=state_representations,
            memory_representations=fold_memory_reps,
            mu=mu,
            device=device,
            seed=seeds[0] + fold_index,
        )
        fold_dir = output_dir / f"fold_{fold_index}"
        core = _train_variant_runs(
            variant="signed_core_field_r128",
            train_rows=payload["train_rows"],
            validation_rows=payload["validation_rows"],
            state_representations=state_representations,
            memory_representations=fold_memory_reps,
            mu=mu,
            seeds=seeds,
            output_dir=fold_dir,
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
            prior_kind="fold_empirical_train_mu",
        )
        _attach_bootstrap_vs_global(core, baselines["global_memory_prior"])
        reference = _train_variant_runs(
            variant="signed_two_tower_reference_r128",
            train_rows=payload["train_rows"],
            validation_rows=payload["validation_rows"],
            state_representations=state_representations,
            memory_representations=fold_memory_reps,
            mu=mu,
            seeds=seeds,
            output_dir=fold_dir,
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
            prior_kind="fold_empirical_train_mu",
        )
        _attach_bootstrap_vs_global(reference, baselines["global_memory_prior"])
        fold_output = {
            "fold": fold,
            "validation": payload["validation"],
            "stage_indices": payload["stage_indices"],
            "memory_count": int(fold_memory_reps.shape[0]),
            "train_label_counts": baselines["train_label_counts"],
            "validation_label_counts": baselines["validation_label_counts"],
            "baselines": baselines,
            "signed_core_field_r128": core,
            "signed_two_tower_reference_r128": reference,
        }
        atomic_write_json(fold_dir / "summary.json", fold_output)
        fold_outputs.append(fold_output)
    return {
        "format": "stage_b_signed_field_task_grouped_cv_v1",
        "fold_seed": fold_seed,
        "folds": fold_outputs,
        "aggregate": _summarize_cv(fold_outputs),
    }


def _summarize_cv(fold_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    per_fold = []
    for fold in fold_outputs:
        global_metrics = fold["baselines"]["global_memory_prior"]["full_score"]
        core = fold["signed_core_field_r128"]["summary"]
        reference = fold["signed_two_tower_reference_r128"]["summary"]
        per_fold.append(
            {
                "fold": fold["fold"]["fold"],
                "validation_task_ids": fold["fold"]["validation_task_ids"],
                "memory_count": fold["memory_count"],
                "global_ndcg@4": _metric_mean(global_metrics, "ndcg@4"),
                "global_positive_mass@4": _metric_mean(global_metrics, "positive_mass_coverage@4"),
                "core_ndcg@4": _summary_mean(core, "ndcg@4"),
                "core_positive_mass@4": _summary_mean(core, "positive_mass_coverage@4"),
                "core_delta_ndcg@4": _summary_mean(core["correct_minus_shuffled"], "ndcg@4"),
                "core_delta_positive_mass@4": _summary_mean(core["correct_minus_shuffled"], "positive_mass_coverage@4"),
                "reference_ndcg@4": _summary_mean(reference, "ndcg@4"),
                "reference_delta_ndcg@4": _summary_mean(reference["correct_minus_shuffled"], "ndcg@4"),
            }
        )
    improvements = [row["core_ndcg@4"] - row["global_ndcg@4"] for row in per_fold]
    deltas = [row["core_delta_ndcg@4"] for row in per_fold]
    return {
        "per_fold": per_fold,
        "core_ndcg@4_improvement": mean_std(improvements),
        "core_correct_minus_shuffled_ndcg@4": mean_std(deltas),
        "positive_improvement_folds": sum(1 for value in improvements if value > 0),
        "fold_count": len(per_fold),
    }


def _load_previous_4b(previous_dir: Path | None) -> dict[str, Any]:
    if previous_dir is None:
        return {"available": False}
    path = previous_dir / "summary.json"
    if not path.exists():
        return {"available": False, "path": str(path), "reason": "missing_summary_json"}
    with path.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    aggregate = summary.get("scorer_ladder", {}).get("aggregate", {})
    return {
        "available": True,
        "path": str(path),
        "source_commit": summary.get("source_commit"),
        "aggregate": {
            key: aggregate.get(key)
            for key in (
                "global_memory_prior",
                "state_only_residual_head",
                "signed_two_tower_residual",
                "current_hard_topk_control",
                "dense_separate_head_address",
                "dense_shared_head_address",
            )
        },
    }


def _gate_decision(
    *,
    continuity: dict[str, Any],
    cross_validation: dict[str, Any],
    previous_4b: dict[str, Any],
    reference_reproduction: dict[str, Any],
) -> dict[str, Any]:
    global_metrics = continuity["baselines"]["global_memory_prior"]["full_score"]
    global_ndcg4 = _metric_mean(global_metrics, "ndcg@4")
    global_mass4 = _metric_mean(global_metrics, "positive_mass_coverage@4")
    core = continuity["variants"]["signed_core_field_r128"]["summary"]
    reference = continuity["variants"]["signed_two_tower_reference_r128"]["summary"]
    learned = continuity["variants"]["signed_core_field_r128_learned_prior"]["summary"]
    core_ndcg4 = _summary_mean(core, "ndcg@4")
    core_mass4 = _summary_mean(core, "positive_mass_coverage@4")
    reference_ndcg4 = _summary_mean(reference, "ndcg@4")
    core_delta_ndcg4 = _summary_mean(core["correct_minus_shuffled"], "ndcg@4")
    core_delta_mass4 = _summary_mean(core["correct_minus_shuffled"], "positive_mass_coverage@4")
    interaction_var = _summary_mean(core["geometry"], "interaction_variance")
    ci_lower = core.get("ndcg@4_minus_global_ci95_lower_min")
    continuity_pass = bool(
        core_ndcg4 >= global_ndcg4 + 0.05
        and core_mass4 >= global_mass4 + 0.04
        and core_delta_ndcg4 >= 0.10
        and core_delta_mass4 >= 0.04
        and interaction_var >= 0.01
        and ci_lower is not None
        and ci_lower > 0
        and core_ndcg4 >= reference_ndcg4 - 0.02
    )
    cv = cross_validation["aggregate"]
    cv_improvement = cv["core_ndcg@4_improvement"]["mean"] or 0.0
    cv_delta = cv["core_correct_minus_shuffled_ndcg@4"]["mean"] or 0.0
    cv_pass = bool(
        cv_improvement >= 0.04
        and cv_delta >= 0.08
        and cv["positive_improvement_folds"] >= 4
    )
    previous_ref_ndcg = None
    if previous_4b.get("available"):
        old = previous_4b["aggregate"].get("signed_two_tower_residual") or {}
        previous_ref_ndcg = _summary_mean(old, "ndcg@4")
    reference_reproduces_4b = (
        previous_ref_ndcg is None
        or abs(reference_ndcg4 - previous_ref_ndcg) <= 0.03
    )
    learned_ndcg4 = _summary_mean(learned, "ndcg@4")
    learned_prior_passes = bool(learned_ndcg4 >= core_ndcg4 - 0.02)
    rank64 = continuity["variants"]["signed_core_field_r64"]["summary"]
    rank64_passes = bool((_summary_mean(rank64, "ndcg@4") >= global_ndcg4 + 0.05))
    if not reference_reproduction["passed"] or not reference_reproduces_4b:
        branch = "reference_reproduction_failed"
    elif reference_ndcg4 >= global_ndcg4 + 0.05 and not continuity_pass:
        branch = "core_field_refactor_mismatch_or_gate_failed"
    elif continuity_pass and not learned_prior_passes:
        branch = "core_passed_learned_prior_failed"
    elif continuity_pass and not rank64_passes:
        branch = "core_rank128_passed_rank64_failed"
    elif continuity_pass and cv_pass:
        branch = "signed_core_field_passed_recommend_stage_c_pilot"
    else:
        branch = "signed_core_field_failed_scientific_gate"
    return {
        "format": "stage_b_signed_field_4c_gate_v1",
        "continuity_pass": continuity_pass,
        "cross_validation_pass": cv_pass,
        "reference_reproduction_pass": bool(reference_reproduction["passed"]),
        "reference_reproduces_previous_4b": reference_reproduces_4b,
        "learned_prior_passes": learned_prior_passes,
        "rank64_passes": rank64_passes,
        "branch": branch,
        "stage_c_allowed": False,
        "values": {
            "global_ndcg@4": global_ndcg4,
            "global_positive_mass@4": global_mass4,
            "reference_ndcg@4": reference_ndcg4,
            "previous_4b_signed_two_tower_ndcg@4": previous_ref_ndcg,
            "core_ndcg@4": core_ndcg4,
            "core_positive_mass@4": core_mass4,
            "core_correct_minus_shuffled_ndcg@4": core_delta_ndcg4,
            "core_correct_minus_shuffled_positive_mass@4": core_delta_mass4,
            "core_interaction_variance": interaction_var,
            "core_ndcg@4_minus_global_ci95_lower_min": ci_lower,
            "cv_mean_ndcg@4_improvement": cv_improvement,
            "cv_mean_correct_minus_shuffled_ndcg@4": cv_delta,
            "cv_positive_improvement_folds": cv["positive_improvement_folds"],
            "learned_prior_ndcg@4": learned_ndcg4,
            "rank64_ndcg@4": _summary_mean(rank64, "ndcg@4"),
        },
    }


def _format_metric(value: Any) -> str:
    if isinstance(value, dict):
        mean = value.get("mean")
        std = value.get("std")
        if mean is None:
            return "`NA`"
        return f"`{float(mean):.6f}/{float(std or 0.0):.6f}`"
    if value is None:
        return "`NA`"
    return f"`{float(value):.6f}`"


def write_report(summary: dict[str, Any]) -> str:
    continuity = summary["continuity_split"]
    variants = continuity["variants"]
    baselines = continuity["baselines"]
    lines = [
        "# Milestone 4C Signed Residual Associative Field",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- hard scope: no Stage C, no program head, no injector, no Qwen action loss, no AppWorld evaluation.",
        "",
        "## Reference Reproduction",
        "",
        f"```json\n{summary['reference_reproduction']}\n```",
        "",
        "## Continuity Split Metrics",
        "",
        "| model | NDCG@4 | PosMass@4 | MRR | Spearman | delta NDCG@4 | delta PosMass@4 | interaction var |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    global_metrics = baselines["global_memory_prior"]["full_score"]
    lines.append(
        "| global memory prior | "
        f"{_format_metric(global_metrics['ndcg@4'])} | "
        f"{_format_metric(global_metrics['positive_mass_coverage@4'])} | "
        f"{_format_metric(global_metrics['mrr'])} | "
        f"{_format_metric(global_metrics['spearman'])} | `NA` | `NA` | `0` |"
    )
    lines.append(
        "| frozen-Qwen hidden cosine | "
        f"{_format_metric(baselines['frozen_qwen_hidden_cosine']['full_score']['ndcg@4'])} | "
        f"{_format_metric(baselines['frozen_qwen_hidden_cosine']['full_score']['positive_mass_coverage@4'])} | "
        f"{_format_metric(baselines['frozen_qwen_hidden_cosine']['full_score']['mrr'])} | "
        f"{_format_metric(baselines['frozen_qwen_hidden_cosine']['full_score']['spearman'])} | `NA` | `NA` | `NA` |"
    )
    for name, payload in variants.items():
        metrics = payload["summary"]
        lines.append(
            f"| {name} | "
            f"{_format_metric(metrics['ndcg@4'])} | "
            f"{_format_metric(metrics['positive_mass_coverage@4'])} | "
            f"{_format_metric(metrics['mrr'])} | "
            f"{_format_metric(metrics['spearman'])} | "
            f"{_format_metric(metrics['correct_minus_shuffled']['ndcg@4'])} | "
            f"{_format_metric(metrics['correct_minus_shuffled']['positive_mass_coverage@4'])} | "
            f"{_format_metric(metrics['geometry'].get('interaction_variance'))} |"
        )
    lines.extend(
        [
            "",
            "## Gate Metrics",
            "",
            "| model | AUROC | AUPRC | balanced acc | false activation | pos mean | no-pos mean |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, payload in variants.items():
        gate = payload["summary"]["gate"]
        lines.append(
            f"| {name} | "
            f"{_format_metric(gate['auroc'])} | "
            f"{_format_metric(gate['auprc'])} | "
            f"{_format_metric(gate['balanced_accuracy'])} | "
            f"{_format_metric(gate['false_activation'])} | "
            f"{_format_metric(gate['positive_state_gate_mean'])} | "
            f"{_format_metric(gate['no_positive_state_gate_mean'])} |"
        )
    lines.extend(
        [
            "",
            "## Cross-Validation",
            "",
            "| fold | memory count | global NDCG@4 | core NDCG@4 | improvement | core delta NDCG@4 |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary["cross_validation"]["aggregate"]["per_fold"]:
        lines.append(
            f"| {row['fold']} | {row['memory_count']} | "
            f"`{row['global_ndcg@4']:.6f}` | `{row['core_ndcg@4']:.6f}` | "
            f"`{row['core_ndcg@4'] - row['global_ndcg@4']:.6f}` | "
            f"`{row['core_delta_ndcg@4']:.6f}` |"
        )
    gate = summary["decision_gate"]
    lines.extend(
        [
            "",
            "## Decision Gate",
            "",
            f"```json\n{gate}\n```",
            "",
            "## Field Algebra",
            "",
            f"```json\n{summary['field_algebra']}\n```",
            "",
            "## Previous 4B Comparison",
            "",
            f"```json\n{summary['previous_4b']}\n```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Milestone 4C signed residual associative field pilot.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--previous-4b-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--cv-epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--prior-epochs", type=int, default=400)
    parser.add_argument("--fold-seed", type=int, default=23)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    start = time.perf_counter()
    cfg = load_config(args.config)
    labels_dir = Path(args.labels_dir)
    data_dir = Path(args.data)
    repr_dir = Path(args.representation_cache_dir)
    teacher_dir = Path(args.teacher_cache_dir)
    previous_4b_dir = Path(args.previous_4b_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)

    rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    train_rows, validation_rows = rows_by_split(rows)
    memory_indices = [int(row["memory_index"]) for row in memory_bank]
    state_reps, state_meta = _load_representation_cache(
        repr_dir / "decision_state_representations.pt",
        expected_count=len(rows),
        expected_source_path=data_dir / "decision_examples.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"pooled_qwen_hidden_v1", "pooled_qwen_hidden_v2"},
    )
    all_memory_reps, memory_meta = _load_representation_cache(
        repr_dir / "memory_record_representations.pt",
        expected_count=46,
        expected_source_path=data_dir / "memory_records.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"chunked_qwen_hidden_v1", "record_qwen_hidden_v2"},
    )
    memory_reps = all_memory_reps[memory_indices]
    if state_reps.shape[1] != memory_reps.shape[1]:
        raise ValueError("state and memory representation dimensions differ")

    metadata = {
        "format": RUN_VERSION,
        "signed_field_version": SIGNED_FIELD_VERSION,
        "source_commit": maybe_git_commit(),
        "config": str(args.config),
        "labels_dir": str(labels_dir),
        "data_dir": str(data_dir),
        "teacher_cache_dir": str(teacher_dir),
        "representation_cache_dir": str(repr_dir),
        "previous_4b_dir": str(previous_4b_dir),
        "output_dir": str(output_dir),
        "seeds": args.seeds,
        "epochs": args.epochs,
        "cv_epochs": args.cv_epochs,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "weight_decay": args.weight_decay,
        "patience": args.patience,
        "prior_epochs": args.prior_epochs,
        "fold_seed": args.fold_seed,
        "device": str(device),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "effective_memory_count": len(memory_bank),
        "state_cache": state_meta,
        "memory_cache": memory_meta,
    }
    atomic_write_json(output_dir / "run_metadata.json", metadata)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")

    print("running field algebra validation", flush=True)
    field_report = field_algebra_validation(rank=128, program_dim=32, count=len(memory_bank), seed=13)
    atomic_write_json(output_dir / "field_algebra_validation.json", field_report)

    print("running reference reproduction check", flush=True)
    reference_reproduction = _reference_reproduction(int(state_reps.shape[1]), int(memory_reps.shape[1]), device=device)
    atomic_write_json(output_dir / "reference_reproduction.json", reference_reproduction)
    if not reference_reproduction["passed"]:
        raise AssertionError(f"Reference/core reproduction failed: {reference_reproduction}")

    print("running continuity split", flush=True)
    continuity = _run_continuity(
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        seeds=args.seeds,
        output_dir=output_dir / "continuity",
        device=device,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        prior_epochs=args.prior_epochs,
    )
    atomic_write_json(output_dir / "continuity_split_summary.json", continuity)

    print("running task-grouped cross-validation", flush=True)
    cv = _run_cross_validation(
        rows=train_rows,
        memory_bank=memory_bank,
        memory_representations=memory_reps,
        state_representations=state_reps,
        seeds=args.seeds,
        output_dir=output_dir / "cross_validation",
        device=device,
        epochs=args.cv_epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        fold_seed=args.fold_seed,
    )
    atomic_write_json(output_dir / "cross_validation_summary.json", cv)

    previous_4b = _load_previous_4b(previous_4b_dir)
    decision = _gate_decision(
        continuity=continuity,
        cross_validation=cv,
        previous_4b=previous_4b,
        reference_reproduction=reference_reproduction,
    )
    summary = {
        **metadata,
        "runtime_s": time.perf_counter() - start,
        "field_algebra": field_report,
        "reference_reproduction": reference_reproduction,
        "previous_4b": previous_4b,
        "continuity_split": continuity,
        "cross_validation": cv,
        "decision_gate": decision,
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", write_report(summary))
    print(f"Wrote Milestone 4C signed field artifacts to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
