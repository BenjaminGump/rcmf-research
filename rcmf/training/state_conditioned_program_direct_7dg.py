from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import math
import random
from typing import Any

import torch
from torch import Tensor

from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key


GLOBAL_SEED = 25101
DIRECT_PROGRAM_VERSION = "state_conditioned_program_direct_7dg_v1"


def require_global_seed(seed: int) -> int:
    seed = int(seed)
    if seed != GLOBAL_SEED:
        raise ValueError(f"EXP-025D-Direct requires GLOBAL_SEED={GLOBAL_SEED}, got {seed}")
    return seed


def seed_everything(seed: int = GLOBAL_SEED) -> None:
    seed = require_global_seed(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def task_grouped_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = GLOBAL_SEED,
    validation_fraction: float = 0.20,
) -> dict[str, Any]:
    seed = require_global_seed(seed)
    if not rows:
        raise ValueError("Cannot split an empty pair manifest")
    by_task: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_task[str(row["state_task_id"])].append(index)
    task_order = sorted(
        by_task,
        key=lambda task_id: stable_key(seed, "direct-a-validation-task", task_id),
    )
    target_rows = max(1, round(len(rows) * float(validation_fraction)))
    validation_tasks: list[str] = []
    validation_rows = 0
    for task_id in task_order:
        previous_distance = abs(validation_rows - target_rows)
        proposed = validation_rows + len(by_task[task_id])
        proposed_distance = abs(proposed - target_rows)
        if not validation_tasks or proposed_distance <= previous_distance:
            validation_tasks.append(task_id)
            validation_rows = proposed
        elif validation_rows >= target_rows:
            break
    if validation_rows < target_rows and len(validation_tasks) < len(task_order) - 1:
        selected = set(validation_tasks)
        task_id = next(task for task in task_order if task not in selected)
        validation_tasks.append(task_id)
        validation_rows += len(by_task[task_id])
    validation_task_set = set(validation_tasks)
    validation_indices = [
        index
        for index, row in enumerate(rows)
        if str(row["state_task_id"]) in validation_task_set
    ]
    training_indices = [
        index
        for index, row in enumerate(rows)
        if str(row["state_task_id"]) not in validation_task_set
    ]
    if not training_indices or not validation_indices:
        raise ValueError("Task-grouped A split produced an empty partition")
    train_tasks = sorted(set(by_task) - validation_task_set)
    train_states = {
        str(rows[index]["state_example_id"]) for index in training_indices
    }
    validation_states = {
        str(rows[index]["state_example_id"]) for index in validation_indices
    }
    report = {
        "format": "direct_behavior_task_grouped_split_7dg_v1",
        "global_seed": seed,
        "validation_fraction_target": float(validation_fraction),
        "train_indices": training_indices,
        "validation_indices": validation_indices,
        "train_pair_count": len(training_indices),
        "validation_pair_count": len(validation_indices),
        "train_task_ids": train_tasks,
        "validation_task_ids": sorted(validation_task_set),
        "train_task_count": len(train_tasks),
        "validation_task_count": len(validation_task_set),
        "train_state_count": len(train_states),
        "validation_state_count": len(validation_states),
        "task_overlap": sorted(set(train_tasks) & validation_task_set),
        "state_overlap": sorted(train_states & validation_states),
    }
    report["manifest_sha256"] = canonical_sha256(report)
    if report["task_overlap"] or report["state_overlap"]:
        raise ValueError("Task-grouped A split leaks tasks or states")
    return report


def role_distribution(
    rows: Sequence[Mapping[str, Any]], indices: Sequence[int]
) -> dict[str, int]:
    return dict(
        sorted(Counter(str(rows[index]["pair_role"]) for index in indices).items())
    )


