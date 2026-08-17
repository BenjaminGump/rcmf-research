from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.training import appworld_semantic_replay_6h2 as semantic_v2
from rcmf.training.appworld_identity_reconciliation_7a import (
    HEADER_ONLY_CLASSIFICATION,
    QUARANTINE_ACTION,
    REPAIR_ACTION,
    audit_corpus_builder_hypotheses,
    build_reconciled_audit_manifest,
    classify_behavioral_provenance,
    classify_dependency,
    select_corpus_decision_branch,
    select_task_remediation,
    task_replay_gate,
    validate_repaired_payload,
    write_jsonl_with_line_replacements,
)
from rcmf.training.appworld_legacy_replay_6h1 import (
    canonical_hash as legacy_canonical_hash,
    upgrade_replay_contract,
)
from scripts.appworld_semantic_replay_bridge_6h2 import collect_token_pairs
from scripts.prepare_appworld_official_traces import load_task_query
from scripts.finalize_appworld_identity_reconciliation_7a import (
    PAIR_5D_RESPONSE_CACHE_PATH,
    STAGE_C1_RESPONSE_CACHE_PATH,
    _reconcile_legacy_contract_query,
)
from scripts.run_appworld_identity_reconciliation_7a import (
    CHECKPOINT_VERSION,
    _checkpoint_index,
    _checkpoint_contract_matches_base,
)


def _builder_row(task_id: str, source: str, active: str, official: str) -> dict:
    return {
        "task_id": task_id,
        "source_query_sha256": source,
        "active_spec_query_sha256": active,
        "official_spec_query_sha256": official,
        "source_matches_other_official_task_ids": [],
        "source_matches_same_base_other_suffix": False,
    }


def test_builder_requires_exact_explicit_task_suffix_and_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "data"
    for suffix, first in (("_2", "Two"), ("_3", "Three")):
        path = root / "tasks" / f"task{suffix}" / "specs.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "instruction": "Do the task",
                    "supervisor": {
                        "first_name": first,
                        "last_name": "Person",
                        "email": f"{first.lower()}@example.com",
                        "phone_number": "123",
                    },
                }
            ),
            encoding="utf-8",
        )
    query, provenance = load_task_query(
        "task_3", "full_demo", task_spec_root=root
    )
    assert "Three Person" in query
    assert "Two Person" not in query
    assert len(provenance["task_spec_sha256"]) == 64
    with pytest.raises(FileNotFoundError, match="pinned task spec"):
        load_task_query("task", "full_demo", task_spec_root=root)


def test_builder_root_cause_requires_all_headers_to_match_active_snapshot() -> None:
    rows = [
        _builder_row("good", "a", "a", "a"),
        _builder_row("b0a8eae_2", "b", "b", "x"),
        _builder_row("b0a8eae_3", "c", "c", "y"),
    ]
    audit = audit_corpus_builder_hypotheses(rows)
    assert audit["exact_root_cause_reproduced"]
    assert audit["hypotheses"]["unpinned_active_task_snapshot_lookup"]
    rows[0]["active_spec_query_sha256"] = "other"
    assert not audit_corpus_builder_hypotheses(rows)["exact_root_cause_reproduced"]


def test_header_only_classification_is_strict() -> None:
    common = {
        "task_and_instruction_match": True,
        "source_identity_evidence_count": 0,
        "official_identity_evidence_count": 8,
        "third_identity_evidence_count": 0,
        "mixed_identity_step_count": 0,
        "account_or_database_mixing": False,
    }
    assert classify_behavioral_provenance(**common) == HEADER_ONLY_CLASSIFICATION
    assert (
        classify_behavioral_provenance(
            **{**common, "source_identity_evidence_count": 1}
        )
        == "mixed_source_environment_corruption"
    )
    assert (
        classify_behavioral_provenance(
            **{**common, "official_identity_evidence_count": 0}
        )
        == "source_snapshot_unrecoverable"
    )


