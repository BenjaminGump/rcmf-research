from __future__ import annotations

import torch

from rcmf.training.addressing_only import rows_to_tensors
from rcmf.training.selector_repair_5c import (
    SelectorRepairLossConfig,
    default_repair_configs,
    selector_repair_loss,
    top_utility_metrics,
)


def _row(
    *,
    state_index: int = 0,
    utilities: list[float | None],
    valid: list[bool] | None = None,
) -> dict:
    valid = valid or [value is not None for value in utilities]
    gain = [max((value or 0.0) - 0.01, 0.0) if ok else 0.0 for value, ok in zip(utilities, valid)]
    return {
        "state_index": state_index,
        "state_example_id": f"s{state_index}",
        "task_id": f"task_{state_index}",
        "episode_id": f"episode_{state_index}",
        "step_index": state_index,
        "split": "train",
        "ordered_effective_memory_ids": [f"m{i}" for i in range(len(utilities))],
        "ordered_effective_memory_indices": list(range(len(utilities))),
        "valid_mask": valid,
        "legal_effective_mask": valid,
        "raw_utility": utilities,
        "positive_mask": [bool(ok and (value or 0.0) > 0.01) for value, ok in zip(utilities, valid)],
        "neutral_mask": [bool(ok and abs(value or 0.0) <= 0.01) for value, ok in zip(utilities, valid)],
        "negative_mask": [bool(ok and (value or 0.0) < -0.01) for value, ok in zip(utilities, valid)],
        "strong_positive_mask": [bool(ok and (value or 0.0) >= 0.05) for value, ok in zip(utilities, valid)],
        "strong_negative_mask": [bool(ok and (value or 0.0) <= -0.05) for value, ok in zip(utilities, valid)],
        "positive_gain": gain,
        "score_statuses": ["scoreable" if ok else "masked" for ok in valid],
        "source_pair_keys": [f"s{state_index}:m{i}" for i in range(len(utilities))],
        "target_sha256_by_memory": [f"target-{i}" for i in range(len(utilities))],
        "memory_text_sha256_by_memory": [f"memory-{i}" for i in range(len(utilities))],
        "no_positive_state": any(valid) and not any(gain_i > 0 for gain_i in gain),
        "all_missing_state": not any(valid),
    }


def test_gap_weighted_pairwise_includes_positive_positive_pairs() -> None:
    labels = rows_to_tensors([_row(utilities=[0.20, 0.12, 0.119])])
    residual = torch.tensor([[0.0, 0.4, -0.3]], requires_grad=True)
    gate = torch.tensor([0.5], requires_grad=True)
    config = SelectorRepairLossConfig(
        name="gap_only",
        huber=0.0,
        gap_pairwise=1.0,
        top_listwise=0.0,
        sign_calibration=0.0,
        near_best=0.0,
        gate=0.0,
        pair_gap_threshold=0.05,
    )
    loss, metrics = selector_repair_loss(residual, gate, torch.zeros(3), labels, config)
    loss.backward()
    assert metrics["loss_gap_pairwise"] > 0.0
    assert residual.grad is not None
    assert residual.grad[0, 0].item() < 0.0
    assert residual.grad[0, 1].item() > 0.0


def test_top_utility_metrics_use_deterministic_raw_teacher_best_rank() -> None:
    labels = rows_to_tensors([_row(utilities=[0.40, 0.20, 0.10])])
    scores = torch.tensor([[0.1, 0.5, -0.2]])
    metrics = top_utility_metrics(scores, labels)
    assert metrics["positive_state_count"] == 1
    assert metrics["raw_teacher_best_recall@1"]["mean"] == 0.0
    assert metrics["raw_teacher_best_recall@2"]["mean"] == 1.0
    assert metrics["raw_teacher_best_rank"]["p50"] == 2.0
    assert metrics["raw_teacher_best_negative_score_fraction"] == 0.0
    assert metrics["strong_positive_negative_score_fraction"] == 1.0 / 3.0


def test_sign_calibration_keeps_signed_scores_unclamped_and_gradients_flow() -> None:
    labels = rows_to_tensors([_row(utilities=[0.08, -0.08])])
    residual = torch.tensor([[-0.2, 0.2]], requires_grad=True)
    gate = torch.tensor([0.5], requires_grad=True)
    config = SelectorRepairLossConfig(
        name="sign_only",
        huber=0.0,
        gap_pairwise=0.0,
        top_listwise=0.0,
        sign_calibration=1.0,
        near_best=0.0,
        gate=0.0,
        margin_positive=0.03,
        margin_negative=0.03,
    )
    loss, metrics = selector_repair_loss(residual, gate, torch.zeros(2), labels, config)
    loss.backward()
    assert metrics["loss_sign_calibration"] > 0.0
    assert residual.detach()[0, 0].item() < 0.0
    assert residual.detach()[0, 1].item() > 0.0
    assert residual.grad is not None
    assert residual.grad[0, 0].item() < 0.0
    assert residual.grad[0, 1].item() > 0.0


def test_default_repair_configs_are_predetermined_and_include_required_ablations() -> None:
    names = [config.name for config in default_repair_configs()]
    assert names[0] == "A_stage4c_original"
    assert any(name.startswith("B_gap_all_pairs") for name in names)
    assert any(name.startswith("C_top_listwise") for name in names)
    assert "D_gap_top_sign" in names
    assert "E_gap_top_sign_nearbest" in names
    assert len(names) == len(set(names))
