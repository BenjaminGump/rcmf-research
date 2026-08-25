from __future__ import annotations

import _bootstrap  # noqa: F401
import torch

from rcmf.training.cross_attention_persistent_hooks_8b import (
    persistent_checkpoint_reader_forward,
)
import scripts.smoke_cross_attention_reader_backward_8b as base


def main() -> None:
    """Keep saved-tensor CPU offload active through checkpoint recomputation."""
    base.offloaded_checkpoint_reader_forward = persistent_checkpoint_reader_forward
    with torch.autograd.graph.save_on_cpu(pin_memory=True):
        base.main()


if __name__ == "__main__":
    main()
