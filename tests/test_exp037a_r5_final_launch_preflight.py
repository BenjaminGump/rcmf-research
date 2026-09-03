from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.resume import StageStateStore
from rcmf.pipeline.scheduler import EventDrivenScheduler
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import (
    evaluate_d06_reproduction_gate,
    validate_stage_completion,
)
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _path_identity,
    _validate_deployment_field,
)
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.prepare_rcmf_reproducible_pipeline_14g import (
    AUTHORIZATION_SCOPE,
    AUTHORIZATION_VERSION,
    PROPOSED_HARD_CAP_HOURS,
    RUN_ROOT,
    RUN_UUID,
    runtime_tables,
    static_invariants,
)
from scripts.run_rcmf_reproducible_pipeline_14b import (
    _authorize as authorize_pipeline,
)


CONFIG = Path("configs/pipeline/rcmf_appworld_repro_14g.yaml")


def _strict_contract(
    config_path: Path, run_root: Path, stages: tuple[StageSpec, ...]
) -> PipelineContract:
    return PipelineContract(
        schema_version="test-14g",
        run_uuid=RUN_UUID,
        source_commit="a" * 40,
        global_seed=25101,
        hard_cap_hours=PROPOSED_HARD_CAP_HOURS,
        stages=stages,
        arms={
            "3d": ArmContract("3d", "full_demo", "arms/3d", "3d"),
            "1d": ArmContract(
                "1d", "full_demo_first_only", "arms/1d", "1d"
            ),
        },
        metadata={
            "pipeline_config_sha256": sha256_file(config_path),
            "canonical_run_root": str(run_root.resolve()),
            "authorization_scope": AUTHORIZATION_SCOPE,
            "authorization_version": AUTHORIZATION_VERSION,
            "pipeline_config_path": str(config_path),
            "require_run_bound_authorization": True,
            "strict_stage_identity": True,
        },
    )


def _valid_authorization(
    contract: PipelineContract,
    run_root: Path,
    contract_sha: str,
    config_path: Path,
) -> dict[str, object]:
    return {
        "authorization_status": "AUTHORIZED",
        "authorization_version": AUTHORIZATION_VERSION,
        "authorized": True,
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "authorization_source": "explicit_run_bound_user_authorization",
        "run_uuid": contract.run_uuid,
        "run_root": str(run_root.resolve()),
        "source_commit": contract.source_commit,
        "contract_sha256": contract_sha,
        "pipeline_config_sha256": sha256_file(config_path),
        "hard_cap_hours": contract.hard_cap_hours,
        "recommended_hard_cap_hours": contract.hard_cap_hours,
        "scope": AUTHORIZATION_SCOPE,
    }


def _write_output(
    stage: StageSpec,
    stage_dir: Path,
    source_commit: str,
    environment: dict[str, str],
) -> None:
    payload_path = stage_dir / "payload.json"
    atomic_write_json(payload_path, {"stage": stage.stage_id})
    atomic_write_json(
        stage_dir / "output_manifest.json",
        {
            "format": "test-14g",
            "stage_id": stage.stage_id,
            "source_commit": source_commit,
            "run_uuid": environment["RCMF_PIPELINE_RUN_UUID"],
            "run_root": environment["RCMF_PIPELINE_RUN_ROOT"],
            "pipeline_config_sha256": environment[
                "RCMF_PIPELINE_CONFIG_SHA256"
            ],
            "contract_sha256": environment[
                "RCMF_PIPELINE_CONTRACT_SHA256"
            ],
            "passed": True,
            "outputs": [
                {"path": "payload.json", "sha256": sha256_file(payload_path)}
            ],
        },
    )


