from __future__ import annotations

import torch
from torch import nn

from rcmf.training.fixed_memory_reader_8a import (
    FixedMemoryReader,
    FixedMemoryReaderHooks,
    class_balanced_row_weights,
    reader_behavior_classification,
    select_reader_checkpoint,
    stratified_live_steps,
)


class _Block(nn.Module):
    def forward(self, hidden_states: torch.Tensor) -> tuple[torch.Tensor]:
        return (hidden_states + 0.25,)


class _Backbone(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = nn.ModuleList(_Block() for _ in range(4))


class _Model(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = _Backbone()

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        for layer in self.model.layers:
            hidden = layer(hidden)[0]
        return hidden


def test_reader_zero_contract_and_fixed_size() -> None:
    reader = FixedMemoryReader(model_dim=8, latent_dim=4, bottleneck=3, layer_count=4)
    hidden = torch.randn(2, 4, 8)
    assert reader.output_layers_zero()
    assert torch.equal(reader.layer_delta(0, hidden, torch.zeros(2, 4)), torch.zeros_like(hidden))
    assert reader.parameter_count() == 4 * (8 * 3 + 4 * 3 + 3 * 8)


def test_reader_accepts_bfloat16_residual_with_float32_parameters() -> None:
    reader = FixedMemoryReader(model_dim=8, latent_dim=4, bottleneck=3, layer_count=4)
    hidden = torch.randn(2, 4, 8, dtype=torch.bfloat16)
    latent = torch.randn(2, 4)
    delta = reader.layer_delta(0, hidden, latent)
    assert delta.dtype == torch.bfloat16
    delta.to(torch.float32).sum().backward()
    assert reader.layers[0].output.weight.grad is not None

def test_zero_initialized_hooks_reproduce_bare_and_skip_decode() -> None:
    model = _Model()
    reader = FixedMemoryReader(model_dim=8, latent_dim=4, bottleneck=3, layer_count=4)
    hidden = torch.randn(1, 7, 8)
    selected = torch.tensor([[1, 2, 3, 4]])
    bare = model(hidden)
    hooks = FixedMemoryReaderHooks(
        model=model,
        reader=reader,
        layer_indices=(0, 1, 2, 3),
        selected_token_indices=selected,
        latent=torch.randn(1, 4),
        expected_prefill_length=7,
    )
    with hooks:
        changed = model(hidden)
        model(torch.randn(1, 1, 8))
    assert torch.equal(changed, bare)
    assert set(hooks.audit.applied_calls) == {0, 1, 2, 3}
    assert set(hooks.audit.skipped_decode_calls) == {0, 1, 2, 3}


def test_reader_gradients_and_locality() -> None:
    model = _Model()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    reader = FixedMemoryReader(model_dim=8, latent_dim=4, bottleneck=3, layer_count=4)
    hidden = torch.randn(1, 7, 8)
    selected = torch.tensor([[1, 2, 3, 4]])
    latent = torch.randn(1, 4)
    hooks = FixedMemoryReaderHooks(
        model=model,
        reader=reader,
        layer_indices=(0, 1, 2, 3),
        selected_token_indices=selected,
        latent=latent,
        expected_prefill_length=7,
    )
    with hooks:
        model(hidden).sum().backward()
    assert all(layer.output.weight.grad is not None for layer in reader.layers)
    assert all(parameter.grad is None for parameter in model.parameters())
    assert all(value == [[1, 2, 3, 4]] for value in hooks.audit.directly_modified_positions.values())


def test_hooks_remain_active_through_checkpoint_recomputation() -> None:
    model = _Model()
    reader = FixedMemoryReader(model_dim=8, latent_dim=4, bottleneck=3, layer_count=4)
    hidden = torch.randn(1, 7, 8, requires_grad=True)
    hooks = FixedMemoryReaderHooks(
        model=model,
        reader=reader,
        layer_indices=(0, 1, 2, 3),
        selected_token_indices=torch.tensor([[1, 2, 3, 4]]),
        latent=torch.randn(1, 4),
        expected_prefill_length=7,
    )
    with hooks:
        output = torch.utils.checkpoint.checkpoint(
            model, hidden, use_reentrant=False
        )
        output.sum().backward()
    assert all(layer.output.weight.grad is not None for layer in reader.layers)
    assert all(count >= 2 for count in hooks.audit.applied_calls.values())

def test_stratification_is_deterministic_and_spans_trajectory() -> None:
    selected = stratified_live_steps(list(range(1, 19)), task_id="task")
    assert selected == stratified_live_steps(list(range(1, 19)), task_id="task")
    assert len(selected) == 6
    assert any(value <= 6 for value in selected)
    assert any(7 <= value <= 12 for value in selected)
    assert any(value >= 13 for value in selected)


def _summary(r1_sig: float, r1_succ: float, r2: float, r3: float, tasks: int) -> dict:
    return {
        "R1": {"action_signature": r1_sig, "semantic_successor": r1_succ, "execution": 1.0, "raw_policy_kl": 0.1},
        "R2": {"action_signature": r2, "semantic_successor": r2, "execution": 1.0},
        "R3": {"action_signature": r3, "semantic_successor": r3, "execution": 1.0},
        "R0": {"action_signature": 0.2, "semantic_successor": 0.2, "execution": 1.0},
        "positive_task_count": tasks,
        "task_count": 8,
        "maximum_layer_ratio": 0.5,
    }


def test_classification_and_selection_prefer_strong() -> None:
    strong = _summary(0.6, 0.6, 0.3, 0.4, 5)
    partial = _summary(0.4, 0.3, 0.35, 0.4, 3)
    assert reader_behavior_classification(strong) == "STRONG"
    assert reader_behavior_classification(partial) == "PARTIAL"
    selected = select_reader_checkpoint(
        [
            {"updates_per_pair": 1, "validation": partial},
            {"updates_per_pair": 2, "validation": strong},
        ]
    )
    assert selected is not None
    assert selected["updates_per_pair"] == 2


def test_class_balancing_equalizes_total_class_weight() -> None:
    labels = ["POSITIVE", "POSITIVE", "NEUTRAL", "HARMFUL", "HARMFUL", "HARMFUL"]
    weights = class_balanced_row_weights(labels)
    totals = {
        label: sum(weight for value, weight in zip(labels, weights, strict=True) if value == label)
        for label in set(labels)
    }
    assert max(totals.values()) - min(totals.values()) < 1.0e-8


def test_full_validation_control_names_are_supported() -> None:
    summary = _summary(0.6, 0.6, 0.3, 0.4, 5)
    full = {
        "R1_correct": summary["R1"],
        "R2_transition_shuffle": summary["R2"],
        "R3_state_shuffle": summary["R3"],
        "R0_zero": summary["R0"],
        "positive_task_count": summary["positive_task_count"],
        "task_count": summary["task_count"],
        "maximum_layer_ratio": summary["maximum_layer_ratio"],
    }
    assert reader_behavior_classification(full) == "STRONG"
    selected = select_reader_checkpoint(
        [{"updates_per_pair": 1, "validation": full}]
    )
    assert selected is not None
    assert selected["classification"] == "STRONG"