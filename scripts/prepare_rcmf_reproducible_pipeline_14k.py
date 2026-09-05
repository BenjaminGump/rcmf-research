#!/usr/bin/env python3
"""Build the final EXP-037A launch package without authorizing or running it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401
import yaml
from rcmf.benchmarks.appworld.reproducible_config_14b import (
    build_arm_runtime_config,
)
from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
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


RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001"
RUN_ROOT = (
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    + RUN_UUID
)
OLD_RUNS = {
    "14i_preflight_001": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_001",
    ),
    "14i_preflight_002": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002",
    ),
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
    "14g": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001",
    ),
    "14h": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001",
    ),
    "14j": (
        "rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001",
        "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
        "rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001",
    ),
}
AUTHORIZATION_SCOPE = (
    "complete_fresh_3d_then_conditional_fresh_1d_and_final_reporting"
)
AUTHORIZATION_VERSION = "exp037a_run_bound_authorization_14k_v1"
PROPOSED_HARD_CAP_HOURS = 80.0
REPLAY_CONFIG = Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14k.yaml"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--smoke-results", type=Path, required=True)
    parser.add_argument("--tests-json", type=Path, required=True)
    parser.add_argument("--runtime-state-json", type=Path, required=True)
    parser.add_argument(
        "--synthetic-resume-results", type=Path, required=True
    )
    parser.add_argument("--d09-resume-results", type=Path, required=True)
    parser.add_argument(
        "--d09-preparation-manifest", type=Path, required=True
    )
    parser.add_argument("--pipeline-audit-summary", type=Path, required=True)
    parser.add_argument("--pipeline-audit-index", type=Path, required=True)
    parser.add_argument("--r12b-token-audit", type=Path, required=True)
    parser.add_argument("--r12b-live-profile", type=Path, required=True)
    parser.add_argument("--r12b-paired-smoke", type=Path, required=True)
    parser.add_argument("--r12b-o06-validation", type=Path, required=True)
    parser.add_argument("--r12b-o07-smoke", type=Path, required=True)
    parser.add_argument("--r12b-d06-compatibility", type=Path, required=True)
    parser.add_argument("--r12b-consumer-audit", type=Path, required=True)
    parser.add_argument("--r12b-source-compatibility", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def runtime_tables() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stages = {
        "format": "exp037a_r12b_runtime_by_stage_14k_v1",
        "basis": [
            "14j S00 through D22 measured wall: 16.601405 h",
            "14j O00 through O05 measured wall after D22: 2.321830 h",
            "R12B fresh repaired O06 measured wall: 1.577605 h",
            "14j D09/D10 measured wall: 1.028789 h / 1.030807 h",
            "14j D18/D19/D20 measured wall: 1.593341 h / 1.998636 h / 1.968076 h",
            "unchanged 14j 3D stage timings provide the 1D downstream analogues",
        ],
        "groups": [
            {
                "group": "shared_preflight_and_transition_representations",
                "stages": "S00-S09",
                "expected_wall_hours": 0.25,
                "conservative_wall_hours": 0.75,
                "measured_14j_wall_hours": 0.135614,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_selector",
                "stages": "D00-D05",
                "expected_wall_hours": 2.4,
                "conservative_wall_hours": 4.0,
                "measured_14j_wall_hours": 2.294682,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_paired_and_early_gate",
                "stages": "D06-D06B",
                "expected_wall_hours": 2.0,
                "conservative_wall_hours": 4.0,
                "measured_14j_d06_wall_hours": 1.910789,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_teacher_prepare_and_smoke",
                "stages": "D07-D08B",
                "expected_wall_hours": 0.6,
                "conservative_wall_hours": 1.5,
                "measured_14j_wall_hours": 0.490681,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_training_validation_and_fields",
                "stages": "D09-D17",
                "expected_wall_hours": 6.1,
                "conservative_wall_hours": 12.0,
                "measured_14j_wall_hours": 6.166395,
                "gpu": "mixed",
            },
            {
                "group": "three_demo_dev_and_final_gate",
                "stages": "D18-D22",
                "expected_wall_hours": 5.6,
                "conservative_wall_hours": 11.5,
                "measured_14j_wall_hours": 5.563617,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_selector",
                "stages": "O00-O05",
                "expected_wall_hours": 2.4,
                "conservative_wall_hours": 4.0,
                "measured_14j_wall_hours": 2.315060,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_paired_teacher_prepare",
                "stages": "O06-O08",
                "expected_wall_hours": 2.25,
                "conservative_wall_hours": 4.5,
                "measured_r12b_o06_wall_hours": 1.577605,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_training_validation_and_fields",
                "stages": "O09-O17",
                "expected_wall_hours": 6.1,
                "conservative_wall_hours": 12.0,
                "gpu": "mixed",
            },
            {
                "group": "conditional_one_demo_dev",
                "stages": "O18-O19",
                "expected_wall_hours": 4.0,
                "conservative_wall_hours": 8.0,
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
        "format": "exp037a_r12b_runtime_by_branch_14k_v1",
        "branch_3d_reproduction_fails": {
            "scope": "shared + complete 3D through D22 + final failure reporting",
            "expected_wall_hours": 18.0,
            "conservative_wall_hours": 36.0,
            "expected_h100_active_hours": 16.0,
        },
        "branch_3d_passes_and_1d_executes": {
            "scope": "shared + complete 3D + D22 + complete conditional 1D + final reporting",
            "expected_wall_hours": 32.0,
            "conservative_wall_hours": 64.0,
            "expected_h100_active_hours": 29.0,
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
        "format": "exp037a_r12b_hard_cap_proposal_14k_v1",
        "longest_permitted_branch": "branch_3d_passes_and_1d_executes",
        "formula": "ceil_practical(max(2.0*32.0,1.25*64.0))",
        "twice_expected_hours": 64.0,
        "one_point_two_five_conservative_hours": 80.0,
        "unrounded_required_hours": 80.0,
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
        "format": "exp037a_r10_scientific_invariants_14k_v1",
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
        "strict_stage_identity": method["strict_stage_identity"] is True,
    }
    return invariants, checks


def stage_manifest_producer_audit() -> dict[str, Any]:
    stages = build_exp037a_stage_graph()
    formal_runner = "scripts/run_rcmf_reproducible_stage_14b.py"
    formal_rows = [
        {
            "stage_id": stage.stage_id,
            "command": list(stage.command),
            "producer": (
                "rcmf.benchmarks.appworld.reproducible_stages_14b."
                "write_stage_manifest"
            ),
            "caller": formal_runner,
            "identity_fields_emitted": [
                "source_commit",
                "run_uuid",
                "run_root",
                "pipeline_config_sha256",
                "contract_sha256",
                "stage_id",
                "attempt_id",
            ],
            "strict_validator_compatible": (
                len(stage.command) >= 2
                and stage.command[1] == formal_runner
            ),
        }
        for stage in stages
    ]
    repository_writers = [
        {
            "producer": (
                "rcmf/benchmarks/appworld/reproducible_stages_14b.py:"
                "write_stage_manifest"
            ),
            "caller": formal_runner,
            "stage_population": "all formal EXP-037A stages",
            "formal": True,
            "strict_validator_compatible": True,
        },
        {
            "producer": (
                "scripts/smoke_rcmf_reproducible_pipeline_14b.py:"
                "_write_mock_output"
            ),
            "caller": "bounded mock scheduler smoke only",
            "stage_population": "mock temporary directories",
            "formal": False,
            "strict_validator_compatible": False,
        },
        {
            "producer": "tests/test_reproducible_pipeline_14b.py helpers",
            "caller": "unit tests only",
            "stage_population": "pytest temporary directories",
            "formal": False,
            "strict_validator_compatible": False,
        },
        {
            "producer": (
                "tests/test_exp037a_r5_final_launch_preflight.py:"
                "_write_output"
            ),
            "caller": "R5 synthetic unit tests only",
            "stage_population": "pytest temporary directories",
            "formal": False,
            "strict_validator_compatible": True,
        },
    ]
    checks = {
        "formal_stage_count": len(formal_rows) == 60,
        "one_formal_runner": {
            tuple(row["command"][:2]) for row in formal_rows
        }
        == {("{python}", formal_runner)},
        "one_formal_producer": len(
            {row["producer"] for row in formal_rows}
        )
        == 1,
        "all_formal_stages_strict_compatible": all(
            row["strict_validator_compatible"] for row in formal_rows
        ),
        "nonformal_writers_excluded_from_stage_graph": all(
            not row["formal"] for row in repository_writers[1:]
        ),
    }
    return {
        "format": "exp037a_r10_stage_manifest_producer_audit_14k_v1",
        "formal_stages": formal_rows,
        "repository_output_manifest_writers": repository_writers,
        "checks": checks,
        "passed": all(checks.values()),
    }


def executable_source_manifest() -> dict[str, Any]:
    paths = {
        "agents": Path("AGENTS.md"),
        "pipeline_config": Path(
            "configs/pipeline/rcmf_appworld_repro_14k.yaml"
        ),
        "arm_3d_config": Path(
            "configs/pipeline/rcmf_appworld_arm_3d_14k.yaml"
        ),
        "arm_1d_config": Path(
            "configs/pipeline/rcmf_appworld_arm_1d_14k.yaml"
        ),
        "preflight_builder": Path(
            "scripts/prepare_rcmf_reproducible_pipeline_14k.py"
        ),
        "formal_launcher": Path(
            "scripts/run_rcmf_reproducible_pipeline_14b.py"
        ),
        "formal_stage_runner": Path(
            "scripts/run_rcmf_reproducible_stage_14b.py"
        ),
        "real_path_smoke": Path(
            "scripts/smoke_exp037a_stage_manifest_path_14j.py"
        ),
        "stage_manifest_producer": Path(
            "rcmf/benchmarks/appworld/reproducible_stages_14b.py"
        ),
        "manifest_identity": Path("rcmf/pipeline/manifests.py"),
        "authorization": Path("rcmf/pipeline/authorization.py"),
        "contracts": Path("rcmf/pipeline/contracts.py"),
        "orchestrator": Path("rcmf/pipeline/orchestrator.py"),
        "scheduler": Path("rcmf/pipeline/scheduler.py"),
        "stage_graph": Path("rcmf/pipeline/stage_graph.py"),
        "validators": Path("rcmf/pipeline/validators.py"),
        "checkpoint_training_runner": Path(
            "scripts/run_rcmf_joint_full_bank_9a.py"
        ),
        "checkpoint_resume_validator": Path(
            "scripts/validate_rcmf_checkpoint_resume_r9.py"
        ),
        "d09_resume_preparer": Path(
            "scripts/prepare_exp037a_r9_d09_resume_smoke.py"
        ),
        "whole_pipeline_audit": Path(
            "scripts/audit_exp037a_pipeline_r10.py"
        ),
        "r7_stage_identity_tests": Path(
            "tests/test_exp037a_r7_stage_manifest_identity.py"
        ),
        "r9_checkpoint_resume_tests": Path(
            "tests/test_exp037a_r9_checkpoint_resume.py"
        ),
        "r10_pipeline_hardening_tests": Path(
            "tests/test_exp037a_r10_pipeline_hardening.py"
        ),
        "paired_causal_runtime_resolver": Path(
            "rcmf/benchmarks/appworld/paired_causal_runtime_14k.py"
        ),
        "structured_rescue_preflight": Path(
            "scripts/prepare_appworld_structured_rescue_7hr.py"
        ),
        "paired_causal_stage_runner": Path(
            "scripts/run_appworld_train_causal_gate_7hr.py"
        ),
        "paired_causal_condition_runner": Path(
            "scripts/run_procedural_causal_audit_7b.py"
        ),
        "r12b_token_contract_audit": Path(
            "scripts/audit_exp037a_r12b_token_contract.py"
        ),
        "r12b_live_profile_audit": Path(
            "scripts/audit_exp037a_r12b_live_profile.py"
        ),
        "r12b_paired_profile_smoke": Path(
            "scripts/smoke_exp037a_r12b_paired_profile.py"
        ),
        "r12b_o06_preparer": Path(
            "scripts/prepare_exp037a_r12b_o06_diagnostic.py"
        ),
        "r12b_o06_validator": Path(
            "scripts/validate_exp037a_r12b_o06_diagnostic.py"
        ),
        "r12b_o07_smoke": Path(
            "scripts/smoke_exp037a_r12b_o07_teacher.py"
        ),
        "r12b_d06_compatibility_audit": Path(
            "scripts/audit_exp037a_r12b_d06_compatibility.py"
        ),
        "r12b_prompt_consumer_audit": Path(
            "scripts/audit_exp037a_r12b_prompt_consumers.py"
        ),
        "r12b_prompt_profile_tests": Path(
            "tests/test_exp037a_r12b_prompt_profile_repair.py"
        ),
        "r12b_14k_preflight_tests": Path(
            "tests/test_exp037a_r12b_14k_preflight.py"
        ),
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Launch source is incomplete: {missing}")
    return {
        "format": "exp037a_r10_executable_source_manifest_14k_v1",
        "files": {
            name: file_identity(path) for name, path in paths.items()
        },
        "scientific_configuration_changes_from_r3": 0,
    }


def build_preflight(
    *,
    config_path: Path,
    output_root: Path,
    source_commit: str,
    smoke_path: Path,
    tests_path: Path,
    runtime_state_path: Path,
    synthetic_resume_path: Path,
    d09_resume_path: Path,
    d09_preparation_path: Path,
    pipeline_audit_summary_path: Path,
    pipeline_audit_index_path: Path,
    r12b_token_audit_path: Path,
    r12b_live_profile_path: Path,
    r12b_paired_smoke_path: Path,
    r12b_o06_validation_path: Path,
    r12b_o07_smoke_path: Path,
    r12b_d06_compatibility_path: Path,
    r12b_consumer_audit_path: Path,
    r12b_source_compatibility_path: Path,
) -> dict[str, Any]:
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
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
    synthetic_resume = _json(synthetic_resume_path)
    d09_resume = _json(d09_resume_path)
    d09_preparation = _json(d09_preparation_path)
    pipeline_audit = _json(pipeline_audit_summary_path)
    pipeline_audit_index = _json(pipeline_audit_index_path)
    token_audit = _json(r12b_token_audit_path)
    live_profile = _json(r12b_live_profile_path)
    paired_smoke = _json(r12b_paired_smoke_path)
    o06_validation = _json(r12b_o06_validation_path)
    o07_smoke = _json(r12b_o07_smoke_path)
    d06_compatibility = _json(r12b_d06_compatibility_path)
    consumer_audit = _json(r12b_consumer_audit_path)
    source_compatibility = _json(r12b_source_compatibility_path)
    invariants, invariant_checks = static_invariants(config, run_root)
    if not all(invariant_checks.values()):
        raise ValueError(f"Scientific invariant check failed: {invariant_checks}")
    if not bool(smoke.get("passed")):
        raise ValueError("Technical smoke did not pass")
    if not bool(tests.get("passed")):
        raise ValueError("Required test suites did not pass")
    evidence_identity_checks = {
        "technical_smoke_source": smoke.get("source_commit")
        == source_commit,
        "tests_source": tests.get("source_commit") == source_commit,
        "runtime_state_source": runtime_state.get("launch_source")
        == source_commit,
        "source_compatibility_candidate": source_compatibility.get(
            "candidate_launch_source"
        )
        == source_commit,
    }
    if not all(evidence_identity_checks.values()):
        raise ValueError(
            "Launch-source evidence identity failed: "
            f"{evidence_identity_checks}"
        )
    synthetic_checks = {
        "passed": synthetic_resume.get("passed") is True,
        "fresh_process": synthetic_resume.get("fresh_resume_process") is True,
        "three_processes": synthetic_resume.get("process_count") == 3,
        "all_comparisons": all(
            bool(value)
            for value in synthetic_resume.get("comparisons", {}).values()
        ),
        "cuda_exercised": synthetic_resume.get("resumed", {}).get("device")
        == "cuda",
    }
    d09_checks = {
        "prepared": d09_preparation.get("passed") is True,
        "sealed_checkpoint_hash": d09_preparation.get(
            "source_checkpoint_sha256"
        )
        == "60c40ca73ecdc7f8fea15ec50e87bca28ad8efcd4c33d98b91ea282273c2bd40",
        "source_unchanged": d09_preparation.get(
            "source_files_unchanged_after_copy"
        )
        is True,
        "not_14k_input": d09_preparation.get(
            "scientific_checkpoint_input_for_14i"
        )
        is False,
        "resume_passed": d09_resume.get("passed") is True,
        "starts_after_576": d09_resume.get("completed_units") == 577,
        "one_backward": d09_resume.get("backward_count_this_attempt") == 1,
        "one_optimizer_step": d09_resume.get(
            "optimizer_step_count_this_attempt"
        )
        == 1,
        "finite": d09_resume.get("all_losses_finite") is True
        and d09_resume.get("trainable_gradients_finite") is True
        and d09_resume.get("post_step_parameters_finite") is True,
        "gradient_paths": d09_resume.get("writer_gradient_nonzero") is True
        and d09_resume.get("reader_gradient_nonzero") is True,
        "frozen_dependencies": d09_resume.get(
            "qwen_frozen_and_gradient_free"
        )
        is True
        and d09_resume.get("selector_tensors_frozen") is True,
        "diagnostic_only": d09_resume.get("scientific_result") is False,
    }
    pipeline_audit_checks = {
        "source_commit": pipeline_audit.get("source_commit") == source_commit,
        "index_source_commit": pipeline_audit_index.get("source_commit")
        == source_commit,
        "all_stages": pipeline_audit.get("stage_count") == 60,
        "all_device_loads_classified": pipeline_audit.get(
            "unclassified_device_load_count", 0
        )
        == 0,
        "scientific_configuration_unchanged": pipeline_audit.get(
            "scientific_configuration_changes"
        )
        == 0,
        "no_long_run": pipeline_audit.get("long_scientific_run_launched")
        is False,
    }
    if not all(synthetic_checks.values()):
        raise ValueError(
            f"Cross-process resume equivalence failed: {synthetic_checks}"
        )
    if not all(d09_checks.values()):
        raise ValueError(f"Sealed D09 resume smoke failed: {d09_checks}")
    if not all(pipeline_audit_checks.values()):
        raise ValueError(
            f"Whole-pipeline audit evidence failed: {pipeline_audit_checks}"
        )

    token_arms = token_audit.get("arms", {})
    r12b_checks = {
        "token_audit_zero_discrete_changes": token_audit.get(
            "zero_discrete_changes"
        )
        is True,
        "token_audit_3d_all_states": token_arms.get("3d", {}).get(
            "state_count"
        )
        == 499,
        "token_audit_1d_all_states": token_arms.get("1d", {}).get(
            "state_count"
        )
        == 499,
        "live_profile_wrong_full_demo_infeasible": live_profile.get(
            "wrong_profile_over_context_count"
        )
        == 6,
        "live_profile_correct_one_demo_feasible": live_profile.get(
            "corrected_profile_feasible_count"
        )
        == 6,
        "live_profile_formal_root_unchanged": live_profile.get(
            "formal_root_unchanged"
        )
        is True,
        "paired_smoke_one_demo": paired_smoke.get(
            "effective_runtime_prompt_profile"
        )
        == "full_demo_first_only",
        "paired_smoke_two_fresh_conditions": paired_smoke.get(
            "condition_count"
        )
        == 2
        and paired_smoke.get("generated_condition_count") == 2
        and paired_smoke.get("generated_condition_count")
        == paired_smoke.get("condition_count"),
        "paired_smoke_no_optimization": paired_smoke.get("optimizer_steps", 0)
        == 0
        and paired_smoke.get("backward_count", 0) == 0,
        "paired_smoke_formal_root_unchanged": paired_smoke.get(
            "formal_root_unchanged"
        )
        is True,
        "full_o06_passed": o06_validation.get("passed") is True,
        "full_o06_one_demo": o06_validation.get(
            "effective_runtime", {}
        ).get("effective_runtime_prompt_profile")
        == "full_demo_first_only",
        "full_o06_fresh_conditions": o06_validation.get(
            "generated_condition_count"
        )
        == 814
        and o06_validation.get("reused_condition_count") == 0,
        "full_o06_complete_pairs": o06_validation.get(
            "paired_state_count"
        )
        == 407
        and (
            o06_validation.get("generated_condition_count", 0)
            + o06_validation.get("reused_condition_count", 0)
        )
        == 814,
        "full_o06_valid_panel_stop": o06_validation.get(
            "minimum_label_gate_passed"
        )
        is True
        or o06_validation.get("maximum_state_space_exhausted") is True,
        "full_o06_formal_root_unchanged": o06_validation.get(
            "formal_root_unchanged"
        )
        is True,
        "o07_one_demo": o07_smoke.get("arm_resolved_prompt_profile")
        == "full_demo_first_only",
        "o07_no_generation_or_optimization": o07_smoke.get(
            "qwen_generation_count", 0
        )
        == 0
        and o07_smoke.get("optimizer_steps", 0) == 0
        and o07_smoke.get("backward_count", 0) == 0,
        "o07_qwen_frozen": o07_smoke.get("qwen_frozen") is True,
        "d06_compatibility_all_conditions": d06_compatibility.get(
            "available_condition_count"
        )
        == 928
        and d06_compatibility.get("exact_prompt_match_count") == 928
        and d06_compatibility.get("mismatch_count") == 0,
        "d06_effective_diff_zero": d06_compatibility.get(
            "effective_runtime", {}
        ).get("three_demo_effective_generation_diff")
        == 0,
        "d06_formal_root_unchanged": d06_compatibility.get(
            "formal_root_unchanged"
        )
        is True,
        "downstream_consumers_passed": consumer_audit.get("passed") is True
        and consumer_audit.get("formal_path_mismatch_count") == 0,
        "source_compatibility_passed": source_compatibility.get("passed")
        is True
        and not source_compatibility.get("production_path_differences", []),
        **evidence_identity_checks,
    }
    if not all(r12b_checks.values()):
        raise ValueError(f"R12B repair validation failed: {r12b_checks}")

    ensure_dir(output_root)
    environment = _environment_manifest(method)
    sources = _source_manifest(config)
    rebuild_shared_cpu(config, output_root)
    initialization = _initialization_manifest(config, output_root)
    arm_3d = build_arm_runtime_config(config, run_root, "3d")
    arm_1d = build_arm_runtime_config(config, run_root, "1d")
    replay_config = yaml.safe_load(REPLAY_CONFIG.read_text(encoding="utf-8"))
    arm_3d_path = config_path.parent / str(
        raw_config["arms"]["3d"]["include"]
    )
    arm_1d_path = config_path.parent / str(
        raw_config["arms"]["1d"]["include"]
    )
    effective_3d_config, effective_3d = (
        resolve_effective_paired_causal_runtime(
        replay_config=replay_config,
        arm_config=arm_3d,
        arm_id="3d",
        replay_config_path=str(REPLAY_CONFIG),
        replay_config_sha256=sha256_file(REPLAY_CONFIG),
        arm_config_path=str(arm_3d_path),
        arm_config_sha256=sha256_file(arm_3d_path),
        )
    )
    effective_1d_config, effective_1d = (
        resolve_effective_paired_causal_runtime(
        replay_config=replay_config,
        arm_config=arm_1d,
        arm_id="1d",
        replay_config_path=str(REPLAY_CONFIG),
        replay_config_sha256=sha256_file(REPLAY_CONFIG),
        arm_config_path=str(arm_1d_path),
        arm_config_sha256=sha256_file(arm_1d_path),
        )
    )
    effective_checks = {
        "three_demo_execution_diff_zero": effective_3d[
            "changed_execution_fields"
        ]
        == [],
        "three_demo_hash_matches_legacy": effective_3d[
            "effective_causal_generation_config_sha256"
        ]
        == effective_3d["legacy_causal_generation_config_sha256"],
        "one_demo_prompt_only_diff": effective_1d[
            "changed_execution_fields"
        ]
        == ["prompt_profile"],
        "three_demo_profile": effective_3d["effective_prompt_profile"]
        == "full_demo",
        "one_demo_profile": effective_1d["effective_prompt_profile"]
        == "full_demo_first_only",
        "three_demo_config_matches_legacy": effective_3d_config[
            "causal_audit"
        ]["generation"]
        == replay_config["stage_c_7b"]["causal_audit"]["generation"],
        "one_demo_nonprompt_fields_match_legacy": {
            key: value
            for key, value in effective_1d_config["causal_audit"][
                "generation"
            ].items()
            if key != "prompt_profile"
        }
        == {
            key: value
            for key, value in replay_config["stage_c_7b"]["causal_audit"][
                "generation"
            ].items()
            if key != "prompt_profile"
        },
    }
    if not all(effective_checks.values()):
        raise ValueError(
            f"Effective paired-generation contract failed: {effective_checks}"
        )
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
    producer_audit = stage_manifest_producer_audit()
    executable_sources = executable_source_manifest()
    if not producer_audit["passed"]:
        raise ValueError("Formal stage-manifest producer audit failed")

    run_identity = {
        "format": "exp037a_r10_run_identity_14k_v1",
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
        "format": "exp037a_r10_launch_source_14k_v1",
        "launch_source_sha": source_commit,
        "formal_execution_checkout": source_commit,
        "records_commit_is_not_executable_source": True,
        "pipeline_config": file_identity(config_path),
        "contract_sha256": contract_sha,
    }
    d06_contract = {
        "format": "exp037a_r10_d06_gate_contract_14k_v1",
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
        "format": "exp037a_r10_writer_reader_smoke_contract_14k_v1",
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
        "format": "exp037a_r10_conditional_1d_audit_14k_v1",
        "gateway": "D22_three_demo_reproduction_gate",
        "required_decision": "THREE_DEMO_REPRODUCTION_PASS",
        "pass_behavior": "launch O00 within scheduler transition target",
        "fail_behavior": "skip all O00-O19 and continue final failure reporting",
        "one_demo_authorized_only_as_conditional_scope": True,
        "scheduler_revalidates_d22_completion_identity": True,
    }
    stage_gate_audit = {
        "format": "exp037a_r10_stage_gate_audit_14k_v1",
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
        "format": "exp037a_r12b_authorization_request_14k_v1",
        "authorization_status": "NOT_AUTHORIZED",
        "authorization_version": AUTHORIZATION_VERSION,
        "authorized": False,
        "authorized_to_launch": False,
        "granted_by_user": False,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": False,
        "previous_200_hour_authorization_inherited": False,
        "failed_r6_authorization_inherited": False,
        "failed_r8_14h_authorization_inherited": False,
        "failed_14j_authorization_inherited": False,
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
        "format": "exp037a_r12b_authorization_validator_audit_14k_v1",
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
        "failed_14h_authorization_fails_closed": True,
        "failed_14j_authorization_fails_closed": True,
        "scheduler_independent_revalidation": True,
        "authorization_request_is_not_runtime_authorization": True,
    }
    resume_audit = {
        "format": "exp037a_r12b_resume_audit_14k_v1",
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
        "rng_restore_contract": {
            "cpu_rng": "cpu_contiguous_uint8",
            "cuda_rng": "cpu_contiguous_uint8_per_device",
            "invalid_type_dtype_shape": "fail_closed",
        },
        "cross_process_equivalence": {
            "input": file_identity(synthetic_resume_path),
            "checks": synthetic_checks,
            "passed": all(synthetic_checks.values()),
        },
        "sealed_d09_one_unit_smoke": {
            "preparation": file_identity(d09_preparation_path),
            "result": file_identity(d09_resume_path),
            "checks": d09_checks,
            "passed": all(d09_checks.values()),
            "scientific_input_for_14k": False,
        },
        "production_checkpoint_migration": False,
        "fresh_rerun_preferred": True,
    }
    effective_paired_generation = {
        "format": "exp037a_r12b_effective_paired_generation_14k_v1",
        "ownership": {
            "shared_baseline": "legacy causal-audit generation config",
            "arm_authoritative_override": "prompt_profile",
            "validated_equal": [
                "model",
                "context_limit",
                "temperature",
                "top_p",
                "do_sample",
                "enable_thinking",
            ],
            "legacy_owned": ["max_new_tokens", "dtype", "device_map"],
        },
        "replay_config": file_identity(REPLAY_CONFIG),
        "arms": {"3d": effective_3d, "1d": effective_1d},
        "checks": effective_checks,
        "passed": all(effective_checks.values()),
    }
    r12b_validation = {
        "format": "exp037a_r12b_repair_validation_14k_v1",
        "checks": r12b_checks,
        "passed": all(r12b_checks.values()),
        "evidence": {
            "token_contract": file_identity(r12b_token_audit_path),
            "live_profile": file_identity(r12b_live_profile_path),
            "paired_profile_smoke": file_identity(r12b_paired_smoke_path),
            "full_o06_validation": file_identity(r12b_o06_validation_path),
            "o07_smoke": file_identity(r12b_o07_smoke_path),
            "d06_compatibility": file_identity(
                r12b_d06_compatibility_path
            ),
            "downstream_prompt_consumers": file_identity(
                r12b_consumer_audit_path
            ),
            "source_compatibility": file_identity(
                r12b_source_compatibility_path
            ),
        },
        "full_o06": {
            "paired_states": o06_validation.get("paired_state_count"),
            "train_states": o06_validation.get("model_train_count"),
            "heldout_states": o06_validation.get("heldout_count"),
            "label_counts": o06_validation.get("label_counts"),
            "static_over_context_count": o06_validation.get(
                "static_over_context_count"
            ),
            "replay_missing_count": o06_validation.get(
                "replay_missing_count"
            ),
            "elapsed_seconds": o06_validation.get("elapsed_seconds"),
            "scientific_result": False,
        },
        "production_behavior_change": (
            "1D paired-causal runtime uses its preregistered "
            "full_demo_first_only profile"
        ),
        "three_demo_scientific_behavior_changes": 0,
        "model_method_changes": 0,
        "selector_changes": 0,
        "panel_rule_changes": 0,
        "memory_changes": 0,
        "loss_training_changes": 0,
        "d06b_d22_changes": 0,
        "context_limit_changes": 0,
    }
    files: dict[str, Mapping[str, Any]] = {
        "environment_manifest.json": environment,
        "authoritative_source_manifest.json": sources,
        "two_arm_contract.json": {
            "format": "two_arm_prompt_intervention_contract_14k_v1",
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
            "format": "rcmf_runtime_preflight_14k_v1",
            "expected_wall_hours": runtime_by_branch,
            "conservative_total_wall_hours": 64.0,
            "expected_h100_active_hours": 29.0,
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
        "checkpoint_resume_validation.json": {
            "format": "exp037a_r10_checkpoint_resume_validation_14k_v1",
            "synthetic": resume_audit["cross_process_equivalence"],
            "sealed_d09": resume_audit["sealed_d09_one_unit_smoke"],
            "passed": True,
        },
        "stage_manifest_producer_audit.json": producer_audit,
        "executable_source_manifest.json": executable_sources,
        "tests.json": tests,
        "runtime_state.json": runtime_state,
        "technical_smoke.json": smoke,
        "whole_pipeline_audit_summary.json": pipeline_audit,
        "whole_pipeline_audit_index.json": pipeline_audit_index,
        "effective_paired_generation_contract.json": (
            effective_paired_generation
        ),
        "r12b_repair_validation.json": r12b_validation,
        "r12b_token_contract_summary.json": token_audit,
        "r12b_live_profile_summary.json": live_profile,
        "r12b_paired_profile_smoke.json": paired_smoke,
        "r12b_o06_validation.json": o06_validation,
        "r12b_o07_smoke.json": o07_smoke,
        "r12b_d06_compatibility.json": d06_compatibility,
        "r12b_prompt_consumer_audit.json": consumer_audit,
        "r12b_source_compatibility.json": source_compatibility,
    }
    for name, payload in files.items():
        atomic_write_json(output_root / name, payload)
    approval_checks = {
        **invariant_checks,
        "source_manifest": all(bool(v) for v in sources["checks"].values()),
        "tests_passed": bool(tests["passed"]),
        "technical_smoke_passed": bool(smoke["passed"]),
        "cross_process_resume_equivalence": all(
            synthetic_checks.values()
        ),
        "sealed_d09_one_unit_resume": all(d09_checks.values()),
        "root_was_fresh": not run_root_existed,
        "authorization_remains_false": auth_request[
            "authorization_status"
        ]
        == "NOT_AUTHORIZED",
        "failed_r6_authorization_not_inherited": auth_request[
            "failed_r6_authorization_inherited"
        ]
        is False,
        "failed_r8_14h_authorization_not_inherited": auth_request[
            "failed_r8_14h_authorization_inherited"
        ]
        is False,
        "failed_14j_authorization_not_inherited": auth_request[
            "failed_14j_authorization_inherited"
        ]
        is False,
        "effective_paired_generation_contract": all(
            effective_checks.values()
        ),
        "r12b_repair_validation": all(r12b_checks.values()),
        "formal_manifest_producer_audit": producer_audit["passed"],
        "whole_pipeline_audit": all(pipeline_audit_checks.values()),
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
        "format": "exp037a_r12b_artifact_index_14k_v1",
        "run_uuid": RUN_UUID,
        "source_commit": source_commit,
        "artifacts": artifact_rows,
        "large_scientific_outputs": [],
    }
    atomic_write_json(output_root / "artifact_index.json", artifact_index)
    summary = {
        "format": "exp037a_r12b_final_launch_preflight_14k_v1",
        "decision": "READY_FOR_14K_REAUTHORIZATION",
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
        "failed_r6_authorization_inherited": False,
        "failed_r8_14h_authorization_inherited": False,
        "failed_14j_authorization_inherited": False,
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
        synthetic_resume_path=args.synthetic_resume_results,
        d09_resume_path=args.d09_resume_results,
        d09_preparation_path=args.d09_preparation_manifest,
        pipeline_audit_summary_path=args.pipeline_audit_summary,
        pipeline_audit_index_path=args.pipeline_audit_index,
        r12b_token_audit_path=args.r12b_token_audit,
        r12b_live_profile_path=args.r12b_live_profile,
        r12b_paired_smoke_path=args.r12b_paired_smoke,
        r12b_o06_validation_path=args.r12b_o06_validation,
        r12b_o07_smoke_path=args.r12b_o07_smoke,
        r12b_d06_compatibility_path=args.r12b_d06_compatibility,
        r12b_consumer_audit_path=args.r12b_consumer_audit,
        r12b_source_compatibility_path=args.r12b_source_compatibility,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()

