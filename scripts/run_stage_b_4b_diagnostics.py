from __future__ import annotations

import argparse
from collections import defaultdict
import copy
from pathlib import Path
import time
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.config import RCMFConfig, load_config, save_resolved_config
from rcmf.training.addressing_4b import (
    DenseResidualAddressScorer,
    STAGE_B_4B_VERSION,
    SignedTwoTowerResidualScorer,
    StateOnlyResidualHead,
    address_geometry,
    bootstrap_metric_ci,
    build_current_stage_b_model,
    distribution,
    evaluate_current_stage_b_model,
    evaluate_scores,
    gradient_norms_for_batch,
    hard_topk_dead_zone_demo,
    hard_topk_overlap_gradient_demo,
    mean_std,
    per_state_metric_values,
    rows_by_split,
    rows_to_tensors,
    summarize_model_runs,
    train_memory_prior,
    train_one_epoch_current_stage_b,
    train_residual_scorer,
    utility_decomposition,
)
from rcmf.training.addressing_only import (
    AddressingLossWeights,
    AddressingOnlyModel,
    addressing_losses,
    task_balanced_batches,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, maybe_git_commit, read_jsonl, sha256_file


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


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
        source_hash = sha256_file(expected_source_path)
        if payload.get("source_sha256") != source_hash:
            raise ValueError(f"Representation source hash mismatch for {path}")
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"representations", "chunk_representations", "owner_indices", "chunk_token_counts"}
    }
    metadata["path"] = str(path)
    metadata["shape"] = list(representations.shape)
    return representations.to(torch.float32), metadata


def _state_reps_for_rows(state_representations: torch.Tensor, rows: list[dict[str, Any]], device: torch.device) -> torch.Tensor:
    indices = torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long)
    return state_representations[indices].to(device=device, dtype=torch.float32)


