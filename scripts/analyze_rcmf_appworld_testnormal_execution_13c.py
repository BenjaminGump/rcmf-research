"""Bind the frozen EXP-036A paired analysis to the EXP-036C run identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json
from scripts import analyze_rcmf_appworld_testnormal_final_13a as base


RUN_UUID = "rcmf_appworld_testnormal_final_13c_20260901_002"
FORMAT = "rcmf_appworld_testnormal_paired_analysis_13c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=100_000)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def analyze(artifact_dir: Path, *, replicates: int) -> dict[str, Any]:
    formal = read_json(artifact_dir / "results/formal_summary.json")
    if formal.get("run_uuid") != RUN_UUID:
        raise RuntimeError("EXP-036C formal summary run UUID differs")
    if not bool(formal.get("evaluation_complete")) or int(
        formal.get("trajectory_count", 0)
    ) != 840:
        raise RuntimeError("EXP-036C formal evaluation is incomplete")

    result = base.analyze(artifact_dir, replicates=replicates)
    result["format"] = FORMAT
    result["run_uuid"] = RUN_UUID
    result["formal_summary_sha256"] = str(formal["summary_sha256"])
    result.pop("analysis_sha256", None)
    result["analysis_sha256"] = canonical_sha256(result)
    atomic_write_json(artifact_dir / "analysis/paired_analysis.json", result)
    return result


def main() -> None:
    args = parse_args()
    result = analyze(args.artifact_dir, replicates=args.bootstrap_replicates)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
