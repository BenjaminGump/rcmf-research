from __future__ import annotations

from scripts.evaluate_cross_attention_reader_policy_8b_v4 import (
    _cross_prompt_policy_row,
)


def test_cross_prompt_derives_prefix_from_input_and_target_lengths() -> None:
    row = {
        "input_ids": [10, 11, 12, 20, 21],
        "labels": [-100, -100, -100, 20, 21],
        "target_len": 2,
        "pad_token_id": 0,
        "last_user_token_indices": [0, 1, 2, 2],
    }
    result = _cross_prompt_policy_row(
        row,
        {"generated_token_ids": [30, 31, 32]},
        pair_id="schema-test",
    )
    assert result["input_ids"] == [10, 11, 12, 30, 31, 32]
    assert result["labels"] == [-100, -100, -100, 30, 31, 32]
    assert result["prompt_len"] == 3
    assert result["target_len"] == 3


def test_cross_prompt_rejects_invalid_derived_lengths() -> None:
    row = {
        "input_ids": [10, 11],
        "target_len": 2,
        "pad_token_id": 0,
        "last_user_token_indices": [0, 0, 0, 0],
    }
    try:
        _cross_prompt_policy_row(
            row, {"generated_token_ids": [30]}, pair_id="invalid"
        )
    except ValueError as error:
        assert "nonempty prompt" in str(error)
    else:
        raise AssertionError("Invalid derived prompt length was accepted")
