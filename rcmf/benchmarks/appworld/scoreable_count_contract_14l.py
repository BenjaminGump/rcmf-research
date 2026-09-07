from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


EXACT_REPRODUCTION = "exact_reproduction"
SEALED_UPSTREAM_OUTCOMES = "sealed_upstream_outcomes"
KNOWN_SCOREABLE_COUNT_POLICIES = {
    EXACT_REPRODUCTION,
    SEALED_UPSTREAM_OUTCOMES,
}
PERMITTED_MODEL_SPLITS = {
    "model_train",
    "heldout_train_validation",
}
CAUSAL_LABELS = {"POSITIVE", "NEUTRAL", "HARMFUL"}


def scoreable_count_contract(
    *,
    arm_id: str,
    policy: str,
    expected_train: int | None = None,
    expected_heldout: int | None = None,
) -> dict[str, Any]:
    if policy not in KNOWN_SCOREABLE_COUNT_POLICIES:
        raise ValueError(f"Unknown scoreable count policy: {policy}")
    if arm_id not in {"3d", "1d"}:
        raise ValueError(f"Unknown arm for scoreable count policy: {arm_id}")
    result: dict[str, Any] = {
        "format": "exp037a_scoreable_count_contract_14l_v1",
        "arm_id": arm_id,
        "policy": policy,
    }
    if policy == EXACT_REPRODUCTION:
        if expected_train is None or expected_heldout is None:
            raise ValueError("Exact reproduction policy requires both expected counts")
        if expected_train <= 0 or expected_heldout <= 0:
            raise ValueError("Exact reproduction counts must be positive")
        result["expected_train_paired_states"] = int(expected_train)
        result["expected_heldout_paired_states"] = int(expected_heldout)
    elif expected_train is not None or expected_heldout is not None:
        raise ValueError("Dynamic scoreable count policy cannot carry exact counts")
    return result


def _state_id(row: Mapping[str, Any]) -> str:
    value = row.get("state_example_id", row.get("state_id"))
    if not value:
        raise ValueError("Paired row has no state identity")
    return str(value)


def _task_id(row: Mapping[str, Any]) -> str:
    value = row.get("state_task_id", row.get("task_id"))
    if not value:
        raise ValueError(f"Paired row {_state_id(row)} has no task identity")
    return str(value)


