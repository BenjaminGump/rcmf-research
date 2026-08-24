from __future__ import annotations

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


GLOBAL_SEED = 25101
LATENT_DIM = 256
READER_BOTTLENECK = 64
LAYER_INDICES = (7, 14, 21, 28)
TOKEN_COUNT = 4
READER_VERSION = "fixed_memory_reader_8a_v1"


class _LayerReader(nn.Module):
    def __init__(self, *, model_dim: int, latent_dim: int, bottleneck: int) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(model_dim, elementwise_affine=False)
        self.hidden = nn.Linear(model_dim, bottleneck, bias=False)
        self.latent = nn.Linear(latent_dim, bottleneck, bias=False)
        self.output = nn.Linear(bottleneck, model_dim, bias=False)
        nn.init.zeros_(self.output.weight)

    def forward(self, hidden: Tensor, latent: Tensor) -> Tensor:
        input_dtype = hidden.dtype
        work_dtype = self.hidden.weight.dtype
        state = F.silu(self.hidden(self.norm(hidden.to(work_dtype))))
        memory = self.latent(latent.to(work_dtype)).unsqueeze(1)
        return self.output(state * memory).to(input_dtype)


class FixedMemoryReader(nn.Module):
    """Map one fixed-size memory latent into native residual coordinates."""

    def __init__(
        self,
        *,
        model_dim: int,
        latent_dim: int = LATENT_DIM,
        bottleneck: int = READER_BOTTLENECK,
        layer_count: int = len(LAYER_INDICES),
    ) -> None:
        super().__init__()
        self.model_dim = int(model_dim)
        self.latent_dim = int(latent_dim)
        self.bottleneck = int(bottleneck)
        self.layer_count = int(layer_count)
        self.layers = nn.ModuleList(
            _LayerReader(
                model_dim=self.model_dim,
                latent_dim=self.latent_dim,
                bottleneck=self.bottleneck,
            )
            for _ in range(self.layer_count)
        )

    def layer_delta(self, slot: int, hidden: Tensor, latent: Tensor) -> Tensor:
        if hidden.ndim != 3 or int(hidden.shape[-1]) != self.model_dim:
            raise ValueError("Reader hidden states must have shape [batch, token, model_dim]")
        if latent.ndim != 2 or int(latent.shape[-1]) != self.latent_dim:
            raise ValueError("Reader latent must have shape [batch, latent_dim]")
        if int(hidden.shape[0]) != int(latent.shape[0]):
            raise ValueError("Reader hidden and latent batch sizes differ")
        return self.layers[int(slot)](hidden, latent)

    def output_layers_zero(self) -> bool:
        return all(bool(torch.equal(layer.output.weight, torch.zeros_like(layer.output.weight))) for layer in self.layers)

    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())


