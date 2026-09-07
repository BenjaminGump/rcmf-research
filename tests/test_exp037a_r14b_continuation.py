from __future__ import annotations

import json
from pathlib import Path

import pytest

import rcmf.benchmarks.appworld.continuation_14l as continuation
import scripts.run_rcmf_reproducible_pipeline_14b as launcher

from rcmf.benchmarks.appworld.continuation_14l import (
    PARENT_CONFIG_SHA256,
    PARENT_CONTRACT_SHA256,
    PARENT_O08_PARTIAL_RELATIVE_PATHS,
    PARENT_REQUIRED_STAGES,
    PARENT_RUN_UUID,
    PARENT_SOURCE_COMMIT,
    execute_parent_import,
    validate_parent_artifact_manifest,
)
from rcmf.benchmarks.appworld.reproducible_config_14b import build_arm_runtime_config
from rcmf.benchmarks.appworld.reproducible_stages_14b import _final_stage
from rcmf.pipeline.authorization import (
    validate_explicit_authorization,
    validate_runtime_authorization,
)
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.stage_graph import build_exp037a_continuation_stage_graph
from rcmf.utils.serialization import sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved


SCOPE = "continue_from_sealed_14k_o07_boundary_through_o19_and_final_reporting"


def _contract(tmp_path: Path, *, continuation: bool = True) -> tuple[PipelineContract, Path, Path]:
    config = tmp_path / "pipeline.yaml"
    config.write_text("pipeline: continuation\n", encoding="utf-8")
    manifest_sha = "a" * 64
    scope_sha = content_sha256([stage.stage_id for stage in build_exp037a_continuation_stage_graph()])
    metadata = {
        "pipeline_config_path": str(config),
        "authorization_scope": SCOPE,
        "authorization_version": "exp037a_run_bound_authorization_14l_v1",
        "authorization_mode": "o07_o08_continuation" if continuation else "full_pipeline",
        "parent_artifact_manifest_sha256": manifest_sha,
        "parent_run_uuid": PARENT_RUN_UUID,
        "stage_scope_sha256": scope_sha,
    }
    contract = PipelineContract(
        schema_version="test",
        run_uuid="continuation-test",
        source_commit="b" * 40,
        global_seed=25101,
        hard_cap_hours=32,
        stages=build_exp037a_continuation_stage_graph(),
        arms={
            "1d": ArmContract(
                arm_id="1d",
                task_conditioned_prompt_profile="full_demo_first_only",
                artifact_prefix="arms/1d",
                run_id="continuation-test-1d",
            )
        },
        metadata=metadata,
    )
    contract_path = tmp_path / "stage_dag.json"
    contract_path.write_text(json.dumps(contract.as_dict()), encoding="utf-8")
    return contract, config, contract_path


def _authorization(contract: PipelineContract, config: Path, contract_path: Path, run_root: Path) -> dict[str, object]:
    return {
        "authorization_version": "exp037a_run_bound_authorization_14l_v1",
        "authorization_status": "AUTHORIZED",
        "authorized": True,
        "granted_by_user": True,
        "continuation_authorized": True,
        "full_pipeline_authorized": False,
        "d06_or_later_authorized": False,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": contract.run_uuid,
        "run_root": str(run_root),
        "source_commit": contract.source_commit,
        "pipeline_config_sha256": sha256_file(config),
        "contract_sha256": sha256_file(contract_path),
        "hard_cap_hours": 32,
        "scope": SCOPE,
        "parent_artifact_manifest_sha256": "a" * 64,
        "parent_run_uuid": PARENT_RUN_UUID,
        "stage_scope_sha256": str(contract.metadata["stage_scope_sha256"]),
    }


