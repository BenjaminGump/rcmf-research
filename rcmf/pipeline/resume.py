from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from rcmf.pipeline.manifests import content_sha256
from rcmf.utils.serialization import append_jsonl, atomic_write_json, ensure_dir, read_jsonl


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppendOnlyAttemptLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def attempt_ids(self) -> set[str]:
        return {str(row["attempt_id"]) for row in read_jsonl(self.path)}

    def open_attempt_ids(self) -> list[str]:
        states: dict[str, str] = {}
        for row in read_jsonl(self.path):
            states[str(row["attempt_id"])] = str(row["event"])
        return sorted(attempt_id for attempt_id, event in states.items() if event == "opened")

    def open(self, attempt_id: str, payload: Mapping[str, Any]) -> None:
        if attempt_id in self.attempt_ids():
            raise ValueError(f"Duplicate attempt ID: {attempt_id}")
        append_jsonl(
            self.path,
            {
                **dict(payload),
                "attempt_id": attempt_id,
                "event": "opened",
                "status": "running",
                "utc": utc_now(),
            },
        )

    def close(self, attempt_id: str, status: str, payload: Mapping[str, Any]) -> None:
        if attempt_id not in self.attempt_ids():
            raise ValueError(f"Unknown attempt ID: {attempt_id}")
        append_jsonl(
            self.path,
            {
                **dict(payload),
                "attempt_id": attempt_id,
                "event": "closed",
                "status": status,
                "utc": utc_now(),
            },
        )


class StageStateStore:
    def __init__(self, run_root: str | Path) -> None:
        self.run_root = ensure_dir(run_root)
        self.stage_root = ensure_dir(self.run_root / "stages")

    def stage_dir(self, stage_id: str) -> Path:
        return ensure_dir(self.stage_root / stage_id)

    def completion_path(self, stage_id: str) -> Path:
        return self.stage_dir(stage_id) / "completion.json"

    def load_completion(self, stage_id: str) -> dict[str, Any] | None:
        path = self.completion_path(stage_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def write_completion(self, stage_id: str, payload: Mapping[str, Any]) -> Path:
        body = dict(payload)
        body.setdefault("stage_id", stage_id)
        body.setdefault("completed_utc", utc_now())
        body["completion_sha256"] = content_sha256(
            {key: value for key, value in body.items() if key != "completion_sha256"}
        )
        path = self.completion_path(stage_id)
        atomic_write_json(path, body)
        return path

    def write_scheduler_state(self, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.run_root / "scheduler_state.json", payload)


@contextmanager
def exclusive_lock(path: str | Path, owner: Mapping[str, Any]) -> Iterator[Path]:
    lock_path = Path(path)
    ensure_dir(lock_path.parent)
    descriptor: int | None = None
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, json.dumps(dict(owner), sort_keys=True).encode("utf-8"))
        os.close(descriptor)
        descriptor = None
        yield lock_path
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def heartbeat_payload(run_uuid: str, stage_id: str | None, pid: int) -> dict[str, Any]:
    return {
        "format": "rcmf_pipeline_heartbeat_v1",
        "run_uuid": run_uuid,
        "stage_id": stage_id,
        "pid": pid,
        "utc": utc_now(),
        "monotonic_seconds": time.monotonic(),
    }
