from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    EvaluationResult,
    ProvenanceClass,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TransitionRecord,
    validate_record_closure,
)


ADAPTER_PROTOCOL_VERSION = "rcmf_reproducible_benchmark_adapter_v2"


class AdapterCapability(str, Enum):
    STABLE_SPLITS = "stable_splits"
    SUCCESSFUL_TRAJECTORY_SOURCE = "successful_trajectory_source"
    # Retained only so historical serialized capability declarations remain
    # readable. New phase contracts require SUCCESSFUL_TRAJECTORY_SOURCE.
    OFFICIAL_TRAJECTORIES = "official_trajectories"
    RESET_AND_REPLAY = "reset_and_replay"
    STATE_RENDERING = "state_rendering"
    TRANSITION_RENDERING = "transition_rendering"
    RUNTIME_TOKEN_COUNTING = "runtime_token_counting"
    CAUSAL_SUPERVISION = "causal_supervision"
    INTERACTIVE_RUNTIME = "interactive_runtime"
    OFFICIAL_EVALUATION = "official_evaluation"
    AUDIT_REDACTION = "audit_redaction"


PHASE_REQUIRED_CAPABILITIES: Mapping[str, frozenset[AdapterCapability]] = {
    "P00C_sealed_upstream_boundary_validation": frozenset(),
    "P00_environment_and_data_provenance": frozenset({AdapterCapability.STABLE_SPLITS}),
    "P01_successful_trajectory_corpus": frozenset(
        {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
        }
    ),
    "P02_memory_transition_ledger": frozenset(
        {
            AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
            AdapterCapability.TRANSITION_RENDERING,
        }
    ),
    "P03_state_and_transition_representations": frozenset(
        {AdapterCapability.STATE_RENDERING, AdapterCapability.TRANSITION_RENDERING}
    ),
    "P04_addressing_selector_supervision": frozenset({AdapterCapability.CAUSAL_SUPERVISION}),
    "P05_paired_causal_outcomes": frozenset(
        {
            AdapterCapability.RESET_AND_REPLAY,
            AdapterCapability.RUNTIME_TOKEN_COUNTING,
            AdapterCapability.CAUSAL_SUPERVISION,
            AdapterCapability.INTERACTIVE_RUNTIME,
        }
    ),
    "P06_policy_teacher_and_training_units": frozenset(
        {AdapterCapability.STATE_RENDERING, AdapterCapability.CAUSAL_SUPERVISION}
    ),
    "P07_writer_reader_training": frozenset(),
    "P08_per_epoch_diagnostics": frozenset(
        {AdapterCapability.INTERACTIVE_RUNTIME, AdapterCapability.OFFICIAL_EVALUATION}
    ),
    "P09_terminal_checkpoint_validation": frozenset(),
    "P10_deployment_field": frozenset(),
    "P11_official_evaluation_and_reporting": frozenset(
        {
            AdapterCapability.STATE_RENDERING,
            AdapterCapability.RUNTIME_TOKEN_COUNTING,
            AdapterCapability.INTERACTIVE_RUNTIME,
            AdapterCapability.OFFICIAL_EVALUATION,
            AdapterCapability.AUDIT_REDACTION,
        }
    ),
}

PORTABLE_PIPELINE_REQUIRED_CAPABILITIES = frozenset(
    item for requirements in PHASE_REQUIRED_CAPABILITIES.values() for item in requirements
)

CAPABILITY_PREREQUISITES: Mapping[
    AdapterCapability, frozenset[AdapterCapability]
] = {
    AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE: frozenset(
        {AdapterCapability.STABLE_SPLITS}
    ),
    AdapterCapability.TRANSITION_RENDERING: frozenset(
        {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
        }
    ),
    AdapterCapability.STATE_RENDERING: frozenset(
        {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
        }
    ),
    AdapterCapability.RUNTIME_TOKEN_COUNTING: frozenset(
        {AdapterCapability.STATE_RENDERING}
    ),
    AdapterCapability.CAUSAL_SUPERVISION: frozenset(
        {
            AdapterCapability.STATE_RENDERING,
            AdapterCapability.TRANSITION_RENDERING,
        }
    ),
    AdapterCapability.INTERACTIVE_RUNTIME: frozenset(
        {AdapterCapability.TRANSITION_RENDERING}
    ),
    AdapterCapability.RESET_AND_REPLAY: frozenset(
        {AdapterCapability.STATE_RENDERING, AdapterCapability.INTERACTIVE_RUNTIME}
    ),
    AdapterCapability.OFFICIAL_EVALUATION: frozenset(
        {AdapterCapability.STABLE_SPLITS, AdapterCapability.INTERACTIVE_RUNTIME}
    ),
}


