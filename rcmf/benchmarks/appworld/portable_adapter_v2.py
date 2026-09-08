from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from rcmf.benchmarks.appworld.pipeline_adapter import AppWorldReproduciblePipelineAdapter
from rcmf.benchmarks.appworld.prompt import build_appworld_messages
from rcmf.pipeline.portable_v2.adapter import (
    ADAPTER_PROTOCOL_VERSION,
    AdapterCapability,
    BenchmarkIdentity,
    PromptProfile,
    TrajectorySource,
)
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    EvaluationResult,
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
    TransitionRecord,
)


TokenCounter = Callable[[Sequence[Mapping[str, str]], str], int]
RuntimeFactory = Callable[[TaskRecord], Any]

APPWORLD_FULL_DEMO_ASSET_SHA256 = "dd74c379c97031a062ba79b2b82d3992ec3b38870792f53d86821544f994c4c3"
APPWORLD_ONE_DEMO_ASSET_SHA256 = "a0a8d3b2e10f167dba5dcab5ad62fa8f6737629b813d2d0e27af4872bef9e27b"


class AppWorldPortableAdapterV2:
    """Portable-v2 facade over the sealed AppWorld rendering/evaluation helpers."""

    def __init__(
        self,
        legacy: AppWorldReproduciblePipelineAdapter,
        *,
        dataset_version: str,
        environment_version: str,
        prompt_manifest_hashes: Mapping[str, str],
        task_records: Mapping[str, Sequence[TaskRecord]] | None = None,
        trajectory_records: Mapping[str, Sequence[TrajectoryRecord]] | None = None,
        token_counter: TokenCounter | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.legacy = legacy
        self.dataset_version = str(dataset_version)
        self.environment_version = str(environment_version)
        self.prompt_manifest_hashes = dict(prompt_manifest_hashes)
        self._task_records = (
            {name: tuple(rows) for name, rows in task_records.items()}
            if task_records is not None
            else None
        )
        self._trajectory_records = {
            name: tuple(rows) for name, rows in (trajectory_records or {}).items()
        }
        self._token_counter = token_counter
        self._runtime_factory = runtime_factory

    def identity(self) -> BenchmarkIdentity:
        legacy = self.legacy.benchmark_identity()
        return BenchmarkIdentity(
            benchmark_name="appworld",
            benchmark_version=self.dataset_version,
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            environment_version=self.environment_version,
            data_version=self.dataset_version,
            deterministic=True,
            action_semantics="python_code",
            reward_semantics="official_binary_success",
            metadata={"legacy_identity": dict(legacy)},
        )

    def capabilities(self) -> frozenset[AdapterCapability]:
        capabilities = {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.OFFICIAL_TRAJECTORIES,
            AdapterCapability.STATE_RENDERING,
            AdapterCapability.TRANSITION_RENDERING,
            AdapterCapability.CAUSAL_SUPERVISION,
            AdapterCapability.AUDIT_REDACTION,
        }
        if self._token_counter is not None:
            capabilities.add(AdapterCapability.RUNTIME_TOKEN_COUNTING)
        if self._runtime_factory is not None:
            capabilities.update(
                {
                    AdapterCapability.RESET_AND_REPLAY,
                    AdapterCapability.INTERACTIVE_RUNTIME,
                    AdapterCapability.OFFICIAL_EVALUATION,
                }
            )
        return frozenset(capabilities)

    def list_tasks(self) -> Mapping[str, Sequence[TaskRecord]]:
        if self._task_records is not None:
            return self._task_records
        return {
            split: tuple(
                TaskRecord(
                    benchmark="appworld",
                    dataset_version=self.dataset_version,
                    split=split,
                    task_id=str(task_id),
                    instruction=f"AppWorld task {task_id}",
                    lineage_keys=(f"appworld-task:{task_id}",),
                    source_identity={"provider": "appworld.load_task_ids"},
                )
                for task_id in task_ids
            )
            for split, task_ids in self.legacy.list_splits().items()
        }

    def trajectory_sources(self) -> Sequence[TrajectorySource]:
        splits = tuple(sorted(self._trajectory_records)) or ("train",)
        injected = next(
            (row for rows in self._trajectory_records.values() for row in rows), None
        )
        return (
            TrajectorySource(
                source_id="appworld_replay_validated_corpus",
                provenance=(
                    injected.provenance
                    if injected is not None
                    else ProvenanceClass.OFFICIAL_MODEL_OR_IL
                ),
                source_identity=(
                    dict(injected.source_identity)
                    if injected is not None
                    else {"corpus_root": str(self.legacy.corpus_root)}
                ),
                training_splits=splits,
            ),
        )

    def successful_trajectories(self, split: str) -> Iterable[TrajectoryRecord]:
        if self._trajectory_records:
            yield from self._trajectory_records.get(split, ())
            return
        for raw in self.legacy.load_successful_training_trajectories():
            raw_split = str(raw.get("split") or raw.get("metadata", {}).get("split", "train"))
            if raw_split != split:
                continue
            steps = tuple(
                TrajectoryStep(
                    step_index=index,
                    pre_action_state=str(step.get("state") or step.get("pre_action_state")),
                    action=str(step.get("action") or step.get("code")),
                    post_action_observation=str(
                        step.get("observation") or step.get("post_action_observation")
                    ),
                    raw_reward=float(step.get("reward", 0.0)),
                    terminal_status=(
                        TerminalStatus.SUCCESS
                        if index == len(raw.get("steps", ())) - 1
                        else TerminalStatus.NOT_TERMINAL
                    ),
                )
                for index, step in enumerate(raw.get("steps", ()))
            )
            trajectory = TrajectoryRecord(
                trajectory_id=str(raw.get("memory_id") or raw.get("trajectory_id")),
                task_id=str(raw.get("task_id")),
                provenance=ProvenanceClass.OFFICIAL_MODEL_OR_IL,
                source_identity={"corpus_root": str(self.legacy.corpus_root)},
                steps=steps,
                raw_reward=float(raw.get("reward", 1.0)),
                success=bool(raw.get("success", True)),
                terminal_status=TerminalStatus.SUCCESS,
                replay_status=ReplayStatus.VALIDATED,
                environment_identity={"benchmark": "appworld"},
                metadata={"legacy_row": dict(raw)},
            )
            trajectory.validate()
            yield trajectory

    def transition_records(
        self, task: TaskRecord, trajectory: TrajectoryRecord
    ) -> Iterable[TransitionRecord]:
        del task
        for step in trajectory.steps:
            row = TransitionRecord(
                transition_id=f"{trajectory.trajectory_id}:transition:{step.step_index}",
                parent_trajectory_id=trajectory.trajectory_id,
                task_id=trajectory.task_id,
                step_index=step.step_index,
                goal=str(trajectory.metadata.get("goal", trajectory.task_id)),
                pre_action_state=step.pre_action_state,
                action=step.action,
                post_action_observation=step.post_action_observation,
                lineage_keys=(f"appworld-task:{trajectory.task_id}",),
                provenance=trajectory.provenance,
                replay_identity={
                    "trajectory_id": trajectory.trajectory_id,
                    "step_index": step.step_index,
                },
            )
            row.validate()
            yield row

    def decision_states(
        self, task: TaskRecord, trajectory: TrajectoryRecord, prompt_profile: str
    ) -> Iterable[DecisionStateRecord]:
        prefix: list[dict[str, Any]] = []
        for step in trajectory.steps:
            row = DecisionStateRecord(
                state_id=f"{trajectory.trajectory_id}:state:{step.step_index}",
                task_id=task.task_id,
                trajectory_prefix=tuple(prefix),
                current_observation=step.pre_action_state,
                target_action_reference={"action": step.action},
                model_split=task.split,
                provenance=trajectory.provenance,
                prompt_profile=prompt_profile,
                environment_replay_reference={
                    "trajectory_id": trajectory.trajectory_id,
                    "step_index": step.step_index,
                },
                metadata={"task_message": task.instruction},
            )
            row.validate()
            yield row
            prefix.append(
                {
                    "response": f"```python\n{step.action}\n```",
                    "observation": step.post_action_observation,
                }
            )

    def prompt_profiles(self) -> Mapping[str, PromptProfile]:
        result = {}
        for name, demonstrations in (("full_demo", 3), ("full_demo_first_only", 1)):
            digest = self.prompt_manifest_hashes.get(name, "")
            result[name] = PromptProfile(
                name=name,
                version="appworld_prompt_profile_v2",
                asset_manifest_sha256=digest,
                demonstration_count=demonstrations,
                action_grammar="fenced_python_code",
                context_limit=40960,
            )
        return result

    def render_messages(
        self, state: DecisionStateRecord, prompt_profile: str
    ) -> Sequence[Mapping[str, str]]:
        if prompt_profile != state.prompt_profile:
            raise ValueError("state prompt profile differs from requested profile")
        if prompt_profile not in self.prompt_profiles():
            raise KeyError(f"unknown AppWorld prompt profile: {prompt_profile}")
        return build_appworld_messages(
            str(state.metadata.get("task_message", state.current_observation)),
            list(state.trajectory_prefix),
            prompt_profile=prompt_profile,
        )

    def count_runtime_tokens(
        self, messages: Sequence[Mapping[str, str]], prompt_profile: str
    ) -> int:
        if self._token_counter is None:
            raise RuntimeError("AppWorld runtime-equivalent token counter is not configured")
        return int(self._token_counter(messages, prompt_profile))

    def build_selector_supervision(
        self,
        states: Sequence[DecisionStateRecord],
        transitions: Sequence[TransitionRecord],
    ) -> Sequence[Mapping[str, Any]]:
        examples = [
            {
                "state_example_id": state.state_id,
                "state_text": state.current_observation,
                "target_text": str(state.target_action_reference.get("action", "")),
            }
            for state in states
        ]
        transition_rows = [self._legacy_transition(row) for row in transitions]
        return self.legacy.build_selector_supervision(examples, transition_rows)

    def causal_conditions(
        self,
        state: DecisionStateRecord,
        transition: TransitionRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        messages = self.render_messages(state, prompt_profile)
        from rcmf.training.transition_memory_6a import messages_with_transition_memory

        return (
            {"condition": "bare", "messages": messages},
            {
                "condition": "raw_transition",
                "messages": messages_with_transition_memory(
                    messages, self._legacy_transition(transition), prompt_profile
                ),
            },
        )

    @staticmethod
    def _legacy_transition(row: TransitionRecord) -> dict[str, Any]:
        return {
            "transition_id": row.transition_id,
            "parent_memory_id": row.parent_trajectory_id,
            "parent_task_id": row.task_id,
            "source_task_goal": row.goal,
            "canonical_pre_action_state": row.pre_action_state,
            "complete_action": row.action,
            "complete_post_action_observation": row.post_action_observation,
        }

    def compare_causal_outcomes(
        self, bare: EvaluationResult, conditioned: EvaluationResult
    ) -> Mapping[str, Any]:
        bare.validate()
        conditioned.validate()
        if bare.task_id != conditioned.task_id:
            raise ValueError("causal pair task identities differ")
        delta = conditioned.raw_reward - bare.raw_reward
        return {
            "label": "POSITIVE" if delta > 0 else "HARMFUL" if delta < 0 else "NEUTRAL",
            "raw_reward_delta": delta,
            "missing": False,
        }

    def create_runtime(self, task: TaskRecord) -> Any:
        if self._runtime_factory is None:
            raise RuntimeError("AppWorld runtime factory is not configured")
        return self._runtime_factory(task)

    def replay_to_state(self, runtime: Any, state: DecisionStateRecord) -> Mapping[str, Any]:
        observations = []
        for step in state.trajectory_prefix:
            action = str(step.get("action") or step.get("code") or "")
            if not action:
                response = str(step.get("response", ""))
                action = response.removeprefix("```python\n").removesuffix("\n```")
            observations.append(self.execute_action(runtime, action))
        return {"observations": observations, "replayed_steps": len(observations)}

    def validate_action(self, action: str, runtime: Any) -> Mapping[str, Any]:
        del runtime
        return {"valid": bool(action.strip()), "action_semantics": "python_code"}

    def execute_action(self, runtime: Any, action: str) -> Mapping[str, Any]:
        return {"observation": self.legacy.execute_action(runtime, action), "action": action}

    def evaluate_task(self, runtime: Any, task: TaskRecord) -> EvaluationResult:
        raw = self.legacy.evaluate_task(runtime)
        result = EvaluationResult(
            task_id=task.task_id,
            raw_reward=1.0 if bool(raw.get("success")) else 0.0,
            binary_success=bool(raw.get("success")),
            terminal_status=(
                TerminalStatus.SUCCESS if bool(raw.get("success")) else TerminalStatus.FAILURE
            ),
            steps=int(raw.get("steps", 0)),
            exceptions=(),
            benchmark_metrics={"official_success": bool(raw.get("success"))},
            audit_references=(),
        )
        result.validate()
        return result

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.legacy.redact_audit_record(record)


@dataclass
class _CapabilityProbeEvaluation:
    success: bool = True


class _CapabilityProbeRuntime:
    def __init__(self, task: TaskRecord) -> None:
        self.task = task
        self.actions: list[str] = []

    def execute(self, action: str) -> str:
        self.actions.append(action)
        return "Output:\n```\nprobe-ok\n```"

    def evaluate(self) -> _CapabilityProbeEvaluation:
        return _CapabilityProbeEvaluation(success=True)

    def close(self) -> None:
        return None


def create_appworld_portable_adapter_v2_1() -> AppWorldPortableAdapterV2:
    """Create the bounded preflight fixture; formal data/runtime are injected later."""
    task = TaskRecord(
        benchmark="appworld",
        dataset_version="appworld-0.1.0",
        split="train",
        task_id="portable-v2.1-capability-probe",
        instruction="Now here is the task:\nTask: exercise the adapter boundary",
        lineage_keys=("appworld:portable-v2.1-capability-probe",),
        source_identity={"fixture": "portable-v2.1"},
    )
    trajectory = TrajectoryRecord(
        trajectory_id="portable-v2.1-capability-probe-trajectory",
        task_id=task.task_id,
        provenance=ProvenanceClass.OFFICIAL_MODEL_OR_IL,
        source_identity={"fixture": "portable-v2.1"},
        steps=(
            TrajectoryStep(
                step_index=0,
                pre_action_state="probe-state",
                action="print('probe')",
                post_action_observation="Output:\n```\nprobe-ok\n```",
                raw_reward=1.0,
                terminal_status=TerminalStatus.SUCCESS,
            ),
        ),
        raw_reward=1.0,
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity={"fixture": "portable-v2.1"},
        metadata={"goal": task.instruction},
    )
    legacy = AppWorldReproduciblePipelineAdapter(
        corpus_root=Path("."), legacy_root=Path("."), split_names=()
    )
    return AppWorldPortableAdapterV2(
        legacy,
        dataset_version="appworld-0.1.0",
        environment_version="appworld-0.1.0",
        prompt_manifest_hashes={
            "full_demo": APPWORLD_FULL_DEMO_ASSET_SHA256,
            "full_demo_first_only": APPWORLD_ONE_DEMO_ASSET_SHA256,
        },
        task_records={"train": (task,)},
        trajectory_records={"train": (trajectory,)},
        token_counter=lambda messages, profile: len(
            json.dumps({"messages": list(messages), "profile": profile}, sort_keys=True)
        ),
        runtime_factory=_CapabilityProbeRuntime,
    )
