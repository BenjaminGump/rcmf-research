from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Iterable, Mapping, Sequence

import torch
from torch import Tensor, nn
import torch.nn.functional as F


COMPACT_RCMF_VERSION = "alfworld_compact_additive_field_v1"
TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[^\w\s]", flags=re.IGNORECASE)


def signed_hash_features(text: str, dimension: int = 256) -> Tensor:
    """Stable CPU text features with no vocabulary fitting or evaluation access."""

    if dimension <= 0:
        raise ValueError("feature dimension must be positive")
    vector = torch.zeros(dimension, dtype=torch.float32)
    tokens = TOKEN_PATTERN.findall(str(text).lower())
    if not tokens:
        tokens = ["<empty>"]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimension
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign
    return F.normalize(vector, dim=0)


def batch_hash_features(texts: Sequence[str], dimension: int = 256) -> Tensor:
    return torch.stack([signed_hash_features(text, dimension) for text in texts], dim=0)


class IndependentTransitionWriter(nn.Module):
    """Compile each complete transition independently into a rank-one contribution."""

    def __init__(
        self,
        *,
        feature_dim: int = 256,
        key_dim: int = 256,
        program_dim: int = 128,
        hidden_dim: int = 512,
    ) -> None:
        super().__init__()
        self.feature_dim = int(feature_dim)
        self.key_dim = int(key_dim)
        self.program_dim = int(program_dim)
        self.backbone = nn.Sequential(
            nn.Linear(self.feature_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
        )
        self.key_head = nn.Linear(hidden_dim, self.key_dim)
        self.value_head = nn.Linear(hidden_dim, self.program_dim)
        self.mu_head = nn.Linear(hidden_dim, 1)

    def forward(self, features: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        hidden = self.backbone(features.to(torch.float32))
        key = F.normalize(self.key_head(hidden), dim=-1)
        value = torch.tanh(self.value_head(hidden))
        mu = torch.sigmoid(self.mu_head(hidden)).squeeze(-1)
        return key, value, mu


class StateQueryEncoder(nn.Module):
    def __init__(self, *, feature_dim: int = 256, key_dim: int = 256) -> None:
        super().__init__()
        self.projection = nn.Linear(feature_dim, key_dim, bias=False)
        if feature_dim == key_dim:
            nn.init.eye_(self.projection.weight)

    def forward(self, features: Tensor) -> Tensor:
        return F.normalize(self.projection(features.to(torch.float32)), dim=-1)


class FixedFieldReader(nn.Module):
    def __init__(self, program_dim: int = 128) -> None:
        super().__init__()
        self.program_dim = int(program_dim)
        self.net = nn.Sequential(
            nn.LayerNorm(self.program_dim),
            nn.Linear(self.program_dim, self.program_dim * 2),
            nn.GELU(),
            nn.Linear(self.program_dim * 2, self.program_dim),
        )

    def forward(self, value: Tensor) -> Tensor:
        return self.net(value.to(torch.float32))


@dataclass(frozen=True)
class CompactMemoryContribution:
    memory_id: str
    parent_id: str
    key: Tensor
    value: Tensor
    mu: float
    rho: float


class ReversibleCompactField:
    """Fixed-dimensional whole-bank state for rank-one independent writes."""

    def __init__(self, *, key_dim: int = 256, program_dim: int = 128) -> None:
        self.key_dim = int(key_dim)
        self.program_dim = int(program_dim)
        # Float64 accumulation makes order effects bounded and reversible audits tight.
        self.A = torch.zeros(self.key_dim, self.program_dim, dtype=torch.float64)
        self.B = torch.zeros(self.program_dim, dtype=torch.float64)
        self.records: dict[str, CompactMemoryContribution] = {}

    @property
    def field_shape(self) -> Mapping[str, tuple[int, ...]]:
        return {"A": tuple(self.A.shape), "B": tuple(self.B.shape)}

    def _validate(self, record: CompactMemoryContribution) -> None:
        if tuple(record.key.shape) != (self.key_dim,):
            raise ValueError("compact contribution key shape differs")
        if tuple(record.value.shape) != (self.program_dim,):
            raise ValueError("compact contribution value shape differs")
        if not math.isfinite(record.mu) or not math.isfinite(record.rho) or record.rho <= 0:
            raise ValueError("compact contribution coefficients are invalid")
        if not torch.isfinite(record.key).all() or not torch.isfinite(record.value).all():
            raise ValueError("compact contribution contains non-finite values")

    def _apply(self, record: CompactMemoryContribution, sign: float) -> None:
        key = record.key.detach().cpu().to(torch.float64)
        value = record.value.detach().cpu().to(torch.float64)
        coefficient = float(sign) * float(record.rho)
        self.A.add_(torch.outer(key, value), alpha=coefficient)
        self.B.add_(value, alpha=coefficient * float(record.mu))

    def add(self, record: CompactMemoryContribution) -> None:
        self._validate(record)
        if record.memory_id in self.records:
            raise ValueError(f"duplicate compact memory ID: {record.memory_id}")
        self._apply(record, 1.0)
        self.records[record.memory_id] = record

    def remove(self, memory_id: str) -> CompactMemoryContribution:
        record = self.records.pop(memory_id)
        self._apply(record, -1.0)
        return record

    def restore(self, record: CompactMemoryContribution) -> None:
        self.add(record)

    def read(self, query: Tensor) -> Tensor:
        query = query.to(torch.float64).cpu()
        if query.ndim == 1:
            raw = self.B + query @ self.A
        elif query.ndim == 2:
            raw = self.B.unsqueeze(0) + query @ self.A
        else:
            raise ValueError("compact field query rank differs")
        return F.normalize(raw.to(torch.float32), dim=-1)

    def rebuild(self, order: Iterable[str] | None = None) -> tuple[Tensor, Tensor]:
        rebuilt = ReversibleCompactField(key_dim=self.key_dim, program_dim=self.program_dim)
        for memory_id in order or sorted(self.records):
            rebuilt.add(self.records[memory_id])
        return rebuilt.A, rebuilt.B


def compile_contributions(
    writer: IndependentTransitionWriter,
    rows: Sequence[Mapping[str, str]],
    *,
    feature_dim: int,
    batch_size: int,
    device: torch.device,
) -> list[CompactMemoryContribution]:
    writer.eval()
    parent_sizes: dict[str, int] = {}
    for row in rows:
        parent = str(row["parent_id"])
        parent_sizes[parent] = parent_sizes.get(parent, 0) + 1
    result: list[CompactMemoryContribution] = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            features = batch_hash_features(
                [str(row["transition_text"]) for row in batch],
                feature_dim,
            ).to(device)
            keys, values, means = writer(features)
            for index, row in enumerate(batch):
                parent = str(row["parent_id"])
                result.append(
                    CompactMemoryContribution(
                        memory_id=str(row["memory_id"]),
                        parent_id=parent,
                        key=keys[index].detach().cpu(),
                        value=values[index].detach().cpu(),
                        mu=float(means[index].detach().cpu()),
                        rho=1.0 / parent_sizes[parent],
                    )
                )
    return result
