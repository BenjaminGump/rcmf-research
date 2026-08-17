from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.transitions import (
    extract_decision_transitions,
    validate_transition_extraction,
)
from rcmf.config import load_config
from rcmf.training.appworld_identity_reconciliation_7a import (
    AFFECTED_TASK_IDS,
    QUARANTINE_ACTION,
    RECONCILED_CORPUS_VERSION,
    REPAIR_ACTION,
    build_reconciled_audit_manifest,
    classify_dependency,
    select_corpus_decision_branch,
    text_sha256,
)
from rcmf.training.appworld_legacy_replay_6h1 import upgrade_replay_contract
from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash, parse_full_demo_query
from rcmf.training.datasets import (
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import (
    example_leakage_keys,
    state_example_id,
    transition_leakage_keys,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file, write_jsonl
from scripts.prepare_appworld_provenance_replay_6h3 import _field_hashes, _full_query, _spec_fields


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


def _reconcile_legacy_contract_query(
    contract: Mapping[str, Any], query: str
) -> dict[str, Any]:
    payload = dict(contract)
    if "expected_task_instruction" in payload:
        payload["expected_task_instruction"] = str(query)
    payload["expected_task_query"] = str(query)
    upgraded = upgrade_replay_contract(payload)
    if upgraded["expected_task_query"] != str(query):
        raise ValueError("Legacy replay contract did not preserve reconciled query")
    return upgraded


def _atomic_copy_or_validate(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(source) != sha256_file(destination):
            raise ValueError(f"Existing immutable output differs: {destination}")
        return
    temporary = destination.with_suffix(destination.suffix + f".partial.{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def _filter_jsonl_tasks(source: Path, destination: Path, quarantined: set[str]) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with source.open("rb") as input_handle, tempfile.NamedTemporaryFile(
        "wb", delete=False, dir=destination.parent
    ) as output_handle:
        for raw_line in input_handle:
            row = json.loads(raw_line)
            task_id = str(row.get("task_id") or row.get("metadata", {}).get("task_id", ""))
            if task_id in quarantined:
                continue
            output_handle.write(raw_line)
            kept += 1
        output_handle.flush()
        os.fsync(output_handle.fileno())
        temporary = Path(output_handle.name)
    if destination.exists():
        if sha256_file(destination) != sha256_file(temporary):
            temporary.unlink(missing_ok=True)
            raise ValueError(f"Existing immutable filtered output differs: {destination}")
        temporary.unlink()
    else:
        os.replace(temporary, destination)
    return kept


def _manifest_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("rows", "query_rows"):
        if isinstance(payload.get(key), list):
            return [dict(row) for row in payload[key]]
    raise ValueError("Manifest has no rows")


def _count_jsonl_needles(path: Path, needles: Sequence[str]) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False, "row_count": 0, "affected_row_count": 0}
    total = 0
    affected = 0
    encoded = [needle.encode("utf-8") for needle in needles]
    with path.open("rb") as handle:
        for line in handle:
            total += 1
            affected += any(needle in line for needle in encoded)
    return {"available": True, "row_count": total, "affected_row_count": affected}


def _dependency_row(
    *,
    artifact: str,
    role: str,
    affected_rows: int,
    reusable_rows: bool,
    training: bool,
    evaluation: bool,
    cache: bool = False,
    checkpoint: bool = False,
    report: bool = False,
    row_recompute: bool = False,
) -> dict[str, Any]:
    return {
        "path_or_version": artifact,
        "role": role,
        "depends_on_b0a8eae_2_as_query": evaluation,
        "depends_on_b0a8eae_3_as_query": training,
        "depends_on_b0a8eae_3_as_memory": training,
        "depends_on_b0a8eae_3_as_transition_parent": "transition" in role,
        "affected_row_count": int(affected_rows),
        "unaffected_rows_reusable": bool(reusable_rows),
        "row_level_recomputation_sufficient": bool(row_recompute),
        "checkpoint_retraining_required": bool(checkpoint and training),
        "report_only_recomputation_sufficient": bool(report and not checkpoint),
        "classification": classify_dependency(
            has_invalid_evaluation_rows=evaluation,
            has_invalid_training_rows=training,
            is_checkpoint=checkpoint,
            is_cache=cache,
            is_report=report,
        ),
    }


def _build_dependency_graph(
    *,
    settings: Mapping[str, Any],
    original_records: Sequence[Any],
    original_transitions: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    memory_by_task = {str(record.task_id): str(record.memory_id) for record in original_records}
    b3_memory = memory_by_task["b0a8eae_3"]
    b3_transition_ids = [
        str(row["transition_id"])
        for row in original_transitions
        if str(row["parent_task_id"]) == "b0a8eae_3"
    ]
    needles = [*AFFECTED_TASK_IDS, b3_memory, *b3_transition_ids]
    teacher = Path("runs/teacher/raw_text_full_cache_20260805_001/teacher_cache_full_rows.jsonl")
    stage_c1 = Path("runs/stage_c1/response_cache_20260806_001/response_cache_rows.jsonl")
    pair_5d = Path("runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache/pair_response_cache_rows.jsonl")
    transition_cache = Path(settings["parent_exp017"]) / "teacher_cache.jsonl"
    counts = {
        "raw_teacher": _count_jsonl_needles(teacher, needles),
        "stage_c1_response": _count_jsonl_needles(stage_c1, needles),
        "pair_5d": _count_jsonl_needles(pair_5d, needles),
        "transition_teacher": _count_jsonl_needles(transition_cache, needles),
    }
    rows = [
        _dependency_row(
            artifact=str(settings["source_data"]), role="filtered_source_corpus", affected_rows=37,
            reusable_rows=True, training=True, evaluation=True, row_recompute=True,
        ),
        _dependency_row(
            artifact="all_state_representation_caches", role="state_representations", affected_rows=35,
            reusable_rows=True, training=True, evaluation=True, cache=True, row_recompute=True,
        ),
        _dependency_row(
            artifact="all_memory_representation_caches", role="memory_representations", affected_rows=2,
            reusable_rows=True, training=True, evaluation=False, cache=True, row_recompute=True,
        ),
        _dependency_row(
            artifact=str(teacher), role="raw_text_teacher_cache", affected_rows=counts["raw_teacher"]["affected_row_count"],
            reusable_rows=True, training=True, evaluation=True, cache=True, row_recompute=True,
        ),
        _dependency_row(
            artifact="runs/stage_b/student_labels_20260806_002", role="stage_b_labels", affected_rows=638,
            reusable_rows=False, training=True, evaluation=True, cache=True, row_recompute=True,
        ),
    ]
    checkpoint_dirs = [
        "runs/stage_b/addressing_only_pilot_20260806_003",
        "runs/stage_b/addressing_4b_20260806_002",
        "runs/stage_b/signed_field_4c_20260806_002",
        "runs/stage_b/selector_repair_5c_20260807_001",
        "runs/stage_c1/signed_program_c1_20260806_002",
        "runs/stage_c/pair_grounding_5d_20260807_001",
        "runs/stage_c/oracle_capacity_5e_20260808_001",
        "runs/stage_c/oracle_convergence_5fa_20260808_001",
        "runs/stage_c/oracle_convergence_5fb_20260809_001",
        "runs/stage_c/oracle_decoder_5fc_20260810_003",
    ]
    for path in checkpoint_dirs:
        rows.append(
            _dependency_row(
                artifact=path,
                role="trained_checkpoint_or_checkpoint_derived_behavior",
                affected_rows=638,
                reusable_rows=False,
                training=True,
                evaluation=True,
                checkpoint=True,
            )
        )
    rows.extend(
        [
            _dependency_row(
                artifact="runs/stage_c/transition_memory_6a_20260814_001/transition_manifest.jsonl",
                role="transition_manifest", affected_rows=len(b3_transition_ids), reusable_rows=True,
                training=True, evaluation=False, cache=True, row_recompute=True,
            ),
            _dependency_row(
                artifact=str(Path(settings["parent_exp017"])), role="transition_teacher_and_program_artifact",
                affected_rows=max(len(b3_transition_ids), counts["transition_teacher"]["affected_row_count"]),
                reusable_rows=True, training=True, evaluation=True, checkpoint=True,
            ),
        ]
    )
    for key in ("parent_exp018", "parent_exp019", "parent_exp020", "parent_exp021"):
        rows.append(
            _dependency_row(
                artifact=str(settings[key]), role="representation_or_target_model_artifact",
                affected_rows=35 + len(b3_transition_ids), reusable_rows=True,
                training=True, evaluation=True, checkpoint=True,
            )
        )
    for key in ("parent_exp022", "parent_exp023", "parent_exp024a", "parent_exp024r", "parent_exp024r2", "parent_exp024r3"):
        rows.append(
            _dependency_row(
                artifact=str(settings[key]), role="procedural_or_replay_report_artifact",
                affected_rows=35 + len(b3_transition_ids), reusable_rows=True,
                training=False, evaluation=True, report=True,
            )
        )
    graph = {
        "format": "identity_reconciliation_artifact_dependency_graph_7a_v1",
        "affected_task_ids": list(AFFECTED_TASK_IDS),
        "b0a8eae_3_memory_id": b3_memory,
        "b0a8eae_3_transition_count": len(b3_transition_ids),
        "direct_cache_counts": counts,
        "artifact_count": len(rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)),
        "rows": rows,
        "trained_checkpoint_clean_claim_allowed": False,
    }
    scoring_rows = sum(
        counts[key]["affected_row_count"] for key in ("raw_teacher", "stage_c1_response", "pair_5d", "transition_teacher")
    )
    rate = float(settings["estimates"]["h100_hourly_row_rate"])
    expected_h100 = scoring_rows / rate if rate else 0.0
    estimates = {
        "format": "identity_reconciliation_minimum_recompute_estimate_7a_v1",
        "qwen_scoring_rows_to_recompute": scoring_rows,
        "state_representation_rows_to_recompute": 35,
        "memory_representation_rows_to_recompute": 2,
        "transition_representation_rows_to_recompute": len(b3_transition_ids),
        "checkpoints_requiring_retraining": checkpoint_dirs,
        "h100_hours": {
            "best": expected_h100 * float(settings["estimates"]["best_case_multiplier"]),
            "expected": expected_h100,
            "conservative": expected_h100 * float(settings["estimates"]["conservative_multiplier"]),
        },
        "wall_time_hours": {
            "best": expected_h100 * 0.8,
            "expected": expected_h100 * 1.2,
            "conservative": expected_h100 * 2.0,
        },
        "storage_bytes_expected": sum(
            path.stat().st_size
            for path in (teacher, stage_c1, pair_5d, transition_cache)
            if path.is_file()
        ),
        "minimum_chain": [
            "regenerate_affected_state_memory_transition_representations",
            "recompute_only_invalid_raw_teacher_rows",
            "rebuild_stage_b_labels_and_manifests",
            "retrain_only_models_required_by_current_v4_hypothesis",
            "rerun_procedural_coverage",
            "resume_one_step_causal_audit_only_after_semantic_replay",
        ],
        "full_historical_v3_rerun_required": False,
    }
    return graph, estimates


def _build_replay_manifests(
    *,
    artifact_dir: Path,
    settings: Mapping[str, Any],
    remediations: Mapping[str, str],
    attempt_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    exp024r = Path(settings["parent_exp024r"])
    environment = _load_json(exp024r / "environment_provenance.json")
    base_manifest = _load_json(Path(str(environment["active_contract_manifest"])))
    base_by_id = {str(row["state_example_id"]): row for row in base_manifest["rows"]}
    audit_rows = _manifest_rows(_load_json(Path(settings["parent_exp022"]) / "one_step_query_manifest.json"))
    audit = build_reconciled_audit_manifest(audit_rows, remediations)
    official_root = Path(settings["snapshots"]["official_010_data_root"])
    contract_dir = (
        artifact_dir
        / "private"
        / "reconciled_replay_contracts"
        / _safe_name(attempt_id)
    )
    contract_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for state in audit["rows"]:
        state_id = str(state["state_example_id"])
        source_row = base_by_id[state_id]
        contract = _load_json(Path(str(source_row["contract_path"])))
        task_id = str(state["task_id"])
        if remediations.get(task_id) == REPAIR_ACTION:
            contract = _reconcile_legacy_contract_query(
                contract,
                _full_query(
                    _spec_fields(official_root / "tasks" / task_id / "specs.json")
                ),
            )
        path = contract_dir / f"{_safe_name(state_id)}.json"
        atomic_write_json(path, contract)
        rows.append(
            {
                "state_example_id": state_id,
                "task_id": task_id,
                "step_id": int(state["step_id"]),
                "history_step_count": int(state["step_id"]) - 1,
                "contract_path": str(path),
                "contract_sha256": canonical_hash(contract),
                "source_contract_sha256": str(source_row["contract_sha256"]),
                "query_header_reconciled": remediations.get(task_id) == REPAIR_ACTION,
                "actions_sha256": str(contract["actions_sha256"]),
            }
        )
    contracts = {
        "format": "identity_reconciled_replay_contract_manifest_7a_v1",
        "state_count": len(rows),
        "task_count": len({row["task_id"] for row in rows}),
        "prior_observation_count": sum(row["history_step_count"] for row in rows),
        "rows": rows,
    }
    contracts["manifest_sha256"] = canonical_hash(contracts)
    source_sentinel = _load_json(exp024r / "sentinel_manifest.json")
    retained_ids = {str(row["state_example_id"]) for row in audit["rows"]}
    sentinel_rows = [
        dict(row) for row in source_sentinel["rows"] if str(row["state_example_id"]) in retained_ids
    ]
    sentinel = {
        "format": "identity_reconciled_sentinel_manifest_7a_v1",
        "selection_rule": "immutable_exp024r_sentinel_minus_preregistered_whole_task_quarantine",
        "state_count": len(sentinel_rows),
        "task_count": len({str(row["task_id"]) for row in sentinel_rows}),
        "prior_observation_count": sum(int(row["step_id"]) - 1 for row in sentinel_rows),
        "replacement_state_count": 0,
        "rows": sentinel_rows,
    }
    sentinel["manifest_sha256"] = canonical_hash(sentinel)
    return audit, sentinel, contracts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp025a")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7a"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    policy_path = args.artifact_dir / "remediation_policy_manifest.json"
    policy = _load_json(policy_path)
    remediations = {str(key): str(value) for key, value in policy["task_remediations"].items()}
    if set(remediations) != set(AFFECTED_TASK_IDS):
        raise ValueError("Remediation policy does not cover both affected tasks")
    source = Path(settings["source_data"])
    candidate = args.artifact_dir / "private" / "candidate_repaired_corpus"
    corpus_dir = Path(settings["reconciled_corpus_dir"])
    config_hash = sha256_file(args.config)
    data_hashes = {
        "policy": sha256_file(policy_path),
        "candidate_memory": sha256_file(candidate / "memory_records.jsonl"),
        "candidate_decisions": sha256_file(candidate / "decision_examples.jsonl"),
        "original_memory": sha256_file(source / "memory_records.jsonl"),
        "original_decisions": sha256_file(source / "decision_examples.jsonl"),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="materialize_structural_corpus_dependencies_and_replay_manifests",
        command=[str(value) for value in sys.argv], local_head=args.local_head,
        github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=config_hash,
        data_manifest_hashes=data_hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        quarantined = {task for task, action in remediations.items() if action == QUARANTINE_ACTION}
        corpus_dir.mkdir(parents=True, exist_ok=True)
        if quarantined:
            _filter_jsonl_tasks(candidate / "memory_records.jsonl", corpus_dir / "memory_records.jsonl", quarantined)
            _filter_jsonl_tasks(candidate / "decision_examples.jsonl", corpus_dir / "decision_examples.jsonl", quarantined)
        else:
            _atomic_copy_or_validate(candidate / "memory_records.jsonl", corpus_dir / "memory_records.jsonl")
            _atomic_copy_or_validate(candidate / "decision_examples.jsonl", corpus_dir / "decision_examples.jsonl")
        records = load_memory_records(corpus_dir / "memory_records.jsonl")
        examples = load_decision_examples(corpus_dir / "decision_examples.jsonl")
        original_records = load_memory_records(source / "memory_records.jsonl")
        official_root = Path(settings["snapshots"]["official_010_data_root"])
        identity_errors = []
        memory_rows = []
        for line, record in enumerate(records, start=1):
            task_id = str(record.task_id)
            expected = _spec_fields(official_root / "tasks" / task_id / "specs.json")
            actual = parse_full_demo_query(str(record.raw_trajectory["query"]))
            if actual != expected:
                identity_errors.append(task_id)
            memory_rows.append(
                {
                    "line": line, "task_id": task_id, "memory_id": str(record.memory_id),
                    "query_sha256": text_sha256(str(record.raw_trajectory["query"])),
                    "official_field_sha256": _field_hashes(expected),
                    "action_sha256": canonical_hash([row["response"] for row in record.raw_trajectory["steps"]]),
                    "observation_sha256": canonical_hash([row["observation"] for row in record.raw_trajectory["steps"]]),
                }
            )
        records_by_task = {str(record.task_id): record for record in records}
        decision_rows = []
        decision_errors = []
        for index, example in enumerate(examples):
            task_id = str(example.metadata["task_id"])
            _, query, history = _parse_appworld_state_text(example.state_text)
            if query != str(records_by_task[task_id].raw_trajectory["query"]):
                decision_errors.append(state_example_id(index, example))
            decision_rows.append(
                {
                    "line": index + 1, "state_example_id": state_example_id(index, example),
                    "task_id": task_id, "step_id": int(example.step_id),
                    "state_sha256": text_sha256(example.state_text),
                    "target_sha256": text_sha256(example.target_text),
                    "history_step_count": len(history),
                    "roles": ["query", "history", "target_action"],
                    "leakage_keys": sorted(example_leakage_keys(example)),
                }
            )
        transitions = [transition for record in records for transition in extract_decision_transitions(record)]
        transition_validation = validate_transition_extraction(records, transitions)
        transition_rows = [transition.to_manifest_row() for transition in transitions]
        write_jsonl(corpus_dir / "transition_manifest.jsonl", transition_rows)
        original_transition_rows = _load_jsonl(Path(settings["parent_exp017"]) / "transition_manifest.jsonl")
        old_b3 = {
            int(row["step_index"]): str(row["transition_id"])
            for row in original_transition_rows if str(row["parent_task_id"]) == "b0a8eae_3"
        }
        new_b3 = {
            int(row["step_index"]): str(row["transition_id"])
            for row in transition_rows if str(row["parent_task_id"]) == "b0a8eae_3"
        }
        transition_change = {
            "old_count": len(old_b3), "new_count": len(new_b3),
            "changed_transition_id_count": sum(old_b3.get(step) != value for step, value in new_b3.items()),
            "mapping": [
                {"step_id": step, "old_transition_id": old_b3.get(step), "new_transition_id": new_b3.get(step)}
                for step in sorted(set(old_b3) | set(new_b3))
            ],
        }
        split = _load_json(Path(settings["split_manifest"]))
        split_manifest = {
            "format": "identity_reconciled_train_validation_task_manifest_7a_v1",
            "train_task_ids": [task for task in split["train_task_ids"] if task not in quarantined],
            "validation_task_ids": [task for task in split["validation_task_ids"] if task not in quarantined],
            "quarantined_task_ids": sorted(quarantined),
        }
        split_manifest["manifest_sha256"] = canonical_hash(split_manifest)
        atomic_write_json(corpus_dir / "train_validation_task_manifest.json", split_manifest)
        atomic_write_json(corpus_dir / "memory_record_manifest.json", {"format": "identity_reconciled_memory_manifest_7a_v1", "rows": memory_rows})
        atomic_write_json(corpus_dir / "decision_example_manifest.json", {"format": "identity_reconciled_decision_manifest_7a_v1", "rows": decision_rows})
        leakage = {
            "format": "identity_reconciled_leakage_lineage_manifest_7a_v1",
            "decision_count": len(decision_rows), "transition_count": len(transition_rows),
            "decision_key_sha256": canonical_hash([row["leakage_keys"] for row in decision_rows]),
            "transition_key_sha256": canonical_hash([sorted(transition_leakage_keys(row)) for row in transition_rows]),
            "quarantined_task_present": any(
                task in json.dumps({"decisions": decision_rows, "transitions": transition_rows})
                for task in quarantined
            ),
        }
        atomic_write_json(corpus_dir / "leakage_lineage_manifest.json", leakage)
        structural_passed = bool(
            not identity_errors and not decision_errors and transition_validation["passed"]
            and not leakage["quarantined_task_present"]
            and len({row["state_example_id"] for row in decision_rows}) == len(decision_rows)
            and len({row["transition_id"] for row in transition_rows}) == len(transition_rows)
        )
        branch = select_corpus_decision_branch(remediations, structural_validation_passed=structural_passed)
        output_hashes = {
            name: sha256_file(corpus_dir / filename)
            for name, filename in {
                "memory_records": "memory_records.jsonl", "decision_examples": "decision_examples.jsonl",
                "transitions": "transition_manifest.jsonl", "task_split": "train_validation_task_manifest.json",
                "memory_manifest": "memory_record_manifest.json", "decision_manifest": "decision_example_manifest.json",
                "leakage_manifest": "leakage_lineage_manifest.json",
            }.items()
        }
        lineage = canonical_hash(
            {"version": RECONCILED_CORPUS_VERSION, "parent_hashes": data_hashes, "policy": remediations, "outputs": output_hashes}
        )
        structural = {
            "format": "identity_reconciled_structural_validation_7a_v1",
            "passed": structural_passed, "decision_branch": branch,
            "task_count": len(records), "decision_count": len(examples),
            "train_task_count": len(split_manifest["train_task_ids"]),
            "validation_task_count": len(split_manifest["validation_task_ids"]),
            "transition_count": len(transitions), "identity_errors": identity_errors,
            "decision_parent_errors": decision_errors, "transition_validation": transition_validation,
            "transition_change": transition_change, "lineage_sha256": lineage,
            "output_hashes": output_hashes, "unaffected_task_count": len(records) - len(set(AFFECTED_TASK_IDS) - quarantined),
        }
        atomic_write_json(corpus_dir / "structural_validation.json", structural)
        summary = {
            "format": RECONCILED_CORPUS_VERSION,
            "parent_corpus": str(source), "remediations": remediations,
            "task_count": len(records), "decision_count": len(examples), "transition_count": len(transitions),
            "train_task_count": len(split_manifest["train_task_ids"]), "validation_task_count": len(split_manifest["validation_task_ids"]),
            "lineage_sha256": lineage, "structural_validation_passed": structural_passed,
            "historical_artifacts_rewritten": False,
        }
        atomic_write_json(corpus_dir / "summary.json", summary)
        graph, estimates = _build_dependency_graph(
            settings=settings, original_records=original_records, original_transitions=original_transition_rows
        )
        atomic_write_json(args.artifact_dir / "artifact_dependency_graph.json", graph)
        atomic_write_json(args.artifact_dir / "minimum_recompute_estimate.json", estimates)
        audit, sentinel, contracts = _build_replay_manifests(
            artifact_dir=args.artifact_dir,
            settings=settings,
            remediations=remediations,
            attempt_id=args.attempt_id,
        )
        atomic_write_json(args.artifact_dir / "reconciled_one_step_manifest.json", audit)
        atomic_write_json(args.artifact_dir / "reconciled_sentinel_manifest.json", sentinel)
        atomic_write_json(args.artifact_dir / "reconciled_replay_contract_manifest.json", contracts)
        finalization = {
            "format": "identity_reconciliation_structural_finalization_7a_v1",
            "decision_branch": branch, "clean_corpus_ready": structural_passed,
            "corpus_dir": str(corpus_dir), "corpus_lineage_sha256": lineage,
            "structural_validation": structural,
            "reconciled_audit_states": audit["state_count"], "reconciled_audit_tasks": audit["task_count"],
            "sentinel_states": sentinel["state_count"], "dependency_artifact_count": graph["artifact_count"],
            "training_and_generation_remain_blocked": True,
        }
        output = args.artifact_dir / "structural_finalization_summary.json"
        atomic_write_json(output, finalization)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(finalization, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
