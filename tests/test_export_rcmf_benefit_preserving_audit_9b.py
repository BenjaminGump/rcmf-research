from scripts.export_rcmf_benefit_preserving_audit_9b import (
    _attempt_summary,
    _first_code_divergence,
)


def _task(*codes):
    return {"steps": [{"exact_executed_code": code} for code in codes]}


def test_first_code_divergence_detects_content_and_length():
    assert _first_code_divergence(_task("a", "b"), _task("a", "c")) == 2
    assert _first_code_divergence(_task("a"), _task("a", "b")) == 2
    assert _first_code_divergence(_task("a", "b"), _task("a", "b")) is None


def test_attempt_summary_requires_closed_pairs():
    rows = [
        {
            "attempt_id": "a",
            "event": "start",
            "start_timestamp_utc": "2026-08-27T00:00:00Z",
        },
        {
            "attempt_id": "a",
            "event": "end",
            "end_timestamp_utc": "2026-08-27T00:00:03Z",
            "exit_code": 0,
        },
        {
            "attempt_id": "b",
            "event": "start",
            "start_timestamp_utc": "2026-08-27T00:00:00Z",
        },
    ]
    summary = _attempt_summary(rows)
    assert summary["attempt_count"] == 2
    assert summary["all_attempts_closed"] is False
    assert summary["duration_seconds_by_attempt"] == {"a": 3.0}
    assert summary["failed_attempt_ids"] == ["b"]
