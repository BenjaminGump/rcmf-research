from __future__ import annotations

import ast
from pathlib import Path

import pytest
import torch

from rcmf.training.interaction_representation_6c import (
    DecomposedInteractionPredictor,
    MainEffectHeads,
    fit_two_way_decomposition,
    interaction_gate,
    interaction_objective,
    majority_sign_baseline,
    paired_task_bootstrap_contrast,
    per_state_ranking_metrics,
    summarize_revised_predictions,
)
from scripts.run_interaction_representation_6c import _compare_numeric


def _row(
    state: str,
    transition: str,
    utility: float,
    *,
    task: str = "task-a",
    parent: str = "parent-a",
) -> dict[str, object]:
    return {
        "pair_id": f"{state}::{transition}",
        "state_example_id": state,
        "state_task_id": task,
        "transition_id": transition,
        "transition_parent_id": parent,
        "cell": "train_state__train_transition",
        "text_utility": utility,
        "utility_category": (
            "positive" if utility > 0.01 else "negative" if utility < -0.01 else "neutral"
        ),
    }


def _prediction(row: dict[str, object], predicted: float, residual: float = 0.0) -> dict[str, object]:
    return {
        "pair_id": row["pair_id"],
        "state_example_id": row["state_example_id"],
        "state_task_id": row["state_task_id"],
        "transition_id": row["transition_id"],
        "transition_parent_id": row["transition_parent_id"],
        "cell": row["cell"],
        "utility_category": row["utility_category"],
        "u_text": row["text_utility"],
        "u_predicted": predicted,
        "residual_target": residual,
        "residual_predicted": residual,
    }


def test_two_way_decomposition_recovers_additive_signal() -> None:
    state_effects = {"s1": -0.3, "s2": 0.1, "s3": 0.2}
    transition_effects = {"m1": -0.4, "m2": 0.15, "m3": 0.25}
    rows = [
        _row(state, transition, 0.7 + state_value + transition_value)
        for state, state_value in state_effects.items()
        for transition, transition_value in transition_effects.items()
    ]
    result = fit_two_way_decomposition(rows, max_iterations=500, tolerance=1.0e-12)
    assert result["converged"]
    assert result["mu"] == pytest.approx(0.7, abs=1.0e-10)
    assert result["additive_main_effect_variance_explained_r2"] == pytest.approx(1.0)
    assert result["residual_interaction_variance"] == pytest.approx(0.0, abs=1.0e-12)
    assert result["utility_matrix_spectrum"]["shape"] == [3, 3]


def test_two_way_decomposition_preserves_interaction_residual() -> None:
    rows = []
    for state_index, state in enumerate(("s1", "s2", "s3")):
        for transition_index, transition in enumerate(("m1", "m2", "m3")):
            interaction = 0.5 if state_index == transition_index else -0.25
            rows.append(_row(state, transition, interaction))
    result = fit_two_way_decomposition(rows)
    assert result["residual_interaction_variance"] > 0.0
    assert result["residual_matrix_spectrum"]["effective_rank"] > 1.0


def test_majority_sign_baseline_excludes_neutral_rows() -> None:
    rows = [
        *[_row("s", f"p{i}", 0.2) for i in range(117)],
        *[_row("s", f"n{i}", -0.2) for i in range(36)],
        *[_row("s", f"z{i}", 0.0) for i in range(103)],
    ]
    result = majority_sign_baseline(rows)
    assert result["always_positive_accuracy"] == pytest.approx(117 / 153)
    assert result["neutral"] == 103


def test_exp018_reproduction_tolerates_serialization_noise_only() -> None:
    expected = {"metric": 0.25, "nested": {"small": 0.0}}
    serialization_noise = {
        "metric": 0.25 + 1.121539000559224e-9,
        "nested": {"small": -1.5e-9},
    }
    comparison = _compare_numeric(expected, serialization_noise)
    assert comparison["passed"]
    assert comparison["maximum_absolute_difference"] < 2.0e-9

    scientific_difference = _compare_numeric(expected, {"metric": 0.250001})
    assert not scientific_difference["passed"]
    assert scientific_difference["outside_tolerance_numeric_keys"] == ["metric"]
    assert scientific_difference["missing_expected_numeric_keys"] == ["nested.small"]


def test_within_state_metrics_reward_correct_memory_order() -> None:
    rows = []
    for task, state in (("task-a", "s1"), ("task-b", "s2")):
        source = [
            _row(state, "m1", 0.4, task=task),
            _row(state, "m2", 0.1, task=task),
            _row(state, "m3", -0.3, task=task),
        ]
        rows.extend(
            _prediction(row, float(row["text_utility"]), residual=float(row["text_utility"]))
            for row in source
        )
    per_state = per_state_ranking_metrics(rows)
    assert all(row["ndcg@4"] == pytest.approx(1.0) for row in per_state)
    assert all(row["best_recall@1"] == 1.0 for row in per_state)
    assert all(row["positive_vs_negative_pairwise_accuracy"] == 1.0 for row in per_state)
    summary = summarize_revised_predictions(rows)
    assert summary["per_state"]["spearman"]["mean"] == pytest.approx(1.0)
    assert summary["interaction_residual_spearman"] == pytest.approx(1.0)


