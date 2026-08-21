from __future__ import annotations

import pytest
import torch
from pathlib import Path

from scripts.run_state_conditioned_program_direct_extend_7dg2 import (
    _atomic_row_directory_rows,
)

from rcmf.config import load_config
from rcmf.training.state_conditioned_program_direct_extend_7dg2 import (
    GLOBAL_SEED,
    calibration_audit,
    continuation_decision,
    factorized_extension_gate,
    runtime_projection,
    select_checkpoint,
    select_program_gain,
    validate_resume_checkpoint,
)


def _summary(rho: float, huber: float, ratio: float = 0.5) -> dict:
    return {
        "u_text_vs_u_student_spearman": rho,
        "sequence_utility_huber": {"mean": huber},
        "delta_ratio": {"max": ratio},
    }


def test_atomic_teacher_row_directory_loader_reads_json_rows(tmp_path: Path) -> None:
    rows_dir = tmp_path / "rows"
    rows_dir.mkdir()
    (rows_dir / "b.json").write_text('{"pair_id":"pair-b"}', encoding="utf-8")
    (rows_dir / "a.json").write_text('{"pair_id":"pair-a"}', encoding="utf-8")
    assert [row["pair_id"] for row in _atomic_row_directory_rows(rows_dir)] == [
        "pair-a",
        "pair-b",
    ]
    with pytest.raises(NotADirectoryError):
        _atomic_row_directory_rows(tmp_path / "missing")


def test_config_locks_parent_checkpoint_seed_schedule_and_gain() -> None:
    cfg = load_config(
        "configs/benchmark/stage_c_state_conditioned_program_direct_extend_7dg2.yaml"
    )
    settings = cfg.raw["stage_c_7dg2"]
    assert settings["global_seed"] == GLOBAL_SEED == 25101
    assert settings["checkpoints"] == [32, 48, 64]
    assert settings["program_gains"] == [0.25, 0.50, 0.75, 1.00]
    assert settings["expected_parent_checkpoint_sha256"] == (
        "9433518d828930dfc31e63d18f5477ba563b8870cb4a91ec3665f6890c5e90ff"
    )
    assert settings["review_threshold_h100_hours"] == 14.0


def test_continuation_uses_either_preregistered_rule() -> None:
    by_huber = continuation_decision(
        _summary(0.40, 1.0),
        _summary(0.38, 0.94),
        train_loss_previous=1.0,
        train_loss_current=0.9,
    )
    by_rho = continuation_decision(
        _summary(0.35, 1.0),
        _summary(0.39, 1.04),
        train_loss_previous=1.0,
        train_loss_current=0.98,
    )
    stopped = continuation_decision(
        _summary(0.40, 1.0),
        _summary(0.35, 1.08),
        train_loss_previous=1.0,
        train_loss_current=0.8,
    )
    assert by_huber["continue"]
    assert by_rho["continue"]
    assert not stopped["continue"]
    assert stopped["clear_train_validation_divergence"]


def test_checkpoint_and_gain_selection_are_a_validation_only() -> None:
    history = [
        {
            "updates_per_pair": updates,
            "a_validation": {"correct": _summary(rho, huber)},
        }
        for updates, rho, huber in (
            (16, 0.41, 0.25),
            (32, 0.29, 0.10),
            (48, 0.43, 0.20),
        )
    ]
    selected = select_checkpoint(history)
    assert selected["selected_updates_per_pair"] == 48
    gain = select_program_gain(
        {
            0.25: _summary(0.31, 0.21),
            0.50: _summary(0.42, 0.18),
            0.75: _summary(0.29, 0.10),
            1.00: _summary(0.40, 0.25),
        }
    )
    assert gain["selected_gamma"] == 0.50


def test_calibration_audit_reports_bias_slope_and_category_means() -> None:
    rows = [
        {"u_text": -1.0, "u_student": -0.25, "utility_category": "negative"},
        {"u_text": 0.0, "u_student": 0.25, "utility_category": "neutral"},
        {"u_text": 1.0, "u_student": 0.75, "utility_category": "positive"},
    ]
    audit = calibration_audit(rows)
    assert audit["row_count"] == 3
    assert audit["mean_bias_student_minus_teacher"] == pytest.approx(0.25)
    assert audit["teacher_from_student_least_squares"]["slope"] == pytest.approx(2.0)
    assert audit["teacher_from_student_least_squares"]["intercept"] == pytest.approx(-0.5)
    assert audit["category_means"]["positive"]["count"] == 1