def test_continuation_graph_is_exact_and_o08_depends_on_boundary() -> None:
    stages = build_exp037a_continuation_stage_graph()
    ids = [stage.stage_id for stage in stages]
    assert ids[:2] == [
        "C00_parent_run_evidence_import",
        "C01_o07_o08_boundary_validation",
    ]
    assert ids[2] == "O08_zero_cache_and_training_units"
    assert ids[-4:] == [
        "F00_two_arm_paired_analysis",
        "F01_portability_validation",
        "F02_git_safe_audit_export",
        "F03_final_report_and_handoff",
    ]
    assert not any(stage_id.startswith(("S", "D")) for stage_id in ids)
    assert not any(stage_id.startswith("O0") and int(stage_id[1:3]) < 8 for stage_id in ids)
    stage_map = {stage.stage_id: stage for stage in stages}
    assert stage_map["O08_zero_cache_and_training_units"].dependencies == (
        "C01_o07_o08_boundary_validation",
    )


def test_continuation_authorization_is_scope_bound(tmp_path: Path) -> None:
    contract, config, contract_path = _contract(tmp_path)
    payload = _authorization(contract, config, contract_path, tmp_path)
    checks = validate_explicit_authorization(
        payload,
        contract,
        run_root=tmp_path,
        contract_path=contract_path,
        pipeline_config_path=config,
    )
    assert all(checks.values())
    for key, value in (
        ("parent_run_uuid", "wrong"),
        ("parent_artifact_manifest_sha256", "wrong"),
        ("stage_scope_sha256", "wrong"),
        ("scope", "complete_fresh_3d_then_conditional_fresh_1d_and_final_reporting"),
    ):
        changed = dict(payload)
        changed[key] = value
        with pytest.raises(PermissionError):
            validate_explicit_authorization(
                changed,
                contract,
                run_root=tmp_path,
                contract_path=contract_path,
                pipeline_config_path=config,
            )


def test_old_full_authorization_cannot_authorize_continuation(tmp_path: Path) -> None:
    contract, config, contract_path = _contract(tmp_path)
    payload = _authorization(contract, config, contract_path, tmp_path)
    payload.update(
        {
            "continuation_authorized": False,
            "full_pipeline_authorized": True,
            "d06_or_later_authorized": True,
        }
    )
    with pytest.raises(PermissionError):
        validate_explicit_authorization(
            payload,
            contract,
            run_root=tmp_path,
            contract_path=contract_path,
            pipeline_config_path=config,
        )


def _parent_manifest(parent_root: Path, artifact: Path) -> dict[str, object]:
    row = {
        "path": str(artifact.resolve()),
        "size_bytes": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
        "roles": ["test"],
    }
    return {
        "parent": {
            "run_uuid": PARENT_RUN_UUID,
            "run_root": str(parent_root.resolve()),
            "source_commit": PARENT_SOURCE_COMMIT,
            "pipeline_config_sha256": PARENT_CONFIG_SHA256,
            "contract_sha256": PARENT_CONTRACT_SHA256,
        },
        "required_stages": list(PARENT_REQUIRED_STAGES),
        "artifact_count": 1,
        "artifacts": [row],
        "artifact_closure_sha256": content_sha256([row]),
    }


