from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import hashlib
import math
from typing import Any

import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.training.deep_residual_carrier_7e import decoder_layers
from rcmf.training.rcmf_joint_full_bank_9a import (
    INSERTION_LAYERS,
    KEY_DIM,
    PAYLOAD_DIM,
    SLOT_COUNT,
    StandardFieldCrossAttentionReader,
    rms_norm,
)


CALIBRATION_VERSION = "rcmf_benefit_preserving_calibration_9b_v1"
POSITIVE_FIELD_VERSION = "rcmf_positive_normalized_field_9b_v1"
EPS = 1.0e-6


@dataclass(frozen=True)
class CalibrationCandidate:
    candidate_id: str
    route: str
    layer_scales: tuple[float, float, float, float]
    field_control: str = "correct"
    cap_quantile: float | None = None
    confidence_target: float | None = None
    positive_kernel: bool = False
    critical_diagnostic_only: bool = False
    outcomes_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "route": self.route,
            "layer_scales": list(self.layer_scales),
            "field_control": self.field_control,
            "cap_quantile": self.cap_quantile,
            "confidence_target": self.confidence_target,
            "positive_kernel": self.positive_kernel,
            "critical_diagnostic_only": self.critical_diagnostic_only,
            "outcomes_used": self.outcomes_used,
        }


def preregistered_candidates() -> tuple[CalibrationCandidate, ...]:
    one = (1.0, 1.0, 1.0, 1.0)
    return (
        CalibrationCandidate("R0-original", "exact_control", one),
        CalibrationCandidate("R0-bare", "exact_control", (0.0,) * 4, "zero"),
        CalibrationCandidate("R0-shuffled", "exact_control", one, "shuffled"),
        CalibrationCandidate("G100", "global_scale", one),
        CalibrationCandidate("G075", "global_scale", (0.75,) * 4),
        CalibrationCandidate("G050", "global_scale", (0.50,) * 4),
        CalibrationCandidate("G025", "global_scale", (0.25,) * 4),
        CalibrationCandidate("L1", "layer_scale", (1.0, 1.0, 1.0, 0.5)),
        CalibrationCandidate("L2", "layer_scale", (1.0, 1.0, 0.5, 0.5)),
        CalibrationCandidate("L3", "layer_scale", (1.0, 0.75, 0.5, 0.25)),
        CalibrationCandidate("L4", "layer_scale", (0.5, 0.75, 1.0, 1.0)),
        CalibrationCandidate(
            "LOO7", "leave_one_layer_out", (0.0, 1.0, 1.0, 1.0),
            critical_diagnostic_only=True,
        ),
        CalibrationCandidate(
            "LOO14", "leave_one_layer_out", (1.0, 0.0, 1.0, 1.0),
            critical_diagnostic_only=True,
        ),
        CalibrationCandidate(
            "LOO21", "leave_one_layer_out", (1.0, 1.0, 0.0, 1.0),
            critical_diagnostic_only=True,
        ),
        CalibrationCandidate(
            "LOO28", "leave_one_layer_out", (1.0, 1.0, 1.0, 0.0),
            critical_diagnostic_only=True,
        ),
        CalibrationCandidate("C50", "trust_region_cap", one, cap_quantile=0.50),
        CalibrationCandidate("C75", "trust_region_cap", one, cap_quantile=0.75),
        CalibrationCandidate("C90", "trust_region_cap", one, cap_quantile=0.90),
        CalibrationCandidate("Q50", "pre_rms_confidence", one, confidence_target=0.50),
        CalibrationCandidate("Q75", "pre_rms_confidence", one, confidence_target=0.75),
        CalibrationCandidate("Q90", "pre_rms_confidence", one, confidence_target=0.90),
        CalibrationCandidate("E-positive", "positive_normalized_kernel", one, positive_kernel=True),
    )