class CapabilityProofError(RuntimeError):
    """A declared adapter capability could not be exercised."""


@dataclass(frozen=True)
class BenchmarkIdentity:
    benchmark_name: str
    benchmark_version: str
    adapter_version: str
    environment_version: str
    data_version: str
    deterministic: bool
    action_semantics: str
    reward_semantics: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for name in (
            "benchmark_name",
            "benchmark_version",
            "adapter_version",
            "environment_version",
            "data_version",
            "action_semantics",
            "reward_semantics",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.adapter_version != ADAPTER_PROTOCOL_VERSION:
            raise ValueError(
                f"adapter version {self.adapter_version!r} is not {ADAPTER_PROTOCOL_VERSION!r}"
            )


@dataclass(frozen=True)
class PromptProfile:
    name: str
    version: str
    asset_manifest_sha256: str
    demonstration_count: int
    action_grammar: str
    context_limit: int

    def validate(self) -> None:
        if not self.name or not self.version or len(self.asset_manifest_sha256) != 64:
            raise ValueError("prompt profile identity is incomplete")
        if self.demonstration_count < 0 or self.context_limit <= 0:
            raise ValueError("prompt profile counts must be non-negative/positive")


@dataclass(frozen=True)
class TrajectorySource:
    source_id: str
    provenance: ProvenanceClass
    source_identity: Mapping[str, Any]
    training_splits: tuple[str, ...]

    def validate(self) -> None:
        if (
            not isinstance(self.source_id, str)
            or not self.source_id.strip()
            or not self.training_splits
        ):
            raise ValueError("trajectory source identity and training splits are required")
        if not isinstance(self.source_identity, Mapping) or not self.source_identity:
            raise ValueError("trajectory source content identity is required")
        if any(
            not isinstance(split, str) or not split.strip()
            for split in self.training_splits
        ):
            raise ValueError("trajectory source training splits must be non-empty strings")
        if len(set(self.training_splits)) != len(self.training_splits):
            raise ValueError("trajectory source training splits must be unique")
        if self.provenance == ProvenanceClass.UNKNOWN_PROHIBITED:
            raise ValueError("unknown trajectory provenance is prohibited")


@runtime_checkable
class ReproducibleBenchmarkAdapterV2(Protocol):
    """The only authoritative adapter boundary for new RCMF dataset ports."""

    def identity(self) -> BenchmarkIdentity: ...

    def capabilities(self) -> frozenset[AdapterCapability]: ...

    def list_tasks(self) -> Mapping[str, Sequence[TaskRecord]]: ...

    def trajectory_sources(self) -> Sequence[TrajectorySource]: ...

    def successful_trajectories(self, split: str) -> Iterable[TrajectoryRecord]: ...

    def transition_records(
        self, task: TaskRecord, trajectory: TrajectoryRecord
    ) -> Iterable[TransitionRecord]: ...

    def decision_states(
        self, task: TaskRecord, trajectory: TrajectoryRecord, prompt_profile: str
    ) -> Iterable[DecisionStateRecord]: ...

    def prompt_profiles(self) -> Mapping[str, PromptProfile]: ...

    def render_messages(
        self, state: DecisionStateRecord, prompt_profile: str
    ) -> Sequence[Mapping[str, str]]: ...

    def count_runtime_tokens(
        self, messages: Sequence[Mapping[str, str]], prompt_profile: str
    ) -> int: ...

    def build_selector_supervision(
        self,
        states: Sequence[DecisionStateRecord],
        transitions: Sequence[TransitionRecord],
    ) -> Sequence[Mapping[str, Any]]: ...

    def causal_conditions(
        self,
        state: DecisionStateRecord,
        transition: TransitionRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def compare_causal_outcomes(
        self, bare: EvaluationResult, conditioned: EvaluationResult
    ) -> Mapping[str, Any]: ...

    def create_runtime(self, task: TaskRecord) -> Any: ...

    def replay_to_state(self, runtime: Any, state: DecisionStateRecord) -> Mapping[str, Any]: ...

    def validate_action(self, action: str, runtime: Any) -> Mapping[str, Any]: ...

    def execute_action(self, runtime: Any, action: str) -> Mapping[str, Any]: ...

    def evaluate_task(self, runtime: Any, task: TaskRecord) -> EvaluationResult: ...

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]: ...


def validate_adapter_capabilities(
    adapter: ReproducibleBenchmarkAdapterV2,
    required: Iterable[AdapterCapability] = PORTABLE_PIPELINE_REQUIRED_CAPABILITIES,
) -> dict[str, Any]:
    if not isinstance(adapter, ReproducibleBenchmarkAdapterV2):
        raise TypeError("adapter does not implement ReproducibleBenchmarkAdapterV2")
    identity = adapter.identity()
    identity.validate()
    declared = frozenset(adapter.capabilities())
    unknown = [item for item in declared if not isinstance(item, AdapterCapability)]
    if unknown:
        raise TypeError(f"adapter declared unknown capabilities: {unknown}")
    required_set = _expand_capabilities(required)
    missing = required_set - declared
    if missing:
        raise RuntimeError(
            "adapter capability preflight failed: "
            + ", ".join(sorted(item.value for item in missing))
        )
    profiles = adapter.prompt_profiles()
    if not profiles:
        raise RuntimeError("adapter provides no prompt profiles")
    for name, profile in profiles.items():
        profile.validate()
        if name != profile.name:
            raise ValueError("prompt profile registry key differs from profile identity")
    return {
        "adapter_protocol_version": ADAPTER_PROTOCOL_VERSION,
        "benchmark": identity.benchmark_name,
        "capabilities": sorted(item.value for item in declared),
        "prompt_profiles": sorted(profiles),
        "passed": True,
    }


def required_capabilities_for_phases(phases: Iterable[str]) -> frozenset[AdapterCapability]:
    required: set[AdapterCapability] = set()
    for phase in phases:
        try:
            required.update(PHASE_REQUIRED_CAPABILITIES[str(phase)])
        except KeyError as exc:
            raise CapabilityProofError(f"unknown portable phase: {phase}") from exc
    return _expand_capabilities(required)


def _expand_capabilities(
    capabilities: Iterable[AdapterCapability],
) -> frozenset[AdapterCapability]:
    expanded = set(capabilities)
    while True:
        before = len(expanded)
        for capability in tuple(expanded):
            expanded.update(CAPABILITY_PREREQUISITES.get(capability, ()))
        if len(expanded) == before:
            return frozenset(expanded)


def probe_adapter_capabilities(
    adapter: ReproducibleBenchmarkAdapterV2,
    *,
    prompt_profile: str,
    required: Iterable[AdapterCapability] = PORTABLE_PIPELINE_REQUIRED_CAPABILITIES,
) -> dict[str, Any]:
    """Exercise each requested capability on one bounded adapter-owned fixture."""
    required_set = _expand_capabilities(required)
    declaration = validate_adapter_capabilities(adapter, required_set)
    tasks_by_split: Mapping[str, Sequence[TaskRecord]] = {}
    sources: tuple[TrajectorySource, ...] = ()
    trajectories: tuple[TrajectoryRecord, ...] = ()
    transitions: tuple[TransitionRecord, ...] = ()
    states: tuple[DecisionStateRecord, ...] = ()
    task: TaskRecord | None = None
    trajectory: TrajectoryRecord | None = None
    split: str | None = None
    profile_capabilities = {
        AdapterCapability.STATE_RENDERING,
        AdapterCapability.RUNTIME_TOKEN_COUNTING,
        AdapterCapability.CAUSAL_SUPERVISION,
    }
    if AdapterCapability.STABLE_SPLITS in required_set:
        tasks_by_split = adapter.list_tasks()
        if not tasks_by_split:
            raise CapabilityProofError("list_tasks returned no splits")
    profiles = adapter.prompt_profiles()
    if required_set & profile_capabilities and prompt_profile not in profiles:
        raise CapabilityProofError(f"prompt profile is unavailable: {prompt_profile}")
    if AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE in required_set:
        sources = tuple(adapter.trajectory_sources())
        if not sources:
            raise CapabilityProofError("adapter declares no successful trajectory sources")
        source_ids: set[str] = set()
        for source in sources:
            source.validate()
            if source.source_id in source_ids:
                raise CapabilityProofError(
                    f"duplicate trajectory source ID: {source.source_id}"
                )
            source_ids.add(source.source_id)
            unknown_splits = set(source.training_splits) - set(tasks_by_split)
            if unknown_splits:
                raise CapabilityProofError(
                    "trajectory source references unknown training splits: "
                    + ", ".join(sorted(unknown_splits))
                )
        training_splits = sorted(
            {value for source in sources for value in source.training_splits}
        )
        if not training_splits:
            raise CapabilityProofError("trajectory sources declare no training splits")
        split = training_splits[0]
        trajectories = tuple(adapter.successful_trajectories(split))
        if not trajectories:
            raise CapabilityProofError(
                f"successful trajectory probe is empty for split {split}"
            )
        task_index = {
            row.task_id: row for rows in tasks_by_split.values() for row in rows
        }
        trajectory = trajectories[0]
        try:
            task = task_index[trajectory.task_id]
        except KeyError as exc:
            raise CapabilityProofError("probe trajectory references an unknown task") from exc
    if AdapterCapability.TRANSITION_RENDERING in required_set:
        assert task is not None and trajectory is not None
        transitions = tuple(adapter.transition_records(task, trajectory))
        if not transitions:
            raise CapabilityProofError("transition conversion probe returned no rows")
    if AdapterCapability.STATE_RENDERING in required_set:
        assert task is not None and trajectory is not None
        states = tuple(adapter.decision_states(task, trajectory, prompt_profile))
        if not states:
            raise CapabilityProofError("state conversion probe returned no rows")
    if transitions and states:
        assert split is not None
        validate_record_closure(
            tasks_by_split=tasks_by_split,
            trajectory_sources=sources,
            trajectories_by_split={split: trajectories},
            transitions=transitions,
            decision_states=states,
            prompt_profile=prompt_profile,
        )
    messages: Sequence[Mapping[str, str]] = ()
    tokens: int | None = None
    if AdapterCapability.STATE_RENDERING in required_set:
        assert states
        messages = adapter.render_messages(states[0], prompt_profile)
        if not messages:
            raise CapabilityProofError("prompt rendering returned no messages")
    if AdapterCapability.RUNTIME_TOKEN_COUNTING in required_set:
        if not messages:
            messages = adapter.render_messages(states[0], prompt_profile)
        tokens = adapter.count_runtime_tokens(messages, prompt_profile)
        if not isinstance(tokens, int) or tokens <= 0:
            raise CapabilityProofError("runtime token counter returned an invalid count")
    if AdapterCapability.CAUSAL_SUPERVISION in required_set:
        assert states and transitions
        supervision = adapter.build_selector_supervision(states, transitions)
        if not supervision:
            raise CapabilityProofError("selector supervision probe returned no rows")
        conditions = adapter.causal_conditions(states[0], transitions[0], prompt_profile)
        if len(conditions) < 2:
            raise CapabilityProofError("causal condition probe did not return a pair")
    bare = EvaluationResult(
        task_id=task.task_id if task is not None else "capability-probe",
        raw_reward=0.0,
        binary_success=False,
        terminal_status=TerminalStatus.FAILURE,
        steps=0,
        exceptions=(),
        benchmark_metrics={},
        audit_references=(),
    )
    conditioned = EvaluationResult(
        task_id=task.task_id if task is not None else "capability-probe",
        raw_reward=1.0,
        binary_success=True,
        terminal_status=TerminalStatus.SUCCESS,
        steps=1,
        exceptions=(),
        benchmark_metrics={},
        audit_references=(),
    )
    if AdapterCapability.CAUSAL_SUPERVISION in required_set:
        comparison = adapter.compare_causal_outcomes(bare, conditioned)
        if not isinstance(comparison, Mapping):
            raise CapabilityProofError("causal outcome comparison is not a mapping")

    runtime = None
    runtime_required = bool(
        required_set
        & {
            AdapterCapability.RESET_AND_REPLAY,
            AdapterCapability.INTERACTIVE_RUNTIME,
            AdapterCapability.OFFICIAL_EVALUATION,
        }
    )
    if runtime_required:
        if task is None or not states or not transitions:
            raise CapabilityProofError("runtime probe prerequisites were not produced")
        try:
            runtime = adapter.create_runtime(task)
            replay = adapter.replay_to_state(runtime, states[0])
            action = transitions[0].action
            validation = adapter.validate_action(action, runtime)
            execution = adapter.execute_action(runtime, action)
            evaluation = adapter.evaluate_task(runtime, task)
            evaluation.validate()
            for name, value in (
                ("replay_to_state", replay),
                ("action validation", validation),
                ("action execution", execution),
            ):
                if not isinstance(value, Mapping):
                    raise CapabilityProofError(f"{name} probe is not a mapping")
        finally:
            if runtime is not None:
                closer = getattr(runtime, "close", None)
                if callable(closer):
                    closer()
    if AdapterCapability.AUDIT_REDACTION in required_set:
        redacted = adapter.redact_audit_record(
            {"task_id": task.task_id if task is not None else "capability-probe", "secret": "fixture"}
        )
        if not isinstance(redacted, Mapping):
            raise CapabilityProofError("audit redaction probe is not a mapping")
    return {
        **declaration,
        "required_capabilities": sorted(item.value for item in required_set),
        "probed_task_id": task.task_id if task is not None else None,
        "probed_trajectory_id": trajectory.trajectory_id if trajectory is not None else None,
        "token_count": tokens,
        "transition_count": len(transitions),
        "decision_state_count": len(states),
        "runtime_exercised": runtime_required,
        "model_loaded": False,
        "training_executed": False,
    }
