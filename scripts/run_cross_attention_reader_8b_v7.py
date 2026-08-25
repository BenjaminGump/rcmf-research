from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

import rcmf.training.cross_attention_bounded_hooks_8b as bounded
from rcmf.training.cross_attention_bounded_hooks_8b import (
    MemoryBoundedCrossAttentionHooks,
    close_bounded_reader_hooks,
)
from rcmf.training.cross_attention_training_8b import (
    DifferentiableCrossAttentionHooks,
)
import scripts.run_cross_attention_reader_8b as base


class CompatibleMemoryBoundedHooks(
    MemoryBoundedCrossAttentionHooks, DifferentiableCrossAttentionHooks
):
    """Retain the base runner's hook interface without replacing eval hooks."""


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
        return bounded.bounded_checkpoint_reader_forward(
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
    bounded.MemoryBoundedCrossAttentionHooks = CompatibleMemoryBoundedHooks
    base._forward = _bounded_dispatch
    base._phase1 = _offloaded_phase(
        base._phase1, track_residual_penalty=False
    )
    base._phase2 = _offloaded_phase(
        base._phase2, track_residual_penalty=True
    )
    base.main()


if __name__ == "__main__":
    main()
