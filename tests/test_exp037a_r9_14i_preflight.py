from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.prepare_rcmf_reproducible_pipeline_14i import (
    AUTHORIZATION_SCOPE,
    AUTHORIZATION_VERSION,
    PROPOSED_HARD_CAP_HOURS,
    RUN_ROOT,
    RUN_UUID,
    executable_source_manifest,
    runtime_tables,
    static_invariants,
)


CONFIG_14H = Path("configs/pipeline/rcmf_appworld_repro_14h.yaml")
CONFIG_14I = Path("configs/pipeline/rcmf_appworld_repro_14i.yaml")
SOURCE = "a" * 40


def _scientific_contract(config: dict) -> dict:
    pipeline = config["pipeline"]
    return {
        key: copy.deepcopy(pipeline[key])
        for key in (
            "global_seed",
            "selector_cv_seed",
            "final_selector_member_seeds",
            "required_environment",
            "reproduction_contract",
            "expected",
            "prompt_assets",
            "selector",
            "memory",
            "reader",
            "training",
            "evaluation",
            "historical_comparison",
        )
    }


def test_14i_science_matches_14h_exactly() -> None:
    config_14h = load_resolved(CONFIG_14H)
    config_14i = load_resolved(CONFIG_14I)
    assert _scientific_contract(config_14i) == _scientific_contract(config_14h)
    invariants, checks = static_invariants(config_14i, Path(RUN_ROOT))
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


def test_14i_identity_is_fresh_and_authorization_is_false() -> None:
    raw = yaml.safe_load(CONFIG_14I.read_text(encoding="utf-8"))
    pipeline = raw["pipeline"]
    authorization = pipeline["conditional_runtime_authorization"]
    assert pipeline["run_uuid"] == RUN_UUID
    assert pipeline["roots"]["run_root"] == RUN_ROOT
    assert "14h_20260904_001" not in RUN_UUID
    assert RUN_UUID.endswith("14i_20260904_002")
    assert authorization["authorization_version"] == AUTHORIZATION_VERSION
    assert authorization["authorization_status"] == "NOT_AUTHORIZED"
    assert authorization["granted_by_user"] is False
    assert authorization["full_pipeline_authorized"] is False
    assert authorization["d06_or_later_authorized"] is False
    assert authorization["one_demo_authorized"] is False
    assert authorization["previous_200_hour_authorization_inherited"] is False
    assert authorization["failed_14h_authorization_inherited"] is False


def test_failed_14h_authorization_cannot_authorize_14i(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    run_root = tmp_path / "run-14i"
    contract = PipelineContract(
        schema_version="test-14i",
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
        "authorization_version": "exp037a_run_bound_authorization_14h_v1",
        "authorized": True,
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": "rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001",
        "run_root": str(tmp_path / "run-14h"),
        "source_commit": "2" * 40,
        "contract_sha256": sha256_file(contract_path),
        "pipeline_config_sha256": sha256_file(config_path),
        "hard_cap_hours": PROPOSED_HARD_CAP_HOURS,
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


def test_14i_stage_gates_and_runtime_proposal_are_unchanged() -> None:
    stage_ids = [stage.stage_id for stage in build_exp037a_stage_graph()]
    assert stage_ids.index("D06B_three_demo_causal_reproduction_gate") + 1 == (
        stage_ids.index("D07_policy_teacher")
    )
    assert stage_ids.index("D08B_writer_reader_one_unit_smoke") + 1 == (
        stage_ids.index("D09_writer_reader_epoch_1")
    )
    assert stage_ids.index("D22_three_demo_reproduction_gate") < stage_ids.index(
        "O00_state_representations"
    )
    stages, branches, cap = runtime_tables()
    assert any("14h formal start" in row for row in stages["basis"])
    assert branches["branch_3d_reproduction_fails"]["expected_wall_hours"] == 26.75
    assert branches["branch_3d_passes_and_1d_executes"][
        "conservative_wall_hours"
    ] == 92.5
    assert cap["proposed_hard_cap_hours"] == 120.0
    assert cap["authorization_status"] == "NOT_AUTHORIZED"


def test_14i_launch_source_manifest_includes_resume_repairs() -> None:
    manifest = executable_source_manifest()
    assert manifest["scientific_configuration_changes_from_r3"] == 0
    assert {
        "checkpoint_training_runner",
        "checkpoint_resume_validator",
        "d09_resume_preparer",
        "r9_checkpoint_resume_tests",
        "r9_14i_preflight_tests",
    } <= set(manifest["files"])
