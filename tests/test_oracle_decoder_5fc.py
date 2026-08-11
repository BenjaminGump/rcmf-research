from __future__ import annotations

from pathlib import Path

import pytest
import torch

from scripts.run_stage_c_oracle_decoder_5fc import _train_tensor_decoder

from rcmf.training.oracle_convergence_5fa import (
    IndependentPairTensorTable,
    assess_plateau,
    assess_plateau_legacy_5fb,
)
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    MLPDeltaDecoder,
    apply_latent_inversion_step,
    assess_u64_inversion_continuation,
    assert_pair_only_input_contract,
    decoder_decision,
    flatten_delta,
    minimally_project_delta_to_ratio,
    module_state_sha256,
    project_independent_latents_to_ratio_,
    state_grouped_three_fold_manifest,
    tensor_reconstruction_plateau,
    uncentered_svd_reconstruction,
    validate_decoder_split_manifest,
    validate_direct_checkpoint,
)


def _point(updates: int, huber: float, spearman: float) -> dict:
    return {
        "updates_per_pair": updates,
        "pair_ids": ["a", "b"],
        "evaluation_summary": {
            "sequence_utility_huber": {"mean": huber},
            "u_text_vs_u_student_spearman": spearman,
        },
    }


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([_point(16, 1.0, 0.50), _point(32, 0.995, 0.505)], True),
        ([_point(16, 1.0, 0.50), _point(32, 1.005, 0.495)], True),
        ([_point(16, 1.0, 0.50), _point(32, 1.20, 0.49)], False),
        (
            [
                _point(16, 1.0, 0.50),
                _point(32, 0.80, 0.70),
                _point(48, 1.0, 0.50),
            ],
            False,
        ),
        ([_point(16, 1.0, 0.50), _point(32, 1.0, 0.50)], True),
    ],
)
def test_corrected_plateau_handles_improvement_deterioration_oscillation_and_flat(
    history: list[dict], expected: bool
) -> None:
    current = int(history[-1]["updates_per_pair"])
    lag = current - int(history[-2]["updates_per_pair"])
    report = assess_plateau(history, current_updates=current, lag=lag)

    assert report["plateau"] is expected


def test_legacy_stage5fb_rule_is_preserved_but_large_deterioration_is_not_future_plateau() -> None:
    history = [_point(112, 0.029525, 0.984810), _point(128, 0.034512, 0.979465)]

    assert assess_plateau_legacy_5fb(history, current_updates=128)["plateau"] is True
    corrected = assess_plateau(history, current_updates=128)
    assert corrected["plateau"] is False
    assert corrected["checks"]["absolute_relative_loss_change_lt_0_01"] is False
    assert corrected["checks"]["current_loss_lte_1_02_best_so_far"] is False


@pytest.mark.parametrize(
    ("u32_huber", "u32_spearman", "u64_huber", "u64_spearman", "expected", "reason"),
    [
        (0.10, 0.80, 0.08, 0.805, True, "material_improvement_at_u64"),
        (0.10, 0.80, 0.0995, 0.82, True, "material_improvement_at_u64"),
        (0.10, 0.80, 0.1005, 0.805, False, "no_material_improvement_at_u64"),
        (
            0.057549,
            0.980861,
            0.082850,
            0.904121,
            False,
            "u64_huber_deteriorated_beyond_best_guard",
        ),
    ],
)
def test_u64_continuation_requires_material_improvement_without_deterioration(
    u32_huber: float,
    u32_spearman: float,
    u64_huber: float,
    u64_spearman: float,
    expected: bool,
    reason: str,
) -> None:
    history = [
        _point(32, u32_huber, u32_spearman),
        _point(64, u64_huber, u64_spearman),
    ]

    report = assess_u64_inversion_continuation(history)

    assert report["assessable"] is True
    assert report["continue_to_128"] is expected
    assert report["reason"] == reason


def _checkpoint_payload(pair_ids: list[str], updates: int, model_dim: int = 8) -> dict:
    state = {
        f"rows.{index}": torch.full((4, model_dim), float(index + 1))
        for index in range(len(pair_ids))
    }
    return {
        "pair_ids": pair_ids,
        "completed_rounds": updates,
        "update_counts": [updates] * len(pair_ids),
        "table_state_dict": state,
        "metadata": {
            "component": "direct_delta",
            "objective": "sequence_utility_plus_sparse_kl",
            "ratio_budget": 1.0,
            "k": 4,
            "position": "last_user_k",
            "injection_site": "input_embedding",
        },
    }


