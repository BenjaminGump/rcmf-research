from __future__ import annotations

from collections import defaultdict
import math
import statistics
from typing import Any, Mapping, Sequence

import torch

from rcmf.training.signature_balanced_field_7c import pearson, spearman


GLOBAL_SEED = 25101
EXTENSION_CHECKPOINTS = (16, 32, 48, 64)
PROGRAM_GAINS = (0.25, 0.50, 0.75, 1.00)


def _rho(summary: Mapping[str, Any]) -> float:
    return float(summary.get("u_text_vs_u_student_spearman") or 0.0)


def _huber(summary: Mapping[str, Any]) -> float:
    return float(summary["sequence_utility_huber"]["mean"])


def huber_reduction(correct: Mapping[str, Any], zero: Mapping[str, Any]) -> float:
    return 1.0 - _huber(correct) / max(_huber(zero), 1.0e-12)


def continuation_decision(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    train_loss_previous: float,
    train_loss_current: float,
) -> dict[str, Any]:
    previous_huber = _huber(previous)
    current_huber = _huber(current)
    previous_rho = _rho(previous)
    current_rho = _rho(current)
    huber_improvement = (previous_huber - current_huber) / max(
        abs(previous_huber), 1.0e-12
    )
    huber_worsening = (current_huber - previous_huber) / max(
        abs(previous_huber), 1.0e-12
    )
    train_improvement = (train_loss_previous - train_loss_current) / max(
        abs(train_loss_previous), 1.0e-12
    )
    criterion_a = huber_improvement >= 0.05 and current_rho >= previous_rho - 0.03
    criterion_b = current_rho - previous_rho >= 0.03 and huber_worsening <= 0.05
    clear_divergence = (
        train_improvement >= 0.05
        and huber_worsening > 0.05
        and current_rho < previous_rho - 0.03
    )
    ratio = float(current["delta_ratio"]["max"])
    finite = all(
        math.isfinite(value)
        for value in (current_huber, current_rho, ratio, train_loss_current)
    )
    checks = {
        "criterion_a_huber_gain_and_spearman_stability": criterion_a,
        "criterion_b_spearman_gain_and_huber_stability": criterion_b,
        "ratio_lte_1": ratio <= 1.0001,
        "finite": finite,
        "no_clear_train_validation_divergence": not clear_divergence,
    }
    return {
        "checks": checks,
        "relative_huber_improvement": huber_improvement,
        "spearman_change": current_rho - previous_rho,
        "relative_train_loss_improvement": train_improvement,
        "clear_train_validation_divergence": clear_divergence,
        "continue": (criterion_a or criterion_b)
        and checks["ratio_lte_1"]
        and finite
        and not clear_divergence,
    }


