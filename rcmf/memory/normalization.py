from __future__ import annotations

import torch
from torch import Tensor
import torch.nn.functional as F


def dense_softmax(logits: Tensor, dim: int = -1) -> Tensor:
    return F.softmax(logits, dim=dim)


def topk_softmax(logits: Tensor, k: int, dim: int = -1) -> Tensor:
    if k <= 0:
        raise ValueError("topk must be positive")
    size = logits.shape[dim]
    if k >= size:
        return dense_softmax(logits, dim=dim)
    values, indices = torch.topk(logits, k=k, dim=dim)
    masked = torch.full_like(logits, torch.finfo(logits.dtype).min)
    masked.scatter_(dim, indices, values)
    return F.softmax(masked, dim=dim)


def sparsemax(logits: Tensor, dim: int = -1) -> Tensor:
    shifted = logits - logits.max(dim=dim, keepdim=True).values
    zs = torch.sort(shifted, descending=True, dim=dim).values
    range_shape = [1] * logits.dim()
    range_shape[dim] = logits.shape[dim]
    k = torch.arange(1, logits.shape[dim] + 1, device=logits.device, dtype=logits.dtype)
    k = k.view(range_shape)
    cumsum = zs.cumsum(dim)
    support = 1 + k * zs > cumsum
    k_z = support.sum(dim=dim, keepdim=True).clamp_min(1)
    tau = (cumsum.gather(dim, k_z - 1) - 1) / k_z.to(logits.dtype)
    return torch.clamp(shifted - tau, min=0.0)


def random_address(batch_shape: tuple[int, ...], rank: int, device: torch.device, seed: int) -> Tensor:
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    logits = torch.randn(*batch_shape, rank, generator=generator, device=device)
    return F.softmax(logits, dim=-1)


def normalize_address(
    logits: Tensor,
    mode: str = "topk_softmax",
    topk: int = 8,
    seed: int = 0,
) -> Tensor:
    if logits.dim() == 0:
        raise ValueError("address logits must have at least one dimension")
    logits = torch.nan_to_num(logits, nan=0.0, posinf=1.0e4, neginf=-1.0e4)
    if mode == "dense_softmax":
        return dense_softmax(logits)
    if mode == "topk_softmax":
        return topk_softmax(logits, k=topk)
    if mode in {"sparsemax", "entmax"}:
        return sparsemax(logits)
    if mode == "random":
        return random_address(logits.shape[:-1], logits.shape[-1], logits.device, seed=seed)
    if mode == "semantic_cosine":
        return dense_softmax(logits)
    raise ValueError(f"Unknown address normalization mode: {mode}")


def rms_normalize(value: Tensor, eps: float = 1.0e-6) -> Tensor:
    return value / torch.sqrt(value.pow(2).mean(dim=-1, keepdim=True) + eps)


def address_entropy(address: Tensor, eps: float = 1.0e-8) -> Tensor:
    probs = address.clamp_min(eps)
    return -(probs * probs.log()).sum(dim=-1)
