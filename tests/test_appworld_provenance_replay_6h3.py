from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from rcmf.training.appworld_provenance_replay_6h3 import (
    build_quarantine_manifest,
    build_quarantine_sentinel,
    classify_provenance_failure,
    semantic_replay_gate,
    select_preflight_branch,
    source_query_layers_agree,
    summarize_corpus_identity,
    training_contamination_report,
)
from scripts.analyze_provenance_quarantine_sensitivity_6h3 import _without_task
from scripts.prepare_appworld_provenance_replay_6h3 import (
    _archive_member_hits,
    _redacted_line_context,
)
from scripts.run_appworld_provenance_replay_6h3 import (
    _checkpoint_index,
    _checkpoint_key,
)


def _state(state_id: str, task_id: str, step_id: int) -> dict:
    return {
        "state_example_id": state_id,
        "task_id": task_id,
        "state_task_id": task_id,
        "step_id": step_id,
    }


def test_corpus_identity_summary_is_task_unique_and_counts_mismatches() -> None:
    rows = [
        {"task_id": "a", "identity_match": True, "mismatched_fields": []},
        {
            "task_id": "b",
            "identity_match": False,
            "mismatched_fields": ["email", "phone_number"],
        },
    ]
    summary = summarize_corpus_identity(rows)
    assert summary["task_count"] == 2
    assert summary["identity_mismatch_task_ids"] == ["b"]
    assert summary["mismatch_field_counts"] == {"email": 1, "phone_number": 1}
    with pytest.raises(ValueError, match="unique"):
        summarize_corpus_identity([rows[0], rows[0]])


def test_source_query_layers_do_not_depend_on_external_source_file_visibility() -> None:
    query_hash = "a" * 64
    assert source_query_layers_agree(query_hash, [query_hash, query_hash])
    assert not source_query_layers_agree(query_hash, [])
    assert not source_query_layers_agree(query_hash, [query_hash, "b" * 64])


def test_failure_classification_separates_header_snapshot_join_and_mixing() -> None:
    common = {
        "source_layers_agree": True,
        "supervisor_only_mismatch": True,
        "exact_identity_matches_other_task": False,
        "source_identity_evidence_count": 0,
        "official_identity_evidence_count": 0,
        "mixed_identity_step_count": 0,
        "exact_snapshot_found": False,
    }
    assert classify_provenance_failure(**common) == "source_query_header_only_corruption"
    assert (
        classify_provenance_failure(**{**common, "exact_identity_matches_other_task": True})
        == "task_id_or_row_join_corruption"
    )
    assert (
        classify_provenance_failure(
            **{
                **common,
                "source_identity_evidence_count": 1,
                "official_identity_evidence_count": 1,
            }
        )
        == "mixed_source_environment_corruption"
    )
    assert (
        classify_provenance_failure(
            **{**common, "source_identity_evidence_count": 2}
        )
        == "source_snapshot_unrecoverable"
    )
    assert (
        classify_provenance_failure(
            **{
                **common,
                "source_identity_evidence_count": 2,
                "exact_snapshot_found": True,
            }
        )
        == "alternate_task_snapshot_used"
    )
    assert (
        classify_provenance_failure(
            **{
                **common,
                "official_identity_evidence_count": 3,
                "exact_snapshot_found": True,
            }
        )
        == "source_query_header_only_corruption"
    )


def test_training_contamination_requires_all_train_side_sources_clear() -> None:
    clean = training_contamination_report(
        task_id="heldout",
        train_task_ids=["train"],
        transition_parent_task_ids=["train"],
        train_label_task_ids=["train"],
        teacher_source_task_ids=["train"],
    )
    assert clean["heldout_only"]
    contaminated = training_contamination_report(
        task_id="heldout",
        train_task_ids=["train"],
        transition_parent_task_ids=["train"],
        train_label_task_ids=["train"],
        teacher_source_task_ids=["heldout"],
    )
    assert contaminated["contaminates_training"]
    assert contaminated["sources"]["teacher_source_memories"]


def test_whole_task_quarantine_has_no_replacement_and_preserves_order() -> None:
    rows = [
        _state("a1", "a", 2),
        _state("bad1", "b0a8eae_2", 6),
        _state("a2", "a", 4),
        _state("bad2", "b0a8eae_2", 7),
        _state("c1", "c", 1),
    ]
    manifest = build_quarantine_manifest(rows)
    assert [row["state_example_id"] for row in manifest["rows"]] == ["a1", "a2", "c1"]
    assert manifest["quarantined_state_ids"] == ["bad1", "bad2"]
    assert manifest["replacement_state_count"] == 0
    assert manifest["retained_prior_observation_count"] == 1 + 3 + 0


