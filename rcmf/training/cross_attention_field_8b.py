from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import math
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.deep_residual_carrier_7e import decoder_layers


GLOBAL_SEED = 25101
MEMORY_SLOT_COUNT = 16
MEMORY_TOKEN_CAP = 256
FUSION_RANK = 16
FUSION_ALPHA = 32.0
FUSION_DROPOUT = 0.2
READER_VERSION = "tokenmem_style_cross_attention_reader_8b_v1"
FIELD_VERSION = "reversible_semantic_slot_field_8b_v1"


def rms_norm(value: Tensor, *, eps: float = 1.0e-6) -> Tensor:
    """Parameter-free RMS normalization with the input dtype preserved."""
    work = value.to(torch.float32)
    normalized = work * torch.rsqrt(work.square().mean(dim=-1, keepdim=True) + eps)
    return normalized.to(value.dtype)


def deterministic_strided_indices(
    token_count: int,
    *,
    slot_count: int = MEMORY_SLOT_COUNT,
    token_cap: int = MEMORY_TOKEN_CAP,
) -> list[int]:
    """Choose a fixed number of deterministic slots from a capped token prefix."""
    count = min(int(token_count), int(token_cap))
    if count <= 0:
        raise ValueError("Memory encoding requires at least one token")
    if slot_count <= 0:
        raise ValueError("slot_count must be positive")
    if slot_count == 1:
        return [0]
    return [
        int(round(index * (count - 1) / (slot_count - 1)))
        for index in range(slot_count)
    ]


def sample_layer_memory_slots(
    hidden_states: Sequence[Tensor],
    *,
    token_count: int,
    slot_count: int = MEMORY_SLOT_COUNT,
    token_cap: int = MEMORY_TOKEN_CAP,
) -> tuple[Tensor, dict[str, Any]]:
    """Sample [layer, slot, model] memory slots from per-layer hidden states."""
    if not hidden_states:
        raise ValueError("No layer hidden states were supplied")
    indices = deterministic_strided_indices(
        token_count, slot_count=slot_count, token_cap=token_cap
    )
    sampled = []
    for hidden in hidden_states:
        if hidden.ndim == 3:
            if int(hidden.shape[0]) != 1:
                raise ValueError("Memory slot caching currently requires batch size one")
            hidden = hidden[0]
        if hidden.ndim != 2:
            raise ValueError("Layer memory states must have shape [token, model]")
        if max(indices) >= int(hidden.shape[0]):
            raise ValueError("Sample index exceeds a layer memory sequence")
        sampled.append(hidden[indices])
    slots = torch.stack(sampled, dim=0)
    provenance = {
        "source_token_count": int(token_count),
        "capped_token_count": min(int(token_count), int(token_cap)),
        "token_cap": int(token_cap),
        "slot_count": int(slot_count),
        "sampled_token_indices": indices,
        "sampling": "deterministic_even_stride_over_capped_prefix",
    }
    return slots, provenance


def _repeat_kv(hidden: Tensor, repetitions: int) -> Tensor:
    if repetitions == 1:
        return hidden
    batch, heads, tokens, width = hidden.shape
    return (
        hidden[:, :, None, :, :]
        .expand(batch, heads, repetitions, tokens, width)
        .reshape(batch, heads * repetitions, tokens, width)
    )


