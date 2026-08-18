from __future__ import annotations

from pathlib import Path

import pytest

from rcmf.config import load_config
from rcmf.training.selector_behavioral_missing_7cr import (
    BOUNDED_METRICS,
    FORBIDDEN_MISSING_FIELDS,
    MISSING_POLICY_VERSION,
    mark_executable,
    mark_over_context_missing,
    one_missing_binary_bounds,
    paired_complete_case_comparison,
    predicted_intent_control_robustness,
    validate_logical_manifest,
    validate_result_keys,
)
from rcmf.training.procedural_causal_analysis_7b import PRIMARY_METRICS
from scripts.run_field_selector_audit_7cr import _smoke_conditions


MISSING_STATE = "appworld:trace:2a163ab_1:step:13:line:33"
MISSING_CLASS = (
    "procedure:046349e3bb380f803cd6bfd1545a10f26aa9cca1cf98bfa5db70b9b4772bf08f"
)
CONDITION_NAMES = (
    "F1_strict_b_field_raw",
    "F2_strict_b_field_signature",
    "F3_deployment_e_field_raw",
    "F4_deployment_e_field_signature",
    "F5_predicted_intent_raw",
)


def _condition(state: str, name: str, index: int) -> dict[str, object]:
    return {
        "condition_key": f"{state}:{name}",
        "state_example_id": state,
        "state_task_id": f"task-{index % 9}",
        "condition_name": name,
        "signature_class_id": f"class-{index}",
        "transition_id": f"transition-{index}",
    }


def _logical_manifest() -> list[dict[str, object]]:
    rows = []
    state_ids = [MISSING_STATE, *(f"state-{index}" for index in range(44))]
    for index, state in enumerate(state_ids):
        for name in CONDITION_NAMES:
            row = _condition(state, name, index)
            if state == MISSING_STATE and name == "F5_predicted_intent_raw":
                row["signature_class_id"] = MISSING_CLASS
                row["transition_id"] = "9a6f8704-c1f5-5f99-84e4-a1e57a23ec83"
                row = mark_over_context_missing(
                    row, prompt_tokens=41134, context_limit=40960
                )
            else:
                row = mark_executable(row)
            rows.append(row)
    return rows


def _metric_row(
    *,
    state: str,
    task: str,
    condition: str,
    value: float,
    stratum: str = "A",
) -> dict[str, object]:
    return {
        "state_example_id": state,
        "state_task_id": task,
        "condition_name": condition,
        "audit_stratum": stratum,
        "metrics": {metric: value for metric in PRIMARY_METRICS},
    }


def test_missing_policy_accounts_for_225_224_1_without_outcome() -> None:
    rows = _logical_manifest()
    summary = validate_logical_manifest(
        rows,
        expected_state_count=45,
        expected_missing_state_id=MISSING_STATE,
        expected_missing_condition="F5_predicted_intent_raw",
        expected_missing_class_id=MISSING_CLASS,
        expected_prompt_tokens=41134,
        expected_context_limit=40960,
    )
    assert summary["policy_version"] == MISSING_POLICY_VERSION
    assert summary["logical_slot_count"] == 225
    assert summary["executable_slot_count"] == 224
    assert summary["missing_slot_count"] == 1
    missing = next(row for row in rows if not row["valid_for_generation"])
    assert missing["condition_status"] == "over_context_missing"
    assert missing["missing_reason"] == (
        "selected_signature_class_has_no_context_feasible_raw_member"
    )
    assert missing["signature_class_id"] == MISSING_CLASS
    assert missing["transition_id"] == "9a6f8704-c1f5-5f99-84e4-a1e57a23ec83"
    assert missing["prompt_tokens"] == 41134
    assert missing["context_limit"] == 40960
    assert all(field not in missing for field in FORBIDDEN_MISSING_FIELDS)


def test_missing_marker_preserves_frozen_selection_and_rejects_feasible_row() -> None:
    source = {
        "condition_key": "key",
        "selected_class_id": MISSING_CLASS,
        "signature_class_id": MISSING_CLASS,
        "transition_id": "fixed-transition",
        "selector_score": 9.25,
        "rank": 1,
    }
    missing = mark_over_context_missing(
        source, prompt_tokens=41134, context_limit=40960
    )
    for field in (
        "selected_class_id",
        "signature_class_id",
        "transition_id",
        "selector_score",
        "rank",
    ):
        assert missing[field] == source[field]
    with pytest.raises(ValueError, match="must exceed"):
        mark_over_context_missing(source, prompt_tokens=40960, context_limit=40960)


def test_result_validation_requires_224_and_forbids_missing_output() -> None:
    rows = _logical_manifest()
    executable = [
        str(row["condition_key"]) for row in rows if row["valid_for_generation"]
    ]
    result = validate_result_keys(rows, executable)
    assert result == {
        "result_count": 224,
        "executable_result_count": 224,
        "missing_result_count": 0,
    }
    missing_key = next(
        str(row["condition_key"])
        for row in rows
        if not row["valid_for_generation"]
    )
    with pytest.raises(ValueError, match="exactly match"):
        validate_result_keys(rows, [*executable, missing_key])


