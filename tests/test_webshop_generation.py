from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

from rcmf.benchmarks.webshop.adapter import DATASET_VERSION, WebShopPortableAdapterV2
from rcmf.benchmarks.webshop.generation import (
    parse_generated_tool_call,
    run_construction_block,
    run_construction_task,
)
from rcmf.pipeline.portable_v2.schemas import TaskRecord
from rcmf.utils.serialization import sha256_file

SOURCE_IDENTITY = {
    "format": "webshop_agent_generated_source_v1",
    "run_uuid": "fixture-run",
    "source_commit": "a" * 40,
    "generation_config_sha256": "b" * 64,
}
ENVIRONMENT_IDENTITY = {
    "format": "webshop_runtime_identity_v1",
    "worker_image_id": "sha256:" + "c" * 64,
}


def test_construction_config_freezes_train_only_greedy_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "configs/datasets/webshop_construction_v1.json").read_text(encoding="utf-8")
    )
    assert config["construction_population"] == {
        "block_size": 250,
        "expansion_order": "ascending_contiguous_blocks",
        "index_end": 12000,
        "index_start": 1500,
        "split": "train",
    }
    assert config["pilot"]["admissible"] is False
    assert config["generation"] == {
        "do_sample": False,
        "dtype": "bfloat16",
        "enable_thinking": False,
        "max_new_tokens": 128,
        "max_rounds": 20,
        "model": "Qwen/Qwen3-8B",
        "model_snapshot_commit": "b968826d9c46dd6066d109eabc6255188de91218",
        "temperature": 0.0,
        "tool_choice": "required",
        "tool_rendering": "qwen_apply_chat_template_tools_v1",
        "top_p": 1.0,
    }


def _task(index: int = 1500) -> TaskRecord:
    return TaskRecord(
        benchmark="agentbench_fc_webshop",
        dataset_version=DATASET_VERSION,
        split="train",
        task_id=f"agentbench-fc-webshop:{index:05d}",
        instruction=f"buy fixture item {index}",
        lineage_keys=(f"webshop-human-goal-index:{index}",),
        source_identity={"fixture": True},
        metadata={"index": index},
    )


@dataclass
class _Result:
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
    def __init__(self, task: TaskRecord, *, replay_mismatch: bool = False) -> None:
        self.task = task
        self.replay_mismatch = replay_mismatch
        self.instruction = task.instruction
        self._step_index = 0
        self.raw_reward = 0.0
        self.done = False
        self.observation = task.instruction + "\n[Search]"
        self._available: Mapping[str, Any] = {
            "has_search_bar": True,
            "clickables": ["search"],
        }

    def available_actions(self) -> Mapping[str, Any]:
        return dict(self._available)

    def step_action(self, action: str) -> _Result:
        step_index = self._step_index
        if action.startswith("search["):
            self.observation = "altered results" if self.replay_mismatch else "results"
            self._available = {
                "has_search_bar": True,
                "clickables": ["back to search", "item"],
            }
            reward, done = 0.0, False
        elif action == "click[item]":
            self.observation = "score 1.0"
            self._available = {"has_search_bar": False, "clickables": []}
            reward, done = 1.0, True
        else:
            self.observation = "invalid"
            reward, done = 0.0, False
        self.raw_reward = reward
        self.done = done
        self._step_index += 1
        return _Result(
            task_id=self.task.task_id,
            step_index=step_index,
            action=action,
            observation=self.observation,
            available_actions=self._available,
            raw_reward=reward,
            done=done,
        )

    def close(self) -> None:
        return None


class _Generator:
    identity: ClassVar[Mapping[str, Any]] = {
        "model": "fixture",
        "enable_thinking": False,
        "do_sample": False,
    }

    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[Sequence[Mapping[str, Any]]] = []

    def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        *,
        max_new_tokens: int,
    ) -> Mapping[str, Any]:
        assert len(tools) == 2
        assert max_new_tokens == 128
        self.calls.append(tuple(dict(message) for message in messages))
        text = self.outputs.pop(0)
        return {
            "assistant_text": text,
            "raw_decoded_with_special_tokens": text + "<|im_end|>",
            "token_ids": [1, 2],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "elapsed_ms": 3.0,
            "input_ids_sha256": "d" * 64,
        }