def test_quarantine_sentinel_removes_task_without_posthoc_replacement() -> None:
    sentinel = [
        _state("keep1", "a", 2),
        _state("bad", "b0a8eae_2", 7),
        _state("keep2", "c", 3),
    ]
    result = build_quarantine_sentinel(
        sentinel, retained_state_ids=["keep1", "keep2"]
    )
    assert result["state_count"] == 2
    assert result["replacement_state_count"] == 0
    assert [row["state_example_id"] for row in result["rows"]] == ["keep1", "keep2"]


def test_semantic_gate_requires_every_exact_count_and_repeat() -> None:
    summary = {
        "state_count": 12,
        "task_count": 8,
        "identity_match_count": 12,
        "complete_history_semantic_match_count": 12,
        "prior_observation_count": 96,
        "prior_semantic_match_count": 96,
        "target_semantic_match_count": 12,
        "complete_semantic_replay_count": 12,
        "exception_count": 0,
        "non_temporal_jwt_mismatch_count": 0,
        "non_token_mismatch_count": 0,
    }
    repeats = [{"semantic_repeat_match": True} for _ in range(12)]
    assert semantic_replay_gate(
        [summary, summary],
        expected_states=12,
        expected_tasks=8,
        expected_prior_observations=96,
        require_repeat_equivalence=True,
        repeat_checks=repeats,
    )["passed"]
    assert not semantic_replay_gate(
        [summary, {**summary, "prior_semantic_match_count": 95}],
        expected_states=12,
        expected_tasks=8,
        expected_prior_observations=96,
        require_repeat_equivalence=True,
        repeat_checks=repeats,
    )["passed"]


def test_preflight_branch_stops_on_multiple_mismatches_or_training_contamination() -> None:
    assert (
        select_preflight_branch(
            mismatch_task_count=2,
            exact_snapshot_found=False,
            training_contaminated=False,
        )
        == "source_dataset_identity_consistency_failure"
    )
    assert (
        select_preflight_branch(
            mismatch_task_count=1,
            exact_snapshot_found=False,
            training_contaminated=True,
        )
        == "provenance_invalid_task_contaminates_training"
    )
    assert (
        select_preflight_branch(
            mismatch_task_count=1,
            exact_snapshot_found=False,
            training_contaminated=False,
        )
        == "provenance_valid_task_quarantine_ready"
    )


def test_archive_search_is_bounded_and_reports_only_redacted_member_identity(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "transfer.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("private/task.json", '{"email":"source@example.com"}')
        handle.writestr("private/ignored.bin", b"source@example.com")
    hits, count, searched_bytes = _archive_member_hits(
        archive,
        query="full query",
        source_fields={
            "first_name": "First",
            "last_name": "Last",
            "email": "source@example.com",
            "phone_number": "123",
        },
        suffixes={".json"},
        maximum_bytes=1024,
    )
    assert count == 1
    assert searched_bytes > 0
    assert hits[0]["matching_component_names"] == ["email"]
    assert "private/task.json" not in json.dumps(hits)
    assert "source@example.com" not in json.dumps(hits)


def test_line_context_hashes_content_instead_of_exposing_it() -> None:
    rows = _redacted_line_context(
        "secret-one\nquery-secret\nsecret-three", start_line=2, end_line=2, radius=1
    )
    assert [row["line"] for row in rows] == [1, 2, 3]
    assert "secret" not in json.dumps(rows)
    assert rows[1]["inside_query_span"]


def test_checkpoint_index_is_atomic_resume_authority_and_keyed_by_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    assert _checkpoint_index(path)["rows"] == {}
    payload = {
        "format": "appworld_provenance_replay_checkpoint_index_6h3_v1",
        "rows": {"key": {"result_sha256": "abc"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _checkpoint_index(path) == payload
    assert _checkpoint_key("sentinel", 2, "state") == "sentinel:repeat_2:state"


def test_quarantine_sensitivity_removes_every_row_for_the_task() -> None:
    rows = [
        {"state_task_id": "a", "pair_id": "1"},
        {"state_task_id": "b0a8eae_2", "pair_id": "2"},
        {"state_task_id": "b0a8eae_2", "pair_id": "3"},
    ]
    assert _without_task(rows, "b0a8eae_2") == [rows[0]]


def test_scope_files_are_qwen_free_and_do_not_edit_identity_inputs() -> None:
    paths = [
        Path("scripts/prepare_appworld_provenance_replay_6h3.py"),
        Path("scripts/run_appworld_provenance_replay_6h3.py"),
        Path("scripts/analyze_provenance_quarantine_sensitivity_6h3.py"),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "transformers" not in source
        assert "from_pretrained" not in source
        assert "world.execute" not in source
    config = Path(
        "configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"
    ).read_text(encoding="utf-8")
    assert "no_qwen_import_forward_or_generation" in config
    assert "no_memory_condition_execution" in config
    assert "b0a8eae_2" in config
