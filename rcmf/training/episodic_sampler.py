from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from rcmf.schemas import DecisionExample, MemoryRecord


@dataclass
class EpisodicBatch:
    support_records: list[MemoryRecord]
    query_examples: list[DecisionExample]

    @property
    def support_episode_ids(self) -> set[str]:
        return {record.episode_id for record in self.support_records}

    @property
    def query_episode_ids(self) -> set[str]:
        return {example.episode_id for example in self.query_examples}

    def assert_no_leakage(self) -> None:
        overlap = self.support_episode_ids.intersection(self.query_episode_ids)
        if overlap:
            raise ValueError(f"Support/query leakage detected for episodes: {sorted(overlap)}")


class EpisodicSampler:
    def __init__(
        self,
        memory_records: Iterable[MemoryRecord],
        decision_examples: Iterable[DecisionExample],
        support_size: int = 8,
        query_size: int = 4,
        seed: int = 1,
    ) -> None:
        if support_size <= 0 or query_size <= 0:
            raise ValueError("support_size and query_size must be positive")
        self.records = list(memory_records)
        self.examples = list(decision_examples)
        self.support_size = support_size
        self.query_size = query_size
        self.rng = random.Random(seed)
        self.records_by_episode: dict[str, list[MemoryRecord]] = {}
        self.examples_by_episode: dict[str, list[DecisionExample]] = {}
        for record in self.records:
            self.records_by_episode.setdefault(record.episode_id, []).append(record)
        for example in self.examples:
            self.examples_by_episode.setdefault(example.episode_id, []).append(example)
        if len(self.records_by_episode) < 2:
            raise ValueError("At least two support episodes are needed to sample no-leakage batches")

    def sample(self) -> EpisodicBatch:
        query_episode_ids = list(self.examples_by_episode)
        if not query_episode_ids:
            raise ValueError("No query episodes are available")
        query_episode = self.rng.choice(query_episode_ids)
        support_pool = [
            record for record in self.records if record.episode_id != query_episode
        ]
        if not support_pool:
            raise ValueError("No non-leaking support records are available")
        self.rng.shuffle(support_pool)
        support_records = support_pool[: self.support_size]
        query_examples = list(self.examples_by_episode[query_episode])
        self.rng.shuffle(query_examples)
        batch = EpisodicBatch(
            support_records=support_records,
            query_examples=query_examples[: self.query_size],
        )
        batch.assert_no_leakage()
        return batch

    @staticmethod
    def check_no_leakage(
        support_episode_ids: Iterable[str],
        query_episode_ids: Iterable[str],
    ) -> None:
        overlap = set(support_episode_ids).intersection(query_episode_ids)
        if overlap:
            raise ValueError(f"Support/query leakage detected for episodes: {sorted(overlap)}")
