from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from rcmf.training.procedural_causal_audit_6h import paired_task_bootstrap


MISSING_POLICY_VERSION = "selector_behavioral_missing_policy_7cr_v1"
MISSING_STATUS = "over_context_missing"
MISSING_REASON = (
    "selected_signature_class_has_no_context_feasible_raw_member"
)
EXECUTABLE_STATUS = "executable"
BOUNDED_METRICS = (
    "exact_primary_app_api_match",
    "canonical_procedural_signature_match",
    "execution_success",
    "semantic_successor_match",
)
FORBIDDEN_MISSING_FIELDS = (
    "model_response",
    "raw_model_response",
    "extracted_code",
    "execution_result",
    "execution_output",
    "metrics",
    "success",
    "failure",
    "imputed_value",
)


def mark_executable(condition: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(condition)
    output.update(
        {
            "condition_status": EXECUTABLE_STATUS,
            "valid_for_generation": True,
            "valid_for_pairwise_comparison": True,
            "missing_reason": None,
        }
    )
    return output


def mark_over_context_missing(
    condition: Mapping[str, Any],
    *,
    prompt_tokens: int,
    context_limit: int,
) -> dict[str, Any]:
    """Record one frozen selection as unavailable without assigning an outcome."""

    if prompt_tokens <= context_limit:
        raise ValueError("Missing condition must exceed the locked context")
    output = dict(condition)
    output.update(
        {
            "condition_status": MISSING_STATUS,
            "valid_for_generation": False,
            "valid_for_pairwise_comparison": False,
            "missing_reason": MISSING_REASON,
            "prompt_tokens": int(prompt_tokens),
            "context_limit": int(context_limit),
            "result_source": {"kind": MISSING_STATUS},
        }
    )
    for field in FORBIDDEN_MISSING_FIELDS:
        output.pop(field, None)
    return output


def validate_logical_manifest(
    conditions: Sequence[Mapping[str, Any]],
    *,
    expected_state_count: int,
    expected_missing_state_id: str,
    expected_missing_condition: str,
    expected_missing_class_id: str,
    expected_prompt_tokens: int,
    expected_context_limit: int,
) -> dict[str, Any]:
    expected_names = {
        "F1_strict_b_field_raw",
        "F2_strict_b_field_signature",
        "F3_deployment_e_field_raw",
        "F4_deployment_e_field_signature",
        "F5_predicted_intent_raw",
    }
    keys = [str(row["condition_key"]) for row in conditions]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate logical condition keys")
    state_ids = {str(row["state_example_id"]) for row in conditions}
    name_counts = Counter(str(row["condition_name"]) for row in conditions)
    expected_slots = expected_state_count * len(expected_names)
    if len(conditions) != expected_slots:
        raise ValueError(
            f"Logical condition count differs: {len(conditions)} != {expected_slots}"
        )
    if len(state_ids) != expected_state_count:
        raise ValueError("Logical manifest state count differs")
    if set(name_counts) != expected_names or any(
        count != expected_state_count for count in name_counts.values()
    ):
        raise ValueError("Each F1-F5 condition must occur once per state")

    missing = [
        row for row in conditions if str(row["condition_status"]) == MISSING_STATUS
    ]
    executable = [
        row
        for row in conditions
        if str(row["condition_status"]) == EXECUTABLE_STATUS
    ]
    if len(missing) != 1 or len(executable) != expected_slots - 1:
        raise ValueError("Manifest must contain exactly one missing logical slot")
    unknown = [
        row
        for row in conditions
        if str(row["condition_status"])
        not in {MISSING_STATUS, EXECUTABLE_STATUS}
    ]
    if unknown:
        raise ValueError("Unknown condition status in logical manifest")
    if any(
        not bool(row["valid_for_generation"])
        or not bool(row["valid_for_pairwise_comparison"])
        for row in executable
    ):
        raise ValueError("Executable condition has disabled validity")

    row = missing[0]
    expected = {
        "state_example_id": expected_missing_state_id,
        "condition_name": expected_missing_condition,
        "signature_class_id": expected_missing_class_id,
        "condition_status": MISSING_STATUS,
        "valid_for_generation": False,
        "valid_for_pairwise_comparison": False,
        "missing_reason": MISSING_REASON,
        "prompt_tokens": expected_prompt_tokens,
        "context_limit": expected_context_limit,
    }
    for field, value in expected.items():
        if row.get(field) != value:
            raise ValueError(f"Missing-row field differs: {field}")
    for field in FORBIDDEN_MISSING_FIELDS:
        if field in row:
            raise ValueError(f"Missing row assigns forbidden field: {field}")
    if row.get("result_source") != {"kind": MISSING_STATUS}:
        raise ValueError("Missing row must not name an executable result source")

    return {
        "policy_version": MISSING_POLICY_VERSION,
        "logical_slot_count": len(conditions),
        "executable_slot_count": len(executable),
        "missing_slot_count": len(missing),
        "state_count": len(state_ids),
        "condition_name_counts": dict(sorted(name_counts.items())),
        "missing_condition_key": str(row["condition_key"]),
    }


def validate_result_keys(
    conditions: Sequence[Mapping[str, Any]],
    result_keys: Sequence[str],
) -> dict[str, Any]:
    executable_keys = {
        str(row["condition_key"])
        for row in conditions
        if bool(row["valid_for_generation"])
    }
    missing_keys = {
        str(row["condition_key"])
        for row in conditions
        if not bool(row["valid_for_generation"])
    }
    actual = {str(value) for value in result_keys}
    if len(actual) != len(result_keys):
        raise ValueError("Duplicate condition result keys")
    if actual != executable_keys:
        raise ValueError("Condition results do not exactly match executable slots")
    if actual.intersection(missing_keys):
        raise ValueError("A result was assigned to a missing logical slot")
    return {
        "result_count": len(actual),
        "executable_result_count": len(executable_keys),
        "missing_result_count": len(actual.intersection(missing_keys)),
    }


def paired_denominators(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    per_task: Counter[str] = Counter()
    paired_states = []
    for state_id, values in sorted(by_state.items()):
        if left not in values or right not in values:
            continue
        task_left = str(values[left]["state_task_id"])
        task_right = str(values[right]["state_task_id"])
        if task_left != task_right:
            raise ValueError("Paired conditions disagree on task identity")
        paired_states.append(state_id)
        per_task[task_left] += 1
    return {
        "left_condition": left,
        "right_condition": right,
        "paired_state_count": len(paired_states),
        "task_count": len(per_task),
        "per_task_paired_state_count": dict(sorted(per_task.items())),
        "paired_state_ids": paired_states,
    }


def paired_complete_case_comparison(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    metrics: Sequence[str],
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    denominators = paired_denominators(rows, left=left, right=right)
    comparisons = {
        metric: paired_task_bootstrap(
            rows,
            left_condition=left,
            right_condition=right,
            metric=metric,
            samples=bootstrap_samples,
            seed=seed + index,
        )
        for index, metric in enumerate(metrics)
    }
    if any(
        int(value["paired_state_count"])
        != int(denominators["paired_state_count"])
        for value in comparisons.values()
    ):
        raise ValueError("Bootstrap and complete-case denominators differ")
    return {"denominators": denominators, "metrics": comparisons}


def one_missing_binary_bounds(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    missing_state_id: str,
    metrics: Sequence[str] = BOUNDED_METRICS,
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    missing_values = by_state.get(missing_state_id, {})
    if left not in missing_values or right in missing_values:
        raise ValueError("Expected exactly the right-hand condition to be missing")

    paired = [
        values
        for state_id, values in by_state.items()
        if state_id != missing_state_id and left in values and right in values
    ]
    total_count = len(paired) + 1
    output = {}
    for metric in metrics:
        complete_sum = sum(
            float(values[left]["metrics"][metric])
            - float(values[right]["metrics"][metric])
            for values in paired
        )
        missing_left = float(missing_values[left]["metrics"][metric])
        output[metric] = {
            "complete_case_difference": complete_sum / len(paired),
            "best_possible_missing_f5_outcome": (
                complete_sum + missing_left - 1.0
            )
            / total_count,
            "worst_possible_missing_f5_outcome": (
                complete_sum + missing_left
            )
            / total_count,
            "adverse_to_field_bound": (
                complete_sum + missing_left - 1.0
            )
            / total_count,
            "favorable_to_field_bound": (
                complete_sum + missing_left
            )
            / total_count,
            "missing_left_observed_value": missing_left,
        }
    return {
        "left_condition": left,
        "right_condition": right,
        "missing_state_id": missing_state_id,
        "paired_complete_case_count": len(paired),
        "bounded_total_state_count": total_count,
        "metrics": output,
        "scientific_imputation_performed": False,
    }


def predicted_intent_control_robustness(
    complete_case: Mapping[str, Any],
    bounds: Mapping[str, Any],
    *,
    primary_metrics: Sequence[str],
) -> dict[str, Any]:
    details = {}
    robust_metrics = []
    for metric in primary_metrics:
        comparison = complete_case["metrics"][metric]
        bound = bounds["metrics"][metric]
        positive = float(comparison["difference"]) > 0.0
        ci_excludes_zero = (
            comparison.get("ci95_low") is not None
            and float(comparison["ci95_low"]) > 0.0
        )
        not_reversed = float(bound["adverse_to_field_bound"]) > 0.0
        robust = positive and ci_excludes_zero and not_reversed
        details[metric] = {
            "complete_case_positive": positive,
            "bootstrap_ci_excludes_zero": ci_excludes_zero,
            "one_row_adverse_bound_remains_positive": not_reversed,
            "robust": robust,
        }
        if robust:
            robust_metrics.append(metric)
    return {
        "passed": bool(robust_metrics),
        "robust_metrics": robust_metrics,
        "metric_checks": details,
        "failure_label": (
            None
            if robust_metrics
            else "predicted_intent_control_comparison_inconclusive"
        ),
    }
