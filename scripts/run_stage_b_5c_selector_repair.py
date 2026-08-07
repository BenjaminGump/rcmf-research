from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.addressing_4b import distribution, evaluate_scores, mean_std, rows_by_split
from rcmf.training.addressing_only import rows_to_tensors
from rcmf.training.datasets import load_decision_examples
from rcmf.training.selector_repair_5c import (
    SELECTOR_REPAIR_VERSION,
    SelectorRepairLossConfig,
    default_repair_configs,
    evaluate_selector_repair_model,
    make_signed_selector,
    save_selector_checkpoint,
    summarize_selector_runs,
    top_utility_metrics,
    train_selector_repair_model,
)
from rcmf.training.signed_residual_field import (
    build_fold_rows,
    deterministic_task_folds,
    signed_geometry,
    train_memory_prior,
)
from rcmf.training.stage_c1 import prepare_selector_payload, split_rows
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, maybe_git_commit, read_jsonl, sha256_file
from scripts.build_stage_c1_response_cache import validate_response_cache
from scripts.run_stage_b_4c_signed_field import _load_representation_cache, _load_rows
from scripts.run_stage_c1_5b_diagnostics import (
    corr_report,
    decompose_rows,
    evaluate_removal,
    load_field_and_injector,
    removal_effect_summary,
    rows_by_state,
    write_jsonl,
)
from scripts.run_stage_c1_signed_program import (
    _build_tokenized_rows,
    _evaluate_cache_baseline,
    _response_rows_by_state,
    evaluate_student,
)


RUN_VERSION = "stage_b_selector_repair_5c_v1"
REFERENCE_5B = {
    "raw_teacher_best_recall@1": 0.113043,
    "raw_teacher_best_recall@4": 0.313043,
    "raw_teacher_best_recall@8": 0.466667,
    "raw_teacher_best_median_rank": 10.0,
    "raw_teacher_best_negative_score_fraction": 0.243478,
    "raw_utility_vs_signed_score_spearman": 0.271534,
}


def _metric_mean(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, dict):
        mean = value.get("mean")
        return float(mean) if mean is not None else 0.0
    if value is None:
        return 0.0
    return float(value)


def _summary_mean(summary: dict[str, Any], section: str, key: str) -> float:
    return _metric_mean(summary.get(section, {}), key)


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    labels = rows_to_tensors(rows)
    valid = labels["valid_mask"]
    utility = labels["utility"]
    return {
        "states": len(rows),
        "memories": int(valid.shape[1]),
        "valid_pairs": int(valid.sum().item()),
        "missing_pairs": int((~valid).sum().item()),
        "positive_pairs": int((valid & (utility > 0.01)).sum().item()),
        "neutral_pairs": int((valid & (utility.abs() <= 0.01)).sum().item()),
        "negative_pairs": int((valid & (utility < -0.01)).sum().item()),
        "positive_states": int((labels["positive_gain"].sum(dim=1) > 0).sum().item()),
        "no_positive_states": int((labels["no_positive_state"] & ~labels["all_missing_state"]).sum().item()),
        "all_missing_states": int(labels["all_missing_state"].sum().item()),
    }


def _global_prior_eval(mu: Tensor, rows: list[dict[str, Any]], *, device: torch.device) -> dict[str, Any]:
    labels = rows_to_tensors(rows, device=device)
    scores = mu.to(device=device, dtype=torch.float32).unsqueeze(0).repeat(len(rows), 1)
    return {
        "full_score": evaluate_scores(scores, labels),
        "top_utility": {key: value for key, value in top_utility_metrics(scores, labels).items() if key != "per_state_rows"},
    }


