from __future__ import annotations

import json
from pathlib import Path

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.student_labels import (
    FULL_TEACHER_CACHE_VERSION,
    FULL_TEACHER_SCORING_DEFINITION,
    SPECIAL_MEMORY_ID,
    compile_stage_b_student_labels,
    example_id,
    pair_key,
)
from rcmf.utils.serialization import atomic_write_json, write_jsonl


def _example(task: str, index: int) -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id=f"appworld:trace:{task}",
        step_id=index + 1,
        state_text=f"state for {task}",
        target_text="apis.foo.bar()",
        target_type="code",
        candidate_memory_ids=None,
        metadata={"task_id": task},
    )


def _record(memory_id: str, task: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="appworld",
        episode_id=f"appworld:trace:{task}",
        task_id=task,
        raw_trajectory={},
        experience_text=f"memory for {task}",
        outcome=1.0,
        success=True,
        metadata={},
    )


def _teacher_row(example_index: int, memory_index: int, utility: float | None) -> dict:
    over_context = utility is None
    return {
        "format": FULL_TEACHER_CACHE_VERSION,
        "pair_key": pair_key(example_index, memory_index),
        "example_index": example_index,
        "candidate_memory_index": memory_index,
        "score_status": "over_context" if over_context else "scored",
        "valid_for_loss": not over_context,
        "L0": None if over_context else 1.0,
        "Lj_text": None if over_context else 1.0 - float(utility),
        "text_utility": utility,
        "target_sha256": "target",
        "memory_text_sha256": "memory",
    }


def test_stage_b_compiler_enforces_inductive_memory_split_and_special_mask(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    teacher_dir = tmp_path / "teacher"
    data_dir.mkdir()
    teacher_dir.mkdir()
    examples = [_example("train_a", 0), _example("train_b", 1), _example("val_c", 2)]
    records = [
        _record("mem-a", "train_a"),
        _record(SPECIAL_MEMORY_ID, "train_b"),
        _record("mem-val", "val_c"),
    ]
    write_jsonl(data_dir / "decision_examples.jsonl", [example.to_dict() for example in examples])
    write_jsonl(data_dir / "memory_records.jsonl", [record.to_dict() for record in records])
    atomic_write_json(
        teacher_dir / "summary.json",
        {
            "cache_version": FULL_TEACHER_CACHE_VERSION,
            "scoring_definition": FULL_TEACHER_SCORING_DEFINITION,
            "source_commit": "abc",
        },
    )
    atomic_write_json(
        teacher_dir / "student_split_manifest.json",
        {
            "seed": 13,
            "train_task_ids": ["train_a", "train_b"],
            "validation_task_ids": ["val_c"],
            "train_example_ids": [example_id(0, examples[0]), example_id(1, examples[1])],
            "validation_example_ids": [example_id(2, examples[2])],
        },
    )
    # The special memory is over-context for every train-state row, so it must be
    # kept in the ledger but removed from the effective Stage-B bank.
    write_jsonl(
        teacher_dir / "teacher_cache_full_rows.jsonl",
        [
            _teacher_row(0, 1, None),
            _teacher_row(1, 0, 0.2),
            _teacher_row(2, 0, -0.1),
            _teacher_row(2, 1, None),
        ],
    )

    compiled = compile_stage_b_student_labels(
        examples=examples,
        records=records,
        teacher_cache_jsonl=teacher_dir / "teacher_cache_full_rows.jsonl",
        teacher_summary_json=teacher_dir / "summary.json",
        split_manifest_json=teacher_dir / "student_split_manifest.json",
        data_dir=data_dir,
    )

    assert compiled.validation["passed"]
    assert [row["memory_id"] for row in compiled.memory_bank] == ["mem-a"]
    special = compiled.summary["special_memory"]
    assert special["memory_id"] == SPECIAL_MEMORY_ID
    assert special["eligible_for_stage_b"] is False
    assert special["exclusion_reason"] == "special_memory_zero_valid_stage_b_train_labels"
    excluded_ids = {row["memory_id"] for row in compiled.summary["excluded_memories"]}
    assert "mem-val" in excluded_ids
    validation_row = [row for row in compiled.rows if row["split"] == "validation"][0]
    assert validation_row["ordered_effective_memory_ids"] == ["mem-a"]
    assert validation_row["valid_mask"] == [True]


def test_no_positive_is_distinct_from_all_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    teacher_dir = tmp_path / "teacher"
    data_dir.mkdir()
    teacher_dir.mkdir()
    examples = [_example("train_a", 0), _example("train_b", 1)]
    records = [_record("mem-a", "train_a"), _record("mem-b", "train_b")]
    write_jsonl(data_dir / "decision_examples.jsonl", [example.to_dict() for example in examples])
    write_jsonl(data_dir / "memory_records.jsonl", [record.to_dict() for record in records])
    atomic_write_json(
        teacher_dir / "summary.json",
        {"cache_version": FULL_TEACHER_CACHE_VERSION, "scoring_definition": FULL_TEACHER_SCORING_DEFINITION},
    )
    atomic_write_json(
        teacher_dir / "student_split_manifest.json",
        {
            "seed": 13,
            "train_task_ids": ["train_a"],
            "validation_task_ids": ["train_b"],
            "train_example_ids": [example_id(0, examples[0])],
            "validation_example_ids": [example_id(1, examples[1])],
        },
    )
    write_jsonl(teacher_dir / "teacher_cache_full_rows.jsonl", [_teacher_row(1, 0, -0.2)])

    compiled = compile_stage_b_student_labels(
        examples=examples,
        records=records,
        teacher_cache_jsonl=teacher_dir / "teacher_cache_full_rows.jsonl",
        teacher_summary_json=teacher_dir / "summary.json",
        split_manifest_json=teacher_dir / "student_split_manifest.json",
        data_dir=data_dir,
    )

    train_row = [row for row in compiled.rows if row["split"] == "train"][0]
    validation_row = [row for row in compiled.rows if row["split"] == "validation"][0]
    assert train_row["all_missing_state"] is True
    assert train_row["no_positive_state"] is False
    assert validation_row["all_missing_state"] is False
    assert validation_row["no_positive_state"] is True