def differentiable_ratio_projection(
    delta: Tensor,
    base_norms: Tensor,
    *,
    maximum_ratio: float = 1.0,
) -> tuple[Tensor, dict[str, Tensor]]:
    if delta.ndim < 2:
        raise ValueError("DeltaE must include a batch dimension")
    norms = delta.to(torch.float32).flatten(start_dim=1).norm(dim=1)
    base = base_norms.to(device=delta.device, dtype=torch.float32).view(-1)
    if len(base) != len(delta):
        raise ValueError("Base-norm count differs from DeltaE batch size")
    allowed = base.clamp_min(1.0e-12) * float(maximum_ratio)
    scales = torch.minimum(torch.ones_like(norms), allowed / norms.clamp_min(1.0e-12))
    projected = delta * scales.to(delta.dtype).view(-1, *([1] * (delta.ndim - 1)))
    projected_norms = projected.to(torch.float32).flatten(start_dim=1).norm(dim=1)
    ratios = projected_norms / base.clamp_min(1.0e-12)
    return projected, {
        "raw_norms": norms,
        "scales": scales,
        "ratios": ratios,
        "maximum_ratio": ratios.max() if ratios.numel() else torch.tensor(0.0),
    }


def continuation_decision(
    u8: Mapping[str, Any], u16: Mapping[str, Any]
) -> dict[str, Any]:
    h8 = float(u8["sequence_utility_huber"]["mean"])
    h16 = float(u16["sequence_utility_huber"]["mean"])
    s8 = float(u8.get("u_text_vs_u_student_spearman") or 0.0)
    s16 = float(u16.get("u_text_vs_u_student_spearman") or 0.0)
    ratio16 = float(u16["delta_ratio"]["max"])
    relative = (h8 - h16) / max(abs(h8), 1.0e-12)
    checks = {
        "validation_huber_improves_at_least_5_percent": relative >= 0.05,
        "validation_spearman_not_materially_worse": s16 >= s8 - 0.02,
        "ratio_lte_1": ratio16 <= 1.0001,
    }
    return {
        "checks": checks,
        "relative_huber_improvement": relative,
        "spearman_change": s16 - s8,
        "select_u16": all(checks.values()),
        "selected_updates_per_pair": 16 if all(checks.values()) else 8,
    }


def _rho(summary: Mapping[str, Any]) -> float:
    return float(summary.get("u_text_vs_u_student_spearman") or 0.0)


def _huber(summary: Mapping[str, Any]) -> float:
    return float(summary["sequence_utility_huber"]["mean"])


def huber_reduction(correct: Mapping[str, Any], zero: Mapping[str, Any]) -> float:
    return 1.0 - _huber(correct) / max(_huber(zero), 1.0e-12)


def pairmlp_behavior_gate(
    cells: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, Any]:
    a = cells["A_validation"]
    b = cells["B"]
    e = cells["E"]
    checks = {
        "A_spearman_gte_0_35": _rho(a["correct"]) >= 0.35,
        "A_huber_reduction_gte_0_20": huber_reduction(a["correct"], a["zero"]) >= 0.20,
        "A_beats_state_shuffle": _huber(a["correct"]) < _huber(a["state_shuffle"]),
        "A_beats_transition_shuffle": _huber(a["correct"]) < _huber(a["transition_shuffle"]),
        "E_spearman_gte_0_20": _rho(e["correct"]) >= 0.20,
        "E_huber_reduction_positive": huber_reduction(e["correct"], e["zero"]) > 0.0,
        "E_beats_state_shuffle": _huber(e["correct"]) < _huber(e["state_shuffle"]),
        "E_beats_transition_shuffle": _huber(e["correct"]) < _huber(e["transition_shuffle"]),
        "B_spearman_positive": _rho(b["correct"]) > 0.0,
        "B_huber_reduction_positive": huber_reduction(b["correct"], b["zero"]) > 0.0,
    }
    return {
        "checks": checks,
        "huber_reduction": {
            cell: huber_reduction(values["correct"], values["zero"])
            for cell, values in cells.items()
        },
        "passed": all(checks.values()),
    }


