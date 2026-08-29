from __future__ import annotations

import inspect
from pathlib import Path

from rcmf.config import load_config
from rcmf.benchmarks.appworld.prompt import FULL_DEMO_FIRST_ONLY_PROFILE
from scripts.prepare_rcmf_joint_full_bank_9a import _paths as prepare_9a_paths
from scripts.run_rcmf_joint_full_bank_9a import _paths as run_9a_paths
from scripts.prepare_rcmf_one_demo_retrain_11b import (
    _prompt_checks,
    _paths as prepare_11b_paths,
    CACHE_FORMAT,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task
from scripts.run_rcmf_one_demo_retrain_dev_11b import FIELD_CONTROLS, CONDITIONS
from scripts.analyze_rcmf_one_demo_retrain_11b import _paired_sets
from scripts.export_rcmf_one_demo_retrain_audit_11b import (
    _publish_staged_tree,
    _stage_existing_tree,
)


NEW_CONFIG = Path("configs/benchmark/stage_c_rcmf_one_demo_retrain_11b.yaml")
OLD_CONFIG = Path("configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml")
REPLAY_CONFIG = Path("configs/benchmark/stage_c_rcmf_one_demo_retrain_replay_11b.yaml")


def test_exp034a_only_changes_prompt_dependent_training_inputs() -> None:
    new = load_config(NEW_CONFIG)
    old = load_config(OLD_CONFIG)
    new_9a = new.raw["stage_c_9a"]
    old_9a = old.raw["stage_c_9a"]
    for key in ("memory", "field", "reader", "training", "validation"):
        assert new_9a[key] == old_9a[key]
    assert new_9a["expected"] == old_9a["expected"]
    assert new_9a["global_seed"] == old_9a["global_seed"] == 25101
    assert new.benchmark.prompt_profile == FULL_DEMO_FIRST_ONLY_PROFILE
    assert new_9a["appworld"]["prompt_profile"] == FULL_DEMO_FIRST_ONLY_PROFILE
    replay = load_config(REPLAY_CONFIG)
    assert (
        replay.raw["stage_c_7b"]["causal_audit"]["generation"]["prompt_profile"]
        == FULL_DEMO_FIRST_ONLY_PROFILE
    )
    assert (
        new.raw["stage_c_7hr"]["appworld"]["prompt_profile"]
        == FULL_DEMO_FIRST_ONLY_PROFILE
    )


def test_exp034a_inherits_complete_upstream_supervision_contract() -> None:
    cfg = load_config(NEW_CONFIG)
    settings = cfg.raw["stage_c_7hr"]
    assert settings["parent_exp025b"]
    assert settings["reconciled_corpus_dir"]
    assert settings["compiler"]["top_k"] == 64
    assert settings["panel"]["initial_state_count"] == 464
    assert settings["panel"]["maximum_state_count"] == 464
    assert settings["panel"]["minimum_per_label"] == 0
    source = Path("scripts/prepare_rcmf_one_demo_retrain_11b.py").read_text()
    assert '"violations": []' in source


def test_old_9a_path_defaults_are_unchanged() -> None:
    old = load_config(OLD_CONFIG).raw["stage_c_9a"]
    artifact = Path("runs/test-old")
    prepared = prepare_9a_paths(old, artifact)
    runtime = run_9a_paths(old, artifact)
    assert prepared["state_cache"] == (
        Path(old["parent_exp025c"]) / "representation_cache/multiview/state_multiview.pt"
    )
    assert prepared["outcomes"] == (
        Path(old["parent_exp028a"]) / "paired_causal/paired_outcomes.json"
    )
    assert runtime["teacher"] == (
        Path(old["parent_exp028a"]) / "structured_compiler/policy_teacher_cache.pt"
    )


def test_exp034a_overrides_only_prompt_dependent_9a_paths() -> None:
    cfg = load_config(NEW_CONFIG)
    settings = cfg.raw["stage_c_9a"]
    artifact = Path(settings["artifact_dir"])
    prepared = prepare_9a_paths(settings, artifact)
    runtime = run_9a_paths(settings, artifact)
    overrides = settings["prompt_dependent_inputs"]
    assert prepared["state_cache"] == Path(overrides["state_cache"])
    assert prepared["outcomes"] == Path(overrides["outcomes"])
    assert prepared["teacher_cache"] == Path(overrides["teacher_cache"])
    assert runtime["outcomes"] == Path(overrides["outcomes"])
    assert runtime["teacher"] == Path(overrides["teacher_cache"])
    assert prepared["transition_cache"] == (
        Path(settings["parent_exp025c"])
        / "representation_cache/multiview/transition_multiview.pt"
    )


def test_state_cache_source_does_not_access_target_or_future() -> None:
    source = Path("scripts/prepare_rcmf_one_demo_retrain_11b.py").read_text(
        encoding="utf-8"
    )
    cache_body = source.split("def _state_cache", 1)[1].split("def _selections", 1)[0]
    assert "target_text" not in cache_body
    assert "target_action_accessed" in cache_body
    assert "future_observation_accessed" in cache_body
    assert CACHE_FORMAT == "one_demo_state_multiview_11b_v1"


def test_exp034a_teacher_preflight_excludes_unused_gate_only_for_11b() -> None:
    source = Path("scripts/run_appworld_structured_compiler_7hr.py").read_text()
    assert 'args.phase != "teacher" or "stage_c_11b" not in cfg.raw' in source
    assert 'if gate_required:' in source


def test_exp034a_writes_reused_runner_runtime_preflight_alias() -> None:
    cfg = load_config(NEW_CONFIG)
    artifact = Path("runs/test-exp034a")
    paths = prepare_11b_paths(cfg, artifact)
    assert paths["early_preflight"] == (
        artifact / "runtime/early_runtime_preflight.json"
    )
    assert paths["runtime_preflight"] == artifact / "runtime_preflight.json"


def test_one_demo_prompt_identity_is_locked() -> None:
    cfg = load_config(NEW_CONFIG)
    checks = _prompt_checks(cfg.raw["stage_c_11b"])
    assert checks == {
        "profile": True,
        "initial_asset": True,
        "initial_message_count": True,
    }


def test_dev_runtime_can_label_new_conditions_without_changing_field_control() -> None:
    signature = inspect.signature(_run_task)
    assert "field_control_condition" in signature.parameters
    assert signature.parameters["field_control_condition"].default is None
    source = inspect.getsource(_run_task)
    assert "runtime_condition = (" in source
    assert "runtime.read(messages, runtime_condition)" in source


def test_exp034a_fixed_scientific_state_counts_and_no_dev_training() -> None:
    cfg = load_config(NEW_CONFIG)
    settings = cfg.raw["stage_c_11b"]
    assert settings["expected"]["train_state_count"] == 366
    assert settings["expected"]["heldout_state_count"] == 98
    assert settings["expected"]["dev_task_count"] == 57
    assert settings["global_seed"] == 25101


def test_exp034a_dev_conditions_map_only_to_existing_field_controls() -> None:
    assert CONDITIONS == ("N1", "N2")
    assert FIELD_CONTROLS == {"N1": "D1", "N2": "D2"}
    source = Path("scripts/run_rcmf_one_demo_retrain_dev_11b.py").read_text()
    assert "rows, FIELD_CONTROLS[condition]" in source
    assert '"field_control_condition": FIELD_CONTROLS[condition]' in source

def test_exp034a_export_preserves_incremental_checkpoint_records(tmp_path: Path) -> None:
    result = tmp_path / "result"
    staged = tmp_path / "result.tmp"
    result.mkdir()
    staged.mkdir()
    (result / "checkpoint.json").write_text("old", encoding="utf-8")
    _stage_existing_tree(result, staged)
    (staged / "summary.json").write_text("new", encoding="utf-8")
    (staged / "checkpoint.json").write_text("updated", encoding="utf-8")
    _publish_staged_tree(staged, result)
    assert (result / "checkpoint.json").read_text(encoding="utf-8") == "updated"
    assert (result / "summary.json").read_text(encoding="utf-8") == "new"
    assert not staged.exists()
    source = Path("scripts/export_rcmf_one_demo_retrain_audit_11b.py").read_text()
    assert '"condition_manifest_sha256": sha256_file(condition_manifest_path)' in source
    assert '"elapsed_seconds": float(new_payload["elapsed_seconds"])' in source
    assert '"paired_outcomes_sha256": sha256_file(paired_outcomes_path)' in source


def test_exp034a_paired_sets_preserve_exact_task_identities() -> None:
    task_ids = ["a_1", "b_1", "c_1", "d_1"]
    result = _paired_sets(
        task_ids,
        {"a_1": True, "b_1": True, "c_1": False, "d_1": False},
        {"a_1": True, "b_1": False, "c_1": True, "d_1": False},
    )
    assert result == {
        "both_success": ["a_1"],
        "left_only": ["b_1"],
        "right_only": ["c_1"],
        "both_failed": ["d_1"],
    }
