from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import _target_suffix
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import read_jsonl, sha256_file, sha256_text


PREFLIGHT_VERSION = "identity_reconciled_incremental_cache_preflight_7b_v1"
AFFECTED_TASK_IDS = frozenset({"b0a8eae_2", "b0a8eae_3"})


def canonical_json_sha256(value: Any) -> str:
    import hashlib

    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def example_task_id(example: DecisionExample) -> str:
    return str(example.metadata.get("task_id") or example.episode_id.rsplit(":", 1)[-1])


def source_identity_audit(
    *,
    old_examples: Sequence[DecisionExample],
    clean_examples: Sequence[DecisionExample],
    old_records: Sequence[MemoryRecord],
    clean_records: Sequence[MemoryRecord],
) -> dict[str, Any]:
    if len(old_examples) != len(clean_examples):
        raise ValueError("Decision-example count changed during identity reconciliation")
    if len(old_records) != len(clean_records):
        raise ValueError("Memory-record count changed during identity reconciliation")
    changed_examples: list[dict[str, Any]] = []
    unchanged_example_errors: list[str] = []
    for index, (old, clean) in enumerate(zip(old_examples, clean_examples, strict=True)):
        old_id = state_example_id(index, old)
        clean_id = state_example_id(index, clean)
        if old_id != clean_id:
            raise ValueError(f"Decision identity changed at index {index}: {old_id} != {clean_id}")
        old_payload = old.to_dict()
        clean_payload = clean.to_dict()
        task_id = example_task_id(clean)
        changed = canonical_json_sha256(old_payload) != canonical_json_sha256(clean_payload)
        if changed:
            changed_examples.append(
                {
                    "index": index,
                    "state_example_id": clean_id,
                    "task_id": task_id,
                    "old_sha256": canonical_json_sha256(old_payload),
                    "clean_sha256": canonical_json_sha256(clean_payload),
                }
            )
            if task_id not in AFFECTED_TASK_IDS:
                unchanged_example_errors.append(clean_id)
        elif task_id in AFFECTED_TASK_IDS:
            unchanged_example_errors.append(f"expected_changed:{clean_id}")

    changed_records: list[dict[str, Any]] = []
    unchanged_record_errors: list[str] = []
    for index, (old, clean) in enumerate(zip(old_records, clean_records, strict=True)):
        if old.memory_id != clean.memory_id or old.task_id != clean.task_id:
            raise ValueError(f"Memory identity changed at index {index}")
        old_payload = old.to_dict()
        clean_payload = clean.to_dict()
        changed = canonical_json_sha256(old_payload) != canonical_json_sha256(clean_payload)
        if changed:
            changed_records.append(
                {
                    "index": index,
                    "memory_id": clean.memory_id,
                    "task_id": clean.task_id,
                    "old_sha256": canonical_json_sha256(old_payload),
                    "clean_sha256": canonical_json_sha256(clean_payload),
                }
            )
            if clean.task_id not in AFFECTED_TASK_IDS:
                unchanged_record_errors.append(clean.memory_id)
        elif clean.task_id in AFFECTED_TASK_IDS:
            unchanged_record_errors.append(f"expected_changed:{clean.memory_id}")
    if unchanged_example_errors or unchanged_record_errors:
        raise ValueError(
            "Source byte-identity audit differs outside the two preregistered tasks: "
            f"examples={unchanged_example_errors[:5]} records={unchanged_record_errors[:5]}"
        )
    return {
        "changed_decision_count": len(changed_examples),
        "changed_memory_count": len(changed_records),
        "unchanged_decision_count": len(clean_examples) - len(changed_examples),
        "unchanged_memory_count": len(clean_records) - len(changed_records),
        "changed_decisions": changed_examples,
        "changed_memories": changed_records,
        "unaffected_rows_byte_identical": True,
    }


def _cache_row_key(cache_name: str, row: Mapping[str, Any]) -> str:
    fields = {
        "raw_text_teacher": ("pair_key", "state_memory_pair_id"),
        "stage_c1_response": ("state_example_id",),
        "pair_response_5d": ("pair_id",),
        "transition_teacher": ("pair_id",),
    }[cache_name]
    for field in fields:
        value = row.get(field)
        if value is not None:
            return str(value)
    raise ValueError(f"{cache_name} row has no stable key")


def affected_reasons(
    cache_name: str,
    row: Mapping[str, Any],
    *,
    affected_memory_ids: set[str],
    affected_old_transition_ids: set[str],
) -> list[str]:
    reasons: list[str] = []
    if str(row.get("task_id")) in AFFECTED_TASK_IDS:
        reasons.append("reconciled_query_state")
    memory_fields = {
        "raw_text_teacher": ("candidate_memory_id",),
        "stage_c1_response": ("best_memory_id",),
        "pair_response_5d": ("memory_id",),
        "transition_teacher": ("parent_memory_id",),
    }[cache_name]
    if any(str(row.get(field)) in affected_memory_ids for field in memory_fields):
        reasons.append("reconciled_memory_record")
    if cache_name == "transition_teacher" and (
        str(row.get("transition_id")) in affected_old_transition_ids
        or str(row.get("parent_task_id")) in AFFECTED_TASK_IDS
    ):
        reasons.append("superseded_transition")
    return sorted(set(reasons))


