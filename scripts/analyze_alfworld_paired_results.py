from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
from statistics import median
from typing import Any, Iterable, Mapping

from rcmf.benchmarks.alfworld.portable_adapter_v2 import TRACK_R_ID
from rcmf.benchmarks.alfworld.task_manifest import (
    TRACK_R_TASK_IDS_SHA256,
    canonical_sha256,
    load_sealed_task_manifest,
    portable_task_records,
)


BOOTSTRAP_SEED = 25101
BOOTSTRAP_REPLICATES = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze the prospectively paired ALFWorld run")
    parser.add_argument("--bare", required=True)
    parser.add_argument("--rcmf", required=True)
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: str | Path, expected_condition: str) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 134:
        raise ValueError(f"{expected_condition} result does not contain exactly 134 rows")
    ids = [str(row["task_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{expected_condition} result contains duplicate task IDs")
    if any(row.get("condition") != expected_condition for row in rows):
        raise ValueError(f"{expected_condition} result condition differs")
    for row in rows:
        identity = row.get("run_identity") or {}
        if identity.get("track_id") != TRACK_R_ID:
            raise ValueError(f"{expected_condition} result track differs")
        if identity.get("task_list_role") != "formal_track_r_complete":
            raise ValueError(f"{expected_condition} result is not a formal complete run")
        if identity.get("task_ids_sha256") != TRACK_R_TASK_IDS_SHA256:
            raise ValueError(f"{expected_condition} task-list identity differs")
    return rows


def _nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def _paired_bootstrap(deltas: list[int]) -> Mapping[str, Any]:
    generator = random.Random(BOOTSTRAP_SEED)
    size = len(deltas)
    estimates = []
    for _ in range(BOOTSTRAP_REPLICATES):
        estimates.append(sum(deltas[generator.randrange(size)] for _ in range(size)) / size)
    estimates.sort()
    return {
        "method": "paired_nonparametric_task_bootstrap_nearest_rank",
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "lower_95": _nearest_rank(estimates, 0.025),
        "upper_95": _nearest_rank(estimates, 0.975),
    }


def _mcnemar_exact(gains: int, losses: int) -> Mapping[str, Any]:
    discordant = gains + losses
    if discordant == 0:
        p_value = 1.0
    else:
        tail = min(gains, losses)
        p_value = min(
            1.0,
            2.0 * sum(math.comb(discordant, index) for index in range(tail + 1)) / (2**discordant),
        )
    return {
        "method": "exact_two_sided_mcnemar_binomial",
        "gains": gains,
        "losses": losses,
        "discordant": discordant,
        "p_value": p_value,
    }


def _condition_diagnostics(row: Mapping[str, Any]) -> Mapping[str, Any]:
    steps = list(row.get("steps") or [])
    return {
        "step_count": int(row.get("step_count", len(steps))),
        "empty_or_invalid_actions": sum(not bool(step.get("action_valid")) for step in steps),
        "max_prompt_tokens": max((int(step.get("prompt_tokens", 0)) for step in steps), default=0),
        "error": row.get("error"),
        "terminal_status": row.get("terminal_status"),
    }


def main() -> int:
    args = parse_args()
    manifest_rows = load_sealed_task_manifest(args.task_manifest)
    expected_tasks = portable_task_records(manifest_rows)["valid_unseen"]
    expected_ids = sorted(task.task_id for task in expected_tasks)
    if len(expected_ids) != 134 or canonical_sha256(expected_ids) != TRACK_R_TASK_IDS_SHA256:
        raise ValueError("sealed Track R task population differs")
    families = {task.task_id: str(task.metadata["task_family"]) for task in expected_tasks}
    bare_rows = read_rows(args.bare, "bare")
    rcmf_rows = read_rows(args.rcmf, "rcmf")
    bare = {str(row["task_id"]): row for row in bare_rows}
    rcmf = {str(row["task_id"]): row for row in rcmf_rows}
    if sorted(bare) != expected_ids or sorted(rcmf) != expected_ids:
        raise ValueError("paired result population differs from sealed Track R")
    paired_rows = []
    for task_id in expected_ids:
        bare_row = bare[task_id]
        rcmf_row = rcmf[task_id]
        bare_success = bool(bare_row["official_success"])
        rcmf_success = bool(rcmf_row["official_success"])
        paired_rows.append(
            {
                "schema_version": "alfworld_paired_task_result_v1",
                "task_id": task_id,
                "task_family": families[task_id],
                "bare_success": bare_success,
                "rcmf_success": rcmf_success,
                "delta": int(rcmf_success) - int(bare_success),
                "bare": _condition_diagnostics(bare_row),
                "rcmf": _condition_diagnostics(rcmf_row),
                "bare_episode_sha256": bare_row["episode_sha256"],
                "rcmf_episode_sha256": rcmf_row["episode_sha256"],
            }
        )
    gains = sum(row["delta"] == 1 for row in paired_rows)
    losses = sum(row["delta"] == -1 for row in paired_rows)
    both_correct = sum(row["bare_success"] and row["rcmf_success"] for row in paired_rows)
    both_wrong = len(paired_rows) - gains - losses - both_correct
    bare_successes = sum(row["bare_success"] for row in paired_rows)
    rcmf_successes = sum(row["rcmf_success"] for row in paired_rows)
    deltas = [int(row["delta"]) for row in paired_rows]
    family_rows = []
    for family in sorted(set(families.values())):
        selected = [row for row in paired_rows if row["task_family"] == family]
        family_rows.append(
            {
                "task_family": family,
                "count": len(selected),
                "bare_successes": sum(row["bare_success"] for row in selected),
                "bare_accuracy": sum(row["bare_success"] for row in selected) / len(selected),
                "rcmf_successes": sum(row["rcmf_success"] for row in selected),
                "rcmf_accuracy": sum(row["rcmf_success"] for row in selected) / len(selected),
                "delta": sum(row["delta"] for row in selected) / len(selected),
            }
        )
    step_distributions = {}
    for condition in ("bare", "rcmf"):
        values = [int(row[condition]["step_count"]) for row in paired_rows]
        step_distributions[condition] = {
            "min": min(values),
            "median": median(values),
            "p95_nearest_rank": _nearest_rank(values, 0.95),
            "max": max(values),
        }
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    per_task_path = output_dir / "per_task_results.jsonl"
    with per_task_path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in paired_rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    summary = {
        "schema_version": "alfworld_paired_scientific_result_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "track_id": TRACK_R_ID,
        "track_role": "UPSTREAM_PROTOCOL_REFERENCE",
        "task_count": 134,
        "task_ids_sha256": TRACK_R_TASK_IDS_SHA256,
        "inputs": {
            "bare": {"path": str(Path(args.bare).resolve()), "sha256": sha256_file(args.bare)},
            "rcmf": {"path": str(Path(args.rcmf).resolve()), "sha256": sha256_file(args.rcmf)},
            "task_manifest": {
                "path": str(Path(args.task_manifest).resolve()),
                "sha256": sha256_file(args.task_manifest),
            },
        },
        "bare": {
            "successes": bare_successes,
            "accuracy": bare_successes / 134,
            "typed_failures": sum(row["bare"]["error"] is not None for row in paired_rows),
        },
        "rcmf": {
            "successes": rcmf_successes,
            "accuracy": rcmf_successes / 134,
            "typed_failures": sum(row["rcmf"]["error"] is not None for row in paired_rows),
        },
        "paired": {
            "both_correct": both_correct,
            "both_wrong": both_wrong,
            "gains": gains,
            "losses": losses,
            "accuracy_delta": (rcmf_successes - bare_successes) / 134,
            "bootstrap_95_ci": _paired_bootstrap(deltas),
            "exact_mcnemar": _mcnemar_exact(gains, losses),
        },
        "family_breakdown": family_rows,
        "step_distributions": step_distributions,
        "invalid_or_empty_action_counts": {
            condition: sum(row[condition]["empty_or_invalid_actions"] for row in paired_rows)
            for condition in ("bare", "rcmf")
        },
        "max_prompt_tokens": {
            condition: max(row[condition]["max_prompt_tokens"] for row in paired_rows)
            for condition in ("bare", "rcmf")
        },
        "per_task_results": {
            "path": str(per_task_path),
            "bytes": per_task_path.stat().st_size,
            "sha256": sha256_file(per_task_path),
        },
        "post_outcome_tuning_permitted": False,
    }
    summary["canonical_result_sha256"] = canonical_sha256(summary)
    summary_path = output_dir / "paired_summary.json"
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, summary_path)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
