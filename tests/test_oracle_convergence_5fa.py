from __future__ import annotations

import copy

import pytest
import torch

from rcmf.training.oracle_capacity_5e import scatter_token_delta
from rcmf.training.oracle_convergence_5fa import (
    IndependentPairTensorTable,
    apply_independent_optimizer_step,
    assess_plateau,
    load_training_checkpoint,
    save_training_checkpoint,
    select_convergence_subset,
    sequence_utility_from_nll,
    sequence_utility_loss,
    update_count_summary,
)
from scripts.run_stage_c_oracle_convergence_5fa import _student_prompt_contract


def _rows() -> list[dict]:
    rows = []
    for category in ("positive", "neutral", "negative", "random"):
        for memory_index in range(36):
            rows.append(
                {
                    "pair_id": f"{category}-{memory_index}",
                    "selection_category": category,
                    "memory_stage_index": memory_index,
                }
            )
    return rows


def _step(
    table: IndependentPairTensorTable,
    optimizer: torch.optim.Optimizer,
    counts: list[int],
    index: int,
    target: float,
) -> None:
    value = table.forward_indices([index])
    loss = (value - target).pow(2).mean()
    apply_independent_optimizer_step(
        optimizer=optimizer,
        loss=loss,
        table=table,
        selected_indices=[index],
        update_counts=counts,
    )


def test_one_round_is_exactly_one_update_per_pair_and_unselected_rows_do_not_move() -> None:
    table = IndependentPairTensorTable(["a", "b", "c"], (1,))
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.1, weight_decay=0.0)
    counts = [0, 0, 0]
    untouched_before = [table.rows[1].detach().clone(), table.rows[2].detach().clone()]

    _step(table, optimizer, counts, 0, 1.0)

    assert counts == [1, 0, 0]
    assert torch.equal(table.rows[1], untouched_before[0])
    assert torch.equal(table.rows[2], untouched_before[1])

    _step(table, optimizer, counts, 1, 1.0)
    _step(table, optimizer, counts, 2, 1.0)

    assert update_count_summary(table.pair_ids, counts)["minimum_updates_per_pair"] == 1
    assert update_count_summary(table.pair_ids, counts)["maximum_updates_per_pair"] == 1


def test_n_rounds_give_exactly_n_updates_per_pair() -> None:
    table = IndependentPairTensorTable(["a", "b"], (1,))
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.1, weight_decay=0.0)
    counts = [0, 0]

    for _ in range(7):
        _step(table, optimizer, counts, 0, 1.0)
        _step(table, optimizer, counts, 1, -1.0)

    accounting = update_count_summary(table.pair_ids, counts)
    assert accounting["updates_per_pair"] == {"a": 7, "b": 7}
    assert accounting["mean_updates_per_pair"] == 7.0
    assert accounting["all_pairs_equal"] is True


def test_ratio_projection_is_applied_after_each_selected_step() -> None:
    table = IndependentPairTensorTable(["a", "b"], (2,))
    optimizer = torch.optim.AdamW(table.parameters(), lr=10.0, weight_decay=0.0)
    counts = [0, 0]
    selected = table.forward_indices([0])
    loss = -(selected.sum())

    apply_independent_optimizer_step(
        optimizer=optimizer,
        loss=loss,
        table=table,
        selected_indices=[0],
        update_counts=counts,
        base_norms=torch.tensor([2.0, 2.0]),
        ratio_budget=0.5,
    )

    assert float(table.rows[0].detach().norm()) <= 1.0 + 1.0e-6
    assert torch.equal(table.rows[1], torch.zeros_like(table.rows[1]))


def test_zero_direct_delta_is_exact_embedding_equivalent() -> None:
    base = torch.randn(2, 8, 5)
    indices = torch.tensor([[1, 2, 3, 4], [0, 5, 6, 7]])
    zero = torch.zeros(2, 4, 5)

    scattered = scatter_token_delta(base_embeddings=base, selected_indices=indices, delta_slots=zero)

    assert torch.equal(base + scattered, base)