def factorized_behavior_gate(
    cells: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, Any]:
    a = cells["A_validation"]
    b = cells["B"]
    e = cells["E"]
    checks = {
        "A_spearman_gte_0_30": _rho(a["correct"]) >= 0.30,
        "A_huber_reduction_positive": huber_reduction(a["correct"], a["zero"]) > 0.0,
        "A_beats_static": _huber(a["correct"]) < _huber(a["static_only"]),
        "A_beats_transition_shuffle": _huber(a["correct"]) < _huber(a["transition_shuffle"]),
        "A_beats_memory_swap": _huber(a["correct"]) < _huber(a["memory_swap"]),
        "B_spearman_gte_0_15": _rho(b["correct"]) >= 0.15,
        "B_huber_reduction_positive": huber_reduction(b["correct"], b["zero"]) > 0.0,
        "B_beats_transition_shuffle": _huber(b["correct"]) < _huber(b["transition_shuffle"]),
        "B_beats_memory_swap": _huber(b["correct"]) < _huber(b["memory_swap"]),
        "E_spearman_gte_0_15": _rho(e["correct"]) >= 0.15,
        "E_huber_reduction_positive": huber_reduction(e["correct"], e["zero"]) > 0.0,
        "E_beats_transition_shuffle": _huber(e["correct"]) < _huber(e["transition_shuffle"]),
        "E_beats_memory_swap": _huber(e["correct"]) < _huber(e["memory_swap"]),
        "ratio_lte_1": all(
            float(values["correct"]["delta_ratio"]["max"]) <= 1.0001
            for values in cells.values()
        ),
    }
    return {
        "checks": checks,
        "diagnostic_trends": {
            cell: {
                "spearman": _rho(values["correct"]),
                "huber_reduction": huber_reduction(values["correct"], values["zero"]),
            }
            for cell, values in cells.items()
        },
        "passed": all(checks.values()),
    }


def runtime_projection(
    *,
    train_pairs: int,
    validation_pairs: int,
    evaluation_pairs: int,
    new_teacher_rows: int,
    one_step_conditions: int,
    rates: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        values = rates[name]
        backward_count = 2 * 16 * int(train_pairs)
        evaluation_forward_count = (
            2 * 4 * int(validation_pairs)
            + 4 * int(evaluation_pairs)
            + 2 * 8 * int(validation_pairs)
            + 8 * int(evaluation_pairs)
        )
        teacher_forward_count = int(new_teacher_rows)
        seconds = (
            backward_count * float(values["backward"])
            + evaluation_forward_count * float(values["forward"])
            + teacher_forward_count * float(values["forward"])
            + int(one_step_conditions) * float(values["generation"])
        )
        scenarios[name] = {
            "backward_count_maximum": backward_count,
            "evaluation_forward_count_maximum": evaluation_forward_count,
            "teacher_forward_count": teacher_forward_count,
            "one_step_generation_count": int(one_step_conditions),
            "h100_hours": seconds / 3600.0,
            "seconds": seconds,
        }
    return {
        "format": "direct_behavior_runtime_projection_7dg_v1",
        "scenarios": scenarios,
        "pairmlp_backward_count_u8": int(train_pairs) * 8,
        "pairmlp_backward_count_u16_maximum": int(train_pairs) * 16,
        "factorized_backward_count_u8": int(train_pairs) * 8,
        "factorized_backward_count_u16_maximum": int(train_pairs) * 16,
    }


def effective_rank(values: Tensor) -> float:
    singular = torch.linalg.svdvals(values.to(torch.float64))
    squared = singular.square()
    if not squared.numel() or float(squared.sum()) <= 0.0:
        return 0.0
    probabilities = squared / squared.sum()
    return float(torch.exp(-(probabilities * probabilities.clamp_min(1.0e-12).log()).sum()))


def target_geometry(values: Tensor) -> dict[str, Any]:
    values = values.to(torch.float32)
    norms = values.norm(dim=1)
    normalized = torch.nn.functional.normalize(values, dim=1)
    cosine = normalized @ normalized.T
    cosine.fill_diagonal_(-math.inf)
    nearest = cosine.max(dim=1).values
    quantiles = torch.quantile(
        norms, torch.tensor([0.0, 0.25, 0.5, 0.75, 1.0], device=norms.device)
    )
    return {
        "shape": list(values.shape),
        "norm": {
            "mean": float(norms.mean()),
            "std": float(norms.std(unbiased=False)),
            "min": float(quantiles[0]),
            "q25": float(quantiles[1]),
            "median": float(quantiles[2]),
            "q75": float(quantiles[3]),
            "max": float(quantiles[4]),
        },
        "effective_rank": effective_rank(values),
        "nearest_neighbor_cosine": {
            "mean": float(nearest.mean()),
            "median": float(nearest.median()),
            "minimum": float(nearest.min()),
            "maximum": float(nearest.max()),
        },
    }
