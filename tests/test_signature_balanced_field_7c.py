from __future__ import annotations

import pytest
import torch

from rcmf.config import load_config
from rcmf.training.signature_balanced_field_7c import (
    ClassTarget,
    SignatureBalancedFieldSelector,
    aggregate_class_scores,
    calibrated_ensemble,
    condition_semantic_key,
    grouped_task_parent_folds,
    select_scoreable_class_exemplar,
    state_class_balanced_weights,
    train_field_selector,
    validate_class_balance,
)
from scripts.run_signature_balanced_field_7c import (
    _class_balanced_calibration_values,
)


def _row(state: str, class_id: str, transition: str) -> dict[str, object]:
    return {
        "state_example_id": state,
        "signature_class_id": class_id,
        "transition_id": transition,
    }


def test_signature_class_balance_equalizes_states_classes_and_members() -> None:
    rows = [
        _row("s1", "c1", "t1"),
        _row("s1", "c1", "t2"),
        _row("s1", "c1", "t3"),
        _row("s1", "c2", "t4"),
        _row("s2", "c3", "t1"),
        _row("s2", "c4", "t2"),
    ]
    weights = state_class_balanced_weights(rows)
    validation = validate_class_balance(rows, weights)
    assert validation["passed"]
    assert sum(weights) == pytest.approx(1.0)
    assert sum(weights[:4]) == pytest.approx(0.5)
    assert sum(weights[:3]) == pytest.approx(weights[3])
    assert weights[0] == pytest.approx(weights[1])
    assert weights[1] == pytest.approx(weights[2])


def test_field_contains_only_state_transition_interaction_without_bias() -> None:
    torch.manual_seed(7)
    model = SignatureBalancedFieldSelector(
        state_views=2,
        transition_views=3,
        input_dim=5,
        projection_dim=4,
        interaction_rank=2,
    )
    assert all(parameter.ndim != 1 for parameter in model.parameters())
    state = torch.randn(4, 2, 5)
    transition = torch.randn(6, 3, 5)
    scores = model.score_matrix(state, transition)
    assert scores.shape == (4, 6)
    assert torch.equal(
        model.score_matrix(torch.zeros_like(state), transition),
        torch.zeros_like(scores),
    )
    assert torch.equal(
        model.score_matrix(state, torch.zeros_like(transition)),
        torch.zeros_like(scores),
    )


def test_class_score_is_member_mean_not_duplicate_reward() -> None:
    targets = [
        ClassTarget("c1", (0, 1, 2), 4.0, 4, 1.0, 1.0, "read"),
        ClassTarget("c2", (3,), 2.0, 2, 0.0, 1.0, "read"),
    ]
    scores = aggregate_class_scores(torch.tensor([1.0, 2.0, 3.0, 4.0]), targets)
    assert scores.tolist() == pytest.approx([2.0, 4.0])


def test_seed_calibration_is_invariant_to_signature_member_duplication() -> None:
    scores = torch.tensor([[1.0, 1.0, 5.0], [2.0, 2.0, 6.0]])
    state_positions = {"s1": 0, "s2": 1}
    transition_positions = {"t1": 0, "t2": 1, "t3": 2}
    base = [
        _row("s1", "c1", "t1"),
        _row("s1", "c2", "t3"),
        _row("s2", "c1", "t1"),
        _row("s2", "c2", "t3"),
    ]
    duplicated = [base[0], _row("s1", "c1", "t2"), *base[1:]]
    base_values = _class_balanced_calibration_values(
        rows=base,
        scores=scores,
        state_positions=state_positions,
        transition_positions=transition_positions,
    )
    duplicated_values = _class_balanced_calibration_values(
        rows=duplicated,
        scores=scores,
        state_positions=state_positions,
        transition_positions=transition_positions,
    )
    assert base_values.tolist() == pytest.approx([1.0, 5.0, 2.0, 6.0])
    assert duplicated_values.tolist() == pytest.approx(base_values.tolist())
    ensemble, stats = calibrated_ensemble([base_values], [scores])
    assert ensemble.shape == scores.shape
    assert stats[0]["train_std"] > 0.0


def test_scoreable_exemplar_uses_canonical_then_locked_median_sha_rule() -> None:
    class_row = {
        "signature_class_id": "c",
        "member_transition_ids": ["t1", "t2", "t3"],
        "canonical_transition_id": "t2",
    }
    transitions = {
        "t1": {"teacher_section_tokens": 10},
        "t2": {"teacher_section_tokens": 20},
        "t3": {"teacher_section_tokens": 30},
    }
    canonical = select_scoreable_class_exemplar(
        class_row=class_row,
        legal_rows=[
            {"transition_id": "t1", "scoreable_under_context": True},
            {"transition_id": "t2", "scoreable_under_context": True},
        ],
        transitions_by_id=transitions,
    )
    assert canonical["transition_id"] == "t2"
    assert not canonical["scoreable_substitution"]
    substituted = select_scoreable_class_exemplar(
        class_row=class_row,
        legal_rows=[
            {"transition_id": "t1", "scoreable_under_context": True},
            {"transition_id": "t2", "scoreable_under_context": False},
            {"transition_id": "t3", "scoreable_under_context": True},
        ],
        transitions_by_id=transitions,
    )
    assert substituted["transition_id"] in {"t1", "t3"}
    assert substituted["scoreable_substitution"]


