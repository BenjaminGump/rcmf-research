from __future__ import annotations

from dataclasses import dataclass
import copy
import math
import random
from typing import Any, Iterable

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.addressing_4b import (
    _pearson,
    bootstrap_metric_ci,
    distribution,
    evaluate_scores,
    mean_std,
    pairwise_cosine_summary,
    per_state_metric_values,
    rows_to_tensors,
    singular_summary,
)
from rcmf.training.addressing_only import task_balanced_batches


SIGNED_FIELD_VERSION = "stage_b_signed_residual_field_v1"


def signed_tower(input_dim: int, hidden_dim: int = 256, output_dim: int = 128, dropout: float = 0.05) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(input_dim, hidden_dim),
        nn.GELU(),
        nn.LayerNorm(hidden_dim),
        nn.Dropout(dropout),
        nn.Linear(hidden_dim, output_dim),
    )


class ReferenceSignedTwoTower(nn.Module):
    """Milestone 4B diagnostic architecture, kept as the exact reference."""

    def __init__(self, state_dim: int, memory_dim: int, tower_dim: int = 128, hidden_dim: int = 256, dropout: float = 0.05) -> None:
        super().__init__()
        self.tower_dim = tower_dim
        self.state_tower = signed_tower(state_dim, hidden_dim=hidden_dim, output_dim=tower_dim, dropout=dropout)
        self.memory_tower = signed_tower(memory_dim, hidden_dim=hidden_dim, output_dim=tower_dim, dropout=dropout)
        self.gate_head = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def encode_state(self, state_representations: Tensor) -> Tensor:
        return self.state_tower(state_representations.to(torch.float32))

    def encode_memory(self, memory_representations: Tensor) -> Tensor:
        return self.memory_tower(memory_representations.to(torch.float32))

    def forward(self, state_representations: Tensor, memory_representations: Tensor) -> dict[str, Tensor]:
        q = self.encode_state(state_representations)
        k = self.encode_memory(memory_representations)
        residual = (q @ k.T) / math.sqrt(self.tower_dim)
        gate = torch.sigmoid(self.gate_head(state_representations.to(torch.float32)).squeeze(-1))
        return {"residual": residual, "q": q, "k": k, "gate": gate, "temperature": residual.new_tensor(1.0)}


class SignedResidualField(nn.Module):
    """RCMF-compatible signed continuous residual interaction.

    The residual is temperature * dot(q_s, k_i) / sqrt(rank). No softmax,
    top-k, nonnegative clamp, rho multiplication, or activation is applied to
    the signed interaction.
    """

    def __init__(
        self,
        state_dim: int,
        memory_dim: int,
        rank: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
        *,
        normalize_qk: bool = False,
        learned_temperature: bool = False,
    ) -> None:
        super().__init__()
        self.rank = rank
        self.normalize_qk = normalize_qk
        self.learned_temperature = learned_temperature
        self.state_query_network = signed_tower(state_dim, hidden_dim=hidden_dim, output_dim=rank, dropout=dropout)
        self.memory_key_network = signed_tower(memory_dim, hidden_dim=hidden_dim, output_dim=rank, dropout=dropout)
        self.gate_head = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )
        if learned_temperature:
            self.log_temperature = nn.Parameter(torch.tensor(0.0))
        else:
            self.register_buffer("temperature", torch.tensor(1.0), persistent=False)

    def positive_temperature(self) -> Tensor:
        if self.learned_temperature:
            return F.softplus(self.log_temperature) + 1.0e-6
        return self.temperature

    def encode_state(self, state_representations: Tensor) -> Tensor:
        q = self.state_query_network(state_representations.to(torch.float32))
        return rms_normalize(q) if self.normalize_qk else q

    def encode_memory(self, memory_representations: Tensor) -> Tensor:
        k = self.memory_key_network(memory_representations.to(torch.float32))
        return rms_normalize(k) if self.normalize_qk else k

    def forward(self, state_representations: Tensor, memory_representations: Tensor) -> dict[str, Tensor]:
        q = self.encode_state(state_representations)
        k = self.encode_memory(memory_representations)
        temperature = self.positive_temperature()
        residual = temperature * (q @ k.T) / math.sqrt(self.rank)
        gate = torch.sigmoid(self.gate_head(state_representations.to(torch.float32)).squeeze(-1))
        return {"residual": residual, "q": q, "k": k, "gate": gate, "temperature": temperature}


