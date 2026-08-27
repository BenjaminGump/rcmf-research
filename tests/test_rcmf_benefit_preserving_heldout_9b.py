from scripts.run_rcmf_benefit_preserving_heldout_9b import (
    _condition_key,
    candidate_gate,
)


def _summary() -> dict:
    return {
        "L0_zero": {
            "exact_api": 0.40,
            "action_signature": 0.30,
            "semantic_successor": 0.35,
            "execution": 0.95,
        },
        "L1_correct": {
            "exact_api": 0.60,
            "action_signature": 0.55,
            "semantic_successor": 0.50,
            "execution": 0.94,
        },
        "L2_key_payload_shuffle": {
            "exact_api": 0.50,
            "action_signature": 0.45,
            "semantic_successor": 0.40,
            "execution": 0.94,
        },
        "L3_state_query_shuffle": {
            "exact_api": 0.45,
            "action_signature": 0.40,
            "semantic_successor": 0.40,
            "execution": 0.94,
        },
        "positive_task_count": 4,
    }


def test_condition_keys_are_deterministic_and_control_specific() -> None:
    first = _condition_key("Q50", "state-1", "L1_correct")
    assert first == _condition_key("Q50", "state-1", "L1_correct")
    assert first != _condition_key("Q50", "state-1", "L2_key_payload_shuffle")
    assert first != _condition_key("Q90", "state-1", "L1_correct")


def test_candidate_gate_requires_preregistered_heldout_checks() -> None:
    summary = _summary()
    assert candidate_gate(summary, original_d1_execution_count=93)["passed"]

    changed = _summary()
    changed["L1_correct"]["exact_api"] = changed["L2_key_payload_shuffle"]["exact_api"]
    assert not candidate_gate(changed, original_d1_execution_count=93)["passed"]

    changed = _summary()
    changed["L1_correct"]["action_signature"] = changed["L2_key_payload_shuffle"]["action_signature"]
    assert not candidate_gate(changed, original_d1_execution_count=93)["passed"]

    changed = _summary()
    changed["positive_task_count"] = 3
    assert not candidate_gate(changed, original_d1_execution_count=93)["passed"]

    changed = _summary()
    changed["L1_correct"].update(
        exact_api=0.40, action_signature=0.30, semantic_successor=0.35
    )
    changed["L2_key_payload_shuffle"].update(exact_api=0.20, action_signature=0.20)
    assert not candidate_gate(changed, original_d1_execution_count=93)["passed"]


def test_candidate_gate_allows_only_one_execution_regression_state() -> None:
    summary = _summary()
    summary["L1_correct"]["execution"] = 91 / 98
    assert not candidate_gate(summary, original_d1_execution_count=93)["passed"]
    summary["L1_correct"]["execution"] = 92 / 98
    assert candidate_gate(summary, original_d1_execution_count=93)["passed"]
