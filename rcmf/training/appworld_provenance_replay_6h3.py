from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash


PROVENANCE_AUDIT_VERSION = "appworld_source_provenance_audit_6h3_v1"
QUARANTINE_MANIFEST_VERSION = "provenance_valid_one_step_manifest_v1"
SNAPSHOT_SEARCH_VERSION = "bounded_source_snapshot_search_6h3_v1"
SENSITIVITY_VERSION = "provenance_quarantine_sensitivity_6h3_v1"
QUARANTINED_TASK_ID = "b0a8eae_2"


def text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def redacted_path(path: str) -> dict[str, str]:
    text = str(path).replace("\\", "/")
    return {
        "basename": text.rsplit("/", 1)[-1],
        "path_sha256": text_sha256(text),
    }


def summarize_corpus_identity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Corpus identity audit requires rows")
    task_ids = [str(row["task_id"]) for row in rows]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Corpus identity rows must be task-level and unique")
    mismatches = [row for row in rows if not bool(row["identity_match"])]
    mismatch_tasks = sorted(str(row["task_id"]) for row in mismatches)
    return {
        "format": PROVENANCE_AUDIT_VERSION,
        "task_count": len(rows),
        "identity_match_count": len(rows) - len(mismatches),
        "identity_mismatch_count": len(mismatches),
        "identity_mismatch_task_ids": mismatch_tasks,
        "mismatch_field_counts": dict(
            sorted(
                Counter(
                    field
                    for row in mismatches
                    for field in row.get("mismatched_fields", [])
                ).items()
            )
        ),
        "multiple_tasks_inconsistent": len(mismatch_tasks) > 1,
        "rows": [dict(row) for row in rows],
    }


def classify_provenance_failure(
    *,
    source_layers_agree: bool,
    supervisor_only_mismatch: bool,
    exact_identity_matches_other_task: bool,
    source_identity_evidence_count: int,
    official_identity_evidence_count: int,
    mixed_identity_step_count: int,
    exact_snapshot_found: bool,
) -> str:
    if exact_identity_matches_other_task:
        return "task_id_or_row_join_corruption"
    if mixed_identity_step_count > 0 or (
        source_identity_evidence_count > 0 and official_identity_evidence_count > 0
    ):
        return "mixed_source_environment_corruption"
    if exact_snapshot_found and source_layers_agree and supervisor_only_mismatch:
        return "alternate_task_snapshot_used"
    if source_layers_agree and supervisor_only_mismatch:
        if source_identity_evidence_count > 0 and official_identity_evidence_count == 0:
            return "alternate_task_snapshot_used"
        if source_identity_evidence_count == 0:
            return "source_query_header_only_corruption"
    return "source_snapshot_unrecoverable"


def training_contamination_report(
    *,
    task_id: str,
    train_task_ids: Sequence[str],
    transition_parent_task_ids: Sequence[str],
    train_label_task_ids: Sequence[str],
    teacher_source_task_ids: Sequence[str],
) -> dict[str, Any]:
    sources = {
        "stage_b_train_task_split": str(task_id) in set(map(str, train_task_ids)),
        "transition_source_parents": str(task_id)
        in set(map(str, transition_parent_task_ids)),
        "stage_b_train_labels": str(task_id) in set(map(str, train_label_task_ids)),
        "teacher_source_memories": str(task_id)
        in set(map(str, teacher_source_task_ids)),
    }
    return {
        "task_id": str(task_id),
        "sources": sources,
        "contaminates_training": any(sources.values()),
        "heldout_only": not any(sources.values()),
    }


