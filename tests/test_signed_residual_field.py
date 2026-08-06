from __future__ import annotations

import math

import torch

from rcmf.training.addressing_4b import SignedTwoTowerResidualScorer
from rcmf.training.addressing_only import rows_to_tensors
from rcmf.training.signed_residual_field import (
    ReferenceSignedTwoTower,
    SignedAssociativeField,
    SignedLossWeights,
    SignedResidualField,
    build_fold_rows,
    copy_reference_weights_to_core,
    field_algebra_validation,
    gate_labels,
    signed_residual_loss,
    train_memory_prior,
)


def _row(
    *,
    state_index: int,
    state_id: str,
    task_id: str,
    split: str,
    memory_ids: list[str],
    valid: list[bool],
    utility: list[float | None],
) -> dict:
    gain = [max((value or 0.0) - 0.01, 0.0) if ok else 0.0 for value, ok in zip(utility, valid)]
    return {
        "state_index": state_index,
        "state_example_id": state_id,
        "task_id": task_id,
        "episode_id": f"{task_id}_episode",
        "step_index": state_index,
        "split": split,
        "ordered_effective_memory_ids": memory_ids,
        "ordered_effective_memory_indices": list(range(len(memory_ids))),
        "valid_mask": valid,
        "legal_effective_mask": valid,
        "raw_utility": utility,
        "positive_mask": [bool(ok and (value or 0.0) > 0.01) for value, ok in zip(utility, valid)],
        "neutral_mask": [bool(ok and abs(value or 0.0) <= 0.01) for value, ok in zip(utility, valid)],
        "negative_mask": [bool(ok and (value or 0.0) < -0.01) for value, ok in zip(utility, valid)],
        "strong_positive_mask": [bool(ok and (value or 0.0) >= 0.05) for value, ok in zip(utility, valid)],
        "strong_negative_mask": [bool(ok and (value or 0.0) <= -0.05) for value, ok in zip(utility, valid)],
        "positive_gain": gain,
        "score_statuses": ["scoreable" if ok else "masked" for ok in valid],
        "source_pair_keys": [f"{state_id}:{memory_id}" for memory_id in memory_ids],
        "target_sha256_by_memory": [f"target-{state_id}-{memory_id}" for memory_id in memory_ids],
        "memory_text_sha256_by_memory": [f"memory-{memory_id}" for memory_id in memory_ids],
        "no_positive_state": any(valid) and not any(gain_i > 0 for gain_i in gain),
        "all_missing_state": not any(valid),
    }


def test_reference_signed_tower_matches_4b_diagnostic_architecture() -> None:
    torch.manual_seed(1)
    state = torch.randn(5, 11)
    memory = torch.randn(7, 13)
    old = SignedTwoTowerResidualScorer(11, 13, tower_dim=8, hidden=17, dropout=0.0).eval()
    new = ReferenceSignedTwoTower(11, 13, tower_dim=8, hidden_dim=17, dropout=0.0).eval()
    new.state_tower.load_state_dict(old.state_net.state_dict())
    new.memory_tower.load_state_dict(old.memory_net.state_dict())
    assert torch.allclose(old(state, memory), new(state, memory)["residual"], atol=1.0e-7)


def test_core_signed_field_matches_reference_under_copied_weights() -> None:
    torch.manual_seed(2)
    state = torch.randn(4, 9)
    memory = torch.randn(6, 10)
    reference = ReferenceSignedTwoTower(9, 10, tower_dim=12, hidden_dim=16, dropout=0.0).eval()
    core = SignedResidualField(9, 10, rank=12, hidden_dim=16, dropout=0.0).eval()
    copy_reference_weights_to_core(reference, core)
    ref_payload = reference(state, memory)
    core_payload = core(state, memory)
    assert torch.allclose(ref_payload["residual"], core_payload["residual"], atol=1.0e-7)
    assert torch.allclose(ref_payload["gate"], core_payload["gate"], atol=1.0e-7)
    assert torch.allclose(ref_payload["q"], core_payload["q"], atol=1.0e-7)
    assert torch.allclose(ref_payload["k"], core_payload["k"], atol=1.0e-7)


def test_signed_interaction_has_positive_and_negative_gradients() -> None:
    q = torch.tensor([[1.0, -2.0, 0.5]], requires_grad=True)
    k = torch.tensor([[0.25, 0.5, -1.0], [-0.75, 0.0, 0.5]], requires_grad=True)
    residual = (q @ k.T) / math.sqrt(3)
    assert residual[0, 0].item() < 0
    assert residual[0, 1].item() < 0
    loss = residual[0, 0] - residual[0, 1]
    loss.backward()
    assert q.grad is not None and q.grad.abs().sum().item() > 0
    assert k.grad is not None and k.grad.abs().sum().item() > 0
    assert (q.grad > 0).any().item()
    assert (q.grad < 0).any().item()


