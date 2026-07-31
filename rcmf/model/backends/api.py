from __future__ import annotations

from typing import Any

from rcmf.model.backends.base import ChatMessage, GenerateOutput


class APIBackend:
    """Teacher-only wrapper around the legacy API caller."""

    def __init__(
        self,
        model_name: str = "deepseek/deepseek-chat",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        messages: list[ChatMessage],
        max_new_tokens: int = 512,
        temperature: float | None = None,
        top_p: float = 1.0,
        **kwargs: Any,
    ) -> GenerateOutput:
        try:
            from model import MODEL
        except ImportError as exc:
            raise RuntimeError("Legacy model.py is required for APIBackend") from exc
        text, usage = MODEL.forward(
            messages=messages,
            model_name=self.model_name,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens or max_new_tokens,
            enable_thinking=False,
        )
        return GenerateOutput(
            text=str(text),
            token_ids=[],
            usage=usage,
            extra={"backend": "api_teacher", "model_name": self.model_name},
        )

    def tokenize_messages(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("APIBackend is for offline generation, not training")

    def forward_train(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("APIBackend is for offline teacher labels only")

    def score_targets(self, *args: Any, **kwargs: Any) -> list[float]:
        raise NotImplementedError("Use fixed offline teacher labels for experiments")

