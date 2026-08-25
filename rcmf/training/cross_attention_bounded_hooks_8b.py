from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from contextlib import AbstractContextManager
import math
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.training.cross_attention_field_8b import (
    CrossAttentionHookAudit,
    CrossAttentionMemoryReader,
    MEMORY_SLOT_COUNT,
)
from rcmf.training.deep_residual_carrier_7e import decoder_layers
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_stage_c_oracle_capacity_5e import _collate


class MemoryBoundedCrossAttentionHooks(AbstractContextManager[CrossAttentionHookAudit]):
    """Inject the exact reader without retaining layer-sized audit tensors."""

    def __init__(
        self,
        *,
        model: nn.Module,
        reader: CrossAttentionMemoryReader,
        memory_slots: Tensor,
        track_residual_penalty: bool,
    ) -> None:
        self.model = model
        self.reader = reader
        self.memory_slots = memory_slots
        self.track_residual_penalty = bool(track_residual_penalty)
        self.capture_audit = True
        self._penalties: dict[int, Tensor] = {}
        self._delta_square_sums: dict[int, float] = {}
        self._entropies: dict[int, float] = {}
        self._handles: list[Any] = []
        layers = decoder_layers(model)
        if len(layers) != reader.layer_count:
            raise ValueError("Reader layer count differs from Qwen")
        self.audit = CrossAttentionHookAudit(
            layer_count=reader.layer_count,
            memory_slot_count=int(memory_slots.shape[-2]),
        )

    def reset(self, memory_slots: Tensor, *, track_residual_penalty: bool) -> None:
        if int(memory_slots.shape[-2]) != MEMORY_SLOT_COUNT:
            raise ValueError("Training reader requires sixteen memory slots")
        self.memory_slots = memory_slots
        self.track_residual_penalty = bool(track_residual_penalty)
        self.capture_audit = True
        self._penalties.clear()
        self._delta_square_sums.clear()
        self._entropies.clear()
        self.audit = CrossAttentionHookAudit(
            layer_count=self.reader.layer_count,
            memory_slot_count=int(memory_slots.shape[-2]),
            calls={},
            query_lengths=defaultdict(list),
            attention_row_sum_error={},
            delta_norms=defaultdict(list),
        )

    def finish_forward(self) -> None:
        # Checkpoint recomputation must inject identically but retain no audit graph.
        self.capture_audit = False

    def _hook(self, layer_index: int):
        def apply(module: nn.Module, args: tuple[Any, ...], output: Any) -> Any:
            del args
            hidden = output[0] if isinstance(output, tuple) else output
            slots = self.memory_slots[layer_index]
            changed, probabilities, delta = self.reader.layers[layer_index](
                hidden,
                slots.to(device=hidden.device, dtype=hidden.dtype),
                module.self_attn,
            )
            if self.capture_audit:
                if self.track_residual_penalty:
                    self._penalties[layer_index] = delta.to(torch.float32).square().mean()
                delta_square_sum = float(
                    delta.detach().to(torch.float32).square().sum().cpu()
                )
                work = probabilities.detach().to(torch.float32).clamp_min(1.0e-12)
                entropy = float((-(work * work.log()).sum(dim=-1).mean()).cpu())
                row_error = float((work.sum(dim=-1) - 1.0).abs().max().cpu())
                self._delta_square_sums[layer_index] = delta_square_sum
                self._entropies[layer_index] = entropy
                self.audit.calls[layer_index] = self.audit.calls.get(layer_index, 0) + 1
                self.audit.query_lengths[layer_index].append(int(hidden.shape[1]))
                self.audit.attention_row_sum_error[layer_index] = row_error
                self.audit.delta_norms[layer_index].append(math.sqrt(delta_square_sum))
            if isinstance(output, tuple):
                return (changed, *output[1:])
            return changed

        return apply

    def residual_penalty(self) -> Tensor:
        if not self._penalties:
            return torch.zeros((), device=next(self.reader.parameters()).device)
        return torch.stack(list(self._penalties.values())).mean()

    def residual_norm(self) -> float:
        return math.sqrt(sum(self._delta_square_sums.values()))

    def attention_entropy(self) -> float:
        if not self._entropies:
            return 0.0
        return sum(self._entropies.values()) / len(self._entropies)

    def __enter__(self) -> CrossAttentionHookAudit:
        for index, layer in enumerate(decoder_layers(self.model)):
            self._handles.append(layer.register_forward_hook(self._hook(index)))
        return self.audit

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()


def bounded_checkpoint_reader_forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, MemoryBoundedCrossAttentionHooks]:
    if not training or slots is None:
        raise ValueError("Memory-bounded forward is for reader training with slots")
    batch = _collate(rows, device=backend.device, k=4)
    existing = getattr(backend, "_exp030a_bounded_reader_hooks", None)
    existing_reader = getattr(backend, "_exp030a_bounded_reader", None)
    if existing is not None and existing_reader is not reader:
        existing.__exit__(None, None, None)
        existing = None
    slots = slots.to(backend.device)
    track_penalty = bool(getattr(backend, "_exp030a_track_residual_penalty", False))
    if existing is None:
        existing = MemoryBoundedCrossAttentionHooks(
            model=backend.model,
            reader=reader,
            memory_slots=slots,
            track_residual_penalty=track_penalty,
        )
        existing.__enter__()
        backend._exp030a_bounded_reader_hooks = existing
        backend._exp030a_bounded_reader = reader
    else:
        existing.reset(slots, track_residual_penalty=track_penalty)
    with torch.enable_grad():
        loss, logits = _bare_target_forward(backend=backend, batch=batch)
    existing.finish_forward()
    return loss, logits, existing


def close_bounded_reader_hooks(backend: Any) -> None:
    existing = getattr(backend, "_exp030a_bounded_reader_hooks", None)
    if existing is not None:
        existing.__exit__(None, None, None)
        backend._exp030a_bounded_reader_hooks = None
        backend._exp030a_bounded_reader = None
