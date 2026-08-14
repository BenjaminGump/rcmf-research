from __future__ import annotations

import json

from scripts.validate_transition_memory_6a import (
    _response_cache_checks,
    _validate_teacher_rows,
)


def test_teacher_validator_preserves_over_context_as_missing() -> None:
    preflight = [
        {"pair_id": "scored", "over_context": False},
        {"pair_id": "missing", "over_context": True},
    ]
    rows = [
        {
            "pair_id": "scored",
            "leakage_overlap": [],
            "truncated": False,
            "score_status": "scored",
            "valid_for_loss": True,
            "L0": 0.5,
            "Lj_transition": 0.2,
            "text_utility": 0.3,
        },
        {
            "pair_id": "missing",
            "leakage_overlap": [],
            "truncated": False,
            "score_status": "over_context",
            "valid_for_loss": False,
            "L0": 0.5,
            "Lj_transition": None,
            "text_utility": None,
        },
    ]
    assert _validate_teacher_rows(rows, preflight) == []
    rows[1]["text_utility"] = 0.0
    assert "invalid over-context teacher row: missing" in _validate_teacher_rows(
        rows, preflight
    )


def test_response_cache_validator_detects_duplicates_and_raw_memory(tmp_path) -> None:
    path = tmp_path / "response.jsonl"
    row = {
        "pair_id": "pair",
        "truncated": False,
        "student_prompt_contains_raw_memory": False,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert _response_cache_checks(path, expected_count=1)["passed"]
    bad = {**row, "student_prompt_contains_raw_memory": True}
    path.write_text(json.dumps(row) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    report = _response_cache_checks(path)
    assert not report["passed"]
    assert len(report["errors"]) == 2
