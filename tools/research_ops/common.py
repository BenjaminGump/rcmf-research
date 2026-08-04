from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_cmd(args: list[str], cwd: Path | None = None, check: bool = False) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout.strip()


def repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    output = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if output:
        return Path(output).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def git_info(root: Path) -> dict[str, Any]:
    status = run_cmd(["git", "status", "--short", "--branch"], cwd=root)
    return {
        "root": str(root),
        "branch": run_cmd(["git", "branch", "--show-current"], cwd=root),
        "commit": run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=root),
        "commit_full": run_cmd(["git", "rev-parse", "HEAD"], cwd=root),
        "status_short_branch": status,
        "dirty": any(
            line and not line.startswith("##") for line in status.splitlines()
        ),
        "remote_v": run_cmd(["git", "remote", "-v"], cwd=root),
    }


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def load_per_task(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = read_json(path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("tasks", "per_task", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    raise ValueError(f"Cannot find per-task list in {path}")


def metric_from_counts(name: str, numerator: int, denominator: int) -> dict[str, Any]:
    value = float(numerator) / float(denominator) if denominator else 0.0
    return {
        "name": name,
        "value": value,
        "numerator": int(numerator),
        "denominator": int(denominator),
    }


def metric_from_file(path: Path | None, successes: list[str], total: int) -> dict[str, Any]:
    if path is not None and path.exists():
        payload = read_json(path)
        if isinstance(payload, dict):
            for value_key in ("success_rate", "accuracy", "score", "average_score"):
                if value_key in payload:
                    value = float(payload[value_key])
                    if value > 1.0:
                        value = value / 100.0
                    numerator = int(round(value * total)) if total else int(payload.get("num_successes", 0))
                    return metric_from_counts("success_rate", numerator, total)
            if "numerator" in payload and "denominator" in payload:
                return metric_from_counts("success_rate", int(payload["numerator"]), int(payload["denominator"]))
    return metric_from_counts("success_rate", len(successes), total)


def success_task_ids(per_task: list[dict[str, Any]]) -> list[str]:
    successes: list[str] = []
    for row in per_task:
        task_id = row.get("task_id") or row.get("id")
        success = row.get("success")
        if success is None and "score" in row:
            try:
                success = float(row["score"]) >= 1.0
            except (TypeError, ValueError):
                success = False
        if task_id and bool(success):
            successes.append(str(task_id))
    return successes


def env_snapshot() -> dict[str, Any]:
    interesting = [
        "CUDA_VISIBLE_DEVICES",
        "HF_HOME",
        "TRANSFORMERS_CACHE",
        "RCMF_PERSIST",
        "PYTHONPATH",
    ]
    return {key: os.environ.get(key) for key in interesting if os.environ.get(key)}
