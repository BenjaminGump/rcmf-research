from __future__ import annotations

import pytest
import torch

from rcmf.config import load_config
from rcmf.training.state_conditioned_program_direct_7dg import (
    GLOBAL_SEED,
    continuation_decision,
    differentiable_ratio_projection,
    factorized_behavior_gate,
    pairmlp_behavior_gate,
    require_global_seed,
    runtime_projection,
    task_grouped_split,
)


def _summary(*, rho: float, huber: float, ratio: float = 0.5) -> dict:
    return {
        "u_text_vs_u_student_spearman": rho,
        "sequence_utility_huber": {"mean": huber},
        "delta_ratio": {"max": ratio},
    }


def test_direct_config_and_seed_are_locked_to_25101() -> None:
    cfg = load_config(
        "configs/benchmark/stage_c_state_conditioned_program_direct_7dg.yaml"
    )
    settings = cfg.raw["stage_c_7dg"]
    assert GLOBAL_SEED == 25101
    assert settings["global_seed"] == GLOBAL_SEED
    assert cfg.raw["experiment"]["seed"] == GLOBAL_SEED
    assert settings["expected_selector_ensemble_sha256"] == (
        "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    )
    assert len(settings["expected_selector_ensemble_sha256"]) == 64
    assert require_global_seed(GLOBAL_SEED) == GLOBAL_SEED
    with pytest.raises(ValueError, match="25101"):
        require_global_seed(GLOBAL_SEED + 1)


def test_task_grouped_split_is_deterministic_and_has_no_task_leakage() -> None:
    rows = [
        {
            "pair_id": f"task-{task}-pair-{pair}",
            "state_task_id": f"task-{task}",
            "state_example_id": f"task-{task}-state-{pair % 2}",
        }
        for task in range(11)
        for pair in range(3 + task % 4)
    ]
    first = task_grouped_split(rows)
    second = task_grouped_split(rows)

    assert first == second
    assert first["task_overlap"] == []
    assert first["state_overlap"] == []
    assert first["train_pair_count"] + first["validation_pair_count"] == len(rows)
    assert first["train_indices"]
    assert first["validation_indices"]


def test_ratio_projection_is_differentiable_and_never_exceeds_one() -> None:
    delta = torch.tensor([[3.0, 4.0], [0.3, 0.4]], requires_grad=True)
    projected, report = differentiable_ratio_projection(
        delta, torch.tensor([2.5, 1.0])
    )
    projected.sum().backward()

    assert float(report["maximum_ratio"].detach()) <= 1.0
    assert torch.allclose(projected[0].norm(), torch.tensor(2.5))
    assert torch.allclose(projected[1], delta.detach()[1])
    assert delta.grad is not None


def test_continuation_requires_five_percent_huber_gain_and_stable_spearman() -> None:
    accepted = continuation_decision(
        _summary(rho=0.40, huber=1.0),
        _summary(rho=0.39, huber=0.94),
    )
    rejected = continuation_decision(
        _summary(rho=0.40, huber=1.0),
        _summary(rho=0.30, huber=0.94),
    )

    assert accepted["select_u16"]
    assert accepted["selected_updates_per_pair"] == 16
    assert not rejected["select_u16"]
    assert rejected["selected_updates_per_pair"] == 8


def test_pairmlp_gate_requires_both_shuffle_gaps() -> None:
    cells = {
        "A_validation": {
            "correct": _summary(rho=0.40, huber=0.70),
            "zero": _summary(rho=0.0, huber=1.00),
            "state_shuffle": _summary(rho=0.0, huber=0.90),
            "transition_shuffle": _summary(rho=0.0, huber=0.90),
        },
        "B": {
            "correct": _summary(rho=0.10, huber=0.90),
            "zero": _summary(rho=0.0, huber=1.00),
            "state_shuffle": _summary(rho=0.0, huber=1.00),
            "transition_shuffle": _summary(rho=0.0, huber=1.00),
        },
        "C": {
            "correct": _summary(rho=0.0, huber=1.0),
            "zero": _summary(rho=0.0, huber=1.0),
            "state_shuffle": _summary(rho=0.0, huber=1.0),
            "transition_shuffle": _summary(rho=0.0, huber=1.0),
        },
        "D": {
            "correct": _summary(rho=0.0, huber=1.0),
            "zero": _summary(rho=0.0, huber=1.0),
            "state_shuffle": _summary(rho=0.0, huber=1.0),
            "transition_shuffle": _summary(rho=0.0, huber=1.0),
        },
        "E": {
            "correct": _summary(rho=0.25, huber=0.90),
            "zero": _summary(rho=0.0, huber=1.00),
            "state_shuffle": _summary(rho=0.0, huber=1.00),
            "transition_shuffle": _summary(rho=0.0, huber=1.00),
        },
    }
    assert pairmlp_behavior_gate(cells)["passed"]
    cells["E"]["transition_shuffle"] = _summary(rho=0.0, huber=0.80)
    assert not pairmlp_behavior_gate(cells)["passed"]


def test_factorized_gate_keeps_c_and_d_diagnostic() -> None:
    def controls(correct: dict) -> dict:
        return {
            "correct": correct,
            "zero": _summary(rho=0.0, huber=1.0),
            "static_only": _summary(rho=0.0, huber=0.95),
            "conditional_only": _summary(rho=0.0, huber=0.95),
            "state_shuffle": _summary(rho=0.0, huber=0.95),
            "transition_shuffle": _summary(rho=0.0, huber=0.95),
            "memory_swap": _summary(rho=0.0, huber=0.95),
            "matched_random": _summary(rho=0.0, huber=0.95),
        }

    cells = {
        "A_validation": controls(_summary(rho=0.35, huber=0.80)),
        "B": controls(_summary(rho=0.20, huber=0.85)),
        "C": controls(_summary(rho=-0.20, huber=1.20)),
        "D": controls(_summary(rho=-0.20, huber=1.20)),
        "E": controls(_summary(rho=0.20, huber=0.85)),
    }
    assert factorized_behavior_gate(cells)["passed"]


def test_runtime_projection_accounts_for_two_models_and_one_step() -> None:
    rates = {
        name: {"forward": 2.0, "backward": 3.0, "generation": 5.0}
        for name in ("best", "expected", "conservative")
    }
    report = runtime_projection(
        train_pairs=10,
        validation_pairs=2,
        evaluation_pairs=4,
        new_teacher_rows=3,
        one_step_conditions=180,
        rates=rates,
    )
    expected = report["scenarios"]["expected"]
    assert expected["backward_count_maximum"] == 320
    assert expected["teacher_forward_count"] == 3
    assert expected["one_step_generation_count"] == 180
