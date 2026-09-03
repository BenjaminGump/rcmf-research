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
from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.utils.serialization import atomic_write_json, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--authorize-and-run", action="store_true")
    parser.add_argument("--authorization-file", type=Path)
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


def _authorize(
    contract_path: Path, run_root: Path, authorization_path: Path | None
) -> dict[str, object]:
    if authorization_path is None:
        raise PermissionError("A fresh explicit --authorization-file is required")
    contract = load_pipeline_contract(contract_path)
    preflight = json.loads(
        (run_root / "preflight/preflight_summary.json").read_text(encoding="utf-8")
    )
    runtime = json.loads(
        (run_root / "preflight/runtime_preflight.json").read_text(encoding="utf-8")
    )
    approval = json.loads(authorization_path.read_text(encoding="utf-8"))
    config_path = Path(str(contract.metadata["pipeline_config_path"]))
    run_bound_checks = validate_explicit_authorization(
        approval,
        contract,
        run_root=run_root,
        contract_path=contract_path,
        pipeline_config_path=config_path,
    )
    checks = {
        "persistent_mount": os.path.ismount("/lambda/nfs/rcmf-persist"),
        "clean_checkout": _status() == "",
        "source_commit": _head()
        == contract.source_commit
        == str(preflight["launch_source_sha"]),
        "all_preflight_checks": all(bool(v) for v in preflight["approval_checks"].values()),
        "preflight_requires_explicit_approval": bool(
            preflight.get("explicit_user_approval_required", True)
        ),
        "recommended_cap_within_approved_cap": float(
            runtime["recommended_hard_cap_hours"]
        )
        <= float(contract.hard_cap_hours),
        "contract_hash_valid": sha256_file(contract_path)
        == sha256_file(run_root / "preflight/stage_dag.json"),
        "one_global_seed": int(contract.global_seed) == 25101,
        **{f"run_bound_{key}": value for key, value in run_bound_checks.items()},
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
        "format": str(contract.metadata["authorization_version"]),
        "authorization_version": str(
            contract.metadata["authorization_version"]
        ),
        "authorized": True,
        "authorization_status": "AUTHORIZED",
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "authorization_source": "explicit_run_bound_user_authorization",
        "authorized_at_utc": existing.get("authorized_at_utc")
        or datetime.now(timezone.utc).isoformat(),
        "run_started_utc": existing.get("run_started_utc")
        or datetime.now(timezone.utc).isoformat(),
        "source_commit": contract.source_commit,
        "run_uuid": contract.run_uuid,
        "run_root": str(run_root.resolve()),
        "contract_sha256": sha256_file(contract_path),
        "pipeline_config_sha256": sha256_file(config_path),
        "preflight_summary_sha256": sha256_file(
            run_root / "preflight/preflight_summary.json"
        ),
        "hard_cap_hours": float(contract.hard_cap_hours),
        "recommended_hard_cap_hours": float(runtime["recommended_hard_cap_hours"]),
        "checks": checks,
        "scope": str(contract.metadata["authorization_scope"]),
        "gate_to_one_demo_target_seconds": 60,
        "monitor_is_scheduler": False,
    }
    atomic_write_json(existing_path, payload)
    return payload


def main() -> None:
    args = parse_args()
    if args.authorize_and_run:
        authorization = _authorize(args.contract, args.run_root, args.authorization_file)
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
