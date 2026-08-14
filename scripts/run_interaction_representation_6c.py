from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.training.interaction_representation_6c import (
    DecomposedInteractionPredictor,
    MainEffectHeads,
    fit_two_way_decomposition,
    interaction_gate,
    majority_sign_baseline,
    paired_task_bootstrap_contrast,
    per_task_gate_metrics,
    predict_decomposed_rows,
    raw_residual_cell_distributions,
    summarize_revised_predictions,
    task_grouped_bootstrap,
    train_decomposed_interaction,
    train_main_effect_heads,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    build_grouped_cv_manifest,
    canonical_json_sha256,
    initialize_or_validate_run_manifest,
    summarize_utility_predictions,
    utc_now,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)


CURRENT_MODEL_KINDS = (
    "decomposed_additive",
    "decomposed_signed_bilinear",
    "decomposed_concat_interaction",
)
INTERACTION_MODEL_KINDS = (
    "decomposed_signed_bilinear",
    "decomposed_concat_interaction",
)
CONTROLS = (
    "correct",
    "shuffled_state",
    "shuffled_transition",
    "both_shuffled",
    "mean_state",
    "mean_transition",
    "zero_interaction",
)
CELLS = (CELL_A, CELL_B, CELL_C, CELL_D)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _numeric_leaves(value: Any, prefix: str = "") -> dict[str, float]:
    output: dict[str, float] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            output.update(_numeric_leaves(item, name))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(float(value)):
            output[prefix] = float(value)
    return output