def validate_scoreable_population(
    *,
    contract: Mapping[str, Any],
    outcomes: Mapping[str, Any],
    teacher_cache: Mapping[str, Any],
    teacher_report: Mapping[str, Any],
    selection_rows: Sequence[Mapping[str, Any]],
    train_task_ids: Sequence[str],
    heldout_task_ids: Sequence[str],
    minimum_per_label: int,
    maximum_state_count: int,
    outcomes_sha256: str | None = None,
    teacher_cache_sha256: str | None = None,
) -> dict[str, Any]:
    policy = str(contract.get("policy", ""))
    arm_id = str(contract.get("arm_id", ""))
    if policy not in KNOWN_SCOREABLE_COUNT_POLICIES:
        raise ValueError(f"Unknown scoreable count policy: {policy or '<missing>'}")
    if arm_id not in {"3d", "1d"}:
        raise ValueError("Scoreable count contract has an invalid arm")

    rows = [dict(row) for row in outcomes.get("rows", [])]
    state_ids = [_state_id(row) for row in rows]
    state_set = set(state_ids)
    train_tasks = [str(value) for value in train_task_ids]
    heldout_tasks = [str(value) for value in heldout_task_ids]
    train_set = set(train_tasks)
    heldout_set = set(heldout_tasks)
    if train_set & heldout_set:
        raise ValueError("Fixed train/heldout task split overlaps")

    replay_missing_rows = [
        dict(row) for row in outcomes.get("replay_semantic_missing_rows", [])
    ]
    replay_missing_ids = {_state_id(row) for row in replay_missing_rows}
    selection_by_id: dict[str, dict[str, Any]] = {}
    for row in selection_rows:
        state_id = _state_id(row)
        if state_id in selection_by_id:
            raise ValueError("Selection manifest contains duplicate state IDs")
        selection_by_id[state_id] = dict(row)
    over_context_ids = {
        state_id
        for state_id, row in selection_by_id.items()
        if row.get("scoreable") is False or bool(row.get("over_context"))
    }

    row_splits = [str(row.get("model_split", "")) for row in rows]
    train_rows = [
        row for row in rows if str(row.get("model_split")) == "model_train"
    ]
    heldout_rows = [
        row
        for row in rows
        if str(row.get("model_split")) == "heldout_train_validation"
    ]
    labels = Counter(str(row.get("label", "")) for row in rows)
    reported_labels = {
        str(key): int(value)
        for key, value in outcomes.get("label_counts", {}).items()
    }
    teacher_ids = [str(value) for value in teacher_cache.get("ordered_state_ids", [])]
    policy_rows = teacher_cache.get("policy_rows", {})
    teacher_rows = teacher_cache.get("teacher_rows", {})
    task_membership_valid = all(
        (
            _task_id(row) in train_set
            and str(row.get("model_split")) == "model_train"
        )
        or (
            _task_id(row) in heldout_set
            and str(row.get("model_split")) == "heldout_train_validation"
        )
        for row in rows
    )
    complete_pairs = all(
        bool(row.get("bare_condition_key"))
        and bool(row.get("raw_condition_key"))
        and bool(row.get("bare_prompt_sha256"))
        and bool(row.get("raw_prompt_sha256"))
        for row in rows
    )
    minimum_gate = all(labels[label] >= int(minimum_per_label) for label in CAUSAL_LABELS)
    exhausted = bool(outcomes.get("maximum_state_space_exhausted"))
    exhausted_recomputed = (
        len(rows) + len(over_context_ids) + len(replay_missing_rows)
        == int(maximum_state_count)
    )
    panel_complete = minimum_gate or exhausted

    checks = {
        "train_task_count_29": len(train_tasks) == 29,
        "heldout_task_count_8": len(heldout_tasks) == 8,
        "task_split_unique": len(train_set) == len(train_tasks)
        and len(heldout_set) == len(heldout_tasks),
        "task_split_disjoint": not bool(train_set & heldout_set),
        "paired_rows_nonempty": bool(rows),
        "paired_state_ids_unique": len(state_ids) == len(state_set),
        "train_population_nonempty": bool(train_rows),
        "heldout_population_nonempty": bool(heldout_rows),
        "permitted_model_splits": set(row_splits) <= PERMITTED_MODEL_SPLITS,
        "all_rows_in_fixed_task_split": task_membership_valid,
        "causal_labels_valid": set(labels) == CAUSAL_LABELS,
        "complete_bare_raw_pairs": complete_pairs,
        "replay_missing_ids_unique": len(replay_missing_ids)
        == len(replay_missing_rows),
        "missing_types_disjoint": not bool(
            replay_missing_ids & over_context_ids
        ),
        "replay_missing_excluded": not bool(state_set & replay_missing_ids),
        "static_over_context_excluded": not bool(state_set & over_context_ids),
        "outcome_state_count_recomputed": int(outcomes.get("state_count", -1))
        == len(rows),
        "outcome_condition_count_recomputed": int(
            outcomes.get("condition_count", -1)
        )
        == 2 * len(rows),
        "outcome_label_counts_recomputed": reported_labels
        == dict(sorted(labels.items())),
        "outcome_replay_missing_count_recomputed": int(
            outcomes.get("replay_semantic_missing_count", -1)
        )
        == len(replay_missing_rows),
        "outcome_over_context_count_recomputed": int(
            outcomes.get("over_context_missing_count", -1)
        )
        == len(over_context_ids),
        "panel_completion_contract": panel_complete,
        "panel_flags_consistent": bool(
            outcomes.get("minimum_label_gate_passed")
        )
        == minimum_gate,
        "maximum_state_space_flag_consistent": exhausted
        == exhausted_recomputed,
        "maximum_state_count_valid": int(maximum_state_count) == 499,
        "teacher_order_exact": teacher_ids == state_ids,
        "teacher_state_ids_unique": len(teacher_ids) == len(set(teacher_ids)),
        "teacher_policy_ids_exact": set(map(str, policy_rows)) == state_set,
        "teacher_target_ids_exact": set(map(str, teacher_rows)) == state_set,
        "teacher_report_passed": bool(teacher_report.get("passed")),
        "teacher_report_state_count": int(teacher_report.get("state_count", -1))
        == len(rows),
        "teacher_report_bare_count": int(
            teacher_report.get("bare_policy_count", -1)
        )
        == len(rows),
        "teacher_report_raw_count": int(
            teacher_report.get("raw_policy_count", -1)
        )
        == len(rows),
        "teacher_cache_binds_paired_outcomes": (
            outcomes_sha256 is None
            or str(teacher_cache.get("paired_outcomes_sha256", ""))
            == outcomes_sha256
        ),
        "teacher_report_binds_teacher_cache": (
            teacher_cache_sha256 is None
            or str(teacher_report.get("cache_sha256", ""))
            == teacher_cache_sha256
        ),
    }
    if policy == EXACT_REPRODUCTION:
        checks.update(
            {
                "exact_train_paired_count": len(train_rows)
                == int(contract.get("expected_train_paired_states", -1)),
                "exact_heldout_paired_count": len(heldout_rows)
                == int(contract.get("expected_heldout_paired_states", -1)),
            }
        )
    elif any(
        key in contract
        for key in (
            "expected_train_paired_states",
            "expected_heldout_paired_states",
        )
    ):
        checks["dynamic_policy_has_no_exact_counts"] = False
    else:
        checks["dynamic_policy_has_no_exact_counts"] = True

    result = {
        "format": "exp037a_scoreable_population_validation_14l_v1",
        "arm_id": arm_id,
        "policy": policy,
        "checks": checks,
        "derived_counts": {
            "paired_states": len(rows),
            "model_train": len(train_rows),
            "heldout_train_validation": len(heldout_rows),
            "labels": dict(sorted(labels.items())),
            "replay_semantic_missing": len(replay_missing_rows),
            "static_over_context": len(over_context_ids),
        },
        "ordered_state_ids": state_ids,
        "panel_completion": {
            "minimum_per_label": int(minimum_per_label),
            "minimum_label_gate_recomputed": minimum_gate,
            "maximum_state_count": int(maximum_state_count),
            "maximum_state_space_exhausted": exhausted,
        },
        "passed": all(checks.values()),
    }
    if not result["passed"]:
        failed = sorted(key for key, value in checks.items() if not value)
        raise ValueError(f"Scoreable population contract failed: {failed}")
    return result
