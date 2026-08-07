from __future__ import annotations

from dataclasses import dataclass
import copy
import math
import random
from typing import Any, Iterable, Sequence

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.addressing_4b import (
    _pearson,
    distribution,
    evaluate_scores,
    mean_std,
    per_state_metric_values,
)
from rcmf.training.addressing_only import rows_to_tensors, task_balanced_batches
from rcmf.training.signed_residual_field import (
    SignedLossWeights,
    SignedResidualField,
    choose_gate_threshold,
    gate_labels,
    gate_metrics,
    residual_stats,
    signed_geometry,
    signed_residual_loss,
    train_memory_prior,
)


SELECTOR_REPAIR_VERSION = "stage_b_selector_repair_5c_v1"
POSITIVE_EPS = 0.01
STRONG_POSITIVE = 0.05
STRONG_NEGATIVE = -0.05


@dataclass(frozen=True)
class SelectorRepairLossConfig:
    name: str
    original_4c_loss: bool = False
    huber: float = 1.0
    gap_pairwise: float = 0.0
    top_listwise: float = 0.0
    sign_calibration: float = 0.0
    near_best: float = 0.0
    gate: float = 0.2
    huber_delta: float = 0.1
    pair_gap_threshold: float = 0.05
    pair_margin: float = 0.05
    gap_weight_scale: float = 0.10
    gap_weight_clip: float = 8.0
    teacher_temperature: float = 0.05
    near_best_delta: float = 0.03
    margin_positive: float = 0.03
    margin_negative: float = 0.03

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "original_4c_loss": self.original_4c_loss,
            "huber": self.huber,
            "gap_pairwise": self.gap_pairwise,
            "top_listwise": self.top_listwise,
            "sign_calibration": self.sign_calibration,
            "near_best": self.near_best,
            "gate": self.gate,
            "huber_delta": self.huber_delta,
            "pair_gap_threshold": self.pair_gap_threshold,
            "pair_margin": self.pair_margin,
            "gap_weight_scale": self.gap_weight_scale,
            "gap_weight_clip": self.gap_weight_clip,
            "teacher_temperature": self.teacher_temperature,
            "near_best_delta": self.near_best_delta,
            "margin_positive": self.margin_positive,
            "margin_negative": self.margin_negative,
        }


def default_repair_configs() -> list[SelectorRepairLossConfig]:
    configs = [SelectorRepairLossConfig(name="A_stage4c_original", original_4c_loss=True)]
    for threshold in (0.02, 0.05, 0.10):
        configs.append(
            SelectorRepairLossConfig(
                name=f"B_gap_all_pairs_gap{_tag_float(threshold)}",
                gap_pairwise=0.6,
                pair_gap_threshold=threshold,
            )
        )
    for temperature in (0.03, 0.05, 0.10):
        configs.append(
            SelectorRepairLossConfig(
                name=f"C_top_listwise_temp{_tag_float(temperature)}",
                top_listwise=0.8,
                teacher_temperature=temperature,
            )
        )
    configs.extend(
        [
            SelectorRepairLossConfig(
                name="D_gap_top_sign",
                gap_pairwise=0.6,
                top_listwise=0.8,
                sign_calibration=0.2,
                pair_gap_threshold=0.05,
                teacher_temperature=0.05,
            ),
            SelectorRepairLossConfig(
                name="E_gap_top_sign_nearbest",
                gap_pairwise=0.6,
                top_listwise=0.8,
                sign_calibration=0.2,
                near_best=0.4,
                pair_gap_threshold=0.05,
                teacher_temperature=0.05,
                near_best_delta=0.03,
            ),
        ]
    )
    return configs


