from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from rcmf.schemas import BenchmarkResult, DecisionExample, MemoryRecord


class BenchmarkAdapter(ABC):
    @abstractmethod
    def load_splits(self, config: Any) -> dict[str, list[str]]:
        raise NotImplementedError

    @abstractmethod
    def build_memory_records(self, split: str) -> Iterable[MemoryRecord]:
        raise NotImplementedError

    @abstractmethod
    def build_decision_examples(self, split: str) -> Iterable[DecisionExample]:
        raise NotImplementedError

    @abstractmethod
    def render_state(self, env_state: Any, history: list[dict[str, str]]) -> str:
        raise NotImplementedError

    @abstractmethod
    def render_experience(self, trajectory: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def run_episode(self, policy: Any, task_id: str, config: Any) -> BenchmarkResult:
        raise NotImplementedError

    @abstractmethod
    def evaluate_episode(self, task_id: str, trace: dict[str, Any]) -> BenchmarkResult:
        raise NotImplementedError

