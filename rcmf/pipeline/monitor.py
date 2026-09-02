from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MONITOR_INTERVAL_SECONDS = 1200
HEARTBEAT_STALE_SECONDS = 720


def _utc_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def read_health_snapshot(run_root: str | Path) -> dict[str, Any]:
    """Read health state only; this function never obtains or mutates scheduler state."""
    root = Path(run_root)
    heartbeat_path = root / "heartbeat.json"
    scheduler_path = root / "scheduler_state.json"
    authorization_path = root / "runtime_authorization.json"
    process_path: Path | None = None
    heartbeat: Mapping[str, Any] = {}
    scheduler: Mapping[str, Any] = {}
    if heartbeat_path.exists():
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    if scheduler_path.exists():
        scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))
    authorization: Mapping[str, Any] = {}
    if authorization_path.exists():
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    stage_id = heartbeat.get("stage_id") or scheduler.get("current_stage")
    if stage_id:
        process_path = root / "stages" / str(stage_id) / "process.json"
    child: Mapping[str, Any] = {}
    if process_path and process_path.exists():
        child = json.loads(process_path.read_text(encoding="utf-8"))
    heartbeat_age = None
    if heartbeat.get("utc"):
        heartbeat_age = datetime.now(timezone.utc).timestamp() - _utc_timestamp(
            str(heartbeat["utc"])
        )
    wall_hours = None
    if authorization.get("run_started_utc"):
        wall_hours = (
            datetime.now(timezone.utc).timestamp()
            - _utc_timestamp(str(authorization["run_started_utc"]))
        ) / 3600.0
    return {
        "format": "rcmf_read_only_watchdog_snapshot_14b_v1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "orchestrator_pid": heartbeat.get("pid"),
        "current_stage": stage_id,
        "child_pid": child.get("pid"),
        "child_command": child.get("command"),
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_stale": heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_SECONDS,
        "scheduler_status": scheduler.get("status"),
        "latest_completed_stage": (scheduler.get("completed") or [None])[-1],
        "scheduler_lock_present": (root / "scheduler.lock").exists(),
        "wall_hours": wall_hours,
        "approved_hard_cap_hours": authorization.get("hard_cap_hours"),
    }


def gpu_snapshot() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": type(exc).__name__}
    return {"available": True, "rows": result.stdout.strip().splitlines()}


def watchdog_capabilities() -> dict[str, Any]:
    return {
        "interval_seconds": MONITOR_INTERVAL_SECONDS,
        "heartbeat_stale_seconds": HEARTBEAT_STALE_SECONDS,
        "can_launch_stages": False,
        "can_acquire_scheduler_lock": False,
        "can_modify_scientific_state": False,
        "pid": os.getpid(),
    }