def test_parent_manifest_rejects_identity_tamper_and_o08_partial(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    artifact = parent / "sealed.json"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = _parent_manifest(parent, artifact)
    assert validate_parent_artifact_manifest(manifest, parent_root=parent)["passed"]
    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hashes"):
        validate_parent_artifact_manifest(manifest, parent_root=parent)
    partial = parent / PARENT_O08_PARTIAL_RELATIVE_PATHS[0]
    partial.parent.mkdir(parents=True)
    partial.write_text("partial\n", encoding="utf-8")
    manifest = _parent_manifest(parent, partial)
    with pytest.raises(ValueError, match="parent_o08_partials_excluded"):
        validate_parent_artifact_manifest(manifest, parent_root=parent)


def _summary(task_ids: list[str], successes: list[str]) -> dict[str, object]:
    return {
        "ordered_task_ids": task_ids,
        "success_count": len(successes),
        "success_ids": successes,
        "success_by_task": {task_id: task_id in successes for task_id in task_ids},
    }


def test_cross_source_final_analysis_rejects_dev_task_order_mismatch(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    run_root = tmp_path / "continuation"
    parent_summaries = parent / "evaluation/common_one_demo_dev/summaries"
    current_summaries = run_root / "evaluation/common_one_demo_dev/summaries"
    gate_path = parent / "gate/three_demo_reproduction_gate.json"
    parent_summaries.mkdir(parents=True)
    current_summaries.mkdir(parents=True)
    gate_path.parent.mkdir(parents=True)
    gate_path.write_text(
        json.dumps({"decision": "THREE_DEMO_REPRODUCTION_PASS", "continue_to_one_demo": True}),
        encoding="utf-8",
    )
    for name in ("B0_1D", "FRESH3D_C_1DDEPLOY", "FRESH3D_S_1DDEPLOY"):
        (parent_summaries / f"{name}.json").write_text(
            json.dumps(_summary(["a", "b"], ["a"])), encoding="utf-8"
        )
    (current_summaries / "FRESH1D_C_1DDEPLOY.json").write_text(
        json.dumps(_summary(["a", "b"], ["b"])), encoding="utf-8"
    )
    (current_summaries / "FRESH1D_S_1DDEPLOY.json").write_text(
        json.dumps(_summary(["b", "a"], ["b"])), encoding="utf-8"
    )
    config = {
        "pipeline": {
            "run_uuid": "continuation-test",
            "continuation": {
                "parent_root": str(parent),
                "parent_run_uuid": PARENT_RUN_UUID,
                "parent_source_commit": PARENT_SOURCE_COMMIT,
                "parent_artifact_manifest_path": str(tmp_path / "manifest.json"),
            },
        }
    }
    with pytest.raises(ValueError, match="task identities or ordering"):
        _final_stage("F00_two_arm_paired_analysis", config, run_root)


def test_14l_missing_count_policy_fails_closed(tmp_path: Path) -> None:
    config = load_resolved(
        Path("configs/pipeline/rcmf_appworld_continuation_14l.yaml")
    )
    config["arms"]["1d"].pop("scoreable_count_policy")
    with pytest.raises(ValueError, match="policy is missing"):
        build_arm_runtime_config(config, tmp_path / "run", "1d")

def test_continuation_authorization_cannot_authorize_full_pipeline(
    tmp_path: Path,
) -> None:
    contract, config, contract_path = _contract(tmp_path, continuation=False)
    payload = _authorization(contract, config, contract_path, tmp_path)
    with pytest.raises(PermissionError):
        validate_explicit_authorization(
            payload,
            contract,
            run_root=tmp_path,
            contract_path=contract_path,
            pipeline_config_path=config,
        )


def test_launcher_preserves_continuation_scope_in_runtime_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, config, contract_path = _contract(tmp_path)
    preflight = tmp_path / "preflight"
    preflight.mkdir()
    (preflight / "stage_dag.json").write_text(
        contract_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (preflight / "preflight_summary.json").write_text(
        json.dumps(
            {
                "launch_source_sha": contract.source_commit,
                "approval_checks": {"ready": True},
                "explicit_user_approval_required": True,
            }
        ),
        encoding="utf-8",
    )
    (preflight / "runtime_preflight.json").write_text(
        json.dumps({"recommended_hard_cap_hours": 32}),
        encoding="utf-8",
    )
    authorization_path = preflight / "explicit_user_authorization.json"
    authorization_path.write_text(
        json.dumps(_authorization(contract, config, contract_path, tmp_path)),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "_head", lambda: contract.source_commit)
    monkeypatch.setattr(launcher, "_status", lambda: "")
    monkeypatch.setattr(launcher.os.path, "ismount", lambda _: True)
    runtime = launcher._authorize(contract_path, tmp_path, authorization_path)
    assert runtime["continuation_authorized"] is True
    assert runtime["full_pipeline_authorized"] is False
    assert runtime["d06_or_later_authorized"] is False
    assert runtime["one_demo_authorized"] is True
    checks = validate_runtime_authorization(
        runtime,
        contract,
        run_root=tmp_path,
        contract_sha256=sha256_file(contract_path),
        pipeline_config_path=config,
    )
    assert all(checks.values())


def test_c00_rebuilds_and_seals_runtime_parent_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "continuation"
    preflight = run_root / "preflight"
    preflight.mkdir(parents=True)
    parent_root = tmp_path / "parent"
    parent_root.mkdir()
    manifest = {
        "format": "fixture",
        "parent": {"run_root": str(parent_root)},
        "artifacts": [],
    }
    manifest_path = preflight / "parent_artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (preflight / "stage_dag.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "parent_artifact_manifest_sha256": sha256_file(manifest_path)
                }
            }
        ),
        encoding="utf-8",
    )
    (run_root / "runtime_authorization.json").write_text(
        json.dumps({"source_commit": "c" * 40}), encoding="utf-8"
    )
    monkeypatch.setattr(
        continuation,
        "validate_parent_artifact_manifest",
        lambda payload, parent_root: {"passed": payload == manifest},
    )
    monkeypatch.setattr(
        continuation,
        "collect_parent_artifact_manifest",
        lambda parent_root, generated_by_source: manifest,
    )
    result = execute_parent_import(
        {
            "pipeline": {
                "continuation": {
                    "parent_root": str(parent_root),
                    "parent_artifact_manifest_path": str(manifest_path),
                }
            }
        },
        run_root,
    )
    runtime_manifest = (
        run_root / "continuation/parent_artifact_manifest_runtime.json"
    )
    assert result["runtime_manifest_exact_preflight_match"] is True
    assert json.loads(runtime_manifest.read_text(encoding="utf-8")) == manifest