class LayerCrossAttentionFusion(nn.Module):
    """Dedicated memory attention plus zero-initialized low-rank fusion."""

    def __init__(
        self,
        *,
        model_dim: int,
        rank: int = FUSION_RANK,
        alpha: float = FUSION_ALPHA,
        dropout: float = FUSION_DROPOUT,
    ) -> None:
        super().__init__()
        self.model_dim = int(model_dim)
        self.rank = int(rank)
        self.alpha = float(alpha)
        self.dropout_probability = float(dropout)
        self.down = nn.Linear(self.model_dim, self.rank, bias=False)
        self.up = nn.Linear(self.rank, self.model_dim, bias=False)
        self.dropout = nn.Dropout(self.dropout_probability)
        nn.init.normal_(self.down.weight, mean=0.0, std=0.01)
        nn.init.zeros_(self.up.weight)

    @property
    def scale(self) -> float:
        return self.alpha / self.rank

    def output_zero(self) -> bool:
        return bool(torch.equal(self.up.weight, torch.zeros_like(self.up.weight)))

    def _project_attention(
        self,
        query_hidden: Tensor,
        memory_slots: Tensor,
        self_attention: nn.Module,
    ) -> tuple[Tensor, Tensor]:
        query_input = rms_norm(query_hidden)
        memory_input = rms_norm(memory_slots)
        query = self_attention.q_proj(query_input)
        key = self_attention.k_proj(memory_input)
        value = self_attention.v_proj(memory_input)

        head_dim = int(getattr(self_attention, "head_dim", 0))
        if head_dim <= 0:
            num_heads = int(getattr(self_attention, "num_heads", 0))
            if num_heads <= 0:
                raise ValueError("Cannot infer cross-attention head dimension")
            head_dim = int(query.shape[-1]) // num_heads
        query_heads = int(query.shape[-1]) // head_dim
        key_heads = int(key.shape[-1]) // head_dim
        if query_heads <= 0 or key_heads <= 0 or query_heads % key_heads:
            raise ValueError("Qwen Q/K head geometry is incompatible with memory attention")

        batch, query_tokens = query.shape[:2]
        memory_tokens = int(key.shape[1])
        query = query.view(batch, query_tokens, query_heads, head_dim).transpose(1, 2)
        key = key.view(batch, memory_tokens, key_heads, head_dim).transpose(1, 2)
        value = value.view(batch, memory_tokens, key_heads, head_dim).transpose(1, 2)
        if hasattr(self_attention, "q_norm"):
            query = self_attention.q_norm(query)
        if hasattr(self_attention, "k_norm"):
            key = self_attention.k_norm(key)
        repetitions = query_heads // key_heads
        key = _repeat_kv(key, repetitions)
        value = _repeat_kv(value, repetitions)

        scores = torch.matmul(query.to(torch.float32), key.to(torch.float32).transpose(-2, -1))
        scores = scores / math.sqrt(head_dim)
        probabilities = F.softmax(scores, dim=-1, dtype=torch.float32).to(value.dtype)
        context = torch.matmul(probabilities, value)
        context = context.transpose(1, 2).contiguous().view(batch, query_tokens, -1)
        if int(context.shape[-1]) != self.model_dim:
            raise ValueError("Cross-attention output does not match Qwen hidden size")
        return context, probabilities

    def forward(
        self,
        query_hidden: Tensor,
        memory_slots: Tensor,
        self_attention: nn.Module,
    ) -> tuple[Tensor, Tensor, Tensor]:
        if query_hidden.ndim != 3 or int(query_hidden.shape[-1]) != self.model_dim:
            raise ValueError("Query hidden states must have shape [batch, token, model]")
        if memory_slots.ndim == 2:
            memory_slots = memory_slots.unsqueeze(0)
        if memory_slots.ndim != 3 or int(memory_slots.shape[-1]) != self.model_dim:
            raise ValueError("Memory slots must have shape [batch, slot, model]")
        if int(memory_slots.shape[0]) == 1 and int(query_hidden.shape[0]) > 1:
            memory_slots = memory_slots.expand(int(query_hidden.shape[0]), -1, -1)
        if int(memory_slots.shape[0]) != int(query_hidden.shape[0]):
            raise ValueError("Query and memory batch sizes differ")
        context, probabilities = self._project_attention(
            query_hidden, memory_slots.to(query_hidden.device), self_attention
        )
        delta = self.up(self.down(self.dropout(context.to(self.down.weight.dtype))))
        delta = (self.scale * delta).to(query_hidden.dtype)
        return query_hidden + delta, probabilities, delta


class CrossAttentionMemoryReader(nn.Module):
    def __init__(
        self,
        *,
        model_dim: int,
        layer_count: int,
        rank: int = FUSION_RANK,
        alpha: float = FUSION_ALPHA,
        dropout: float = FUSION_DROPOUT,
    ) -> None:
        super().__init__()
        self.model_dim = int(model_dim)
        self.layer_count = int(layer_count)
        self.layers = nn.ModuleList(
            LayerCrossAttentionFusion(
                model_dim=self.model_dim,
                rank=rank,
                alpha=alpha,
                dropout=dropout,
            )
            for _ in range(self.layer_count)
        )

    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())

    def output_layers_zero(self) -> bool:
        return all(layer.output_zero() for layer in self.layers)


@dataclass
class CrossAttentionHookAudit:
    layer_count: int
    memory_slot_count: int
    calls: dict[int, int] = field(default_factory=dict)
    query_lengths: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    attention_row_sum_error: dict[int, float] = field(default_factory=dict)
    delta_norms: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer_count": self.layer_count,
            "memory_slot_count": self.memory_slot_count,
            "calls": {str(key): value for key, value in self.calls.items()},
            "query_lengths": {
                str(key): value for key, value in self.query_lengths.items()
            },
            "attention_row_sum_error": {
                str(key): value for key, value in self.attention_row_sum_error.items()
            },
            "delta_norms": {
                str(key): value for key, value in self.delta_norms.items()
            },
        }


