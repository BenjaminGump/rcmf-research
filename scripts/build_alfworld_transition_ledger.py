from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from rcmf.benchmarks.alfworld.task_manifest import (
    TASK_MANIFEST_SHA256,
    load_sealed_task_manifest,
    portable_task_records,
)
from rcmf.benchmarks.alfworld.trajectories import portable_trajectory, read_corpus_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build complete ALFWorld TRAIN transition ledger")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--run-uuid", required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    tasks_by_split = portable_task_records(load_sealed_task_manifest(args.task_manifest))
    task_index = {task.task_id: task for task in tasks_by_split["train"]}
    corpus_rows = read_corpus_jsonl(args.corpus)
    admitted = [row for row in corpus_rows if row["status"] == "SUCCESS"]
    output = Path(args.output).resolve()
    manifest_output = Path(args.manifest_output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite transition ledger: {output}")
    transition_count = 0
    task_counts: dict[str, int] = {}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for corpus_row in admitted:
            trajectory = portable_trajectory(corpus_row)
            task = task_index[trajectory.task_id]
            for step in trajectory.steps:
                row = {
                    "schema_version": "alfworld_transition_memory_v1",
                    "memory_id": f"{trajectory.trajectory_id}:transition:{step.step_index}",
                    "parent_id": trajectory.trajectory_id,
                    "task_id": task.task_id,
                    "split": "train",
                    "task_family": task.metadata["task_family"],
                    "step_index": step.step_index,
                    "goal": task.instruction,
                    "pre_action_state": step.pre_action_state,
                    "action": step.action,
                    "post_action_observation": step.post_action_observation,
                    "transition_text": "\n".join(
                        (
                            f"Goal: {task.instruction}",
                            f"State: {step.pre_action_state}",
                            f"Action: {step.action}",
                            f"Observation: {step.post_action_observation}",
                        )
                    ),
                    "state_text": f"Goal: {task.instruction}\nState: {step.pre_action_state}",
                    "provenance": "OFFICIAL_EXPERT",
                    "replay_sequence_sha256": corpus_row["sequence_sha256"],
                    "task_manifest_sha256": TASK_MANIFEST_SHA256,
                }
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                stream.write("\n")
                transition_count += 1
                family = str(task.metadata["task_family"])
                task_counts[family] = task_counts.get(family, 0) + 1
    manifest = {
        "schema_version": "alfworld_transition_memory_ledger_manifest_v1",
        "source_commit": args.source_commit,
        "run_uuid": args.run_uuid,
        "split": "train",
        "provenance": "OFFICIAL_EXPERT",
        "task_manifest_sha256": TASK_MANIFEST_SHA256,
        "corpus": {"path": str(Path(args.corpus).resolve()), "sha256": sha256_file(Path(args.corpus))},
        "admitted_trajectory_count": len(admitted),
        "excluded_failed_trajectory_count": len(corpus_rows) - len(admitted),
        "transition_count": transition_count,
        "family_transition_counts": dict(sorted(task_counts.items())),
        "ledger": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha256_file(output)},
        "complete": True,
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_output.with_suffix(manifest_output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_output)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
