from __future__ import annotations

from rcmf.model.backends.base import ChatMessage, ModelBackend


class NoMemoryPolicy:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    def generate(self, messages: list[ChatMessage], **kwargs):
        return self.backend.generate(messages=messages, injector=None, memory_z=None, **kwargs)

