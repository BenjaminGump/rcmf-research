from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
)
from rcmf.benchmarks.appworld.reproducible_config_14b import (
    build_arm_runtime_config,
)
from rcmf.pipeline.authorization import validate_explicit_authorization
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.prepare_rcmf_reproducible_pipeline_14k import (
    AUTHORIZATION_SCOPE,
    AUTHORIZATION_VERSION,
    PROPOSED_HARD_CAP_HOURS,
    RUN_ROOT,
    RUN_UUID,
    executable_source_manifest,
    runtime_tables,
    static_invariants,
)


CONFIG_14J = Path("configs/pipeline/rcmf_appworld_repro_14j.yaml")
CONFIG_14K = Path("configs/pipeline/rcmf_appworld_repro_14k.yaml")
REPLAY_CONFIG = Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml")
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


def test_14k_science_matches_frozen_14j_exactly() -> None:
    config_14j = load_resolved(CONFIG_14J)
    config_14k = load_resolved(CONFIG_14K)
    assert _scientific_contract(config_14k) == _scientific_contract(config_14j)
    invariants, checks = static_invariants(config_14k, Path(RUN_ROOT))
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


def test_14k_identity_is_fresh_and_authorization_is_false() -> None:
    raw = yaml.safe_load(CONFIG_14K.read_text(encoding="utf-8"))
    pipeline = raw["pipeline"]
    authorization = pipeline["conditional_runtime_authorization"]
    assert pipeline["run_uuid"] == RUN_UUID
    assert pipeline["roots"]["run_root"] == RUN_ROOT
    assert "14j_20260904_001" not in RUN_UUID
    assert RUN_UUID.endswith("14k_20260905_001")
    assert authorization["authorization_version"] == AUTHORIZATION_VERSION
    assert authorization["authorization_status"] == "NOT_AUTHORIZED"
    assert authorization["granted_by_user"] is False
    assert authorization["full_pipeline_authorized"] is False
    assert authorization["d06_or_later_authorized"] is False
    assert authorization["one_demo_authorized"] is False
    assert authorization["previous_200_hour_authorization_inherited"] is False
    assert authorization["failed_14h_authorization_inherited"] is False
    assert authorization["failed_14j_authorization_inherited"] is False


def test_failed_14h_authorization_cannot_authorize_14k(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("test: true\n", encoding="utf-8")
    run_root = tmp_path / "run-14k"
    contract = PipelineContract(
        schema_version="test-14k",
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
        "authorization_version": "exp037a_run_bound_authorization_14j_v1",
        "authorized": True,
        "granted_by_user": True,
        "full_pipeline_authorized": True,
        "d06_or_later_authorized": True,
        "one_demo_authorized": True,
        "previous_200_hour_authorization_inherited": False,
        "run_uuid": "rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001",
        "run_root": str(tmp_path / "run-14j"),
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


def test_14k_stage_gates_and_runtime_proposal_are_unchanged() -> None:
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
    assert any("14j S00 through D22" in row for row in stages["basis"])
    assert branches["branch_3d_reproduction_fails"]["expected_wall_hours"] == 18.0
    assert branches["branch_3d_passes_and_1d_executes"][
        "conservative_wall_hours"
    ] == 64.0
    assert cap["proposed_hard_cap_hours"] == 80.0
    assert cap["authorization_status"] == "NOT_AUTHORIZED"


def test_14k_launch_source_manifest_includes_r12b_repair() -> None:
    manifest = executable_source_manifest()
    assert manifest["scientific_configuration_changes_from_r3"] == 0
    assert {
        "checkpoint_training_runner",
        "checkpoint_resume_validator",
        "d09_resume_preparer",
        "whole_pipeline_audit",
        "r9_checkpoint_resume_tests",
        "r10_pipeline_hardening_tests",
        "paired_causal_runtime_resolver",
        "paired_causal_stage_runner",
        "r12b_prompt_consumer_audit",
        "r12b_prompt_profile_tests",
        "r12b_14k_preflight_tests",
    } <= set(manifest["files"])


def test_14k_effective_paired_generation_is_prompt_only() -> None:
    config = load_resolved(CONFIG_14K)
    replay = yaml.safe_load(REPLAY_CONFIG.read_text(encoding="utf-8"))
    legacy = replay["stage_c_7b"]["causal_audit"]["generation"]
    generations = {}
    provenances = {}
    for arm_id in ("3d", "1d"):
        arm = build_arm_runtime_config(config, Path(RUN_ROOT), arm_id)
        effective, provenance = resolve_effective_paired_causal_runtime(
            replay_config=replay,
            arm_config=arm,
            arm_id=arm_id,
            arm_config_path=f"arm_{arm_id}.yaml",
            arm_config_sha256="a" * 64,
            replay_config_path=str(REPLAY_CONFIG),
            replay_config_sha256="b" * 64,
        )
        generations[arm_id] = effective["causal_audit"]["generation"]
        provenances[arm_id] = provenance
    assert generations["3d"] == legacy
    assert provenances["3d"]["changed_execution_fields"] == []
    assert provenances["3d"]["three_demo_effective_generation_diff"] == 0
    assert provenances["1d"]["changed_execution_fields"] == [
        "prompt_profile"
    ]
    assert generations["1d"]["prompt_profile"] == "full_demo_first_only"
    assert {
        key: value
        for key, value in generations["1d"].items()
        if key != "prompt_profile"
    } == {key: value for key, value in legacy.items() if key != "prompt_profile"}

