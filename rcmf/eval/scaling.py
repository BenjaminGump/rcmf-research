from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import torch

from rcmf.memory.compiler import HashingMemoryCompiler
from rcmf.memory.state import MemoryState
from rcmf.utils.serialization import write_jsonl


@dataclass
class ScalingRow:
    memory_count: int
    read_seconds: float
    rank: int
    program_dim: int


def measure_read_scaling(
    counts: list[int],
    rank: int,
    program_dim: int,
    output_csv_jsonl: str | Path | None = None,
) -> list[ScalingRow]:
    rows: list[ScalingRow] = []
    compiler = HashingMemoryCompiler(rank=rank, program_dim=program_dim)
    for count in counts:
        state = MemoryState(rank=rank, program_dim=program_dim)
        for idx in range(count):
            state.add(compiler.compile_text(f"m{idx}", f"memory {idx}"))
        address = torch.softmax(torch.randn(rank), dim=-1)
        import time

        start = time.perf_counter()
        state.read(address, normalization="mass")
        rows.append(
            ScalingRow(
                memory_count=count,
                read_seconds=time.perf_counter() - start,
                rank=rank,
                program_dim=program_dim,
            )
        )
    if output_csv_jsonl is not None:
        write_jsonl(output_csv_jsonl, (asdict(row) for row in rows))
    return rows

