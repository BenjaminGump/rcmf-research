from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from rcmf.benchmarks.appworld.portable_adapter_v2 import (
    AppWorldPortableAdapterV2,
    create_appworld_portable_adapter_v2_1,
)
from rcmf.pipeline.portable_v2.adapter import (
    AdapterCapability,
    CapabilityProofError,
    probe_adapter_capabilities,
    required_capabilities_for_phases,
)
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import (
    PortablePhase,
    PortableRunMode,
    PortableRunPolicy,
    build_portable_v2_stage_graph,
    execution_phases_for_policy,
    phases_for_policy,
)
from rcmf.pipeline.portable_v2.executor import (
    EXECUTOR_PROTOCOL_VERSION,
    PortableExecutionError,
    PortableExecutionIdentity,
    PortableExecutorBinding,
    PortablePhaseContext,
    PortablePhaseWork,
    execute_and_validate_phase,
    load_executor_factory,
    validate_phase_manifest,
)
from rcmf.pipeline.portable_v2.schemas import (
    SCHEMA_VERSION,
    DecisionStateRecord,
    EvaluationResult,
    PortableSchemaError,
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
    TransitionRecord,
    validate_record_closure,
)
from rcmf.pipeline.portable_v2.adapter import TrajectorySource


ROOT = Path(__file__).resolve().parents[1]


