from __future__ import annotations

import torch


def test_nonreentrant_checkpoint_early_stop_context_is_available() -> None:
    context = torch.utils.checkpoint.set_checkpoint_early_stop(False)
    with context:
        value = torch.tensor(1.0, requires_grad=True)
        assert value.requires_grad
