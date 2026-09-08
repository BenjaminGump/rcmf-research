from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pytest

from rcmf.pipeline.portable_v2.adapter import (
    ADAPTER_PROTOCOL_VERSION,
    AdapterCapability,
    BenchmarkIdentity,
    PromptProfile,
    TrajectorySource,
    validate_adapter_capabilities,
)
from rcmf.pipeline.portable_v2.artifacts import (
    ArtifactOwner,
    ArtifactResolver,
)
from rcmf.pipeline.portable_v2.conformance import run_manifest_only_conformance
from rcmf.pipeline.portable_v2.dag import (
    PortablePhase,
    PortableRunMode,
    PortableRunPolicy,
    build_portable_v2_stage_graph,
)
from rcmf.pipeline.portable_v2.registry import AdapterRegistry
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
    ensure_no_split_leakage,
)
from rcmf.utils.serialization import sha256_file


class PortableFixtureAdapter:
    def __init__(
        self,
        *,
        name: str,
        train_count: int,
        evaluation_count: int,
        train_split: str,
        evaluation_split: str,
        prompt_profile: str,
        action_semantics: str,
        continuous_reward: bool,
    ) -> None:
        self.name = name
        self.train_count = train_count
        self.evaluation_count = evaluation_count
        self.train_split = train_split
        self.evaluation_split = evaluation_split
        self.profile = prompt_profile
        self.action_semantics = action_semantics
        self.continuous_reward = continuous_reward

    def identity(self) -> BenchmarkIdentity:
        return BenchmarkIdentity(
            benchmark_name=self.name,
            benchmark_version=f"{self.name}-data-v1",
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            environment_version="fixture-env-v1",
            data_version="fixture-data-v1",
            deterministic=True,
            action_semantics=self.action_semantics,
            reward_semantics=("continuous" if self.continuous_reward else "binary"),
        )

    def capabilities(self) -> frozenset[AdapterCapability]:
        return frozenset(AdapterCapability)

    def list_tasks(self) -> Mapping[str, Sequence[TaskRecord]]:
        def rows(split: str, count: int) -> tuple[TaskRecord, ...]:
            return tuple(
                TaskRecord(
                    benchmark=self.name,
                    dataset_version="fixture-data-v1",
                    split=split,
                    task_id=f"{split}-{index}",
                    instruction=f"goal {split} {index}",
                    lineage_keys=(f"{self.name}:{split}:{index}",),
                    source_identity={"fixture": True},
                )
                for index in range(count)
            )

        return {
            self.train_split: rows(self.train_split, self.train_count),
            self.evaluation_split: rows(self.evaluation_split, self.evaluation_count),
        }

    def trajectory_sources(self) -> Sequence[TrajectorySource]:
        provenance = (
            ProvenanceClass.OFFICIAL_HUMAN
            if self.continuous_reward
            else ProvenanceClass.OFFICIAL_EXPERT
        )
        return (
            TrajectorySource("fixture-source", provenance, {"fixture": True}, (self.train_split,)),
        )

    def successful_trajectories(self, split: str) -> Iterable[TrajectoryRecord]:
        provenance = self.trajectory_sources()[0].provenance
        for task in self.list_tasks()[split]:
            action = "search[item]" if self.continuous_reward else "go to table"
            yield TrajectoryRecord(
                trajectory_id=f"trajectory:{task.task_id}",
                task_id=task.task_id,
                provenance=provenance,
                source_identity={"fixture": True},
                steps=(
                    TrajectoryStep(
                        step_index=0,
                        pre_action_state="room or storefront",
                        action=action,
                        post_action_observation="done",
                        raw_reward=1.0,
                        terminal_status=TerminalStatus.SUCCESS,
                    ),
                ),
                raw_reward=1.0,
                success=True,
                terminal_status=TerminalStatus.SUCCESS,
                replay_status=ReplayStatus.VALIDATED,
                environment_identity={"fixture": True},
                metadata={"goal": task.instruction},
            )

    def transition_records(
        self, task: TaskRecord, trajectory: TrajectoryRecord
    ) -> Iterable[TransitionRecord]:
        for step in trajectory.steps:
            yield TransitionRecord(
                transition_id=f"transition:{task.task_id}:{step.step_index}",
                parent_trajectory_id=trajectory.trajectory_id,
                task_id=task.task_id,
                step_index=step.step_index,
                goal=task.instruction,
                pre_action_state=step.pre_action_state,
                action=step.action,
                post_action_observation=step.post_action_observation,
                lineage_keys=task.lineage_keys,
                provenance=trajectory.provenance,
                replay_identity={"trajectory": trajectory.trajectory_id},
            )

    def decision_states(
        self, task: TaskRecord, trajectory: TrajectoryRecord, prompt_profile: str
    ) -> Iterable[DecisionStateRecord]:
        step = trajectory.steps[0]
        yield DecisionStateRecord(
            state_id=f"state:{task.task_id}",
            task_id=task.task_id,
            trajectory_prefix=(),
            current_observation=step.pre_action_state,
            target_action_reference={"action": step.action},
            model_split=task.split,
            provenance=trajectory.provenance,
            prompt_profile=prompt_profile,
            environment_replay_reference={"trajectory": trajectory.trajectory_id, "step": 0},
            metadata={"instruction": task.instruction},
        )

    def prompt_profiles(self) -> Mapping[str, PromptProfile]:
        return {
            self.profile: PromptProfile(
                self.profile,
                "fixture-prompt-v1",
                "a" * 64,
                2 if "two" in self.profile else 1,
                self.action_semantics,
                4096,
            )
        }

    def render_messages(
        self, state: DecisionStateRecord, prompt_profile: str
    ) -> Sequence[Mapping[str, str]]:
        if prompt_profile != self.profile or state.prompt_profile != prompt_profile:
            raise ValueError("prompt profile mismatch")
        return (
            {"role": "system", "content": f"profile={prompt_profile}"},
            {"role": "user", "content": state.current_observation},
        )

    def count_runtime_tokens(
        self, messages: Sequence[Mapping[str, str]], prompt_profile: str
    ) -> int:
        assert prompt_profile == self.profile
        return sum(len(row["content"].split()) for row in messages)

    def build_selector_supervision(
        self,
        states: Sequence[DecisionStateRecord],
        transitions: Sequence[TransitionRecord],
    ) -> Sequence[Mapping[str, Any]]:
        return tuple(
            {"state_id": state.state_id, "transition_id": transition.transition_id, "legal": True}
            for state, transition in zip(states, transitions)
        )

    def causal_conditions(
        self,
        state: DecisionStateRecord,
        transition: TransitionRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        return (
            {"condition": "bare", "state_id": state.state_id},
            {"condition": "memory", "transition_id": transition.transition_id},
        )

    def compare_causal_outcomes(
        self, bare: EvaluationResult, conditioned: EvaluationResult
    ) -> Mapping[str, Any]:
        return {"reward_delta": conditioned.raw_reward - bare.raw_reward}

    def create_runtime(self, task: TaskRecord) -> Any:
        return {"task_id": task.task_id, "actions": []}

    def replay_to_state(self, runtime: Any, state: DecisionStateRecord) -> Mapping[str, Any]:
        return {"replayed": len(state.trajectory_prefix)}

    def validate_action(self, action: str, runtime: Any) -> Mapping[str, Any]:
        return {"valid": bool(action), "runtime": runtime["task_id"]}

    def execute_action(self, runtime: Any, action: str) -> Mapping[str, Any]:
        runtime["actions"].append(action)
        return {"observation": "done"}

    def evaluate_task(self, runtime: Any, task: TaskRecord) -> EvaluationResult:
        return EvaluationResult(
            task_id=task.task_id,
            raw_reward=0.75 if self.continuous_reward else 1.0,
            binary_success=None if self.continuous_reward else True,
            terminal_status=TerminalStatus.SUCCESS,
            steps=len(runtime["actions"]),
            exceptions=(),
            benchmark_metrics={},
            audit_references=(),
        )

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(record)


def test_schema_round_trips_text_and_search_click_actions() -> None:
    for continuous, action in ((False, "go to shelf"), (True, "search[red shoes]")):
        step = TrajectoryStep(0, "state", action, "observation", 0.75, TerminalStatus.SUCCESS)
        trajectory = TrajectoryRecord(
            "trajectory",
            "task",
            ProvenanceClass.OFFICIAL_HUMAN if continuous else ProvenanceClass.OFFICIAL_EXPERT,
            {"source": "fixture"},
            (step,),
            0.75,
            True,
            TerminalStatus.SUCCESS,
            ReplayStatus.VALIDATED,
            {"runtime": "fixture"},
        )
        assert TrajectoryRecord.from_dict(trajectory.as_dict()) == trajectory
        result = EvaluationResult(
            "task",
            0.75,
            None if continuous else True,
            TerminalStatus.SUCCESS,
            1,
            (),
            {"partial_credit": continuous},
            (),
        )
        assert EvaluationResult.from_dict(result.as_dict()) == result


def test_unknown_provenance_and_split_leakage_fail_closed() -> None:
    with pytest.raises(ValueError, match="UNKNOWN_PROHIBITED"):
        TrajectoryRecord(
            "trajectory",
            "task",
            ProvenanceClass.UNKNOWN_PROHIBITED,
            {},
            (TrajectoryStep(0, "s", "a", "o", 0.0, TerminalStatus.FAILURE),),
            0.0,
            False,
            TerminalStatus.FAILURE,
            ReplayStatus.FAILED,
            {},
        ).validate()
    common = dict(
        benchmark="fixture",
        dataset_version="v1",
        task_id="task",
        instruction="goal",
        lineage_keys=("shared-lineage",),
        source_identity={},
    )
    with pytest.raises(ValueError, match="both"):
        ensure_no_split_leakage(
            (TaskRecord(split="train", **common), TaskRecord(split="test", **common))
        )


def test_capability_failure_and_registry_have_no_fallback() -> None:
    adapter = PortableFixtureAdapter(
        name="alf-like",
        train_count=2,
        evaluation_count=1,
        train_split="train",
        evaluation_split="valid_unseen",
        prompt_profile="two_demo",
        action_semantics="text_command",
        continuous_reward=False,
    )
    original = adapter.capabilities
    adapter.capabilities = lambda: frozenset({AdapterCapability.STABLE_SPLITS})  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="capability preflight failed"):
        validate_adapter_capabilities(adapter)
    adapter.capabilities = original  # type: ignore[method-assign]
    registry = AdapterRegistry()
    registry.register("alf-like:v1", lambda: adapter)
    assert registry.resolve("alf-like:v1") is adapter
    with pytest.raises(KeyError, match="fallback is prohibited"):
        registry.resolve("unknown:v1")


