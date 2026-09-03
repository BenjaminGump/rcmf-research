#!/usr/bin/env python3
"""Build the final EXP-037A launch package without authorizing or running it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproducible_config_14b import (
    build_arm_runtime_config,
)
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.manifests import file_identity
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import validate_resolved_arm_diff
from rcmf.utils.serialization import (
    atomic_write_json,
    ensure_dir,
    sha256_file,
)
from scripts.prepare_rcmf_reproducible_pipeline_14b import (
    _environment_manifest,
    _initialization_manifest,
    _source_manifest,
    load_resolved,
    rebuild_shared_cpu,
)
from scripts.prepare_rcmf_reproducible_pipeline_14f import ARM_DIFF_ALLOWLIST


RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001"
RUN_ROOT = (
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    + RUN_UUID
)
OLD_RUNS = {
    "14b": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001",
    ),
    "14f": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001",
    ),
}
AUTHORIZATION_SCOPE = (
    "complete_fresh_3d_then_conditional_fresh_1d_and_final_reporting"
)
AUTHORIZATION_VERSION = "exp037a_run_bound_authorization_14g_v1"
PROPOSED_HARD_CAP_HOURS = 120.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14g.yaml"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--smoke-results", type=Path, required=True)
    parser.add_argument("--tests-json", type=Path, required=True)
    parser.add_argument("--runtime-state-json", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def runtime_tables() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stages = {
        "format": "exp037a_r5_runtime_by_stage_14g_v1",
        "basis": [
            "R3 measured S05+D00-D05 selector reconstruction: 2.437285 h",
            "failed 14b D06 measured wall: 1.868714 h",
            "original full-scope EXP-037A preflight: 47.5 h expected / 92 h conservative",
            "EXP-031A/034A/034B/036C complete-run throughput anchors",
        ],
        "groups": [
            {
                "group": "shared_preflight_and_transition_representations",
                "stages": "S00-S09",
                "expected_wall_hours": 2.5,
                "conservative_wall_hours": 5.0,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_selector",
                "stages": "D00-D05",
                "expected_wall_hours": 2.5,
                "conservative_wall_hours": 4.0,
                "measured_r3_wall_hours": 2.437285044068057,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_paired_and_early_gate",
                "stages": "D06-D06B",
                "expected_wall_hours": 2.0,
                "conservative_wall_hours": 4.0,
                "measured_d06_wall_hours": 1.8687141789252766,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_teacher_prepare_and_smoke",
                "stages": "D07-D08B",
                "expected_wall_hours": 1.5,
                "conservative_wall_hours": 3.0,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_training_validation_and_fields",
                "stages": "D09-D17",
                "expected_wall_hours": 6.0,
                "conservative_wall_hours": 15.0,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_dev_and_final_gate",
                "stages": "D18-D22",
                "expected_wall_hours": 12.0,
                "conservative_wall_hours": 25.0,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_selector",
                "stages": "O00-O05",
                "expected_wall_hours": 2.5,
                "conservative_wall_hours": 4.0,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_paired_teacher_prepare",
                "stages": "O06-O08",
                "expected_wall_hours": 3.5,
                "conservative_wall_hours": 6.0,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_training_validation_and_fields",
                "stages": "O09-O17",
                "expected_wall_hours": 6.0,
                "conservative_wall_hours": 12.0,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_dev",
                "stages": "O18-O19",
                "expected_wall_hours": 9.0,
                "conservative_wall_hours": 14.0,
                "gpu": "yes",
            },
            {
                "group": "final_analysis_and_reporting",
                "stages": "F00-F03",
                "expected_wall_hours": 0.25,
                "conservative_wall_hours": 0.5,
                "gpu": "no",
            },
        ],
    }
    branches = {
        "format": "exp037a_r5_runtime_by_branch_14g_v1",
        "branch_3d_reproduction_fails": {
            "scope": "shared + complete 3D through D22 + final failure reporting",
            "expected_wall_hours": 26.75,
            "conservative_wall_hours": 56.5,
            "expected_h100_active_hours": 21.85,
        },
        "branch_3d_passes_and_1d_executes": {
            "scope": "shared + complete 3D + D22 + complete conditional 1D + final reporting",
            "expected_wall_hours": 47.75,
            "conservative_wall_hours": 92.5,
            "expected_h100_active_hours": 39.05,
        },
        "storage": {
            "expected_gib": 46.0,
            "conservative_gib": 90.0,
        },
        "cost": {
            "available": False,
            "reason": "no configured Lambda H100 hourly rate is present",
        },
    }
    cap = {
        "format": "exp037a_r5_hard_cap_proposal_14g_v1",
        "longest_permitted_branch": "branch_3d_passes_and_1d_executes",
        "formula": "ceil_practical(max(2.0*47.75,1.25*92.5))",
        "twice_expected_hours": 95.5,
        "one_point_two_five_conservative_hours": 115.625,
        "unrounded_required_hours": 115.625,
        "proposed_hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
        "authorization_status": "NOT_AUTHORIZED",
        "anomaly_guard_not_runtime_target": True,
    }
    return stages, branches, cap


def static_invariants(
    config: Mapping[str, Any], run_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    method = config["pipeline"]
    reproduction = method["reproduction_contract"]
    arm_3d = build_arm_runtime_config(config, run_root, "3d")
    arm_1d = build_arm_runtime_config(config, run_root, "1d")
    arm_diff = validate_resolved_arm_diff(
        arm_3d,
        arm_1d,
        allowlist=ARM_DIFF_ALLOWLIST,
        allowed_prefixes=(),
    )
    invariants = {
        "format": "exp037a_r5_scientific_invariants_14g_v1",
        "selector_parent_split": dict(reproduction["selector_parent_split"]),
        "selector_legal_pairs": int(method["expected"]["selector_legal_pairs"]),
        "selector_cv_seed": int(method["selector_cv_seed"]),
        "selector_final_member_seeds": list(
            method["final_selector_member_seeds"]
        ),
        "causal_panel": dict(reproduction["causal_panel"]),
        "post_d06_reproduction_gate": dict(
            reproduction["post_d06_reproduction_gate"]
        ),
        "writer_reader": {
            "epochs": list(method["training"]["epochs"]),
            "writer_learning_rate": method["training"][
                "writer_learning_rate"
            ],
            "reader_learning_rate": method["training"][
                "reader_learning_rate"
            ],
            "losses": {
                key: value
                for key, value in method["training"].items()
                if key.endswith("_weight")
            },
            "checkpoint_selection": "heldout_train_tasks_only",
        },
        "prompt_profiles": {
            arm: row["task_conditioned_prompt_profile"]
            for arm, row in config["arms"].items()
        },
        "only_intended_arm_difference": "task_conditioned_prompt_profile",
        "resolved_arm_diff": arm_diff,
        "scientific_changes_from_r3": 0,
        "historical_artifacts_role": "comparison_only_after_fresh_artifact_seal",
    }
    checks = {
        "run_uuid": method["run_uuid"] == RUN_UUID,
        "run_root": str(method["roots"]["run_root"]) == RUN_ROOT,
        "authorization_scope": method["authorization_scope"]
        == AUTHORIZATION_SCOPE,
        "authorization_version": method["conditional_runtime_authorization"][
            "authorization_version"
        ]
        == AUTHORIZATION_VERSION,
        "hard_cap_proposal": float(method["proposed_hard_cap_hours"])
        == PROPOSED_HARD_CAP_HOURS,
        "selector_parent_split": reproduction["selector_parent_split"]
        == {
            "algorithm": "sha256_order_first_heldout_then_remaining_train",
            "seed": 18018,
            "train_parent_count": 29,
            "heldout_parent_count": 8,
        },
        "legal_pairs": int(method["expected"]["selector_legal_pairs"]) == 310433,
        "selector_seeds": method["selector_cv_seed"] == 25071
        and method["final_selector_member_seeds"] == [25071, 25072, 25073],
        "causal_panel": reproduction["causal_panel"]
        == {
            "initial_state_count": 256,
            "maximum_state_count": 499,
            "minimum_per_label": 40,
        },
        "post_d06_is_outcome_only": reproduction[
            "post_d06_reproduction_gate"
        ]["construction_input"]
        is False,
        "post_d06_expected_counts": reproduction[
            "post_d06_reproduction_gate"
        ]["expected_train_completed"]
        == 366
        and reproduction["post_d06_reproduction_gate"][
            "expected_heldout_completed"
        ]
        == 98,
        "arm_prompt_profiles": config["arms"]["3d"][
            "task_conditioned_prompt_profile"
        ]
        == "full_demo"
        and config["arms"]["1d"]["task_conditioned_prompt_profile"]
        == "full_demo_first_only",
        "arm_diff_prompt_only": bool(arm_diff["passed"]),
        "historical_selector_reference": method["historical_comparison"][
            "selector_ensemble_sha256"
        ]
        == "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f",
    }
    return invariants, checks


def build_preflight(
    *,
    config_path: Path,
    output_root: Path,
    source_commit: str,
    smoke_path: Path,
    tests_path: Path,
    runtime_state_path: Path,
) -> dict[str, Any]:
    config = load_resolved(config_path)
    method = config["pipeline"]
    run_root = Path(str(method["roots"]["run_root"]))
    expected_output = run_root / "preflight"
    if output_root.resolve(strict=False) != expected_output.resolve(strict=False):
        raise ValueError("Final preflight must be written under the canonical run root")
    run_root_existed = run_root.exists()
    prior_entries = (
        sorted(str(path.relative_to(run_root)) for path in run_root.rglob("*"))
        if run_root_existed
        else []
    )
    if run_root_existed:
        raise FileExistsError(f"Final run root is not fresh: {run_root}")
    if any(
        run_root.resolve(strict=False) == Path(root).resolve(strict=False)
        for _, root in OLD_RUNS.values()
    ):
        raise ValueError("Final run root aliases a historical run")

    smoke = _json(smoke_path)
    tests = _json(tests_path)
    runtime_state = _json(runtime_state_path)
    invariants, invariant_checks = static_invariants(config, run_root)
    if not all(invariant_checks.values()):
        raise ValueError(f"Scientific invariant check failed: {invariant_checks}")
    if not bool(smoke.get("passed")) or not bool(smoke.get("all_tests_passed")):
        raise ValueError("Technical smoke did not pass")
    if not bool(tests.get("passed")):
        raise ValueError("Required test suites did not pass")

    ensure_dir(output_root)
    environment = _environment_manifest(method)
    sources = _source_manifest(config)
    rebuild_shared_cpu(config, output_root)
    initialization = _initialization_manifest(config, output_root)
    arm_3d = build_arm_runtime_config(config, run_root, "3d")
    arm_1d = build_arm_runtime_config(config, run_root, "1d")
    arms = {
        arm_id: ArmContract(
            arm_id=arm_id,
            task_conditioned_prompt_profile=str(
                row["task_conditioned_prompt_profile"]
            ),
            artifact_prefix=str(row["artifact_prefix"]),
            run_id=str(row["run_id"]),
        )
        for arm_id, row in config["arms"].items()
    }
    config_sha = sha256_file(config_path)
    contract = PipelineContract(
        schema_version=str(method["schema_version"]),
        run_uuid=RUN_UUID,
        source_commit=source_commit,
        global_seed=int(method["global_seed"]),
        hard_cap_hours=PROPOSED_HARD_CAP_HOURS,
        stages=build_exp037a_stage_graph(),
        arms=arms,
        shared_initialization={
            "writer": initialization["writer"]["sha256"],
            "reader": initialization["reader"]["sha256"],
        },
        metadata={
            "pipeline_config_path": str(method["config_path"]),
            "pipeline_config_sha256": config_sha,
            "canonical_run_root": RUN_ROOT,
            "authorization_scope": AUTHORIZATION_SCOPE,
            "authorization_version": AUTHORIZATION_VERSION,
            "require_run_bound_authorization": True,
            "strict_stage_identity": True,
            "maximum_recoverable_attempts_per_stage": int(
                method["maximum_recoverable_attempts_per_stage"]
            ),
            "recoverable_retry_delay_seconds": float(
                method["recoverable_retry_delay_seconds"]
            ),
        },
    )
    atomic_write_json(output_root / "stage_dag.json", contract.as_dict())
    contract_sha = sha256_file(output_root / "stage_dag.json")
    runtime_by_stage, runtime_by_branch, hard_cap = runtime_tables()

    run_identity = {
        "format": "exp037a_r5_run_identity_14g_v1",
        "run_uuid": RUN_UUID,
        "canonical_run_root": RUN_ROOT,
        "root_existed_before_preflight": run_root_existed,
        "root_entries_before_preflight": prior_entries,
        "scientific_outputs_before_preflight": 0,
        "historical_predecessors": {
            name: {"run_uuid": value[0], "run_root": value[1]}
            for name, value in OLD_RUNS.items()
        },
        "historical_roots_written": False,
        "pipeline_config": file_identity(config_path),
        "contract_sha256": contract_sha,
    }
    launch_source = {
        "format": "exp037a_r5_launch_source_14g_v1",
        "launch_source_sha": source_commit,
        "formal_execution_checkout": source_commit,
        "records_commit_is_not_executable_source": True,
        "pipeline_config": file_identity(config_path),
        "contract_sha256": contract_sha,
    }
    d06_contract = {
        "format": "exp037a_r5_d06_gate_contract_14g_v1",
        "stage_id": "D06B_three_demo_causal_reproduction_gate",
        "fresh_d06_must_be_sealed_first": True,
        "historical_inputs_to_generation": False,
        "expected_train_completed": 366,
        "expected_heldout_completed": 98,
        "expected_label_counts": {
            "POSITIVE": 129,
            "NEUTRAL": 300,
            "HARMFUL": 35,
        },
        "exact_comparisons": [
            "completed_state_ids",
            "completed_status",
            "paired_labels",
            "label_counts",
            "over_context_state_ids",
            "replay_semantic_failure_state_ids",
        ],
        "failure_blocks": ["D07", "D08", "D09", "1D"],
        "historical_references": {
            name: file_identity(Path(str(value)))
            for name, value in method["reproduction_contract"][
                "audit_references"
            ].items()
            if name in {"paired_outcomes", "selected_memories"}
        },
    }
    writer_smoke = {
        "format": "exp037a_r5_writer_reader_smoke_contract_14g_v1",
        **dict(
            method["reproduction_contract"]["writer_reader_one_unit_smoke"]
        ),
        "uses_fresh_d08_inputs": True,
        "initialization": "cloned_locked_initialization",
        "forward_backward_optimizer_steps": 1,
        "smoke_parameters_discarded": True,
        "scientific_d09_restarts_from_untouched_initialization": True,
        "failure_blocks_d09": True,
    }
    conditional_1d = {
        "format": "exp037a_r5_conditional_1d_audit_14g_v1",
        "gateway": "D22_three_demo_reproduction_gate",
        "required_decision": "THREE_DEMO_REPRODUCTION_PASS",
        "pass_behavior": "launch O00 within scheduler transition target",
        "fail_behavior": "skip all O00-O19 and continue final failure reporting",
        "one_demo_authorized_only_as_conditional_scope": True,
        "scheduler_revalidates_d22_completion_identity": True,
    }
    stage_gate_audit = {
        "format": "exp037a_r5_stage_gate_audit_14g_v1",
        "stage_ids": [
            stage.stage_id for stage in build_exp037a_stage_graph()
        ],
        "d06_gate_precedes_d07": True,
        "d08b_gate_precedes_d09": True,
        "d22_is_only_one_demo_gateway": True,
        "global_deadline_environment": "RCMF_PIPELINE_HARD_DEADLINE_EPOCH",
        "child_process_group_terminated_at_deadline": True,
    }
    auth_request = {
        "format": "exp037a_r5_authorization_request_14g_v1",
        "authorization_status": "NOT_AUTHORIZED",
        "authorization_version": AUTHORIZATION_VERSION,
        "authorized": False,
        "authorized_to_launch": False,
        "granted_by_user": False,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": False,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": RUN_UUID,
        "run_root": RUN_ROOT,
        "source_commit": source_commit,
        "contract_sha256": contract_sha,
        "pipeline_config_sha256": config_sha,
        "hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
        "recommended_hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
        "scope": AUTHORIZATION_SCOPE,
        "global_seed": int(method["global_seed"]),
        "selector_seeds": {
            "cv": int(method["selector_cv_seed"]),
            "final": list(method["final_selector_member_seeds"]),
        },
        "runtime": runtime_by_branch,
        "checkpoint_restart_plan": {
            "atomic_stage_outputs": True,
            "append_only_attempts": True,
            "strict_run_source_config_contract_hash_before_skip": True,
            "resume_first_incomplete_stage": True,
        },
        "gates": {
            "d06": d06_contract["stage_id"],
            "d08": "D08_zero_cache_and_training_units",
            "writer_reader_smoke": writer_smoke["stage_id"],
            "d22": conditional_1d["gateway"],
        },
        "direct_submission_to_launcher_must_fail": True,
    }
    authorization_audit = {
        "format": "exp037a_r5_authorization_validator_audit_14g_v1",
        "bound_fields": [
            "run_uuid",
            "run_root",
            "source_commit",
            "contract_sha256",
            "pipeline_config_sha256",
            "hard_cap_hours",
            "scope",
            "authorization_version",
        ],
        "missing_authorization_fails_before_science": True,
        "stale_or_partial_authorization_fails_closed": True,
        "old_200_hour_authorization_fails_closed": True,
        "scheduler_independent_revalidation": True,
        "authorization_request_is_not_runtime_authorization": True,
    }
    resume_audit = {
        "format": "exp037a_r5_resume_audit_14g_v1",
        "completion_bound_fields": [
            "run_uuid",
            "run_root",
            "source_commit",
            "pipeline_config_sha256",
            "contract_sha256",
            "stage_id",
            "output_hashes",
        ],
        "completion_hash_self_validated": True,
        "foreign_completion_rejected": True,
        "interrupted_attempts_closed_before_resume": True,
        "resume_policy": "first incomplete hash-valid stage",
    }
    files: dict[str, Mapping[str, Any]] = {
        "environment_manifest.json": environment,
        "authoritative_source_manifest.json": sources,
        "two_arm_contract.json": {
            "format": "two_arm_prompt_intervention_contract_14g_v1",
            "only_intended_intervention": "task_conditioned_prompt_profile",
            "arms": {key: value.as_dict() for key, value in arms.items()},
        },
        "resolved_arm_3d.json": arm_3d,
        "resolved_arm_1d.json": arm_1d,
        "resolved_config_diff.json": invariants["resolved_arm_diff"],
        "initialization_manifest.json": initialization,
        "run_identity.json": run_identity,
        "launch_source.json": launch_source,
        "scientific_invariants.json": invariants,
        "stage_gate_audit.json": stage_gate_audit,
        "d06_reproduction_gate_contract.json": d06_contract,
        "writer_reader_smoke_contract.json": writer_smoke,
        "conditional_1d_audit.json": conditional_1d,
        "runtime_by_stage.json": runtime_by_stage,
        "runtime_by_branch.json": runtime_by_branch,
        "runtime_preflight.json": {
            "format": "rcmf_runtime_preflight_14g_v1",
            "expected_wall_hours": runtime_by_branch,
            "conservative_total_wall_hours": 92.5,
            "expected_h100_active_hours": 39.05,
            "storage": runtime_by_branch["storage"],
            "recommended_hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
            "approved_hard_cap_hours": None,
            "authorized_by_user_message": False,
            "explicit_user_approval_required": True,
            "restart_plan": {
                "atomic_stage_outputs": True,
                "append_only_attempts": True,
                "strict_identity_validation_before_skip": True,
                "resume_at_first_incomplete_stage": True,
            },
        },
        "hard_cap_proposal.json": hard_cap,
        "authorization_request.json": auth_request,
        "authorization_validator_audit.json": authorization_audit,
        "resume_audit.json": resume_audit,
        "tests.json": tests,
        "runtime_state.json": runtime_state,
        "technical_smoke.json": smoke,
    }
    for name, payload in files.items():
        atomic_write_json(output_root / name, payload)
    approval_checks = {
        **invariant_checks,
        "source_manifest": all(bool(v) for v in sources["checks"].values()),
        "tests_passed": bool(tests["passed"]),
        "technical_smoke_passed": bool(smoke["passed"]),
        "root_was_fresh": not run_root_existed,
        "authorization_remains_false": auth_request[
            "authorization_status"
        ]
        == "NOT_AUTHORIZED",
        "no_runtime_authorization_file": not (
            run_root / "runtime_authorization.json"
        ).exists(),
        "no_scientific_stage_outputs": not (run_root / "stages").exists(),
    }
    if not all(approval_checks.values()):
        raise ValueError(f"Final launch preflight failed: {approval_checks}")
    artifact_rows = [
        file_identity(output_root / "stage_dag.json"),
        *[file_identity(output_root / name) for name in sorted(files)],
    ]
    artifact_index = {
        "format": "exp037a_r5_artifact_index_14g_v1",
        "run_uuid": RUN_UUID,
        "source_commit": source_commit,
        "artifacts": artifact_rows,
        "large_scientific_outputs": [],
    }
    atomic_write_json(output_root / "artifact_index.json", artifact_index)
    summary = {
        "format": "exp037a_r5_final_launch_preflight_14g_v1",
        "decision": "READY_FOR_FINAL_RUN_APPROVAL",
        "run_uuid": RUN_UUID,
        "run_root": RUN_ROOT,
        "launch_source_sha": source_commit,
        "source_commit": source_commit,
        "contract_sha256": contract_sha,
        "pipeline_config_sha256": config_sha,
        "approval_checks": approval_checks,
        "authorization_status": "NOT_AUTHORIZED",
        "authorized_to_launch": False,
        "explicit_user_approval_required": True,
        "previous_200_hour_authorization_inherited": False,
        "proposed_hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
        "h100_scientific_active_hours": 0,
        "scientific_stage_execution_count": 0,
        "output_hashes": {
            row["path"]: row["sha256"] for row in artifact_rows
        },
        "artifact_index_sha256": sha256_file(
            output_root / "artifact_index.json"
        ),
    }
    atomic_write_json(output_root / "preflight_summary.json", summary)
    return summary


def main() -> None:
    args = parse_args()
    summary = build_preflight(
        config_path=args.config,
        output_root=args.output_root,
        source_commit=args.source_commit,
        smoke_path=args.smoke_results,
        tests_path=args.tests_json,
        runtime_state_path=args.runtime_state_json,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
