from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file, save_file
from torch import Tensor


@dataclass
class MemoryDelta:
    memory_id: str
    delta_v: Tensor
    delta_c: Tensor
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.delta_v.dim() != 2:
            raise ValueError("delta_v must have shape [rank, program_dim]")
        if self.delta_c.dim() != 1:
            raise ValueError("delta_c must have shape [rank]")
        if self.delta_v.shape[0] != self.delta_c.shape[0]:
            raise ValueError("delta_v and delta_c rank mismatch")
        self.delta_v = self.delta_v.detach().to(dtype=torch.float32)
        self.delta_c = self.delta_c.detach().to(dtype=torch.float32)

    @property
    def rank(self) -> int:
        return int(self.delta_c.shape[0])

    @property
    def program_dim(self) -> int:
        return int(self.delta_v.shape[1])

    def to(self, device: torch.device | str) -> "MemoryDelta":
        return MemoryDelta(
            memory_id=self.memory_id,
            delta_v=self.delta_v.to(device=device, dtype=torch.float32),
            delta_c=self.delta_c.to(device=device, dtype=torch.float32),
            metadata=dict(self.metadata),
        )


class MemoryState:
    """Single-bank FP32 RCMF state."""

    def __init__(
        self,
        rank: int,
        program_dim: int,
        device: torch.device | str | None = None,
        v: Tensor | None = None,
        c: Tensor | None = None,
    ) -> None:
        if rank <= 0 or program_dim <= 0:
            raise ValueError("rank and program_dim must be positive")
        self.rank = int(rank)
        self.program_dim = int(program_dim)
        self.device = torch.device(device or "cpu")
        self.V = (
            v.detach().to(device=self.device, dtype=torch.float32).clone()
            if v is not None
            else torch.zeros(self.rank, self.program_dim, device=self.device, dtype=torch.float32)
        )
        self.c = (
            c.detach().to(device=self.device, dtype=torch.float32).clone()
            if c is not None
            else torch.zeros(self.rank, device=self.device, dtype=torch.float32)
        )
        self._assert_state_shape()

    def _assert_state_shape(self) -> None:
        assert self.V.shape == (self.rank, self.program_dim), (
            self.V.shape,
            self.rank,
            self.program_dim,
        )
        assert self.c.shape == (self.rank,), (self.c.shape, self.rank)
        assert self.V.dtype == torch.float32
        assert self.c.dtype == torch.float32

    def clone(self) -> "MemoryState":
        return MemoryState(self.rank, self.program_dim, self.device, self.V, self.c)

    def _coerce_delta(self, delta: MemoryDelta) -> MemoryDelta:
        if delta.rank != self.rank or delta.program_dim != self.program_dim:
            raise ValueError(
                f"Delta shape {(delta.rank, delta.program_dim)} does not match "
                f"state {(self.rank, self.program_dim)}"
            )
        return delta.to(self.device)

    def add(self, delta: MemoryDelta) -> None:
        delta = self._coerce_delta(delta)
        self.V.add_(delta.delta_v)
        self.c.add_(delta.delta_c)
        self._assert_state_shape()

    def remove(self, delta: MemoryDelta) -> None:
        delta = self._coerce_delta(delta)
        self.V.sub_(delta.delta_v)
        self.c.sub_(delta.delta_c)
        self._assert_state_shape()

    def replace(self, old_delta: MemoryDelta, new_delta: MemoryDelta) -> None:
        self.remove(old_delta)
        self.add(new_delta)

    def read(self, address: Tensor, normalization: str = "mass", eps: float = 1.0e-6) -> Tensor:
        b = torch.nan_to_num(
            address.to(device=self.device, dtype=torch.float32),
            nan=0.0,
            posinf=1.0e4,
            neginf=-1.0e4,
        )
        if b.shape[-1] != self.rank:
            raise ValueError(f"Address last dimension must be {self.rank}, got {b.shape[-1]}")
        m = b @ self.V
        d = b @ self.c
        if normalization == "none":
            return m
        if normalization == "mass":
            scale = (1.0 - torch.exp(-d)) / (d + eps)
            return m * scale.unsqueeze(-1) if scale.dim() > 0 else m * scale
        if normalization == "sqrt_count":
            denom = torch.sqrt(d.clamp_min(0.0) + eps)
            return m / (denom.unsqueeze(-1) if denom.dim() > 0 else denom)
        if normalization == "global_norm":
            norm = self.c.sum().clamp_min(eps)
            return m / norm
        raise ValueError(f"Unknown memory normalization: {normalization}")

    def snapshot(self, path: str | Path, metadata: dict[str, str] | None = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        meta = {
            "rank": str(self.rank),
            "program_dim": str(self.program_dim),
        }
        if metadata:
            meta.update({str(key): str(value) for key, value in metadata.items()})
        save_file(
            {"V": self.V.detach().cpu(), "c": self.c.detach().cpu()},
            tmp_path,
            metadata=meta,
        )
        tmp_path.replace(path)

    @classmethod
    def load(cls, path: str | Path, device: torch.device | str | None = None) -> "MemoryState":
        tensors = load_file(Path(path))
        if "V" not in tensors or "c" not in tensors:
            raise ValueError(f"Memory snapshot is missing V or c: {path}")
        v = tensors["V"].to(dtype=torch.float32)
        c = tensors["c"].to(dtype=torch.float32)
        return cls(rank=int(c.shape[0]), program_dim=int(v.shape[1]), device=device, v=v, c=c)


def memory_delta_from_components(
    memory_id: str,
    alpha: Tensor,
    program: Tensor,
    rho: Tensor | float,
    metadata: dict[str, Any] | None = None,
) -> MemoryDelta:
    if alpha.dim() != 1:
        raise ValueError("alpha must have shape [rank]")
    if program.dim() != 1:
        raise ValueError("program must have shape [program_dim]")
    rho_tensor = torch.as_tensor(rho, dtype=torch.float32, device=alpha.device).reshape(())
    delta_v = rho_tensor * torch.outer(alpha.to(torch.float32), program.to(torch.float32))
    delta_c = rho_tensor * alpha.to(torch.float32)
    return MemoryDelta(memory_id=memory_id, delta_v=delta_v, delta_c=delta_c, metadata=metadata or {})


def compile_deltas_to_tensors(deltas: list[MemoryDelta]) -> tuple[Tensor, Tensor]:
    if not deltas:
        raise ValueError("Cannot infer memory shape from an empty delta list")
    rank, program_dim = deltas[0].rank, deltas[0].program_dim
    v = torch.zeros(rank, program_dim, dtype=torch.float32, device=deltas[0].delta_v.device)
    c = torch.zeros(rank, dtype=torch.float32, device=deltas[0].delta_c.device)
    for delta in deltas:
        if delta.rank != rank or delta.program_dim != program_dim:
            raise ValueError("All deltas must have the same shape")
        v = v + delta.delta_v.to(dtype=torch.float32)
        c = c + delta.delta_c.to(dtype=torch.float32)
    return v, c


def read_memory_tensors(
    v: Tensor,
    c: Tensor,
    address: Tensor,
    normalization: str = "mass",
    eps: float = 1.0e-6,
) -> Tensor:
    if v.dim() != 2 or c.dim() != 1:
        raise ValueError("v must be [rank, program_dim] and c must be [rank]")
    if address.shape[-1] != c.shape[0]:
        raise ValueError("address rank mismatch")
    v = v.to(dtype=torch.float32)
    c = c.to(dtype=torch.float32)
    b = torch.nan_to_num(
        address.to(dtype=torch.float32, device=v.device),
        nan=0.0,
        posinf=1.0e4,
        neginf=-1.0e4,
    )
    m = b @ v
    d = b @ c.to(device=v.device)
    if normalization == "none":
        return m
    if normalization == "mass":
        scale = (1.0 - torch.exp(-d)) / (d + eps)
        return m * scale.unsqueeze(-1)
    if normalization == "sqrt_count":
        return m / torch.sqrt(d.clamp_min(0.0).unsqueeze(-1) + eps)
    if normalization == "global_norm":
        return m / c.sum().clamp_min(eps)
    raise ValueError(f"Unknown memory normalization: {normalization}")
