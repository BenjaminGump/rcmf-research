from __future__ import annotations

import copy
import hashlib
import json
from itertools import pairwise

import pytest
import torch

from rcmf.training.oracle_convergence_5fa import (
    IndependentPairTensorTable,
    apply_independent_optimizer_step,
    load_training_checkpoint,
    save_training_checkpoint,
)
from rcmf.training.oracle_convergence_5fb import (
    eligible_plateau,
    extension_checkpoint_schedule,
    final_control_bootstrap,
    metric_reproduction_report,
    numerical_instability_report,
    tensor_state_sha256,
    terminal_decision,
    validate_source_checkpoint_payload,
)


def _step(
    table: IndependentPairTensorTable,
    optimizer: torch.optim.Optimizer,
    counts: list[int],
    index: int,
    target: float,
) -> None:
    value = table.forward_indices([index])
    loss = (value - target).pow(2).mean()
    apply_independent_optimizer_step(
        optimizer=optimizer,
        loss=loss,
        table=table,
        selected_indices=[index],
        update_counts=counts,
    )


def _saved_payload(tmp_path, *, rounds: int = 1):
    pair_ids = ["a", "b"]
    table = IndependentPairTensorTable(pair_ids, (2,))
    optimizer = torch.optim.AdamW(table.parameters(), lr=0.05, weight_decay=0.0)
    counts = [0, 0]
    for _ in range(rounds):
        _step(table, optimizer, counts, 0, 1.0)
        _step(table, optimizer, counts, 1, -1.0)
    path = tmp_path / "source.pt"
    save_training_checkpoint(
        path,
        table=table,
        optimizer=optimizer,
        update_counts=counts,
        completed_rounds=rounds,
        metadata={
            "component": "direct_delta",
            "objective": "sequence_utility_plus_sparse_kl",
            "ratio_budget": 1.0,
            "k": 4,
            "pair_ids": pair_ids,
        },
    )
    return path, table, optimizer, counts


def test_source_resume_validation_checks_exact_ids_updates_optimizer_and_legacy_hash(
    tmp_path,
) -> None:
    path, table, _, _ = _saved_payload(tmp_path, rounds=3)
    payload = torch.load(path, map_location="cpu", weights_only=False)

    report = validate_source_checkpoint_payload(
        payload,
        expected_pair_ids=["a", "b"],
        expected_updates=3,
        expected_lr=0.05,
    )

    assert report["passed"] is True
    assert report["update_accounting"]["minimum_updates_per_pair"] == 3
    assert report["update_accounting"]["maximum_updates_per_pair"] == 3
    assert report["optimizer_state_present"] is True
    assert report["optimizer_state_count"] == 2
    assert report["legacy_checkpoint_has_embedded_delta_hash"] is False
    assert report["delta_tensor_sha256"] == tensor_state_sha256(table.state_dict())


def test_source_resume_validation_rejects_pair_order_or_missing_optimizer(tmp_path) -> None:
    path, _, _, _ = _saved_payload(tmp_path)
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["optimizer_state_dict"] = {"state": {}, "param_groups": []}

    report = validate_source_checkpoint_payload(
        payload,
        expected_pair_ids=["b", "a"],
        expected_updates=1,
        expected_lr=0.05,
    )

    assert report["passed"] is False
    assert any("ordered pair IDs" in error for error in report["errors"])
    assert any("optimizer state" in error for error in report["errors"])


def test_resume_produces_same_next_update_as_uninterrupted_run(tmp_path) -> None:
    path, uninterrupted, uninterrupted_optimizer, counts = _saved_payload(tmp_path, rounds=2)
    resumed = IndependentPairTensorTable(["a", "b"], (2,))
    resumed_optimizer = torch.optim.AdamW(resumed.parameters(), lr=0.05, weight_decay=0.0)
    restored = load_training_checkpoint(path, table=resumed, optimizer=resumed_optimizer)

    _step(uninterrupted, uninterrupted_optimizer, counts, 1, 0.25)
    restored_counts = restored["update_counts"]
    _step(resumed, resumed_optimizer, restored_counts, 1, 0.25)

    assert counts == restored_counts
    for key, value in uninterrupted.state_dict().items():
        assert torch.equal(value, resumed.state_dict()[key])
    assert (
        uninterrupted_optimizer.state_dict()["state"].keys()
        == resumed_optimizer.state_dict()["state"].keys()
    )


