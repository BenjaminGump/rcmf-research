from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import torch
from torch import Tensor, nn


@dataclass
class PreparedInputs:
    inputs: dict[str, Any]
    memory_metadata: dict[str, Any]


class MemoryInjector(ABC, nn.Module):
    @abstractmethod
    def prepare_train_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        labels: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        raise NotImplementedError

    @abstractmethod
    def prepare_generate_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        raise NotImplementedError


def build_position_ids(attention_mask: Tensor) -> Tensor:
    return attention_mask.to(torch.long).cumsum(dim=-1).sub_(1).clamp_min_(0)

