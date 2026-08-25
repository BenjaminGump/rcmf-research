from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.training.cross_attention_checkpoint_compat_8b import (
    checkpoint_safe_reader_forward,
)
import scripts.run_cross_attention_reader_8b as reader_base
import scripts.evaluate_cross_attention_reader_policy_8b as evaluation


def _cross_prompt_policy_row(
    prompt_row: Mapping[str, Any], teacher: Mapping[str, Any], *, pair_id: str
) -> dict[str, Any]:
    input_ids = [int(value) for value in prompt_row["input_ids"]]
    target_len = int(prompt_row["target_len"])
    prompt_length = len(input_ids) - target_len
    if prompt_length <= 0 or target_len <= 0:
        raise ValueError("Policy row cannot derive a nonempty prompt and target")
    prefix = input_ids[:prompt_length]
    target = [int(value) for value in teacher["generated_token_ids"]]
    return {
        "pair_id": pair_id,
        "input_ids": prefix + target,
        "labels": [-100] * len(prefix) + target,
        "pad_token_id": int(prompt_row["pad_token_id"]),
        "last_user_token_indices": [
            int(value) for value in prompt_row["last_user_token_indices"]
        ],
        "target_len": len(target),
        "prompt_len": len(prefix),
        "response_cache": {},
        "student_prompt_contains_raw_memory": False,
    }


def main() -> None:
    reader_base._forward = checkpoint_safe_reader_forward
    evaluation._cross_prompt_policy_row = _cross_prompt_policy_row
    evaluation.main()


if __name__ == "__main__":
    main()