def test_resume_contract_requires_exact_u16_pair_order_optimizer_and_rng() -> None:
    pair_ids = [f"pair-{index}" for index in range(479)]
    payload = {
        "format": "direct_behavior_program_checkpoint_7dg_v1",
        "model_name": "full_factorized_r16_observation_excluded",
        "global_seed": 25101,
        "pair_ids": pair_ids,
        "update_counts": [16] * len(pair_ids),
        "completed_rounds": 16,
        "split_sha256": "split",
        "initial_decoder_sha256": "decoder",
        "source_commit": "source",
        "model_state_dict": {"a": torch.zeros(1)},
        "decoder_state_dict": {"b": torch.zeros(1)},
        "optimizer_state_dict": {
            "state": {0: {"exp_avg": torch.zeros(1)}},
            "param_groups": [{}, {}],
        },
        "python_random_state": (3, (), None),
        "torch_rng_state": torch.zeros(2, dtype=torch.uint8),
        "cuda_rng_state": [torch.zeros(2, dtype=torch.uint8)],
    }
    report = validate_resume_checkpoint(
        payload,
        expected_pair_ids=pair_ids,
        expected_split_sha256="split",
        expected_initial_decoder_sha256="decoder",
        expected_source_commit="source",
    )
    assert report["passed"]
    payload["update_counts"][-1] = 15
    assert not validate_resume_checkpoint(
        payload,
        expected_pair_ids=pair_ids,
        expected_split_sha256="split",
        expected_initial_decoder_sha256="decoder",
        expected_source_commit="source",
    )["passed"]


def test_extension_gate_requires_positive_b_and_e_calibration_and_controls() -> None:
    def controls(correct_huber: float, rho: float) -> dict:
        return {
            "correct": _summary(rho, correct_huber),
            "static_only": _summary(0.0, 0.95),
            "state_shuffle": _summary(0.0, 0.95),
            "transition_shuffle": _summary(0.0, 0.90),
            "memory_swap": _summary(0.0, 0.92),
            "zero": _summary(0.0, 1.0),
        }

    a = {"correct": _summary(0.35, 0.8), "zero": _summary(0.0, 1.0)}
    cells = {
        "B": controls(0.8, 0.20),
        "C": controls(1.2, -0.10),
        "D": controls(1.2, -0.10),
        "E": controls(0.8, 0.20),
    }
    assert factorized_extension_gate(a_validation=a, cells=cells)["passed"]
    cells["E"]["correct"] = _summary(0.20, 1.1)
    assert not factorized_extension_gate(a_validation=a, cells=cells)["passed"]


def test_measured_runtime_projection_stays_below_authorized_expected_window() -> None:
    rates = {
        "best": {"forward": 1.25, "generation": 6.0},
        "expected": {"forward": 1.85, "generation": 7.85},
        "conservative": {"forward": 3.5, "generation": 12.0},
    }
    report = runtime_projection(
        measured_u8_to_u16_seconds=6509.99,
        a_validation_pairs=128,
        final_cell_pairs=494,
        one_step_conditions=180,
        checkpoint_bytes=308_888_849,
        rates=rates,
    )
    expected = report["scenarios"]["expected"]
    assert expected["u16_to_u32_h100_hours"] == pytest.approx(3.61666, rel=1e-3)
    assert expected["maximum_total_additional_h100_hours"] < 14.0


def test_one_step_path_applies_frozen_global_gain_and_uses_extension_run_uuid() -> None:
    source = Path(
        "scripts/run_state_conditioned_program_direct_one_step_7dg.py"
    ).read_text(encoding="utf-8")
    assert 'program_gain = float(training_summary.get("selected_gamma", 1.0))' in source
    assert "z = z * program_gain" in source
    assert 'run_settings = cfg.raw.get("stage_c_7dg2", settings)' in source
    assert 'run_uuid=str(run_settings["run_uuid"])' in source