@dataclass
class ReaderHookAudit:
    selected_layers: tuple[int, ...]
    selected_token_indices: list[list[int]]
    expected_prefill_length: int
    applied_calls: dict[int, int] = field(default_factory=dict)
    skipped_decode_calls: dict[int, int] = field(default_factory=dict)
    directly_modified_positions: dict[int, list[list[int]]] = field(default_factory=dict)
    base_norms: dict[int, list[float]] = field(default_factory=dict)
    raw_delta_norms: dict[int, list[float]] = field(default_factory=dict)
    applied_delta_norms: dict[int, list[float]] = field(default_factory=dict)
    projection_scales: dict[int, list[float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_layers": list(self.selected_layers),
            "selected_token_indices": self.selected_token_indices,
            "expected_prefill_length": self.expected_prefill_length,
            "applied_calls": {str(key): value for key, value in self.applied_calls.items()},
            "skipped_decode_calls": {
                str(key): value for key, value in self.skipped_decode_calls.items()
            },
            "directly_modified_positions": {
                str(key): value for key, value in self.directly_modified_positions.items()
            },
            "base_norms": {str(key): value for key, value in self.base_norms.items()},
            "raw_delta_norms": {
                str(key): value for key, value in self.raw_delta_norms.items()
            },
            "applied_delta_norms": {
                str(key): value for key, value in self.applied_delta_norms.items()
            },
            "projection_scales": {
                str(key): value for key, value in self.projection_scales.items()
            },
        }


class FixedMemoryReaderHooks(AbstractContextManager[ReaderHookAudit]):
    """Apply reader-generated residuals at four prompt positions only."""

    def __init__(
        self,
        *,
        model: nn.Module,
        reader: FixedMemoryReader,
        layer_indices: Sequence[int],
        selected_token_indices: Tensor,
        latent: Tensor,
        expected_prefill_length: int,
        maximum_layer_ratio: float = 1.0,
    ) -> None:
        self.model = model
        self.reader = reader
        self.layer_indices = tuple(int(value) for value in layer_indices)
        self.selected_token_indices = selected_token_indices.to(torch.long)
        self.latent = latent
        self.expected_prefill_length = int(expected_prefill_length)
        self.maximum_layer_ratio = float(maximum_layer_ratio)
        if len(self.layer_indices) != reader.layer_count:
            raise ValueError("Reader layer count differs from selected residual layers")
        if self.selected_token_indices.ndim != 2:
            raise ValueError("Selected token indices must have shape [batch, token]")
        if int(self.selected_token_indices.shape[1]) != TOKEN_COUNT:
            raise ValueError("Fixed reader requires exactly four selected tokens")
        if int(self.selected_token_indices.shape[0]) != int(latent.shape[0]):
            raise ValueError("Reader token-index and latent batch sizes differ")
        if bool((self.selected_token_indices < 0).any()) or bool(
            (self.selected_token_indices >= self.expected_prefill_length).any()
        ):
            raise ValueError("Reader token position lies outside prompt prefill")
        if not 0.0 < self.maximum_layer_ratio <= 1.0:
            raise ValueError("maximum_layer_ratio must be in (0, 1]")
        layers = decoder_layers(model)
        if any(index < 0 or index >= len(layers) for index in self.layer_indices):
            raise ValueError("Reader layer index lies outside model.layers")
        self.audit = ReaderHookAudit(
            selected_layers=self.layer_indices,
            selected_token_indices=self.selected_token_indices.detach().cpu().tolist(),
            expected_prefill_length=self.expected_prefill_length,
        )
        self.raw_layer_ratios: dict[int, Tensor] = {}
        self.applied_layer_ratios: dict[int, Tensor] = {}
        self.applied_deltas: dict[int, Tensor] = {}
        self._handles: list[Any] = []

    def _hook(self, slot: int, layer_index: int):
        def apply(
            module: nn.Module,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
            del module
            positional = bool(args)
            hidden = args[0] if positional else kwargs.get("hidden_states")
            if hidden is None:
                raise RuntimeError("Reader hook did not receive hidden_states")
            if int(hidden.shape[1]) != self.expected_prefill_length:
                self.audit.skipped_decode_calls[layer_index] = (
                    self.audit.skipped_decode_calls.get(layer_index, 0) + 1
                )
                return None
            if int(hidden.shape[0]) != int(self.latent.shape[0]):
                raise ValueError("Reader hook batch size changed")
            gathered = torch.stack(
                [
                    hidden[row, self.selected_token_indices[row].to(hidden.device)]
                    for row in range(int(hidden.shape[0]))
                ],
                dim=0,
            )
            raw = self.reader.layer_delta(
                slot,
                gathered,
                self.latent.to(device=hidden.device, dtype=hidden.dtype),
            )
            raw32 = raw.to(torch.float32)
            base32 = gathered.to(torch.float32)
            raw_norm = raw32.flatten(start_dim=1).norm(dim=1)
            base_norm = base32.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-12)
            raw_ratio = raw_norm / base_norm
            scale = torch.minimum(
                torch.ones_like(raw_ratio),
                self.maximum_layer_ratio / raw_ratio.clamp_min(1.0e-12),
            )
            applied = raw * scale[:, None, None].to(raw.dtype)
            applied_ratio = applied.to(torch.float32).flatten(start_dim=1).norm(dim=1) / base_norm
            updated = hidden.clone()
            positions = []
            for row in range(int(hidden.shape[0])):
                indices = self.selected_token_indices[row].to(hidden.device)
                positions.append([int(value) for value in indices.detach().cpu()])
                updated[row, indices] = updated[row, indices] + applied[row]
            self.raw_layer_ratios[layer_index] = raw_ratio
            self.applied_layer_ratios[layer_index] = applied_ratio
            self.applied_deltas[layer_index] = applied
            self.audit.applied_calls[layer_index] = self.audit.applied_calls.get(layer_index, 0) + 1
            self.audit.directly_modified_positions[layer_index] = positions
            self.audit.base_norms[layer_index] = base_norm.detach().cpu().tolist()
            self.audit.raw_delta_norms[layer_index] = raw_norm.detach().cpu().tolist()
            self.audit.applied_delta_norms[layer_index] = (
                applied.to(torch.float32).flatten(start_dim=1).norm(dim=1).detach().cpu().tolist()
            )
            self.audit.projection_scales[layer_index] = scale.detach().cpu().tolist()
            if positional:
                return (updated, *args[1:]), kwargs
            changed = dict(kwargs)
            changed["hidden_states"] = updated
            return args, changed

        return apply

    def maximum_ratio_tensor(self) -> Tensor:
        if not self.applied_layer_ratios:
            return torch.zeros((), device=self.latent.device)
        return torch.stack(
            [self.applied_layer_ratios[index] for index in self.layer_indices], dim=1
        ).max()

    def residual_norm_tensor(self) -> Tensor:
        if not self.applied_deltas:
            return torch.zeros((), device=self.latent.device)
        return torch.stack(
            [self.applied_deltas[index].to(torch.float32).pow(2).sum() for index in self.layer_indices]
        ).sum().sqrt()

    def __enter__(self) -> ReaderHookAudit:
        layers = decoder_layers(self.model)
        for slot, layer_index in enumerate(self.layer_indices):
            self._handles.append(
                layers[layer_index].register_forward_pre_hook(
                    self._hook(slot, layer_index), with_kwargs=True
                )
            )
        return self.audit

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        for handle in reversed(self._handles):
            handle.remove()
        self._handles.clear()


def stratified_live_steps(
    step_ids: Sequence[int], *, task_id: str, maximum: int = 6, seed: int = GLOBAL_SEED
) -> list[int]:
    ordered = sorted({int(value) for value in step_ids})
    if len(ordered) <= int(maximum):
        return ordered
    buckets: list[list[int]] = [[], [], []]
    for index, step_id in enumerate(ordered):
        bucket = min(2, (3 * index) // len(ordered))
        buckets[bucket].append(step_id)
    quota = max(1, int(maximum) // 3)
    selected = []
    for bucket_index, values in enumerate(buckets):
        ranked = sorted(
            values,
            key=lambda step_id: hashlib.sha256(
                f"{seed}:on-policy:{task_id}:{bucket_index}:{step_id}".encode()
            ).hexdigest(),
        )
        selected.extend(ranked[:quota])
    if len(selected) < int(maximum):
        remainder = [value for value in ordered if value not in set(selected)]
        selected.extend(remainder[: int(maximum) - len(selected)])
    return sorted(selected[: int(maximum)])


def _control_metrics(
    summary: Mapping[str, Any], short_name: str, full_name: str
) -> Mapping[str, Any]:
    value = summary.get(short_name, summary.get(full_name))
    if not isinstance(value, Mapping):
        raise KeyError(f"Missing reader validation control: {short_name}/{full_name}")
    return value


def reader_behavior_classification(summary: Mapping[str, Any]) -> str:
    r1 = _control_metrics(summary, "R1", "R1_correct")
    r2 = _control_metrics(summary, "R2", "R2_transition_shuffle")
    r3 = _control_metrics(summary, "R3", "R3_state_shuffle")
    r0 = _control_metrics(summary, "R0", "R0_zero")
    signature_improves = float(r1["action_signature"]) > float(r0["action_signature"])
    successor_improves = float(r1["semantic_successor"]) > float(r0["semantic_successor"])
    signature_beats_both = float(r1["action_signature"]) > max(
        float(r2["action_signature"]), float(r3["action_signature"])
    )
    successor_beats_both = float(r1["semantic_successor"]) > max(
        float(r2["semantic_successor"]), float(r3["semantic_successor"])
    )
    signature_beats_one = float(r1["action_signature"]) > min(
        float(r2["action_signature"]), float(r3["action_signature"])
    )
    successor_beats_one = float(r1["semantic_successor"]) > min(
        float(r2["semantic_successor"]), float(r3["semantic_successor"])
    )
    execution_ok = float(r1["execution"]) >= float(r0["execution"]) - 0.05 - 1.0e-12
    task_ok = int(summary["positive_task_count"]) * 2 >= int(summary["task_count"])
    companion_ok = not (
        float(r1["action_signature"]) + 0.05 < min(
            float(r2["action_signature"]), float(r3["action_signature"])
        )
        or float(r1["semantic_successor"]) + 0.05 < min(
            float(r2["semantic_successor"]), float(r3["semantic_successor"])
        )
    )
    if (
        (signature_improves or successor_improves)
        and (signature_beats_both or successor_beats_both)
        and companion_ok
        and execution_ok
        and task_ok
    ):
        return "STRONG"
    if (
        (signature_improves or successor_improves)
        and (signature_beats_one or successor_beats_one)
        and execution_ok
    ):
        return "PARTIAL"
    return "CLEAR_FAILURE"


def reader_validation_score(summary: Mapping[str, Any]) -> float:
    r1 = _control_metrics(summary, "R1", "R1_correct")
    r2 = _control_metrics(summary, "R2", "R2_transition_shuffle")
    r3 = _control_metrics(summary, "R3", "R3_state_shuffle")
    r0 = _control_metrics(summary, "R0", "R0_zero")
    return (
        float(r1["semantic_successor"])
        - max(float(r2["semantic_successor"]), float(r3["semantic_successor"]))
        + 0.5
        * (
            float(r1["action_signature"])
            - max(float(r2["action_signature"]), float(r3["action_signature"]))
        )
        + 0.25
        * (
            float(r1["semantic_successor"])
            - float(r0["semantic_successor"])
        )
        - max(0.0, float(r0["execution"]) - float(r1["execution"]))
        - 0.01 * float(r1.get("raw_policy_kl", 0.0))
    )


def select_reader_checkpoint(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    visited = []
    for row in rows:
        copied = dict(row)
        copied["classification"] = reader_behavior_classification(copied["validation"])
        copied["selection_score"] = reader_validation_score(copied["validation"])
        ratio = float(copied["validation"]["maximum_layer_ratio"])
        copied["eligible"] = math.isfinite(copied["selection_score"]) and ratio <= 1.0001
        visited.append(copied)
    for classification in ("STRONG", "PARTIAL"):
        candidates = [
            row
            for row in visited
            if row["eligible"] and row["classification"] == classification
        ]
        if candidates:
            return max(
                candidates,
                key=lambda row: (
                    float(row["selection_score"]),
                    -float(
                        _control_metrics(
                            row["validation"], "R1", "R1_correct"
                        ).get("raw_policy_kl", 0.0)
                    ),
                    -int(row["updates_per_pair"]),
                ),
            )
    return None


def class_balanced_row_weights(labels: Sequence[str]) -> list[float]:
    counts = {label: labels.count(label) for label in sorted(set(labels))}
    if not counts:
        raise ValueError("Class balancing requires labels")
    raw = [1.0 / counts[label] for label in labels]
    scale = len(raw) / sum(raw)
    return [value * scale for value in raw]