def copy_reference_weights_to_core(reference: ReferenceSignedTwoTower, core: SignedResidualField) -> None:
    if reference.tower_dim != core.rank:
        raise ValueError("reference tower_dim must match core rank")
    core.state_query_network.load_state_dict(reference.state_tower.state_dict())
    core.memory_key_network.load_state_dict(reference.memory_tower.state_dict())
    core.gate_head.load_state_dict(reference.gate_head.state_dict())


def rms_normalize(value: Tensor, eps: float = 1.0e-6) -> Tensor:
    return value / torch.sqrt(value.pow(2).mean(dim=-1, keepdim=True) + eps)


class MemoryPriorHead(nn.Module):
    def __init__(self, memory_dim: int, hidden_dim: int = 256, dropout: float = 0.05) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(memory_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, memory_representations: Tensor) -> Tensor:
        return self.net(memory_representations.to(torch.float32)).squeeze(-1)


class StateOnlyResidualHeadWithGate(nn.Module):
    def __init__(self, state_dim: int, memory_count: int, hidden_dim: int = 256, dropout: float = 0.05) -> None:
        super().__init__()
        self.residual_head = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, memory_count),
        )
        self.gate_head = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, state_representations: Tensor, memory_representations: Tensor) -> dict[str, Tensor]:
        del memory_representations
        state = state_representations.to(torch.float32)
        residual = self.residual_head(state)
        return {
            "residual": residual,
            "q": torch.empty(0, device=state.device),
            "k": torch.empty(0, device=state.device),
            "gate": torch.sigmoid(self.gate_head(state).squeeze(-1)),
            "temperature": state.new_tensor(1.0),
        }


def train_memory_prior(rows: list[dict[str, Any]], memory_count: int | None = None) -> Tensor:
    labels = rows_to_tensors(rows)
    valid = labels["valid_mask"].to(torch.float32)
    utility = labels["utility"].to(torch.float32)
    denom = valid.sum(dim=0).clamp_min(1.0)
    mu = (utility * valid).sum(dim=0) / denom
    if memory_count is not None and mu.numel() != memory_count:
        raise ValueError(f"mu memory count mismatch: {mu.numel()} != {memory_count}")
    return mu


def subset_rows_to_memory_indices(rows: list[dict[str, Any]], stage_indices: list[int]) -> list[dict[str, Any]]:
    output = []
    vector_keys = [
        "ordered_effective_memory_ids",
        "ordered_effective_memory_indices",
        "valid_mask",
        "legal_effective_mask",
        "raw_utility",
        "positive_mask",
        "neutral_mask",
        "negative_mask",
        "strong_positive_mask",
        "strong_negative_mask",
        "positive_gain",
        "score_statuses",
        "source_pair_keys",
        "target_sha256_by_memory",
        "memory_text_sha256_by_memory",
    ]
    for row in rows:
        cloned = dict(row)
        for key in vector_keys:
            if key in cloned:
                cloned[key] = [cloned[key][index] for index in stage_indices]
        valid = [bool(value) for value in cloned["valid_mask"]]
        gains = [float(value) for value in cloned["positive_gain"]]
        cloned["all_missing_state"] = not any(valid)
        cloned["no_positive_state"] = any(valid) and not any(valid_i and gain > 0 for valid_i, gain in zip(valid, gains))
        cloned["memory_id_to_stage_index"] = {
            memory_id: index for index, memory_id in enumerate(cloned["ordered_effective_memory_ids"])
        }
        output.append(cloned)
    return output


def deterministic_task_folds(task_ids: Iterable[str], *, folds: int = 5, seed: int = 17) -> list[dict[str, Any]]:
    tasks = sorted({str(task_id) for task_id in task_ids})
    rng = random.Random(seed)
    rng.shuffle(tasks)
    buckets = [[] for _ in range(folds)]
    for index, task_id in enumerate(tasks):
        buckets[index % folds].append(task_id)
    output = []
    for fold_index, validation_tasks in enumerate(buckets):
        validation_set = set(validation_tasks)
        train_tasks = [task_id for task_id in tasks if task_id not in validation_set]
        output.append(
            {
                "fold": fold_index,
                "seed": seed,
                "train_task_ids": sorted(train_tasks),
                "validation_task_ids": sorted(validation_tasks),
            }
        )
    return output


