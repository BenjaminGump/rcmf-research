from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import torch

from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    GENERATION_IDENTITY_SHA256,
    MODEL_REVISION,
    TRACK_R_ID,
    create_alfworld_portable_adapter_v2_1,
)
from rcmf.benchmarks.alfworld.execution_lock import (
    TRACK_R_LOCK_IDENTITY_SHA256,
    load_execution_lock,
)
from rcmf.benchmarks.alfworld.runtime_agent import (
    load_frozen_qwen,
    run_alfworld_episode,
    run_alfworld_episodes_batched,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    TRACK_R_TASK_IDS_SHA256,
    canonical_sha256,
    load_sealed_task_manifest,
)
from rcmf.benchmarks.alfworld.training import (
    deployment_memory_query,
    load_deployment_checkpoint,
    sha256_file,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exact frozen-Qwen ALFWorld episodes")
    parser.add_argument("--condition", choices=("bare", "rcmf"), required=True)
    parser.add_argument("--split", choices=("train", "valid_unseen"), default="valid_unseen")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--trajectory-corpus", required=True)
    parser.add_argument("--prompt-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--model-snapshot", required=True)
    parser.add_argument("--benchmark-lock", required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--task-ids-json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--generation-batch-size", type=int, default=1)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_existing(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ids = [str(row["task_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("agent output contains duplicate task IDs")
    return rows


def _order_tasks_to_frozen_manifest(
    tasks: list[object],
    manifest_rows: list[dict[str, object]],
    *,
    split: str,
    expected_order_sha256: str,
) -> list[object]:
    ordered_ids = [str(row["task_id"]) for row in manifest_rows if row["split"] == split]
    actual_order_sha256 = canonical_sha256(ordered_ids)
    if actual_order_sha256 != expected_order_sha256:
        raise ValueError("sealed task-manifest evaluation order differs from benchmark lock")
    task_index = {str(task.task_id): task for task in tasks}
    if len(task_index) != len(tasks) or set(task_index) != set(ordered_ids):
        raise ValueError("adapter task population differs from sealed task-manifest order")
    return [task_index[task_id] for task_id in ordered_ids]


def main() -> int:
    args = arguments()
    if (args.condition == "rcmf") != bool(args.checkpoint):
        raise ValueError("RCMF condition requires exactly one checkpoint; bare rejects it")
    if args.generation_batch_size <= 0:
        raise ValueError("generation batch size must be positive")
    if args.formal and (args.task_ids_json or args.split != "valid_unseen"):
        raise ValueError("formal Track R evaluation requires the complete valid_unseen split")
    benchmark_lock = load_execution_lock(args.benchmark_lock)
    if benchmark_lock["lock_identity_sha256"] != TRACK_R_LOCK_IDENTITY_SHA256:
        raise ValueError("agent execution requires the current Harness v2 action-boundary lock")
    microbatch_max = int(
        benchmark_lock["payload"]["runtime_execution"]["microbatch_max_size"]
    )
    if args.generation_batch_size > microbatch_max:
        raise ValueError("generation batch size exceeds the frozen benchmark lock")
    adapter = create_alfworld_portable_adapter_v2_1(
        task_manifest_path=args.task_manifest,
        trajectory_corpus_path=args.trajectory_corpus,
        prompt_root=args.prompt_root,
        data_root=args.data_root,
        tokenizer_path=args.model_snapshot,
    )
    tasks = list(adapter.list_tasks()[args.split])
    if args.split == "valid_unseen" and canonical_sha256(
        sorted(task.task_id for task in tasks)
    ) != TRACK_R_TASK_IDS_SHA256:
        raise ValueError("Track R full valid_unseen population identity differs")
    if args.split == "valid_unseen":
        tasks = _order_tasks_to_frozen_manifest(
            tasks,
            load_sealed_task_manifest(args.task_manifest),
            split=args.split,
            expected_order_sha256=str(
                benchmark_lock["payload"]["runtime_execution"][
                    "deterministic_evaluation_order_sha256"
                ]
            ),
        )
    task_list_role = "formal_track_r_complete" if args.formal else "engineering_subset"
    if args.task_ids_json:
        selected_ids = json.loads(Path(args.task_ids_json).read_text(encoding="utf-8"))
        if not isinstance(selected_ids, list) or not selected_ids:
            raise ValueError("task ID selection must be a non-empty array")
        task_index = {task.task_id: task for task in tasks}
        if len(selected_ids) != len(set(selected_ids)) or any(item not in task_index for item in selected_ids):
            raise ValueError("task ID selection is duplicate or outside Track R")
        tasks = [task_index[str(item)] for item in selected_ids]
    if args.formal and len(tasks) != 134:
        raise ValueError("formal Track R evaluation requires all 134 tasks")

    output = Path(args.output).resolve()
    summary_output = Path(args.summary_output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prior = _read_existing(output)
    completed = {str(row["task_id"]) for row in prior}
    run_identity = {
        "run_uuid": args.run_uuid,
        "source_commit": args.source_commit,
        "track_id": TRACK_R_ID,
        "track_role": "UPSTREAM_PROTOCOL_REFERENCE",
        "task_list_role": task_list_role,
        "split": args.split,
        "task_ids_sha256": canonical_sha256(sorted(task.task_id for task in tasks)),
        "evaluation_order_sha256": canonical_sha256(
            [task.task_id for task in tasks]
        ),
        "condition": args.condition,
        "checkpoint_sha256": sha256_file(args.checkpoint) if args.checkpoint else None,
        "benchmark_lock_sha256": benchmark_lock["file_sha256"],
        "benchmark_lock_identity_sha256": benchmark_lock["lock_identity_sha256"],
        "generation_batch_size": args.generation_batch_size,
        "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
        "model_revision": MODEL_REVISION,
    }
    for row in prior:
        if row.get("run_identity") != run_identity:
            raise ValueError("existing agent output run identity differs")

    process_started = time.time()
    started_utc = datetime.now(timezone.utc).isoformat()
    flash_identity = benchmark_lock["payload"]["runtime_execution"][
        "flash_attn_installation_manifest_sha256"
    ]
    backend = load_frozen_qwen(
        args.model_snapshot,
        flash_attn_installation_sha256=flash_identity,
    )
    deployment = (
        load_deployment_checkpoint(args.checkpoint, device=backend.device)
        if args.checkpoint
        else None
    )
    model_loaded_utc = datetime.now(timezone.utc).isoformat()
    pending = [task for task in tasks if task.task_id not in completed]
    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for start in range(0, len(pending), args.generation_batch_size):
            group = pending[start : start + args.generation_batch_size]
            if args.generation_batch_size == 1:
                task = group[0]
                group_rows = [
                    run_alfworld_episode(
                        adapter=adapter,
                        task=task,
                        backend=backend,
                        condition=args.condition,
                        run_identity=run_identity,
                        injector=deployment["modules"]["injector"] if deployment else None,
                        memory_query=(
                            deployment_memory_query(
                                deployment,
                                task_instruction=task.instruction,
                                device=backend.device,
                            )
                            if deployment
                            else None
                        ),
                    )
                ]
            else:
                queries = (
                    {
                        task.task_id: deployment_memory_query(
                            deployment,
                            task_instruction=task.instruction,
                            device=backend.device,
                        )
                        for task in group
                    }
                    if deployment
                    else None
                )
                group_rows = run_alfworld_episodes_batched(
                    adapter=adapter,
                    tasks=group,
                    backend=backend,
                    condition=args.condition,
                    run_identity=run_identity,
                    batch_size=args.generation_batch_size,
                    injector=deployment["modules"]["injector"] if deployment else None,
                    memory_queries=queries,
                )
            for offset, row in enumerate(group_rows, 1):
                stream.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                print(
                    json.dumps(
                        {
                            "ordinal": len(completed) + start + offset,
                            "population": len(tasks),
                            "task_id": row["task_id"],
                            "success": row["official_success"],
                            "steps": row["step_count"],
                            "error": row["error"],
                            "elapsed_seconds": row["elapsed_seconds"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    rows = _read_existing(output)
    ended_utc = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": "alfworld_agent_run_summary_v1",
        "run_identity": run_identity,
        "started_utc": started_utc,
        "model_loaded_utc": model_loaded_utc,
        "ended_utc": ended_utc,
        "elapsed_seconds": round(time.time() - process_started, 6),
        "requested_tasks": len(tasks),
        "completed_tasks": len(rows),
        "all_tasks_completed": len(rows) == len(tasks),
        "successes": sum(bool(row["official_success"]) for row in rows),
        "typed_failures": sum(row.get("error") is not None for row in rows),
        "output": {"path": str(output), "bytes": output.stat().st_size, "sha256": _sha256(output)},
        "peak_cuda_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0,
        "model_weights_loaded": True,
        "model_forward_and_generation_run": True,
    }
    temp = summary_output.with_suffix(summary_output.suffix + ".tmp")
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, summary_output)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if summary["all_tasks_completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
