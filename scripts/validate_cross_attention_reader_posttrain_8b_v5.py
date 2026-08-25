from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

import rcmf.training.cross_attention_bounded_hooks_8b as bounded
from rcmf.training.cross_attention_bounded_hooks_8b import (
    close_bounded_reader_hooks,
)
from scripts.run_cross_attention_reader_8b_v7 import (
    CompatibleMemoryBoundedHooks,
)
import scripts.validate_cross_attention_reader_posttrain_8b as validation


_ORIGINAL_FORWARD = validation._forward


def _dispatch(
    *,
    backend: Any,
    reader: Any,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, Any]:
    if training:
        backend._exp030a_track_residual_penalty = False
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


def main() -> None:
    bounded.MemoryBoundedCrossAttentionHooks = CompatibleMemoryBoundedHooks
    validation._forward = _dispatch
    with torch.autograd.graph.save_on_cpu(pin_memory=True):
        validation.main()


if __name__ == "__main__":
    main()
