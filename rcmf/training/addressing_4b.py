from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import copy
import math
import random
from typing import Any, Iterable

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import RCMFConfig
from rcmf.memory.normalization import normalize_address
from rcmf.training.addressing_only import (
    AddressingLossWeights,
    AddressingOnlyModel,
    addressing_losses,
    evaluate_scores,
    rows_to_tensors,
    task_balanced_batches,
)


STAGE_B_4B_VERSION = "stage_b_addressing_diagnostics_4b_v1"


def mean_std(values: Iterable[float]) -> dict[str, float | int | None]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {"count": 0, "mean": None, "std": None}
    mean = sum(numbers) / len(numbers)
    var = sum((value - mean) ** 2 for value in numbers) / len(numbers)
    return {"count": len(numbers), "mean": mean, "std": math.sqrt(var)}


def distribution(values: Iterable[float], *, digits: int | None = None) -> dict[str, float | int | None]:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return {"count": 0}

    def pct(frac: float) -> float:
        index = min(len(numbers) - 1, max(0, int(round((len(numbers) - 1) * frac))))
        value = numbers[index]
        return round(value, digits) if digits is not None else value

    mean = sum(numbers) / len(numbers)
    var = sum((value - mean) ** 2 for value in numbers) / len(numbers)
    if digits is not None:
        mean = round(mean, digits)
        std = round(math.sqrt(var), digits)
    else:
        std = math.sqrt(var)
    return {
        "count": len(numbers),
        "min": pct(0.0),
        "p01": pct(0.01),
        "p05": pct(0.05),
        "p25": pct(0.25),
        "p50": pct(0.50),
        "p75": pct(0.75),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": pct(1.0),
        "mean": mean,
        "std": std,
    }


def effective_rank_from_singular_values(singular_values: Tensor, eps: float = 1.0e-12) -> float:
    values = singular_values.detach().to(torch.float64).clamp_min(0)
    total = values.sum()
    if float(total.item()) <= eps:
        return 0.0
    probs = values / total
    entropy = -(probs * (probs + eps).log()).sum()
    return float(entropy.exp().item())


def singular_summary(matrix: Tensor) -> dict[str, Any]:
    x = matrix.detach().to(torch.float32).cpu()
    if x.numel() == 0:
        return {"shape": list(x.shape), "effective_rank": 0.0, "singular_values": {"count": 0}}
    singular_values = torch.linalg.svdvals(x)
    return {
        "shape": list(x.shape),
        "effective_rank": effective_rank_from_singular_values(singular_values),
        "singular_values": distribution(singular_values.tolist()),
        "top_20_singular_values": [float(v) for v in singular_values[:20].tolist()],
    }


def pairwise_cosine_summary(matrix: Tensor) -> dict[str, Any]:
    x = matrix.detach().to(torch.float32).cpu()
    if x.shape[0] < 2:
        return {"count": 0}
    normalized = F.normalize(x, dim=-1)
    cosine = normalized @ normalized.T
    off_diag = cosine[~torch.eye(cosine.shape[0], dtype=torch.bool)]
    return distribution(off_diag.tolist())


def basis_usage(values: Tensor) -> dict[str, Any]:
    x = values.detach().to(torch.float32).cpu()
    if x.numel() == 0:
        return {"usage_count": 0, "max_load_fraction": 0.0, "entropy": 0.0}
    top1 = x.argmax(dim=-1).tolist()
    counts = Counter(int(item) for item in top1)
    total = sum(counts.values())
    probs = [count / total for count in counts.values()] if total else []
    entropy = -sum(p * math.log(p + 1.0e-12) for p in probs)
    normalized_entropy = entropy / math.log(x.shape[-1]) if x.shape[-1] > 1 else 0.0
    return {
        "usage_count": len(counts),
        "max_load_fraction": max(counts.values()) / total if total else 0.0,
        "entropy": entropy,
        "normalized_entropy": normalized_entropy,
        "counts_top20": dict(counts.most_common(20)),
    }


def _topk_support(matrix: Tensor, k: int) -> Tensor:
    if matrix.shape[-1] <= 0:
        raise ValueError("matrix must have a positive last dimension")
    k = min(k, matrix.shape[-1])
    return torch.topk(matrix.detach().to(torch.float32), k=k, dim=-1).indices.cpu()


