from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

from rcmf.benchmarks.alfworld.environment import ENVIRONMENT_VERSION
from rcmf.benchmarks.alfworld.task_manifest import (
    DATASET_VERSION,
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import (
    ALFWorldOfficialExpertTrajectoryProvider,
    CORPUS_FORMAT,
    PROVIDER_ID,
    build_corpus_manifest,
    validate_corpus_row,
)
from rcmf.pipeline.portable_v2.schemas import ProvenanceClass, TaskRecord


def _filename(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".json"


def _failure_row(
    task: TaskRecord,
    *,
    run_uuid: str,
    source_commit: str,
    hard_timeout_seconds: int,
    message: str,
) -> dict[str, Any]:
    row = {
        "schema_version": CORPUS_FORMAT,
        "provider": PROVIDER_ID,
        "provenance": ProvenanceClass.OFFICIAL_EXPERT.value,
        "task_id": task.task_id,
        "split": "train",
        "task_family": task.metadata["task_family"],
        "instruction": task.instruction,
        "status": "EXPERT_TIMEOUT",
        "success": False,
        "terminal": False,
        "initial_observation": "",
        "steps": [],
        "bindings": {
            "source_commit": source_commit,
            "run_uuid": run_uuid,
            "task_manifest_sha256": TASK_MANIFEST_SHA256,
            "isolated_replay": True,
            "hard_process_group_timeout_seconds": hard_timeout_seconds,
            **dict(task.source_identity),
            "game_path": task.metadata["game_path"],
            "dataset_version": DATASET_VERSION,
            "environment_version": ENVIRONMENT_VERSION,
        },
        "error": {
            "type": "IsolatedReplayProcessGroupTimeout",
            "message": message,
            "hard_timeout_seconds": hard_timeout_seconds,
        },
    }
    return ALFWorldOfficialExpertTrajectoryProvider._seal(row)


def _run_one(
    task: TaskRecord,
    *,
    args: argparse.Namespace,
    row_dir: Path,
    log_dir: Path,
    temp_root: Path,
) -> dict[str, Any]:
    target = row_dir / _filename(task.task_id)
    if target.is_file():
        row = json.loads(target.read_text(encoding="utf-8"))
        validate_corpus_row(row)
        return row
    task_key = target.stem
    task_temp = temp_root / task_key
    task_temp.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_key}.log"
    command = [
        sys.executable,
        str(Path(__file__).with_name("replay_alfworld_expert_task.py")),
        "--task-manifest",
        args.task_manifest,
        "--task-id",
        task.task_id,
        "--data-root",
        args.data_root,
        "--output",
        str(target),
        "--run-uuid",
        args.run_uuid,
        "--source-commit",
        args.source_commit,
        "--max-steps",
        str(args.max_steps),
    ]
    environment = dict(os.environ)
    environment["TMPDIR"] = str(task_temp)
    started = time.time()
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=args.hard_timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            row = _failure_row(
                task,
                run_uuid=args.run_uuid,
                source_commit=args.source_commit,
                hard_timeout_seconds=args.hard_timeout_seconds,
                message="isolated official expert process group exceeded its hard timeout",
            )
            target.write_text(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            return row
    if return_code != 0 or not target.is_file():
        row = _failure_row(
            task,
            run_uuid=args.run_uuid,
            source_commit=args.source_commit,
            hard_timeout_seconds=args.hard_timeout_seconds,
            message=f"isolated official expert subprocess exited {return_code} without a valid row",
        )
        target.write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return row
    row = json.loads(target.read_text(encoding="utf-8"))
    validate_corpus_row(row)
    row["isolated_elapsed_seconds"] = round(time.time() - started, 3)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Hard-isolated ALFWorld expert replay subset")
    parser.add_argument("--task-manifest", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task-ids-json")
    selection.add_argument("--retry-from-row-dir")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--row-dir", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--temp-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=250)
    parser.add_argument("--hard-timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.workers <= 0 or args.hard_timeout_seconds <= 0:
        raise ValueError("isolated workers and timeout must be positive")
    all_tasks = portable_task_records(load_sealed_task_manifest(args.task_manifest))["train"]
    index = {task.task_id: task for task in all_tasks}
    if args.task_ids_json:
        ids = json.loads(Path(args.task_ids_json).read_text(encoding="utf-8"))
    else:
        prior_rows = []
        for path in Path(args.retry_from_row_dir).resolve(strict=True).glob("*.json"):
            row = json.loads(path.read_text(encoding="utf-8"))
            validate_corpus_row(row)
            prior_rows.append(row)
        prior = {str(row["task_id"]): row for row in prior_rows}
        ids = [
            task.task_id
            for task in all_tasks
            if task.task_id not in prior or prior[task.task_id]["status"] != "SUCCESS"
        ]
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ValueError("isolated task selection must be a non-empty unique array")
    if any(str(task_id) not in index for task_id in ids):
        raise ValueError("isolated task selection contains a non-TRAIN task")
    tasks = [index[str(task_id)] for task_id in ids]
    row_dir = Path(args.row_dir).resolve()
    log_dir = Path(args.log_dir).resolve()
    temp_root = Path(args.temp_root).resolve()
    for directory in (row_dir, log_dir, temp_root):
        directory.mkdir(parents=True, exist_ok=True)
    rows_by_id = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                task,
                args=args,
                row_dir=row_dir,
                log_dir=log_dir,
                temp_root=temp_root,
            ): task
            for task in tasks
        }
        for future in as_completed(futures):
            row = future.result()
            rows_by_id[str(row["task_id"])] = row
            print(
                json.dumps(
                    {
                        "completed": len(rows_by_id),
                        "population": len(tasks),
                        "task_id": row["task_id"],
                        "status": row["status"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    ordered = [rows_by_id[task.task_id] for task in tasks]
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in ordered:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    manifest = build_corpus_manifest(output, ordered)
    manifest.update(
        {
            "schema_version": "alfworld_isolated_expert_subset_manifest_v1",
            "run_uuid": args.run_uuid,
            "source_commit": args.source_commit,
            "workers": args.workers,
            "hard_timeout_seconds": args.hard_timeout_seconds,
            "task_ids": [task.task_id for task in tasks],
        }
    )
    manifest_output = Path(args.manifest_output).resolve()
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_output.with_suffix(manifest_output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_output)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
