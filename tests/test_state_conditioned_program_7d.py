from __future__ import annotations

import copy

import pytest
import torch

from rcmf.training.oracle_decoder_5fc import (
    apply_latent_inversion_step,
    module_state_sha256,
)
from rcmf.training.oracle_convergence_5fa import IndependentPairTensorTable
from rcmf.training.state_conditioned_program_7d import (
    WeightedFactorizedTransitionField,
    assert_program_student_contract,
    build_frozen_cell_pairs,
    build_program_training_pairs,
    deterministic_random_orthonormal_decoder,
    frozen_pair_context_status,
    grouped_decoder_pair_split,
    orthonormalize_decoder_preserving_outputs_,
    projected_program_parameter_counts,
    selector_candidate_projection,
    update_count_summary,
    weighted_field_algebra_validation,
)


def _synthetic_labels() -> tuple[list[dict], list[dict], list[str], list[str], dict]:
    states = [f"s{index}" for index in range(12)]
    transitions = [f"t{index}" for index in range(10)]
    classes = {
        f"c{index}": {
            "signature_class_id": f"c{index}",
            "canonical_transition_id": f"t{index}",
            "canonical_parent_id": f"p{index % 4}",
            "member_transition_ids": [f"t{index}"],
            "serialized_token_counts": [100 + index],
        }
        for index in range(10)
    }
    rows_a = []
    rows_c = []
    for state_index, state in enumerate(states):
        for transition_index, transition in enumerate(transitions):
            row = {
                "state_example_id": state,
                "state_task_id": f"task{state_index % 6}",
                "transition_id": transition,
                "transition_parent_id": f"p{transition_index % 4}",
                "transition_parent_task_id": f"memory-task{transition_index % 4}",
                "signature_class_id": f"c{transition_index}",
                "procedural_tier": (state_index + transition_index) % 5,
                "exact_api_sequence": state_index % 10 == transition_index,
                "state_stage_compatible": transition_index % 2 == 0,
                "state_stage_conflict_count": transition_index % 3,
                "same_coarse_action_type": transition_index % 2 == state_index % 2,
                "canonical_action_schema_match": transition_index == state_index % 10,
                "same_primary_app": transition_index % 3 == state_index % 3,
            }
            (rows_a if transition_index < 8 else rows_c).append(row)
    return rows_a, rows_c, states, transitions, classes


def test_pair_manifest_is_deterministic_a_only_and_no_heldout_label_selected() -> None:
    rows_a, rows_c, states, transitions, classes = _synthetic_labels()
    scores = torch.arange(len(states) * len(transitions), dtype=torch.float32).view(
        len(states), len(transitions)
    )
    kwargs = {
        "labels_a": rows_a,
        "deployment_candidate_rows": selector_candidate_projection(rows_c),
        "scores": scores,
        "ordered_state_ids": states,
        "ordered_transition_ids": transitions,
        "transition_token_counts": {
            transition: 100 + index
            for index, transition in enumerate(transitions)
        },
        "classes": classes,
        "target_size": 20,
        "maximum_size": 30,
        "seed": 17,
    }

    first = build_program_training_pairs(**kwargs)
    second = build_program_training_pairs(**kwargs)

    assert first == second
    assert first["pair_count"] == 20
    assert {row["cell"] for row in first["pairs"]} == {"A"}
    assert all(not row["selection_uses_heldout_labels"] for row in first["pairs"])
    assert first["task_count"] == 6


def test_frozen_cell_selection_uses_scores_and_structural_classes_only() -> None:
    rows_a, _, states, transitions, classes = _synthetic_labels()
    scores = torch.zeros(len(states), len(transitions))
    scores[:, 7] = 9.0

    manifest = build_frozen_cell_pairs(
        candidate_rows=selector_candidate_projection(rows_a),
        scores=scores,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        transition_token_counts={
            transition: 100 + index
            for index, transition in enumerate(transitions)
        },
        classes=classes,
        state_count=6,
        cell="B",
        seed=19,
    )

    assert manifest["pair_count"] == 6
    assert {row["transition_id"] for row in manifest["pairs"]} == {"t7"}
    assert all(row["selection_uses_heldout_labels"] is False for row in manifest["pairs"])


