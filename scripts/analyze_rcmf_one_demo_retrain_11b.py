"""Paired EXP-034A analysis against reused D0 and immutable EXP-033A D1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json
from scripts.analyze_rcmf_one_demo_dev_11a import (
    exact_mcnemar,
    family_concentration,
    leave_one_task_out,
    paired_bootstrap_ci,
    trajectory_metrics,
)


GLOBAL_SEED = 25101
RUN_UUID = "rcmf_exp031a_one_demo_retrain_11b_20260829_001"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_path(root: Path, condition: str, task_id: str) -> Path:
    return root / f"dev/conditions/{condition}/task_results/{task_id}.json"


def _paired_sets(
    task_ids: Sequence[str], left: Mapping[str, bool], right: Mapping[str, bool]
) -> dict[str, list[str]]:
    return {
        "both_success": [task for task in task_ids if left[task] and right[task]],
        "left_only": [task for task in task_ids if left[task] and not right[task]],
        "right_only": [task for task in task_ids if not left[task] and right[task]],
        "both_failed": [task for task in task_ids if not left[task] and not right[task]],
    }


def _comparison(
    task_ids: Sequence[str], left: Mapping[str, bool], right: Mapping[str, bool], *, replicates: int
) -> dict[str, Any]:
    left_values = [left[task] for task in task_ids]
    right_values = [right[task] for task in task_ids]
    sets = _paired_sets(task_ids, left, right)
    return {
        "effect": (sum(left_values) - sum(right_values)) / len(task_ids),
        "paired_sets": sets,
        "paired_bootstrap_95_ci": paired_bootstrap_ci(
            left_values, right_values, seed=GLOBAL_SEED, replicates=replicates
        ),
        "exact_mcnemar": exact_mcnemar(left_values, right_values),
        "leave_one_task_out": leave_one_task_out(left_values, right_values),
        "family_concentration": {
            "left_only": family_concentration(sets["left_only"]),
            "right_only": family_concentration(sets["right_only"]),
        },
    }


def analyze(artifact_dir: Path, old_root: Path, *, replicates: int) -> dict[str, Any]:
    final = _json(artifact_dir / "dev/final_summary.json")
    if not bool(final["evaluation_complete"]):
        raise RuntimeError("EXP-034A dev evaluation is incomplete")
    task_ids = [str(value) for value in final["task_ids"]]
    tasks = {
        "D0": {task: _json(_task_path(old_root, "D0", task)) for task in task_ids},
        "old_D1": {task: _json(_task_path(old_root, "D1", task)) for task in task_ids},
        "N1": {task: _json(_task_path(artifact_dir, "N1", task)) for task in task_ids},
        "N2": {task: _json(_task_path(artifact_dir, "N2", task)) for task in task_ids},
    }
    success = {
        condition: {task: bool(rows[task]["success"]) for task in task_ids}
        for condition, rows in tasks.items()
    }
    comparisons = {
        "N1_vs_D0": _comparison(task_ids, success["N1"], success["D0"], replicates=replicates),
        "N1_vs_N2": _comparison(task_ids, success["N1"], success["N2"], replicates=replicates),
        "N1_vs_old_D1": _comparison(
            task_ids, success["N1"], success["old_D1"], replicates=replicates
        ),
    }
    old_gain_ids = comparisons["N1_vs_D0"]["paired_sets"]
    old_d1_vs_d0 = _paired_sets(task_ids, success["old_D1"], success["D0"])
    old_gains = old_d1_vs_d0["left_only"]
    old_losses = old_d1_vs_d0["right_only"]
    fate = {
        "old_exp033a_11_gains": {
            "task_ids": old_gains,
            "retained_by_N1": [task for task in old_gains if success["N1"][task]],
            "lost_by_N1": [task for task in old_gains if not success["N1"][task]],
        },
        "old_exp033a_6_losses": {
            "task_ids": old_losses,
            "recovered_by_N1": [task for task in old_losses if success["N1"][task]],
            "still_failed_by_N1": [task for task in old_losses if not success["N1"][task]],
        },
    }
    if len(old_gains) != 11 or len(old_losses) != 6:
        raise RuntimeError("Immutable EXP-033A gain/loss sets differ")
    n1_d0 = comparisons["N1_vs_D0"]["effect"]
    n1_n2 = comparisons["N1_vs_N2"]["effect"]
    n1_old = comparisons["N1_vs_old_D1"]["effect"]
    result = {
        "format": "rcmf_one_demo_retrain_paired_analysis_11b_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "task_count": len(task_ids),
        "success": {
            condition: {
                "count": sum(values.values()),
                "rate": sum(values.values()) / len(task_ids),
                "task_ids": [task for task in task_ids if values[task]],
            }
            for condition, values in success.items()
        },
        "comparisons": comparisons,
        "old_gain_loss_fate": fate,
        "trajectory_metrics": {
            condition: trajectory_metrics([tasks[condition][task] for task in task_ids])
            for condition in tasks
        },
        "descriptive_classification": {
            "N1_exceeds_D0_point_estimate": n1_d0 > 0,
            "N1_exceeds_N2_point_estimate": n1_n2 > 0,
            "N1_improves_old_D1_point_estimate": n1_old > 0,
            "no_architecture_decision": True,
        },
        "dev_used_for_training_or_checkpoint_selection": False,
        "dev_is_developer_exposed": True,
        "no_final_statistical_generalization_claim": True,
    }
    atomic_write_json(artifact_dir / "analysis/paired_analysis.json", result)
    atomic_write_json(
        artifact_dir / "analysis/trajectory_metrics.json", result["trajectory_metrics"]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--old-artifact-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=100_000)
    args = parser.parse_args()
    result = analyze(
        args.artifact_dir, args.old_artifact_dir, replicates=args.bootstrap_replicates
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