def test_signed_interaction_no_dead_zone_for_disjoint_largest_coordinates() -> None:
    q = torch.tensor([[5.0, 0.0, 0.0, 0.0]], requires_grad=True)
    k = torch.tensor([[0.0, 0.0, 0.0, 7.0]], requires_grad=True)
    residual = (q * k).sum()
    assert residual.item() == 0.0
    residual.backward()
    assert q.grad is not None and q.grad[0, 3].item() == 7.0
    assert k.grad is not None and k.grad[0, 0].item() == 5.0


def test_signed_loss_masks_all_missing_gate_rows() -> None:
    rows = [
        _row(
            state_index=0,
            state_id="s0",
            task_id="task_a",
            split="train",
            memory_ids=["m0", "m1"],
            valid=[True, True],
            utility=[0.20, -0.10],
        ),
        _row(
            state_index=1,
            state_id="s1",
            task_id="task_b",
            split="train",
            memory_ids=["m0", "m1"],
            valid=[False, False],
            utility=[None, None],
        ),
    ]
    labels = rows_to_tensors(rows)
    target, mask = gate_labels(labels)
    assert target.tolist() == [1.0, 0.0]
    assert mask.tolist() == [True, False]
    residual = torch.zeros(2, 2, requires_grad=True)
    gate = torch.tensor([0.75, 0.01], requires_grad=True)
    loss, metrics = signed_residual_loss(residual, gate, torch.zeros(2), labels, SignedLossWeights())
    loss.backward()
    assert metrics["loss_gate"] > 0
    assert gate.grad is not None
    assert gate.grad[0].abs().item() > 0
    assert gate.grad[1].abs().item() == 0.0


def test_field_algebra_and_reversibility_validation_passes() -> None:
    report = field_algebra_validation(rank=8, program_dim=5, count=6, seed=3)
    assert report["passed"]


def test_signed_associative_field_add_remove_replace_exactness() -> None:
    field = SignedAssociativeField(rank=3, program_dim=2)
    key_a = torch.tensor([1.0, -2.0, 0.5])
    prog_a = torch.tensor([0.25, -0.75])
    key_b = torch.tensor([-0.5, 0.0, 2.0])
    prog_b = torch.tensor([1.5, 0.5])
    field.add("a", key_a, prog_a)
    assert torch.allclose(field.V, torch.outer(key_a, prog_a))
    field.replace("a", key_b, prog_b)
    assert torch.allclose(field.V, torch.outer(key_b, prog_b))
    assert torch.allclose(field.G, torch.outer(key_b, key_b))
    field.remove("a")
    assert torch.allclose(field.V, torch.zeros_like(field.V))
    assert torch.allclose(field.G, torch.zeros_like(field.G))


def test_task_grouped_fold_excludes_validation_memories_and_train_own_task() -> None:
    memory_bank = [
        {"memory_id": "m_task_a", "task_id": "task_a"},
        {"memory_id": "m_task_b", "task_id": "task_b"},
        {"memory_id": "m_task_c", "task_id": "task_c"},
    ]
    memory_ids = [row["memory_id"] for row in memory_bank]
    rows = [
        _row(
            state_index=0,
            state_id="s_a",
            task_id="task_a",
            split="train",
            memory_ids=memory_ids,
            valid=[False, True, True],
            utility=[None, 0.12, -0.20],
        ),
        _row(
            state_index=1,
            state_id="s_b",
            task_id="task_b",
            split="train",
            memory_ids=memory_ids,
            valid=[True, False, True],
            utility=[0.08, None, -0.03],
        ),
        _row(
            state_index=2,
            state_id="s_c",
            task_id="task_c",
            split="validation",
            memory_ids=memory_ids,
            valid=[True, True, False],
            utility=[0.04, -0.02, None],
        ),
    ]
    fold = {"fold": 0, "train_task_ids": ["task_a", "task_b"], "validation_task_ids": ["task_c"]}
    payload = build_fold_rows(rows, memory_bank, fold)
    assert payload["validation"]["passed"]
    assert [memory["memory_id"] for memory in payload["memory_bank"]] == ["m_task_a", "m_task_b"]
    assert payload["train_rows"][0]["valid_mask"] == [False, True]
    assert payload["train_rows"][1]["valid_mask"] == [True, False]
    assert payload["validation_rows"][0]["valid_mask"] == [True, True]


def test_memory_prior_uses_train_rows_only() -> None:
    memory_ids = ["m0", "m1"]
    train_rows = [
        _row(
            state_index=0,
            state_id="train_0",
            task_id="task_a",
            split="train",
            memory_ids=memory_ids,
            valid=[True, True],
            utility=[0.10, -0.20],
        ),
        _row(
            state_index=1,
            state_id="train_1",
            task_id="task_b",
            split="train",
            memory_ids=memory_ids,
            valid=[True, False],
            utility=[0.30, None],
        ),
    ]
    validation_row = _row(
        state_index=2,
        state_id="validation_0",
        task_id="task_c",
        split="validation",
        memory_ids=memory_ids,
        valid=[True, True],
        utility=[100.0, 100.0],
    )
    mu_train = train_memory_prior(train_rows)
    mu_with_validation = train_memory_prior(train_rows + [validation_row])
    assert torch.allclose(mu_train, torch.tensor([0.20, -0.20]))
    assert not torch.allclose(mu_train, mu_with_validation)