def test_heldout_supervision_fields_are_absent_and_cannot_change_selection() -> None:
    rows_a, _, states, transitions, classes = _synthetic_labels()
    poisoned = copy.deepcopy(rows_a)
    for index, row in enumerate(poisoned):
        row["procedural_tier"] = 10_000 - index
        row["heldout_behavior"] = float("nan")
    projected = selector_candidate_projection(poisoned)
    assert all("procedural_tier" not in row for row in projected)
    assert all("heldout_behavior" not in row for row in projected)

    scores = torch.zeros(len(states), len(transitions))
    scores[:, 6] = 7.0
    clean = build_frozen_cell_pairs(
        candidate_rows=selector_candidate_projection(rows_a),
        scores=scores,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        transition_token_counts={
            transition: 100 + index
            for index, transition in enumerate(transitions)
        },
        classes=classes,
        state_count=6,
        cell="D",
        seed=21,
    )
    contaminated = build_frozen_cell_pairs(
        candidate_rows=projected,
        scores=scores,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        transition_token_counts={
            transition: 100 + index
            for index, transition in enumerate(transitions)
        },
        classes=classes,
        state_count=6,
        cell="D",
        seed=21,
    )
    assert clean == contaminated


def test_legal_exemplar_uses_transition_keyed_tokens_not_sorted_class_counts() -> None:
    classes = {
        "c": {
            "signature_class_id": "c",
            "canonical_transition_id": "t0",
            "member_transition_ids": ["t0", "t1", "t2"],
            "serialized_token_counts": [5, 10, 100],
        }
    }
    rows = [
        {
            "state_example_id": "s",
            "state_task_id": "task",
            "transition_id": transition_id,
            "transition_parent_id": f"p{index}",
            "transition_parent_task_id": f"memory{index}",
            "signature_class_id": "c",
        }
        for index, transition_id in enumerate(("t1", "t2"), start=1)
    ]
    manifest = build_frozen_cell_pairs(
        candidate_rows=rows,
        scores=torch.ones(1, 3),
        ordered_state_ids=["s"],
        ordered_transition_ids=["t0", "t1", "t2"],
        transition_token_counts={"t0": 5, "t1": 100, "t2": 10},
        classes=classes,
        state_count=None,
        cell="B",
        seed=22,
    )
    assert manifest["pairs"][0]["transition_id"] == "t2"


def test_over_context_pair_is_missing_without_selection_change() -> None:
    source = {
        "pair_id": "s::transition::t",
        "state_example_id": "s",
        "transition_id": "t",
        "signature_class_id": "c",
        "over_context": True,
    }
    output = frozen_pair_context_status(source)
    assert output["pair_id"] == source["pair_id"]
    assert output["transition_id"] == source["transition_id"]
    assert output["signature_class_id"] == source["signature_class_id"]
    assert output["score_status"] == "over_context_missing"
    assert output["valid_for_teacher_cache"] is False
    assert output["context_substitution"] is False
    assert output["cross_class_substitution"] is False
    assert output["truncated"] is False


def test_decoder_group_split_has_exact_counts_and_no_state_overlap() -> None:
    rows = [
        {
            "pair_id": f"s{index}::t",
            "state_example_id": f"s{index}",
            "state_task_id": f"task{index % 7}",
        }
        for index in range(80)
    ]

    split = grouped_decoder_pair_split(
        rows, calibration_count=48, heldout_count=16, seed=23
    )

    assert split["calibration_pair_count"] == 48
    assert split["heldout_pair_count"] == 16
    assert split["state_overlap_count"] == 0
    assert {
        row["state_example_id"] for row in split["calibration_pairs"]
    }.isdisjoint({row["state_example_id"] for row in split["heldout_pairs"]})


def test_random_decoder_is_deterministic_orthonormal_no_bias_and_zero_preserving() -> None:
    first = deterministic_random_orthonormal_decoder(
        latent_dim=8, output_dim=32, seed=29
    )
    second = deterministic_random_orthonormal_decoder(
        latent_dim=8, output_dim=32, seed=29
    )
    weight = first.linear.weight.detach()

    assert first.linear.bias is None
    assert torch.equal(weight, second.linear.weight)
    assert torch.allclose(weight.T @ weight, torch.eye(8), atol=1e-6)
    assert torch.equal(first(torch.zeros(3, 8)), torch.zeros(3, 32))


def test_qr_orthonormalization_preserves_decoded_delta() -> None:
    torch.manual_seed(31)
    decoder = deterministic_random_orthonormal_decoder(
        latent_dim=8, output_dim=32, seed=31
    )
    with torch.no_grad():
        decoder.linear.weight.mul_(torch.linspace(0.5, 2.0, 8))
    latents = torch.randn(12, 8)
    before = decoder(latents).detach().clone()

    report = orthonormalize_decoder_preserving_outputs_(decoder, latents)

    assert torch.allclose(decoder(latents), before, atol=2e-6, rtol=2e-6)
    assert report["maximum_orthonormality_error"] < 1e-5


