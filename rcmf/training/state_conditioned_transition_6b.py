from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.oracle_convergence_5fa import custom_huber
from rcmf.training.pair_grounding_5d import spearman
from rcmf.utils.serialization import atomic_write_json


STATE_CONDITIONED_TRANSITION_VERSION = "factorized_state_transition_program_6b_v1"
TRANSITION_SPLIT_VERSION = "parent_grouped_transition_split_6b_v1"
TWO_AXIS_MANIFEST_VERSION = "state_transition_two_axis_manifest_6b_v1"
REPRESENTATION_CACHE_VERSION = "frozen_qwen_state_transition_representations_6b_v1"
REPRESENTATION_GATE_VERSION = "state_transition_representation_gate_6b_v1"
FIELD_ALGEBRA_VERSION = "factorized_transition_field_algebra_6b_v1"
RUN_MANIFEST_VERSION = "resumable_experiment_run_manifest_v1"
RUN_MANIFEST_CONFIG_SUPERSESSION_VERSION = (
    "run_manifest_config_supersession_v1"
)
ATTEMPT_LEDGER_VERSION = "append_only_attempt_ledger_v1"
HEARTBEAT_VERSION = "persistent_experiment_heartbeat_v1"

CELL_A = "train_state__train_transition"
CELL_B = "heldout_state__train_transition"
CELL_C = "train_state__heldout_transition"
CELL_D = "heldout_state__heldout_transition"
CELL_NAMES = (CELL_A, CELL_B, CELL_C, CELL_D)
UTILITY_NEUTRAL_EPS = 0.01


def utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_order_key(seed: int, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{namespace}:{value}".encode("utf-8")).hexdigest()


def utility_category(value: float, eps: float = UTILITY_NEUTRAL_EPS) -> str:
    if float(value) > float(eps):
        return "positive"
    if float(value) < -float(eps):
        return "negative"
    return "neutral"


def deterministic_parent_split(
    panel_rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    train_parent_count: int = 29,
    heldout_parent_count: int = 8,
) -> dict[str, Any]:
    parent_ids = sorted({str(row["parent_memory_id"]) for row in panel_rows})
    expected = int(train_parent_count) + int(heldout_parent_count)
    if len(parent_ids) != expected:
        raise ValueError(f"Expected {expected} panel parents, found {len(parent_ids)}")
    ordered = sorted(
        parent_ids,
        key=lambda value: (stable_order_key(seed, "transition-parent", value), value),
    )
    heldout = sorted(ordered[:heldout_parent_count])
    train = sorted(ordered[heldout_parent_count:])
    split_by_parent = {value: "train" for value in train}
    split_by_parent.update({value: "heldout" for value in heldout})
    transition_counts = Counter(str(row["parent_memory_id"]) for row in panel_rows)
    payload = {
        "format": TRANSITION_SPLIT_VERSION,
        "seed": int(seed),
        "selection": "sha256_order_first_heldout_then_remaining_train",
        "train_parent_count": len(train),
        "heldout_parent_count": len(heldout),
        "train_parent_ids": train,
        "heldout_parent_ids": heldout,
        "split_by_parent": split_by_parent,
        "panel_transition_counts_by_parent": dict(sorted(transition_counts.items())),
    }
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    return payload


def _cell_name(state_split: str, transition_split: str) -> str:
    state = "train_state" if state_split == "train" else "heldout_state"
    transition = (
        "train_transition" if transition_split == "train" else "heldout_transition"
    )
    cell = f"{state}__{transition}"
    if cell not in CELL_NAMES:
        raise ValueError(f"Unknown two-axis cell {cell}")
    return cell


def build_two_axis_rows(
    *,
    teacher_rows: Sequence[Mapping[str, Any]],
    panel_rows: Sequence[Mapping[str, Any]],
    query_manifest: Mapping[str, Any],
    parent_split: Mapping[str, Any],
) -> list[dict[str, Any]]:
    panel_by_id = {str(row["transition_id"]): dict(row) for row in panel_rows}
    if len(panel_by_id) != len(panel_rows):
        raise ValueError("Transition panel contains duplicate IDs")
    query_by_id = {
        str(row["state_example_id"]): dict(row)
        for row in query_manifest["query_rows"]
    }
    if len(query_by_id) != len(query_manifest["query_rows"]):
        raise ValueError("Query manifest contains duplicate state IDs")
    parent_splits = {
        str(key): str(value) for key, value in parent_split["split_by_parent"].items()
    }
    output: list[dict[str, Any]] = []
    pair_ids: set[str] = set()
    for source in teacher_rows:
        if not bool(source.get("valid_for_loss")):
            continue
        pair_id = str(source["pair_id"])
        if pair_id in pair_ids:
            raise ValueError(f"Duplicate valid pair key: {pair_id}")
        pair_ids.add(pair_id)
        state_id = str(source["state_example_id"])
        transition_id = str(source["transition_id"])
        if state_id not in query_by_id or transition_id not in panel_by_id:
            raise ValueError(f"Pair references an unknown state or transition: {pair_id}")
        query = query_by_id[state_id]
        transition = panel_by_id[transition_id]
        parent_id = str(transition["parent_memory_id"])
        if parent_id not in parent_splits:
            raise ValueError(f"Transition parent is absent from split: {parent_id}")
        state_split = str(query["split"])
        if state_split not in {"train", "validation"}:
            raise ValueError(f"Unexpected query split: {state_split}")
        normalized_state_split = "train" if state_split == "train" else "heldout"
        transition_split = parent_splits[parent_id]
        utility = float(source["text_utility"])
        output.append(
            {
                "pair_id": pair_id,
                "state_example_id": state_id,
                "state_index": int(query["example_index"]),
                "state_task_id": str(query["task_id"]),
                "state_apps": list(query.get("apps", [])),
                "state_step_id": int(query["step_id"]),
                "state_prompt_tokens": int(source["state_prompt_tokens"]),
                "state_split": normalized_state_split,
                "transition_id": transition_id,
                "transition_parent_id": parent_id,
                "transition_parent_task_id": str(transition["parent_task_id"]),
                "transition_split": transition_split,
                "transition_step_index": int(transition["step_index"]),
                "transition_step_count": int(transition["step_count"]),
                "transition_step_bucket": str(source["transition_step_bucket"]),
                "transition_apps": list(transition.get("apps", [])),
                "transition_action_type": str(transition["action_type"]),
                "cell": _cell_name(normalized_state_split, transition_split),
                "L0": float(source["L0"]),
                "Lj_transition": float(source["Lj_transition"]),
                "text_utility": utility,
                "utility_category": utility_category(utility),
                "target_tokens": int(source["target_tokens"]),
                "valid_for_loss": True,
                "over_context": False,
                "truncated": False,
                "target_sha256": str(source["target_sha256"]),
                "target_token_sha256": str(source["target_token_sha256"]),
                "transition_content_sha256": str(
                    source["transition_content_sha256"]
                ),
                "base_prompt_sha256": str(source["base_prompt_sha256"]),
            }
        )
    return sorted(output, key=lambda row: str(row["pair_id"]))


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0}

    def quantile(fraction: float) -> float:
        index = int(math.floor((len(ordered) - 1) * fraction + 0.5))
        return ordered[index]

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


