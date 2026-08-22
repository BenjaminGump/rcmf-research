from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import math
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.training.deep_residual_carrier_7e import layer_and_global_ratios


GLOBAL_SEED = 25101
PROGRAM_DIM = 256
K_TOKENS = 4
LAYER_INDICES = (7, 14, 21, 28)
COMPILER_VERSION = "deep_residual_amortized_compiler_7f_v1"


class SharedDeepResidualDecoder(nn.Module):
    """Shared no-bias map from a compact program to the locked residual carrier."""

    def __init__(
        self,
        *,
        program_dim: int = PROGRAM_DIM,
        layer_count: int = len(LAYER_INDICES),
        token_count: int = K_TOKENS,
        model_dim: int = 4096,
    ) -> None:
        super().__init__()
        self.program_dim = int(program_dim)
        self.layer_count = int(layer_count)
        self.token_count = int(token_count)
        self.model_dim = int(model_dim)
        self.linear = nn.Linear(
            self.program_dim,
            self.layer_count * self.token_count * self.model_dim,
            bias=False,
        )

    def forward(self, values: Tensor) -> Tensor:
        decoded = self.linear(values)
        return decoded.view(
            len(values), self.layer_count, self.token_count, self.model_dim
        )


def differentiable_layer_ratio_projection(
    delta: Tensor,
    original_states: Tensor,
    *,
    maximum_ratio: float = 1.0,
) -> tuple[Tensor, dict[str, Tensor]]:
    """Project each layer independently while retaining gradients through DeltaH."""

    if delta.shape != original_states.shape:
        raise ValueError("DeltaH and original residual states must have matching shapes")
    if delta.ndim != 4:
        raise ValueError("Deep residual tensors must have shape [batch, layer, token, hidden]")
    delta32 = delta.to(torch.float32)
    base32 = original_states.to(device=delta.device, dtype=torch.float32)
    raw_norm = delta32.flatten(start_dim=2).norm(dim=2)
    base_norm = base32.flatten(start_dim=2).norm(dim=2).clamp_min(1.0e-12)
    raw_ratio = raw_norm / base_norm
    scale = torch.minimum(
        torch.ones_like(raw_ratio),
        float(maximum_ratio) / raw_ratio.clamp_min(1.0e-12),
    )
    projected = delta * scale[..., None, None].to(delta.dtype)
    layer_ratio, global_ratio = layer_and_global_ratios(projected, base32)
    return projected, {
        "raw_layer_ratio": raw_ratio,
        "layer_ratio": layer_ratio,
        "global_ratio": global_ratio,
        "maximum_ratio": layer_ratio.max(),
    }


