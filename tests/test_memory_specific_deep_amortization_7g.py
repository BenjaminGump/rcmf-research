from __future__ import annotations

import pytest

from rcmf.training.memory_specific_deep_amortization_7g import (
    build_mismatch_manifest,
    select_checkpoint,
    selection_diagnostics,
)
from scripts.run_deep_residual_amortized_one_step_7f import (
    _pair_evaluation_seconds,
    _preflight,
)


def _rows() -> list[dict[str, str]]:
    return [
        {
            "pair_id": f"p{index}",
            "state_example_id": f"s{index}",
            "state_task_id": f"task{index}",
            "transition_id": f"m{index}",
            "signature_class_id": f"class{index}",
        }
        for index in range(4)
    ]


def test_mismatch_manifest_is_deterministic_and_disjoint() -> None:
    first = build_mismatch_manifest(_rows())
    second = build_mismatch_manifest(_rows())
    assert first == second
    assert first["uses_training_rows_only"] is True
    assert first["uses_heldout_outcomes"] is False
    for row in first["rows"]:
        source = next(value for value in _rows() if value["pair_id"] == row["pair_id"])
        assert row["transition_mismatch_transition_id"] != source["transition_id"]
        assert row["state_mismatch_state_example_id"] != source["state_example_id"]
        assert row["transition_signature_differs"] is True
        assert row["state_task_differs"] is True


def test_duplicate_pair_ids_are_rejected() -> None:
    rows = _rows()
    rows.append(dict(rows[0]))
    with pytest.raises(ValueError, match="duplicate"):
        build_mismatch_manifest(rows)


def test_selection_diagnostics_rewards_correct_and_quiet_mismatches() -> None:
    value = selection_diagnostics(
        {
            "correct_raw_policy_kl": 0.2,
            "zero_raw_policy_kl": 1.0,
            "transition_mismatch_bare_policy_kl": 0.1,
            "state_mismatch_bare_policy_kl": 0.2,
        }
    )
    assert value["raw_policy_kl_improvement"] == pytest.approx(0.8)
    assert value["transition_specificity"] == pytest.approx(0.7)
    assert value["state_specificity"] == pytest.approx(0.6)
    assert value["selection_score"] == pytest.approx(0.65)


def test_checkpoint_selection_uses_a_validation_specificity() -> None:
    base = {
        "maximum_ratio": 0.9,
        "a_validation": {
            "zero_raw_policy_kl": 1.0,
            "transition_mismatch_bare_policy_kl": 0.1,
            "state_mismatch_bare_policy_kl": 0.1,
        },
    }
    history = [
        {
            **base,
            "updates_per_pair": 2,
            "a_validation": {**base["a_validation"], "correct_raw_policy_kl": 0.5},
        },
        {
            **base,
            "updates_per_pair": 4,
            "a_validation": {**base["a_validation"], "correct_raw_policy_kl": 0.2},
        },
    ]
    selected = select_checkpoint(history)
    assert selected["updates_per_pair"] == 4
    assert selected["selection_constraints_passed"] is True


def test_one_step_preflight_has_7g_runtime_fallback() -> None:
    runtime = {
        "one_step_generation_seconds_expected": 7.85,
        "policy_forward_seconds_expected": 1.85,
    }
    assert _pair_evaluation_seconds(runtime) == pytest.approx(1.85)
    assert _pair_evaluation_seconds(
        {**runtime, "pair_evaluation_seconds_expected": 3.7}
    ) == pytest.approx(3.7)
    assert callable(_preflight)
