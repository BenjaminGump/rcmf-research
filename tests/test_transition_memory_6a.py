from __future__ import annotations

import copy

import pytest

from rcmf.benchmarks.appworld.traces import AppWorldTraceStep, render_state_for_step
from rcmf.benchmarks.appworld.prompt import get_initial_messages
from rcmf.benchmarks.appworld.transitions import (
    extract_decision_transitions,
    reconstruct_parent_trajectory,
    select_transition_panel,
    transition_teacher_section,
    validate_transition_extraction,
)
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.transition_memory_6a import (
    decoder_manifest_state_ids,
    deterministic_identity_derangement,
    granularity_advantage,
    is_legal_transition_pair,
    messages_with_transition_memory,
    pair_oracle_capacity_gate,
    select_query_manifest,
    transition_field_algebra_validation,
    transition_pilot_decision,
)


def _record(step_count: int = 6) -> MemoryRecord:
    steps = []
    for index in range(1, step_count + 1):
        action = f"```python\nprint(apis.spotify.call_{index}())\n```"
        if index == step_count:
            action = "```python\napis.supervisor.complete_task()\n```"
        steps.append(
            {
                "step_id": index,
                "response": action,
                "observation": f"observation-{index}",
            }
        )
    return MemoryRecord(
        memory_id="memory-a",
        benchmark="appworld",
        episode_id="appworld:trace:task_a",
        task_id="task_a",
        raw_trajectory={
            "query": "Solve task A exactly.",
            "system_prompt": "System instructions.",
            "steps": steps,
            "final_answer": "done",
            "source_path": "task_a/environment_io.md",
        },
        experience_text="full trajectory",
        outcome=1.0,
        success=True,
        metadata={
            "source_replay_id": "replay-a",
            "source_lineage_id": "lineage-a",
        },
    )


def _example(task_id: str, step_id: int, state_text: str = "state") -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id=f"appworld:trace:{task_id}",
        step_id=step_id,
        state_text=state_text,
        target_text=f"```python\nprint(apis.spotify.step_{step_id}())\n```",
        target_type="code",
        candidate_memory_ids=None,
        metadata={"task_id": task_id},
    )


def test_transition_extraction_boundaries_alignment_and_reconstruction() -> None:
    record = _record()
    transitions = extract_decision_transitions(record)
    assert len(transitions) == 6
    assert "[TRACE SO FAR]" not in transitions[0].canonical_pre_action_state
    expected_second = render_state_for_step(
        record.raw_trajectory["query"],
        [
            AppWorldTraceStep(
                index=1,
                response=record.raw_trajectory["steps"][0]["response"],
                observation=record.raw_trajectory["steps"][0]["observation"],
            )
        ],
        system_prompt=record.raw_trajectory["system_prompt"],
    )
    assert transitions[1].canonical_pre_action_state == expected_second
    assert transitions[-1].complete_action == record.raw_trajectory["steps"][-1]["response"]
    assert transitions[-1].complete_post_action_observation == "observation-6"
    assert transitions[-1].completion_related is True
    reconstructed = reconstruct_parent_trajectory(transitions)
    assert reconstructed["query"] == record.raw_trajectory["query"]
    assert reconstructed["steps"] == record.raw_trajectory["steps"]
    assert reconstructed["final_answer"] == "done"
    assert validate_transition_extraction([record], transitions)["passed"] is True


def test_transition_ids_and_hashes_are_stable_and_content_sensitive() -> None:
    record = _record()
    first = extract_decision_transitions(record)
    second = extract_decision_transitions(copy.deepcopy(record))
    assert [row.transition_id for row in first] == [row.transition_id for row in second]
    assert [row.transition_content_sha256 for row in first] == [
        row.transition_content_sha256 for row in second
    ]
    changed = copy.deepcopy(record)
    changed.raw_trajectory["steps"][2]["observation"] = "changed"
    third = extract_decision_transitions(changed)
    assert first[2].transition_id != third[2].transition_id
    assert first[3].transition_id != third[3].transition_id


def test_source_step_order_mismatch_stops_extraction() -> None:
    record = _record()
    record.raw_trajectory["steps"][1]["step_id"] = 3
    with pytest.raises(ValueError, match="source ordering differs"):
        extract_decision_transitions(record)


def test_panel_selects_first_thirds_and_final_noncompletion_without_duplicates() -> None:
    transitions = extract_decision_transitions(_record(step_count=6))
    selected, report = select_transition_panel(transitions)
    assert [row.step_index for row in selected] == [1, 3, 4, 5]
    assert report["transition_count"] == 4
    assert report["completion_transition_count"] == 0
    one = extract_decision_transitions(_record(step_count=1))
    selected_one, _ = select_transition_panel(one)
    assert len(selected_one) == 1


