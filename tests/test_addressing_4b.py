from __future__ import annotations

import torch
import pytest

from rcmf.memory.normalization import normalize_address
from rcmf.training.addressing_4b import (
    hard_topk_dead_zone_demo,
    hard_topk_overlap_gradient_demo,
    support_overlap_diagnostics,
    train_memory_prior,
    utility_decomposition,
)


def _label_row(split: str, utilities: list[float | None]) -> dict:
    valid = [value is not None for value in utilities]
    positive_gain = [max(float(value) - 0.01, 0.0) if value is not None else 0.0 for value in utilities]
    return {
        "state_index": 0,
        "state_example_id": f"{split}-{len(utilities)}",
        "task_id": split,
        "split": split,
        "raw_utility": utilities,
        "valid_mask": valid,
        "positive_gain": positive_gain,
        "strong_positive_mask": [value is not None and value >= 0.05 for value in utilities],
        "strong_negative_mask": [value is not None and value <= -0.05 for value in utilities],
        "negative_mask": [value is not None and value < -0.01 for value in utilities],
        "no_positive_state": bool(valid) and not any(value > 0 for value in positive_gain),
        "all_missing_state": not any(valid),
    }


def test_hard_topk_disjoint_support_has_zero_gradient() -> None:
    demo = hard_topk_dead_zone_demo(rank=8, topk=2)

    assert demo["support_intersection_size"] == 0
    assert demo["q"] == 0.0
    assert demo["state_grad_norm"] == 0.0
    assert demo["memory_grad_norm"] == 0.0
    assert demo["can_move_state_support"] is False
    assert demo["can_move_memory_support"] is False


def test_hard_topk_overlap_control_has_gradient() -> None:
    demo = hard_topk_overlap_gradient_demo(rank=8, topk=2)

    assert demo["q"] > 0.0
    assert demo["state_grad_norm"] > 0.0
    assert demo["memory_grad_norm"] > 0.0


def test_support_overlap_diagnostics_counts_zero_pairs() -> None:
    state_logits = torch.tensor([[4.0, 3.0, 0.0, -1.0], [-1.0, 0.0, 3.0, 4.0]])
    memory_logits = torch.tensor([[-1.0, 0.0, 3.0, 4.0]])
    state = normalize_address(state_logits, mode="topk_softmax", topk=2)
    memory = normalize_address(memory_logits, mode="topk_softmax", topk=2)

    diagnostics = support_overlap_diagnostics(state, memory, topk=2)

    assert diagnostics["support_intersection_histogram"] == {0: 1, 2: 1}
    assert diagnostics["zero_support_overlap_fraction"] == 0.5
    assert diagnostics["zero_raw_dot_fraction"] == 0.5
    assert diagnostics["nonzero_overlap_memories_per_state_first50"] == [0, 1]


def test_utility_decomposition_uses_train_mu_only() -> None:
    train_rows = [
        _label_row("train", [1.0, 0.0, None]),
        _label_row("train", [0.0, 2.0, 1.0]),
    ]
    validation_rows = [
        _label_row("validation", [10.0, 10.0, 10.0]),
    ]

    mu = train_memory_prior(train_rows)
    decomposition = utility_decomposition(train_rows, validation_rows, mu=mu)

    assert torch.allclose(mu, torch.tensor([0.5, 1.0, 1.0]))
    assert decomposition["global_train_mean_utility"] == pytest.approx(0.8)
    assert decomposition["validation_shape"] == [1, 3]
    assert decomposition["residual_distribution"]["validation"]["mean"] == pytest.approx(9.1666666667)
