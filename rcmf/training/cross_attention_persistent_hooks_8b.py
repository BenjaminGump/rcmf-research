from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import torch
from torch import Tensor

from rcmf.training.cross_attention_field_8b import (
    CrossAttentionHookAudit,
    CrossAttentionMemoryReader,
)
from rcmf.training.cross_attention_training_8b import DifferentiableCrossAttentionHooks
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_stage_c_oracle_capacity_5e import _collate


def _reset(hooks: DifferentiableCrossAttentionHooks) -> None:
    hooks.deltas.clear()
    hooks.probabilities.clear()
    hooks.audit = CrossAttentionHookAudit(
        layer_count=hooks.reader.layer_count,
        memory_slot_count=(
            0 if hooks.memory_slots is None else int(hooks.memory_slots.shape[-2])
        ),
        calls={},
        query_lengths=defaultdict(list),
        attention_row_sum_error={},
        delta_norms=defaultdict(list),
    )


def _persistent_hooks(
    *, backend: Any, reader: CrossAttentionMemoryReader, slots: Tensor
) -> DifferentiableCrossAttentionHooks:
    existing = getattr(backend, "_exp030a_persistent_reader_hooks", None)
    existing_reader = getattr(backend, "_exp030a_persistent_reader", None)
    if existing is not None and existing_reader is not reader:
        existing.__exit__(None, None, None)
        existing = None
    if existing is None:
        existing = DifferentiableCrossAttentionHooks(
            model=backend.model,
            reader=reader,
            memory_slots=slots.to(backend.device),
        )
        existing.__enter__()
        backend._exp030a_persistent_reader_hooks = existing
        backend._exp030a_persistent_reader = reader
    existing.memory_slots = slots.to(backend.device)
    _reset(existing)
    return existing


def persistent_checkpoint_reader_forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, DifferentiableCrossAttentionHooks]:
    """Keep decoder hooks registered until checkpoint recomputation finishes."""
    batch = _collate(rows, device=backend.device, k=4)
    if training:
        if slots is None:
            raise ValueError("Training reader forward requires external memory slots")
        hooks = _persistent_hooks(backend=backend, reader=reader, slots=slots)
        with torch.enable_grad():
            loss, logits = _bare_target_forward(backend=backend, batch=batch)
        return loss, logits, hooks

    existing = getattr(backend, "_exp030a_persistent_reader_hooks", None)
    existing_reader = getattr(backend, "_exp030a_persistent_reader", None)
    if existing is not None and existing_reader is reader and slots is not None:
        existing.memory_slots = slots.to(backend.device)
        _reset(existing)
        with torch.no_grad():
            loss, logits = _bare_target_forward(backend=backend, batch=batch)
        return loss, logits, existing

    hooks = DifferentiableCrossAttentionHooks(
        model=backend.model,
        reader=reader,
        memory_slots=None if slots is None else slots.to(backend.device),
    )
    with torch.no_grad():
        with hooks:
            loss, logits = _bare_target_forward(backend=backend, batch=batch)
    return loss, logits, hooks


def close_persistent_reader_hooks(backend: Any) -> None:
    existing = getattr(backend, "_exp030a_persistent_reader_hooks", None)
    if existing is not None:
        existing.__exit__(None, None, None)
        backend._exp030a_persistent_reader_hooks = None
        backend._exp030a_persistent_reader = None

