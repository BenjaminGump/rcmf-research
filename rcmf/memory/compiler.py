from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.config import AddressSection, EncoderSection, MemorySection
from rcmf.memory.normalization import normalize_address, rms_normalize
from rcmf.memory.state import MemoryDelta, memory_delta_from_components


@dataclass
class CompiledMemoryBatch:
    alpha: Tensor
    program: Tensor
    rho: Tensor
    delta_v: Tensor
    delta_c: Tensor


class LightweightTextEncoder(nn.Module):
    """Small encoder used for both experience and state text token IDs."""

    def __init__(
        self,
        vocab_size: int,
        hidden_size: int = 512,
        num_layers: int = 2,
        num_heads: int = 8,
        intermediate_size: int = 2048,
        dropout: float = 0.1,
        token_embedding: nn.Embedding | None = None,
        train_token_embedding: bool = False,
    ) -> None:
        super().__init__()
        if token_embedding is None:
            self.token_embedding = nn.Embedding(vocab_size, hidden_size)
        else:
            self.token_embedding = token_embedding
            if token_embedding.embedding_dim != hidden_size:
                self.input_projection = nn.Linear(token_embedding.embedding_dim, hidden_size)
            else:
                self.input_projection = nn.Identity()
        self.token_embedding.weight.requires_grad_(train_token_embedding)
        layer = nn.TransformerEncoderLayer(
            d_model=hidden_size,
            nhead=num_heads,
            dim_feedforward=intermediate_size,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.final_norm = nn.LayerNorm(hidden_size)

    def forward(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> Tensor:
        if input_ids.dim() != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        x = self.token_embedding(input_ids)
        if hasattr(self, "input_projection"):
            if isinstance(self.input_projection, nn.Linear):
                x = x.to(dtype=self.input_projection.weight.dtype)
            x = self.input_projection(x)
        padding_mask = None
        if attention_mask is not None:
            if attention_mask.shape != input_ids.shape:
                raise ValueError("attention_mask shape must match input_ids")
            padding_mask = attention_mask == 0
        encoded = self.encoder(x, src_key_padding_mask=padding_mask)
        encoded = self.final_norm(encoded)
        if attention_mask is None:
            return encoded[:, -1, :]
        lengths = attention_mask.to(torch.long).sum(dim=1).clamp_min(1) - 1
        return encoded[torch.arange(encoded.shape[0], device=encoded.device), lengths]


class ExperienceCompiler(nn.Module):
    def __init__(
        self,
        encoder: nn.Module,
        hidden_size: int,
        memory: MemorySection,
        address: AddressSection,
        use_write_strength: bool = True,
    ) -> None:
        super().__init__()
        self.encoder = encoder
        self.rank = memory.rank
        self.program_dim = memory.program_dim
        self.address_mode = address.mode
        self.address_topk = address.topk
        self.use_write_strength = use_write_strength
        self.alpha_head = nn.Linear(hidden_size, self.rank)
        self.program_head = nn.Linear(hidden_size, self.program_dim)
        self.rho_head = nn.Linear(hidden_size, 1)
        nn.init.constant_(self.rho_head.bias, -2.1972246)

    def forward(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> CompiledMemoryBatch:
        h = self.encoder(input_ids, attention_mask)
        alpha_logits = self.alpha_head(h)
        alpha = normalize_address(alpha_logits, mode=self.address_mode, topk=self.address_topk)
        program = rms_normalize(torch.tanh(self.program_head(h)))
        if self.use_write_strength:
            rho = torch.sigmoid(self.rho_head(h)).squeeze(-1)
        else:
            rho = torch.ones(h.shape[0], device=h.device, dtype=h.dtype)
        delta_v = rho[:, None, None] * alpha[:, :, None] * program[:, None, :]
        delta_c = rho[:, None] * alpha
        assert delta_v.shape == (input_ids.shape[0], self.rank, self.program_dim)
        assert delta_c.shape == (input_ids.shape[0], self.rank)
        return CompiledMemoryBatch(
            alpha=alpha,
            program=program,
            rho=rho,
            delta_v=delta_v.to(torch.float32),
            delta_c=delta_c.to(torch.float32),
        )

    def compile_one(
        self,
        memory_id: str,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryDelta:
        if input_ids.dim() == 1:
            input_ids = input_ids.unsqueeze(0)
        if attention_mask is not None and attention_mask.dim() == 1:
            attention_mask = attention_mask.unsqueeze(0)
        batch = self.forward(input_ids, attention_mask)
        return MemoryDelta(
            memory_id=memory_id,
            delta_v=batch.delta_v[0],
            delta_c=batch.delta_c[0],
            metadata=metadata or {},
        )


class StateEncoder(nn.Module):
    def __init__(
        self,
        encoder: nn.Module,
        hidden_size: int,
        memory: MemorySection,
        address: AddressSection,
    ) -> None:
        super().__init__()
        self.encoder = encoder
        self.rank = memory.rank
        self.address_mode = address.mode
        self.address_topk = address.topk
        self.address_head = nn.Linear(hidden_size, self.rank)

    def forward(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> Tensor:
        h = self.encoder(input_ids, attention_mask)
        logits = self.address_head(h)
        address = normalize_address(logits, mode=self.address_mode, topk=self.address_topk)
        assert address.shape == (input_ids.shape[0], self.rank)
        return address


class HashingMemoryCompiler:
    """Deterministic smoke compiler, not a trained RCMF model."""

    def __init__(self, rank: int, program_dim: int, topk: int = 8) -> None:
        self.rank = rank
        self.program_dim = program_dim
        self.topk = min(topk, rank)

    def compile_text(self, memory_id: str, text: str) -> MemoryDelta:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed)
        logits = torch.randn(self.rank, generator=generator)
        alpha = normalize_address(logits, mode="topk_softmax", topk=self.topk)
        program = rms_normalize(torch.randn(self.program_dim, generator=generator).tanh())
        rho = torch.tensor(0.1 + 0.9 * (digest[8] / 255.0), dtype=torch.float32)
        return memory_delta_from_components(
            memory_id=memory_id,
            alpha=alpha,
            program=program,
            rho=rho,
            metadata={"compiler": "hashing_smoke"},
        )


def build_lightweight_encoder(
    vocab_size: int,
    encoder_cfg: EncoderSection,
    token_embedding: nn.Embedding | None = None,
) -> LightweightTextEncoder:
    return LightweightTextEncoder(
        vocab_size=vocab_size,
        hidden_size=encoder_cfg.hidden_size,
        num_layers=encoder_cfg.num_layers,
        num_heads=encoder_cfg.num_heads,
        intermediate_size=encoder_cfg.intermediate_size,
        dropout=encoder_cfg.dropout,
        token_embedding=token_embedding,
        train_token_embedding=encoder_cfg.train_token_embedding,
    )
