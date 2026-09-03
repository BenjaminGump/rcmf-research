#!/usr/bin/env python3
"""Run the bounded EXP-037A-R3 repair diagnostic through D05 only."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from typing import Any, Mapping

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproduction_audit_14e import (
    audit_representation_identities,
    audit_selector_and_context,
    audit_static_contract,
)
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    execute_stage,
    initialize_runtime_layout,
)
from rcmf.pipeline.manifests import file_identity
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    ensure_dir,
    sha256_file,
)
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved, prepare


RUN_ID = "rcmf_exp037a_r3_selector_reproduction_14e_20260903_001"
ALLOWED_PIPELINE_STAGES = (
    "S05_transition_representations",
    "D00_state_representations",
    "D01_selector_candidate_cv",
    "D02_selector_candidate_selection",
    "D03_final_selector_ensemble",
    "D04_selector_factorization",
    "D05_selected_memory_manifest",
)
PROHIBITED_STAGE_PREFIXES = ("D06", "D07", "D08", "D09", "O")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"),
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--stop-after",
        choices=ALLOWED_PIPELINE_STAGES,
        default="D05_selected_memory_manifest",
    )
    return parser.parse_args()


def diagnostic_config(config_path: Path, run_root: Path) -> dict[str, Any]:
    config = copy.deepcopy(load_resolved(config_path))
    pipeline = config["pipeline"]
    pipeline["run_uuid"] = RUN_ID
    pipeline["roots"]["run_root"] = str(run_root)
    pipeline["working_branch"] = (
        "research/v6-rcmf-exp037a-reproduction-contract-repair"
    )
    pipeline["approved_hard_cap_hours"] = 18
    pipeline["conditional_runtime_authorization"] = {
        "authorization_version": "exp037a_r3_selector_diagnostic_v1",
        "granted_by_user": True,
        "scope": "fresh_selector_reconstruction_through_D05_only",
        "maximum_wall_hours": 18,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": False,
        "old_200_hour_authorization_inherited": False,
    }
    return config


def runtime_preflight(source_commit: str) -> dict[str, Any]:
    anchors = {
        "S05_transition_representations": 487.4812981300056,
        "D00_state_representations": 622.1346927732229,
        "D01_selector_candidate_cv": 3606.7848129598424,
        "D02_selector_candidate_selection": 0.019501507747918367,
        "D03_final_selector_ensemble": 1519.0908979219384,
        "D04_selector_factorization": 1.2913607521913946,
        "D05_selected_memory_manifest": 46.604309672955424,
    }
    measured_seconds = sum(anchors.values())
    result = {
        "format": "exp037a_r3_selector_diagnostic_runtime_preflight_14e_v1",
        "source_commit": source_commit,
        "purpose": "fresh three-demo selector reconstruction and render-only context audit",
        "scope": list(ALLOWED_PIPELINE_STAGES),
        "prohibited": ["D06", "D07", "D08", "D09", "one_demo_arm"],
        "hardware": "NVIDIA H100 80GB HBM3",
        "global_seed": 25101,
        "cv_seed": 25071,
        "final_member_seeds": [25071, 25072, 25073],
        "measured_prior_stage_seconds": anchors,
        "measured_prior_total_hours": measured_seconds / 3600.0,
        "expected_wall_hours": 2.25,
        "conservative_wall_hours": 4.0,
        "expected_h100_active_hours": 1.9,
        "requires_explicit_over_18h_approval": False,
        "old_200_hour_authorization_inherited": False,
        "restart_plan": {
            "atomic_stage_results": True,
            "append_only_attempts": True,
            "skip_only_after_source_and_result_hash_validation": True,
            "resume_at_first_incomplete_allowed_stage": True,
        },
    }
    result["authorized"] = result["conservative_wall_hours"] <= 18.0
    return result


def _run_stage(
    *,
    stage_id: str,
    config: Mapping[str, Any],
    run_root: Path,
    source_commit: str,
) -> dict[str, Any]:
    if stage_id not in ALLOWED_PIPELINE_STAGES or stage_id.startswith(
        PROHIBITED_STAGE_PREFIXES
    ):
        raise PermissionError(f"Stage is outside the R3 diagnostic scope: {stage_id}")
    stage_dir = ensure_dir(run_root / "diagnostic_stages" / stage_id)
    completion_path = stage_dir / "completion.json"
    if completion_path.exists():
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        result_path = Path(str(completion["result_path"]))
        if (
            completion.get("source_commit") != source_commit
            or not completion.get("passed")
            or not result_path.is_file()
            or sha256_file(result_path) != completion.get("result_sha256")
        ):
            raise ValueError(f"Existing diagnostic completion is invalid: {stage_id}")
        return json.loads(result_path.read_text(encoding="utf-8"))
    attempt_id = f"{stage_id}-r3-{time.time_ns()}"
    started = time.perf_counter()
    started_utc = datetime.now(timezone.utc).isoformat()
    attempt = {
        "format": "exp037a_r3_selector_diagnostic_attempt_14e_v1",
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "source_commit": source_commit,
        "started_utc": started_utc,
        "status": "running",
    }
    append_jsonl(run_root / "attempts.jsonl", attempt)
    os.environ["RCMF_PIPELINE_ATTEMPT_ID"] = attempt_id
    try:
        result = execute_stage(
            stage_id=stage_id,
            config=config,
            run_root=run_root,
            stage_dir=stage_dir,
            source_commit=source_commit,
            attempt_id=attempt_id,
        )
        result_path = stage_dir / "stage_result.json"
        atomic_write_json(result_path, result)
        completion = {
            "format": "exp037a_r3_selector_diagnostic_completion_14e_v1",
            "attempt_id": attempt_id,
            "stage_id": stage_id,
            "source_commit": source_commit,
            "started_utc": started_utc,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.perf_counter() - started,
            "result_path": str(result_path),
            "result_sha256": sha256_file(result_path),
            "passed": bool(result.get("passed", True)),
        }
        atomic_write_json(completion_path, completion)
        append_jsonl(run_root / "attempts.jsonl", {**attempt, **completion, "status": "completed"})
        return dict(result)
    except BaseException as error:
        failure = {
            "format": "exp037a_r3_selector_diagnostic_failure_14e_v1",
            "attempt_id": attempt_id,
            "stage_id": stage_id,
            "source_commit": source_commit,
            "elapsed_seconds": time.perf_counter() - started,
            "exception_type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "failed_utc": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(stage_dir / "failure.json", failure)
        append_jsonl(run_root / "attempts.jsonl", {**attempt, **failure, "status": "failed"})
        raise


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    args = parse_args()
    run_root = args.run_root.resolve()
    normalized = str(run_root).replace("\\", "/")
    if "/runs/diagnostics/" not in normalized:
        raise ValueError("R3 output root must be under runs/diagnostics")
    if _git_head() != args.source_commit:
        raise ValueError("Checked-out source differs from diagnostic source commit")
    config = diagnostic_config(args.config, run_root)
    ensure_dir(run_root)
    effective_path = run_root / "effective_pipeline_config.json"
    if effective_path.exists():
        existing = json.loads(effective_path.read_text(encoding="utf-8"))
        if existing != config:
            raise ValueError("Existing diagnostic config differs")
    else:
        atomic_write_json(effective_path, config)

    if not (run_root / "preflight/preflight_summary.json").exists():
        prepare(config, run_root / "preflight", args.source_commit, None)
    initialize_runtime_layout(config, run_root)
    static = audit_static_contract(config, run_root, run_root / "audit")
    runtime = runtime_preflight(args.source_commit)
    atomic_write_json(run_root / "runtime_authorization.json", runtime)
    if not runtime["authorized"]:
        raise PermissionError("Selector diagnostic exceeds the 18-hour gate")

    stage_results: dict[str, Any] = {}
    for stage_id in ALLOWED_PIPELINE_STAGES:
        stage_results[stage_id] = _run_stage(
            stage_id=stage_id,
            config=config,
            run_root=run_root,
            source_commit=args.source_commit,
        )
        if stage_id == "D00_state_representations":
            audit_representation_identities(config, run_root, run_root / "audit")
        if stage_id == "D02_selector_candidate_selection":
            selected = str(stage_results[stage_id]["selected_candidate"]["name"])
            expected = str(
                config["pipeline"]["reproduction_contract"][
                    "expected_selector_winner"
                ]
            )
            if selected != expected:
                result = {
                    "decision": "INCONCLUSIVE_SELECTOR_REPRODUCTION",
                    "fresh_winner": selected,
                    "expected_winner": expected,
                    "downstream_stages_run": False,
                }
                atomic_write_json(run_root / "decision.json", result)
                print(json.dumps(result, sort_keys=True))
                return
        if stage_id == args.stop_after:
            break

    if args.stop_after != "D05_selected_memory_manifest":
        result = {
            "decision": "BOUNDED_DIAGNOSTIC_STOPPED_AT_REQUESTED_STAGE",
            "stage": args.stop_after,
        }
        atomic_write_json(run_root / "decision.json", result)
        print(json.dumps(result, sort_keys=True))
        return

    selector_context = audit_selector_and_context(
        config, run_root, run_root / "audit"
    )
    result = {
        "format": "exp037a_r3_selector_diagnostic_summary_14e_v1",
        "run_id": RUN_ID,
        "source_commit": args.source_commit,
        "decision": "REPAIR_VALIDATED_READY_FOR_3D_PREFLIGHT",
        "static_contract": static,
        "selector_context": selector_context,
        "runtime_preflight": runtime,
        "effective_config": file_identity(effective_path),
        "optimizer_scope": "fresh_selector_only",
        "historical_selector_loaded_deserialized_or_executed": False,
        "historical_outcome_used_to_construct_fresh_panel": False,
        "full_d06_paired_generation_run": False,
        "d07_d08_d09_run": False,
        "one_demo_arm_run": False,
        "new_long_scientific_run_launched": False,
    }
    atomic_write_json(run_root / "summary.json", result)
    atomic_write_json(run_root / "decision.json", {"decision": result["decision"]})
    print(json.dumps({"decision": result["decision"], "run_root": str(run_root)}, sort_keys=True))


if __name__ == "__main__":
    main()