def test_same_generic_dag_runs_two_relocated_variable_fixtures(tmp_path: Path) -> None:
    alf = PortableFixtureAdapter(
        name="alf-like",
        train_count=2,
        evaluation_count=1,
        train_split="train",
        evaluation_split="valid_unseen",
        prompt_profile="react_task_type_two_demo_v1",
        action_semantics="text_command",
        continuous_reward=False,
    )
    shop = PortableFixtureAdapter(
        name="shop-like",
        train_count=5,
        evaluation_count=3,
        train_split="human_train",
        evaluation_split="official_eval",
        prompt_profile="react_official_one_demo_v1",
        action_semantics="search_click",
        continuous_reward=True,
    )
    full_policy = PortableRunPolicy(PortableRunMode.FULL, alf.profile, 2, 25101)
    continuation_policy = PortableRunPolicy(
        PortableRunMode.SEALED_UPSTREAM_CONTINUATION,
        shop.profile,
        3,
        25101,
        continuation_from=PortablePhase.MEMORY_LEDGER,
    )
    first = run_manifest_only_conformance(
        adapter=alf, policy=full_policy, run_root=tmp_path / "one" / "run"
    )
    second = run_manifest_only_conformance(
        adapter=shop, policy=continuation_policy, run_root=tmp_path / "relocated" / "two"
    )
    assert first["stage_count"] == 12
    assert second["stage_count"] == 10
    assert first["counts"]["tasks"] == 3
    assert second["counts"]["tasks"] == 8
    assert first["counts"]["transitions"] == 2
    assert second["counts"]["transitions"] == 5
    assert not first["scientific_execution"] and not second["scientific_execution"]


