from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    write_stage_manifest,
)
from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.manifests import (
    STAGE_IDENTITY_KEYS,
    stage_identity_payload,
)
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.scheduler import EventDrivenScheduler
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.prepare_rcmf_reproducible_pipeline_14h import (
    AUTHORIZATION_SCOPE,
    AUTHORIZATION_VERSION,
    PROPOSED_HARD_CAP_HOURS,
    RUN_ROOT,
    RUN_UUID,
    executable_source_manifest,
    stage_manifest_producer_audit,
    static_invariants,
)
from scripts.run_rcmf_reproducible_stage_14b import (
    _failure_payload,
    _verified_stage_identity,
)


CONFIG = Path("configs/pipeline/rcmf_appworld_repro_14h.yaml")
SOURCE = "a" * 40
CONFIG_SHA = "b" * 64
CONTRACT_SHA = "c" * 64


def _identity(run_root: Path, stage_id: str) -> dict[str, str]:
    return stage_identity_payload(
        source_commit=SOURCE,
        run_uuid=RUN_UUID,
        run_root=run_root,
        pipeline_config_sha256=CONFIG_SHA,
        contract_sha256=CONTRACT_SHA,
        stage_id=stage_id,
        attempt_id=f"{stage_id}-attempt-1",
        require_complete=True,
    )


def _write_real_manifest(run_root: Path, stage_id: str) -> Path:
    stage_dir = run_root / "stages" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    stage = next(
        row for row in build_exp037a_stage_graph() if row.stage_id == stage_id
    )
    for dependency in stage.dependencies:
        dependency_path = run_root / "stages" / dependency / "completion.json"
        dependency_path.parent.mkdir(parents=True, exist_ok=True)
        if not dependency_path.exists():
            atomic_write_json(dependency_path, {"passed": True})
    return write_stage_manifest(
        stage_id=stage_id,
        stage_dir=stage_dir,
        stage_identity=_identity(run_root, stage_id),
        arm=stage.arm,
        prompt_profile=(
            "full_demo"
            if stage.arm == "3d"
            else "full_demo_first_only"
            if stage.arm == "1d"
            else None
        ),
        result={"passed": True, "synthetic": True},
        command=list(stage.command),
        started_utc="2026-09-04T00:00:00Z",
        elapsed_seconds=0.0,
        run_root=run_root,
    )


def _strict_validate(stage_dir: Path, run_root: Path) -> dict[str, object]:
    return validate_stage_completion(
        stage_dir,
        SOURCE,
        expected_run_uuid=RUN_UUID,
        expected_pipeline_config_sha256=CONFIG_SHA,
        expected_contract_sha256=CONTRACT_SHA,
        expected_run_root=run_root,
    )


