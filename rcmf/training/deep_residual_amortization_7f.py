from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
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


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def deterministic_mismatch_indices(
    entity_ids: Sequence[str],
    row_keys: Sequence[str],
    *,
    namespace: str,
    seed: int = GLOBAL_SEED,
) -> list[int]:
    """Choose deterministic control rows whose entity identity truly differs."""

    if len(entity_ids) != len(row_keys):
        raise ValueError("Entity IDs and row keys must have matching lengths")
    output = []
    for index, entity_id in enumerate(entity_ids):
        candidates = [
            candidate
            for candidate, other_id in enumerate(entity_ids)
            if str(other_id) != str(entity_id)
        ]
        if not candidates:
            raise ValueError(f"{namespace} control has no mismatched entity")
        output.append(
            min(
                candidates,
                key=lambda candidate: hashlib.sha256(
                    (
                        f"{seed}:{namespace}:{row_keys[index]}:"
                        f"{row_keys[candidate]}:{entity_ids[candidate]}"
                    ).encode("utf-8")
                ).hexdigest(),
            )
        )
    return output


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


def revised_u16_runtime_authorization(
    *,
    phase_a_actual_h100_hours: float,
    pairmlp_elapsed_through_u8_hours: float,
    fixed_final_evaluation_hours: float,
    phase_c_one_step_hours: float,
    review_threshold_h100_hours: float,
) -> dict[str, Any]:
    """Conservatively authorize u16 from measured u8 throughput."""

    values = (
        phase_a_actual_h100_hours,
        pairmlp_elapsed_through_u8_hours,
        fixed_final_evaluation_hours,
        phase_c_one_step_hours,
        review_threshold_h100_hours,
    )
    if not all(math.isfinite(float(value)) and float(value) >= 0.0 for value in values):
        raise ValueError("Runtime authorization values must be finite and nonnegative")
    projected_incremental = float(pairmlp_elapsed_through_u8_hours)
    projected_total = (
        float(phase_a_actual_h100_hours)
        + float(pairmlp_elapsed_through_u8_hours)
        + projected_incremental
        + float(fixed_final_evaluation_hours)
        + float(phase_c_one_step_hours)
    )
    return {
        "phase_a_actual_h100_hours": float(phase_a_actual_h100_hours),
        "pairmlp_elapsed_through_u8_hours": float(pairmlp_elapsed_through_u8_hours),
        "projected_incremental_u8_to_u16_hours": projected_incremental,
        "fixed_final_evaluation_hours": float(fixed_final_evaluation_hours),
        "phase_c_one_step_hours": float(phase_c_one_step_hours),
        "projected_total_h100_hours_through_u16": projected_total,
        "review_threshold_h100_hours": float(review_threshold_h100_hours),
        "automatic_u16_authorized": projected_total
        <= float(review_threshold_h100_hours),
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


def build_amortized_one_step_manifest(
    field_selected_rows: Sequence[Mapping[str, Any]],
    *,
    model_kind: str,
    seed: int = GLOBAL_SEED,
) -> dict[str, Any]:
    """Freeze correct, state-shuffle, transition-shuffle, and zero conditions."""

    if model_kind not in {"pairmlp", "factorized"}:
        raise ValueError(f"Unknown amortized one-step model kind: {model_kind}")
    rows_by_state: dict[str, dict[str, Any]] = {}
    for source in field_selected_rows:
        if str(source.get("condition_name")) != "F3_deployment_e_field_raw":
            continue
        state_id = str(source["state_example_id"])
        if state_id in rows_by_state:
            raise ValueError(f"Duplicate deployment selection for {state_id}")
        rows_by_state[state_id] = dict(source)
    if len(rows_by_state) != 45:
        raise ValueError("Amortized one-step manifest requires exactly 45 F3 states")
    state_order = sorted(
        rows_by_state,
        key=lambda value: hashlib.sha256(
            f"{seed}:state-shuffle:{value}".encode("utf-8")
        ).hexdigest(),
    )
    shuffled_state = {
        state_id: state_order[(index + 1) % len(state_order)]
        for index, state_id in enumerate(state_order)
    }
    transition_ids = sorted(
        {str(row["transition_id"]) for row in rows_by_state.values()}
    )
    if len(transition_ids) < 2:
        raise ValueError("Transition shuffle requires at least two selected transitions")
    names = (
        (
            "P1_pairmlp_correct",
            "P2_pairmlp_transition_shuffle",
            "P3_pairmlp_state_shuffle",
            "P0_zero_program",
        )
        if model_kind == "pairmlp"
        else (
            "H1_factorized_correct",
            "H2_factorized_static_only",
            "H3_factorized_transition_shuffle",
            "H4_zero_program",
        )
    )
    conditions = []
    for state_id, source in sorted(rows_by_state.items()):
        own_transition = str(source["transition_id"])
        other_transition = min(
            (value for value in transition_ids if value != own_transition),
            key=lambda value: hashlib.sha256(
                f"{seed}:transition-shuffle:{state_id}:{value}".encode("utf-8")
            ).hexdigest(),
        )
        for name in names:
            state_shuffle = name.endswith("state_shuffle")
            transition_shuffle = name.endswith("transition_shuffle")
            condition = {
                "format": "deep_residual_amortized_one_step_condition_7f_v1",
                "condition_name": name,
                "model_kind": model_kind,
                "state_example_id": state_id,
                "state_task_id": str(source["state_task_id"]),
                "state_step_id": int(source["state_step_id"]),
                "audit_stratum": str(source["audit_stratum"]),
                "api_documentation_action": bool(
                    source.get("api_documentation_action", False)
                ),
                "procedural_tier": source.get("procedural_tier"),
                "signature_class_id": source.get("signature_class_id"),
                "selector_transition_id": own_transition,
                "program_state_example_id": (
                    shuffled_state[state_id] if state_shuffle else state_id
                ),
                "program_transition_id": (
                    other_transition if transition_shuffle else own_transition
                ),
                "student_prompt_contains_raw_transition": False,
                "selection_source": "frozen_exp025cr_deployment_e",
                "selection_uses_qwen_or_appworld_outcomes": False,
                "valid_for_generation": True,
            }
            condition["condition_key"] = _canonical_sha256(condition)
            conditions.append(condition)
    manifest = {
        "format": "deep_residual_amortized_one_step_manifest_7f_v1",
        "global_seed": int(seed),
        "model_kind": model_kind,
        "state_count": len(rows_by_state),
        "condition_count": len(conditions),
        "condition_name_counts": {
            name: sum(row["condition_name"] == name for row in conditions)
            for name in names
        },
        "state_shuffle": shuffled_state,
        "conditions": conditions,
        "student_prompt_contains_raw_transition": False,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    return manifest


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
    winning_metrics = [
        name
        for name in primary
        if float(p1_minus_p2[name]) >= material
        and float(p1_minus_p3[name]) >= material
    ]
    beats_both_on_one = bool(winning_metrics)
    other_not_materially_worse = any(
        all(
            float(p1_minus_p2[other]) >= -material
            and float(p1_minus_p3[other]) >= -material
            for other in primary
            if other != winner
        )
        for winner in winning_metrics
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