def _records() -> tuple[dict[str, tuple[TaskRecord, ...]], tuple[TrajectorySource, ...], dict[str, tuple[TrajectoryRecord, ...]], tuple[TransitionRecord, ...], tuple[DecisionStateRecord, ...]]:
    task = TaskRecord("fixture", "v1", "train", "task-1", "goal", ("lineage-1",), {"source": "fixture"})
    step = TrajectoryStep(0, "state", "action", "done", 1.0, TerminalStatus.SUCCESS)
    trajectory = TrajectoryRecord(
        "trajectory-1", "task-1", ProvenanceClass.OFFICIAL_EXPERT, {"source": "fixture"},
        (step,), 1.0, True, TerminalStatus.SUCCESS, ReplayStatus.VALIDATED, {"env": "fixture"}
    )
    transition = TransitionRecord(
        "transition-1", "trajectory-1", "task-1", 0, "goal", "state", "action", "done",
        ("lineage-1",), ProvenanceClass.OFFICIAL_EXPERT, {"trajectory_id": "trajectory-1", "step_index": 0}
    )
    state = DecisionStateRecord(
        "state-1", "task-1", (), "state", {"action": "action"}, "train",
        ProvenanceClass.OFFICIAL_EXPERT, "profile", {"trajectory_id": "trajectory-1", "step_index": 0}
    )
    return (
        {"train": (task,)},
        (TrajectorySource("source-1", ProvenanceClass.OFFICIAL_EXPERT, {"source": "fixture"}, ("train",)),),
        {"train": (trajectory,)},
        (transition,),
        (state,),
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_text_rejects_none_empty_and_whitespace(value: object) -> None:
    payload = TaskRecord("b", "v", "train", "t", "goal", ("l",), {}).as_dict()
    payload["task_id"] = value
    with pytest.raises(PortableSchemaError):
        TaskRecord.from_dict(payload)


def test_exact_record_schema_version_is_required() -> None:
    payload = TaskRecord("b", "v", "train", "t", "goal", ("l",), {}).as_dict()
    for value in (None, "rcmf_portable_records_v1", ""):
        changed = dict(payload)
        changed["schema_version"] = value
        with pytest.raises(PortableSchemaError):
            TaskRecord.from_dict(changed)
    assert payload["schema_version"] == SCHEMA_VERSION


def test_complete_record_identity_closure_and_mutations() -> None:
    tasks, sources, trajectories, transitions, states = _records()
    assert validate_record_closure(
        tasks_by_split=tasks,
        trajectory_sources=sources,
        trajectories_by_split=trajectories,
        transitions=transitions,
        decision_states=states,
        prompt_profile="profile",
    ) == {"tasks": 1, "trajectories": 1, "transitions": 1, "decision_states": 1}
    mutations = (
        ({"other": tasks["train"]}, trajectories, transitions, states, "split"),
        (tasks, trajectories, (replace(transitions[0], task_id="other"),), states, "task differs"),
        (tasks, trajectories, transitions, (replace(states[0], task_id="other"),), "unknown task"),
        (tasks, trajectories, transitions, (replace(states[0], prompt_profile="other"),), "prompt profile"),
    )
    for task_rows, trajectory_rows, transition_rows, state_rows, match in mutations:
        with pytest.raises(PortableSchemaError, match=match):
            validate_record_closure(
                tasks_by_split=task_rows,
                trajectory_sources=sources,
                trajectories_by_split=trajectory_rows,
                transitions=transition_rows,
                decision_states=state_rows,
                prompt_profile="profile",
            )
    with pytest.raises(PortableSchemaError, match="duplicate global task"):
        validate_record_closure(
            tasks_by_split={"train": (tasks["train"][0], tasks["train"][0])},
            trajectory_sources=sources,
            trajectories_by_split=trajectories,
            transitions=transitions,
            decision_states=states,
            prompt_profile="profile",
        )


@pytest.mark.parametrize(
    ("part", "match"),
    (
        ("trajectory_split", "emitted for"),
        ("trajectory_source", "matches 0 declared sources"),
        ("duplicate_trajectory", "duplicate trajectory"),
        ("duplicate_transition", "duplicate transition"),
        ("duplicate_state", "duplicate state"),
        ("transition_replay_parent", "replay identity"),
        ("transition_replay_step", "replay step"),
        ("transition_content", "content differs"),
        ("state_split", "state split differs"),
    ),
)
def test_record_closure_rejects_every_cross_record_identity(part: str, match: str) -> None:
    tasks, sources, trajectories, transitions, states = _records()
    trajectory_rows = trajectories
    transition_rows = transitions
    state_rows = states
    if part == "trajectory_split":
        trajectory_rows = {"evaluation": trajectories["train"]}
    elif part == "trajectory_source":
        trajectory_rows = {
            "train": (replace(trajectories["train"][0], source_identity={"other": True}),)
        }
    elif part == "duplicate_trajectory":
        trajectory_rows = {"train": (trajectories["train"][0],) * 2}
    elif part == "duplicate_transition":
        transition_rows = transitions * 2
    elif part == "duplicate_state":
        state_rows = states * 2
    elif part == "transition_replay_parent":
        transition_rows = (
            replace(transitions[0], replay_identity={"trajectory_id": "other", "step_index": 0}),
        )
    elif part == "transition_replay_step":
        transition_rows = (
            replace(transitions[0], replay_identity={"trajectory_id": "trajectory-1", "step_index": 1}),
        )
    elif part == "transition_content":
        transition_rows = (replace(transitions[0], action="different"),)
    elif part == "state_split":
        state_rows = (replace(states[0], model_split="evaluation"),)
    with pytest.raises(PortableSchemaError, match=match):
        validate_record_closure(
            tasks_by_split=tasks,
            trajectory_sources=sources,
            trajectories_by_split=trajectory_rows,
            transitions=transition_rows,
            decision_states=state_rows,
            prompt_profile="profile",
        )


def test_trajectory_steps_must_be_contiguous_and_terminally_coherent() -> None:
    _, _, trajectories, _, _ = _records()
    trajectory = trajectories["train"][0]
    with pytest.raises(ValueError, match="contiguous"):
        replace(
            trajectory,
            steps=(replace(trajectory.steps[0], step_index=1),),
        ).validate()
    with pytest.raises(PortableSchemaError, match="final step"):
        replace(
            trajectory,
            steps=(replace(trajectory.steps[0], terminal_status=TerminalStatus.FAILURE),),
        ).validate()


def test_terminal_replay_success_and_continuous_reward_contracts() -> None:
    _, _, trajectories, _, _ = _records()
    with pytest.raises(PortableSchemaError, match="success and terminal"):
        replace(trajectories["train"][0], success=False).validate()
    with pytest.raises(PortableSchemaError, match="replay validated"):
        replace(trajectories["train"][0], replay_status=ReplayStatus.FAILED).validate()
    EvaluationResult("task", 0.73, None, TerminalStatus.FAILURE, 4, (), {"reward": 0.73}, ()).validate()
    with pytest.raises(PortableSchemaError, match="binary success"):
        EvaluationResult("task", 1.0, True, TerminalStatus.FAILURE, 1, (), {}, ()).validate()


def test_appworld_capabilities_are_prerequisite_owned_and_probed() -> None:
    adapter = create_appworld_portable_adapter_v2_1()
    report = probe_adapter_capabilities(adapter, prompt_profile="full_demo")
    assert report["runtime_exercised"] is True
    assert report["model_loaded"] is False
    adapter._runtime_factory = None
    assert AdapterCapability.INTERACTIVE_RUNTIME not in adapter.capabilities()
    with pytest.raises(RuntimeError, match="capability preflight failed"):
        probe_adapter_capabilities(adapter, prompt_profile="full_demo")


def test_required_capabilities_depend_on_selected_phase_graph() -> None:
    full = required_capabilities_for_phases(phase.value for phase in PortablePhase)
    training = required_capabilities_for_phases((PortablePhase.TRAINING.value,))
    assert AdapterCapability.INTERACTIVE_RUNTIME in full
    assert not training
    with pytest.raises(CapabilityProofError, match="unknown portable phase"):
        required_capabilities_for_phases(("P99_unknown",))


def test_full_and_continuation_executor_dispatch_are_semantic() -> None:
    full = PortableRunPolicy(PortableRunMode.FULL, "full_demo", 2, 25101)
    continuation = PortableRunPolicy(
        PortableRunMode.SEALED_UPSTREAM_CONTINUATION,
        "full_demo",
        2,
        25101,
        PortablePhase.TRAINING_UNITS,
    )
    assert execution_phases_for_policy(full) == phases_for_policy(full)
    continuation_phases = execution_phases_for_policy(continuation)
    assert continuation_phases[0] == PortablePhase.SEALED_UPSTREAM_BOUNDARY
    assert continuation_phases[1] == PortablePhase.TRAINING
    graph = build_portable_v2_stage_graph(continuation)
    assert graph[0].stage_id == PortablePhase.SEALED_UPSTREAM_BOUNDARY.value
    assert graph[1].dependencies == (PortablePhase.SEALED_UPSTREAM_BOUNDARY.value,)
    assert all("14" not in " ".join(stage.command) for stage in graph)


def test_v2_1_config_binds_dataset_adapter_executor_and_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    config = PortablePipelineConfig.load(ROOT / "configs/pipeline/rcmf_portable_canonical_v2_1.yaml")
    report = config.validate_bindings()
    assert report["passed"]
    assert report["dataset_profile_sha256"] == config.dataset_profile.sha256
    assert len(config.resolve_run_root(run_uuid="fixture").parts) > 1


def test_config_rejects_unknown_safety_and_missing_executor(tmp_path: Path) -> None:
    original = yaml.safe_load((ROOT / "configs/pipeline/rcmf_portable_canonical_v2_1.yaml").read_text())
    original["dataset_profile"] = str((ROOT / "configs/datasets/appworld_portable_v2_1.yaml").resolve())
    original["runtime"]["allow_magic_fallback"] = True
    path = tmp_path / "unknown.yaml"
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown runtime safety"):
        PortablePipelineConfig.load(path)
    original["runtime"].pop("allow_magic_fallback")
    original["phase_executor_factory"] = "missing.module:factory"
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    with pytest.raises((ModuleNotFoundError, PortableExecutionError)):
        PortablePipelineConfig.load(path)


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        ("benchmark", "benchmark differs"),
        ("adapter", "adapter identity differs"),
        ("prompt", "prompt asset identity differs"),
        ("ownership", "ownership must be explicit"),
    ),
)
def test_dataset_profile_binding_mutations_fail(
    tmp_path: Path, mutation: str, match: str
) -> None:
    config = yaml.safe_load(
        (ROOT / "configs/pipeline/rcmf_portable_canonical_v2_1.yaml").read_text()
    )
    profile = yaml.safe_load(
        (ROOT / "configs/datasets/appworld_portable_v2_1.yaml").read_text()
    )
    if mutation == "benchmark":
        profile["benchmark"] = "other"
    elif mutation == "adapter":
        profile["adapter_identity"] = "other.module:factory"
    elif mutation == "prompt":
        profile["prompt_profiles"]["full_demo"] = "0" * 64
    else:
        profile["ownership"] = {}
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    config["dataset_profile"] = str(profile_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        PortablePipelineConfig.load(config_path)


def test_executor_binding_rejects_wrong_adapter_or_missing_phase() -> None:
    reference = "rcmf.benchmarks.appworld.portable_executor_v2_1:create_appworld_portable_executor_v2_1"
    phases = phases_for_policy(PortableRunPolicy(PortableRunMode.FULL, "full_demo", 2, 25101))
    with pytest.raises(PortableExecutionError, match="different adapter"):
        load_executor_factory(reference, adapter_factory="other:factory", required_phases=phases)
    binding = PortableExecutorBinding(EXECUTOR_PROTOCOL_VERSION, "a:b", (PortablePhase.PROVENANCE.value,))
    with pytest.raises(PortableExecutionError, match="lacks required phases"):
        binding.validate(adapter_factory="a:b", required_phases=(PortablePhase.TRAINING,))


def test_real_executor_manifest_binds_inputs_dependencies_outputs_and_identity(tmp_path: Path) -> None:
    run_root = (tmp_path / "run").resolve()
    input_path = tmp_path / "input.json"
    input_path.write_text("{}", encoding="utf-8")
    output_root = run_root / "stages" / PortablePhase.PROVENANCE.value
    identity = PortableExecutionIdentity("a" * 40, "run", run_root, "b" * 64, "c" * 64, "fixture:adapter")

    class Executor:
        protocol_version = EXECUTOR_PROTOCOL_VERSION

        def execute_phase(self, context: PortablePhaseContext) -> PortablePhaseWork:
            output = context.output_root / "evidence.json"
            output.parent.mkdir(parents=True)
            output.write_text(json.dumps({"executed": True}), encoding="utf-8")
            return PortablePhaseWork(({"operation": "inspect_input", "count": 1},), {"evidence": output}, {})

    context = PortablePhaseContext(identity, PortablePhase.PROVENANCE, (), {"input": input_path}, output_root, {})
    manifest = execute_and_validate_phase(Executor(), context)
    assert manifest["source_commit"] == "a" * 40
    assert manifest["input_artifacts"][0]["sha256"]
    assert manifest["output_artifacts"][0]["sha256"]
    assert validate_phase_manifest(output_root / "stage_manifest.json", expected=identity, phase=PortablePhase.PROVENANCE)["passed"]

    class NoOp:
        protocol_version = EXECUTOR_PROTOCOL_VERSION

        def execute_phase(self, context: PortablePhaseContext) -> PortablePhaseWork:
            return PortablePhaseWork((), {}, {})

    with pytest.raises(PortableExecutionError, match="no bounded work"):
        execute_and_validate_phase(NoOp(), replace(context, output_root=run_root / "noop"))

    dependent_input = tmp_path / "dependent.json"
    dependent_input.write_text("input", encoding="utf-8")
    dependent_context = replace(
        context,
        phase=PortablePhase.SUCCESSFUL_CORPUS,
        dependency_manifests=(output_root / "stage_manifest.json",),
        input_artifacts={"dependent": dependent_input},
        output_root=run_root / "dependent",
    )
    execute_and_validate_phase(Executor(), dependent_context)
    dependent_input.write_text("mutated", encoding="utf-8")
    with pytest.raises(PortableExecutionError, match="input_artifacts hash differs"):
        validate_phase_manifest(
            dependent_context.output_root / "stage_manifest.json",
            expected=identity,
            phase=PortablePhase.SUCCESSFUL_CORPUS,
        )