def test_complete_case_bootstrap_keeps_remaining_rows_from_missing_task() -> None:
    rows = []
    for index in range(45):
        state = MISSING_STATE if index == 0 else f"state-{index}"
        task = f"task-{index // 5}"
        rows.append(
            _metric_row(
                state=state,
                task=task,
                condition="F3_deployment_e_field_raw",
                value=1.0,
            )
        )
        if state != MISSING_STATE:
            rows.append(
                _metric_row(
                    state=state,
                    task=task,
                    condition="F5_predicted_intent_raw",
                    value=0.0,
                )
            )
    report = paired_complete_case_comparison(
        rows,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        metrics=PRIMARY_METRICS,
        bootstrap_samples=200,
        seed=17,
    )
    assert report["denominators"]["paired_state_count"] == 44
    assert report["denominators"]["task_count"] == 9
    assert report["denominators"]["per_task_paired_state_count"]["task-0"] == 4
    assert report["denominators"]["per_task_paired_state_count"]["task-1"] == 5
    for metric in PRIMARY_METRICS:
        assert report["metrics"][metric]["paired_state_count"] == 44
        assert report["metrics"][metric]["difference"] == pytest.approx(1.0)
        assert report["metrics"][metric]["ci95_low"] == pytest.approx(1.0)


def test_primary_complete_cases_are_31_of_32() -> None:
    rows = []
    for index in range(32):
        state = MISSING_STATE if index == 0 else f"primary-{index}"
        task = f"task-{index % 9}"
        rows.append(
            _metric_row(
                state=state,
                task=task,
                condition="F3_deployment_e_field_raw",
                value=1.0,
            )
        )
        if index:
            rows.append(
                _metric_row(
                    state=state,
                    task=task,
                    condition="F5_predicted_intent_raw",
                    value=0.0,
                )
            )
    report = paired_complete_case_comparison(
        rows,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        metrics=PRIMARY_METRICS,
        bootstrap_samples=200,
        seed=19,
    )
    assert report["denominators"]["paired_state_count"] == 31


def test_one_row_bounds_are_mathematical_not_scientific_imputation() -> None:
    rows = [
        _metric_row(
            state=MISSING_STATE,
            task="task-0",
            condition="F3_deployment_e_field_raw",
            value=1.0,
        )
    ]
    for index in range(3):
        state = f"state-{index}"
        rows.extend(
            (
                _metric_row(
                    state=state,
                    task=f"task-{index}",
                    condition="F3_deployment_e_field_raw",
                    value=1.0,
                ),
                _metric_row(
                    state=state,
                    task=f"task-{index}",
                    condition="F5_predicted_intent_raw",
                    value=0.0,
                ),
            )
        )
    bounds = one_missing_binary_bounds(
        rows,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        missing_state_id=MISSING_STATE,
    )
    assert not bounds["scientific_imputation_performed"]
    assert bounds["paired_complete_case_count"] == 3
    assert bounds["bounded_total_state_count"] == 4
    for metric in BOUNDED_METRICS:
        assert bounds["metrics"][metric][
            "best_possible_missing_f5_outcome"
        ] == pytest.approx(0.75)
        assert bounds["metrics"][metric][
            "worst_possible_missing_f5_outcome"
        ] == pytest.approx(1.0)


def test_predicted_intent_robustness_requires_positive_ci_and_adverse_bound() -> None:
    complete = {
        "metrics": {
            metric: {"difference": 0.25, "ci95_low": 0.10, "ci95_high": 0.4}
            for metric in BOUNDED_METRICS
        }
    }
    bounds = {
        "metrics": {
            metric: {"adverse_to_field_bound": 0.05}
            for metric in BOUNDED_METRICS
        }
    }
    assert predicted_intent_control_robustness(
        complete, bounds, primary_metrics=BOUNDED_METRICS
    )["passed"]
    bounds["metrics"]["exact_primary_app_api_match"][
        "adverse_to_field_bound"
    ] = -0.01
    for metric in BOUNDED_METRICS[1:]:
        complete["metrics"][metric]["ci95_low"] = -0.01
    result = predicted_intent_control_robustness(
        complete, bounds, primary_metrics=BOUNDED_METRICS
    )
    assert not result["passed"]
    assert result["failure_label"] == (
        "predicted_intent_control_comparison_inconclusive"
    )


def test_smoke_covers_f1_f3_f4_and_an_executable_f5() -> None:
    conditions = []
    for index, state in enumerate(("state-a", "state-b")):
        for name in CONDITION_NAMES:
            conditions.append(mark_executable(_condition(state, name, index)))
    selected = _smoke_conditions(conditions)
    assert len(selected) == 4
    assert {row["condition_name"] for row in selected} == {
        "F1_strict_b_field_raw",
        "F3_deployment_e_field_raw",
        "F4_deployment_e_field_signature",
        "F5_predicted_intent_raw",
    }
    assert all(row["valid_for_generation"] for row in selected)


def test_7cr_config_freezes_parent_selector_and_missing_contract() -> None:
    config = load_config(
        "configs/benchmark/stage_c_signature_balanced_field_7cr.yaml"
    ).raw["stage_c_7cr"]
    assert config["source"]["starting_head"] == (
        "841cbe179d0d577e9eb9cd4e37299cb9b123915f"
    )
    assert config["expected_selector_ensemble_sha256"] == (
        "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    )
    assert len(config["expected_selector_ensemble_sha256"]) == 64
    assert config["missing_policy"]["state_example_id"] == MISSING_STATE
    assert config["missing_policy"]["signature_class_id"] == MISSING_CLASS
    assert config["missing_policy"]["prompt_tokens"] == 41134
    assert config["missing_policy"]["context_limit"] == 40960


def test_7cr_entrypoints_do_not_import_selector_training() -> None:
    for path in (
        Path("scripts/prepare_field_selector_audit_7cr.py"),
        Path("scripts/run_field_selector_audit_7cr.py"),
        Path("scripts/analyze_field_selector_audit_7cr.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "train_field_selector" not in source
        assert "run_signature_balanced_field_7c" not in source
