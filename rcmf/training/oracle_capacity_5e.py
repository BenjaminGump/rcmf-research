from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import random
from typing import Any, Sequence

import torch
from torch import Tensor, nn

from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.pair_grounding_5d import POSITIVE_UTILITY_EPS, spearman


ORACLE_CAPACITY_VERSION = "stage_c_oracle_capacity_5e_v1"
ORACLE_SUBSET_VERSION = "stage_c_oracle_pair_subset_5e_v1"
TARGET_TOKEN_UTILITY_IDENTITY_VERSION = "target_token_teacher_delta_identity_5e_v1"


@dataclass(frozen=True)
class ObjectiveSpec:
    name: str
    target_delta_weight: float
    sparse_delta_weight: float
    sparse_teacher_kl_weight: float
    huber_delta: float = 0.1


OBJECTIVES: dict[str, ObjectiveSpec] = {
    "sparse_delta_huber": ObjectiveSpec(
        name="sparse_delta_huber",
        target_delta_weight=0.0,
        sparse_delta_weight=1.0,
        sparse_teacher_kl_weight=0.0,
    ),
    "target_delta_huber": ObjectiveSpec(
        name="target_delta_huber",
        target_delta_weight=1.0,
        sparse_delta_weight=0.0,
        sparse_teacher_kl_weight=0.0,
    ),
    "target_delta_plus_sparse_kl": ObjectiveSpec(
        name="target_delta_plus_sparse_kl",
        target_delta_weight=1.0,
        sparse_delta_weight=0.0,
        sparse_teacher_kl_weight=0.2,
    ),
}


def target_token_teacher_deltas(row: dict[str, Any]) -> list[float]:
    return [
        float(item["teacher_target_logprob"]) - float(item["baseline_target_logprob"])
        for item in row["target_positions"]
    ]


def mean_target_token_teacher_delta(row: dict[str, Any]) -> float:
    values = target_token_teacher_deltas(row)
    if not values:
        raise ValueError(f"pair row has no target positions: {row.get('pair_id')}")
    return sum(values) / len(values)


