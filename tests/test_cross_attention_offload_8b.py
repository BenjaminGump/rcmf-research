from __future__ import annotations

import torch


def test_save_on_cpu_context_preserves_gradient() -> None:
    value = torch.tensor([2.0], requires_grad=True)
    with torch.autograd.graph.save_on_cpu(pin_memory=False):
        loss = value.square().sum()
    loss.backward()
    assert value.grad is not None
    assert float(value.grad) == 4.0
