from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
from typing import Any, Mapping, Sequence, TypeVar

from rcmf.utils.serialization import to_jsonable


SCHEMA_VERSION = "rcmf_portable_records_v2"


class PortableSchemaError(ValueError):
    """A portable record or cross-record identity contract is invalid."""


class ProvenanceClass(str, Enum):
    OFFICIAL_EXPERT = "OFFICIAL_EXPERT"
    OFFICIAL_HUMAN = "OFFICIAL_HUMAN"
    OFFICIAL_HUMAN_SAMPLE = "OFFICIAL_HUMAN_SAMPLE"
    OFFICIAL_MODEL_OR_IL = "OFFICIAL_MODEL_OR_IL"
    ORACLE_GENERATED_FROM_TRAIN_METADATA = "ORACLE_GENERATED_FROM_TRAIN_METADATA"
    AGENT_GENERATED = "AGENT_GENERATED"
    UNKNOWN_PROHIBITED = "UNKNOWN_PROHIBITED"


class ReplayStatus(str, Enum):
    VALIDATED = "VALIDATED"
    NOT_REPLAYED = "NOT_REPLAYED"
    FAILED = "FAILED"


class TerminalStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TERMINATED = "TERMINATED"
    TRUNCATED = "TRUNCATED"
    NOT_TERMINAL = "NOT_TERMINAL"
    ERROR = "ERROR"


def _text(value: Any, name: str) -> str:
    if value is None or not isinstance(value, str):
        raise PortableSchemaError(f"{name} must be a string")
    if not value.strip():
        raise PortableSchemaError(f"{name} must be non-empty and non-whitespace")
    return value


def _schema_version(value: Any) -> str:
    result = _text(value, "schema_version")
    if result != SCHEMA_VERSION:
        raise PortableSchemaError(
            f"schema_version must equal the supported version {SCHEMA_VERSION!r}"
        )
    return result


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise PortableSchemaError(f"{name} must be a boolean")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _reward(value: Any, name: str = "raw_reward") -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


class PortableRecord:
    schema_version: str

    def as_dict(self) -> dict[str, Any]:
        return to_jsonable(asdict(self))

    def _validate_schema_version(self) -> None:
        _schema_version(self.schema_version)


