from __future__ import annotations

import json
from pathlib import Path

from rcmf.config import load_config
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import sha256_file
from scripts.prepare_rcmf_appworld_testnormal_execution_13c import (
    RUN_UUID,
    SCIENTIFIC_CONFIG_KEYS,
    atomic_resume_fixture,
    authorized_runtime_preflight,
    condition_manifest_for_run,
)


CONFIG = Path("configs/benchmark/stage_c_rcmf_appworld_testnormal_execution_13c.yaml")
AUTHORIZATION = Path(
    "research/results/exp036c_appworld_testnormal_final/runtime_authorization_200h.json"
)


def test_13c_changes_only_authorization_and_run_metadata() -> None:
    old = load_config(
        "configs/benchmark/stage_c_rcmf_appworld_testnormal_deterministic_13b.yaml"
    ).raw
    new = load_config(CONFIG).raw
    assert new["stage_c_13a"]["run_uuid"] == RUN_UUID
    assert new["stage_c_13a"]["runtime"]["approved_wall_hours"] == 200.0
    assert new["stage_c_13a"]["runtime"]["hard_stop_wall_hours"] == 200.0
    for key in SCIENTIFIC_CONFIG_KEYS:
        assert new["stage_c_13a"][key] == old["stage_c_13a"][key]
    assert new["stage_c_9a"]["appworld"] == old["stage_c_9a"]["appworld"]


def test_runtime_code_is_exactly_the_validated_13b_path() -> None:
    cfg = load_config(CONFIG).raw["stage_c_13a"]
    continuation = cfg["continuation"]
    paths = {
        "launcher": Path(cfg["determinism"]["launcher_path"]),
        "formal_runner": Path(cfg["determinism"]["runner_entrypoint"]),
        "runtime_module": Path(
            "rcmf/training/rcmf_appworld_testnormal_deterministic_13b.py"
        ),
    }
    assert all(
        sha256_file(path) == continuation[f"{name}_sha256"]
        for name, path in paths.items()
    )


def test_runtime_authorization_is_separate_and_explicit() -> None:
    record = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    assert record["run_uuid"] == RUN_UUID
    assert record["hard_cap_wall_hours"] == 200.0
    assert record["old_42_hour_cap_superseded"] is True
    assert record["no_scientific_setting_changed"] is True


def test_authorized_preflight_preserves_measurements_and_raises_only_cap() -> None:
    source = {
        "format": "old",
        "expected_total_wall_hours": 32.8593,
        "conservative_total_wall_hours": 50.3493,
        "approved_wall_hours": 42.0,
        "automatic_launch_allowed": False,
        "report_sha256": "old",
    }
    result = authorized_runtime_preflight(source, authorization_sha256="auth")
    assert result["expected_total_wall_hours"] == 32.8593
    assert result["conservative_total_wall_hours"] == 50.3493
    assert result["approved_wall_hours"] == 200.0
    assert result["automatic_launch_allowed"] is True
    assert result["report_sha256"] == canonical_sha256(
        {key: value for key, value in result.items() if key != "report_sha256"}
    )


def test_condition_manifest_change_is_run_uuid_only() -> None:
    source = {"run_uuid": "old", "task_ids": ["a"], "manifest_sha256": "old"}
    expected = {"run_uuid": RUN_UUID, "task_ids": ["a"]}
    expected["manifest_sha256"] = canonical_sha256(expected)
    assert condition_manifest_for_run(source, RUN_UUID) == expected


def test_atomic_resume_fixture_is_self_authenticating() -> None:
    row = atomic_resume_fixture(
        source_head="a" * 40,
        config_sha256="config",
        condition_manifest_sha256="manifest",
    )
    assert row["non_scientific"] is True
    assert row["audit_complete"] is True
    assert row["result_sha256"] == canonical_sha256(
        {key: value for key, value in row.items() if key != "result_sha256"}
    )

