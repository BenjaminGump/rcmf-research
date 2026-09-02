#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import _bootstrap  # noqa: F401

from rcmf.pipeline.orchestrator import load_pipeline_contract, result_as_dict, run_pipeline
from rcmf.utils.serialization import atomic_write_json, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--authorize-and-run", action="store_true")
    return parser.parse_args()


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _status() -> str:
    return subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _authorize(contract_path: Path, run_root: Path) -> dict[str, object]:
    contract = load_pipeline_contract(contract_path)
    preflight = json.loads(
        (run_root / "preflight/preflight_summary.json").read_text(encoding="utf-8")
    )
    runtime = json.loads(
        (run_root / "preflight/runtime_preflight.json").read_text(encoding="utf-8")
    )
    approval = json.loads(
        (run_root / "preflight/approval_request.json").read_text(encoding="utf-8")
    )
    checks = {
        "persistent_mount": os.path.ismount("/lambda/nfs/rcmf-persist"),
        "clean_checkout": _status() == "",
        "source_commit": _head() == contract.source_commit == str(preflight["source_commit"]),
        "all_preflight_checks": all(bool(v) for v in preflight["approval_checks"].values()),
        "approval_package_authorized": bool(approval["authorized_to_launch_when_persisted"]),
        "recommended_cap_at_most_200": float(runtime["recommended_hard_cap_hours"]) <= 200.0,
        "approved_cap_is_200": float(contract.hard_cap_hours) == 200.0,
        "contract_hash_valid": sha256_file(contract_path)
        == sha256_file(run_root / "preflight/stage_dag.json"),
        "one_global_seed": int(contract.global_seed) == 25101,
    }
    if not all(checks.values()):
        raise PermissionError(f"EXP-037A conditional authorization checks failed: {checks}")
    existing_path = run_root / "runtime_authorization.json"
    existing = (
        json.loads(existing_path.read_text(encoding="utf-8"))
        if existing_path.exists()
        else {}
    )
    payload: dict[str, object] = {
        "format": "exp037a_runtime_authorization_14b_v1",
        "authorized": True,
        "authorization_source": "user_conditional_total_authorization",
        "authorized_at_utc": existing.get("authorized_at_utc")
        or datetime.now(timezone.utc).isoformat(),
        "run_started_utc": existing.get("run_started_utc")
        or datetime.now(timezone.utc).isoformat(),
        "source_commit": contract.source_commit,
        "contract_sha256": sha256_file(contract_path),
        "preflight_summary_sha256": sha256_file(
            run_root / "preflight/preflight_summary.json"
        ),
        "hard_cap_hours": 200.0,
        "recommended_hard_cap_hours": float(runtime["recommended_hard_cap_hours"]),
        "checks": checks,
        "scope": "complete_3d_then_1d_only_on_THREE_DEMO_REPRODUCTION_PASS",
        "gate_to_one_demo_target_seconds": 60,
        "monitor_is_scheduler": False,
    }
    atomic_write_json(existing_path, payload)
    return payload


def main() -> None:
    args = parse_args()
    if args.authorize_and_run:
        authorization = _authorize(args.contract, args.run_root)
        print(json.dumps({"authorization": authorization}, sort_keys=True), flush=True)
    result = run_pipeline(
        args.contract,
        args.run_root,
        python_executable=sys.executable,
    )
    payload = result_as_dict(result)
    atomic_write_json(args.run_root / "orchestrator_result.json", payload)
    print(json.dumps(payload, sort_keys=True), flush=True)
    if result.status != "complete":
        completion = (
            args.run_root / "stages" / str(result.failed_stage) / "completion.json"
            if result.failed_stage
            else None
        )
        exit_code = 65
        if completion and completion.exists():
            row = json.loads(completion.read_text(encoding="utf-8"))
            if int(row.get("exit_code", 65)) == 75:
                exit_code = 75
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
