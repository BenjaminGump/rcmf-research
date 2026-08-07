from __future__ import annotations

import math

import torch

from rcmf.training.pair_grounding_5d import (
    PAIR_RESPONSE_CACHE_VERSION,
    PAIR_RESPONSE_SCORING_DEFINITION,
    PairSelectionConfig,
    SingleMemoryProgramModel,
    add_teacher_delta_fields,
    deterministic_memory_folds,
    select_stratified_pair_set,
    validate_pair_response_cache,
)
from scripts.run_stage_c_pair_grounding_5d import _student_pair_terms


def _label_row(state: int, split: str, utilities: list[float | None]) -> dict:
    valid = [value is not None for value in utilities]
    return {
        "state_index": state,
        "state_example_id": f"s{state}",
        "task_id": f"task_{state}",
        "episode_id": f"episode_{state}",
        "step_id": state,
        "split": split,
        "ordered_effective_memory_ids": [f"m{i}" for i in range(len(utilities))],
        "valid_mask": valid,
        "legal_effective_mask": valid,
        "raw_utility": utilities,
        "source_pair_keys": [f"e{state}:m{i}" if ok else None for i, ok in enumerate(valid)],
        "target_sha256_by_memory": [f"target-{state}" if ok else None for ok in valid],
        "memory_text_sha256_by_memory": [f"memory-{i}" if ok else None for i, ok in enumerate(valid)],
        "L0": 1.0,
    }


def test_pair_selection_reports_missing_categories_without_replacement() -> None:
    memory_bank = [
        {"memory_index": 0, "memory_id": "m0", "task_id": "mem_task0", "episode_id": "mem_ep0"},
        {"memory_index": 1, "memory_id": "m1", "task_id": "mem_task1", "episode_id": "mem_ep1"},
    ]
    rows = [
        _label_row(0, "train", [0.4, -0.2]),
        _label_row(1, "train", [0.2, -0.1]),
        _label_row(2, "train", [0.0, None]),
        _label_row(3, "validation", [0.3, -0.3]),
        _label_row(4, "validation", [0.005, -0.2]),
    ]

    selected, summary = select_stratified_pair_set(
        rows,
        memory_bank,
        config=PairSelectionConfig(train_per_category=2, validation_per_category=1, seed=5),
    )

    assert len({row["pair_id"] for row in selected}) == len(selected)
    assert summary["by_split_category"]["train"]["positive"] == 2
    assert summary["by_split_category"]["train"]["negative"] == 2
    assert summary["missing_category_slot_count"] > 0
    assert any(item["category"] == "neutral" for item in summary["missing_category_slots"])


def test_add_teacher_delta_fields_and_cache_validation() -> None:
    base = math.log(0.4)
    teacher = math.log(0.5)
    positions = add_teacher_delta_fields(
        [
            {
                "target_position_index": 0,
                "target_token_id": 1,
                "baseline_target_logprob": base,
                "teacher_target_logprob": teacher,
                "baseline_union_logprobs": [base, math.log(0.2)],
                "teacher_union_logprobs": [teacher, math.log(0.1)],
                "baseline_other_probability": 0.4,
                "teacher_other_probability": 0.4,
                "union_token_ids": [1, 2],
                "baseline_logsumexp": 0.0,
                "teacher_logsumexp": 0.0,
            }
        ]
    )
    row = {
        "format": PAIR_RESPONSE_CACHE_VERSION,
        "scoring_definition": PAIR_RESPONSE_SCORING_DEFINITION,
        "pair_id": "s0::memory::m0",
        "pair_key": "e0:m0",
        "state_example_id": "s0",
        "memory_id": "m0",
        "split": "train",
        "target_tokens": 1,
        "context_limit": 10,
        "teacher_total_tokens_with_target": 5,
        "baseline_mean_target_nll": -base,
        "teacher_mean_target_nll": -teacher,
        "L0": -base,
        "Lj_text": -teacher,
        "text_utility": -base + teacher,
        "memory_text_sha256": "mh",
        "target_positions": positions,
    }
    validation = validate_pair_response_cache(
        [row],
        selected_pairs=[
            {
                "pair_id": "s0::memory::m0",
                "pair_key": "e0:m0",
                "state_example_id": "s0",
                "memory_id": "m0",
                "split": "train",
                "memory_text_sha256": "mh",
            }
        ],
        teacher_rows={
            "e0:m0": {
                "candidate_memory_id": "m0",
                "valid_for_loss": True,
                "leakage_overlap": [],
                "memory_text_sha256": "mh",
            }
        },
    )

    assert positions[0]["delta_teacher_union_logprobs"][0] == teacher - base
    assert validation["passed"] is True


def test_single_memory_program_read_selects_only_requested_memory() -> None:
    model = SingleMemoryProgramModel(memory_dim=3, memory_count=4, program_dim=2, program_kind="content")
    programs = torch.tensor([[1.0, 0.0], [0.0, 2.0], [3.0, 0.0], [0.0, 4.0]])
    model.program_head = torch.nn.Identity()
    memory_reps = programs.clone()
    indices = torch.tensor([3, 1])

    z = model.z_for_memory_indices(memory_reps, indices)

    assert torch.allclose(z, torch.tensor([[0.0, 4.0], [0.0, 2.0]]))


def test_neutral_preservation_loss_has_student_delta_gradient() -> None:
    logits = torch.tensor([[0.0, 0.8, -0.4]], requires_grad=True)
    labels = torch.tensor([[-100, 1]])
    baseline = torch.log_softmax(torch.tensor([0.3, 0.2, -0.1], dtype=torch.float64), dim=-1)
    teacher = baseline.clone()
    response = {
        "pair_id": "s0::memory::m0",
        "pair_key": "e0:m0",
        "state_example_id": "s0",
        "memory_id": "m0",
        "split": "train",
        "selection_category": "neutral",
        "utility_category": "neutral",
        "memory_stage_index": 0,
        "text_utility": 0.0,
        "baseline_mean_target_nll": -float(baseline[1]),
        "teacher_mean_target_nll": -float(teacher[1]),
        "prompt_tokens": 1,
        "teacher_prompt_tokens": 1,
        "raw_memory_tokens": 1,
        "target_tokens": 1,
        "target_positions": add_teacher_delta_fields(
            [
                {
                    "target_position_index": 0,
                    "target_token_id": 1,
                    "baseline_target_logprob": float(baseline[1]),
                    "teacher_target_logprob": float(teacher[1]),
                    "baseline_union_logprobs": [float(baseline[0]), float(baseline[1])],
                    "teacher_union_logprobs": [float(teacher[0]), float(teacher[1])],
                    "baseline_other_probability": float(baseline[2].exp()),
                    "teacher_other_probability": float(teacher[2].exp()),
                    "union_token_ids": [0, 1],
                    "baseline_logsumexp": 0.0,
                    "teacher_logsumexp": 0.0,
                }
            ]
        ),
    }

    terms, _ = _student_pair_terms(logits, labels, [response], target_lengths=[1], huber_delta=0.1)
    terms["neutral_preservation"].backward()

    assert logits.grad is not None
    assert float(logits.grad.abs().sum()) > 0.0


def test_memory_folds_hold_out_every_memory_once() -> None:
    folds = deterministic_memory_folds(36, folds=5, seed=41)
    heldout = [index for fold in folds for index in fold["heldout_memory_stage_indices"]]

    assert sorted(heldout) == list(range(36))
    assert all(set(fold["train_memory_stage_indices"]).isdisjoint(fold["heldout_memory_stage_indices"]) for fold in folds)
