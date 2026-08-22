from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from rcmf.training.state_conditioned_program_7d import (
    canonical_sha256,
    stable_key,
)

GLOBAL_SEED = 25101
PAIR_BEHAVIOR_CONDITIONS = (
    "P1_pairmlp_correct",
    "P2_pairmlp_shuffled_transition",
    "P3_pairmlp_shuffled_state",
)
PRIMARY_BEHAVIOR_METRICS = (
    "canonical_procedural_signature_match",
    "semantic_successor_match",
)


def build_pair_behavior_manifest(
    field_selected_rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = GLOBAL_SEED,
    program_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze P1/P2/P3 pairings without consulting generated outcomes."""
    if int(seed) != GLOBAL_SEED:
        raise ValueError(f"EXP-025D-G3 requires GLOBAL_SEED={GLOBAL_SEED}")
    rows_by_state: dict[str, Mapping[str, Any]] = {}
    for row in field_selected_rows:
        if str(row.get("condition_name")) != "F3_deployment_e_field_raw":
            continue
        state_id = str(row["state_example_id"])
        if state_id in rows_by_state:
            raise ValueError(f"Duplicate frozen F3 state: {state_id}")
        rows_by_state[state_id] = row
    if len(rows_by_state) < 2:
        raise ValueError("PairBehavior controls require at least two frozen states")

    state_ids = sorted(rows_by_state)
    transition_ids = sorted(
        {str(row["transition_id"]) for row in rows_by_state.values()}
    )
    if len(transition_ids) < 2:
        raise ValueError("Transition shuffle requires at least two transition IDs")

    conditions: list[dict[str, Any]] = []
    for state_id in state_ids:
        source = rows_by_state[state_id]
        own_transition = str(source["transition_id"])
        shuffled_transition = min(
            (value for value in transition_ids if value != own_transition),
            key=lambda value: stable_key(
                seed, "pair-behavior-transition-shuffle", state_id, value
            ),
        )
        shuffled_state = min(
            (value for value in state_ids if value != state_id),
            key=lambda value: stable_key(
                seed, "pair-behavior-state-shuffle", state_id, value
            ),
        )
        for name in PAIR_BEHAVIOR_CONDITIONS:
            program_state_id = (
                shuffled_state if name == "P3_pairmlp_shuffled_state" else state_id
            )
            program_transition_id = (
                shuffled_transition
                if name == "P2_pairmlp_shuffled_transition"
                else own_transition
            )
            payload = {
                "format": "direct_pair_behavior_condition_7dg3_v1",
                "condition_name": name,
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
                "program_state_id": program_state_id,
                "program_transition_id": program_transition_id,
                "prompt_kind": "bare_compiled_program",
                "student_prompt_contains_raw_transition": False,
                "selection_source": "frozen_exp025cr_deployment_e",
                "pairing_source": "deterministic_global_seed_25101",
                "selection_uses_qwen_or_appworld_outcomes": False,
                "valid_for_generation": True,
            }
            if program_provenance is not None:
                payload["program_provenance"] = dict(program_provenance)
            payload["condition_key"] = canonical_sha256(payload)
            conditions.append(payload)

    manifest = {
        "format": "direct_pair_behavior_manifest_7dg3_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": len(rows_by_state),
        "condition_count": len(conditions),
        "condition_name_counts": dict(
            sorted(Counter(row["condition_name"] for row in conditions).items())
        ),
        "raw_transition_prompt_count": sum(
            bool(row["student_prompt_contains_raw_transition"])
            for row in conditions
        ),
        "program_provenance": (
            None if program_provenance is None else dict(program_provenance)
        ),
        "conditions": conditions,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def _difference(comparison: Mapping[str, Any], metric: str) -> float:
    return float(comparison[metric]["difference"])


def _beats_without_material_degradation(
    comparison: Mapping[str, Any], *, tolerance: float
) -> bool:
    signature = _difference(
        comparison, "canonical_procedural_signature_match"
    )
    successor = _difference(comparison, "semantic_successor_match")
    return bool(
        (signature > 0.0 and successor >= -float(tolerance))
        or (successor > 0.0 and signature >= -float(tolerance))
    )


def pair_behavior_gate(
    *,
    p1_minus_c0: Mapping[str, Any],
    p1_minus_p2: Mapping[str, Any],
    p1_minus_p3: Mapping[str, Any],
    f3_minus_c0: Mapping[str, Any],
    positive_task_count: int,
    material_degradation_tolerance: float = 0.05,
) -> dict[str, Any]:
    retention: dict[str, float | None] = {}
    for metric in PRIMARY_BEHAVIOR_METRICS:
        denominator = _difference(f3_minus_c0, metric)
        numerator = _difference(p1_minus_c0, metric)
        retention[metric] = (
            None if abs(denominator) <= 1.0e-12 else numerator / denominator
        )
    checks = {
        "improves_signature_or_successor": any(
            _difference(p1_minus_c0, metric) > 0.0
            for metric in PRIMARY_BEHAVIOR_METRICS
        ),
        "retains_40_percent_one_metric": any(
            value is not None and value >= 0.40 for value in retention.values()
        ),
        "beats_transition_shuffle_without_material_degradation": (
            _beats_without_material_degradation(
                p1_minus_p2, tolerance=material_degradation_tolerance
            )
        ),
        "beats_state_shuffle_without_material_degradation": (
            _beats_without_material_degradation(
                p1_minus_p3, tolerance=material_degradation_tolerance
            )
        ),
        "execution_drop_lte_0_05": (
            _difference(p1_minus_c0, "execution_success") >= -0.05
        ),
        "positive_at_least_5_of_9_tasks": int(positive_task_count) >= 5,
    }
    passed = all(checks.values())
    return {
        "material_degradation_tolerance": float(material_degradation_tolerance),
        "oracle_gain_retention": retention,
        "checks": checks,
        "passed": passed,
        "decision_branch": (
            "direct_pair_behavior_valid_factorization_bottleneck"
            if passed
            else "teacher_forced_objective_not_behaviorally_retained"
        ),
    }


def runtime_projection(
    *,
    condition_count: int,
    generation_rates: Mapping[str, Mapping[str, float]],
    replay_rates: Mapping[str, float],
    projected_bytes_per_condition: int,
) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    for name in ("best", "expected", "conservative"):
        generation_seconds = int(condition_count) * float(
            generation_rates[name]["generation"]
        )
        wall_seconds = generation_seconds + int(condition_count) * float(
            replay_rates[name]
        )
        scenarios[name] = {
            "h100_hours": generation_seconds / 3600.0,
            "wall_hours": wall_seconds / 3600.0,
        }
    return {
        "condition_count": int(condition_count),
        "scenarios": scenarios,
        "projected_artifact_bytes": (
            int(condition_count) * int(projected_bytes_per_condition)
        ),
    }
