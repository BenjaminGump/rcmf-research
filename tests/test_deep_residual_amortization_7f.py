from __future__ import annotations

from pathlib import Path

import pytest
import torch
import yaml

from rcmf.training.deep_residual_amortization_7f import (
    SharedDeepResidualDecoder,
    aggregate_and_select_class,
    best_visited_checkpoint,
    classify_one_step_behavior,
    continue_after_u8,
    differentiable_layer_ratio_projection,
)


def test_first37_task_ids_are_loaded_as_exact_strings() -> None:
    config_path = Path("configs/benchmark/stage_c_deep_residual_amortization_7f.yaml")
    task_ids = yaml.safe_load(config_path.read_text(encoding="utf-8"))["stage_c_7f"]["first37"]["task_ids"]
    assert len(task_ids) == 37
    assert len(set(task_ids)) == 37
    assert all(isinstance(task_id, str) for task_id in task_ids)
    assert task_ids[24:27] == ["8749218_1", "8749218_2", "8749218_3"]


def test_shared_decoder_has_locked_shape_and_no_bias() -> None:
    decoder = SharedDeepResidualDecoder(program_dim=8, model_dim=16)
    output = decoder(torch.ones(2, 8))
    assert output.shape == (2, 4, 4, 16)
    assert decoder.linear.bias is None


def test_layer_projection_is_differentiable_and_bounded() -> None:
    raw = torch.full((2, 4, 4, 8), 10.0, requires_grad=True)
    base = torch.ones_like(raw)
    projected, ratios = differentiable_layer_ratio_projection(raw, base)
    assert float(ratios["maximum_ratio"].detach()) == pytest.approx(1.0, abs=1.0e-6)
    projected.sum().backward()
    assert raw.grad is not None
    assert torch.isfinite(raw.grad).all()


def test_best_checkpoint_uses_huber_and_positive_spearman() -> None:
    history = [
        {"updates_per_pair": 4, "a_validation_huber": 0.2, "a_validation_spearman": 0.3, "maximum_ratio": 1.0},
        {"updates_per_pair": 8, "a_validation_huber": 0.1, "a_validation_spearman": -0.1, "maximum_ratio": 1.0},
        {"updates_per_pair": 16, "a_validation_huber": 0.15, "a_validation_spearman": 0.4, "maximum_ratio": 1.0},
    ]
    assert best_visited_checkpoint(history)["updates_per_pair"] == 16


def test_u8_continuation_uses_u4_only() -> None:
    result = continue_after_u8(
        {
            "a_validation_huber": 0.95,
            "a_validation_spearman": 0.22,
            "maximum_ratio": 1.0,
            "previous": {"a_validation_huber": 1.0, "a_validation_spearman": 0.20},
        }
    )
    assert result["continue_to_u16"]


def test_class_selection_uses_mean_not_duplicate_sum() -> None:
    result = aggregate_and_select_class(
        [0.6, 0.6, 1.0],
        ["large", "large", "small"],
        legal_transition_ids=["a", "b", "c"],
        ordered_transition_ids=["a", "b", "c"],
    )
    assert result["selected_class_id"] == "small"


def test_behavior_classification_strong_and_partial() -> None:
    strong = classify_one_step_behavior(
        p1_minus_c0={"action_signature": 0.2, "semantic_successor": 0.1},
        p1_minus_p2={"action_signature": 0.1, "semantic_successor": 0.0},
        p1_minus_p3={"action_signature": 0.1, "semantic_successor": 0.0},
        execution_drop=0.0,
        positive_task_count=5,
    )
    assert strong["classification"] == "STRONG_POSITIVE"
    partial = classify_one_step_behavior(
        p1_minus_c0={"action_signature": 0.1, "semantic_successor": 0.0},
        p1_minus_p2={"action_signature": 0.02, "semantic_successor": 0.0},
        p1_minus_p3={"action_signature": -0.01, "semantic_successor": 0.0},
        execution_drop=0.0,
        positive_task_count=4,
    )
    assert partial["classification"] == "PARTIAL_POSITIVE"
