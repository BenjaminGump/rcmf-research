from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.training.cross_attention_bounded_hooks_8b import (
    MemoryBoundedCrossAttentionHooks,
    bounded_checkpoint_reader_forward,
    close_bounded_reader_hooks,
)
import scripts.run_cross_attention_reader_8b as base


_ORIGINAL_FORWARD = base._forward


def _bounded_dispatch(
    *,
    backend: Any,
    reader: Any,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, Any]:
    if training:
        return bounded_checkpoint_reader_forward(
            backend=backend,
            reader=reader,
            rows=rows,
            slots=slots,
            training=True,
        )
    close_bounded_reader_hooks(backend)
    return _ORIGINAL_FORWARD(
        backend=backend,
        reader=reader,
        rows=rows,
        slots=slots,
        training=False,
    )


def _offloaded_phase(
    function: Callable[..., dict[str, Any]], *, track_residual_penalty: bool
) -> Callable[..., dict[str, Any]]:
    def run(**kwargs: Any) -> dict[str, Any]:
        backend = kwargs["backend"]
        backend._exp030a_track_residual_penalty = bool(track_residual_penalty)
        try:
            with torch.autograd.graph.save_on_cpu(pin_memory=True):
                return function(**kwargs)
        finally:
            close_bounded_reader_hooks(backend)

    return run


def main() -> None:
    base._forward = _bounded_dispatch
    # Preserve the base runner's explicit compatibility assertion.
    base.DifferentiableCrossAttentionHooks = MemoryBoundedCrossAttentionHooks
    base._phase1 = _offloaded_phase(
        base._phase1, track_residual_penalty=False
    )
    base._phase2 = _offloaded_phase(
        base._phase2, track_residual_penalty=True
    )
    base.main()


if __name__ == "__main__":
    main()
