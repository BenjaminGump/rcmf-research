from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.orchestrator import load_pipeline_contract
from rcmf.pipeline.scheduler import EventDrivenScheduler
from rcmf.utils.serialization import sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14f import (
    EXPECTED_RUN_ROOT,
    EXPECTED_RUN_UUID,
    OLD_RUN_ROOT,
    OLD_RUN_UUID,
    build_preflight,
)
from scripts.run_rcmf_reproducible_pipeline_14b import _authorize


CONFIG = Path("configs/pipeline/rcmf_appworld_repro_14f.yaml")


def _package(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    output = tmp_path / "preflight"
    summary = build_preflight(CONFIG, output, "a" * 40)
    return output, summary


def _valid_authorization(contract_path: Path, run_root: Path) -> dict[str, object]:
    contract = load_pipeline_contract(contract_path)
    return {
        "authorization_status": "AUTHORIZED",
        "authorized": True,
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": contract.run_uuid,
        "run_root": str(run_root.resolve()),
        "source_commit": contract.source_commit,
        "contract_sha256": sha256_file(contract_path),
        "pipeline_config_sha256": sha256_file(CONFIG),
        "hard_cap_hours": contract.hard_cap_hours,
    }


def test_static_preflight_is_unapproved_and_preserves_r3_science(tmp_path: Path) -> None:
    output, summary = _package(tmp_path)
    assert summary["decision"] == "READY_FOR_EXPLICIT_USER_APPROVAL"
    assert summary["authorized_to_launch"] is False
    assert summary["h100_scientific_active_hours"] == 0
    identity = json.loads((output / "run_identity.json").read_text())
    assert identity["new_run_uuid"] == EXPECTED_RUN_UUID != OLD_RUN_UUID
    assert identity["new_run_root"] == EXPECTED_RUN_ROOT != OLD_RUN_ROOT
    authorization = json.loads((output / "authorization_state.json").read_text())
    assert authorization["granted_by_user"] is False
    assert authorization["previous_200_hour_authorization_inherited"] is False
    assert authorization["proposed_hard_cap_hours"] == 80
    invariants = json.loads((output / "scientific_invariants.json").read_text())
    assert invariants["causal_panel"] == {
        "initial_state_count": 256,
        "maximum_state_count": 499,
        "minimum_per_label": 40,
    }
    assert invariants["post_d06_reproduction_gate"]["expected_train_completed"] == 366
    assert invariants["post_d06_reproduction_gate"]["expected_heldout_completed"] == 98
    assert invariants["post_d06_reproduction_gate"]["construction_input"] is False
    assert invariants["resolved_arm_diff"]["passed"]


def test_launcher_requires_fresh_explicit_authorization_file(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="explicit --authorization-file"):
        _authorize(tmp_path / "missing.json", tmp_path / "run", None)


def test_unapproved_package_cannot_authorize_or_launch(tmp_path: Path) -> None:
    output, _ = _package(tmp_path)
    contract_path = output / "stage_dag.json"
    contract = load_pipeline_contract(contract_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    unapproved = json.loads((output / "authorization_state.json").read_text())
    with pytest.raises(PermissionError, match="Run-bound explicit authorization failed"):
        validate_explicit_authorization(
            unapproved,
            contract,
            run_root=run_root,
            contract_path=contract_path,
            pipeline_config_path=CONFIG,
        )
    (run_root / "runtime_authorization.json").write_text(json.dumps(unapproved))
    called: list[str] = []
    strict_contract = PipelineContract(
        schema_version="test",
        run_uuid=contract.run_uuid,
        source_commit=contract.source_commit,
        global_seed=25101,
        hard_cap_hours=80.0,
        stages=(StageSpec("S00", "shared", command=("ignored",)),),
        arms={
            "3d": ArmContract("3d", "full_demo", "arms/3d", "3d"),
            "1d": ArmContract("1d", "full_demo_first_only", "arms/1d", "1d"),
        },
        metadata={"require_run_bound_authorization": True},
    )

    def runner(*_: object) -> int:
        called.append("ran")
        return 0

    with pytest.raises(PermissionError):
        EventDrivenScheduler(
            strict_contract,
            run_root,
            python_executable="python",
            config_path=CONFIG,
            runner=runner,
            contract_sha256=sha256_file(contract_path),
        ).run()
    assert called == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_uuid", "stale-run"),
        ("run_root", "/stale/root"),
        ("source_commit", "b" * 40),
        ("contract_sha256", "0" * 64),
        ("pipeline_config_sha256", "1" * 64),
        ("hard_cap_hours", 200.0),
        ("previous_200_hour_authorization_inherited", True),
    ],
)
def test_stale_or_inherited_authorization_is_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    output, _ = _package(tmp_path)
    contract_path = output / "stage_dag.json"
    contract = load_pipeline_contract(contract_path)
    run_root = Path(contract.metadata.get("test_run_root", tmp_path / "future-run"))
    payload = _valid_authorization(contract_path, run_root)
    payload[field] = value
    with pytest.raises(PermissionError, match="Run-bound explicit authorization failed"):
        validate_explicit_authorization(
            payload,
            contract,
            run_root=run_root,
            contract_path=contract_path,
            pipeline_config_path=CONFIG,
        )


def test_launcher_has_no_hard_coded_200_hour_authorization() -> None:
    source = Path("scripts/run_rcmf_reproducible_pipeline_14b.py").read_text()
    assert "approved_cap_is_200" not in source
    assert "\"hard_cap_hours\": 200.0" not in source
    assert "user_conditional_total_authorization" not in source
    assert "explicit_run_bound_user_authorization" in source


def test_hardening_preflight_never_runs_a_scientific_stage() -> None:
    source = Path("scripts/prepare_rcmf_reproducible_pipeline_14f.py").read_text()
    forbidden = ("run_pipeline(", "subprocess.Popen(", "optimizer.step(", "torch.load(")
    assert all(token not in source for token in forbidden)
