from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProgramBenchmarkAdapter(Protocol):
    """Minimal benchmark boundary needed by compiled transition programs."""

    def render_state(self, env_state: Any, history: list[dict[str, str]]) -> str: ...

    def extract_transition_memories(self, trajectory: Any) -> Sequence[Any]: ...

    def render_raw_memory_teacher(
        self, memory: Any, prompt_profile: str
    ) -> str: ...

    def reference_target(self, example: Any) -> str: ...

    def evaluate_generated_action(
        self,
        response_text: str,
        code: str,
        target_action: str,
        observation: str,
        target_observation: str,
    ) -> Mapping[str, Any]: ...