def test_continuation_config_resolves_dynamic_counts_and_sealed_upstream_flags(
    tmp_path: Path,
) -> None:
    config = load_resolved(
        Path("configs/pipeline/rcmf_appworld_continuation_14l.yaml")
    )
    arm = config["arms"]["1d"]
    assert arm["prompt_dependent"] == {
        "rebuild_state_representations": False,
        "rebuild_selected_memories": False,
        "rebuild_paired_outcomes": False,
        "rebuild_policy_teachers": False,
        "rebuild_zero_cache": True,
        "rebuild_training_units": True,
    }
    resolved = build_arm_runtime_config(config, tmp_path / "run", "1d")
    settings = resolved["stage_c_9a"]
    assert settings["scoreable_count_contract"] == {
        "format": "exp037a_scoreable_count_contract_14l_v1",
        "arm_id": "1d",
        "policy": "sealed_upstream_outcomes",
    }
    assert "scoreable_train_state_count" not in settings["expected"]
    assert "scoreable_heldout_state_count" not in settings["expected"]
    assert settings["expected"]["complete_train_memory_count"] == 401
    assert settings["expected"]["heldout_memory_count"] == 98
    assert resolved["stage_c_7hr"]["panel"]["scoreable_count_policy"] == (
        "sealed_upstream_outcomes"
    )
    assert "post_d06_reproduction_expectation" not in resolved["stage_c_7hr"]["panel"]

@pytest.mark.parametrize(
    "field,value,failed_check",
    [
        ("run_uuid", "wrong", "parent_run_uuid"),
        ("run_root", "wrong", "parent_run_root"),
        ("source_commit", "0" * 40, "parent_source"),
        ("pipeline_config_sha256", "0" * 64, "parent_config"),
        ("contract_sha256", "0" * 64, "parent_contract"),
    ],
)
def test_parent_manifest_rejects_run_identity_mismatch(
    tmp_path: Path, field: str, value: str, failed_check: str
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    artifact = parent / "sealed.json"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = _parent_manifest(parent, artifact)
    manifest["parent"][field] = value
    with pytest.raises(ValueError, match=failed_check):
        validate_parent_artifact_manifest(manifest, parent_root=parent)