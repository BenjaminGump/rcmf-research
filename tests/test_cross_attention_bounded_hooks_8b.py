from __future__ import annotations

import torch
from torch import nn

import rcmf.training.cross_attention_bounded_hooks_8b as bounded
from rcmf.training.cross_attention_field_8b import CrossAttentionMemoryReader


class _Attention(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.q_proj = nn.Linear(8, 8, bias=False)
        self.k_proj = nn.Linear(8, 8, bias=False)
        self.v_proj = nn.Linear(8, 8, bias=False)
        self.head_dim = 4


class _Block(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.self_attn = _Attention()


def test_bounded_hook_retains_scalars_not_layer_tensors(monkeypatch) -> None:
    block = _Block()
    monkeypatch.setattr(bounded, "decoder_layers", lambda model: [block])
    reader = CrossAttentionMemoryReader(model_dim=8, layer_count=1, rank=2)
    slots = torch.randn(1, 16, 8)
    hooks = bounded.MemoryBoundedCrossAttentionHooks(
        model=nn.Module(),
        reader=reader,
        memory_slots=slots,
        track_residual_penalty=False,
    )
    hidden = torch.randn(1, 3, 8, requires_grad=True)
    changed = hooks._hook(0)(block, (), hidden)
    hooks.finish_forward()
    changed.square().mean().backward()
    assert not hasattr(hooks, "deltas")
    assert not hasattr(hooks, "probabilities")
    assert hooks.residual_penalty().item() == 0.0
    assert hooks.residual_norm() >= 0.0
    assert reader.layers[0].up.weight.grad is not None


def test_checkpoint_recompute_capture_is_disabled(monkeypatch) -> None:
    block = _Block()
    monkeypatch.setattr(bounded, "decoder_layers", lambda model: [block])
    reader = CrossAttentionMemoryReader(model_dim=8, layer_count=1, rank=2)
    hooks = bounded.MemoryBoundedCrossAttentionHooks(
        model=nn.Module(),
        reader=reader,
        memory_slots=torch.randn(1, 16, 8),
        track_residual_penalty=True,
    )
    hidden = torch.randn(1, 3, 8, requires_grad=True)
    hooks._hook(0)(block, (), hidden)
    assert hooks.audit.calls == {0: 1}
    assert len(hooks._penalties) == 1
    hooks.finish_forward()
    hooks._hook(0)(block, (), hidden)
    assert hooks.audit.calls == {0: 1}
    assert len(hooks._penalties) == 1
