from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from rcmf.benchmarks.alfworld.environment import (
    ALFWorldTextRuntime,
    ENVIRONMENT_VERSION,
    validate_text_action,
)
from rcmf.benchmarks.alfworld.prompt_profile import (
    PROFILE_NAME,
    PROMPT_ASSET_SHA256,
    render_react_messages,
    render_react_trajectory,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    DATASET_VERSION,
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import (
    PROVIDER_ID,
    portable_trajectory,
    read_corpus_jsonl,
)
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

TRACK_R_ID = "alfworld_upstream_react_valid_unseen_reference_v1"
TRACK_R_ROLE = "UPSTREAM_PROTOCOL_REFERENCE"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
GENERATION_IDENTITY_SHA256 = "6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9"
EFFECTIVE_CONTEXT_LIMIT = 40960
ACTION_CAP = 49


class ALFWorldPortableAdapterV2:
    """Real ALFWorld implementation of ReproducibleBenchmarkAdapterV2."""

    def __init__(
        self,
        *,
        task_records: Mapping[str, Sequence[TaskRecord]],
        trajectories: Sequence[TrajectoryRecord],
        prompt_root: str | Path,
        data_root: str | Path | None,
        token_counter: TokenCounter,
        runtime_factory: RuntimeFactory | None = None,
        corpus_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self._task_records = {
            str(split): tuple(rows) for split, rows in task_records.items()
        }
        self._trajectories = tuple(trajectories)
        self.prompt_root = Path(prompt_root)
        self.data_root = Path(data_root).resolve(strict=True) if data_root is not None else None
        self._token_counter = token_counter
        self._runtime_factory = runtime_factory
        self.corpus_identity = dict(corpus_identity or {})
        self._trajectory_source_identity = {
            "provider": PROVIDER_ID,
            "task_manifest_sha256": TASK_MANIFEST_SHA256,
            **self.corpus_identity,
        }
        self._task_index = {
            task.task_id: task for tasks in self._task_records.values() for task in tasks
        }

    def identity(self) -> BenchmarkIdentity:
        return BenchmarkIdentity(
            benchmark_name="alfworld",
            benchmark_version=TRACK_R_ID,
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            environment_version=ENVIRONMENT_VERSION,
            data_version=DATASET_VERSION,
            deterministic=True,
            action_semantics="alfworld_text_command",
            reward_semantics="official_binary_success",
            metadata={
                "track_id": TRACK_R_ID,
                "track_role": TRACK_R_ROLE,
                "task_manifest_sha256": TASK_MANIFEST_SHA256,
                "split_roles": {
                    "training": "train",
                    "official_evaluation": "valid_unseen",
                },
                "trajectory_provenance": ProvenanceClass.OFFICIAL_EXPERT.value,
                "model_revision": MODEL_REVISION,
                "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
                "action_cap": ACTION_CAP,
                "corpus_identity": self.corpus_identity,
            },
        )

    def capabilities(self) -> frozenset[AdapterCapability]:
        capabilities = {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.OFFICIAL_TRAJECTORIES,
            AdapterCapability.STATE_RENDERING,
            AdapterCapability.TRANSITION_RENDERING,
            AdapterCapability.RUNTIME_TOKEN_COUNTING,
            AdapterCapability.CAUSAL_SUPERVISION,
            AdapterCapability.AUDIT_REDACTION,
        }
        if self._runtime_factory is not None or self.data_root is not None:
            capabilities.update(
                {
                    AdapterCapability.RESET_AND_REPLAY,
                    AdapterCapability.INTERACTIVE_RUNTIME,
                    AdapterCapability.OFFICIAL_EVALUATION,
                }
            )
        return frozenset(capabilities)

    def list_tasks(self) -> Mapping[str, Sequence[TaskRecord]]:
        return self._task_records

    def trajectory_sources(self) -> Sequence[TrajectorySource]:
        return (
            TrajectorySource(
                source_id=PROVIDER_ID,
                provenance=ProvenanceClass.OFFICIAL_EXPERT,
                source_identity=self._trajectory_source_identity,
                training_splits=("train",),
            ),
        )

    def successful_trajectories(self, split: str) -> Iterable[TrajectoryRecord]:
        if split != "train":
            return iter(())
        return iter(self._trajectories)

    def transition_records(
        self,
        task: TaskRecord,
        trajectory: TrajectoryRecord,
    ) -> Iterable[TransitionRecord]:
        if task.split != "train" or trajectory.task_id != task.task_id:
            raise ValueError("ALFWorld transition conversion accepts matching TRAIN records only")
        for step in trajectory.steps:
            row = TransitionRecord(
                transition_id=f"{trajectory.trajectory_id}:transition:{step.step_index}",
                parent_trajectory_id=trajectory.trajectory_id,
                task_id=task.task_id,
                step_index=step.step_index,
                goal=task.instruction,
                pre_action_state=step.pre_action_state,
                action=step.action,
                post_action_observation=step.post_action_observation,
                lineage_keys=task.lineage_keys,
                provenance=ProvenanceClass.OFFICIAL_EXPERT,
                replay_identity={
                    "provider": PROVIDER_ID,
                    "trajectory_id": trajectory.trajectory_id,
                    "sequence_sha256": trajectory.metadata["sequence_sha256"],
                    "step_index": step.step_index,
                },
                metadata={"task_family": task.metadata["task_family"]},
            )
            row.validate()
            yield row

    def decision_states(
        self,
        task: TaskRecord,
        trajectory: TrajectoryRecord,
        prompt_profile: str,
    ) -> Iterable[DecisionStateRecord]:
        if prompt_profile != PROFILE_NAME:
            raise KeyError(f"unknown ALFWorld prompt profile: {prompt_profile}")
        prefix: list[dict[str, str]] = []
        initial = str(trajectory.metadata["initial_observation"])
        for step in trajectory.steps:
            row = DecisionStateRecord(
                state_id=f"{trajectory.trajectory_id}:state:{step.step_index}",
                task_id=task.task_id,
                trajectory_prefix=tuple(prefix),
                current_observation=step.pre_action_state,
                target_action_reference={"action": step.action},
                model_split=task.split,
                provenance=ProvenanceClass.OFFICIAL_EXPERT,
                prompt_profile=prompt_profile,
                environment_replay_reference={
                    "trajectory_id": trajectory.trajectory_id,
                    "step_index": step.step_index,
                },
                metadata={
                    "initial_observation": initial,
                    "task_family": task.metadata["task_family"],
                    "game_path": task.metadata["game_path"],
                },
            )
            row.validate()
            yield row
            prefix.append({"action": step.action, "observation": step.post_action_observation})

    def prompt_profiles(self) -> Mapping[str, PromptProfile]:
        return {
            PROFILE_NAME: PromptProfile(
                name=PROFILE_NAME,
                version="upstream-react-6bdb3a1",
                asset_manifest_sha256=PROMPT_ASSET_SHA256,
                demonstration_count=2,
                action_grammar="alfworld_text_command_first_decoded_line",
                context_limit=EFFECTIVE_CONTEXT_LIMIT,
            )
        }

    def render_messages(
        self,
        state: DecisionStateRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, str]]:
        if state.prompt_profile != prompt_profile or prompt_profile != PROFILE_NAME:
            raise ValueError("ALFWorld decision-state prompt identity differs")
        trajectory = render_react_trajectory(
            str(state.metadata["initial_observation"]),
            state.trajectory_prefix,
        )
        return render_react_messages(
            profile_root=self.prompt_root,
            gamefile=str(state.metadata["game_path"]),
            current_trajectory=trajectory,
        )

    def count_runtime_tokens(
        self,
        messages: Sequence[Mapping[str, str]],
        prompt_profile: str,
    ) -> int:
        if prompt_profile != PROFILE_NAME:
            raise KeyError(f"unknown ALFWorld prompt profile: {prompt_profile}")
        count = int(self._token_counter(messages, prompt_profile))
        if count <= 0 or count + 512 > EFFECTIVE_CONTEXT_LIMIT:
            raise ValueError("ALFWorld no-truncation context contract failed")
        return count

    def build_selector_supervision(
        self,
        states: Sequence[DecisionStateRecord],
        transitions: Sequence[TransitionRecord],
    ) -> Sequence[Mapping[str, Any]]:
        transition_index = {row.transition_id: row for row in transitions}
        rows = []
        for state in states:
            transition_id = state.state_id.replace(":state:", ":transition:")
            transition = transition_index.get(transition_id)
            if transition is None:
                continue
            rows.append(
                {
                    "state_id": state.state_id,
                    "transition_id": transition.transition_id,
                    "state_text": state.current_observation,
                    "transition_text": "\n".join(
                        (
                            transition.goal,
                            transition.pre_action_state,
                            transition.action,
                            transition.post_action_observation,
                        )
                    ),
                    "target_action": transition.action,
                    "label": 1.0,
                    "provenance": ProvenanceClass.OFFICIAL_EXPERT.value,
                }
            )
        if not rows:
            raise ValueError("ALFWorld selector supervision has no aligned state/transition rows")
        return tuple(rows)

    def causal_conditions(
        self,
        state: DecisionStateRecord,
        transition: TransitionRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        messages = tuple(self.render_messages(state, prompt_profile))
        # This explicit raw-transition condition is confined to TRAIN-only causal
        # supervision. It is never the production RCMF query prompt.
        transition_text = (
            "TRAIN transition reference:\n"
            f"Goal: {transition.goal}\n"
            f"State: {transition.pre_action_state}\n"
            f"Action: {transition.action}\n"
            f"Observation: {transition.post_action_observation}"
        )
        conditioned = tuple(messages) + ({"role": "user", "content": transition_text},)
        return (
            {"condition": "bare", "messages": messages},
            {"condition": "raw_transition_train_only", "messages": conditioned},
        )

    def compare_causal_outcomes(
        self,
        bare: EvaluationResult,
        conditioned: EvaluationResult,
    ) -> Mapping[str, Any]:
        bare.validate()
        conditioned.validate()
        if bare.task_id != conditioned.task_id:
            raise ValueError("ALFWorld causal pair task identities differ")
        delta = conditioned.raw_reward - bare.raw_reward
        return {
            "label": "POSITIVE" if delta > 0 else "HARMFUL" if delta < 0 else "NEUTRAL",
            "raw_reward_delta": delta,
            "missing": False,
        }

    def create_runtime(self, task: TaskRecord) -> Any:
        if self._runtime_factory is not None:
            return self._runtime_factory(task)
        if self.data_root is None:
            raise RuntimeError("ALFWorld data root is not configured")
        return ALFWorldTextRuntime(task, data_root=self.data_root, with_expert=False)

    def replay_to_state(self, runtime: Any, state: DecisionStateRecord) -> Mapping[str, Any]:
        replayed = []
        for row in state.trajectory_prefix:
            replayed.append(self.execute_action(runtime, str(row["action"])))
        return {
            "replayed_steps": len(replayed),
            "observations": replayed,
            "observation": runtime.observation,
        }

    def validate_action(self, action: str, runtime: Any) -> Mapping[str, Any]:
        del runtime
        return validate_text_action(action)

    def execute_action(self, runtime: Any, action: str) -> Mapping[str, Any]:
        validation = validate_text_action(action)
        if not validation["valid"]:
            raise ValueError("cannot execute an empty ALFWorld command")
        return runtime.step(str(validation["normalized_action"]))

    def evaluate_task(self, runtime: Any, task: TaskRecord) -> EvaluationResult:
        success = bool(runtime.state.get("won", False)) and bool(runtime.done)
        error = getattr(runtime, "error", None)
        result = EvaluationResult(
            task_id=task.task_id,
            raw_reward=1.0 if success else 0.0,
            binary_success=success,
            terminal_status=(
                TerminalStatus.SUCCESS
                if success
                else TerminalStatus.ERROR
                if error
                else TerminalStatus.FAILURE
                if runtime.done
                else TerminalStatus.TRUNCATED
            ),
            steps=len(runtime.actions),
            exceptions=((dict(error),) if error else ()),
            benchmark_metrics={
                "official_success": success,
                "official_won": bool(runtime.state.get("won", False)),
                "environment_done": bool(runtime.done),
            },
            audit_references=(),
        )
        result.validate()
        return result

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        prohibited = {"authorization", "credential", "credentials", "secret", "token"}
        return {key: value for key, value in record.items() if key.lower() not in prohibited}


class _ProbeRuntime:
    def __init__(self, task: TaskRecord) -> None:
        self.task = task
        self.initial_observation = (
            "-= Welcome to TextWorld, ALFRED! =-\n\n"
            "You are in the middle of a room. Your task is to put an apple on a table."
        )
        self.actions: list[str] = []
        self.observations: list[str] = []
        self.done = False
        self.state = {"won": False}
        self.error = None

    @property
    def observation(self) -> str:
        return self.initial_observation if not self.observations else self.observations[-1]

    def step(self, action: str) -> dict[str, Any]:
        self.actions.append(action)
        self.observations.append("probe observation")
        self.done = True
        self.state = {"won": True}
        return {
            "action": action,
            "observation": "probe observation",
            "raw_reward": 1.0,
            "done": True,
            "official_won": True,
            "step": len(self.actions) - 1,
        }

    def close(self) -> None:
        return None


def _probe_adapter() -> ALFWorldPortableAdapterV2:
    task = TaskRecord(
        benchmark="alfworld",
        dataset_version=DATASET_VERSION,
        split="train",
        task_id="alfworld:portable-v2.1-capability-probe",
        instruction="put an apple on a table",
        lineage_keys=("probe-trial", "probe-lineage", "probe-scene"),
        source_identity={"fixture": "portable-v2.1"},
        metadata={
            "game_path": "train/pick_and_place_simple/probe/game.tw-pddl",
            "task_family": "pick_and_place",
        },
    )
    initial = _ProbeRuntime(task).initial_observation
    trajectory = TrajectoryRecord(
        trajectory_id=f"{task.task_id}:official-expert",
        task_id=task.task_id,
        provenance=ProvenanceClass.OFFICIAL_EXPERT,
        source_identity={
            "provider": PROVIDER_ID,
            "task_manifest_sha256": TASK_MANIFEST_SHA256,
            "fixture": "portable-v2.1",
        },
        steps=(
            TrajectoryStep(
                step_index=0,
                pre_action_state=initial,
                action="look",
                post_action_observation="probe observation",
                raw_reward=1.0,
                terminal_status=TerminalStatus.SUCCESS,
            ),
        ),
        raw_reward=1.0,
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity={"fixture": "portable-v2.1"},
        metadata={
            "goal": task.instruction,
            "task_family": "pick_and_place",
            "initial_observation": initial,
            "sequence_sha256": "0" * 64,
        },
    )
    prompt_root = Path(__file__).resolve().parents[3] / "assets" / "prompts" / "alfworld" / PROFILE_NAME
    return ALFWorldPortableAdapterV2(
        task_records={"train": (task,)},
        trajectories=(trajectory,),
        prompt_root=prompt_root,
        data_root=None,
        token_counter=lambda messages, profile: len(json.dumps([list(messages), profile])),
        runtime_factory=_ProbeRuntime,
        corpus_identity={"fixture": "portable-v2.1"},
    )


def create_alfworld_portable_adapter_v2_1(
    *,
    task_manifest_path: str | None = None,
    trajectory_corpus_path: str | None = None,
    prompt_root: str | None = None,
    data_root: str | None = None,
    tokenizer_path: str | None = None,
) -> ALFWorldPortableAdapterV2:
    """Create the capability fixture or an exact, path-bound real adapter."""

    paths = (task_manifest_path, trajectory_corpus_path, prompt_root, data_root, tokenizer_path)
    if all(value is None for value in paths):
        return _probe_adapter()
    if any(value is None for value in paths):
        raise ValueError("real ALFWorld adapter requires every sealed path")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_path),
        local_files_only=True,
        trust_remote_code=True,
    )

    def count_tokens(messages: Sequence[Mapping[str, str]], _: str) -> int:
        rendered = tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        return len(tokenizer(rendered, add_special_tokens=True, truncation=False)["input_ids"])

    manifest_rows = load_sealed_task_manifest(str(task_manifest_path))
    corpus_rows = read_corpus_jsonl(str(trajectory_corpus_path))
    corpus_file = Path(str(trajectory_corpus_path)).resolve(strict=True)
    import hashlib

    corpus_sha = hashlib.sha256(corpus_file.read_bytes()).hexdigest()
    corpus_identity = {
        "path": str(corpus_file),
        "sha256": corpus_sha,
        "row_count": len(corpus_rows),
        "admitted_count": sum(row["status"] == "SUCCESS" for row in corpus_rows),
    }
    source_identity = {
        "provider": PROVIDER_ID,
        "task_manifest_sha256": TASK_MANIFEST_SHA256,
        **corpus_identity,
    }
    trajectories = tuple(
        portable_trajectory(row, source_identity=source_identity)
        for row in corpus_rows
        if row["status"] == "SUCCESS"
    )
    return ALFWorldPortableAdapterV2(
        task_records=portable_task_records(manifest_rows),
        trajectories=trajectories,
        prompt_root=str(prompt_root),
        data_root=str(data_root),
        token_counter=count_tokens,
        corpus_identity=corpus_identity,
    )
