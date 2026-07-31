from __future__ import annotations

from rcmf.model.backends.base import ChatMessage, ModelBackend
from rcmf.schemas import MemoryRecord


class FullContextPolicy:
    def __init__(
        self,
        backend: ModelBackend,
        memories: list[MemoryRecord],
        max_chars: int = 24000,
    ) -> None:
        self.backend = backend
        self.memories = memories
        self.max_chars = max_chars

    def _memory_context(self) -> str:
        chunks: list[str] = []
        total = 0
        for record in self.memories:
            text = record.experience_text
            if total + len(text) > self.max_chars:
                break
            chunks.append(text)
            total += len(text)
        return "\n\n".join(chunks)

    def generate(self, messages: list[ChatMessage], **kwargs):
        context = self._memory_context()
        augmented = [dict(msg) for msg in messages]
        if context:
            augmented.insert(
                1,
                {
                    "role": "system",
                    "content": f"[RAW EPISODIC MEMORY]\n{context}",
                },
            )
        return self.backend.generate(messages=augmented, injector=None, memory_z=None, **kwargs)

