from __future__ import annotations

from rcmf.training.cross_attention_memory_sampling_8b import (
    capped_memory_token_indices,
    slot_indices,
)


def test_capped_sampling_spans_full_memory_without_query_truncation() -> None:
    indices = capped_memory_token_indices(1000, 256)
    assert len(indices) == 256
    assert indices[0] == 0
    assert indices[-1] == 999
    assert indices == sorted(indices)
    assert capped_memory_token_indices(7, 256) == list(range(7))


def test_sixteen_slots_span_capped_memory() -> None:
    indices = slot_indices(256, 16)
    assert len(indices) == 16
    assert indices[0] == 0
    assert indices[-1] == 255
