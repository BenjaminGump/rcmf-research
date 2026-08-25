from __future__ import annotations

from rcmf.training.cross_attention_validation_8b import (
    classify_live_reader,
    policy_gate_passes,
    select_reader_checkpoint,
)


def _summary(correct_signature: float = 0.7) -> dict:
    return {
        "X0_no_memory": {"action_signature": 0.4, "semantic_successor": 0.5, "execution": 1.0},
        "X1_correct_memory": {"action_signature": correct_signature, "semantic_successor": 0.7, "execution": 1.0},
        "X2_transition_shuffle": {"action_signature": 0.5, "semantic_successor": 0.5, "execution": 1.0},
        "X3_state_shuffle": {"action_signature": 0.5, "semantic_successor": 0.6, "execution": 1.0},
        "positive_task_count": 5,
        "task_count": 8,
    }


def _policy(correct: float = 0.2) -> dict:
    return {
        "positive_raw_teacher_policy_kl": {
            "X0_no_memory": 1.0,
            "X1_correct_memory": correct,
            "X2_transition_shuffle": 0.8,
            "X3_state_shuffle": 0.9,
        }
    }


def test_strong_live_reader_classification() -> None:
    assert classify_live_reader(_summary()) == "STRONG"


def test_policy_gate_requires_both_shuffle_controls() -> None:
    assert policy_gate_passes(_policy())
    assert not policy_gate_passes(_policy(correct=0.95))


def test_checkpoint_selection_prefers_strong_then_live_specificity() -> None:
    weak = _summary(correct_signature=0.6)
    strong = _summary(correct_signature=0.8)
    selected = select_reader_checkpoint(
        [
            {"epoch": 1, "live_summary": weak, "policy_evaluation": _policy()},
            {"epoch": 2, "live_summary": strong, "policy_evaluation": _policy()},
        ]
    )
    assert selected is not None
    assert selected["epoch"] == 2


def test_checkpoint_selection_rejects_policy_failure() -> None:
    assert (
        select_reader_checkpoint(
            [{"epoch": 1, "live_summary": _summary(), "policy_evaluation": _policy(0.95)}]
        )
        is None
    )
