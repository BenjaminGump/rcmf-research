from __future__ import annotations

import torch

from rcmf.config import RCMFConfig
from rcmf.training.addressing_only import (
    AddressingLossWeights,
    AddressingOnlyModel,
    addressing_losses,
    evaluate_scores,
    rows_to_tensors,
)


def _cfg() -> RCMFConfig:
    cfg = RCMFConfig()
    cfg.encoder.type = "qwen_hidden"
    cfg.encoder.hidden_size = 16
    cfg.encoder.num_layers = 1
    cfg.encoder.dropout = 0.0
    cfg.memory.rank = 8
    cfg.memory.program_dim = 8
    cfg.address.mode = "dense_softmax"
    cfg.compiler.use_write_strength = True
    return cfg


def _rows() -> list[dict]:
    return [
        {
            "state_index": 0,
            "task_id": "a",
            "raw_utility": [0.2, -0.2, None],
            "valid_mask": [True, True, False],
            "positive_gain": [0.19, 0.0, 0.0],
            "strong_positive_mask": [True, False, False],
            "strong_negative_mask": [False, True, False],
            "negative_mask": [False, True, False],
            "no_positive_state": False,
            "all_missing_state": False,
        },
        {
            "state_index": 1,
            "task_id": "b",
            "raw_utility": [-0.1, -0.2, None],
            "valid_mask": [True, True, False],
            "positive_gain": [0.0, 0.0, 0.0],
            "strong_positive_mask": [False, False, False],
            "strong_negative_mask": [True, True, False],
            "negative_mask": [True, True, False],
            "no_positive_state": True,
            "all_missing_state": False,
        },
    ]


def test_addressing_score_has_gradients_and_freezes_program_head() -> None:
    torch.manual_seed(0)
    model = AddressingOnlyModel(_cfg(), representation_dim=12)
    state_reps = torch.randn(2, 12)
    memory_reps = torch.randn(3, 12)
    labels = rows_to_tensors(_rows())
    program_before = {key: value.detach().clone() for key, value in model.compiler.program_head.state_dict().items()}

    payload = model(state_reps, memory_reps)
    loss, metrics = addressing_losses(payload["q"], labels, AddressingLossWeights())
    loss.backward()

    assert payload["q"].requires_grad
    assert metrics["loss"] > 0
    assert any(param.grad is not None for param in model.state_encoder.parameters())
    assert any(param.grad is not None for param in model.compiler.alpha_head.parameters())
    assert any(param.grad is not None for param in model.compiler.rho_head.parameters())
    assert all(param.grad is None for param in model.compiler.program_head.parameters())
    for key, value in model.compiler.program_head.state_dict().items():
        assert torch.equal(value, program_before[key])


def test_evaluate_scores_handles_best_memory_ties() -> None:
    labels = rows_to_tensors(
        [
            {
                "state_index": 0,
                "raw_utility": [0.2, 0.2, -0.1],
                "valid_mask": [True, True, True],
                "positive_gain": [0.19, 0.19, 0.0],
                "strong_positive_mask": [True, True, False],
                "strong_negative_mask": [False, False, True],
                "negative_mask": [False, False, True],
                "no_positive_state": False,
                "all_missing_state": False,
            }
        ]
    )
    scores = torch.tensor([[0.0, 1.0, 0.5]], dtype=torch.float32)

    metrics = evaluate_scores(scores, labels)

    assert metrics["best_recall@1"]["mean"] == 1.0
    assert metrics["positive_mass_coverage@1"]["mean"] == 0.5
