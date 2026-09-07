#!/usr/bin/env python3
"""Run the bounded production-path C00/C01/O08 repair diagnostic."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

import _bootstrap  # noqa: F401
import yaml

from rcmf.benchmarks.appworld.continuation_14l import (
    PARENT_RUN_UUID,
    build_continuation_arm_config,
    collect_parent_artifact_manifest,
)
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.scheduler import EventDrivenScheduler
from rcmf.pipeline.stage_graph import build_exp037a_continuation_stage_graph
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, ensure_dir, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-config", type=Path, required=True)
    parser.add_argument("--parent-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument(
        "--stop-after-c01",
        action="store_true",
        help="Exercise only C00/C01 through the production scheduler path.",
    )
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_diagnostic_config(
    template_path: Path,
    output_root: Path,
    parent_root: Path,
    source_commit: str,
    parent_manifest_sha: str,
) -> tuple[Path, dict[str, Any]]:
    config = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    run_uuid = "exp037a_r14b_o08_production_path_diagnostic"
    config_path = output_root / "preflight/diagnostic_pipeline.yaml"
    arm_pointer = output_root / "preflight/diagnostic_arm_1d.yaml"
    config["pipeline"]["run_uuid"] = run_uuid
    config["pipeline"]["config_path"] = str(config_path)
    config["pipeline"]["roots"]["run_root"] = str(output_root)
    config["pipeline"]["proposed_hard_cap_hours"] = 18
    config["pipeline"]["authorization_scope"] = (
        "r14b_bounded_o08_engineering_diagnostic"
    )
    config["pipeline"]["continuation"]["parent_root"] = str(parent_root)
    config["pipeline"]["continuation"]["parent_artifact_manifest_path"] = str(
        output_root / "preflight/parent_artifact_manifest.json"
    )
    config["pipeline"]["continuation"][
        "parent_artifact_manifest_sha256"
    ] = parent_manifest_sha
    config["arms"] = {"1d": {"include": arm_pointer.name}}
    ensure_dir(config_path.parent)
    arm_pointer.write_text(
        Path("configs/pipeline/rcmf_appworld_arm_1d_continuation_14l.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    resolved = build_continuation_arm_config(
        parent_root=parent_root,
        continuation_root=output_root,
        continuation_run_uuid=run_uuid,
        working_branch="diagnostic",
        proposed_hard_cap_hours=18,
    )
    resolved_path = output_root / "resolved_configs/arm_1d.yaml"
    ensure_dir(resolved_path.parent)
    resolved_path.write_text(yaml.safe_dump(resolved, sort_keys=False), encoding="utf-8")
    return config_path, config


def main() -> None:
    args = parse_args()
    output_root = args.output_root.resolve(strict=False)
    parent_root = args.parent_root.resolve(strict=True)
    if output_root.exists():
        raise FileExistsError(f"Diagnostic root must be fresh: {output_root}")
    ensure_dir(output_root / "preflight")
    before = collect_parent_artifact_manifest(
        parent_root=parent_root, generated_by_source=args.source_commit
    )
    parent_manifest_path = output_root / "preflight/parent_artifact_manifest.json"
    atomic_write_json(parent_manifest_path, before)
    parent_manifest_sha = sha256_file(parent_manifest_path)
    config_path, config = _write_diagnostic_config(
        args.template_config,
        output_root,
        parent_root,
        args.source_commit,
        parent_manifest_sha,
    )
    init_root = ensure_dir(output_root / "preflight/initialization_snapshots")
    copied_initializations = {}
    for name in ("writer_initial.pt", "reader_initial.pt"):
        source = parent_root / "preflight/initialization_snapshots" / name
        target = init_root / name
        shutil.copy2(source, target)
        if sha256_file(source) != sha256_file(target):
            raise RuntimeError(f"Initialization copy hash differs: {name}")
        copied_initializations[name] = {
            "source": file_identity(source),
            "copy": file_identity(target),
            "hardlinked": source.stat().st_ino == target.stat().st_ino,
        }
        if copied_initializations[name]["hardlinked"]:
            raise RuntimeError("Diagnostic initialization must be a byte copy")
    stage_count = 2 if args.stop_after_c01 else 3
    stages = tuple(build_exp037a_continuation_stage_graph()[:stage_count])
    stage_scope_sha = content_sha256([stage.stage_id for stage in stages])
    config_sha = sha256_file(config_path)
    contract = PipelineContract(
        schema_version="exp037a_r14b_o08_diagnostic_contract_v1",
        run_uuid=str(config["pipeline"]["run_uuid"]),
        source_commit=args.source_commit,
        global_seed=25101,
        hard_cap_hours=18,
        stages=stages,
        arms={
            "1d": ArmContract(
                arm_id="1d",
                task_conditioned_prompt_profile="full_demo_first_only",
                artifact_prefix="arms/1d",
                run_id=f"{config['pipeline']['run_uuid']}-1d",
            )
        },
        shared_initialization={
            name: str(init_root / name)
            for name in ("writer_initial.pt", "reader_initial.pt")
        },
        metadata={
            "pipeline_config_path": str(config_path),
            "pipeline_config_sha256": config_sha,
            "canonical_run_root": str(output_root),
            "strict_stage_identity": True,
            "require_run_bound_authorization": True,
            "authorization_mode": "o07_o08_continuation",
            "authorization_scope": "r14b_bounded_o08_engineering_diagnostic",
            "authorization_version": "exp037a_r14b_diagnostic_authorization_v1",
            "parent_artifact_manifest_sha256": parent_manifest_sha,
            "parent_run_uuid": PARENT_RUN_UUID,
            "stage_scope_sha256": stage_scope_sha,
            "maximum_recoverable_attempts_per_stage": 1,
            "recoverable_retry_delay_seconds": 0,
        },
    )
    contract_path = output_root / "preflight/stage_dag.json"
    atomic_write_json(contract_path, contract.as_dict())
    contract_sha = sha256_file(contract_path)
    runtime_authorization = {
        "format": "exp037a_r14b_diagnostic_authorization_v1",
        "authorization_version": "exp037a_r14b_diagnostic_authorization_v1",
        "authorization_status": "AUTHORIZED",
        "authorized": True,
        "granted_by_user": True,
        "continuation_authorized": True,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "authorization_source": "explicit_run_bound_user_authorization",
        "run_started_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": args.source_commit,
        "run_uuid": contract.run_uuid,
        "run_root": str(output_root),
        "contract_sha256": contract_sha,
        "pipeline_config_sha256": config_sha,
        "hard_cap_hours": 18,
        "recommended_hard_cap_hours": 2,
        "scope": "r14b_bounded_o08_engineering_diagnostic",
        "parent_artifact_manifest_sha256": parent_manifest_sha,
        "parent_run_uuid": PARENT_RUN_UUID,
        "stage_scope_sha256": stage_scope_sha,
    }
    atomic_write_json(output_root / "runtime_authorization.json", runtime_authorization)
    predeclared = {
        "format": "exp037a_r14b_o08_diagnostic_predeclaration_v1",
        "source_commit": args.source_commit,
        "parent_manifest_sha256": parent_manifest_sha,
        "global_seed": 25101,
        "hardware": "NVIDIA H100 80GB HBM3",
        "expected_wall_hours": 0.5,
        "conservative_wall_hours": 2.0,
        "hard_cap_hours": 18,
        "scientific_result": False,
        "stages": [stage.stage_id for stage in stages],
    }
    atomic_write_json(output_root / "preflight/diagnostic_predeclaration.json", predeclared)
    scheduler = EventDrivenScheduler(
        contract,
        output_root,
        python_executable=args.python,
        config_path=config_path,
        heartbeat_interval_seconds=240,
        contract_sha256=contract_sha,
    )
    result = scheduler.run()
    after = collect_parent_artifact_manifest(
        parent_root=parent_root, generated_by_source=args.source_commit
    )
    parent_unchanged = (
        before["artifact_closure_sha256"] == after["artifact_closure_sha256"]
        and before["artifact_count"] == after["artifact_count"]
    )
    stage_rows = {}
    for stage in stages:
        stage_dir = output_root / "stages" / stage.stage_id
        stage_rows[stage.stage_id] = validate_stage_completion(
            stage_dir,
            args.source_commit,
            expected_run_uuid=contract.run_uuid,
            expected_pipeline_config_sha256=config_sha,
            expected_contract_sha256=contract_sha,
            expected_run_root=output_root,
        )
    count_validation_path = (
        output_root / "arms/1d/data/scoreable_count_validation.json"
    )
    count_validation = (
        {"passed": True, "reason": "o08_not_in_diagnostic_scope"}
        if args.stop_after_c01
        else _json(count_validation_path)
        if count_validation_path.is_file()
        else {"passed": False, "reason": "not_produced"}
    )
    o08_manifest = output_root / "stages/O08_zero_cache_and_training_units/output_manifest.json"
    scientific_checkpoints = sorted(
        str(path)
        for path in (output_root / "arms/1d/joint_training/checkpoints").glob("*.pt")
    )
    summary = {
        "format": "exp037a_r14b_o08_diagnostic_result_v1",
        "passed": result.status == "complete"
        and all(bool(row["passed"]) for row in stage_rows.values())
        and bool(count_validation["passed"])
        and parent_unchanged
        and not scientific_checkpoints,
        "scheduler_status": result.status,
        "completed_stages": result.completed,
        "failed_stage": result.failed_stage,
        "parent_before_closure_sha256": before["artifact_closure_sha256"],
        "parent_after_closure_sha256": after["artifact_closure_sha256"],
        "parent_root_unchanged": parent_unchanged,
        "parent_o08_partial_outputs_used": False,
        "copied_initializations": copied_initializations,
        "count_validation": count_validation,
        "o08_output_manifest": (
            file_identity(o08_manifest) if o08_manifest.is_file() else None
        ),
        "stage_validations": stage_rows,
        "scientific_checkpoints": scientific_checkpoints,
        "optimizer_step_count": 0,
        "backward_count": 0,
        "stop_after_c01": bool(args.stop_after_c01),
        "full_run_compatibility_inputs_created": (
            output_root / "shared/compat_exp025b/clean_cache_rebuild"
        ).exists(),
        "preflight_shared_transitions_expected": (
            output_root / "preflight/shared/transitions.jsonl"
        ).exists(),
        "scientific_result": False,
    }
    atomic_write_json(output_root / "o08_diagnostic_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