def test_real_writer_real_validator_round_trip(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    manifest_path = _write_real_manifest(run_root, "S00_environment_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {key: manifest[key] for key in STAGE_IDENTITY_KEYS} == _identity(
        run_root, "S00_environment_manifest"
    )
    validation = _strict_validate(manifest_path.parent, run_root)
    assert validation["passed"]
    assert all(validation["checks"].values())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_commit", "d" * 40),
        ("run_uuid", "foreign-run"),
        ("run_root", "/foreign/root"),
        ("pipeline_config_sha256", "d" * 64),
        ("contract_sha256", "e" * 64),
        ("stage_id", "S01_authoritative_corpus"),
    ],
)
def test_each_stage_identity_mutation_fails(
    tmp_path: Path, field: str, value: str
) -> None:
    run_root = tmp_path / "run"
    manifest_path = _write_real_manifest(run_root, "S00_environment_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    atomic_write_json(manifest_path, manifest)
    assert not _strict_validate(manifest_path.parent, run_root)["passed"]


def test_output_hash_mutation_fails(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    manifest_path = _write_real_manifest(run_root, "S00_environment_manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"][0]["sha256"] = "0" * 64
    atomic_write_json(manifest_path, manifest)
    assert not _strict_validate(manifest_path.parent, run_root)["passed"]


def test_dependency_completion_hash_mutation_fails(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    first = _write_real_manifest(run_root, "S00_environment_manifest")
    atomic_write_json(first.parent / "completion.json", {"passed": True})
    second = _write_real_manifest(run_root, "S01_authoritative_corpus")
    assert _strict_validate(second.parent, run_root)["passed"]
    atomic_write_json(first.parent / "completion.json", {"passed": False})
    validation = _strict_validate(second.parent, run_root)
    assert not validation["passed"]
    assert not validation["checks"]["input_completion_hashes"]


def test_all_formal_stages_round_trip_through_real_writer(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    stages = build_exp037a_stage_graph()
    results = {}
    for stage in stages:
        manifest_path = _write_real_manifest(run_root, stage.stage_id)
        result = _strict_validate(manifest_path.parent, run_root)
        results[stage.stage_id] = result["passed"]
        atomic_write_json(
            manifest_path.parent / "completion.json", {"passed": True}
        )
    assert len(results) == 60
    assert all(results.values())


def test_success_and_failure_identity_schema_parity(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    manifest_path = _write_real_manifest(run_root, "S00_environment_manifest")
    success = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = _identity(run_root, "S00_environment_manifest")
    failure = _failure_payload(identity, RuntimeError("test"), recoverable=False)
    assert set(STAGE_IDENTITY_KEYS) <= set(success)
    assert set(STAGE_IDENTITY_KEYS) <= set(failure)
    assert {key: success[key] for key in STAGE_IDENTITY_KEYS} == {
        key: failure[key] for key in STAGE_IDENTITY_KEYS
    }


def test_formal_runner_fails_closed_on_incomplete_scheduler_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    run_root = tmp_path / "run"
    args = argparse.Namespace(
        config=config_path,
        run_root=run_root,
        source_commit=SOURCE,
        stage="S00_environment_manifest",
    )
    config = {
        "pipeline": {
            "run_uuid": RUN_UUID,
            "roots": {"run_root": str(run_root)},
            "strict_stage_identity": True,
        }
    }
    for key in (
        "RCMF_PIPELINE_RUN_UUID",
        "RCMF_PIPELINE_RUN_ROOT",
        "RCMF_PIPELINE_CONFIG_SHA256",
        "RCMF_PIPELINE_CONTRACT_SHA256",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(PermissionError, match="identity is incomplete"):
        _verified_stage_identity(args, config)


def test_14h_science_and_stage_producer_contract_are_frozen() -> None:
    config = load_resolved(CONFIG)
    invariants, checks = static_invariants(config, Path(RUN_ROOT))
    assert all(checks.values())
    assert invariants["scientific_changes_from_r3"] == 0
    assert invariants["causal_panel"] == {
        "initial_state_count": 256,
        "maximum_state_count": 499,
        "minimum_per_label": 40,
    }
    assert invariants["prompt_profiles"] == {
        "3d": "full_demo",
        "1d": "full_demo_first_only",
    }
    audit = stage_manifest_producer_audit()
    assert audit["passed"]
    assert len(audit["formal_stages"]) == 60
    sources = executable_source_manifest()
    assert len(sources["files"]) == 17
    assert sources["scientific_configuration_changes_from_r3"] == 0


def test_failed_14g_authorization_cannot_authorize_14h(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    run_root = tmp_path / "run-14h"
    contract = PipelineContract(
        schema_version="test-14h",
        run_uuid=RUN_UUID,
        source_commit=SOURCE,
        global_seed=25101,
        hard_cap_hours=PROPOSED_HARD_CAP_HOURS,
        stages=(StageSpec("S00", "shared", command=("ignored",)),),
        arms={
            "3d": ArmContract("3d", "full_demo", "arms/3d", "3d"),
            "1d": ArmContract(
                "1d", "full_demo_first_only", "arms/1d", "1d"
            ),
        },
        metadata={
            "authorization_scope": AUTHORIZATION_SCOPE,
            "authorization_version": AUTHORIZATION_VERSION,
        },
    )
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract.as_dict())
    stale = {
        "authorization_status": "AUTHORIZED",
        "authorization_version": "exp037a_run_bound_authorization_14g_v1",
        "authorized": True,
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": "rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001",
        "run_root": str(tmp_path / "run-14g"),
        "source_commit": "f" * 40,
        "contract_sha256": "0" * 64,
        "pipeline_config_sha256": "1" * 64,
        "hard_cap_hours": 120.0,
        "scope": AUTHORIZATION_SCOPE,
    }
    with pytest.raises(PermissionError, match="Run-bound explicit"):
        validate_explicit_authorization(
            stale,
            contract,
            run_root=run_root,
            contract_path=contract_path,
            pipeline_config_path=config_path,
        )


def test_scheduler_reruns_manifest_rejected_by_strict_validator(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    run_root = tmp_path / "run"
    stage = StageSpec(
        "S00_environment_manifest", "shared", command=("ignored",)
    )
    contract = PipelineContract(
        schema_version="r7-scheduler-test",
        run_uuid=RUN_UUID,
        source_commit=SOURCE,
        global_seed=25101,
        hard_cap_hours=1.0,
        stages=(stage,),
        arms={},
        metadata={
            "require_run_bound_authorization": True,
            "strict_stage_identity": True,
            "pipeline_config_sha256": sha256_file(config_path),
            "canonical_run_root": str(run_root),
        },
    )
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract.as_dict())
    contract_sha = sha256_file(contract_path)
    atomic_write_json(
        run_root / "runtime_authorization.json",
        {
            "authorization_status": "AUTHORIZED",
            "authorized": True,
            "granted_by_user": True,
            "full_pipeline_authorized": True,
            "d06_or_later_authorized": True,
            "one_demo_authorized": True,
            "previous_200_hour_authorization_inherited": False,
            "authorization_source": "explicit_run_bound_user_authorization",
            "run_uuid": RUN_UUID,
            "run_root": str(run_root),
            "source_commit": SOURCE,
            "contract_sha256": contract_sha,
            "pipeline_config_sha256": sha256_file(config_path),
            "hard_cap_hours": 1.0,
            "recommended_hard_cap_hours": 1.0,
        },
    )
    calls: list[str] = []

    def runner(stage, command, stage_dir, environment):
        del command
        calls.append(stage.stage_id)
        write_stage_manifest(
            stage_id=stage.stage_id,
            stage_dir=stage_dir,
            stage_identity=stage_identity_payload(
                source_commit=SOURCE,
                run_uuid=environment["RCMF_PIPELINE_RUN_UUID"],
                run_root=environment["RCMF_PIPELINE_RUN_ROOT"],
                pipeline_config_sha256=environment[
                    "RCMF_PIPELINE_CONFIG_SHA256"
                ],
                contract_sha256=environment[
                    "RCMF_PIPELINE_CONTRACT_SHA256"
                ],
                stage_id=stage.stage_id,
                attempt_id=environment["RCMF_PIPELINE_ATTEMPT_ID"],
                require_complete=True,
            ),
            arm="shared",
            prompt_profile=None,
            result={"passed": True},
            command=["ignored"],
            started_utc="2026-09-04T00:00:00Z",
            elapsed_seconds=0.0,
            run_root=run_root,
        )
        return 0

    scheduler = EventDrivenScheduler(
        contract,
        run_root,
        python_executable="python",
        config_path=config_path,
        runner=runner,
        contract_sha256=contract_sha,
    )
    assert scheduler.run().status == "complete"
    assert scheduler.run().status == "complete"
    assert calls == ["S00_environment_manifest"]
    manifest_path = run_root / "stages/S00_environment_manifest/output_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_uuid"] = "tampered-run"
    atomic_write_json(manifest_path, manifest)
    assert scheduler.run().status == "complete"
    assert calls == ["S00_environment_manifest", "S00_environment_manifest"]
