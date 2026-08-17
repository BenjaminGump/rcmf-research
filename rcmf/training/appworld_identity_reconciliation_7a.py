from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash


IDENTITY_RECONCILIATION_VERSION = "appworld_identity_reconciliation_7a_v1"
RECONCILED_CORPUS_VERSION = "appworld_successful_trajectory_identity_reconciled_v1"
AFFECTED_TASK_IDS = ("b0a8eae_2", "b0a8eae_3")
REPAIR_ACTION = "repair_query_header_to_official_metadata"
QUARANTINE_ACTION = "quarantine_entire_task"
HEADER_ONLY_CLASSIFICATION = "source_query_header_only_corruption"


def text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def audit_corpus_builder_hypotheses(
    rows: Sequence[Mapping[str, Any]],
    *,
    affected_task_ids: Sequence[str] = AFFECTED_TASK_IDS,
) -> dict[str, Any]:
    """Identify the only supported query-header construction mechanism."""
    if not rows:
        raise ValueError("Builder audit requires task rows")
    by_task = {str(row["task_id"]): row for row in rows}
    if len(by_task) != len(rows):
        raise ValueError("Builder audit task IDs must be unique")
    affected = [by_task[str(task_id)] for task_id in affected_task_ids]
    all_source_match_active = all(
        str(row["source_query_sha256"]) == str(row["active_spec_query_sha256"])
        for row in rows
    )
    affected_active_differs_official = all(
        str(row["active_spec_query_sha256"])
        != str(row["official_spec_query_sha256"])
        for row in affected
    )
    unaffected_active_matches_official = all(
        str(row["active_spec_query_sha256"])
        == str(row["official_spec_query_sha256"])
        for row in rows
        if str(row["task_id"]) not in set(map(str, affected_task_ids))
    )
    matches_other_official = any(
        bool(row.get("source_matches_other_official_task_ids")) for row in affected
    )
    exact_reproduction = bool(
        all_source_match_active
        and affected_active_differs_official
        and unaffected_active_matches_official
        and not matches_other_official
    )
    hypotheses = {
        "positional_or_row_offset_join": not matches_other_official and False,
        "base_id_suffix_ignored": any(
            bool(row.get("source_matches_same_base_other_suffix")) for row in affected
        ),
        "stale_metadata_cache": False,
        "source_file_concatenation_boundary_error": False,
        "adjacent_trajectory_header_copy": matches_other_official,
        "unpinned_active_task_snapshot_lookup": exact_reproduction,
    }
    return {
        "format": "appworld_corpus_builder_root_cause_7a_v1",
        "task_count": len(rows),
        "affected_task_ids": sorted(map(str, affected_task_ids)),
        "all_source_headers_match_active_snapshot": all_source_match_active,
        "affected_active_snapshot_differs_from_official": affected_active_differs_official,
        "unaffected_active_snapshot_matches_official": unaffected_active_matches_official,
        "hypotheses": hypotheses,
        "exact_root_cause_reproduced": exact_reproduction,
        "root_cause": (
            "official_trace_ingestion_rebuilt_query_from_unpinned_active_task_snapshot"
            if exact_reproduction
            else "unresolved"
        ),
    }


def classify_behavioral_provenance(
    *,
    task_and_instruction_match: bool,
    source_identity_evidence_count: int,
    official_identity_evidence_count: int,
    third_identity_evidence_count: int,
    mixed_identity_step_count: int,
    account_or_database_mixing: bool,
) -> str:
    if not task_and_instruction_match:
        return "task_id_or_row_join_corruption"
    if (
        mixed_identity_step_count > 0
        or account_or_database_mixing
        or third_identity_evidence_count > 0
        or (
            source_identity_evidence_count > 0
            and official_identity_evidence_count > 0
        )
    ):
        return "mixed_source_environment_corruption"
    if source_identity_evidence_count > 0:
        return "source_snapshot_unrecoverable"
    if official_identity_evidence_count <= 0:
        return "source_snapshot_unrecoverable"
    return HEADER_ONLY_CLASSIFICATION


def task_replay_gate(
    summary: Mapping[str, Any],
    *,
    expected_states: int,
    expected_prior_observations: int,
) -> dict[str, Any]:
    checks = {
        "state_count": int(summary["state_count"]) == int(expected_states),
        "identity": int(summary["identity_match_count"]) == int(expected_states),
        "histories": int(summary["complete_history_semantic_match_count"])
        == int(expected_states),
        "prior_count": int(summary["prior_observation_count"])
        == int(expected_prior_observations),
        "prior_match": int(summary["prior_semantic_match_count"])
        == int(expected_prior_observations),
        "targets": int(summary["target_semantic_match_count"])
        == int(expected_states),
        "complete": int(summary["complete_semantic_replay_count"])
        == int(expected_states),
        "non_temporal": int(summary["non_temporal_jwt_mismatch_count"]) == 0,
        "non_token": int(summary["non_token_mismatch_count"]) == 0,
        "exceptions": int(summary["exception_count"]) == 0,
    }
    return {"passed": all(checks.values()), "checks": checks}


