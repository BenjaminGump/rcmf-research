from __future__ import annotations

import torch
from torch import nn

from rcmf.training.cross_attention_field_8b import CrossAttentionMemoryReader
from rcmf.training.cross_attention_training_8b import (
    DifferentiableCrossAttentionHooks,
    all_fusion_layers_receive_gradient,
    fusion_gradient_norms,
)


class _Attention(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.head_dim = 4
        self.num_heads = 2
        self.q_proj = nn.Linear(8, 8, bias=False)
        self.k_proj = nn.Linear(8, 8, bias=False)
        self.v_proj = nn.Linear(8, 8, bias=False)


class _Block(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.self_attn = _Attention()

    def forward(self, hidden_states: torch.Tensor) -> tuple[torch.Tensor]:
        return (hidden_states + 0.1,)


class _Model(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = nn.Module()
        self.model.layers = nn.ModuleList([_Block(), _Block()])

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        for layer in self.model.layers:
            hidden = layer(hidden)[0]
        return hidden


def test_differentiable_hooks_expose_penalty_and_gradient_audit() -> None:
    model = _Model()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    reader = CrossAttentionMemoryReader(
        model_dim=8, layer_count=2, rank=4, alpha=4.0, dropout=0.0
    )
    hooks = DifferentiableCrossAttentionHooks(
        model=model,
        reader=reader,
        memory_slots=torch.randn(2, 16, 8),
    )
    with hooks:
        output = model(torch.randn(1, 5, 8))
        loss = output.square().mean() + hooks.residual_penalty()
        loss.backward()
    gradients = fusion_gradient_norms(reader)
    assert all_fusion_layers_receive_gradient(gradients, require_down=False)
    assert hooks.residual_norm() == 0.0
    assert hooks.attention_entropy() > 0.0
    assert all(parameter.grad is None for parameter in model.parameters())
