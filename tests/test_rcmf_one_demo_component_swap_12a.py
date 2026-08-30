from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import torch

from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    CONDITIONS,
    compile_field_pair,
    condition_order_for_task,
    condition_parts,
    field_rebuild_errors,
    load_writer_reader_package,
    permutation_from_rows,
    remove_restore_error,
    select_leakage_safe_memory_ids,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_condition_cells_and_counterbalanced_order() -> None:
    assert len(CONDITIONS) == 8
    orders = [condition_order_for_task(index) for index in range(8)]
    assert all(set(order) == set(CONDITIONS) for order in orders)
    for position in range(8):
        assert {order[position] for order in orders} == set(CONDITIONS)
    assert condition_parts("OF-S") == ("OF", "S", "old", "fresh")
    assert condition_parts("FO-C") == ("FO", "C", "fresh", "old")


def test_common_shuffle_is_bijection_and_preserves_payload_multiset() -> None:
    ids = ["a", "b", "c", "d"]
    rows = [
        {"key_transition_id": "a", "payload_transition_id": "b"},
        {"key_transition_id": "b", "payload_transition_id": "c"},
        {"key_transition_id": "c", "payload_transition_id": "d"},
        {"key_transition_id": "d", "payload_transition_id": "a"},
    ]
    permutation = permutation_from_rows(ids, rows)
    payloads = torch.arange(4 * 8 * 256, dtype=torch.float32).reshape(4, 8, 256)
    assert sorted(permutation.tolist()) == list(range(4))
    assert torch.equal(
        payloads.flatten(start_dim=1).sort(dim=0).values,
        payloads[permutation].flatten(start_dim=1).sort(dim=0).values,
    )


def test_shuffle_with_fixed_point_is_rejected() -> None:
    rows = [
        {"key_transition_id": "a", "payload_transition_id": "a"},
        {"key_transition_id": "b", "payload_transition_id": "b"},
    ]
    with pytest.raises(ValueError, match="fixed point"):
        permutation_from_rows(["a", "b"], rows)


def test_cross_field_assembly_rebuild_and_remove_restore() -> None:
    generator = torch.Generator().manual_seed(25101)
    keys = torch.randn(4, 960, generator=generator)
    old_payloads = torch.randn(4, 8, 256, generator=generator)
    fresh_payloads = torch.randn(4, 8, 256, generator=generator)
    rho = torch.tensor([0.25, 0.25, 0.5, 1.0])
    permutation = torch.tensor([1, 2, 3, 0])
    oo = compile_field_pair(
        keys=keys, payloads=old_payloads, rho=rho, permutation=permutation
    )
    of = compile_field_pair(
        keys=keys, payloads=fresh_payloads, rho=rho, permutation=permutation
    )
    assert tuple(oo["A"].shape) == (960, 8, 256)
    assert tuple(oo["B"].shape) == (8, 256)
    assert not torch.equal(oo["A"], of["A"])
    assert max(field_rebuild_errors(fields=oo, historical=oo).values()) == 0.0
    errors = remove_restore_error(
        fields=oo,
        key=keys[0],
        payload=old_payloads[0],
        shuffled_payload=old_payloads[permutation[0]],
        rho=float(rho[0]),
    )
    assert max(errors.values()) <= 1.0e-5


def test_401_memory_exclusion_uses_parent_tasks() -> None:
    ids = ["m1", "m2", "m3"]
    parents = {"m1": "train-a", "m2": "heldout-a", "m3": "train-b"}
    selected = select_leakage_safe_memory_ids(
        ordered_transition_ids=ids,
        parent_task_by_transition=parents,
        train_task_ids=["train-a", "train-b"],
        heldout_task_ids=["heldout-a"],
    )
    assert selected == ["m1", "m3"]


def test_writer_reader_package_strict_sha_and_strict_load(tmp_path: Path) -> None:
    writer = AlignedTransitionWriter()
    reader = StandardFieldCrossAttentionReader()
    checkpoint = {
        "writer_state_dict": writer.state_dict(),
        "reader_state_dict": reader.state_dict(),
        "writer_sha256": "writer-test",
        "reader_sha256": "reader-test",
    }
    path = tmp_path / "checkpoint.pt"
    torch.save(checkpoint, path)
    expected = file_sha256(path)
    loaded_writer, loaded_reader, identity = load_writer_reader_package(
        name="test",
        checkpoint_path=path,
        expected_checkpoint_sha256=expected,
    )
    assert identity.checkpoint_sha256 == expected
    assert not any(parameter.requires_grad for parameter in loaded_writer.parameters())
    assert not any(parameter.requires_grad for parameter in loaded_reader.parameters())
    with pytest.raises(ValueError, match="SHA differs"):
        load_writer_reader_package(
            name="test", checkpoint_path=path, expected_checkpoint_sha256="0" * 64
        )
