from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.benchmarks.webshop.evaluation import CONDITIONS, EVALUATION_TASK_FORMAT
from rcmf.pipeline.manifests import content_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _bootstrap_ci(values: Sequence[float], *, seed: int, samples: int = 10000) -> list[float]:
    if not values:
        return [float("nan"), float("nan")]
    rng = random.Random(seed)
    means = [
        statistics.fmean(values[rng.randrange(len(values))] for _ in values) for _ in range(samples)
    ]
    return [_quantile(means, 0.025), _quantile(means, 0.975)]


def _mcnemar(left: Sequence[bool], right: Sequence[bool]) -> Mapping[str, object]:
    left_only = sum(a and not b for a, b in zip(left, right, strict=True))
    right_only = sum(b and not a for a, b in zip(left, right, strict=True))
    discordant = left_only + right_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, k) * (0.5**discordant)
            for k in range(min(left_only, right_only) + 1)
        )
        p_value = min(1.0, 2.0 * tail)
    return {
        "left_only_success": left_only,
        "right_only_success": right_only,
        "discordant": discordant,
        "exact_two_sided_p": p_value,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-lock", type=Path, required=True)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision-output", type=Path)
    args = parser.parse_args()
    lock = _object(args.evaluation_lock)
    lock_body = dict(lock)
    lock_sha = lock_body.pop("lock_sha256", None)
    if lock_sha != content_sha256(lock_body):
        raise RuntimeError("WebShop evaluation lock hash differs")
    by_condition: dict[str, dict[str, Mapping[str, object]]] = {}
    summaries = {}
    for condition in CONDITIONS:
        summary_path = args.evaluation_root / condition / "summary.json"
        summary = _object(summary_path)
        if (
            summary.get("evaluation_lock_sha256") != lock_sha
            or summary.get("condition") != condition
            or int(summary.get("task_count", -1)) != int(lock["task_count"])
        ):
            raise RuntimeError(f"WebShop condition summary differs: {condition}")
        summaries[condition] = summary
        rows = {}
        for path in sorted((args.evaluation_root / condition / "raw_tasks").glob("task_*.json")):
            row = _object(path)
            body = dict(row)
            recorded = body.pop("task_artifact_sha256", None)
            if (
                row.get("format") != EVALUATION_TASK_FORMAT
                or row.get("evaluation_lock_sha256") != lock_sha
                or row.get("condition") != condition
                or recorded != content_sha256(body)
            ):
                raise RuntimeError(f"WebShop task artifact differs: {path}")
            rows[str(row["task_id"])] = row
        by_condition[condition] = rows
    task_ids = list(lock["ordered_task_ids"])
    if any(set(rows) != set(task_ids) for rows in by_condition.values()):
        raise RuntimeError("WebShop paired condition task sets differ")
    rewards = {
        condition: [float(by_condition[condition][task_id]["raw_reward"]) for task_id in task_ids]
        for condition in CONDITIONS
    }
    comparisons = {}
    for left, right in (("RCMF-C", "B0"), ("RCMF-C", "RCMF-S")):
        differences = [a - b for a, b in zip(rewards[left], rewards[right], strict=True)]
        comparisons[f"{left}_minus_{right}"] = {
            "mean_paired_reward_difference": statistics.fmean(differences),
            "median_paired_reward_difference": statistics.median(differences),
            "bootstrap_95_ci": _bootstrap_ci(
                differences,
                seed=25101 + (0 if right == "B0" else 1),
            ),
            "gain_task_ids": [
                task_id for task_id, value in zip(task_ids, differences, strict=True) if value > 0
            ],
            "loss_task_ids": [
                task_id for task_id, value in zip(task_ids, differences, strict=True) if value < 0
            ],
            "tie_count": sum(value == 0 for value in differences),
            "mcnemar": _mcnemar(
                [value == 1.0 for value in rewards[left]],
                [value == 1.0 for value in rewards[right]],
            ),
        }
    condition_metrics = {}
    for condition in CONDITIONS:
        values = rewards[condition]
        condition_metrics[condition] = {
            "mean_raw_reward": statistics.fmean(values),
            "median_raw_reward": statistics.median(values),
            "bootstrap_mean_95_ci": _bootstrap_ci(values, seed=25200 + CONDITIONS.index(condition)),
            "full_success_count": sum(value == 1.0 for value in values),
            "full_success_rate": statistics.fmean(value == 1.0 for value in values),
            "reward_distribution": dict(
                sorted({str(value): values.count(value) for value in sorted(set(values))}.items())
            ),
            "summary_sha256": sha256_file(args.evaluation_root / condition / "summary.json"),
        }
    analysis = {
        "format": "rcmf_agentbench_fc_webshop_paired_analysis_v1",
        "scope": lock["scope"],
        "evaluation_lock_sha256": lock_sha,
        "task_count": len(task_ids),
        "ordered_task_ids": task_ids,
        "condition_metrics": condition_metrics,
        "paired_comparisons": comparisons,
        "all_trajectories_complete": all(
            int(summaries[condition]["completed_task_count"]) == len(task_ids)
            for condition in CONDITIONS
        ),
        "excluded_200_499_executed": False,
        "raw_memory_prompt_used": False,
        "scientific_label": (
            "AgentBench-FC WebShop standard-200"
            if lock["scope"] == "standard200"
            else "AgentBench-FC WebShop fixed validation-50"
        ),
    }
    analysis["analysis_sha256"] = content_sha256(analysis)
    atomic_write_json(args.output, analysis)
    if args.decision_output is not None:
        if lock["scope"] != "validation":
            raise ValueError("a method decision may only be written from validation")
        correct_bare = float(comparisons["RCMF-C_minus_B0"]["mean_paired_reward_difference"])
        correct_shuffle = float(comparisons["RCMF-C_minus_RCMF-S"]["mean_paired_reward_difference"])
        proceed = bool(
            analysis["all_trajectories_complete"] and correct_bare > 0.0 and correct_shuffle > 0.0
        )
        decision = {
            "format": "rcmf_agentbench_fc_webshop_validation_decision_v1",
            "decision": "PROCEED" if proceed else "REVISE",
            "decision_rule": (
                "directional mechanism gate: correct field must have strictly positive "
                "paired mean reward versus both bare and matched shuffled field over the "
                "fixed 50-task validation population; no arbitrary effect threshold"
            ),
            "validation_analysis_sha256": sha256_file(args.output),
            "method_package_sha256": lock["method_package_sha256"],
            "correct_minus_bare": correct_bare,
            "correct_minus_shuffle": correct_shuffle,
            "standard200_outcome_inspected": False,
        }
        decision["decision_sha256"] = content_sha256(decision)
        atomic_write_json(args.decision_output, decision)
    print(json.dumps(analysis, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
