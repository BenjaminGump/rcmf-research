from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import random
from typing import Any, Iterable

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import RCMFConfig
from rcmf.memory.compiler import (
    ExperienceCompiler,
    StateEncoder,
    build_representation_projector,
)


@dataclass
class AddressingLossWeights:
    listwise: float = 1.0
    pairwise: float = 0.5
    negative_suppression: float = 0.1
    no_positive_off: float = 0.05
    positive_activation: float = 0.1
    margin: float = 0.05
    min_positive_mass: float = 0.05
    hard_negatives: int = 8
    negative_weight_scale: float = 0.25
    negative_weight_clip: float = 4.0


class AddressingOnlyModel(nn.Module):
    """Stage-B model for applicability scoring only.

    The model score is exactly q(s, i) = rho_i * dot(b(s), alpha_i).
    It does not construct a memory program, injector, or Qwen action loss path.
    """

    def __init__(self, config: RCMFConfig, representation_dim: int) -> None:
        super().__init__()
        experience_encoder = build_representation_projector(representation_dim, config.encoder)
        state_encoder_backbone = (
            experience_encoder
            if config.encoder.shared_state_experience_encoder
            else build_representation_projector(representation_dim, config.encoder)
        )
        self.compiler = ExperienceCompiler(
            encoder=experience_encoder,
            hidden_size=config.encoder.hidden_size,
            memory=config.memory,
            address=config.address,
            use_write_strength=config.compiler.use_write_strength,
        )
        self.state_encoder = StateEncoder(
            encoder=state_encoder_backbone,
            hidden_size=config.encoder.hidden_size,
            memory=config.memory,
            address=config.address,
        )
        for param in self.compiler.program_head.parameters():
            param.requires_grad_(False)

    def compile_memories(self, memory_representations: Tensor) -> dict[str, Tensor]:
        compiled = self.compiler(memory_representations, None)
        return {"alpha": compiled.alpha, "rho": compiled.rho}

    def forward(self, state_representations: Tensor, memory_representations: Tensor) -> dict[str, Tensor]:
        compiled = self.compile_memories(memory_representations)
        state_address = self.state_encoder(state_representations, None)
        raw_scores = state_address.to(torch.float32) @ compiled["alpha"].to(torch.float32).T
        q = raw_scores * compiled["rho"].to(torch.float32).unsqueeze(0)
        q = torch.nan_to_num(q, nan=0.0, posinf=1.0e4, neginf=0.0).clamp_min(0.0)
        return {
            "q": q,
            "state_address": state_address,
            "alpha": compiled["alpha"],
            "rho": compiled["rho"],
        }

    def trainable_parameter_names(self) -> list[str]:
        return [name for name, param in self.named_parameters() if param.requires_grad]


def rows_to_tensors(rows: list[dict[str, Any]], device: torch.device | str = "cpu") -> dict[str, Tensor]:
    if not rows:
        raise ValueError("rows must not be empty")
    utility_rows = [
        [0.0 if value is None else float(value) for value in row["raw_utility"]]
        for row in rows
    ]
    tensors = {
        "state_indices": torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long, device=device),
        "utility": torch.tensor(utility_rows, dtype=torch.float32, device=device),
        "valid_mask": torch.tensor([row["valid_mask"] for row in rows], dtype=torch.bool, device=device),
        "positive_gain": torch.tensor([row["positive_gain"] for row in rows], dtype=torch.float32, device=device),
        "strong_positive_mask": torch.tensor(
            [row["strong_positive_mask"] for row in rows], dtype=torch.bool, device=device
        ),
        "strong_negative_mask": torch.tensor(
            [row["strong_negative_mask"] for row in rows], dtype=torch.bool, device=device
        ),
        "negative_mask": torch.tensor([row["negative_mask"] for row in rows], dtype=torch.bool, device=device),
        "no_positive_state": torch.tensor(
            [bool(row["no_positive_state"]) for row in rows], dtype=torch.bool, device=device
        ),
        "all_missing_state": torch.tensor(
            [bool(row["all_missing_state"]) for row in rows], dtype=torch.bool, device=device
        ),
    }
    return tensors


def _masked_sum(value: Tensor, mask: Tensor, dim: int = -1) -> Tensor:
    return (value * mask.to(value.dtype)).sum(dim=dim)


