from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
from typing import Any

from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.adapter import (
    ADAPTER_PROTOCOL_VERSION,
    AdapterCapability,
    BenchmarkIdentity,
    PromptProfile,
    TrajectorySource,
)
from rcmf.pipeline.portable_v2.prompts import PromptAssetManifest
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


BENCHMARK_NAME = "agentbench_fc_webshop"
PROMPT_PROFILE = "agentbench_fc_webshop_v1"
DATASET_VERSION = "agentbench-fc-webshop-data-e8bd3b120fe5"
ENVIRONMENT_VERSION = "webshop-repro-runtime-2026-af342a9b210b"
ACTION_SEMANTICS = "agentbench_fc_function_calls_v1"
REWARD_SEMANTICS = "raw_continuous_reward_exact_1_is_full_success"
MODEL_NAME = "Qwen/Qwen3-8B"
MAX_ROUNDS = 20
RUNTIME_SEED = 233
SPLIT_RANGES: Mapping[str, range] = {
    "standard200": range(0, 200),
    "validation": range(500, 1500),
    "train": range(1500, 12000),
}
SPLIT_ROLES: Mapping[str, str] = {
    "training": "train",
    "validation": "validation",
    "official_evaluation": "standard200",
}
ACTION_RE = re.compile(r"^(search|click)\[(.+)\]$", re.DOTALL)

TokenCounter = Callable[
    [Sequence[Mapping[str, Any]], str, Sequence[Mapping[str, Any]]], int
]
RuntimeFactory = Callable[[TaskRecord], Any]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _prompt_root() -> Path:
    return _repo_root() / "assets/prompts/webshop/agentbench_fc_webshop_v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json(path: str | Path) -> Mapping[str, Any]:
    resolved = Path(os.path.expandvars(str(path))).expanduser().resolve(strict=True)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError(f"JSON artifact must be an object: {resolved}")
    return payload


def task_id(index: int) -> str:
    return f"agentbench-fc-webshop:{index:05d}"


def lineage_key(index: int) -> str:
    return f"webshop-human-goal-index:{index}"


def split_for_index(index: int) -> str:
    matches = [name for name, values in SPLIT_RANGES.items() if index in values]
    if len(matches) != 1:
        raise ValueError(f"WebShop index is outside the frozen populations: {index}")
    return matches[0]


def parse_environment_action(action: str) -> tuple[str, str]:
    match = ACTION_RE.fullmatch(action)
    if match is None or not match.group(2).strip():
        raise ValueError("action must be exactly search[...] or click[...] with a value")
    return match.group(1), match.group(2)


def action_type(action: str) -> str:
    try:
        return parse_environment_action(action)[0]
    except ValueError:
        return "invalid"


def action_to_tool_call(action: str, *, call_id: str) -> Mapping[str, Any]:
    verb, value = parse_environment_action(action)
    function_name, argument_name = (
        ("search_action", "keywords") if verb == "search" else ("click_action", "value")
    )
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": function_name,
            "arguments": json.dumps(
                {argument_name: value}, ensure_ascii=False, separators=(",", ":")
            ),
        },
    }


def format_turn(observation: str, available_actions: Mapping[str, Any], *, initial: bool) -> str:
    if not observation:
        raise ValueError("WebShop observation must be non-empty")
    label = "The initial observation:" if initial else "Observation:"
    return f"{label}\n{observation}\n\nAvailable Actions:\n{dict(available_actions)}"


def format_visible_state(observation: str, available_actions: Mapping[str, Any]) -> str:
    return f"Observation:\n{observation}\n\nAvailable Actions:\n{dict(available_actions)}"


def _step_metadata(step: TrajectoryStep, key: str) -> Any:
    if key not in step.metadata:
        raise ValueError(f"trajectory step {step.step_index} is missing {key}")
    return step.metadata[key]


def _terminal_status(*, done: bool, reward: float, rounds: int) -> TerminalStatus:
    if reward == 1.0:
        return TerminalStatus.SUCCESS
    if done:
        return TerminalStatus.FAILURE
    if rounds >= MAX_ROUNDS:
        return TerminalStatus.TRUNCATED
    return TerminalStatus.TERMINATED


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("runtime result must be a mapping or dataclass")