def support_overlap_diagnostics(
    state_address: Tensor,
    alpha: Tensor,
    *,
    topk: int,
    zero_eps: float = 1.0e-12,
) -> dict[str, Any]:
    states = state_address.detach().to(torch.float32).cpu()
    memories = alpha.detach().to(torch.float32).cpu()
    state_support = _topk_support(states, topk)
    memory_support = _topk_support(memories, topk)
    state_sets = [set(int(item) for item in row.tolist()) for row in state_support]
    memory_sets = [set(int(item) for item in row.tolist()) for row in memory_support]
    intersections: list[int] = []
    top1_equal = 0
    nonzero_overlap_per_state = []
    for state_index, state_set in enumerate(state_sets):
        count = 0
        state_top = int(state_support[state_index, 0].item())
        for memory_index, memory_set in enumerate(memory_sets):
            inter = len(state_set.intersection(memory_set))
            intersections.append(inter)
            if inter > 0:
                count += 1
            if state_top == int(memory_support[memory_index, 0].item()):
                top1_equal += 1
        nonzero_overlap_per_state.append(count)
    raw_dot = states @ memories.T
    pair_count = raw_dot.numel()
    zero_dot = (raw_dot.abs() <= zero_eps).sum().item()
    return {
        "topk": topk,
        "state_top1_indices": [int(item) for item in state_support[:, 0].tolist()],
        "memory_top1_indices": [int(item) for item in memory_support[:, 0].tolist()],
        "state_topk_supports_first20": [
            [int(item) for item in row.tolist()] for row in state_support[:20]
        ],
        "memory_topk_supports": [[int(item) for item in row.tolist()] for row in memory_support],
        "support_intersection_size": distribution(intersections),
        "support_intersection_histogram": dict(Counter(intersections)),
        "zero_support_overlap_fraction": float(sum(1 for value in intersections if value == 0) / len(intersections))
        if intersections
        else 0.0,
        "raw_dot_distribution": distribution(raw_dot.flatten().tolist()),
        "zero_raw_dot_fraction": float(zero_dot / pair_count) if pair_count else 0.0,
        "numerically_zero_eps": zero_eps,
        "top1_equality_fraction": float(top1_equal / pair_count) if pair_count else 0.0,
        "nonzero_overlap_memories_per_state": distribution(nonzero_overlap_per_state),
        "nonzero_overlap_memories_per_state_first50": nonzero_overlap_per_state[:50],
        "state_basis_usage": basis_usage(states),
        "memory_basis_usage": basis_usage(memories),
    }


def address_geometry(state_address: Tensor, alpha: Tensor, rho: Tensor | None, *, topk: int) -> dict[str, Any]:
    state = state_address.detach().to(torch.float32).cpu()
    memory = alpha.detach().to(torch.float32).cpu()
    state_centered = state - state.mean(dim=0, keepdim=True)
    memory_centered = memory - memory.mean(dim=0, keepdim=True)
    output = {
        "state_pairwise_cosine": pairwise_cosine_summary(state),
        "alpha_pairwise_cosine": pairwise_cosine_summary(memory),
        "state_centered_spectrum": singular_summary(state_centered),
        "alpha_centered_spectrum": singular_summary(memory_centered),
        "state_top1_basis": basis_usage(state),
        "alpha_top1_basis": basis_usage(memory),
        "support_overlap": support_overlap_diagnostics(state, memory, topk=topk),
    }
    if rho is not None:
        output["rho"] = distribution(rho.detach().to(torch.float32).cpu().tolist())
    return output


def hard_topk_dead_zone_demo(rank: int = 8, topk: int = 2) -> dict[str, Any]:
    state_logits = torch.tensor([[5.0, 4.0, 0.0, -1.0, -2.0, -3.0, -4.0, -5.0]], requires_grad=True)
    memory_logits = torch.tensor([[-5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 4.0, 5.0]], requires_grad=True)
    if state_logits.shape[-1] != rank:
        state_logits = torch.linspace(float(rank), 1.0, rank).view(1, -1).requires_grad_(True)
        memory_logits = torch.linspace(1.0, float(rank), rank).view(1, -1).requires_grad_(True)
    state_address = normalize_address(state_logits, mode="topk_softmax", topk=topk)
    memory_address = normalize_address(memory_logits, mode="topk_softmax", topk=topk)
    q = (state_address * memory_address).sum()
    q.backward()
    state_support = set(torch.topk(state_logits.detach(), k=topk, dim=-1).indices[0].tolist())
    memory_support = set(torch.topk(memory_logits.detach(), k=topk, dim=-1).indices[0].tolist())
    return {
        "rank": rank,
        "topk": topk,
        "state_support": sorted(int(item) for item in state_support),
        "memory_support": sorted(int(item) for item in memory_support),
        "support_intersection_size": len(state_support.intersection(memory_support)),
        "q": float(q.detach().item()),
        "state_logits_grad": [float(v) for v in state_logits.grad.detach().flatten().tolist()],
        "memory_logits_grad": [float(v) for v in memory_logits.grad.detach().flatten().tolist()],
        "state_grad_norm": float(state_logits.grad.detach().norm().item()),
        "memory_grad_norm": float(memory_logits.grad.detach().norm().item()),
        "can_move_state_support": bool(state_logits.grad.detach().abs().max().item() > 0),
        "can_move_memory_support": bool(memory_logits.grad.detach().abs().max().item() > 0),
    }


def hard_topk_overlap_gradient_demo(rank: int = 8, topk: int = 2) -> dict[str, Any]:
    state_logits = torch.tensor([[5.0, 4.0, 0.0, -1.0, -2.0, -3.0, -4.0, -5.0]], requires_grad=True)
    memory_logits = torch.tensor([[4.5, 3.5, -3.0, -2.0, -1.0, 0.0, -4.0, -5.0]], requires_grad=True)
    state_address = normalize_address(state_logits, mode="topk_softmax", topk=topk)
    memory_address = normalize_address(memory_logits, mode="topk_softmax", topk=topk)
    q = (state_address * memory_address).sum()
    q.backward()
    return {
        "q": float(q.detach().item()),
        "state_grad_norm": float(state_logits.grad.detach().norm().item()),
        "memory_grad_norm": float(memory_logits.grad.detach().norm().item()),
    }


