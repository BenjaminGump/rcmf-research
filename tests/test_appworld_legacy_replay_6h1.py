from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.training.appworld_legacy_replay_6h1 import (
    LEGACY_REPLAY_CONTRACT_VERSION,
    LOCKED_NORMALIZATION_VERSION,
    build_sentinel_manifest,
    canonical_hash,
    normalize_observation_locked,
    summarize_replay_results,
    upgrade_replay_contract,
    validate_legacy_runtime,
    validate_replay_contract,
    venv_root_from_executable,
)
from rcmf.training.procedural_causal_audit_6h import normalize_observation
from scripts.appworld_legacy_replay_bridge_6h1 import (
    _full_demo_task_query,
    _task_identity_checks,
)
from scripts.prepare_appworld_legacy_environment_6h1 import (
    _select_compatible_runtime,
    _verification_passed,
)
from scripts.run_appworld_legacy_replay_6h1 import _decision, _effective_contract


def _contract() -> dict:
    actions = [
        {
            "step_id": 1,
            "is_target": False,
            "code": "print(1)",
            "expected_observation": "1",
        },
        {
            "step_id": 2,
            "is_target": True,
            "code": "print(2)",
            "expected_observation": "2",
        },
    ]
    return {
        "format": LEGACY_REPLAY_CONTRACT_VERSION,
        "normalization_version": LOCKED_NORMALIZATION_VERSION,
        "state_example_id": "state",
        "task_id": "task",
        "target_step": 2,
        "history_step_count": 1,
        "expected_task_query": "Now here is another task in a different environment.",
        "legacy_python": "/home/ubuntu/venvs/appworld-0.1.0-replay/bin/python",
        "appworld_root": "/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root",
        "experiment_name": "base",
        "actions": actions,
        "actions_sha256": canonical_hash(actions),
    }


def _old_row(state_id: str, task_id: str, history: int, divergence: int | None) -> dict:
    return {
        "state_example_id": state_id,
        "task_id": task_id,
        "step_id": history + 1,
        "history_step_count": history,
        "history_checks": [
            {"step_id": index, "observation_match": divergence != index}
            for index in range(1, history + 1)
        ],
    }


def _result(state_id: str, task_id: str, *, passed: bool, history: int = 1) -> dict:
    steps = [
        {
            "step_id": index,
            "is_target": index == history + 1,
            "normalized_match": passed,
            "raw_match": passed,
        }
        for index in range(1, history + 2)
    ]
    return {
        "state_example_id": state_id,
        "task_id": task_id,
        "steps": steps,
        "initial_task_identity_match": True,
        "complete_history_match": passed,
        "target_observation_match": passed,
        "target_raw_observation_match": passed,
        "first_divergence_step": None if passed else 1,
        "passed": passed,
    }


def test_locked_observation_normalization_is_exactly_exp024a() -> None:
    values = [
        "Output:\n```\n{'b': 2, 'a': 1}\n```",
        '{"a": 1, "b": 2}',
        "plain text  \nnext",
    ]
    assert [normalize_observation_locked(value) for value in values] == [
        normalize_observation(value) for value in values
    ]


def test_contract_preserves_replay_order_and_target_identity() -> None:
    contract = _contract()
    validate_replay_contract(contract)
    changed = json.loads(json.dumps(contract))
    changed["actions"][0]["step_id"] = 2
    changed["actions_sha256"] = canonical_hash(changed["actions"])
    with pytest.raises(ValueError, match="order"):
        validate_replay_contract(changed)


def test_effective_contract_changes_only_attempt_experiment_identity() -> None:
    base = _contract()
    result = _effective_contract(base, attempt_id="attempt-002", run_uuid="run")
    assert result["attempt_id"] == "attempt-002"
    assert "attempt-002" in result["experiment_name"]
    assert result["actions"] == base["actions"]
    assert result["actions_sha256"] == base["actions_sha256"]


def test_v1_contract_upgrade_preserves_full_task_query() -> None:
    contract = _contract()
    task_query = contract.pop("expected_task_query")
    contract["expected_task_instruction"] = task_query
    contract["format"] = "appworld_legacy_replay_contract_6h1_v1"
    upgraded = upgrade_replay_contract(contract)
    assert upgraded["format"] == LEGACY_REPLAY_CONTRACT_VERSION
    assert upgraded["expected_task_query"] == task_query
    assert "expected_task_instruction" not in upgraded


def test_full_demo_query_identity_compares_equivalent_boundaries() -> None:
    supervisor = {
        "first_name": "Melissa",
        "last_name": "Bailey",
        "email": "mel.bailey@gmail.com",
        "phone_number": "3383946795",
    }
    instruction = "Like all matching transactions."
    query = _full_demo_task_query(instruction, supervisor)
    contract = {"task_id": "2a163ab_1", "expected_task_query": query}
    metadata = {
        "task_id": "2a163ab_1",
        "db_version": "0.1.0",
        "instruction": instruction,
        "supervisor": supervisor,
    }
    assert all(_task_identity_checks(contract, metadata).values())
    assert metadata["instruction"] != query


