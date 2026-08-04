from __future__ import annotations

from typing import Any

from torch import Tensor, nn

from rcmf.injection.base import MemoryInjector, PreparedInputs


class NoneMemoryInjector(MemoryInjector):
    """No-memory path used for baseline and equivalence tests."""

    def prepare_train_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        labels: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        inputs: dict[str, Any] = {"input_ids": input_ids}
        if attention_mask is not None:
            inputs["attention_mask"] = attention_mask
        if labels is not None:
            inputs["labels"] = labels
        return PreparedInputs(inputs=inputs, memory_metadata={"injector": "none"})

    def prepare_generate_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        inputs: dict[str, Any] = {"input_ids": input_ids}
        if attention_mask is not None:
            inputs["attention_mask"] = attention_mask
        return PreparedInputs(inputs=inputs, memory_metadata={"injector": "none"})


def build_injector(
    injector_type: str,
    program_dim: int,
    model_dim: int,
    vocab_size: int | None = None,
    num_prefix_tokens: int = 8,
    num_tokens: int | None = None,
    position: str = "first_k",
    initial_scale: float = 0.1,
) -> MemoryInjector:
    if injector_type == "none":
        return NoneMemoryInjector()
    if injector_type == "prefix":
        return PrefixMemoryInjector(
            program_dim=program_dim,
            model_dim=model_dim,
            num_prefix_tokens=num_prefix_tokens,
            initial_scale=initial_scale,
        )
    if injector_type == "additive_prefix":
        return AdditiveTokenMemoryInjector(
            program_dim=program_dim,
            model_dim=model_dim,
            num_tokens=num_tokens or num_prefix_tokens,
            position="first_k",
            initial_scale=initial_scale,
        )
    if injector_type == "additive_token":
        return AdditiveTokenMemoryInjector(
            program_dim=program_dim,
            model_dim=model_dim,
            num_tokens=num_tokens or num_prefix_tokens,
            position=position,
            initial_scale=initial_scale,
        )
    if injector_type == "logit_bias":
        if vocab_size is None:
            raise ValueError("vocab_size is required for logit_bias injector")
        return LogitBiasMemoryInjector(
            program_dim=program_dim,
            vocab_size=vocab_size,
            initial_scale=initial_scale,
        )
    raise ValueError(f"Unknown injector type: {injector_type}")


from rcmf.injection.logit_bias import LogitBiasMemoryInjector
from rcmf.injection.prefix import (
    AdditivePrefixMemoryInjector,
    AdditiveTokenMemoryInjector,
    PrefixMemoryInjector,
)
