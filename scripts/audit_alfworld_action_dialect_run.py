"""Fail-closed CPU audit for ALFWorld action-dialect-v3 episode outputs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from rcmf.benchmarks.alfworld.environment import (
    REACT_PUT_MOVE_BRIDGE_ID,
    translate_react_action_to_alfworld,
)
from rcmf.benchmarks.alfworld.execution_lock import (
    TRACK_R_LOCK_FILE_SHA256,
    TRACK_R_LOCK_IDENTITY_SHA256,
    load_execution_lock,
)
from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    GENERATION_IDENTITY_SHA256,
    MODEL_REVISION,
)
from rcmf.benchmarks.alfworld.runtime_agent import (
    first_decoded_line,
    raw_first_decoded_line,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    canonical_sha256,
    load_sealed_task_manifest,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--benchmark-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-run-uuid", required=True)
    parser.add_argument("--expected-generation-batch-size", type=int, required=True)
    parser.add_argument("--diagnostic-uuid", required=True)
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_evaluator(row: dict[str, Any]) -> list[str]:
    violations = []
    steps = list(row.get("steps") or [])
    last = steps[-1] if steps else {}
    done = bool(last.get("environment_done", False))
    won = bool(last.get("official_won", False))
    expected_success = done and won
    if bool(row.get("official_success")) != expected_success:
        violations.append("official_success differs from terminal done AND won")
    if float(row.get("raw_reward", 0.0)) != (1.0 if expected_success else 0.0):
        violations.append("episode raw_reward differs from binary official success")
    expected_terminal = (
        "SUCCESS"
        if expected_success
        else "ERROR"
        if row.get("error")
        else "FAILURE"
        if done
        else "TRUNCATED"
    )
    if row.get("terminal_status") != expected_terminal:
        violations.append("terminal status differs from evaluator contract")
    return violations


def main() -> int:
    args = _arguments()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite audit evidence: {args.output}")
    if args.expected_count not in {30, 134}:
        raise ValueError("action-dialect audit supports only preregistered 30 or 134 tasks")
    lock = load_execution_lock(args.benchmark_lock)
    if (
        lock["file_sha256"] != TRACK_R_LOCK_FILE_SHA256
        or lock["lock_identity_sha256"] != TRACK_R_LOCK_IDENTITY_SHA256
    ):
        raise ValueError("audit benchmark lock differs from v3 portable identity")
    manifest = load_sealed_task_manifest(args.task_manifest)
    expected_ids = [
        str(row["task_id"]) for row in manifest if row["split"] == "valid_unseen"
    ][: args.expected_count]
    rows = _read_jsonl(args.episodes)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    violations: list[dict[str, Any]] = []
    observed_ids = [str(row.get("task_id")) for row in rows]
    if len(rows) != args.expected_count:
        violations.append({"scope": "population", "message": "row count differs"})
    if observed_ids != expected_ids:
        violations.append({"scope": "population", "message": "physical order differs"})
    if len(observed_ids) != len(set(observed_ids)):
        violations.append({"scope": "population", "message": "duplicate task ID"})

    expected_set_sha = canonical_sha256(sorted(expected_ids))
    expected_order_sha = canonical_sha256(expected_ids)
    expected_role = (
        "formal_track_r_complete" if args.expected_count == 134 else "engineering_subset"
    )
    expected_identity = {
        "run_uuid": args.expected_run_uuid,
        "source_commit": args.expected_source_commit,
        "track_id": "alfworld_upstream_react_valid_unseen_reference_v1",
        "track_role": "UPSTREAM_PROTOCOL_REFERENCE",
        "task_list_role": expected_role,
        "split": "valid_unseen",
        "task_ids_sha256": expected_set_sha,
        "evaluation_order_sha256": expected_order_sha,
        "condition": "bare",
        "checkpoint_sha256": None,
        "benchmark_lock_sha256": TRACK_R_LOCK_FILE_SHA256,
        "benchmark_lock_identity_sha256": TRACK_R_LOCK_IDENTITY_SHA256,
        "generation_batch_size": args.expected_generation_batch_size,
        "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
        "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
        "model_revision": MODEL_REVISION,
    }
    bridge_count = 0
    literal_matched_put_to_environment = 0
    checked_steps = 0
    family_counts: Counter[str] = Counter()
    family_success: Counter[str] = Counter()
    for ordinal, row in enumerate(rows, 1):
        task_id = str(row.get("task_id"))
        family = str(row.get("task_family"))
        family_counts[family] += 1
        family_success[family] += int(bool(row.get("official_success")))
        if row.get("schema_version") != "alfworld_agent_episode_v2":
            violations.append({"task_id": task_id, "message": "episode schema differs"})
        claimed_episode_sha = row.get("episode_sha256")
        hash_payload = dict(row)
        hash_payload.pop("episode_sha256", None)
        if claimed_episode_sha != canonical_sha256(hash_payload):
            violations.append({"task_id": task_id, "message": "episode hash differs"})
        identity = row.get("run_identity") or {}
        if identity != expected_identity:
            violations.append({"task_id": task_id, "message": "run identity differs"})
        if row.get("generation_identity_sha256") != GENERATION_IDENTITY_SHA256:
            violations.append({"task_id": task_id, "message": "generation identity differs"})
        if row.get("action_dialect_bridge_id") != REACT_PUT_MOVE_BRIDGE_ID:
            violations.append({"task_id": task_id, "message": "bridge identity differs"})
        for step in row.get("steps") or ():
            checked_steps += 1
            raw = str(step.get("raw_model_text", ""))
            if step.get("first_decoded_line") != raw_first_decoded_line(raw):
                violations.append({"task_id": task_id, "step": step.get("step_index"), "message": "raw first line differs"})
            parsed = first_decoded_line(raw)
            if step.get("parsed_action") != parsed:
                violations.append({"task_id": task_id, "step": step.get("step_index"), "message": "normalized model action differs"})
            if not step.get("action_valid", False):
                continue
            translated = translate_react_action_to_alfworld(parsed)
            bridge_count += int(translated["bridge_applied"])
            if translated["bridge_applied"] and step.get("executed_action") == parsed:
                literal_matched_put_to_environment += 1
            if step.get("executed_action") != translated["environment_action"]:
                violations.append({"task_id": task_id, "step": step.get("step_index"), "message": "executed action differs from exact bridge"})
            if step.get("action_dialect_bridge_applied") is not translated[
                "bridge_applied"
            ]:
                violations.append({"task_id": task_id, "step": step.get("step_index"), "message": "bridge flag differs"})
            if step.get("action_dialect_bridge_id") != REACT_PUT_MOVE_BRIDGE_ID:
                violations.append({"task_id": task_id, "step": step.get("step_index"), "message": "step bridge identity differs"})
        for message in _validate_evaluator(row):
            violations.append({"task_id": task_id, "message": message})

    success_count = sum(bool(row.get("official_success")) for row in rows)
    typed_failures = sum(row.get("error") is not None for row in rows)
    if summary.get("schema_version") != "alfworld_agent_run_summary_v1":
        violations.append({"scope": "summary", "message": "summary schema differs"})
    if summary.get("run_identity") != expected_identity:
        violations.append({"scope": "summary", "message": "summary run identity differs"})
    if (
        summary.get("requested_tasks") != args.expected_count
        or summary.get("completed_tasks") != len(rows)
        or summary.get("all_tasks_completed") is not True
    ):
        violations.append({"scope": "summary", "message": "summary population differs"})
    if (
        summary.get("successes") != success_count
        or summary.get("typed_failures") != typed_failures
    ):
        violations.append({"scope": "summary", "message": "summary outcomes differ"})
    if (summary.get("output") or {}).get("sha256") != _sha256_file(args.episodes):
        violations.append({"scope": "summary", "message": "summary output hash differs"})
    structural_pass = (
        not violations
        and bridge_count > 0
        and literal_matched_put_to_environment == 0
        and typed_failures == 0
    )
    if args.expected_count == 30:
        outcome_pass = success_count > 13 and family_success["pick_and_place"] >= 1
        decision = (
            "READY_FOR_CORRECTED_FULL_134_BARE"
            if structural_pass and outcome_pass
            else "STOP_ACTION_DIALECT_CORRECTION_DID_NOT_IMPROVE_FIRST_30"
        )
    else:
        outcome_pass = None
        decision = (
            "PASS_FULL_134_BARE_ACTION_DIALECT_STRUCTURAL_AUDIT_RCMF_C_REVIEW_REQUIRED"
            if structural_pass
            else "STOP_FULL_134_BARE_ACTION_DIALECT_STRUCTURAL_AUDIT_FAILED"
        )

    result = {
        "schema_version": "alfworld_action_dialect_run_audit_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_uuid": args.diagnostic_uuid,
        "decision": decision,
        "authority": {
            "episodes_path": str(args.episodes.resolve()),
            "summary_path": str(args.summary.resolve()),
            "benchmark_lock_path": str(args.benchmark_lock.resolve()),
            "run_uuid": args.expected_run_uuid,
            "source_commit": args.expected_source_commit,
            "model_revision": MODEL_REVISION,
            "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
            "benchmark_lock_file_sha256": TRACK_R_LOCK_FILE_SHA256,
            "benchmark_lock_identity_sha256": TRACK_R_LOCK_IDENTITY_SHA256,
            "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
        },
        "population": {
            "expected": args.expected_count,
            "observed": len(rows),
            "task_ids_sha256": expected_set_sha,
            "evaluation_order_sha256": expected_order_sha,
        },
        "checks": {
            "structural_pass": structural_pass,
            "outcome_improvement_pass": outcome_pass,
            "violation_count": len(violations),
            "violations": violations,
            "checked_steps": checked_steps,
            "bridge_activations": bridge_count,
            "matched_put_sent_literally": literal_matched_put_to_environment,
            "typed_failures": typed_failures,
        },
        "outcomes": {
            "official_successes": success_count,
            "official_success_rate": success_count / len(rows) if rows else 0.0,
            "family_task_counts": dict(sorted(family_counts.items())),
            "family_successes": dict(sorted(family_success.items())),
            "first_30_reference_total_success": 13 if args.expected_count == 30 else None,
            "first_30_reference_pick_and_place_success": 0 if args.expected_count == 30 else None,
        },
        "summary": summary,
        "negative_statements": {
            "model_loaded_by_audit": False,
            "model_forward_or_generation_by_audit": False,
            "performance_search": False,
            "task_removal_or_substitution": False,
        },
    }
    result["canonical_identity_sha256"] = canonical_sha256(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
