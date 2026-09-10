from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import time
from typing import Any

from rcmf.benchmarks.alfworld.task_manifest import (
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import (
    ALFWorldOfficialExpertTrajectoryProvider,
    build_corpus_manifest,
    read_corpus_jsonl,
    validate_corpus_row,
)
from rcmf.pipeline.portable_v2.schemas import TaskRecord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parallel exact ALFWorld TRAIN expert replay")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--row-dir", required=True)
    parser.add_argument("--temp-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=250)
    parser.add_argument("--task-timeout-seconds", type=int, default=300)
    return parser.parse_args()


def _filename(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".json"


def _replay_one(
    task: TaskRecord,
    *,
    data_root: str,
    temp_root: str,
    run_uuid: str,
    source_commit: str,
    max_steps: int,
    task_timeout_seconds: int,
) -> dict[str, Any]:
    process_temp = Path(temp_root) / str(os.getpid())
    process_temp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(process_temp)
    tempfile.tempdir = str(process_temp)
    def timeout_handler(signum: int, frame: Any) -> None:
        del signum, frame
        raise TimeoutError(f"official expert replay exceeded {task_timeout_seconds} seconds")

    prior_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(task_timeout_seconds)
    try:
        provider = ALFWorldOfficialExpertTrajectoryProvider(
            data_root=data_root,
            max_steps=max_steps,
            identity_bindings={
                "source_commit": source_commit,
                "run_uuid": run_uuid,
                "task_manifest_sha256": TASK_MANIFEST_SHA256,
                "parallel_replay": True,
                "task_timeout_seconds": task_timeout_seconds,
            },
        )
        return provider.replay(task)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prior_handler)


def _load_row(path: Path, task_id: str) -> dict[str, Any]:
    row = json.loads(path.read_text(encoding="utf-8"))
    validate_corpus_row(row)
    if row["task_id"] != task_id:
        raise ValueError(f"row artifact identity differs: {path}")
    return row


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    if args.task_timeout_seconds <= 0:
        raise ValueError("--task-timeout-seconds must be positive")
    if args.limit < 0:
        raise ValueError("--limit must be zero or positive")
    manifest_rows = load_sealed_task_manifest(args.task_manifest)
    tasks = list(portable_task_records(manifest_rows)["train"])
    if args.limit:
        tasks = tasks[: args.limit]
    row_dir = Path(args.row_dir).resolve()
    temp_root = Path(args.temp_root).resolve()
    output = Path(args.output).resolve()
    manifest_output = Path(args.manifest_output).resolve()
    for directory in (row_dir, temp_root, output.parent, manifest_output.parent):
        directory.mkdir(parents=True, exist_ok=True)

    completed: dict[str, dict[str, Any]] = {}
    for task in tasks:
        path = row_dir / _filename(task.task_id)
        if path.is_file():
            completed[task.task_id] = _load_row(path, task.task_id)
    missing = [task for task in tasks if task.task_id not in completed]
    started = time.time()
    if missing:
        # TextWorld/Fast Downward owns subprocess and temporary-directory state;
        # clean spawned workers avoid inherited fork state and cross-game reuse.
        with ProcessPoolExecutor(
            max_workers=args.workers,
            mp_context=multiprocessing.get_context("spawn"),
            max_tasks_per_child=1,
        ) as pool:
            futures = {
                pool.submit(
                    _replay_one,
                    task,
                    data_root=args.data_root,
                    temp_root=str(temp_root),
                    run_uuid=args.run_uuid,
                    source_commit=args.source_commit,
                    max_steps=args.max_steps,
                    task_timeout_seconds=args.task_timeout_seconds,
                ): task
                for task in missing
            }
            for future in as_completed(futures):
                task = futures[future]
                row = future.result()
                validate_corpus_row(row)
                target = row_dir / _filename(task.task_id)
                temp = row_dir / (target.name + f".{os.getpid()}.tmp")
                temp.write_text(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n",
                    encoding="utf-8",
                )
                os.replace(temp, target)
                completed[task.task_id] = row
                print(
                    json.dumps(
                        {
                            "completed": len(completed),
                            "population": len(tasks),
                            "task_id": task.task_id,
                            "status": row["status"],
                            "steps": len(row["steps"]),
                            "elapsed_seconds": round(time.time() - started, 3),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    ordered = [completed[task.task_id] for task in tasks]
    if output.exists():
        existing = read_corpus_jsonl(output)
        if existing != ordered:
            raise ValueError("existing consolidated corpus differs; refusing overwrite")
    else:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            for row in ordered:
                stream.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
    manifest = build_corpus_manifest(output, ordered)
    manifest.update(
        {
            "run_uuid": args.run_uuid,
            "source_commit": args.source_commit,
            "requested_population": len(tasks),
            "complete": len(ordered) == len(tasks),
            "diagnostic_limit": args.limit or None,
            "worker_count": args.workers,
            "elapsed_seconds_this_invocation": round(time.time() - started, 3),
            "row_artifact_count": len(completed),
        }
    )
    temp_manifest = manifest_output.with_suffix(manifest_output.suffix + ".tmp")
    temp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_manifest, manifest_output)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