def build_fold_rows(
    rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    fold: dict[str, Any],
) -> dict[str, Any]:
    train_tasks = set(str(task_id) for task_id in fold["train_task_ids"])
    validation_tasks = set(str(task_id) for task_id in fold["validation_task_ids"])
    selected_stage_indices = [
        index for index, memory in enumerate(memory_bank) if str(memory["task_id"]) in train_tasks
    ]
    train_source_rows = [row for row in rows if str(row["task_id"]) in train_tasks]
    validation_source_rows = [row for row in rows if str(row["task_id"]) in validation_tasks]
    train_rows = subset_rows_to_memory_indices(train_source_rows, selected_stage_indices)
    validation_rows = subset_rows_to_memory_indices(validation_source_rows, selected_stage_indices)
    selected_memory_bank = [memory_bank[index] for index in selected_stage_indices]
    validation_errors = validate_fold_split(train_rows, validation_rows, selected_memory_bank, train_tasks, validation_tasks)
    return {
        "fold": fold,
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "memory_bank": selected_memory_bank,
        "stage_indices": selected_stage_indices,
        "validation": validation_errors,
    }


def validate_fold_split(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    train_tasks: set[str],
    validation_tasks: set[str],
) -> dict[str, Any]:
    errors = []
    for memory in memory_bank:
        task = str(memory["task_id"])
        if task in validation_tasks:
            errors.append(f"validation_task_memory_visible:{memory['memory_id']}")
        if task not in train_tasks:
            errors.append(f"non_train_fold_memory_visible:{memory['memory_id']}")
    for row in train_rows:
        for valid, memory in zip(row["valid_mask"], memory_bank):
            if valid and str(memory["task_id"]) == str(row["task_id"]):
                errors.append(f"train_own_task_not_masked:{row['state_example_id']}:{memory['memory_id']}")
    for row in validation_rows:
        if str(row["task_id"]) in train_tasks:
            errors.append(f"validation_row_from_train_task:{row['state_example_id']}")
    return {
        "passed": not errors,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "memory_count": len(memory_bank),
    }


def gate_labels(labels: dict[str, Tensor]) -> tuple[Tensor, Tensor]:
    positive = labels["positive_gain"].sum(dim=1) > 0
    all_missing = labels["all_missing_state"]
    valid_mask = ~all_missing
    return positive.to(torch.float32), valid_mask


def choose_gate_threshold(gate: Tensor, target: Tensor, mask: Tensor) -> float:
    scores = gate.detach().to(torch.float32).cpu()
    labels = target.detach().to(torch.float32).cpu()
    valid = mask.detach().cpu().bool()
    scores = scores[valid]
    labels = labels[valid]
    if scores.numel() == 0:
        return 0.5
    candidates = sorted(set(float(value) for value in scores.tolist()))
    if not candidates:
        return 0.5
    best_threshold = candidates[0]
    best_balanced = -1.0
    for threshold in candidates:
        pred = scores >= threshold
        pos = labels > 0.5
        neg = ~pos
        tpr = float((pred[pos]).to(torch.float32).mean().item()) if bool(pos.any().item()) else 0.0
        tnr = float((~pred[neg]).to(torch.float32).mean().item()) if bool(neg.any().item()) else 0.0
        balanced = 0.5 * (tpr + tnr)
        if balanced > best_balanced:
            best_balanced = balanced
            best_threshold = threshold
    return float(best_threshold)


def gate_metrics(gate: Tensor, labels: dict[str, Tensor], *, threshold: float) -> dict[str, Any]:
    target, mask = gate_labels(labels)
    scores = gate.detach().to(torch.float32).cpu()[mask.detach().cpu().bool()]
    y = target.detach().to(torch.float32).cpu()[mask.detach().cpu().bool()]
    if scores.numel() == 0:
        return {"count": 0}
    pred = scores >= threshold
    pos = y > 0.5
    neg = ~pos
    tpr = float(pred[pos].to(torch.float32).mean().item()) if bool(pos.any().item()) else 0.0
    tnr = float((~pred[neg]).to(torch.float32).mean().item()) if bool(neg.any().item()) else 0.0
    precision_recall = _precision_recall_curve(scores.tolist(), y.tolist())
    return {
        "count": int(scores.numel()),
        "threshold": threshold,
        "auroc": _auroc(scores.tolist(), y.tolist()),
        "auprc": _auprc(precision_recall),
        "balanced_accuracy": 0.5 * (tpr + tnr),
        "positive_state_gate": distribution(scores[pos].tolist()) if bool(pos.any().item()) else {"count": 0},
        "no_positive_state_gate": distribution(scores[neg].tolist()) if bool(neg.any().item()) else {"count": 0},
        "false_activation": float(pred[neg].to(torch.float32).mean().item()) if bool(neg.any().item()) else None,
        "positive_count": int(pos.sum().item()),
        "no_positive_count": int(neg.sum().item()),
    }


