from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import os
from pathlib import Path
import random
import tempfile
from typing import Any, Sequence

import torch
from torch import Tensor, nn

from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.pair_grounding_5d import POSITIVE_UTILITY_EPS, spearman


ORACLE_CONVERGENCE_VERSION = "stage_c_direct_delta_convergence_5fa_v1"
CONVERGENCE_SUBSET_VERSION = "stage_c_direct_delta_convergence_subset_5fa_v1"
UPDATE_ACCOUNTING_VERSION = "stage_c_pair_update_accounting_5fa_v1"


@dataclass(frozen=True)
class ConvergenceObjective:
    name: str
    target_delta_weight: float = 0.0
    sequence_utility_weight: float = 0.0
    sparse_teacher_kl_weight: float = 0.0
    huber_delta: float = 0.1


OBJECTIVES_5FA: dict[str, ConvergenceObjective] = {
    "target_delta_huber": ConvergenceObjective(
        name="target_delta_huber",
        target_delta_weight=1.0,
    ),
    "target_delta_plus_sparse_kl": ConvergenceObjective(
        name="target_delta_plus_sparse_kl",
        target_delta_weight=1.0,
        sparse_teacher_kl_weight=0.2,
    ),
    "sequence_utility_huber": ConvergenceObjective(
        name="sequence_utility_huber",
        sequence_utility_weight=1.0,
    ),
    "sequence_utility_plus_sparse_kl": ConvergenceObjective(
        name="sequence_utility_plus_sparse_kl",
        sequence_utility_weight=1.0,
        sparse_teacher_kl_weight=0.05,
    ),
}


class IndependentPairTensorTable(nn.Module):
    """One optimizer-visible parameter per pair, preventing Adam state bleed."""

    def __init__(
        self,
        pair_ids: Sequence[str],
        row_shape: Sequence[int],
        *,
        init_std: float = 0.0,
        dtype: torch.dtype = torch.float32,
    ) -> None:
        super().__init__()
        normalized = [str(pair_id) for pair_id in pair_ids]
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate pair IDs in independent table")
        if not normalized:
            raise ValueError("independent table requires at least one pair")
        self.pair_ids = tuple(normalized)
        self.pair_to_index = {pair_id: index for index, pair_id in enumerate(normalized)}
        parameters = []
        for _ in normalized:
            value = torch.empty(tuple(int(size) for size in row_shape), dtype=dtype)
            if init_std == 0.0:
                nn.init.zeros_(value)
            else:
                nn.init.normal_(value, mean=0.0, std=float(init_std))
            parameters.append(nn.Parameter(value))
        self.rows = nn.ParameterList(parameters)

    def forward_indices(self, indices: Sequence[int]) -> Tensor:
        if not indices:
            raise ValueError("cannot select an empty independent-table batch")
        return torch.stack([self.rows[int(index)] for index in indices], dim=0)

    def forward_pair_ids(self, pair_ids: Sequence[str]) -> Tensor:
        return self.forward_indices([self.pair_to_index[str(pair_id)] for pair_id in pair_ids])

    def stacked(self) -> Tensor:
        return torch.stack(list(self.rows), dim=0)


def custom_huber(error: Tensor, *, delta: float) -> Tensor:
    abs_error = error.abs()
    threshold = torch.as_tensor(float(delta), device=error.device, dtype=error.dtype)
    return torch.where(
        abs_error <= threshold,
        0.5 * error.pow(2) / threshold.clamp_min(1.0e-12),
        abs_error - 0.5 * threshold,
    )


def sequence_utility_from_nll(*, baseline_nll: Tensor, student_nll: Tensor) -> Tensor:
    return baseline_nll - student_nll


def sequence_utility_loss(
    *,
    baseline_nll: Tensor,
    student_nll: Tensor,
    teacher_utility: Tensor,
    huber_delta: float,
) -> dict[str, Tensor]:
    student_utility = sequence_utility_from_nll(
        baseline_nll=baseline_nll,
        student_nll=student_nll,
    )
    error = student_utility - teacher_utility
    return {
        "student_utility": student_utility,
        "sequence_utility_error": error,
        "sequence_utility_huber": custom_huber(error, delta=huber_delta).mean(),
        "sequence_utility_mae": error.abs().mean(),
        "sequence_utility_mse": error.pow(2).mean(),
    }