def validate_target_token_utility_identity(
    rows: Sequence[dict[str, Any]],
    *,
    atol: float = 5.0e-5,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    max_abs_error = 0.0
    for row in rows:
        mean_delta = mean_target_token_teacher_delta(row)
        utility = float(row["text_utility"])
        error = abs(mean_delta - utility)
        max_abs_error = max(max_abs_error, error)
        if error > atol:
            errors.append(
                {
                    "pair_id": row.get("pair_id"),
                    "mean_target_delta": mean_delta,
                    "text_utility": utility,
                    "abs_error": error,
                }
            )
    return {
        "format": TARGET_TOKEN_UTILITY_IDENTITY_VERSION,
        "pair_count": len(rows),
        "atol": atol,
        "max_abs_error": max_abs_error,
        "passed": not errors,
        "errors_first_20": errors[:20],
        "error_count": len(errors),
    }


def select_balanced_validation_subset(
    rows: Sequence[dict[str, Any]],
    *,
    target_total: int = 192,
    seed: int = 20260808,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if target_total % 4 != 0:
        raise ValueError("target_total must be divisible by four")
    validation_rows = [row for row in rows if str(row.get("split")) == "validation"]
    categories = ("positive", "neutral", "negative", "random")
    per_category = target_total // len(categories)
    selected: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {
        "format": ORACLE_SUBSET_VERSION,
        "target_total": target_total,
        "seed": seed,
        "requested_per_category": per_category,
        "available_by_category": {},
        "selected_by_category": {},
    }
    for category in categories:
        bucket = [row for row in validation_rows if str(row.get("selection_category")) == category]
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in bucket:
            grouped[int(row["memory_stage_index"])].append(row)
        for items in grouped.values():
            items.sort(key=lambda item: (str(item["state_example_id"]), str(item["pair_id"])))
        memory_indices = sorted(grouped)
        rng = random.Random(seed + 17 * categories.index(category))
        memory_order = memory_indices[:]
        rng.shuffle(memory_order)
        chosen: list[dict[str, Any]] = []
        round_index = 0
        while len(chosen) < per_category:
            progressed = False
            for memory_index in memory_order:
                items = grouped[memory_index]
                if round_index < len(items):
                    chosen.append(items[round_index])
                    progressed = True
                    if len(chosen) >= per_category:
                        break
            if not progressed:
                break
            round_index += 1
        coverage["available_by_category"][category] = len(bucket)
        coverage["selected_by_category"][category] = len(chosen)
        selected.extend(chosen)
    selected = sorted(selected, key=lambda row: (str(row["selection_category"]), int(row["memory_stage_index"]), str(row["pair_id"])))
    memories = sorted({int(row["memory_stage_index"]) for row in selected})
    coverage["selected_total"] = len(selected)
    coverage["unique_memory_count"] = len(memories)
    coverage["memory_stage_indices"] = memories
    coverage["balanced"] = len(set(coverage["selected_by_category"].values())) == 1
    coverage["passed_min_count"] = len(selected) >= target_total
    if len(selected) < target_total:
        raise ValueError(f"Only selected {len(selected)} validation pairs; requested {target_total}")
    return selected, coverage


def select_last_user_k_indices(
    *,
    input_len: int,
    last_user_token_indices: Sequence[int] | None,
    labels: Sequence[int] | None,
    k: int,
) -> list[int]:
    if k <= 0:
        raise ValueError("k must be positive")
    prompt_indices: list[int]
    if labels is not None:
        prompt_indices = [index for index, label in enumerate(labels) if int(label) == -100]
    else:
        prompt_indices = list(range(input_len))
    prompt_set = set(prompt_indices)
    candidates = [
        int(index)
        for index in (last_user_token_indices or [])
        if 0 <= int(index) < input_len and int(index) in prompt_set
    ]
    if not candidates:
        candidates = prompt_indices
    selected = candidates[-k:]
    return selected + [-1] * max(0, k - len(selected))


def scatter_token_delta(
    *,
    base_embeddings: Tensor,
    selected_indices: Tensor,
    delta_slots: Tensor,
) -> Tensor:
    if base_embeddings.dim() != 3:
        raise ValueError("base_embeddings must have shape [batch, seq, dim]")
    if selected_indices.shape[:2] != delta_slots.shape[:2]:
        raise ValueError("selected_indices and delta_slots must agree on [batch, k]")
    if delta_slots.shape[0] != base_embeddings.shape[0] or delta_slots.shape[-1] != base_embeddings.shape[-1]:
        raise ValueError("delta_slots shape is incompatible with base_embeddings")
    out = torch.zeros_like(base_embeddings)
    for row_index in range(base_embeddings.shape[0]):
        for slot_index, token_index in enumerate(selected_indices[row_index].tolist()):
            if int(token_index) < 0:
                continue
            out[row_index, int(token_index), :] += delta_slots[row_index, slot_index].to(out.dtype)
    return out


def perturbation_ratios(
    *,
    delta_slots: Tensor,
    selected_base_embeddings: Tensor,
    eps: float = 1.0e-8,
) -> Tensor:
    if delta_slots.shape != selected_base_embeddings.shape:
        raise ValueError("delta_slots and selected_base_embeddings must have identical shape")
    delta_norm = delta_slots.to(torch.float32).flatten(start_dim=1).norm(dim=1)
    base_norm = selected_base_embeddings.to(torch.float32).flatten(start_dim=1).norm(dim=1).clamp_min(eps)
    return delta_norm / base_norm


def project_delta_slots_to_ratio_(
    delta_slots: Tensor,
    selected_base_norms: Tensor,
    *,
    max_ratio: float,
    row_indices: Tensor | None = None,
    eps: float = 1.0e-8,
) -> None:
    if row_indices is None:
        current = delta_slots
        base = selected_base_norms.to(device=current.device, dtype=torch.float32)
    else:
        current = delta_slots.index_select(0, row_indices)
        base = selected_base_norms.to(device=delta_slots.device, dtype=torch.float32).index_select(0, row_indices)
    flat = current.data.to(torch.float32).flatten(start_dim=1)
    norms = flat.norm(dim=1).clamp_min(eps)
    max_norms = base * float(max_ratio)
    scales = torch.minimum(torch.ones_like(norms), max_norms / norms)
    projected = (flat * scales.view(-1, 1)).view_as(current).to(delta_slots.dtype)
    if row_indices is None:
        delta_slots.data.copy_(projected)
    else:
        delta_slots.data.index_copy_(0, row_indices, projected)


class FreePairLatentTable(nn.Module):
    def __init__(self, pair_ids: Sequence[str], latent_dim: int, *, init_std: float = 0.0) -> None:
        super().__init__()
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("duplicate pair_ids in FreePairLatentTable")
        self.pair_to_index = {str(pair_id): index for index, pair_id in enumerate(pair_ids)}
        self.latents = nn.Parameter(torch.empty(len(pair_ids), latent_dim))
        if init_std == 0.0:
            nn.init.zeros_(self.latents)
        else:
            nn.init.normal_(self.latents, mean=0.0, std=init_std)

    def forward(self, rows: Sequence[dict[str, Any]]) -> Tensor:
        indices = []
        for row in rows:
            pair_id = str(row["pair_id"])
            if pair_id not in self.pair_to_index:
                raise KeyError(f"unknown pair id: {pair_id}")
            indices.append(self.pair_to_index[pair_id])
        index_tensor = torch.tensor(indices, dtype=torch.long, device=self.latents.device)
        return self.latents.index_select(0, index_tensor)


class FreeMemoryLatentTable(nn.Module):
    def __init__(self, memory_stage_indices: Sequence[int], latent_dim: int, *, init_std: float = 0.0) -> None:
        super().__init__()
        unique = sorted({int(index) for index in memory_stage_indices})
        self.memory_to_index = {memory_index: offset for offset, memory_index in enumerate(unique)}
        self.latents = nn.Parameter(torch.empty(len(unique), latent_dim))
        if init_std == 0.0:
            nn.init.zeros_(self.latents)
        else:
            nn.init.normal_(self.latents, mean=0.0, std=init_std)

    def forward(self, rows: Sequence[dict[str, Any]]) -> Tensor:
        indices = []
        for row in rows:
            memory_index = int(row["memory_stage_index"])
            if memory_index not in self.memory_to_index:
                raise KeyError(f"unknown memory stage index: {memory_index}")
            indices.append(self.memory_to_index[memory_index])
        index_tensor = torch.tensor(indices, dtype=torch.long, device=self.latents.device)
        return self.latents.index_select(0, index_tensor)


def summarize_oracle_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    u_text = [float(row["u_text"]) for row in rows]
    u_student = [float(row["u_student"]) for row in rows]
    non_neutral = [
        row
        for row in rows
        if abs(float(row["u_text"])) > POSITIVE_UTILITY_EPS
    ]
    sign_agreement = [
        1.0 if math.copysign(1.0, float(row["u_text"])) == math.copysign(1.0, float(row["u_student"])) else 0.0
        for row in non_neutral
    ]
    direction = [
        1.0 if float(row["u_text"]) * float(row["u_student"]) > 0.0 else 0.0
        for row in non_neutral
    ]
    target_teacher_all: list[float] = []
    target_student_all: list[float] = []
    for row in rows:
        target_teacher_all.extend(float(value) for value in row.get("target_delta_teacher", []))
        target_student_all.extend(float(value) for value in row.get("target_delta_student", []))
    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({str(row.get("selection_category")) for row in rows}):
        bucket = [row for row in rows if str(row.get("selection_category")) == category]
        by_category[category] = {
            "count": len(bucket),
            "u_text": mean_std(float(row["u_text"]) for row in bucket),
            "u_student": mean_std(float(row["u_student"]) for row in bucket),
            "target_nll": mean_std(float(row["student_target_nll"]) for row in bucket),
        }
    return {
        "count": len(rows),
        "u_text": mean_std(u_text),
        "u_student": mean_std(u_student),
        "u_text_vs_u_student_pearson": _pearson(u_text, u_student),
        "u_text_vs_u_student_spearman": spearman(u_text, u_student),
        "positive_negative_sign_agreement": None if not sign_agreement else sum(sign_agreement) / len(sign_agreement),
        "teacher_utility_direction_reproduced_fraction": None if not direction else sum(direction) / len(direction),
        "target_token_delta_huber": mean_std(float(row["target_token_delta_huber"]) for row in rows),
        "target_token_delta_mse": mean_std(float(row["target_token_delta_mse"]) for row in rows),
        "target_token_delta_correlation_global": _pearson(target_teacher_all, target_student_all),
        "target_token_delta_correlation_row_mean": mean_std(
            float(row["target_token_delta_pearson"])
            for row in rows
            if row.get("target_token_delta_pearson") is not None
        ),
        "target_nll": mean_std(float(row["student_target_nll"]) for row in rows),
        "sparse_teacher_kl": mean_std(float(row["sparse_teacher_kl"]) for row in rows),
        "sparse_delta_huber": mean_std(float(row["sparse_delta_huber"]) for row in rows),
        "sparse_delta_mse": mean_std(float(row["sparse_delta_mse"]) for row in rows),
        "delta_ratio": mean_std(float(row["delta_ratio"]) for row in rows if row.get("delta_ratio") is not None),
        "by_selection_category": by_category,
        "selection_category_counts": dict(Counter(str(row.get("selection_category")) for row in rows)),
    }


def stage_5e_decision(
    *,
    direct_summary: dict[str, Any],
    pair_z_summary: dict[str, Any] | None,
    memory_z_summary: dict[str, Any] | None,
    content_reference_summary: dict[str, Any] | None,
    objective_ablation: dict[str, Any],
) -> dict[str, Any]:
    direct_pass = bool(
        (direct_summary.get("u_text_vs_u_student_spearman") or -1.0) >= 0.70
        and (direct_summary.get("positive_negative_sign_agreement") or 0.0) >= 0.80
        and (direct_summary.get("target_token_delta_correlation_global") or -1.0) >= 0.80
        and ((direct_summary.get("delta_ratio") or {}).get("max") or 999.0) <= 2.0001
    )
    pair_pass = bool(
        pair_z_summary
        and (pair_z_summary.get("u_text_vs_u_student_spearman") or -1.0) >= 0.60
        and (pair_z_summary.get("positive_negative_sign_agreement") or 0.0) >= 0.75
    )
    memory_pass = bool(
        memory_z_summary
        and (memory_z_summary.get("u_text_vs_u_student_spearman") or -1.0) > 0.0
        and (memory_z_summary.get("positive_negative_sign_agreement") or 0.0) > 0.55
    )
    content_spearman = None
    if content_reference_summary:
        content_spearman = content_reference_summary.get("u_text_vs_u_program_spearman")
        if isinstance(content_spearman, dict):
            content_spearman = content_spearman.get("mean")
    old_sparse = objective_ablation.get("sparse_delta_huber", {})
    target_delta = objective_ablation.get("target_delta_huber", {})
    target_objective_better = bool(
        (target_delta.get("u_text_vs_u_student_spearman") or -1.0)
        > (old_sparse.get("u_text_vs_u_student_spearman") or -1.0) + 0.10
    )
    if not direct_pass:
        branch = "direct_delta_fails"
        bottleneck = "additive_token_injection_location_bandwidth_or_behavioral_target"
    elif not pair_pass:
        branch = "direct_delta_succeeds_but_pair_z_fails"
        bottleneck = "latent_128_or_injector_mlp_capacity"
    elif not memory_pass:
        branch = "pair_z_succeeds_but_memory_z_fails"
        bottleneck = "fixed_per_memory_program_expressivity_or_missing_state_program_interaction"
    elif content_spearman is not None and content_spearman < 0.30:
        branch = "memory_z_succeeds_but_content_program_fails"
        bottleneck = "memory_representation_or_program_head_compiler"
    else:
        branch = "injection_channel_and_fixed_memory_latent_succeed"
        bottleneck = "not_isolated"
    return {
        "format": "stage_c_oracle_capacity_decision_5e_v1",
        "direct_delta_capacity_gate_passed": direct_pass,
        "pair_latent_injector_gate_passed": pair_pass,
        "fixed_memory_latent_gate_passed": memory_pass,
        "target_delta_objective_better_than_sparse_delta": target_objective_better,
        "branch": branch,
        "identified_bottleneck": bottleneck,
        "stage_c2_allowed": False,
    }
