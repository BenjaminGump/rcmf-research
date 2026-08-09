from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from typing import Any

import torch
from torch import Tensor

from rcmf.training.oracle_convergence_5fa import (
    ORACLE_CONVERGENCE_VERSION,
    assess_plateau,
    summarize_convergence_rows,
    update_count_summary,
)
from rcmf.training.pair_grounding_5d import spearman

ORACLE_EXTENSION_VERSION = "stage_c_direct_delta_convergence_5fb_v1"
SOURCE_UPDATES = 64
MINIMUM_TERMINAL_UPDATES = 128
HARD_CAP_UPDATES = 256
CHECKPOINT_INTERVAL = 16
EXPECTED_SOURCE_CHECKPOINT_SHA256 = (
    "26993056d9ac06d6fb43316fdd8ce4cc2557497d994a62500dbc6d16193ea840"
)
EXPECTED_SOURCE_DELTA_SHA256 = "897db72059a5cb5e8a38beb28b618bc3a7906ce6b973e8d601bd685ce8150424"
EXPECTED_STAGE5FA_SOURCE_COMMIT = "451b7a763dd3ca0a08ff7cf430d2d2e5b16396c8"


def tensor_state_sha256(state_dict: Mapping[str, Tensor]) -> str:
    """Hash tensor keys, metadata, and raw bytes without depending on torch.save."""
    digest = hashlib.sha256()
    for name in sorted(state_dict):
        tensor = state_dict[name].detach().cpu().contiguous()
        metadata = {
            "name": name,
            "dtype": str(tensor.dtype),
            "shape": list(tensor.shape),
        }
        digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(tensor.view(torch.uint8).numpy().tobytes(order="C"))
    return digest.hexdigest()