def select_checkpoint(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [
        row
        for row in history
        if int(row["updates_per_pair"]) in EXTENSION_CHECKPOINTS
        and _rho(row["a_validation"]["correct"]) >= 0.30
        and math.isfinite(_huber(row["a_validation"]["correct"]))
    ]
    if not eligible:
        raise ValueError("No visited checkpoint satisfies the A-validation selection gate")
    selected = min(
        eligible,
        key=lambda row: (
            _huber(row["a_validation"]["correct"]),
            -_rho(row["a_validation"]["correct"]),
            int(row["updates_per_pair"]),
        ),
    )
    return {
        "selected_updates_per_pair": int(selected["updates_per_pair"]),
        "selected_huber": _huber(selected["a_validation"]["correct"]),
        "selected_spearman": _rho(selected["a_validation"]["correct"]),
        "eligible_updates_per_pair": [
            int(row["updates_per_pair"])
            for row in sorted(eligible, key=lambda row: int(row["updates_per_pair"]))
        ],
    }


def select_program_gain(
    candidates: Mapping[float, Mapping[str, Any]],
) -> dict[str, Any]:
    eligible = [
        (float(gamma), summary)
        for gamma, summary in candidates.items()
        if float(gamma) in PROGRAM_GAINS
        and _rho(summary) >= 0.30
        and float(summary["delta_ratio"]["max"]) <= 1.0001
        and math.isfinite(_huber(summary))
    ]
    if not eligible:
        raise ValueError("No global program gain satisfies the A-validation gate")
    gamma, summary = min(
        eligible,
        key=lambda item: (_huber(item[1]), -_rho(item[1]), item[0]),
    )
    return {
        "selected_gamma": gamma,
        "selected_huber": _huber(summary),
        "selected_spearman": _rho(summary),
        "eligible_gammas": sorted(value for value, _ in eligible),
    }


def calibration_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Calibration audit requires saved prediction rows")
    teacher = [float(row["u_text"]) for row in rows]
    student = [float(row["u_student"]) for row in rows]
    teacher_mean = statistics.fmean(teacher)
    student_mean = statistics.fmean(student)
    student_variance = statistics.fmean(
        (value - student_mean) ** 2 for value in student
    )
    covariance = statistics.fmean(
        (left - student_mean) * (right - teacher_mean)
        for left, right in zip(student, teacher)
    )
    slope = 0.0 if student_variance <= 1.0e-18 else covariance / student_variance
    intercept = teacher_mean - slope * student_mean
    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"teacher": [], "student": []}
    )
    for row, teacher_value, student_value in zip(rows, teacher, student):
        category = str(row["utility_category"])
        grouped[category]["teacher"].append(teacher_value)
        grouped[category]["student"].append(student_value)
    return {
        "format": "factorized_program_calibration_audit_7dg2_v1",
        "row_count": len(rows),
        "teacher_utility": {
            "mean": teacher_mean,
            "std": statistics.pstdev(teacher),
        },
        "student_utility": {
            "mean": student_mean,
            "std": statistics.pstdev(student),
        },
        "mean_bias_student_minus_teacher": student_mean - teacher_mean,
        "pearson": pearson(teacher, student),
        "spearman": spearman(teacher, student),
        "teacher_from_student_least_squares": {
            "slope": slope,
            "intercept": intercept,
        },
        "category_means": {
            category: {
                "count": len(values["teacher"]),
                "teacher_mean": statistics.fmean(values["teacher"]),
                "student_mean": statistics.fmean(values["student"]),
            }
            for category, values in sorted(grouped.items())
        },
    }


def validate_resume_checkpoint(
    payload: Mapping[str, Any],
    *,
    expected_pair_ids: Sequence[str],
    expected_split_sha256: str,
    expected_initial_decoder_sha256: str,
    expected_source_commit: str,
) -> dict[str, Any]:
    counts = [int(value) for value in payload.get("update_counts", [])]
    optimizer = payload.get("optimizer_state_dict", {})
    optimizer_state = optimizer.get("state", {}) if isinstance(optimizer, Mapping) else {}
    groups = optimizer.get("param_groups", []) if isinstance(optimizer, Mapping) else []
    finite_optimizer = True
    for values in optimizer_state.values():
        for value in values.values():
            if torch.is_tensor(value) and not bool(torch.isfinite(value).all()):
                finite_optimizer = False
    checks = {
        "format": payload.get("format") == "direct_behavior_program_checkpoint_7dg_v1",
        "model_name": payload.get("model_name")
        == "full_factorized_r16_observation_excluded",
        "global_seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
        "pair_identity_and_order": list(payload.get("pair_ids", []))
        == list(expected_pair_ids),
        "pair_count_479": len(expected_pair_ids) == 479,
        "all_pairs_exactly_u16": len(counts) == len(expected_pair_ids)
        and bool(counts)
        and min(counts) == max(counts) == 16,
        "completed_rounds_u16": int(payload.get("completed_rounds", -1)) == 16,
        "split_sha256": str(payload.get("split_sha256"))
        == str(expected_split_sha256),
        "initial_decoder_sha256": str(payload.get("initial_decoder_sha256"))
        == str(expected_initial_decoder_sha256),
        "source_commit": str(payload.get("source_commit"))
        == str(expected_source_commit),
        "model_parameters_present": bool(payload.get("model_state_dict")),
        "decoder_parameters_present": bool(payload.get("decoder_state_dict")),
        "adam_state_present": bool(optimizer_state) and len(groups) == 2,
        "adam_state_finite": finite_optimizer,
        "python_rng_present": payload.get("python_random_state") is not None,
        "torch_rng_present": torch.is_tensor(payload.get("torch_rng_state")),
        "cuda_rng_present": len(payload.get("cuda_rng_state", [])) == 1,
    }
    return {"checks": checks, "passed": all(checks.values())}


