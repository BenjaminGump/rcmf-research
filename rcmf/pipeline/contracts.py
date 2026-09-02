from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ArmContract:
    arm_id: str
    task_conditioned_prompt_profile: str
    artifact_prefix: str
    run_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "arm_id": self.arm_id,
            "task_conditioned_prompt_profile": self.task_conditioned_prompt_profile,
            "artifact_prefix": self.artifact_prefix,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    arm: str
    dependencies: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    validator: str = "completion_manifest"
    scientific: bool = False
    uses_gpu: bool = False
    conditional_on: str | None = None
    expected_outputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "arm": self.arm,
            "dependencies": list(self.dependencies),
            "command": list(self.command),
            "validator": self.validator,
            "scientific": self.scientific,
            "uses_gpu": self.uses_gpu,
            "conditional_on": self.conditional_on,
            "expected_outputs": list(self.expected_outputs),
        }


@dataclass(frozen=True)
class PipelineContract:
    schema_version: str
    run_uuid: str
    source_commit: str
    global_seed: int
    hard_cap_hours: float
    stages: tuple[StageSpec, ...]
    arms: Mapping[str, ArmContract]
    shared_initialization: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def stage_map(self) -> dict[str, StageSpec]:
        return {stage.stage_id: stage for stage in self.stages}

    def validate(self) -> None:
        if self.global_seed <= 0:
            raise ValueError("global_seed must be positive")
        if self.hard_cap_hours <= 0:
            raise ValueError("hard_cap_hours must be positive")
        stage_map = self.stage_map()
        if len(stage_map) != len(self.stages):
            raise ValueError("stage IDs must be unique")
        for stage in self.stages:
            unknown = set(stage.dependencies) - set(stage_map)
            if unknown:
                raise ValueError(f"{stage.stage_id} has unknown dependencies: {sorted(unknown)}")
            if stage.conditional_on and stage.conditional_on not in stage_map:
                raise ValueError(
                    f"{stage.stage_id} has unknown condition stage: {stage.conditional_on}"
                )
        self._validate_acyclic(stage_map)

    @staticmethod
    def _validate_acyclic(stage_map: Mapping[str, StageSpec]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(stage_id: str) -> None:
            if stage_id in visiting:
                raise ValueError(f"stage graph contains a cycle at {stage_id}")
            if stage_id in visited:
                return
            visiting.add(stage_id)
            for dependency in stage_map[stage_id].dependencies:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in stage_map:
            visit(stage_id)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "run_uuid": self.run_uuid,
            "source_commit": self.source_commit,
            "global_seed": self.global_seed,
            "hard_cap_hours": self.hard_cap_hours,
            "arms": {name: arm.as_dict() for name, arm in sorted(self.arms.items())},
            "shared_initialization": dict(self.shared_initialization),
            "metadata": dict(self.metadata),
            "stages": [stage.as_dict() for stage in self.stages],
        }


def deep_diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    """Return deterministic leaf-level differences between two resolved objects."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        rows: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in left:
                rows.append({"path": child, "left": "<missing>", "right": right[key]})
            elif key not in right:
                rows.append({"path": child, "left": left[key], "right": "<missing>"})
            else:
                rows.extend(deep_diff(left[key], right[key], child))
        return rows
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)):
        if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
            return [{"path": path, "left": left, "right": right}]
        rows = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left):
                rows.append({"path": child, "left": "<missing>", "right": right[index]})
            elif index >= len(right):
                rows.append({"path": child, "left": left[index], "right": "<missing>"})
            else:
                rows.extend(deep_diff(left[index], right[index], child))
        return rows
    return [] if left == right else [{"path": path, "left": left, "right": right}]

