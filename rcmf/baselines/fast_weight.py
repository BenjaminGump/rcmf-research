from __future__ import annotations

import torch

from rcmf.memory.compiler import HashingMemoryCompiler
from rcmf.memory.normalization import normalize_address, rms_normalize
from rcmf.memory.state import MemoryState
from rcmf.schemas import MemoryRecord


class SimpleFastWeightMemory:
    """Delta-rule associative baseline with the same R/P surface as RCMF."""

    def __init__(self, rank: int, program_dim: int, topk: int = 8) -> None:
        self.rank = rank
        self.program_dim = program_dim
        self.compiler = HashingMemoryCompiler(rank=rank, program_dim=program_dim, topk=topk)
        self.state = MemoryState(rank=rank, program_dim=program_dim)

    def fit(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self.state.add(self.compiler.compile_text(record.memory_id, record.experience_text))

    def read(self, state_text: str) -> torch.Tensor:
        seed = abs(hash(state_text)) % (2**31)
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed)
        logits = torch.randn(self.rank, generator=generator)
        address = normalize_address(logits, mode="topk_softmax", topk=min(8, self.rank))
        return rms_normalize(self.state.read(address, normalization="none"))

