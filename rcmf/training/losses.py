from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor

from rcmf.memory.normalization import address_entropy


def utility_mse(predicted: Tensor, target: Tensor, mask: Tensor | None = None) -> Tensor:
    loss = (predicted - target.to(predicted.device, predicted.dtype)).pow(2)
    if mask is not None:
        loss = loss * mask.to(loss.device, loss.dtype)
        return loss.sum() / mask.to(loss.dtype).sum().clamp_min(1.0)
    return loss.mean()


def pairwise_ranking_loss(
    positive_scores: Tensor,
    negative_scores: Tensor,
    margin: float = 0.1,
) -> Tensor:
    return F.relu(margin - positive_scores + negative_scores).mean()


def hard_negative_ranking_loss(scores: Tensor, labels: Tensor, margin: float = 0.1) -> Tensor:
    positives = scores[labels > 0]
    negatives = scores[labels <= 0]
    if positives.numel() == 0 or negatives.numel() == 0:
        return scores.new_tensor(0.0)
    return pairwise_ranking_loss(positives[:, None], negatives[None, :], margin=margin)


def sparse_address_loss(address: Tensor) -> Tensor:
    return address_entropy(address).mean()


def orthogonal_address_loss(address: Tensor) -> Tensor:
    if address.shape[0] < 2:
        return address.new_tensor(0.0)
    centered = address - address.mean(dim=0, keepdim=True)
    covariance = centered.T @ centered / max(1, address.shape[0] - 1)
    off_diag = covariance - torch.diag(torch.diag(covariance))
    return off_diag.pow(2).mean()


def interference_loss(reference_logits: Tensor, perturbed_logits: Tensor) -> Tensor:
    return F.mse_loss(perturbed_logits, reference_logits.detach())


def utility_scores(state_address: Tensor, memory_alpha: Tensor) -> Tensor:
    if state_address.shape[-1] != memory_alpha.shape[-1]:
        raise ValueError("state_address and memory_alpha rank mismatch")
    return state_address @ memory_alpha.T

