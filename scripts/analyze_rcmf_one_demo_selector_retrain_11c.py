"""Analyze frozen EXP-034B dev rows with the verified EXP-034A statistics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import analyze_rcmf_one_demo_retrain_11b as base

from rcmf.utils.serialization import atomic_write_json


RUN_UUID = "rcmf_one_demo_selector_retrain_11c_20260830_001"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def analyze(
    artifact_dir: Path,
    exp033a_root: Path,
    exp034a_root: Path,
    *,
    replicates: int,
) -> dict[str, Any]:
    base.RUN_UUID = RUN_UUID
    result = base.analyze(artifact_dir, exp033a_root, replicates=replicates)
    task_ids = [str(value) for value in result["success"]["N1"]["task_ids"]]
    # The success list omits failures; use the immutable final manifest order.
    task_ids = [
        str(value)
        for value in _json(artifact_dir / "dev/final_summary.json")["task_ids"]
    ]
    current = {
        condition: {
            task: bool(_json(base._task_path(artifact_dir, condition, task))["success"])
            for task in task_ids
        }
        for condition in ("N1", "N2")
    }
    old = {
        condition: {
            task: bool(_json(base._task_path(exp034a_root, condition, task))["success"])
            for task in task_ids
        }
        for condition in ("N1", "N2")
    }
    result["format"] = "rcmf_one_demo_selector_retrain_paired_analysis_11c_v1"
    result["comparisons_to_exp034a"] = {
        "new_N1_vs_exp034a_N1": base._comparison(
            task_ids, current["N1"], old["N1"], replicates=replicates
        ),
        "new_N2_vs_exp034a_N2": base._comparison(
            task_ids, current["N2"], old["N2"], replicates=replicates
        ),
    }
    d0_count = int(result["success"]["D0"]["count"])
    n1_count = int(result["success"]["N1"]["count"])
    n2_count = int(result["success"]["N2"]["count"])
    if n1_count <= d0_count and n1_count <= n2_count:
        decision = "STOP"
        rationale = (
            "The correct field does not exceed bare and does not exceed the "
            "matched key-payload shuffle; absolute and specificity directions "
            "both contradict the hypothesis."
        )
    elif n1_count > d0_count and n1_count > n2_count:
        decision = "PROCEED"
        rationale = (
            "The correct field has positive absolute and matched-shuffle "
            "directions; uncertainty and concentration remain reported."
        )
    else:
        decision = "INCONCLUSIVE"
        rationale = (
            "Absolute and matched-shuffle directions disagree or tie, so the "
            "controlled reconstruction is not directionally decisive."
        )
    result["scientific_interpretation"] = {
        "decision": decision,
        "rule": "joint absolute and matched-shuffle direction; no task-count cutoff",
        "rationale": rationale,
    }
    atomic_write_json(artifact_dir / "analysis/paired_analysis.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--exp033a-artifact-dir", type=Path, required=True)
    parser.add_argument("--exp034a-artifact-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=100_000)
    args = parser.parse_args()
    result = analyze(
        args.artifact_dir,
        args.exp033a_artifact_dir,
        args.exp034a_artifact_dir,
        replicates=args.bootstrap_replicates,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