def addressing_losses(
    q: Tensor,
    labels: dict[str, Tensor],
    weights: AddressingLossWeights,
    eps: float = 1.0e-8,
) -> tuple[Tensor, dict[str, float]]:
    valid = labels["valid_mask"]
    utility = labels["utility"]
    positive_gain = labels["positive_gain"] * valid.to(q.dtype)
    gain_sum = positive_gain.sum(dim=1)
    positive_state = gain_sum > 0

    losses: list[Tensor] = []
    metrics: dict[str, float] = {}

    if bool(positive_state.any().item()):
        teacher = positive_gain[positive_state] / gain_sum[positive_state].clamp_min(eps).unsqueeze(1)
        q_valid = q[positive_state] * valid[positive_state].to(q.dtype)
        student = q_valid / q_valid.sum(dim=1).clamp_min(eps).unsqueeze(1)
        listwise = -(teacher * (student + eps).log()).sum(dim=1).mean()
    else:
        listwise = q.sum() * 0.0
    losses.append(weights.listwise * listwise)
    metrics["loss_listwise"] = float(listwise.detach().cpu())

    pairwise_terms: list[Tensor] = []
    for row in range(q.shape[0]):
        pos_indices = labels["strong_positive_mask"][row].nonzero(as_tuple=False).flatten()
        if pos_indices.numel() == 0:
            continue
        non_positive_mask = valid[row] & (positive_gain[row] <= 0)
        hard_scores = q[row].masked_fill(~non_positive_mask, -1.0)
        hard_count = min(weights.hard_negatives, int(non_positive_mask.sum().item()))
        hard_indices = (
            torch.topk(hard_scores, k=hard_count).indices
            if hard_count > 0
            else torch.empty(0, dtype=torch.long, device=q.device)
        )
        strong_neg_indices = labels["strong_negative_mask"][row].nonzero(as_tuple=False).flatten()
        neg_indices = torch.unique(torch.cat([strong_neg_indices, hard_indices]))
        if neg_indices.numel() == 0:
            continue
        pos_scores = q[row, pos_indices].view(-1, 1)
        neg_scores = q[row, neg_indices].view(1, -1)
        pairwise_terms.append(F.softplus(weights.margin - pos_scores + neg_scores).mean())
    pairwise = torch.stack(pairwise_terms).mean() if pairwise_terms else q.sum() * 0.0
    losses.append(weights.pairwise * pairwise)
    metrics["loss_pairwise"] = float(pairwise.detach().cpu())

    negative_mask = valid & labels["negative_mask"]
    negative_weight = ((-utility - 0.01) / max(weights.negative_weight_scale, eps)).clamp(
        min=0.0,
        max=weights.negative_weight_clip,
    )
    if bool(negative_mask.any().item()):
        negative_suppression = (q * negative_weight * negative_mask.to(q.dtype)).sum() / negative_mask.sum().clamp_min(1)
    else:
        negative_suppression = q.sum() * 0.0
    losses.append(weights.negative_suppression * negative_suppression)
    metrics["loss_negative_suppression"] = float(negative_suppression.detach().cpu())

    no_positive = labels["no_positive_state"] & ~labels["all_missing_state"]
    read_mass = _masked_sum(q, valid, dim=1)
    if bool(no_positive.any().item()):
        no_positive_off = read_mass[no_positive].mean()
    else:
        no_positive_off = q.sum() * 0.0
    losses.append(weights.no_positive_off * no_positive_off)
    metrics["loss_no_positive_off"] = float(no_positive_off.detach().cpu())

    strong_positive_state = labels["strong_positive_mask"].any(dim=1)
    if bool(strong_positive_state.any().item()):
        strong_mass = _masked_sum(
            q,
            labels["strong_positive_mask"] & valid,
            dim=1,
        )
        positive_activation = F.relu(weights.min_positive_mass - strong_mass[strong_positive_state]).mean()
    else:
        positive_activation = q.sum() * 0.0
    losses.append(weights.positive_activation * positive_activation)
    metrics["loss_positive_activation"] = float(positive_activation.detach().cpu())

    total = torch.stack(losses).sum()
    metrics["loss"] = float(total.detach().cpu())
    metrics["read_mass_positive_mean"] = (
        float(read_mass[positive_state].detach().mean().cpu()) if bool(positive_state.any().item()) else 0.0
    )
    metrics["read_mass_no_positive_mean"] = (
        float(read_mass[no_positive].detach().mean().cpu()) if bool(no_positive.any().item()) else 0.0
    )
    return total, metrics


def _rank_average(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end) / 2.0
        for pos in range(cursor, end):
            ranks[order[pos]] = average
        cursor = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(ys) < 2:
        return None
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    x_centered = [x - x_mean for x in xs]
    y_centered = [y - y_mean for y in ys]
    denom = math.sqrt(sum(x * x for x in x_centered) * sum(y * y for y in y_centered))
    if denom <= 0:
        return None
    return sum(x * y for x, y in zip(x_centered, y_centered)) / denom


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return _pearson(_rank_average(xs), _rank_average(ys))