def test_frozen_decoder_update_counts_optimizer_resume_and_ratio_projection() -> None:
    pair_ids = ["a", "b"]
    decoder = deterministic_random_orthonormal_decoder(
        latent_dim=2, output_dim=4, seed=37
    )
    for parameter in decoder.parameters():
        parameter.requires_grad_(False)
    decoder_hash = module_state_sha256(decoder)
    table = IndependentPairTensorTable(pair_ids, (2,), init_std=0.1)
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.05, weight_decay=0.0)
    counts = [0, 0]

    for index in range(2):
        loss = decoder(table.forward_indices([index])).square().mean()
        apply_latent_inversion_step(
            optimizer=optimizer,
            loss=loss,
            table=table,
            decoder=decoder,
            selected_indices=[index],
            update_counts=counts,
            base_norms=torch.full((2,), 0.05),
            ratio_budget=1.0,
        )
    checkpoint = {
        "table": copy.deepcopy(table.state_dict()),
        "optimizer": copy.deepcopy(optimizer.state_dict()),
        "counts": list(counts),
    }
    uninterrupted_loss = decoder(table.forward_indices([0])).square().mean()
    apply_latent_inversion_step(
        optimizer=optimizer,
        loss=uninterrupted_loss,
        table=table,
        decoder=decoder,
        selected_indices=[0],
        update_counts=counts,
        base_norms=torch.full((2,), 0.05),
    )
    expected = table.rows[0].detach().clone()

    resumed_table = IndependentPairTensorTable(pair_ids, (2,), init_std=0.1)
    resumed_table.load_state_dict(checkpoint["table"])
    resumed_optimizer = torch.optim.AdamW(
        resumed_table.parameters(), lr=0.05, weight_decay=0.0
    )
    resumed_optimizer.load_state_dict(checkpoint["optimizer"])
    resumed_counts = list(checkpoint["counts"])
    resumed_loss = decoder(resumed_table.forward_indices([0])).square().mean()
    apply_latent_inversion_step(
        optimizer=resumed_optimizer,
        loss=resumed_loss,
        table=resumed_table,
        decoder=decoder,
        selected_indices=[0],
        update_counts=resumed_counts,
        base_norms=torch.full((2,), 0.05),
    )

    assert torch.equal(resumed_table.rows[0], expected)
    assert resumed_counts == counts == [2, 1]
    assert update_count_summary(pair_ids, counts)["per_pair_update_counts"] == {
        "a": 2,
        "b": 1,
    }
    assert module_state_sha256(decoder) == decoder_hash
    assert float(decoder(resumed_table.stacked()).norm(dim=1).max().detach()) <= 0.05001


def test_program_contract_rejects_selector_raw_transition_signature_or_full_bank() -> None:
    assert assert_program_student_contract([{"pair_id": "p"}])["passed"]
    for key in ("raw_transition_text", "signature_card", "selector_score", "full_bank"):
        report = assert_program_student_contract([{"pair_id": "p", key: "bad"}])
        assert not report["passed"]


def test_weighted_field_explicit_reversible_fixed_shape_and_zero_program() -> None:
    report = weighted_field_algebra_validation(seed=41)
    assert report["passed"]
    assert all(report["checks"].values())

    field = WeightedFactorizedTransitionField(3, 2, 4)
    field.add(
        "t",
        "p",
        0.25,
        torch.ones(3),
        torch.ones(4),
        torch.ones(2, 4),
    )
    actual = field.read(torch.ones(3), torch.ones(2))
    expected = 0.25 * 3.0 * (
        torch.ones(4, dtype=actual.dtype) + 2.0 * torch.ones(4, dtype=actual.dtype)
    )
    assert torch.allclose(actual, expected)
    assert torch.allclose(
        actual, field.explicit_read(torch.ones(3), torch.ones(2))
    )


def test_parameter_projection_counts_no_bias_decoder_and_fixed_architectures() -> None:
    report = projected_program_parameter_counts(
        representation_dim=8,
        hidden_dim=4,
        program_dim=2,
        model_dim=16,
        controller_ranks=[3, 1],
        train_parent_transition_count=7,
    )
    tower_8_to_2 = 8 * 4 + 3 * 4 + 4 * 2 + 2
    assert report["architectures"]["state_only"] == tower_8_to_2
    assert report["architectures"]["free_id"] == 14
    assert report["decoder"] == 2 * 4 * 16


def test_decoder_output_ratio_not_latent_norm_controls_budget() -> None:
    decoder = deterministic_random_orthonormal_decoder(
        latent_dim=2, output_dim=4, seed=43
    )
    with torch.no_grad():
        decoder.linear.weight.mul_(10.0)
    table = IndependentPairTensorTable(["p"], (2,), init_std=1.0)
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.01, weight_decay=0.0)
    counts = [0]
    loss = -decoder(table.forward_indices([0])).square().mean()
    apply_latent_inversion_step(
        optimizer=optimizer,
        loss=loss,
        table=table,
        decoder=decoder,
        selected_indices=[0],
        update_counts=counts,
        base_norms=torch.ones(1),
        ratio_budget=1.0,
    )
    assert float(decoder(table.stacked()).norm().detach()) <= 1.00001
