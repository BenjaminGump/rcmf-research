from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class ReproducibleBenchmarkAdapter(Protocol):
    def benchmark_identity(self) -> Mapping[str, Any]: ...

    def list_splits(self) -> Mapping[str, Sequence[str]]: ...

    def load_successful_training_trajectories(self) -> Iterable[Mapping[str, Any]]: ...

    def canonicalize_trajectory(self, trajectory: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def extract_transition_records(
        self, trajectory: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]: ...

    def lineage_keys(self, row: Mapping[str, Any]) -> Sequence[str]: ...

    def render_state(self, example: Mapping[str, Any], prompt_profile: str) -> Any: ...

    def render_transition(self, record: Mapping[str, Any]) -> str: ...

    def build_selector_supervision(
        self,
        examples: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]: ...

    def build_causal_teacher_conditions(
        self,
        example: Mapping[str, Any],
        transition: Mapping[str, Any],
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def execute_action(self, runtime: Any, action: str) -> Any: ...

    def evaluate_task(self, runtime: Any) -> Mapping[str, Any]: ...

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass
class MockBenchmarkAdapter:
    """Tiny deterministic adapter used to exercise the full generic DAG."""

    task_ids: tuple[str, ...] = ("train-a", "train-b", "validation-a")

    def benchmark_identity(self) -> Mapping[str, Any]:
        return {"name": "mock", "version": "1", "deterministic": True}

    def list_splits(self) -> Mapping[str, Sequence[str]]:
        return {"train": self.task_ids[:2], "validation": self.task_ids[2:]}

    def load_successful_training_trajectories(self) -> Iterable[Mapping[str, Any]]:
        for index, task_id in enumerate(self.task_ids[:2]):
            yield {
                "task_id": task_id,
                "episode_id": f"episode-{index}",
                "steps": [{"state": f"state-{index}", "action": f"action-{index}", "observation": "ok"}],
            }

    def canonicalize_trajectory(self, trajectory: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(trajectory)

    def extract_transition_records(
        self, trajectory: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]:
        for index, step in enumerate(trajectory.get("steps", [])):
            yield {"transition_id": f"{trajectory['task_id']}:{index}", **dict(step)}

    def lineage_keys(self, row: Mapping[str, Any]) -> Sequence[str]:
        return [str(row.get("task_id", "")), str(row.get("episode_id", ""))]

    def render_state(self, example: Mapping[str, Any], prompt_profile: str) -> Any:
        return {"profile": prompt_profile, "state": example.get("state", "")}

    def render_transition(self, record: Mapping[str, Any]) -> str:
        return "\n".join(str(record.get(key, "")) for key in ("state", "action", "observation"))

    def build_selector_supervision(
        self,
        examples: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        return [
            {
                "example_id": str(example.get("state", index)),
                "transition_id": str(transition.get("transition_id", index)),
                "label": float(index % 2),
            }
            for index, (example, transition) in enumerate(zip(examples, transitions))
        ]

    def build_causal_teacher_conditions(
        self,
        example: Mapping[str, Any],
        transition: Mapping[str, Any],
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        return [
            {"condition": "zero", "profile": prompt_profile, "example": dict(example)},
            {
                "condition": "conditioned",
                "profile": prompt_profile,
                "example": dict(example),
                "transition": dict(transition),
            },
        ]

    def execute_action(self, runtime: Any, action: str) -> Any:
        return runtime(action) if callable(runtime) else {"action": action}

    def evaluate_task(self, runtime: Any) -> Mapping[str, Any]:
        return {"success": bool(runtime)}

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(record)