class CrossAttentionReaderHooks(AbstractContextManager[CrossAttentionHookAudit]):
    """Attach external-memory attention after every Qwen decoder block."""

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
            raise ValueError("Reader layer count differs from Qwen decoder layer count")
        if memory_slots is not None:
            if memory_slots.ndim not in (3, 4):
                raise ValueError(
                    "Memory slots must have shape [layer, slot, model] or "
                    "[layer, batch, slot, model]"
                )
            if int(memory_slots.shape[0]) != reader.layer_count:
                raise ValueError("Memory slot layer count differs from reader")
            if int(memory_slots.shape[-2]) != MEMORY_SLOT_COUNT:
                raise ValueError("Reader requires exactly sixteen memory slots")
            if int(memory_slots.shape[-1]) != reader.model_dim:
                raise ValueError("Memory slot model dimension differs from reader")
        self.audit = CrossAttentionHookAudit(
            layer_count=reader.layer_count,
            memory_slot_count=0 if memory_slots is None else int(memory_slots.shape[-2]),
        )
        self._handles: list[Any] = []

    def _hook(self, layer_index: int):
        def apply(module: nn.Module, args: tuple[Any, ...], output: Any) -> Any:
            del args
            if self.memory_slots is None:
                return output
            if isinstance(output, tuple):
                hidden = output[0]
            else:
                hidden = output
            slots = self.memory_slots[layer_index]
            changed, probabilities, delta = self.reader.layers[layer_index](
                hidden,
                slots.to(device=hidden.device, dtype=hidden.dtype),
                module.self_attn,
            )
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

    def __enter__(self) -> CrossAttentionHookAudit:
        if self.memory_slots is None:
            return self.audit
        for layer_index, layer in enumerate(decoder_layers(self.model)):
            self._handles.append(layer.register_forward_hook(self._hook(layer_index)))
        return self.audit

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()


@dataclass(frozen=True)
class MemoryFieldRecord:
    memory_id: str
    parent_id: str
    key: Tensor
    mu: float
    slots: Tensor
    rho: float


