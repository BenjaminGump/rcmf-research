from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


class TemplateBenchmarkAdapter:
    """Non-runnable checklist implementation for a future benchmark port."""

    def _missing(self, name: str) -> None:
        raise NotImplementedError(f"A benchmark port must implement {name}")

    def benchmark_identity(self) -> Mapping[str, Any]:
        self._missing("benchmark_identity")

    def list_splits(self) -> Mapping[str, Sequence[str]]:
        self._missing("list_splits")

    def load_successful_training_trajectories(self) -> Iterable[Mapping[str, Any]]:
        self._missing("load_successful_training_trajectories")

    def canonicalize_trajectory(self, trajectory: Mapping[str, Any]) -> Mapping[str, Any]:
        self._missing("canonicalize_trajectory")

    def extract_transition_records(
        self, trajectory: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]:
        self._missing("extract_transition_records")

    def lineage_keys(self, row: Mapping[str, Any]) -> Sequence[str]:
        self._missing("lineage_keys")

    def render_state(self, example: Mapping[str, Any], prompt_profile: str) -> Any:
        self._missing("render_state")

    def render_transition(self, record: Mapping[str, Any]) -> str:
        self._missing("render_transition")

    def build_selector_supervision(self, examples: Sequence[Mapping[str, Any]], transitions: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
        self._missing("build_selector_supervision")

    def build_causal_teacher_conditions(self, example: Mapping[str, Any], transition: Mapping[str, Any], prompt_profile: str) -> Sequence[Mapping[str, Any]]:
        self._missing("build_causal_teacher_conditions")

    def execute_action(self, runtime: Any, action: str) -> Any:
        self._missing("execute_action")

    def evaluate_task(self, runtime: Any) -> Mapping[str, Any]:
        self._missing("evaluate_task")

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        self._missing("redact_audit_record")