def objective_loss(
    *,
    target_delta_huber: Tensor,
    sequence_utility_huber: Tensor,
    sparse_teacher_kl: Tensor,
    objective: ConvergenceObjective,
) -> Tensor:
    return (
        float(objective.target_delta_weight) * target_delta_huber
        + float(objective.sequence_utility_weight) * sequence_utility_huber
        + float(objective.sparse_teacher_kl_weight) * sparse_teacher_kl
    )


def apply_independent_optimizer_step(
    *,
    optimizer: torch.optim.Optimizer,
    loss: Tensor,
    table: IndependentPairTensorTable,
    selected_indices: Sequence[int],
    update_counts: list[int],
    base_norms: Tensor | None = None,
    ratio_budget: float | None = None,
    shared_parameters: Sequence[nn.Parameter] = (),
    max_grad_norm: float | None = None,
) -> float:
    selected = [int(index) for index in selected_indices]
    if len(selected) != len(set(selected)):
        raise ValueError("a pair may appear only once in an optimizer batch")
    if len(update_counts) != len(table.rows):
        raise ValueError("update count length does not match table")
    if (base_norms is None) != (ratio_budget is None):
        raise ValueError("base_norms and ratio_budget must be supplied together")

    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    active_parameters = [table.rows[index] for index in selected] + list(shared_parameters)
    grads = [parameter.grad.detach().to(torch.float32).flatten() for parameter in active_parameters if parameter.grad is not None]
    if grads:
        grad_norm = float(torch.cat(grads).norm().detach().cpu())
    else:
        grad_norm = 0.0
    if max_grad_norm is not None:
        torch.nn.utils.clip_grad_norm_(active_parameters, float(max_grad_norm))
    optimizer.step()

    if base_norms is not None and ratio_budget is not None:
        with torch.no_grad():
            for index in selected:
                parameter = table.rows[index]
                norm = parameter.to(torch.float32).norm().clamp_min(1.0e-8)
                maximum = base_norms[index].to(device=parameter.device, dtype=torch.float32) * float(ratio_budget)
                scale = torch.minimum(torch.ones_like(norm), maximum / norm)
                parameter.mul_(scale.to(parameter.dtype))
    for index in selected:
        update_counts[index] += 1
    return grad_norm


def update_count_summary(pair_ids: Sequence[str], update_counts: Sequence[int]) -> dict[str, Any]:
    if len(pair_ids) != len(update_counts):
        raise ValueError("pair IDs and update counts differ in length")
    counts = [int(value) for value in update_counts]
    return {
        "format": UPDATE_ACCOUNTING_VERSION,
        "pair_count": len(counts),
        "updates_per_pair": {str(pair_id): count for pair_id, count in zip(pair_ids, counts)},
        "minimum_updates_per_pair": min(counts) if counts else None,
        "maximum_updates_per_pair": max(counts) if counts else None,
        "mean_updates_per_pair": (sum(counts) / len(counts)) if counts else None,
        "all_pairs_equal": len(set(counts)) <= 1,
        "total_pair_updates": sum(counts),
    }