def test_repair_policy_requires_complete_replay_and_unchanged_behavior() -> None:
    assert (
        select_task_remediation(
            classification=HEADER_ONLY_CLASSIFICATION,
            replay_gate_passed=True,
            actions_unchanged=True,
            observations_unchanged=True,
        )
        == REPAIR_ACTION
    )
    assert (
        select_task_remediation(
            classification=HEADER_ONLY_CLASSIFICATION,
            replay_gate_passed=False,
            actions_unchanged=True,
            observations_unchanged=True,
        )
        == QUARANTINE_ACTION
    )


def test_task_replay_gate_is_all_or_nothing() -> None:
    summary = {
        "state_count": 17,
        "identity_match_count": 17,
        "complete_history_semantic_match_count": 17,
        "prior_observation_count": 136,
        "prior_semantic_match_count": 136,
        "target_semantic_match_count": 17,
        "complete_semantic_replay_count": 17,
        "non_temporal_jwt_mismatch_count": 0,
        "non_token_mismatch_count": 0,
        "exception_count": 0,
    }
    assert task_replay_gate(
        summary, expected_states=17, expected_prior_observations=136
    )["passed"]
    summary["target_semantic_match_count"] = 16
    assert not task_replay_gate(
        summary, expected_states=17, expected_prior_observations=136
    )["passed"]


def test_jsonl_replacement_preserves_unaffected_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"a": 1}\r\n{"a": 2}\n{"a": 3}\n')
    output = tmp_path / "output.jsonl"
    report = write_jsonl_with_line_replacements(source, output, {2: {"a": 20}})
    lines = output.read_bytes().splitlines(keepends=True)
    assert lines[0] == b'{"a": 1}\r\n'
    assert lines[2] == b'{"a": 3}\n'
    assert json.loads(lines[1]) == {"a": 20}
    assert report["replaced_lines"] == [2]


def test_repaired_payload_changes_only_query_derived_content() -> None:
    original = {
        "task_id": "task",
        "memory_id": "memory",
        "raw_trajectory": {
            "query": "bad",
            "steps": [{"step_id": 1, "response": "code", "observation": "obs"}],
        },
    }
    corrected = json.loads(json.dumps(original))
    corrected["raw_trajectory"]["query"] = "official"
    assert validate_repaired_payload(original, corrected)["passed"]
    corrected["raw_trajectory"]["steps"][0]["observation"] = "changed"
    assert not validate_repaired_payload(original, corrected)["passed"]


def test_whole_task_manifest_policy_never_adds_replacements() -> None:
    rows = [
        {"state_example_id": "a", "task_id": "b0a8eae_2", "step_id": 2},
        {"state_example_id": "b", "task_id": "other", "step_id": 3},
    ]
    manifest = build_reconciled_audit_manifest(
        rows,
        {"b0a8eae_2": QUARANTINE_ACTION, "b0a8eae_3": REPAIR_ACTION},
    )
    assert manifest["state_count"] == 1
    assert manifest["replacement_state_count"] == 0
    assert manifest["rows"][0]["state_example_id"] == "b"


def test_corpus_branch_and_dependency_classification() -> None:
    assert (
        select_corpus_decision_branch(
            {"b0a8eae_2": REPAIR_ACTION, "b0a8eae_3": REPAIR_ACTION},
            structural_validation_passed=True,
        )
        == "identity_reconciled_46_task_corpus_ready"
    )
    assert (
        classify_dependency(
            has_invalid_evaluation_rows=False,
            has_invalid_training_rows=True,
            is_checkpoint=True,
            is_cache=False,
            is_report=False,
        )
        == "model_retraining_required"
    )


def test_dependency_audit_uses_materialized_response_cache_filenames() -> None:
    assert STAGE_C1_RESPONSE_CACHE_PATH.name == "response_cache.jsonl"
    assert PAIR_5D_RESPONSE_CACHE_PATH.name == "pair_response_cache.jsonl"