def _auroc(scores: list[float], labels: list[float]) -> float | None:
    positives = [score for score, label in zip(scores, labels) if label > 0.5]
    negatives = [score for score, label in zip(scores, labels) if label <= 0.5]
    if not positives or not negatives:
        return None
    correct = 0.0
    total = 0
    for pos in positives:
        for neg in negatives:
            correct += 1.0 if pos > neg else 0.5 if pos == neg else 0.0
            total += 1
    return correct / total


def _precision_recall_curve(scores: list[float], labels: list[float]) -> list[tuple[float, float]]:
    pairs = sorted(zip(scores, labels), key=lambda item: item[0], reverse=True)
    positives = sum(1 for _, label in pairs if label > 0.5)
    if positives == 0:
        return []
    tp = 0
    fp = 0
    points = [(1.0, 0.0)]
    for _, label in pairs:
        if label > 0.5:
            tp += 1
        else:
            fp += 1
        precision = tp / max(1, tp + fp)
        recall = tp / positives
        points.append((recall, precision))
    return points


def _auprc(points: list[tuple[float, float]]) -> float | None:
    if not points:
        return None
    area = 0.0
    prev_recall, prev_precision = points[0]
    for recall, precision in points[1:]:
        area += (recall - prev_recall) * precision
        prev_recall, prev_precision = recall, precision
    return area


@dataclass
class SignedLossWeights:
    huber: float = 1.0
    listwise: float = 0.5
    pairwise: float = 0.2
    gate: float = 0.2
    margin: float = 0.05
    huber_delta: float = 0.1
    hard_negatives: int = 8


def signed_residual_loss(
    residual: Tensor,
    gate: Tensor,
    mu: Tensor,
    labels: dict[str, Tensor],
    weights: SignedLossWeights,
) -> tuple[Tensor, dict[str, float]]:
    valid = labels["valid_mask"]
    utility = labels["utility"]
    mu_device = mu.to(device=utility.device, dtype=torch.float32)
    target = utility - mu_device.unsqueeze(0)
    score = mu_device.unsqueeze(0) + residual
    huber = F.huber_loss(residual[valid], target[valid], reduction="mean", delta=weights.huber_delta) if bool(valid.any().item()) else residual.sum() * 0.0

    gain = labels["positive_gain"] * valid.to(torch.float32)
    gain_sum = gain.sum(dim=1)
    positive_state = gain_sum > 0
    if bool(positive_state.any().item()):
        teacher = gain[positive_state] / gain_sum[positive_state].clamp_min(1.0e-8).unsqueeze(1)
        log_probs = F.log_softmax(score[positive_state].masked_fill(~valid[positive_state], -1.0e9), dim=1)
        listwise = -(teacher * log_probs).sum(dim=1).mean()
    else:
        listwise = residual.sum() * 0.0

    pairwise_terms = []
    for row in range(score.shape[0]):
        pos_indices = (valid[row] & (utility[row] > 0.01)).nonzero(as_tuple=False).flatten()
        if pos_indices.numel() == 0:
            continue
        non_positive = valid[row] & (utility[row] <= 0.01)
        count = min(weights.hard_negatives, int(non_positive.sum().item()))
        if count <= 0:
            continue
        neg_indices = torch.topk(score[row].masked_fill(~non_positive, -1.0e9), k=count).indices
        pairwise_terms.append(
            F.softplus(weights.margin - score[row, pos_indices].view(-1, 1) + score[row, neg_indices].view(1, -1)).mean()
        )
    pairwise = torch.stack(pairwise_terms).mean() if pairwise_terms else residual.sum() * 0.0
    gate_target, gate_mask = gate_labels(labels)
    gate_loss = F.binary_cross_entropy(gate[gate_mask], gate_target.to(gate.device)[gate_mask]) if bool(gate_mask.any().item()) else gate.sum() * 0.0
    total = weights.huber * huber + weights.listwise * listwise + weights.pairwise * pairwise + weights.gate * gate_loss
    return total, {
        "loss": float(total.detach().cpu()),
        "loss_huber": float(huber.detach().cpu()),
        "loss_listwise": float(listwise.detach().cpu()),
        "loss_pairwise": float(pairwise.detach().cpu()),
        "loss_gate": float(gate_loss.detach().cpu()),
    }