def test_14g_identity_science_and_runtime_scope_are_frozen() -> None:
    config = load_resolved(CONFIG)
    invariants, checks = static_invariants(config, Path(RUN_ROOT))
    assert all(checks.values())
    assert invariants["scientific_changes_from_r3"] == 0
    assert len(
        config["pipeline"]["historical_comparison"][
            "selector_ensemble_sha256"
        ]
    ) == 64
    assert invariants["causal_panel"] == {
        "initial_state_count": 256,
        "maximum_state_count": 499,
        "minimum_per_label": 40,
    }
    _, branches, cap = runtime_tables()
    assert branches["branch_3d_reproduction_fails"][
        "expected_wall_hours"
    ] == 26.75
    assert branches["branch_3d_passes_and_1d_executes"][
        "expected_wall_hours"
    ] == 47.75
    assert cap["proposed_hard_cap_hours"] == 120.0
    assert cap["authorization_status"] == "NOT_AUTHORIZED"


def test_required_early_gates_are_in_topological_order() -> None:
    stages = build_exp037a_stage_graph()
    ids = [stage.stage_id for stage in stages]
    by_id = {stage.stage_id: stage for stage in stages}
    assert ids.index("D06_paired_causal_outcomes") + 1 == ids.index(
        "D06B_three_demo_causal_reproduction_gate"
    )
    assert ids.index("D06B_three_demo_causal_reproduction_gate") + 1 == ids.index(
        "D07_policy_teacher"
    )
    assert ids.index("D08_zero_cache_and_training_units") + 1 == ids.index(
        "D08B_writer_reader_one_unit_smoke"
    )
    assert ids.index("D08B_writer_reader_one_unit_smoke") + 1 == ids.index(
        "D09_writer_reader_epoch_1"
    )
    assert by_id["D07_policy_teacher"].dependencies == (
        "D06B_three_demo_causal_reproduction_gate",
    )
    assert by_id["D08B_writer_reader_one_unit_smoke"].dependencies == (
        "D08_zero_cache_and_training_units",
    )
    assert by_id["D09_writer_reader_epoch_1"].dependencies == (
        "D08B_writer_reader_one_unit_smoke",
    )


def _d06_fixture() -> tuple[
    dict[str, object], dict[str, object], list[dict[str, object]]
]:
    rows = [
        {
            "state_example_id": "train-1",
            "model_split": "model_train",
            "label": "POSITIVE",
        },
        {
            "state_example_id": "train-2",
            "model_split": "model_train",
            "label": "NEUTRAL",
        },
        {
            "state_example_id": "heldout-1",
            "model_split": "heldout_train_validation",
            "label": "HARMFUL",
        },
    ]
    selections = [
        {
            "state_example_id": f"state-{index}",
            "scoreable": index < 3,
            "over_context": index >= 3,
        }
        for index in range(499)
    ]
    payload = {
        "rows": rows,
        "label_counts": {"POSITIVE": 1, "NEUTRAL": 1, "HARMFUL": 1},
        "replay_semantic_missing_rows": [{"state_example_id": "state-4"}],
    }
    return payload, json.loads(json.dumps(payload)), selections


def test_d06_gate_passes_only_exact_post_seal_reproduction() -> None:
    fresh, historical, selections = _d06_fixture()
    passed = evaluate_d06_reproduction_gate(
        fresh=fresh,
        historical=historical,
        fresh_selections=selections,
        historical_selections=json.loads(json.dumps(selections)),
        expected_train_completed=2,
        expected_heldout_completed=1,
        expected_label_counts={"POSITIVE": 1, "NEUTRAL": 1, "HARMFUL": 1},
    )
    assert passed["decision"] == "D06_THREE_DEMO_REPRODUCTION_PASS"
    assert passed["passed"]
    historical["rows"][0]["label"] = "NEUTRAL"
    failed = evaluate_d06_reproduction_gate(
        fresh=fresh,
        historical=historical,
        fresh_selections=selections,
        historical_selections=selections,
        expected_train_completed=2,
        expected_heldout_completed=1,
        expected_label_counts={"POSITIVE": 1, "NEUTRAL": 1, "HARMFUL": 1},
    )
    assert failed["decision"] == "D06_THREE_DEMO_REPRODUCTION_FAIL"
    assert not failed["checks"]["paired_labels_exact"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_uuid", "foreign-run"),
        ("run_root", "/foreign/root"),
        ("source_commit", "b" * 40),
        ("contract_sha256", "0" * 64),
        ("pipeline_config_sha256", "1" * 64),
        ("hard_cap_hours", 200.0),
        ("scope", "three-demo-only"),
        ("authorization_version", "stale-authorization-version"),
        ("one_demo_authorized", False),
        ("previous_200_hour_authorization_inherited", True),
    ],
)
def test_stale_wrong_cap_or_partial_scope_authorization_fails(
    tmp_path: Path, field: str, value: object
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true", encoding="utf-8")
    run_root = tmp_path / "run"
    contract = _strict_contract(config_path, run_root, ())
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract.as_dict())
    payload = _valid_authorization(
        contract, run_root, sha256_file(contract_path), config_path
    )
    payload[field] = value
    with pytest.raises(PermissionError, match="Run-bound explicit authorization"):
        validate_explicit_authorization(
            payload,
            contract,
            run_root=run_root,
            contract_path=contract_path,
            pipeline_config_path=config_path,
        )


