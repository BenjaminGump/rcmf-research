#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import append_jsonl, atomic_write_json


RECOVERABLE_PARENT_EXIT_CODES = {-15, -9, 75, 137, 143}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--maximum-parent-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=60.0)
    return parser.parse_args()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hard_cap_reached(run_root: Path) -> bool:
    path = run_root / "runtime_authorization.json"
    if not path.exists():
        return False
    authorization = json.loads(path.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(
        str(authorization["run_started_utc"]).replace("Z", "+00:00")
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
    return elapsed >= float(authorization["hard_cap_hours"])


def main() -> None:
    args = parse_args()
    if args.maximum_parent_attempts < 1:
        raise ValueError("maximum-parent-attempts must be positive")
    args.run_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "scripts/run_rcmf_reproducible_pipeline_14b.py",
        "--contract",
        str(args.contract),
        "--run-root",
        str(args.run_root),
        "--authorize-and-run",
    ]
    ledger = args.run_root / "supervisor_attempts.jsonl"
    for ordinal in range(1, args.maximum_parent_attempts + 1):
        attempt_id = f"parent-{int(time.time() * 1_000_000)}"
        append_jsonl(
            ledger,
            {
                "event": "opened",
                "attempt_id": attempt_id,
                "ordinal": ordinal,
                "maximum_parent_attempts": args.maximum_parent_attempts,
                "command": command,
                "utc": _utc_now(),
            },
        )
        exit_code = subprocess.run(command, check=False).returncode
        recoverable = exit_code in RECOVERABLE_PARENT_EXIT_CODES
        append_jsonl(
            ledger,
            {
                "event": "closed",
                "attempt_id": attempt_id,
                "ordinal": ordinal,
                "exit_code": exit_code,
                "recoverable": recoverable,
                "utc": _utc_now(),
            },
        )
        atomic_write_json(
            args.run_root / "supervisor_state.json",
            {
                "format": "rcmf_pipeline_supervisor_state_14b_v1",
                "attempt_id": attempt_id,
                "ordinal": ordinal,
                "exit_code": exit_code,
                "recoverable": recoverable,
                "updated_utc": _utc_now(),
            },
        )
        if exit_code == 0:
            raise SystemExit(0)
        if not recoverable or ordinal == args.maximum_parent_attempts:
            raise SystemExit(exit_code)
        if _hard_cap_reached(args.run_root):
            raise SystemExit(124)
        time.sleep(args.retry_delay_seconds)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    main()