def gradient_norms_for_batch(
    model: AddressingOnlyModel,
    rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    *,
    device: torch.device,
) -> dict[str, float]:
    labels = rows_to_tensors(rows, device=device)
    state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    model.train()
    model.zero_grad(set_to_none=True)
    payload = model(state_reps, memory_reps)
    loss, _ = addressing_losses(payload["q"], labels, AddressingLossWeights())
    loss.backward()
    groups = {
        "state_representation_projector": list(model.state_encoder.encoder.parameters()),
        "state_address_head": list(model.state_encoder.address_head.parameters()),
        "memory_representation_projector": list(model.compiler.encoder.parameters()),
        "alpha_head": list(model.compiler.alpha_head.parameters()),
        "rho_head": list(model.compiler.rho_head.parameters()),
        "program_head": list(model.compiler.program_head.parameters()),
    }
    return {name: _grad_norm(params) for name, params in groups.items()}


def _grad_norm(params: Iterable[nn.Parameter]) -> float:
    total = 0.0
    for param in params:
        if param.grad is None:
            continue
        total += float(param.grad.detach().to(torch.float32).pow(2).sum().cpu())
    return math.sqrt(total)


def rows_by_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    if not train or not validation:
        raise ValueError("rows must contain both train and validation splits")
    return train, validation


def labels_to_matrices(rows: list[dict[str, Any]], device: torch.device | str = "cpu") -> dict[str, Tensor]:
    labels = rows_to_tensors(rows, device=device)
    utility = labels["utility"]
    valid = labels["valid_mask"]
    utility_masked = utility.masked_fill(~valid, float("nan"))
    return {
        "utility": utility,
        "valid": valid,
        "utility_masked": utility_masked,
        "positive_gain": labels["positive_gain"],
        "no_positive_state": labels["no_positive_state"],
        "all_missing_state": labels["all_missing_state"],
    }


def train_memory_prior(train_rows: list[dict[str, Any]]) -> Tensor:
    labels = rows_to_tensors(train_rows)
    valid = labels["valid_mask"].to(torch.float32)
    utility = labels["utility"].to(torch.float32)
    denom = valid.sum(dim=0).clamp_min(1.0)
    return (utility * valid).sum(dim=0) / denom


def utility_decomposition(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    *,
    mu: Tensor | None = None,
) -> dict[str, Any]:
    train = labels_to_matrices(train_rows)
    validation = labels_to_matrices(validation_rows)
    mu = train_memory_prior(train_rows) if mu is None else mu.detach().cpu().to(torch.float32)
    train_valid = train["valid"]
    train_utility = train["utility"]
    validation_valid = validation["valid"]
    validation_utility = validation["utility"]
    train_values = train_utility[train_valid]
    global_mean = float(train_values.mean().item())
    train_pred = mu.unsqueeze(0).expand_as(train_utility)
    train_residual = train_utility - train_pred
    validation_residual = validation_utility - mu.unsqueeze(0).expand_as(validation_utility)
    total_sse = float(((train_utility[train_valid] - global_mean) ** 2).sum().item())
    memory_sse = float((train_residual[train_valid] ** 2).sum().item())
    variance_explained = 1.0 - memory_sse / total_sse if total_sse > 0 else 0.0

    train_state_mean = _masked_mean_by_row(train_utility, train_valid)
    validation_state_mean = _masked_mean_by_row(validation_utility, validation_valid)
    train_utility_imputed = train_utility.clone()
    train_residual_imputed = train_residual.clone()
    for memory_index in range(train_utility.shape[1]):
        missing = ~train_valid[:, memory_index]
        train_utility_imputed[missing, memory_index] = mu[memory_index]
        train_residual_imputed[missing, memory_index] = 0.0
    train_utility_centered = train_utility_imputed - train_utility_imputed.mean(dim=0, keepdim=True)
    train_residual_centered = train_residual_imputed - train_residual_imputed.mean(dim=0, keepdim=True)
    return {
        "format": "stage_b_teacher_utility_decomposition_v1",
        "train_shape": list(train_utility.shape),
        "validation_shape": list(validation_utility.shape),
        "global_train_mean_utility": global_mean,
        "mu_distribution": distribution(mu.tolist()),
        "variance": {
            "train_total_variance": float(train_values.var(unbiased=False).item()),
            "train_memory_main_effect_sse": memory_sse,
            "train_total_centered_sse": total_sse,
            "variance_explained_by_memory_main_effect": variance_explained,
            "train_residual_variance": float(train_residual[train_valid].var(unbiased=False).item()),
            "train_memory_mean_variance": float(mu.var(unbiased=False).item()),
            "train_state_mean_variance": float(train_state_mean.var(unbiased=False).item()),
            "validation_state_mean_variance": float(validation_state_mean.var(unbiased=False).item()),
        },
        "residual_distribution": {
            "train": distribution(train_residual[train_valid].tolist()),
            "validation": distribution(validation_residual[validation_valid].tolist()),
        },
        "per_memory": _per_memory_variance(train_utility, train_valid, train_residual, mu),
        "per_state": {
            "train_mean_distribution": distribution(train_state_mean.tolist()),
            "validation_mean_distribution": distribution(validation_state_mean.tolist()),
            "train_variance_distribution": distribution(_masked_var_by_row(train_utility, train_valid).tolist()),
            "validation_variance_distribution": distribution(
                _masked_var_by_row(validation_utility, validation_valid).tolist()
            ),
        },
        "spectra": {
            "train_utility_imputed_centered": singular_summary(train_utility_centered),
            "train_residual_imputed_centered": singular_summary(train_residual_centered),
        },
    }