def _tag_float(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def selector_repair_loss(
    residual: Tensor,
    gate: Tensor,
    mu: Tensor,
    labels: dict[str, Tensor],
    config: SelectorRepairLossConfig,
) -> tuple[Tensor, dict[str, float]]:
    if config.original_4c_loss:
        loss, metrics = signed_residual_loss(residual, gate, mu, labels, SignedLossWeights())
        return loss, {f"loss_{config.name}": float(loss.detach().cpu()), **metrics}

    valid = labels["valid_mask"]
    utility = labels["utility"]
    mu_device = mu.to(device=utility.device, dtype=torch.float32)
    target = utility - mu_device.unsqueeze(0)
    score = mu_device.unsqueeze(0) + residual
    zero = residual.sum() * 0.0
    losses: list[Tensor] = []
    metrics: dict[str, float] = {}

    if bool(valid.any().item()):
        huber = F.huber_loss(residual[valid], target[valid], reduction="mean", delta=config.huber_delta)
    else:
        huber = zero
    losses.append(config.huber * huber)
    metrics["loss_huber"] = float(huber.detach().cpu())

    gap_pairwise = _gap_weighted_pairwise(score, utility, valid, config)
    losses.append(config.gap_pairwise * gap_pairwise)
    metrics["loss_gap_pairwise"] = float(gap_pairwise.detach().cpu())

    top_listwise = _top_utility_listwise(score, utility, valid, config)
    losses.append(config.top_listwise * top_listwise)
    metrics["loss_top_listwise"] = float(top_listwise.detach().cpu())

    sign_calibration = _sign_calibration(score, utility, valid, config)
    losses.append(config.sign_calibration * sign_calibration)
    metrics["loss_sign_calibration"] = float(sign_calibration.detach().cpu())

    near_best = _near_best_loss(score, utility, valid, config)
    losses.append(config.near_best * near_best)
    metrics["loss_near_best"] = float(near_best.detach().cpu())

    gate_target, gate_mask = gate_labels(labels)
    if bool(gate_mask.any().item()):
        gate_loss = F.binary_cross_entropy(gate[gate_mask], gate_target.to(gate.device)[gate_mask])
    else:
        gate_loss = zero
    losses.append(config.gate * gate_loss)
    metrics["loss_gate"] = float(gate_loss.detach().cpu())

    total = torch.stack(losses).sum()
    metrics["loss"] = float(total.detach().cpu())
    return total, metrics


def _gap_weighted_pairwise(score: Tensor, utility: Tensor, valid: Tensor, config: SelectorRepairLossConfig) -> Tensor:
    terms: list[Tensor] = []
    for row in range(score.shape[0]):
        valid_indices = valid[row].nonzero(as_tuple=False).flatten()
        if valid_indices.numel() < 2:
            continue
        row_u = utility[row, valid_indices]
        row_s = score[row, valid_indices]
        gap = row_u[:, None] - row_u[None, :]
        pair_mask = gap >= float(config.pair_gap_threshold)
        if not bool(pair_mask.any().item()):
            continue
        score_gap = row_s[:, None] - row_s[None, :]
        raw_weight = (gap / max(1.0e-8, float(config.gap_weight_scale))).clamp(
            min=1.0,
            max=float(config.gap_weight_clip),
        )
        loss = F.softplus(float(config.pair_margin) - score_gap)
        weighted = loss[pair_mask] * raw_weight[pair_mask]
        terms.append(weighted.mean())
    return torch.stack(terms).mean() if terms else score.sum() * 0.0


def _top_utility_listwise(score: Tensor, utility: Tensor, valid: Tensor, config: SelectorRepairLossConfig) -> Tensor:
    terms: list[Tensor] = []
    temperature = max(1.0e-6, float(config.teacher_temperature))
    for row in range(score.shape[0]):
        valid_indices = valid[row].nonzero(as_tuple=False).flatten()
        if valid_indices.numel() == 0:
            continue
        row_u = utility[row, valid_indices]
        if float(row_u.max().detach().cpu()) <= POSITIVE_EPS:
            continue
        row_s = score[row, valid_indices]
        teacher = F.softmax((row_u - row_u.max()) / temperature, dim=0)
        log_probs = F.log_softmax(row_s, dim=0)
        terms.append(-(teacher * log_probs).sum())
    return torch.stack(terms).mean() if terms else score.sum() * 0.0


def _near_best_loss(score: Tensor, utility: Tensor, valid: Tensor, config: SelectorRepairLossConfig) -> Tensor:
    terms: list[Tensor] = []
    for row in range(score.shape[0]):
        valid_indices = valid[row].nonzero(as_tuple=False).flatten()
        if valid_indices.numel() == 0:
            continue
        row_u = utility[row, valid_indices]
        max_u = row_u.max()
        if float(max_u.detach().cpu()) <= POSITIVE_EPS:
            continue
        near = (row_u >= max_u - float(config.near_best_delta)) & (row_u > POSITIVE_EPS)
        if not bool(near.any().item()):
            continue
        log_probs = F.log_softmax(score[row, valid_indices], dim=0)
        terms.append(-torch.logsumexp(log_probs[near], dim=0))
    return torch.stack(terms).mean() if terms else score.sum() * 0.0


def _sign_calibration(score: Tensor, utility: Tensor, valid: Tensor, config: SelectorRepairLossConfig) -> Tensor:
    strong_pos = valid & (utility >= STRONG_POSITIVE)
    strong_neg = valid & (utility <= STRONG_NEGATIVE)
    terms: list[Tensor] = []
    if bool(strong_pos.any().item()):
        terms.append(F.softplus(float(config.margin_positive) - score[strong_pos]).mean())
    if bool(strong_neg.any().item()):
        terms.append(F.softplus(float(config.margin_negative) + score[strong_neg]).mean())
    return torch.stack(terms).mean() if terms else score.sum() * 0.0


def top_utility_metrics(
    scores: Tensor,
    labels: dict[str, Tensor],
    *,
    ks: tuple[int, ...] = (1, 2, 4, 8),
    positive_eps: float = POSITIVE_EPS,
    near_best_delta: float = 0.03,
) -> dict[str, Any]:
    scores_cpu = scores.detach().to(torch.float32).cpu()
    utility = labels["utility"].detach().to(torch.float32).cpu()
    valid = labels["valid_mask"].detach().cpu()
    positive_gain = labels["positive_gain"].detach().to(torch.float32).cpu()
    recall: dict[int, list[float]] = {k: [] for k in ks}
    near_recall: dict[int, list[float]] = {k: [] for k in ks}
    mass: dict[int, list[float]] = {k: [] for k in ks}
    ranks: list[int] = []
    negative_best_flags: list[float] = []
    strong_positive_negative_flags: list[float] = []
    all_utilities: list[float] = []
    all_scores: list[float] = []
    per_state_rows: list[dict[str, Any]] = []
    for row in range(scores_cpu.shape[0]):
        valid_indices = valid[row].nonzero(as_tuple=False).flatten().tolist()
        if not valid_indices:
            continue
        utilities = [float(utility[row, index].item()) for index in valid_indices]
        row_scores = [float(scores_cpu[row, index].item()) for index in valid_indices]
        gains = [float(positive_gain[row, index].item()) for index in valid_indices]
        max_utility = max(utilities)
        if max_utility <= positive_eps:
            continue
        positive_positions = [pos for pos, value in enumerate(utilities) if value > positive_eps]
        near_positions = {
            pos
            for pos, value in enumerate(utilities)
            if value >= max_utility - near_best_delta and value > positive_eps
        }
        best_pos = min(pos for pos, value in enumerate(utilities) if abs(value - max_utility) <= 1.0e-8)
        order = sorted(range(len(valid_indices)), key=lambda pos: (-row_scores[pos], valid_indices[pos]))
        rank = order.index(best_pos) + 1
        ranks.append(rank)
        negative_best_flags.append(float(row_scores[best_pos] < 0.0))
        for pos in positive_positions:
            if utilities[pos] >= STRONG_POSITIVE:
                strong_positive_negative_flags.append(float(row_scores[pos] < 0.0))
        for pos, value in enumerate(utilities):
            all_utilities.append(value)
            all_scores.append(row_scores[pos])
        total_gain = sum(gains)
        state_out: dict[str, Any] = {
            "row_index": row,
            "raw_teacher_best_rank": rank,
            "raw_teacher_best_score": row_scores[best_pos],
            "raw_teacher_best_utility": utilities[best_pos],
            "raw_teacher_best_negative_score": bool(row_scores[best_pos] < 0.0),
            "near_best_count": len(near_positions),
        }
        for k in ks:
            top = set(order[: min(k, len(order))])
            value = float(best_pos in top)
            near_value = float(bool(near_positions.intersection(top)))
            recall[k].append(value)
            near_recall[k].append(near_value)
            state_out[f"raw_teacher_best_recall@{k}"] = value
            state_out[f"near_best_recall@{k}"] = near_value
            if total_gain > 0:
                covered = sum(gains[pos] for pos in top) / max(1.0e-12, total_gain)
                mass[k].append(covered)
                state_out[f"top_utility_mass@{k}"] = covered
            else:
                state_out[f"top_utility_mass@{k}"] = None
        per_state_rows.append(state_out)
    output = {
        "positive_state_count": len(ranks),
        "raw_teacher_best_rank": distribution(ranks),
        "raw_teacher_best_negative_score_fraction": _mean(negative_best_flags),
        "strong_positive_negative_score_fraction": _mean(strong_positive_negative_flags),
        "raw_utility_vs_signed_score_pearson": _pearson(all_utilities, all_scores),
        "raw_utility_vs_signed_score_spearman": _spearman(all_utilities, all_scores),
        "per_state_rows": per_state_rows,
    }
    for k in ks:
        output[f"raw_teacher_best_recall@{k}"] = mean_std(recall[k])
        output[f"near_best_recall@{k}"] = mean_std(near_recall[k])
        output[f"top_utility_mass@{k}"] = mean_std(mass[k])
    return output


def _mean(values: Sequence[float]) -> float | None:
    return sum(float(value) for value in values) / len(values) if values else None


def _rank_average(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: float(values[index]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and float(values[order[end]]) == float(values[order[cursor]]):
            end += 1
        average = (cursor + 1 + end) / 2.0
        for pos in range(cursor, end):
            ranks[order[pos]] = average
        cursor = end
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return None
    x_values = [x for x, _ in pairs]
    y_values = [y for _, y in pairs]
    if len(set(x_values)) < 2 or len(set(y_values)) < 2:
        return None
    return _pearson(_rank_average(x_values), _rank_average(y_values))


def train_selector_repair_model(
    *,
    model: nn.Module,
    loss_config: SelectorRepairLossConfig,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: Tensor,
    memory_representations: Tensor,
    mu: Tensor,
    seed: int,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    patience: int,
    early_stopping: bool = True,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    model = model.to(device)
    optimizer = torch.optim.AdamW(
        [param for param in model.parameters() if param.requires_grad],
        lr=lr,
        weight_decay=weight_decay,
    )
    best_metric = -float("inf")
    best_epoch = 0
    best_state = copy.deepcopy(model.state_dict())
    bad = 0
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        epoch_losses: list[float] = []
        rng = random.Random(seed * 100_000 + epoch)
        for indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
            batch_rows = [train_rows[index] for index in indices]
            labels = rows_to_tensors(batch_rows, device=device)
            state_batch = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
            memory_batch = memory_representations.to(device=device, dtype=torch.float32)
            payload = model(state_batch, memory_batch)
            loss, _ = selector_repair_loss(payload["residual"], payload["gate"], mu.to(device), labels, loss_config)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([param for param in model.parameters() if param.requires_grad], 1.0)
            optimizer.step()
            epoch_losses.append(float(loss.detach().cpu()))
        metrics = evaluate_selector_repair_model(
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
        metric = selector_selection_score(metrics["validation"])
        history.append(
            {
                "epoch": epoch,
                "train_loss": sum(epoch_losses) / len(epoch_losses) if epoch_losses else None,
                "selection_score": metric,
                "validation_ndcg@4": _metric_mean(metrics["validation"]["full_score"], "ndcg@4"),
                "validation_raw_best_recall@8": _metric_mean(metrics["validation"]["top_utility"], "raw_teacher_best_recall@8"),
                "validation_raw_best_recall@4": _metric_mean(metrics["validation"]["top_utility"], "raw_teacher_best_recall@4"),
            }
        )
        if not early_stopping:
            best_metric = metric
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            continue
        if metric > best_metric + 1.0e-6:
            best_metric = metric
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    output = evaluate_selector_repair_model(
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
    output["loss_config"] = loss_config.as_dict()
    output["best_epoch"] = best_epoch
    output["epochs_ran"] = epoch
    output["selection_score"] = best_metric
    output["history"] = history
    output["state_dict"] = {key: value.detach().cpu() for key, value in model.state_dict().items()}
    return output


def selector_selection_score(validation: dict[str, Any]) -> float:
    top = validation["top_utility"]
    full = validation["full_score"]
    recall8 = _metric_mean(top, "raw_teacher_best_recall@8")
    recall4 = _metric_mean(top, "raw_teacher_best_recall@4")
    ndcg4 = _metric_mean(full, "ndcg@4")
    spearman = float(top.get("raw_utility_vs_signed_score_spearman") or 0.0)
    return recall8 + 0.4 * recall4 + 0.2 * ndcg4 + 0.1 * spearman


def evaluate_selector_repair_model(
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
    mu_device = mu.to(device=device, dtype=torch.float32)
    train_labels = rows_to_tensors(train_rows, device=device)
    validation_labels = rows_to_tensors(validation_rows, device=device)
    train_state = _state_tensor(train_rows, state_representations, device)
    validation_state = _state_tensor(validation_rows, state_representations, device)
    model.eval()
    with torch.no_grad():
        train_payload = model(train_state, memory)
        validation_payload = model(validation_state, memory)
        threshold = choose_gate_threshold(
            train_payload["gate"].detach().cpu(),
            gate_labels(train_labels)[0].detach().cpu(),
            gate_labels(train_labels)[1].detach().cpu(),
        )
        train_scores = mu_device.unsqueeze(0) + train_payload["residual"]
        validation_scores = mu_device.unsqueeze(0) + validation_payload["residual"]
        output = {
            "train": {
                "full_score": evaluate_scores(train_scores, train_labels),
                "top_utility": _strip_per_state(top_utility_metrics(train_scores, train_labels)),
                "gate": gate_metrics(train_payload["gate"], train_labels, threshold=threshold),
            },
            "validation": {
                "full_score": evaluate_scores(validation_scores, validation_labels),
                "top_utility": _strip_per_state(top_utility_metrics(validation_scores, validation_labels)),
                "top_utility_per_state": top_utility_metrics(validation_scores, validation_labels)["per_state_rows"],
                "residual_stats": residual_stats(validation_payload["residual"], mu_device, validation_labels),
                "gate": gate_metrics(validation_payload["gate"], validation_labels, threshold=threshold),
                "per_state_full_score": per_state_metric_values(validation_scores, validation_labels),
            },
        }
        if "q" in validation_payload and validation_payload["q"].numel() and "k" in validation_payload and validation_payload["k"].numel():
            shuffled_state = _state_control(validation_state, "shuffled_state", seed=seed)
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
            output["controls"] = {}
            for control in ("shuffled_state", "mean_state", "zero_state"):
                state_variant = _state_control(validation_state, control, seed=seed)
                payload = model(state_variant, memory)
                scores = mu_device.unsqueeze(0) + payload["residual"]
                output["controls"][control] = {
                    "full_score": evaluate_scores(scores, validation_labels),
                    "top_utility": _strip_per_state(top_utility_metrics(scores, validation_labels)),
                    "per_state_full_score": per_state_metric_values(scores, validation_labels),
                }
            output["control_deltas"] = control_deltas(output["validation"], output["controls"])
    return output


def _strip_per_state(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "per_state_rows"}


def _state_tensor(rows: list[dict[str, Any]], state_representations: Tensor, device: torch.device) -> Tensor:
    indices = torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long)
    return state_representations[indices].to(device=device, dtype=torch.float32)


def _state_control(state: Tensor, control: str, *, seed: int) -> Tensor:
    if control == "correct":
        return state
    if control == "shuffled_state":
        if state.shape[0] <= 1:
            return state
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + 40_000)
        order = torch.randperm(state.shape[0], generator=generator).to(state.device)
        if torch.equal(order, torch.arange(state.shape[0], device=state.device)):
            order = torch.roll(order, shifts=1)
        return state.index_select(0, order)
    if control == "mean_state":
        return state.mean(dim=0, keepdim=True).expand_as(state)
    if control == "zero_state":
        return torch.zeros_like(state)
    raise ValueError(f"unknown state control: {control}")


def control_deltas(validation: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for name, control in controls.items():
        item = {}
        for metric in ("ndcg@4", "positive_mass_coverage@4"):
            item[metric] = _metric_mean(validation["full_score"], metric) - _metric_mean(control["full_score"], metric)
        for metric in ("raw_teacher_best_recall@4", "raw_teacher_best_recall@8", "top_utility_mass@4", "top_utility_mass@8"):
            item[metric] = _metric_mean(validation["top_utility"], metric) - _metric_mean(control["top_utility"], metric)
        correct_corr = float(validation["top_utility"].get("raw_utility_vs_signed_score_spearman") or 0.0)
        control_corr = float(control["top_utility"].get("raw_utility_vs_signed_score_spearman") or 0.0)
        item["raw_utility_vs_signed_score_spearman"] = correct_corr - control_corr
        output[name] = item
    return output


def _metric_mean(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, dict):
        mean = value.get("mean")
        return float(mean) if mean is not None else 0.0
    if value is None:
        return 0.0
    return float(value)


def summarize_selector_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    full_metrics = (
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
    )
    top_metrics = (
        "raw_teacher_best_recall@1",
        "raw_teacher_best_recall@2",
        "raw_teacher_best_recall@4",
        "raw_teacher_best_recall@8",
        "near_best_recall@1",
        "near_best_recall@2",
        "near_best_recall@4",
        "near_best_recall@8",
        "top_utility_mass@4",
        "top_utility_mass@8",
    )
    output["full_score"] = {
        metric: mean_std(_metric_mean(run["validation"]["full_score"], metric) for run in runs)
        for metric in full_metrics
    }
    output["top_utility"] = {
        metric: mean_std(_metric_mean(run["validation"]["top_utility"], metric) for run in runs)
        for metric in top_metrics
    }
    output["top_utility"]["raw_teacher_best_negative_score_fraction"] = mean_std(
        run["validation"]["top_utility"].get("raw_teacher_best_negative_score_fraction")
        for run in runs
        if run["validation"]["top_utility"].get("raw_teacher_best_negative_score_fraction") is not None
    )
    output["top_utility"]["strong_positive_negative_score_fraction"] = mean_std(
        run["validation"]["top_utility"].get("strong_positive_negative_score_fraction")
        for run in runs
        if run["validation"]["top_utility"].get("strong_positive_negative_score_fraction") is not None
    )
    output["top_utility"]["raw_utility_vs_signed_score_spearman"] = mean_std(
        run["validation"]["top_utility"].get("raw_utility_vs_signed_score_spearman")
        for run in runs
        if run["validation"]["top_utility"].get("raw_utility_vs_signed_score_spearman") is not None
    )
    output["top_utility"]["raw_utility_vs_signed_score_pearson"] = mean_std(
        run["validation"]["top_utility"].get("raw_utility_vs_signed_score_pearson")
        for run in runs
        if run["validation"]["top_utility"].get("raw_utility_vs_signed_score_pearson") is not None
    )
    output["top_utility"]["raw_teacher_best_rank"] = _rank_summary_from_runs(runs)
    for control in ("shuffled_state", "mean_state", "zero_state"):
        output[f"correct_minus_{control}"] = {
            metric: mean_std(
                run.get("control_deltas", {}).get(control, {}).get(metric)
                for run in runs
                if run.get("control_deltas", {}).get(control, {}).get(metric) is not None
            )
            for metric in (
                "ndcg@4",
                "positive_mass_coverage@4",
                "raw_teacher_best_recall@4",
                "raw_teacher_best_recall@8",
                "top_utility_mass@4",
                "top_utility_mass@8",
                "raw_utility_vs_signed_score_spearman",
            )
        }
    output["residual_stats"] = {
        metric: mean_std(
            run["validation"]["residual_stats"].get(metric)
            for run in runs
            if run["validation"].get("residual_stats", {}).get(metric) is not None
        )
        for metric in ("residual_mse", "residual_huber", "residual_correlation")
    }
    output["geometry"] = summarize_repair_geometry(runs)
    output["selection_score"] = mean_std(run.get("selection_score") for run in runs if run.get("selection_score") is not None)
    output["best_epoch"] = mean_std(run.get("best_epoch") for run in runs if run.get("best_epoch") is not None)
    return output


def _rank_summary_from_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ranks: list[float] = []
    for run in runs:
        for row in run["validation"].get("top_utility_per_state", []):
            ranks.append(float(row["raw_teacher_best_rank"]))
    return distribution(ranks)


def summarize_repair_geometry(runs: list[dict[str, Any]]) -> dict[str, Any]:
    paths = {
        "interaction_variance": ("interaction_variance",),
        "prior_variance": ("prior_variance",),
        "correct_vs_shuffled_valid_interaction_abs_delta": ("correct_vs_shuffled_valid_interaction_abs_delta",),
        "q_centered_effective_rank": ("q_centered_spectrum", "effective_rank"),
        "k_centered_effective_rank": ("k_centered_spectrum", "effective_rank"),
        "q_norm_mean": ("q_norm", "mean"),
        "k_norm_mean": ("k_norm", "mean"),
        "q_pairwise_cosine_mean": ("q_pairwise_cosine", "mean"),
        "k_pairwise_cosine_mean": ("k_pairwise_cosine", "mean"),
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


def save_selector_checkpoint(
    path: Any,
    *,
    run: dict[str, Any],
    loss_config: SelectorRepairLossConfig,
    seed: int,
    prior_kind: str,
    source_commit: str | None,
    extra: dict[str, Any] | None = None,
) -> str:
    checkpoint_path = path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    state_dict = run.pop("state_dict")
    payload = {
        "format": SELECTOR_REPAIR_VERSION,
        "model_kind": "signed_core_field_r128",
        "prior_kind": prior_kind,
        "seed": int(seed),
        "best_epoch": run.get("best_epoch"),
        "epochs_ran": run.get("epochs_ran"),
        "loss_config": loss_config.as_dict(),
        "state_dict": state_dict,
        "source_commit": source_commit,
    }
    if extra:
        payload.update(extra)
    tmp_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
    torch.save(payload, tmp_path)
    tmp_path.replace(checkpoint_path)
    run["checkpoint"] = str(checkpoint_path)
    return str(checkpoint_path)


def make_signed_selector(state_dim: int, memory_dim: int) -> SignedResidualField:
    return SignedResidualField(state_dim, memory_dim, rank=128, hidden_dim=256, dropout=0.05)