def summarize_two_axis_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for cell in CELL_NAMES:
        selected = [row for row in rows if str(row["cell"]) == cell]
        output[cell] = {
            "pair_count": len(selected),
            "state_count": len({str(row["state_example_id"]) for row in selected}),
            "state_task_count": len({str(row["state_task_id"]) for row in selected}),
            "transition_count": len({str(row["transition_id"]) for row in selected}),
            "transition_parent_count": len(
                {str(row["transition_parent_id"]) for row in selected}
            ),
            "utility": _distribution(
                [float(row["text_utility"]) for row in selected]
            ),
            "utility_categories": dict(
                Counter(str(row["utility_category"]) for row in selected)
            ),
            "state_apps": dict(
                Counter(app for row in selected for app in row.get("state_apps", []))
            ),
            "transition_apps": dict(
                Counter(
                    app for row in selected for app in row.get("transition_apps", [])
                )
            ),
            "transition_step_buckets": dict(
                Counter(str(row["transition_step_bucket"]) for row in selected)
            ),
            "state_task_ids": sorted(
                {str(row["state_task_id"]) for row in selected}
            ),
            "transition_parent_ids": sorted(
                {str(row["transition_parent_id"]) for row in selected}
            ),
        }
    return output


def deterministic_derangement(
    values: Sequence[str], *, seed: int, namespace: str
) -> dict[str, str]:
    unique = sorted({str(value) for value in values})
    if len(unique) < 2:
        raise ValueError("A derangement needs at least two identities")
    ordered = sorted(
        unique, key=lambda value: (stable_order_key(seed, namespace, value), value)
    )
    for offset in range(1, len(ordered)):
        candidate = {
            value: ordered[(index + offset) % len(ordered)]
            for index, value in enumerate(ordered)
        }
        if all(source != target for source, target in candidate.items()):
            return candidate
    raise RuntimeError("Could not construct a deterministic derangement")


class DenseTower(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        *,
        hidden_dim: int = 256,
        dropout: float = 0.05,
        zero_output: bool = False,
    ) -> None:
        super().__init__()
        self.input = nn.Linear(input_dim, hidden_dim)
        self.norm = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)
        self.output = nn.Linear(hidden_dim, output_dim)
        if zero_output:
            nn.init.zeros_(self.output.weight)
            nn.init.zeros_(self.output.bias)

    def forward(self, values: Tensor) -> Tensor:
        hidden = self.input(values.to(torch.float32))
        hidden = self.dropout(self.norm(F.gelu(hidden)))
        return self.output(hidden)


