"""Paired task and trajectory analysis for EXP-036A."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.rcmf_appworld_testnormal_final_13a import (
    CONDITIONS,
    GLOBAL_SEED,
    PAIRED_COMPARISONS,
    paired_bootstrap,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json
from scripts.analyze_rcmf_one_demo_dev_11a import (
    exact_mcnemar,
    family_concentration,
    leave_one_task_out,
    trajectory_metrics,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=100_000)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_path(root: Path, condition: str, task_id: str) -> Path:
    return root / "formal/conditions" / condition / "task_results" / f"{task_id}.json"


def paired_sets(
    task_ids: list[str], left: Mapping[str, bool], right: Mapping[str, bool]
) -> dict[str, list[str]]:
    return {
        "both_success": [task for task in task_ids if left[task] and right[task]],
        "left_only": [task for task in task_ids if left[task] and not right[task]],
        "right_only": [task for task in task_ids if not left[task] and right[task]],
        "both_failed": [task for task in task_ids if not left[task] and not right[task]],
    }


def gpu_metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    peak_allocated = [int(row["resource_metrics"]["peak_allocated_bytes"]) for row in rows]
    peak_reserved = [int(row["resource_metrics"]["peak_reserved_bytes"]) for row in rows]
    gpu_active = 0.0
    environment = evaluator = 0.0
    for row in rows:
        evaluator += float(row["resource_metrics"]["evaluator_seconds"])
        for step in row["steps"]:
            gpu_active += float(step["generation_seconds"])
            gpu_active += float(step["field"]["query_seconds"])
            gpu_active += float(step["field"]["field_read_seconds"])
            environment += float(step.get("environment_execution_seconds", 0.0))
    return {
        "peak_allocated_bytes": max(peak_allocated),
        "peak_reserved_bytes": max(peak_reserved),
        "mean_peak_allocated_bytes": statistics.fmean(peak_allocated),
        "mean_peak_reserved_bytes": statistics.fmean(peak_reserved),
        "measured_model_gpu_active_seconds": gpu_active,
        "environment_execution_seconds": environment,
        "evaluator_seconds": evaluator,
    }


def analyze(artifact_dir: Path, *, replicates: int) -> dict[str, Any]:
    final = read_json(artifact_dir / "results/formal_summary.json")
    if not bool(final["evaluation_complete"]) or int(final["trajectory_count"]) != 840:
        raise RuntimeError("EXP-036A formal evaluation is incomplete")
    task_ids = [str(value) for value in final["task_ids"]]
    tasks = {
        condition: {
            task_id: read_json(task_path(artifact_dir, condition, task_id))
            for task_id in task_ids
        }
        for condition in CONDITIONS
    }
    success = {
        condition: {
            task_id: bool(tasks[condition][task_id]["success"]) for task_id in task_ids
        }
        for condition in CONDITIONS
    }
    comparisons = {}
    for left, right in PAIRED_COMPARISONS:
        key = f"{left}_minus_{right}"
        left_values = [success[left][task] for task in task_ids]
        right_values = [success[right][task] for task in task_ids]
        sets = paired_sets(task_ids, success[left], success[right])
        comparisons[key] = {
            "left": left,
            "right": right,
            "effect_count": sum(left_values) - sum(right_values),
            "effect_rate": (sum(left_values) - sum(right_values)) / len(task_ids),
            "paired_sets": sets,
            "paired_bootstrap_95_ci": paired_bootstrap(
                left_values, right_values, replicates=replicates
            ),
            "exact_mcnemar": exact_mcnemar(left_values, right_values),
            "leave_one_task_out": leave_one_task_out(left_values, right_values),
            "family_concentration": {
                "left_only": family_concentration(sets["left_only"]),
                "right_only": family_concentration(sets["right_only"]),
            },
        }
    per_task = [
        {
            "task_id": task_id,
            "family": task_id.rsplit("_", 1)[0],
            "success": {condition: success[condition][task_id] for condition in CONDITIONS},
            "steps": {
                condition: int(tasks[condition][task_id]["step_count"])
                for condition in CONDITIONS
            },
            "wall_seconds": {
                condition: float(tasks[condition][task_id]["wall_seconds"])
                for condition in CONDITIONS
            },
        }
        for task_id in task_ids
    ]
    condition_rows = {
        condition: [tasks[condition][task] for task in task_ids]
        for condition in CONDITIONS
    }
    result = {
        "format": "rcmf_appworld_testnormal_paired_analysis_13a_v1",
        "run_uuid": "rcmf_appworld_testnormal_final_13a_20260831_001",
        "global_seed": GLOBAL_SEED,
        "task_count": len(task_ids),
        "trajectory_count": len(task_ids) * len(CONDITIONS),
        "success": {
            condition: {
                "count": sum(values.values()),
                "rate": sum(values.values()) / len(task_ids),
                "task_ids": [task for task in task_ids if values[task]],
            }
            for condition, values in success.items()
        },
        "comparisons": comparisons,
        "trajectory_metrics": {
            condition: trajectory_metrics(condition_rows[condition])
            for condition in CONDITIONS
        },
        "gpu_and_execution_metrics": {
            condition: gpu_metrics(condition_rows[condition])
            for condition in CONDITIONS
        },
        "per_task": per_task,
        "claims": {
            "primary_method": "BEST",
            "secondary_ablation": "FULL1D",
            "test_normal_partially_exposed": True,
            "confirmatory_generalization_claim": False,
            "framework_verdict": "reserved_for_user_and_chatgpt_review",
        },
    }
    result["analysis_sha256"] = canonical_sha256(result)
    atomic_write_json(artifact_dir / "analysis/paired_analysis.json", result)
    atomic_write_json(
        artifact_dir / "analysis/trajectory_metrics.json", result["trajectory_metrics"]
    )
    atomic_write_json(
        artifact_dir / "analysis/formal_efficiency.json",
        result["gpu_and_execution_metrics"],
    )
    path = artifact_dir / "analysis/per_task.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in per_task),
        encoding="utf-8",
    )
    temporary.replace(path)
    return result


def main() -> None:
    args = parse_args()
    result = analyze(args.artifact_dir, replicates=args.bootstrap_replicates)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
