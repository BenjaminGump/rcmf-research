from __future__ import annotations

from pathlib import Path

import yaml

from rcmf.pipeline.stage_graph import build_exp037a_continuation_stage_graph
from scripts.prepare_exp037a_continuation_14n import (
    AUTHORIZATION_SCOPE,
    AUTHORIZATION_VERSION,
    EXPECTED_RUN_ROOT,
    EXPECTED_RUN_UUID,
)


OLD_RUN_UUID = "rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001"
FAILED_14M_RUN_UUID = "rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002"
PARENT_RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001"


def _yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_14n_identity_is_fresh_and_unauthorized() -> None:
    config_path = Path("configs/pipeline/rcmf_appworld_continuation_14n.yaml")
    config = _yaml(str(config_path))
    pipeline = config["pipeline"]
    authorization = pipeline["conditional_runtime_authorization"]

    assert pipeline["run_uuid"] == EXPECTED_RUN_UUID
    assert pipeline["roots"]["run_root"] == EXPECTED_RUN_ROOT
    assert pipeline["config_path"] == str(config_path).replace("\\", "/")
    assert pipeline["authorization_scope"] == AUTHORIZATION_SCOPE
    assert authorization["authorization_version"] == AUTHORIZATION_VERSION
    assert authorization["authorization_status"] == "NOT_AUTHORIZED"
    assert authorization["granted_by_user"] is False
    assert authorization["full_pipeline_authorized"] is False
    assert authorization["d06_or_later_authorized"] is False
    assert authorization["one_demo_authorized"] is False
    assert authorization["continuation_authorized"] is False
    assert pipeline["proposed_hard_cap_hours"] == 32
    assert pipeline["schema_version"].endswith("continuation_14n_v1")
    assert OLD_RUN_UUID not in config_path.read_text(encoding="utf-8")
    assert FAILED_14M_RUN_UUID not in config_path.read_text(encoding="utf-8")


def test_14n_parent_boundary_and_scope_are_exact() -> None:
    config = _yaml("configs/pipeline/rcmf_appworld_continuation_14n.yaml")
    continuation = config["pipeline"]["continuation"]
    stages = [stage.stage_id for stage in build_exp037a_continuation_stage_graph()]

    assert continuation["parent_run_uuid"] == PARENT_RUN_UUID
    assert continuation["parent_source_commit"] == (
        "004f866647cfabb38a141b88e6d83821df88c403"
    )
    assert continuation["boundary"] == "after_O07_before_O08"
    assert continuation["parent_o08_partial_outputs_allowed"] is False
    assert continuation["parent_artifact_manifest_path"] == (
        f"{EXPECTED_RUN_ROOT}/preflight/parent_artifact_manifest.json"
    )
    assert stages == [
        "C00_parent_run_evidence_import",
        "C01_o07_o08_boundary_validation",
        "O08_zero_cache_and_training_units",
        "O09_writer_reader_epoch_1",
        "O10_writer_reader_epoch_2",
        "O11_heldout_teacher_forced",
        "O12_heldout_one_step",
        "O13_heldout_full_trajectory",
        "O14_checkpoint_selection",
        "O15_401_memory_field",
        "O16_compile_and_add_98_memories",
        "O17_499_memory_deployment_field",
        "O18_common_one_demo_dev_correct",
        "O19_common_one_demo_dev_shuffle",
        "F00_two_arm_paired_analysis",
        "F01_portability_validation",
        "F02_git_safe_audit_export",
        "F03_final_report_and_handoff",
    ]


def test_14n_scientific_configuration_matches_14l() -> None:
    old = _yaml("configs/pipeline/rcmf_appworld_continuation_14l.yaml")
    new = _yaml("configs/pipeline/rcmf_appworld_continuation_14n.yaml")
    scientific_sections = (
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
    for section in scientific_sections:
        assert new["pipeline"][section] == old["pipeline"][section]

    old_arm = _yaml("configs/pipeline/rcmf_appworld_arm_1d_continuation_14l.yaml")
    new_arm = _yaml("configs/pipeline/rcmf_appworld_arm_1d_continuation_14n.yaml")
    old_arm.pop("run_id")
    new_arm.pop("run_id")
    assert new_arm == old_arm


def test_14n_builder_requires_r16_and_r18_evidence() -> None:
    source = Path("scripts/prepare_exp037a_continuation_14n.py").read_text(
        encoding="utf-8"
    )
    assert "--o13-path-diagnostic" in source
    assert "--o13-smoke" in source
    assert "--path-ownership-audit" in source
    assert '"o13_path_repair_validated"' in source
    assert "--continuation-dispatch-audit" in source
    assert "--c00-c01-diagnostic" in source
    assert '"semantic_continuation_dispatch_validated"' in source
    assert OLD_RUN_UUID not in source
    assert FAILED_14M_RUN_UUID not in source
