from __future__ import annotations

from scripts.validate_interaction_representation_6c import (
    validate_attempt_events,
    validate_learning_manifest,
)


def _learning_rows() -> list[dict[str, str]]:
    return [
        {
            "pair_id": f"{task}:{parent}",
            "state_task_id": task,
            "transition_parent_id": parent,
        }
        for task in ("t1", "t2", "t3", "t4")
        for parent in ("p1", "p2")
    ]


def test_attempt_validation_accepts_failed_then_resumed_attempts() -> None:
    rows = [
        {"event": "start", "attempt_id": "a1", "run_uuid": "run"},
        {
            "event": "end",
            "attempt_id": "a1",
            "run_uuid": "run",
            "exit_code": 1,
        },
        {"event": "start", "attempt_id": "a2", "run_uuid": "run"},
        {
            "event": "end",
            "attempt_id": "a2",
            "run_uuid": "run",
            "exit_code": 0,
        },
    ]
    report = validate_attempt_events(rows, expected_run_uuid="run")
    assert report["passed"]
    assert report["failed_attempt_ids"] == ["a1"]
    assert report["completed_attempt_ids"] == ["a2"]


def test_attempt_validation_rejects_unpaired_attempt() -> None:
    report = validate_attempt_events(
        [{"event": "start", "attempt_id": "a1", "run_uuid": "run"}],
        expected_run_uuid="run",
    )
    assert not report["passed"]
    assert any("attempt_start_end_mismatch" in value for value in report["errors"])


def test_learning_manifest_validates_nested_tasks_and_parent_coverage() -> None:
    rows = _learning_rows()
    levels = []
    for count, tasks in ((1, ["t1"]), (2, ["t1", "t2"]), (4, ["t1", "t2", "t3", "t4"])):
        selected = [row for row in rows if row["state_task_id"] in tasks]
        import hashlib

        levels.append(
            {
                "task_count": count,
                "task_ids": tasks,
                "pair_count": len(selected),
                "pair_ids_sha256": hashlib.sha256(
                    "\n".join(sorted(row["pair_id"] for row in selected)).encode()
                ).hexdigest(),
                "all_parent_coverage": True,
            }
        )
    manifest = {
        "fold_count": 5,
        "task_counts": [4, 8, 12],
        "folds": [{"fold": fold, "levels": levels} for fold in range(5)],
    }
    # The validator requires the production counts in the manifest header, while
    # the synthetic levels exercise the nesting/hash/parent invariants.
    assert validate_learning_manifest(manifest, rows) == []


def test_learning_manifest_rejects_missing_parent_coverage() -> None:
    rows = _learning_rows()
    manifest = {
        "fold_count": 5,
        "task_counts": [4, 8, 12],
        "folds": [
            {
                "fold": fold,
                "levels": [
                    {
                        "task_count": count,
                        "task_ids": ["t1"] if count == 1 else ["t1", "t2"],
                        "pair_count": 0,
                        "pair_ids_sha256": "bad",
                        "all_parent_coverage": False,
                    }
                    for count in (1, 2, 4)
                ],
            }
            for fold in range(5)
        ],
    }
    errors = validate_learning_manifest(manifest, rows)
    assert any("learning_parent_coverage" in value for value in errors)