def _mean_std(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "std": None}
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return {"count": len(values), "mean": mean, "std": math.sqrt(var)}


def evaluate_scores(
    scores: Tensor,
    labels: dict[str, Tensor],
    *,
    ks: tuple[int, ...] = (1, 4, 8),
    false_activation_threshold: float = 0.1,
) -> dict[str, Any]:
    scores_cpu = scores.detach().to(torch.float32).cpu()
    utility = labels["utility"].detach().to(torch.float32).cpu()
    valid = labels["valid_mask"].detach().cpu()
    gain = labels["positive_gain"].detach().to(torch.float32).cpu()
    no_positive = labels["no_positive_state"].detach().cpu()
    all_missing = labels["all_missing_state"].detach().cpu()
    metrics: dict[str, Any] = {}
    ndcg_by_k: dict[int, list[float]] = {k: [] for k in ks}
    recall_by_k: dict[int, list[float]] = {k: [] for k in ks}
    mass_by_k: dict[int, list[float]] = {k: [] for k in ks}
    mrr_values: list[float] = []
    pairwise_values: list[float] = []
    spearman_values: list[float] = []
    read_mass_positive: list[float] = []
    read_mass_no_positive: list[float] = []
    false_activation_values: list[float] = []
    evaluated_states = 0
    positive_gain_states = 0
    pairwise_states = 0
    spearman_states = 0
    for row in range(scores_cpu.shape[0]):
        valid_indices = valid[row].nonzero(as_tuple=False).flatten().tolist()
        if not valid_indices:
            continue
        evaluated_states += 1
        row_scores = [float(scores_cpu[row, index].item()) for index in valid_indices]
        row_utilities = [float(utility[row, index].item()) for index in valid_indices]
        row_gain = [float(gain[row, index].item()) for index in valid_indices]
        order = sorted(range(len(valid_indices)), key=lambda pos: row_scores[pos], reverse=True)
        ideal_gain_order = sorted(range(len(valid_indices)), key=lambda pos: row_gain[pos], reverse=True)
        total_gain = sum(row_gain)
        if total_gain > 0:
            positive_gain_states += 1
            read_mass_positive.append(sum(row_scores))
        if bool(no_positive[row].item()) and not bool(all_missing[row].item()):
            mass = sum(row_scores)
            read_mass_no_positive.append(mass)
            false_activation_values.append(float(mass > false_activation_threshold))
        best_utility = max(row_utilities)
        best_positions = {pos for pos, value in enumerate(row_utilities) if abs(value - best_utility) <= 1.0e-8}
        for k in ks:
            top = order[: min(k, len(order))]
            recall_by_k[k].append(float(bool(best_positions.intersection(top))))
            if total_gain > 0:
                dcg = sum(row_gain[pos] / math.log2(rank + 2) for rank, pos in enumerate(top))
                ideal_top = ideal_gain_order[: min(k, len(ideal_gain_order))]
                idcg = sum(row_gain[pos] / math.log2(rank + 2) for rank, pos in enumerate(ideal_top))
                ndcg_by_k[k].append(dcg / idcg if idcg > 0 else 0.0)
                mass_by_k[k].append(sum(row_gain[pos] for pos in top) / total_gain)
        for rank, pos in enumerate(order, start=1):
            if pos in best_positions:
                mrr_values.append(1.0 / rank)
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
            pairwise_values.append(correct / total if total else 0.0)
            pairwise_states += 1
        corr = _spearman(row_utilities, row_scores)
        if corr is not None:
            spearman_values.append(corr)
            spearman_states += 1
    for k in ks:
        metrics[f"ndcg@{k}"] = _mean_std(ndcg_by_k[k])
        metrics[f"best_recall@{k}"] = _mean_std(recall_by_k[k])
        metrics[f"positive_mass_coverage@{k}"] = _mean_std(mass_by_k[k])
    metrics["mrr"] = _mean_std(mrr_values)
    metrics["positive_vs_negative_pairwise_accuracy"] = _mean_std(pairwise_values)
    metrics["spearman"] = _mean_std(spearman_values)
    metrics["read_mass_positive"] = _mean_std(read_mass_positive)
    metrics["read_mass_no_positive"] = _mean_std(read_mass_no_positive)
    metrics["false_activation_no_positive"] = _mean_std(false_activation_values)
    metrics["evaluated_states"] = evaluated_states
    metrics["positive_gain_states"] = positive_gain_states
    metrics["pairwise_states"] = pairwise_states
    metrics["spearman_states"] = spearman_states
    return metrics