def test_decomposed_model_keeps_main_heads_frozen_for_ranking_loss() -> None:
    main = MainEffectHeads(state_dim=4, transition_dim=4, hidden_dim=8, dropout=0.0)
    model = DecomposedInteractionPredictor(
        "decomposed_signed_bilinear",
        main_effects=main,
        mu=0.1,
        state_dim=4,
        transition_dim=4,
        hidden_dim=8,
        interaction_dim=3,
        dropout=0.0,
    )
    state = torch.randn(4, 4)
    transition = torch.randn(4, 4)
    utility = torch.tensor([0.4, 0.1, -0.2, -0.4])
    components = model.components(state, transition)
    loss, _ = interaction_objective(
        score=components["score"],
        interaction=components["interaction"],
        utility=utility,
        residual_target=utility,
        state_groups=[[0, 1, 2, 3]],
        residual_huber_delta=0.1,
        utility_huber_delta=0.1,
        teacher_temperature=0.1,
        student_temperature=0.1,
        pair_gap_threshold=0.05,
        pair_gap_clip=5.0,
        loss_weights={
            "residual_huber": 1.0,
            "state_listwise": 0.2,
            "gap_pairwise": 0.2,
            "raw_utility_auxiliary": 0.1,
        },
    )
    loss.backward()
    assert all(parameter.grad is None for parameter in model.main_effects.parameters())
    assert any(
        parameter.grad is not None
        for name, parameter in model.named_parameters()
        if "interaction" in name and "main_effects" not in name
    )


def _gate_metric(ndcg: float, *, raw_spearman: float = 0.4, residual: float = 0.4) -> dict[str, object]:
    return {
        "pooled_raw_spearman": raw_spearman,
        "interaction_residual_spearman": residual,
        "per_state": {
            "spearman": {"mean": 0.4},
            "ndcg@4": {"mean": ndcg},
        },
    }


def test_revised_gate_requires_both_shuffle_axes_and_tasks() -> None:
    thresholds = {
        "pooled_raw_spearman": 0.2,
        "mean_per_state_spearman": 0.2,
        "interaction_residual_spearman": 0.2,
        "ndcg4_single_axis_gain": 0.05,
        "ndcg4_transition_shuffle_drop": 0.08,
        "ndcg4_state_shuffle_drop": 0.08,
        "minimum_positive_heldout_tasks": 3,
    }
    task = {
        f"t{i}": {
            "correct_ndcg@4": 0.8,
            "state_only_ndcg@4": 0.5,
            "transition_only_ndcg@4": 0.5,
            "shuffled_state_ndcg@4": 0.4,
            "shuffled_transition_ndcg@4": 0.4,
        }
        for i in range(4)
    }
    result = interaction_gate(
        candidate=_gate_metric(0.8),
        state_only=_gate_metric(0.5),
        transition_only=_gate_metric(0.45),
        shuffled_state=_gate_metric(0.6),
        shuffled_transition=_gate_metric(0.6),
        per_task=task,
        transition_shuffle_contrast={
            "ndcg@4_correct_minus_control": {"ci95_low": 0.1}
        },
        thresholds=thresholds,
    )
    assert result["passed"]
    failed = interaction_gate(
        candidate=_gate_metric(0.8),
        state_only=_gate_metric(0.5),
        transition_only=_gate_metric(0.45),
        shuffled_state=_gate_metric(0.6),
        shuffled_transition=_gate_metric(0.75),
        per_task=task,
        transition_shuffle_contrast={
            "ndcg@4_correct_minus_control": {"ci95_low": -0.01}
        },
        thresholds=thresholds,
    )
    assert not failed["passed"]
    assert not failed["checks"]["transition_shuffle_drop"]


def test_task_grouped_paired_bootstrap_is_deterministic() -> None:
    correct = []
    control = []
    for task_index in range(4):
        task = f"task-{task_index}"
        state = f"state-{task_index}"
        for transition_index, utility in enumerate((0.4, 0.1, -0.3)):
            row = _row(state, f"m{transition_index}", utility, task=task)
            correct.append(_prediction(row, utility, residual=utility))
            control.append(_prediction(row, -utility, residual=-utility))
    settings = {
        "ranking_ks": [1, 4, 8],
        "neutral_epsilon": 0.01,
        "best_tie_tolerance": 1.0e-8,
        "huber_delta": 0.1,
    }
    first = paired_task_bootstrap_contrast(
        correct, control, samples=50, seed=7, metric_settings=settings
    )
    second = paired_task_bootstrap_contrast(
        correct, control, samples=50, seed=7, metric_settings=settings
    )
    assert first == second
    assert first["ndcg@4_correct_minus_control"]["ci95_low"] > 0


def test_exp019_source_has_no_behavioral_or_appworld_runner_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_interaction_representation_6c.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert "forward_train" not in called
    assert "generate" not in called
    assert "run_appworld" not in called
    assert "build_include_mask" not in called