def _masked_mean_by_row(values: Tensor, mask: Tensor) -> Tensor:
    denom = mask.to(torch.float32).sum(dim=1).clamp_min(1.0)
    return (values * mask.to(torch.float32)).sum(dim=1) / denom


def _masked_var_by_row(values: Tensor, mask: Tensor) -> Tensor:
    mean = _masked_mean_by_row(values, mask)
    centered = (values - mean.unsqueeze(1)) * mask.to(torch.float32)
    denom = mask.to(torch.float32).sum(dim=1).clamp_min(1.0)
    return centered.pow(2).sum(dim=1) / denom


def _per_memory_variance(utility: Tensor, valid: Tensor, residual: Tensor, mu: Tensor) -> list[dict[str, Any]]:
    rows = []
    for memory_index in range(utility.shape[1]):
        mask = valid[:, memory_index]
        values = utility[mask, memory_index]
        residual_values = residual[mask, memory_index]
        rows.append(
            {
                "memory_stage_index": memory_index,
                "valid_count": int(mask.sum().item()),
                "mu": float(mu[memory_index].item()),
                "utility_variance": float(values.var(unbiased=False).item()) if values.numel() else None,
                "residual_variance": float(residual_values.var(unbiased=False).item()) if values.numel() else None,
                "positive_count": int((values > 0.01).sum().item()) if values.numel() else 0,
                "negative_count": int((values < -0.01).sum().item()) if values.numel() else 0,
            }
        )
    return rows


def per_state_metric_values(scores: Tensor, labels: dict[str, Tensor], *, k_values: tuple[int, ...] = (1, 4, 8)) -> dict[str, Any]:
    scores_cpu = scores.detach().to(torch.float32).cpu()
    utility = labels["utility"].detach().to(torch.float32).cpu()
    valid = labels["valid_mask"].detach().cpu()
    gain = labels["positive_gain"].detach().to(torch.float32).cpu()
    no_positive = labels["no_positive_state"].detach().cpu()
    all_missing = labels["all_missing_state"].detach().cpu()
    rows: list[dict[str, Any]] = []
    for row_index in range(scores_cpu.shape[0]):
        valid_indices = valid[row_index].nonzero(as_tuple=False).flatten().tolist()
        if not valid_indices:
            continue
        row_scores = [float(scores_cpu[row_index, index].item()) for index in valid_indices]
        row_utilities = [float(utility[row_index, index].item()) for index in valid_indices]
        row_gain = [float(gain[row_index, index].item()) for index in valid_indices]
        order = sorted(range(len(valid_indices)), key=lambda pos: row_scores[pos], reverse=True)
        ideal = sorted(range(len(valid_indices)), key=lambda pos: row_gain[pos], reverse=True)
        total_gain = sum(row_gain)
        best_utility = max(row_utilities)
        best_positions = {pos for pos, value in enumerate(row_utilities) if abs(value - best_utility) <= 1.0e-8}
        row_out: dict[str, Any] = {
            "row_index": row_index,
            "has_positive_gain": total_gain > 0,
            "has_pairwise": any(v > 0.01 for v in row_utilities) and any(v < -0.01 for v in row_utilities),
            "is_no_positive": bool(no_positive[row_index].item()) and not bool(all_missing[row_index].item()),
        }
        for k in k_values:
            top = order[: min(k, len(order))]
            row_out[f"best_recall@{k}"] = float(bool(best_positions.intersection(top)))
            if total_gain > 0:
                dcg = sum(row_gain[pos] / math.log2(rank + 2) for rank, pos in enumerate(top))
                ideal_top = ideal[: min(k, len(ideal))]
                idcg = sum(row_gain[pos] / math.log2(rank + 2) for rank, pos in enumerate(ideal_top))
                row_out[f"ndcg@{k}"] = dcg / idcg if idcg > 0 else None
                row_out[f"positive_mass_coverage@{k}"] = sum(row_gain[pos] for pos in top) / total_gain
            else:
                row_out[f"ndcg@{k}"] = None
                row_out[f"positive_mass_coverage@{k}"] = None
        for rank, pos in enumerate(order, start=1):
            if pos in best_positions:
                row_out["mrr"] = 1.0 / rank
                break
        pos_positions = [pos for pos, value in enumerate(row_utilities) if value > 0.01]
        neg_positions = [pos for pos, value in enumerate(row_utilities) if value < -0.01]
        if pos_positions and neg_positions:
            correct = 0
            total = 0
            for pos in pos_positions:
                for neg in neg_positions:
                    correct += int(row_scores[pos] > row_scores[neg])
                    total += 1
            row_out["positive_vs_negative_pairwise_accuracy"] = correct / total if total else None
        else:
            row_out["positive_vs_negative_pairwise_accuracy"] = None
        row_out["spearman"] = _spearman(row_utilities, row_scores)
        row_out["read_mass"] = sum(row_scores)
        rows.append(row_out)
    return {"rows": rows, "summary": aggregate_per_state_metrics(rows)}


