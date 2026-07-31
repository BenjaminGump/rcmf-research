from __future__ import annotations

from typing import Any

import torch
from torch import Tensor, nn

from rcmf.injection.base import MemoryInjector, PreparedInputs


class LogitBiasMemoryInjector(MemoryInjector):
    """Optional ablation: map memory z to a vocabulary bias."""

    def __init__(self, program_dim: int, vocab_size: int, initial_scale: float = 0.1) -> None:
        super().__init__()
        self.program_dim = program_dim
        self.vocab_size = vocab_size
        self.bias_head = nn.Linear(program_dim, vocab_size)
        nn.init.zeros_(self.bias_head.weight)
        nn.init.zeros_(self.bias_head.bias)
        self.bias_scale = nn.Parameter(torch.tensor(float(initial_scale)))

    def forward(self, memory_z: Tensor) -> Tensor:
        if memory_z.dim() != 2 or memory_z.shape[-1] != self.program_dim:
            raise ValueError("memory_z has the wrong shape")
        return self.bias_head(memory_z.to(torch.float32)) * self.bias_scale

    def apply_to_logits(self, logits: Tensor, memory_z: Tensor) -> Tensor:
        bias = self(memory_z).to(device=logits.device, dtype=logits.dtype)
        return logits + bias[:, None, :]

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
        if memory_z is not None:
            inputs["memory_logit_bias"] = self(memory_z)
        return PreparedInputs(inputs=inputs, memory_metadata={"injector": "logit_bias"})

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
        if memory_z is not None:
            inputs["memory_logit_bias"] = self(memory_z)
        return PreparedInputs(inputs=inputs, memory_metadata={"injector": "logit_bias"})

