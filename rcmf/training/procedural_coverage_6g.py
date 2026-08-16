from __future__ import annotations

from collections import Counter, defaultdict
import math
from statistics import mean
from typing import Any, Mapping, Sequence


PROCEDURAL_COVERAGE_VERSION = "full_transition_procedural_coverage_6g_v1"
FULL_TRANSITION_SIGNATURE_VERSION = "full_transition_signature_manifest_6g_v1"
CONTEXT_PREFLIGHT_VERSION = "full_transition_context_preflight_6g_v1"


def two_axis_cell(state_split: str, transition_split: str) -> str:
    key = (str(state_split), str(transition_split))
    cells = {
        ("train", "train"): "A",
        ("validation", "train"): "B",
        ("train", "heldout"): "C",
        ("validation", "heldout"): "D",
    }
    try:
        return cells[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported two-axis split: {key}") from exc


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * float(fraction)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def numeric_summary(values: Sequence[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    if not clean:
        return {
            "count": 0,
            "min": None,
            "mean": None,
            "q25": None,
            "median": None,
            "q75": None,
            "q90": None,
            "q95": None,
            "q99": None,
            "max": None,
        }
    return {
        "count": len(clean),
        "min": min(clean),
        "mean": mean(clean),
        "q25": percentile(clean, 0.25),
        "median": percentile(clean, 0.50),
        "q75": percentile(clean, 0.75),
        "q90": percentile(clean, 0.90),
        "q95": percentile(clean, 0.95),
        "q99": percentile(clean, 0.99),
        "max": max(clean),
    }


def _high_tier_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if int(row["procedural_tier"]) >= 3]


def _hard_same_intent_pair_count(rows: Sequence[Mapping[str, Any]]) -> int:
    by_intent: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        by_intent[str(row["transition_coarse_action_type"])][
            int(row["procedural_tier"])
        ] += 1
    total = 0
    for tier_counts in by_intent.values():
        count = sum(tier_counts.values())
        total += count * (count - 1) // 2
        total -= sum(value * (value - 1) // 2 for value in tier_counts.values())
    return total


def candidate_space_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    state_ids: Sequence[str],
    state_task_by_id: Mapping[str, str],
) -> dict[str, Any]:
    expected_ids = [str(value) for value in state_ids]
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Candidate-space state IDs are duplicated")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        state_id = str(row["state_example_id"])
        if state_id not in set(expected_ids):
            raise ValueError(f"Unexpected state in candidate space: {state_id}")
        grouped[state_id].append(row)

    state_rows: list[dict[str, Any]] = []
    hard_pair_count = 0
    hard_pair_states = 0
    for state_id in expected_ids:
        candidates = grouped.get(state_id, [])
        high = _high_tier_rows(candidates)
        tier_counts = Counter(int(row["procedural_tier"]) for row in candidates)
        maximum = max(tier_counts, default=-1)
        best = [row for row in candidates if int(row["procedural_tier"]) == maximum]
        hard_count = _hard_same_intent_pair_count(candidates)
        hard_pair_count += hard_count
        hard_pair_states += hard_count > 0
        high_signatures = {
            str(row["transition_signature_sha256"]) for row in high
        }
        high_parents = {str(row["transition_parent_id"]) for row in high}
        high_tasks = {str(row["transition_parent_task_id"]) for row in high}
        high_api_doc = [
            row for row in high if bool(row.get("transition_api_documentation_action"))
        ]
        state_rows.append(
            {
                "state_example_id": state_id,
                "state_task_id": str(state_task_by_id[state_id]),
                "candidate_count": len(candidates),
                "maximum_tier": maximum,
                "tier_4_count": tier_counts[4],
                "tier_3_count": tier_counts[3],
                "tier_3_or_4_count": len(high),
                "exact_api_candidate_count": sum(
                    bool(row.get("exact_api_sequence")) for row in candidates
                ),
                "unique_signature_count": len(
                    {
                        str(row["transition_signature_sha256"])
                        for row in candidates
                    }
                ),
                "unique_high_tier_signature_count": len(high_signatures),
                "distinct_parent_count": len(
                    {str(row["transition_parent_id"]) for row in candidates}
                ),
                "distinct_high_tier_parent_count": len(high_parents),
                "distinct_source_task_count": len(
                    {str(row["transition_parent_task_id"]) for row in candidates}
                ),
                "distinct_high_tier_source_task_count": len(high_tasks),
                "best_candidate_ids": sorted(str(row["transition_id"]) for row in best),
                "best_parent_ids": sorted(
                    {str(row["transition_parent_id"]) for row in best}
                ),
                "hard_same_intent_pair_count": hard_count,
                "coverage_only_one_high_tier_signature": bool(high)
                and len(high_signatures) == 1,
                "coverage_only_one_high_tier_parent": bool(high)
                and len(high_parents) == 1,
                "coverage_only_one_high_tier_source_task": bool(high)
                and len(high_tasks) == 1,
                "coverage_only_api_documentation_transitions": bool(high)
                and len(high_api_doc) == len(high),
            }
        )

    high_states = [row for row in state_rows if int(row["maximum_tier"]) >= 3]
    exact_states = [
        row for row in state_rows if int(row["exact_api_candidate_count"]) > 0
    ]
    diversified_states = [
        row
        for row in state_rows
        if int(row["unique_high_tier_signature_count"]) >= 2
        and int(row["distinct_high_tier_parent_count"]) >= 2
    ]
    task_counts: dict[str, dict[str, int | float]] = {}
    for task_id in sorted(set(state_task_by_id.values())):
        values = [row for row in state_rows if row["state_task_id"] == task_id]
        high_count = sum(int(row["maximum_tier"]) >= 3 for row in values)
        task_counts[task_id] = {
            "state_count": len(values),
            "states_with_tier3_or_4": high_count,
            "coverage": high_count / len(values) if values else 0.0,
        }
    return {
        "format": PROCEDURAL_COVERAGE_VERSION,
        "pair_count": len(rows),
        "state_count": len(expected_ids),
        "tier_counts": {
            str(tier): sum(
                int(row["procedural_tier"]) == tier for row in rows
            )
            for tier in range(5)
        },
        "states_with_tier3_or_4": len(high_states),
        "tier3_or_4_state_coverage": len(high_states) / len(expected_ids)
        if expected_ids
        else 0.0,
        "states_with_exact_api": len(exact_states),
        "exact_api_state_coverage": len(exact_states) / len(expected_ids)
        if expected_ids
        else 0.0,
        "states_with_diverse_tier3_or_4": len(diversified_states),
        "diverse_tier3_or_4_state_coverage": len(diversified_states)
        / len(expected_ids)
        if expected_ids
        else 0.0,
        "candidate_count": numeric_summary(
            [int(row["candidate_count"]) for row in state_rows]
        ),
        "unique_signature_count": numeric_summary(
            [int(row["unique_signature_count"]) for row in state_rows]
        ),
        "distinct_parent_count": numeric_summary(
            [int(row["distinct_parent_count"]) for row in state_rows]
        ),
        "distinct_source_task_count": numeric_summary(
            [int(row["distinct_source_task_count"]) for row in state_rows]
        ),
        "hard_same_intent_pair_count": hard_pair_count,
        "hard_same_intent_state_count": hard_pair_states,
        "hard_same_intent_state_coverage": hard_pair_states / len(expected_ids)
        if expected_ids
        else 0.0,
        "single_signature_high_tier_state_count": sum(
            bool(row["coverage_only_one_high_tier_signature"]) for row in state_rows
        ),
        "single_parent_high_tier_state_count": sum(
            bool(row["coverage_only_one_high_tier_parent"]) for row in state_rows
        ),
        "single_source_task_high_tier_state_count": sum(
            bool(row["coverage_only_one_high_tier_source_task"]) for row in state_rows
        ),
        "api_doc_only_high_tier_state_count": sum(
            bool(row["coverage_only_api_documentation_transitions"])
            for row in state_rows
        ),
        "task_coverage": task_counts,
        "state_rows": state_rows,
    }


def signature_redundancy_summary(
    transition_signature_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in transition_signature_rows:
        groups[str(row["action_signature"]["signature_sha256"])].append(row)
    group_rows: list[dict[str, Any]] = []
    for signature_hash, rows in sorted(groups.items()):
        sample = rows[0]["action_signature"]
        group_rows.append(
            {
                "signature_sha256": signature_hash,
                "transition_count": len(rows),
                "transition_ids": sorted(str(row["transition_id"]) for row in rows),
                "parent_ids": sorted({str(row["parent_id"]) for row in rows}),
                "parent_task_ids": sorted(
                    {str(row["parent_task_id"]) for row in rows}
                ),
                "parent_diversity": len({str(row["parent_id"]) for row in rows}),
                "primary_app": str(sample["primary_app"]),
                "primary_api": str(sample["primary_api"]),
                "coarse_action_type": str(sample["coarse_action_type"]),
                "api_documentation_action": bool(
                    sample.get("api_documentation_action")
                ),
            }
        )
    group_sizes = [int(row["transition_count"]) for row in group_rows]
    api_docs = [
        row
        for row in transition_signature_rows
        if bool(row["action_signature"].get("api_documentation_action"))
    ]
    return {
        "format": FULL_TRANSITION_SIGNATURE_VERSION,
        "transition_count": len(transition_signature_rows),
        "unique_signature_count": len(group_rows),
        "duplicate_transition_count": len(transition_signature_rows)
        - len(group_rows),
        "duplicate_group_count": sum(
            int(row["transition_count"]) > 1 for row in group_rows
        ),
        "group_size": numeric_summary(group_sizes),
        "group_size_distribution": dict(Counter(group_sizes)),
        "largest_groups": sorted(
            group_rows,
            key=lambda row: (-int(row["transition_count"]), row["signature_sha256"]),
        )[:25],
        "api_documentation_transition_count": len(api_docs),
        "api_documentation_transition_fraction": len(api_docs)
        / len(transition_signature_rows)
        if transition_signature_rows
        else 0.0,
        "unique_signature_by_app": dict(
            Counter(str(row["primary_app"]) for row in group_rows)
        ),
        "unique_signature_by_api": dict(
            Counter(
                f"{row['primary_app']}.{row['primary_api']}" for row in group_rows
            )
        ),
        "unique_signature_by_action_type": dict(
            Counter(str(row["coarse_action_type"]) for row in group_rows)
        ),
        "groups": group_rows,
    }


def context_preflight_summary(
    preflight_rows: Sequence[Mapping[str, Any]],
    *,
    label_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    label_by_pair = {str(row["pair_id"]): row for row in label_rows}
    if len(label_by_pair) != len(label_rows):
        raise ValueError("Duplicate label pair IDs")
    preflight_by_pair = {str(row["pair_id"]): row for row in preflight_rows}
    if len(preflight_by_pair) != len(preflight_rows):
        raise ValueError("Duplicate preflight pair IDs")
    if set(label_by_pair) != set(preflight_by_pair):
        raise ValueError("Context preflight and procedural-label pair IDs differ")
    over = [row for row in preflight_rows if bool(row["over_context"])]
    by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_transition: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in preflight_rows:
        by_state[str(row["state_example_id"])].append(row)
        by_transition[str(row["transition_id"])].append(row)
        by_parent[str(row["parent_memory_id"])].append(row)

    def missingness(values: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
        return {
            key: {
                "legal_pairs": len(rows),
                "over_context_pairs": sum(bool(row["over_context"]) for row in rows),
                "over_context_rate": sum(bool(row["over_context"]) for row in rows)
                / len(rows),
            }
            for key, rows in sorted(values.items())
        }

    states_only_high_over: list[dict[str, Any]] = []
    for state_id, rows in sorted(by_state.items()):
        high_pair_ids = [
            str(row["pair_id"])
            for row in rows
            if int(label_by_pair[str(row["pair_id"])]["procedural_tier"]) >= 3
        ]
        scoreable_high = [
            pair_id
            for pair_id in high_pair_ids
            if not bool(preflight_by_pair[pair_id]["over_context"])
        ]
        if high_pair_ids and not scoreable_high:
            states_only_high_over.append(
                {
                    "state_example_id": state_id,
                    "high_tier_legal_count": len(high_pair_ids),
                    "high_tier_over_context_count": len(high_pair_ids),
                    "pair_ids": high_pair_ids,
                }
            )
    token_fields = (
        "state_prompt_tokens",
        "transition_section_tokens",
        "combined_prompt_tokens",
        "target_tokens",
        "total_tokens_with_target",
    )
    return {
        "format": CONTEXT_PREFLIGHT_VERSION,
        "legal_pair_count": len(preflight_rows),
        "scoreable_pair_count": len(preflight_rows) - len(over),
        "over_context_pair_count": len(over),
        "over_context_rate": len(over) / len(preflight_rows)
        if preflight_rows
        else 0.0,
        "truncated_pair_count": sum(bool(row.get("truncated")) for row in preflight_rows),
        "token_counts": {
            field: numeric_summary([float(row[field]) for row in preflight_rows])
            for field in token_fields
        },
        "over_context_by_state": missingness(by_state),
        "over_context_by_transition": missingness(by_transition),
        "over_context_by_parent": missingness(by_parent),
        "states_whose_only_tier3_or_4_candidates_are_over_context": states_only_high_over,
        "states_whose_only_tier3_or_4_candidates_are_over_context_count": len(
            states_only_high_over
        ),
    }


def missing_state_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    *,
    state_ids: Sequence[str],
    query_signatures: Mapping[str, Mapping[str, Any]],
    transition_signatures: Sequence[Mapping[str, Any]],
    scoreable_only: bool,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not scoreable_only or bool(row.get("scoreable_under_context")):
            grouped[str(row["state_example_id"])].append(row)
    all_api_sequences = {
        tuple(str(value) for value in row["action_signature"]["ordered_api_sequence"])
        for row in transition_signatures
    }
    diagnostics: list[dict[str, Any]] = []
    for state_id in state_ids:
        candidates = grouped.get(str(state_id), [])
        maximum = max(
            (int(row["procedural_tier"]) for row in candidates), default=-1
        )
        if maximum >= 3:
            continue
        best = [row for row in candidates if int(row["procedural_tier"]) == maximum]
        query = query_signatures[str(state_id)]
        target = query["target_signature"]
        missing_components: set[str] = set()
        for row in best or candidates:
            if not bool(row.get("same_primary_app")):
                missing_components.add("primary_app")
            if not bool(row.get("exact_api_sequence")):
                missing_components.add("ordered_api_sequence")
            if not bool(row.get("canonical_action_schema_match")):
                missing_components.add("argument_or_control_flow_schema")
            if not bool(row.get("state_stage_compatible")):
                missing_components.add("state_stage")
        target_sequence = tuple(
            str(value) for value in target.get("ordered_api_sequence", [])
        )
        diagnostics.append(
            {
                "state_example_id": str(state_id),
                "state_task_id": str(query["task_id"]),
                "target_signature_sha256": str(target["signature_sha256"]),
                "target_primary_app": str(target["primary_app"]),
                "target_primary_api": str(target["primary_api"]),
                "target_ordered_api_sequence": list(target_sequence),
                "target_coarse_action_type": str(target["coarse_action_type"]),
                "maximum_available_tier": maximum,
                "exact_api_available": any(
                    bool(row.get("exact_api_sequence")) for row in candidates
                ),
                "missing_signature_components": sorted(missing_components),
                "target_api_sequence_exists_anywhere_in_499": target_sequence
                in all_api_sequences,
                "procedure_absent_from_complete_corpus": target_sequence
                not in all_api_sequences,
                "best_candidate_ids": sorted(
                    str(row["transition_id"]) for row in best
                ),
                "best_parent_ids": sorted(
                    {str(row["transition_parent_id"]) for row in best}
                ),
                "scoreable_only": bool(scoreable_only),
            }
        )
    return diagnostics


def select_decision_branch(
    *,
    b_coverage: float,
    b_diverse_coverage: float,
    e_coverage: float,
    threshold: float,
) -> str:
    if b_coverage >= threshold:
        if b_diverse_coverage >= threshold:
            return "full_transition_bank_procedural_coverage_passed"
        return "nominal_procedural_coverage_lacks_diversity"
    if e_coverage >= threshold:
        return "procedural_coverage_depends_on_heldout_parent_transitions"
    return "complete_training_transition_corpus_coverage_insufficient"


def future_runtime_projection(
    *,
    newly_added_transitions: int,
    representation_observed_seconds: float,
    representation_observed_transitions: int,
    representation_token_ratio: float,
    representation_quadratic_token_ratio: float,
    model_reference_seconds: float,
    pair_scale: float,
    new_cross_encoder_pairs: int,
    cross_encoder_seconds_per_pair: float,
    one_step_condition_count: int,
    generation_seconds: Mapping[str, float],
    replay_execution_seconds: Mapping[str, float],
    storage_bytes: Mapping[str, int],
    review_threshold_h100_hours: float,
) -> dict[str, Any]:
    count_scale = newly_added_transitions / max(1, representation_observed_transitions)
    representation = {
        "best_seconds": representation_observed_seconds * count_scale,
        "expected_seconds": representation_observed_seconds
        * max(count_scale, representation_token_ratio),
        "conservative_seconds": representation_observed_seconds
        * max(count_scale, representation_quadratic_token_ratio),
    }
    model = {
        "best_seconds": model_reference_seconds * pair_scale * 0.25,
        "expected_seconds": model_reference_seconds * pair_scale * 0.50,
        "conservative_seconds": model_reference_seconds * pair_scale,
    }
    cross = {
        "best_seconds": new_cross_encoder_pairs
        * cross_encoder_seconds_per_pair
        * 0.85,
        "expected_seconds": new_cross_encoder_pairs * cross_encoder_seconds_per_pair,
        "conservative_seconds": new_cross_encoder_pairs
        * cross_encoder_seconds_per_pair
        * 1.25,
    }
    one_step = {
        key + "_seconds": one_step_condition_count
        * (float(generation_seconds[key]) + float(replay_execution_seconds[key]))
        for key in ("best", "expected", "conservative")
    }
    phase = {
        "new_transition_multiview_representations": representation,
        "field_compatible_procedural_model": model,
        "optional_prompt_cross_encoder": cross,
        "deterministic_one_step_audit": one_step,
    }
    required_expected = (
        representation["expected_seconds"]
        + model["expected_seconds"]
        + one_step["expected_seconds"]
    ) / 3600.0
    optional_expected = cross["expected_seconds"] / 3600.0
    return {
        "format": "future_exp024_runtime_storage_projection_6g_v1",
        "phase_seconds": phase,
        "required_best_h100_hours": (
            representation["best_seconds"]
            + model["best_seconds"]
            + one_step["best_seconds"]
        )
        / 3600.0,
        "required_expected_h100_hours": required_expected,
        "required_conservative_h100_hours": (
            representation["conservative_seconds"]
            + model["conservative_seconds"]
            + one_step["conservative_seconds"]
        )
        / 3600.0,
        "optional_cross_encoder_expected_h100_hours": optional_expected,
        "total_with_optional_expected_h100_hours": required_expected
        + optional_expected,
        "review_threshold_h100_hours": float(review_threshold_h100_hours),
        "required_expected_exceeds_review_threshold": required_expected
        > review_threshold_h100_hours,
        "with_optional_expected_exceeds_review_threshold": required_expected
        + optional_expected
        > review_threshold_h100_hours,
        "artifact_size_bytes": dict(storage_bytes),
        "artifact_size_total_bytes": sum(int(value) for value in storage_bytes.values()),
        "assumptions": {
            "newly_added_transitions": newly_added_transitions,
            "representation_observed_seconds": representation_observed_seconds,
            "representation_observed_transitions": representation_observed_transitions,
            "representation_token_ratio": representation_token_ratio,
            "representation_quadratic_token_ratio": representation_quadratic_token_ratio,
            "model_reference_seconds": model_reference_seconds,
            "pair_scale": pair_scale,
            "new_cross_encoder_pairs": new_cross_encoder_pairs,
            "cross_encoder_seconds_per_pair": cross_encoder_seconds_per_pair,
            "one_step_condition_count": one_step_condition_count,
            "generation_seconds_per_condition": dict(generation_seconds),
            "replay_execution_seconds_per_condition": dict(replay_execution_seconds),
        },
    }
