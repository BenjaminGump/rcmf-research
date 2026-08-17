from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.clean_cache_rebuild_7b import (
    affected_reasons,
    audit_jsonl_cache,
    source_identity_audit,
)


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