def test_replay_failure_cannot_enter_successful_conformance_corpus(tmp_path: Path) -> None:
    adapter = PortableFixtureAdapter(
        name="broken",
        train_count=1,
        evaluation_count=1,
        train_split="train",
        evaluation_split="eval",
        prompt_profile="p",
        action_semantics="opaque",
        continuous_reward=False,
    )
    original = adapter.successful_trajectories

    def failed(split: str) -> Iterable[TrajectoryRecord]:
        for row in original(split):
            yield replace(row, replay_status=ReplayStatus.FAILED)

    adapter.successful_trajectories = failed  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="replay validated"):
        run_manifest_only_conformance(
            adapter=adapter,
            policy=PortableRunPolicy(PortableRunMode.FULL, "p", 1, 25101),
            run_root=tmp_path / "broken",
        )


def test_artifact_resolution_is_owned_hash_bound_and_relocatable(tmp_path: Path) -> None:
    source = tmp_path / "moved" / "data.json"
    source.parent.mkdir()
    source.write_text("{}", encoding="utf-8")
    manifest = {
        "state_cache": {
            "logical_name": "state_cache",
            "path": str(source),
            "sha256": sha256_file(source),
            "owner": "SEALED_UPSTREAM",
            "producer_manifest_sha256": "d" * 64,
        }
    }
    resolver = ArtifactResolver(manifest)
    assert resolver.resolve("state_cache", expected_owner=ArtifactOwner.SEALED_UPSTREAM) == source
    with pytest.raises(ValueError, match="owned by"):
        resolver.resolve("state_cache", expected_owner=ArtifactOwner.CURRENT_RUN)
    with pytest.raises(KeyError, match="not declared"):
        resolver.resolve("missing", expected_owner=ArtifactOwner.SEALED_UPSTREAM)


def test_portable_stage_graph_uses_terminal_validation_and_no_selection() -> None:
    graph = build_portable_v2_stage_graph(
        PortableRunPolicy(PortableRunMode.FULL, "profile", 2, 25101)
    )
    ids = [stage.stage_id for stage in graph]
    assert "P09_terminal_checkpoint_validation" in ids
    assert all("checkpoint_selection" not in stage_id for stage_id in ids)
    assert all(stage.command[:3] == ("{python}", "-m", "rcmf.pipeline.portable_v2.run_phase") for stage in graph)
    assert all(stage.validator == "portable_v2_1_manifest" for stage in graph)