def test_sentinel_includes_no_history_each_task_step_two_and_extremes() -> None:
    rows = [
        _old_row("zero-a", "task-a", 0, None),
        _old_row("zero-b", "task-b", 0, None),
        _old_row("a-step2", "task-a", 3, 2),
        _old_row("b-step2", "task-b", 4, 2),
        _old_row("c-step1", "task-c", 7, 1),
    ]
    manifest = build_sentinel_manifest(rows)
    selected = {row["state_example_id"]: row for row in manifest["rows"]}
    assert {"zero-a", "zero-b"}.issubset(selected)
    assert manifest["task_count"] == 3
    assert sum("step_2_divergence" in row["selection_reasons"] for row in selected.values()) == 2
    assert any("early" in row["selection_reasons"] for row in selected.values())
    assert any("late" in row["selection_reasons"] for row in selected.values())


def test_runtime_rejects_current_python_or_nonlegacy_root(tmp_path: Path) -> None:
    executable = tmp_path / "appworld-0.1.0-replay" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    root = tmp_path / "appworld_legacy" / "0.1.0" / "root"
    root.mkdir(parents=True)
    validate_legacy_runtime(executable=executable, root=root)
    with pytest.raises(ValueError, match="aliases"):
        validate_legacy_runtime(
            executable=executable,
            root=root,
            current_executable=executable,
        )


def test_venv_root_is_lexical_and_does_not_follow_python_symlink() -> None:
    executable = Path("/home/ubuntu/venvs/appworld-0.1.0-replay/bin/python")
    assert venv_root_from_executable(executable).as_posix().endswith(
        "/home/ubuntu/venvs/appworld-0.1.0-replay"
    )


def test_replay_summary_rejects_duplicate_state_keys() -> None:
    row = _result("state", "task", passed=True)
    with pytest.raises(ValueError, match="duplicate"):
        summarize_replay_results([row, row])


def test_no_history_sentinel_failure_blocks_full_replay() -> None:
    summary = summarize_replay_results([_result("zero", "task", passed=False, history=0)])
    summary["no_history_rows"] = [{"state_example_id": "zero", "passed": False}]
    decision = _decision(summary, phase="sentinel", expected_states=45)
    assert decision["decision_branch"] == "appworld_010_initial_data_or_task_snapshot_mismatch"
    assert not decision["full_replay_allowed"]


def test_bridge_is_standalone_and_has_no_qwen_or_current_appworld_import() -> None:
    source = Path("scripts/appworld_legacy_replay_bridge_6h1.py").read_text(encoding="utf-8")
    assert "transformers" not in source
    assert "Qwen" not in source
    assert "rcmf" not in source
    assert "from appworld import AppWorld" in source
    assert "with AppWorld(" in source
    assert "APPWORLD_ROOT" in source
    assert "sys.executable" in source
    assert "world.execute" in source


def test_config_locks_version_triple_and_paths() -> None:
    text = Path("configs/benchmark/stage_c_appworld_legacy_replay_6h1.yaml").read_text(
        encoding="utf-8"
    )
    assert text.count("0.1.0") >= 6
    assert "appworld-0.1.0-replay" in text
    assert "appworld_legacy/0.1.0/root" in text
    assert "128bdb088bd1c76b8ac763e831334f0843507e9e5a5e2e88ec4e2949e2e5d476" in text


def test_official_verification_requires_positive_semantic_result() -> None:
    assert _verification_passed("All tests passed.", 0, kind="tests")
    assert not _verification_passed("Some tests failed.", 0, kind="tests")
    assert _verification_passed("Passed 20/20 tasks.", 0, kind="tasks")
    assert not _verification_passed("Passed 19/20 tasks.\nFailed task_ids:", 0, kind="tasks")


def test_official_wheel_typing_self_promotes_isolated_runtime_to_py311(
    tmp_path: Path,
) -> None:
    import zipfile

    wheel = tmp_path / "appworld.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("appworld/environment.py", "from typing import Any, Self\n")
    requested = {
        "python_version": "3.10",
        "seed_python": "/usr/bin/python3.10",
        "venv": "/home/ubuntu/venvs/appworld-0.1.0-replay",
        "executable": "/home/ubuntu/venvs/appworld-0.1.0-replay/bin/python",
        "appworld_cli": "/home/ubuntu/venvs/appworld-0.1.0-replay/bin/appworld",
    }
    effective, provenance = _select_compatible_runtime(requested, wheel)
    assert effective["python_version"] == "3.11"
    assert effective["venv"].endswith("-py311-click817")
    assert effective["dependency_constraints"] == {"click": "8.1.7"}
    assert provenance["runtime_profile"] == "py311_click817"
    assert provenance["runtime_changed"]
    assert not provenance["scientific_parameter_changed"]