def build_quarantine_manifest(
    rows: Sequence[Mapping[str, Any]],
    *,
    quarantined_task_id: str = QUARANTINED_TASK_ID,
) -> dict[str, Any]:
    original = [dict(row) for row in rows]
    state_ids = [str(row["state_example_id"]) for row in original]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("Original one-step manifest contains duplicate states")
    quarantined = [
        row for row in original if str(row["task_id"]) == str(quarantined_task_id)
    ]
    retained = [
        row for row in original if str(row["task_id"]) != str(quarantined_task_id)
    ]
    payload = {
        "format": QUARANTINE_MANIFEST_VERSION,
        "source_manifest_sha256": canonical_hash(original),
        "quarantined_task_id": str(quarantined_task_id),
        "original_state_count": len(original),
        "original_task_count": len({str(row["task_id"]) for row in original}),
        "quarantined_state_count": len(quarantined),
        "quarantined_state_ids": [str(row["state_example_id"]) for row in quarantined],
        "retained_state_count": len(retained),
        "retained_task_count": len({str(row["task_id"]) for row in retained}),
        "retained_prior_observation_count": sum(
            int(row["step_id"]) - 1 for row in retained
        ),
        "replacement_state_count": 0,
        "selection_rule": "original_immutable_manifest_minus_entire_quarantined_task",
        "rows": retained,
    }
    payload["manifest_sha256"] = canonical_hash(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )
    return payload


def build_quarantine_sentinel(
    sentinel_rows: Sequence[Mapping[str, Any]],
    *,
    retained_state_ids: Sequence[str],
    quarantined_task_id: str = QUARANTINED_TASK_ID,
) -> dict[str, Any]:
    retained_ids = set(map(str, retained_state_ids))
    selected = [
        dict(row)
        for row in sentinel_rows
        if str(row["task_id"]) != str(quarantined_task_id)
    ]
    if any(str(row["state_example_id"]) not in retained_ids for row in selected):
        raise ValueError("Quarantine sentinel contains a state outside the retained manifest")
    payload = {
        "format": "provenance_valid_sentinel_manifest_6h3_v1",
        "source_sentinel_sha256": canonical_hash(list(sentinel_rows)),
        "selection_rule": "original_fixed_sentinel_minus_quarantined_task_no_replacement",
        "state_count": len(selected),
        "task_count": len({str(row["task_id"]) for row in selected}),
        "prior_observation_count": sum(int(row["step_id"]) - 1 for row in selected),
        "replacement_state_count": 0,
        "rows": selected,
    }
    payload["manifest_sha256"] = canonical_hash(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )
    return payload


def semantic_replay_gate(
    summaries: Sequence[Mapping[str, Any]],
    *,
    expected_states: int,
    expected_tasks: int,
    expected_prior_observations: int,
    require_repeat_equivalence: bool,
    repeat_checks: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    checks: list[bool] = []
    for summary in summaries:
        checks.extend(
            [
                int(summary["state_count"]) == int(expected_states),
                int(summary["task_count"]) == int(expected_tasks),
                int(summary["identity_match_count"]) == int(expected_states),
                int(summary["complete_history_semantic_match_count"])
                == int(expected_states),
                int(summary["prior_observation_count"])
                == int(expected_prior_observations),
                int(summary["prior_semantic_match_count"])
                == int(expected_prior_observations),
                int(summary["target_semantic_match_count"]) == int(expected_states),
                int(summary["complete_semantic_replay_count"]) == int(expected_states),
                int(summary["exception_count"]) == 0,
                int(summary["non_temporal_jwt_mismatch_count"]) == 0,
                int(summary["non_token_mismatch_count"]) == 0,
            ]
        )
    if require_repeat_equivalence:
        checks.append(len(summaries) == 2)
        checks.append(len(repeat_checks) == int(expected_states))
        checks.append(all(bool(row["semantic_repeat_match"]) for row in repeat_checks))
    return {
        "passed": all(checks),
        "expected_states": int(expected_states),
        "expected_tasks": int(expected_tasks),
        "expected_prior_observations": int(expected_prior_observations),
        "repeat_equivalence_required": bool(require_repeat_equivalence),
        "check_count": len(checks),
    }


def select_preflight_branch(
    *,
    mismatch_task_count: int,
    exact_snapshot_found: bool,
    training_contaminated: bool,
) -> str:
    if int(mismatch_task_count) > 1:
        return "source_dataset_identity_consistency_failure"
    if exact_snapshot_found:
        return "exact_historical_snapshot_found_pending_replay"
    if training_contaminated:
        return "provenance_invalid_task_contaminates_training"
    return "provenance_valid_task_quarantine_ready"