def _representative_batch(train_rows: list[dict[str, Any]], batch_size: int = 32) -> list[dict[str, Any]]:
    positive = [row for row in train_rows if sum(float(v) for v in row["positive_gain"]) > 0]
    no_positive = [row for row in train_rows if row.get("no_positive_state")]
    rows = positive[: batch_size // 2] + no_positive[: batch_size - batch_size // 2]
    if len(rows) < batch_size:
        seen = {row["state_example_id"] for row in rows}
        rows.extend(row for row in train_rows if row["state_example_id"] not in seen)
    return rows[:batch_size]


def _load_best_checkpoint(model: AddressingOnlyModel, path: Path, device: torch.device) -> dict[str, Any]:
    payload = torch.load(path, map_location=device)
    model.load_state_dict(payload["model"])
    return {
        "checkpoint": str(path),
        "epoch": payload.get("epoch"),
        "format": payload.get("format"),
        "git_commit": payload.get("git_commit"),
        "extra": payload.get("extra"),
    }


def _forensic_snapshot(
    *,
    name: str,
    model: AddressingOnlyModel,
    validation_rows: list[dict[str, Any]],
    train_batch_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    cfg: RCMFConfig,
    device: torch.device,
    seed: int,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels = rows_to_tensors(validation_rows, device=device)
    state_reps = _state_reps_for_rows(state_representations, validation_rows, device)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    model.eval()
    with torch.no_grad():
        payload = model(state_reps, memory_reps)
    grad_norms = gradient_norms_for_batch(
        model,
        train_batch_rows,
        state_representations,
        memory_representations,
        device=device,
    )
    raw_dot = payload["state_address"].to(torch.float32) @ payload["alpha"].to(torch.float32).T
    return {
        "name": name,
        "seed": seed,
        "artifact": artifact or {},
        "validation_metrics": evaluate_scores(payload["q"], labels),
        "per_state_metrics": per_state_metric_values(payload["q"], labels),
        "geometry": address_geometry(payload["state_address"], payload["alpha"], payload["rho"], topk=cfg.address.topk),
        "raw_dot_zero_fraction": float((raw_dot.abs() <= 1.0e-12).to(torch.float32).mean().item()),
        "q_zero_fraction": float((payload["q"].abs() <= 1.0e-12).to(torch.float32).mean().item()),
        "gradient_norms": grad_norms,
    }


def run_forensics(
    *,
    cfg: RCMFConfig,
    seeds: list[int],
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    pilot_dir: Path,
    device: torch.device,
) -> dict[str, Any]:
    train_batch = _representative_batch(train_rows)
    snapshots: list[dict[str, Any]] = []
    for seed in seeds:
        init_model = build_current_stage_b_model(cfg, int(state_representations.shape[1]), seed=seed, device=device)
        snapshots.append(
            _forensic_snapshot(
                name="reconstructed_init",
                model=init_model,
                validation_rows=validation_rows,
                train_batch_rows=train_batch,
                state_representations=state_representations,
                memory_representations=memory_representations,
                cfg=cfg,
                device=device,
                seed=seed,
                artifact={"source": "reconstructed_from_seed"},
            )
        )
        epoch1_model = copy.deepcopy(init_model)
        train_one_epoch_current_stage_b(
            epoch1_model,
            train_rows,
            state_representations,
            memory_representations,
            seed=seed,
            device=device,
        )
        snapshots.append(
            _forensic_snapshot(
                name="reconstructed_epoch1",
                model=epoch1_model,
                validation_rows=validation_rows,
                train_batch_rows=train_batch,
                state_representations=state_representations,
                memory_representations=memory_representations,
                cfg=cfg,
                device=device,
                seed=seed,
                artifact={"source": "retrained_one_epoch_from_seed"},
            )
        )
        best_model = build_current_stage_b_model(cfg, int(state_representations.shape[1]), seed=seed, device=device)
        artifact = _load_best_checkpoint(best_model, pilot_dir / f"seed_{seed}" / "checkpoint_best.pt", device)
        snapshots.append(
            _forensic_snapshot(
                name="loaded_best_checkpoint",
                model=best_model,
                validation_rows=validation_rows,
                train_batch_rows=train_batch,
                state_representations=state_representations,
                memory_representations=memory_representations,
                cfg=cfg,
                device=device,
                seed=seed,
                artifact=artifact,
            )
        )
    dead = hard_topk_dead_zone_demo(rank=cfg.memory.rank, topk=cfg.address.topk)
    overlap = hard_topk_overlap_gradient_demo(rank=cfg.memory.rank, topk=cfg.address.topk)
    return {
        "format": "stage_b_4b_forensic_diagnostics_v1",
        "snapshots": snapshots,
        "hard_topk_dead_zone": dead,
        "hard_topk_overlap_control": overlap,
        "conclusion": _forensic_conclusion(snapshots, dead),
    }


def _forensic_conclusion(snapshots: list[dict[str, Any]], dead_zone: dict[str, Any]) -> dict[str, Any]:
    best = [snap for snap in snapshots if snap["name"] == "loaded_best_checkpoint"]
    zero_overlap = [
        snap["geometry"]["support_overlap"]["zero_support_overlap_fraction"]
        for snap in best
        if snap.get("geometry")
    ]
    state_load = [
        snap["geometry"]["state_top1_basis"]["max_load_fraction"]
        for snap in best
        if snap.get("geometry")
    ]
    alpha_load = [
        snap["geometry"]["alpha_top1_basis"]["max_load_fraction"]
        for snap in best
        if snap.get("geometry")
    ]
    raw_zero = [snap["raw_dot_zero_fraction"] for snap in best]
    return {
        "A_disjoint_support_zero_gradient_trapping": bool(
            dead_zone["state_grad_norm"] == 0.0
            and dead_zone["memory_grad_norm"] == 0.0
            and zero_overlap
            and sum(zero_overlap) / len(zero_overlap) > 0.8
        ),
        "B_shared_basis_collapse": bool(
            state_load
            and alpha_load
            and sum(state_load) / len(state_load) > 0.9
            and sum(alpha_load) / len(alpha_load) > 0.9
        ),
        "C_rho_global_prior_domination": bool(raw_zero and sum(raw_zero) / len(raw_zero) > 0.8),
        "mean_best_zero_support_overlap_fraction": sum(zero_overlap) / len(zero_overlap) if zero_overlap else None,
        "mean_best_state_top1_load": sum(state_load) / len(state_load) if state_load else None,
        "mean_best_alpha_top1_load": sum(alpha_load) / len(alpha_load) if alpha_load else None,
        "mean_best_raw_dot_zero_fraction": sum(raw_zero) / len(raw_zero) if raw_zero else None,
        "verified_cause": (
            "hard-top-k disjoint-support zero-gradient trapping plus shared-basis collapse"
            if dead_zone["state_grad_norm"] == 0.0 and zero_overlap and sum(zero_overlap) / len(zero_overlap) > 0.8
            else "requires scorer ablations"
        ),
    }


def _train_current_control(
    *,
    cfg: RCMFConfig,
    seed: int,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    output_dir: Path,
    device: torch.device,
    max_epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    model = AddressingOnlyModel(cfg, representation_dim=int(state_representations.shape[1])).to(device)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=weight_decay)
    best_state = copy.deepcopy(model.state_dict())
    best_metric = -1.0
    best_epoch = 0
    bad = 0
    labels_validation = rows_to_tensors(validation_rows, device=device)
    for epoch in range(1, max_epochs + 1):
        rng = __import__("random").Random(seed * 100_000 + epoch)
        model.train()
        for indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
            batch_rows = [train_rows[index] for index in indices]
            labels = rows_to_tensors(batch_rows, device=device)
            state_batch = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
            memory_batch = memory_representations.to(device=device, dtype=torch.float32)
            payload = model(state_batch, memory_batch)
            loss, _ = addressing_losses(payload["q"], labels, AddressingLossWeights())
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
        model.eval()
        with torch.no_grad():
            state_val = _state_reps_for_rows(state_representations, validation_rows, device)
            payload = model(state_val, memory_representations.to(device=device, dtype=torch.float32))
            metric = evaluate_scores(payload["q"], labels_validation)["ndcg@4"]["mean"] or 0.0
        if metric > best_metric + 1.0e-6:
            best_metric = metric
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    checkpoint_path = output_dir / f"current_hard_topk_seed_{seed}.pt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": STAGE_B_4B_VERSION,
            "model_kind": "current_hard_topk_control",
            "seed": seed,
            "best_epoch": best_epoch,
            "model": model.state_dict(),
            "git_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    metrics = evaluate_current_stage_b_model(
        model,
        validation_rows,
        state_representations,
        memory_representations,
        device=device,
        seed=seed,
    )
    metrics["best_epoch"] = best_epoch
    metrics["epochs_ran"] = epoch
    metrics["checkpoint"] = str(checkpoint_path)
    return metrics


def _save_residual_checkpoint(output_dir: Path, name: str, seed: int, run: dict[str, Any]) -> str:
    checkpoint_path = output_dir / f"{name}_seed_{seed}.pt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    state = run.pop("state_dict")
    torch.save(
        {
            "format": STAGE_B_4B_VERSION,
            "model_kind": name,
            "seed": seed,
            "state_dict": state,
            "best_epoch": run.get("best_epoch"),
            "git_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    run["checkpoint"] = str(checkpoint_path)
    return str(checkpoint_path)


def run_scorer_ladder(
    *,
    cfg: RCMFConfig,
    seeds: list[int],
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    mu: torch.Tensor,
    output_dir: Path,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    run_dense: bool,
) -> dict[str, Any]:
    labels_validation = rows_to_tensors(validation_rows, device=device)
    mu_scores = mu.to(device=device, dtype=torch.float32).unsqueeze(0).repeat(len(validation_rows), 1)
    global_prior = {
        "full_score": evaluate_scores(mu_scores, labels_validation),
        "residual_only": evaluate_scores(torch.zeros_like(mu_scores), labels_validation),
        "per_state_full_score": per_state_metric_values(mu_scores, labels_validation),
    }
    model_runs: dict[str, list[dict[str, Any]]] = {
        "state_only_residual_head": [],
        "signed_two_tower_residual": [],
        "current_hard_topk_control": [],
    }
    if run_dense:
        model_runs["dense_separate_head_address"] = []
        model_runs["dense_shared_head_address"] = []
    checkpoints: dict[str, list[str]] = defaultdict(list)
    for seed in seeds:
        state_only = train_residual_scorer(
            model=StateOnlyResidualHead(int(state_representations.shape[1]), int(mu.numel())),
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            seed=seed,
            device=device,
            max_epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
        )
        checkpoints["state_only_residual_head"].append(
            _save_residual_checkpoint(output_dir / "checkpoints", "state_only_residual_head", seed, state_only)
        )
        model_runs["state_only_residual_head"].append(state_only)

        two_tower = train_residual_scorer(
            model=SignedTwoTowerResidualScorer(int(state_representations.shape[1]), int(memory_representations.shape[1])),
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            mu=mu,
            seed=seed,
            device=device,
            max_epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
        )
        checkpoints["signed_two_tower_residual"].append(
            _save_residual_checkpoint(output_dir / "checkpoints", "signed_two_tower_residual", seed, two_tower)
        )
        model_runs["signed_two_tower_residual"].append(two_tower)

        hard = _train_current_control(
            cfg=cfg,
            seed=seed,
            train_rows=train_rows,
            validation_rows=validation_rows,
            state_representations=state_representations,
            memory_representations=memory_representations,
            output_dir=output_dir / "checkpoints",
            device=device,
            max_epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            weight_decay=weight_decay,
            patience=patience,
        )
        checkpoints["current_hard_topk_control"].append(hard["checkpoint"])
        model_runs["current_hard_topk_control"].append(hard)

        if run_dense:
            dense_separate = train_residual_scorer(
                model=DenseResidualAddressScorer(cfg, int(state_representations.shape[1]), shared_head_init=False),
                train_rows=train_rows,
                validation_rows=validation_rows,
                state_representations=state_representations,
                memory_representations=memory_representations,
                mu=mu,
                seed=seed,
                device=device,
                max_epochs=epochs,
                batch_size=batch_size,
                lr=lr,
                weight_decay=weight_decay,
                patience=patience,
            )
            checkpoints["dense_separate_head_address"].append(
                _save_residual_checkpoint(output_dir / "checkpoints", "dense_separate_head_address", seed, dense_separate)
            )
            model_runs["dense_separate_head_address"].append(dense_separate)

            dense_shared = train_residual_scorer(
                model=DenseResidualAddressScorer(cfg, int(state_representations.shape[1]), shared_head_init=True),
                train_rows=train_rows,
                validation_rows=validation_rows,
                state_representations=state_representations,
                memory_representations=memory_representations,
                mu=mu,
                seed=seed,
                device=device,
                max_epochs=epochs,
                batch_size=batch_size,
                lr=lr,
                weight_decay=weight_decay,
                patience=patience,
            )
            checkpoints["dense_shared_head_address"].append(
                _save_residual_checkpoint(output_dir / "checkpoints", "dense_shared_head_address", seed, dense_shared)
            )
            model_runs["dense_shared_head_address"].append(dense_shared)

    aggregate = {
        "global_memory_prior": {
            "full_score": global_prior["full_score"],
            "residual_only": global_prior["residual_only"],
        },
    }
    for name, runs in model_runs.items():
        primary_key = "metrics" if name == "current_hard_topk_control" else "full_score"
        aggregate[name] = summarize_model_runs(runs, primary_key=primary_key)
        aggregate[name]["correct_minus_shuffled"] = _control_deltas(runs, hard_control=name == "current_hard_topk_control")
        aggregate[name]["bootstrap_ci"] = _bootstrap_for_model(name, runs, global_prior)
        if runs and name != "current_hard_topk_control":
            aggregate[name]["residual_only"] = summarize_model_runs(runs, primary_key="residual_only")
    decision = _decision_tree(aggregate, run_dense=run_dense)
    return {
        "format": "stage_b_4b_scorer_ladder_v1",
        "global_prior": global_prior,
        "model_runs": model_runs,
        "aggregate": aggregate,
        "checkpoints": dict(checkpoints),
        "decision_tree": decision,
    }


def _metric_mean(payload: dict[str, Any], metric: str) -> float:
    value = payload.get(metric)
    if isinstance(value, dict) and value.get("mean") is not None:
        return float(value["mean"])
    return 0.0


def _control_deltas(runs: list[dict[str, Any]], *, hard_control: bool) -> dict[str, Any]:
    metrics = ("ndcg@4", "positive_mass_coverage@4", "mrr", "spearman")
    out = {}
    for metric in metrics:
        deltas = []
        for run in runs:
            if hard_control:
                correct = run["metrics"]
                shuffled = run["shuffled"]
            else:
                correct = run["full_score"]
                shuffled = run["controls"]["shuffled_state"]["full_score"]
            deltas.append(_metric_mean(correct, metric) - _metric_mean(shuffled, metric))
        out[metric] = mean_std(deltas)
    return out


def _bootstrap_for_model(name: str, runs: list[dict[str, Any]], global_prior: dict[str, Any]) -> dict[str, Any]:
    if not runs:
        return {}
    output = {}
    for index, run in enumerate(runs):
        if name == "current_hard_topk_control":
            correct_rows = run["per_state"]["rows"]
            shuffled_rows = per_state_metric_values(
                torch.zeros(1, 1),
                {
                    "utility": torch.zeros(1, 1),
                    "valid_mask": torch.zeros(1, 1, dtype=torch.bool),
                    "positive_gain": torch.zeros(1, 1),
                    "no_positive_state": torch.zeros(1, dtype=torch.bool),
                    "all_missing_state": torch.zeros(1, dtype=torch.bool),
                },
            )["rows"]
            del shuffled_rows
            # Hard-control per-state shuffled rows are not stored in compact form.
            continue
        correct_rows = run["per_state_full_score"]["rows"]
        shuffled_rows = run["controls"]["shuffled_state"]["per_state_full_score"]["rows"]
        global_rows = global_prior["per_state_full_score"]["rows"]
        output[f"seed_{index}"] = bootstrap_metric_ci(
            {
                "correct": correct_rows,
                "shuffled": shuffled_rows,
                "global": global_rows,
            }
        )
    return output


def _decision_tree(aggregate: dict[str, Any], *, run_dense: bool) -> dict[str, Any]:
    global_ndcg = _metric_mean(aggregate["global_memory_prior"]["full_score"], "ndcg@4")
    global_mass = _metric_mean(aggregate["global_memory_prior"]["full_score"], "positive_mass_coverage@4")
    state_ndcg = aggregate["state_only_residual_head"]["ndcg@4"]["mean"] or 0.0
    state_mass = aggregate["state_only_residual_head"]["positive_mass_coverage@4"]["mean"] or 0.0
    state_delta = aggregate["state_only_residual_head"]["correct_minus_shuffled"]["ndcg@4"]["mean"] or 0.0
    two_ndcg = aggregate["signed_two_tower_residual"]["ndcg@4"]["mean"] or 0.0
    two_delta = aggregate["signed_two_tower_residual"]["correct_minus_shuffled"]["ndcg@4"]["mean"] or 0.0
    if not (state_ndcg > global_ndcg and state_mass >= global_mass and state_delta > 0.02):
        return {
            "branch": "state_only_failed",
            "conclusion": "current state representation and labels do not generalize enough to beat global prior and shuffled controls",
            "stage_c_allowed": False,
        }
    if not (two_ndcg > global_ndcg and two_delta > 0.02):
        return {
            "branch": "two_tower_failed",
            "conclusion": "memory representation/compiler side is the bottleneck under the signed two-tower diagnostic",
            "stage_c_allowed": False,
        }
    if not run_dense:
        return {
            "branch": "dense_not_run",
            "conclusion": "Parts A-C completed but dense ablation was disabled",
            "stage_c_allowed": False,
        }
    dense_ndcg = aggregate["dense_separate_head_address"]["ndcg@4"]["mean"] or 0.0
    dense_delta = aggregate["dense_separate_head_address"]["correct_minus_shuffled"]["ndcg@4"]["mean"] or 0.0
    if not (dense_ndcg > global_ndcg and dense_delta > 0.02):
        return {
            "branch": "dense_rcmf_address_failed",
            "conclusion": "signed two-tower succeeds but dense RCMF addressing does not, so address parameterization remains the bottleneck",
            "stage_c_allowed": False,
        }
    return {
        "branch": "dense_address_passed",
        "conclusion": "dense addressing beats global prior and degrades under state shuffling; recommend dense warm-up and sparsity annealing next",
        "stage_c_allowed": False,
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
    scorer = summary["scorer_ladder"]
    aggregate = scorer["aggregate"]
    lines = [
        "# Milestone 4B State-Conditioned Addressing Diagnostics",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- labels: `{summary['labels_dir']}`",
        f"- previous pilot: `{summary['previous_pilot_dir']}`",
        f"- hard scope: no Stage C, no program-head training, no injector, no Qwen action loss, no AppWorld evaluation.",
        "",
        "## Forensic Conclusion",
        "",
        f"```json\n{summary['forensics']['conclusion']}\n```",
        "",
        "## Hard Top-K Dead Zone",
        "",
        f"```json\n{summary['forensics']['hard_topk_dead_zone']}\n```",
        "",
        "## Utility Decomposition",
        "",
        f"- memory main-effect variance explained: `{summary['utility_decomposition']['variance']['variance_explained_by_memory_main_effect']:.6f}`",
        f"- train residual variance: `{summary['utility_decomposition']['variance']['train_residual_variance']:.6f}`",
        f"- residual effective rank: `{summary['utility_decomposition']['spectra']['train_residual_imputed_centered']['effective_rank']:.6f}`",
        "",
        "## Scorer Ladder",
        "",
        "| model | NDCG@4 | pos mass@4 | MRR | Spearman | correct-shuffled NDCG@4 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    global_metrics = aggregate["global_memory_prior"]["full_score"]
    lines.append(
        "| global memory prior | "
        f"{_format_metric(global_metrics['ndcg@4'])} | "
        f"{_format_metric(global_metrics['positive_mass_coverage@4'])} | "
        f"{_format_metric(global_metrics['mrr'])} | "
        f"{_format_metric(global_metrics['spearman'])} | `NA` |"
    )
    for name, metrics in aggregate.items():
        if name == "global_memory_prior":
            continue
        lines.append(
            f"| {name} | "
            f"{_format_metric(metrics['ndcg@4'])} | "
            f"{_format_metric(metrics['positive_mass_coverage@4'])} | "
            f"{_format_metric(metrics['mrr'])} | "
            f"{_format_metric(metrics['spearman'])} | "
            f"{_format_metric(metrics['correct_minus_shuffled']['ndcg@4'])} |"
        )
    lines.extend(
        [
            "",
            "## Decision Tree",
            "",
            f"```json\n{scorer['decision_tree']}\n```",
            "",
            "## Checkpoints",
            "",
        ]
    )
    for name, paths in scorer["checkpoints"].items():
        for path in paths:
            lines.append(f"- {name}: `{path}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Milestone 4B addressing diagnostics and lightweight ablations.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--previous-pilot-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--skip-dense", action="store_true")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()
    start = time.perf_counter()
    cfg = load_config(args.config)
    labels_dir = Path(args.labels_dir)
    data_dir = Path(args.data)
    repr_dir = Path(args.representation_cache_dir)
    previous_pilot_dir = Path(args.previous_pilot_dir)
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
        "format": STAGE_B_4B_VERSION,
        "source_commit": maybe_git_commit(),
        "config": args.config,
        "labels_dir": str(labels_dir),
        "data_dir": str(data_dir),
        "representation_cache_dir": str(repr_dir),
        "previous_pilot_dir": str(previous_pilot_dir),
        "output_dir": str(output_dir),
        "seeds": args.seeds,
        "device": str(device),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "effective_memory_count": len(memory_bank),
        "state_cache": state_meta,
        "memory_cache": memory_meta,
    }
    atomic_write_json(output_dir / "run_metadata.json", metadata)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")

    print("running Part A forensics", flush=True)
    forensics = run_forensics(
        cfg=cfg,
        seeds=args.seeds,
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        pilot_dir=previous_pilot_dir,
        device=device,
    )
    atomic_write_json(output_dir / "forensic_diagnostics.json", forensics)

    print("running Part B utility decomposition", flush=True)
    mu = train_memory_prior(train_rows)
    decomp = utility_decomposition(train_rows, validation_rows, mu=mu)
    atomic_write_json(output_dir / "utility_decomposition.json", decomp)

    print("running Parts C-D scorer ladder", flush=True)
    scorer = run_scorer_ladder(
        cfg=cfg,
        seeds=args.seeds,
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        mu=mu,
        output_dir=output_dir,
        device=device,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        run_dense=not args.skip_dense,
    )
    atomic_write_json(output_dir / "scorer_ablation_summary.json", scorer)

    summary = {
        **metadata,
        "runtime_s": time.perf_counter() - start,
        "forensics": forensics,
        "utility_decomposition": decomp,
        "scorer_ladder": scorer,
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", write_report(summary))
    print(f"Wrote Milestone 4B diagnostics to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