def audit_jsonl_cache(
    *,
    cache_name: str,
    path: Path,
    affected_memory_ids: set[str],
    affected_old_transition_ids: set[str],
) -> dict[str, Any]:
    rows = list(read_jsonl(path))
    keys: set[str] = set()
    affected: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for position, row in enumerate(rows):
        key = _cache_row_key(cache_name, row)
        if key in keys:
            raise ValueError(f"Duplicate {cache_name} cache key: {key}")
        keys.add(key)
        reasons = affected_reasons(
            cache_name,
            row,
            affected_memory_ids=affected_memory_ids,
            affected_old_transition_ids=affected_old_transition_ids,
        )
        if reasons:
            reason_counts.update(reasons)
            affected.append(
                {
                    "position": position,
                    "key": key,
                    "state_example_id": row.get("state_example_id"),
                    "task_id": row.get("task_id"),
                    "memory_id": row.get("candidate_memory_id")
                    or row.get("best_memory_id")
                    or row.get("memory_id"),
                    "transition_id": row.get("transition_id"),
                    "reasons": reasons,
                }
            )
    return {
        "cache_name": cache_name,
        "path": str(path),
        "sha256": sha256_file(path),
        "row_count": len(rows),
        "affected_row_count": len(affected),
        "reusable_row_count": len(rows) - len(affected),
        "reason_counts": dict(sorted(reason_counts.items())),
        "affected_rows": affected,
        "duplicate_key_count": 0,
    }


def validate_unaffected_cache_rows(
    *,
    audits: Mapping[str, Mapping[str, Any]],
    clean_examples: Sequence[DecisionExample],
    clean_records: Sequence[MemoryRecord],
    clean_transitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    examples_by_id = {
        state_example_id(index, example): example
        for index, example in enumerate(clean_examples)
    }
    records_by_id = {str(record.memory_id): record for record in clean_records}
    transitions_by_id = {
        str(row["transition_id"]): row for row in clean_transitions
    }
    affected_keys = {
        name: {str(row["key"]) for row in audit["affected_rows"]}
        for name, audit in audits.items()
    }
    errors: list[dict[str, Any]] = []
    checked = Counter()
    for name, audit in audits.items():
        for row in read_jsonl(Path(str(audit["path"]))):
            key = _cache_row_key(name, row)
            if key in affected_keys[name]:
                continue
            state_id = str(row["state_example_id"])
            example = examples_by_id.get(state_id)
            if example is None:
                errors.append({"cache": name, "key": key, "error": "state_missing"})
                continue
            row_errors = []
            if str(row.get("task_id")) != example_task_id(example):
                row_errors.append("task_id")
            if str(row.get("target_sha256")) != sha256_text(_target_suffix(example)):
                row_errors.append("target_sha256")
            memory_id = row.get("candidate_memory_id") or row.get("best_memory_id") or row.get("memory_id")
            if memory_id is not None:
                record = records_by_id.get(str(memory_id))
                if record is None:
                    row_errors.append("memory_missing")
                elif row.get("memory_text_sha256") is not None and str(row["memory_text_sha256"]) != sha256_text(record.experience_text):
                    row_errors.append("memory_text_sha256")
            transition_id = row.get("transition_id")
            if transition_id is not None:
                transition = transitions_by_id.get(str(transition_id))
                if transition is None:
                    row_errors.append("transition_missing")
                else:
                    if str(row.get("transition_content_sha256")) != str(transition["transition_content_sha256"]):
                        row_errors.append("transition_content_sha256")
                    section_hash = sha256_text(transition_teacher_section(dict(transition)))
                    if str(row.get("teacher_section_sha256")) != section_hash:
                        row_errors.append("teacher_section_sha256")
            if row_errors:
                errors.append({"cache": name, "key": key, "errors": row_errors})
            checked[name] += 1
    if errors:
        raise ValueError(f"Unchanged cache-row validation failed: {errors[:5]}")
    return {
        "checked_reusable_rows": dict(sorted(checked.items())),
        "error_count": 0,
        "passed": True,
    }


def transition_change_manifest(
    *, old_transitions: Sequence[Mapping[str, Any]], clean_transitions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    old = {
        (str(row["parent_task_id"]), int(row["step_index"])): row
        for row in old_transitions
    }
    clean = {
        (str(row["parent_task_id"]), int(row["step_index"])): row
        for row in clean_transitions
    }
    mapping = []
    for identity, old_row in sorted(old.items()):
        if identity[0] not in AFFECTED_TASK_IDS:
            if canonical_json_sha256(old_row) != canonical_json_sha256(clean[identity]):
                raise ValueError(f"Unaffected transition changed: {identity}")
            continue
        clean_row = clean[identity]
        if str(old_row["transition_id"]) != str(clean_row["transition_id"]):
            mapping.append(
                {
                    "parent_task_id": identity[0],
                    "step_index": identity[1],
                    "old_transition_id": str(old_row["transition_id"]),
                    "clean_transition_id": str(clean_row["transition_id"]),
                    "old_content_sha256": str(old_row["transition_content_sha256"]),
                    "clean_content_sha256": str(clean_row["transition_content_sha256"]),
                }
            )
    return {
        "changed_transition_count": len(mapping),
        "mapping": mapping,
        "unaffected_transitions_byte_identical": True,
    }
