#!/usr/bin/env python3
"""Build the EXP-037A-R4 approval package without executing scientific stages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproducible_config_14b import build_arm_runtime_config
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import validate_resolved_arm_diff
from rcmf.utils.serialization import atomic_write_json, ensure_dir, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved


OLD_RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001"
OLD_RUN_ROOT = (
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    + OLD_RUN_UUID
)
EXPECTED_RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001"
EXPECTED_RUN_ROOT = (
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    + EXPECTED_RUN_UUID
)
ARM_DIFF_ALLOWLIST = {
    "benchmark.prompt_profile",
    "experiment.name",
    "stage_c_11b.artifact_dir",
    "stage_c_11b.prompt_profile",
    "stage_c_11b.run_uuid",
    "stage_c_7c.artifact_dir",
    "stage_c_7c.generation.prompt_profile",
    "stage_c_7c.multiview_cache.output_root",
    "stage_c_7c.run_uuid",
    "stage_c_7hr.appworld.prompt_profile",
    "stage_c_7hr.artifact_dir",
    "stage_c_7hr.parent_exp025c",
    "stage_c_7hr.run_uuid",
    "stage_c_9a.appworld.prompt_profile",
    "stage_c_9a.artifact_dir",
    "stage_c_9a.parent_exp025c",
    "stage_c_9a.parent_exp028a",
    "stage_c_9a.prompt_dependent_inputs.outcomes",
    "stage_c_9a.prompt_dependent_inputs.state_cache",
    "stage_c_9a.prompt_dependent_inputs.teacher_cache",
    "stage_c_9a.run_uuid",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14f.yaml"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def build_preflight(
    config_path: Path, output_root: Path, source_commit: str
) -> dict[str, Any]:
    config = load_resolved(config_path)
    method = config["pipeline"]
    run_uuid = str(method["run_uuid"])
    configured_run_root = str(method["roots"]["run_root"])
    run_root = Path(configured_run_root)
    auth = method["conditional_runtime_authorization"]
    reproduction = method["reproduction_contract"]
    selector_split = reproduction["selector_parent_split"]
    panel = reproduction["causal_panel"]
    post_d06 = reproduction["post_d06_reproduction_gate"]

    arm_3d = build_arm_runtime_config(config, run_root, "3d")
    arm_1d = build_arm_runtime_config(config, run_root, "1d")
    arm_diff = validate_resolved_arm_diff(
        arm_3d,
        arm_1d,
        allowlist=ARM_DIFF_ALLOWLIST,
        allowed_prefixes=(),
    )
    run_root_fresh = not run_root.exists()
    checks = {
        "new_run_uuid": run_uuid == EXPECTED_RUN_UUID and run_uuid != OLD_RUN_UUID,
        "new_run_root": configured_run_root == EXPECTED_RUN_ROOT
        and configured_run_root != OLD_RUN_ROOT,
        "new_run_root_absent": run_root_fresh,
        "authorization_status_not_authorized": auth["authorization_status"]
        == "NOT_AUTHORIZED",
        "granted_by_user_false": auth["granted_by_user"] is False,
        "old_200h_not_inherited": auth["previous_200_hour_authorization_inherited"]
        is False,
        "full_pipeline_false": auth["full_pipeline_authorized"] is False,
        "d06_or_later_false": auth["d06_or_later_authorized"] is False,
        "one_demo_false": auth["one_demo_authorized"] is False,
        "automatic_three_demo_false": auth[
            "automatic_three_demo_launch_after_preflight"
        ]
        is False,
        "proposed_cap_80": float(method["proposed_hard_cap_hours"]) == 80.0,
        "selector_parent_split": selector_split["seed"] == 18018
        and selector_split["train_parent_count"] == 29
        and selector_split["heldout_parent_count"] == 8,
        "panel_contract": panel
        == {
            "initial_state_count": 256,
            "maximum_state_count": 499,
            "minimum_per_label": 40,
        },
        "selector_seeds": method["selector_cv_seed"] == 25071
        and method["final_selector_member_seeds"] == [25071, 25072, 25073],
        "post_d06_outcome_gate_only": post_d06
        == {
            "expected_train_completed": 366,
            "expected_heldout_completed": 98,
            "construction_input": False,
        },
        "arm_prompt_profiles": config["arms"]["3d"][
            "task_conditioned_prompt_profile"
        ]
        == "full_demo"
        and config["arms"]["1d"]["task_conditioned_prompt_profile"]
        == "full_demo_first_only",
        "arm_diff_prompt_only": bool(arm_diff["passed"]),
    }
    if not all(checks.values()):
        raise ValueError(f"EXP-037A-R4 static preflight failed: {checks}")

    config_sha = sha256_file(config_path)
    arms = {
        arm_id: ArmContract(
            arm_id=arm_id,
            task_conditioned_prompt_profile=str(row["task_conditioned_prompt_profile"]),
            artifact_prefix=str(row["artifact_prefix"]),
            run_id=str(row["run_id"]),
        )
        for arm_id, row in config["arms"].items()
    }
    contract = PipelineContract(
        schema_version=str(method["schema_version"]),
        run_uuid=run_uuid,
        source_commit=source_commit,
        global_seed=int(method["global_seed"]),
        hard_cap_hours=float(method["proposed_hard_cap_hours"]),
        stages=build_exp037a_stage_graph(),
        arms=arms,
        shared_initialization={"status": "fresh_stage_output_required"},
        metadata={
            "pipeline_config_path": str(method["config_path"]),
            "pipeline_config_sha256": config_sha,
            "require_run_bound_authorization": True,
            "maximum_recoverable_attempts_per_stage": int(
                method["maximum_recoverable_attempts_per_stage"]
            ),
            "recoverable_retry_delay_seconds": float(
                method["recoverable_retry_delay_seconds"]
            ),
        },
    )

    ensure_dir(output_root)
    atomic_write_json(output_root / "stage_dag.json", contract.as_dict())
    run_identity = {
        "format": "exp037a_r4_run_identity_v1",
        "previous_run_uuid": OLD_RUN_UUID,
        "new_run_uuid": run_uuid,
        "previous_run_root": OLD_RUN_ROOT,
        "new_run_root": configured_run_root,
        "new_root_existed_before_preflight": not run_root_fresh,
        "new_root_prior_entries": [],
        "source_commit": source_commit,
        "config_path": str(config_path),
        "config_sha256": config_sha,
    }
    scientific_invariants = {
        "format": "exp037a_r4_scientific_invariants_v1",
        "selector_parent_split": dict(selector_split),
        "causal_panel": dict(panel),
        "selector_cv_seed": method["selector_cv_seed"],
        "selector_final_member_seeds": method["final_selector_member_seeds"],
        "arm_prompt_profiles": {
            arm: row["task_conditioned_prompt_profile"]
            for arm, row in config["arms"].items()
        },
        "post_d06_reproduction_gate": dict(post_d06),
        "only_intended_arm_difference": "task_conditioned_prompt_profile",
        "resolved_arm_diff": arm_diff,
        "scientific_changes_from_r3": 0,
    }
    authorization_state = {
        "format": "exp037a_r4_authorization_state_v1",
        "status": "READY_FOR_USER_APPROVAL",
        "authorization_status": "NOT_AUTHORIZED",
        "authorized_to_launch": False,
        "explicit_user_approval_required": True,
        "granted_by_user": False,
        "previous_200_hour_authorization_inherited": False,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": False,
        "automatic_three_demo_launch_after_preflight": False,
        "proposed_hard_cap_hours": 80.0,
    }
    runtime = {
        "format": "exp037a_r4_runtime_storage_proposal_v1",
        "expected_wall_hours": 26.5,
        "conservative_wall_hours": 56.0,
        "expected_h100_active_hours": 21.8,
        "expected_storage_gib": 46.0,
        "conservative_storage_gib": 90.0,
        "proposed_hard_cap_hours": 80.0,
        "recommended_hard_cap_hours": 80.0,
        "could_exceed_18_hours": True,
        "explicit_user_approval_required": True,
        "restart_plan": {
            "atomic_stage_outputs": True,
            "append_only_attempts": True,
            "content_hash_validation_before_skip": True,
            "resume_from_first_incomplete_stage": True,
            "persistent_event_driven_parent": True,
        },
    }
    launcher_checks = {
        "format": "exp037a_r4_launcher_authorization_checks_v1",
        "requires_explicit_authorization_file": True,
        "bound_fields": [
            "run_uuid",
            "run_root",
            "source_commit",
            "contract_sha256",
            "pipeline_config_sha256",
            "hard_cap_hours",
        ],
        "stale_authorization_rejected": True,
        "old_config_granted_by_user_is_insufficient": True,
        "hard_coded_200_hour_cap": False,
        "scientific_stage_execution_count": 0,
    }
    payloads = {
        "run_identity.json": run_identity,
        "scientific_invariants.json": scientific_invariants,
        "authorization_state.json": authorization_state,
        "runtime_storage_proposal.json": runtime,
        "launcher_authorization_checks.json": launcher_checks,
    }
    for name, payload in payloads.items():
        atomic_write_json(output_root / name, payload)
    summary = {
        "format": "exp037a_r4_3d_preflight_hardening_v1",
        "status": "READY_FOR_USER_APPROVAL",
        "decision": "READY_FOR_EXPLICIT_USER_APPROVAL",
        "run_uuid": run_uuid,
        "source_commit": source_commit,
        "approval_checks": checks,
        "authorized_to_launch": False,
        "explicit_user_approval_required": True,
        "previous_200_hour_authorization_inherited": False,
        "recommended_hard_cap_hours": 80.0,
        "proposed_hard_cap_hours": 80.0,
        "h100_scientific_active_hours": 0,
        "scientific_stage_execution_count": 0,
        "output_hashes": {
            name: sha256_file(output_root / name)
            for name in ["stage_dag.json", *payloads]
        },
    }
    atomic_write_json(output_root / "preflight_summary.json", summary)
    return summary


def main() -> None:
    args = parse_args()
    summary = build_preflight(args.config, args.output_root, args.source_commit)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
