from __future__ import annotations

import torch

from rcmf.memory.state import MemoryState, memory_delta_from_components


def test_add_remove_recover_fp32() -> None:
    state = MemoryState(rank=8, program_dim=4)
    alpha = torch.softmax(torch.randn(8), dim=-1)
    program = torch.tanh(torch.randn(4))
    delta = memory_delta_from_components("m1", alpha, program, 0.7)
    before_v = state.V.clone()
    before_c = state.c.clone()
    state.add(delta)
    state.remove(delta)
    assert state.V.dtype == torch.float32
    assert state.c.dtype == torch.float32
    assert torch.max(torch.abs(state.V - before_v)).item() < 1e-6
    assert torch.max(torch.abs(state.c - before_c)).item() < 1e-6


def test_replace_and_read_shapes() -> None:
    state = MemoryState(rank=8, program_dim=4)
    old = memory_delta_from_components("old", torch.softmax(torch.randn(8), -1), torch.randn(4), 0.5)
    new = memory_delta_from_components("new", torch.softmax(torch.randn(8), -1), torch.randn(4), 0.3)
    state.add(old)
    state.replace(old, new)
    address = torch.softmax(torch.randn(3, 8), dim=-1)
    z_mass = state.read(address, normalization="mass")
    z_raw = state.read(address, normalization="none")
    z_sqrt = state.read(address, normalization="sqrt_count")
    assert z_mass.shape == (3, 4)
    assert z_raw.shape == (3, 4)
    assert z_sqrt.shape == (3, 4)


def test_snapshot_round_trip(tmp_path) -> None:
    state = MemoryState(rank=4, program_dim=3)
    delta = memory_delta_from_components("m", torch.softmax(torch.randn(4), -1), torch.randn(3), 0.9)
    state.add(delta)
    path = tmp_path / "memory.safetensors"
    state.snapshot(path)
    loaded = MemoryState.load(path)
    assert torch.allclose(loaded.V, state.V)
    assert torch.allclose(loaded.c, state.c)

