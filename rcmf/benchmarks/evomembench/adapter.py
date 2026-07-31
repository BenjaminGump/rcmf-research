from __future__ import annotations

from typing import Any, Iterable

from rcmf.benchmarks.base import BenchmarkAdapter
from rcmf.schemas import BenchmarkResult, DecisionExample, MemoryRecord


class EvoMemBenchAdapter(BenchmarkAdapter):
    """Adapter surface for the official EvoMemBench package."""

    def load_splits(self, config: Any) -> dict[str, list[str]]:
        raise NotImplementedError("Install and wire the official EvoMemBench evaluator first")

    def build_memory_records(self, split: str) -> Iterable[MemoryRecord]:
        raise NotImplementedError

    def build_decision_examples(self, split: str) -> Iterable[DecisionExample]:
        raise NotImplementedError

    def render_state(self, env_state: Any, history: list[dict[str, str]]) -> str:
        return str(env_state)

    def render_experience(self, trajectory: dict[str, Any]) -> str:
        return str(trajectory)

    def run_episode(self, policy: Any, task_id: str, config: Any) -> BenchmarkResult:
        raise NotImplementedError

    def evaluate_episode(self, task_id: str, trace: dict[str, Any]) -> BenchmarkResult:
        raise NotImplementedError

