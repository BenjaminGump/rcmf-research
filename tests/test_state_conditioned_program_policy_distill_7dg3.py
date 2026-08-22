from __future__ import annotations

import copy

import pytest
import torch

from rcmf.training.state_conditioned_program_policy_distill_7dg3 import (
    GLOBAL_SEED,
    POLICY_CONDITIONS,
    build_policy_behavior_manifest,
    build_policy_pair_manifest,
    policy_evaluation_diagnostics,
    sparse_policy_kl,
)


def _row(cell: str, index: int, task: int) -> dict[str, object]:
    return {
        "cell": cell,
        "pair_id": f"{cell}-pair-{index}",
        "pair_role": f"role-{index % 3}",
        "state_example_id": f"state-{cell}-{index}",
        "state_task_id": f"task-{task}",
        "transition_id": f"transition-{cell}-{index % 11}",
        "transition_parent_id": f"parent-{index % 7}",
        "transition_parent_task_id": f"parent-task-{index % 7}",
        "signature_class_id": f"signature-{index % 13}",
        "valid_for_teacher_cache": True,
        "truncated": False,
        "teacher_prompt_tokens": 100 + index,
    }


def test_policy_pair_manifest_is_deterministic_and_task_complete() -> None:
    a_rows = [_row("A", index, index % 4) for index in range(28)]
    manifests = {
        "A": a_rows,
        **{cell: [_row(cell, index, index % 3) for index in range(12)] for cell in "BCDE"},
    }
    split = {
        "train_indices": list(range(20)),
        "validation_indices": list(range(20, 28)),
        "train_task_ids": [f"task-{value}" for value in range(4)],
    }
    kwargs = {
        "training_count": 12,
        "evaluation_counts": {
            "A_validation": 4,
            "B": 4,
            "C": 4,
            "D": 4,
            "E": 4,
        },
        "context_limit": 40960,
        "max_new_tokens": 512,
        "seed": GLOBAL_SEED,
    }
    first = build_policy_pair_manifest(manifests, split, **kwargs)
    second = build_policy_pair_manifest(manifests, split, **kwargs)
    assert first == second
    assert first["training_count"] == 12
    assert first["training_task_count"] == 4
    assert first["logical_pair_count"] == 32
    assert set(first["evaluation_counts"].values()) == {4}
    assert first["manifest_sha256"]


def test_policy_pair_manifest_rejects_non_global_seed() -> None:
    manifests = {cell: [_row(cell, index, 0) for index in range(3)] for cell in "ABCDE"}
    split = {
        "train_indices": [0, 1],
        "validation_indices": [2],
        "train_task_ids": ["task-0"],
    }
    with pytest.raises(ValueError, match="GLOBAL_SEED"):
        build_policy_pair_manifest(
            manifests,
            split,
            training_count=1,
            evaluation_counts={
                "A_validation": 1,
                "B": 1,
                "C": 1,
                "D": 1,
                "E": 1,
            },
            context_limit=40960,
            max_new_tokens=512,
            seed=7,
        )


def test_sparse_policy_kl_is_zero_for_matching_bucket_distribution() -> None:
    logits = torch.tensor([[2.0, 1.0, 0.0]], requires_grad=True)
    log_probs = torch.log_softmax(logits.detach().to(torch.float64), dim=-1)[0]
    selected = [0, 1]
    teacher = [
        {
            "top_token_ids": selected,
            "top_logprobs": [float(log_probs[index]) for index in selected],
            "other_probability": float(log_probs[2].exp()),
        }
    ]
    loss = sparse_policy_kl(logits, teacher)
    assert float(loss.detach()) == pytest.approx(0.0, abs=1.0e-6)
    loss.backward()
    assert logits.grad is not None


def test_policy_diagnostics_measure_correct_kl_reduction() -> None:
    controls = {
        name: {
            "policy_kl": value,
            "teacher_token_nll": 1.0,
            "teacher_token_top1_accuracy": 0.5,
            "ground_truth_nll": 1.0,
            "maximum_ratio": 0.5,
        }
        for name, value in {
            "correct": 0.4,
            "state_shuffle": 0.7,
            "transition_shuffle": 0.8,
            "zero": 0.9,
        }.items()
    }
    result = policy_evaluation_diagnostics(controls)
    assert result["finite"]
    assert result["correct_minus_zero_policy_kl_reduction"] == pytest.approx(0.5)
    assert result["correct_minus_state_shuffle_policy_kl_reduction"] == pytest.approx(0.3)


def test_policy_behavior_manifest_preserves_frozen_pairings() -> None:
    source_conditions = []
    for index, name in enumerate(
        (
            "P1_pairmlp_correct",
            "P2_pairmlp_shuffled_transition",
            "P3_pairmlp_shuffled_state",
        )
    ):
        source_conditions.append(
            {
                "format": "old",
                "condition_key": f"old-{index}",
                "condition_name": name,
                "state_example_id": "state-1",
                "program_state_id": "state-2" if index == 2 else "state-1",
                "program_transition_id": "transition-2" if index == 1 else "transition-1",
                "selector_transition_id": "transition-1",
                "student_prompt_contains_raw_transition": False,
            }
        )
    frozen = {
        "manifest_sha256": "frozen-sha",
        "conditions": source_conditions,
    }
    result = build_policy_behavior_manifest(
        copy.deepcopy(frozen), checkpoint_provenance={"sha256": "checkpoint"}
    )
    assert result["condition_count"] == 3
    assert set(result["condition_name_counts"]) == set(POLICY_CONDITIONS)
    assert all(not row["student_prompt_contains_raw_transition"] for row in result["conditions"])
    assert result["conditions"][1]["program_transition_id"] == "transition-2"
    assert result["conditions"][2]["program_state_id"] == "state-2"
