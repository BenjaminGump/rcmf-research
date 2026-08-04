from __future__ import annotations

import random

import pytest

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.episodic_sampler import EpisodicBatch, EpisodicSampler
from scripts.train import _support_indices_for_examples


def _record(memory_id: str, episode_id: str, metadata: dict | None = None) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="b",
        episode_id=episode_id,
        task_id=episode_id,
        raw_trajectory={},
        experience_text="x",
        outcome=1.0,
        success=True,
        metadata=metadata or {},
    )


def _example(episode_id: str, metadata: dict | None = None) -> DecisionExample:
    return DecisionExample(
        benchmark="b",
        episode_id=episode_id,
        step_id=0,
        state_text="s",
        target_text="a",
        target_type="code",
        candidate_memory_ids=None,
        metadata=metadata or {},
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


def test_train_support_indices_exclude_task_episode_and_lineage() -> None:
    records = [
        _record("same-task", "other-episode", {"task_id": "task-a"}),
        _record("same-episode", "episode-a", {"task_id": "task-b"}),
        _record("same-lineage", "episode-b", {"lineage_id": "lineage-a"}),
        _record("legal", "episode-c", {"task_id": "task-c", "lineage_id": "lineage-c"}),
    ]
    examples = [
        _example(
            "episode-a",
            {"task_id": "task-a", "lineage_id": "lineage-a"},
        )
    ]

    chosen = _support_indices_for_examples(
        records,
        examples,
        mode="all_except_current_task",
        support_size=4,
        rng=random.Random(1),
    )

    assert chosen == [3]
