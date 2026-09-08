from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    EvaluationResult,
    ProvenanceClass,
    TaskRecord,
    TrajectoryRecord,
    TransitionRecord,
)


ADAPTER_PROTOCOL_VERSION = "rcmf_reproducible_benchmark_adapter_v2"


class AdapterCapability(str, Enum):
    STABLE_SPLITS = "stable_splits"
    OFFICIAL_TRAJECTORIES = "official_trajectories"
    RESET_AND_REPLAY = "reset_and_replay"
    STATE_RENDERING = "state_rendering"
    TRANSITION_RENDERING = "transition_rendering"
    RUNTIME_TOKEN_COUNTING = "runtime_token_counting"
    CAUSAL_SUPERVISION = "causal_supervision"
    INTERACTIVE_RUNTIME = "interactive_runtime"
    OFFICIAL_EVALUATION = "official_evaluation"
    AUDIT_REDACTION = "audit_redaction"


PORTABLE_PIPELINE_REQUIRED_CAPABILITIES = frozenset(AdapterCapability)


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
            if not str(getattr(self, name)):
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
        if not self.source_id or not self.training_splits:
            raise ValueError("trajectory source identity and training splits are required")
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
    missing = frozenset(required) - declared
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