def test_exp025a_scope_is_qwen_and_gpu_free() -> None:
    paths = [
        Path("rcmf/training/appworld_identity_reconciliation_7a.py"),
        Path("scripts/prepare_appworld_official_traces.py"),
        Path("scripts/prepare_appworld_identity_reconciliation_7a.py"),
        Path("scripts/run_appworld_identity_reconciliation_7a.py"),
        Path("scripts/finalize_appworld_identity_reconciliation_7a.py"),
        Path("scripts/analyze_contaminated_checkpoint_sensitivity_7a.py"),
        Path("scripts/analyze_appworld_identity_reconciliation_7a.py"),
        Path("scripts/validate_appworld_identity_reconciliation_7a.py"),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "from_pretrained" not in source
        assert "transformers" not in source
        assert "cuda" not in source.lower()


def test_replay_checkpoint_is_the_resume_authority(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    assert _checkpoint_index(path) == {"format": CHECKPOINT_VERSION, "rows": {}}
    payload = {
        "format": CHECKPOINT_VERSION,
        "rows": {
            "affected:repeat_1:state": {
                "state_example_id": "state",
                "result_sha256": "abc",
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _checkpoint_index(path) == payload


def test_replay_bridge_skips_access_token_schema_placeholders() -> None:
    expected = {"response_schema": {"access_token": "string"}}
    actual = {"response_schema": {"access_token": "string"}}
    assert collect_token_pairs(expected, actual, semantic=semantic_v2) == []


def test_reconciled_query_survives_legacy_v1_contract_upgrade() -> None:
    legacy = {
        "format": "appworld_legacy_replay_contract_6h1_v1",
        "state_example_id": "state",
        "task_id": "task",
        "target_step": 1,
        "history_step_count": 0,
        "expected_task_instruction": "stale query",
        "normalization_version": "appworld_observation_normalization_6h_v1",
        "legacy_python": "/legacy/python",
        "appworld_root": "/legacy/root",
        "experiment_name": "test",
        "random_seed": 1,
        "max_interactions": 10,
        "max_api_calls_per_interaction": 10,
        "source_hashes": {},
        "actions": [
            {
                "step_id": 1,
                "is_target": True,
                "code": "print('ok')",
                "response_sha256": "a" * 64,
                "expected_observation": "ok",
                "expected_observation_sha256": "b" * 64,
            }
        ],
    }
    legacy["actions_sha256"] = legacy_canonical_hash(legacy["actions"])
    reconciled = _reconcile_legacy_contract_query(legacy, "official query")
    assert reconciled["expected_task_query"] == "official query"
    assert "expected_task_instruction" not in reconciled


def test_replay_checkpoint_rejects_changed_source_contract() -> None:
    base = {
        "format": "appworld_legacy_replay_contract_6h1_v1",
        "state_example_id": "state",
        "task_id": "task",
        "target_step": 1,
        "history_step_count": 0,
        "expected_task_instruction": "query-a",
        "normalization_version": "appworld_observation_normalization_6h_v1",
        "legacy_python": "/legacy/python",
        "appworld_root": "/legacy/root",
        "experiment_name": "test",
        "random_seed": 1,
        "max_interactions": 10,
        "max_api_calls_per_interaction": 10,
        "source_hashes": {},
        "actions": [
            {
                "step_id": 1,
                "is_target": True,
                "code": "print('ok')",
                "response_sha256": "a" * 64,
                "expected_observation": "ok",
                "expected_observation_sha256": "b" * 64,
            }
        ],
    }
    base["actions_sha256"] = legacy_canonical_hash(base["actions"])
    generated = {
        "source_contract_sha256": semantic_v2.canonical_hash(
            upgrade_replay_contract(base)
        )
    }
    assert _checkpoint_contract_matches_base(generated, base)
    changed = {**base, "expected_task_instruction": "query-b"}
    assert not _checkpoint_contract_matches_base(generated, changed)
