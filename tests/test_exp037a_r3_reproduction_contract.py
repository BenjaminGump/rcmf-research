from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.benchmarks.appworld.reproduction_audit_14e import (
    compare_label_cells,
    simulate_historical_adaptive_expansion,
)
from rcmf.benchmarks.appworld.reproduction_contract_14e import (
    reconstruct_historical_selector_parent_split,
    resolved_causal_panel_contract,
    validate_post_d06_expectations_are_not_panel_inputs,
)
from rcmf.benchmarks.appworld.reproducible_config_14b import (
    build_arm_runtime_config,
)
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.run_exp037a_r3_selector_diagnostic import (
    ALLOWED_PIPELINE_STAGES,
    PROHIBITED_STAGE_PREFIXES,
    diagnostic_config,
)


def _contract() -> dict[str, object]:
    return {
        "algorithm": "sha256_order_first_heldout_then_remaining_train",
        "seed": 18018,
        "train_parent_count": 29,
        "heldout_parent_count": 8,
    }


def _transitions() -> list[dict[str, str]]:
    return [
        {
            "parent_memory_id": f"parent-{index:02d}",
            "parent_task_id": f"task-{index:02d}",
        }
        for index in range(37)
    ]


def test_parent_split_is_reconstructed_from_authoritative_parents() -> None:
    first = reconstruct_historical_selector_parent_split(_transitions(), _contract())
    second = reconstruct_historical_selector_parent_split(
        list(reversed(_transitions())), _contract()
    )
    assert first == second
    assert first["format"] == "identity_reconciled_locked_parent_split_7b_v1"
    assert list(first["split_by_parent"].values()).count("train") == 29
    assert list(first["split_by_parent"].values()).count("heldout") == 8
    assert len(first["split_by_parent_task"]) == 37


def test_parent_split_rejects_nonhistorical_contract() -> None:
    changed = {**_contract(), "seed": 25101}
    with pytest.raises(ValueError, match="contract differs"):
        reconstruct_historical_selector_parent_split(_transitions(), changed)


def test_panel_contract_is_256_499_40_and_not_366_98() -> None:
    config = load_resolved(Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"))
    pipeline = config["pipeline"]
    panel = resolved_causal_panel_contract(pipeline)
    assert panel == {
        "initial_state_count": 256,
        "maximum_state_count": 499,
        "minimum_per_label": 40,
    }
    audit = validate_post_d06_expectations_are_not_panel_inputs(pipeline, panel)
    assert audit["passed"]
    assert audit["post_d06_expected_completed"] == {
        "model_train": 366,
        "heldout": 98,
    }


def test_downstream_expected_counts_do_not_control_panel(tmp_path: Path) -> None:
    config = load_resolved(Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"))
    config["pipeline"]["expected"]["downstream_train_states"] = 1
    config["pipeline"]["expected"]["downstream_heldout_states"] = 2
    arm = build_arm_runtime_config(config, tmp_path / "run", "3d")
    panel = arm["stage_c_7hr"]["panel"]
    assert panel["initial_state_count"] == 256
    assert panel["maximum_state_count"] == 499
    assert panel["minimum_per_label"] == 40
    assert panel["post_d06_reproduction_expectation"] == {
        "model_train": 1,
        "heldout": 2,
    }


def test_arm_contract_diff_remains_prompt_only(tmp_path: Path) -> None:
    config = load_resolved(Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"))
    arm_3d = build_arm_runtime_config(config, tmp_path / "run", "3d")
    arm_1d = build_arm_runtime_config(config, tmp_path / "run", "1d")
    assert arm_3d["stage_c_7hr"]["panel"] == arm_1d["stage_c_7hr"]["panel"]
    assert arm_3d["benchmark"]["prompt_profile"] == "full_demo"
    assert arm_1d["benchmark"]["prompt_profile"] == "full_demo_first_only"


def test_label_comparison_is_keyed_and_order_is_diagnostic(tmp_path: Path) -> None:
    rows = [
        {"state_example_id": "s1", "transition_id": "m1", "cell": "A"},
        {"state_example_id": "s1", "transition_id": "m2", "cell": "B"},
    ]
    fresh = tmp_path / "fresh.jsonl"
    historical = tmp_path / "historical.jsonl"
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    fresh.write_text(text, encoding="utf-8")
    historical.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in reversed(rows)),
        encoding="utf-8",
    )
    result = compare_label_cells(fresh, historical, expected_count=2)
    assert result["row_count"] == 2
    assert result["historical_row_count"] == 2
    assert result["pair_order_mismatch_count"] == 2
    assert result["moved_cell_count"] == 0
    assert result["passed"]


def test_label_comparison_detects_cell_movement(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.jsonl"
    historical = tmp_path / "historical.jsonl"
    fresh.write_text(
        json.dumps({"state_example_id": "s1", "transition_id": "m1", "cell": "A"})
        + "\n",
        encoding="utf-8",
    )
    historical.write_text(
        json.dumps({"state_example_id": "s1", "transition_id": "m1", "cell": "B"})
        + "\n",
        encoding="utf-8",
    )
    result = compare_label_cells(fresh, historical, expected_count=1)
    assert result["moved_cell_count"] == 1
    assert not result["passed"]


def test_diagnostic_runner_cannot_cross_d05() -> None:
    assert ALLOWED_PIPELINE_STAGES[-1] == "D05_selected_memory_manifest"
    assert all(
        not stage.startswith(PROHIBITED_STAGE_PREFIXES)
        for stage in ALLOWED_PIPELINE_STAGES
    )


def test_diagnostic_config_revokes_old_long_run_authorization(tmp_path: Path) -> None:
    config = diagnostic_config(
        Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"), tmp_path
    )
    authorization = config["pipeline"]["conditional_runtime_authorization"]
    assert authorization["old_200_hour_authorization_inherited"] is False
    assert authorization["full_pipeline_authorized"] is False
    assert authorization["d06_or_later_authorized"] is False
    assert config["pipeline"]["approved_hard_cap_hours"] == 18


def test_historical_selector_cannot_be_deserialized_by_audit_or_runner() -> None:
    roots = (
        Path("rcmf/benchmarks/appworld/reproduction_audit_14e.py"),
        Path("scripts/run_exp037a_r3_selector_diagnostic.py"),
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in roots)
    assert "torch.load" not in text
    assert "load_state_dict" not in text


def test_historical_panel_simulation_expands_until_quota_or_exhaustion() -> None:
    panel = {
        "state_ids": ["s0", "s1"],
        "expansion_order": ["s2", "s3", "s4"],
    }
    outcomes = {
        "rows": [
            {"state_example_id": "s0", "label": "POSITIVE"},
            {"state_example_id": "s1", "label": "NEUTRAL"},
            {"state_example_id": "s2", "label": "HARMFUL"},
            {"state_example_id": "s3", "label": "POSITIVE"},
        ]
    }
    exhausted = simulate_historical_adaptive_expansion(panel, outcomes, minimum=2)
    assert exhausted["attempted_state_count"] == 5
    assert exhausted["all_logical_states_attempted"]
    assert not exhausted["quota_met"]
    early = simulate_historical_adaptive_expansion(
        panel,
        {"rows": outcomes["rows"] + [{"state_example_id": "s4", "label": "HARMFUL"}]},
        minimum=1,
    )
    assert early["attempted_state_count"] == 3
    assert early["quota_met"]