def test_transition_teacher_section_and_message_insertion_are_exact() -> None:
    transition = extract_decision_transitions(_record())[0]
    section = transition_teacher_section(transition)
    assert section.count("[DECISION TRANSITION MEMORY]") == 1
    assert "SOURCE TASK GOAL:\nSolve task A exactly." in section
    assert "SOURCE STATE BEFORE ACTION:" in section
    assert "SOURCE ACTION:" in section
    assert "SOURCE OBSERVATION AFTER ACTION:\nobservation-1" in section
    base = [
        *[dict(message) for message in get_initial_messages("full_demo")],
        {"role": "user", "content": "current state"},
    ]
    inserted = messages_with_transition_memory(base, transition, "full_demo")
    assert base[-1]["content"] == "current state"
    assert inserted[-1]["content"].count("[DECISION TRANSITION MEMORY]") == 1
    assert inserted[-1]["content"].endswith(
        "[CURRENT APPWORLD STATE START]\ncurrent state\n[CURRENT APPWORLD STATE END]"
    )


def test_formal_task_episode_replay_lineage_leakage_exclusion() -> None:
    transition = extract_decision_transitions(_record())[0]
    assert is_legal_transition_pair(_example("task_a", 1), transition) is False
    replay = _example("task_b", 1)
    replay.metadata["replay_id"] = "replay-a"
    assert is_legal_transition_pair(replay, transition) is False
    lineage = _example("task_c", 1)
    lineage.metadata["derived_from_id"] = "lineage-a"
    assert is_legal_transition_pair(lineage, transition) is False
    assert is_legal_transition_pair(_example("task_d", 1), transition) is True


def test_query_manifest_is_task_grouped_and_excludes_exp016c_states() -> None:
    train_tasks = [f"train_family_{index:02d}_1" for index in range(37)]
    validation_tasks = [f"validation_family_{index:02d}_1" for index in range(9)]
    examples = []
    for task_id in train_tasks + validation_tasks:
        examples.extend(_example(task_id, step) for step in (1, 2, 3, 4))
    prompt_tokens = [1000 + 37 * index for index in range(len(examples))]
    excluded_index = len(train_tasks) * 4 + 1
    excluded_id = (
        f"{examples[excluded_index].episode_id}:step:{examples[excluded_index].step_id}:"
        f"line:{excluded_index + 1}"
    )
    decoder_manifest = {
        "state_count": 1,
        "ordered_source_pair_ids": [f"{excluded_id}::memory::m0"],
        "folds": [],
    }
    assert decoder_manifest_state_ids(decoder_manifest) == {excluded_id}
    split_manifest = {
        "train_task_ids": train_tasks,
        "validation_task_ids": validation_tasks,
    }
    manifest = select_query_manifest(
        examples=examples,
        prompt_token_counts=prompt_tokens,
        split_manifest=split_manifest,
        decoder_manifest=decoder_manifest,
        seed=17017,
    )
    assert manifest["query_count"] == 32
    assert manifest["train_query_count"] == 24
    assert manifest["validation_query_count"] == 8
    assert len(manifest["train_task_ids"]) == 12
    assert len(manifest["validation_task_ids"]) == 4
    assert manifest["selected_exp016c_overlap"] == []
    assert all(
        len(
            [
                row
                for row in manifest["query_rows"]
                if row["task_id"] == task_id
            ]
        )
        == 2
        for task_id in manifest["train_task_ids"] + manifest["validation_task_ids"]
    )


def test_transition_field_parent_and_transition_reversibility() -> None:
    report = transition_field_algebra_validation(
        rank=7, program_dim=5, parent_count=3, steps=4, seed=11
    )
    assert report["passed"] is True
    assert all(report["checks"].values())


def test_transition_pair_gate_and_decision_tree() -> None:
    summary = {
        "u_text_vs_u_student_spearman": 0.82,
        "positive_negative_sign_agreement": 0.88,
        "sequence_utility_huber": {"mean": 0.04},
        "delta_ratio": {"max": 0.99},
        "by_utility_category": {"neutral": {"mean_abs_u_student": 0.03}},
    }
    zero = {"sequence_utility_huber": {"mean": 0.10}}
    assert pair_oracle_capacity_gate(summary=summary, zero_summary=zero)["passed"]
    decision = transition_pilot_decision(
        teacher_valid=True,
        pair_oracle_passed=True,
        direct_oracle_passed=None,
        static_transition_passed=False,
        granularity_passed=None,
    )
    assert decision["branch"] == "static_transition_program_insufficient"


def test_deterministic_identity_derangement_has_no_fixed_points() -> None:
    identities = [f"transition-{index}" for index in range(8)]
    first = deterministic_identity_derangement(
        identities, seed=17019, namespace="shuffle"
    )
    second = deterministic_identity_derangement(
        identities, seed=17019, namespace="shuffle"
    )
    assert first == second
    assert set(first) == set(identities)
    assert set(first.values()) == set(identities)
    assert all(source != target for source, target in first.items())


def test_granularity_advantage_requires_material_not_tiny_differences() -> None:
    trajectory = {
        "utility_spearman": 0.20,
        "sign_agreement": 0.60,
        "normalized_huber_reduction": 0.20,
        "swap_sensitivity": 0.01,
        "positive_task_count": 2,
    }
    tiny = {key: value + 0.001 for key, value in trajectory.items()}
    assert not granularity_advantage(tiny, trajectory)["passed"]
    material = dict(trajectory)
    material["utility_spearman"] += 0.06
    material["sign_agreement"] += 0.06
    report = granularity_advantage(material, trajectory)
    assert report["passed"]
    assert report["advantage_count"] == 2
