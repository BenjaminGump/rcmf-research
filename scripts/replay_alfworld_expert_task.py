from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rcmf.benchmarks.alfworld.task_manifest import (
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import ALFWorldOfficialExpertTrajectoryProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay one isolated ALFWorld official expert task")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--max-steps", type=int, default=250)
    args = parser.parse_args()
    tasks = portable_task_records(load_sealed_task_manifest(args.task_manifest))["train"]
    matches = [task for task in tasks if task.task_id == args.task_id]
    if len(matches) != 1:
        raise ValueError("isolated replay task ID is not one exact TRAIN task")
    provider = ALFWorldOfficialExpertTrajectoryProvider(
        data_root=args.data_root,
        max_steps=args.max_steps,
        identity_bindings={
            "source_commit": args.source_commit,
            "run_uuid": args.run_uuid,
            "task_manifest_sha256": TASK_MANIFEST_SHA256,
            "isolated_replay": True,
        },
    )
    row = provider.replay(matches[0])
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