def _run_config_seeds(
    *,
    config: SelectorRepairLossConfig,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    seeds: Sequence[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    prior_kind: str,
    early_stopping: bool,
    source_commit: str | None,
    extra_checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runs = []
    checkpoints = []
    state_dim = int(state_representations.shape[1])
    memory_dim = int(memory_representations.shape[1])
    for seed in seeds:
        print(f"training selector config={config.name} seed={seed} prior={prior_kind}", flush=True)
        model = make_signed_selector(state_dim, memory_dim)
        run = train_selector_repair_model(
            model=model,
            loss_config=config,
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            seed=int(seed),
            device=device,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
            early_stopping=early_stopping,
        )
        ckpt = output_dir / "checkpoints" / f"{_safe_name(config.name)}_{prior_kind}_seed_{seed}.pt"
        checkpoints.append(
            save_selector_checkpoint(
                ckpt,
                run=run,
                loss_config=config,
                seed=int(seed),
                prior_kind=prior_kind,
                source_commit=source_commit,
                extra=extra_checkpoint,
            )
        )
        runs.append(run)
    return {
        "loss_config": config.as_dict(),
        "runs": runs,
        "summary": summarize_selector_runs(runs),
        "checkpoints": checkpoints,
    }


def _run_cross_validation(
    *,
    rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    configs: list[SelectorRepairLossConfig],
    seeds: Sequence[int],
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    fold_seed: int,
    source_commit: str | None,
) -> dict[str, Any]:
    tasks = sorted({str(row["task_id"]) for row in rows})
    folds = deterministic_task_folds(tasks, folds=5, seed=fold_seed)
    fold_outputs = []
    for fold in folds:
        fold_index = int(fold["fold"])
        print(f"5C CV fold {fold_index}", flush=True)
        payload = build_fold_rows(rows, memory_bank, fold)
        if not payload["validation"]["passed"]:
            raise ValueError(f"Fold leakage validation failed: {payload['validation']}")
        fold_dir = output_dir / f"fold_{fold_index}"
        fold_dir.mkdir(parents=True, exist_ok=True)
        fold_memory_reps = memory_representations[payload["stage_indices"]]
        mu = train_memory_prior(payload["train_rows"], memory_count=int(fold_memory_reps.shape[0]))
        baselines = {
            "global_memory_prior": _global_prior_eval(mu, payload["validation_rows"], device=device),
            "train_label_counts": _label_counts(payload["train_rows"]),
            "validation_label_counts": _label_counts(payload["validation_rows"]),
        }
        config_outputs = {}
        for config in configs:
            result = _run_config_seeds(
                config=config,
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
                early_stopping=True,
                source_commit=source_commit,
                extra_checkpoint={"fold": fold, "stage_indices": payload["stage_indices"]},
            )
            config_outputs[config.name] = result
            atomic_write_json(fold_dir / f"{_safe_name(config.name)}_partial.json", result)
        fold_output = {
            "fold": fold,
            "validation": payload["validation"],
            "stage_indices": payload["stage_indices"],
            "memory_count": int(fold_memory_reps.shape[0]),
            "baselines": baselines,
            "configs": config_outputs,
        }
        atomic_write_json(fold_dir / "summary.json", fold_output)
        fold_outputs.append(fold_output)
    aggregate = _summarize_cv(fold_outputs)
    return {
        "format": "stage_b_selector_repair_5c_task_grouped_cv_v1",
        "fold_seed": fold_seed,
        "folds": fold_outputs,
        "aggregate": aggregate,
        "selection": select_repaired_config(aggregate),
    }


def _summarize_cv(folds: list[dict[str, Any]]) -> dict[str, Any]:
    config_names = sorted(folds[0]["configs"])
    baseline_name = "A_stage4c_original"
    output: dict[str, Any] = {}
    per_config_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in config_names}
    for fold in folds:
        global_full = fold["baselines"]["global_memory_prior"]["full_score"]
        global_top = fold["baselines"]["global_memory_prior"]["top_utility"]
        baseline = fold["configs"][baseline_name]["summary"] if baseline_name in fold["configs"] else None
        baseline_recall8 = _summary_mean(baseline or {}, "top_utility", "raw_teacher_best_recall@8")
        baseline_spearman = _summary_mean(baseline or {}, "top_utility", "raw_utility_vs_signed_score_spearman")
        for name, payload in fold["configs"].items():
            summary = payload["summary"]
            row = {
                "fold": int(fold["fold"]["fold"]),
                "config": name,
                "validation_task_ids": fold["fold"]["validation_task_ids"],
                "memory_count": int(fold["memory_count"]),
                "global_ndcg@4": _metric_mean(global_full, "ndcg@4"),
                "global_raw_best_recall@8": _metric_mean(global_top, "raw_teacher_best_recall@8"),
                "stage4c_original_recall@8": baseline_recall8,
                "stage4c_original_spearman": baseline_spearman,
                "ndcg@4": _summary_mean(summary, "full_score", "ndcg@4"),
                "mrr": _summary_mean(summary, "full_score", "mrr"),
                "positive_vs_negative_pairwise_accuracy": _summary_mean(summary, "full_score", "positive_vs_negative_pairwise_accuracy"),
                "residual_correlation": _summary_mean(summary, "residual_stats", "residual_correlation"),
                "raw_teacher_best_recall@1": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@1"),
                "raw_teacher_best_recall@2": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@2"),
                "raw_teacher_best_recall@4": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@4"),
                "raw_teacher_best_recall@8": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@8"),
                "near_best_recall@4": _summary_mean(summary, "top_utility", "near_best_recall@4"),
                "near_best_recall@8": _summary_mean(summary, "top_utility", "near_best_recall@8"),
                "top_utility_mass@4": _summary_mean(summary, "top_utility", "top_utility_mass@4"),
                "top_utility_mass@8": _summary_mean(summary, "top_utility", "top_utility_mass@8"),
                "raw_teacher_best_negative_score_fraction": _summary_mean(summary, "top_utility", "raw_teacher_best_negative_score_fraction"),
                "strong_positive_negative_score_fraction": _summary_mean(summary, "top_utility", "strong_positive_negative_score_fraction"),
                "raw_utility_vs_signed_score_spearman": _summary_mean(summary, "top_utility", "raw_utility_vs_signed_score_spearman"),
                "median_best_rank": float(summary["top_utility"]["raw_teacher_best_rank"].get("p50") or 0.0),
                "p75_best_rank": float(summary["top_utility"]["raw_teacher_best_rank"].get("p75") or 0.0),
                "p95_best_rank": float(summary["top_utility"]["raw_teacher_best_rank"].get("p95") or 0.0),
                "correct_minus_shuffled_ndcg@4": _summary_mean(summary, "correct_minus_shuffled_state", "ndcg@4"),
                "correct_minus_shuffled_recall@4": _summary_mean(summary, "correct_minus_shuffled_state", "raw_teacher_best_recall@4"),
                "correct_minus_shuffled_recall@8": _summary_mean(summary, "correct_minus_shuffled_state", "raw_teacher_best_recall@8"),
                "correct_minus_shuffled_mass@4": _summary_mean(summary, "correct_minus_shuffled_state", "top_utility_mass@4"),
                "correct_minus_shuffled_mass@8": _summary_mean(summary, "correct_minus_shuffled_state", "top_utility_mass@8"),
                "correct_minus_shuffled_spearman": _summary_mean(summary, "correct_minus_shuffled_state", "raw_utility_vs_signed_score_spearman"),
                "interaction_variance": _summary_mean(summary, "geometry", "interaction_variance"),
                "q_effective_rank": _summary_mean(summary, "geometry", "q_centered_effective_rank"),
                "k_effective_rank": _summary_mean(summary, "geometry", "k_centered_effective_rank"),
                "best_epoch": _summary_mean(summary, "best_epoch", "mean"),
            }
            row["ndcg@4_improvement_over_global"] = row["ndcg@4"] - row["global_ndcg@4"]
            row["recall@8_improvement_over_stage4c"] = row["raw_teacher_best_recall@8"] - row["stage4c_original_recall@8"]
            row["spearman_improvement_over_stage4c"] = row["raw_utility_vs_signed_score_spearman"] - row["stage4c_original_spearman"]
            per_config_rows[name].append(row)
    for name, rows in per_config_rows.items():
        output[name] = _aggregate_config_rows(rows)
    return {
        "per_config": output,
        "per_fold_rows": [row for rows in per_config_rows.values() for row in rows],
        "stage4c_original_config": baseline_name,
    }


def _aggregate_config_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scalar_metrics = sorted(
        {
            key
            for row in rows
            for key, value in row.items()
            if isinstance(value, (int, float)) and key not in {"fold"}
        }
    )
    aggregate = {metric: mean_std(row[metric] for row in rows) for metric in scalar_metrics}
    aggregate["positive_recall8_improvement_folds"] = sum(
        1 for row in rows if row["recall@8_improvement_over_stage4c"] > 0
    )
    aggregate["positive_ndcg_global_improvement_folds"] = sum(
        1 for row in rows if row["ndcg@4_improvement_over_global"] > 0
    )
    aggregate["fold_count"] = len(rows)
    aggregate["per_fold"] = rows
    return aggregate


def select_repaired_config(aggregate: dict[str, Any]) -> dict[str, Any]:
    candidates = aggregate["per_config"]
    baseline = candidates["A_stage4c_original"]
    baseline_spearman = _agg_mean(baseline, "raw_utility_vs_signed_score_spearman")
    rows = []
    for name, payload in candidates.items():
        interaction_var = _agg_mean(payload, "interaction_variance")
        no_collapse = bool(interaction_var >= 0.01 and _agg_mean(payload, "q_effective_rank") > 2.0 and _agg_mean(payload, "k_effective_rank") > 2.0)
        cv_gate = bool(
            _agg_mean(payload, "raw_teacher_best_recall@8") >= 0.60
            and _agg_mean(payload, "raw_teacher_best_recall@4") >= 0.40
            and int(payload.get("positive_recall8_improvement_folds", 0)) >= 4
            and _agg_mean(payload, "ndcg@4_improvement_over_global") >= 0.05
            and _agg_mean(payload, "correct_minus_shuffled_ndcg@4") >= 0.08
            and _agg_mean(payload, "raw_utility_vs_signed_score_spearman") > baseline_spearman
            and no_collapse
        )
        score = (
            2.0 * _agg_mean(payload, "raw_teacher_best_recall@8")
            + 1.0 * _agg_mean(payload, "raw_teacher_best_recall@4")
            + 0.5 * _agg_mean(payload, "ndcg@4")
            + 0.3 * _agg_mean(payload, "raw_utility_vs_signed_score_spearman")
            - 0.2 * _agg_mean(payload, "raw_teacher_best_negative_score_fraction")
        )
        rows.append(
            {
                "config": name,
                "cv_gate_pass": cv_gate,
                "no_interaction_collapse": no_collapse,
                "selection_score": score,
                "raw_teacher_best_recall@8": _agg_mean(payload, "raw_teacher_best_recall@8"),
                "raw_teacher_best_recall@4": _agg_mean(payload, "raw_teacher_best_recall@4"),
                "ndcg@4_improvement_over_global": _agg_mean(payload, "ndcg@4_improvement_over_global"),
                "correct_minus_shuffled_ndcg@4": _agg_mean(payload, "correct_minus_shuffled_ndcg@4"),
                "spearman": _agg_mean(payload, "raw_utility_vs_signed_score_spearman"),
                "negative_best_fraction": _agg_mean(payload, "raw_teacher_best_negative_score_fraction"),
            }
        )
    passed = [row for row in rows if row["cv_gate_pass"]]
    pool = passed or [row for row in rows if row["config"] != "A_stage4c_original"] or rows
    selected = max(pool, key=lambda row: row["selection_score"])
    return {
        "format": "stage_b_selector_repair_5c_cv_selection_v1",
        "selected_config": selected["config"],
        "selected_passed_cv_gate": bool(selected["cv_gate_pass"]),
        "baseline_spearman": baseline_spearman,
        "candidate_rows": sorted(rows, key=lambda row: row["selection_score"], reverse=True),
        "selection_rule": "prefer configs passing CV gate; otherwise choose highest predetermined composite score excluding original baseline",
    }


def _agg_mean(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, dict):
        mean = value.get("mean")
        return float(mean) if mean is not None else 0.0
    if value is None:
        return 0.0
    return float(value)


def _run_continuity_selected(
    *,
    selected_config: SelectorRepairLossConfig,
    selected_cv: dict[str, Any],
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    seeds: Sequence[int],
    output_dir: Path,
    device: torch.device,
    batch_size: int,
    lr: float,
    weight_decay: float,
    source_commit: str | None,
) -> dict[str, Any]:
    mu = train_memory_prior(train_rows, memory_count=int(memory_representations.shape[0]))
    fixed_epochs = _continuity_epoch_count(selected_cv, selected_config.name)
    print(f"continuity selected config={selected_config.name} fixed_epochs={fixed_epochs}", flush=True)
    baselines = {
        "global_memory_prior": _global_prior_eval(mu, validation_rows, device=device),
        "train_label_counts": _label_counts(train_rows),
        "validation_label_counts": _label_counts(validation_rows),
    }
    trained = _run_config_seeds(
        config=selected_config,
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_representations,
        memory_representations=memory_representations,
        mu=mu,
        seeds=seeds,
        output_dir=output_dir,
        device=device,
        epochs=fixed_epochs,
        batch_size=batch_size,
        lr=lr,
        weight_decay=weight_decay,
        patience=fixed_epochs + 1,
        prior_kind="empirical_train_mu",
        early_stopping=False,
        source_commit=source_commit,
        extra_checkpoint={"continuity_fixed_epochs_from_cv": fixed_epochs},
    )
    summary = trained["summary"]
    return {
        "format": "stage_b_selector_repair_5c_continuity_v1",
        "selected_config": selected_config.as_dict(),
        "fixed_epochs_from_cv": fixed_epochs,
        "mu_distribution": distribution(mu.tolist()),
        "baselines": baselines,
        "selected": trained,
        "continuity_gate": continuity_gate(summary),
    }


def _continuity_epoch_count(cv: dict[str, Any], config_name: str) -> int:
    config = cv["aggregate"]["per_config"][config_name]
    value = int(round(float(config["best_epoch"]["mean"] or 1.0)))
    return max(1, min(160, value))


def continuity_gate(summary: dict[str, Any]) -> dict[str, Any]:
    top = summary["top_utility"]
    full = summary["full_score"]
    rank = top["raw_teacher_best_rank"]
    values = {
        "raw_teacher_best_recall@8": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@8"),
        "raw_teacher_best_recall@4": _summary_mean(summary, "top_utility", "raw_teacher_best_recall@4"),
        "median_best_rank": float(rank.get("p50") or 999.0),
        "raw_teacher_best_negative_score_fraction": _summary_mean(summary, "top_utility", "raw_teacher_best_negative_score_fraction"),
        "raw_utility_vs_signed_score_spearman": _summary_mean(summary, "top_utility", "raw_utility_vs_signed_score_spearman"),
        "ndcg@4": _summary_mean(summary, "full_score", "ndcg@4"),
        "correct_minus_shuffled_ndcg@4": _summary_mean(summary, "correct_minus_shuffled_state", "ndcg@4"),
    }
    passed = bool(
        values["raw_teacher_best_recall@8"] >= 0.62
        and values["raw_teacher_best_recall@4"] >= 0.43
        and values["median_best_rank"] <= 6
        and values["raw_teacher_best_negative_score_fraction"] <= 0.12
        and values["raw_utility_vs_signed_score_spearman"] >= 0.35
        and values["ndcg@4"] >= 0.53
        and values["correct_minus_shuffled_ndcg@4"] >= 0.10
    )
    return {
        "format": "stage_b_selector_repair_5c_continuity_gate_v1",
        "passed": passed,
        "reference_5b": REFERENCE_5B,
        "values": values,
    }


def _config_by_name(configs: Sequence[SelectorRepairLossConfig], name: str) -> SelectorRepairLossConfig:
    for config in configs:
        if config.name == name:
            return config
    raise KeyError(name)


def load_repaired_selector_checkpoint(
    checkpoint_path: Path,
    *,
    state_dim: int,
    memory_dim: int,
    device: torch.device,
) -> tuple[torch.nn.Module, dict[str, Any]]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    if checkpoint.get("format") != SELECTOR_REPAIR_VERSION:
        raise ValueError(f"unexpected selector checkpoint format at {checkpoint_path}: {checkpoint.get('format')}")
    if checkpoint.get("model_kind") != "signed_core_field_r128":
        raise ValueError(f"unexpected selector model kind at {checkpoint_path}: {checkpoint.get('model_kind')}")
    model = make_signed_selector(state_dim, memory_dim).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    for param in model.parameters():
        param.requires_grad_(False)
    return model, checkpoint


def selector_payload_from_checkpoint(
    checkpoint_path: Path,
    *,
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    device: torch.device,
) -> tuple[dict[str, Tensor], dict[str, Any]]:
    selector, checkpoint = load_repaired_selector_checkpoint(
        checkpoint_path,
        state_dim=int(state_representations.shape[1]),
        memory_dim=int(memory_representations.shape[1]),
        device=device,
    )
    payload = prepare_selector_payload(
        selector=selector,
        state_representations=state_representations,
        memory_representations=memory_representations,
        mu=mu,
        device=device,
    )
    return payload, checkpoint


def _checkpoint_by_seed(paths: Sequence[str]) -> dict[int, Path]:
    output: dict[int, Path] = {}
    for path in paths:
        checkpoint = torch.load(path, map_location="cpu")
        output[int(checkpoint["seed"])] = Path(path)
    return output


def stage_index_to_memory_id(row: dict[str, Any], index: int) -> str:
    return str(row["ordered_effective_memory_ids"][int(index)])


def run_stage_c1_projection(
    *,
    cfg: Any,
    data_dir: Path,
    teacher_cache_dir: Path,
    labels_dir: Path,
    response_cache_dir: Path,
    stage_c1_dir: Path,
    label_rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    selector_checkpoints: Sequence[str],
    seeds: Sequence[int],
    output_dir: Path,
    device: torch.device,
    batch_size: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    print("5C eval-only Stage-C1 projection: validating response cache", flush=True)
    response_rows = _load_rows(response_cache_dir / "response_cache.jsonl")
    teacher_rows = {str(row.get("pair_key")): row for row in read_jsonl(teacher_cache_dir / "teacher_cache_full_rows.jsonl")}
    response_validation = validate_response_cache(
        response_rows,
        label_rows=label_rows,
        memory_bank=memory_bank,
        teacher_rows=teacher_rows,
    )
    atomic_write_json(output_dir / "response_cache_validation.json", response_validation)
    if not response_validation["passed"]:
        raise ValueError(f"response cache validation failed: {response_validation['errors_first_50']}")

    print("5C eval-only Stage-C1 projection: loading frozen Qwen backend", flush=True)
    backend = build_backend(cfg, load_model=True)
    context_limit = getattr(getattr(backend.model, "config", None), "max_position_embeddings", 40960)
    model_dim = int(getattr(getattr(backend.model, "config", None), "hidden_size"))
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    response_by_state = _response_rows_by_state(response_rows)
    train_label_rows, validation_label_rows = split_rows(label_rows)
    validation_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        label_rows=validation_label_rows,
        response_by_state=response_by_state,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(context_limit),
    )
    positive_rows = [row for row in validation_rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    mu = train_memory_prior(train_label_rows, memory_count=len(memory_bank))
    checkpoint_map = _checkpoint_by_seed(selector_checkpoints)
    projection_rows = []
    seed_summaries = []
    for seed in seeds:
        print(f"5C eval-only Stage-C1 projection seed={seed} positive_states={len(positive_rows)}", flush=True)
        selector_payload, selector_checkpoint = selector_payload_from_checkpoint(
            checkpoint_map[int(seed)],
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            device=device,
        )
        field, injector, stage_c1_checkpoint = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_representations.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_representations.to(device=device, dtype=torch.float32)).detach()
        full_eval = evaluate_student(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=positive_rows,
            memory_representations=memory_representations,
            device=device,
            seed=int(seed),
            batch_size=batch_size,
            trained_programs=programs,
        )
        full_by_id = rows_by_state(full_eval["rows"])
        decomp = decompose_rows(
            selector_payload=selector_payload,
            rows=positive_rows,
            programs=programs,
            device=device,
        )
        utility_values: list[float] = []
        delta_norms: list[float] = []
        teacher_effects: list[float] = []
        selector_top_effects: list[float] = []
        for row_index, row in enumerate(positive_rows):
            scores = decomp["scores"][row_index].detach().cpu()
            score_order = torch.argsort(scores, descending=True)
            score_ranks = torch.empty_like(score_order)
            score_ranks[score_order] = torch.arange(1, scores.numel() + 1)
            contribution_norms = decomp["gate_c"][row_index].norm(dim=1).detach().cpu()
            contribution_order = torch.argsort(contribution_norms, descending=True)
            contribution_ranks = torch.empty_like(contribution_order)
            contribution_ranks[contribution_order] = torch.arange(1, contribution_norms.numel() + 1)
            best = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
            selector_top = int(torch.argmax(scores).item())
            full_nll = float(full_by_id[row["state_example_id"]]["student_target_nll"])
            teacher_removal = evaluate_removal(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                row=row,
                memory_representations=memory_representations,
                programs=programs,
                remove_index=best,
                full_nll=full_nll,
                device=device,
                seed=int(seed),
            )
            selector_removal = evaluate_removal(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                row=row,
                memory_representations=memory_representations,
                programs=programs,
                remove_index=selector_top,
                full_nll=full_nll,
                device=device,
                seed=int(seed),
            )
            teacher_effects.append(float(teacher_removal["delta_vs_full"]))
            selector_top_effects.append(float(selector_removal["delta_vs_full"]))
            for memory_index, (valid, value) in enumerate(zip(row["valid_mask"], row["raw_utility"])):
                if valid and value is not None:
                    utility_values.append(float(value))
                    delta_norms.append(float(decomp["delta_z"][row_index, memory_index].detach().cpu().norm()))
            projection_rows.append(
                {
                    "seed": int(seed),
                    "state_example_id": row["state_example_id"],
                    "state_index": int(row["state_index"]),
                    "task_id": row["task_id"],
                    "teacher_best_memory_id": row["response_cache"]["best_memory_id"],
                    "teacher_best_stage_index": best,
                    "teacher_utility": float(row["response_cache"]["teacher_utility"]),
                    "teacher_best_signed_score": float(scores[best].item()),
                    "teacher_best_signed_score_rank": int(score_ranks[best].item()),
                    "teacher_best_contribution_rank": int(contribution_ranks[best].item()),
                    "teacher_best_contribution_norm": float(contribution_norms[best].item()),
                    "selector_top_stage_index": selector_top,
                    "selector_top_memory_id": stage_index_to_memory_id(row, selector_top),
                    "selector_top_signed_score": float(scores[selector_top].item()),
                    "full_nll": full_nll,
                    "teacher_best_removal": teacher_removal,
                    "selector_top_removal": selector_removal,
                    "selector_checkpoint": str(checkpoint_map[int(seed)]),
                    "selector_loss_config": selector_checkpoint.get("loss_config"),
                    "stage_c1_content_checkpoint": str(stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt"),
                    "stage_c1_content_source_commit": stage_c1_checkpoint.get("source_commit"),
                }
            )
        seed_rows = [row for row in projection_rows if row["seed"] == int(seed)]
        seed_summaries.append(
            {
                "seed": int(seed),
                "positive_state_count": len(positive_rows),
                "selector_checkpoint": str(checkpoint_map[int(seed)]),
                "teacher_best_signed_score_rank": distribution(row["teacher_best_signed_score_rank"] for row in seed_rows),
                "teacher_best_contribution_rank": distribution(row["teacher_best_contribution_rank"] for row in seed_rows),
                "teacher_best_loo_effect": distribution(teacher_effects),
                "selector_top_loo_effect": distribution(selector_top_effects),
                "raw_utility_vs_analytic_delta_z_norm": corr_report(utility_values, delta_norms),
            }
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    write_jsonl(output_dir / "stage_c1_projection_rows.jsonl", projection_rows)
    summary = {
        "format": "stage_b_selector_repair_5c_eval_only_stage_c1_projection_v1",
        "hard_scope": "eval_only_existing_stage_c1_programs_injectors_no_retraining",
        "positive_state_count_per_seed": len(positive_rows),
        "state_seed_rows": len(projection_rows),
        "response_cache_validation": response_validation,
        "seeds": seed_summaries,
        "aggregate": {
            "teacher_best_signed_score_rank": distribution(row["teacher_best_signed_score_rank"] for row in projection_rows),
            "teacher_best_contribution_rank": distribution(row["teacher_best_contribution_rank"] for row in projection_rows),
            "teacher_best_loo_effect": removal_effect_summary(
                [{"removals": {"teacher_best": row["teacher_best_removal"]}} for row in projection_rows],
                seed=20260807,
            )["effects"]["teacher_best"],
            "selector_top_loo_effect": removal_effect_summary(
                [{"removals": {"selector_top": row["selector_top_removal"]}} for row in projection_rows],
                seed=20260808,
            )["effects"]["selector_top"],
            "teacher_minus_selector_top_loo": distribution(
                float(row["teacher_best_removal"]["delta_vs_full"]) - float(row["selector_top_removal"]["delta_vs_full"])
                for row in projection_rows
            ),
        },
        "rows_path": str(output_dir / "stage_c1_projection_rows.jsonl"),
    }
    all_utility = []
    all_delta_norms = []
    for seed_summary in seed_summaries:
        corr = seed_summary["raw_utility_vs_analytic_delta_z_norm"]
        if corr.get("count"):
            pass
    for row in positive_rows:
        del row
    for record in projection_rows:
        # Per-pair raw utility versus analytic delta-z is captured in seed-level
        # correlations above; aggregate is recomputed from the saved rows only
        # for the requested top-level summary fields.
        pass
    valid_utility = []
    valid_delta = []
    for seed in seeds:
        selector_payload, _ = selector_payload_from_checkpoint(
            checkpoint_map[int(seed)],
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            device=device,
        )
        field, _, _ = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_representations.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_representations.to(device=device, dtype=torch.float32)).detach()
        decomp = decompose_rows(selector_payload=selector_payload, rows=positive_rows, programs=programs, device=device)
        for row_index, row in enumerate(positive_rows):
            for memory_index, (valid, value) in enumerate(zip(row["valid_mask"], row["raw_utility"])):
                if valid and value is not None:
                    valid_utility.append(float(value))
                    valid_delta.append(float(decomp["delta_z"][row_index, memory_index].detach().cpu().norm()))
    summary["aggregate"]["raw_utility_vs_analytic_delta_z_norm"] = corr_report(valid_utility, valid_delta)
    atomic_write_json(output_dir / "stage_c1_projection_summary.json", summary)
    return summary


def make_decision(cv: dict[str, Any], continuity: dict[str, Any]) -> dict[str, Any]:
    selected = cv["selection"]
    continuity_gate_payload = continuity["continuity_gate"]
    selected_name = selected["selected_config"]
    selected_cv = cv["aggregate"]["per_config"][selected_name]
    baseline = cv["aggregate"]["per_config"]["A_stage4c_original"]
    materially_improved = (
        _agg_mean(selected_cv, "raw_teacher_best_recall@8") >= _agg_mean(baseline, "raw_teacher_best_recall@8") + 0.05
        and _agg_mean(selected_cv, "raw_teacher_best_recall@4") >= _agg_mean(baseline, "raw_teacher_best_recall@4") + 0.03
    )
    ndcg_state_ok = (
        _agg_mean(selected_cv, "ndcg@4_improvement_over_global") >= 0.05
        and _agg_mean(selected_cv, "correct_minus_shuffled_ndcg@4") >= 0.08
    )
    if not selected["selected_passed_cv_gate"] or not materially_improved or not ndcg_state_ok:
        branch = "selector_capacity_or_representation_tradeoff"
    elif not continuity_gate_payload["passed"]:
        branch = "task_generalization_failure"
    else:
        branch = "selector_teacher_alignment_repaired"
    return {
        "format": "stage_b_selector_repair_5c_decision_v1",
        "branch": branch,
        "cv_gate_passed": bool(selected["selected_passed_cv_gate"]),
        "continuity_gate_passed": bool(continuity_gate_payload["passed"]),
        "stage_c_allowed": False,
        "program_retraining_allowed": False,
        "next_required_program_milestone": "pair_level_or_single_memory_behavioral_grounding_before_any_full_field_stage_c1_repeat",
        "selected_config": selected_name,
        "values": {
            "materially_improved_alignment_cv": bool(materially_improved),
            "ndcg_state_dependence_cv_ok": bool(ndcg_state_ok),
            "selected_cv_recall@8": _agg_mean(selected_cv, "raw_teacher_best_recall@8"),
            "selected_cv_recall@4": _agg_mean(selected_cv, "raw_teacher_best_recall@4"),
            "selected_cv_ndcg@4_improvement_over_global": _agg_mean(selected_cv, "ndcg@4_improvement_over_global"),
            "selected_cv_correct_minus_shuffled_ndcg@4": _agg_mean(selected_cv, "correct_minus_shuffled_ndcg@4"),
            "selected_continuity": continuity_gate_payload["values"],
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
    cv = summary["cross_validation"]
    selected = cv["selection"]["selected_config"]
    lines = [
        "# Milestone 5C Raw-Teacher Top-Utility Selector Repair",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- output: `{summary['output_dir']}`",
        f"- hard scope: selector-only training; no Stage-C training, no injector training, no AppWorld generation/evaluation.",
        f"- selected config: `{selected}`",
        f"- decision branch: `{summary['decision']['branch']}`",
        "",
        "## 5-Fold CV Ablation",
        "",
        "| config | R@4 | R@8 | NDCG@4 | NDCG-global | delta NDCG@4 | Spearman | neg-best | pass |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in cv["selection"]["candidate_rows"]:
        payload = cv["aggregate"]["per_config"][row["config"]]
        lines.append(
            f"| {row['config']} | "
            f"{_format_metric(payload['raw_teacher_best_recall@4'])} | "
            f"{_format_metric(payload['raw_teacher_best_recall@8'])} | "
            f"{_format_metric(payload['ndcg@4'])} | "
            f"{_format_metric(payload['ndcg@4_improvement_over_global'])} | "
            f"{_format_metric(payload['correct_minus_shuffled_ndcg@4'])} | "
            f"{_format_metric(payload['raw_utility_vs_signed_score_spearman'])} | "
            f"{_format_metric(payload['raw_teacher_best_negative_score_fraction'])} | "
            f"`{row['cv_gate_pass']}` |"
        )
    cont = summary["continuity"]["selected"]["summary"]
    lines.extend(
        [
            "",
            "## Continuity Split",
            "",
            f"- raw-teacher-best Recall@1/4/8: `{_summary_mean(cont, 'top_utility', 'raw_teacher_best_recall@1'):.6f}`, `{_summary_mean(cont, 'top_utility', 'raw_teacher_best_recall@4'):.6f}`, `{_summary_mean(cont, 'top_utility', 'raw_teacher_best_recall@8'):.6f}`",
            f"- median / p75 / p95 rank: `{cont['top_utility']['raw_teacher_best_rank'].get('p50')}`, `{cont['top_utility']['raw_teacher_best_rank'].get('p75')}`, `{cont['top_utility']['raw_teacher_best_rank'].get('p95')}`",
            f"- teacher-best negative-score fraction: `{_summary_mean(cont, 'top_utility', 'raw_teacher_best_negative_score_fraction'):.6f}`",
            f"- utility-score Spearman: `{_summary_mean(cont, 'top_utility', 'raw_utility_vs_signed_score_spearman'):.6f}`",
            f"- NDCG@4: `{_summary_mean(cont, 'full_score', 'ndcg@4'):.6f}`",
            f"- correct-minus-shuffled NDCG@4: `{_summary_mean(cont, 'correct_minus_shuffled_state', 'ndcg@4'):.6f}`",
            "",
            "## Eval-Only Stage-C1 Projection",
        ]
    )
    projection = summary.get("stage_c1_projection")
    if projection:
        agg = projection["aggregate"]
        lines.extend(
            [
                f"- teacher-best signed-score rank: `{agg['teacher_best_signed_score_rank']}`",
                f"- teacher-best contribution rank: `{agg['teacher_best_contribution_rank']}`",
                f"- teacher-best LOO effect: `{agg['teacher_best_loo_effect']}`",
                f"- selector-top LOO effect: `{agg['selector_top_loo_effect']}`",
                f"- raw utility vs analytic delta-z: `{agg['raw_utility_vs_analytic_delta_z_norm']}`",
            ]
        )
    else:
        lines.append("- skipped by CLI flag; not used for final milestone runs.")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"```json\n{json.dumps(summary['decision'], indent=2, sort_keys=True)}\n```",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 5C raw-teacher top-utility alignment repair for the signed selector.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--response-cache-dir", required=True)
    parser.add_argument("--stage-c1-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--cv-epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--fold-seed", type=int, default=23)
    parser.add_argument("--projection-batch-size", type=int, default=1)
    parser.add_argument("--skip-stage-c1-projection", action="store_true")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    data_dir = Path(args.data)
    labels_dir = Path(args.labels_dir)
    repr_dir = Path(args.representation_cache_dir)
    teacher_cache_dir = Path(args.teacher_cache_dir)
    response_cache_dir = Path(args.response_cache_dir)
    stage_c1_dir = Path(args.stage_c1_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)
    source_commit = maybe_git_commit()
    label_rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    train_rows, validation_rows = rows_by_split(label_rows)
    memory_indices = [int(row["memory_index"]) for row in memory_bank]
    state_reps, state_meta = _load_representation_cache(
        repr_dir / "decision_state_representations.pt",
        expected_count=len(label_rows),
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
    configs = default_repair_configs()
    metadata = {
        "format": RUN_VERSION,
        "source_commit": source_commit,
        "output_dir": str(output_dir),
        "config": str(args.config),
        "data_dir": str(data_dir),
        "teacher_cache_dir": str(teacher_cache_dir),
        "labels_dir": str(labels_dir),
        "representation_cache_dir": str(repr_dir),
        "response_cache_dir": str(response_cache_dir),
        "stage_c1_dir": str(stage_c1_dir),
        "seeds": args.seeds,
        "cv_epochs": args.cv_epochs,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "weight_decay": args.weight_decay,
        "patience": args.patience,
        "fold_seed": args.fold_seed,
        "device": str(device),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "effective_memory_count": len(memory_bank),
        "candidate_configs": [config.as_dict() for config in configs],
        "state_cache": state_meta,
        "memory_cache": memory_meta,
        "teacher_cache_sha256": sha256_file(teacher_cache_dir / "teacher_cache_full_rows.jsonl"),
        "label_rows_sha256": sha256_file(labels_dir / "student_labels.jsonl"),
    }
    atomic_write_json(output_dir / "run_metadata.json", metadata)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    atomic_write_json(output_dir / "candidate_loss_configs.json", [config.as_dict() for config in configs])

    cv = _run_cross_validation(
        rows=train_rows,
        memory_bank=memory_bank,
        state_representations=state_reps,
        memory_representations=memory_reps,
        configs=configs,
        seeds=args.seeds,
        output_dir=output_dir / "cross_validation",
        device=device,
        epochs=args.cv_epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        fold_seed=args.fold_seed,
        source_commit=source_commit,
    )
    atomic_write_json(output_dir / "cross_validation_summary.json", cv)
    selected_config = _config_by_name(configs, cv["selection"]["selected_config"])
    continuity = _run_continuity_selected(
        selected_config=selected_config,
        selected_cv=cv,
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        seeds=args.seeds,
        output_dir=output_dir / "continuity",
        device=device,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        source_commit=source_commit,
    )
    atomic_write_json(output_dir / "continuity_summary.json", continuity)
    projection = None
    if not args.skip_stage_c1_projection:
        projection = run_stage_c1_projection(
            cfg=cfg,
            data_dir=data_dir,
            teacher_cache_dir=teacher_cache_dir,
            labels_dir=labels_dir,
            response_cache_dir=response_cache_dir,
            stage_c1_dir=stage_c1_dir,
            label_rows=label_rows,
            memory_bank=memory_bank,
            state_representations=state_reps,
            memory_representations=memory_reps,
            selector_checkpoints=continuity["selected"]["checkpoints"],
            seeds=args.seeds,
            output_dir=output_dir / "stage_c1_projection",
            device=device,
            batch_size=args.projection_batch_size,
        )
    decision = make_decision(cv, continuity)
    summary = {
        **metadata,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": "selector_training_only_with_eval_only_old_stage_c1_projection",
        "cross_validation": cv,
        "selected_loss_config": selected_config.as_dict(),
        "continuity": continuity,
        "stage_c1_projection": projection,
        "decision": decision,
        "stage_c2_launched": False,
        "appworld_generation_evaluation_launched": False,
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", write_report(summary))
    print(f"Wrote Milestone 5C selector repair artifacts to {output_dir}", flush=True)
    print(json.dumps(decision, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