def baseline_global_mean_train_utility(
    train_labels: dict[str, Tensor],
    validation_labels: dict[str, Tensor],
) -> Tensor:
    train_valid = train_labels["valid_mask"]
    utility = train_labels["utility"]
    denom = train_valid.to(torch.float32).sum(dim=0).clamp_min(1.0)
    mean = (utility * train_valid.to(torch.float32)).sum(dim=0) / denom
    return mean.unsqueeze(0).repeat(validation_labels["valid_mask"].shape[0], 1)


def baseline_frozen_qwen_cosine(
    state_representations: Tensor,
    memory_representations: Tensor,
) -> Tensor:
    state = F.normalize(state_representations.to(torch.float32), dim=-1)
    memory = F.normalize(memory_representations.to(torch.float32), dim=-1)
    return state @ memory.T


def baseline_random(
    shape: tuple[int, int],
    seed: int,
    device: torch.device | str = "cpu",
) -> Tensor:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    return torch.rand(shape, generator=generator, dtype=torch.float32).to(device)


def rho_only_scores(q_payload: dict[str, Tensor], state_count: int) -> Tensor:
    return q_payload["rho"].to(torch.float32).unsqueeze(0).repeat(state_count, 1)


def geometry_diagnostics(
    state_address: Tensor,
    alpha: Tensor,
    rho: Tensor,
    *,
    topk: int = 4,
) -> dict[str, Any]:
    return {
        "state_address": _matrix_geometry(state_address, topk=topk),
        "alpha": _matrix_geometry(alpha, topk=topk),
        "rho": _distribution(rho.detach().to(torch.float32).cpu().tolist()),
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0}
    sorted_values = sorted(values)
    def pct(frac: float) -> float:
        index = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * frac))))
        return sorted_values[index]
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "count": len(values),
        "min": sorted_values[0],
        "p05": pct(0.05),
        "p25": pct(0.25),
        "p50": pct(0.50),
        "p75": pct(0.75),
        "p95": pct(0.95),
        "max": sorted_values[-1],
        "mean": mean,
        "std": math.sqrt(var),
    }


def _effective_rank(singular_values: Tensor, eps: float = 1.0e-12) -> float:
    values = singular_values.detach().to(torch.float64).clamp_min(0)
    total = values.sum()
    if float(total.item()) <= eps:
        return 0.0
    probs = values / total
    entropy = -(probs * (probs + eps).log()).sum()
    return float(entropy.exp().item())


def _matrix_geometry(matrix: Tensor, *, topk: int = 4) -> dict[str, Any]:
    x = matrix.detach().to(torch.float32).cpu()
    if x.numel() == 0:
        return {}
    normalized = F.normalize(x, dim=-1)
    cosine = normalized @ normalized.T
    if cosine.shape[0] > 1:
        off_diag = cosine[~torch.eye(cosine.shape[0], dtype=torch.bool)]
        cosine_summary = _distribution(off_diag.tolist())
    else:
        cosine_summary = {"count": 0}
    centered = x - x.mean(dim=0, keepdim=True)
    singular_values = torch.linalg.svdvals(centered)
    top1 = x.argmax(dim=1).tolist()
    top1_counts = Counter(int(item) for item in top1)
    topk_indices = torch.topk(x, k=min(topk, x.shape[1]), dim=1).indices.flatten().tolist()
    topk_counts = Counter(int(item) for item in topk_indices)
    return {
        "shape": list(x.shape),
        "pairwise_cosine": cosine_summary,
        "mean_centered_variation": float(centered.norm(dim=1).mean().item()),
        "centered_effective_rank": _effective_rank(singular_values),
        "singular_values": _distribution(singular_values.tolist()),
        "top1_basis_usage_count": len(top1_counts),
        "top1_max_basis_load_fraction": max(top1_counts.values()) / len(top1) if top1 else 0.0,
        "topk_basis_usage_count": len(topk_counts),
        "topk_max_basis_load_fraction": max(topk_counts.values()) / len(topk_indices) if topk_indices else 0.0,
        "top1_basis_counts_top10": dict(top1_counts.most_common(10)),
        "topk_basis_counts_top10": dict(topk_counts.most_common(10)),
    }


def task_balanced_batches(
    rows: list[dict[str, Any]],
    batch_size: int,
    rng: random.Random,
) -> Iterable[list[int]]:
    by_task: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_task[str(row["task_id"])].append(index)
    for indices in by_task.values():
        rng.shuffle(indices)
    task_order = list(by_task)
    rng.shuffle(task_order)
    cursors = {task: 0 for task in task_order}
    batch: list[int] = []
    while True:
        progressed = False
        for task in task_order:
            cursor = cursors[task]
            if cursor >= len(by_task[task]):
                continue
            batch.append(by_task[task][cursor])
            cursors[task] = cursor + 1
            progressed = True
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if not progressed:
            break
    if batch:
        yield batch
