from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from math import floor
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.training.direct_injection_channel_7dh import channel_gate


GLOBAL_SEED = 25101
K_TOKENS = 4
LAYER_FRACTIONS = (0.2, 0.4, 0.6, 0.8)
RESIDUAL_CONDITIONS = ("R_correct_residual", "S_shuffled_residual")


def selected_layer_indices(num_hidden_layers: int) -> tuple[int, ...]:
    if num_hidden_layers <= 0:
        raise ValueError("num_hidden_layers must be positive")
    values = tuple(
        floor(fraction * (num_hidden_layers - 1)) for fraction in LAYER_FRACTIONS
    )
    return tuple(dict.fromkeys(values))


def decoder_layers(model: nn.Module) -> Sequence[nn.Module]:
    base = getattr(model, "model", None)
    layers = getattr(base, "layers", None)
    if layers is None:
        raise ValueError("Model does not expose model.layers decoder blocks")
    return layers


def validate_delta_shape(
    delta: Tensor,
    *,
    batch_size: int,
    layer_count: int,
    token_count: int,
    model_dim: int,
) -> None:
    expected = (batch_size, layer_count, token_count, model_dim)
    if tuple(delta.shape) != expected:
        raise ValueError(f"DeltaH must have shape {expected}, got {tuple(delta.shape)}")
    if not bool(torch.isfinite(delta).all()):
        raise ValueError("DeltaH contains nonfinite values")