def test_direct_checkpoint_identity_enforces_pair_order_shape_and_updates() -> None:
    payload = _checkpoint_payload(["a", "b"], 112)

    passed = validate_direct_checkpoint(
        payload, expected_pair_ids=["a", "b"], expected_updates=112, model_dim=8
    )
    failed = validate_direct_checkpoint(
        payload, expected_pair_ids=["b", "a"], expected_updates=128, model_dim=8
    )

    assert passed["passed"] is True
    assert passed["shape"] == [2, 4, 8]
    assert failed["passed"] is False


def test_uncentered_rank_192_reconstructs_exactly_and_lower_rank_has_expected_rank() -> None:
    generator = torch.Generator().manual_seed(13)
    delta = torch.randn(192, 4, 48, generator=generator)

    rank128 = uncentered_svd_reconstruction(delta, 128)["flat"]
    rank192 = uncentered_svd_reconstruction(delta, 192)["flat"]

    assert torch.linalg.matrix_rank(rank128, tol=1.0e-4) <= 128
    assert torch.allclose(rank192, delta.flatten(start_dim=1), atol=2.0e-5, rtol=2.0e-5)


def test_float64_rank_192_factorization_removes_float32_reconstruction_drift() -> None:
    generator = torch.Generator().manual_seed(29)
    delta = torch.randn(192, 4, 48, generator=generator)
    factorization = torch.linalg.svd(
        flatten_delta(delta).to(torch.float64), full_matrices=False
    )

    rank192 = uncentered_svd_reconstruction(
        delta, 192, factorization=factorization
    )["flat"]

    assert torch.equal(rank192, flatten_delta(delta))


def test_minimal_ratio_projection_preserves_in_tolerance_rows_exactly() -> None:
    delta = torch.tensor([[[1.0000001, 0.0]], [[1.01, 0.0]]], dtype=torch.float32)

    projected, report = minimally_project_delta_to_ratio(
        delta, base_norms=torch.ones(2), max_ratio=1.0, tolerance=1.0e-6
    )

    assert torch.equal(projected[0], delta[0])
    assert float(projected[1].norm()) == pytest.approx(1.0, abs=1.0e-6)
    assert report["projected_row_count"] == 1


def test_tensor_plateau_uses_absolute_floor_near_machine_precision() -> None:
    history = [
        {
            "epoch": 16,
            "metrics": {
                "loss": 1.0e-9,
                "normalized_mse": 1.0e-10,
                "relative_frobenius_error": 1.0e-5,
                "mean_cosine": 1.0,
            },
        },
        {
            "epoch": 32,
            "metrics": {
                "loss": 1.6e-9,
                "normalized_mse": 1.1e-10,
                "relative_frobenius_error": 1.1e-5,
                "mean_cosine": 1.0,
            },
        },
    ]

    report = tensor_reconstruction_plateau(
        history, current_epoch=32, previous_epoch=16
    )

    assert report["plateau"] is True
    assert report["plateau_mode"] == "absolute_numerical_floor"


def test_tensor_plateau_accepts_sub_tenth_percent_reconstruction() -> None:
    history = [
        {
            "epoch": 576,
            "metrics": {
                "loss": 8.08e-6,
                "normalized_mse": 7.70e-6,
                "relative_frobenius_error": 0.00230,
                "mean_cosine": 0.9999962,
            },
        },
        {
            "epoch": 592,
            "metrics": {
                "loss": 3.66e-7,
                "normalized_mse": 3.48e-7,
                "relative_frobenius_error": 0.000590,
                "mean_cosine": 0.99999988,
            },
        },
    ]

    report = tensor_reconstruction_plateau(
        history, current_epoch=592, previous_epoch=576
    )

    assert report["plateau"] is True
    assert report["plateau_mode"] == "absolute_numerical_floor"


def _split_rows() -> list[dict]:
    rows = []
    categories = ("positive", "neutral", "negative", "random")
    for state in range(18):
        for offset in range(2):
            rows.append(
                {
                    "pair_id": f"state-{state}-pair-{offset}",
                    "state_example_id": f"state-{state}",
                    "selection_category": categories[(state + offset) % 4],
                    "memory_stage_index": (state * 2 + offset) % 12,
                }
            )
    return rows