def aggregate_per_state_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = sorted({key for row in rows for key in row if key not in {"row_index", "has_positive_gain", "has_pairwise", "is_no_positive"}})
    output = {}
    for name in metric_names:
        output[name] = mean_std(row[name] for row in rows if row.get(name) is not None)
    return output


def _rank_average(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for pos in range(cursor, end):
            ranks[order[pos]] = rank
        cursor = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    x = [value - x_mean for value in xs]
    y = [value - y_mean for value in ys]
    denom = math.sqrt(sum(value * value for value in x) * sum(value * value for value in y))
    if denom <= 0:
        return None
    return sum(a * b for a, b in zip(x, y)) / denom


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return _pearson(_rank_average(xs), _rank_average(ys))


def bootstrap_metric_ci(
    per_state_rows: dict[str, list[dict[str, Any]]],
    *,
    metrics: tuple[str, ...] = (
        "ndcg@4",
        "positive_mass_coverage@4",
        "mrr",
        "positive_vs_negative_pairwise_accuracy",
        "spearman",
    ),
    seed: int = 13,
    samples: int = 1000,
) -> dict[str, Any]:
    if not per_state_rows:
        return {}
    names = list(per_state_rows)
    n = len(next(iter(per_state_rows.values())))
    rng = random.Random(seed)
    output: dict[str, Any] = {}
    for metric in metrics:
        output[metric] = {}
        for name in names:
            values = [row.get(metric) for row in per_state_rows[name]]
            clean = [float(v) for v in values if v is not None]
            output[metric][name] = mean_std(clean)
        if len(names) >= 2:
            base = names[0]
            for other in names[1:]:
                diffs = []
                for _ in range(samples):
                    sampled = [rng.randrange(n) for _ in range(n)]
                    base_values = [per_state_rows[base][index].get(metric) for index in sampled]
                    other_values = [per_state_rows[other][index].get(metric) for index in sampled]
                    paired = [
                        (float(a), float(b))
                        for a, b in zip(base_values, other_values)
                        if a is not None and b is not None
                    ]
                    if not paired:
                        continue
                    diffs.append(sum(a - b for a, b in paired) / len(paired))
                sorted_diffs = sorted(diffs)
                if sorted_diffs:
                    lo = sorted_diffs[int(0.025 * (len(sorted_diffs) - 1))]
                    hi = sorted_diffs[int(0.975 * (len(sorted_diffs) - 1))]
                    output[metric][f"{base}_minus_{other}_bootstrap_ci95"] = {
                        "mean": sum(sorted_diffs) / len(sorted_diffs),
                        "lo": lo,
                        "hi": hi,
                        "samples": len(sorted_diffs),
                    }
    return output


class StateOnlyResidualHead(nn.Module):
    def __init__(self, state_dim: int, memory_count: int, hidden: int = 256, dropout: float = 0.05) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.GELU(),
            nn.LayerNorm(hidden),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
            nn.GELU(),
            nn.LayerNorm(hidden),
            nn.Dropout(dropout),
            nn.Linear(hidden, memory_count),
        )

    def forward(self, state_reps: Tensor, memory_reps: Tensor | None = None) -> Tensor:
        del memory_reps
        return self.net(state_reps.to(torch.float32))


class SignedTwoTowerResidualScorer(nn.Module):
    def __init__(self, state_dim: int, memory_dim: int, tower_dim: int = 128, hidden: int = 256, dropout: float = 0.05) -> None:
        super().__init__()
        self.tower_dim = tower_dim
        self.state_net = nn.Sequential(
            nn.Linear(state_dim, hidden),
            nn.GELU(),
            nn.LayerNorm(hidden),
            nn.Dropout(dropout),
            nn.Linear(hidden, tower_dim),
        )
        self.memory_net = nn.Sequential(
            nn.Linear(memory_dim, hidden),
            nn.GELU(),
            nn.LayerNorm(hidden),
            nn.Dropout(dropout),
            nn.Linear(hidden, tower_dim),
        )

    def forward(self, state_reps: Tensor, memory_reps: Tensor) -> Tensor:
        state = self.state_net(state_reps.to(torch.float32))
        memory = self.memory_net(memory_reps.to(torch.float32))
        return (state @ memory.T) / math.sqrt(self.tower_dim)


