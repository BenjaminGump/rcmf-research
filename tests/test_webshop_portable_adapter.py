from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from rcmf.benchmarks.webshop.adapter import (
    ACTION_SEMANTICS,
    DATASET_VERSION,
    PROMPT_PROFILE,
    REWARD_SEMANTICS,
    WebShopPortableAdapterV2,
    action_to_tool_call,
    format_visible_state,
    parse_environment_action,
)
from rcmf.pipeline.portable_v2.adapter import (
    AdapterCapability,
    probe_adapter_capabilities,
)
from rcmf.pipeline.portable_v2.conformance import run_manifest_only_conformance
from rcmf.pipeline.portable_v2.config import DatasetProfile
from rcmf.pipeline.portable_v2.dag import PortableRunMode, PortableRunPolicy
from rcmf.pipeline.portable_v2.schemas import (
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_IDENTITY = {
    "format": "webshop_agent_generated_source_v1",
    "model": "Qwen/Qwen3-8B",
    "run_uuid": "fixture-run",
    "source_commit": "a" * 40,
}
RUNTIME_IDENTITY = {
    "worker_image_id": "sha256:" + "b" * 64,
    "agentbench_commit": "d1e4a10db08c87075c78972e48ecc182be03e2d5",
    "princeton_commit": "64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd",
}


def _task(split: str, index: int) -> TaskRecord:
    return TaskRecord(
        benchmark="agentbench_fc_webshop",
        dataset_version=DATASET_VERSION,
        split=split,
        task_id=f"agentbench-fc-webshop:{index:05d}",
        instruction=f"Instruction: buy fixture item {index}",
        lineage_keys=(f"webshop-human-goal-index:{index}",),
        source_identity={"fixture": True, "runtime_task_index": index},
        metadata={"index": index},
    )


def _trajectory(task: TaskRecord) -> TrajectoryRecord:
    initial_observation = task.instruction + "\n[Search]"
    initial_actions = {"has_search_bar": True, "clickables": ["search"]}
    results_observation = "Search results\n[fixture-asin]"
    results_actions = {
        "has_search_bar": True,
        "clickables": ["back to search", "next >", "fixture-asin"],
    }
    done_observation = "Your score (min 0.0, max 1.0): 1.0"
    done_actions = {"has_search_bar": False, "clickables": []}
    steps = (
        TrajectoryStep(
            step_index=0,
            pre_action_state=format_visible_state(initial_observation, initial_actions),
            action="search[fixture item]",
            post_action_observation=results_observation,
            raw_reward=0.0,
            terminal_status=TerminalStatus.NOT_TERMINAL,
            metadata={
                "call_id": "call-0",
                "pre_observation": initial_observation,
                "pre_available_actions": initial_actions,
                "post_observation": results_observation,
                "post_available_actions": results_actions,
                "page_type": "search",
            },
        ),
        TrajectoryStep(
            step_index=1,
            pre_action_state=format_visible_state(results_observation, results_actions),
            action="click[fixture-asin]",
            post_action_observation=done_observation,
            raw_reward=1.0,
            terminal_status=TerminalStatus.SUCCESS,
            metadata={
                "call_id": "call-1",
                "pre_observation": results_observation,
                "pre_available_actions": results_actions,
                "post_observation": done_observation,
                "post_available_actions": done_actions,
                "page_type": "results",
            },
        ),
    )
    return TrajectoryRecord(
        trajectory_id=f"trajectory:{task.task_id}",
        task_id=task.task_id,
        provenance=ProvenanceClass.AGENT_GENERATED,
        source_identity=SOURCE_IDENTITY,
        steps=steps,
        raw_reward=1.0,
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity=RUNTIME_IDENTITY,
        agent_identity={"model": "Qwen/Qwen3-8B", "temperature": 0.0},
        metadata={"index": int(task.metadata["index"]), "goal": task.instruction},
    )


@dataclass
class _Step:
    task_id: str
    step_index: int
    action: str
    observation: str
    available_actions: Mapping[str, Any]
    raw_reward: float
    done: bool
    valid_syntax: bool = True
    environment_accepted: bool = True
    failure_type: str | None = None


class _Runtime:
    def __init__(self, task: TaskRecord) -> None:
        self.task = task
        self._step_index = 0
        self.raw_reward = 0.0
        self.done = False
        self.observation = ""
        self._available: Mapping[str, Any] = {}
        self.reset()

    def reset(self) -> Mapping[str, Any]:
        self._step_index = 0
        self.raw_reward = 0.0
        self.done = False
        self.observation = self.task.instruction + "\n[Search]"
        self._available = {"has_search_bar": True, "clickables": ["search"]}
        return {
            "observation": self.observation,
            "available_actions": self._available,
        }

    def available_actions(self) -> Mapping[str, Any]:
        return self._available

    def step_action(self, action: str) -> _Step:
        index = self._step_index
        if action == "search[fixture item]":
            self.observation = "Search results\n[fixture-asin]"
            self._available = {
                "has_search_bar": True,
                "clickables": ["back to search", "next >", "fixture-asin"],
            }
            reward, done = 0.0, False
        elif action == "click[fixture-asin]":
            self.observation = "Your score (min 0.0, max 1.0): 1.0"
            self._available = {"has_search_bar": False, "clickables": []}
            reward, done = 1.0, True
        else:
            reward, done = 0.0, False
        self.raw_reward = reward
        self.done = done
        self._step_index += 1
        return _Step(
            task_id=self.task.task_id,
            step_index=index,
            action=action,
            observation=self.observation,
            available_actions=self._available,
            raw_reward=reward,
            done=done,
        )

    def close(self) -> None:
        return None


def _adapter() -> WebShopPortableAdapterV2:
    train = _task("train", 1500)
    tasks = {
        "standard200": (_task("standard200", 0),),
        "validation": (_task("validation", 500),),
        "train": (train,),
    }
    return WebShopPortableAdapterV2(
        task_records=tasks,
        trajectory_records={"train": (_trajectory(train),)},
        trajectory_source_identity=SOURCE_IDENTITY,
        runtime_identity=RUNTIME_IDENTITY,
        token_counter=lambda messages, profile, tools: len(
            json.dumps(
                {"messages": list(messages), "profile": profile, "tools": list(tools)},
                sort_keys=True,
            )
        ),
        runtime_factory=_Runtime,
    )


def test_prompt_assets_are_exact_agentbench_fc_program() -> None:
    root = ROOT / "assets/prompts/webshop/agentbench_fc_webshop_v1"
    assert hashlib.sha256((root / "system_prompt.txt").read_bytes()).hexdigest() == (
        "4d2c361799681a200b69c21229a3ea07a79b7c1e74fd0cb308defd0c85d3ce11"
    )
    assert hashlib.sha256((root / "tools.json").read_bytes()).hexdigest() == (
        "be6b939ad34d95f55759f679df37f167bda2585d8eea128bea6f2e6a0e7af7c3"
    )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["content_changed"] is False
    assert manifest["upstream_commit"] == "d1e4a10db08c87075c78972e48ecc182be03e2d5"
    profile = DatasetProfile.load(ROOT / "configs/datasets/webshop_v1.yaml")
    assert profile.benchmark == "agentbench_fc_webshop"
    assert profile.prompt_profiles[PROMPT_PROFILE] == hashlib.sha256(
        (root / "manifest.json").read_bytes()
    ).hexdigest()
    assert profile.trajectory_source["provenance"] == "AGENT_GENERATED"
    assert profile.splits["official_evaluation"] == "standard200"


def test_action_translation_is_strict_and_preserves_complete_value() -> None:
    assert parse_environment_action("search[red water bottle]") == (
        "search",
        "red water bottle",
    )
    call = action_to_tool_call("click[item-123]", call_id="call-fixture")
    assert call["function"]["name"] == "click_action"
    assert json.loads(call["function"]["arguments"]) == {"value": "item-123"}
    for invalid in ("", "think[x]", "search[]", " click[x]", "click[x] trailing"):
        with pytest.raises(ValueError):
            parse_environment_action(invalid)


def test_adapter_contract_prompt_replay_reward_and_redaction(tmp_path: Path) -> None:
    adapter = _adapter()
    assert adapter.identity().action_semantics == ACTION_SEMANTICS
    assert adapter.identity().reward_semantics == REWARD_SEMANTICS
    assert set(adapter.capabilities()) >= {
        AdapterCapability.SUCCESSFUL_TRAJECTORY_SOURCE,
        AdapterCapability.INTERACTIVE_RUNTIME,
        AdapterCapability.OFFICIAL_EVALUATION,
    }
    task = adapter.list_tasks()["train"][0]
    trajectory = tuple(adapter.successful_trajectories("train"))[0]
    states = tuple(adapter.decision_states(task, trajectory, PROMPT_PROFILE))
    messages = adapter.render_messages(states[1], PROMPT_PROFILE)
    assert [row["role"] for row in messages] == ["system", "user", "assistant", "tool"]
    assert messages[2]["tool_calls"][0]["function"]["name"] == "search_action"
    assert messages[3]["content"].startswith("Action: search[fixture item]")
    assert adapter.generation_request(states[1], PROMPT_PROFILE)["tool_choice"] == "required"

    runtime = adapter.create_runtime(task)
    assert adapter.replay_to_state(runtime, states[1])["replayed_steps"] == 1
    adapter.execute_action(runtime, trajectory.steps[1].action)
    result = adapter.evaluate_task(runtime, task)
    assert result.raw_reward == 1.0
    assert result.binary_success is True
    assert result.terminal_status == TerminalStatus.SUCCESS
    redacted = adapter.redact_audit_record(
        {"server_goal": {"asin": "secret"}, "action": "click[secret]"}
    )
    assert redacted["server_goal"]["redacted"] is True
    assert redacted["action"] == "click[secret]"
    runtime.close()

    report = run_manifest_only_conformance(
        adapter=adapter,
        policy=PortableRunPolicy(PortableRunMode.FULL, PROMPT_PROFILE, 2, 25101),
        run_root=tmp_path / "webshop-portable",
    )
    assert report["passed"] is True
    assert report["counts"] == {
        "splits": {"standard200": 1, "train": 1, "validation": 1},
        "tasks": 3,
        "trajectories": 1,
        "transitions": 2,
        "decision_states": 2,
    }


def test_standard200_is_not_a_trajectory_source() -> None:
    adapter = _adapter()
    assert adapter.trajectory_sources()[0].training_splits == ("train",)
    with pytest.raises(ValueError, match="train-only"):
        tuple(adapter.successful_trajectories("standard200"))
    report = probe_adapter_capabilities(adapter, prompt_profile=PROMPT_PROFILE)
    assert report["passed"] is True


def test_partial_reward_is_not_full_success() -> None:
    adapter = _adapter()
    task = adapter.list_tasks()["validation"][0]
    runtime = _Runtime(task)
    runtime.raw_reward = 0.75
    runtime.done = True
    result = adapter.evaluate_task(runtime, task)
    assert result.raw_reward == 0.75
    assert result.binary_success is False
    assert result.terminal_status == TerminalStatus.FAILURE