def factorized_extension_gate(
    *,
    a_validation: Mapping[str, Mapping[str, Any]],
    cells: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    b = cells["B"]
    e = cells["E"]
    checks = {
        "A_huber_reduction_positive": huber_reduction(
            a_validation["correct"], a_validation["zero"]
        )
        > 0.0,
        "B_spearman_gte_0_15": _rho(b["correct"]) >= 0.15,
        "B_huber_reduction_positive": huber_reduction(b["correct"], b["zero"])
        > 0.0,
        "B_beats_transition_shuffle": _huber(b["correct"])
        < _huber(b["transition_shuffle"]),
        "B_beats_memory_swap": _huber(b["correct"])
        < _huber(b["memory_swap"]),
        "E_spearman_gte_0_15": _rho(e["correct"]) >= 0.15,
        "E_huber_reduction_positive": huber_reduction(e["correct"], e["zero"])
        > 0.0,
        "E_beats_transition_shuffle": _huber(e["correct"])
        < _huber(e["transition_shuffle"]),
        "E_beats_memory_swap": _huber(e["correct"])
        < _huber(e["memory_swap"]),
        "ratio_lte_1": all(
            float(control["delta_ratio"]["max"]) <= 1.0001
            for values in [a_validation, *cells.values()]
            for control in values.values()
        ),
    }
    return {
        "checks": checks,
        "huber_reduction": {
            "A_validation": huber_reduction(
                a_validation["correct"], a_validation["zero"]
            ),
            **{
                cell: huber_reduction(values["correct"], values["zero"])
                for cell, values in cells.items()
            },
        },
        "passed": all(checks.values()),
    }


def runtime_projection(
    *,
    measured_u8_to_u16_seconds: float,
    a_validation_pairs: int,
    final_cell_pairs: int,
    one_step_conditions: int,
    checkpoint_bytes: int,
    rates: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    phases = {}
    for scenario in ("best", "expected", "conservative"):
        multiplier = {"best": 0.90, "expected": 1.0, "conservative": 1.15}[
            scenario
        ]
        interval_seconds = 2.0 * float(measured_u8_to_u16_seconds) * multiplier
        calibration_forwards = 5 * int(a_validation_pairs)
        final_forwards = 6 * int(final_cell_pairs)
        calibration_seconds = calibration_forwards * float(rates[scenario]["forward"])
        final_seconds = final_forwards * float(rates[scenario]["forward"])
        one_step_seconds = int(one_step_conditions) * float(
            rates[scenario]["generation"]
        )
        total = 3 * interval_seconds + calibration_seconds + final_seconds + one_step_seconds
        phases[scenario] = {
            "u16_to_u32_h100_hours": interval_seconds / 3600.0,
            "u32_to_u48_h100_hours": interval_seconds / 3600.0,
            "u48_to_u64_h100_hours": interval_seconds / 3600.0,
            "calibration_forward_count": calibration_forwards,
            "calibration_h100_hours": calibration_seconds / 3600.0,
            "final_BCDE_forward_count": final_forwards,
            "final_BCDE_h100_hours": final_seconds / 3600.0,
            "optional_H1_H4_generation_count": int(one_step_conditions),
            "optional_H1_H4_h100_hours": one_step_seconds / 3600.0,
            "maximum_total_additional_h100_hours": total / 3600.0,
        }
    return {
        "format": "factorized_program_extension_runtime_projection_7dg2_v1",
        "measured_u8_to_u16_seconds": float(measured_u8_to_u16_seconds),
        "scenarios": phases,
        "projected_checkpoint_bytes": 3 * int(checkpoint_bytes),
    }
