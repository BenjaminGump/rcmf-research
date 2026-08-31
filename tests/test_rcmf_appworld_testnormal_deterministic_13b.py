from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (
    DETERMINISM_MODE,
    REQUIRED_PYTHON_HASH_SEED,
    TASK_RESULT_FORMAT,
    assert_hash_seed_process,
    augment_task_row,
    build_runtime_preflight,
    compare_complete_smoke_rows,
    compare_probe_rows,
    freeze_formal_manifest,
    freeze_hash_seed_mode,
    read_mode_manifest,
    validate_formal_manifest,
    write_process_identity,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file


CONFIG = Path(
    "configs/benchmark/stage_c_rcmf_appworld_testnormal_deterministic_13b.yaml"
)
LAUNCHER = Path("scripts/launch_rcmf_appworld_testnormal_deterministic_13b.sh")


def test_13b_config_changes_only_run_and_determinism_identity() -> None:
    old = load_config(
        "configs/benchmark/stage_c_rcmf_appworld_testnormal_final_13a.yaml"
    ).raw["stage_c_13a"]
    new = load_config(CONFIG).raw["stage_c_13a"]
    assert new["run_uuid"] == "rcmf_appworld_testnormal_final_13b_20260831_001"
    assert new["starting_head"] == "69a218a212f05709ca4c278f1ae14a89b44031a4"
    assert new["determinism"]["current_candidate_mode"] == DETERMINISM_MODE
    assert new["determinism"]["canonicalizer_enabled"] is False
    for key in ("prompt", "test_normal", "packages", "shared"):
        assert new[key] == old[key]


def test_launcher_sets_hash_seed_before_exec() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.index("export PYTHONHASHSEED=25101") < source.index('exec "$@"')
    assert "RCMF_DETERMINISM_LAUNCH_COMMAND" in source
    assert "canonical" not in source.lower()


def test_launcher_fresh_interpreters_share_hash_sentinel() -> None:
    if os.name == "nt":
        pytest.skip("Bash launcher execution is validated on Lambda")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash launcher execution is validated on Lambda")
    code = (
        "import hashlib,os; "
        "v={'exp036b','python','hash','25101','appworld'}; "
        "print(os.environ.get('PYTHONHASHSEED'),hashlib.sha256(repr(v).encode()).hexdigest())"
    )
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    outputs = [
        subprocess.check_output(
            [bash, str(LAUNCHER), sys.executable, "-c", code],
            text=True,
            env=env,
        ).strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    assert outputs[0].startswith("25101 ")


def test_hash_seed_guard_rejects_wrong_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    with pytest.raises(RuntimeError, match="did not start"):
        assert_hash_seed_process()


def test_process_identity_records_parent_and_children(tmp_path: Path) -> None:
    assert os.environ.get("PYTHONHASHSEED") == REQUIRED_PYTHON_HASH_SEED
    identity = write_process_identity(
        artifact_dir=tmp_path,
        attempt_id="unit-process-001",
        launcher_path=LAUNCHER,
        entrypoint_path=Path(__file__),
        legacy_python=Path(sys.executable),
        source_head="a" * 40,
    )
    assert identity["python_hash_seed"] == REQUIRED_PYTHON_HASH_SEED
    assert identity["hash_randomization"] == 1
    assert identity["environment_changed_after_interpreter_start"] is False
    assert set(identity["child_process_inheritance"]) == {
        "execution_python",
        "legacy_appworld_python",
    }
    assert all(
        child["python_hash_seed"] == REQUIRED_PYTHON_HASH_SEED
        for child in identity["child_process_inheritance"].values()
    )


class _Backend:
    @staticmethod
    def tokenize_messages(messages, *, add_generation_prompt):
        assert add_generation_prompt is True
        values = [len(json.dumps(messages, sort_keys=True)), len(messages)]
        return SimpleNamespace(input_ids=torch.tensor([values]))


def _row() -> dict[str, object]:
    step = {
        "complete_environment_observation": "friends = {'a', 'b'}",
        "exact_model_message_array": [{"role": "user", "content": "task"}],
        "generated_token_ids": [1, 2],
        "raw_model_response": "response",
        "exact_executed_code": "print(1)",
        "task_completed_status": False,
        "state_fingerprint_before": "before",
        "state_fingerprint_after": "after",
    }
    return {
        "format": "old",
        "task_id": "task",
        "success": False,
        "step_count": 1,
        "steps": [step],
        "world_identity": {
            "task_id": "task",
            "experiment_name": "unique",
            "fresh_isolated_world": True,
            "appworld_root": "/root",
            "evaluator_success_source": "evaluation.success",
        },
    }


def test_task_row_preserves_one_observation_body_with_two_references() -> None:
    row = augment_task_row(
        row=_row(),
        backend=_Backend(),
        process_identity={"identity_sha256": "p", "artifact_sha256": "pf"},
        mode={"manifest_sha256": "m", "artifact_sha256": "mf"},
        result_format=TASK_RESULT_FORMAT,
    )
    rendering = row["steps"][0]["observation_rendering"]
    assert rendering["bodies_identical"] is True
    assert rendering["raw_body_field"] == "complete_environment_observation"
    assert rendering["model_visible_body_field"] == "complete_environment_observation"
    assert rendering["canonicalization_applied"] is False
    assert rendering["evaluator_state_modified"] is False
    assert row["steps"][0]["complete_environment_observation"] == "friends = {'a', 'b'}"
    assert row["result_sha256"] == canonical_sha256(
        {key: value for key, value in row.items() if key != "result_sha256"}
    )


def test_probe_comparison_covers_prompts_tokens_responses_code_and_world() -> None:
    left = augment_task_row(
        row=_row(),
        backend=_Backend(),
        process_identity={"identity_sha256": "p1", "artifact_sha256": "p1f"},
        mode={"manifest_sha256": "m", "artifact_sha256": "mf"},
        result_format=TASK_RESULT_FORMAT,
    )
    right = copy.deepcopy(left)
    right["world_identity"]["experiment_name"] = "another-fresh-world"
    assert compare_probe_rows(left, right)["passed"] is True
    right["steps"][0]["complete_environment_observation"] = "different"
    result = compare_probe_rows(left, right)
    assert result["passed"] is False
    assert result["checks"]["raw_observations"] is False


def _complete_smoke_row() -> dict[str, object]:
    row = augment_task_row(
        row=_row(),
        backend=_Backend(),
        process_identity={"identity_sha256": "p", "artifact_sha256": "pf"},
        mode={"manifest_sha256": "m", "artifact_sha256": "mf"},
        result_format=TASK_RESULT_FORMAT,
    )
    row.update(
        {
            "model_identity": {"model": "qwen"},
            "deployment_field_sha256": "field",
            "query_encoder_sha256": "query-encoder",
            "package_manifest_sha256": "package",
            "exp036a_package": "BEST",
            "exp036a_binding": "correct",
            "generation_settings": {"seed": 25101},
        }
    )
    row["steps"][0].update(
        {
            "extracted_code": "print(1)",
            "automatically_repaired_response": "response",
            "automatically_repaired_code": "print(1)",
            "field": {
                "state_views": {"sha256": "views"},
                "query": {"sha256": "query"},
                "slots": {"sha256": "slots"},
                "deployment_field_sha256": "field",
                "complete_bank_memory_count": 499,
                "field_control": "correct",
                "runtime_memory_retrieval": False,
                "runtime_per_memory_scoring": False,
            },
            "reader_audit": {"active": True, "delta_norms": {"7": [1.0]}},
        }
    )
    return row


def test_complete_smoke_comparison_covers_component_and_field_identities() -> None:
    left = _complete_smoke_row()
    right = copy.deepcopy(left)
    right["world_identity"]["experiment_name"] = "fresh-repeat"
    assert compare_complete_smoke_rows(left, right)["passed"] is True
    right["steps"][0]["field"]["slots"]["sha256"] = "different"
    result = compare_complete_smoke_rows(left, right)
    assert result["passed"] is False
    assert result["checks"]["query_field_identities"] is False


def test_freeze_hash_seed_mode_requires_both_conditions_and_processes(tmp_path: Path) -> None:
    task_id = "task"
    row = augment_task_row(
        row=_row(),
        backend=_Backend(),
        process_identity={"identity_sha256": "p", "artifact_sha256": "pf"},
        mode={"manifest_sha256": "m", "artifact_sha256": "mf"},
        result_format=TASK_RESULT_FORMAT,
    )
    for condition in ("B0", "FULL1D-S"):
        for label in ("A", "B"):
            path = (
                tmp_path
                / "determinism_probe"
                / f"process_{label}"
                / "conditions"
                / condition
                / "task_results"
                / f"{task_id}.json"
            )
            atomic_write_json(path, row)
    root_cause = tmp_path / "root_cause.json"
    root_cause.write_text("{}\n", encoding="utf-8")
    launcher = tmp_path / "launcher.sh"
    launcher.write_text("export PYTHONHASHSEED=25101\n", encoding="utf-8")
    mode = freeze_hash_seed_mode(
        artifact_dir=tmp_path,
        task_id=task_id,
        launcher_path=launcher,
        root_cause_path=root_cause,
    )
    assert mode["mode"] == DETERMINISM_MODE
    assert mode["canonicalizer"] == {"enabled": False, "identity": "disabled"}
    loaded = read_mode_manifest(tmp_path)
    assert loaded["manifest_sha256"] == mode["manifest_sha256"]
    assert loaded["artifact_sha256"] == sha256_file(
        tmp_path / "manifests/determinism_mode.json"
    )


def test_13b_has_no_set_canonicalizer_implementation() -> None:
    source = Path(
        "rcmf/training/rcmf_appworld_testnormal_deterministic_13b.py"
    ).read_text(encoding="utf-8")
    assert "canonicalize_model_visible_observation" not in source
    assert 'DETERMINISM_MODE = "hash_seed_only"' in source
    assert '"evaluator_state_modified": False' in source


def test_old_13a_config_and_runner_remain_default() -> None:
    old = load_config(
        "configs/benchmark/stage_c_rcmf_appworld_testnormal_final_13a.yaml"
    ).raw["stage_c_13a"]
    assert "determinism" not in old
    source = Path("scripts/run_rcmf_appworld_testnormal_final_13a.py").read_text(
        encoding="utf-8"
    )
    assert "rcmf_appworld_testnormal_task_13a_v1" in source
    assert "PYTHONHASHSEED" not in source


def test_runtime_preflight_is_condition_stratified_and_enforces_cap(
    tmp_path: Path,
) -> None:
    conditions = ("B0", "BEST-C", "BEST-S", "FULL1D-C", "FULL1D-S")
    rows = [
        {"condition": condition, "wall_seconds": float(10 + index)}
        for index, condition in enumerate(conditions)
        for _ in range(2)
    ]
    settings = {
        "runtime": {
            "approved_wall_hours": 42.0,
            "auxiliary_estimate": {
                "efficiency_expected_hours": 2.5,
                "efficiency_conservative_hours": 4.0,
                "reversibility_expected_hours": 0.1,
                "reversibility_conservative_hours": 0.25,
                "basis": "fixed",
            },
        }
    }
    report = build_runtime_preflight(
        artifact_dir=tmp_path,
        primary_rows=rows,
        deterministic={condition: {"passed": True} for condition in conditions},
        settings=settings,
        mode={"mode": "hash_seed_only", "manifest_sha256": "mode"},
        smoke_task_ids=["a", "b"],
    )
    assert report["formal_condition_count"] == 840
    assert set(report["per_condition_wall_time"]) == set(conditions)
    assert report["automatic_launch_allowed"] is True
    assert report["report_sha256"] == canonical_sha256(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )


def test_formal_manifest_freezes_mode_and_zero_formal_rows(tmp_path: Path) -> None:
    condition_manifest = {
        "manifest_sha256": "logical",
        "task_ids": [f"task-{index}" for index in range(168)],
        "task_list_sha256": "tasks",
    }
    atomic_write_json(
        tmp_path / "manifests" / "condition_manifest.json", condition_manifest
    )
    mode = {
        "mode": "hash_seed_only",
        "manifest_sha256": "mode",
        "launcher": {"sha256": "launcher"},
        "canonicalizer": {"enabled": False, "identity": "disabled"},
        "model_visible_observation_contract": "exact raw observation string",
        "raw_observation_preserved": True,
        "evaluator_state_modified": False,
    }
    frozen = freeze_formal_manifest(
        artifact_dir=tmp_path,
        condition_manifest=condition_manifest,
        mode=mode,
        source_head="a" * 40,
        config_sha256="config",
    )
    assert frozen["formal_rows_generated"] == 0
    assert frozen["smoke_rows_reusable_as_formal"] is False
    loaded = validate_formal_manifest(
        artifact_dir=tmp_path,
        condition_manifest=condition_manifest,
        mode=mode,
    )
    assert loaded == frozen


def test_smoke_runner_requires_fifteen_fresh_process_rows() -> None:
    source = Path("scripts/run_rcmf_appworld_testnormal_smoke_13b.py").read_text(
        encoding="utf-8"
    )
    assert '"process_count": 15' in source
    assert '"fresh_process_per_trajectory": True' in source
    assert "compare_complete_smoke_rows" in source


def test_stop_exporter_preserves_runtime_gate_and_redaction_contract() -> None:
    source = Path(
        "scripts/export_rcmf_appworld_testnormal_stop_audit_13b.py"
    ).read_text(encoding="utf-8")
    assert "STOPPED_BEFORE_FORMAL" in source
    assert "runtime_preflight_42_hour_cap" in source
    assert "compare_complete_smoke_rows" in source
    assert "strict_verify_tree(args.audit_root)" in source
    assert "strict_verify_tree(args.result_root)" in source
    assert "formal_expected_trajectory_count" in source