def select_task_remediation(
    *,
    classification: str,
    replay_gate_passed: bool,
    actions_unchanged: bool,
    observations_unchanged: bool,
) -> str:
    if (
        classification == HEADER_ONLY_CLASSIFICATION
        and replay_gate_passed
        and actions_unchanged
        and observations_unchanged
    ):
        return REPAIR_ACTION
    return QUARANTINE_ACTION


def select_corpus_decision_branch(
    remediations: Mapping[str, str], *, structural_validation_passed: bool
) -> str:
    if not structural_validation_passed:
        return "source_corpus_not_reconcilable"
    expected = set(AFFECTED_TASK_IDS)
    if set(remediations) != expected:
        raise ValueError("Corpus policy must cover both affected tasks")
    repaired = {task for task, action in remediations.items() if action == REPAIR_ACTION}
    quarantined = {
        task for task, action in remediations.items() if action == QUARANTINE_ACTION
    }
    if repaired == expected:
        return "identity_reconciled_46_task_corpus_ready"
    if repaired == {"b0a8eae_2"} and quarantined == {"b0a8eae_3"}:
        return "repaired_validation_quarantined_train_corpus_ready"
    if quarantined == expected:
        return "provenance_quarantined_44_task_corpus_ready"
    return "source_corpus_not_reconcilable"


def write_jsonl_with_line_replacements(
    source: Path,
    destination: Path,
    replacements: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Replace one-based JSONL rows while preserving every unaffected byte."""
    if any(int(line) < 1 for line in replacements):
        raise ValueError("JSONL replacement lines are one-based")
    destination.parent.mkdir(parents=True, exist_ok=True)
    replaced: list[int] = []
    unchanged_hashes: list[str] = []
    with source.open("rb") as input_handle, tempfile.NamedTemporaryFile(
        "wb", delete=False, dir=destination.parent
    ) as output_handle:
        for line_number, raw_line in enumerate(input_handle, start=1):
            replacement = replacements.get(line_number)
            if replacement is None:
                output_handle.write(raw_line)
                unchanged_hashes.append(hashlib.sha256(raw_line).hexdigest())
                continue
            output_handle.write(
                (json.dumps(dict(replacement), sort_keys=True, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
            )
            replaced.append(line_number)
        output_handle.flush()
        os.fsync(output_handle.fileno())
        temporary = Path(output_handle.name)
    missing = sorted(set(map(int, replacements)) - set(replaced))
    if missing:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"JSONL replacement lines do not exist: {missing}")
    os.replace(temporary, destination)
    return {
        "source": str(source),
        "destination": str(destination),
        "replaced_lines": replaced,
        "unchanged_line_count": len(unchanged_hashes),
        "unchanged_lines_sha256": canonical_hash(unchanged_hashes),
    }


def validate_repaired_payload(
    original: Mapping[str, Any], corrected: Mapping[str, Any]
) -> dict[str, Any]:
    original_raw = dict(original["raw_trajectory"])
    corrected_raw = dict(corrected["raw_trajectory"])
    checks = {
        "task_id_unchanged": original["task_id"] == corrected["task_id"],
        "memory_id_unchanged": original["memory_id"] == corrected["memory_id"],
        "actions_unchanged": [row["response"] for row in original_raw["steps"]]
        == [row["response"] for row in corrected_raw["steps"]],
        "observations_unchanged": [row["observation"] for row in original_raw["steps"]]
        == [row["observation"] for row in corrected_raw["steps"]],
        "step_ids_unchanged": [row["step_id"] for row in original_raw["steps"]]
        == [row["step_id"] for row in corrected_raw["steps"]],
        "query_changed": original_raw["query"] != corrected_raw["query"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def build_reconciled_audit_manifest(
    rows: Sequence[Mapping[str, Any]], remediations: Mapping[str, str]
) -> dict[str, Any]:
    retained = [
        dict(row)
        for row in rows
        if remediations.get(str(row["task_id"])) != QUARANTINE_ACTION
    ]
    state_ids = [str(row["state_example_id"]) for row in retained]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("Reconciled audit manifest contains duplicate states")
    payload = {
        "format": "identity_reconciled_one_step_manifest_7a_v1",
        "selection_rule": "immutable_exp024a_manifest_with_preregistered_whole_task_policy",
        "original_state_count": len(rows),
        "state_count": len(retained),
        "task_count": len({str(row["task_id"]) for row in retained}),
        "prior_observation_count": sum(int(row["step_id"]) - 1 for row in retained),
        "replacement_state_count": 0,
        "rows": retained,
    }
    payload["manifest_sha256"] = canonical_hash(payload)
    return payload


def classify_dependency(
    *,
    has_invalid_evaluation_rows: bool,
    has_invalid_training_rows: bool,
    is_checkpoint: bool,
    is_cache: bool,
    is_report: bool,
) -> str:
    if has_invalid_training_rows and is_checkpoint:
        return "model_retraining_required"
    if has_invalid_training_rows and is_cache:
        return "incremental_cache_recompute_required"
    if has_invalid_training_rows:
        return "training_rows_invalidated"
    if has_invalid_evaluation_rows and is_report:
        return "report_recompute_required"
    if has_invalid_evaluation_rows:
        return "evaluation_rows_invalidated"
    if is_report:
        return "historical_only_do_not_reuse"
    return "structurally_unaffected"