def _compare_numeric(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    expected_values = _numeric_leaves(expected)
    actual_values = _numeric_leaves(actual)
    common = sorted(set(expected_values).intersection(actual_values))
    differences = {
        key: abs(expected_values[key] - actual_values[key]) for key in common
    }
    maximum_key = max(differences, key=differences.get) if differences else None
    return {
        "compared_numeric_leaf_count": len(common),
        "maximum_absolute_difference": (
            differences[maximum_key] if maximum_key is not None else 0.0
        ),
        "maximum_difference_key": maximum_key,
        "missing_expected_numeric_keys": sorted(set(expected_values) - set(actual_values)),
    }


def _exp018_snapshot(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "run_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
        "parts_a_d_summary.json",
        "parts_a_d_postrun_validation.json",
        "two_axis_split_manifest.json",
        "two_axis_pair_rows.jsonl",
        "representation_cache/query_state_representations.pt",
        "representation_cache/transition_representations.pt",
        "cheap_gate/model_results.json",
        "cheap_gate/cheap_interaction_report.json",
    )
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise FileNotFoundError(f"EXP-018 required files are missing: {missing}")
    validation = _load_json(root / "parts_a_d_postrun_validation.json")
    summary = _load_json(root / "parts_a_d_summary.json")
    if not bool(validation.get("passed")) or validation.get("errors"):
        raise ValueError("EXP-018 independent validation is not clean")
    if str(summary.get("source_commit")) != (
        "0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c"
    ):
        raise ValueError("EXP-018 source commit differs")
    if int(validation.get("pair_count", -1)) != int(expected["scoreable_rows"]):
        raise ValueError("EXP-018 pair count differs")
    if validation.get("cell_counts") != {
        str(key): int(value) for key, value in expected["cells"].items()
    }:
        raise ValueError("EXP-018 cell counts differ")
    attempts = [json.loads(line) for line in (root / "attempts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(attempts) != 2 or [row.get("event") for row in attempts] != ["start", "end"]:
        raise ValueError("EXP-018 attempt ledger is not the expected immutable pair")
    hashes = {name: sha256_file(root / name) for name in required}
    return {
        "format": "immutable_exp018_snapshot_6c_v1",
        "source_commit": str(summary["source_commit"]),
        "final_record_commit": "82cce2f99470a074591372bb2b9aaed8af0cf688",
        "pair_count": int(validation["pair_count"]),
        "cell_counts": validation["cell_counts"],
        "decision_branch": str(validation["decision_branch"]),
        "attempt_events": [row["event"] for row in attempts],
        "hashes": hashes,
    }


def _verify_exp018_unchanged(root: Path, snapshot: Mapping[str, Any]) -> None:
    for name, expected_hash in snapshot["hashes"].items():
        actual = sha256_file(root / str(name))
        if actual != str(expected_hash):
            raise RuntimeError(f"Immutable EXP-018 file changed: {name}")


def _reproduce_exp018_metrics(
    exp018: Path, rows: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    model_results = _load_json(exp018 / "cheap_gate/model_results.json")
    cheap_report = _load_json(exp018 / "cheap_gate/cheap_interaction_report.json")
    reproductions: dict[str, Any] = {}
    selected_rows: dict[str, list[dict[str, Any]]] = {}
    maximum_difference = 0.0
    for kind, model in model_results.items():
        reproductions[kind] = {}
        for cell, cell_result in model["cells"].items():
            reproductions[kind][cell] = {}
            for control, stored in cell_result["controls"].items():
                prediction_path = Path(str(stored["rows_path"]))
                predicted = _load_rows(prediction_path)
                actual = summarize_utility_predictions(
                    predicted, huber_delta=0.1
                )
                comparison = _compare_numeric(stored, actual)
                maximum_difference = max(
                    maximum_difference,
                    float(comparison["maximum_absolute_difference"]),
                )
                reproductions[kind][cell][control] = comparison
                if cell == CELL_D and control == "correct" and kind in {
                    "state_only",
                    "transition_only",
                    "concat_mlp",
                    "signed_bilinear",
                }:
                    selected_rows[kind] = predicted
    global_mean = float(cheap_report["global_train_utility_mean"])
    global_reproductions = {}
    for cell in CELLS:
        predicted = [
            {
                "pair_id": str(row["pair_id"]),
                "state_example_id": str(row["state_example_id"]),
                "state_task_id": str(row["state_task_id"]),
                "transition_id": str(row["transition_id"]),
                "transition_parent_id": str(row["transition_parent_id"]),
                "cell": str(row["cell"]),
                "utility_category": str(row["utility_category"]),
                "u_text": float(row["text_utility"]),
                "u_predicted": global_mean,
                "control": "global_mean",
            }
            for row in rows
            if str(row["cell"]) == cell
        ]
        actual = summarize_utility_predictions(predicted, huber_delta=0.1)
        comparison = _compare_numeric(cheap_report["global_results"][cell], actual)
        maximum_difference = max(
            maximum_difference, float(comparison["maximum_absolute_difference"])
        )
        global_reproductions[cell] = comparison
    return (
        {
            "format": "exp018_cheap_gate_reproduction_6c_v1",
            "models": reproductions,
            "global": global_reproductions,
            "maximum_absolute_difference": maximum_difference,
            "tolerance": 1.0e-10,
            "passed": maximum_difference <= 1.0e-10,
        },
        selected_rows,
    )


def _model_seed(base: int, *parts: Any) -> int:
    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def _new_main_heads(settings: Mapping[str, Any], dimension: int) -> MainEffectHeads:
    return MainEffectHeads(
        state_dim=dimension,
        transition_dim=dimension,
        hidden_dim=int(settings["main_hidden_dim"]),
        dropout=float(settings["main_dropout"]),
    )


def _train_or_load_main_heads(
    *,
    checkpoint: Path,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    seed: int,
    device: torch.device,
    metadata: Mapping[str, Any],
) -> tuple[MainEffectHeads, dict[str, Any], bool]:
    model = _new_main_heads(settings, int(state_representations.shape[-1]))
    expected = {
        **dict(metadata),
        "pair_ids_sha256": sha256_text(
            "\n".join(sorted(str(row["pair_id"]) for row in rows))
        ),
        "decomposition_sha256": canonical_json_sha256(decomposition),
    }
    if checkpoint.exists():
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if payload.get("metadata") != expected:
            raise ValueError(f"Incompatible main-effect checkpoint: {checkpoint}")
        model.load_state_dict(payload["model_state_dict"])
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        return model.to(device).eval(), payload["training"], True
    torch.manual_seed(int(seed))
    training = train_main_effect_heads(
        model=model,
        decomposition=decomposition,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        epochs=int(settings["main_epochs"]),
        learning_rate=float(settings["main_learning_rate"]),
        weight_decay=float(settings["main_weight_decay"]),
        huber_delta=float(settings["huber_delta"]),
        seed=seed,
        device=device,
    )
    optimizer_state = training.pop("optimizer_state_dict")
    payload = {
        "format": "main_effect_heads_checkpoint_6c_v1",
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer_state,
        "training": training,
        "metadata": expected,
    }
    atomic_torch_save(payload, checkpoint)
    return model.to(device).eval(), training, False


def _new_decomposed_model(
    kind: str,
    *,
    main_effects: MainEffectHeads,
    decomposition: Mapping[str, Any],
    settings: Mapping[str, Any],
    dimension: int,
) -> DecomposedInteractionPredictor:
    return DecomposedInteractionPredictor(
        kind,
        main_effects=main_effects,
        mu=float(decomposition["mu"]),
        state_dim=dimension,
        transition_dim=dimension,
        hidden_dim=int(settings["hidden_dim"]),
        interaction_dim=int(settings["interaction_dim"]),
        dropout=float(settings["dropout"]),
    )


def _train_or_load_interaction(
    *,
    checkpoint: Path,
    kind: str,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    main_effects: MainEffectHeads,
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    epochs: int,
    seed: int,
    device: torch.device,
    metadata: Mapping[str, Any],
) -> tuple[DecomposedInteractionPredictor, dict[str, Any], bool]:
    model = _new_decomposed_model(
        kind,
        main_effects=main_effects,
        decomposition=decomposition,
        settings=settings,
        dimension=int(state_representations.shape[-1]),
    )
    expected = {
        **dict(metadata),
        "kind": kind,
        "epochs": int(epochs),
        "pair_ids_sha256": sha256_text(
            "\n".join(sorted(str(row["pair_id"]) for row in rows))
        ),
        "decomposition_sha256": canonical_json_sha256(decomposition),
    }
    if checkpoint.exists():
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if payload.get("metadata") != expected:
            raise ValueError(f"Incompatible interaction checkpoint: {checkpoint}")
        model.load_state_dict(payload["model_state_dict"])
        return model.to(device).eval(), payload["training"], True
    torch.manual_seed(int(seed))
    training = train_decomposed_interaction(
        model=model,
        rows=rows,
        decomposition=decomposition,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        epochs=int(epochs),
        settings=settings,
        seed=seed,
        device=device,
    )
    optimizer_state = training.pop("optimizer_state_dict")
    payload = {
        "format": "decomposed_interaction_checkpoint_6c_v1",
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer_state,
        "training": training,
        "metadata": expected,
    }
    atomic_torch_save(payload, checkpoint)
    return model.to(device).eval(), training, False


def _metric_kwargs(settings: Mapping[str, Any], huber_delta: float) -> dict[str, Any]:
    return {
        "ranking_ks": tuple(int(value) for value in settings["ranking_ks"]),
        "neutral_epsilon": float(settings["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["best_tie_tolerance"]),
        "huber_delta": float(huber_delta),
    }


def _strip_per_state(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "per_state_rows"}


def _enrich_baseline_rows(
    *,
    rows: Sequence[Mapping[str, Any]],
    model: DecomposedInteractionPredictor,
    decomposition: Mapping[str, Any],
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
) -> list[dict[str, Any]]:
    # The additive model computes train-only extrapolated main-effect targets.
    scaffold = predict_decomposed_rows(
        model=model,
        rows=[
            {
                **dict(row),
                "text_utility": float(row["u_text"]),
                "utility_category": str(row["utility_category"]),
            }
            for row in rows
        ],
        decomposition=decomposition,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        device=device,
        control="correct",
    )
    scaffold_by_pair = {str(row["pair_id"]): row for row in scaffold}
    return [
        {
            **dict(row),
            "residual_target": float(
                scaffold_by_pair[str(row["pair_id"])]["residual_target"]
            ),
            "residual_predicted": 0.0,
        }
        for row in rows
    ]


def _cv_parts_ab(
    *,
    rows: Sequence[Mapping[str, Any]],
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    decomposition_settings: Mapping[str, Any],
    model_settings: Mapping[str, Any],
    metric_settings: Mapping[str, Any],
    output_dir: Path,
    common_metadata: Mapping[str, Any],
    attempt: AttemptLedger,
    device: torch.device,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_grouped_cv_manifest(
        rows,
        folds=int(model_settings["folds"]),
        seed=int(model_settings["seed"]),
    )
    atomic_write_json(output_dir / "grouped_cv_manifest.json", manifest)
    row_by_pair = {str(row["pair_id"]): row for row in rows}
    results: dict[str, Any] = {kind: {"candidates": []} for kind in INTERACTION_MODEL_KINDS}
    total_jobs = len(INTERACTION_MODEL_KINDS) * len(model_settings["epoch_candidates"]) * len(manifest["folds"])
    completed = 0
    metric_kwargs = _metric_kwargs(
        metric_settings, float(model_settings["utility_huber_delta"])
    )
    for fold in manifest["folds"]:
        fold_index = int(fold["fold"])
        train_rows = [row_by_pair[value] for value in fold["train_pair_ids"]]
        validation_rows = [
            row_by_pair[value] for value in fold["validation_pair_ids"]
        ]
        decomposition = fit_two_way_decomposition(
            train_rows,
            max_iterations=int(
                decomposition_settings["alternating_least_squares_iterations"]
            ),
            tolerance=float(decomposition_settings["tolerance"]),
        )
        main_checkpoint = output_dir / "checkpoints" / f"fold_{fold_index}" / "main_effects.pt"
        main, _, _ = _train_or_load_main_heads(
            checkpoint=main_checkpoint,
            rows=train_rows,
            decomposition=decomposition,
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_position=state_position,
            transition_position=transition_position,
            settings=decomposition_settings,
            seed=_model_seed(int(model_settings["seed"]), "main", fold_index),
            device=device,
            metadata={**dict(common_metadata), "fold": fold_index},
        )
        for kind in INTERACTION_MODEL_KINDS:
            for epochs in [int(value) for value in model_settings["epoch_candidates"]]:
                checkpoint = output_dir / "checkpoints" / f"fold_{fold_index}" / kind / f"epochs_{epochs}.pt"
                model, training, reused = _train_or_load_interaction(
                    checkpoint=checkpoint,
                    kind=kind,
                    rows=train_rows,
                    decomposition=decomposition,
                    main_effects=main,
                    state_representations=state_representations,
                    transition_representations=transition_representations,
                    state_position=state_position,
                    transition_position=transition_position,
                    settings=model_settings,
                    epochs=epochs,
                    seed=_model_seed(int(model_settings["seed"]), kind, fold_index, epochs),
                    device=device,
                    metadata={**dict(common_metadata), "fold": fold_index},
                )
                predicted = predict_decomposed_rows(
                    model=model,
                    rows=validation_rows,
                    decomposition=decomposition,
                    state_representations=state_representations,
                    transition_representations=transition_representations,
                    state_position=state_position,
                    transition_position=transition_position,
                    device=device,
                )
                metrics = _strip_per_state(
                    summarize_revised_predictions(predicted, **metric_kwargs)
                )
                candidate = next(
                    (
                        item
                        for item in results[kind]["candidates"]
                        if int(item["epochs"]) == epochs
                    ),
                    None,
                )
                if candidate is None:
                    candidate = {"epochs": epochs, "folds": []}
                    results[kind]["candidates"].append(candidate)
                candidate["folds"].append(
                    {
                        "fold": fold_index,
                        "train_count": len(train_rows),
                        "validation_count": len(validation_rows),
                        "metrics": metrics,
                        "checkpoint": str(checkpoint),
                        "checkpoint_sha256": sha256_file(checkpoint),
                        "training": training,
                        "reused": reused,
                    }
                )
                completed += 1
                attempt.progress(
                    status="parts_a_b_grouped_cv",
                    completed_jobs=completed,
                    total_jobs=total_jobs,
                    fold=fold_index,
                    model_kind=kind,
                    epochs=epochs,
                    latest_validated_checkpoint=str(checkpoint),
                )
    for kind in INTERACTION_MODEL_KINDS:
        for candidate in results[kind]["candidates"]:
            folds = candidate["folds"]
            candidate["mean_ndcg@4"] = statistics.fmean(
                float(item["metrics"]["per_state"]["ndcg@4"]["mean"] or 0.0)
                for item in folds
            )
            candidate["mean_per_state_spearman"] = statistics.fmean(
                float(item["metrics"]["per_state"]["spearman"]["mean"] or 0.0)
                for item in folds
            )
            candidate["mean_residual_spearman"] = statistics.fmean(
                float(item["metrics"]["interaction_residual_spearman"] or 0.0)
                for item in folds
            )
            candidate["mean_raw_huber"] = statistics.fmean(
                float(item["metrics"]["raw_huber"]) for item in folds
            )
        selected = max(
            results[kind]["candidates"],
            key=lambda item: (
                float(item["mean_ndcg@4"]),
                float(item["mean_residual_spearman"]),
                float(item["mean_per_state_spearman"]),
                -float(item["mean_raw_huber"]),
                -int(item["epochs"]),
            ),
        )
        results[kind]["selected_epochs"] = int(selected["epochs"])
        results[kind]["selection_rule"] = (
            "max_mean_ndcg4_then_residual_spearman_then_per_state_spearman_"
            "then_negative_huber_then_fewer_epochs"
        )
    atomic_write_json(output_dir / "cv_results.json", results)
    return results, manifest


def _parts_a_b_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-019 Parts A-B: Interaction Residual Objective Repair",
        "",
        f"- status: `{summary['status']}`",
        f"- next phase required: `{summary['next_phase_required']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- EXP-018 reproduction passed: `{summary['exp018_reproduction']['passed']}`",
        "",
        "## Majority-Sign Baselines",
        "",
        "| Cell | Positive | Neutral | Negative | Always-positive accuracy |",
        "|---|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        values = summary["majority_sign_baselines"][cell]
        lines.append(
            f"| {cell} | {values['positive']} | {values['neutral']} | "
            f"{values['negative']} | {float(values['always_positive_accuracy']):.6f} |"
        )
    decomposition = summary["decomposition"]
    lines.extend(
        [
            "",
            "## Cell-A Decomposition",
            "",
            f"- total variance: `{decomposition['total_utility_variance']:.9f}`",
            f"- state-only R2: `{decomposition['state_only_variance_explained_r2']:.6f}`",
            f"- transition-only R2: `{decomposition['transition_only_variance_explained_r2']:.6f}`",
            f"- additive-main R2: `{decomposition['additive_main_effect_variance_explained_r2']:.6f}`",
            f"- residual variance: `{decomposition['residual_interaction_variance']:.9f}`",
            "",
            "## Double-Held-Out Metrics",
            "",
            "Values are pooled Spearman / mean per-state Spearman / NDCG@4 / residual Spearman / Huber.",
            "",
            "| Model | Correct | Shuffled state | Shuffled transition |",
            "|---|---:|---:|---:|",
        ]
    )
    for kind in CURRENT_MODEL_KINDS:
        controls = summary["models"][kind]["cells"][CELL_D]["controls"]

        def value(control: str) -> str:
            metric = controls[control]["metrics"]
            return (
                f"{float(metric['pooled_raw_spearman'] or 0):.6f} / "
                f"{float(metric['per_state']['spearman']['mean'] or 0):.6f} / "
                f"{float(metric['per_state']['ndcg@4']['mean'] or 0):.6f} / "
                f"{float(metric['interaction_residual_spearman'] or 0):.6f} / "
                f"{float(metric['raw_huber']):.6f}"
            )

        lines.append(
            f"| {kind} | {value('correct')} | {value('shuffled_state')} | "
            f"{value('shuffled_transition')} |"
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
            f"- signed bilinear passed: `{summary['part_b_gate']['passed']}`",
            f"- decision after Part B: `{summary['decision_after_part_b']}`",
            "",
            "No Qwen behavioral backpropagation, behavioral program training, injector training, selector training, full field, AppWorld evaluation, Stage C2, end-to-end RCMF, or V4 tag occurred.",
            "",
        ]
    )
    return "\n".join(lines)


def run_parts_a_b(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6c"]
    config_sha = sha256_file(args.config)
    exp018 = Path(settings["exp018_artifact"])
    expected = settings["expected"]
    snapshot = _exp018_snapshot(exp018, expected)
    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    cells = {
        cell: [row for row in pair_rows if str(row["cell"]) == cell]
        for cell in CELLS
    }
    if len(pair_rows) != int(expected["scoreable_rows"]):
        raise ValueError("EXP-019 pair-row count differs")
    for cell, count in expected["cells"].items():
        if len(cells[str(cell)]) != int(count):
            raise ValueError(f"EXP-019 cell count differs for {cell}")

    state_cache_path = exp018 / "representation_cache/query_state_representations.pt"
    transition_cache_path = exp018 / "representation_cache/transition_representations.pt"
    state_cache = torch.load(state_cache_path, map_location="cpu", weights_only=False)
    transition_cache = torch.load(
        transition_cache_path, map_location="cpu", weights_only=False
    )
    state_representations = state_cache["representations"].to(torch.float32)
    transition_representations = transition_cache["representations"].to(torch.float32)
    state_ids = [str(value) for value in state_cache["ordered_state_example_ids"]]
    transition_ids = [
        str(value) for value in transition_cache["ordered_transition_ids"]
    ]
    if tuple(state_representations.shape) != (int(expected["states"]), 4096):
        raise ValueError("State representation shape differs")
    if tuple(transition_representations.shape) != (
        int(expected["transitions"]),
        4096,
    ):
        raise ValueError("Transition representation shape differs")
    if tensor_state_sha256({"representations": state_representations}) != str(
        state_cache["representation_tensor_sha256"]
    ):
        raise ValueError("State representation tensor hash differs")
    if tensor_state_sha256({"representations": transition_representations}) != str(
        transition_cache["representation_tensor_sha256"]
    ):
        raise ValueError("Transition representation tensor hash differs")

    data_hashes = {
        "exp018_run_manifest": snapshot["hashes"]["run_manifest.json"],
        "exp018_summary": snapshot["hashes"]["parts_a_d_summary.json"],
        "exp018_validation": snapshot["hashes"][
            "parts_a_d_postrun_validation.json"
        ],
        "two_axis_manifest": snapshot["hashes"]["two_axis_split_manifest.json"],
        "two_axis_rows": snapshot["hashes"]["two_axis_pair_rows.jsonl"],
        "state_representations": snapshot["hashes"][
            "representation_cache/query_state_representations.pt"
        ],
        "transition_representations": snapshot["hashes"][
            "representation_cache/transition_representations.pt"
        ],
    }
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    run_manifest = initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_sha,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "parts_a_b",
            "conditional_parts_c_d",
            "conditional_part_e",
            "learning_curves",
        ],
    )
    summary_path = args.artifact_dir / "parts_a_b_summary.json"
    if summary_path.exists():
        summary = _load_json(summary_path)
        if summary.get("run_uuid") != settings["run_uuid"]:
            raise ValueError("Existing Part A/B summary run UUID differs")
        _verify_exp018_unchanged(exp018, snapshot)
        print(json.dumps({"reused": True, "summary": str(summary_path), "status": summary["status"]}, sort_keys=True))
        return summary
    existing_attempts = []
    if (args.artifact_dir / "attempts.jsonl").exists():
        existing_attempts = [
            json.loads(line)
            for line in (args.artifact_dir / "attempts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    if any(row.get("attempt_id") == args.attempt_id for row in existing_attempts):
        raise ValueError(f"Attempt ID already exists without a completed summary: {args.attempt_id}")

    started = time.perf_counter()
    command = [
        "scripts/run_interaction_representation_6c.py",
        "--phase",
        "parts_a_b",
        "--config",
        str(args.config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--attempt-id",
        args.attempt_id,
        "--local-head",
        args.local_head,
        "--github-head",
        args.github_head,
        "--lambda-head",
        args.lambda_head,
        "--tmux-session",
        args.tmux_session,
    ]
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="parts_a_b",
        command=command,
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(
            settings["runtime"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
        atomic_write_json(args.artifact_dir / "exp018_immutable_snapshot.json", snapshot)
        attempt.progress(status="reproducing_exp018_metrics")
        reproduction, old_d_rows = _reproduce_exp018_metrics(exp018, pair_rows)
        if not reproduction["passed"]:
            raise RuntimeError(
                f"EXP-018 metric reproduction failed: {reproduction['maximum_absolute_difference']}"
            )
        atomic_write_json(args.artifact_dir / "exp018_metric_reproduction.json", reproduction)

        majority = {
            cell: majority_sign_baseline(
                cells[cell],
                neutral_epsilon=float(settings["metrics"]["neutral_epsilon"]),
            )
            for cell in CELLS
        }
        expected_d = 117 / (117 + 36)
        if abs(float(majority[CELL_D]["always_positive_accuracy"]) - expected_d) > 1.0e-12:
            raise RuntimeError("D majority-sign baseline differs from 117/(117+36)")
        attempt.progress(status="fitting_cell_a_decomposition")
        decomposition = fit_two_way_decomposition(
            cells[CELL_A],
            max_iterations=int(
                settings["decomposition"][
                    "alternating_least_squares_iterations"
                ]
            ),
            tolerance=float(settings["decomposition"]["tolerance"]),
        )
        if not decomposition["converged"]:
            raise RuntimeError("Cell-A decomposition did not converge")
        cell_distributions = raw_residual_cell_distributions(
            rows_by_cell=cells, decomposition=decomposition
        )
        atomic_write_json(args.artifact_dir / "utility_decomposition.json", decomposition)

        state_position = {value: index for index, value in enumerate(state_ids)}
        transition_position = {
            value: index for index, value in enumerate(transition_ids)
        }
        common_metadata = {
            "config_sha256": config_sha,
            "state_representation_sha256": str(
                state_cache["representation_tensor_sha256"]
            ),
            "transition_representation_sha256": str(
                transition_cache["representation_tensor_sha256"]
            ),
            "two_axis_rows_sha256": data_hashes["two_axis_rows"],
        }
        cv_results, cv_manifest = _cv_parts_ab(
            rows=cells[CELL_A],
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_position=state_position,
            transition_position=transition_position,
            decomposition_settings=settings["decomposition"],
            model_settings=settings["current_representation"],
            metric_settings=settings["metrics"],
            output_dir=args.artifact_dir / "parts_a_b" / "cv",
            common_metadata=common_metadata,
            attempt=attempt,
            device=torch.device(args.device),
        )
        full_main_checkpoint = args.artifact_dir / "parts_a_b/checkpoints/main_effects.pt"
        main_effects, main_training, main_reused = _train_or_load_main_heads(
            checkpoint=full_main_checkpoint,
            rows=cells[CELL_A],
            decomposition=decomposition,
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_position=state_position,
            transition_position=transition_position,
            settings=settings["decomposition"],
            seed=_model_seed(
                int(settings["current_representation"]["seed"]), "main", "full"
            ),
            device=torch.device(args.device),
            metadata={**common_metadata, "fold": "all_cell_a"},
        )
        models: dict[str, Any] = {}
        model_objects: dict[str, DecomposedInteractionPredictor] = {}
        metric_kwargs = _metric_kwargs(
            settings["metrics"],
            float(settings["current_representation"]["utility_huber_delta"]),
        )
        bootstrap_settings = {
            **dict(settings["metrics"]),
            "huber_delta": float(
                settings["current_representation"]["utility_huber_delta"]
            ),
        }
        prediction_root = args.artifact_dir / "parts_a_b/predictions"
        for kind in CURRENT_MODEL_KINDS:
            epochs = (
                0
                if kind == "decomposed_additive"
                else int(cv_results[kind]["selected_epochs"])
            )
            checkpoint = args.artifact_dir / "parts_a_b/checkpoints" / f"{kind}.pt"
            model, training, reused = _train_or_load_interaction(
                checkpoint=checkpoint,
                kind=kind,
                rows=cells[CELL_A],
                decomposition=decomposition,
                main_effects=main_effects,
                state_representations=state_representations,
                transition_representations=transition_representations,
                state_position=state_position,
                transition_position=transition_position,
                settings=settings["current_representation"],
                epochs=epochs,
                seed=_model_seed(
                    int(settings["current_representation"]["seed"]),
                    kind,
                    "full",
                    epochs,
                ),
                device=torch.device(args.device),
                metadata={**common_metadata, "fold": "all_cell_a"},
            )
            model_objects[kind] = model
            result = {
                "epochs": epochs,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
                "training": training,
                "reused": reused,
                "cells": {},
            }
            for cell in CELLS:
                control_results = {}
                for control_index, control in enumerate(CONTROLS):
                    predicted = predict_decomposed_rows(
                        model=model,
                        rows=cells[cell],
                        decomposition=decomposition,
                        state_representations=state_representations,
                        transition_representations=transition_representations,
                        state_position=state_position,
                        transition_position=transition_position,
                        device=torch.device(args.device),
                        control=control,
                        seed=int(settings["current_representation"]["seed"])
                        + control_index,
                    )
                    path = prediction_root / kind / cell / f"{control}.jsonl"
                    write_jsonl(path, predicted)
                    metrics = summarize_revised_predictions(predicted, **metric_kwargs)
                    control_result = {
                        "metrics": _strip_per_state(metrics),
                        "rows_path": str(path),
                        "rows_sha256": sha256_file(path),
                    }
                    if control == "correct" or cell == CELL_D:
                        control_result["task_grouped_bootstrap_ci95"] = (
                            task_grouped_bootstrap(
                                predicted,
                                samples=int(settings["metrics"]["bootstrap_samples"]),
                                seed=int(settings["metrics"]["bootstrap_seed"])
                                + control_index,
                                metric_settings=bootstrap_settings,
                            )
                        )
                    control_results[control] = control_result
                result["cells"][cell] = {"controls": control_results}
                attempt.progress(
                    status="evaluating_parts_a_b",
                    model_kind=kind,
                    cell=cell,
                    latest_validated_checkpoint=str(checkpoint),
                )
            models[kind] = result
            atomic_write_json(args.artifact_dir / "parts_a_b/model_results.json", models)

        additive = model_objects["decomposed_additive"]
        baseline_results: dict[str, Any] = {}
        baseline_rows: dict[str, list[dict[str, Any]]] = {}
        for kind in ("state_only", "transition_only"):
            enriched = _enrich_baseline_rows(
                rows=old_d_rows[kind],
                model=additive,
                decomposition=decomposition,
                state_representations=state_representations,
                transition_representations=transition_representations,
                state_position=state_position,
                transition_position=transition_position,
                device=torch.device(args.device),
            )
            baseline_rows[kind] = enriched
            baseline_results[kind] = {
                "metrics": _strip_per_state(
                    summarize_revised_predictions(enriched, **metric_kwargs)
                ),
                "task_grouped_bootstrap_ci95": task_grouped_bootstrap(
                    enriched,
                    samples=int(settings["metrics"]["bootstrap_samples"]),
                    seed=int(settings["metrics"]["bootstrap_seed"])
                    + (101 if kind == "state_only" else 102),
                    metric_settings=bootstrap_settings,
                ),
            }

        gates: dict[str, Any] = {}
        contrasts: dict[str, Any] = {}
        per_task_results: dict[str, Any] = {}
        for kind in INTERACTION_MODEL_KINDS:
            control_rows = {
                control: _load_rows(
                    Path(
                        models[kind]["cells"][CELL_D]["controls"][control][
                            "rows_path"
                        ]
                    )
                )
                for control in CONTROLS
            }
            contrasts[kind] = {
                control: paired_task_bootstrap_contrast(
                    control_rows["correct"],
                    control_rows[control],
                    samples=int(settings["metrics"]["bootstrap_samples"]),
                    seed=int(settings["metrics"]["bootstrap_seed"])
                    + index
                    + (1000 if kind == "decomposed_concat_interaction" else 0),
                    metric_settings=bootstrap_settings,
                )
                for index, control in enumerate(
                    ("shuffled_state", "shuffled_transition", "both_shuffled"),
                    start=1,
                )
            }
            per_task = per_task_gate_metrics(
                correct_rows=control_rows["correct"],
                state_only_rows=baseline_rows["state_only"],
                transition_only_rows=baseline_rows["transition_only"],
                shuffled_state_rows=control_rows["shuffled_state"],
                shuffled_transition_rows=control_rows["shuffled_transition"],
                metric_settings=bootstrap_settings,
            )
            per_task_results[kind] = per_task
            gates[kind] = interaction_gate(
                candidate=models[kind]["cells"][CELL_D]["controls"]["correct"][
                    "metrics"
                ],
                state_only=baseline_results["state_only"]["metrics"],
                transition_only=baseline_results["transition_only"]["metrics"],
                shuffled_state=models[kind]["cells"][CELL_D]["controls"][
                    "shuffled_state"
                ]["metrics"],
                shuffled_transition=models[kind]["cells"][CELL_D]["controls"][
                    "shuffled_transition"
                ]["metrics"],
                per_task=per_task,
                transition_shuffle_contrast=contrasts[kind][
                    "shuffled_transition"
                ],
                thresholds=settings["interaction_gate"],
            )
        part_b_gate = gates["decomposed_signed_bilinear"]
        decision = (
            "objective_main_effect_shortcut_repaired"
            if part_b_gate["passed"]
            else "continue_to_multiview_parts_c_d"
        )
        status = (
            "stopped_after_current_representation_repair_pass"
            if part_b_gate["passed"]
            else "parts_a_b_completed_multiview_required"
        )

        # Add the train-only main-head residual distributions for all cells.
        predicted_residual_distributions = {}
        for cell in CELLS:
            additive_rows = _load_rows(
                Path(
                    models["decomposed_additive"]["cells"][cell]["controls"][
                        "correct"
                    ]["rows_path"]
                )
            )
            predicted_residual_distributions[cell] = {
                "raw": raw_residual_cell_distributions(
                    rows_by_cell={cell: cells[cell]}, decomposition=decomposition
                )[cell]["raw"],
                "residual_with_train_only_main_head_extrapolation": {
                    "count": len(additive_rows),
                    "mean": statistics.fmean(
                        float(row["residual_target"]) for row in additive_rows
                    ),
                    "std": statistics.pstdev(
                        float(row["residual_target"]) for row in additive_rows
                    ),
                    "min": min(float(row["residual_target"]) for row in additive_rows),
                    "max": max(float(row["residual_target"]) for row in additive_rows),
                },
            }
        _verify_exp018_unchanged(exp018, snapshot)
        summary = {
            "format": "interaction_representation_parts_a_b_summary_6c_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "timestamp_utc": utc_now(),
            "status": status,
            "decision_after_part_b": decision,
            "next_phase_required": not bool(part_b_gate["passed"]),
            "run_manifest": run_manifest,
            "exp018_immutable_snapshot": snapshot,
            "exp018_reproduction": reproduction,
            "majority_sign_baselines": majority,
            "decomposition": decomposition,
            "raw_and_residual_cell_distributions": cell_distributions,
            "train_only_main_head_residual_distributions": predicted_residual_distributions,
            "cv_manifest": cv_manifest,
            "cv_results": cv_results,
            "main_effect_training": main_training,
            "main_effect_checkpoint": str(full_main_checkpoint),
            "main_effect_checkpoint_sha256": sha256_file(full_main_checkpoint),
            "main_effect_checkpoint_reused": main_reused,
            "models": models,
            "single_axis_baselines": baseline_results,
            "paired_bootstrap_contrasts": contrasts,
            "per_heldout_task": per_task_results,
            "candidate_gates": gates,
            "part_b_gate": part_b_gate,
            "runtime_seconds": time.perf_counter() - started,
            "hard_scope": {
                "qwen_frozen": True,
                "qwen_forward_run": False,
                "qwen_behavioral_backpropagation_run": False,
                "behavioral_program_trained": False,
                "injector_trained": False,
                "selector_modified": False,
                "production_field_constructed": False,
                "appworld_generation_or_evaluation_run": False,
                "stage_c2_started": False,
                "end_to_end_rcmf_started": False,
                "full_demo_examples_changed": False,
                "v4_tag_created_or_moved": False,
            },
        }
        atomic_write_json(summary_path, summary)
        atomic_write_text(
            args.artifact_dir / "parts_a_b_report.md", _parts_a_b_report(summary)
        )
        attempt.progress(
            status=status,
            decision_after_part_b=decision,
            latest_validated_checkpoint=str(summary_path),
        )
    print(
        json.dumps(
            {
                "reused": False,
                "summary": str(summary_path),
                "status": summary["status"],
                "decision": summary["decision_after_part_b"],
            },
            sort_keys=True,
        )
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the conditional EXP-019 representation-repair phases."
    )
    parser.add_argument("--phase", choices=("parts_a_b",), default="parts_a_b")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp019")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    parser.add_argument(
        "--device", default="cuda" if torch.cuda.is_available() else "cpu"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.phase == "parts_a_b":
        run_parts_a_b(args)
        return
    raise ValueError(f"Unsupported phase: {args.phase}")


if __name__ == "__main__":
    main()