def test_tensor_hash_is_stable_and_detects_a_changed_delta() -> None:
    state = {"rows.0": torch.tensor([[1.0, -2.0]]), "rows.1": torch.tensor([[3.0, 4.0]])}
    copied = copy.deepcopy(state)

    legacy_audit_digest = hashlib.sha256()
    for key, value in sorted(state.items()):
        tensor = value.detach().cpu().contiguous()
        legacy_audit_digest.update(key.encode("utf-8"))
        legacy_audit_digest.update(str(tensor.dtype).encode("ascii"))
        legacy_audit_digest.update(json.dumps(list(tensor.shape)).encode("ascii"))
        legacy_audit_digest.update(tensor.numpy().tobytes(order="C"))

    assert tensor_state_sha256(state) == tensor_state_sha256(copied)
    assert tensor_state_sha256(state) == legacy_audit_digest.hexdigest()
    copied["rows.1"][0, 0] += 1.0
    assert tensor_state_sha256(state) != tensor_state_sha256(copied)


def test_fixed_extension_schedule_starts_at_u80_and_ends_at_u256() -> None:
    schedule = extension_checkpoint_schedule()

    assert schedule[:4] == [80, 96, 112, 128]
    assert schedule[-1] == 256
    assert all(right - left == 16 for left, right in pairwise(schedule))


def test_plateau_cannot_terminate_before_u128() -> None:
    history = [
        {
            "updates_per_pair": 96,
            "pair_ids": ["a"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.1000},
                "u_text_vs_u_student_spearman": 0.90,
            },
        },
        {
            "updates_per_pair": 112,
            "pair_ids": ["a"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.0995},
                "u_text_vs_u_student_spearman": 0.905,
            },
        },
    ]

    early = eligible_plateau(history, current_updates=112)
    history.append(
        {
            "updates_per_pair": 128,
            "pair_ids": ["a"],
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.0991},
                "u_text_vs_u_student_spearman": 0.909,
            },
        }
    )
    eligible = eligible_plateau(history, current_updates=128)

    assert early["plateau"] is True
    assert early["eligible_to_stop"] is False
    assert eligible["plateau"] is True
    assert eligible["eligible_to_stop"] is True


def test_two_consecutive_large_huber_worsenings_are_numerically_unstable() -> None:
    history = [
        {
            "updates_per_pair": 64,
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.1},
                "u_text_vs_u_student_spearman": 0.9,
            },
        },
        {
            "updates_per_pair": 80,
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.13},
                "u_text_vs_u_student_spearman": 0.8,
            },
        },
        {
            "updates_per_pair": 96,
            "evaluation_summary": {
                "sequence_utility_huber": {"mean": 0.17},
                "u_text_vs_u_student_spearman": 0.7,
            },
        },
    ]

    report = numerical_instability_report(history)

    assert report["unstable"] is True
    assert report["two_consecutive_gt_25_percent"] is True


def test_metric_reproduction_uses_fixed_absolute_tolerance() -> None:
    report = metric_reproduction_report(
        actual={"metric": {"mean": 1.00001}},
        expected={"metric": {"mean": 1.0}},
        paths=[("metric", "mean")],
        tolerance=5.0e-5,
    )

    assert report["passed"] is True
    assert report["maximum_absolute_delta"] == pytest.approx(1.0e-5)


def _control_rows(scale: float) -> list[dict]:
    values = [1.0, 0.5, -0.5, -1.0]
    rows = []
    for index, utility in enumerate(values):
        student = utility * scale
        error = student - utility
        rows.append(
            {
                "pair_id": f"p{index}",
                "u_text": utility,
                "u_student": student,
                "sequence_utility_huber": 0.5 * error * error,
            }
        )
    return rows


def test_final_control_bootstrap_is_paired_deterministic_and_oriented() -> None:
    final = _control_rows(0.9)
    zero = _control_rows(0.0)
    random = list(reversed(_control_rows(-0.1)))
    u64 = _control_rows(0.7)

    first = final_control_bootstrap(
        final_rows=final,
        zero_rows=zero,
        random_rows=random,
        u64_rows=u64,
        samples=200,
        seed=13,
    )
    second = final_control_bootstrap(
        final_rows=final,
        zero_rows=zero,
        random_rows=random,
        u64_rows=u64,
        samples=200,
        seed=13,
    )

    assert first == second
    assert first["final_minus_zero_sequence_huber"]["point_estimate"] < 0.0
    assert first["final_minus_u64_sequence_huber"]["point_estimate"] < 0.0
    assert first["final_minus_zero_sign_agreement"]["point_estimate"] > 0.0


@pytest.mark.parametrize(
    ("plateau", "gate", "updates", "expected"),
    [
        (True, True, 128, "input_embedding_channel_capacity_passed_after_convergence"),
        (True, False, 128, "converged_input_embedding_channel_insufficient"),
        (False, False, 256, "direct_oracle_still_improving_at_hard_cap"),
    ],
)
def test_terminal_decision_branches(plateau: bool, gate: bool, updates: int, expected: str) -> None:
    assert (
        terminal_decision(final_updates=updates, plateau=plateau, gate_passed=gate)["branch"]
        == expected
    )
