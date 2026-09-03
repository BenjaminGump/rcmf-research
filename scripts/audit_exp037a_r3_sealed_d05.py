#!/usr/bin/env python3
"""Audit sealed EXP-037A-R3 D05 outputs without rerunning pipeline stages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproduction_audit_14e import (
    audit_selector_and_context,
)
from rcmf.pipeline.manifests import file_identity
from rcmf.utils.serialization import atomic_write_json, sha256_file


RUN_ID = "rcmf_exp037a_r3_sealed_d05_audit_14e_20260903_001"
REQUIRED_STAGES = (
    "S05_transition_representations",
    "D00_state_representations",
    "D01_selector_candidate_cv",
    "D02_selector_candidate_selection",
    "D03_final_selector_ensemble",
    "D04_selector_factorization",
    "D05_selected_memory_manifest",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--fresh-source-commit", required=True)
    return parser.parse_args()


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sealed_stage_completions(
    fresh_root: Path, fresh_source_commit: str
) -> list[dict[str, Any]]:
    output = []
    for stage_id in REQUIRED_STAGES:
        completion_path = fresh_root / "diagnostic_stages" / stage_id / "completion.json"
        if not completion_path.is_file():
            raise FileNotFoundError(f"Missing sealed completion: {stage_id}")
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        result_path = Path(str(completion["result_path"]))
        if completion.get("source_commit") != fresh_source_commit:
            raise ValueError(f"Source mismatch in sealed completion: {stage_id}")
        if not completion.get("passed"):
            raise ValueError(f"Sealed stage did not pass: {stage_id}")
        if not result_path.is_file():
            raise FileNotFoundError(f"Missing sealed result: {stage_id}")
        if sha256_file(result_path) != completion.get("result_sha256"):
            raise ValueError(f"Result hash mismatch in sealed stage: {stage_id}")
        output.append(
            {
                "stage_id": stage_id,
                "completion": file_identity(completion_path),
                "result": file_identity(result_path),
            }
        )
    return output


def main() -> None:
    args = parse_args()
    fresh_root = args.fresh_root.resolve()
    output_root = args.output_root.resolve()
    if fresh_root == output_root or fresh_root in output_root.parents:
        raise ValueError("Audit output must be outside the sealed fresh root")
    if "/runs/diagnostics/" not in str(output_root).replace("\\", "/"):
        raise ValueError("Audit output must be under runs/diagnostics")
    for prohibited in ("D06", "D07", "D08", "D09"):
        if any((fresh_root / "diagnostic_stages").glob(f"{prohibited}*")):
            raise ValueError(f"Prohibited downstream stage exists: {prohibited}")

    completions = _sealed_stage_completions(
        fresh_root, args.fresh_source_commit
    )
    effective_path = fresh_root / "effective_pipeline_config.json"
    config = json.loads(effective_path.read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=False)
    selector_context = audit_selector_and_context(
        config, fresh_root, output_root / "audit"
    )
    summary = {
        "format": "exp037a_r3_sealed_d05_audit_14e_v1",
        "run_id": RUN_ID,
        "audit_source_commit": _head(),
        "fresh_source_commit": args.fresh_source_commit,
        "fresh_root": str(fresh_root),
        "fresh_root_mutated": False,
        "sealed_stage_completions": completions,
        "effective_config": file_identity(effective_path),
        "selector_context": selector_context,
        "decision": "REPAIR_VALIDATED_READY_FOR_3D_PREFLIGHT",
        "historical_selector_loaded_deserialized_or_executed": False,
        "historical_outcome_used_to_construct_fresh_panel": False,
        "optimizer_or_backward_count_in_audit": 0,
        "full_d06_paired_generation_run": False,
        "d07_d08_d09_run": False,
        "one_demo_arm_run": False,
        "new_long_scientific_run_launched": False,
    }
    atomic_write_json(output_root / "summary.json", summary)
    atomic_write_json(output_root / "decision.json", {"decision": summary["decision"]})
    print(json.dumps({"decision": summary["decision"], "output_root": str(output_root)}, sort_keys=True))


if __name__ == "__main__":
    main()
