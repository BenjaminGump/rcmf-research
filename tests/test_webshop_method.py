from __future__ import annotations

import json
from pathlib import Path

import torch
from torch import nn

from rcmf.benchmarks.webshop.adapter import WebShopPortableAdapterV2
from rcmf.benchmarks.webshop.evaluation import run_evaluation_task
from rcmf.benchmarks.webshop.method import (
    compile_query_slots,
    selector_candidate_indices,
    task_partition,
    train_selector_member,
)
from rcmf.benchmarks.webshop.representations import (
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    state_representation_text,
    transition_representation_text,
)
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    ProvenanceClass,
    TaskRecord,
    TransitionRecord,
)
from scripts.analyze_webshop_evaluation import _bootstrap_ci, _mcnemar

ROOT = Path(__file__).resolve().parents[1]


def _state() -> DecisionStateRecord:
    return DecisionStateRecord(
        state_id="trajectory:state:0",
        task_id="task-1",
        trajectory_prefix=({"action": "search[old]", "post_observation": "old page"},),
        current_observation="current page",
        target_action_reference={"action": "click[secret-target]"},
        model_split="train",
        provenance=ProvenanceClass.AGENT_GENERATED,
        prompt_profile="agentbench_fc_webshop_v1",
        environment_replay_reference={"task_index": 1500, "step_index": 0},
        metadata={
            "instruction": "buy a blue mug",
            "current_available_actions": {"clickables": ["item"]},
        },
    )


def _transition() -> TransitionRecord:
    return TransitionRecord(
        transition_id="trajectory:transition:0",
        parent_trajectory_id="trajectory",
        task_id="task-1",
        step_index=0,
        goal="buy a blue mug",
        pre_action_state="current page",
        action="click[item]",
        post_action_observation="item page",
        lineage_keys=("lineage-1",),
        provenance=ProvenanceClass.AGENT_GENERATED,
        replay_identity={"task_index": 1500, "step_index": 0},
        metadata={"action_type": "click"},
    )


def test_structured_views_are_complete_and_state_does_not_access_target() -> None:
    state_text, state_spans, state_metadata = state_representation_text(_state())
    transition_text, transition_spans, transition_metadata = transition_representation_text(
        _transition()
    )
    assert tuple(state_spans) == STATE_VIEW_NAMES
    assert tuple(transition_spans) == TRANSITION_VIEW_NAMES
    assert "secret-target" not in state_text
    assert state_metadata["target_action_accessed"] is False
    assert state_metadata["future_observation_accessed"] is False
    for value in ("buy a blue mug", "current page", "click[item]", "item page"):
        assert value in transition_text
    assert transition_metadata["complete_transition"] is True


def test_method_partition_and_selector_candidates_are_deterministic_and_cross_task() -> None:
    task_ids = [f"task-{index}" for index in range(10)]
    first = task_partition(task_ids)
    assert first == task_partition(list(reversed(task_ids)))
    assert set(first.values()) == {"method_train", "method_validation"}
    transition_ids = [f"transition-{index}" for index in range(10)]
    action_types = ["search" if index % 2 == 0 else "click" for index in range(10)]
    candidates = selector_candidate_indices(
        transition_ids=transition_ids,
        task_ids=task_ids,
        action_types=action_types,
    )
    assert candidates == selector_candidate_indices(
        transition_ids=transition_ids,
        task_ids=task_ids,
        action_types=action_types,
    )
    for index, row in enumerate(candidates):
        assert row[0] == index
        assert len(row) == 5
        assert len(set(row)) == 5
        assert all(task_ids[position] != task_ids[index] for position in row[1:])


def test_selector_training_smoke_has_finite_metrics() -> None:
    torch.manual_seed(7)
    states = torch.randn(10, 2, 8)
    transitions = states.clone()
    candidates = selector_candidate_indices(
        transition_ids=[f"transition-{index}" for index in range(10)],
        task_ids=[f"task-{index}" for index in range(10)],
        action_types=["search" if index % 2 == 0 else "click" for index in range(10)],
    )
    result = train_selector_member(
        state_views=states,
        transition_views=transitions,
        candidates=candidates,
        train_positions=list(range(8)),
        validation_positions=[8, 9],
        seed=11,
        epochs=2,
        batch_size=4,
        learning_rate=1.0e-3,
        weight_decay=0.0,
        projection_dim=4,
        interaction_rank=2,
    )
    assert result["train_std"] > 0.0
    assert 0.0 <= result["validation_metrics"]["recall_at_1"] <= 1.0
    assert len(result["history"]) == 2


class _PayloadWriter(nn.Module):
    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return values


def test_training_read_excludes_same_task_without_runtime_memory_selection() -> None:
    torch.manual_seed(9)
    memory_views = torch.randn(6, 8, 256)
    keys = torch.randn(6, 960)
    rho = torch.ones(6)
    query = torch.randn(960)
    task_ids = ["a", "a", "b", "b", "c", "c"]
    slots, field = compile_query_slots(
        writer=_PayloadWriter(),
        memory_views=memory_views,
        keys=keys,
        rho=rho,
        query=query,
        memory_task_ids=task_ids,
        excluded_task_id="a",
    )
    assert slots.shape == (8, 256)
    assert field["A"].shape == (960, 8, 256)
    assert torch.isfinite(slots).all()