@dataclass(frozen=True)
class TaskRecord(PortableRecord):
    benchmark: str
    dataset_version: str
    split: str
    task_id: str
    instruction: str
    lineage_keys: tuple[str, ...]
    source_identity: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        for name in ("benchmark", "dataset_version", "split", "task_id", "instruction"):
            _text(getattr(self, name), name)
        if not self.lineage_keys or any(
            not isinstance(value, str) or not value.strip() for value in self.lineage_keys
        ):
            raise ValueError("lineage_keys must contain stable non-empty values")
        _mapping(self.source_identity, "source_identity")
        _mapping(self.metadata, "metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskRecord":
        row = cls(
            benchmark=_text(value.get("benchmark"), "benchmark"),
            dataset_version=_text(value.get("dataset_version"), "dataset_version"),
            split=_text(value.get("split"), "split"),
            task_id=_text(value.get("task_id"), "task_id"),
            instruction=_text(value.get("instruction"), "instruction"),
            lineage_keys=tuple(str(item) for item in value.get("lineage_keys", ())),
            source_identity=_mapping(value.get("source_identity", {}), "source_identity"),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


@dataclass(frozen=True)
class TrajectoryStep(PortableRecord):
    step_index: int
    pre_action_state: str
    action: str
    post_action_observation: str
    raw_reward: float
    terminal_status: TerminalStatus
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        _text(self.pre_action_state, "pre_action_state")
        _text(self.action, "action")
        _text(self.post_action_observation, "post_action_observation")
        _reward(self.raw_reward)
        _mapping(self.metadata, "metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrajectoryStep":
        row = cls(
            step_index=int(value.get("step_index", -1)),
            pre_action_state=_text(value.get("pre_action_state"), "pre_action_state"),
            action=_text(value.get("action"), "action"),
            post_action_observation=_text(
                value.get("post_action_observation"), "post_action_observation"
            ),
            raw_reward=_reward(value.get("raw_reward", 0.0)),
            terminal_status=TerminalStatus(value.get("terminal_status", "NOT_TERMINAL")),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


@dataclass(frozen=True)
class TrajectoryRecord(PortableRecord):
    trajectory_id: str
    task_id: str
    provenance: ProvenanceClass
    source_identity: Mapping[str, Any]
    steps: tuple[TrajectoryStep, ...]
    raw_reward: float
    success: bool
    terminal_status: TerminalStatus
    replay_status: ReplayStatus
    environment_identity: Mapping[str, Any]
    agent_identity: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        _text(self.trajectory_id, "trajectory_id")
        _text(self.task_id, "task_id")
        if self.provenance == ProvenanceClass.UNKNOWN_PROHIBITED:
            raise ValueError("UNKNOWN_PROHIBITED trajectories cannot enter the corpus")
        if not self.steps:
            raise ValueError("trajectory must contain at least one complete step")
        for expected, step in enumerate(self.steps):
            step.validate()
            if step.step_index != expected:
                raise ValueError("trajectory steps must be contiguous and ordered from zero")
            if expected < len(self.steps) - 1 and step.terminal_status != TerminalStatus.NOT_TERMINAL:
                raise PortableSchemaError("only the final trajectory step may be terminal")
        if self.terminal_status == TerminalStatus.NOT_TERMINAL:
            raise PortableSchemaError("a complete trajectory cannot be NOT_TERMINAL")
        if self.steps[-1].terminal_status != self.terminal_status:
            raise PortableSchemaError("final step terminal status differs from trajectory status")
        if self.success != (self.terminal_status == TerminalStatus.SUCCESS):
            raise PortableSchemaError("trajectory success and terminal status are inconsistent")
        if self.success and self.replay_status != ReplayStatus.VALIDATED:
            raise PortableSchemaError("a successful trajectory must be replay validated")
        if self.replay_status == ReplayStatus.FAILED and self.success:
            raise PortableSchemaError("a replay-failed trajectory cannot be successful")
        _reward(self.raw_reward)
        _mapping(self.source_identity, "source_identity")
        _mapping(self.environment_identity, "environment_identity")
        if self.agent_identity is not None:
            _mapping(self.agent_identity, "agent_identity")
        _mapping(self.metadata, "metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrajectoryRecord":
        row = cls(
            trajectory_id=_text(value.get("trajectory_id"), "trajectory_id"),
            task_id=_text(value.get("task_id"), "task_id"),
            provenance=ProvenanceClass(value.get("provenance", "UNKNOWN_PROHIBITED")),
            source_identity=_mapping(value.get("source_identity", {}), "source_identity"),
            steps=tuple(TrajectoryStep.from_dict(item) for item in value.get("steps", ())),
            raw_reward=_reward(value.get("raw_reward", 0.0)),
            success=_bool(value.get("success"), "success"),
            terminal_status=TerminalStatus(value.get("terminal_status", "NOT_TERMINAL")),
            replay_status=ReplayStatus(value.get("replay_status", "NOT_REPLAYED")),
            environment_identity=_mapping(
                value.get("environment_identity", {}), "environment_identity"
            ),
            agent_identity=(
                _mapping(value["agent_identity"], "agent_identity")
                if value.get("agent_identity") is not None
                else None
            ),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


@dataclass(frozen=True)
class TransitionRecord(PortableRecord):
    transition_id: str
    parent_trajectory_id: str
    task_id: str
    step_index: int
    goal: str
    pre_action_state: str
    action: str
    post_action_observation: str
    lineage_keys: tuple[str, ...]
    provenance: ProvenanceClass
    replay_identity: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        for name in (
            "transition_id",
            "parent_trajectory_id",
            "task_id",
            "goal",
            "pre_action_state",
            "action",
            "post_action_observation",
        ):
            _text(getattr(self, name), name)
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        if not self.lineage_keys or any(
            not isinstance(value, str) or not value.strip() for value in self.lineage_keys
        ):
            raise ValueError("transition lineage_keys must be non-empty")
        if self.provenance == ProvenanceClass.UNKNOWN_PROHIBITED:
            raise ValueError("unknown transition provenance is prohibited")
        _mapping(self.replay_identity, "replay_identity")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TransitionRecord":
        row = cls(
            transition_id=_text(value.get("transition_id"), "transition_id"),
            parent_trajectory_id=_text(
                value.get("parent_trajectory_id"), "parent_trajectory_id"
            ),
            task_id=_text(value.get("task_id"), "task_id"),
            step_index=int(value.get("step_index", -1)),
            goal=_text(value.get("goal"), "goal"),
            pre_action_state=_text(value.get("pre_action_state"), "pre_action_state"),
            action=_text(value.get("action"), "action"),
            post_action_observation=_text(
                value.get("post_action_observation"), "post_action_observation"
            ),
            lineage_keys=tuple(str(item) for item in value.get("lineage_keys", ())),
            provenance=ProvenanceClass(value.get("provenance", "UNKNOWN_PROHIBITED")),
            replay_identity=_mapping(value.get("replay_identity", {}), "replay_identity"),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


@dataclass(frozen=True)
class DecisionStateRecord(PortableRecord):
    state_id: str
    task_id: str
    trajectory_prefix: tuple[Mapping[str, Any], ...]
    current_observation: str
    target_action_reference: Mapping[str, Any]
    model_split: str
    provenance: ProvenanceClass
    prompt_profile: str
    environment_replay_reference: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        for name in ("state_id", "task_id", "current_observation", "model_split", "prompt_profile"):
            _text(getattr(self, name), name)
        if self.provenance == ProvenanceClass.UNKNOWN_PROHIBITED:
            raise ValueError("unknown decision-state provenance is prohibited")
        _mapping(self.target_action_reference, "target_action_reference")
        _mapping(self.environment_replay_reference, "environment_replay_reference")
        for step in self.trajectory_prefix:
            _mapping(step, "trajectory_prefix step")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecisionStateRecord":
        row = cls(
            state_id=_text(value.get("state_id"), "state_id"),
            task_id=_text(value.get("task_id"), "task_id"),
            trajectory_prefix=tuple(
                _mapping(item, "trajectory_prefix step")
                for item in value.get("trajectory_prefix", ())
            ),
            current_observation=_text(
                value.get("current_observation"), "current_observation"
            ),
            target_action_reference=_mapping(
                value.get("target_action_reference", {}), "target_action_reference"
            ),
            model_split=_text(value.get("model_split"), "model_split"),
            provenance=ProvenanceClass(value.get("provenance", "UNKNOWN_PROHIBITED")),
            prompt_profile=_text(value.get("prompt_profile"), "prompt_profile"),
            environment_replay_reference=_mapping(
                value.get("environment_replay_reference", {}),
                "environment_replay_reference",
            ),
            metadata=_mapping(value.get("metadata", {}), "metadata"),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


@dataclass(frozen=True)
class EvaluationResult(PortableRecord):
    task_id: str
    raw_reward: float
    binary_success: bool | None
    terminal_status: TerminalStatus
    steps: int
    exceptions: tuple[Mapping[str, Any], ...]
    benchmark_metrics: Mapping[str, Any]
    audit_references: tuple[Mapping[str, Any], ...]
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        self._validate_schema_version()
        _text(self.task_id, "task_id")
        _reward(self.raw_reward)
        if self.steps < 0:
            raise ValueError("steps must be non-negative")
        if self.terminal_status == TerminalStatus.NOT_TERMINAL:
            raise PortableSchemaError("an evaluation result must be terminal")
        if self.binary_success is True and self.terminal_status != TerminalStatus.SUCCESS:
            raise PortableSchemaError("binary success requires SUCCESS terminal status")
        if self.binary_success is False and self.terminal_status == TerminalStatus.SUCCESS:
            raise PortableSchemaError("binary failure cannot use SUCCESS terminal status")
        _mapping(self.benchmark_metrics, "benchmark_metrics")
        for item in (*self.exceptions, *self.audit_references):
            _mapping(item, "evaluation record")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationResult":
        row = cls(
            task_id=_text(value.get("task_id"), "task_id"),
            raw_reward=_reward(value.get("raw_reward", 0.0)),
            binary_success=(
                _bool(value["binary_success"], "binary_success")
                if value.get("binary_success") is not None
                else None
            ),
            terminal_status=TerminalStatus(value.get("terminal_status", "NOT_TERMINAL")),
            steps=int(value.get("steps", 0)),
            exceptions=tuple(
                _mapping(item, "exception") for item in value.get("exceptions", ())
            ),
            benchmark_metrics=_mapping(
                value.get("benchmark_metrics", {}), "benchmark_metrics"
            ),
            audit_references=tuple(
                _mapping(item, "audit_reference")
                for item in value.get("audit_references", ())
            ),
            schema_version=_schema_version(value.get("schema_version")),
        )
        row.validate()
        return row


RecordT = TypeVar("RecordT", bound=PortableRecord)


def ensure_no_split_leakage(tasks: tuple[TaskRecord, ...]) -> None:
    owners: dict[str, str] = {}
    for task in tasks:
        task.validate()
        for key in task.lineage_keys:
            prior = owners.setdefault(key, task.split)
            if prior != task.split:
                raise ValueError(
                    f"lineage key {key!r} appears in both {prior!r} and {task.split!r}"
                )


def validate_record_closure(
    *,
    tasks_by_split: Mapping[str, Sequence[TaskRecord]],
    trajectory_sources: Sequence[Any],
    trajectories_by_split: Mapping[str, Sequence[TrajectoryRecord]],
    transitions: Sequence[TransitionRecord],
    decision_states: Sequence[DecisionStateRecord],
    prompt_profile: str,
) -> dict[str, int]:
    """Validate the complete portable identity graph without dataset assumptions."""
    expected_profile = _text(prompt_profile, "prompt_profile")
    tasks: list[TaskRecord] = []
    task_by_id: dict[str, TaskRecord] = {}
    for split_key, rows in tasks_by_split.items():
        split = _text(split_key, "split key")
        for task in rows:
            task.validate()
            if task.split != split:
                raise PortableSchemaError(
                    f"task {task.task_id!r} split {task.split!r} differs from mapping key {split!r}"
                )
            if task.task_id in task_by_id:
                raise PortableSchemaError(f"duplicate global task ID: {task.task_id}")
            task_by_id[task.task_id] = task
            tasks.append(task)
    ensure_no_split_leakage(tuple(tasks))

    sources = tuple(trajectory_sources)
    for source in sources:
        source.validate()
    trajectories: list[TrajectoryRecord] = []
    trajectory_by_id: dict[str, TrajectoryRecord] = {}
    for split_key, rows in trajectories_by_split.items():
        split = _text(split_key, "trajectory split key")
        for trajectory in rows:
            trajectory.validate()
            task = task_by_id.get(trajectory.task_id)
            if task is None:
                raise PortableSchemaError(
                    f"trajectory {trajectory.trajectory_id!r} references unknown task {trajectory.task_id!r}"
                )
            if task.split != split:
                raise PortableSchemaError(
                    f"trajectory {trajectory.trajectory_id!r} emitted for {split!r} but task belongs to {task.split!r}"
                )
            matches = [
                source
                for source in sources
                if source.provenance == trajectory.provenance
                and split in source.training_splits
                and dict(source.source_identity) == dict(trajectory.source_identity)
            ]
            if len(matches) != 1:
                raise PortableSchemaError(
                    f"trajectory {trajectory.trajectory_id!r} matches {len(matches)} declared sources"
                )
            if trajectory.trajectory_id in trajectory_by_id:
                raise PortableSchemaError(
                    f"duplicate trajectory ID: {trajectory.trajectory_id}"
                )
            trajectory_by_id[trajectory.trajectory_id] = trajectory
            trajectories.append(trajectory)

    transition_by_id: dict[str, TransitionRecord] = {}
    for transition in transitions:
        transition.validate()
        if transition.transition_id in transition_by_id:
            raise PortableSchemaError(f"duplicate transition ID: {transition.transition_id}")
        parent = trajectory_by_id.get(transition.parent_trajectory_id)
        if parent is None:
            raise PortableSchemaError(
                f"transition {transition.transition_id!r} references unknown trajectory"
            )
        if transition.task_id != parent.task_id:
            raise PortableSchemaError("transition task differs from parent trajectory task")
        if transition.provenance != parent.provenance:
            raise PortableSchemaError("transition provenance differs from parent trajectory")
        replay_trajectory = transition.replay_identity.get(
            "trajectory_id", transition.replay_identity.get("trajectory")
        )
        if str(replay_trajectory) != parent.trajectory_id:
            raise PortableSchemaError("transition replay identity differs from parent trajectory")
        replay_step = transition.replay_identity.get(
            "step_index", transition.replay_identity.get("step", transition.step_index)
        )
        if int(replay_step) != transition.step_index:
            raise PortableSchemaError("transition replay step differs from transition step")
        if transition.step_index >= len(parent.steps):
            raise PortableSchemaError("transition step is outside its parent trajectory")
        step = parent.steps[transition.step_index]
        if (
            transition.pre_action_state != step.pre_action_state
            or transition.action != step.action
            or transition.post_action_observation != step.post_action_observation
        ):
            raise PortableSchemaError("transition content differs from its parent step")
        transition_by_id[transition.transition_id] = transition

    state_by_id: dict[str, DecisionStateRecord] = {}
    for state in decision_states:
        state.validate()
        if state.state_id in state_by_id:
            raise PortableSchemaError(f"duplicate state ID: {state.state_id}")
        task = task_by_id.get(state.task_id)
        if task is None:
            raise PortableSchemaError(f"decision state {state.state_id!r} references unknown task")
        if state.model_split != task.split:
            raise PortableSchemaError("decision-state split differs from task split")
        if state.prompt_profile != expected_profile:
            raise PortableSchemaError("decision-state prompt profile differs from run policy")
        reference = state.environment_replay_reference
        trajectory_id = reference.get("trajectory_id", reference.get("trajectory"))
        step_index = reference.get("step_index", reference.get("step"))
        parent = trajectory_by_id.get(str(trajectory_id))
        if parent is None:
            raise PortableSchemaError("decision state references an unknown trajectory")
        if parent.task_id != state.task_id:
            raise PortableSchemaError("decision-state task differs from parent trajectory task")
        if step_index is None or int(step_index) < 0 or int(step_index) >= len(parent.steps):
            raise PortableSchemaError("decision state references an invalid trajectory step")
        step = parent.steps[int(step_index)]
        if state.current_observation != step.pre_action_state:
            raise PortableSchemaError("decision-state observation differs from parent step")
        if str(state.target_action_reference.get("action", "")) != step.action:
            raise PortableSchemaError("decision-state target action differs from parent step")
        state_by_id[state.state_id] = state
    return {
        "tasks": len(task_by_id),
        "trajectories": len(trajectory_by_id),
        "transitions": len(transition_by_id),
        "decision_states": len(state_by_id),
    }