def residual_stats(residual: Tensor, mu: Tensor, labels: dict[str, Tensor]) -> dict[str, Any]:
    valid = labels["valid_mask"]
    utility = labels["utility"]
    target = utility - mu.to(utility.device).unsqueeze(0)
    pred = residual.detach().to(torch.float32)
    if not bool(valid.any().item()):
        return {}
    errors = pred[valid] - target[valid]
    return {
        "residual_mse": float(errors.pow(2).mean().cpu()),
        "residual_huber": float(F.huber_loss(pred[valid], target[valid], reduction="mean", delta=0.1).cpu()),
        "residual_correlation": _pearson(target[valid].detach().cpu().tolist(), pred[valid].cpu().tolist()),
        "residual_target": distribution(target[valid].detach().cpu().tolist()),
        "residual_pred": distribution(pred[valid].cpu().tolist()),
    }


def signed_geometry(
    q: Tensor,
    k: Tensor,
    residual: Tensor,
    mu: Tensor,
    labels: dict[str, Tensor],
    *,
    shuffled_residual: Tensor | None = None,
) -> dict[str, Any]:
    q_cpu = q.detach().to(torch.float32).cpu()
    k_cpu = k.detach().to(torch.float32).cpu()
    residual_cpu = residual.detach().to(torch.float32).cpu()
    valid = labels["valid_mask"].detach().cpu()
    mu_scores = mu.detach().to(torch.float32).cpu().unsqueeze(0).expand_as(residual_cpu)
    valid_residual = residual_cpu[valid]
    valid_mu = mu_scores[valid]
    near_zero = valid_residual.abs() <= 1.0e-6
    output = {
        "q_norm": distribution(q_cpu.norm(dim=1).tolist()),
        "k_norm": distribution(k_cpu.norm(dim=1).tolist()),
        "q_pairwise_cosine": pairwise_cosine_summary(q_cpu),
        "k_pairwise_cosine": pairwise_cosine_summary(k_cpu),
        "q_centered_spectrum": singular_summary(q_cpu - q_cpu.mean(dim=0, keepdim=True)),
        "k_centered_spectrum": singular_summary(k_cpu - k_cpu.mean(dim=0, keepdim=True)),
        "q_coordinate_variance": distribution(q_cpu.var(dim=0, unbiased=False).tolist()),
        "k_coordinate_variance": distribution(k_cpu.var(dim=0, unbiased=False).tolist()),
        "interaction_distribution": distribution(valid_residual.tolist()),
        "interaction_variance": float(valid_residual.var(unbiased=False).item()) if valid_residual.numel() else 0.0,
        "prior_distribution": distribution(valid_mu.tolist()),
        "prior_variance": float(valid_mu.var(unbiased=False).item()) if valid_mu.numel() else 0.0,
        "interaction_fraction_positive": float((valid_residual > 1.0e-6).to(torch.float32).mean().item()) if valid_residual.numel() else 0.0,
        "interaction_fraction_negative": float((valid_residual < -1.0e-6).to(torch.float32).mean().item()) if valid_residual.numel() else 0.0,
        "interaction_fraction_near_zero": float(near_zero.to(torch.float32).mean().item()) if valid_residual.numel() else 0.0,
    }
    if shuffled_residual is not None:
        shuffled = shuffled_residual.detach().to(torch.float32).cpu()
        output["correct_vs_shuffled_interaction_abs_delta"] = float((residual_cpu - shuffled).abs().mean().item())
        output["correct_vs_shuffled_valid_interaction_abs_delta"] = float((residual_cpu[valid] - shuffled[valid]).abs().mean().item())
    return output


def train_prior_head(
    memory_representations: Tensor,
    mu: Tensor,
    *,
    seed: int,
    epochs: int = 400,
    lr: float = 1.0e-3,
    device: torch.device,
) -> tuple[MemoryPriorHead, dict[str, Any]]:
    torch.manual_seed(seed)
    model = MemoryPriorHead(int(memory_representations.shape[1])).to(device)
    memory = memory_representations.to(device=device, dtype=torch.float32)
    target = mu.to(device=device, dtype=torch.float32)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1.0e-4)
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    for epoch in range(1, epochs + 1):
        pred = model(memory)
        loss = F.mse_loss(pred, target)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        value = float(loss.detach().cpu())
        if value < best_loss:
            best_loss = value
            best_state = copy.deepcopy(model.state_dict())
    model.load_state_dict(best_state)
    for param in model.parameters():
        param.requires_grad_(False)
    with torch.no_grad():
        pred = model(memory).detach().cpu()
    return model, {
        "train_mu_mse": float(F.mse_loss(pred, mu.to(torch.float32)).item()),
        "train_mu_correlation": _pearson(mu.to(torch.float32).tolist(), pred.tolist()),
        "mu_hat_distribution": distribution(pred.tolist()),
        "epochs": epochs,
    }