def validate_source_checkpoint_payload(
    payload: Mapping[str, Any],
    *,
    expected_pair_ids: Sequence[str],
    expected_updates: int = SOURCE_UPDATES,
    expected_lr: float = 0.05,
    expected_objective: str = "sequence_utility_plus_sparse_kl",
    expected_ratio_budget: float = 1.0,
    expected_k: int = 4,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    pair_ids = [str(value) for value in payload.get("pair_ids", [])]
    expected_ids = [str(value) for value in expected_pair_ids]
    if payload.get("format") != ORACLE_CONVERGENCE_VERSION:
        errors.append(f"unexpected format {payload.get('format')!r}")
    if pair_ids != expected_ids:
        errors.append("ordered pair IDs do not match the preserved manifest")
    if int(payload.get("completed_rounds", -1)) != int(expected_updates):
        errors.append("completed_rounds is not the expected source update count")

    counts = [int(value) for value in payload.get("update_counts", [])]
    if len(counts) != len(expected_ids):
        errors.append("update-count vector length differs from pair manifest")
    accounting = update_count_summary(pair_ids, counts) if len(counts) == len(pair_ids) else {}
    if accounting and (
        accounting["minimum_updates_per_pair"] != expected_updates
        or accounting["maximum_updates_per_pair"] != expected_updates
        or accounting["mean_updates_per_pair"] != float(expected_updates)
    ):
        errors.append("source per-pair update counts are not uniformly equal to the expected count")

    optimizer = payload.get("optimizer_state_dict") or {}
    optimizer_state = optimizer.get("state") or {}
    parameter_groups = optimizer.get("param_groups") or []
    if not optimizer_state:
        errors.append("optimizer state is absent or empty")
    if len(optimizer_state) != len(expected_ids):
        errors.append("optimizer state count differs from pair count")
    restored_lrs = [float(group.get("lr", math.nan)) for group in parameter_groups]
    if not restored_lrs or any(
        not math.isclose(value, expected_lr, rel_tol=0.0, abs_tol=1.0e-12) for value in restored_lrs
    ):
        errors.append(f"restored optimizer learning rate differs from {expected_lr}")

    metadata = dict(payload.get("metadata") or {})
    expected_metadata = {
        "component": "direct_delta",
        "objective": expected_objective,
        "ratio_budget": float(expected_ratio_budget),
        "k": int(expected_k),
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            errors.append(
                f"source metadata mismatch for {key}: {metadata.get(key)!r} != {expected!r}"
            )
    if [str(value) for value in metadata.get("pair_ids", [])] != expected_ids:
        errors.append("source metadata pair IDs do not match the preserved manifest")
    if (
        expected_source_commit is not None
        and metadata.get("source_commit") != expected_source_commit
    ):
        errors.append(
            f"source commit mismatch: {metadata.get('source_commit')!r} != {expected_source_commit!r}"
        )

    table_state = payload.get("table_state_dict")
    table_hash = (
        tensor_state_sha256(table_state)
        if isinstance(table_state, Mapping) and table_state
        else None
    )
    if table_hash is None:
        errors.append("DeltaE table state is absent")
    return {
        "passed": not errors,
        "errors": errors,
        "pair_count": len(pair_ids),
        "completed_rounds": payload.get("completed_rounds"),
        "update_accounting": accounting,
        "optimizer_state_present": bool(optimizer_state),
        "optimizer_state_count": len(optimizer_state),
        "optimizer_learning_rates": restored_lrs,
        "delta_tensor_sha256": table_hash,
        "legacy_checkpoint_has_embedded_delta_hash": "delta_tensor_sha256" in metadata,
        "metadata": metadata,
    }


def extension_checkpoint_schedule(
    *,
    source_updates: int = SOURCE_UPDATES,
    hard_cap: int = HARD_CAP_UPDATES,
    interval: int = CHECKPOINT_INTERVAL,
) -> list[int]:
    if source_updates % interval or hard_cap % interval or hard_cap <= source_updates:
        raise ValueError("source and hard-cap updates must form a positive fixed-interval schedule")
    return list(range(source_updates + interval, hard_cap + 1, interval))


def delta_tensor_summary(tensor: Tensor) -> dict[str, Any]:
    rows = tensor.detach().to(torch.float32).cpu().flatten(start_dim=1)
    norms = rows.norm(dim=1)
    return {
        "row_count": int(norms.numel()),
        "mean_norm": float(norms.mean()) if norms.numel() else None,
        "max_norm": float(norms.max()) if norms.numel() else None,
        "min_norm": float(norms.min()) if norms.numel() else None,
        "tensor_sha256": tensor_state_sha256({"delta_tensor": tensor}),
    }


def add_selection_category_metrics(
    summary: dict[str, Any], rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    result = dict(summary)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("selection_category"))].append(row)
    result["by_selection_category"] = {
        category: summarize_convergence_rows(bucket) for category, bucket in sorted(grouped.items())
    }
    return result


def metric_reproduction_report(
    *,
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
    tolerance: float,
) -> dict[str, Any]:
    comparisons = []
    for path in paths:
        actual_value: Any = actual
        expected_value: Any = expected
        for key in path:
            actual_value = actual_value[key]
            expected_value = expected_value[key]
        delta = abs(float(actual_value) - float(expected_value))
        comparisons.append(
            {
                "path": ".".join(path),
                "expected": float(expected_value),
                "actual": float(actual_value),
                "absolute_delta": delta,
                "passed": delta <= float(tolerance),
            }
        )
    return {
        "passed": all(item["passed"] for item in comparisons),
        "absolute_tolerance": float(tolerance),
        "maximum_absolute_delta": max(
            (item["absolute_delta"] for item in comparisons), default=0.0
        ),
        "comparisons": comparisons,
    }


def numerical_instability_report(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(history, key=lambda item: int(item["updates_per_pair"]))
    nonfinite = []
    for item in ordered:
        summary = item["evaluation_summary"]
        values = {
            "sequence_utility_huber": summary["sequence_utility_huber"]["mean"],
            "spearman": summary.get("u_text_vs_u_student_spearman"),
        }
        for name, value in values.items():
            if value is None or not math.isfinite(float(value)):
                nonfinite.append(
                    {"updates_per_pair": item["updates_per_pair"], "metric": name, "value": value}
                )
    worsening_windows = []
    for previous, current in pairwise(ordered):
        previous_loss = float(previous["evaluation_summary"]["sequence_utility_huber"]["mean"])
        current_loss = float(current["evaluation_summary"]["sequence_utility_huber"]["mean"])
        relative_worsening = (current_loss - previous_loss) / max(abs(previous_loss), 1.0e-12)
        worsening_windows.append(
            {
                "from_updates": int(previous["updates_per_pair"]),
                "to_updates": int(current["updates_per_pair"]),
                "relative_worsening": relative_worsening,
                "worse_by_more_than_25_percent": relative_worsening > 0.25,
            }
        )
    consecutive = bool(
        len(worsening_windows) >= 2
        and worsening_windows[-1]["worse_by_more_than_25_percent"]
        and worsening_windows[-2]["worse_by_more_than_25_percent"]
    )
    return {
        "unstable": bool(nonfinite) or consecutive,
        "nonfinite_metrics": nonfinite,
        "worsening_windows": worsening_windows,
        "two_consecutive_gt_25_percent": consecutive,
    }


def terminal_decision(
    *,
    final_updates: int,
    plateau: bool,
    gate_passed: bool,
    hard_cap: int = HARD_CAP_UPDATES,
    numerical_instability: bool = False,
) -> dict[str, Any]:
    if numerical_instability:
        branch = "direct_oracle_numerical_instability"
    elif plateau and gate_passed:
        branch = "input_embedding_channel_capacity_passed_after_convergence"
    elif plateau:
        branch = "converged_input_embedding_channel_insufficient"
    elif int(final_updates) >= int(hard_cap):
        branch = "direct_oracle_still_improving_at_hard_cap"
    else:
        branch = "incomplete_before_terminal_condition"
    return {
        "branch": branch,
        "final_updates_per_pair": int(final_updates),
        "plateau_reached": bool(plateau),
        "utility_capacity_gate_passed": bool(gate_passed),
        "hard_cap": int(hard_cap),
        "numerical_instability": bool(numerical_instability),
    }


def _aligned_rows(
    first: Sequence[dict[str, Any]], second: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    second_by_id = {str(row["pair_id"]): row for row in second}
    first_ids = [str(row["pair_id"]) for row in first]
    if set(first_ids) != set(second_by_id) or len(first_ids) != len(second):
        raise ValueError("paired-bootstrap controls do not contain the same pair IDs")
    return list(first), [second_by_id[pair_id] for pair_id in first_ids]


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * float(quantile)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def paired_bootstrap_difference(
    first: Sequence[dict[str, Any]],
    second: Sequence[dict[str, Any]],
    *,
    statistic: Callable[[Sequence[dict[str, Any]]], float],
    samples: int = 5000,
    seed: int = 20260809,
) -> dict[str, Any]:
    left, right = _aligned_rows(first, second)
    if not left:
        raise ValueError("cannot bootstrap an empty row set")
    point = float(statistic(left) - statistic(right))
    rng = random.Random(seed)
    differences = []
    for _ in range(int(samples)):
        indices = [rng.randrange(len(left)) for _ in range(len(left))]
        differences.append(
            float(
                statistic([left[index] for index in indices])
                - statistic([right[index] for index in indices])
            )
        )
    return {
        "orientation": "first_minus_second",
        "point_estimate": point,
        "ci95": [_percentile(differences, 0.025), _percentile(differences, 0.975)],
        "bootstrap_samples": int(samples),
        "seed": int(seed),
        "pair_count": len(left),
    }


def _mean_field(field: str) -> Callable[[Sequence[dict[str, Any]]], float]:
    return lambda rows: sum(float(row[field]) for row in rows) / len(rows)


def _sign_agreement(rows: Sequence[dict[str, Any]]) -> float:
    non_neutral = [row for row in rows if abs(float(row["u_text"])) > 0.01]
    if not non_neutral:
        return math.nan
    return sum(
        (float(row["u_text"]) > 0.0) == (float(row["u_student"]) > 0.0) for row in non_neutral
    ) / len(non_neutral)


def _utility_spearman(rows: Sequence[dict[str, Any]]) -> float:
    value = spearman(
        [float(row["u_text"]) for row in rows],
        [float(row["u_student"]) for row in rows],
    )
    return float(value or 0.0)


def final_control_bootstrap(
    *,
    final_rows: Sequence[dict[str, Any]],
    zero_rows: Sequence[dict[str, Any]],
    random_rows: Sequence[dict[str, Any]],
    u64_rows: Sequence[dict[str, Any]],
    samples: int = 5000,
    seed: int = 20260809,
) -> dict[str, Any]:
    huber = _mean_field("sequence_utility_huber")
    return {
        "final_minus_zero_sequence_huber": paired_bootstrap_difference(
            final_rows, zero_rows, statistic=huber, samples=samples, seed=seed + 1
        ),
        "final_minus_random_sequence_huber": paired_bootstrap_difference(
            final_rows, random_rows, statistic=huber, samples=samples, seed=seed + 2
        ),
        "final_minus_u64_sequence_huber": paired_bootstrap_difference(
            final_rows, u64_rows, statistic=huber, samples=samples, seed=seed + 3
        ),
        "final_minus_zero_sign_agreement": paired_bootstrap_difference(
            final_rows, zero_rows, statistic=_sign_agreement, samples=samples, seed=seed + 4
        ),
        "final_minus_random_utility_spearman": paired_bootstrap_difference(
            final_rows, random_rows, statistic=_utility_spearman, samples=samples, seed=seed + 5
        ),
    }


def eligible_plateau(history: Sequence[dict[str, Any]], *, current_updates: int) -> dict[str, Any]:
    report = assess_plateau(history, current_updates=current_updates, lag=CHECKPOINT_INTERVAL)
    report["minimum_terminal_updates"] = MINIMUM_TERMINAL_UPDATES
    report["eligible_to_stop"] = bool(
        int(current_updates) >= MINIMUM_TERMINAL_UPDATES and report.get("plateau")
    )
    return report
