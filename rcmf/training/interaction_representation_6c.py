from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import copy
import math
import random
from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.oracle_convergence_5fa import custom_huber
from rcmf.training.pair_grounding_5d import spearman
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    DenseTower,
    deterministic_derangement,
    utility_category,
)


INTERACTION_REPAIR_VERSION = "interaction_residual_representation_6c_v1"
DECOMPOSITION_VERSION = "cell_a_two_way_main_effect_decomposition_6c_v1"
REVISED_METRICS_VERSION = "within_state_interaction_metrics_6c_v1"
REVISED_GATE_VERSION = "memory_specific_interaction_gate_6c_v1"


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return {"count": 0}

    def quantile(fraction: float) -> float:
        position = int(math.floor((len(ordered) - 1) * fraction + 0.5))
        return ordered[position]

    mean = sum(ordered) / len(ordered)
    variance = sum((value - mean) ** 2 for value in ordered) / len(ordered)
    return {
        "count": len(ordered),
        "mean": mean,
        "std": math.sqrt(variance),
        "min": ordered[0],
        "p05": quantile(0.05),
        "p25": quantile(0.25),
        "median": quantile(0.50),
        "p75": quantile(0.75),
        "p95": quantile(0.95),
        "max": ordered[-1],
    }


def majority_sign_baseline(
    rows: Sequence[Mapping[str, Any]], *, neutral_epsilon: float = 0.01
) -> dict[str, Any]:
    def value(row: Mapping[str, Any]) -> float:
        if "text_utility" in row:
            return float(row["text_utility"])
        return float(row["u_text"])

    positive = sum(value(row) > neutral_epsilon for row in rows)
    negative = sum(value(row) < -neutral_epsilon for row in rows)
    neutral = len(rows) - positive - negative
    denominator = positive + negative
    always_positive = positive / denominator if denominator else None
    always_negative = negative / denominator if denominator else None
    return {
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "non_neutral": denominator,
        "always_positive_accuracy": always_positive,
        "always_negative_accuracy": always_negative,
        "majority_sign_accuracy": (
            max(positive, negative) / denominator if denominator else None
        ),
        "majority_sign": "positive" if positive >= negative else "negative",
    }


def _weighted_center(
    effects: dict[str, float], counts: Mapping[str, int]
) -> tuple[dict[str, float], float]:
    total = sum(int(counts[key]) for key in effects)
    center = (
        sum(float(effects[key]) * int(counts[key]) for key in effects) / total
        if total
        else 0.0
    )
    return {key: float(value) - center for key, value in effects.items()}, center


def _spectrum_summary(matrix: Tensor) -> dict[str, Any]:
    values = torch.linalg.svdvals(matrix.to(torch.float64)).detach().cpu()
    nonzero = values[values > 1.0e-12]
    if not len(nonzero):
        return {
            "shape": list(matrix.shape),
            "rank": 0,
            "effective_rank": 0.0,
            "stable_rank": 0.0,
            "singular_values": [],
        }
    probabilities = nonzero / nonzero.sum()
    effective_rank = float(torch.exp(-(probabilities * probabilities.log()).sum()))
    stable_rank = float((nonzero.square().sum() / nonzero.max().square()).item())
    return {
        "shape": list(matrix.shape),
        "rank": int(nonzero.numel()),
        "effective_rank": effective_rank,
        "stable_rank": stable_rank,
        "singular_values": [float(value) for value in values.tolist()],
        "top_singular_fraction": float(nonzero[0] / nonzero.sum()),
    }


