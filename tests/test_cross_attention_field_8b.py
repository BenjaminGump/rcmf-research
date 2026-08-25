from __future__ import annotations

import copy

import torch
from torch import nn

from rcmf.training.cross_attention_field_8b import (
    CrossAttentionMemoryReader,
    CrossAttentionReaderHooks,
    MemoryFieldRecord,
    ReversibleSemanticSlotField,
    deterministic_strided_indices,
    sample_layer_memory_slots,
    selector_ensemble_intercept,
    selector_seed_key,
    selector_seed_query,
)


class _SelfAttention(nn.Module):
    def __init__(self, width: int = 8) -> None:
        super().__init__()
        self.head_dim = 4
        self.num_heads = 2
        self.q_proj = nn.Linear(width, width, bias=False)
        self.k_proj = nn.Linear(width, width, bias=False)
        self.v_proj = nn.Linear(width, width, bias=False)


class _Block(nn.Module):
    def __init__(self, width: int = 8) -> None:
        super().__init__()
        self.self_attn = _SelfAttention(width)

    def forward(self, hidden_states: torch.Tensor) -> tuple[torch.Tensor]:
        return (hidden_states + 0.125,)


class _Backbone(nn.Module):
    def __init__(self, layers: int = 3, width: int = 8) -> None:
        super().__init__()
        self.layers = nn.ModuleList(_Block(width) for _ in range(layers))


class _Model(nn.Module):
    def __init__(self, layers: int = 3, width: int = 8) -> None:
        super().__init__()
        self.model = _Backbone(layers, width)

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        for layer in self.model.layers:
            hidden = layer(hidden)[0]
        return hidden


def test_strided_memory_sampling_is_fixed_and_deterministic() -> None:
    assert deterministic_strided_indices(1) == [0] * 16
    assert deterministic_strided_indices(300)[-1] == 255
    hidden = [torch.arange(40, dtype=torch.float32).reshape(5, 8) for _ in range(3)]
    slots, provenance = sample_layer_memory_slots(hidden, token_count=5)
    assert tuple(slots.shape) == (3, 16, 8)
    assert provenance["slot_count"] == 16
    assert provenance["sampled_token_indices"] == deterministic_strided_indices(5)


def test_no_memory_and_zero_fusion_are_exact_bare() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = CrossAttentionMemoryReader(model_dim=8, layer_count=3, dropout=0.0)
    hidden = torch.randn(1, 5, 8)
    memory = torch.randn(3, 16, 8)
    bare = model(hidden)
    with CrossAttentionReaderHooks(model=model, reader=reader, memory_slots=None):
        no_memory = model(hidden)
    hooks = CrossAttentionReaderHooks(model=model, reader=reader, memory_slots=memory)
    with hooks:
        zero_fusion = model(hidden)
    assert torch.equal(no_memory, bare)
    assert torch.equal(zero_fusion, bare)
    assert reader.output_layers_zero()
    assert set(hooks.audit.calls) == {0, 1, 2}
    assert max(hooks.audit.attention_row_sum_error.values()) < 1.0e-6


def test_trained_cross_attention_is_memory_specific_and_queries_decode_tokens() -> None:
    torch.manual_seed(25101)
    model = _Model()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    reader = CrossAttentionMemoryReader(model_dim=8, layer_count=3, dropout=0.0)
    for layer in reader.layers:
        nn.init.normal_(layer.up.weight, std=0.05)
    hidden = torch.randn(1, 5, 8)
    memory_a = torch.randn(3, 16, 8)
    memory_b = torch.randn(3, 16, 8)
    with CrossAttentionReaderHooks(model=model, reader=reader, memory_slots=memory_a):
        output_a = model(hidden)
        decode_a = model(torch.randn(1, 1, 8))
    with CrossAttentionReaderHooks(model=model, reader=reader, memory_slots=memory_b):
        output_b = model(hidden)
    assert not torch.equal(output_a, output_b)
    assert tuple(decode_a.shape) == (1, 1, 8)
    output_a.sum().backward()
    assert all(layer.up.weight.grad is not None for layer in reader.layers)
    assert all(parameter.grad is None for parameter in model.parameters())


