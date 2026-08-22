from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key


GLOBAL_SEED = 25101
K_VALUES = (4, 8, 16)
CHANNEL_CONDITIONS = ("O_direct_delta", "S_shuffled_delta")


class DirectDeltaInjector(AdditiveTokenMemoryInjector):
    """Inject a flattened K x d_model tensor without a decoder or latent map."""

    def __init__(self, *, model_dim: int, num_tokens: int) -> None:
        nn.Module.__init__(self)
        if model_dim <= 0 or num_tokens <= 0:
            raise ValueError("model_dim and num_tokens must be positive")
        self.program_dim = int(model_dim) * int(num_tokens)
        self.model_dim = int(model_dim)
        self.num_prefix_tokens = int(num_tokens)
        self.num_tokens = int(num_tokens)
        self.position = "last_user_k"

    def forward(self, memory_z: Tensor) -> Tensor:
        expected = self.num_tokens * self.model_dim
        if memory_z.dim() != 2 or memory_z.shape[-1] != expected:
            raise ValueError(
                f"memory_z must have shape [batch, {expected}], got {tuple(memory_z.shape)}"
            )
        if not bool(torch.isfinite(memory_z).all()):
            raise ValueError("Direct DeltaE contains nonfinite values")
        return memory_z.to(torch.float32).view(
            memory_z.shape[0], self.num_tokens, self.model_dim
        )


def require_global_seed(seed: int) -> None:
    if int(seed) != GLOBAL_SEED:
        raise ValueError(f"EXP-026A requires GLOBAL_SEED={GLOBAL_SEED}")


def cyclic_derangement(pair_ids: Sequence[str], *, namespace: str) -> list[int]:
    if len(pair_ids) < 2:
        raise ValueError("A shuffled-oracle control requires at least two pairs")
    order = sorted(
        range(len(pair_ids)),
        key=lambda index: stable_key(GLOBAL_SEED, namespace, pair_ids[index]),
    )
    permutation = list(range(len(pair_ids)))
    for offset, index in enumerate(order):
        permutation[index] = order[(offset + 1) % len(order)]
    if any(index == source for index, source in enumerate(permutation)):
        raise AssertionError("Cyclic control is not a derangement")
    return permutation