class ReversibleSemanticSlotField:
    """A fixed-shape additive slot field with exact record subtraction."""

    def __init__(
        self,
        *,
        layer_count: int,
        key_dim: int,
        slot_count: int,
        model_dim: int,
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
        key_chunk_size: int = 32,
    ) -> None:
        self.layer_count = int(layer_count)
        self.key_dim = int(key_dim)
        self.slot_count = int(slot_count)
        self.model_dim = int(model_dim)
        self.device = torch.device(device)
        self.dtype = dtype
        self.key_chunk_size = int(key_chunk_size)
        self.A = torch.zeros(
            self.layer_count,
            self.key_dim,
            self.slot_count,
            self.model_dim,
            device=self.device,
            dtype=self.dtype,
        )
        self.B = torch.zeros(
            self.layer_count,
            self.slot_count,
            self.model_dim,
            device=self.device,
            dtype=self.dtype,
        )
        self.records: dict[str, MemoryFieldRecord] = {}
        self.parent_index: dict[str, set[str]] = defaultdict(set)

    @property
    def field_shape(self) -> dict[str, tuple[int, ...]]:
        return {"A": tuple(self.A.shape), "B": tuple(self.B.shape)}

    def _validate_record(self, record: MemoryFieldRecord) -> None:
        if tuple(record.key.shape) != (self.key_dim,):
            raise ValueError("Memory key shape differs from field key dimension")
        if tuple(record.slots.shape) != (
            self.layer_count,
            self.slot_count,
            self.model_dim,
        ):
            raise ValueError("Memory slot shape differs from the fixed field shape")
        if not math.isfinite(float(record.mu)) or not math.isfinite(float(record.rho)):
            raise ValueError("Memory field coefficients must be finite")
        if float(record.rho) <= 0.0:
            raise ValueError("Parent-normalized rho must be positive")

    def _apply(self, record: MemoryFieldRecord, sign: float) -> None:
        key = record.key.to(device=self.device, dtype=self.dtype)
        slots = record.slots.to(device=self.device, dtype=self.dtype)
        coefficient = float(sign) * float(record.rho)
        for layer in range(self.layer_count):
            layer_slots = slots[layer]
            for start in range(0, self.key_dim, self.key_chunk_size):
                end = min(self.key_dim, start + self.key_chunk_size)
                update = key[start:end, None, None] * layer_slots[None, :, :]
                self.A[layer, start:end].add_(update, alpha=coefficient)
        self.B.add_(slots, alpha=coefficient * float(record.mu))

    def add_memory_fast(self, record: MemoryFieldRecord) -> None:
        self._validate_record(record)
        if record.memory_id in self.records:
            raise ValueError(f"Duplicate memory ID: {record.memory_id}")
        self._apply(record, +1.0)
        self.records[record.memory_id] = record
        self.parent_index[record.parent_id].add(record.memory_id)

    def remove_memory_fast(self, memory_id: str) -> MemoryFieldRecord:
        if memory_id not in self.records:
            raise KeyError(memory_id)
        record = self.records.pop(memory_id)
        self._apply(record, -1.0)
        self.parent_index[record.parent_id].remove(memory_id)
        if not self.parent_index[record.parent_id]:
            del self.parent_index[record.parent_id]
        return record

    def replace_memory_fast(self, memory_id: str, replacement: MemoryFieldRecord) -> None:
        self._validate_record(replacement)
        if replacement.memory_id != memory_id and replacement.memory_id in self.records:
            raise ValueError(f"Duplicate replacement memory ID: {replacement.memory_id}")
        original = self.remove_memory_fast(memory_id)
        try:
            self.add_memory_fast(replacement)
        except Exception:
            self.add_memory_fast(original)
            raise

    def remove_parent_fast(self, parent_id: str) -> list[MemoryFieldRecord]:
        memory_ids = sorted(self.parent_index.get(parent_id, set()))
        return [self.remove_memory_fast(memory_id) for memory_id in memory_ids]

    def restore_parent_fast(self, records: Sequence[MemoryFieldRecord]) -> None:
        for record in records:
            self.add_memory_fast(record)

    def audit_rebuild(self) -> tuple[Tensor, Tensor]:
        rebuilt = ReversibleSemanticSlotField(
            layer_count=self.layer_count,
            key_dim=self.key_dim,
            slot_count=self.slot_count,
            model_dim=self.model_dim,
            device=self.device,
            dtype=self.dtype,
            key_chunk_size=self.key_chunk_size,
        )
        for memory_id in sorted(self.records):
            rebuilt.add_memory_fast(self.records[memory_id])
        return rebuilt.A, rebuilt.B

    def read(self, query: Tensor) -> Tensor:
        query = query.to(device=self.device, dtype=self.dtype)
        if query.ndim == 1:
            if tuple(query.shape) != (self.key_dim,):
                raise ValueError("Field query shape differs from key dimension")
            raw = self.B + torch.einsum("k,lksd->lsd", query, self.A)
        elif query.ndim == 2:
            if int(query.shape[-1]) != self.key_dim:
                raise ValueError("Field query shape differs from key dimension")
            raw = self.B.unsqueeze(0) + torch.einsum("bk,lksd->blsd", query, self.A)
        else:
            raise ValueError("Field query must have shape [key] or [batch, key]")
        if not self.records:
            return torch.zeros_like(raw)
        return rms_norm(raw)

    def explicit_read(self, query: Tensor) -> Tensor:
        query = query.to(device=self.device, dtype=self.dtype)
        if query.ndim != 1 or tuple(query.shape) != (self.key_dim,):
            raise ValueError("Explicit field audit expects one query vector")
        raw = torch.zeros_like(self.B)
        for record in self.records.values():
            score = float(record.mu) + torch.dot(
                query, record.key.to(device=self.device, dtype=self.dtype)
            )
            raw.add_(
                record.slots.to(device=self.device, dtype=self.dtype),
                alpha=float(record.rho) * float(score),
            )
        if not self.records:
            return raw
        return rms_norm(raw)


def selector_seed_key(
    transition_factors: Tensor,
    interaction_core: Tensor,
    *,
    train_std: float,
    ensemble_size: int,
) -> Tensor:
    """Return the exact flattened memory key for one calibrated selector seed."""
    if transition_factors.ndim != 2 or interaction_core.ndim != 3:
        raise ValueError("Selector factors/core have unexpected rank")
    state_views, transition_views, rank = interaction_core.shape
    if tuple(transition_factors.shape) != (transition_views, rank):
        raise ValueError("Transition factor shape differs from selector core")
    scale = math.sqrt(state_views * transition_views * rank)
    effective = torch.einsum(
        "vwr,wr->vr", interaction_core, transition_factors
    ) / scale
    return (effective / (float(ensemble_size) * float(train_std))).reshape(-1)


def selector_seed_query(state_factors: Tensor) -> Tensor:
    if state_factors.ndim != 2:
        raise ValueError("Selector state factors must have shape [view, rank]")
    return state_factors.reshape(-1)


def selector_ensemble_intercept(
    train_means: Sequence[float], train_stds: Sequence[float]
) -> float:
    if len(train_means) != len(train_stds) or not train_means:
        raise ValueError("Selector calibration arrays differ")
    return -sum(
        float(mean) / float(std)
        for mean, std in zip(train_means, train_stds, strict=True)
    ) / len(train_means)