def candidate_manifest() -> dict[str, Any]:
    rows = [candidate.as_dict() for candidate in preregistered_candidates()]
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate EXP-031B candidate ID")
    canonical = repr(
        sorted(
            (row["candidate_id"], row["route"], tuple(row["layer_scales"]))
            for row in rows
        )
    )
    return {
        "format": CALIBRATION_VERSION,
        "insertion_layers": list(INSERTION_LAYERS),
        "candidates": rows,
        "candidate_count": len(rows),
        "first37_candidate_limit": 2,
        "heldout_live_candidate_limit": 4,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "raw_memory_prompt": False,
        "learned_or_hard_gate": False,
        "outcomes_used": False,
        "library_sha256": hashlib.sha256(canonical.encode("ascii")).hexdigest(),
    }


def raw_compiled_field(*, query: Tensor, A: Tensor, B: Tensor) -> Tensor:
    if query.ndim == 1:
        return B + torch.einsum("k,ksp->sp", query.to(A.dtype), A)
    if query.ndim == 2:
        return B.unsqueeze(0) + torch.einsum("bk,ksp->bsp", query.to(A.dtype), A)
    raise ValueError("Query must be rank one or two")


def read_confidence_field(
    *, query: Tensor, A: Tensor, B: Tensor, tau: float | None, nonempty: bool
) -> tuple[Tensor, dict[str, Tensor]]:
    raw = raw_compiled_field(query=query, A=A, B=B)
    raw_rms = raw.float().square().mean(dim=(-2, -1), keepdim=True).sqrt()
    if not nonempty:
        return torch.zeros_like(raw), {
            "raw": raw,
            "raw_rms": raw_rms,
            "confidence": torch.zeros_like(raw_rms),
        }
    confidence = torch.ones_like(raw_rms)
    if tau is not None:
        if not math.isfinite(tau) or tau <= 0.0:
            raise ValueError("Confidence tau must be finite and positive")
        confidence = raw_rms / (raw_rms + float(tau))
    return rms_norm(raw) * confidence.to(raw.dtype), {
        "raw": raw,
        "raw_rms": raw_rms,
        "confidence": confidence,
    }


def tau_for_median_confidence(median_raw_rms: float, target: float) -> float:
    if not math.isfinite(median_raw_rms) or median_raw_rms <= 0.0:
        raise ValueError("Median raw-field RMS must be finite and positive")
    if not 0.0 < target < 1.0:
        raise ValueError("Confidence target must be between zero and one")
    return median_raw_rms * (1.0 - target) / target


def residual_ratio(delta: Tensor, hidden: Tensor, *, eps: float = EPS) -> Tensor:
    delta_rms = delta.float().square().mean(dim=-1, keepdim=True).sqrt()
    hidden_rms = hidden.float().square().mean(dim=-1, keepdim=True).sqrt()
    return delta_rms / (hidden_rms + eps)


def calibrate_residual(
    *, hidden: Tensor, delta: Tensor, scale: float, cap: float | None = None
) -> tuple[Tensor, dict[str, Tensor]]:
    if not math.isfinite(scale) or scale < 0.0:
        raise ValueError("Residual scale must be finite and nonnegative")
    if scale == 0.0:
        calibrated = torch.zeros_like(delta)
    elif scale == 1.0:
        calibrated = delta
    else:
        calibrated = delta * float(scale)
    before = residual_ratio(calibrated, hidden)
    multiplier = torch.ones_like(before)
    if cap is not None:
        if not math.isfinite(cap) or cap <= 0.0:
            raise ValueError("Residual cap must be finite and positive")
        multiplier = torch.clamp(float(cap) / (before + EPS), max=1.0)
        calibrated = calibrated * multiplier.to(calibrated.dtype)
    return calibrated, {
        "ratio_before_cap": before,
        "ratio_after_cap": residual_ratio(calibrated, hidden),
        "cap_multiplier": multiplier,
    }


