from __future__ import annotations

import torch

from rcmf.memory.ledger import MemoryLedger
from rcmf.memory.state import MemoryState, memory_delta_from_components
from rcmf.schemas import MemoryRecord


def _record(memory_id: str, episode_id: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="appworld",
        episode_id=episode_id,
        task_id=episode_id,
        raw_trajectory={"steps": []},
        experience_text=f"experience {episode_id}",
        outcome=1.0,
        success=True,
        metadata={},
    )


def _delta(memory_id: str) -> object:
    return memory_delta_from_components(
        memory_id,
        torch.softmax(torch.randn(8), -1),
        torch.randn(4),
        0.5,
    )


def test_ledger_rebuild_matches_online_state(tmp_path) -> None:
    ledger = MemoryLedger(tmp_path / "ledger")
    state = MemoryState(rank=8, program_dim=4)
    d1 = _delta("m1")
    d2 = _delta("m2")
    d3 = _delta("m3")
    ledger.add_record(_record("m1", "e1"), d1, state=state)
    ledger.add_record(_record("m2", "e2"), d2, state=state)
    ledger.delete("m1", state=state)
    ledger.replace("m2", _record("m3", "e3"), d3, state=state)
    rebuilt = ledger.rebuild_state(rank=8, program_dim=4)
    assert torch.allclose(rebuilt.V, state.V)
    assert torch.allclose(rebuilt.c, state.c)
    active = ledger.active_events()
    assert set(active) == {"m3"}

