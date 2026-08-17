from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.clean_cache_rebuild_7b import (
    affected_reasons,
    audit_jsonl_cache,
    source_identity_audit,
    transition_change_manifest,
)
from rcmf.training.clean_cache_execution_7b import (
    _selected_pair_matches_old,
    seed_pair_response,
    seed_jsonl,
    transition_representation_work_queue,
)
from rcmf.utils.serialization import read_jsonl


def _example(task: str, state: str = "state") -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id=f"appworld:trace:{task}",
        step_id=1,
        state_text=state,
        target_text="print('ok')",
        target_type="code",
        candidate_memory_ids=[],
        metadata={"task_id": task},
    )


def _record(task: str, text: str = "memory") -> MemoryRecord:
    return MemoryRecord(
        memory_id=f"memory-{task}", benchmark="appworld", task_id=task,
        episode_id=f"appworld:trace:{task}", experience_text=text,
        outcome="success", success=True, raw_trajectory={}, metadata={},
    )


def test_affected_reason_uses_query_and_both_reconciled_memories() -> None:
    memories = {"memory-b0a8eae_2", "memory-b0a8eae_3"}
    reasons = affected_reasons(
        "raw_text_teacher",
        {"task_id": "clean", "candidate_memory_id": "memory-b0a8eae_2"},
        affected_memory_ids=memories,
        affected_old_transition_ids=set(),
    )
    assert reasons == ["reconciled_memory_record"]
    reasons = affected_reasons(
        "pair_response_5d",
        {"task_id": "b0a8eae_3", "memory_id": "other"},
        affected_memory_ids=memories,
        affected_old_transition_ids=set(),
    )
    assert reasons == ["reconciled_query_state"]


def test_transition_invalidation_tracks_superseded_transition() -> None:
    reasons = affected_reasons(
        "transition_teacher",
        {"task_id": "clean", "transition_id": "old", "parent_task_id": "b0a8eae_3"},
        affected_memory_ids=set(),
        affected_old_transition_ids={"old"},
    )
    assert reasons == ["superseded_transition"]


def test_source_identity_requires_only_preregistered_tasks_to_change() -> None:
    old_examples = [_example("clean"), _example("b0a8eae_2", "old")]
    clean_examples = [_example("clean"), _example("b0a8eae_2", "new")]
    old_records = [_record("clean"), _record("b0a8eae_3", "old")]
    clean_records = [_record("clean"), _record("b0a8eae_3", "new")]
    report = source_identity_audit(
        old_examples=old_examples, clean_examples=clean_examples,
        old_records=old_records, clean_records=clean_records,
    )
    assert report["changed_decision_count"] == 1
    assert report["changed_memory_count"] == 1
    bad_clean = [_example("clean", "changed"), clean_examples[1]]
    with pytest.raises(ValueError, match="outside the two preregistered tasks"):
        source_identity_audit(
            old_examples=old_examples, clean_examples=bad_clean,
            old_records=old_records, clean_records=clean_records,
        )