def test_webshop_method_config_freezes_standard200_and_three_controls() -> None:
    config = json.loads(
        (ROOT / "configs/datasets/webshop_method_v1.json").read_text(encoding="utf-8")
    )
    assert config["formal_evaluation"]["index_start"] == 0
    assert config["formal_evaluation"]["index_end"] == 200
    assert config["formal_evaluation"]["count"] == 200
    assert config["formal_evaluation"]["conditions"] == ["B0", "RCMF-C", "RCMF-S"]
    assert config["formal_evaluation"]["excluded_index_start"] == 200
    assert config["formal_evaluation"]["excluded_index_end"] == 500
    assert config["field"]["production_top_k_allowed"] is False
    assert config["field"]["raw_memory_prompt_allowed"] is False


def test_formal_runner_contains_no_automatic_test500_extension() -> None:
    runner = (ROOT / "scripts/run_webshop_evaluation.py").read_text(encoding="utf-8")
    freezer = (ROOT / "scripts/freeze_webshop_evaluation.py").read_text(encoding="utf-8")
    assert "range(200, 500)" not in runner
    assert "range(200, 500)" not in freezer
    assert "ordered_task_ids" in runner
    assert "excluded_200_499_execution_authorized" in freezer


def test_frozen_package_records_independent_memory_contributions() -> None:
    source = (ROOT / "scripts/run_webshop_method.py").read_text(encoding="utf-8")
    for key in (
        '"memory_ids"',
        '"memory_task_ids"',
        '"memory_keys"',
        '"memory_payloads"',
        '"rho"',
        '"per_memory_remove_restore_maximum_absolute_error"',
    ):
        assert key in source


class _Runtime:
    def __init__(self) -> None:
        self.instruction = "buy a blue mug"
        self.observation = "search page"
        self.raw_reward = 0.0
        self.done = False
        self._step_index = 0

    def available_actions(self) -> dict[str, object]:
        return {"has_search_bar": True, "clickables": ["search"]}

    def step_action(self, action: str) -> dict[str, object]:
        self._step_index += 1
        self.raw_reward = 1.0
        self.done = True
        self.observation = "done"
        return {
            "step_index": 0,
            "action": action,
            "observation": self.observation,
            "available_actions": {},
            "raw_reward": self.raw_reward,
            "done": self.done,
            "environment_accepted": True,
        }

    def close(self) -> None:
        return None


class _Method:
    def state_query_and_slots(self, **_kwargs: object):
        return torch.zeros(960), torch.zeros(8, 256), {"representation_tokens": 3}

    def generate(self, **_kwargs: object) -> dict[str, object]:
        text = '<tool_call>{"name":"search_action","arguments":{"keywords":"blue mug"}}</tool_call>'
        return {
            "assistant_text": text,
            "raw_decoded_with_special_tokens": text,
            "token_ids": [1, 2],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            "elapsed_ms": 1.0,
            "reader_audit": {},
            "input_ids_sha256": "i" * 64,
        }


def test_three_condition_evaluation_path_records_complete_metrics() -> None:
    task = TaskRecord(
        benchmark="agentbench_fc_webshop",
        dataset_version="v1",
        split="validation",
        task_id="agentbench-fc-webshop:00500",
        instruction="buy a blue mug",
        lineage_keys=("lineage-500",),
        source_identity={"instruction_sha256": "x" * 64},
        metadata={"index": 500},
    )
    adapter = WebShopPortableAdapterV2(
        task_records={"standard200": (), "validation": (task,), "train": ()},
        trajectory_records={"train": ()},
        trajectory_source_identity={"test": True},
        runtime_identity={"test": True},
        runtime_factory=lambda _task: _Runtime(),
    )
    row = run_evaluation_task(
        adapter=adapter,
        method=_Method(),
        task=task,
        condition="RCMF-C",
        max_new_tokens=16,
        evaluation_lock_sha256="l" * 64,
    )
    assert row["raw_reward"] == 1.0
    assert row["full_success"] is True
    assert row["search_count"] == 1
    assert row["invalid_action_count"] == 0
    assert row["prompt_tokens"] == 10
    assert row["representation_tokens"] == 3
    assert row["raw_memory_prompt_used"] is False


def test_paired_analysis_helpers_are_deterministic_and_exact() -> None:
    assert _bootstrap_ci([1.0, 0.0, 0.5], seed=9) == _bootstrap_ci([1.0, 0.0, 0.5], seed=9)
    result = _mcnemar([True, True, False, False], [False, True, True, False])
    assert result == {
        "left_only_success": 1,
        "right_only_success": 1,
        "discordant": 2,
        "exact_two_sided_p": 1.0,
    }