def test_semantic_condition_key_deduplicates_names_but_not_prompt_content() -> None:
    base = {
        "state_example_id": "s",
        "condition_name": "F1",
        "prompt_kind": "raw_transition",
        "transition_id": "t",
    }
    renamed = {**base, "condition_name": "F3"}
    card = {**base, "condition_name": "F2", "prompt_kind": "signature_card"}
    assert condition_semantic_key(base) == condition_semantic_key(renamed)
    assert condition_semantic_key(base) != condition_semantic_key(card)


def test_grouped_folds_hold_out_tasks_and_parents_exactly_once() -> None:
    tasks = [f"task-{index}" for index in range(7)]
    parents = [f"parent-{index}" for index in range(5)]
    folds = grouped_task_parent_folds(tasks, parents, fold_count=3, seed=17)
    assert len(folds) == 3
    assert set().union(*(fold["heldout_tasks"] for fold in folds)) == set(tasks)
    assert set().union(*(fold["heldout_parents"] for fold in folds)) == set(parents)
    for index, fold in enumerate(folds):
        for other in folds[index + 1 :]:
            assert fold["heldout_tasks"].isdisjoint(other["heldout_tasks"])
            assert fold["heldout_parents"].isdisjoint(other["heldout_parents"])


def test_clean_multiview_renderer_matches_immutable_exp020_contract() -> None:
    current = load_config(
        "configs/benchmark/stage_c_signature_balanced_field_7c.yaml"
    )
    exp020 = load_config(
        "configs/benchmark/stage_c_all_task_interaction_6d.yaml"
    )
    assert current.raw["stage_c_7c"]["multiview_cache"]["renderer_version"] == (
        exp020.raw["stage_c_6d"]["multiview"]["renderer_version"]
    )


def test_field_training_resume_matches_uninterrupted_optimizer_state() -> None:
    states = ["s1", "s2", "s3", "s4"]
    transitions = ["t1", "t2", "t3", "t4"]
    rows = []
    for state_index, state in enumerate(states):
        for transition_index, transition in enumerate(transitions):
            rows.append(
                {
                    "state_example_id": state,
                    "transition_id": transition,
                    "signature_class_id": f"c{transition_index}",
                    "procedural_tier": (state_index + transition_index) % 5,
                    "exact_api_sequence": transition_index == state_index,
                    "state_stage_compatible": transition_index % 2 == 0,
                    "transition_coarse_action_type": (
                        "read" if transition_index < 3 else "write"
                    ),
                }
            )
    torch.manual_seed(19)
    state_values = torch.randn(4, 2, 5)
    transition_values = torch.randn(4, 2, 5)
    candidate = {
        "learning_rate": 1.0e-3,
        "epochs": 2,
        "temperature": 1.0,
        "listwise_weight": 1.0,
        "pairwise_weight": 1.0,
        "hard_negative_weight": 0.5,
        "exact_api_weight": 0.1,
        "stage_weight": 0.1,
    }

    def model() -> SignatureBalancedFieldSelector:
        torch.manual_seed(23)
        return SignatureBalancedFieldSelector(
            state_views=2,
            transition_views=2,
            input_dim=5,
            projection_dim=3,
            interaction_rank=2,
        )

    uninterrupted = model()
    train_field_selector(
        model=uninterrupted,
        rows=rows,
        state_representations=state_values,
        transition_representations=transition_values,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        candidate=candidate,
        batch_states=2,
        maximum_pair_samples_per_state=16,
        maximum_hard_samples_per_state=8,
        weight_decay=0.0,
        seed=29,
        device=torch.device("cpu"),
    )
    first_stage = model()
    checkpoint: dict[str, object] = {}
    first_candidate = {**candidate, "epochs": 1}
    train_field_selector(
        model=first_stage,
        rows=rows,
        state_representations=state_values,
        transition_representations=transition_values,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        candidate=first_candidate,
        batch_states=2,
        maximum_pair_samples_per_state=16,
        maximum_hard_samples_per_state=8,
        weight_decay=0.0,
        seed=29,
        device=torch.device("cpu"),
        checkpoint_callback=lambda payload: checkpoint.update(payload),
        checkpoint_interval_epochs=1,
    )
    resumed = model()
    train_field_selector(
        model=resumed,
        rows=rows,
        state_representations=state_values,
        transition_representations=transition_values,
        ordered_state_ids=states,
        ordered_transition_ids=transitions,
        candidate=candidate,
        batch_states=2,
        maximum_pair_samples_per_state=16,
        maximum_hard_samples_per_state=8,
        weight_decay=0.0,
        seed=29,
        device=torch.device("cpu"),
        resume=checkpoint,
    )
    for expected, actual in zip(
        uninterrupted.parameters(), resumed.parameters(), strict=True
    ):
        assert torch.equal(expected, actual)
