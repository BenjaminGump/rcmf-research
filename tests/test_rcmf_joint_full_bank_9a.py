from __future__ import annotations

import copy

import pytest
import torch
from torch import nn

from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    FieldReaderHooks,
    FrozenSelectorDecomposition,
    RCMFFieldRecord,
    ReversibleRCMFField,
    SLOT_COUNT,
    StandardFieldCrossAttentionReader,
    compile_differentiable_field,
    deterministic_payload_permutation,
    read_compiled_field,
    rms_norm,
    subtract_task_field,
)
from rcmf.training.signature_balanced_field_7c import (
    SignatureBalancedFieldSelector,
)
from scripts.prepare_rcmf_joint_full_bank_9a import _signature_map


class _Block(nn.Module):
    def __init__(self, width: int) -> None:
        super().__init__()
        self.projection = nn.Linear(width, width, bias=False)

    def forward(self, hidden_states: torch.Tensor) -> tuple[torch.Tensor]:
        return (hidden_states + 0.05 * self.projection(hidden_states),)


class _Backbone(nn.Module):
    def __init__(self, layers: int, width: int) -> None:
        super().__init__()
        self.layers = nn.ModuleList(_Block(width) for _ in range(layers))


class _Model(nn.Module):
    def __init__(self, layers: int = 4, width: int = 8) -> None:
        super().__init__()
        self.model = _Backbone(layers, width)

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        for layer in self.model.layers:
            hidden = layer(hidden)[0]
        return hidden


def _record(identity: str, parent: str, task: str, seed: int) -> RCMFFieldRecord:
    generator = torch.Generator().manual_seed(seed)
    return RCMFFieldRecord(
        memory_id=identity,
        parent_id=parent,
        parent_task_id=task,
        key=torch.randn(960, generator=generator),
        payload=torch.randn(8, 256, generator=generator),
        rho=0.5 if parent == "p1" else 1.0,
    )


def test_writer_uses_eight_aligned_slots_and_observation_is_not_dropped() -> None:
    torch.manual_seed(25101)
    writer = AlignedTransitionWriter(input_dim=8, hidden_dim=6, payload_dim=5)
    views = torch.randn(2, 8, 8)
    changed = views.clone()
    changed[:, 6:] = torch.randn_like(changed[:, 6:])
    original_payload = writer(views)
    changed_payload = writer(changed)
    assert tuple(original_payload.shape) == (2, 8, 5)
    assert torch.equal(original_payload[:, :6], changed_payload[:, :6])
    assert not torch.equal(original_payload[:, 6:], changed_payload[:, 6:])
    assert writer.writers["goal"].input is writer.writers["goal"].input
    assert writer.writers["goal"] is not writer.writers["observation"]


def test_selector_decomposition_matches_calibrated_ensemble() -> None:
    torch.manual_seed(25101)
    models = [
        SignatureBalancedFieldSelector(
            state_views=2,
            transition_views=3,
            input_dim=4,
            projection_dim=5,
            interaction_rank=2,
        )
        for _ in range(3)
    ]
    decomposition = FrozenSelectorDecomposition(
        models=models,
        train_means=[0.2, -0.1, 0.7],
        train_stds=[0.9, 1.3, 0.8],
    )
    state = torch.randn(7, 2, 4)
    transition = torch.randn(11, 3, 4)
    direct = decomposition.direct_scores(state, transition)
    decomposed = decomposition.decomposed_scores(state, transition)
    assert decomposition.key_dim == 12
    assert torch.allclose(direct, decomposed, atol=1.0e-5, rtol=1.0e-5)


