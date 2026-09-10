from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from rcmf.benchmarks.alfworld.task_manifest import (
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import (
    ALFWorldOfficialExpertTrajectoryProvider,
    build_corpus_manifest,
    read_corpus_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay official ALFWorld TRAIN expert plans")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=250)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 0:
        raise ValueError("--limit must be zero (complete) or positive (diagnostic)")
    output = Path(args.output).resolve()
    manifest_output = Path(args.manifest_output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = load_sealed_task_manifest(args.task_manifest)
    tasks = list(portable_task_records(rows)["train"])
    if args.limit:
        tasks = tasks[: args.limit]
    existing = read_corpus_jsonl(output) if output.exists() else []
    existing_ids = {str(row["task_id"]) for row in existing}
    allowed = {task.task_id for task in tasks}
    if not existing_ids <= allowed:
        raise ValueError("existing corpus contains task IDs outside this exact invocation")
    provider = ALFWorldOfficialExpertTrajectoryProvider(
        data_root=args.data_root,
        max_steps=args.max_steps,
        identity_bindings={
            "source_commit": args.source_commit,
            "run_uuid": args.run_uuid,
            "task_manifest_sha256": TASK_MANIFEST_SHA256,
        },
    )
    started = time.time()
    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for ordinal, task in enumerate(tasks, 1):
            if task.task_id in existing_ids:
                continue
            row = provider.replay(task)
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            print(
                json.dumps(
                    {
                        "ordinal": ordinal,
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
    completed = read_corpus_jsonl(output)
    manifest = build_corpus_manifest(output, completed)
    manifest.update(
        {
            "run_uuid": args.run_uuid,
            "source_commit": args.source_commit,
            "requested_population": len(tasks),
            "complete": len(completed) == len(tasks),
            "diagnostic_limit": args.limit or None,
            "elapsed_seconds_this_invocation": round(time.time() - started, 3),
        }
    )
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    temp = manifest_output.with_suffix(manifest_output.suffix + ".tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, manifest_output)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0 if manifest["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
