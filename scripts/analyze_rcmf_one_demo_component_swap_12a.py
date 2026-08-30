"""Preregistered paired task analysis for EXP-035A."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.training.rcmf_one_demo_component_swap_12a import CELL_NAMES, CONDITIONS
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json, atomic_write_text
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence


BOOTSTRAP_SAMPLES = 100_000
ANALYSIS_SEED = 25101


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_path(root: Path, condition: str, task_id: str) -> Path:
    return root / "trajectories/conditions" / condition / "task_results" / f"{task_id}.json"


def exact_mcnemar(correct: Sequence[bool], shuffle: Sequence[bool]) -> dict[str, Any]:
    correct_only = sum(int(left and not right) for left, right in zip(correct, shuffle, strict=True))
    shuffle_only = sum(int(right and not left) for left, right in zip(correct, shuffle, strict=True))
    discordant = correct_only + shuffle_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, index) for index in range(min(correct_only, shuffle_only) + 1))
        p_value = min(1.0, 2.0 * tail / (2**discordant))
    return {
        "correct_only": correct_only,
        "shuffle_only": shuffle_only,
        "discordant": discordant,
        "exact_two_sided_p": p_value,
    }


def statistics_for_indices(
    success: Mapping[str, Sequence[bool]], indices: Sequence[int]
) -> dict[str, float]:
    deltas = {}
    for cell in CELL_NAMES:
        differences = [
            int(success[f"{cell}-C"][index]) - int(success[f"{cell}-S"][index])
            for index in indices
        ]
        deltas[cell] = statistics.fmean(differences)
    selector_old_wr = deltas["OO"] - deltas["FO"]
    selector_fresh_wr = deltas["OF"] - deltas["FF"]
    wr_old_selector = deltas["OO"] - deltas["OF"]
    wr_fresh_selector = deltas["FO"] - deltas["FF"]
    return {
        "Delta_OO": deltas["OO"],
        "Delta_OF": deltas["OF"],
        "Delta_FO": deltas["FO"],
        "Delta_FF": deltas["FF"],
        "selector_old_WR": selector_old_wr,
        "selector_fresh_WR": selector_fresh_wr,
        "M_selector": 0.5 * (selector_old_wr + selector_fresh_wr),
        "WR_old_selector": wr_old_selector,
        "WR_fresh_selector": wr_fresh_selector,
        "M_WR": 0.5 * (wr_old_selector + wr_fresh_selector),
        "interaction": deltas["OO"] - deltas["OF"] - deltas["FO"] + deltas["FF"],
    }


def percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap(success: Mapping[str, Sequence[bool]], task_count: int) -> dict[str, Any]:
    generator = random.Random(ANALYSIS_SEED)
    values: dict[str, list[float]] = {}
    for _ in range(BOOTSTRAP_SAMPLES):
        indices = [generator.randrange(task_count) for _ in range(task_count)]
        row = statistics_for_indices(success, indices)
        for key, value in row.items():
            values.setdefault(key, []).append(value)
    return {
        key: {
            "lower_95": percentile(samples, 0.025),
            "upper_95": percentile(samples, 0.975),
        }
        for key, samples in values.items()
    }


def leave_one_out(success: Mapping[str, Sequence[bool]], task_count: int) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    for omitted in range(task_count):
        indices = [index for index in range(task_count) if index != omitted]
        row = statistics_for_indices(success, indices)
        for key, value in row.items():
            values.setdefault(key, []).append(value)
    output = {}
    full = statistics_for_indices(success, list(range(task_count)))
    for key, samples in values.items():
        point = full[key]
        direction = 0 if point == 0 else 1 if point > 0 else -1
        direction_changes = any(
            (0 if value == 0 else 1 if value > 0 else -1) != direction for value in samples
        )
        output[key] = {
            "minimum": min(samples),
            "maximum": max(samples),
            "values": samples,
            "deleting_one_task_changes_direction": direction_changes,
        }
    return output


def loop_count(row: Mapping[str, Any]) -> int:
    counts = row.get("counts", {})
    return int(counts.get("repeated_action", 0)) + int(counts.get("repeated_invalid_action", 0))


def classify(point: Mapping[str, float], loo: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    selector_consistent = point["selector_old_WR"] > 0 and point["selector_fresh_WR"] > 0
    wr_consistent = point["WR_old_selector"] > 0 and point["WR_fresh_selector"] > 0
    selector_stable = selector_consistent and not loo["M_selector"][
        "deleting_one_task_changes_direction"
    ]
    wr_stable = wr_consistent and not loo["M_WR"]["deleting_one_task_changes_direction"]
    interaction_stable = point["interaction"] != 0 and not loo["interaction"][
        "deleting_one_task_changes_direction"
    ]
    if selector_stable and point["M_selector"] > abs(point["M_WR"]):
        decision = "PROCEED_SELECTOR_HYPOTHESIS"
    elif wr_stable and point["M_WR"] > abs(point["M_selector"]):
        decision = "PROCEED_WRITER_READER_HYPOTHESIS"
    elif (
        interaction_stable
        and not selector_stable
        and not wr_stable
        and point["Delta_OO"] > min(point["Delta_OF"], point["Delta_FO"])
    ):
        decision = "PROCEED_COADAPTATION_HYPOTHESIS"
    else:
        decision = "INCONCLUSIVE"
    return {
        "decision": decision,
        "selector_contrasts_directionally_consistent": selector_consistent,
        "selector_loo_stable": selector_stable,
        "writer_reader_contrasts_directionally_consistent": wr_consistent,
        "writer_reader_loo_stable": wr_stable,
        "interaction_loo_stable": interaction_stable,
        "heldout_diagnostic_not_confirmatory": True,
    }


def main() -> None:
    args = parse_args()
    manifest = read_json(args.artifact_dir / "manifests/condition_manifest.json")
    task_ids = [str(value) for value in manifest["task_ids"]]
    rows = {
        condition: {
            task_id: read_json(task_path(args.artifact_dir, condition, task_id))
            for task_id in task_ids
        }
        for condition in CONDITIONS
    }
    if any(
        row["status"] != "complete" or row["success_source"] != "evaluation.success"
        for condition_rows in rows.values()
        for row in condition_rows.values()
    ):
        raise RuntimeError("EXP-035A analysis found an invalid trajectory row")
    success = {
        condition: [bool(rows[condition][task_id]["success"]) for task_id in task_ids]
        for condition in CONDITIONS
    }
    point = statistics_for_indices(success, list(range(len(task_ids))))
    confidence = bootstrap(success, len(task_ids))
    loo = leave_one_out(success, len(task_ids))

    cell_results = {}
    for cell in CELL_NAMES:
        correct_condition, shuffle_condition = f"{cell}-C", f"{cell}-S"
        correct, shuffle = success[correct_condition], success[shuffle_condition]
        cell_results[cell] = {
            "correct_success_count": sum(correct),
            "shuffle_success_count": sum(shuffle),
            "Delta": point[f"Delta_{cell}"],
            "correct_only_task_ids": [
                task_id
                for task_id, left, right in zip(task_ids, correct, shuffle, strict=True)
                if left and not right
            ],
            "shuffle_only_task_ids": [
                task_id
                for task_id, left, right in zip(task_ids, correct, shuffle, strict=True)
                if right and not left
            ],
            "both_success_task_ids": [
                task_id
                for task_id, left, right in zip(task_ids, correct, shuffle, strict=True)
                if left and right
            ],
            "both_fail_task_ids": [
                task_id
                for task_id, left, right in zip(task_ids, correct, shuffle, strict=True)
                if not left and not right
            ],
            "mcnemar": exact_mcnemar(correct, shuffle),
            "bootstrap_95": confidence[f"Delta_{cell}"],
            "leave_one_task_out": loo[f"Delta_{cell}"],
        }

    condition_metrics = {}
    for condition in CONDITIONS:
        condition_rows = [rows[condition][task_id] for task_id in task_ids]
        condition_metrics[condition] = {
            "success_count": sum(bool(row["success"]) for row in condition_rows),
            "total_steps": sum(int(row["step_count"]) for row in condition_rows),
            "mean_steps": statistics.fmean(int(row["step_count"]) for row in condition_rows),
            "median_steps": statistics.median(int(row["step_count"]) for row in condition_rows),
            "loop_count": sum(loop_count(row) for row in condition_rows),
            "context_termination_count": sum(
                int(row["counts"].get("context_overflow", 0)) for row in condition_rows
            ),
            "completion_call_count": sum(
                int(row["counts"].get("completion_action", 0)) for row in condition_rows
            ),
            "premature_completion_count": sum(
                int(row["counts"].get("premature_completion", 0)) for row in condition_rows
            ),
            "execution_exception_count": sum(
                int(row["counts"].get("execution_exception", 0)) for row in condition_rows
            ),
            "prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in condition_rows),
            "generated_tokens": sum(
                int(row["usage"].get("completion_tokens", 0)) for row in condition_rows
            ),
            "wall_seconds": sum(float(row["wall_seconds"]) for row in condition_rows),
        }

    per_task = []
    comparisons = {}
    for task_id in task_ids:
        task_success = {condition: bool(rows[condition][task_id]["success"]) for condition in CONDITIONS}
        per_task.append(
            {
                "task_id": task_id,
                "success": task_success,
                "specificity": {
                    cell: int(task_success[f"{cell}-C"]) - int(task_success[f"{cell}-S"])
                    for cell in CELL_NAMES
                },
                "steps": {
                    condition: int(rows[condition][task_id]["step_count"])
                    for condition in CONDITIONS
                },
                "loops": {
                    condition: loop_count(rows[condition][task_id]) for condition in CONDITIONS
                },
            }
        )
        comparisons[task_id] = {
            cell: first_divergence(
                rows[f"{cell}-C"][task_id], rows[f"{cell}-S"][task_id]
            )
            for cell in CELL_NAMES
        }

    decision = classify(point, loo)
    analysis = {
        "format": "rcmf_one_demo_component_swap_analysis_12a_v1",
        "run_uuid": manifest["run_uuid"],
        "global_seed": ANALYSIS_SEED,
        "task_ids": task_ids,
        "task_count": len(task_ids),
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_unit": "task_with_all_eight_conditions_retained",
        "success_matrix": {condition: success[condition] for condition in CONDITIONS},
        "cells": cell_results,
        "contrasts": {
            key: {
                "point_estimate": point[key],
                "bootstrap_95": confidence[key],
                "leave_one_task_out": loo[key],
            }
            for key in (
                "selector_old_WR",
                "selector_fresh_WR",
                "M_selector",
                "WR_old_selector",
                "WR_fresh_selector",
                "M_WR",
                "interaction",
            )
        },
        "condition_metrics": condition_metrics,
        "per_task": per_task,
        "first_divergence": comparisons,
        "decision": decision,
        "mechanism_claim_policy": {
            "repeated_action_and_exception_counts": "VERIFIED from traces",
            "wrong_procedural_family": "INFERENCE unless exact trace/API evidence is materialized",
            "api_documentation_attractor": "INFERENCE unless exact emitted API calls support it",
            "global_bookkeeping_failure": "UNVERIFIED without evaluator-state evidence",
        },
        "official_dev_run": False,
        "first37_run": False,
        "test_normal_run": False,
        "test_challenge_run": False,
    }
    analysis["analysis_sha256"] = canonical_sha256(analysis)
    result_root = args.artifact_dir / "results"
    atomic_write_json(result_root / "analysis.json", analysis)
    atomic_write_text(
        result_root / "per_task.jsonl",
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in per_task),
    )
    atomic_write_json(result_root / "comparisons.json", comparisons)
    print(json.dumps(analysis, indent=2))


if __name__ == "__main__":
    main()
