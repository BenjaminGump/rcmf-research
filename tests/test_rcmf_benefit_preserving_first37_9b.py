from __future__ import annotations

import inspect

from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    CalibratedFieldReaderHooks,
)
from scripts.run_rcmf_benefit_preserving_first37_9b import (
    _candidate,
    _candidate_hook,
    _family_status,
    scientific_decision,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _generate, _run_task


def _settings() -> dict:
    return {
        "critical_states": {
            "gains": [
                "0d01c76_3",
                "325d6ec_2",
                "325d6ec_3",
                "634f342_1",
                "634f342_2",
                "634f342_3",
            ],
            "retained": ["8749218_2", "8749218_3"],
            "losses": [
                "0d01c76_1",
                "0d01c76_2",
                "29a7b7e_3",
                "325d6ec_1",
                "8749218_1",
                "d6ac34d_2",
            ],
        },
        "first37": {
            "gain_families": {
                "cross_app_import": ["0d01c76_3"],
                "spotify_state_machine": ["325d6ec_2", "325d6ec_3"],
                "exact_set_migration": [
                    "634f342_1",
                    "634f342_2",
                    "634f342_3",
                ],
            }
        },
    }


def test_l1_candidate_is_frozen_layer_scale_only() -> None:
    candidate = _candidate()
    assert candidate.candidate_id == "L1"
    assert candidate.route == "layer_scale"
    assert candidate.layer_scales == (1.0, 1.0, 1.0, 0.5)
    assert candidate.field_control == "correct"
    assert not candidate.critical_diagnostic_only
    assert not candidate.outcomes_used


def test_candidate_hook_uses_calibrated_reader() -> None:
    source = inspect.getsource(_candidate_hook)
    assert "CalibratedFieldReaderHooks" in source
    assert "layer_scales=candidate.layer_scales" in source
    assert CalibratedFieldReaderHooks.__name__ in source


def test_parent_first37_extensions_are_default_off() -> None:
    generation = inspect.signature(_generate)
    task = inspect.signature(_run_task)
    assert generation.parameters["hook_factory"].default is None
    assert task.parameters["hook_factory"].default is None
    assert task.parameters["extra_result_fields"].default is None


def test_family_status_requires_all_three_families() -> None:
    status = _family_status(
        {
            "0d01c76_3",
            "325d6ec_2",
            "634f342_1",
            "8749218_2",
            "8749218_3",
        },
        _settings(),
    )
    assert all(row["represented"] for row in status.values())
    status = _family_status(
        {"325d6ec_2", "634f342_1"},
        _settings(),
    )
    assert not status["cross_app_import"]["represented"]


def test_scientific_decision_proceeds_only_when_all_gates_pass() -> None:
    d0 = {
        "0d01c76_1", "0d01c76_2", "29a7b7e_3", "325d6ec_1",
        "8749218_1", "8749218_2", "8749218_3", "d6ac34d_2",
    }
    correct = {
        "0d01c76_3", "325d6ec_2", "325d6ec_3",
        "634f342_1", "634f342_2", "634f342_3",
        "8749218_2", "8749218_3", "0d01c76_1", "29a7b7e_3",
    }
    shuffled = {"8749218_2", "8749218_3", "0d01c76_1", "29a7b7e_3"}
    result = scientific_decision(
        d0_success=d0,
        original_d1_success=correct - {"0d01c76_1", "29a7b7e_3"},
        correct_success=correct,
        shuffled_success=shuffled,
        settings_9b=_settings(),
    )
    assert result["scientific_decision"].startswith("PROCEED")
    assert all(result["gates"].values())


def test_scientific_decision_stops_on_two_lost_gains() -> None:
    d0 = {
        "0d01c76_1", "0d01c76_2", "29a7b7e_3", "325d6ec_1",
        "8749218_1", "8749218_2", "8749218_3", "d6ac34d_2",
    }
    correct = {
        "0d01c76_3", "325d6ec_2", "634f342_1", "634f342_2",
        "8749218_2", "8749218_3", "0d01c76_1", "29a7b7e_3",
        "taskx_1", "tasky_1",
    }
    result = scientific_decision(
        d0_success=d0,
        original_d1_success=set(),
        correct_success=correct,
        shuffled_success=set(),
        settings_9b=_settings(),
    )
    assert result["scientific_decision"] == "STOP_ROUTE"
    assert result["stop_reasons"]["two_or_more_original_gains_lost"]


def test_scientific_decision_is_inconclusive_at_one_task_shuffle_margin() -> None:
    d0 = {"8749218_2", "8749218_3"}
    correct = {
        "0d01c76_3", "325d6ec_2", "325d6ec_3",
        "634f342_1", "634f342_2", "634f342_3",
        "8749218_2", "8749218_3", "0d01c76_1", "29a7b7e_3",
    }
    shuffled = set(sorted(correct)[:-1])
    result = scientific_decision(
        d0_success=d0,
        original_d1_success=correct,
        correct_success=correct,
        shuffled_success=shuffled,
        settings_9b=_settings(),
    )
    assert result["scientific_decision"] == "INCONCLUSIVE"
    assert not result["gates"]["correct_at_least_2_over_shuffle"]
