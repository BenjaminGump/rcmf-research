from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from typing import Any

import torch
from torch import Tensor

from rcmf.training.cross_attention_field_8b import CrossAttentionMemoryReader
from rcmf.training.cross_attention_training_8b import DifferentiableCrossAttentionHooks
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_stage_c_oracle_capacity_5e import _collate


def checkpoint_safe_reader_forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, DifferentiableCrossAttentionHooks]:
    """Run post-block reader hooks through complete checkpoint recomputation."""
    batch = _collate(rows, device=backend.device, k=4)
    hooks = DifferentiableCrossAttentionHooks(
        model=backend.model,
        reader=reader,
        memory_slots=None if slots is None else slots.to(backend.device),
    )
    gradient_context = torch.enable_grad() if training else torch.no_grad()
    checkpoint_context = (
        torch.utils.checkpoint.set_checkpoint_early_stop(False)
        if training
        else nullcontext()
    )
    with checkpoint_context:
        with gradient_context:
            with hooks:
                loss, logits = _bare_target_forward(backend=backend, batch=batch)
    return loss, logits, hooks