class DenseResidualAddressScorer(nn.Module):
    def __init__(
        self,
        config: RCMFConfig,
        representation_dim: int,
        *,
        shared_head_init: bool = False,
    ) -> None:
        super().__init__()
        cfg = copy.deepcopy(config)
        cfg.address.mode = "dense_softmax"
        self.backbone = AddressingOnlyModel(cfg, representation_dim=representation_dim)
        if shared_head_init:
            self.backbone.compiler.alpha_head.load_state_dict(self.backbone.state_encoder.address_head.state_dict())
        self.logit_scale = nn.Parameter(torch.tensor(1.0))
        self.gate_head = nn.Sequential(
            nn.Linear(cfg.encoder.hidden_size, cfg.encoder.hidden_size),
            nn.GELU(),
            nn.LayerNorm(cfg.encoder.hidden_size),
            nn.Linear(cfg.encoder.hidden_size, 1),
        )

    def forward(self, state_reps: Tensor, memory_reps: Tensor) -> dict[str, Tensor]:
        compiled = self.backbone.compile_memories(memory_reps)
        state_hidden = self.backbone.state_encoder.encoder(state_reps, None)
        state_address_logits = self.backbone.state_encoder.address_head(state_hidden)
        state_address = normalize_address(
            state_address_logits,
            mode=self.backbone.state_encoder.address_mode,
            topk=self.backbone.state_encoder.address_topk,
        )
        raw_dot = state_address.to(torch.float32) @ compiled["alpha"].to(torch.float32).T
        residual = self.logit_scale * raw_dot
        gate = torch.sigmoid(self.gate_head(state_hidden).squeeze(-1))
        return {
            "residual": residual,
            "state_address": state_address,
            "alpha": compiled["alpha"],
            "rho": compiled["rho"],
            "gate": gate,
            "raw_dot": raw_dot,
        }


@dataclass
class ResidualLossWeights:
    regression: float = 1.0
    listwise: float = 0.5
    ranking: float = 0.2
    gate: float = 0.2
    margin: float = 0.05
    huber_delta: float = 0.1
    hard_negatives: int = 8


def residual_training_loss(
    residual_pred: Tensor,
    mu: Tensor,
    labels: dict[str, Tensor],
    weights: ResidualLossWeights,
    *,
    gate: Tensor | None = None,
) -> tuple[Tensor, dict[str, float]]:
    valid = labels["valid_mask"]
    utility = labels["utility"]
    residual_target = utility - mu.to(utility.device).unsqueeze(0)
    score = mu.to(utility.device).unsqueeze(0) + residual_pred
    positive_gain = labels["positive_gain"] * valid.to(torch.float32)
    valid_float = valid.to(torch.float32)
    regression = F.huber_loss(
        residual_pred[valid],
        residual_target[valid],
        reduction="mean",
        delta=weights.huber_delta,
    ) if bool(valid.any().item()) else residual_pred.sum() * 0.0
    gain_sum = positive_gain.sum(dim=1)
    positive_state = gain_sum > 0
    if bool(positive_state.any().item()):
        teacher = positive_gain[positive_state] / gain_sum[positive_state].clamp_min(1.0e-8).unsqueeze(1)
        masked_score = score[positive_state].masked_fill(~valid[positive_state], -1.0e9)
        log_probs = F.log_softmax(masked_score, dim=1)
        listwise = -(teacher * log_probs).sum(dim=1).mean()
    else:
        listwise = residual_pred.sum() * 0.0
    pairwise_terms = []
    for row in range(score.shape[0]):
        pos_indices = (valid[row] & (utility[row] > 0.01)).nonzero(as_tuple=False).flatten()
        if pos_indices.numel() == 0:
            continue
        neg_mask = valid[row] & (utility[row] <= 0.01)
        hard_count = min(weights.hard_negatives, int(neg_mask.sum().item()))
        if hard_count <= 0:
            continue
        hard_indices = torch.topk(score[row].masked_fill(~neg_mask, -1.0e9), k=hard_count).indices
        pos_scores = score[row, pos_indices].view(-1, 1)
        neg_scores = score[row, hard_indices].view(1, -1)
        pairwise_terms.append(F.softplus(weights.margin - pos_scores + neg_scores).mean())
    ranking = torch.stack(pairwise_terms).mean() if pairwise_terms else residual_pred.sum() * 0.0
    if gate is not None:
        gate_target = (gain_sum > 0).to(torch.float32)
        gate_mask = ~labels["all_missing_state"]
        gate_loss = F.binary_cross_entropy(gate[gate_mask], gate_target[gate_mask]) if bool(gate_mask.any().item()) else gate.sum() * 0.0
    else:
        gate_loss = residual_pred.sum() * 0.0
    total = (
        weights.regression * regression
        + weights.listwise * listwise
        + weights.ranking * ranking
        + weights.gate * gate_loss
        + 0.0 * valid_float.sum()
    )
    return total, {
        "loss": float(total.detach().cpu()),
        "loss_regression": float(regression.detach().cpu()),
        "loss_listwise": float(listwise.detach().cpu()),
        "loss_ranking": float(ranking.detach().cpu()),
        "loss_gate": float(gate_loss.detach().cpu()),
    }