def fit_two_way_decomposition(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_iterations: int = 1000,
    tolerance: float = 1.0e-10,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Two-way decomposition requires rows")
    states = sorted({str(row["state_example_id"]) for row in rows})
    transitions = sorted({str(row["transition_id"]) for row in rows})
    by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_transition: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_state[str(row["state_example_id"])].append(row)
        by_transition[str(row["transition_id"])].append(row)
    state_counts = {key: len(value) for key, value in by_state.items()}
    transition_counts = {key: len(value) for key, value in by_transition.items()}
    utilities = [float(row["text_utility"]) for row in rows]
    mu = sum(utilities) / len(utilities)
    state_effects = {key: 0.0 for key in states}
    transition_effects = {key: 0.0 for key in transitions}
    converged = False
    movement = float("inf")
    iteration = 0
    for iteration in range(1, int(max_iterations) + 1):
        previous_mu = mu
        previous_state = dict(state_effects)
        previous_transition = dict(transition_effects)
        state_effects = {
            state: sum(
                float(row["text_utility"])
                - mu
                - transition_effects[str(row["transition_id"])]
                for row in selected
            )
            / len(selected)
            for state, selected in by_state.items()
        }
        state_effects, shift = _weighted_center(state_effects, state_counts)
        mu += shift
        transition_effects = {
            transition: sum(
                float(row["text_utility"])
                - mu
                - state_effects[str(row["state_example_id"])]
                for row in selected
            )
            / len(selected)
            for transition, selected in by_transition.items()
        }
        transition_effects, shift = _weighted_center(
            transition_effects, transition_counts
        )
        mu += shift
        movement = max(
            abs(mu - previous_mu),
            max(abs(state_effects[key] - previous_state[key]) for key in states),
            max(
                abs(transition_effects[key] - previous_transition[key])
                for key in transitions
            ),
        )
        if movement < float(tolerance):
            converged = True
            break

    predictions = []
    state_only_predictions = []
    transition_only_predictions = []
    residuals = []
    for row in rows:
        state = str(row["state_example_id"])
        transition = str(row["transition_id"])
        value = float(row["text_utility"])
        state_only_predictions.append(mu + state_effects[state])
        transition_only_predictions.append(mu + transition_effects[transition])
        prediction = mu + state_effects[state] + transition_effects[transition]
        predictions.append(prediction)
        residuals.append(value - prediction)
    total_ss = sum((value - sum(utilities) / len(utilities)) ** 2 for value in utilities)

    def r_squared(predicted: Sequence[float]) -> float | None:
        if total_ss <= 0.0:
            return None
        error = sum((value - estimate) ** 2 for value, estimate in zip(utilities, predicted))
        return 1.0 - error / total_ss

    state_position = {value: index for index, value in enumerate(states)}
    transition_position = {value: index for index, value in enumerate(transitions)}
    utility_matrix = torch.empty(len(states), len(transitions), dtype=torch.float64)
    residual_matrix = torch.zeros_like(utility_matrix)
    observed = torch.zeros_like(utility_matrix, dtype=torch.bool)
    for state in states:
        for transition in transitions:
            utility_matrix[state_position[state], transition_position[transition]] = (
                mu + state_effects[state] + transition_effects[transition]
            )
    for row, residual in zip(rows, residuals):
        state_index = state_position[str(row["state_example_id"])]
        transition_index = transition_position[str(row["transition_id"])]
        utility_matrix[state_index, transition_index] = float(row["text_utility"])
        residual_matrix[state_index, transition_index] = float(residual)
        observed[state_index, transition_index] = True

    total_variance = sum((value - sum(utilities) / len(utilities)) ** 2 for value in utilities) / len(utilities)
    state_component = [state_effects[str(row["state_example_id"])] for row in rows]
    transition_component = [
        transition_effects[str(row["transition_id"])] for row in rows
    ]
    return {
        "format": DECOMPOSITION_VERSION,
        "cell": CELL_A,
        "row_count": len(rows),
        "state_count": len(states),
        "transition_count": len(transitions),
        "mu": mu,
        "state_effects": state_effects,
        "transition_effects": transition_effects,
        "state_counts": state_counts,
        "transition_counts": transition_counts,
        "iterations": iteration,
        "converged": converged,
        "final_max_parameter_movement": movement,
        "total_utility_variance": total_variance,
        "state_component_variance": _distribution(state_component)["std"] ** 2,
        "transition_component_variance": _distribution(transition_component)["std"] ** 2,
        "residual_interaction_variance": _distribution(residuals)["std"] ** 2,
        "state_only_variance_explained_r2": r_squared(state_only_predictions),
        "transition_only_variance_explained_r2": r_squared(
            transition_only_predictions
        ),
        "additive_main_effect_variance_explained_r2": r_squared(predictions),
        "raw_utility": _distribution(utilities),
        "residual": _distribution(residuals),
        "utility_matrix_missing_count": int((~observed).sum().item()),
        "utility_matrix_imputation": "cell_a_additive_main_effect_prediction",
        "residual_matrix_missing_imputation": "zero_interaction_residual",
        "utility_matrix_spectrum": _spectrum_summary(utility_matrix),
        "residual_matrix_spectrum": _spectrum_summary(residual_matrix),
    }


class MainEffectHeads(nn.Module):
    def __init__(
        self,
        *,
        state_dim: int,
        transition_dim: int,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.state_head = DenseTower(
            state_dim, 1, hidden_dim=hidden_dim, dropout=dropout
        )
        self.transition_head = DenseTower(
            transition_dim, 1, hidden_dim=hidden_dim, dropout=dropout
        )

    def state(self, representation: Tensor) -> Tensor:
        return self.state_head(representation).squeeze(-1)

    def transition(self, representation: Tensor) -> Tensor:
        return self.transition_head(representation).squeeze(-1)


def train_main_effect_heads(
    *,
    model: MainEffectHeads,
    decomposition: Mapping[str, Any],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    epochs: int,
    learning_rate: float,
    weight_decay: float,
    huber_delta: float,
    seed: int,
    device: torch.device,
) -> dict[str, Any]:
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device).train()
    state_ids = sorted(str(value) for value in decomposition["state_effects"])
    transition_ids = sorted(
        str(value) for value in decomposition["transition_effects"]
    )
    state_x = torch.stack(
        [state_representations[state_position[value]] for value in state_ids]
    ).to(device=device, dtype=torch.float32)
    transition_x = torch.stack(
        [
            transition_representations[transition_position[value]]
            for value in transition_ids
        ]
    ).to(device=device, dtype=torch.float32)
    state_y = torch.tensor(
        [float(decomposition["state_effects"][value]) for value in state_ids],
        device=device,
    )
    transition_y = torch.tensor(
        [
            float(decomposition["transition_effects"][value])
            for value in transition_ids
        ],
        device=device,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay)
    )
    history = []
    for epoch in range(1, int(epochs) + 1):
        state_prediction = model.state(state_x)
        transition_prediction = model.transition(transition_x)
        state_loss = custom_huber(
            state_prediction - state_y, delta=float(huber_delta)
        ).mean()
        transition_loss = custom_huber(
            transition_prediction - transition_y, delta=float(huber_delta)
        ).mean()
        loss = state_loss + transition_loss
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu()
        )
        optimizer.step()
        if epoch == 1 or epoch % 10 == 0 or epoch == int(epochs):
            history.append(
                {
                    "epoch": epoch,
                    "loss": float(loss.detach().cpu()),
                    "state_huber": float(state_loss.detach().cpu()),
                    "transition_huber": float(transition_loss.detach().cpu()),
                    "gradient_norm": gradient_norm,
                }
            )
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return {
        "epochs": int(epochs),
        "optimizer_updates": int(epochs),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


class DecomposedInteractionPredictor(nn.Module):
    KINDS = {
        "decomposed_additive",
        "decomposed_signed_bilinear",
        "decomposed_concat_interaction",
    }

    def __init__(
        self,
        kind: str,
        *,
        main_effects: MainEffectHeads,
        mu: float,
        state_dim: int,
        transition_dim: int,
        hidden_dim: int = 256,
        interaction_dim: int = 128,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        if kind not in self.KINDS:
            raise ValueError(f"Unknown decomposed interaction kind: {kind}")
        self.kind = kind
        self.main_effects = copy.deepcopy(main_effects)
        for parameter in self.main_effects.parameters():
            parameter.requires_grad_(False)
        self.register_buffer("mu", torch.tensor(float(mu), dtype=torch.float32))
        self.interaction_dim = int(interaction_dim)
        if kind == "decomposed_signed_bilinear":
            self.state_interaction = DenseTower(
                state_dim,
                interaction_dim,
                hidden_dim=hidden_dim,
                dropout=dropout,
            )
            self.transition_interaction = DenseTower(
                transition_dim,
                interaction_dim,
                hidden_dim=hidden_dim,
                dropout=dropout,
            )
        elif kind == "decomposed_concat_interaction":
            if state_dim != transition_dim:
                raise ValueError("Concat interaction requires equal dimensions")
            self.interaction_head = DenseTower(
                state_dim * 3, 1, hidden_dim=hidden_dim, dropout=dropout
            )

    def interaction(self, state: Tensor, transition: Tensor) -> Tensor:
        if self.kind == "decomposed_additive":
            return torch.zeros(state.shape[0], device=state.device, dtype=torch.float32)
        if self.kind == "decomposed_signed_bilinear":
            query = self.state_interaction(state)
            key = self.transition_interaction(transition)
            return (query * key).sum(dim=-1) / math.sqrt(self.interaction_dim)
        features = torch.cat([state, transition, state * transition], dim=-1)
        return self.interaction_head(features).squeeze(-1)

    def components(self, state: Tensor, transition: Tensor) -> dict[str, Tensor]:
        state_main = self.main_effects.state(state)
        transition_main = self.main_effects.transition(transition)
        interaction = self.interaction(state, transition)
        return {
            "mu": self.mu.expand_as(interaction),
            "state_main": state_main,
            "transition_main": transition_main,
            "interaction": interaction,
            "score": self.mu + state_main + transition_main + interaction,
        }

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        return self.components(state, transition)["score"]


def _row_tensors(
    rows: Sequence[Mapping[str, Any]],
    *,
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
) -> tuple[Tensor, Tensor, Tensor]:
    state = torch.stack(
        [state_representations[state_position[str(row["state_example_id"])]] for row in rows]
    ).to(device=device, dtype=torch.float32)
    transition = torch.stack(
        [
            transition_representations[
                transition_position[str(row["transition_id"])]
            ]
            for row in rows
        ]
    ).to(device=device, dtype=torch.float32)
    utility = torch.tensor(
        [float(row["text_utility"]) for row in rows],
        device=device,
        dtype=torch.float32,
    )
    return state, transition, utility


def _exact_training_residuals(
    rows: Sequence[Mapping[str, Any]], decomposition: Mapping[str, Any]
) -> Tensor:
    return torch.tensor(
        [
            float(row["text_utility"])
            - float(decomposition["mu"])
            - float(decomposition["state_effects"][str(row["state_example_id"])])
            - float(
                decomposition["transition_effects"][str(row["transition_id"])]
            )
            for row in rows
        ],
        dtype=torch.float32,
    )


def _group_positions(rows: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row["state_example_id"])].append(index)
    return [grouped[key] for key in sorted(grouped)]


def interaction_objective(
    *,
    score: Tensor,
    interaction: Tensor,
    utility: Tensor,
    residual_target: Tensor,
    state_groups: Sequence[Sequence[int]],
    residual_huber_delta: float,
    utility_huber_delta: float,
    teacher_temperature: float,
    student_temperature: float,
    pair_gap_threshold: float,
    pair_gap_clip: float,
    loss_weights: Mapping[str, float],
) -> tuple[Tensor, dict[str, Tensor]]:
    residual_huber = custom_huber(
        interaction - residual_target.to(interaction.device),
        delta=float(residual_huber_delta),
    ).mean()
    listwise_values = []
    pairwise_values = []
    for positions in state_groups:
        index = torch.tensor(positions, device=score.device, dtype=torch.long)
        group_utility = utility[index]
        group_score = score[index]
        teacher = torch.softmax(
            (group_utility - group_utility.max()) / float(teacher_temperature), dim=0
        )
        student_log = torch.log_softmax(
            group_score / float(student_temperature), dim=0
        )
        listwise_values.append(-(teacher * student_log).sum())
        gaps = group_utility[:, None] - group_utility[None, :]
        selected = gaps >= float(pair_gap_threshold)
        if bool(selected.any()):
            score_gaps = group_score[:, None] - group_score[None, :]
            weights = (gaps / float(pair_gap_threshold)).clamp(
                min=1.0, max=float(pair_gap_clip)
            )
            pairwise_values.append(
                (F.softplus(-score_gaps[selected]) * weights[selected]).mean()
            )
    listwise = torch.stack(listwise_values).mean()
    pairwise = (
        torch.stack(pairwise_values).mean()
        if pairwise_values
        else torch.zeros((), device=score.device)
    )
    raw_huber = custom_huber(
        score - utility, delta=float(utility_huber_delta)
    ).mean()
    components = {
        "residual_huber": residual_huber,
        "state_listwise": listwise,
        "gap_pairwise": pairwise,
        "raw_utility_auxiliary": raw_huber,
    }
    total = sum(float(loss_weights[key]) * value for key, value in components.items())
    return total, components


def train_decomposed_interaction(
    *,
    model: DecomposedInteractionPredictor,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    epochs: int,
    settings: Mapping[str, Any],
    seed: int,
    device: torch.device,
) -> dict[str, Any]:
    if model.kind == "decomposed_additive":
        return {
            "epochs": 0,
            "optimizer_updates": 0,
            "history": [],
            "optimizer_state_dict": {},
        }
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device).train()
    model.main_effects.eval()
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(
        trainable,
        lr=float(settings["learning_rate"]),
        weight_decay=float(settings["weight_decay"]),
    )
    state, transition, utility = _row_tensors(
        rows,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        device=device,
    )
    residual_target = _exact_training_residuals(rows, decomposition).to(device)
    groups = _group_positions(rows)
    history = []
    for epoch in range(1, int(epochs) + 1):
        components = model.components(state, transition)
        loss, losses = interaction_objective(
            score=components["score"],
            interaction=components["interaction"],
            utility=utility,
            residual_target=residual_target,
            state_groups=groups,
            residual_huber_delta=float(settings["residual_huber_delta"]),
            utility_huber_delta=float(settings["utility_huber_delta"]),
            teacher_temperature=float(settings["teacher_temperature"]),
            student_temperature=float(settings["student_temperature"]),
            pair_gap_threshold=float(settings["pair_gap_threshold"]),
            pair_gap_clip=float(settings["pair_gap_clip"]),
            loss_weights=settings["losses"],
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(
            torch.nn.utils.clip_grad_norm_(trainable, 1.0).detach().cpu()
        )
        optimizer.step()
        if epoch == 1 or epoch % 10 == 0 or epoch == int(epochs):
            history.append(
                {
                    "epoch": epoch,
                    "total_loss": float(loss.detach().cpu()),
                    **{
                        key: float(value.detach().cpu())
                        for key, value in losses.items()
                    },
                    "gradient_norm": gradient_norm,
                }
            )
    model.eval()
    return {
        "epochs": int(epochs),
        "optimizer_updates": int(epochs),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


@torch.no_grad()
def predict_decomposed_rows(
    *,
    model: DecomposedInteractionPredictor,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
    control: str = "correct",
    seed: int = 0,
) -> list[dict[str, Any]]:
    allowed = {
        "correct",
        "shuffled_state",
        "shuffled_transition",
        "both_shuffled",
        "mean_state",
        "mean_transition",
        "zero_interaction",
    }
    if control not in allowed:
        raise ValueError(f"Unknown decomposed control: {control}")
    state_map = {key: key for key in state_position}
    transition_map = {key: key for key in transition_position}
    if control in {"shuffled_state", "both_shuffled"}:
        state_map.update(
            deterministic_derangement(
                [str(row["state_example_id"]) for row in rows],
                seed=seed,
                namespace=f"6c-{control}-state",
            )
        )
    if control in {"shuffled_transition", "both_shuffled"}:
        transition_map.update(
            deterministic_derangement(
                [str(row["transition_id"]) for row in rows],
                seed=seed,
                namespace=f"6c-{control}-transition",
            )
        )
    state_all = state_representations.to(device=device, dtype=torch.float32)
    transition_all = transition_representations.to(device=device, dtype=torch.float32)
    mean_state = state_all.mean(dim=0)
    mean_transition = transition_all.mean(dim=0)
    model.to(device).eval()
    output: list[dict[str, Any]] = []
    for start in range(0, len(rows), 512):
        block = rows[start : start + 512]
        state = torch.stack(
            [
                mean_state
                if control == "mean_state"
                else state_all[
                    state_position[state_map[str(row["state_example_id"])]]
                ]
                for row in block
            ]
        )
        transition = torch.stack(
            [
                mean_transition
                if control == "mean_transition"
                else transition_all[
                    transition_position[
                        transition_map[str(row["transition_id"])]
                    ]
                ]
                for row in block
            ]
        )
        values = model.components(state, transition)
        if control == "zero_interaction":
            values["interaction"] = torch.zeros_like(values["interaction"])
            values["score"] = (
                values["mu"] + values["state_main"] + values["transition_main"]
            )

        correct_state = torch.stack(
            [state_all[state_position[str(row["state_example_id"])]] for row in block]
        )
        correct_transition = torch.stack(
            [
                transition_all[transition_position[str(row["transition_id"])]]
                for row in block
            ]
        )
        target_state_main = model.main_effects.state(correct_state)
        target_transition_main = model.main_effects.transition(correct_transition)
        for index, row in enumerate(block):
            state_id = str(row["state_example_id"])
            transition_id = str(row["transition_id"])
            state_effect = float(
                decomposition["state_effects"].get(
                    state_id, float(target_state_main[index].cpu())
                )
            )
            transition_effect = float(
                decomposition["transition_effects"].get(
                    transition_id, float(target_transition_main[index].cpu())
                )
            )
            residual_target = (
                float(row["text_utility"])
                - float(decomposition["mu"])
                - state_effect
                - transition_effect
            )
            output.append(
                {
                    "pair_id": str(row["pair_id"]),
                    "state_example_id": state_id,
                    "state_task_id": str(row["state_task_id"]),
                    "transition_id": transition_id,
                    "transition_parent_id": str(row["transition_parent_id"]),
                    "cell": str(row["cell"]),
                    "utility_category": str(row["utility_category"]),
                    "u_text": float(row["text_utility"]),
                    "u_predicted": float(values["score"][index].cpu()),
                    "residual_target": residual_target,
                    "residual_predicted": float(values["interaction"][index].cpu()),
                    "state_main_target": state_effect,
                    "transition_main_target": transition_effect,
                    "state_main_predicted": float(values["state_main"][index].cpu()),
                    "transition_main_predicted": float(
                        values["transition_main"][index].cpu()
                    ),
                    "control": control,
                }
            )
    return output


def _dcg(relevance: Sequence[float], k: int) -> float:
    return sum(
        (2.0 ** float(value) - 1.0) / math.log2(index + 2.0)
        for index, value in enumerate(relevance[: int(k)])
    )


def per_state_ranking_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    ranking_ks: Sequence[int] = (1, 4, 8),
    neutral_epsilon: float = 0.01,
    best_tie_tolerance: float = 1.0e-8,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_example_id"])].append(row)
    output = []
    for state_id in sorted(grouped):
        selected = grouped[state_id]
        teacher = [float(row["u_text"]) for row in selected]
        predicted = [float(row["u_predicted"]) for row in selected]
        order = sorted(
            range(len(selected)),
            key=lambda index: (
                -predicted[index],
                str(selected[index]["transition_id"]),
            ),
        )
        gains = [max(value - float(neutral_epsilon), 0.0) for value in teacher]
        total_gain = sum(gains)
        ideal = sorted(gains, reverse=True)
        maximum = max(teacher)
        best = {
            index
            for index, value in enumerate(teacher)
            if maximum - value <= float(best_tie_tolerance)
        }
        pairwise = []
        positive = [
            index for index, value in enumerate(teacher) if value > neutral_epsilon
        ]
        negative = [
            index for index, value in enumerate(teacher) if value < -neutral_epsilon
        ]
        for positive_index in positive:
            for negative_index in negative:
                pairwise.append(predicted[positive_index] > predicted[negative_index])
        state = {
            "state_example_id": state_id,
            "state_task_id": str(selected[0]["state_task_id"]),
            "count": len(selected),
            "spearman": spearman(teacher, predicted),
            "positive_vs_negative_pairwise_accuracy": (
                sum(pairwise) / len(pairwise) if pairwise else None
            ),
            "has_positive_gain": total_gain > 0.0,
            "positive_count": len(positive),
            "negative_count": len(negative),
        }
        for k in ranking_ks:
            top = order[: int(k)]
            state[f"best_recall@{k}"] = (
                float(bool(best.intersection(top))) if total_gain > 0.0 else None
            )
            state[f"ndcg@{k}"] = (
                _dcg([gains[index] for index in top], int(k))
                / _dcg(ideal, int(k))
                if total_gain > 0.0 and _dcg(ideal, int(k)) > 0.0
                else None
            )
            state[f"positive_mass_coverage@{k}"] = (
                sum(gains[index] for index in top) / total_gain
                if total_gain > 0.0
                else None
            )
        output.append(state)
    return output


def summarize_revised_predictions(
    rows: Sequence[Mapping[str, Any]],
    *,
    ranking_ks: Sequence[int] = (1, 4, 8),
    neutral_epsilon: float = 0.01,
    best_tie_tolerance: float = 1.0e-8,
    huber_delta: float = 0.1,
) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    teacher = [float(row["u_text"]) for row in rows]
    predicted = [float(row["u_predicted"]) for row in rows]
    errors = [estimate - target for target, estimate in zip(teacher, predicted)]
    residual_teacher = [float(row.get("residual_target", 0.0)) for row in rows]
    residual_predicted = [
        float(row.get("residual_predicted", 0.0)) for row in rows
    ]
    per_state = per_state_ranking_metrics(
        rows,
        ranking_ks=ranking_ks,
        neutral_epsilon=neutral_epsilon,
        best_tie_tolerance=best_tie_tolerance,
    )
    ranking_names = ["spearman", "positive_vs_negative_pairwise_accuracy"]
    for k in ranking_ks:
        ranking_names.extend(
            [f"ndcg@{k}", f"best_recall@{k}", f"positive_mass_coverage@{k}"]
        )
    ranking = {
        name: mean_std(
            float(row[name]) for row in per_state if row.get(name) is not None
        )
        for name in ranking_names
    }
    non_neutral = [
        row
        for row in rows
        if abs(float(row["u_text"])) > float(neutral_epsilon)
    ]
    sign = [
        (float(row["u_text"]) > 0.0) == (float(row["u_predicted"]) > 0.0)
        for row in non_neutral
    ]
    return {
        "format": REVISED_METRICS_VERSION,
        "count": len(rows),
        "state_count": len(per_state),
        "pooled_raw_pearson": _pearson(teacher, predicted),
        "pooled_raw_spearman": spearman(teacher, predicted),
        "raw_mae": sum(abs(value) for value in errors) / len(errors),
        "raw_mse": sum(value * value for value in errors) / len(errors),
        "raw_huber": sum(
            float(custom_huber(torch.tensor(value), delta=float(huber_delta)))
            for value in errors
        )
        / len(errors),
        "positive_negative_sign_agreement": sum(sign) / len(sign) if sign else None,
        "majority_sign_baseline": majority_sign_baseline(
            rows, neutral_epsilon=neutral_epsilon
        ),
        "interaction_residual_pearson": _pearson(
            residual_teacher, residual_predicted
        ),
        "interaction_residual_spearman": spearman(
            residual_teacher, residual_predicted
        ),
        "residual_target": _distribution(residual_teacher),
        "residual_predicted": _distribution(residual_predicted),
        "per_state": ranking,
        "per_state_rows": per_state,
    }


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    index = int(math.floor((len(ordered) - 1) * float(fraction)))
    return ordered[index]


def task_grouped_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    samples: int,
    seed: int,
    metric_settings: Mapping[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_task_id"])].append(row)
    tasks = sorted(grouped)
    rng = random.Random(int(seed))
    metric_paths = {
        "pooled_raw_spearman": lambda summary: summary["pooled_raw_spearman"],
        "raw_huber": lambda summary: summary["raw_huber"],
        "interaction_residual_pearson": lambda summary: summary[
            "interaction_residual_pearson"
        ],
        "interaction_residual_spearman": lambda summary: summary[
            "interaction_residual_spearman"
        ],
        "mean_per_state_spearman": lambda summary: summary["per_state"][
            "spearman"
        ]["mean"],
        "mean_per_state_ndcg@1": lambda summary: summary["per_state"]["ndcg@1"][
            "mean"
        ],
        "mean_per_state_ndcg@4": lambda summary: summary["per_state"]["ndcg@4"][
            "mean"
        ],
        "mean_per_state_ndcg@8": lambda summary: summary["per_state"]["ndcg@8"][
            "mean"
        ],
        "mean_best_recall@1": lambda summary: summary["per_state"][
            "best_recall@1"
        ]["mean"],
        "mean_best_recall@4": lambda summary: summary["per_state"][
            "best_recall@4"
        ]["mean"],
        "mean_best_recall@8": lambda summary: summary["per_state"][
            "best_recall@8"
        ]["mean"],
        "mean_positive_mass_coverage@1": lambda summary: summary["per_state"][
            "positive_mass_coverage@1"
        ]["mean"],
        "mean_positive_mass_coverage@4": lambda summary: summary["per_state"][
            "positive_mass_coverage@4"
        ]["mean"],
        "mean_positive_mass_coverage@8": lambda summary: summary["per_state"][
            "positive_mass_coverage@8"
        ]["mean"],
        "mean_pairwise_accuracy": lambda summary: summary["per_state"][
            "positive_vs_negative_pairwise_accuracy"
        ]["mean"],
    }
    values = {key: [] for key in metric_paths}
    for _ in range(int(samples)):
        sampled_tasks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        sampled_rows: list[dict[str, Any]] = []
        for replicate, task in enumerate(sampled_tasks):
            for row in grouped[task]:
                copied = dict(row)
                copied["state_example_id"] = (
                    f"bootstrap:{replicate}:{copied['state_example_id']}"
                )
                copied["state_task_id"] = f"bootstrap:{replicate}:{task}"
                sampled_rows.append(copied)
        summary = summarize_revised_predictions(
            sampled_rows,
            ranking_ks=metric_settings["ranking_ks"],
            neutral_epsilon=float(metric_settings["neutral_epsilon"]),
            best_tie_tolerance=float(metric_settings["best_tie_tolerance"]),
            huber_delta=float(metric_settings["huber_delta"]),
        )
        for key, getter in metric_paths.items():
            value = getter(summary)
            if value is not None and math.isfinite(float(value)):
                values[key].append(float(value))
    return {
        key: {
            "mean": sum(selected) / len(selected) if selected else None,
            "ci95_low": _quantile(selected, 0.025),
            "ci95_high": _quantile(selected, 0.975),
            "samples": len(selected),
        }
        for key, selected in values.items()
    }


def paired_task_bootstrap_contrast(
    correct_rows: Sequence[Mapping[str, Any]],
    control_rows: Sequence[Mapping[str, Any]],
    *,
    samples: int,
    seed: int,
    metric_settings: Mapping[str, Any],
) -> dict[str, Any]:
    correct_by_pair = {str(row["pair_id"]): row for row in correct_rows}
    control_by_pair = {str(row["pair_id"]): row for row in control_rows}
    if set(correct_by_pair) != set(control_by_pair):
        raise ValueError("Paired bootstrap controls have different pair IDs")
    grouped: dict[str, list[str]] = defaultdict(list)
    for pair_id, row in correct_by_pair.items():
        grouped[str(row["state_task_id"])].append(pair_id)
    tasks = sorted(grouped)
    rng = random.Random(int(seed))
    contrasts = {
        "ndcg@4_correct_minus_control": [],
        "raw_huber_control_minus_correct": [],
        "per_state_spearman_correct_minus_control": [],
    }
    for _ in range(int(samples)):
        sampled_tasks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        correct_sample: list[dict[str, Any]] = []
        control_sample: list[dict[str, Any]] = []
        for replicate, task in enumerate(sampled_tasks):
            for pair_id in grouped[task]:
                for source, target in (
                    (correct_by_pair[pair_id], correct_sample),
                    (control_by_pair[pair_id], control_sample),
                ):
                    copied = dict(source)
                    copied["state_example_id"] = (
                        f"bootstrap:{replicate}:{copied['state_example_id']}"
                    )
                    copied["state_task_id"] = f"bootstrap:{replicate}:{task}"
                    target.append(copied)
        kwargs = {
            "ranking_ks": metric_settings["ranking_ks"],
            "neutral_epsilon": float(metric_settings["neutral_epsilon"]),
            "best_tie_tolerance": float(metric_settings["best_tie_tolerance"]),
            "huber_delta": float(metric_settings["huber_delta"]),
        }
        correct_summary = summarize_revised_predictions(correct_sample, **kwargs)
        control_summary = summarize_revised_predictions(control_sample, **kwargs)
        contrasts["ndcg@4_correct_minus_control"].append(
            float(correct_summary["per_state"]["ndcg@4"]["mean"])
            - float(control_summary["per_state"]["ndcg@4"]["mean"])
        )
        contrasts["raw_huber_control_minus_correct"].append(
            float(control_summary["raw_huber"])
            - float(correct_summary["raw_huber"])
        )
        contrasts["per_state_spearman_correct_minus_control"].append(
            float(correct_summary["per_state"]["spearman"]["mean"])
            - float(control_summary["per_state"]["spearman"]["mean"])
        )
    return {
        key: {
            "mean": sum(values) / len(values),
            "ci95_low": _quantile(values, 0.025),
            "ci95_high": _quantile(values, 0.975),
            "samples": len(values),
        }
        for key, values in contrasts.items()
    }


def raw_residual_cell_distributions(
    *,
    rows_by_cell: Mapping[str, Sequence[Mapping[str, Any]]],
    decomposition: Mapping[str, Any],
) -> dict[str, Any]:
    output = {}
    for cell, rows in rows_by_cell.items():
        residuals = [
            float(row["text_utility"])
            - float(decomposition["mu"])
            - float(
                decomposition["state_effects"].get(
                    str(row["state_example_id"]), 0.0
                )
            )
            - float(
                decomposition["transition_effects"].get(
                    str(row["transition_id"]), 0.0
                )
            )
            for row in rows
        ]
        output[cell] = {
            "raw": _distribution([float(row["text_utility"]) for row in rows]),
            "residual_using_cell_a_known_levels_only": _distribution(residuals),
            "unknown_state_effect_policy": "zero_centered_prior",
            "unknown_transition_effect_policy": "zero_centered_prior",
        }
    return output


def interaction_gate(
    *,
    candidate: Mapping[str, Any],
    state_only: Mapping[str, Any],
    transition_only: Mapping[str, Any],
    shuffled_state: Mapping[str, Any],
    shuffled_transition: Mapping[str, Any],
    per_task: Mapping[str, Mapping[str, float]],
    transition_shuffle_contrast: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    correct_ndcg = float(candidate["per_state"]["ndcg@4"]["mean"] or 0.0)
    best_baseline = max(
        float(state_only["per_state"]["ndcg@4"]["mean"] or 0.0),
        float(transition_only["per_state"]["ndcg@4"]["mean"] or 0.0),
    )
    state_shuffle_ndcg = float(
        shuffled_state["per_state"]["ndcg@4"]["mean"] or 0.0
    )
    transition_shuffle_ndcg = float(
        shuffled_transition["per_state"]["ndcg@4"]["mean"] or 0.0
    )
    positive_tasks = sum(
        float(values["correct_ndcg@4"])
        > max(
            float(values["state_only_ndcg@4"]),
            float(values["transition_only_ndcg@4"]),
            float(values["shuffled_state_ndcg@4"]),
            float(values["shuffled_transition_ndcg@4"]),
        )
        for values in per_task.values()
    )
    contrast = transition_shuffle_contrast[
        "ndcg@4_correct_minus_control"
    ]
    checks = {
        "pooled_raw_spearman": float(candidate["pooled_raw_spearman"] or 0.0)
        >= float(thresholds["pooled_raw_spearman"]),
        "mean_per_state_spearman": float(
            candidate["per_state"]["spearman"]["mean"] or 0.0
        )
        >= float(thresholds["mean_per_state_spearman"]),
        "interaction_residual_spearman": float(
            candidate["interaction_residual_spearman"] or 0.0
        )
        >= float(thresholds["interaction_residual_spearman"]),
        "ndcg4_beats_best_single_axis": correct_ndcg
        >= best_baseline + float(thresholds["ndcg4_single_axis_gain"]),
        "transition_shuffle_drop": correct_ndcg - transition_shuffle_ndcg
        >= float(thresholds["ndcg4_transition_shuffle_drop"]),
        "state_shuffle_drop": correct_ndcg - state_shuffle_ndcg
        >= float(thresholds["ndcg4_state_shuffle_drop"]),
        "transition_shuffle_bootstrap_ci_excludes_zero": float(
            contrast["ci95_low"] or 0.0
        )
        > 0.0,
        "heldout_task_consistency": positive_tasks
        >= int(thresholds["minimum_positive_heldout_tasks"]),
    }
    return {
        "format": REVISED_GATE_VERSION,
        "checks": checks,
        "passed": all(checks.values()),
        "correct_ndcg@4": correct_ndcg,
        "best_single_axis_ndcg@4": best_baseline,
        "state_shuffle_ndcg@4": state_shuffle_ndcg,
        "transition_shuffle_ndcg@4": transition_shuffle_ndcg,
        "positive_heldout_tasks": positive_tasks,
        "heldout_task_count": len(per_task),
        "thresholds": dict(thresholds),
    }


def per_task_gate_metrics(
    *,
    correct_rows: Sequence[Mapping[str, Any]],
    state_only_rows: Sequence[Mapping[str, Any]],
    transition_only_rows: Sequence[Mapping[str, Any]],
    shuffled_state_rows: Sequence[Mapping[str, Any]],
    shuffled_transition_rows: Sequence[Mapping[str, Any]],
    metric_settings: Mapping[str, Any],
) -> dict[str, Any]:
    sources = {
        "correct": correct_rows,
        "state_only": state_only_rows,
        "transition_only": transition_only_rows,
        "shuffled_state": shuffled_state_rows,
        "shuffled_transition": shuffled_transition_rows,
    }
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for name, rows in sources.items():
        for row in rows:
            grouped[str(row["state_task_id"])][name].append(row)
    kwargs = {
        "ranking_ks": metric_settings["ranking_ks"],
        "neutral_epsilon": float(metric_settings["neutral_epsilon"]),
        "best_tie_tolerance": float(metric_settings["best_tie_tolerance"]),
        "huber_delta": float(metric_settings["huber_delta"]),
    }
    output = {}
    for task in sorted(grouped):
        summaries = {
            name: summarize_revised_predictions(grouped[task][name], **kwargs)
            for name in sources
        }
        output[task] = {
            f"{name}_ndcg@4": float(summary["per_state"]["ndcg@4"]["mean"] or 0.0)
            for name, summary in summaries.items()
        }
    return output
