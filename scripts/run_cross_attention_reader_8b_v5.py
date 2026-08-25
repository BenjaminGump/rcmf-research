from __future__ import annotations

from collections.abc import Callable
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.training.cross_attention_persistent_hooks_8b import (
    persistent_checkpoint_reader_forward,
)
import scripts.run_cross_attention_reader_8b as base


def _offloaded_phase(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def run(**kwargs: Any) -> dict[str, Any]:
        with torch.autograd.graph.save_on_cpu(pin_memory=True):
            return function(**kwargs)

    return run


def main() -> None:
    """Offload tensors saved by both forward and checkpoint recomputation."""
    base._forward = persistent_checkpoint_reader_forward
    base._phase1 = _offloaded_phase(base._phase1)
    base._phase2 = _offloaded_phase(base._phase2)
    base.main()


if __name__ == "__main__":
    main()
