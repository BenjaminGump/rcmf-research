from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.traces import (
    AppWorldTrace,
    AppWorldTraceStep,
    decision_examples_from_trace,
    memory_record_from_trace,
)
from rcmf.config import load_config
from rcmf.training.appworld_identity_reconciliation_7a import (
    AFFECTED_TASK_IDS,
    HEADER_ONLY_CLASSIFICATION,
    audit_corpus_builder_hypotheses,
    classify_behavioral_provenance,
    text_sha256,
    validate_repaired_payload,
    write_jsonl_with_line_replacements,
)
from rcmf.training.appworld_legacy_replay_6h1 import build_replay_contract
from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash, parse_full_demo_query
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.prepare_appworld_provenance_replay_6h3 import (
    _field_hashes,
    _full_query,
    _spec_fields,
    _trajectory_identity_evidence,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


def _snapshot_query_index(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    fields_by_task: dict[str, dict[str, str]] = {}
    query_to_tasks: dict[str, list[str]] = defaultdict(list)
    for path in sorted((root / "tasks").glob("*/specs.json")):
        fields = _spec_fields(path)
        task_id = path.parent.name
        fields_by_task[task_id] = fields
        query_to_tasks[text_sha256(_full_query(fields))].append(task_id)
    return fields_by_task, dict(query_to_tasks)


def _third_identity_evidence(
    record: Any,
    *,
    task_id: str,
    source_fields: Mapping[str, str],
    official_fields: Mapping[str, str],
    identity_catalog: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    excluded = {
        str(source_fields[field])
        for field in ("first_name", "last_name", "email", "phone_number")
    } | {
        str(official_fields[field])
        for field in ("first_name", "last_name", "email", "phone_number")
    }
    candidates: dict[str, tuple[str, str]] = {}
    for other_task, fields in identity_catalog.items():
        if other_task == task_id:
            continue
        for field in ("email", "phone_number"):
            value = str(fields.get(field, ""))
            if value and value not in excluded:
                candidates[value] = (other_task, field)
    rows = []
    for step in record.raw_trajectory["steps"]:
        locations = []
        for location in ("response", "observation"):
            text = str(step.get(location, ""))
            for value, (other_task, field) in candidates.items():
                if value in text:
                    locations.append(
                        {
                            "location": location,
                            "field": field,
                            "identity_task_sha256": text_sha256(other_task),
                        }
                    )
        if locations:
            rows.append({"step_id": int(step["step_id"]), "matches": locations})
    return {
        "third_identity_evidence_count": sum(len(row["matches"]) for row in rows),
        "steps": rows,
    }


def _reconciled_trace(record: Any, official_query: str) -> AppWorldTrace:
    raw = dict(record.raw_trajectory)
    return AppWorldTrace(
        task_id=str(record.task_id),
        query=official_query,
        steps=[
            AppWorldTraceStep(
                index=int(row["step_id"]),
                response=str(row["response"]),
                observation=str(row["observation"]),
            )
            for row in raw["steps"]
        ],
        is_correct=bool(record.success),
        system_prompt=str(raw.get("system_prompt", "")),
        final_answer=str(raw.get("final_answer", "")),
        source_path=str(raw.get("source_path", record.metadata.get("source_path", ""))),
        source_kind=str(record.metadata.get("source", "official_appworld_experiment_output")),
    )


def _build_candidate_corpus(
    *,
    records: Sequence[Any],
    examples: Sequence[Any],
    official_fields: Mapping[str, Mapping[str, str]],
    source_dir: Path,
    artifact_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    record_replacements: dict[int, Mapping[str, Any]] = {}
    example_replacements: dict[int, Mapping[str, Any]] = {}
    examples_by_task: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for index, example in enumerate(examples):
        examples_by_task[str(example.metadata["task_id"])].append((index, example))
    repaired_records: dict[str, Any] = {}
    repaired_examples: dict[int, Any] = {}
    validation_rows = []
    for record_line, record in enumerate(records, start=1):
        task_id = str(record.task_id)
        if task_id not in AFFECTED_TASK_IDS:
            continue
        trace = _reconciled_trace(record, _full_query(official_fields[task_id]))
        repaired_record = memory_record_from_trace(trace)
        repaired_records[task_id] = repaired_record
        record_replacements[record_line] = repaired_record.to_dict()
        repaired_for_task = decision_examples_from_trace(trace)
        originals = examples_by_task[task_id]
        if len(repaired_for_task) != len(originals):
            raise ValueError(f"Decision count changed for {task_id}")
        per_decision = []
        for repaired, (index, original) in zip(repaired_for_task, originals, strict=True):
            if repaired.target_text != original.target_text or repaired.step_id != original.step_id:
                raise ValueError(f"Decision target changed for {task_id} line {index + 1}")
            repaired_examples[index] = repaired
            example_replacements[index + 1] = repaired.to_dict()
            per_decision.append(
                {
                    "line": index + 1,
                    "step_id": int(repaired.step_id),
                    "target_unchanged": repaired.target_text == original.target_text,
                    "old_state_sha256": text_sha256(original.state_text),
                    "new_state_sha256": text_sha256(repaired.state_text),
                }
            )
        payload_check = validate_repaired_payload(record.to_dict(), repaired_record.to_dict())
        validation_rows.append(
            {
                "task_id": task_id,
                "memory_record_line": record_line,
                "record_validation": payload_check,
                "decision_count": len(per_decision),
                "all_targets_unchanged": all(row["target_unchanged"] for row in per_decision),
                "decision_rows": per_decision,
            }
        )

    private = artifact_dir / "private" / "candidate_repaired_corpus"
    private.mkdir(parents=True, exist_ok=True)
    record_write = write_jsonl_with_line_replacements(
        source_dir / "memory_records.jsonl",
        private / "memory_records.jsonl",
        record_replacements,
    )
    example_write = write_jsonl_with_line_replacements(
        source_dir / "decision_examples.jsonl",
        private / "decision_examples.jsonl",
        example_replacements,
    )
    candidate_records = load_memory_records(private / "memory_records.jsonl")
    candidate_examples = load_decision_examples(private / "decision_examples.jsonl")
    report = {
        "format": "candidate_identity_reconciled_structural_corpus_7a_v1",
        "records": record_write,
        "examples": example_write,
        "validation_rows": validation_rows,
        "actions_observations_unchanged": all(
            row["record_validation"]["passed"] for row in validation_rows
        ),
        "targets_unchanged": all(row["all_targets_unchanged"] for row in validation_rows),
        "candidate_memory_records_sha256": sha256_file(private / "memory_records.jsonl"),
        "candidate_decision_examples_sha256": sha256_file(private / "decision_examples.jsonl"),
    }
    return report, {"records": repaired_records, "examples": repaired_examples}, candidate_records, candidate_examples


def _build_affected_contracts(
    *,
    records: Sequence[Any],
    examples: Sequence[Any],
    settings: Mapping[str, Any],
    artifact_dir: Path,
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    records_by_task = {str(record.task_id): record for record in records}
    rows = []
    contracts_dir = artifact_dir / "private" / "affected_replay_contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for index, example in enumerate(examples):
        task_id = str(example.metadata["task_id"])
        if task_id not in AFFECTED_TASK_IDS:
            continue
        state_id = state_example_id(index, example)
        query = {
            "state_example_id": state_id,
            "task_id": task_id,
            "step_id": int(example.step_id),
        }
        contract = build_replay_contract(
            query=query,
            example=example,
            record=records_by_task[task_id],
            legacy_python=Path(settings["legacy"]["executable"]),
            appworld_root=Path(settings["legacy"]["appworld_root"]),
            experiment_name=f"exp025a_candidate_{_safe_name(state_id)}",
            random_seed=int(settings["replay"]["random_seed"]),
            max_interactions=int(settings["replay"]["max_interactions"]),
            max_api_calls_per_interaction=int(settings["replay"]["max_api_calls_per_interaction"]),
            source_hashes=source_hashes,
        )
        path = contracts_dir / f"{_safe_name(state_id)}.json"
        atomic_write_json(path, contract)
        rows.append(
            {
                "state_example_id": state_id,
                "task_id": task_id,
                "step_id": int(example.step_id),
                "history_step_count": int(example.step_id) - 1,
                "contract_path": str(path),
                "contract_sha256": canonical_hash(contract),
            }
        )
    payload = {
        "format": "affected_task_candidate_replay_contract_manifest_7a_v1",
        "state_count": len(rows),
        "task_count": len({row["task_id"] for row in rows}),
        "prior_observation_count": sum(row["history_step_count"] for row in rows),
        "rows": rows,
    }
    payload["manifest_sha256"] = canonical_hash(payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp025a")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7a"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    source = Path(settings["source_data"])
    r3 = Path(settings["parent_exp024r3"])
    paths = {
        "memory_records": source / "memory_records.jsonl",
        "decision_examples": source / "decision_examples.jsonl",
        "source_summary": source / "summary.json",
        "source_filter": source / "filter_summary.json",
        "split_manifest": Path(settings["split_manifest"]),
        "r3_corpus_identity": r3 / "corpus_identity_consistency.json",
        "r3_official_probe": r3 / "corpus_official_identity_probe.json",
        "r3_training_audit": r3 / "training_contamination_audit.json",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "all_46_task_builder_and_identity_audit",
            "b0a8eae_2_and_b0a8eae_3_behavioral_provenance",
            "candidate_header_repair_contracts",
            "no_qwen_no_h100_no_training",
        ],
    )

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="builder_forensic_and_candidate_contract_preflight",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        records = load_memory_records(paths["memory_records"])
        examples = load_decision_examples(paths["decision_examples"])
        expected = settings["expected"]
        if len(records) != int(expected["task_count"]) or len(examples) != int(expected["decision_count"]):
            raise ValueError("Source corpus counts changed")
        records_by_task = {str(record.task_id): record for record in records}
        examples_by_task = Counter(str(example.metadata["task_id"]) for example in examples)
        if len(records_by_task) != len(records) or sum(examples_by_task.values()) != len(examples):
            raise ValueError("Source corpus task/decision identities are not unique and complete")

        active_root = Path(settings["snapshots"]["active_unpinned_data_root"])
        official_root = Path(settings["snapshots"]["official_010_data_root"])
        backup_root = Path(settings["snapshots"]["immutable_backup_data_root"])
        active_fields, _ = _snapshot_query_index(active_root)
        official_fields, official_query_index = _snapshot_query_index(official_root)
        backup_fields, _ = _snapshot_query_index(backup_root)

        identity_rows = []
        source_fields_by_task = {}
        for record in records:
            task_id = str(record.task_id)
            source_query = str(record.raw_trajectory["query"])
            source_fields = parse_full_demo_query(source_query)
            source_fields_by_task[task_id] = source_fields
            active_query = _full_query(active_fields[task_id])
            official_query = _full_query(official_fields[task_id])
            source_hash = text_sha256(source_query)
            same_base = task_id.rsplit("_", 1)[0]
            other_official = [
                other for other in official_query_index.get(source_hash, []) if other != task_id
            ]
            identity_rows.append(
                {
                    "task_id": task_id,
                    "decision_count": examples_by_task[task_id],
                    "source_query_sha256": source_hash,
                    "source_field_sha256": _field_hashes(source_fields),
                    "active_spec_query_sha256": text_sha256(active_query),
                    "active_spec_field_sha256": _field_hashes(active_fields[task_id]),
                    "official_spec_query_sha256": text_sha256(official_query),
                    "official_spec_field_sha256": _field_hashes(official_fields[task_id]),
                    "backup_spec_query_sha256": text_sha256(_full_query(backup_fields[task_id])),
                    "official_backup_agree": official_fields[task_id] == backup_fields[task_id],
                    "source_matches_active_snapshot": source_query == active_query,
                    "source_matches_official_snapshot": source_query == official_query,
                    "source_matches_other_official_task_ids": other_official,
                    "source_matches_same_base_other_suffix": any(
                        other.rsplit("_", 1)[0] == same_base for other in other_official
                    ),
                }
            )
        mismatch_ids = sorted(
            row["task_id"] for row in identity_rows if not row["source_matches_official_snapshot"]
        )
        if mismatch_ids != sorted(AFFECTED_TASK_IDS):
            raise RuntimeError(
                "systematic_source_corpus_construction_failure: " + json.dumps(mismatch_ids)
            )
        builder = audit_corpus_builder_hypotheses(identity_rows)
        if not builder["exact_root_cause_reproduced"]:
            raise RuntimeError("Corpus-construction root cause was not exactly reproduced")
        builder.update(
            {
                "source_builder": "scripts/prepare_appworld_official_traces.py@starting_head",
                "source_builder_behavior": "archived_environment_io_plus_active_Task.load_query",
                "prospective_fix": "explicit_pinned_task_spec_root_required",
                "identity_table_sha256": canonical_hash(identity_rows),
            }
        )
        corpus_identity = {
            "format": "identity_reconciliation_corpus_table_7a_v1",
            "task_count": len(identity_rows),
            "decision_count": len(examples),
            "identity_match_count": len(identity_rows) - len(mismatch_ids),
            "identity_mismatch_count": len(mismatch_ids),
            "identity_mismatch_task_ids": mismatch_ids,
            "rows": identity_rows,
        }
        atomic_write_json(args.artifact_dir / "corpus_builder_root_cause.json", builder)
        atomic_write_json(args.artifact_dir / "corpus_identity_reconciliation_table.json", corpus_identity)

        forensic_rows = []
        classifications = {}
        for task_id in AFFECTED_TASK_IDS:
            evidence = _trajectory_identity_evidence(
                records_by_task[task_id],
                source_fields=source_fields_by_task[task_id],
                official_fields=official_fields[task_id],
            )
            third = _third_identity_evidence(
                records_by_task[task_id],
                task_id=task_id,
                source_fields=source_fields_by_task[task_id],
                official_fields=official_fields[task_id],
                identity_catalog=official_fields,
            )
            task_instruction_match = (
                source_fields_by_task[task_id]["instruction"]
                == official_fields[task_id]["instruction"]
            )
            classification = classify_behavioral_provenance(
                task_and_instruction_match=task_instruction_match,
                source_identity_evidence_count=int(evidence["source_identity_evidence_count"]),
                official_identity_evidence_count=int(evidence["official_identity_evidence_count"]),
                third_identity_evidence_count=int(third["third_identity_evidence_count"]),
                mixed_identity_step_count=int(evidence["mixed_identity_step_count"]),
                account_or_database_mixing=False,
            )
            classifications[task_id] = classification
            forensic_rows.append(
                {
                    "task_id": task_id,
                    "classification_before_replay": classification,
                    "task_id_and_instruction_match": task_instruction_match,
                    "project_snapshot_matches_source_header": source_fields_by_task[task_id]
                    == active_fields[task_id],
                    "official_backup_agree": official_fields[task_id] == backup_fields[task_id],
                    "identity_evidence": evidence,
                    "third_identity_evidence": third,
                    "identity_neutral_step_count": len(records_by_task[task_id].raw_trajectory["steps"])
                    - len(evidence["steps_with_identity_evidence"])
                    - len(third["steps"]),
                    "raw_identity_values_redacted": True,
                }
            )
        if any(value != HEADER_ONLY_CLASSIFICATION for value in classifications.values()):
            raise RuntimeError("Affected-task behavioral provenance is not header-only")
        forensic = {
            "format": "affected_task_behavioral_provenance_7a_v1",
            "task_count": len(forensic_rows),
            "rows": forensic_rows,
        }
        atomic_write_json(args.artifact_dir / "affected_task_behavioral_provenance.json", forensic)

        candidate_report, _, candidate_records, candidate_examples = _build_candidate_corpus(
            records=records,
            examples=examples,
            official_fields=official_fields,
            source_dir=source,
            artifact_dir=args.artifact_dir,
        )
        atomic_write_json(args.artifact_dir / "candidate_repair_structural_validation.json", candidate_report)
        contracts = _build_affected_contracts(
            records=candidate_records,
            examples=candidate_examples,
            settings=settings,
            artifact_dir=args.artifact_dir,
            source_hashes={
                "source_memory_records": data_hashes["memory_records"],
                "source_decision_examples": data_hashes["decision_examples"],
                "candidate_memory_records": candidate_report["candidate_memory_records_sha256"],
                "candidate_decision_examples": candidate_report["candidate_decision_examples_sha256"],
                "builder_root_cause": sha256_file(args.artifact_dir / "corpus_builder_root_cause.json"),
            },
        )
        if contracts["state_count"] != sum(
            int(expected["affected_tasks"][task]["decisions"]) for task in AFFECTED_TASK_IDS
        ):
            raise ValueError("Affected replay contract count changed")
        atomic_write_json(args.artifact_dir / "affected_replay_contract_manifest.json", contracts)
        decision = {
            "format": "identity_reconciliation_preflight_decision_7a_v1",
            "decision_branch": "affected_task_candidate_semantic_replay_ready",
            "root_cause": builder["root_cause"],
            "mismatch_task_ids": mismatch_ids,
            "classifications_before_replay": classifications,
            "candidate_replay_allowed": True,
            "downstream_remediation_not_yet_selected": True,
            "qwen_count": 0,
            "h100_hours": 0.0,
            "model_training_count": 0,
        }
        atomic_write_json(args.artifact_dir / "preflight_decision.json", decision)
        attempt.progress(latest_validated_checkpoint=str(args.artifact_dir / "preflight_decision.json"))
        print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