def _adapter(task: TaskRecord, *, replay_mismatch: bool = False) -> WebShopPortableAdapterV2:
    created = 0

    def factory(row: TaskRecord) -> _Runtime:
        nonlocal created
        created += 1
        return _Runtime(row, replay_mismatch=replay_mismatch and created > 1)

    validation = _task(5000)
    standard = TaskRecord(
        benchmark=task.benchmark,
        dataset_version=task.dataset_version,
        split="standard200",
        task_id="agentbench-fc-webshop:00000",
        instruction="standard fixture",
        lineage_keys=("webshop-human-goal-index:0",),
        source_identity={"fixture": True},
        metadata={"index": 0},
    )
    validation = TaskRecord(
        **{
            **validation.__dict__,
            "split": "validation",
            "task_id": "agentbench-fc-webshop:00500",
            "lineage_keys": ("webshop-human-goal-index:500",),
            "metadata": {"index": 500},
        }
    )
    return WebShopPortableAdapterV2(
        task_records={
            "train": (task,),
            "validation": (validation,),
            "standard200": (standard,),
        },
        trajectory_records={"train": ()},
        trajectory_source_identity=SOURCE_IDENTITY,
        runtime_identity=ENVIRONMENT_IDENTITY,
        runtime_factory=factory,
    )


def _outputs() -> list[str]:
    return [
        '<tool_call>{"name":"search_action","arguments":{"keywords":"fixture"}}</tool_call>',
        '<tool_call>{"name":"click_action","arguments":{"value":"item"}}</tool_call>',
    ]


def test_generated_tool_call_parser_preserves_reasoning_and_is_strict() -> None:
    parsed = parse_generated_tool_call(
        'inspect first\n<tool_call>{"name":"search_action","arguments":"{\\"keywords\\":\\"red shoes\\"}"}</tool_call>',
        call_id="call-7",
    )
    assert parsed.action == "search[red shoes]"
    assert parsed.assistant_content == "inspect first"
    assert parsed.tool_call["id"] == "call-7"
    for invalid in (
        "no tool",
        "<tool_call>{}</tool_call>",
        "<tool_call>{bad}</tool_call>",
        '<tool_call>{"name":"other","arguments":{"value":"x"}}</tool_call>',
        '<tool_call>{"name":"click_action","arguments":{"value":"x"}}</tool_call>' * 2,
    ):
        with pytest.raises(ValueError):
            parse_generated_tool_call(invalid, call_id="call-0")


def test_train_task_preserves_invalid_round_then_admits_exact_replay() -> None:
    task = _task()
    generator = _Generator(["not a tool", *_outputs()])
    row = run_construction_task(
        adapter=_adapter(task),
        task=task,
        generator=generator,
        source_identity=SOURCE_IDENTITY,
        environment_identity=ENVIRONMENT_IDENTITY,
        max_new_tokens=128,
        admissible=True,
    )
    assert row["model_round_count"] == 3
    assert row["environment_step_count"] == 2
    assert row["rounds"][0]["parse_status"] == "INVALID_TOOL_CALL"
    assert "No valid tool call found" in generator.calls[1][-1]["content"]
    assert row["generated_exact_1_success"] is True
    assert row["replay"]["status"] == "VALIDATED"
    assert row["admitted"] is True
    assert row["admitted_trajectory"]["provenance"] == "AGENT_GENERATED"
    assert row["admitted_trajectory"]["raw_reward"] == 1.0
    assert row["standard200_outcome_inspected"] is False


def test_replay_mismatch_is_typed_and_not_admitted() -> None:
    task = _task()
    row = run_construction_task(
        adapter=_adapter(task, replay_mismatch=True),
        task=task,
        generator=_Generator(_outputs()),
        source_identity=SOURCE_IDENTITY,
        environment_identity=ENVIRONMENT_IDENTITY,
        max_new_tokens=128,
        admissible=True,
    )
    assert row["generated_exact_1_success"] is True
    assert row["replay"]["status"] == "FAILED"
    assert row["replay"]["mismatch"]["type"] == "WEBSHOP_REPLAY_STEP_MISMATCH"
    assert row["admitted"] is False
    assert row["admitted_trajectory"] is None


def test_block_resume_validates_task_hash_without_regeneration(tmp_path: Path) -> None:
    task = _task()
    first = _Generator(_outputs())
    summary = run_construction_block(
        adapter=_adapter(task),
        tasks=(task,),
        generator=first,
        source_identity=SOURCE_IDENTITY,
        environment_identity=ENVIRONMENT_IDENTITY,
        output_root=tmp_path,
        max_new_tokens=128,
        admissible=True,
    )
    raw = tmp_path / "raw_tasks/task_01500.json"
    before = sha256_file(raw)
    assert summary["admitted_count"] == 1
    second = _Generator([])
    run_construction_block(
        adapter=_adapter(task),
        tasks=(task,),
        generator=second,
        source_identity=SOURCE_IDENTITY,
        environment_identity=ENVIRONMENT_IDENTITY,
        output_root=tmp_path,
        max_new_tokens=128,
        admissible=True,
    )
    assert second.calls == []
    assert sha256_file(raw) == before
    assert (tmp_path / "admitted_trajectories.jsonl").read_text().count("\n") == 1