def test_reader_state_round_trip_preserves_outputs() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = CrossAttentionMemoryReader(model_dim=8, layer_count=3, dropout=0.0)
    for layer in reader.layers:
        nn.init.normal_(layer.up.weight, std=0.05)
    restored = CrossAttentionMemoryReader(model_dim=8, layer_count=3, dropout=0.0)
    restored.load_state_dict(copy.deepcopy(reader.state_dict()))
    hidden = torch.randn(1, 3, 8)
    memory = torch.randn(3, 16, 8)
    with CrossAttentionReaderHooks(model=model, reader=reader, memory_slots=memory):
        expected = model(hidden)
    with CrossAttentionReaderHooks(model=model, reader=restored, memory_slots=memory):
        actual = model(hidden)
    assert torch.equal(actual, expected)


def _record(memory_id: str, parent: str, seed: int) -> MemoryFieldRecord:
    generator = torch.Generator().manual_seed(seed)
    return MemoryFieldRecord(
        memory_id=memory_id,
        parent_id=parent,
        key=torch.randn(5, generator=generator),
        mu=float(seed) / 100.0,
        slots=torch.randn(3, 4, 6, generator=generator),
        rho=0.5 if parent == "p1" else 1.0,
    )


def test_reversible_field_matches_explicit_sum_and_audit_rebuild() -> None:
    field = ReversibleSemanticSlotField(
        layer_count=3, key_dim=5, slot_count=4, model_dim=6, key_chunk_size=2
    )
    records = [_record("a", "p1", 1), _record("b", "p1", 2), _record("c", "p2", 3)]
    for record in records:
        field.add_memory_fast(record)
    query = torch.randn(5)
    assert torch.allclose(field.read(query), field.explicit_read(query), atol=1.0e-5)
    rebuilt_a, rebuilt_b = field.audit_rebuild()
    assert torch.allclose(field.A, rebuilt_a, atol=1.0e-6)
    assert torch.allclose(field.B, rebuilt_b, atol=1.0e-6)
    assert field.field_shape == {"A": (3, 5, 4, 6), "B": (3, 4, 6)}


def test_reversible_field_add_remove_replace_and_parent_restore() -> None:
    field = ReversibleSemanticSlotField(
        layer_count=3, key_dim=5, slot_count=4, model_dim=6
    )
    a = _record("a", "p1", 1)
    b = _record("b", "p1", 2)
    c = _record("c", "p2", 3)
    field.add_memory_fast(a)
    field.add_memory_fast(b)
    baseline = (field.A.clone(), field.B.clone())
    field.add_memory_fast(c)
    field.remove_memory_fast("c")
    assert torch.allclose(field.A, baseline[0], atol=1.0e-6)
    assert torch.allclose(field.B, baseline[1], atol=1.0e-6)
    replacement = _record("a2", "p2", 4)
    field.replace_memory_fast("a", replacement)
    assert set(field.records) == {"a2", "b"}
    removed = field.remove_parent_fast("p1")
    assert set(field.records) == {"a2"}
    field.restore_parent_fast(removed)
    assert set(field.records) == {"a2", "b"}


def test_zero_field_has_fixed_shape_and_zero_read() -> None:
    field = ReversibleSemanticSlotField(
        layer_count=2, key_dim=3, slot_count=4, model_dim=5
    )
    assert torch.equal(field.read(torch.randn(3)), torch.zeros(2, 4, 5))


def test_selector_additive_decomposition_is_exact() -> None:
    torch.manual_seed(25101)
    state = torch.randn(2, 3)
    transition = torch.randn(4, 3)
    core = torch.randn(2, 4, 3)
    mean, std = 0.7, 1.3
    direct = torch.einsum("vr,vwr,wr->", state, core, transition)
    direct = direct / (2 * 4 * 3) ** 0.5
    standardized = (direct - mean) / std
    query = selector_seed_query(state)
    key = selector_seed_key(
        transition, core, train_std=std, ensemble_size=1
    )
    intercept = selector_ensemble_intercept([mean], [std])
    assert torch.allclose(standardized, torch.dot(query, key) + intercept, atol=1.0e-6)