def test_sequence_utility_identity_and_gradient() -> None:
    student_nll = torch.tensor([0.9], requires_grad=True)
    baseline_nll = torch.tensor([1.0])
    teacher_utility = torch.tensor([0.2])

    terms = sequence_utility_loss(
        baseline_nll=baseline_nll,
        student_nll=student_nll,
        teacher_utility=teacher_utility,
        huber_delta=0.1,
    )
    terms["sequence_utility_huber"].backward()

    assert torch.allclose(sequence_utility_from_nll(baseline_nll=baseline_nll, student_nll=student_nll), torch.tensor([0.1]))
    assert student_nll.grad is not None
    assert float(student_nll.grad.abs()) > 0.0


def test_checkpoint_resume_preserves_table_optimizer_and_update_counts(tmp_path) -> None:
    table = IndependentPairTensorTable(["a", "b"], (2,))
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.1, weight_decay=0.0)
    counts = [0, 0]
    _step(table, optimizer, counts, 0, 1.0)
    expected_table = copy.deepcopy(table.state_dict())
    path = tmp_path / "checkpoint.pt"
    save_training_checkpoint(
        path,
        table=table,
        optimizer=optimizer,
        update_counts=counts,
        completed_rounds=1,
        metadata={"component": "unit_test"},
    )

    restored_table = IndependentPairTensorTable(["a", "b"], (2,))
    restored_optimizer = torch.optim.AdamW(restored_table.parameters(), lr=0.1, weight_decay=0.0)
    restored = load_training_checkpoint(path, table=restored_table, optimizer=restored_optimizer)

    assert restored["update_counts"] == [1, 0]
    assert restored["completed_rounds"] == 1
    for key, value in expected_table.items():
        assert torch.equal(restored_table.state_dict()[key], value)
    assert restored_optimizer.state_dict()["state"]


def test_convergence_subset_is_balanced_deterministic_and_covers_memories() -> None:
    first, first_report = select_convergence_subset(_rows(), target_total=64, seed=13)
    second, second_report = select_convergence_subset(_rows(), target_total=64, seed=13)

    assert [row["pair_id"] for row in first] == [row["pair_id"] for row in second]
    assert first_report == second_report
    assert first_report["selected_by_category"] == {
        "positive": 16,
        "neutral": 16,
        "negative": 16,
        "random": 16,
    }
    assert first_report["covered_memory_count"] == 36
    assert first_report["all_available_memories_covered"] is True


def test_plateau_uses_fixed_subset_metrics_sixteen_updates_apart() -> None:
    checkpoints = [
        {
            "updates_per_pair": 48,
            "pair_ids": ["a", "b"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.1000},
                "u_text_vs_u_student_spearman": 0.80,
            },
        },
        {
            "updates_per_pair": 64,
            "pair_ids": ["a", "b"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.0995},
                "u_text_vs_u_student_spearman": 0.805,
            },
        },
    ]

    result = assess_plateau(checkpoints, current_updates=64)

    assert result["assessable"] is True
    assert result["plateau"] is True


def test_plateau_rejects_different_pair_subsets() -> None:
    checkpoints = [
        {
            "updates_per_pair": 48,
            "pair_ids": ["a"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.1},
                "u_text_vs_u_student_spearman": 0.8,
            },
        },
        {
            "updates_per_pair": 64,
            "pair_ids": ["b"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.1},
                "u_text_vs_u_student_spearman": 0.8,
            },
        },
    ]

    with pytest.raises(ValueError, match="different pair subsets"):
        assess_plateau(checkpoints, current_updates=64)


def test_student_prompt_contract_contains_only_baseline_prompt_and_target() -> None:
    rows = [
        {
            "pair_id": "p0",
            "prompt_len": 3,
            "target_len": 2,
            "input_ids": [1, 2, 3, 4, 5],
            "response_cache": {"prompt_tokens": 3},
        }
    ]

    report = _student_prompt_contract(rows)

    assert report["passed"] is True
    assert report["raw_memory_text_in_student_prompt"] is False
    assert report["selector_payload_accessed"] is False