def test_reversible_field_matches_explicit_sum_and_rebuild() -> None:
    field = ReversibleRCMFField()
    records = [
        _record("a", "p1", "t1", 1),
        _record("b", "p1", "t1", 2),
        _record("c", "p2", "t2", 3),
    ]
    for record in records:
        field.add_memory_fast(record)
    query = torch.randn(960)
    assert torch.allclose(field.read(query), field.explicit_read(query), atol=2.0e-5)
    A, B = field.audit_rebuild()
    assert torch.allclose(field.A, A, atol=1.0e-6)
    assert torch.allclose(field.B, B, atol=1.0e-6)
    assert field.field_shape == {"A": (960, 8, 256), "B": (8, 256)}


def test_fast_add_remove_replace_parent_restore_and_no_unrelated_iteration() -> None:
    class NoScanDict(dict[str, RCMFFieldRecord]):
        def __iter__(self):
            raise AssertionError("unrelated records were iterated")

        def items(self):
            raise AssertionError("unrelated records were iterated")

        def values(self):
            raise AssertionError("unrelated records were iterated")

    field = ReversibleRCMFField()
    field.records = NoScanDict()
    a = _record("a", "p1", "t1", 1)
    b = _record("b", "p1", "t1", 2)
    c = _record("c", "p2", "t2", 3)
    field.add_memory_fast(a)
    field.add_memory_fast(b)
    baseline = field.A.clone()
    _ = field.read(torch.randn(960))
    field.add_memory_fast(c)
    field.remove_memory_fast("c")
    assert torch.allclose(field.A, baseline, atol=1.0e-6)
    replacement = _record("a2", "p2", "t2", 4)
    field.replace_memory_fast("a", replacement)
    removed = field.remove_parent_fast("p1")
    assert [record.memory_id for record in removed] == ["b"]
    field.restore_parent_fast(removed)
    assert set(field.records.keys()) == {"a2", "b"}


@pytest.mark.parametrize("size", [1, 8, 64])
def test_field_shape_is_independent_of_bank_size(size: int) -> None:
    field = ReversibleRCMFField()
    for index in range(size):
        field.add_memory_fast(_record(str(index), f"p{index}", f"t{index}", index + 1))
    assert field.field_shape == {"A": (960, 8, 256), "B": (8, 256)}
    assert tuple(field.read(torch.randn(960)).shape) == (8, 256)


def test_zero_field_and_task_subtraction_match_explicit_legal_bank() -> None:
    empty = ReversibleRCMFField()
    assert torch.equal(empty.read(torch.randn(960)), torch.zeros(8, 256))

    records = [
        _record("a", "p1", "task-a", 1),
        _record("b", "p2", "task-b", 2),
        _record("c", "p3", "task-b", 3),
    ]
    keys = torch.stack([row.key for row in records])
    payloads = torch.stack([row.payload for row in records])
    rho = torch.tensor([row.rho for row in records])
    total_A, total_B = compile_differentiable_field(
        keys=keys, payloads=payloads, rho=rho
    )
    task_mask = torch.tensor([False, True, True])
    task_A, task_B = compile_differentiable_field(
        keys=keys[task_mask], payloads=payloads[task_mask], rho=rho[task_mask]
    )
    legal_A, legal_B = subtract_task_field(
        A_total=total_A, B_total=total_B, A_task=task_A, B_task=task_B
    )
    explicit_A, explicit_B = compile_differentiable_field(
        keys=keys[~task_mask], payloads=payloads[~task_mask], rho=rho[~task_mask]
    )
    query = torch.randn(960)
    assert torch.allclose(legal_A, explicit_A, atol=1.0e-5)
    assert torch.allclose(legal_B, explicit_B, atol=1.0e-6)
    assert torch.allclose(
        read_compiled_field(query=query, A=legal_A, B=legal_B, nonempty=True),
        read_compiled_field(query=query, A=explicit_A, B=explicit_B, nonempty=True),
        atol=1.0e-5,
    )


