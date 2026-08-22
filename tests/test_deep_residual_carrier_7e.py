from __future__ import annotations

from pathlib import Path

import pytest
import torch
from torch import nn

from rcmf.training.deep_residual_carrier_7e import (
    DeepResidualHooks,
    continuation_decision,
    deep_residual_gate,
    layer_and_global_ratios,
    project_deep_delta_,
    ratios_from_recorded_base_norms,
    runtime_projection,
    selected_layer_indices,
)


class ToyBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.projection = nn.Linear(dim, dim, bias=False)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        return hidden_states + self.projection(hidden_states)


class ToyBase(nn.Module):
    def __init__(self, layers: int, dim: int) -> None:
        super().__init__()
        self.layers = nn.ModuleList(ToyBlock(dim) for _ in range(layers))

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        for layer in self.layers:
            hidden_states = layer(hidden_states)
        return hidden_states


class ToyModel(nn.Module):
    def __init__(self, layers: int = 6, dim: int = 8) -> None:
        super().__init__()
        self.model = ToyBase(layers, dim)


def _comparison(signature: float, successor: float, execution: float = 0.0):
    return {
        "canonical_procedural_signature_match": {"difference": signature},
        "semantic_successor_match": {"difference": successor},
        "execution_success": {"difference": execution},
    }


def test_layer_indices_are_fractional_and_deterministic() -> None:
    assert selected_layer_indices(36) == (7, 14, 21, 28)
    assert selected_layer_indices(1) == (0,)


def test_zero_residual_is_exact_and_nonzero_is_local_at_hook_boundary() -> None:
    torch.manual_seed(1)
    model = ToyModel()
    hidden = torch.randn(1, 7, 8)
    indices = torch.tensor([[1, 2, 4, 5]])
    zero = torch.zeros(1, 2, 4, 8)
    bare = model.model(hidden)
    with DeepResidualHooks(
        model=model,
        layer_indices=(1, 4),
        selected_token_indices=indices,
        delta=zero,
        expected_prefill_length=7,
    ) as audit:
        actual = model.model(hidden)
    assert torch.equal(actual, bare)
    assert set(audit.applied_calls) == {1, 4}
    assert audit.directly_modified_positions[1] == [[1, 2, 4, 5]]


def test_residual_gradient_reaches_every_active_layer() -> None:
    model = ToyModel()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    hidden = torch.randn(1, 7, 8)
    indices = torch.tensor([[1, 2, 4, 5]])
    delta = nn.Parameter(torch.full((1, 2, 4, 8), 0.01))
    with DeepResidualHooks(
        model=model,
        layer_indices=(1, 4),
        selected_token_indices=indices,
        delta=delta,
        expected_prefill_length=7,
    ):
        model.model(hidden).square().mean().backward()
    assert delta.grad is not None
    assert torch.all(delta.grad.flatten(start_dim=2).norm(dim=2) > 0)
    assert all(parameter.grad is None for parameter in model.parameters())


def test_generated_token_length_is_not_injected() -> None:
    model = ToyModel()
    indices = torch.tensor([[1, 2, 4, 5]])
    delta = torch.ones(1, 2, 4, 8)
    with DeepResidualHooks(
        model=model,
        layer_indices=(1, 4),
        selected_token_indices=indices,
        delta=delta,
        expected_prefill_length=7,
    ) as audit:
        model.model(torch.randn(1, 1, 8))
    assert audit.applied_calls == {}
    assert audit.skipped_decode_calls == {1: 1, 4: 1}


def test_projection_enforces_each_layer_and_reports_global_ratio() -> None:
    delta = torch.full((2, 4, 4, 8), 10.0)
    original = torch.ones_like(delta)
    project_deep_delta_(delta, original, max_ratio=1.0)
    layer, global_ratio = layer_and_global_ratios(delta, original)
    assert float(layer.max()) == pytest.approx(1.0, abs=1.0e-6)
    assert float(global_ratio.max()) == pytest.approx(1.0, abs=1.0e-6)


def test_recorded_base_norm_ratio_uses_delta_device_and_shape() -> None:
    delta = torch.ones(1, 2, 4, 8)
    layer, global_ratio = ratios_from_recorded_base_norms(delta, [4.0, 8.0])
    assert layer.device == delta.device
    assert global_ratio.device == delta.device
    assert tuple(layer.shape) == (1, 2)
    assert tuple(global_ratio.shape) == (1,)
    assert layer[0].tolist() == pytest.approx([2**0.5, 2**-0.5])

    unbatched_layer, unbatched_global = ratios_from_recorded_base_norms(
        delta[0], [4.0, 8.0]
    )
    assert torch.equal(unbatched_layer, layer)
    assert torch.equal(unbatched_global, global_ratio)


def test_gate_reuses_locked_retention_and_shuffle_contract() -> None:
    result = deep_residual_gate(
        r_minus_c0=_comparison(0.30, 0.20, -0.03),
        r_minus_s=_comparison(0.06, 0.00),
        f3_minus_c0=_comparison(0.40, 0.40),
        positive_task_count=6,
    )
    assert result["passed"] is True


def test_continuation_requires_improvement_without_other_metric_deterioration() -> None:
    passed = continuation_decision(
        {"teacher_policy_kl": 1.0, "teacher_token_ce": 1.0},
        {
            "teacher_policy_kl": 0.94,
            "teacher_token_ce": 1.04,
            "delta_ratio_max": 1.0,
        },
    )
    assert passed["continue_to_u16"] is True
    failed = continuation_decision(
        {"teacher_policy_kl": 1.0, "teacher_token_ce": 1.0},
        {
            "teacher_policy_kl": 0.94,
            "teacher_token_ce": 1.06,
            "delta_ratio_max": 1.0,
        },
    )
    assert failed["continue_to_u16"] is False


def test_runtime_projection_counts_one_backward_per_optimizer_update() -> None:
    result = runtime_projection(
        pair_count=32,
        maximum_updates_per_pair=16,
        validation_state_count=4,
        generation_count=64,
        rates={
            name: {"forward": 1.0, "backward": 2.0, "generation": 3.0}
            for name in ("best", "expected", "conservative")
        },
    )
    assert result["optimizer_backward_calls_minimum"] == 256
    assert result["optimizer_backward_calls_maximum"] == 512
    assert result["generation_count"] == 64


def test_experiment_script_uses_canonical_position_id_helper() -> None:
    source = Path("scripts/run_deep_residual_carrier_7e.py").read_text(encoding="utf-8")
    assert "from rcmf.injection.base import build_position_ids" in source
    assert "from rcmf.training.oracle_capacity_5e import build_position_ids" not in source


def test_checkpointed_backward_runs_inside_residual_hook_context() -> None:
    source = Path("scripts/run_deep_residual_carrier_7e.py").read_text(encoding="utf-8")
    assert "Checkpoint recomputation must see the same residual hooks as forward" in source
    assert "Keep hooks installed through activation-checkpoint recomputation" in source