def derive_layer_caps(
    ratio_rows: Mapping[int, Tensor], quantile: float
) -> dict[int, float]:
    if not 0.0 < quantile < 1.0:
        raise ValueError("Cap quantile must be between zero and one")
    if set(ratio_rows) != set(INSERTION_LAYERS):
        raise ValueError("Ratio rows must cover the four frozen reader layers")
    return {
        layer: float(torch.quantile(values.detach().float().flatten(), quantile))
        for layer, values in ratio_rows.items()
    }


def positive_feature(value: Tensor) -> Tensor:
    return F.elu(value) + 1.0


def compile_positive_field(
    *, keys: Tensor, payloads: Tensor, rho: Tensor
) -> tuple[Tensor, Tensor]:
    if keys.ndim != 2 or int(keys.shape[1]) != KEY_DIM:
        raise ValueError("Positive-field keys must have shape [memory,960]")
    if payloads.ndim != 3 or tuple(payloads.shape[1:]) != (
        SLOT_COUNT,
        PAYLOAD_DIM,
    ):
        raise ValueError("Positive-field payloads must have shape [memory,8,256]")
    if int(keys.shape[0]) != int(payloads.shape[0]) or tuple(rho.shape) != (
        int(keys.shape[0]),
    ):
        raise ValueError("Positive-field inputs have inconsistent memory counts")
    phi_keys = positive_feature(keys.to(payloads.dtype))
    weighted = payloads * rho[:, None, None].to(payloads.dtype)
    numerator = torch.einsum("nk,nsp->ksp", phi_keys, weighted)
    normalizer = torch.einsum("nk,n->k", phi_keys, rho.to(phi_keys.dtype))
    return numerator, normalizer


def read_positive_field(
    *, query: Tensor, numerator: Tensor, normalizer: Tensor, nonempty: bool
) -> Tensor:
    phi_query = positive_feature(query.to(numerator.dtype))
    if query.ndim == 1:
        raw = torch.einsum("k,ksp->sp", phi_query, numerator)
        denominator = torch.dot(phi_query, normalizer) + EPS
    elif query.ndim == 2:
        raw = torch.einsum("bk,ksp->bsp", phi_query, numerator)
        denominator = (
            torch.einsum("bk,k->b", phi_query, normalizer)[:, None, None] + EPS
        )
    else:
        raise ValueError("Positive-field query must be rank one or two")
    return rms_norm(raw / denominator) if nonempty else torch.zeros_like(raw)


@dataclass(frozen=True)
class PositiveFieldRecord:
    memory_id: str
    parent_id: str
    key: Tensor
    payload: Tensor
    rho: float