def test_arbitrary_insertion_order_and_payload_derangement() -> None:
    records = [_record(str(i), f"p{i}", f"task-{i % 3}", i + 1) for i in range(8)]
    first = ReversibleRCMFField()
    second = ReversibleRCMFField()
    for record in records:
        first.add_memory_fast(record)
    for record in reversed(records):
        second.add_memory_fast(record)
    assert torch.allclose(first.A, second.A, atol=1.0e-5)
    rows = [
        {
            "transition_id": row.memory_id,
            "parent_task_id": row.parent_task_id,
            "signature_class_id": f"sig-{index % 4}",
        }
        for index, row in enumerate(records)
    ]
    permutation = deterministic_payload_permutation(rows)
    assert sorted(permutation) == list(range(len(rows)))
    assert all(index != target for index, target in enumerate(permutation))


def test_signature_equivalence_manifest_expands_members() -> None:
    payload = {
        "classes": [
            {
                "signature_class_id": "signature-a",
                "member_transition_ids": ["transition-1", "transition-2"],
            },
            {
                "signature_class_id": "signature-b",
                "member_transition_ids": ["transition-3"],
            },
        ]
    }
    assert _signature_map(payload) == {
        "transition-1": "signature-a",
        "transition-2": "signature-a",
        "transition-3": "signature-b",
    }


def test_reader_zero_equivalence_decode_access_and_save_load() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = StandardFieldCrossAttentionReader(
        insertion_layers=(0, 1, 2, 3),
        model_dim=8,
        payload_dim=6,
        attention_dim=8,
        heads=2,
    )
    hidden = torch.randn(1, 5, 8)
    slots = torch.randn(1, SLOT_COUNT, 6)
    bare = model(hidden)
    with FieldReaderHooks(model=model, reader=reader, slots=slots) as audit:
        initial = model(hidden)
        decode = model(torch.randn(1, 1, 8))
    assert torch.equal(initial, bare)
    assert reader.outputs_are_zero()
    assert tuple(decode.shape) == (1, 1, 8)
    assert set(audit.calls) == {0, 1, 2, 3}
    assert all(lengths == [5, 1] for lengths in audit.query_lengths.values())

    for adapter in reader.adapters.values():
        nn.init.normal_(adapter.output.weight, std=0.03)
    restored = copy.deepcopy(reader)
    with FieldReaderHooks(model=model, reader=reader, slots=slots):
        expected = model(hidden)
    with FieldReaderHooks(model=model, reader=restored, slots=slots):
        actual = model(hidden)
    assert torch.equal(actual, expected)
    with FieldReaderHooks(model=model, reader=reader, slots=slots.roll(1, dims=1)):
        shuffled = model(hidden)
    assert not torch.equal(expected, shuffled)


def test_all_readers_and_writers_receive_full_field_gradients_while_qwen_is_frozen() -> None:
    torch.manual_seed(25101)
    model = _Model()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    writer = AlignedTransitionWriter(input_dim=8, hidden_dim=6, payload_dim=6)
    reader = StandardFieldCrossAttentionReader(
        insertion_layers=(0, 1, 2, 3),
        model_dim=8,
        payload_dim=6,
        attention_dim=8,
        heads=2,
    )
    for adapter in reader.adapters.values():
        nn.init.normal_(adapter.output.weight, std=0.03)

    views = torch.randn(5, 8, 8)
    keys = torch.randn(5, 7)
    rho = torch.full((5,), 0.2)
    payloads = writer(views)
    A = torch.einsum("nk,nsp,n->ksp", keys, payloads, rho)
    slots = rms_norm(torch.einsum("k,ksp->sp", torch.randn(7), A)).unsqueeze(0)
    hidden = torch.randn(1, 4, 8)
    with FieldReaderHooks(model=model, reader=reader, slots=slots):
        loss = model(hidden).square().mean()
        loss.backward()

    assert all(
        parameter.grad is not None and bool(torch.isfinite(parameter.grad).all())
        for parameter in writer.parameters()
    )
    assert all(
        parameter.grad is not None and bool(torch.isfinite(parameter.grad).all())
        for parameter in reader.parameters()
    )
    assert all(parameter.grad is None for parameter in model.parameters())
