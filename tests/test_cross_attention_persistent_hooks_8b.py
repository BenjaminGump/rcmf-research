from __future__ import annotations

import torch
from torch import nn
from torch.utils.checkpoint import checkpoint


def test_forward_hook_survives_nonreentrant_checkpoint_backward() -> None:
    block = nn.Linear(4, 4, bias=False)
    for parameter in block.parameters():
        parameter.requires_grad_(False)
    adapter = nn.Linear(4, 4, bias=False)

    def hook(module, args, output):
        del module, args
        return output + adapter(output)

    handle = block.register_forward_hook(hook)
    try:
        value = torch.ones(2, 4)
        output = checkpoint(block, value, use_reentrant=False)
        output.square().mean().backward()
    finally:
        handle.remove()
    assert adapter.weight.grad is not None
    assert float(adapter.weight.grad.norm()) > 0.0
