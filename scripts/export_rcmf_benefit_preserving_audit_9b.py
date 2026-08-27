"""Export Git-safe, reconstructible audit records for EXP-031B.

Unredacted AppWorld observations, model responses, and tensor shards remain on
Lambda.  This exporter materializes credential/JWT-redacted review traces,
compacts the lossless query/slot tensors, and emits the machine-readable result
tables required by the EXP-031B charter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from export_rcmf_joint_full_bank_audit_9a import (  # noqa: E402
    atomic_json,
    atomic_jsonl,
    atomic_torch,
    first37_record,
    load_deployment,
    load_json,
    materialized_step,
    raw_tensor_sha256,
    redact,
    register_asset,
    register_sensitive_observation,
    sha_file,
    sha_text,
    top_contributions,
    verify_git_safe_redaction,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger  # noqa: E402

RUN_UUID = "rcmf_benefit_preserving_calibration_9b_20260827_001"
FORMAT = "rcmf_benefit_preserving_detailed_audit_9b_v1"
GLOBAL_SEED = 25101
EXPECTED_CRITICAL_ROWS = 308
EXPECTED_HELDOUT_ROWS = 882
EXPECTED_FIRST37_TASKS = 37
EXPECTED_FIRST37_STEPS = {"D1": 1071, "D2": 923}
SOURCE_COMMIT = "49f03a2b758f93069b768e3af79fbf1f6282befd"


def _canonical_sha(value: Any) -> str:
    return sha_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _load_complete_rows(directory: Path, expected: int) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        row = load_json(path)
        if row.get("status") == "complete":
            rows.append((path, row))
    if len(rows) != expected:
        raise ValueError(f"Expected {expected} complete rows in {directory}, found {len(rows)}")
    return rows


def _register_one_step_secrets(row: Mapping[str, Any]) -> None:
    code = row.get("executed_code", row.get("extracted_code", ""))
    register_sensitive_observation(code, row.get("complete_environment_observation", ""))
    for turn in row.get("trajectory_so_far", ()):
        register_sensitive_observation(
            turn.get("response", ""), turn.get("observation", "")
        )
    for observation in row.get("actual_replay_observations", ()):
        if isinstance(observation, Mapping):
            register_sensitive_observation(
                observation.get("action", ""), observation.get("observation", "")
            )


def _register_first37_secrets(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step["exact_executed_code"], step["complete_environment_observation"]
        )
        for turn in step["complete_trajectory_so_far"]:
            register_sensitive_observation(
                turn.get("response", ""), turn.get("observation", "")
            )


def _static_and_dynamic(
    messages: Sequence[Mapping[str, Any]],
    assets: dict[str, Any],
    identity: str | None = None,
) -> tuple[str, Mapping[str, Any]]:
    if not messages:
        raise ValueError("Model message array is empty")
    static = [dict(row) for row in messages[:-1]]
    identity = identity or _canonical_sha(static)
    register_asset(assets, static, identity)
    return identity, dict(messages[-1])


def _load_compact_tensor(
    artifact: Path,
    expected_file_sha256: str,
    query_sha256: str | None,
    slots_sha256: str | None,
) -> dict[str, Any]:
    if sha_file(artifact) != expected_file_sha256:
        raise ValueError(f"Tensor artifact hash differs: {artifact}")
    payload = torch.load(artifact, map_location="cpu", weights_only=False)
    query = payload.get("query")
    slots = payload["slots"]
    if query is not None and query_sha256 and raw_tensor_sha256(query) != query_sha256:
        raise ValueError(f"Query tensor hash differs: {artifact}")
    if slots_sha256 and raw_tensor_sha256(slots) != slots_sha256:
        raise ValueError(f"Slot tensor hash differs: {artifact}")
    return {
        "query": None if query is None else query.detach().cpu(),
        "slots": slots.detach().cpu(),
    }


def _compact_first37_tensor(step: Mapping[str, Any]) -> dict[str, Any]:
    field = step["field"]
    payload = _load_compact_tensor(
        Path(str(field["tensor_artifact"])),
        str(field["tensor_artifact_sha256"]),
        None if field["query"] is None else str(field["query"].get("sha256")),
        str(field["slots"].get("sha256")),
    )
    return payload


def _offline_contribution(
    query: torch.Tensor | None,
    deployment: Mapping[str, Any],
    shuffled: bool,
) -> list[dict[str, Any]]:
    if query is None:
        return []
    return top_contributions(query, deployment, shuffled=shuffled)


def _critical_record(
    source_path: Path,
    row: Mapping[str, Any],
    tensor_key: str,
    assets: dict[str, Any],
    contribution: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    static_id, dynamic = _static_and_dynamic(row["model_messages"], assets)
    field = dict(row["field"])
    field["top_memory_contributions"] = {
        "status": "computed_offline_after_run",
        "not_used_by_model_or_field_read": True,
        "ranking": list(contribution),
    }
    return redact(
        {
            "format": FORMAT,
            "audit_scope": "critical_live_one_step",
            "task_id": row["task_id"],
            "step_id": row["step_id"],
            "group": row["group"],
            "condition_key": row["condition_key"],
            "candidate_id": row["candidate_id"],
            "candidate": row["candidate"],
            "mechanism": row["mechanism"],
            "static_prompt_asset_sha256": static_id,
            "dynamic_message": dynamic,
            "reconstruction_rule": "static asset plus dynamic message",
            "rendered_messages_raw_sha256": row["rendered_messages_sha256"],
            "source_rendered_messages_sha256": row["source_rendered_messages_sha256"],
            "exact_stored_prompt_hash_match": row["exact_stored_prompt_hash_match"],
            "actual_replay_observations": row["actual_replay_observations"],
            "prompt_tokens": row["prompt_tokens"],
            "context_limit": row["context_limit"],
            "truncation_applied": row["truncation_applied"],
            "generation": {
                "model": "Qwen/Qwen3-8B",
                "checkpoint_sha256": row["checkpoint_sha256"],
                "seed": row["appworld_random_seed"],
                "temperature": 0.0,
                "top_p": 1.0,
                "max_new_tokens": 512,
                "do_sample": False,
                "enable_thinking": False,
                "raw_model_response": row["raw_model_response"],
                "generated_token_ids": row["generated_token_ids"],
                "fixed_model_response": row["fixed_model_response"],
                "extracted_code": row["extracted_code"],
            },
            "live_executed": row["live_executed"],
            "execution_exception": row["execution_exception"],
            "complete_environment_observation": row["complete_environment_observation"],
            "normalized_observation": row["normalized_observation"],
            "metrics": row["metrics"],
            "same_world_execution": row["same_world_execution"],
            "same_python_namespace": row["same_python_namespace"],
            "field": field,
            "field_tensor_bundle_key": tensor_key,
            "reader_audit": row["reader_audit"],
            "runtime_memory_retrieval": row["runtime_memory_retrieval"],
            "runtime_per_memory_scoring": row["runtime_per_memory_scoring"],
            "student_prompt_contains_raw_memory": row["student_prompt_contains_raw_memory"],
            "raw_lambda_source": str(source_path),
            "raw_lambda_source_sha256": sha_file(source_path),
        }
    )


def _heldout_record(
    source_path: Path,
    row: Mapping[str, Any],
    tensor_key: str,
    assets: dict[str, Any],
    contribution: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    static_id, dynamic = _static_and_dynamic(
        row["model_messages"], assets, str(row["static_prompt_asset_sha256"])
    )
    field = dict(row["field"])
    field["top_memory_contributions_offline"] = list(contribution)
    return redact(
        {
            "format": FORMAT,
            "audit_scope": "heldout_live_one_step",
            "task_id": row["source_task_id"],
            "state_id": row["source_state_id"],
            "step_id": row["source_step_id"],
            "condition_key": row["condition_key"],
            "control": row["control"],
            "candidate_id": row["candidate_id"],
            "candidate": row["candidate"],
            "field_query_state_id": row["field_query_state_id"],
            "prompt_profile": row["prompt_profile"],
            "static_prompt_asset_sha256": static_id,
            "task_message": row["task_message"],
            "trajectory_so_far": row["trajectory_so_far"],
            "dynamic_message": dynamic,
            "reconstruction_rule": "static asset plus dynamic message",
            "rendered_messages_raw_sha256": row["rendered_messages_sha256"],
            "prompt_tokens": row["prompt_tokens"],
            "context_limit": row["context_limit"],
            "truncation_applied": row["truncation_applied"],
            "generation": {
                "model": row["model_name"],
                "tokenizer": row["tokenizer_identity"],
                "checkpoint_sha256": row["checkpoint_sha256"],
                "seed": row["seed"],
                "temperature": row["temperature"],
                "top_p": row["top_p"],
                "max_new_tokens": row["max_new_tokens"],
                "do_sample": row["do_sample"],
                "enable_thinking": row["enable_thinking"],
                "raw_model_response": row["raw_model_response"],
                "generated_token_ids": row["generated_token_ids"],
                "extracted_code": row["extracted_code"],
                "automatically_repaired_response": row["automatically_repaired_response"],
                "automatically_repaired_code": row["automatically_repaired_code"],
                "executed_code": row["executed_code"],
            },
            "execution_exception": row["execution_exception"],
            "complete_environment_observation": row["complete_environment_observation"],
            "normalized_observation": row["normalized_observation"],
            "task_completed_status": row["task_completed_status"],
            "metrics": row["metrics"],
            "field": field,
            "field_tensor_bundle_key": tensor_key,
            "reader_audit": row["reader_audit"],
            "same_world_execution": row["same_world_execution"],
            "same_python_namespace": row["same_python_namespace"],
            "runtime_memory_retrieval": row["runtime_memory_retrieval"],
            "runtime_per_memory_scoring": row["runtime_per_memory_scoring"],
            "student_prompt_contains_raw_memory": row["student_prompt_contains_raw_memory"],
            "raw_lambda_source": str(source_path),
            "raw_lambda_source_sha256": sha_file(source_path),
        }
    )


def _first_code_divergence(left: Mapping[str, Any], right: Mapping[str, Any]) -> int | None:
    left_steps = left["steps"]
    right_steps = right["steps"]
    for offset in range(max(len(left_steps), len(right_steps))):
        if offset >= len(left_steps) or offset >= len(right_steps):
            return offset + 1
        if left_steps[offset]["exact_executed_code"] != right_steps[offset]["exact_executed_code"]:
            return offset + 1
    return None


def _selected_step(task: Mapping[str, Any], step_id: int) -> Mapping[str, Any] | None:
    return next((row for row in task["steps"] if int(row["step_id"]) == step_id), None)


def _comparison_payload(
    task_id: str,
    tasks: Mapping[str, Mapping[str, Any]],
    contributions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    divergence = {
        "D0_vs_D1": _first_code_divergence(tasks["D0"], tasks["D1"]),
        "D1_vs_D2": _first_code_divergence(tasks["D1"], tasks["D2"]),
    }
    selected = {
        value for value in divergence.values() if value is not None
    }
    selected.update(int(tasks[c]["steps"][-1]["step_id"]) for c in ("D0", "D1", "D2"))
    materialized = {}
    for step_id in sorted(selected):
        materialized[str(step_id)] = {}
        for condition in ("D0", "D1", "D2"):
            step = _selected_step(tasks[condition], step_id)
            if step is None:
                materialized[str(step_id)][condition] = None
                continue
            row = materialized_step(step)
            row["offline_top_memory_contributions"] = list(
                contributions.get(f"{condition}:{task_id}:{step_id}", ())
            )
            materialized[str(step_id)][condition] = row
    return {
        "format": FORMAT,
        "task_id": task_id,
        "outcomes": {
            condition: {
                "success": bool(tasks[condition]["success"]),
                "steps": int(tasks[condition]["step_count"]),
                "wall_seconds": float(tasks[condition]["wall_seconds"]),
            }
            for condition in ("D0", "D1", "D2")
        },
        "first_code_divergence": divergence,
        "materialized_first_divergence_and_terminal_steps": materialized,
        "single_seed_development_diagnostic": True,
    }


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _attempt_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["attempt_id"])][str(row["event"])] = row
    durations = {}
    for attempt_id, events in grouped.items():
        if "start" in events and "end" in events:
            durations[attempt_id] = (
                _timestamp(str(events["end"]["end_timestamp_utc"]))
                - _timestamp(str(events["start"]["start_timestamp_utc"]))
            ).total_seconds()
    failed = sorted(
        attempt_id
        for attempt_id, events in grouped.items()
        if int(events.get("end", {}).get("exit_code", 1)) != 0
    )
    return {
        "attempt_count": len(grouped),
        "ledger_rows": len(rows),
        "all_attempts_closed": all("start" in value and "end" in value for value in grouped.values()),
        "failed_attempt_ids": failed,
        "failed_attempt_count": len(failed),
        "duration_seconds_by_attempt": durations,
    }


def _machine_summary(
    artifact_root: Path,
    stage8b: Mapping[str, Any],
    stage8c: Mapping[str, Any],
    final: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    attempt_info = _attempt_summary(attempts)
    accepted_gpu_attempts = [
        "exp031b-stage8a-equivalence-001",
        "exp031b-stage8a-profile-001",
        "exp031b-stage8a-forward-smoke-001",
        "exp031b-stage8a-diagnose-002",
        "exp031b-stage8b-v2-baseline-001",
        "exp031b-stage8b-v2-smoke-002",
        "exp031b-stage8b-v2-live-001",
        "exp031b-stage8c-smoke-001",
        "exp031b-stage8c-live-001",
        "exp031b-stage8d-l1-smoke-d1-001",
        "exp031b-stage8d-l1-smoke-d2-001",
        "exp031b-stage8d-l1-run-d1-001",
        "exp031b-stage8d-l1-run-d2-001",
    ]
    preserved_invalid_gpu_attempts = [
        "exp031b-stage8a-diagnose-001",
        "exp031b-stage8b-live-001",
        "exp031b-stage8b-smoke-001",
        "exp031b-stage8b-v2-smoke-001",
    ]
    durations = attempt_info["duration_seconds_by_attempt"]
    accepted_seconds = sum(float(durations[name]) for name in accepted_gpu_attempts)
    invalid_seconds = sum(float(durations[name]) for name in preserved_invalid_gpu_attempts)
    all_times = [
        _timestamp(str(row.get("start_timestamp_utc", row.get("end_timestamp_utc"))))
        for row in attempts
    ]
    return {
        "format": "rcmf_benefit_preserving_result_summary_9b_v1",
        "run_uuid": RUN_UUID,
        "source_commit": SOURCE_COMMIT,
        "global_seed": GLOBAL_SEED,
        "scientific_decision": final["scientific_decision"],
        "decision_branch": "benefit_preserving_calibration_stop_route",
        "mechanical_exp031a_label": final["mechanical_exp031a_label"],
        "candidate": final["candidate"],
        "success_count": final["success_count"],
        "success_ids": final["success_ids"],
        "gates": final["gates"],
        "stop_reasons": final["stop_reasons"],
        "preserved_original_gains": final["preserved_original_gains"],
        "lost_original_gains": final["lost_original_gains"],
        "preserved_retained_successes": final["preserved_retained_successes"],
        "family_preservation": final["family_preservation"],
        "recovered_original_d1_losses": final["recovered_original_d1_losses"],
        "equivalent_new_net_gains": final["equivalent_new_net_gains"],
        "stage8b": redact(stage8b),
        "stage8c": redact(stage8c),
        "attempts": attempt_info,
        "accepted_h100_active_hours": accepted_seconds / 3600.0,
        "preserved_invalid_h100_active_hours": invalid_seconds / 3600.0,
        "total_accounted_h100_active_hours": (accepted_seconds + invalid_seconds) / 3600.0,
        "wall_span_hours": (max(all_times) - min(all_times)).total_seconds() / 3600.0,
        "artifact_root": str(artifact_root),
        "artifact_size_bytes": sum(
            path.stat().st_size for path in artifact_root.rglob("*") if path.is_file()
        ),
        "second_candidate_pair_run": False,
        "no_retraining": True,
        "runtime_retrieval": False,
        "hard_memory_gate": False,
        "single_seed_development_result": True,
    }


def _load_attempts(artifact_root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (artifact_root / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _refresh_result_attempts(artifact_root: Path, result_root: Path) -> None:
    attempts = _load_attempts(artifact_root)
    summary = load_json(result_root / "summary.json")
    attempt_info = _attempt_summary(attempts)
    all_times = [
        _timestamp(str(row.get("start_timestamp_utc", row.get("end_timestamp_utc"))))
        for row in attempts
    ]
    summary["attempts"] = attempt_info
    summary["wall_span_hours"] = (
        max(all_times) - min(all_times)
    ).total_seconds() / 3600.0
    atomic_jsonl(result_root / "attempts.jsonl", [redact(row) for row in attempts])
    atomic_json(result_root / "summary.json", summary)
    verification = verify_git_safe_redaction(result_root)
    if verification["registered_sensitive_observation_leak_count"] != 0:
        raise RuntimeError("Refreshed result attempts failed redaction verification")

def export(artifact_root: Path, parent_root: Path, audit_root: Path, result_root: Path) -> dict[str, Any]:
    if audit_root.exists() or result_root.exists():
        raise FileExistsError("Refusing to overwrite an existing EXP-031B audit/result root")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-031B temporary export root requires review")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)

    deployment = load_deployment(parent_root)
    critical_rows = _load_complete_rows(
        artifact_root / "stage_8b_exact_prompt_v2/condition_outputs",
        EXPECTED_CRITICAL_ROWS,
    )
    heldout_rows = _load_complete_rows(
        artifact_root / "stage_8c_heldout_live/condition_outputs",
        EXPECTED_HELDOUT_ROWS,
    )
    first37_paths = {
        condition: sorted(
            (artifact_root / f"stage_8d_first37/L1/conditions/{condition}/task_results").glob("*.json")
        )
        for condition in ("D1", "D2")
    }
    if any(len(paths) != EXPECTED_FIRST37_TASKS for paths in first37_paths.values()):
        raise ValueError("EXP-031B first37 task accounting differs")

    for _, row in critical_rows + heldout_rows:
        _register_one_step_secrets(row)
    for paths in first37_paths.values():
        for path in paths:
            _register_first37_secrets(load_json(path))

    assets: dict[str, Any] = {}
    raw_static = load_json(
        artifact_root / "stage_8d_first37/L1/raw_audit/static_prompt_assets.json"
    )
    for identity, row in raw_static["assets"].items():
        register_asset(assets, row["messages"], str(identity))
    tensor_bundle: dict[str, Any] = {
        "format": "rcmf_benefit_preserving_compact_field_tensors_9b_v1",
        "critical_live": {},
        "heldout_live": {},
        "first37": {},
    }

    critical_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    critical_table = []
    for source_path, row in critical_rows:
        field = row["field"]
        tensor = _load_compact_tensor(
            Path(str(field["tensor_artifact"])),
            str(field["tensor_artifact_sha256"]),
            str(field["query_sha256"]),
            str(field["slots_sha256"]),
        )
        key = str(row["condition_key"])
        tensor_bundle["critical_live"][key] = tensor
        contribution = _offline_contribution(
            tensor["query"], deployment, shuffled=str(row["mechanism"]) == "key_payload_shuffle"
        )
        safe = _critical_record(source_path, row, key, assets, contribution)
        critical_groups[str(row["candidate_id"])].append(safe)
        critical_table.append(
            redact(
                {
                    "condition_key": key,
                    "task_id": row["task_id"],
                    "step_id": row["step_id"],
                    "group": row["group"],
                    "candidate_id": row["candidate_id"],
                    "mechanism": row["mechanism"],
                    "metrics": row["metrics"],
                    "execution_exception": row["execution_exception"],
                    "rendered_messages_sha256": row["rendered_messages_sha256"],
                    "tensor_artifact_sha256": field["tensor_artifact_sha256"],
                }
            )
        )
    for candidate_id, rows in critical_groups.items():
        rows.sort(key=lambda value: (str(value["task_id"]), int(value["step_id"]), str(value["condition_key"])))
        atomic_jsonl(audit_tmp / "critical_live" / f"{candidate_id}.jsonl", rows)

    heldout_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    heldout_table = []
    for source_path, row in heldout_rows:
        field = row["field"]
        tensor = _load_compact_tensor(
            Path(str(field["slot_artifact"])),
            str(field["slot_artifact_sha256"]),
            str(field["query_sha256"]),
            str(field["slots_sha256"]),
        )
        key = str(row["condition_key"])
        tensor_bundle["heldout_live"][key] = tensor
        contribution = _offline_contribution(
            tensor["query"], deployment, shuffled=str(row["control"]) == "L2_key_payload_shuffle"
        )
        safe = _heldout_record(source_path, row, key, assets, contribution)
        heldout_groups[(str(row["candidate_id"]), str(row["source_task_id"]))].append(safe)
        heldout_table.append(
            redact(
                {
                    "condition_key": key,
                    "task_id": row["source_task_id"],
                    "state_id": row["source_state_id"],
                    "step_id": row["source_step_id"],
                    "candidate_id": row["candidate_id"],
                    "control": row["control"],
                    "metrics": row["metrics"],
                    "execution_exception": row["execution_exception"],
                    "rendered_messages_sha256": row["rendered_messages_sha256"],
                    "tensor_artifact_sha256": field["slot_artifact_sha256"],
                }
            )
        )
    for (candidate_id, task_id), rows in heldout_groups.items():
        rows.sort(key=lambda value: (str(value["state_id"]), str(value["control"])))
        atomic_jsonl(audit_tmp / "heldout_live" / candidate_id / f"{task_id}.jsonl", rows)

    parent_d0_paths = sorted((parent_root / "first37/conditions/D0/task_results").glob("*.json"))
    if len(parent_d0_paths) != EXPECTED_FIRST37_TASKS:
        raise ValueError("Immutable EXP-031A D0 task accounting differs")
    first37_tasks: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    contribution_rows: dict[str, Sequence[Mapping[str, Any]]] = {}
    step_counts = {}
    for path in parent_d0_paths:
        task = load_json(path)
        first37_tasks[str(task["task_id"])]["D0"] = task
    for condition, paths in first37_paths.items():
        count = 0
        for task_path in paths:
            task = load_json(task_path)
            task_id = str(task["task_id"])
            first37_tasks[task_id][condition] = task
            safe_steps = []
            for step in task["steps"]:
                step_id = int(step["step_id"])
                key = f"{condition}:{task_id}:{step_id}"
                tensor = _compact_first37_tensor(step)
                tensor_bundle["first37"][key] = tensor
                contribution = _offline_contribution(
                    tensor["query"], deployment, shuffled=condition == "D2"
                )
                contribution_rows[key] = contribution
                safe = first37_record(task_path, task, step, key, contribution)
                safe["format"] = FORMAT
                safe_steps.append(safe)
                count += 1
            atomic_jsonl(audit_tmp / "first37" / condition / f"{task_id}.jsonl", safe_steps)
        if count != EXPECTED_FIRST37_STEPS[condition]:
            raise ValueError(f"Expected {EXPECTED_FIRST37_STEPS[condition]} {condition} steps, found {count}")
        step_counts[condition] = count

    for task_id, tasks in sorted(first37_tasks.items()):
        if set(tasks) != {"D0", "D1", "D2"}:
            raise ValueError(f"Incomplete comparison task {task_id}: {sorted(tasks)}")
        comparison = _comparison_payload(task_id, tasks, contribution_rows)
        atomic_json(audit_tmp / "comparisons" / f"{task_id}.json", comparison)

    tensor_path = audit_tmp / "field_tensors/query_and_slots.pt"
    atomic_torch(tensor_path, tensor_bundle)
    atomic_json(audit_tmp / "static_prompt_assets.json", {"format": FORMAT, "assets": assets})

    attempts = _load_attempts(artifact_root)
    attempts_safe = [redact(row) for row in attempts]
    stage8a = load_json(artifact_root / "stage_8a/candidate_summary.json")
    stage8b = load_json(artifact_root / "stage_8b_exact_prompt_v2/critical_live_summary.json")
    stage8c = load_json(artifact_root / "stage_8c_heldout_live/heldout_live_summary.json")
    final = load_json(artifact_root / "stage_8d_first37/L1/final_summary.json")
    if final["scientific_decision"] != "STOP_ROUTE":
        raise ValueError("EXP-031B final decision differs from STOP_ROUTE")

    atomic_jsonl(result_tmp / "attempts.jsonl", attempts_safe)
    atomic_json(result_tmp / "candidate_matrix.json", redact(stage8a))
    atomic_jsonl(result_tmp / "critical_replays.jsonl", critical_table)
    atomic_jsonl(result_tmp / "heldout_per_state.jsonl", heldout_table)
    atomic_jsonl(result_tmp / "first37_per_task.jsonl", [redact(row) for row in final["per_task"]])
    complexity = {
        "format": "rcmf_benefit_preserving_complexity_9b_v1",
        "memory_count": 499,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "field_shape_independent_of_memory_count": True,
        "write_scans_existing_records": False,
        "conditions": {
            condition: {
                "mean_query_seconds": final["condition_summaries"][condition]["mean_query_seconds"],
                "mean_field_read_seconds": final["condition_summaries"][condition]["mean_field_read_seconds"],
                "steps": final["condition_summaries"][condition]["total_steps"],
            }
            for condition in ("D1", "D2")
        },
    }
    atomic_json(result_tmp / "complexity.json", complexity)
    machine = _machine_summary(artifact_root, stage8b, stage8c, final, attempts)
    atomic_json(result_tmp / "summary.json", machine)

    redaction_verification = verify_git_safe_redaction(audit_tmp)
    result_redaction_verification = verify_git_safe_redaction(result_tmp)
    verification = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "critical_rows": len(critical_rows),
        "heldout_rows": len(heldout_rows),
        "first37_task_rows": 2 * EXPECTED_FIRST37_TASKS,
        "first37_steps": step_counts,
        "comparison_tasks": len(first37_tasks),
        "static_asset_count": len(assets),
        "tensor_bundle_sha256": sha_file(tensor_path),
        "tensor_bundle_bytes": tensor_path.stat().st_size,
        "tensor_bundle_counts": {
            key: len(value) for key, value in tensor_bundle.items() if isinstance(value, dict)
        },
        "audit_redaction": redaction_verification,
        "result_redaction": result_redaction_verification,
        "raw_unredacted_lambda_root": str(artifact_root),
        "raw_unredacted_artifacts_preserved": True,
        "runtime_retrieval": False,
        "hard_memory_gate": False,
        "behavioral_conclusion_ready": True,
    }
    atomic_json(audit_tmp / "verification.json", verification)

    files = []
    for path in sorted(audit_tmp.rglob("*")):
        if path.is_file() and path.name != "index.json":
            files.append(
                {
                    "path": (audit_root / path.relative_to(audit_tmp)).relative_to(REPO_ROOT).as_posix(),
                    "sha256": sha_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "source_commit": SOURCE_COMMIT,
        "artifact_root": str(artifact_root),
        "audit_root": audit_root.relative_to(REPO_ROOT).as_posix(),
        "counts": {
            "critical_live_rows": len(critical_rows),
            "heldout_live_rows": len(heldout_rows),
            "first37_task_rows": 74,
            "first37_steps": step_counts,
            "comparison_tasks": len(first37_tasks),
        },
        "decision": {
            "scientific_decision": final["scientific_decision"],
            "decision_branch": "benefit_preserving_calibration_stop_route",
            "correct_minus_bare": final["L1_correct_minus_D0"],
            "correct_minus_shuffle": final["L1_correct_minus_shuffle"],
            "lost_original_gains": final["lost_original_gains"],
        },
        "reconstruction": {
            "static_assets": "static_prompt_assets.json",
            "step_records": "critical_live/**, heldout_live/**, first37/**",
            "materialized_divergence_and_terminal_steps": "comparisons/**",
            "compact_tensors": "field_tensors/query_and_slots.pt",
            "raw_unredacted_lambda_root": str(artifact_root),
        },
        "verification": "verification.json",
        "files": files,
    }
    atomic_json(audit_tmp / "index.json", index)
    audit_tmp.replace(audit_root)
    result_tmp.replace(result_root)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--parent-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="none")
    parser.add_argument(
        "--audit-root",
        type=Path,
        default=REPO_ROOT / "research/audits" / RUN_UUID,
    )
    parser.add_argument(
        "--result-root",
        type=Path,
        default=REPO_ROOT / "research/results/exp031b_rcmf_benefit_preserving_calibration",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if os.name != "nt" and not os.path.ismount("/lambda/nfs/rcmf-persist"):
        raise RuntimeError("Persistent filesystem is not mounted")
    existing_attempts = {
        str(row["attempt_id"]) for row in _load_attempts(args.artifact_root)
    }
    if args.attempt_id in existing_attempts:
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    data_hashes = {
        "checkpoint": "d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1",
        "deployment_field": "5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e",
        "final_summary": sha_file(
            args.artifact_root / "stage_8d_first37/L1/final_summary.json"
        ),
    }
    with AttemptLedger(
        args.artifact_root,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="stage_8e_git_safe_audit_export",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=240.0,
    ) as attempt:
        index = export(
            args.artifact_root,
            args.parent_root,
            args.audit_root,
            args.result_root,
        )
        attempt.progress(
            status="stage_8e_git_safe_audit_export_complete",
            latest_validated_checkpoint=str(args.audit_root / "index.json"),
        )
    _refresh_result_attempts(args.artifact_root, args.result_root)
    print(json.dumps(index["decision"], sort_keys=True))


if __name__ == "__main__":
    main()
