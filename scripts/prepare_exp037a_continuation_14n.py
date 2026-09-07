#!/usr/bin/env python3
"""Build the frozen, unauthorized EXP-037A 14n continuation package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

import _bootstrap  # noqa: F401
import yaml

from rcmf.benchmarks.appworld.continuation_14l import (
    PARENT_CONFIG_SHA256,
    PARENT_CONTRACT_SHA256,
    PARENT_RUN_UUID,
    PARENT_SOURCE_COMMIT,
    build_continuation_arm_config,
    collect_parent_artifact_manifest,
    validate_parent_boundary,
)
from rcmf.benchmarks.appworld.scoreable_count_contract_14l import (
    EXACT_REPRODUCTION,
    SEALED_UPSTREAM_OUTCOMES,
    scoreable_count_contract,
)
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.stage_graph import build_exp037a_continuation_stage_graph
from rcmf.utils.serialization import atomic_write_json, ensure_dir, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved


EXPECTED_RUN_UUID = "rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003"
EXPECTED_RUN_ROOT = (
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    + EXPECTED_RUN_UUID
)
AUTHORIZATION_SCOPE = (
    "continue_from_sealed_14k_o07_boundary_through_o19_and_final_reporting"
)
AUTHORIZATION_VERSION = "exp037a_run_bound_authorization_14n_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_continuation_14n.yaml"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--stale-audit", type=Path, required=True)
    parser.add_argument("--o08-diagnostic", type=Path, required=True)
    parser.add_argument("--o09-smoke", type=Path, required=True)
    parser.add_argument("--o13-path-diagnostic", type=Path, required=True)
    parser.add_argument("--o13-smoke", type=Path, required=True)
    parser.add_argument("--path-ownership-audit", type=Path, required=True)
    parser.add_argument("--continuation-dispatch-audit", type=Path, required=True)
    parser.add_argument("--c00-c01-diagnostic", type=Path, required=True)
    parser.add_argument("--tests-json", type=Path, required=True)
    parser.add_argument("--scientific-diff", type=Path, required=True)
    parser.add_argument("--runtime-estimate", type=Path, required=True)
    parser.add_argument("--runtime-state", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_initializations(parent_root: Path, run_root: Path) -> dict[str, Any]:
    target_root = ensure_dir(run_root / "preflight/initialization_snapshots")
    rows = {}
    for name in ("writer_initial.pt", "reader_initial.pt"):
        source = parent_root / "preflight/initialization_snapshots" / name
        target = target_root / name
        shutil.copy2(source, target)
        source_identity = file_identity(source)
        target_identity = file_identity(target)
        hardlinked = source.stat().st_ino == target.stat().st_ino
        if source_identity["sha256"] != target_identity["sha256"] or hardlinked:
            raise RuntimeError(f"Initialization snapshot copy is not isolated: {name}")
        rows[name] = {
            "parent": source_identity,
            "continuation_copy": target_identity,
            "byte_identical": True,
            "hardlinked": False,
        }
    return rows


def main() -> None:
    args = parse_args()
    config = load_resolved(args.config)
    method = config["pipeline"]
    run_root = args.output_root.resolve(strict=False)
    parent_root = Path(str(method["continuation"]["parent_root"])).resolve(
        strict=True
    )
    if run_root.exists():
        raise FileExistsError(f"Continuation root must be absent: {run_root}")
    if str(method["run_uuid"]) != EXPECTED_RUN_UUID:
        raise ValueError("Continuation run UUID differs from the frozen 14n identity")
    if str(run_root) != EXPECTED_RUN_ROOT:
        raise ValueError("Continuation root differs from the frozen 14n identity")
    if str(method["authorization_scope"]) != AUTHORIZATION_SCOPE:
        raise ValueError("Continuation authorization scope differs")
    evidence = {
        "stale_contract_audit": _json(args.stale_audit),
        "o08_diagnostic": _json(args.o08_diagnostic),
        "o09_one_unit_smoke": _json(args.o09_smoke),
        "o13_path_diagnostic": _json(args.o13_path_diagnostic),
        "o13_integration_smoke": _json(args.o13_smoke),
        "path_ownership_audit": _json(args.path_ownership_audit),
        "continuation_dispatch_audit": _json(args.continuation_dispatch_audit),
        "c00_c01_diagnostic": _json(args.c00_c01_diagnostic),
        "tests": _json(args.tests_json),
        "scientific_diff": _json(args.scientific_diff),
        "runtime_estimate": _json(args.runtime_estimate),
        "runtime_state": _json(args.runtime_state),
    }
    required_passes = {
        "stale_contract_audit": bool(evidence["stale_contract_audit"].get("passed")),
        "o08_diagnostic": bool(evidence["o08_diagnostic"].get("passed")),
        "o09_one_unit_smoke": bool(evidence["o09_one_unit_smoke"].get("passed")),
        "o13_path_diagnostic": bool(evidence["o13_path_diagnostic"].get("passed")),
        "o13_integration_smoke": bool(evidence["o13_integration_smoke"].get("passed")),
        "path_ownership_audit": bool(evidence["path_ownership_audit"].get("passed")),
        "continuation_dispatch_audit": bool(
            evidence["continuation_dispatch_audit"].get("passed")
        ),
        "c00_c01_diagnostic": bool(evidence["c00_c01_diagnostic"].get("passed")),
        "tests": bool(evidence["tests"].get("passed")),
        "scientific_diff": bool(evidence["scientific_diff"].get("passed")),
        "runtime_estimate": bool(evidence["runtime_estimate"].get("passed")),
        "runtime_state": bool(evidence["runtime_state"].get("passed")),
    }
    if not all(required_passes.values()):
        raise RuntimeError(f"Required 14n evidence did not pass: {required_passes}")
    preflight = ensure_dir(run_root / "preflight")
    parent_manifest = collect_parent_artifact_manifest(
        parent_root=parent_root, generated_by_source=args.source_commit
    )
    parent_manifest_path = preflight / "parent_artifact_manifest.json"
    atomic_write_json(parent_manifest_path, parent_manifest)
    parent_manifest_sha = sha256_file(parent_manifest_path)
    boundary = validate_parent_boundary(
        manifest=parent_manifest, continuation_root=run_root
    )
    atomic_write_json(preflight / "continuation_boundary_manifest.json", boundary)
    copied_initializations = _copy_initializations(parent_root, run_root)
    resolved = build_continuation_arm_config(
        parent_root=parent_root,
        continuation_root=run_root,
        continuation_run_uuid=EXPECTED_RUN_UUID,
        working_branch="research/v6-rcmf-exp037a-continuation-semantic-dispatch-repair",
        proposed_hard_cap_hours=float(method["proposed_hard_cap_hours"]),
    )
    resolved_path = run_root / "resolved_configs/arm_1d.yaml"
    ensure_dir(resolved_path.parent)
    resolved_path.write_text(yaml.safe_dump(resolved, sort_keys=False), encoding="utf-8")
    config_sha = sha256_file(args.config)
    stages = build_exp037a_continuation_stage_graph()
    stage_ids = [stage.stage_id for stage in stages]
    stage_scope_sha = content_sha256(stage_ids)
    contract = PipelineContract(
        schema_version=str(method["schema_version"]),
        run_uuid=EXPECTED_RUN_UUID,
        source_commit=args.source_commit,
        global_seed=int(method["global_seed"]),
        hard_cap_hours=float(method["proposed_hard_cap_hours"]),
        stages=stages,
        arms={
            "1d": ArmContract(
                arm_id="1d",
                task_conditioned_prompt_profile="full_demo_first_only",
                artifact_prefix="arms/1d",
                run_id=f"{EXPECTED_RUN_UUID}-1d",
            )
        },
        shared_initialization={
            name: str(run_root / "preflight/initialization_snapshots" / name)
            for name in copied_initializations
        },
        metadata={
            "pipeline_config_path": str(method["config_path"]),
            "pipeline_config_sha256": config_sha,
            "canonical_run_root": EXPECTED_RUN_ROOT,
            "strict_stage_identity": True,
            "require_run_bound_authorization": True,
            "authorization_mode": "o07_o08_continuation",
            "authorization_scope": AUTHORIZATION_SCOPE,
            "authorization_version": AUTHORIZATION_VERSION,
            "parent_artifact_manifest_sha256": parent_manifest_sha,
            "parent_run_uuid": PARENT_RUN_UUID,
            "parent_source_commit": PARENT_SOURCE_COMMIT,
            "parent_pipeline_config_sha256": PARENT_CONFIG_SHA256,
            "parent_contract_sha256": PARENT_CONTRACT_SHA256,
            "stage_scope_sha256": stage_scope_sha,
            "maximum_recoverable_attempts_per_stage": int(
                method["maximum_recoverable_attempts_per_stage"]
            ),
            "recoverable_retry_delay_seconds": float(
                method["recoverable_retry_delay_seconds"]
            ),
        },
    )
    contract_path = preflight / "stage_dag.json"
    atomic_write_json(contract_path, contract.as_dict())
    contract_sha = sha256_file(contract_path)
    run_identity = {
        "format": "exp037a_continuation_run_identity_14n_v1",
        "run_uuid": EXPECTED_RUN_UUID,
        "run_root": EXPECTED_RUN_ROOT,
        "root_existed_before_preflight": False,
        "root_prior_entries": [],
        "source_commit": args.source_commit,
        "pipeline_config_path": str(args.config),
        "pipeline_config_sha256": config_sha,
        "contract_sha256": contract_sha,
        "stage_scope_sha256": stage_scope_sha,
    }
    parent_identity = {
        "format": "exp037a_parent_run_identity_14n_v1",
        "run_uuid": PARENT_RUN_UUID,
        "run_root": str(parent_root),
        "source_commit": PARENT_SOURCE_COMMIT,
        "pipeline_config_sha256": PARENT_CONFIG_SHA256,
        "contract_sha256": PARENT_CONTRACT_SHA256,
        "boundary": "after_O07_before_O08",
        "parent_root_immutable": True,
        "parent_o08_partial_outputs_used": False,
    }
    count_contract = {
        "format": "exp037a_count_ownership_contract_14n_v1",
        "three_demo": scoreable_count_contract(
            arm_id="3d",
            policy=EXACT_REPRODUCTION,
            expected_train=366,
            expected_heldout=98,
        ),
        "one_demo": scoreable_count_contract(
            arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
        ),
        "one_demo_observed_parent_counts_are_not_success_criteria": True,
        "fixed_memory_counts": {
            "model_training": 401,
            "heldout_parent": 98,
            "deployment": 499,
        },
    }
    scientific_invariants = {
        "format": "exp037a_continuation_scientific_invariants_14n_v1",
        "parent_o00_o07_behavior_change": 0,
        "three_demo_behavior_change": 0,
        "prompt_profile": "full_demo_first_only",
        "causal_panel": {"initial": 256, "maximum": 499, "minimum_per_label": 40},
        "memory_counts": {"model_training": 401, "heldout_parent": 98, "deployment": 499},
        "writer_reader_epochs": 2,
        "selector_changed": False,
        "generation_changed": False,
        "losses_changed": False,
        "evaluation_changed": False,
        "count_ownership_repair_only": True,
        "path_ownership_repair_only": True,
        "o00_o12_scientific_behavior_change": 0,
        "state_query_shuffle_definition_changed": False,
        "continuation_dispatch_repair_only": True,
    }
    restart_plan = {
        "format": "exp037a_continuation_restart_plan_14n_v1",
        "C00_C01": "cheap_repeatable_read_only_parent_validation",
        "O08": "rebuild_from_scratch_under_continuation_source",
        "O09_O10": "strict_atomic_checkpoint_resume_contract",
        "completed_stage_skip": "strict_new_run_identity_and_hash_validation_only",
        "parent_artifacts_recomputed": False,
        "parent_artifacts_mutated": False,
        "parent_completion_files_copied_into_continuation": False,
    }
    runtime_preflight = {
        **evidence["runtime_estimate"],
        "format": "exp037a_continuation_runtime_preflight_14n_v1",
        "run_uuid": EXPECTED_RUN_UUID,
        "run_root": EXPECTED_RUN_ROOT,
        "source_commit": args.source_commit,
        "recommended_hard_cap_hours": float(
            method["proposed_hard_cap_hours"]
        ),
        "authorization_status": "NOT_AUTHORIZED",
        "authorized_to_launch": False,
        "explicit_user_approval_required": True,
    }
    authorization_request = {
        "format": "exp037a_continuation_authorization_request_14n_v1",
        "authorization_version": AUTHORIZATION_VERSION,
        "authorization_status": "NOT_AUTHORIZED",
        "authorized": False,
        "authorized_to_launch": False,
        "granted_by_user": False,
        "continuation_authorized": False,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": False,
        "previous_200_hour_authorization_inherited": False,
        "parent_14k_authorization_inherited": False,
        "source_commit": args.source_commit,
        "run_uuid": EXPECTED_RUN_UUID,
        "run_root": EXPECTED_RUN_ROOT,
        "pipeline_config_sha256": config_sha,
        "contract_sha256": contract_sha,
        "parent_artifact_manifest_sha256": parent_manifest_sha,
        "parent_run_uuid": PARENT_RUN_UUID,
        "stage_scope_sha256": stage_scope_sha,
        "hard_cap_hours": float(method["proposed_hard_cap_hours"]),
        "scope": AUTHORIZATION_SCOPE,
        "explicit_user_approval_required": True,
    }
    payloads = {
        "run_identity.json": run_identity,
        "parent_run_identity.json": parent_identity,
        "count_ownership_contract.json": count_contract,
        "scientific_invariants.json": scientific_invariants,
        "stale_constant_audit.json": evidence["stale_contract_audit"],
        "scientific_diff.json": evidence["scientific_diff"],
        "o08_diagnostic.json": evidence["o08_diagnostic"],
        "o09_one_unit_smoke.json": evidence["o09_one_unit_smoke"],
        "o13_path_diagnostic.json": evidence["o13_path_diagnostic"],
        "o13_integration_smoke.json": evidence["o13_integration_smoke"],
        "path_ownership_audit.json": evidence["path_ownership_audit"],
        "continuation_dispatch_audit.json": evidence[
            "continuation_dispatch_audit"
        ],
        "c00_c01_diagnostic.json": evidence["c00_c01_diagnostic"],
        "runtime_estimate.json": evidence["runtime_estimate"],
        "runtime_preflight.json": runtime_preflight,
        "runtime_state.json": evidence["runtime_state"],
        "restart_plan.json": restart_plan,
        "authorization_request.json": authorization_request,
        "tests.json": evidence["tests"],
        "initialization_copies.json": copied_initializations,
    }
    for name, payload in payloads.items():
        atomic_write_json(preflight / name, payload)
    index_paths = sorted(
        [path for path in preflight.rglob("*") if path.is_file()]
        + [resolved_path]
    )
    artifact_index = {
        "format": "exp037a_continuation_preflight_artifact_index_14n_v1",
        "run_uuid": EXPECTED_RUN_UUID,
        "artifacts": [file_identity(path, run_root) for path in index_paths],
        "runtime_authorization_present": False,
        "scientific_stages_present": False,
        "formal_attempts_present": False,
    }
    atomic_write_json(preflight / "artifact_index.json", artifact_index)
    approval_checks = {
        "source_is_frozen": len(args.source_commit) == 40,
        "parent_identity_exact": parent_identity["source_commit"] == PARENT_SOURCE_COMMIT,
        "parent_closure_valid": bool(boundary["passed"]),
        "parent_o08_partials_excluded": not bool(boundary["parent_o08_partial_outputs_used"]),
        "continuation_graph_exact": stage_ids[0:3]
        == [
            "C00_parent_run_evidence_import",
            "C01_o07_o08_boundary_validation",
            "O08_zero_cache_and_training_units",
        ]
        and stage_ids[-1] == "F03_final_report_and_handoff",
        "no_upstream_stages": not any(
            stage_id.startswith(("S", "D"))
            or stage_id.startswith("O0") and int(stage_id[1:3]) < 8
            for stage_id in stage_ids
        ),
        "count_contract_dynamic_1d": count_contract["one_demo"]["policy"]
        == SEALED_UPSTREAM_OUTCOMES,
        "all_bounded_evidence_passed": all(required_passes.values()),
        "o13_path_repair_validated": all(
            required_passes[name]
            for name in (
                "o13_path_diagnostic",
                "o13_integration_smoke",
                "path_ownership_audit",
            )
        ),
        "semantic_continuation_dispatch_validated": all(
            required_passes[name]
            for name in ("continuation_dispatch_audit", "c00_c01_diagnostic")
        ),
        "authorization_false": authorization_request["authorized"] is False,
        "runtime_authorization_absent": not (run_root / "runtime_authorization.json").exists(),
        "formal_stages_absent": not (run_root / "stages").exists(),
        "formal_attempts_absent": not (run_root / "attempts.jsonl").exists(),
    }
    summary = {
        "format": "exp037a_continuation_preflight_summary_14n_v1",
        "decision": "READY_FOR_14N_AUTONOMOUS_LAUNCH",
        "launch_source_sha": args.source_commit,
        "run_uuid": EXPECTED_RUN_UUID,
        "run_root": EXPECTED_RUN_ROOT,
        "pipeline_config_sha256": config_sha,
        "contract_sha256": contract_sha,
        "parent_artifact_manifest_sha256": parent_manifest_sha,
        "artifact_index_sha256": sha256_file(preflight / "artifact_index.json"),
        "authorization_request_sha256": sha256_file(
            preflight / "authorization_request.json"
        ),
        "stage_scope": stage_ids,
        "stage_scope_sha256": stage_scope_sha,
        "approval_checks": approval_checks,
        "authorization_status": "NOT_AUTHORIZED",
        "authorized_to_launch": False,
        "explicit_user_approval_required": True,
        "no_long_continuation_run_launched": True,
        "passed": all(approval_checks.values()),
    }
    if not summary["passed"]:
        raise RuntimeError(f"Continuation preflight failed: {approval_checks}")
    atomic_write_json(preflight / "preflight_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
