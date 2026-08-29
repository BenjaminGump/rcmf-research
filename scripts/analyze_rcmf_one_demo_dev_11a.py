"""Paired task-level and trajectory analysis for EXP-033A."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json


GLOBAL_SEED = 25101
CONDITIONS = ("D0", "D1", "D2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=100_000)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_path(root: Path, condition: str, task_id: str) -> Path:
    return root / f"dev/conditions/{condition}/task_results/{task_id}.json"


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Cannot calculate a quantile from no values")
    location = (len(ordered) - 1) * probability
    lower = int(math.floor(location))
    upper = int(math.ceil(location))
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def paired_bootstrap_ci(
    left: Sequence[bool],
    right: Sequence[bool],
    *,
    seed: int = GLOBAL_SEED,
    replicates: int = 100_000,
) -> dict[str, Any]:
    if len(left) != len(right) or not left:
        raise ValueError("Paired bootstrap requires equal nonempty rows")
    rng = random.Random(seed)
    values = []
    for _ in range(replicates):
        delta = 0
        for _index in range(len(left)):
            selected = rng.randrange(len(left))
            delta += int(left[selected]) - int(right[selected])
        values.append(delta / len(left))
    observed = (sum(left) - sum(right)) / len(left)
    return {
        "observed": observed,
        "lower_95": _quantile(values, 0.025),
        "upper_95": _quantile(values, 0.975),
        "replicates": replicates,
        "seed": seed,
        "analysis_only_randomness": True,
    }


def exact_mcnemar(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    left_only = sum(bool(a) and not bool(b) for a, b in zip(left, right))
    right_only = sum(not bool(a) and bool(b) for a, b in zip(left, right))
    discordant = left_only + right_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, index) for index in range(min(left_only, right_only) + 1))
        p_value = min(1.0, 2.0 * tail / (2**discordant))
    return {
        "left_only": left_only,
        "right_only": right_only,
        "discordant": discordant,
        "two_sided_exact_p": p_value,
    }


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def leave_one_task_out(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    values = []
    for omitted in range(len(left)):
        denominator = len(left) - 1
        values.append(
            (
                sum(int(value) for index, value in enumerate(left) if index != omitted)
                - sum(int(value) for index, value in enumerate(right) if index != omitted)
            )
            / denominator
        )
    observed = (sum(left) - sum(right)) / len(left)
    return {
        "observed": observed,
        "minimum": min(values),
        "maximum": max(values),
        "qualitative_direction_changes_when_one_task_removed": any(
            _sign(value) != _sign(observed) for value in values
        ),
        "per_omission": values,
    }


def _family(task_id: str) -> str:
    return task_id.rsplit("_", 1)[0]


def family_concentration(task_ids: Sequence[str]) -> dict[str, Any]:
    counts = Counter(_family(task_id) for task_id in task_ids)
    total = sum(counts.values())
    maximum = max(counts.values(), default=0)
    return {
        "task_count": total,
        "family_count": len(counts),
        "counts": dict(sorted(counts.items())),
        "maximum_family_fraction": maximum / total if total else 0.0,
    }


def _strict_no_progress_loops(task: Mapping[str, Any]) -> int:
    count = 0
    steps = list(task["steps"])
    for previous, current in zip(steps, steps[1:]):
        if (
            previous["exact_executed_code"] == current["exact_executed_code"]
            and previous["complete_environment_observation"]
            == current["complete_environment_observation"]
        ):
            count += 1
    return count


def trajectory_metrics(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    steps = [int(task["step_count"]) for task in tasks]
    wall = [float(task["wall_seconds"]) for task in tasks]
    counts: Counter[str] = Counter()
    prompt_tokens = generated_tokens = strict_loops = 0
    query_seconds: list[float] = []
    read_seconds: list[float] = []
    slot_norms: list[float] = []
    residual_norms: list[float] = []
    attention_entropy: list[float] = []
    for task in tasks:
        counts.update(task["counts"])
        prompt_tokens += int(task["usage"].get("prompt_tokens", 0))
        generated_tokens += int(task["usage"].get("completion_tokens", 0))
        strict_loops += _strict_no_progress_loops(task)
        for step in task["steps"]:
            query_seconds.append(float(step["field"]["query_seconds"]))
            read_seconds.append(float(step["field"]["field_read_seconds"]))
            slot_norms.append(float(step["field"]["slots"]["norm"]))
            for values in step["reader_audit"].get("delta_norms", {}).values():
                residual_norms.extend(float(value) for value in values)
            for values in step["reader_audit"].get("attention_entropy", {}).values():
                attention_entropy.extend(float(value) for value in values)
    return {
        "task_count": len(tasks),
        "total_steps": sum(steps),
        "mean_steps": statistics.fmean(steps),
        "median_steps": statistics.median(steps),
        "prompt_tokens": prompt_tokens,
        "generated_tokens": generated_tokens,
        "execution_exceptions": int(counts["execution_exception"]),
        "context_limit_terminations": int(counts["context_overflow"]),
        "repeated_identical_actions": int(counts["repeated_action"]),
        "strict_no_progress_loops": strict_loops,
        "completion_calls": int(counts["completion_action"]),
        "mean_task_wall_seconds": statistics.fmean(wall),
        "mean_state_query_seconds": statistics.fmean(query_seconds),
        "mean_field_read_seconds": statistics.fmean(read_seconds),
        "mean_slot_norm": statistics.fmean(slot_norms),
        "mean_reader_residual_norm": (
            statistics.fmean(residual_norms) if residual_norms else 0.0
        ),
        "mean_reader_attention_entropy": (
            statistics.fmean(attention_entropy) if attention_entropy else None
        ),
    }


def _paired_sets(
    task_ids: Sequence[str], left: Mapping[str, bool], right: Mapping[str, bool]
) -> dict[str, list[str]]:
    return {
        "both_success": [task for task in task_ids if left[task] and right[task]],
        "left_only": [task for task in task_ids if left[task] and not right[task]],
        "right_only": [task for task in task_ids if not left[task] and right[task]],
        "both_failed": [task for task in task_ids if not left[task] and not right[task]],
    }


def analyze(artifact_dir: Path, *, replicates: int) -> dict[str, Any]:
    final = _json(artifact_dir / "dev/final_summary.json")
    if not bool(final["evaluation_complete"]):
        raise RuntimeError("EXP-033A formal dev evaluation is incomplete")
    task_ids = [str(value) for value in final["task_ids"]]
    tasks = {
        condition: {
            task_id: _json(_task_path(artifact_dir, condition, task_id))
            for task_id in task_ids
        }
        for condition in CONDITIONS
    }
    success = {
        condition: {task_id: bool(tasks[condition][task_id]["success"]) for task_id in task_ids}
        for condition in CONDITIONS
    }
    d1 = [success["D1"][task] for task in task_ids]
    d0 = [success["D0"][task] for task in task_ids]
    d2 = [success["D2"][task] for task in task_ids]
    d0_d1 = _paired_sets(task_ids, success["D1"], success["D0"])
    d2_d1 = _paired_sets(task_ids, success["D1"], success["D2"])
    absolute_effect = sum(d1) / len(d1) - sum(d0) / len(d0)
    specificity_effect = sum(d1) / len(d1) - sum(d2) / len(d2)
    if absolute_effect > 0 and specificity_effect <= 0:
        pattern = "absolute_improvement_without_matched_shuffle_specificity"
    elif specificity_effect > 0 and absolute_effect <= 0:
        pattern = "matched_shuffle_specificity_without_absolute_improvement"
    elif absolute_effect > 0 and specificity_effect > 0:
        pattern = "absolute_improvement_and_matched_shuffle_specificity"
    elif absolute_effect == 0 and specificity_effect == 0:
        pattern = "no_aggregate_difference"
    else:
        pattern = "nonpositive_or_mixed_aggregate_effect"
    result = {
        "format": "rcmf_one_demo_dev_paired_analysis_11a_v1",
        "run_uuid": "rcmf_exp031a_one_demo_dev_11a_20260829_001",
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
        "effects": {
            "D1_minus_D0": absolute_effect,
            "D1_minus_D2": specificity_effect,
        },
        "paired_sets": {"D1_vs_D0": d0_d1, "D1_vs_D2": d2_d1},
        "paired_bootstrap_95_ci": {
            "D1_minus_D0": paired_bootstrap_ci(d1, d0, replicates=replicates),
            "D1_minus_D2": paired_bootstrap_ci(d1, d2, replicates=replicates),
        },
        "exact_mcnemar": {
            "D1_vs_D0": exact_mcnemar(d1, d0),
            "D1_vs_D2": exact_mcnemar(d1, d2),
        },
        "leave_one_task_out": {
            "D1_minus_D0": leave_one_task_out(d1, d0),
            "D1_minus_D2": leave_one_task_out(d1, d2),
        },
        "family_concentration": {
            "D1_gains_over_D0": family_concentration(d0_d1["left_only"]),
            "D1_losses_to_D0": family_concentration(d0_d1["right_only"]),
            "D1_wins_over_D2": family_concentration(d2_d1["left_only"]),
            "D2_wins_over_D1": family_concentration(d2_d1["right_only"]),
        },
        "trajectory_metrics": {
            condition: trajectory_metrics([tasks[condition][task] for task in task_ids])
            for condition in CONDITIONS
        },
        "qualitative_pattern": pattern,
        "framework_verdict": "reserved_for_user_and_chatgpt_review",
        "dev_is_developer_exposed": True,
        "no_final_statistical_generalization_claim": True,
    }
    atomic_write_json(artifact_dir / "analysis/paired_analysis.json", result)
    atomic_write_json(
        artifact_dir / "analysis/trajectory_metrics.json", result["trajectory_metrics"]
    )
    return result


def main() -> None:
    args = parse_args()
    result = analyze(args.artifact_dir, replicates=args.bootstrap_replicates)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