def test_three_fold_manifest_is_deterministic_state_grouped_and_leak_free() -> None:
    rows = _split_rows()

    first = state_grouped_three_fold_manifest(rows, seed=17)
    second = state_grouped_three_fold_manifest(rows, seed=17)

    assert first == second
    assert validate_decoder_split_manifest(first, rows=rows)["passed"] is True
    assert sum(len(fold["heldout_pair_ids"]) for fold in first["folds"]) == len(rows)
    assert [len(fold["heldout_pair_ids"]) for fold in first["folds"]] == [12, 12, 12]
    assert all(fold["train_covers_all_memories"] for fold in first["folds"])
    assert first["assignment_search"]["missing_train_memory_count"] == 0


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires CUDA device split")
def test_linear_tensor_decoder_accepts_cpu_target_and_cuda_basis(tmp_path) -> None:
    generator = torch.Generator().manual_seed(41)
    target = torch.randn(4, 64, generator=generator)
    basis = torch.randn(128, 64, generator=generator).cuda()
    settings = {
        "decoder_hidden_dim": 512,
        "tensor_training": {
            "version": "cuda_test",
            "batch_size": 4,
            "checkpoint_interval_epochs": 1,
            "minimum_epochs": 1,
            "maximum_epochs": 1,
            "linear_learning_rate": 0.001,
            "mlp_learning_rate": 0.0003,
            "latent_initial_std": 0.02,
            "require_documented_plateau": False,
            "numerical_floor": {
                "normalized_mse": 5.0e-6,
                "relative_frobenius_error": 2.0e-3,
                "one_minus_mean_cosine": 2.0e-6,
            },
        },
    }

    _, summary = _train_tensor_decoder(
        architecture="linear",
        train_pair_ids=["a", "b", "c", "d"],
        train_target=target,
        basis=basis,
        settings=settings,
        device=torch.device("cuda"),
        seed=13,
        output_dir=tmp_path,
    )

    assert summary["epochs"] == 1
    assert Path(summary["best_checkpoint"]).exists()


@pytest.mark.parametrize("decoder_type", ["linear", "mlp"])
def test_zero_latent_maps_exactly_to_zero_delta(decoder_type: str) -> None:
    decoder = (
        LinearDeltaDecoder(128, 64)
        if decoder_type == "linear"
        else MLPDeltaDecoder(128, 64)
    )

    output = decoder(torch.zeros(3, 128))

    assert torch.equal(output, torch.zeros_like(output))


def test_frozen_decoder_inversion_updates_only_selected_latent_and_preserves_decoder() -> None:
    pair_ids = ["a", "b"]
    table = IndependentPairTensorTable(pair_ids, (4,), init_std=0.1)
    decoder = LinearDeltaDecoder(4, 6)
    for parameter in decoder.parameters():
        parameter.requires_grad_(False)
    before_decoder = module_state_sha256(decoder)
    before_unselected = table.rows[1].detach().clone()
    counts = [0, 0]
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.01, weight_decay=0.0)
    prediction = decoder(table.forward_indices([0]))
    loss = prediction.square().mean()

    apply_latent_inversion_step(
        optimizer=optimizer,
        loss=loss,
        table=table,
        decoder=decoder,
        selected_indices=[0],
        update_counts=counts,
        base_norms=torch.ones(2),
    )

    assert counts == [1, 0]
    assert torch.equal(table.rows[1], before_unselected)
    assert module_state_sha256(decoder) == before_decoder


def test_ratio_projection_enforces_decoder_output_budget() -> None:
    table = IndependentPairTensorTable(["a", "b"], (2,))
    decoder = LinearDeltaDecoder(2, 2)
    with torch.no_grad():
        decoder.linear.weight.copy_(torch.eye(2))
        table.rows[0].copy_(torch.tensor([3.0, 4.0]))
        table.rows[1].copy_(torch.tensor([0.1, 0.2]))

    report = project_independent_latents_to_ratio_(
        table, decoder, torch.ones(2), max_ratio=1.0
    )

    assert report["max_ratio"] <= 1.00001
    assert float(table.rows[0].detach().norm()) == pytest.approx(1.0, abs=1.0e-5)


def test_pair_only_contract_rejects_selector_raw_memory_or_full_bank_payloads() -> None:
    assert assert_pair_only_input_contract([{"pair_id": "a"}])["passed"] is True
    report = assert_pair_only_input_contract(
        [{"pair_id": "a", "raw_memory_text": "secret", "selector_score": 1.0}]
    )
    assert report["passed"] is False


def test_decision_tree_prefers_dimension_failure_then_decoder_specific_branches() -> None:
    assert decoder_decision(
        rank128_passed=False,
        rank192_reproduced=True,
        linear_passed=False,
        mlp_passed=False,
        joint_mlp_passed=False,
    )["branch"] == "latent_dimension_128_insufficient"
    assert decoder_decision(
        rank128_passed=True,
        rank192_reproduced=True,
        linear_passed=True,
        mlp_passed=False,
        joint_mlp_passed=False,
    )["branch"] == "current_injector_mlp_decoder_is_bottleneck"
    assert decoder_decision(
        rank128_passed=True,
        rank192_reproduced=True,
        linear_passed=True,
        mlp_passed=True,
        joint_mlp_passed=True,
    )["branch"] == "shared_128d_decoder_capacity_passed"