def test_authorization_request_cannot_launch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true", encoding="utf-8")
    run_root = tmp_path / "run"
    contract = _strict_contract(config_path, run_root, ())
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract.as_dict())
    request = _valid_authorization(
        contract, run_root, sha256_file(contract_path), config_path
    )
    request.update(
        {
            "authorization_status": "NOT_AUTHORIZED",
            "authorized": False,
            "granted_by_user": False,
        }
    )
    with pytest.raises(PermissionError):
        validate_explicit_authorization(
            request,
            contract,
            run_root=run_root,
            contract_path=contract_path,
            pipeline_config_path=config_path,
        )


def test_valid_synthetic_authorization_passes_without_running_science(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true", encoding="utf-8")
    run_root = tmp_path / "run"
    contract = _strict_contract(config_path, run_root, ())
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract.as_dict())
    checks = validate_explicit_authorization(
        _valid_authorization(
            contract, run_root, sha256_file(contract_path), config_path
        ),
        contract,
        run_root=run_root,
        contract_path=contract_path,
        pipeline_config_path=config_path,
    )
    assert all(checks.values())
    assert not (run_root / "stages").exists()


def test_launcher_persists_valid_run_bound_authorization_without_science(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true", encoding="utf-8")
    run_root = tmp_path / "run"
    preflight = run_root / "preflight"
    preflight.mkdir(parents=True)
    contract = _strict_contract(config_path, run_root, ())
    contract_path = preflight / "stage_dag.json"
    atomic_write_json(contract_path, contract.as_dict())
    atomic_write_json(
        preflight / "preflight_summary.json",
        {
            "launch_source_sha": contract.source_commit,
            "approval_checks": {"ready": True},
            "explicit_user_approval_required": True,
        },
    )
    atomic_write_json(
        preflight / "runtime_preflight.json",
        {"recommended_hard_cap_hours": contract.hard_cap_hours},
    )
    authorization_path = tmp_path / "authorization.json"
    atomic_write_json(
        authorization_path,
        _valid_authorization(
            contract, run_root, sha256_file(contract_path), config_path
        ),
    )
    monkeypatch.setattr(
        "scripts.run_rcmf_reproducible_pipeline_14b._head",
        lambda: contract.source_commit,
    )
    monkeypatch.setattr(
        "scripts.run_rcmf_reproducible_pipeline_14b._status", lambda: ""
    )
    monkeypatch.setattr(
        "scripts.run_rcmf_reproducible_pipeline_14b.os.path.ismount",
        lambda _: True,
    )
    payload = authorize_pipeline(
        contract_path, run_root, authorization_path
    )
    assert payload["authorization_version"] == AUTHORIZATION_VERSION
    assert payload["hard_cap_hours"] == PROPOSED_HARD_CAP_HOURS
    assert (run_root / "runtime_authorization.json").exists()
    assert not (run_root / "stages").exists()


def test_launcher_uses_final_preflight_source_and_no_hardcoded_cap() -> None:
    source = Path("scripts/run_rcmf_reproducible_pipeline_14b.py").read_text(
        encoding="utf-8"
    )
    assert 'preflight["launch_source_sha"]' in source
    assert "exp037a_runtime_authorization_14f_v1" not in source
    assert "approved_cap_is_200" not in source
    assert '"hard_cap_hours": 200' not in source


def test_strict_resume_rejects_foreign_stage_output(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true", encoding="utf-8")
    run_root = tmp_path / "run"
    run_root.mkdir()
    stage = StageSpec("S00", "shared", command=("ignored",))
    contract = _strict_contract(config_path, run_root, (stage,))
    contract_sha = "c" * 64
    atomic_write_json(
        run_root / "runtime_authorization.json",
        _valid_authorization(contract, run_root, contract_sha, config_path),
    )
    calls: list[str] = []

    def runner(
        stage: StageSpec,
        command: list[str],
        stage_dir: Path,
        environment: dict[str, str],
    ) -> int:
        del command
        calls.append(stage.stage_id)
        _write_output(stage, stage_dir, contract.source_commit, environment)
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
    assert calls == ["S00"]
    manifest_path = run_root / "stages/S00/output_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_uuid"] = "foreign-run"
    atomic_write_json(manifest_path, manifest)
    assert scheduler.run().status == "complete"
    assert calls == ["S00", "S00"]


def test_strict_validator_checks_all_resume_identity_fields(
    tmp_path: Path,
) -> None:
    stage_dir = tmp_path / "S00"
    stage_dir.mkdir()
    payload = stage_dir / "payload.json"
    atomic_write_json(payload, {"ok": True})
    manifest = {
        "stage_id": "S00",
        "source_commit": "a" * 40,
        "run_uuid": RUN_UUID,
        "run_root": str(tmp_path.resolve()),
        "pipeline_config_sha256": "b" * 64,
        "contract_sha256": "c" * 64,
        "passed": True,
        "outputs": [{"path": "payload.json", "sha256": sha256_file(payload)}],
    }
    atomic_write_json(stage_dir / "output_manifest.json", manifest)
    assert validate_stage_completion(
        stage_dir,
        "a" * 40,
        expected_run_uuid=RUN_UUID,
        expected_pipeline_config_sha256="b" * 64,
        expected_contract_sha256="c" * 64,
        expected_run_root=tmp_path,
    )["passed"]
    manifest["contract_sha256"] = "d" * 64
    atomic_write_json(stage_dir / "output_manifest.json", manifest)
    assert not validate_stage_completion(
        stage_dir,
        "a" * 40,
        expected_run_uuid=RUN_UUID,
        expected_pipeline_config_sha256="b" * 64,
        expected_contract_sha256="c" * 64,
        expected_run_root=tmp_path,
    )["passed"]


def test_preflight_builder_contains_no_scientific_execution() -> None:
    source = Path(
        "scripts/prepare_rcmf_reproducible_pipeline_14g.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "run_pipeline(",
        "optimizer.step(",
        "build_backend(",
        "paired_causal_generation",
        "torch.load(",
    )
    assert all(token not in source for token in forbidden)


def test_completion_hash_remains_self_authenticating(tmp_path: Path) -> None:
    store = StageStateStore(tmp_path)
    path = store.write_completion("S00", {"passed": True})
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["completion_sha256"] == content_sha256(
        {key: value for key, value in row.items() if key != "completion_sha256"}
    )


def test_deployment_field_validation_handles_no_selected_checkpoint(
    tmp_path: Path,
) -> None:
    selection = (
        tmp_path
        / "arms/3d/heldout_validation/live_full_field/checkpoint_selection.json"
    )
    selection.parent.mkdir(parents=True)
    atomic_write_json(selection, {"selected": None})
    result = _validate_deployment_field(tmp_path, "3d")
    assert result["status"] == "NO_DEPLOYABLE_CHECKPOINT"
    assert result["passed"]


def test_writer_reader_smoke_input_identity_handles_directories(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "zero-cache"
    directory.mkdir()
    (directory / "a.json").write_text("{}", encoding="utf-8")
    (directory / "b.json").write_text('{"x":1}', encoding="utf-8")
    first = _path_identity(directory)
    second = _path_identity(directory)
    assert first == second
    assert first["kind"] == "directory"
    assert first["file_count"] == 2