def residual_eval_stats(residual_pred: Tensor, mu: Tensor, labels: dict[str, Tensor]) -> dict[str, Any]:
    valid = labels["valid_mask"]
    residual_target = labels["utility"] - mu.to(labels["utility"].device).unsqueeze(0)
    pred = residual_pred.detach().to(torch.float32)
    target = residual_target.detach().to(torch.float32)
    if not bool(valid.any().item()):
        return {}
    errors = pred[valid] - target[valid]
    corr = _pearson(target[valid].cpu().tolist(), pred[valid].cpu().tolist())
    return {
        "residual_mse": float(errors.pow(2).mean().cpu()),
        "residual_huber": float(F.huber_loss(pred[valid], target[valid], reduction="mean", delta=0.1).cpu()),
        "residual_correlation": corr,
        "residual_pred_distribution": distribution(pred[valid].cpu().tolist()),
        "residual_target_distribution": distribution(target[valid].cpu().tolist()),
    }


def train_residual_scorer(
    *,
    model: nn.Module,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    seed: int,
    device: torch.device,
    max_epochs: int = 80,
    batch_size: int = 64,
    lr: float = 1.0e-3,
    weight_decay: float = 1.0e-4,
    patience: int = 12,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    model = model.to(device)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=weight_decay)
    loss_weights = ResidualLossWeights()
    best_state = copy.deepcopy(model.state_dict())
    best_metric = -1.0
    best_epoch = 0
    bad = 0
    mu_device = mu.to(device=device, dtype=torch.float32)
    for epoch in range(1, max_epochs + 1):
        model.train()
        rng = random.Random(seed * 100_000 + epoch)
        for indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
            batch_rows = [train_rows[index] for index in indices]
            labels = rows_to_tensors(batch_rows, device=device)
            state_batch = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
            memory_batch = memory_representations.to(device=device, dtype=torch.float32)
            payload = model(state_batch, memory_batch)
            if isinstance(payload, dict):
                residual_pred = payload["residual"]
                gate = payload.get("gate")
            else:
                residual_pred = payload
                gate = None
            loss, _ = residual_training_loss(residual_pred, mu_device, labels, loss_weights, gate=gate)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
        metrics = evaluate_residual_model(
            model,
            validation_rows,
            state_representations,
            memory_representations,
            mu,
            device=device,
        )
        primary = metrics["full_score"]["ndcg@4"]["mean"] or 0.0
        if primary > best_metric + 1.0e-6:
            best_metric = primary
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    final = evaluate_residual_model(
        model,
        validation_rows,
        state_representations,
        memory_representations,
        mu,
        device=device,
        include_controls=True,
        seed=seed,
    )
    final["best_epoch"] = best_epoch
    final["epochs_ran"] = epoch
    final["state_dict"] = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    return final


def evaluate_residual_model(
    model: nn.Module,
    rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    *,
    device: torch.device,
    include_controls: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    labels = rows_to_tensors(rows, device=device)
    state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    mu_device = mu.to(device=device, dtype=torch.float32)
    model.eval()
    with torch.no_grad():
        payload = model(state_reps, memory_reps)
        if isinstance(payload, dict):
            residual_pred = payload["residual"]
            gate = payload.get("gate")
            geometry = _maybe_dense_geometry(payload)
        else:
            residual_pred = payload
            gate = None
            geometry = {}
        full_scores = mu_device.unsqueeze(0) + residual_pred
        residual_scores = residual_pred
        output = {
            "full_score": evaluate_scores(full_scores, labels),
            "residual_only": evaluate_scores(residual_scores, labels),
            "residual_stats": residual_eval_stats(residual_pred, mu_device, labels),
            "per_state_full_score": per_state_metric_values(full_scores, labels),
            "per_state_residual_only": per_state_metric_values(residual_scores, labels),
            "geometry": geometry,
            "contribution": contribution_summary(mu_device, residual_pred, labels),
        }
        if gate is not None:
            output["gate"] = gate_summary(gate, labels)
    if include_controls:
        output["controls"] = evaluate_state_controls(
            model,
            rows,
            state_representations,
            memory_representations,
            mu,
            device=device,
            seed=seed,
        )
    return output


def _maybe_dense_geometry(payload: dict[str, Tensor]) -> dict[str, Any]:
    if "state_address" not in payload or "alpha" not in payload:
        return {}
    return address_geometry(payload["state_address"], payload["alpha"], payload.get("rho"), topk=4)


def gate_summary(gate: Tensor, labels: dict[str, Tensor]) -> dict[str, Any]:
    gate_cpu = gate.detach().to(torch.float32).cpu()
    gain_sum = labels["positive_gain"].detach().cpu().sum(dim=1)
    positive = gain_sum > 0
    no_positive = labels["no_positive_state"].detach().cpu() & ~labels["all_missing_state"].detach().cpu()
    return {
        "distribution": distribution(gate_cpu.tolist()),
        "positive_state_gate": distribution(gate_cpu[positive].tolist()),
        "no_positive_state_gate": distribution(gate_cpu[no_positive].tolist()),
        "false_activation_at_0.5": float((gate_cpu[no_positive] > 0.5).to(torch.float32).mean().item())
        if bool(no_positive.any().item())
        else None,
    }


def contribution_summary(mu: Tensor, residual_pred: Tensor, labels: dict[str, Tensor]) -> dict[str, Any]:
    valid = labels["valid_mask"]
    mu_scores = mu.to(residual_pred.device).unsqueeze(0).expand_as(residual_pred)
    mu_values = mu_scores[valid].detach().to(torch.float32).cpu()
    residual_values = residual_pred[valid].detach().to(torch.float32).cpu()
    full_values = (mu_scores + residual_pred)[valid].detach().to(torch.float32).cpu()
    return {
        "global_prior_distribution": distribution(mu_values.tolist()),
        "state_interaction_distribution": distribution(residual_values.tolist()),
        "full_score_distribution": distribution(full_values.tolist()),
        "global_prior_variance": float(mu_values.var(unbiased=False).item()) if mu_values.numel() else 0.0,
        "state_interaction_variance": float(residual_values.var(unbiased=False).item()) if residual_values.numel() else 0.0,
        "full_score_variance": float(full_values.var(unbiased=False).item()) if full_values.numel() else 0.0,
    }


def evaluate_state_controls(
    model: nn.Module,
    rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    *,
    device: torch.device,
    seed: int,
) -> dict[str, Any]:
    labels = rows_to_tensors(rows, device=device)
    state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    controls: dict[str, Any] = {}
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 20_000)
    variants = {
        "shuffled_state": state_reps[torch.randperm(state_reps.shape[0], generator=generator).to(device)],
        "zero_state": torch.zeros_like(state_reps),
        "mean_state": state_reps.mean(dim=0, keepdim=True).expand_as(state_reps),
    }
    model.eval()
    with torch.no_grad():
        for name, reps in variants.items():
            payload = model(reps, memory_reps)
            residual = payload["residual"] if isinstance(payload, dict) else payload
            full_scores = mu.to(device=device, dtype=torch.float32).unsqueeze(0) + residual
            controls[name] = {
                "full_score": evaluate_scores(full_scores, labels),
                "residual_only": evaluate_scores(residual, labels),
                "per_state_full_score": per_state_metric_values(full_scores, labels),
            }
    return controls


