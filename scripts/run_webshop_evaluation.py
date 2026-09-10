from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.webshop.adapter import WebShopPortableAdapterV2, load_task_catalog
from rcmf.benchmarks.webshop.evaluation import (
    CONDITIONS,
    EVALUATION_TASK_FORMAT,
    FrozenWebShopMethod,
    run_evaluation_task,
)
from rcmf.benchmarks.webshop.runtime_client import WebShopHTTPRuntime
from rcmf.pipeline.manifests import content_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _validate_existing(
    path: Path, *, lock_sha256: str, condition: str, task_id: str
) -> Mapping[str, object]:
    row = _object(path)
    body = dict(row)
    recorded = body.pop("task_artifact_sha256", None)
    if (
        row.get("format") != EVALUATION_TASK_FORMAT
        or row.get("evaluation_lock_sha256") != lock_sha256
        or row.get("condition") != condition
        or row.get("task_id") != task_id
        or recorded != content_sha256(body)
    ):
        raise RuntimeError(f"existing WebShop evaluation artifact differs: {path}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-lock", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--runtime-identity", type=Path, required=True)
    parser.add_argument("--model-snapshot", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--session-namespace", required=True)
    parser.add_argument("--condition", choices=CONDITIONS, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("PYTHONHASHSEED") != "25101"
        or os.environ.get("CUBLAS_WORKSPACE_CONFIG") != ":4096:8"
    ):
        raise RuntimeError("WebShop evaluation requires deterministic process-start variables")
    lock = _object(args.evaluation_lock)
    body = dict(lock)
    recorded_lock_sha = body.pop("lock_sha256", None)
    if recorded_lock_sha != content_sha256(body) or lock.get("status") != "FROZEN_BEFORE_OUTCOMES":
        raise RuntimeError("WebShop evaluation lock hash/status differs")
    checks = {
        "source_commit": lock.get("source_commit") == _head(),
        "task_catalog": lock.get("task_catalog_sha256") == sha256_file(args.task_catalog),
        "runtime_identity": lock.get("runtime_identity_sha256")
        == sha256_file(args.runtime_identity),
        "condition": args.condition in lock.get("conditions", ()),
        "method_package": lock.get("method_package_sha256")
        == sha256_file(Path(str(lock["method_package"]))),
        "standard200_pre_outcome": lock.get("standard200_outcome_inspected") is False,
    }
    if not all(checks.values()):
        raise RuntimeError(f"WebShop evaluation lock checks failed: {checks}")
    catalog = load_task_catalog(args.task_catalog)
    split = str(lock["split"])
    start = int(lock["index_start"])
    end = int(lock["index_end"])
    tasks = [task for task in catalog[split] if start <= int(task.metadata["index"]) < end]
    if [task.task_id for task in tasks] != list(lock["ordered_task_ids"]):
        raise RuntimeError("WebShop evaluation ordered task population differs")
    runtime_identity = _object(args.runtime_identity)
    adapter = WebShopPortableAdapterV2(
        task_records=catalog,
        trajectory_records={"train": ()},
        trajectory_source_identity={"evaluation_only": True},
        runtime_identity=runtime_identity,
        token_counter=None,
        runtime_factory=lambda task: WebShopHTTPRuntime(
            int(task.metadata["index"]),
            endpoint=args.endpoint,
            session_namespace=args.session_namespace,
        ),
    )
    method = FrozenWebShopMethod(
        package_path=str(lock["method_package"]), model_snapshot=args.model_snapshot
    )
    task_root = args.output_root.resolve() / args.condition / "raw_tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    for task in tasks:
        path = task_root / f"task_{int(task.metadata['index']):05d}.json"
        if path.exists():
            row = _validate_existing(
                path,
                lock_sha256=str(recorded_lock_sha),
                condition=args.condition,
                task_id=task.task_id,
            )
        else:
            row = run_evaluation_task(
                adapter=adapter,
                method=method,
                task=task,
                condition=args.condition,
                max_new_tokens=int(lock["max_new_tokens"]),
                evaluation_lock_sha256=str(recorded_lock_sha),
            )
            atomic_write_json(path, row)
        rows.append(row)
    rewards = [float(row["raw_reward"]) for row in rows]
    elapsed = time.perf_counter() - started
    summary = {
        "format": "rcmf_agentbench_fc_webshop_evaluation_condition_v1",
        "evaluation_lock_sha256": recorded_lock_sha,
        "scope": lock["scope"],
        "split": split,
        "condition": args.condition,
        "task_count": len(rows),
        "completed_task_count": sum(row["error"] is None for row in rows),
        "error_count": sum(row["error"] is not None for row in rows),
        "error_types": dict(
            sorted(Counter(row["error"]["type"] for row in rows if row["error"]).items())
        ),
        "mean_raw_reward": statistics.fmean(rewards),
        "median_raw_reward": statistics.median(rewards),
        "full_success_count": sum(reward == 1.0 for reward in rewards),
        "full_success_rate": statistics.fmean(reward == 1.0 for reward in rewards),
        "environment_steps": sum(int(row["environment_step_count"]) for row in rows),
        "search_count": sum(int(row["search_count"]) for row in rows),
        "click_count": sum(int(row["click_count"]) for row in rows),
        "invalid_action_count": sum(int(row["invalid_action_count"]) for row in rows),
        "environment_noop_count": sum(int(row["environment_noop_count"]) for row in rows),
        "max_round_termination_count": sum(bool(row["max_round_termination"]) for row in rows),
        "prompt_tokens": sum(int(row["prompt_tokens"]) for row in rows),
        "generated_tokens": sum(int(row["generated_tokens"]) for row in rows),
        "representation_tokens": sum(int(row["representation_tokens"]) for row in rows),
        "elapsed_seconds": elapsed,
        "h100_hours": elapsed / 3600.0,
        "peak_gpu_memory_bytes": (
            int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0
        ),
        "task_artifact_sha256s": [row["task_artifact_sha256"] for row in rows],
        "all_tasks_present": len(rows) == int(lock["task_count"]),
        "all_tasks_completed_without_errors": (
            len(rows) == int(lock["task_count"]) and all(row["error"] is None for row in rows)
        ),
        "raw_memory_prompt_used": False,
        "runtime_memory_scan_used": False,
        "excluded_200_499_executed": False,
    }
    summary["summary_sha256"] = content_sha256(summary)
    atomic_write_json(args.output_root.resolve() / args.condition / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["all_tasks_completed_without_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
