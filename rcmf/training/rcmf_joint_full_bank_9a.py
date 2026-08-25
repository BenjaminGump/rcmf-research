from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import hashlib
import math
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.deep_residual_carrier_7e import decoder_layers
from rcmf.training.signature_balanced_field_7c import (
    SignatureBalancedFieldSelector,
)


GLOBAL_SEED = 25101
VIEW_DIM = 4096
WRITER_HIDDEN_DIM = 512
PAYLOAD_DIM = 256
SLOT_COUNT = 8
KEY_DIM = 960
ATTENTION_DIM = 512
ATTENTION_HEADS = 8
INSERTION_LAYERS = (7, 14, 21, 28)
FIELD_VERSION = "rcmf_reversible_full_bank_field_9a_v1"
WRITER_VERSION = "rcmf_aligned_complete_transition_writer_9a_v1"
READER_VERSION = "rcmf_standard_field_cross_attention_reader_9a_v1"


def rms_norm(value: Tensor, *, eps: float = 1.0e-6) -> Tensor:
    work = value.to(torch.float32)
    normalized = work * torch.rsqrt(work.square().mean(dim=-1, keepdim=True) + eps)
    return normalized.to(value.dtype)


def tensor_sha256(value: Tensor) -> str:
    work = value.detach().contiguous().cpu()
    payload = (
        str(work.dtype).encode("ascii")
        + str(tuple(work.shape)).encode("ascii")
        + work.numpy().tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


class SectionWriter(nn.Module):
    def __init__(
        self,
        *,
        input_dim: int = VIEW_DIM,
        hidden_dim: int = WRITER_HIDDEN_DIM,
        payload_dim: int = PAYLOAD_DIM,
    ) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(input_dim)
        self.input = nn.Linear(input_dim, hidden_dim)
        self.output = nn.Linear(hidden_dim, payload_dim)

    def forward(self, values: Tensor, pooling_embedding: Tensor) -> Tensor:
        if values.ndim != 3 or int(values.shape[1]) != 2:
            raise ValueError("A section writer expects [batch, mean/final, input]")
        if tuple(pooling_embedding.shape) != (2, self.input.out_features):
            raise ValueError("Pooling embedding shape differs from writer hidden size")
        hidden = self.input(self.norm(values))
        hidden = hidden + pooling_embedding.unsqueeze(0).to(hidden.dtype)
        return self.output(F.gelu(hidden))


class AlignedTransitionWriter(nn.Module):
    """Compile eight complete-section readouts into aligned RCMF payload slots."""

    section_names = ("goal", "pre_state", "action", "observation")

    def __init__(
        self,
        *,
        input_dim: int = VIEW_DIM,
        hidden_dim: int = WRITER_HIDDEN_DIM,
        payload_dim: int = PAYLOAD_DIM,
    ) -> None:
        super().__init__()
        self.input_dim = int(input_dim)
        self.hidden_dim = int(hidden_dim)
        self.payload_dim = int(payload_dim)
        self.pooling_type_embedding = nn.Parameter(torch.empty(2, hidden_dim))
        nn.init.normal_(self.pooling_type_embedding, mean=0.0, std=0.02)
        self.writers = nn.ModuleDict(
            {
                name: SectionWriter(
                    input_dim=input_dim,
                    hidden_dim=hidden_dim,
                    payload_dim=payload_dim,
                )
                for name in self.section_names
            }
        )

    def forward(self, views: Tensor) -> Tensor:
        if views.ndim != 3 or tuple(views.shape[1:]) != (SLOT_COUNT, self.input_dim):
            raise ValueError(
                f"RCMF writer expects [batch,{SLOT_COUNT},{self.input_dim}]"
            )
        sections = []
        for index, name in enumerate(self.section_names):
            sections.append(
                self.writers[name](
                    views[:, 2 * index : 2 * index + 2],
                    self.pooling_type_embedding,
                )
            )
        return torch.cat(sections, dim=1)

    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


class FrozenSelectorDecomposition(nn.Module):
    """Exact calibrated q/k decomposition of the frozen EXP-025C-R ensemble."""

    def __init__(
        self,
        *,
        models: Sequence[SignatureBalancedFieldSelector],
        train_means: Sequence[float],
        train_stds: Sequence[float],
    ) -> None:
        super().__init__()
        if len(models) != len(train_means) or len(models) != len(train_stds):
            raise ValueError("Selector models and calibration rows differ")
        if not models:
            raise ValueError("Selector decomposition needs at least one seed")
        self.models = nn.ModuleList(models)
        self.train_means = tuple(float(value) for value in train_means)
        self.train_stds = tuple(float(value) for value in train_stds)
        for model in self.models:
            model.eval()
            for parameter in model.parameters():
                parameter.requires_grad_(False)
        dimensions = {
            model.state_views * model.interaction_rank for model in self.models
        }
        if len(dimensions) != 1:
            raise ValueError("Selector seeds have inconsistent q/k dimensions")
        self.seed_key_dim = dimensions.pop()
        self.key_dim = self.seed_key_dim * len(self.models)

    @classmethod
    def from_checkpoints(
        cls,
        checkpoints: Sequence[Mapping[str, Any]],
        calibration: Sequence[Mapping[str, Any]],
    ) -> "FrozenSelectorDecomposition":
        models = []
        for checkpoint in checkpoints:
            state = checkpoint["model_state_dict"]
            state_views = len(
                [key for key in state if key.startswith("state_projection.") and key.endswith(".weight")]
            )
            transition_views = len(
                [
                    key
                    for key in state
                    if key.startswith("transition_projection.") and key.endswith(".weight")
                ]
            )
            first = state["state_projection.0.weight"]
            model = SignatureBalancedFieldSelector(
                state_views=state_views,
                transition_views=transition_views,
                input_dim=int(first.shape[1]),
                projection_dim=int(first.shape[0]),
                interaction_rank=int(state["state_rank.weight"].shape[0]),
            )
            model.load_state_dict(state)
            models.append(model)
        return cls(
            models=models,
            train_means=[float(row["train_mean"]) for row in calibration],
            train_stds=[float(row["train_std"]) for row in calibration],
        )

    @property
    def intercept(self) -> float:
        return -sum(
            mean / std
            for mean, std in zip(self.train_means, self.train_stds, strict=True)
        ) / len(self.models)

    @torch.no_grad()
    def query(self, state_views: Tensor) -> Tensor:
        return torch.cat(
            [model.state_factors(state_views).reshape(state_views.shape[0], -1) for model in self.models],
            dim=-1,
        )

    @torch.no_grad()
    def key(self, transition_views: Tensor) -> Tensor:
        values = []
        ensemble_size = len(self.models)
        for model, std in zip(self.models, self.train_stds, strict=True):
            factors = model.transition_factors(transition_views)
            scale = math.sqrt(
                model.state_views * model.transition_views * model.interaction_rank
            )
            effective = torch.einsum(
                "vwr,bwr->bvr", model.tensor_core, factors
            ) / scale
            values.append((effective / (ensemble_size * std)).flatten(start_dim=1))
        return torch.cat(values, dim=-1)

    @torch.no_grad()
    def direct_scores(self, state_views: Tensor, transition_views: Tensor) -> Tensor:
        calibrated = []
        for model, mean, std in zip(
            self.models, self.train_means, self.train_stds, strict=True
        ):
            calibrated.append(
                (model.score_matrix(state_views, transition_views) - mean) / std
            )
        return torch.stack(calibrated, dim=0).mean(dim=0)

    @torch.no_grad()
    def decomposed_scores(
        self, state_views: Tensor, transition_views: Tensor
    ) -> Tensor:
        return self.query(state_views) @ self.key(transition_views).T + self.intercept


@dataclass(frozen=True)
class RCMFFieldRecord:
    memory_id: str
    parent_id: str
    parent_task_id: str
    key: Tensor
    payload: Tensor
    rho: float
    mu: float = 0.0


class ReversibleRCMFField:
    """Production fixed-shape accumulator with direct reversible record deltas."""

    def __init__(
        self,
        *,
        key_dim: int = KEY_DIM,
        slot_count: int = SLOT_COUNT,
        payload_dim: int = PAYLOAD_DIM,
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> None:
        self.key_dim = int(key_dim)
        self.slot_count = int(slot_count)
        self.payload_dim = int(payload_dim)
        self.device = torch.device(device)
        self.dtype = dtype
        self.A = torch.zeros(
            self.key_dim,
            self.slot_count,
            self.payload_dim,
            device=self.device,
            dtype=self.dtype,
        )
        self.B = torch.zeros(
            self.slot_count,
            self.payload_dim,
            device=self.device,
            dtype=self.dtype,
        )
        self.records: dict[str, RCMFFieldRecord] = {}
        self.parent_index: dict[str, set[str]] = defaultdict(set)

    @property
    def field_shape(self) -> dict[str, tuple[int, ...]]:
        return {"A": tuple(self.A.shape), "B": tuple(self.B.shape)}

    @property
    def field_bytes(self) -> int:
        return self.A.numel() * self.A.element_size() + self.B.numel() * self.B.element_size()

    def _validate(self, record: RCMFFieldRecord) -> None:
        if tuple(record.key.shape) != (self.key_dim,):
            raise ValueError("Memory key shape differs from the field")
        if tuple(record.payload.shape) != (self.slot_count, self.payload_dim):
            raise ValueError("Memory payload shape differs from the field")
        if not math.isfinite(record.rho) or record.rho <= 0.0:
            raise ValueError("rho must be finite and positive")
        if not math.isfinite(record.mu):
            raise ValueError("mu must be finite")

    def _apply(self, record: RCMFFieldRecord, sign: float) -> None:
        key = record.key.to(device=self.device, dtype=self.dtype)
        payload = record.payload.to(device=self.device, dtype=self.dtype)
        coefficient = float(sign) * float(record.rho)
        self.A.add_(torch.einsum("k,sp->ksp", key, payload), alpha=coefficient)
        if record.mu:
            self.B.add_(payload, alpha=coefficient * float(record.mu))

    def add_memory_fast(self, record: RCMFFieldRecord) -> None:
        self._validate(record)
        if record.memory_id in self.records:
            raise ValueError(f"Duplicate memory ID: {record.memory_id}")
        self._apply(record, 1.0)
        self.records[record.memory_id] = record
        self.parent_index[record.parent_id].add(record.memory_id)

    def remove_memory_fast(self, memory_id: str) -> RCMFFieldRecord:
        record = self.records.pop(memory_id)
        self._apply(record, -1.0)
        members = self.parent_index[record.parent_id]
        members.remove(memory_id)
        if not members:
            del self.parent_index[record.parent_id]
        return record

    def replace_memory_fast(
        self, memory_id: str, replacement: RCMFFieldRecord
    ) -> None:
        self._validate(replacement)
        if replacement.memory_id != memory_id and replacement.memory_id in self.records:
            raise ValueError(f"Duplicate replacement memory ID: {replacement.memory_id}")
        original = self.remove_memory_fast(memory_id)
        try:
            self.add_memory_fast(replacement)
        except Exception:
            self.add_memory_fast(original)
            raise

    def remove_parent_fast(self, parent_id: str) -> list[RCMFFieldRecord]:
        memory_ids = sorted(self.parent_index.get(parent_id, ()))
        return [self.remove_memory_fast(memory_id) for memory_id in memory_ids]

    def restore_parent_fast(self, records: Sequence[RCMFFieldRecord]) -> None:
        for record in records:
            self.add_memory_fast(record)

    def audit_rebuild(self) -> tuple[Tensor, Tensor]:
        A = torch.zeros_like(self.A)
        B = torch.zeros_like(self.B)
        for memory_id in sorted(self.records):
            record = self.records[memory_id]
            key = record.key.to(device=self.device, dtype=self.dtype)
            payload = record.payload.to(device=self.device, dtype=self.dtype)
            A.add_(
                torch.einsum("k,sp->ksp", key, payload),
                alpha=float(record.rho),
            )
            if record.mu:
                B.add_(payload, alpha=float(record.rho) * float(record.mu))
        return A, B

    def read(self, query: Tensor) -> Tensor:
        if query.ndim not in (1, 2) or int(query.shape[-1]) != self.key_dim:
            raise ValueError("Field query has the wrong shape")
        query = query.to(device=self.device, dtype=self.dtype)
        if query.ndim == 1:
            raw = self.B + torch.einsum("k,ksp->sp", query, self.A)
        else:
            raw = self.B.unsqueeze(0) + torch.einsum("bk,ksp->bsp", query, self.A)
        if not self.records:
            return torch.zeros_like(raw)
        return rms_norm(raw)

    def explicit_read(self, query: Tensor) -> Tensor:
        if query.ndim != 1 or tuple(query.shape) != (self.key_dim,):
            raise ValueError("Explicit audit read expects one query")
        raw = torch.zeros_like(self.B)
        for record in self.records.values():
            score = record.mu + torch.dot(
                query.to(self.device, self.dtype),
                record.key.to(self.device, self.dtype),
            )
            raw.add_(
                record.payload.to(self.device, self.dtype),
                alpha=float(record.rho) * float(score),
            )
        return torch.zeros_like(raw) if not self.records else rms_norm(raw)


def compile_differentiable_field(
    *,
    keys: Tensor,
    payloads: Tensor,
    rho: Tensor,
    mu: Tensor | None = None,
) -> tuple[Tensor, Tensor]:
    if keys.ndim != 2 or int(keys.shape[1]) != KEY_DIM:
        raise ValueError("Keys must have shape [memory,960]")
    if payloads.ndim != 3 or tuple(payloads.shape[1:]) != (SLOT_COUNT, PAYLOAD_DIM):
        raise ValueError("Payloads must have shape [memory,8,256]")
    if int(keys.shape[0]) != int(payloads.shape[0]) or tuple(rho.shape) != (
        int(keys.shape[0]),
    ):
        raise ValueError("Field inputs have inconsistent memory counts")
    weighted = payloads * rho[:, None, None].to(payloads.dtype)
    A = torch.einsum("nk,nsp->ksp", keys.to(payloads.dtype), weighted)
    if mu is None:
        B = payloads.new_zeros((SLOT_COUNT, PAYLOAD_DIM))
    else:
        if tuple(mu.shape) != tuple(rho.shape):
            raise ValueError("mu and rho differ")
        B = torch.einsum("n,nsp->sp", rho.to(payloads.dtype) * mu.to(payloads.dtype), payloads)
    return A, B


def read_compiled_field(
    *, query: Tensor, A: Tensor, B: Tensor, nonempty: bool
) -> Tensor:
    if query.ndim == 1:
        raw = B + torch.einsum("k,ksp->sp", query.to(A.dtype), A)
    elif query.ndim == 2:
        raw = B.unsqueeze(0) + torch.einsum("bk,ksp->bsp", query.to(A.dtype), A)
    else:
        raise ValueError("Query must be rank one or two")
    return rms_norm(raw) if nonempty else torch.zeros_like(raw)


def subtract_task_field(
    *,
    A_total: Tensor,
    B_total: Tensor,
    A_task: Tensor,
    B_task: Tensor,
) -> tuple[Tensor, Tensor]:
    return A_total - A_task, B_total - B_task


def deterministic_payload_permutation(
    rows: Sequence[Mapping[str, Any]], *, seed: int = GLOBAL_SEED
) -> list[int]:
    if len(rows) < 2:
        raise ValueError("A payload permutation requires at least two memories")
    ordered = sorted(
        range(len(rows)),
        key=lambda index: (
            hashlib.sha256(
                f"{seed}:rcmf-key-payload:{rows[index]['transition_id']}".encode("utf-8")
            ).hexdigest(),
            str(rows[index]["transition_id"]),
        ),
    )
    best: tuple[int, int, int, list[int]] | None = None
    for offset in range(1, len(ordered)):
        mapping = [0] * len(rows)
        for position, source in enumerate(ordered):
            mapping[source] = ordered[(position + offset) % len(ordered)]
        fixed = sum(index == target for index, target in enumerate(mapping))
        same_task = sum(
            str(rows[index]["parent_task_id"]) == str(rows[target]["parent_task_id"])
            for index, target in enumerate(mapping)
        )
        same_signature = sum(
            str(rows[index].get("signature_class_id", ""))
            == str(rows[target].get("signature_class_id", ""))
            for index, target in enumerate(mapping)
        )
        candidate = (fixed, same_task, same_signature, mapping)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    assert best is not None
    if best[0]:
        raise RuntimeError("Deterministic payload permutation has fixed points")
    return best[3]


class LayerFieldCrossAttention(nn.Module):
    def __init__(
        self,
        *,
        model_dim: int = VIEW_DIM,
        payload_dim: int = PAYLOAD_DIM,
        attention_dim: int = ATTENTION_DIM,
        heads: int = ATTENTION_HEADS,
    ) -> None:
        super().__init__()
        if attention_dim % heads:
            raise ValueError("Attention dimension must divide evenly across heads")
        self.model_dim = int(model_dim)
        self.payload_dim = int(payload_dim)
        self.attention_dim = int(attention_dim)
        self.heads = int(heads)
        self.head_dim = self.attention_dim // self.heads
        self.query_norm = nn.LayerNorm(model_dim)
        self.memory_norm = nn.LayerNorm(payload_dim)
        self.query = nn.Linear(model_dim, attention_dim, bias=False)
        self.key = nn.Linear(payload_dim, attention_dim, bias=False)
        self.value = nn.Linear(payload_dim, attention_dim, bias=False)
        self.output = nn.Linear(attention_dim, model_dim, bias=False)
        nn.init.zeros_(self.output.weight)

    def output_is_zero(self) -> bool:
        return bool(torch.equal(self.output.weight, torch.zeros_like(self.output.weight)))

    def forward(self, hidden: Tensor, slots: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        if hidden.ndim != 3 or int(hidden.shape[-1]) != self.model_dim:
            raise ValueError("Reader query must have shape [batch,token,model]")
        if slots.ndim == 2:
            slots = slots.unsqueeze(0)
        if slots.ndim != 3 or tuple(slots.shape[1:]) != (
            SLOT_COUNT,
            self.payload_dim,
        ):
            raise ValueError("Reader slots must have shape [batch,8,256]")
        if int(slots.shape[0]) == 1 and int(hidden.shape[0]) > 1:
            slots = slots.expand(int(hidden.shape[0]), -1, -1)
        if int(slots.shape[0]) != int(hidden.shape[0]):
            raise ValueError("Reader query and slots use different batch sizes")
        q = self.query(self.query_norm(hidden))
        memory = self.memory_norm(slots.to(hidden.device, hidden.dtype))
        k = self.key(memory)
        v = self.value(memory)
        batch, query_tokens = q.shape[:2]
        q = q.view(batch, query_tokens, self.heads, self.head_dim).transpose(1, 2)
        k = k.view(batch, SLOT_COUNT, self.heads, self.head_dim).transpose(1, 2)
        v = v.view(batch, SLOT_COUNT, self.heads, self.head_dim).transpose(1, 2)
        scores = torch.matmul(
            q.to(torch.float32), k.to(torch.float32).transpose(-2, -1)
        ) / math.sqrt(self.head_dim)
        probabilities = F.softmax(scores, dim=-1, dtype=torch.float32).to(v.dtype)
        context = torch.matmul(probabilities, v)
        context = context.transpose(1, 2).contiguous().view(
            batch, query_tokens, self.attention_dim
        )
        delta = self.output(context.to(self.output.weight.dtype)).to(hidden.dtype)
        return hidden + delta, probabilities, delta


class StandardFieldCrossAttentionReader(nn.Module):
    def __init__(
        self,
        *,
        insertion_layers: Sequence[int] = INSERTION_LAYERS,
        model_dim: int = VIEW_DIM,
        payload_dim: int = PAYLOAD_DIM,
        attention_dim: int = ATTENTION_DIM,
        heads: int = ATTENTION_HEADS,
    ) -> None:
        super().__init__()
        self.insertion_layers = tuple(int(value) for value in insertion_layers)
        if len(set(self.insertion_layers)) != len(self.insertion_layers):
            raise ValueError("Reader insertion layers must be unique")
        self.adapters = nn.ModuleDict(
            {
                str(layer): LayerFieldCrossAttention(
                    model_dim=model_dim,
                    payload_dim=payload_dim,
                    attention_dim=attention_dim,
                    heads=heads,
                )
                for layer in self.insertion_layers
            }
        )

    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())

    def outputs_are_zero(self) -> bool:
        return all(adapter.output_is_zero() for adapter in self.adapters.values())


@dataclass
class FieldReaderAudit:
    calls: dict[int, int] = field(default_factory=dict)
    query_lengths: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    attention_entropy: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    attention_row_sum_error: dict[int, float] = field(default_factory=dict)
    delta_norms: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": {str(k): v for k, v in self.calls.items()},
            "query_lengths": {str(k): v for k, v in self.query_lengths.items()},
            "attention_entropy": {str(k): v for k, v in self.attention_entropy.items()},
            "attention_row_sum_error": {
                str(k): v for k, v in self.attention_row_sum_error.items()
            },
            "delta_norms": {str(k): v for k, v in self.delta_norms.items()},
        }


class FieldReaderHooks(AbstractContextManager[FieldReaderAudit]):
    def __init__(
        self,
        *,
        model: nn.Module,
        reader: StandardFieldCrossAttentionReader,
        slots: Tensor,
    ) -> None:
        self.model = model
        self.reader = reader
        self.slots = slots
        self.audit = FieldReaderAudit()
        self.deltas: dict[int, Tensor] = {}
        self.probabilities: dict[int, Tensor] = {}
        layers = decoder_layers(model)
        if not reader.insertion_layers:
            raise ValueError("Reader has no insertion layers")
        if max(reader.insertion_layers) >= len(layers):
            raise ValueError("Reader insertion layer exceeds model depth")
        self._layers = layers
        self._handles: list[Any] = []

    def _hook(self, layer_index: int):
        def apply(module: nn.Module, args: tuple[Any, ...], output: Any) -> Any:
            del module, args
            hidden = output[0] if isinstance(output, tuple) else output
            changed, probabilities, delta = self.reader.adapters[str(layer_index)](
                hidden, self.slots
            )
            self.deltas[layer_index] = delta
            self.probabilities[layer_index] = probabilities
            self.audit.calls[layer_index] = self.audit.calls.get(layer_index, 0) + 1
            self.audit.query_lengths[layer_index].append(int(hidden.shape[1]))
            work = probabilities.detach().to(torch.float32).clamp_min(1.0e-12)
            self.audit.attention_entropy[layer_index].append(
                float((-(work * work.log()).sum(dim=-1).mean()).cpu())
            )
            self.audit.attention_row_sum_error[layer_index] = float(
                (work.sum(dim=-1) - 1.0).abs().max().cpu()
            )
            self.audit.delta_norms[layer_index].append(
                float(delta.detach().to(torch.float32).norm().cpu())
            )
            if isinstance(output, tuple):
                return (changed, *output[1:])
            return changed

        return apply

    def residual_penalty(self) -> Tensor:
        if not self.deltas:
            return self.slots.sum() * 0.0
        return torch.stack(
            [value.to(torch.float32).square().mean() for value in self.deltas.values()]
        ).mean()

    def attention_entropy_tensor(self) -> Tensor:
        if not self.probabilities:
            return self.slots.sum() * 0.0
        values = []
        for probabilities in self.probabilities.values():
            work = probabilities.to(torch.float32).clamp_min(1.0e-12)
            values.append(-(work * work.log()).sum(dim=-1).mean())
        return torch.stack(values).mean()

    def __enter__(self) -> FieldReaderAudit:
        for layer_index in self.reader.insertion_layers:
            self._handles.append(
                self._layers[layer_index].register_forward_hook(
                    self._hook(layer_index)
                )
            )
        return self.audit

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()


def freeze_module(module: nn.Module) -> None:
    module.eval()
    for parameter in module.parameters():
        parameter.requires_grad_(False)


def assert_frozen_without_gradients(module: nn.Module) -> None:
    if any(parameter.requires_grad for parameter in module.parameters()):
        raise RuntimeError("Frozen module contains trainable parameters")
    if any(parameter.grad is not None for parameter in module.parameters()):
        raise RuntimeError("Frozen module received gradients")
