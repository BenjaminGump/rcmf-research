from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from typing import Any

import torch
from torch import Tensor

from rcmf.training.cross_attention_field_8b import CrossAttentionMemoryReader
from rcmf.training.cross_attention_persistent_hooks_8b import (
    persistent_checkpoint_reader_forward,
)
from rcmf.training.cross_attention_training_8b import DifferentiableCrossAttentionHooks


def offloaded_checkpoint_reader_forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, DifferentiableCrossAttentionHooks]:
    """Offload exact saved activations while preserving checkpoint recomputation."""
    context = (
        torch.autograd.graph.save_on_cpu(pin_memory=True)
        if training and backend.device.type == "cuda"
        else nullcontext()
    )
    with context:
        return persistent_checkpoint_reader_forward(
            backend=backend,
            reader=reader,
            rows=rows,
            slots=slots,
            training=training,
        )