class UtilityPredictor(nn.Module):
    KINDS = {
        "state_only",
        "transition_only",
        "additive",
        "signed_bilinear",
        "concat_mlp",
    }

    def __init__(
        self,
        kind: str,
        *,
        state_dim: int,
        transition_dim: int,
        hidden_dim: int = 256,
        interaction_dim: int = 128,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        if kind not in self.KINDS:
            raise ValueError(f"Unknown utility predictor: {kind}")
        self.kind = kind
        if kind in {"state_only", "additive", "signed_bilinear"}:
            self.state_main = DenseTower(
                state_dim, 1, hidden_dim=hidden_dim, dropout=dropout
            )
        if kind in {"transition_only", "additive", "signed_bilinear"}:
            self.transition_main = DenseTower(
                transition_dim, 1, hidden_dim=hidden_dim, dropout=dropout
            )
        if kind == "signed_bilinear":
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
            self.interaction_scale = math.sqrt(float(interaction_dim))
            self.bias = nn.Parameter(torch.zeros(()))
        if kind == "concat_mlp":
            if state_dim != transition_dim:
                raise ValueError("concat_mlp requires equal state and transition dimensions")
            self.concat = DenseTower(
                state_dim * 3, 1, hidden_dim=hidden_dim, dropout=dropout
            )

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        if self.kind == "state_only":
            return self.state_main(state).squeeze(-1)
        if self.kind == "transition_only":
            return self.transition_main(transition).squeeze(-1)
        if self.kind == "additive":
            return (
                self.state_main(state).squeeze(-1)
                + self.transition_main(transition).squeeze(-1)
            )
        if self.kind == "signed_bilinear":
            state_interaction = self.state_interaction(state)
            transition_interaction = self.transition_interaction(transition)
            return (
                self.bias
                + self.state_main(state).squeeze(-1)
                + self.transition_main(transition).squeeze(-1)
                + (state_interaction * transition_interaction).sum(dim=-1)
                / self.interaction_scale
            )
        features = torch.cat([state, transition, state * transition], dim=-1)
        return self.concat(features).squeeze(-1)


def build_grouped_cv_manifest(
    rows: Sequence[Mapping[str, Any]], *, folds: int, seed: int
) -> dict[str, Any]:
    if folds < 2:
        raise ValueError("Grouped CV needs at least two folds")
    tasks = sorted({str(row["state_task_id"]) for row in rows})
    parents = sorted({str(row["transition_parent_id"]) for row in rows})
    task_order = sorted(
        tasks, key=lambda value: (stable_order_key(seed, "cv-task", value), value)
    )
    parent_order = sorted(
        parents,
        key=lambda value: (stable_order_key(seed, "cv-parent", value), value),
    )
    task_fold = {value: index % folds for index, value in enumerate(task_order)}
    parent_fold = {value: index % folds for index, value in enumerate(parent_order)}
    fold_rows = []
    all_pair_ids = {str(row["pair_id"]) for row in rows}
    for fold in range(folds):
        heldout_tasks = {key for key, value in task_fold.items() if value == fold}
        heldout_parents = {key for key, value in parent_fold.items() if value == fold}
        train_ids = sorted(
            str(row["pair_id"])
            for row in rows
            if str(row["state_task_id"]) not in heldout_tasks
            and str(row["transition_parent_id"]) not in heldout_parents
        )
        validation_ids = sorted(
            str(row["pair_id"])
            for row in rows
            if str(row["state_task_id"]) in heldout_tasks
            and str(row["transition_parent_id"]) in heldout_parents
        )
        if not train_ids or not validation_ids:
            raise ValueError(f"Grouped CV fold {fold} has an empty train or validation set")
        if set(train_ids).intersection(validation_ids):
            raise RuntimeError("Grouped CV train/validation overlap")
        fold_rows.append(
            {
                "fold": fold,
                "train_pair_ids": train_ids,
                "validation_pair_ids": validation_ids,
                "heldout_state_task_ids": sorted(heldout_tasks),
                "heldout_transition_parent_ids": sorted(heldout_parents),
                "unused_cross_axis_pair_count": len(all_pair_ids)
                - len(train_ids)
                - len(validation_ids),
            }
        )
    payload = {
        "format": "task_parent_grouped_cv_manifest_6b_v1",
        "seed": int(seed),
        "fold_count": int(folds),
        "state_task_assignment": task_fold,
        "transition_parent_assignment": parent_fold,
        "folds": fold_rows,
    }
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    return payload


def _tensor_indices(
    rows: Sequence[Mapping[str, Any]],
    *,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
) -> tuple[Tensor, Tensor, Tensor]:
    states = torch.tensor(
        [state_position[str(row["state_example_id"])] for row in rows],
        dtype=torch.long,
        device=device,
    )
    transitions = torch.tensor(
        [transition_position[str(row["transition_id"])] for row in rows],
        dtype=torch.long,
        device=device,
    )
    utilities = torch.tensor(
        [float(row["text_utility"]) for row in rows],
        dtype=torch.float32,
        device=device,
    )
    return states, transitions, utilities


def train_utility_predictor(
    *,
    model: UtilityPredictor,
    rows: Sequence[Mapping[str, Any]],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    huber_delta: float,
    seed: int,
    device: torch.device,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Utility predictor received no training rows")
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device)
    model.train()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay)
    )
    state_indices, transition_indices, utilities = _tensor_indices(
        rows,
        state_position=state_position,
        transition_position=transition_position,
        device=device,
    )
    state_representations = state_representations.to(device=device, dtype=torch.float32)
    transition_representations = transition_representations.to(
        device=device, dtype=torch.float32
    )
    generator = torch.Generator(device="cpu").manual_seed(int(seed))
    history = []
    update_count = 0
    for epoch in range(1, int(epochs) + 1):
        order = torch.randperm(len(rows), generator=generator).tolist()
        epoch_losses = []
        epoch_gradients = []
        for start in range(0, len(order), int(batch_size)):
            selected = order[start : start + int(batch_size)]
            selected_tensor = torch.tensor(selected, dtype=torch.long, device=device)
            prediction = model(
                state_representations[state_indices[selected_tensor]],
                transition_representations[transition_indices[selected_tensor]],
            )
            loss = custom_huber(
                prediction - utilities[selected_tensor], delta=float(huber_delta)
            ).mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            gradient_values = [
                parameter.grad.detach().to(torch.float32).flatten()
                for parameter in model.parameters()
                if parameter.grad is not None
            ]
            gradient_norm = (
                float(torch.cat(gradient_values).norm().cpu())
                if gradient_values
                else 0.0
            )
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            update_count += 1
            epoch_losses.append(float(loss.detach().cpu()))
            epoch_gradients.append(gradient_norm)
        history.append(
            {
                "epoch": epoch,
                "mean_loss": sum(epoch_losses) / len(epoch_losses),
                "mean_gradient_norm": sum(epoch_gradients) / len(epoch_gradients),
                "optimizer_updates": update_count,
            }
        )
    model.eval()
    return {
        "epochs": int(epochs),
        "optimizer_updates": update_count,
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


@torch.no_grad()
def predict_utility_rows(
    *,
    model: UtilityPredictor | None,
    rows: Sequence[Mapping[str, Any]],
    state_representations: Tensor,
    transition_representations: Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
    control: str = "correct",
    seed: int = 0,
    global_mean: float | None = None,
) -> list[dict[str, Any]]:
    if control not in {
        "correct",
        "shuffled_state",
        "shuffled_transition",
        "both_shuffled",
        "mean_state",
        "mean_transition",
    }:
        raise ValueError(f"Unknown representation control: {control}")
    state_map = {key: key for key in state_position}
    transition_map = {key: key for key in transition_position}
    if control in {"shuffled_state", "both_shuffled"}:
        selected_states = [str(row["state_example_id"]) for row in rows]
        replacement = deterministic_derangement(
            selected_states, seed=seed, namespace=f"{control}-state"
        )
        state_map.update(replacement)
    if control in {"shuffled_transition", "both_shuffled"}:
        selected_transitions = [str(row["transition_id"]) for row in rows]
        replacement = deterministic_derangement(
            selected_transitions, seed=seed, namespace=f"{control}-transition"
        )
        transition_map.update(replacement)
    state_tensor = state_representations.to(device=device, dtype=torch.float32)
    transition_tensor = transition_representations.to(
        device=device, dtype=torch.float32
    )
    if control == "mean_state":
        state_tensor = state_tensor.mean(dim=0, keepdim=True)
    if control == "mean_transition":
        transition_tensor = transition_tensor.mean(dim=0, keepdim=True)
    output = []
    batch_size = 512
    for start in range(0, len(rows), batch_size):
        block = rows[start : start + batch_size]
        if global_mean is not None:
            prediction = torch.full(
                (len(block),), float(global_mean), dtype=torch.float32, device=device
            )
        else:
            if model is None:
                raise ValueError("A non-global prediction requires a model")
            if control == "mean_state":
                state = state_tensor.expand(len(block), -1)
            else:
                state = torch.stack(
                    [
                        state_tensor[
                            state_position[state_map[str(row["state_example_id"])]]
                        ]
                        for row in block
                    ]
                )
            if control == "mean_transition":
                transition = transition_tensor.expand(len(block), -1)
            else:
                transition = torch.stack(
                    [
                        transition_tensor[
                            transition_position[
                                transition_map[str(row["transition_id"])]
                            ]
                        ]
                        for row in block
                    ]
                )
            prediction = model(state, transition)
        for row, value in zip(block, prediction.detach().cpu().tolist()):
            output.append(
                {
                    "pair_id": str(row["pair_id"]),
                    "state_example_id": str(row["state_example_id"]),
                    "state_task_id": str(row["state_task_id"]),
                    "transition_id": str(row["transition_id"]),
                    "transition_parent_id": str(row["transition_parent_id"]),
                    "cell": str(row["cell"]),
                    "utility_category": str(row["utility_category"]),
                    "u_text": float(row["text_utility"]),
                    "u_predicted": float(value),
                    "control": control,
                }
            )
    return output


def summarize_utility_predictions(
    rows: Sequence[Mapping[str, Any]], *, huber_delta: float
) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    teacher = [float(row["u_text"]) for row in rows]
    predicted = [float(row["u_predicted"]) for row in rows]
    errors = [estimate - target for target, estimate in zip(teacher, predicted)]
    non_neutral = [
        row
        for row in rows
        if utility_category(float(row["u_text"])) in {"positive", "negative"}
    ]
    sign = [
        (float(row["u_text"]) > 0.0) == (float(row["u_predicted"]) > 0.0)
        for row in non_neutral
    ]
    huber = [
        float(custom_huber(torch.tensor(value), delta=huber_delta))
        for value in errors
    ]
    by_category = {}
    for category in ("positive", "neutral", "negative"):
        selected = [
            row for row in rows if utility_category(float(row["u_text"])) == category
        ]
        by_category[category] = {
            "count": len(selected),
            "u_text": _distribution([float(row["u_text"]) for row in selected]),
            "u_predicted": _distribution(
                [float(row["u_predicted"]) for row in selected]
            ),
        }
    return {
        "count": len(rows),
        "u_text_vs_prediction_pearson": _pearson(teacher, predicted),
        "u_text_vs_prediction_spearman": spearman(teacher, predicted),
        "positive_negative_sign_agreement": (
            sum(sign) / len(sign) if sign else None
        ),
        "mae": sum(abs(value) for value in errors) / len(errors),
        "mse": sum(value * value for value in errors) / len(errors),
        "huber": sum(huber) / len(huber),
        "u_text": _distribution(teacher),
        "u_predicted": _distribution(predicted),
        "by_utility_category": by_category,
    }


def representation_interaction_gate(
    *,
    model_results: Mapping[str, Mapping[str, Any]],
    minimum_spearman: float = 0.20,
    minimum_sign_agreement: float = 0.60,
    minimum_baseline_spearman_gain: float = 0.05,
    minimum_shuffle_spearman_drop: float = 0.05,
) -> dict[str, Any]:
    state = model_results["state_only"]["correct"]
    transition = model_results["transition_only"]["correct"]

    def checks(kind: str) -> dict[str, bool]:
        correct = model_results[kind]["correct"]
        shuffled_state = model_results[kind]["shuffled_state"]
        shuffled_transition = model_results[kind]["shuffled_transition"]
        correct_spearman = float(correct.get("u_text_vs_prediction_spearman") or 0.0)
        state_spearman = float(state.get("u_text_vs_prediction_spearman") or 0.0)
        transition_spearman = float(
            transition.get("u_text_vs_prediction_spearman") or 0.0
        )
        return {
            "spearman": correct_spearman >= float(minimum_spearman),
            "sign_agreement": float(
                correct.get("positive_negative_sign_agreement") or 0.0
            )
            >= float(minimum_sign_agreement),
            "beats_state_only_spearman": correct_spearman
            >= state_spearman + float(minimum_baseline_spearman_gain),
            "beats_transition_only_spearman": correct_spearman
            >= transition_spearman + float(minimum_baseline_spearman_gain),
            "beats_state_only_huber": float(correct["huber"]) < float(state["huber"]),
            "beats_transition_only_huber": float(correct["huber"])
            < float(transition["huber"]),
            "state_shuffle_degrades": correct_spearman
            - float(shuffled_state.get("u_text_vs_prediction_spearman") or 0.0)
            >= float(minimum_shuffle_spearman_drop),
            "transition_shuffle_degrades": correct_spearman
            - float(
                shuffled_transition.get("u_text_vs_prediction_spearman") or 0.0
            )
            >= float(minimum_shuffle_spearman_drop),
        }

    concat_checks = checks("concat_mlp")
    bilinear_checks = checks("signed_bilinear")
    concat_passed = all(concat_checks.values())
    bilinear_passed = all(bilinear_checks.values())
    if not concat_passed:
        branch = "state_transition_representations_insufficient"
        proceed = False
    elif not bilinear_passed:
        branch = "field_compatible_interaction_factorization_insufficient"
        proceed = False
    else:
        branch = "representation_gate_passed"
        proceed = True
    return {
        "format": REPRESENTATION_GATE_VERSION,
        "thresholds": {
            "minimum_spearman": float(minimum_spearman),
            "minimum_sign_agreement": float(minimum_sign_agreement),
            "minimum_baseline_spearman_gain": float(
                minimum_baseline_spearman_gain
            ),
            "minimum_shuffle_spearman_drop": float(
                minimum_shuffle_spearman_drop
            ),
        },
        "concat_mlp": {"checks": concat_checks, "passed": concat_passed},
        "signed_bilinear": {
            "checks": bilinear_checks,
            "passed": bilinear_passed,
        },
        "branch": branch,
        "proceed_to_behavioral_training": proceed,
    }


class FactorizedTransitionProgram(nn.Module):
    def __init__(
        self,
        *,
        state_dim: int,
        transition_dim: int,
        controller_rank: int = 16,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.controller_rank = int(controller_rank)
        self.program_dim = int(program_dim)
        self.state_controller = DenseTower(
            state_dim,
            self.controller_rank,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.static_program_head = DenseTower(
            transition_dim,
            self.program_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
            zero_output=True,
        )
        self.conditional_basis_head = DenseTower(
            transition_dim,
            self.controller_rank * self.program_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
            zero_output=True,
        )

    def components(self, state: Tensor, transition: Tensor) -> dict[str, Tensor]:
        controller = self.state_controller(state)
        static = self.static_program_head(transition)
        basis = self.conditional_basis_head(transition).view(
            -1, self.controller_rank, self.program_dim
        )
        conditional = torch.einsum("bg,bgp->bp", controller, basis)
        return {
            "controller": controller,
            "static": static,
            "basis": basis,
            "conditional": conditional,
            "z": static + conditional,
        }

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        return self.components(state, transition)["z"]


class StaticTransitionProgram(nn.Module):
    def __init__(self, transition_dim: int, program_dim: int = 128) -> None:
        super().__init__()
        self.head = DenseTower(transition_dim, program_dim, zero_output=True)

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        del state
        return self.head(transition)


class StateOnlyProgram(nn.Module):
    def __init__(self, state_dim: int, program_dim: int = 128) -> None:
        super().__init__()
        self.head = DenseTower(state_dim, program_dim, zero_output=True)

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        del transition
        return self.head(state)


class ConditionalOnlyTransitionProgram(FactorizedTransitionProgram):
    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        return self.components(state, transition)["conditional"]


class PairMLPProgram(nn.Module):
    def __init__(
        self, *, state_dim: int, transition_dim: int, program_dim: int = 128
    ) -> None:
        super().__init__()
        if state_dim != transition_dim:
            raise ValueError("PairMLPProgram requires equal representation dimensions")
        self.head = DenseTower(state_dim * 3, program_dim, zero_output=True)

    def forward(self, state: Tensor, transition: Tensor) -> Tensor:
        return self.head(torch.cat([state, transition, state * transition], dim=-1))


def project_program_to_ratio(
    z: Tensor, *, maximum_delta_norm: Tensor, eps: float = 1.0e-8
) -> tuple[Tensor, Tensor]:
    norms = z.to(torch.float32).norm(dim=-1)
    maximum = maximum_delta_norm.to(device=z.device, dtype=torch.float32)
    scales = torch.minimum(
        torch.ones_like(norms), maximum / norms.clamp_min(float(eps))
    )
    projected = z * scales.to(z.dtype).unsqueeze(-1)
    ratios = projected.to(torch.float32).norm(dim=-1) / maximum.clamp_min(float(eps))
    return projected, ratios


@dataclass
class _FieldRecord:
    parent_id: str
    key: Tensor
    static_program: Tensor
    conditional_basis: Tensor
    delta_v0: Tensor
    delta_t: Tensor


class FactorizedTransitionField:
    def __init__(
        self,
        key_rank: int,
        controller_rank: int,
        program_dim: int,
        *,
        dtype: torch.dtype = torch.float64,
    ) -> None:
        self.key_rank = int(key_rank)
        self.controller_rank = int(controller_rank)
        self.program_dim = int(program_dim)
        self.dtype = dtype
        self.V0 = torch.zeros(self.key_rank, self.program_dim, dtype=dtype)
        self.T = torch.zeros(
            self.key_rank, self.controller_rank, self.program_dim, dtype=dtype
        )
        self.records: dict[str, _FieldRecord] = {}

    def _record(
        self,
        parent_id: str,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> _FieldRecord:
        key = key.detach().to(dtype=self.dtype).reshape(self.key_rank)
        static_program = static_program.detach().to(dtype=self.dtype).reshape(
            self.program_dim
        )
        conditional_basis = conditional_basis.detach().to(dtype=self.dtype).reshape(
            self.controller_rank, self.program_dim
        )
        return _FieldRecord(
            parent_id=str(parent_id),
            key=key,
            static_program=static_program,
            conditional_basis=conditional_basis,
            delta_v0=torch.outer(key, static_program),
            delta_t=torch.einsum("k,gp->kgp", key, conditional_basis),
        )

    def add(
        self,
        transition_id: str,
        parent_id: str,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> None:
        transition_id = str(transition_id)
        if transition_id in self.records:
            raise KeyError(f"Transition already exists: {transition_id}")
        record = self._record(
            parent_id, key, static_program, conditional_basis
        )
        self.records[transition_id] = record
        self.V0.add_(record.delta_v0)
        self.T.add_(record.delta_t)

    def remove(self, transition_id: str) -> None:
        record = self.records.pop(str(transition_id))
        self.V0.sub_(record.delta_v0)
        self.T.sub_(record.delta_t)

    def remove_parent(self, parent_id: str) -> list[str]:
        selected = sorted(
            key
            for key, record in self.records.items()
            if record.parent_id == str(parent_id)
        )
        for transition_id in selected:
            self.remove(transition_id)
        return selected

    def replace(
        self,
        transition_id: str,
        parent_id: str,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> None:
        transition_id = str(transition_id)
        self.remove(transition_id)
        self.add(
            transition_id, parent_id, key, static_program, conditional_basis
        )

    def read(self, query: Tensor, controller: Tensor) -> Tensor:
        query = query.to(dtype=self.dtype).reshape(self.key_rank)
        controller = controller.to(dtype=self.dtype).reshape(self.controller_rank)
        return query @ self.V0 + torch.einsum("k,kgp,g->p", query, self.T, controller)

    def explicit_read(self, query: Tensor, controller: Tensor) -> Tensor:
        query = query.to(dtype=self.dtype).reshape(self.key_rank)
        controller = controller.to(dtype=self.dtype).reshape(self.controller_rank)
        output = torch.zeros(self.program_dim, dtype=self.dtype)
        for record in self.records.values():
            pair_program = record.static_program + controller @ record.conditional_basis
            output.add_(torch.dot(query, record.key) * pair_program)
        return output

    @property
    def runtime_shapes(self) -> dict[str, list[int]]:
        return {"V0": list(self.V0.shape), "T": list(self.T.shape)}


def factorized_field_algebra_validation(seed: int = 18018) -> dict[str, Any]:
    generator = torch.Generator().manual_seed(int(seed))
    key_rank, controller_rank, program_dim = 7, 5, 11
    source = []
    parent_ids = ("parent-a", "parent-a", "parent-b", "parent-c")
    for index, parent_id in enumerate(parent_ids):
        source.append(
            (
                f"transition-{index}",
                parent_id,
                torch.randn(key_rank, generator=generator, dtype=torch.float64),
                torch.randn(program_dim, generator=generator, dtype=torch.float64),
                torch.randn(
                    controller_rank,
                    program_dim,
                    generator=generator,
                    dtype=torch.float64,
                ),
            )
        )
    query = torch.randn(key_rank, generator=generator, dtype=torch.float64)
    controller = torch.randn(
        controller_rank, generator=generator, dtype=torch.float64
    )
    field = FactorizedTransitionField(key_rank, controller_rank, program_dim)
    for item in source:
        field.add(*item)
    original_v0 = field.V0.clone()
    original_t = field.T.clone()
    original_read = field.read(query, controller)
    explicit_equal = torch.allclose(
        original_read, field.explicit_read(query, controller), atol=1.0e-10
    )

    removed = source[2]
    field.remove(removed[0])
    removal_changed = not torch.allclose(field.read(query, controller), original_read)
    field.add(*removed)
    remove_restore = torch.allclose(field.V0, original_v0, atol=1.0e-10) and torch.allclose(
        field.T, original_t, atol=1.0e-10
    )

    replacement = (
        removed[0],
        "parent-z",
        torch.randn(key_rank, generator=generator, dtype=torch.float64),
        torch.randn(program_dim, generator=generator, dtype=torch.float64),
        torch.randn(
            controller_rank,
            program_dim,
            generator=generator,
            dtype=torch.float64,
        ),
    )
    field.replace(*replacement)
    replacement_equal = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1.0e-10
    )
    field.replace(*removed)
    replace_restore = torch.allclose(field.V0, original_v0, atol=1.0e-10) and torch.allclose(
        field.T, original_t, atol=1.0e-10
    )

    parent_records = [item for item in source if item[1] == "parent-a"]
    field.remove_parent("parent-a")
    parent_removal_equal = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1.0e-10
    )
    for item in reversed(parent_records):
        field.add(*item)
    parent_restore = torch.allclose(field.V0, original_v0, atol=1.0e-10) and torch.allclose(
        field.T, original_t, atol=1.0e-10
    )

    reverse_field = FactorizedTransitionField(key_rank, controller_rank, program_dim)
    for item in reversed(source):
        reverse_field.add(*item)
    arbitrary_order = torch.allclose(
        reverse_field.V0, original_v0, atol=1.0e-10
    ) and torch.allclose(reverse_field.T, original_t, atol=1.0e-10)
    shape_before = field.runtime_shapes
    extra = (
        "transition-extra",
        "parent-extra",
        torch.randn(key_rank, generator=generator, dtype=torch.float64),
        torch.randn(program_dim, generator=generator, dtype=torch.float64),
        torch.randn(
            controller_rank,
            program_dim,
            generator=generator,
            dtype=torch.float64,
        ),
    )
    field.add(*extra)
    shape_after = field.runtime_shapes
    checks = {
        "explicit_sum_equals_compiled_contraction": bool(explicit_equal),
        "single_remove_changes_read": bool(removal_changed),
        "single_add_remove_exact_restoration": bool(remove_restore),
        "replace_matches_explicit": bool(replacement_equal),
        "replace_exact_restoration": bool(replace_restore),
        "parent_removal_matches_explicit": bool(parent_removal_equal),
        "parent_exact_restoration": bool(parent_restore),
        "arbitrary_insertion_order": bool(arbitrary_order),
        "runtime_shape_independent_of_transition_count": shape_before == shape_after,
    }
    return {
        "format": FIELD_ALGEBRA_VERSION,
        "seed": int(seed),
        "checks": checks,
        "passed": all(checks.values()),
        "runtime_shapes": shape_before,
        "source_transition_count": len(source),
    }


def append_jsonl_fsync(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(row), sort_keys=True, ensure_ascii=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


class PersistentHeartbeat:
    def __init__(
        self,
        path: Path,
        *,
        run_uuid: str,
        attempt_id: str,
        phase: str,
        interval_s: float = 240.0,
    ) -> None:
        if float(interval_s) > 300.0:
            raise ValueError("Heartbeat interval may not exceed five minutes")
        self.path = Path(path)
        self.interval_s = float(interval_s)
        self.payload: dict[str, Any] = {
            "format": HEARTBEAT_VERSION,
            "run_uuid": str(run_uuid),
            "attempt_id": str(attempt_id),
            "phase": str(phase),
            "pid": os.getpid(),
            "status": "starting",
        }
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def update(self, **values: Any) -> None:
        with self._lock:
            self.payload.update(values)
            self.payload["timestamp_utc"] = utc_now()
            atomic_write_json(self.path, self.payload)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.update()

    def start(self) -> None:
        self.update(status="running")
        self._thread = threading.Thread(
            target=self._run, name="rcmf-persistent-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self, *, status: str) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.update(status=status)


class AttemptLedger(AbstractContextManager["AttemptLedger"]):
    def __init__(
        self,
        artifact_dir: Path,
        *,
        run_uuid: str,
        attempt_id: str,
        phase: str,
        command: Sequence[str],
        local_head: str,
        github_head: str,
        lambda_head: str,
        tmux_session: str,
        config_sha256: str,
        data_manifest_hashes: Mapping[str, str],
        parent_attempt_id: str | None = None,
        resume_checkpoint: str | None = None,
        scientific_parameter_changed: bool = False,
        heartbeat_interval_s: float = 240.0,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.run_uuid = str(run_uuid)
        self.attempt_id = str(attempt_id)
        self.phase = str(phase)
        self.started_at = utc_now()
        self.base = {
            "format": ATTEMPT_LEDGER_VERSION,
            "run_uuid": self.run_uuid,
            "attempt_id": self.attempt_id,
            "phase": self.phase,
            "start_timestamp_utc": self.started_at,
            "local_head": str(local_head),
            "github_head": str(github_head),
            "lambda_head": str(lambda_head),
            "tmux_session": str(tmux_session),
            "process_command": list(command),
            "pid": os.getpid(),
            "config_sha256": str(config_sha256),
            "data_manifest_hashes": dict(sorted(data_manifest_hashes.items())),
            "parent_attempt_id": parent_attempt_id,
            "resume_checkpoint": resume_checkpoint,
            "scientific_parameter_changed": bool(scientific_parameter_changed),
        }
        self.heartbeat = PersistentHeartbeat(
            self.artifact_dir / "heartbeat.json",
            run_uuid=self.run_uuid,
            attempt_id=self.attempt_id,
            phase=self.phase,
            interval_s=heartbeat_interval_s,
        )
        self.latest_checkpoint: str | None = resume_checkpoint

    @property
    def attempts_path(self) -> Path:
        return self.artifact_dir / "attempts.jsonl"

    def __enter__(self) -> "AttemptLedger":
        append_jsonl_fsync(
            self.attempts_path,
            {**self.base, "event": "start", "timestamp_utc": self.started_at},
        )
        self.heartbeat.start()
        return self

    def progress(self, **values: Any) -> None:
        checkpoint = values.get("latest_validated_checkpoint")
        if checkpoint is not None:
            self.latest_checkpoint = str(checkpoint)
        self.heartbeat.update(**values)

    def finish(self, *, exit_code: int, stop_reason: str) -> None:
        status = "completed" if int(exit_code) == 0 else "failed"
        self.heartbeat.stop(status=status)
        append_jsonl_fsync(
            self.attempts_path,
            {
                **self.base,
                "event": "end",
                "timestamp_utc": utc_now(),
                "end_timestamp_utc": utc_now(),
                "exit_code": int(exit_code),
                "stop_reason": str(stop_reason),
                "latest_validated_checkpoint": self.latest_checkpoint,
            },
        )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc is None:
            self.finish(exit_code=0, stop_reason="normal_completion")
        else:
            self.finish(
                exit_code=1,
                stop_reason=f"{exc_type.__name__}: {exc}" if exc_type else str(exc),
            )
        return False


def initialize_or_validate_run_manifest(
    path: Path,
    *,
    run_uuid: str,
    config_sha256: str,
    data_manifest_hashes: Mapping[str, str],
    source_commit: str,
    command_scope: Sequence[str],
) -> dict[str, Any]:
    expected = {
        "format": RUN_MANIFEST_VERSION,
        "run_uuid": str(run_uuid),
        "config_sha256": str(config_sha256),
        "data_manifest_hashes": dict(sorted(data_manifest_hashes.items())),
        "initial_source_commit": str(source_commit),
        "command_scope": list(command_scope),
    }
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        for key, value in expected.items():
            if key == "initial_source_commit":
                continue
            if current.get(key) != value:
                raise ValueError(f"Run manifest mismatch for {key}")
        return current
    payload = {**expected, "created_at_utc": utc_now()}
    atomic_write_json(path, payload)
    return payload


def validate_or_record_run_manifest_config_supersession(
    path: Path,
    *,
    run_uuid: str,
    previous_config_sha256: str,
    replacement_config_sha256: str,
    data_manifest_hashes: Mapping[str, str],
    source_commit: str,
    command_scope: Sequence[str],
    parent_attempt_id: str,
    reason: str,
    supersession_path: Path | None = None,
) -> dict[str, Any]:
    """Record a provenance-only config correction without rewriting a run manifest."""
    if not path.exists():
        raise ValueError("Cannot supersede a missing run manifest")
    if not parent_attempt_id:
        raise ValueError("A config supersession requires a parent attempt")
    if not reason:
        raise ValueError("A config supersession requires an explicit reason")
    if previous_config_sha256 == replacement_config_sha256:
        raise ValueError("A config supersession must change the config hash")

    current_bytes = path.read_bytes()
    current = json.loads(current_bytes.decode("utf-8"))
    expected = {
        "format": RUN_MANIFEST_VERSION,
        "run_uuid": str(run_uuid),
        "config_sha256": str(previous_config_sha256),
        "data_manifest_hashes": dict(sorted(data_manifest_hashes.items())),
        "command_scope": list(command_scope),
    }
    for key, value in expected.items():
        if current.get(key) != value:
            raise ValueError(f"Run manifest supersession mismatch for {key}")

    target = supersession_path or path.with_name(
        "run_manifest_supersessions.jsonl"
    )
    identity = {
        "format": RUN_MANIFEST_CONFIG_SUPERSESSION_VERSION,
        "run_uuid": str(run_uuid),
        "original_run_manifest_sha256": hashlib.sha256(current_bytes).hexdigest(),
        "previous_config_sha256": str(previous_config_sha256),
        "replacement_config_sha256": str(replacement_config_sha256),
        "source_commit": str(source_commit),
        "parent_attempt_id": str(parent_attempt_id),
        "reason": str(reason),
        "scientific_parameter_changed": False,
    }
    existing = []
    if target.exists():
        existing = [
            json.loads(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for row in existing:
        if all(row.get(key) == value for key, value in identity.items()):
            return {**current, "effective_config_sha256": replacement_config_sha256}
    if existing:
        raise ValueError("A different run-manifest config supersession already exists")
    append_jsonl_fsync(target, {**identity, "timestamp_utc": utc_now()})
    return {**current, "effective_config_sha256": replacement_config_sha256}