def build_current_stage_b_model(config: RCMFConfig, representation_dim: int, *, seed: int, device: torch.device) -> AddressingOnlyModel:
    torch.manual_seed(seed)
    random.seed(seed)
    return AddressingOnlyModel(config, representation_dim=representation_dim).to(device)


def train_one_epoch_current_stage_b(
    model: AddressingOnlyModel,
    train_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    *,
    seed: int,
    device: torch.device,
    lr: float = 1.0e-3,
    weight_decay: float = 1.0e-4,
    batch_size: int = 64,
) -> AddressingOnlyModel:
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=weight_decay)
    rng = random.Random(seed * 100_000 + 1)
    model.train()
    for indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
        batch_rows = [train_rows[index] for index in indices]
        labels = rows_to_tensors(batch_rows, device=device)
        state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
        memory_reps = memory_representations.to(device=device, dtype=torch.float32)
        payload = model(state_reps, memory_reps)
        loss, _ = addressing_losses(payload["q"], labels, AddressingLossWeights())
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        optimizer.step()
    return model


def evaluate_current_stage_b_model(
    model: AddressingOnlyModel,
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    *,
    device: torch.device,
    seed: int,
) -> dict[str, Any]:
    labels = rows_to_tensors(validation_rows, device=device)
    state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    model.eval()
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 30_000)
    with torch.no_grad():
        payload = model(state_reps, memory_reps)
        shuffled = model(state_reps[torch.randperm(state_reps.shape[0], generator=generator).to(device)], memory_reps)
        zeroed = model(torch.zeros_like(state_reps), memory_reps)
        mean_state = model(state_reps.mean(dim=0, keepdim=True).expand_as(state_reps), memory_reps)
    return {
        "metrics": evaluate_scores(payload["q"], labels),
        "per_state": per_state_metric_values(payload["q"], labels),
        "shuffled": evaluate_scores(shuffled["q"], labels),
        "zero_state": evaluate_scores(zeroed["q"], labels),
        "mean_state": evaluate_scores(mean_state["q"], labels),
        "geometry": address_geometry(payload["state_address"], payload["alpha"], payload["rho"], topk=4),
        "contribution": {
            "rho_distribution": distribution(payload["rho"].detach().cpu().tolist()),
            "raw_dot_distribution": distribution(
                (payload["state_address"].to(torch.float32) @ payload["alpha"].to(torch.float32).T)
                .detach()
                .cpu()
                .flatten()
                .tolist()
            ),
            "q_distribution": distribution(payload["q"].detach().cpu().flatten().tolist()),
        },
    }


def summarize_model_runs(runs: list[dict[str, Any]], *, primary_key: str = "full_score") -> dict[str, Any]:
    if not runs:
        return {}
    metric_names = [
        "ndcg@1",
        "ndcg@4",
        "ndcg@8",
        "best_recall@1",
        "best_recall@4",
        "best_recall@8",
        "positive_mass_coverage@1",
        "positive_mass_coverage@4",
        "positive_mass_coverage@8",
        "mrr",
        "positive_vs_negative_pairwise_accuracy",
        "spearman",
    ]
    output = {}
    for metric in metric_names:
        values = []
        for run in runs:
            metrics = run.get(primary_key, run.get("metrics", {}))
            item = metrics.get(metric)
            if isinstance(item, dict) and item.get("mean") is not None:
                values.append(float(item["mean"]))
        output[metric] = mean_std(values)
    return output