def best_visited_checkpoint(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply the locked A-validation-only checkpoint rule."""

    eligible = []
    for row in history:
        huber = float(row["a_validation_huber"])
        spearman = float(row["a_validation_spearman"])
        ratio = float(row["maximum_ratio"])
        if (
            math.isfinite(huber)
            and math.isfinite(spearman)
            and math.isfinite(ratio)
            and spearman > 0.0
            and ratio <= 1.0001
        ):
            eligible.append(row)
    if not eligible:
        raise ValueError("No visited checkpoint satisfies the preregistered constraints")
    selected = min(
        eligible,
        key=lambda row: (
            float(row["a_validation_huber"]),
            -float(row["a_validation_spearman"]),
            int(row["updates_per_pair"]),
        ),
    )
    return dict(selected)


def continue_after_u8(
    u8: Mapping[str, Any],
    *,
    minimum_huber_improvement: float = 0.03,
    minimum_spearman_improvement: float = 0.02,
    maximum_huber_deterioration: float = 0.05,
) -> dict[str, Any]:
    """Decide u8 -> u16 using only the earlier u4 A-validation checkpoint."""

    previous = u8.get("previous")
    if not isinstance(previous, Mapping):
        raise ValueError("u8 continuation row must embed the preceding u4 metrics")
    before_huber = float(previous["a_validation_huber"])
    after_huber = float(u8["a_validation_huber"])
    huber_improvement = (before_huber - after_huber) / max(abs(before_huber), 1.0e-12)
    spearman_improvement = float(u8["a_validation_spearman"]) - float(
        previous["a_validation_spearman"]
    )
    ratio_ok = float(u8["maximum_ratio"]) <= 1.0001
    finite = all(
        math.isfinite(value)
        for value in (
            before_huber,
            after_huber,
            spearman_improvement,
            float(u8["maximum_ratio"]),
        )
    )
    continue_training = bool(
        finite
        and ratio_ok
        and (
            huber_improvement >= float(minimum_huber_improvement)
            or (
                spearman_improvement >= float(minimum_spearman_improvement)
                and huber_improvement >= -float(maximum_huber_deterioration)
            )
        )
    )
    return {
        "huber_relative_improvement": huber_improvement,
        "spearman_improvement": spearman_improvement,
        "ratio_ok": ratio_ok,
        "finite": finite,
        "continue_to_u16": continue_training,
    }


def aggregate_and_select_class(
    transition_scores: Sequence[float],
    transition_class_ids: Sequence[str],
    *,
    legal_transition_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
) -> dict[str, Any]:
    """Select by class-mean score without rewarding duplicate frequency."""

    if len(transition_scores) != len(ordered_transition_ids):
        raise ValueError("Score and transition ledgers differ")
    if len(transition_class_ids) != len(ordered_transition_ids):
        raise ValueError("Class and transition ledgers differ")
    legal = set(str(value) for value in legal_transition_ids)
    grouped: dict[str, list[tuple[str, float]]] = {}
    for transition_id, class_id, score in zip(
        ordered_transition_ids,
        transition_class_ids,
        transition_scores,
        strict=True,
    ):
        if str(transition_id) in legal:
            grouped.setdefault(str(class_id), []).append((str(transition_id), float(score)))
    if not grouped:
        raise ValueError("No legal transition class is available")
    class_scores = {
        class_id: sum(value for _, value in rows) / len(rows)
        for class_id, rows in grouped.items()
    }
    selected_class = min(
        class_scores,
        key=lambda class_id: (
            -class_scores[class_id],
            hashlib.sha256(class_id.encode("utf-8")).hexdigest(),
        ),
    )
    return {
        "selected_class_id": selected_class,
        "class_score": class_scores[selected_class],
        "legal_member_transition_ids": sorted(value for value, _ in grouped[selected_class]),
        "class_scores": class_scores,
    }


def classify_one_step_behavior(
    *,
    p1_minus_c0: Mapping[str, float],
    p1_minus_p2: Mapping[str, float],
    p1_minus_p3: Mapping[str, float],
    execution_drop: float,
    positive_task_count: int,
    material: float = 0.05,
) -> dict[str, Any]:
    """Classify PairMLP/factorized behavior with the EXP-027A three-band rule."""

    primary = ("action_signature", "semantic_successor")
    improves_bare = any(float(p1_minus_c0[name]) > 0.0 for name in primary)
    beats_both_on_one = any(
        float(p1_minus_p2[name]) >= material and float(p1_minus_p3[name]) >= material
        for name in primary
    )
    other_not_materially_worse = any(
        float(p1_minus_p2[name]) >= -material
        and float(p1_minus_p3[name]) >= -material
        for name in primary
    )
    execution_ok = float(execution_drop) <= material
    strong = bool(
        improves_bare
        and beats_both_on_one
        and other_not_materially_worse
        and execution_ok
        and int(positive_task_count) >= 5
    )
    memory_specific = any(
        float(p1_minus_p2[name]) > 0.0 or float(p1_minus_p3[name]) > 0.0
        for name in primary
    )
    if strong:
        classification = "STRONG_POSITIVE"
    elif improves_bare and memory_specific and execution_ok:
        classification = "PARTIAL_POSITIVE"
    else:
        classification = "CLEAR_FAILURE"
    return {
        "classification": classification,
        "checks": {
            "improves_bare": improves_bare,
            "beats_both_shuffles_on_one_primary_metric": beats_both_on_one,
            "other_primary_metric_not_materially_worse": other_not_materially_worse,
            "execution_drop_lte_5pp": execution_ok,
            "positive_tasks_gte_5": int(positive_task_count) >= 5,
            "positive_memory_specific_gap": memory_specific,
        },
    }

