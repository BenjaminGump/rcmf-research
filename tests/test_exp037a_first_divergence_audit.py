from pathlib import Path

import pytest

from scripts.audit_exp037a_first_divergence import (
    CONTEXT_LIMIT,
    _ordered_identity,
    assert_output_isolated,
    context_budget_row,
    deterministic_matched_controls,
    read_json,
    validate_missing_partition,
)


def test_audit_output_must_be_outside_immutable_roots(tmp_path: Path) -> None:
    historical = tmp_path / "historical"
    fresh = tmp_path / "fresh"
    historical.mkdir()
    fresh.mkdir()

    with pytest.raises(ValueError, match="immutable run root"):
        assert_output_isolated(historical / "audit", (historical, fresh))

    output = tmp_path / "diagnostics" / "audit"
    assert_output_isolated(output, (historical, fresh))
    assert not output.exists()


@pytest.mark.parametrize("suffix", [".pt", ".pth", ".pkl", ".safetensors"])
def test_historical_selector_checkpoint_cannot_be_deserialized(
    tmp_path: Path, suffix: str
) -> None:
    checkpoint = tmp_path / f"historical_selector{suffix}"
    checkpoint.write_bytes(b"not a real checkpoint")

    with pytest.raises(ValueError, match="deserialization is forbidden"):
        read_json(checkpoint)


def test_state_set_identity_is_deterministic_and_order_sensitive() -> None:
    first = _ordered_identity(["state-b", "state-a"])
    repeated = _ordered_identity(["state-b", "state-a"])
    reordered = _ordered_identity(["state-a", "state-b"])

    assert first == repeated
    assert first["ordered_sha256"] != reordered["ordered_sha256"]
    assert first["set_sha256"] == reordered["set_sha256"]


def test_missing_partition_preserves_19_5_0_accounting() -> None:
    over_context = {f"over-{index}" for index in range(19)}
    replay = {f"replay-{index}" for index in range(5)}
    completed = {f"completed-{index}" for index in range(440)}
    initial = completed | over_context | replay

    partition = validate_missing_partition(
        initial,
        completed,
        over_context,
        replay,
        expected_missing_count=24,
    )

    assert partition["over_context_count"] == 19
    assert partition["replay_count"] == 5
    assert partition["overlap_count"] == 0
    assert len(partition["missing"]) == 24


def test_missing_partition_rejects_claimed_23_5_4_overlap() -> None:
    over_context = {f"state-{index}" for index in range(23)}
    replay = {"state-19", "state-20", "state-21", "state-22", "replay-only"}
    initial = over_context | replay

    with pytest.raises(ValueError, match="unexpectedly overlap"):
        validate_missing_partition(
            initial,
            set(),
            over_context,
            replay,
            expected_missing_count=24,
        )


def test_context_budget_arithmetic_uses_rendered_input_only() -> None:
    historical = {
        "base_prompt_sha256": "a" * 64,
        "base_prompt_tokens": 39000,
        "selected_class_id": "class-old",
        "selected_transition_id": "memory-old",
        "over_context": False,
        "attempts": [
            {"transition_id": "memory-old", "prompt_tokens": CONTEXT_LIMIT}
        ],
    }
    fresh = {
        "base_prompt_sha256": "a" * 64,
        "base_prompt_tokens": 39000,
        "selected_class_id": "class-new",
        "selected_transition_id": "memory-new",
        "over_context": True,
        "attempts": [
            {"transition_id": "memory-new", "prompt_tokens": CONTEXT_LIMIT + 1}
        ],
    }
    transitions = {
        "memory-old": {
            "transition_content_sha256": "b" * 64,
            "teacher_section_sha256": "c" * 64,
            "teacher_section_tokens": 1900,
        },
        "memory-new": {
            "transition_content_sha256": "d" * 64,
            "teacher_section_sha256": "e" * 64,
            "teacher_section_tokens": 2000,
        },
    }

    row = context_budget_row(
        "state-1", historical, fresh, transitions, transitions
    )
    historical_attempt = row["historical"]["attempts"][0]
    fresh_attempt = row["fresh"]["attempts"][0]

    assert historical_attempt["decision"] == "PASS"
    assert historical_attempt["headroom"] == 0
    assert fresh_attempt["decision"] == "FAIL"
    assert fresh_attempt["headroom"] == -1
    assert fresh_attempt["generation_reservation_tokens_for_admission"] == 0
    assert fresh_attempt["framework_overhead_outside_counted_render"] == 0
    assert fresh_attempt["effective_admission_tokens"] == CONTEXT_LIMIT + 1


def test_matched_control_selection_is_deterministic() -> None:
    selections = {
        "missing-a": {"state_task_id": "task-a", "base_prompt_tokens": 100},
        "missing-b": {"state_task_id": "task-b", "base_prompt_tokens": 200},
        "control-a1": {"state_task_id": "task-a", "base_prompt_tokens": 99},
        "control-a2": {"state_task_id": "task-a", "base_prompt_tokens": 130},
        "control-b1": {"state_task_id": "task-b", "base_prompt_tokens": 203},
    }
    completed = ["control-a1", "control-a2", "control-b1"]

    first = deterministic_matched_controls(
        ["missing-b", "missing-a"], completed, selections
    )
    second = deterministic_matched_controls(
        ["missing-b", "missing-a"], reversed(completed), selections
    )

    assert first == second == ["control-a1", "control-b1"]