def load_task_catalog(path: str | Path) -> Mapping[str, tuple[TaskRecord, ...]]:
    payload = _read_json(path)
    if payload.get("format") != "agentbench_fc_webshop_task_catalog_v1":
        raise ValueError("unexpected WebShop task catalog format")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TypeError("WebShop task catalog rows must be a list")
    body = dict(payload)
    recorded = body.pop("catalog_sha256", None)
    harness_digest = hashlib.sha256(
        (_canonical_json(body) + "\n").encode("utf-8")
    ).hexdigest()
    if recorded != harness_digest:
        raise ValueError("WebShop task catalog hash differs")
    expected_indices = {index for values in SPLIT_RANGES.values() for index in values}
    observed_indices: set[int] = set()
    result: dict[str, list[TaskRecord]] = {name: [] for name in SPLIT_RANGES}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TypeError("WebShop task catalog row must be an object")
        index = int(raw["index"])
        split = split_for_index(index)
        instruction = str(raw["instruction"])
        instruction_sha = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        if raw.get("instruction_sha256") != instruction_sha:
            raise ValueError(f"instruction hash differs at WebShop index {index}")
        if raw.get("split") != split or raw.get("task_id") != task_id(index):
            raise ValueError(f"task identity differs at WebShop index {index}")
        if raw.get("lineage_key") != lineage_key(index):
            raise ValueError(f"lineage identity differs at WebShop index {index}")
        if index in observed_indices:
            raise ValueError(f"duplicate WebShop task index: {index}")
        observed_indices.add(index)
        result[split].append(
            TaskRecord(
                benchmark=BENCHMARK_NAME,
                dataset_version=DATASET_VERSION,
                split=split,
                task_id=task_id(index),
                instruction=instruction,
                lineage_keys=(lineage_key(index),),
                source_identity={
                    "catalog_sha256": recorded,
                    "instruction_sha256": instruction_sha,
                    "runtime_task_index": index,
                },
                metadata={"index": index},
            )
        )
    if observed_indices != expected_indices:
        missing = sorted(expected_indices - observed_indices)
        extra = sorted(observed_indices - expected_indices)
        raise ValueError(
            f"WebShop task catalog population differs; missing={missing[:5]} extra={extra[:5]}"
        )
    tasks = {
        name: tuple(sorted(values, key=lambda row: int(row.metadata["index"])))
        for name, values in result.items()
    }
    ensure_no_split_leakage(tuple(row for values in tasks.values() for row in values))
    return tasks


def load_trajectory_corpus(path: str | Path) -> Mapping[str, tuple[TrajectoryRecord, ...]]:
    resolved = Path(os.path.expandvars(str(path))).expanduser().resolve(strict=True)
    result: dict[str, list[TrajectoryRecord]] = {"train": []}
    seen: set[str] = set()
    with resolved.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, Mapping):
                raise TypeError(f"trajectory row {line_number} is not an object")
            if raw.get("split") != "train":
                raise ValueError(f"trajectory row {line_number} is not train-only")
            trajectory = TrajectoryRecord.from_dict(raw)
            if trajectory.provenance != ProvenanceClass.AGENT_GENERATED:
                raise ValueError("WebShop construction corpus must retain AGENT_GENERATED provenance")
            if not trajectory.success or trajectory.raw_reward != 1.0:
                raise ValueError("WebShop corpus may contain only exact-1.0 successes")
            if trajectory.replay_status != ReplayStatus.VALIDATED:
                raise ValueError("WebShop corpus trajectory has not replay-validated")
            index = int(trajectory.metadata.get("index", -1))
            if index not in SPLIT_RANGES["train"] or trajectory.task_id != task_id(index):
                raise ValueError("WebShop trajectory task is outside the frozen train split")
            if trajectory.trajectory_id in seen:
                raise ValueError(f"duplicate WebShop trajectory ID: {trajectory.trajectory_id}")
            seen.add(trajectory.trajectory_id)
            result["train"].append(trajectory)
    if not result["train"]:
        raise ValueError("WebShop successful trajectory corpus is empty")
    return {"train": tuple(result["train"])}


