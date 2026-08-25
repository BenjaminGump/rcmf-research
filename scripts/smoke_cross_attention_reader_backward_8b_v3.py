from __future__ import annotations

import _bootstrap  # noqa: F401
import torch

from rcmf.training.cross_attention_bounded_hooks_8b import (
    bounded_checkpoint_reader_forward,
)
import scripts.smoke_cross_attention_reader_backward_8b as base


def main() -> None:
    base.offloaded_checkpoint_reader_forward = bounded_checkpoint_reader_forward
    with torch.autograd.graph.save_on_cpu(pin_memory=True):
        base.main()


if __name__ == "__main__":
    main()