def atomic_torch_save(payload: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        torch.save(payload, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def save_training_checkpoint(
    path: str | Path,
    *,
    table: IndependentPairTensorTable,
    optimizer: torch.optim.Optimizer,
    update_counts: Sequence[int],
    completed_rounds: int,
    metadata: dict[str, Any],
    shared_module: nn.Module | None = None,
) -> None:
    payload = {
        "format": ORACLE_CONVERGENCE_VERSION,
        "component": metadata.get("component"),
        "pair_ids": list(table.pair_ids),
        "table_state_dict": {key: value.detach().cpu() for key, value in table.state_dict().items()},
        "optimizer_state_dict": optimizer.state_dict(),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(table.pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "metadata": metadata,
    }
    if shared_module is not None:
        payload["shared_module_state_dict"] = {
            key: value.detach().cpu() for key, value in shared_module.state_dict().items()
        }
    atomic_torch_save(payload, path)


def load_training_checkpoint(
    path: str | Path,
    *,
    table: IndependentPairTensorTable,
    optimizer: torch.optim.Optimizer,
    shared_module: nn.Module | None = None,
) -> dict[str, Any]:
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if payload.get("format") != ORACLE_CONVERGENCE_VERSION:
        raise ValueError(f"unexpected checkpoint format: {payload.get('format')}")
    if list(payload.get("pair_ids", [])) != list(table.pair_ids):
        raise ValueError("checkpoint pair IDs do not match the current table")
    table.load_state_dict(payload["table_state_dict"])
    if shared_module is not None:
        state = payload.get("shared_module_state_dict")
        if state is None:
            raise ValueError("checkpoint has no shared module state")
        shared_module.load_state_dict(state)
    optimizer.load_state_dict(payload["optimizer_state_dict"])
    counts = [int(value) for value in payload["update_counts"]]
    if len(counts) != len(table.rows):
        raise ValueError("checkpoint update counts do not match table")
    return {
        "update_counts": counts,
        "completed_rounds": int(payload["completed_rounds"]),
        "metadata": dict(payload.get("metadata", {})),
        "update_accounting": update_count_summary(table.pair_ids, counts),
    }


def select_convergence_subset(
    rows: Sequence[dict[str, Any]],
    *,
    target_total: int = 64,
    seed: int = 20260808,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    categories = ("positive", "neutral", "negative", "random")
    if target_total % len(categories) != 0:
        raise ValueError("target_total must be divisible by four")
    per_category = target_total // len(categories)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for category in categories:
        bucket = [row for row in rows if str(row.get("selection_category")) == category]
        bucket.sort(key=lambda row: (int(row["memory_stage_index"]), str(row["pair_id"])))
        if len(bucket) < per_category:
            raise ValueError(f"category {category} has only {len(bucket)} rows; need {per_category}")
        by_category[category] = bucket

    rng = random.Random(seed)
    all_memories = sorted({int(row["memory_stage_index"]) for row in rows})
    rng.shuffle(all_memories)
    selected: dict[str, list[dict[str, Any]]] = {category: [] for category in categories}
    selected_ids: set[str] = set()
    covered_memories: set[int] = set()

    # Cover constrained memories first while balancing the four category quotas.
    memory_options: dict[int, list[str]] = {}
    for memory_index in all_memories:
        memory_options[memory_index] = [
            category
            for category in categories
            if any(int(row["memory_stage_index"]) == memory_index for row in by_category[category])
        ]
    ordered_memories = sorted(all_memories, key=lambda value: (len(memory_options[value]), all_memories.index(value)))
    for memory_index in ordered_memories:
        choices = [category for category in memory_options[memory_index] if len(selected[category]) < per_category]
        if not choices:
            continue
        minimum = min(len(selected[category]) for category in choices)
        choices = [category for category in choices if len(selected[category]) == minimum]
        category = choices[(memory_index + seed) % len(choices)]
        candidates = [
            row
            for row in by_category[category]
            if int(row["memory_stage_index"]) == memory_index and str(row["pair_id"]) not in selected_ids
        ]
        if not candidates:
            continue
        chosen = candidates[(memory_index + seed) % len(candidates)]
        selected[category].append(chosen)
        selected_ids.add(str(chosen["pair_id"]))
        covered_memories.add(memory_index)

    for category_index, category in enumerate(categories):
        candidates = [row for row in by_category[category] if str(row["pair_id"]) not in selected_ids]
        category_rng = random.Random(seed + 1009 * (category_index + 1))
        category_rng.shuffle(candidates)
        for row in candidates:
            if len(selected[category]) >= per_category:
                break
            selected[category].append(row)
            selected_ids.add(str(row["pair_id"]))
            covered_memories.add(int(row["memory_stage_index"]))

    flat = [row for category in categories for row in selected[category]]
    if len(flat) != target_total:
        raise ValueError(f"selected {len(flat)} rows; expected {target_total}")
    flat.sort(key=lambda row: (categories.index(str(row["selection_category"])), int(row["memory_stage_index"]), str(row["pair_id"])))
    report = {
        "format": CONVERGENCE_SUBSET_VERSION,
        "seed": seed,
        "source_pair_count": len(rows),
        "target_total": target_total,
        "selected_total": len(flat),
        "requested_per_category": per_category,
        "selected_by_category": dict(Counter(str(row["selection_category"]) for row in flat)),
        "available_memory_count": len(set(int(row["memory_stage_index"]) for row in rows)),
        "covered_memory_count": len(covered_memories),
        "covered_memory_indices": sorted(covered_memories),
        "all_available_memories_covered": covered_memories == set(all_memories),
        "pair_ids": [str(row["pair_id"]) for row in flat],
    }
    return flat, report


def enrich_sequence_utility_rows(rows: Sequence[dict[str, Any]], *, huber_delta: float) -> list[dict[str, Any]]:
    enriched = []
    for source in rows:
        row = dict(source)
        error = float(row["u_student"]) - float(row["u_text"])
        error_tensor = torch.tensor(error, dtype=torch.float32)
        row["sequence_utility_error"] = error
        row["sequence_utility_mae"] = abs(error)
        row["sequence_utility_mse"] = error * error
        row["sequence_utility_huber"] = float(custom_huber(error_tensor, delta=huber_delta).item())
        enriched.append(row)
    return enriched


def _utility_category(value: float) -> str:
    if value > POSITIVE_UTILITY_EPS:
        return "positive"
    if value < -POSITIVE_UTILITY_EPS:
        return "negative"
    return "neutral"


def summarize_convergence_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    u_text = [float(row["u_text"]) for row in rows]
    u_student = [float(row["u_student"]) for row in rows]
    non_neutral = [row for row in rows if _utility_category(float(row["u_text"])) != "neutral"]
    sign_agreement = [
        float((float(row["u_text"]) > 0.0) == (float(row["u_student"]) > 0.0))
        for row in non_neutral
    ]
    target_teacher = [float(value) for row in rows for value in row.get("target_delta_teacher", [])]
    target_student = [float(value) for row in rows for value in row.get("target_delta_student", [])]
    by_utility: dict[str, dict[str, Any]] = {}
    for category in ("positive", "neutral", "negative"):
        bucket = [row for row in rows if _utility_category(float(row["u_text"])) == category]
        by_utility[category] = {
            "count": len(bucket),
            "u_text": mean_std(float(row["u_text"]) for row in bucket),
            "u_student": mean_std(float(row["u_student"]) for row in bucket),
            "mean_abs_u_student": (
                sum(abs(float(row["u_student"])) for row in bucket) / len(bucket) if bucket else None
            ),
            "positive_student_fraction": (
                sum(float(row["u_student"]) > 0.0 for row in bucket) / len(bucket) if bucket else None
            ),
            "negative_student_fraction": (
                sum(float(row["u_student"]) < 0.0 for row in bucket) / len(bucket) if bucket else None
            ),
        }
    ratios = [float(row["delta_ratio"]) for row in rows if row.get("delta_ratio") is not None]
    return {
        "count": len(rows),
        "u_text": mean_std(u_text),
        "u_student": mean_std(u_student),
        "u_text_vs_u_student_pearson": _pearson(u_text, u_student),
        "u_text_vs_u_student_spearman": spearman(u_text, u_student),
        "positive_negative_sign_agreement": sum(sign_agreement) / len(sign_agreement) if sign_agreement else None,
        "sequence_utility_mae": mean_std(float(row["sequence_utility_mae"]) for row in rows),
        "sequence_utility_mse": mean_std(float(row["sequence_utility_mse"]) for row in rows),
        "sequence_utility_huber": mean_std(float(row["sequence_utility_huber"]) for row in rows),
        "target_nll": mean_std(float(row["student_target_nll"]) for row in rows),
        "target_token_delta_huber": mean_std(float(row["target_token_delta_huber"]) for row in rows),
        "target_token_delta_mse": mean_std(float(row["target_token_delta_mse"]) for row in rows),
        "target_token_delta_correlation_global": _pearson(target_teacher, target_student),
        "sparse_teacher_kl": mean_std(float(row["sparse_teacher_kl"]) for row in rows),
        "delta_ratio": {
            **mean_std(ratios),
            "min": min(ratios) if ratios else None,
            "max": max(ratios) if ratios else None,
        },
        "by_utility_category": by_utility,
        "selection_category_counts": dict(Counter(str(row.get("selection_category")) for row in rows)),
    }


def assess_plateau(
    checkpoints: Sequence[dict[str, Any]],
    *,
    current_updates: int,
    lag: int = 16,
) -> dict[str, Any]:
    current = next((item for item in checkpoints if int(item["updates_per_pair"]) == int(current_updates)), None)
    previous = next((item for item in checkpoints if int(item["updates_per_pair"]) == int(current_updates) - int(lag)), None)
    if current is None or previous is None or current_updates < 32:
        return {
            "assessable": False,
            "plateau": False,
            "current_updates": int(current_updates),
            "previous_updates": int(current_updates) - int(lag),
        }
    current_pair_ids = current.get("pair_ids")
    previous_pair_ids = previous.get("pair_ids")
    if current_pair_ids is not None or previous_pair_ids is not None:
        if list(current_pair_ids or []) != list(previous_pair_ids or []):
            raise ValueError("convergence checkpoints were evaluated on different pair subsets")
    current_summary = current["evaluation_summary"]
    previous_summary = previous["evaluation_summary"]
    current_loss = float(current_summary["sequence_utility_huber"]["mean"])
    previous_loss = float(previous_summary["sequence_utility_huber"]["mean"])
    relative_loss_improvement = (previous_loss - current_loss) / max(abs(previous_loss), 1.0e-12)
    current_spearman = float(current_summary.get("u_text_vs_u_student_spearman") or 0.0)
    previous_spearman = float(previous_summary.get("u_text_vs_u_student_spearman") or 0.0)
    spearman_improvement = current_spearman - previous_spearman
    plateau = relative_loss_improvement < 0.01 and spearman_improvement < 0.01
    return {
        "assessable": True,
        "plateau": plateau,
        "current_updates": int(current_updates),
        "previous_updates": int(current_updates) - int(lag),
        "relative_sequence_utility_loss_improvement": relative_loss_improvement,
        "absolute_spearman_improvement": spearman_improvement,
        "criteria": {
            "relative_loss_improvement_lt": 0.01,
            "absolute_spearman_improvement_lt": 0.01,
            "minimum_updates": 32,
        },
    }


def utility_capacity_gate(
    *,
    summary: dict[str, Any],
    zero_summary: dict[str, Any],
    plateau: bool,
) -> dict[str, Any]:
    trained_huber = float(summary["sequence_utility_huber"]["mean"])
    zero_huber = float(zero_summary["sequence_utility_huber"]["mean"])
    reduction = 1.0 - trained_huber / max(zero_huber, 1.0e-12)
    by_category = summary["by_utility_category"]
    checks = {
        "spearman_gte_0_80": float(summary.get("u_text_vs_u_student_spearman") or -1.0) >= 0.80,
        "sign_agreement_gte_0_85": float(summary.get("positive_negative_sign_agreement") or 0.0) >= 0.85,
        "sequence_huber_reduction_gte_0_50": reduction >= 0.50,
        "positive_mean_gt_zero": float(by_category["positive"]["u_student"]["mean"] or 0.0) > 0.0,
        "negative_mean_lt_zero": float(by_category["negative"]["u_student"]["mean"] or 0.0) < 0.0,
        "neutral_mean_abs_lte_0_05": float(by_category["neutral"]["mean_abs_u_student"] or math.inf) <= 0.05,
        "ratio_lte_1_0": float(summary["delta_ratio"]["max"] or math.inf) <= 1.0001,
        "documented_plateau": bool(plateau),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "sequence_utility_huber_reduction_vs_zero": reduction,
        "trained_sequence_utility_huber": trained_huber,
        "zero_sequence_utility_huber": zero_huber,
    }


def choose_pilot_objective(runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        raise ValueError("no pilot objective runs supplied")

    def key(item: tuple[str, dict[str, Any]]) -> tuple[float, ...]:
        run = item[1]
        summary = run["final_evaluation"]["summary"]
        gate = run["utility_capacity_gate"]
        return (
            float(bool(gate["passed"])),
            float(bool(run["convergence"]["plateau"])),
            float(summary.get("u_text_vs_u_student_spearman") or -999.0),
            float(summary.get("positive_negative_sign_agreement") or -999.0),
            float(gate["sequence_utility_huber_reduction_vs_zero"]),
            -float(summary["sequence_utility_huber"]["mean"]),
        )

    name, run = max(runs.items(), key=key)
    return {
        "selection_rule": (
            "lexicographic: utility gate, documented plateau, utility Spearman, sign agreement, "
            "Huber reduction versus zero, negative utility Huber"
        ),
        "selected_objective": name,
        "selected_updates_per_pair": int(run["updates_per_pair"]),
        "selection_key": list(key((name, run))),
        "selected_from_pair_ids": list(run["pair_ids"]),
    }