def build_channel_pair_manifest(
    *,
    conditions: Sequence[Mapping[str, Any]],
    e_pairs: Sequence[Mapping[str, Any]],
    last_user_counts: Mapping[str, int],
    cached_teacher_pair_ids: set[str],
) -> dict[str, Any]:
    selected = [
        dict(row)
        for row in conditions
        if str(row.get("condition_name")) == "P1_pairmlp_correct"
        and str(row.get("audit_stratum")) in {"A", "B"}
    ]
    selected.sort(key=lambda row: str(row["state_example_id"]))
    by_pair = {str(row["pair_id"]): dict(row) for row in e_pairs}
    if len(by_pair) != len(e_pairs):
        raise ValueError("E pair rows contain duplicate pair IDs")

    rows: list[dict[str, Any]] = []
    for condition in selected:
        pair_id = (
            f"{condition['state_example_id']}::transition::"
            f"{condition['program_transition_id']}"
        )
        if pair_id not in by_pair:
            raise KeyError(f"Frozen F3 pair is absent from clean E rows: {pair_id}")
        pair = by_pair[pair_id]
        count = int(last_user_counts[pair_id])
        rows.append(
            {
                **pair,
                "audit_stratum": str(condition["audit_stratum"]),
                "procedural_tier": int(condition["procedural_tier"]),
                "signature_class_id": str(condition["signature_class_id"]),
                "last_user_token_count": count,
                "teacher_cache_source": (
                    "reused_exp025d_g3" if pair_id in cached_teacher_pair_ids else "new_exp026a"
                ),
                "k_feasible": {str(k): count >= k for k in K_VALUES},
            }
        )

    pair_ids = [str(row["pair_id"]) for row in rows]
    if len(rows) != 32 or len(set(pair_ids)) != 32:
        raise ValueError(f"Expected 32 unique primary F3 pairs, found {len(rows)}")
    if len({str(row["state_task_id"]) for row in rows}) != 9:
        raise ValueError("The primary capacity manifest must cover all nine tasks")

    controls = {}
    feasibility = {}
    for k in K_VALUES:
        feasible_indices = [
            index for index, row in enumerate(rows) if bool(row["k_feasible"][str(k)])
        ]
        feasible_ids = [pair_ids[index] for index in feasible_indices]
        local_permutation = cyclic_derangement(
            feasible_ids, namespace=f"direct-delta-k{k}-control"
        )
        controls[str(k)] = {
            pair_ids[index]: feasible_ids[local_permutation[offset]]
            for offset, index in enumerate(feasible_indices)
        }
        feasibility[str(k)] = {
            "feasible_count": len(feasible_indices),
            "missing_count": len(rows) - len(feasible_indices),
            "missing_pair_ids": [
                pair_ids[index]
                for index in range(len(rows))
                if index not in set(feasible_indices)
            ],
        }
    if int(feasibility["16"]["feasible_count"]) < 28:
        raise ValueError("Fewer than 28 primary states support K=16")

    payload = {
        "format": "direct_injection_channel_pair_manifest_7dh_v1",
        "global_seed": GLOBAL_SEED,
        "pair_count": len(rows),
        "task_count": len({str(row["state_task_id"]) for row in rows}),
        "pairs": rows,
        "feasibility": feasibility,
        "cyclic_controls": controls,
        "cached_teacher_count": sum(
            str(row["pair_id"]) in cached_teacher_pair_ids for row in rows
        ),
        "new_teacher_count": sum(
            str(row["pair_id"]) not in cached_teacher_pair_ids for row in rows
        ),
        "selection_uses_behavioral_outcomes": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def continuation_decision(
    u4: Mapping[str, Any],
    u8: Mapping[str, Any],
    *,
    minimum_relative_improvement: float = 0.05,
) -> dict[str, Any]:
    def improvement(metric: str) -> float:
        before = float(u4[metric])
        after = float(u8[metric])
        return (before - after) / max(abs(before), 1.0e-12)

    kl = improvement("teacher_policy_kl")
    ce = improvement("teacher_token_ce")
    checks = {
        "policy_kl_improved_materially": kl >= float(minimum_relative_improvement),
        "teacher_ce_improved_materially": ce >= float(minimum_relative_improvement),
        "ratio_lte_1": float(u8["delta_ratio_max"]) <= 1.0001,
    }
    return {
        "policy_kl_relative_improvement": kl,
        "teacher_ce_relative_improvement": ce,
        "checks": checks,
        "continue_to_u16": bool(
            checks["ratio_lte_1"]
            and (
                checks["policy_kl_improved_materially"]
                or checks["teacher_ce_improved_materially"]
            )
        ),
    }


def channel_gate(
    *,
    o_minus_c0: Mapping[str, Any],
    o_minus_s: Mapping[str, Any],
    f3_minus_c0: Mapping[str, Any],
    positive_task_count: int,
    material_improvement: float = 0.05,
    material_degradation: float = 0.05,
) -> dict[str, Any]:
    signature = "canonical_procedural_signature_match"
    successor = "semantic_successor_match"
    execution = "execution_success"

    def delta(values: Mapping[str, Any], metric: str) -> float:
        return float(values[metric]["mean_difference"])

    def retention(metric: str) -> float | None:
        denominator = delta(f3_minus_c0, metric)
        if abs(denominator) <= 1.0e-12:
            return None
        return delta(o_minus_c0, metric) / denominator

    retention_signature = retention(signature)
    retention_successor = retention(successor)
    retention_values = {
        signature: retention_signature,
        successor: retention_successor,
    }
    seventy = any(
        value is not None and value >= 0.70 for value in retention_values.values()
    )
    fifty_other = False
    if retention_signature is None:
        fifty_other = retention_successor is not None and retention_successor >= 0.70
    elif retention_successor is None:
        fifty_other = retention_signature >= 0.70
    else:
        fifty_other = (
            retention_signature >= 0.70 and retention_successor >= 0.50
        ) or (
            retention_successor >= 0.70 and retention_signature >= 0.50
        )
    shuffle_signature = delta(o_minus_s, signature)
    shuffle_successor = delta(o_minus_s, successor)
    beats_shuffle = (
        shuffle_signature >= float(material_improvement)
        and shuffle_successor >= -float(material_degradation)
    ) or (
        shuffle_successor >= float(material_improvement)
        and shuffle_signature >= -float(material_degradation)
    )
    checks = {
        "retains_70_percent_on_one_primary_metric": seventy,
        "retains_50_percent_on_other_primary_metric": fifty_other,
        "beats_shuffled_oracle_without_material_degradation": beats_shuffle,
        "execution_not_more_than_5pp_below_bare": delta(o_minus_c0, execution) >= -0.05,
        "positive_on_at_least_6_tasks": int(positive_task_count) >= 6,
    }
    return {
        "retention": retention_values,
        "shuffle_deltas": {
            signature: shuffle_signature,
            successor: shuffle_successor,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def runtime_projection(
    *,
    feasible_counts: Mapping[str, int],
    new_teacher_count: int,
    rates: Mapping[str, Mapping[str, float]],
    maximum_updates_per_pair: int,
) -> dict[str, Any]:
    generations = 2 * sum(int(feasible_counts[str(k)]) for k in K_VALUES)
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        values = rates[name]
        updates = sum(
            int(feasible_counts[str(k)]) * int(maximum_updates_per_pair)
            for k in K_VALUES
        )
        minimum_updates = sum(int(feasible_counts[str(k)]) * 8 for k in K_VALUES)
        teacher_seconds = int(new_teacher_count) * (
            float(values["generation"]) + float(values["forward"])
        )
        maximum_training_seconds = updates * 2.0 * (
            float(values["forward"]) + float(values["backward"])
        )
        minimum_training_seconds = minimum_updates * 2.0 * (
            float(values["forward"]) + float(values["backward"])
        )
        # Zero/u4/u8/u16 teacher-forced reports each require the policy and
        # ground-truth forward paths for every feasible pair.
        evaluation_seconds = (
            4
            * 2
            * sum(int(feasible_counts[str(k)]) for k in K_VALUES)
            * float(values["forward"])
        )
        generation_seconds = generations * float(values["generation"])
        scenarios[name] = {
            "minimum_h100_hours": (
                teacher_seconds
                + minimum_training_seconds
                + evaluation_seconds
                + generation_seconds
            )
            / 3600.0,
            "maximum_h100_hours": (
                teacher_seconds
                + maximum_training_seconds
                + evaluation_seconds
                + generation_seconds
            )
            / 3600.0,
            "teacher_hours": teacher_seconds / 3600.0,
            "minimum_training_hours": minimum_training_seconds / 3600.0,
            "maximum_training_hours": maximum_training_seconds / 3600.0,
            "teacher_forced_evaluation_hours": evaluation_seconds / 3600.0,
            "generation_hours": generation_seconds / 3600.0,
        }
    return {
        "new_teacher_count": int(new_teacher_count),
        "optimizer_backward_calls_minimum": sum(
            int(feasible_counts[str(k)]) * 8 for k in K_VALUES
        ),
        "optimizer_backward_calls_maximum": sum(
            int(feasible_counts[str(k)]) * int(maximum_updates_per_pair)
            for k in K_VALUES
        ),
        "qwen_backward_path_equivalents_minimum": 2
        * sum(int(feasible_counts[str(k)]) * 8 for k in K_VALUES),
        "qwen_backward_path_equivalents_maximum": 2
        * sum(
            int(feasible_counts[str(k)]) * int(maximum_updates_per_pair)
            for k in K_VALUES
        ),
        "one_step_generation_count": generations,
        "appworld_reconstruction_execution_count": generations,
        "scenarios": scenarios,
    }