def test_cache_audit_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    row = {"pair_key": "same", "task_id": "clean", "candidate_memory_id": "other"}
    path.write_text("\n".join(json.dumps(row) for _ in range(2)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        audit_jsonl_cache(
            cache_name="raw_text_teacher", path=path,
            affected_memory_ids=set(), affected_old_transition_ids=set(),
        )


def test_transition_change_allows_only_documented_qwen_field_omissions() -> None:
    shared = {
        "parent_task_id": "clean",
        "step_index": 1,
        "transition_id": "same",
        "transition_content_sha256": "content",
    }
    old = {**shared, "teacher_section_tokens": 12, "tokenizer_name_or_path": "Qwen/Qwen3-8B"}
    report = transition_change_manifest(old_transitions=[old], clean_transitions=[shared])
    assert report["changed_transition_count"] == 0
    assert report["unaffected_transition_structural_fields_identical"]
    assert report[
        "qwen_derived_old_fields_intentionally_absent_from_clean_structural_manifest"
    ] == ["teacher_section_tokens", "tokenizer_name_or_path"]
    with pytest.raises(ValueError, match="schema changed unexpectedly"):
        transition_change_manifest(
            old_transitions=[{**shared, "undocumented": 1}],
            clean_transitions=[shared],
        )


def test_seed_jsonl_is_idempotent_and_preserves_completed_rows(tmp_path: Path) -> None:
    output = tmp_path / "cache.jsonl"
    output.write_text(json.dumps({"id": "affected", "value": 7}) + "\n", encoding="utf-8")
    reusable = [{"id": "a", "value": 1}, {"id": "b", "value": 2}]
    expected = {"affected", "a", "b"}
    first = seed_jsonl(
        output_path=output,
        reusable_rows=reusable,
        expected_keys=expected,
        key_fn=lambda row: str(row["id"]),
    )
    second = seed_jsonl(
        output_path=output,
        reusable_rows=reusable,
        expected_keys=expected,
        key_fn=lambda row: str(row["id"]),
    )
    rows = list(read_jsonl(output))
    assert len(rows) == 3
    assert rows[0] == {"id": "affected", "value": 7}
    assert first["appended_row_count"] == 2
    assert second["appended_row_count"] == 0


def test_seed_jsonl_rejects_changed_reusable_row(tmp_path: Path) -> None:
    output = tmp_path / "cache.jsonl"
    output.write_text(json.dumps({"id": "a", "value": 9}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Reusable row changed"):
        seed_jsonl(
            output_path=output,
            reusable_rows=[{"id": "a", "value": 1}],
            expected_keys={"a"},
            key_fn=lambda row: str(row["id"]),
        )


def test_pair_response_reuse_maps_raw_utility_to_legacy_text_utility() -> None:
    selected = {
        "pair_id": "pair",
        "state_example_id": "state",
        "task_id": "clean-task",
        "memory_id": "memory",
        "memory_task_id": "clean-memory-task",
        "selection_category": "positive",
        "utility_category": "positive",
        "L0": 0.5,
        "raw_utility": 0.25,
        "memory_text_sha256": "memory-hash",
    }
    legacy = {**selected, "text_utility": selected["raw_utility"]}
    legacy.pop("raw_utility")
    assert _selected_pair_matches_old(selected, legacy)
    legacy["text_utility"] = 0.2
    assert not _selected_pair_matches_old(selected, legacy)


def test_pair_response_reuse_rejects_identity_change() -> None:
    selected = {
        "pair_id": "pair",
        "state_example_id": "state",
        "task_id": "clean-task",
        "memory_id": "memory",
        "memory_task_id": "clean-memory-task",
        "selection_category": "positive",
        "utility_category": "positive",
        "L0": 0.5,
        "raw_utility": 0.25,
        "memory_text_sha256": "memory-hash",
    }
    legacy = {**selected, "text_utility": selected["raw_utility"]}
    legacy.pop("raw_utility")
    legacy["memory_id"] = "other-memory"
    assert not _selected_pair_matches_old(selected, legacy)


def test_pair_response_resume_allows_reconciled_rows_only_when_explicit() -> None:
    selected = {
        "pair_id": "pair",
        "state_example_id": "state",
        "task_id": "b0a8eae_3",
        "memory_id": "memory",
        "memory_task_id": "clean-memory-task",
        "selection_category": "positive",
        "utility_category": "positive",
        "L0": 0.5,
        "raw_utility": 0.25,
        "memory_text_sha256": "memory-hash",
    }
    completed = {**selected, "text_utility": selected["raw_utility"]}
    completed.pop("raw_utility")
    assert not _selected_pair_matches_old(selected, completed)
    assert _selected_pair_matches_old(
        selected, completed, allow_reconciled_tasks=True
    )


def test_pair_response_seed_restores_legacy_row_over_recomputed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = {
        "pair_id": "pair",
        "state_example_id": "state",
        "task_id": "clean-task",
        "memory_id": "memory",
        "memory_task_id": "clean-memory-task",
        "selection_category": "positive",
        "utility_category": "positive",
        "L0": 0.5,
        "raw_utility": 0.25,
        "memory_text_sha256": "memory-hash",
    }
    legacy = {**selected, "text_utility": selected["raw_utility"], "cache_source": "old"}
    legacy.pop("raw_utility")
    recomputed = {**legacy, "cache_source": "clean", "corpus_lineage_sha256": "lineage"}
    old_path = tmp_path / "old.jsonl"
    old_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "student_labels.jsonl").write_text("{}\n", encoding="utf-8")
    (labels_dir / "effective_memory_bank.jsonl").write_text("{}\n", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "pair_response_cache.jsonl").write_text(
        json.dumps(recomputed) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "rcmf.training.clean_cache_execution_7b.select_stratified_pair_set",
        lambda *_args, **_kwargs: ([selected], {"selected_pair_count": 1}),
    )

    report = seed_pair_response(
        labels_dir=labels_dir, old_path=old_path, output_dir=output_dir
    )

    assert list(read_jsonl(output_dir / "pair_response_cache.jsonl")) == [legacy]
    assert report["legacy_reusable_row_count"] == 1
    assert report["replaced_existing_with_legacy_count"] == 1
    assert report["cascade_recompute_count"] == 0
    assert report["remaining_recompute_count"] == 0


def test_transition_representation_queue_includes_rows_outside_model_panel() -> None:
    mapping = [
        {
            "old_transition_id": f"old-{index}",
            "clean_transition_id": f"clean-{index}",
            "parent_task_id": "b0a8eae_3",
            "step_index": index,
        }
        for index in range(17)
    ]
    clean = [
        {
            "transition_id": f"clean-{index}",
            "parent_task_id": "b0a8eae_3",
            "step_index": index,
        }
        for index in range(17)
    ]
    panel_ids = {"clean-1", "clean-4", "clean-8", "clean-12"}
    queue = transition_representation_work_queue(
        transition_mapping=mapping, clean_transitions=clean
    )
    assert len(queue) == 17
    assert sum(row["transition_id"] in panel_ids for row in queue) == 4