def train_signed_model(
    *,
    model: nn.Module,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    seed: int,
    device: torch.device,
    epochs: int = 80,
    batch_size: int = 64,
    lr: float = 1.0e-3,
    weight_decay: float = 1.0e-4,
    patience: int = 12,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    model = model.to(device)
    optimizer = torch.optim.AdamW([param for param in model.parameters() if param.requires_grad], lr=lr, weight_decay=weight_decay)
    weights = SignedLossWeights()
    best_metric = -1.0
    best_epoch = 0
    best_state = copy.deepcopy(model.state_dict())
    bad = 0
    for epoch in range(1, epochs + 1):
        model.train()
        rng = random.Random(seed * 100_000 + epoch)
        for indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
            batch_rows = [train_rows[index] for index in indices]
            labels = rows_to_tensors(batch_rows, device=device)
            state_batch = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
            memory_batch = memory_representations.to(device=device, dtype=torch.float32)
            payload = model(state_batch, memory_batch)
            loss, _ = signed_residual_loss(payload["residual"], payload["gate"], mu.to(device), labels, weights)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([param for param in model.parameters() if param.requires_grad], 1.0)
            optimizer.step()
        metrics = evaluate_signed_model(
            model,
            train_rows,
            validation_rows,
            state_representations,
            memory_representations,
            mu,
            device=device,
            seed=seed,
            include_controls=False,
        )
        primary = metrics["validation"]["full_score"]["ndcg@4"]["mean"] or 0.0
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
    output = evaluate_signed_model(
        model,
        train_rows,
        validation_rows,
        state_representations,
        memory_representations,
        mu,
        device=device,
        seed=seed,
        include_controls=True,
    )
    output["best_epoch"] = best_epoch
    output["epochs_ran"] = epoch
    output["state_dict"] = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    return output


def _state_tensor(rows: list[dict[str, Any]], state_representations: Tensor, device: torch.device) -> Tensor:
    indices = torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long)
    return state_representations[indices].to(device=device, dtype=torch.float32)


def evaluate_signed_model(
    model: nn.Module,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    *,
    device: torch.device,
    seed: int,
    include_controls: bool,
) -> dict[str, Any]:
    memory = memory_representations.to(device=device, dtype=torch.float32)
    train_labels = rows_to_tensors(train_rows, device=device)
    validation_labels = rows_to_tensors(validation_rows, device=device)
    train_state = _state_tensor(train_rows, state_representations, device)
    validation_state = _state_tensor(validation_rows, state_representations, device)
    mu_device = mu.to(device=device, dtype=torch.float32)
    model.eval()
    with torch.no_grad():
        train_payload = model(train_state, memory)
        validation_payload = model(validation_state, memory)
        threshold = choose_gate_threshold(
            train_payload["gate"].detach().cpu(),
            gate_labels(train_labels)[0].detach().cpu(),
            gate_labels(train_labels)[1].detach().cpu(),
        )
        full_scores = mu_device.unsqueeze(0) + validation_payload["residual"]
        train_full_scores = mu_device.unsqueeze(0) + train_payload["residual"]
        output = {
            "train": {
                "full_score": evaluate_scores(train_full_scores, train_labels),
                "gate": gate_metrics(train_payload["gate"], train_labels, threshold=threshold),
            },
            "validation": {
                "full_score": evaluate_scores(full_scores, validation_labels),
                "residual_only": evaluate_scores(validation_payload["residual"], validation_labels),
                "residual_stats": residual_stats(validation_payload["residual"], mu_device, validation_labels),
                "gate": gate_metrics(validation_payload["gate"], validation_labels, threshold=threshold),
                "per_state_full_score": per_state_metric_values(full_scores, validation_labels),
                "per_state_residual_only": per_state_metric_values(validation_payload["residual"], validation_labels),
            },
        }
        if "q" in validation_payload and validation_payload["q"].numel() and "k" in validation_payload and validation_payload["k"].numel():
            shuffled_state = _shuffled_state(validation_state, seed=seed)
            shuffled_payload = model(shuffled_state, memory)
            output["validation"]["geometry"] = signed_geometry(
                validation_payload["q"],
                validation_payload["k"],
                validation_payload["residual"],
                mu_device,
                validation_labels,
                shuffled_residual=shuffled_payload["residual"],
            )
        if include_controls:
            output["controls"] = evaluate_state_controls(
                model,
                validation_rows,
                state_representations,
                memory_representations,
                mu,
                device=device,
                seed=seed,
            )
    return output


