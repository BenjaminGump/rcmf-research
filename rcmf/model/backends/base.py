from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import torch
from torch import Tensor

from rcmf.injection.base import MemoryInjector


ChatMessage = dict[str, str]


@dataclass
class TokenizedBatch:
    input_ids: Tensor
    attention_mask: Tensor
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrainOutput:
    loss: Tensor | None
    logits: Tensor
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerateOutput:
    text: str
    token_ids: list[int]
    usage: dict[str, int]
    ttft_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class ModelBackend(Protocol):
    tokenizer: Any
    model: Any

    def tokenize_messages(
        self,
        messages: list[ChatMessage],
        add_generation_prompt: bool = True,
        return_tensors: str = "pt",
    ) -> TokenizedBatch:
        ...

    def forward_train(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
        labels: Tensor | None = None,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> TrainOutput:
        ...

    def encode_input_ids(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
    ) -> Tensor:
        ...

    def encode_texts(
        self,
        texts: list[str],
        batch_size: int = 1,
        add_special_tokens: bool = True,
    ) -> Tensor:
        ...

    def encode_text_chunks(
        self,
        texts: list[str],
        batch_size: int = 1,
        add_special_tokens: bool = True,
        max_chunk_tokens: int | None = None,
    ) -> tuple[Tensor, Tensor]:
        ...

    def generate(
        self,
        messages: list[ChatMessage],
        max_new_tokens: int = 512,
        temperature: float = 0.0,
        top_p: float = 1.0,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> GenerateOutput:
        ...

    def score_targets(
        self,
        messages: list[ChatMessage],
        targets: list[str],
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> list[float]:
        ...
