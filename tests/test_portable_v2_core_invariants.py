from __future__ import annotations

import ast
import inspect
import textwrap

import torch

from rcmf.training.rcmf_joint_full_bank_9a import RCMFFieldRecord, ReversibleRCMFField


def _record(index: int) -> RCMFFieldRecord:
    generator = torch.Generator().manual_seed(1000 + index)
    return RCMFFieldRecord(
        memory_id=f"memory-{index}",
        parent_id=f"parent-{index // 2}",
        parent_task_id=f"task-{index // 2}",
        key=torch.randn(5, generator=generator),
        payload=torch.randn(3, 4, generator=generator),
        rho=1.0 / (index + 1),
        mu=0.1 * index,
    )


def test_reversible_add_remove_and_fixed_size_read() -> None:
    field = ReversibleRCMFField(key_dim=5, slot_count=3, payload_dim=4)
    empty_a = field.A.clone()
    empty_b = field.B.clone()
    shape = field.field_shape
    first = _record(0)
    field.add_memory_fast(first)
    one_a = field.A.clone()
    one_b = field.B.clone()
    query = torch.arange(5, dtype=torch.float32)
    assert torch.allclose(field.read(query), field.explicit_read(query), atol=1.0e-6)
    for index in range(1, 17):
        field.add_memory_fast(_record(index))
    assert field.field_shape == shape == {"A": (5, 3, 4), "B": (3, 4)}
    removed = field.remove_parent_fast("parent-0")
    field.restore_parent_fast(removed)
    rebuilt_a, rebuilt_b = field.audit_rebuild()
    assert torch.allclose(field.A, rebuilt_a, atol=1.0e-6)
    assert torch.allclose(field.B, rebuilt_b, atol=1.0e-6)
    for index in reversed(range(17)):
        field.remove_memory_fast(f"memory-{index}")
    assert torch.allclose(field.A, empty_a, atol=1.0e-5)
    assert torch.allclose(field.B, empty_b, atol=1.0e-5)
    field.add_memory_fast(first)
    assert torch.allclose(field.A, one_a, atol=1.0e-5)
    assert torch.allclose(field.B, one_b, atol=1.0e-5)


def test_production_add_remove_read_functions_do_not_loop_over_bank() -> None:
    for method in (
        ReversibleRCMFField.add_memory_fast,
        ReversibleRCMFField.remove_memory_fast,
        ReversibleRCMFField.read,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        assert not any(isinstance(node, (ast.For, ast.AsyncFor, ast.While)) for node in ast.walk(tree))
