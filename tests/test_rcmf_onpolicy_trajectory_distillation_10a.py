from __future__ import annotations

import copy

import pytest

from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    balance_union_rows,
    candidate_eligibility,
    classify_task,
    configure_reader_only_trainables,
    configure_writer_last_layer_trainables,
    deterministic_bank_augmentation,
    first37_decision,
    first_common_history_preference,
    select_final_candidate,
    strict_no_progress_loops,
    successful_trajectory_weights,
    trainable_parameter_names,
)


def _step(step_id: int, code: str, observation: str, history: list[dict] | None = None) -> dict:
    return {
        "step_id": step_id,
        "exact_executed_code": code,
        "complete_environment_observation": observation,
        "locked_normalized_observation": observation,
        "state_fingerprint_before": "same",
        "state_fingerprint_after": "same",
        "complete_trajectory_so_far": list(history or []),
        "raw_model_response": code,
    }


def _result(success: bool, *, steps: list[dict] | None = None, exceptions: int = 0) -> dict:
    rows = list(steps or [_step(1, "x=1", "ok")])
    return {
        "success": success,
        "steps": rows,
        "step_count": len(rows),
        "strict_no_progress_loop_count": 0,
        "counts": {"execution_exception": exceptions},
        "usage": {"completion_tokens": 10},
    }


@pytest.mark.parametrize(
    ("bare", "rcmf", "expected"),
    [
        (True, False, "bare_only_success"),
        (False, True, "rcmf_only_success"),
        (True, True, "both_success"),
        (False, False, "neither_success"),
    ],
)
def test_task_classification(bare: bool, rcmf: bool, expected: str) -> None:
    assert classify_task(bare_success=bare, rcmf_success=rcmf) == expected


def test_both_success_lexicographic_selection_and_t1_tie() -> None:
    assert successful_trajectory_weights(
        bare=_result(True, exceptions=1), rcmf=_result(True)
    )[0]["condition"] == "T1"
    rows = successful_trajectory_weights(bare=_result(True), rcmf=_result(True))
    assert [(row["condition"], row["weight"]) for row in rows] == [
        ("T1", 1.0),
        ("T0", 0.5),
    ]


def test_first_preference_uses_only_common_history() -> None:
    history: list[dict] = []
    bare = _result(True, steps=[_step(1, "a=1", "a", history)])
    rcmf = _result(False, steps=[_step(1, "a=2", "b", history)])
    row = first_common_history_preference(bare=bare, rcmf=rcmf)
    assert row is not None and row["preferred_condition"] == "T0"
    changed = copy.deepcopy(rcmf)
    changed["steps"][0]["complete_trajectory_so_far"] = [{"response": "prior"}]
    assert first_common_history_preference(bare=bare, rcmf=changed) is None


def test_strict_no_progress_requires_failed_identical_state_action_observation() -> None:
    steps = [_step(i, "x = 1", "same") for i in range(1, 4)]
    loops = strict_no_progress_loops(_result(False, steps=steps))
    assert loops[0]["repetition_count"] == 3
    assert strict_no_progress_loops(_result(True, steps=steps)) == []
    changed = copy.deepcopy(steps)
    changed[-1]["state_fingerprint_after"] = "advanced"
    assert strict_no_progress_loops(_result(False, steps=changed)) == []


def test_bank_augmentation_is_deterministic_and_excludes_query() -> None:
    parents = [f"task-{index}" for index in range(29)]
    rows = [
        deterministic_bank_augmentation(
            unit_id=f"unit-{index}", query_task_id="task-0", parent_task_ids=parents
        )
        for index in range(200)
    ]
    assert rows == [
        deterministic_bank_augmentation(
            unit_id=f"unit-{index}", query_task_id="task-0", parent_task_ids=parents
        )
        for index in range(200)
    ]
    active = [row for row in rows if row["active"]]
    assert 35 <= len(active) <= 65
    assert all("task-0" not in row["removed_parent_task_ids"] for row in active)
    assert all(row["removed_parent_count"] == 3 for row in active)


def test_group_balancing_equalizes_primary_groups() -> None:
    rows = [
        {"balance_group": "preservation", "sample_weight": 1.0},
        {"balance_group": "preservation", "sample_weight": 1.0},
        {"balance_group": "memory_benefit", "sample_weight": 1.0},
        {"balance_group": "both_success", "sample_weight": 1.0},
        {"balance_group": "both_success", "sample_weight": 0.5},
    ]
    balanced = balance_union_rows(rows)
    totals: dict[str, float] = {}
    for row in balanced:
        totals.setdefault(row["balance_group"], 0.0)
        totals[row["balance_group"]] += row["balanced_weight"]
    assert totals["preservation"] == pytest.approx(1.0)
    assert totals["memory_benefit"] == pytest.approx(1.0)
    assert totals["both_success"] == pytest.approx(1.0)


def test_reader_only_and_writer_last_layer_parameter_contracts() -> None:
    writer = AlignedTransitionWriter()
    reader = StandardFieldCrossAttentionReader()
    reader_only = configure_reader_only_trainables(writer=writer, reader=reader)
    assert sum(parameter.numel() for parameter in reader_only) == 4 * 512 * 4096
    assert trainable_parameter_names(writer) == []
    assert trainable_parameter_names(reader) == sorted(
        f"adapters.{layer}.output.weight" for layer in (7, 14, 21, 28)
    )
    reader_params, writer_params = configure_writer_last_layer_trainables(
        writer=writer, reader=reader
    )
    assert sum(parameter.numel() for parameter in reader_params) == 4 * 512 * 4096
    assert sum(parameter.numel() for parameter in writer_params) == 4 * (256 * 512 + 256)
    assert all(
        name.endswith(".output.weight") or name.endswith(".output.bias")
        for name in trainable_parameter_names(writer)
    )


def test_candidate_eligibility_and_selection() -> None:
    gate = candidate_eligibility(
        correct_success_ids=["a", "b", "c"],
        shuffle_success_ids=["a"],
        original_success_ids=["a", "b", "c"],
        correct_loop_count=0,
        original_loop_count=0,
        infrastructure_valid=True,
    )
    assert gate["eligible"]
    common = {
        "eligible": True,
        "correct_success_count": 5,
        "correct_minus_shuffle": 2,
        "retained_original_success_count": 5,
        "no_progress_loop_count": 1,
        "total_steps": 50,
    }
    selected = select_final_candidate(
        [dict(common, stage="writer_reader"), dict(common, stage="reader_only")]
    )
    assert selected is not None and selected["stage"] == "reader_only"


def test_first37_locked_decision_contract() -> None:
    base = {
        "N1": 10,
        "N2": 8,
        "F0": 8,
        "retained_original_gain_count": 5,
        "retained_original_success_count": 2,
        "gain_families": [
            "cross_app_import",
            "spotify_state_machine",
            "exact_set_migration",
        ],
        "recovered_original_loss_count": 2,
        "equivalent_new_gain_family_count": 0,
        "complexity_contract_valid": True,
        "no_progress_loops_materially_increased": False,
    }
    assert first37_decision(base)["decision"] == (
        "trajectory_union_distillation_preliminary_positive"
    )
    failed = dict(base, N1=7, retained_original_gain_count=4)
    assert first37_decision(failed)["decision"] == "trajectory_union_distillation_stop"