@dataclass
class ResidualHookAudit:
    selected_layer_indices: tuple[int, ...]
    selected_token_indices: list[list[int]]
    expected_prefill_length: int
    applied_calls: dict[int, int] = field(default_factory=dict)
    skipped_decode_calls: dict[int, int] = field(default_factory=dict)
    directly_modified_positions: dict[int, list[list[int]]] = field(default_factory=dict)
    base_norms: dict[int, list[float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_layer_indices": list(self.selected_layer_indices),
            "selected_token_indices": self.selected_token_indices,
            "expected_prefill_length": self.expected_prefill_length,
            "applied_calls": {str(k): v for k, v in self.applied_calls.items()},
            "skipped_decode_calls": {
                str(k): v for k, v in self.skipped_decode_calls.items()
            },
            "directly_modified_positions": {
                str(k): v for k, v in self.directly_modified_positions.items()
            },
            "base_norms": {str(k): v for k, v in self.base_norms.items()},
        }


class DeepResidualHooks(AbstractContextManager[ResidualHookAudit]):
    """Apply per-layer deltas only to fixed prompt positions at block input."""

    def __init__(
        self,
        *,
        model: nn.Module,
        layer_indices: Sequence[int],
        selected_token_indices: Tensor,
        delta: Tensor,
        expected_prefill_length: int,
    ) -> None:
        self.model = model
        self.layer_indices = tuple(int(value) for value in layer_indices)
        self.selected_token_indices = selected_token_indices.to(torch.long)
        self.delta = delta
        self.expected_prefill_length = int(expected_prefill_length)
        validate_delta_shape(
            delta,
            batch_size=int(selected_token_indices.shape[0]),
            layer_count=len(self.layer_indices),
            token_count=int(selected_token_indices.shape[1]),
            model_dim=int(delta.shape[-1]),
        )
        if bool((self.selected_token_indices < 0).any()):
            raise ValueError("Deep residual injection requires fully valid token indices")
        if bool((self.selected_token_indices >= self.expected_prefill_length).any()):
            raise ValueError("Selected token index lies outside the prompt prefill")
        layers = decoder_layers(model)
        if any(index < 0 or index >= len(layers) for index in self.layer_indices):
            raise ValueError("Selected residual layer lies outside model.layers")
        self.audit = ResidualHookAudit(
            selected_layer_indices=self.layer_indices,
            selected_token_indices=self.selected_token_indices.detach().cpu().tolist(),
            expected_prefill_length=self.expected_prefill_length,
        )
        self._handles: list[Any] = []

    def _hook(self, slot: int, layer_index: int):
        def apply_delta(
            module: nn.Module,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
            del module
            positional = bool(args)
            hidden = args[0] if positional else kwargs.get("hidden_states")
            if hidden is None:
                raise RuntimeError("Decoder layer hook did not receive hidden_states")
            if int(hidden.shape[1]) != self.expected_prefill_length:
                self.audit.skipped_decode_calls[layer_index] = (
                    self.audit.skipped_decode_calls.get(layer_index, 0) + 1
                )
                return None
            if int(hidden.shape[0]) != int(self.selected_token_indices.shape[0]):
                raise ValueError("Residual-hook batch size changed")
            updated = hidden.clone()
            layer_delta = self.delta[:, slot].to(
                device=hidden.device, dtype=hidden.dtype
            )
            norms: list[float] = []
            positions: list[list[int]] = []
            for row_index in range(int(hidden.shape[0])):
                indices = self.selected_token_indices[row_index].to(hidden.device)
                positions.append([int(value) for value in indices.detach().cpu()])
                norms.append(
                    float(
                        hidden[row_index, indices]
                        .detach()
                        .to(torch.float32)
                        .flatten()
                        .norm()
                        .cpu()
                    )
                )
                updated[row_index, indices] = (
                    updated[row_index, indices] + layer_delta[row_index]
                )
            self.audit.applied_calls[layer_index] = (
                self.audit.applied_calls.get(layer_index, 0) + 1
            )
            self.audit.directly_modified_positions[layer_index] = positions
            self.audit.base_norms.setdefault(layer_index, norms)
            if positional:
                return (updated, *args[1:]), kwargs
            changed = dict(kwargs)
            changed["hidden_states"] = updated
            return args, changed

        return apply_delta

    def __enter__(self) -> ResidualHookAudit:
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


def capture_original_layer_states(
    *,
    model: nn.Module,
    input_ids: Tensor,
    attention_mask: Tensor,
    selected_token_indices: Tensor,
    layer_indices: Sequence[int],
    position_ids: Tensor | None = None,
) -> Tensor:
    """Capture zero-intervention block inputs at the selected prompt positions."""
    layers = decoder_layers(model)
    captured: dict[int, Tensor] = {}
    handles = []

    def capture(layer_index: int):
        def hook(
            module: nn.Module,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> None:
            del module
            hidden = args[0] if args else kwargs.get("hidden_states")
            if hidden is None:
                raise RuntimeError("Capture hook did not receive hidden_states")
            rows = []
            for row_index in range(int(hidden.shape[0])):
                indices = selected_token_indices[row_index].to(hidden.device)
                rows.append(hidden[row_index, indices].detach().to(torch.float32))
            captured[layer_index] = torch.stack(rows, dim=0).cpu()

        return hook

    try:
        for layer_index in layer_indices:
            handles.append(
                layers[int(layer_index)].register_forward_pre_hook(
                    capture(int(layer_index)), with_kwargs=True
                )
            )
        base = getattr(model, "model", None)
        if base is None:
            raise ValueError("Model does not expose its frozen base model")
        with torch.no_grad():
            base(
                input_ids=input_ids,
                attention_mask=attention_mask,
                position_ids=position_ids,
                use_cache=False,
                return_dict=True,
            )
    finally:
        for handle in reversed(handles):
            handle.remove()
    if set(captured) != {int(value) for value in layer_indices}:
        raise RuntimeError("Not every selected layer was captured")
    return torch.stack([captured[int(index)] for index in layer_indices], dim=1)


def layer_and_global_ratios(delta: Tensor, original_states: Tensor) -> tuple[Tensor, Tensor]:
    if delta.shape != original_states.shape:
        raise ValueError("DeltaH and original residual states must have matching shapes")
    delta32 = delta.to(torch.float32)
    base32 = original_states.to(torch.float32)
    layer = delta32.flatten(start_dim=2).norm(dim=2) / base32.flatten(
        start_dim=2
    ).norm(dim=2).clamp_min(1.0e-12)
    global_ratio = delta32.flatten(start_dim=1).norm(dim=1) / base32.flatten(
        start_dim=1
    ).norm(dim=1).clamp_min(1.0e-12)
    return layer, global_ratio


def ratios_from_recorded_base_norms(
    delta: Tensor, base_norm_values: Sequence[float]
) -> tuple[Tensor, Tensor]:
    """Compute audit ratios without assuming recorded norms share DeltaH's device."""
    if delta.ndim == 3:
        delta = delta.unsqueeze(0)
    elif delta.ndim != 4:
        raise ValueError(
            "DeltaH must have shape [layers, tokens, hidden] or "
            "[batch, layers, tokens, hidden]"
        )
    if len(base_norm_values) != int(delta.shape[1]):
        raise ValueError("Recorded base norms must contain one value per layer")
    delta32 = delta.detach().to(torch.float32)
    base_norms = torch.as_tensor(
        base_norm_values,
        device=delta32.device,
        dtype=torch.float32,
    ).unsqueeze(0)
    layer = delta32.flatten(start_dim=2).norm(dim=2) / base_norms.clamp_min(1.0e-12)
    global_ratio = delta32.flatten(start_dim=1).norm(dim=1) / base_norms.square().sum(
        dim=1
    ).sqrt().clamp_min(1.0e-12)
    return layer, global_ratio


@torch.no_grad()
def project_deep_delta_(
    delta: Tensor,
    original_states: Tensor,
    *,
    max_ratio: float,
) -> None:
    if delta.shape != original_states.shape:
        raise ValueError("DeltaH and original residual states must have matching shapes")
    delta32 = delta.to(torch.float32)
    base32 = original_states.to(torch.float32)
    delta_norm = delta32.flatten(start_dim=2).norm(dim=2)
    base_norm = base32.flatten(start_dim=2).norm(dim=2)
    scale = torch.minimum(
        torch.ones_like(delta_norm),
        float(max_ratio) * base_norm / delta_norm.clamp_min(1.0e-12),
    )
    delta.mul_(scale[..., None, None].to(device=delta.device, dtype=delta.dtype))


def deep_residual_gate(
    *,
    r_minus_c0: Mapping[str, Any],
    r_minus_s: Mapping[str, Any],
    f3_minus_c0: Mapping[str, Any],
    positive_task_count: int,
    material_improvement: float = 0.05,
    material_degradation: float = 0.05,
) -> dict[str, Any]:
    return channel_gate(
        o_minus_c0=r_minus_c0,
        o_minus_s=r_minus_s,
        f3_minus_c0=f3_minus_c0,
        positive_task_count=positive_task_count,
        material_improvement=material_improvement,
        material_degradation=material_degradation,
    )


def continuation_decision(
    u4: Mapping[str, Any],
    u8: Mapping[str, Any],
    *,
    minimum_relative_improvement: float = 0.05,
    maximum_relative_deterioration: float = 0.05,
) -> dict[str, Any]:
    def improvement(metric: str) -> float:
        before = float(u4[metric])
        after = float(u8[metric])
        return (before - after) / max(abs(before), 1.0e-12)

    kl = improvement("teacher_policy_kl")
    ce = improvement("teacher_token_ce")
    ratio_ok = float(u8["delta_ratio_max"]) <= 1.0001
    continue_to_u16 = ratio_ok and (
        (
            kl >= float(minimum_relative_improvement)
            and ce >= -float(maximum_relative_deterioration)
        )
        or (
            ce >= float(minimum_relative_improvement)
            and kl >= -float(maximum_relative_deterioration)
        )
    )
    return {
        "policy_kl_relative_improvement": kl,
        "teacher_ce_relative_improvement": ce,
        "maximum_relative_deterioration": float(maximum_relative_deterioration),
        "checks": {
            "policy_kl_improved_materially": kl
            >= float(minimum_relative_improvement),
            "teacher_ce_improved_materially": ce
            >= float(minimum_relative_improvement),
            "other_metric_not_materially_worse": (
                kl >= -float(maximum_relative_deterioration)
                and ce >= -float(maximum_relative_deterioration)
            ),
            "ratio_lte_1": ratio_ok,
        },
        "continue_to_u16": continue_to_u16,
    }


def runtime_projection(
    *,
    pair_count: int,
    maximum_updates_per_pair: int,
    validation_state_count: int,
    generation_count: int,
    rates: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        rate = rates[name]
        validation = validation_state_count * (
            6.0 * float(rate["forward"]) + 2.0 * float(rate["generation"])
        )
        minimum_training = pair_count * 8 * (
            2.0 * float(rate["forward"]) + float(rate["backward"])
        )
        maximum_training = pair_count * maximum_updates_per_pair * (
            2.0 * float(rate["forward"]) + float(rate["backward"])
        )
        checkpoint_evaluation = 4 * pair_count * 2.0 * float(rate["forward"])
        generation = generation_count * float(rate["generation"])
        scenarios[name] = {
            "minimum_h100_hours": (
                validation + minimum_training + checkpoint_evaluation + generation
            )
            / 3600.0,
            "maximum_h100_hours": (
                validation + maximum_training + checkpoint_evaluation + generation
            )
            / 3600.0,
            "implementation_validation_hours": validation / 3600.0,
            "minimum_training_hours": minimum_training / 3600.0,
            "maximum_training_hours": maximum_training / 3600.0,
            "checkpoint_evaluation_hours": checkpoint_evaluation / 3600.0,
            "generation_hours": generation / 3600.0,
        }
    return {
        "optimizer_backward_calls_minimum": pair_count * 8,
        "optimizer_backward_calls_maximum": pair_count * maximum_updates_per_pair,
        "generation_count": generation_count,
        "scenarios": scenarios,
    }
