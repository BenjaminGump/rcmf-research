from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.training.cross_attention_field_8b import (
    CrossAttentionHookAudit,
    CrossAttentionMemoryReader,
    MEMORY_SLOT_COUNT,
)
from rcmf.training.deep_residual_carrier_7e import decoder_layers


class DifferentiableCrossAttentionHooks(
    AbstractContextManager[CrossAttentionHookAudit]
):
    """Training hooks that retain residual tensors for regularization audits."""

    def __init__(
        self,
        *,
        model: nn.Module,
        reader: CrossAttentionMemoryReader,
        memory_slots: Tensor | None,
    ) -> None:
        self.model = model
        self.reader = reader
        self.memory_slots = memory_slots
        layers = decoder_layers(model)
        if len(layers) != reader.layer_count:
            raise ValueError("Reader layer count differs from Qwen")
        if memory_slots is not None and tuple(memory_slots.shape[:1]) != (
            reader.layer_count,
        ):
            raise ValueError("Memory slot layer count differs from Qwen")
        if memory_slots is not None and int(memory_slots.shape[-2]) != MEMORY_SLOT_COUNT:
            raise ValueError("Training reader requires sixteen memory slots")
        self.audit = CrossAttentionHookAudit(
            layer_count=reader.layer_count,
            memory_slot_count=0 if memory_slots is None else int(memory_slots.shape[-2]),
        )
        self.deltas: dict[int, Tensor] = {}
        self.probabilities: dict[int, Tensor] = {}
        self._handles: list[Any] = []

    def _hook(self, layer_index: int):
        def apply(module: nn.Module, args: tuple[Any, ...], output: Any) -> Any:
            del args
            if self.memory_slots is None:
                return output
            hidden = output[0] if isinstance(output, tuple) else output
            slots = self.memory_slots[layer_index]
            changed, probabilities, delta = self.reader.layers[layer_index](
                hidden,
                slots.to(device=hidden.device, dtype=hidden.dtype),
                module.self_attn,
            )
            self.deltas[layer_index] = delta
            self.probabilities[layer_index] = probabilities
            self.audit.calls[layer_index] = self.audit.calls.get(layer_index, 0) + 1
            self.audit.query_lengths[layer_index].append(int(hidden.shape[1]))
            row_error = (probabilities.to(torch.float32).sum(dim=-1) - 1.0).abs().max()
            self.audit.attention_row_sum_error[layer_index] = max(
                self.audit.attention_row_sum_error.get(layer_index, 0.0),
                float(row_error.detach().cpu()),
            )
            self.audit.delta_norms[layer_index].append(
                float(delta.to(torch.float32).norm().detach().cpu())
            )
            if isinstance(output, tuple):
                return (changed, *output[1:])
            return changed

        return apply

    def residual_penalty(self) -> Tensor:
        if not self.deltas:
            device = next(self.reader.parameters()).device
            return torch.zeros((), device=device)
        return torch.stack(
            [delta.to(torch.float32).square().mean() for delta in self.deltas.values()]
        ).mean()

    def residual_norm(self) -> float:
        if not self.deltas:
            return 0.0
        total = torch.stack(
            [delta.to(torch.float32).square().sum() for delta in self.deltas.values()]
        ).sum()
        return float(total.sqrt().detach().cpu())

    def attention_entropy(self) -> float:
        if not self.probabilities:
            return 0.0
        values = []
        for probabilities in self.probabilities.values():
            work = probabilities.to(torch.float32).clamp_min(1.0e-12)
            values.append(-(work * work.log()).sum(dim=-1).mean())
        return float(torch.stack(values).mean().detach().cpu())

    def __enter__(self) -> CrossAttentionHookAudit:
        if self.memory_slots is None:
            return self.audit
        for index, layer in enumerate(decoder_layers(self.model)):
            self._handles.append(layer.register_forward_hook(self._hook(index)))
        return self.audit

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()


def fusion_gradient_norms(reader: CrossAttentionMemoryReader) -> dict[str, list[float]]:
    output: dict[str, list[float]] = {"down": [], "up": []}
    for layer in reader.layers:
        for name in output:
            parameter = getattr(layer, name).weight
            output[name].append(
                0.0
                if parameter.grad is None
                else float(parameter.grad.to(torch.float32).norm().detach().cpu())
            )
    return output


def all_fusion_layers_receive_gradient(
    gradients: Mapping[str, list[float]], *, require_down: bool
) -> bool:
    if not all(value > 0.0 for value in gradients["up"]):
        return False
    return not require_down or all(value > 0.0 for value in gradients["down"])