class ReversiblePositiveRCMFField:
    def __init__(
        self,
        *,
        device: torch.device | str = "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> None:
        self.device = torch.device(device)
        self.dtype = dtype
        self.numerator = torch.zeros(
            KEY_DIM, SLOT_COUNT, PAYLOAD_DIM, device=self.device, dtype=dtype
        )
        self.normalizer = torch.zeros(KEY_DIM, device=self.device, dtype=dtype)
        self.records: dict[str, PositiveFieldRecord] = {}
        self.parent_index: dict[str, set[str]] = defaultdict(set)

    @property
    def field_shape(self) -> dict[str, tuple[int, ...]]:
        return {"N": tuple(self.numerator.shape), "Z": tuple(self.normalizer.shape)}

    def _apply(self, record: PositiveFieldRecord, sign: float) -> None:
        if tuple(record.key.shape) != (KEY_DIM,) or tuple(
            record.payload.shape
        ) != (SLOT_COUNT, PAYLOAD_DIM):
            raise ValueError("Positive-field record shape differs")
        if not math.isfinite(record.rho) or record.rho <= 0.0:
            raise ValueError("Positive-field rho must be finite and positive")
        key = positive_feature(record.key.to(self.device, self.dtype))
        payload = record.payload.to(self.device, self.dtype)
        coefficient = float(sign) * float(record.rho)
        self.numerator.add_(
            torch.einsum("k,sp->ksp", key, payload), alpha=coefficient
        )
        self.normalizer.add_(key, alpha=coefficient)

    def add_memory_fast(self, record: PositiveFieldRecord) -> None:
        if record.memory_id in self.records:
            raise ValueError(f"Duplicate memory ID: {record.memory_id}")
        self._apply(record, 1.0)
        self.records[record.memory_id] = record
        self.parent_index[record.parent_id].add(record.memory_id)

    def remove_memory_fast(self, memory_id: str) -> PositiveFieldRecord:
        record = self.records.pop(memory_id)
        self._apply(record, -1.0)
        members = self.parent_index[record.parent_id]
        members.remove(memory_id)
        if not members:
            del self.parent_index[record.parent_id]
        return record

    def replace_memory_fast(
        self, memory_id: str, replacement: PositiveFieldRecord
    ) -> None:
        original = self.remove_memory_fast(memory_id)
        try:
            self.add_memory_fast(replacement)
        except Exception:
            self.add_memory_fast(original)
            raise

    def remove_parent_fast(self, parent_id: str) -> list[PositiveFieldRecord]:
        return [
            self.remove_memory_fast(memory_id)
            for memory_id in sorted(self.parent_index.get(parent_id, ()))
        ]

    def restore_parent_fast(self, records: Sequence[PositiveFieldRecord]) -> None:
        for record in records:
            self.add_memory_fast(record)

    def audit_rebuild(self) -> tuple[Tensor, Tensor]:
        numerator = torch.zeros_like(self.numerator)
        normalizer = torch.zeros_like(self.normalizer)
        for memory_id in sorted(self.records):
            record = self.records[memory_id]
            key = positive_feature(record.key.to(self.device, self.dtype))
            payload = record.payload.to(self.device, self.dtype)
            numerator.add_(
                torch.einsum("k,sp->ksp", key, payload), alpha=float(record.rho)
            )
            normalizer.add_(key, alpha=float(record.rho))
        return numerator, normalizer

    def read(self, query: Tensor) -> Tensor:
        return read_positive_field(
            query=query.to(self.device, self.dtype),
            numerator=self.numerator,
            normalizer=self.normalizer,
            nonempty=bool(self.records),
        )


@dataclass
class CalibrationReaderAudit:
    calls: dict[int, int] = field(default_factory=dict)
    attention_entropy: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    row_sum_error: dict[int, float] = field(default_factory=dict)
    hidden_rms: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    delta_rms: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    maximum_ratio: dict[int, float] = field(default_factory=dict)
    capped_fraction: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def as_dict(self) -> dict[str, Any]:
        values = (
            "calls",
            "attention_entropy",
            "row_sum_error",
            "hidden_rms",
            "delta_rms",
            "maximum_ratio",
            "capped_fraction",
        )
        return {
            name: {str(key): value for key, value in getattr(self, name).items()}
            for name in values
        }


class CalibratedFieldReaderHooks(
    AbstractContextManager[CalibrationReaderAudit]
):
    def __init__(
        self,
        *,
        model: torch.nn.Module,
        reader: StandardFieldCrossAttentionReader,
        slots: Tensor,
        layer_scales: Sequence[float] = (1.0, 1.0, 1.0, 1.0),
        layer_caps: Mapping[int, float] | None = None,
    ) -> None:
        if len(layer_scales) != len(reader.insertion_layers):
            raise ValueError("Layer-scale count differs from reader layers")
        self.reader = reader
        self.slots = slots
        self.layer_scales = dict(
            zip(reader.insertion_layers, map(float, layer_scales), strict=True)
        )
        self.layer_caps = dict(layer_caps or {})
        if not set(self.layer_caps).issubset(set(reader.insertion_layers)):
            raise ValueError("Residual cap names an unknown reader layer")
        self.zero_field = bool(torch.count_nonzero(slots.detach()).item() == 0)
        self.audit = CalibrationReaderAudit()
        self.probabilities: dict[int, Tensor] = {}
        self.raw_deltas: dict[int, Tensor] = {}
        self.calibrated_deltas: dict[int, Tensor] = {}
        self.token_hidden_rms: dict[int, Tensor] = {}
        self.token_delta_rms: dict[int, Tensor] = {}
        self.token_ratios: dict[int, Tensor] = {}
        layers = decoder_layers(model)
        if max(reader.insertion_layers) >= len(layers):
            raise ValueError("Reader insertion layer exceeds model depth")
        self._layers = layers
        self._handles: list[Any] = []

    def _hook(self, layer_index: int):
        def apply(
            module: torch.nn.Module, args: tuple[Any, ...], output: Any
        ) -> Any:
            del module, args
            hidden = output[0] if isinstance(output, tuple) else output
            adapter = self.reader.adapters[str(layer_index)]
            if self.zero_field:
                changed = hidden
                raw_delta = torch.zeros_like(hidden)
                probabilities = hidden.new_full(
                    (
                        int(hidden.shape[0]),
                        adapter.heads,
                        int(hidden.shape[1]),
                        SLOT_COUNT,
                    ),
                    1.0 / SLOT_COUNT,
                )
                calibrated = raw_delta
                stats = {
                    "ratio_after_cap": torch.zeros_like(
                        hidden[..., :1], dtype=torch.float32
                    ),
                    "cap_multiplier": torch.ones_like(
                        hidden[..., :1], dtype=torch.float32
                    ),
                }
            else:
                original_changed, probabilities, raw_delta = adapter(
                    hidden, self.slots
                )
                scale = self.layer_scales[layer_index]
                cap = self.layer_caps.get(layer_index)
                if scale == 1.0 and cap is None:
                    changed = original_changed
                    calibrated = raw_delta
                    ratio = residual_ratio(raw_delta, hidden)
                    stats = {
                        "ratio_after_cap": ratio,
                        "cap_multiplier": torch.ones_like(ratio),
                    }
                else:
                    calibrated, stats = calibrate_residual(
                        hidden=hidden, delta=raw_delta, scale=scale, cap=cap
                    )
                    changed = hidden + calibrated
            self.raw_deltas[layer_index] = raw_delta
            self.calibrated_deltas[layer_index] = calibrated
            self.probabilities[layer_index] = probabilities
            self.token_hidden_rms[layer_index] = hidden.detach().to(
                device="cpu", dtype=torch.float32
            ).square().mean(dim=-1, keepdim=True).sqrt()
            self.token_delta_rms[layer_index] = calibrated.detach().to(
                device="cpu", dtype=torch.float32
            ).square().mean(dim=-1, keepdim=True).sqrt()
            self.token_ratios[layer_index] = residual_ratio(
                calibrated, hidden
            ).detach().to(device="cpu", dtype=torch.float32)
            self.audit.calls[layer_index] = (
                self.audit.calls.get(layer_index, 0) + 1
            )
            work = probabilities.detach().float().clamp_min(1.0e-12)
            self.audit.attention_entropy[layer_index].append(
                float((-(work * work.log()).sum(dim=-1).mean()).cpu())
            )
            self.audit.row_sum_error[layer_index] = float(
                (work.sum(dim=-1) - 1.0).abs().max().cpu()
            )
            self.audit.hidden_rms[layer_index].append(
                float(hidden.detach().float().square().mean().sqrt().cpu())
            )
            self.audit.delta_rms[layer_index].append(
                float(calibrated.detach().float().square().mean().sqrt().cpu())
            )
            self.audit.maximum_ratio[layer_index] = max(
                self.audit.maximum_ratio.get(layer_index, 0.0),
                float(stats["ratio_after_cap"].max().detach().cpu()),
            )
            self.audit.capped_fraction[layer_index].append(
                float(
                    (stats["cap_multiplier"] < 1.0)
                    .float()
                    .mean()
                    .cpu()
                )
            )
            if isinstance(output, tuple):
                return (changed, *output[1:])
            return changed

        return apply

    def __enter__(self) -> CalibrationReaderAudit:
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