class QwenToolTokenCounter:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self._tokenizer: Any | None = None

    def __call__(
        self,
        messages: Sequence[Mapping[str, Any]],
        profile: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> int:
        if profile != PROMPT_PROFILE:
            raise ValueError("unexpected WebShop prompt profile")
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        token_ids = self._tokenizer.apply_chat_template(
            list(messages),
            tools=list(tools),
            add_generation_prompt=True,
            tokenize=True,
        )
        return len(token_ids)


class WebShopPortableAdapterV2:
    def __init__(
        self,
        *,
        task_records: Mapping[str, Sequence[TaskRecord]],
        trajectory_records: Mapping[str, Sequence[TrajectoryRecord]],
        trajectory_source_identity: Mapping[str, Any],
        runtime_identity: Mapping[str, Any],
        prompt_root: str | Path | None = None,
        token_counter: TokenCounter | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self._tasks = {name: tuple(rows) for name, rows in task_records.items()}
        self._trajectories = {
            name: tuple(rows) for name, rows in trajectory_records.items()
        }
        self._trajectory_source_identity = dict(trajectory_source_identity)
        self._runtime_identity = dict(runtime_identity)
        self._prompt_root = Path(prompt_root or _prompt_root()).resolve(strict=True)
        self._prompt_manifest_path = self._prompt_root / "manifest.json"
        self._prompt_manifest = PromptAssetManifest.load(self._prompt_manifest_path)
        self._system_prompt = (self._prompt_root / "system_prompt.txt").read_text(
            encoding="utf-8"
        )
        self._tools = tuple(
            json.loads((self._prompt_root / "tools.json").read_text(encoding="utf-8"))
        )
        self._token_counter = token_counter
        self._runtime_factory = runtime_factory
        if set(self._tasks) != set(SPLIT_RANGES):
            raise ValueError("WebShop adapter requires train, validation, and standard200 splits")
        ensure_no_split_leakage(
            tuple(task for values in self._tasks.values() for task in values)
        )
        if not self._trajectory_source_identity:
            raise ValueError("WebShop trajectory source identity is required")
        if not self._runtime_identity:
            raise ValueError("WebShop runtime identity is required")

    @property
    def tools(self) -> tuple[Mapping[str, Any], ...]:
        return self._tools

    def identity(self) -> BenchmarkIdentity:
        return BenchmarkIdentity(
            benchmark_name=BENCHMARK_NAME,
            benchmark_version="agentbench-fc-webshop-standard200-v1",
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            environment_version=ENVIRONMENT_VERSION,
            data_version=DATASET_VERSION,
            deterministic=True,
            action_semantics=ACTION_SEMANTICS,
            reward_semantics=REWARD_SEMANTICS,
            metadata={
                "split_roles": dict(SPLIT_ROLES),
                "split_ranges": {
                    name: {"start": values.start, "end": values.stop, "count": len(values)}
                    for name, values in SPLIT_RANGES.items()
                },
                "trajectory_provenance": ProvenanceClass.AGENT_GENERATED.value,
                "runtime_identity": dict(self._runtime_identity),
                "max_rounds": MAX_ROUNDS,
                "runtime_seed": RUNTIME_SEED,
            },
        )

    def capabilities(self) -> frozenset[AdapterCapability]:
        capabilities = {
            AdapterCapability.STABLE_SPLITS,
            AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
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
        return self._tasks

    def trajectory_sources(self) -> Sequence[TrajectorySource]:
        return (
            TrajectorySource(
                source_id="webshop_qwen3_8b_train_successes_v1",
                provenance=ProvenanceClass.AGENT_GENERATED,
                source_identity=self._trajectory_source_identity,
                training_splits=("train",),
            ),
        )

    def successful_trajectories(self, split: str) -> Iterable[TrajectoryRecord]:
        if split != "train":
            raise ValueError("WebShop construction trajectories are train-only")
        for trajectory in self._trajectories.get(split, ()):
            trajectory.validate()
            if dict(trajectory.source_identity) != self._trajectory_source_identity:
                raise ValueError("trajectory source identity differs from its declared source")
            if (
                trajectory.provenance != ProvenanceClass.AGENT_GENERATED
                or not trajectory.success
                or trajectory.raw_reward != 1.0
                or trajectory.replay_status != ReplayStatus.VALIDATED
            ):
                raise ValueError("trajectory does not satisfy WebShop admission policy")
            yield trajectory

    def transition_records(
        self, task: TaskRecord, trajectory: TrajectoryRecord
    ) -> Iterable[TransitionRecord]:
        if task.task_id != trajectory.task_id or task.split != "train":
            raise ValueError("WebShop memory transition is not bound to its train task")
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
                provenance=trajectory.provenance,
                replay_identity={
                    "trajectory_id": trajectory.trajectory_id,
                    "step_index": step.step_index,
                    "task_index": task.metadata["index"],
                },
                metadata={
                    "raw_reward": step.raw_reward,
                    "action_type": action_type(step.action),
                    "available_actions": step.metadata.get("pre_available_actions", {}),
                },
            )
            row.validate()
            yield row

    def decision_states(
        self, task: TaskRecord, trajectory: TrajectoryRecord, prompt_profile: str
    ) -> Iterable[DecisionStateRecord]:
        if prompt_profile != PROMPT_PROFILE:
            raise KeyError(f"unknown WebShop prompt profile: {prompt_profile}")
        prefix: list[Mapping[str, Any]] = []
        for step in trajectory.steps:
            row = DecisionStateRecord(
                state_id=f"{trajectory.trajectory_id}:state:{step.step_index}",
                task_id=task.task_id,
                trajectory_prefix=tuple(prefix),
                current_observation=step.pre_action_state,
                target_action_reference={
                    "action": step.action,
                    "action_type": action_type(step.action),
                },
                model_split=task.split,
                provenance=trajectory.provenance,
                prompt_profile=prompt_profile,
                environment_replay_reference={
                    "trajectory_id": trajectory.trajectory_id,
                    "step_index": step.step_index,
                    "task_index": task.metadata["index"],
                },
                metadata={
                    "instruction": task.instruction,
                    "initial_observation": _step_metadata(
                        trajectory.steps[0], "pre_observation"
                    ),
                    "initial_available_actions": _step_metadata(
                        trajectory.steps[0], "pre_available_actions"
                    ),
                    "current_available_actions": _step_metadata(
                        step, "pre_available_actions"
                    ),
                    "current_page_type": step.metadata.get("page_type", "unknown"),
                },
            )
            row.validate()
            yield row
            prefix.append(
                {
                    "action": step.action,
                    "call_id": str(step.metadata.get("call_id", f"call-{step.step_index}")),
                    "post_observation": _step_metadata(step, "post_observation"),
                    "post_available_actions": _step_metadata(
                        step, "post_available_actions"
                    ),
                }
            )

    def prompt_profiles(self) -> Mapping[str, PromptProfile]:
        return {
            PROMPT_PROFILE: PromptProfile(
                name=PROMPT_PROFILE,
                version="agentbench_fc_webshop_prompt_v1",
                asset_manifest_sha256=sha256_file(self._prompt_manifest_path),
                demonstration_count=0,
                action_grammar="search_action(keywords)|click_action(value)",
                context_limit=40960,
            )
        }

    def render_messages(
        self, state: DecisionStateRecord, prompt_profile: str
    ) -> Sequence[Mapping[str, Any]]:
        if prompt_profile != PROMPT_PROFILE or state.prompt_profile != prompt_profile:
            raise ValueError("WebShop prompt profile mismatch")
        messages: list[Mapping[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": format_turn(
                    str(state.metadata["initial_observation"]),
                    state.metadata["initial_available_actions"],
                    initial=True,
                ),
            },
        ]
        for item in state.trajectory_prefix:
            call_id = str(item["call_id"])
            action = str(item["action"])
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [action_to_tool_call(action, call_id=call_id)],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": (
                        f"Action: {action}\n\n"
                        + format_turn(
                            str(item["post_observation"]),
                            item["post_available_actions"],
                            initial=False,
                        )
                    ),
                }
            )
        return tuple(messages)

    def generation_request(
        self, state: DecisionStateRecord, prompt_profile: str
    ) -> Mapping[str, Any]:
        return {
            "messages": self.render_messages(state, prompt_profile),
            "tools": self.tools,
            "tool_choice": "required",
        }

    def count_runtime_tokens(
        self, messages: Sequence[Mapping[str, Any]], prompt_profile: str
    ) -> int:
        if self._token_counter is None:
            raise RuntimeError("Qwen tool-aware token counter is not configured")
        return int(self._token_counter(messages, prompt_profile, self.tools))

    def build_selector_supervision(
        self,
        states: Sequence[DecisionStateRecord],
        transitions: Sequence[TransitionRecord],
    ) -> Sequence[Mapping[str, Any]]:
        transition_by_parent_step = {
            (row.parent_trajectory_id, row.step_index): row for row in transitions
        }
        ordered = sorted(
            transitions,
            key=lambda row: hashlib.sha256(row.transition_id.encode()).hexdigest(),
        )
        result = []
        for state in states:
            reference = state.environment_replay_reference
            key = (str(reference["trajectory_id"]), int(reference["step_index"]))
            positive = transition_by_parent_step.get(key)
            if positive is None:
                raise ValueError(f"selector state has no positive transition: {state.state_id}")
            target_type = action_type(positive.action)
            negatives = [
                row.transition_id
                for row in ordered
                if row.transition_id != positive.transition_id
                and row.task_id != state.task_id
                and action_type(row.action) == target_type
            ][:2]
            for row in ordered:
                if len(negatives) >= 4:
                    break
                if (
                    row.transition_id != positive.transition_id
                    and row.task_id != state.task_id
                    and action_type(row.action) != target_type
                    and row.transition_id not in negatives
                ):
                    negatives.append(row.transition_id)
            result.append(
                {
                    "state_id": state.state_id,
                    "positive_transition_id": positive.transition_id,
                    "negative_transition_ids": negatives[:4],
                    "target_action_type": target_type,
                    "page_type": state.metadata.get("current_page_type", "unknown"),
                    "label_basis": (
                        "same_replay_transition_positive; deterministic_cross_task_negatives"
                    ),
                }
            )
        return tuple(result)

    def causal_conditions(
        self,
        state: DecisionStateRecord,
        transition: TransitionRecord,
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        messages = tuple(self.render_messages(state, prompt_profile))
        teacher = {
            "goal": transition.goal,
            "pre_action_state": transition.pre_action_state,
            "complete_action": transition.action,
            "post_action_observation": transition.post_action_observation,
        }
        return (
            {"condition": "bare", "messages": messages},
            {
                "condition": "raw_transition_teacher_training_only",
                "messages": (
                    {
                        "role": "system",
                        "content": "TRAINING-ONLY transition evidence:\n" + _canonical_json(teacher),
                    },
                    *messages,
                ),
                "deployment_allowed": False,
            },
        )

    def compare_causal_outcomes(
        self, bare: EvaluationResult, conditioned: EvaluationResult
    ) -> Mapping[str, Any]:
        bare.validate()
        conditioned.validate()
        if bare.task_id != conditioned.task_id:
            raise ValueError("WebShop causal pair task identities differ")
        delta = conditioned.raw_reward - bare.raw_reward
        return {
            "label": "POSITIVE" if delta > 0 else "HARMFUL" if delta < 0 else "NEUTRAL",
            "raw_reward_delta": delta,
            "bare_full_success": bare.raw_reward == 1.0,
            "conditioned_full_success": conditioned.raw_reward == 1.0,
            "missing": False,
        }

    def create_runtime(self, task: TaskRecord) -> Any:
        if self._runtime_factory is None:
            raise RuntimeError("frozen Lambda WebShop runtime factory is not configured")
        return self._runtime_factory(task)

    def replay_to_state(self, runtime: Any, state: DecisionStateRecord) -> Mapping[str, Any]:
        reset = runtime.reset()
        expected_initial = str(state.metadata["initial_observation"])
        if str(reset["observation"]) != expected_initial:
            raise RuntimeError("WEBSHOP_REPLAY_INITIAL_OBSERVATION_MISMATCH")
        observations = []
        for item in state.trajectory_prefix:
            result = self.execute_action(runtime, str(item["action"]))
            if result["observation"] != str(item["post_observation"]):
                raise RuntimeError("WEBSHOP_REPLAY_STEP_OBSERVATION_MISMATCH")
            if dict(result["available_actions"]) != dict(item["post_available_actions"]):
                raise RuntimeError("WEBSHOP_REPLAY_AVAILABLE_ACTIONS_MISMATCH")
            observations.append(result)
        return {
            "replayed_steps": len(observations),
            "observations": observations,
            "state_sha256": content_sha256(
                {
                    "observation": runtime.observation,
                    "available_actions": runtime.available_actions(),
                }
            ),
        }

    def validate_action(self, action: str, runtime: Any) -> Mapping[str, Any]:
        try:
            verb, value = parse_environment_action(action)
        except ValueError as exc:
            return {"valid": False, "environment_accepted": False, "error": str(exc)}
        available = runtime.available_actions()
        accepted = verb == "search" or value.lower() in set(available.get("clickables", ()))
        return {
            "valid": True,
            "environment_accepted": accepted,
            "action_type": verb,
        }

    def execute_action(self, runtime: Any, action: str) -> Mapping[str, Any]:
        parse_environment_action(action)
        return dict(_as_mapping(runtime.step_action(action)))

    def evaluate_task(self, runtime: Any, task: TaskRecord) -> EvaluationResult:
        reward = float(runtime.raw_reward)
        rounds = int(getattr(runtime, "_step_index", 0))
        done = bool(runtime.done)
        if not 0.0 <= reward <= 1.0:
            raise ValueError("WebShop official reward must be in [0, 1]")
        result = EvaluationResult(
            task_id=task.task_id,
            raw_reward=reward,
            binary_success=reward == 1.0,
            terminal_status=_terminal_status(done=done, reward=reward, rounds=rounds),
            steps=rounds,
            exceptions=(),
            benchmark_metrics={
                "raw_reward": reward,
                "exact_1_full_success": reward == 1.0,
                "done": done,
                "max_rounds": MAX_ROUNDS,
            },
            audit_references=(
                {
                    "task_index": task.metadata["index"],
                    "runtime_identity_sha256": content_sha256(self._runtime_identity),
                },
            ),
        )
        result.validate()
        return result

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        hidden = {"asin", "evaluator_goal", "goal_options", "server_goal"}

        def redact(value: Any) -> Any:
            if isinstance(value, Mapping):
                result = {}
                for key, item in value.items():
                    if str(key).lower() in hidden:
                        result[str(key)] = {
                            "redacted": True,
                            "sha256": hashlib.sha256(
                                _canonical_json(item).encode("utf-8")
                            ).hexdigest(),
                        }
                    else:
                        result[str(key)] = redact(item)
                return result
            if isinstance(value, (list, tuple)):
                return [redact(item) for item in value]
            return value

        return redact(record)


def _runtime_factory(reference: str, session_namespace: str) -> RuntimeFactory:
    if reference.count(":") != 1:
        raise ValueError("runtime factory reference must be module:attribute")
    module_name, attribute = reference.split(":", 1)

    def create(task: TaskRecord) -> Any:
        runtime_class = getattr(import_module(module_name), attribute)
        return runtime_class(
            int(task.metadata["index"]),
            deterministic_seed=RUNTIME_SEED,
            session_namespace=session_namespace,
        )

    return create


def create_webshop_portable_adapter_v1(
    *,
    task_catalog_path: str,
    trajectory_corpus_path: str,
    trajectory_source_manifest_path: str,
    runtime_identity_path: str,
    runtime_factory_ref: str = (
        "benchmarks.webshop_agentbench_fc.environment:InProcessWebShopRuntime"
    ),
    session_namespace: str = "rcmf-webshop-v1",
    model_name: str = MODEL_NAME,
) -> WebShopPortableAdapterV2:
    source_manifest = _read_json(trajectory_source_manifest_path)
    source_identity = source_manifest.get("source_identity")
    if not isinstance(source_identity, Mapping) or not source_identity:
        raise ValueError("trajectory source manifest lacks source_identity")
    runtime_identity = _read_json(runtime_identity_path)
    counter = QwenToolTokenCounter(model_name)

    def token_counter(
        messages: Sequence[Mapping[str, Any]],
        profile: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> int:
        return counter(messages, profile, tools)

    return WebShopPortableAdapterV2(
        task_records=load_task_catalog(task_catalog_path),
        trajectory_records=load_trajectory_corpus(trajectory_corpus_path),
        trajectory_source_identity=source_identity,
        runtime_identity=runtime_identity,
        token_counter=token_counter,
        runtime_factory=_runtime_factory(runtime_factory_ref, session_namespace),
    )
