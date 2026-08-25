from __future__ import annotations

from scripts.finalize_cross_attention_reader_policy_gate_8b import (
    finalize_policy_summary,
)


def _report(epoch: int, *, zero: float, correct: float, transition: float, state: float) -> dict:
    return {
        "epoch": epoch,
        "checkpoint_sha256": f"checkpoint-{epoch}",
        "positive_raw_teacher_policy_kl": {
            "X0_no_memory": zero,
            "X1_correct_memory": correct,
            "X2_transition_shuffle": transition,
            "X3_state_shuffle": state,
        },
    }


def test_policy_gate_failure_stops_before_live_and_field() -> None:
    result = finalize_policy_summary(
        {
            "checkpoint_count": 2,
            "reports": [
                _report(1, zero=0.58, correct=0.84, transition=0.98, state=1.77),
                _report(2, zero=0.58, correct=1.35, transition=1.25, state=2.42),
            ],
        }
    )
    assert result["eligible_policy_checkpoint_epochs"] == []
    assert result["best_diagnostic_epoch"] == 1
    assert result["heldout_live_status"] == "not_run_blocked_by_policy_gate"
    assert result["decision_branch"] == (
        "published_cross_attention_reader_failed_on_appworld"
    )
    assert result["reversible_field_authorized"] is False


def test_policy_gate_pass_requires_live_before_any_field_authorization() -> None:
    result = finalize_policy_summary(
        {
            "checkpoint_count": 1,
            "reports": [
                _report(1, zero=1.0, correct=0.2, transition=0.8, state=0.9),
            ],
        }
    )
    assert result["eligible_policy_checkpoint_epochs"] == [1]
    assert result["heldout_live_status"] == "required_before_reader_selection"
    assert result["decision_branch"] == (
        "reader_policy_gate_passed_live_validation_required"
    )
    assert result["reversible_field_authorized"] is False