def _shuffled_state(state: Tensor, *, seed: int) -> Tensor:
    if state.shape[0] <= 1:
        return state
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 40_000)
    order = torch.randperm(state.shape[0], generator=generator).to(state.device)
    return state[order]


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
    state = _state_tensor(rows, state_representations, device)
    memory = memory_representations.to(device=device, dtype=torch.float32)
    mu_device = mu.to(device=device, dtype=torch.float32)
    variants = {
        "shuffled_state": _shuffled_state(state, seed=seed),
        "mean_state": state.mean(dim=0, keepdim=True).expand_as(state),
        "zero_state": torch.zeros_like(state),
    }
    output = {}
    model.eval()
    with torch.no_grad():
        for name, state_variant in variants.items():
            payload = model(state_variant, memory)
            scores = mu_device.unsqueeze(0) + payload["residual"]
            output[name] = {
                "full_score": evaluate_scores(scores, labels),
                "residual_only": evaluate_scores(payload["residual"], labels),
                "per_state_full_score": per_state_metric_values(scores, labels),
            }
    return output


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [
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
    for metric in metrics:
        output[metric] = mean_std(
            run["validation"]["full_score"][metric]["mean"]
            for run in runs
            if run["validation"]["full_score"].get(metric, {}).get("mean") is not None
        )
    for metric in ("residual_mse", "residual_huber", "residual_correlation"):
        output[metric] = mean_std(
            run["validation"]["residual_stats"].get(metric)
            for run in runs
            if run["validation"].get("residual_stats", {}).get(metric) is not None
        )
    output["correct_minus_shuffled"] = summarize_control_delta(runs, "shuffled_state")
    output["correct_minus_mean"] = summarize_control_delta(runs, "mean_state")
    output["correct_minus_zero"] = summarize_control_delta(runs, "zero_state")
    output["gate"] = summarize_gate(runs)
    output["geometry"] = summarize_geometry(runs)
    output["bootstrap_ci"] = {
        f"seed_{index}": bootstrap_metric_ci(
            {
                "correct": run["validation"]["per_state_full_score"]["rows"],
                "shuffled": run["controls"]["shuffled_state"]["per_state_full_score"]["rows"],
            }
        )
        for index, run in enumerate(runs)
        if "controls" in run
    }
    return output


def summarize_control_delta(runs: list[dict[str, Any]], control_name: str) -> dict[str, Any]:
    metrics = ("ndcg@4", "positive_mass_coverage@4", "mrr", "spearman")
    output = {}
    for metric in metrics:
        deltas = []
        for run in runs:
            if "controls" not in run:
                continue
            correct = run["validation"]["full_score"][metric]["mean"] or 0.0
            control = run["controls"][control_name]["full_score"][metric]["mean"] or 0.0
            deltas.append(correct - control)
        output[metric] = mean_std(deltas)
    return output


def summarize_gate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ["auroc", "auprc", "balanced_accuracy", "false_activation"]
    output = {key: mean_std(run["validation"]["gate"].get(key) for run in runs if run["validation"]["gate"].get(key) is not None) for key in keys}
    output["positive_state_gate_mean"] = mean_std(
        run["validation"]["gate"]["positive_state_gate"].get("mean")
        for run in runs
        if run["validation"]["gate"].get("positive_state_gate", {}).get("mean") is not None
    )
    output["no_positive_state_gate_mean"] = mean_std(
        run["validation"]["gate"]["no_positive_state_gate"].get("mean")
        for run in runs
        if run["validation"]["gate"].get("no_positive_state_gate", {}).get("mean") is not None
    )
    return output


def summarize_geometry(runs: list[dict[str, Any]]) -> dict[str, Any]:
    paths = {
        "interaction_variance": ("interaction_variance",),
        "prior_variance": ("prior_variance",),
        "interaction_fraction_positive": ("interaction_fraction_positive",),
        "interaction_fraction_negative": ("interaction_fraction_negative",),
        "interaction_fraction_near_zero": ("interaction_fraction_near_zero",),
        "correct_vs_shuffled_valid_interaction_abs_delta": ("correct_vs_shuffled_valid_interaction_abs_delta",),
        "q_centered_effective_rank": ("q_centered_spectrum", "effective_rank"),
        "k_centered_effective_rank": ("k_centered_spectrum", "effective_rank"),
        "q_norm_mean": ("q_norm", "mean"),
        "k_norm_mean": ("k_norm", "mean"),
    }
    output = {}
    for name, path in paths.items():
        values = []
        for run in runs:
            geometry = run["validation"].get("geometry", {})
            cursor: Any = geometry
            for key in path:
                if not isinstance(cursor, dict) or key not in cursor:
                    cursor = None
                    break
                cursor = cursor[key]
            if cursor is not None:
                values.append(float(cursor))
        output[name] = mean_std(values)
    return output


class SignedAssociativeField:
    def __init__(self, rank: int, program_dim: int, *, device: torch.device | str = "cpu") -> None:
        self.rank = rank
        self.program_dim = program_dim
        self.device = torch.device(device)
        self.V = torch.zeros(rank, program_dim, dtype=torch.float32, device=self.device)
        self.G = torch.zeros(rank, rank, dtype=torch.float32, device=self.device)
        self.records: dict[str, tuple[Tensor, Tensor]] = {}

    def add(self, memory_id: str, key: Tensor, program: Tensor) -> None:
        if memory_id in self.records:
            raise ValueError(f"memory already exists: {memory_id}")
        key = key.to(device=self.device, dtype=torch.float32)
        program = program.to(device=self.device, dtype=torch.float32)
        self._validate(key, program)
        self.records[memory_id] = (key.clone(), program.clone())
        self.V += torch.outer(key, program)
        self.G += torch.outer(key, key)

    def remove(self, memory_id: str) -> None:
        key, program = self.records.pop(memory_id)
        self.V -= torch.outer(key, program)
        self.G -= torch.outer(key, key)

    def replace(self, memory_id: str, key: Tensor, program: Tensor) -> None:
        if memory_id in self.records:
            self.remove(memory_id)
        self.add(memory_id, key, program)

    def _validate(self, key: Tensor, program: Tensor) -> None:
        if key.shape != (self.rank,):
            raise ValueError("key shape mismatch")
        if program.shape != (self.program_dim,):
            raise ValueError("program shape mismatch")


def field_algebra_validation(*, rank: int = 16, program_dim: int = 7, count: int = 9, seed: int = 13) -> dict[str, Any]:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    keys = torch.randn(count, rank, generator=generator)
    programs = torch.randn(count, program_dim, generator=generator)
    q = torch.randn(rank, generator=generator)
    field = SignedAssociativeField(rank, program_dim)
    for index in range(count):
        field.add(f"m{index}", keys[index], programs[index])
    lhs_v = q @ field.V
    rhs_v = ((keys @ q).unsqueeze(1) * programs).sum(dim=0)
    lhs_g = q @ field.G @ q
    rhs_g = (keys @ q).pow(2).sum()
    one = SignedAssociativeField(rank, program_dim)
    one.add("x", keys[0], programs[0])
    one.remove("x")
    replace = SignedAssociativeField(rank, program_dim)
    replace.add("x", keys[0], programs[0])
    replace.replace("x", keys[1], programs[1])
    order = SignedAssociativeField(rank, program_dim)
    order_ids = list(range(count))
    random.Random(seed).shuffle(order_ids)
    for index in order_ids:
        order.add(f"m{index}", keys[index], programs[index])
    random.Random(seed + 1).shuffle(order_ids)
    for index in order_ids:
        order.remove(f"m{index}")
    return {
        "format": "signed_associative_field_validation_v1",
        "rank": rank,
        "program_dim": program_dim,
        "count": count,
        "v_identity_max_abs_error": float((lhs_v - rhs_v).abs().max().item()),
        "g_identity_abs_error": float((lhs_g - rhs_g).abs().item()),
        "add_remove_v_norm": float(one.V.norm().item()),
        "add_remove_g_norm": float(one.G.norm().item()),
        "replace_v_max_abs_error": float((replace.V - torch.outer(keys[1], programs[1])).abs().max().item()),
        "replace_g_max_abs_error": float((replace.G - torch.outer(keys[1], keys[1])).abs().max().item()),
        "arbitrary_order_v_norm": float(order.V.norm().item()),
        "arbitrary_order_g_norm": float(order.G.norm().item()),
        "passed": bool(
            torch.allclose(lhs_v, rhs_v, atol=1.0e-5)
            and torch.allclose(lhs_g, rhs_g, atol=1.0e-5)
            and torch.allclose(one.V, torch.zeros_like(one.V), atol=1.0e-6)
            and torch.allclose(one.G, torch.zeros_like(one.G), atol=1.0e-6)
            and torch.allclose(replace.V, torch.outer(keys[1], programs[1]), atol=1.0e-6)
            and torch.allclose(replace.G, torch.outer(keys[1], keys[1]), atol=1.0e-6)
            and torch.allclose(order.V, torch.zeros_like(order.V), atol=1.0e-5)
            and torch.allclose(order.G, torch.zeros_like(order.G), atol=1.0e-5)
        ),
    }
