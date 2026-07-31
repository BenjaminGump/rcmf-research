from __future__ import annotations

import pytest

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.episodic_sampler import EpisodicBatch, EpisodicSampler


def _record(memory_id: str, episode_id: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="b",
        episode_id=episode_id,
        task_id=episode_id,
        raw_trajectory={},
        experience_text="x",
        outcome=1.0,
        success=True,
    )


def _example(episode_id: str) -> DecisionExample:
    return DecisionExample(
        benchmark="b",
        episode_id=episode_id,
        step_id=0,
        state_text="s",
        target_text="a",
        target_type="code",
        candidate_memory_ids=None,
    )


def test_batch_rejects_overlap() -> None:
    batch = EpisodicBatch([_record("m1", "e1")], [_example("e1")])
    with pytest.raises(ValueError):
        batch.assert_no_leakage()


def test_sampler_produces_disjoint_batches() -> None:
    sampler = EpisodicSampler([_record("m1", "e1"), _record("m2", "e2")], [_example("e1")])
    batch = sampler.sample()
    batch.assert_no_leakage()
    assert batch.support_episode_ids.isdisjoint(batch.query_episode_ids)

