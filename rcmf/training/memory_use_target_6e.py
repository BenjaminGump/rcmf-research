from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import ast
import hashlib
import json
import math
import random
import re
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import API_CALL_RE, _action_type
from rcmf.training.interaction_representation_6c import (
    fit_two_way_decomposition,
    per_state_ranking_metrics,
    summarize_revised_predictions,
)
from rcmf.training.oracle_convergence_5fa import custom_huber
from rcmf.training.pair_grounding_5d import spearman
from rcmf.training.state_conditioned_transition_6b import DenseTower


TARGET_AUDIT_VERSION = "relative_intent_memory_use_target_6e_v1"

TWO_AXIS_CELL_IDS = {
    "A": "A",
    "B": "B",
    "C": "C",
    "D": "D",
    "train_state__train_transition": "A",
    "heldout_state__train_transition": "B",
    "train_state__heldout_transition": "C",
    "heldout_state__heldout_transition": "D",
}


def canonical_two_axis_cell(value: object) -> str:
    """Map immutable EXP-020 split names to the compact A/B/C/D convention."""
    cell = str(value)
    try:
        return TWO_AXIS_CELL_IDS[cell]
    except KeyError as exc:
        raise ValueError(f"Unknown two-axis cell: {cell!r}") from exc


SERIALIZATION_VERSION = "raw_transition_serialization_audit_6e_v1"
ACTION_SIGNATURE_VERSION = "deterministic_action_signature_6e_v1"
TARGET_NAMES = ("T0", "T3", "T4", "T5", "T6", "T7")
READ_HINTS = (
    "search", "show", "get", "find", "list", "check", "retrieve", "describe",
)
WRITE_HINTS = (
    "create", "update", "delete", "remove", "move", "send", "follow", "play",
    "compress", "login", "complete", "add", "set", "write", "save",
)
PYTHON_CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")


def stable_key(seed: int, *parts: Any) -> str:
    payload = ":".join(str(value) for value in (seed, *parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return {"count": 0}

    def quantile(fraction: float) -> float:
        position = (len(ordered) - 1) * float(fraction)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

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


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    x = torch.tensor(left, dtype=torch.float64)
    y = torch.tensor(right, dtype=torch.float64)
    x = x - x.mean()
    y = y - y.mean()
    denominator = float(torch.linalg.vector_norm(x) * torch.linalg.vector_norm(y))
    return float((x * y).sum() / denominator) if denominator > 0.0 else None


def _api_calls(text: str) -> list[tuple[str, str]]:
    return [(str(app), str(api)) for app, api in API_CALL_RE.findall(text)]


def _ast_call_names(text: str) -> list[str]:
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    source = "\n".join(blocks) if blocks else text
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return sorted(set(PYTHON_CALL_RE.findall(source)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = []
        current: ast.AST = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        if parts:
            names.add(".".join(reversed(parts)))
    return sorted(names)


def action_signature(text: str) -> dict[str, Any]:
    calls = _api_calls(text)
    apps = sorted({app for app, _ in calls})
    apis = sorted({f"{app}.{api}" for app, api in calls})
    api_names = sorted({api for _, api in calls})
    lowered = [api.lower() for api in api_names]
    documentation = any(app == "api_docs" for app in apps)
    authentication = any("login" in api or "authenticate" in api for api in lowered)
    completion = any(
        app == "supervisor" and api in {"complete_task", "complete"}
        for app, api in calls
    )
    read = bool(lowered) and all(any(hint in api for hint in READ_HINTS) for api in lowered)
    write = any(any(hint in api for hint in WRITE_HINTS) for api in lowered)
    python_only = not calls and bool(text.strip())
    primary_app = calls[0][0] if calls else "__no_api__"
    primary_api = f"{calls[0][0]}.{calls[0][1]}" if calls else "__no_api__"
    if completion:
        coarse = "completion"
    elif documentation:
        coarse = "api_documentation"
    elif authentication:
        coarse = "authentication"
    elif write:
        coarse = "write_mutation"
    elif read:
        coarse = "read_query"
    elif calls:
        coarse = _action_type(apis[0])
    else:
        coarse = "python_reasoning"
    return {
        "format": ACTION_SIGNATURE_VERSION,
        "apps": apps,
        "apis": apis,
        "api_names": api_names,
        "primary_app": primary_app,
        "primary_api": primary_api,
        "probe_action_type": _action_type(text),
        "coarse_action_type": coarse,
        "api_documentation_action": documentation,
        "authentication_login_action": authentication,
        "read_query_action": read,
        "write_mutation_action": write,
        "completion_action": completion,
        "python_only_reasoning_action": python_only,
        "function_ast_call_names": _ast_call_names(text),
    }


def transition_fields(transition: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_task_goal": str(transition["source_task_goal"]),
        "source_state_before_action": str(transition["canonical_pre_action_state"]),
        "source_action": str(transition["complete_action"]),
        "source_observation_after_action": str(
            transition["complete_post_action_observation"]
        ),
    }


def transition_teacher_section_for_template(
    transition: Mapping[str, Any], template: str
) -> str:
    fields = transition_fields(transition)
    if template == "canonical_json":
        body = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    elif template == "compact_tagged":
        body = (
            f"<goal>{fields['source_task_goal']}</goal>\n"
            f"<state>{fields['source_state_before_action']}</state>\n"
            f"<action>{fields['source_action']}</action>\n"
            "<observation>"
            f"{fields['source_observation_after_action']}"
            "</observation>"
        )
    else:
        raise ValueError(f"Unknown transition teacher serialization: {template}")
    return f"[DECISION TRANSITION MEMORY]\n\n{body}"


def messages_with_serialized_transition(
    base_messages: Sequence[dict[str, str]],
    transition: Mapping[str, Any],
    prompt_profile: str,
    template: str,
) -> list[dict[str, str]]:
    messages = [dict(message) for message in base_messages]
    initial_count = int(
        appworld_renderer_metadata(prompt_profile)["initial_message_count"]
    )
    section = transition_teacher_section_for_template(transition, template)
    for index in range(initial_count, len(messages)):
        if messages[index].get("role") == "user":
            messages[index]["content"] = (
                f"{section}\n\n"
                "[CURRENT APPWORLD STATE START]\n"
                f"{messages[index]['content']}\n"
                "[CURRENT APPWORLD STATE END]"
            )
            return messages
    raise ValueError("Could not locate current task user message")


def _coverage_select(
    rows: Sequence[Mapping[str, Any]], *, count: int, seed: int, namespace: str
) -> list[dict[str, Any]]:
    remaining = {str(row["pair_id"]): dict(row) for row in rows}
    chosen: list[dict[str, Any]] = []
    task_counts: Counter[str] = Counter()
    parent_counts: Counter[str] = Counter()
    app_counts: Counter[str] = Counter()
    while remaining and len(chosen) < int(count):
        def score(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
            pair_id, row = item
            apps = sorted(
                set(str(value) for value in row.get("state_apps", []))
                | set(str(value) for value in row.get("transition_apps", []))
            )
            return (
                task_counts[str(row["state_task_id"])],
                parent_counts[str(row["transition_parent_id"])],
                sum(app_counts[value] for value in apps),
                stable_key(seed, namespace, pair_id),
                pair_id,
            )

        pair_id, row = min(remaining.items(), key=score)
        chosen.append(row)
        task_counts[str(row["state_task_id"])] += 1
        parent_counts[str(row["transition_parent_id"])] += 1
        for app in set(row.get("state_apps", [])) | set(row.get("transition_apps", [])):
            app_counts[str(app)] += 1
        del remaining[pair_id]
    return chosen


def select_serialization_audit_pairs(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    cells: Sequence[str] = ("A", "D"),
    pairs_per_cell: int = 96,
    category_targets: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    targets = dict(category_targets or {
        "positive": 24, "neutral": 24, "negative": 24, "random": 24
    })
    selected: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for cell in cells:
        pool = [dict(row) for row in rows if str(row["cell"]) == str(cell)]
        used: set[str] = set()
        counts: dict[str, Any] = {}
        for category in ("positive", "neutral", "negative"):
            available = [
                row for row in pool
                if str(row["utility_category"]) == category
                and str(row["pair_id"]) not in used
            ]
            picked = _coverage_select(
                available,
                count=min(targets[category], len(available)),
                seed=seed,
                namespace=f"serialization:{cell}:{category}",
            )
            for row in picked:
                row["audit_selection_category"] = category
                used.add(str(row["pair_id"]))
                selected.append(row)
            counts[category] = {"available": len(available), "selected": len(picked)}
        random_pool = [row for row in pool if str(row["pair_id"]) not in used]
        random_count = min(targets["random"], len(random_pool))
        random_rows = sorted(
            random_pool,
            key=lambda row: (
                stable_key(seed, f"serialization:{cell}:random", row["pair_id"]),
                str(row["pair_id"]),
            ),
        )[:random_count]
        for row in random_rows:
            row["audit_selection_category"] = "random"
            used.add(str(row["pair_id"]))
            selected.append(row)
        counts["random"] = {"available": len(random_pool), "selected": len(random_rows)}
        actual = sum(int(value["selected"]) for value in counts.values())
        if actual != int(pairs_per_cell):
            raise ValueError(
                f"Serialization audit cell {cell} selected {actual}, expected {pairs_per_cell}; "
                f"coverage={counts}"
            )
        selected_cell = [row for row in selected if str(row["cell"]) == str(cell)]
        coverage[str(cell)] = {
            "selection": counts,
            "pair_count": len(selected_cell),
            "query_task_count": len({str(row["state_task_id"]) for row in selected_cell}),
            "transition_parent_count": len({str(row["transition_parent_id"]) for row in selected_cell}),
            "apps": dict(Counter(
                str(app) for row in selected_cell
                for app in set(row.get("state_apps", [])) | set(row.get("transition_apps", []))
            )),
        }
    selected.sort(key=lambda row: (str(row["cell"]), str(row["pair_id"])))
    return {
        "format": SERIALIZATION_VERSION,
        "seed": int(seed),
        "pair_count": len(selected),
        "cells": list(cells),
        "coverage": coverage,
        "rows": selected,
    }


def _average_percentile(values: Sequence[float]) -> list[float]:
    if len(values) <= 1:
        return [0.5 for _ in values]
    order = sorted(range(len(values)), key=lambda index: (float(values[index]), index))
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and float(values[order[end]]) == float(values[order[position]]):
            end += 1
        average_rank = (position + end - 1) / 2.0
        for index in order[position:end]:
            ranks[index] = average_rank / (len(values) - 1)
        position = end
    return ranks


def add_relative_targets(
    rows: Sequence[Mapping[str, Any]], *, scale_epsilon: float, robust_clip: float
) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    output = [dict(row) for row in rows]
    for index, row in enumerate(output):
        grouped[str(row["state_example_id"])].append(index)
    for indices in grouped.values():
        utilities = [float(output[index]["text_utility"]) for index in indices]
        ordered = sorted(utilities)
        median = distribution(utilities)["median"]
        mean = sum(utilities) / len(utilities)
        q1 = distribution(utilities)["p25"]
        q3 = distribution(utilities)["p75"]
        scale = max(float(q3) - float(q1), float(scale_epsilon))
        percentiles = _average_percentile(utilities)
        for local, index in enumerate(indices):
            centered = utilities[local] - float(median)
            output[index].update({
                "T0": utilities[local],
                "T1_median": centered,
                "T1_mean": utilities[local] - mean,
                "T2": max(-float(robust_clip), min(float(robust_clip), centered / scale)),
                "T3": percentiles[local],
                "state_utility_median": median,
                "state_utility_mean": mean,
                "state_utility_iqr": float(q3) - float(q1),
            })
    return output


def pairwise_coverage(
    rows: Sequence[Mapping[str, Any]], *, thresholds: Sequence[float]
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_example_id"])].append(row)
    output: dict[str, Any] = {}
    for threshold in thresholds:
        pair_count = 0
        state_count = 0
        for selected in grouped.values():
            count = 0
            for left in range(len(selected)):
                for right in range(left + 1, len(selected)):
                    if abs(float(selected[left]["text_utility"]) - float(selected[right]["text_utility"])) >= float(threshold):
                        count += 1
            pair_count += count
            state_count += count > 0
        output[f"{float(threshold):.2f}"] = {
            "pair_count": pair_count,
            "state_count": state_count,
            "state_coverage": state_count / len(grouped) if grouped else 0.0,
        }
    return output


def transition_popularity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row["transition_id"])].append(float(row["text_utility"]))
    means = {key: sum(values) / len(values) for key, values in grouped.items()}
    ordered = sorted(means.items(), key=lambda item: (-item[1], item[0]))
    return {"distribution": distribution(list(means.values())), "ordered": ordered}


def _matrix_spectrum(
    rows: Sequence[Mapping[str, Any]],
    *,
    value_fn: Any,
    missing_fn: Any,
) -> dict[str, Any]:
    states = sorted({str(row["state_example_id"]) for row in rows})
    transitions = sorted({str(row["transition_id"]) for row in rows})
    state_position = {value: index for index, value in enumerate(states)}
    transition_position = {value: index for index, value in enumerate(transitions)}
    matrix = torch.empty(len(states), len(transitions), dtype=torch.float64)
    for state in states:
        for transition in transitions:
            matrix[state_position[state], transition_position[transition]] = float(
                missing_fn(state, transition)
            )
    for row in rows:
        matrix[
            state_position[str(row["state_example_id"])],
            transition_position[str(row["transition_id"])],
        ] = float(value_fn(row))
    singular = torch.linalg.svdvals(matrix)
    nonzero = singular[singular > 1.0e-12]
    if not len(nonzero):
        return {"shape": list(matrix.shape), "rank": 0, "effective_rank": 0.0, "stable_rank": 0.0, "singular_values": []}
    probabilities = nonzero / nonzero.sum()
    return {
        "shape": list(matrix.shape),
        "rank": int(nonzero.numel()),
        "effective_rank": float(torch.exp(-(probabilities * probabilities.log()).sum())),
        "stable_rank": float(nonzero.square().sum() / nonzero.max().square()),
        "singular_values": [float(value) for value in singular],
    }


def decompose_locked_utility(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cell_a = [row for row in rows if str(row["cell"]) == "A"]
    decomposition = fit_two_way_decomposition(cell_a)
    state_effects = decomposition["state_effects"]
    transition_effects = decomposition["transition_effects"]
    mu = float(decomposition["mu"])
    summaries = {}
    for cell in ("A", "B", "C", "D"):
        selected = [row for row in rows if str(row["cell"]) == cell]
        raw = [float(row["text_utility"]) for row in selected]
        residual = []
        state_component = []
        transition_component = []
        additive_component = []
        for row in selected:
            a = float(state_effects.get(str(row["state_example_id"]), 0.0))
            b = float(transition_effects.get(str(row["transition_id"]), 0.0))
            state_component.append(a)
            transition_component.append(b)
            additive_component.append(a + b)
            residual.append(float(row["text_utility"]) - mu - a - b)
        summaries[cell] = {
            "row_count": len(selected),
            "raw_utility": distribution(raw),
            "state_main_variance": distribution(state_component).get("std", 0.0) ** 2,
            "transition_main_variance": distribution(transition_component).get("std", 0.0) ** 2,
            "additive_main_variance": distribution(additive_component).get("std", 0.0) ** 2,
            "residual_variance": distribution(residual).get("std", 0.0) ** 2,
            "residual": distribution(residual),
            "sign_counts": dict(Counter(str(row["utility_category"]) for row in selected)),
            "utility_scale_per_state": distribution([
                float(value["std"])
                for state in {str(row["state_example_id"]) for row in selected}
                if (value := distribution([
                    float(row["text_utility"]) for row in selected
                    if str(row["state_example_id"]) == state
                ])).get("count", 0)
            ]),
            "transition_popularity": transition_popularity(selected),
            "raw_utility_spectrum": _matrix_spectrum(
                selected,
                value_fn=lambda row: float(row["text_utility"]),
                missing_fn=lambda state, transition: (
                    mu
                    + float(state_effects.get(state, 0.0))
                    + float(transition_effects.get(transition, 0.0))
                ),
            ),
            "residual_spectrum": _matrix_spectrum(
                selected,
                value_fn=lambda row: (
                    float(row["text_utility"])
                    - mu
                    - float(state_effects.get(str(row["state_example_id"]), 0.0))
                    - float(transition_effects.get(str(row["transition_id"]), 0.0))
                ),
                missing_fn=lambda _state, _transition: 0.0,
            ),
        }
    return {"format": TARGET_AUDIT_VERSION, "cell_a_decomposition": decomposition, "cells": summaries}


def intent_feature_vector(
    query_probabilities: Mapping[str, Mapping[str, float]],
    transition_signature: Mapping[str, Any],
) -> list[float]:
    apps = {str(transition_signature.get("primary_app", "__no_api__"))}
    apis = {str(transition_signature.get("primary_api", "__no_api__"))}
    action = str(transition_signature["probe_action_type"])
    completion = "true" if bool(transition_signature["completion_action"]) else "false"
    return [
        sum(float(query_probabilities.get("target_app", {}).get(value, 0.0)) for value in apps),
        sum(float(query_probabilities.get("target_api", {}).get(value, 0.0)) for value in apis),
        float(query_probabilities.get("action_type", {}).get(action, 0.0)),
        float(query_probabilities.get("completion_action", {}).get(completion, 0.0)),
    ]


class IntentCompatibilityModel(nn.Module):
    def __init__(self, feature_dim: int = 4) -> None:
        super().__init__()
        self.linear = nn.Linear(feature_dim, 1)

    def forward(self, features: Tensor) -> Tensor:
        return self.linear(features).squeeze(-1)


class CachedArchitectureScorer(nn.Module):
    def __init__(
        self,
        kind: str,
        *,
        state_views: int,
        transition_views: int,
        input_dim: int,
        cross_dim: int,
        projection_dim: int,
        interaction_rank: int,
        hidden_dim: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.kind = str(kind)
        if kind == "field":
            self.state_projection = nn.ModuleList(
                nn.Linear(input_dim, projection_dim, bias=False)
                for _ in range(state_views)
            )
            self.transition_projection = nn.ModuleList(
                nn.Linear(input_dim, projection_dim, bias=False)
                for _ in range(transition_views)
            )
            self.state_rank = nn.Linear(projection_dim, interaction_rank, bias=False)
            self.transition_rank = nn.Linear(projection_dim, interaction_rank, bias=False)
            self.tensor_core = nn.Parameter(
                torch.empty(state_views, transition_views, interaction_rank)
            )
            nn.init.normal_(self.tensor_core, std=1.0 / math.sqrt(interaction_rank))
        elif kind == "cross":
            self.cross_head = DenseTower(
                cross_dim, 1, hidden_dim=hidden_dim, dropout=dropout
            )
        else:
            raise ValueError(f"Unknown cached architecture: {kind}")

    def forward(
        self,
        state: Tensor | None = None,
        transition: Tensor | None = None,
        cross: Tensor | None = None,
    ) -> Tensor:
        if self.kind == "cross":
            if cross is None:
                raise ValueError("Cross features are required")
            return self.cross_head(cross).squeeze(-1)
        if state is None or transition is None:
            raise ValueError("State and transition views are required")
        q = torch.stack(
            [layer(state[:, index]) for index, layer in enumerate(self.state_projection)],
            dim=1,
        )
        k = torch.stack(
            [layer(transition[:, index]) for index, layer in enumerate(self.transition_projection)],
            dim=1,
        )
        q = self.state_rank(q)
        k = self.transition_rank(k)
        return torch.einsum("bvr,vwr,bwr->b", q, self.tensor_core, k) / math.sqrt(
            q.shape[1] * k.shape[1] * q.shape[-1]
        )


def _state_groups(rows: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row["state_example_id"])].append(index)
    return [grouped[key] for key in sorted(grouped)]


def relative_target_objective(
    scores: Tensor,
    rows: Sequence[Mapping[str, Any]],
    *,
    target_name: str,
    pair_gap_threshold: float,
    pair_gap_weight_clip: float,
    teacher_temperature: float,
    student_temperature: float,
    huber_delta: float,
    loss_weights: Mapping[str, float],
    matched_intent_only: bool = False,
) -> tuple[Tensor, dict[str, Tensor]]:
    utilities = torch.tensor(
        [float(row["text_utility"]) for row in rows],
        device=scores.device,
        dtype=scores.dtype,
    )
    percentiles = torch.tensor(
        [float(row["T3"]) for row in rows], device=scores.device, dtype=scores.dtype
    )
    groups = _state_groups(rows)
    regression_target = utilities if target_name == "T0" else percentiles
    regression = custom_huber(scores - regression_target, delta=float(huber_delta)).mean()
    listwise_terms = []
    pairwise_terms = []
    for indices in groups:
        index = torch.tensor(indices, device=scores.device, dtype=torch.long)
        utility = utilities[index]
        score = scores[index]
        teacher = torch.softmax(utility / float(teacher_temperature), dim=0)
        student_log = torch.log_softmax(score / float(student_temperature), dim=0)
        listwise_terms.append(-(teacher * student_log).sum())
        gap = utility[:, None] - utility[None, :]
        pair_mask = torch.triu(
            torch.ones_like(gap, dtype=torch.bool), diagonal=1
        ) & (gap.abs() >= float(pair_gap_threshold))
        if matched_intent_only:
            signatures = [rows[value]["transition_signature"] for value in indices]
            app_vocabulary = sorted({
                str(app) for signature in signatures for app in signature["apps"]
            })
            app_position = {app: position for position, app in enumerate(app_vocabulary)}
            membership = torch.zeros(
                (len(indices), len(app_vocabulary)),
                device=scores.device,
                dtype=torch.float32,
            )
            for row_index, signature in enumerate(signatures):
                for app in signature["apps"]:
                    membership[row_index, app_position[str(app)]] = 1.0
            same_app = membership @ membership.T > 0.0
            type_vocabulary = {
                value: position for position, value in enumerate(sorted({
                    str(signature["coarse_action_type"]) for signature in signatures
                }))
            }
            type_ids = torch.tensor(
                [type_vocabulary[str(signature["coarse_action_type"])] for signature in signatures],
                device=scores.device,
                dtype=torch.long,
            )
            pair_mask &= same_app & (type_ids[:, None] == type_ids[None, :])
        if pair_mask.any():
            direction = gap.sign()
            weight = (gap.abs() / float(pair_gap_weight_clip)).clamp(max=1.0)
            score_gap = score[:, None] - score[None, :]
            pairwise_terms.append(
                (weight * F.softplus(-direction * score_gap))[pair_mask]
            )
    listwise = torch.stack(listwise_terms).mean() if listwise_terms else scores.sum() * 0.0
    pairwise = torch.cat(pairwise_terms).mean() if pairwise_terms else scores.sum() * 0.0
    if target_name == "T0":
        total = regression + float(loss_weights["listwise"]) * listwise + float(loss_weights["pairwise"]) * pairwise
    elif target_name in {"T3", "T6"}:
        total = float(loss_weights["percentile_regression"]) * regression + float(loss_weights["listwise"]) * listwise + float(loss_weights["pairwise"]) * pairwise
    else:
        total = pairwise + 0.1 * listwise
    return total, {"regression": regression, "listwise": listwise, "pairwise": pairwise}


def gap_weighted_pairwise_accuracy(
    rows: Sequence[Mapping[str, Any]], *, threshold: float, weight_clip: float
) -> float | None:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_example_id"])].append(row)
    numerator = 0.0
    denominator = 0.0
    for selected in grouped.values():
        for left in range(len(selected)):
            for right in range(left + 1, len(selected)):
                gap = float(selected[left]["u_text"]) - float(selected[right]["u_text"])
                if abs(gap) < float(threshold):
                    continue
                weight = min(abs(gap) / float(weight_clip), 1.0)
                predicted_gap = float(selected[left]["u_predicted"]) - float(selected[right]["u_predicted"])
                numerator += weight * float((gap > 0.0) == (predicted_gap > 0.0))
                denominator += weight
    return numerator / denominator if denominator > 0.0 else None


def summarize_target_predictions(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_key: str,
    ranking_ks: Sequence[int],
    neutral_epsilon: float,
    best_tie_tolerance: float,
    huber_delta: float,
    pair_gap_threshold: float,
    pair_gap_weight_clip: float,
) -> dict[str, Any]:
    normalized = []
    candidate = []
    for row in rows:
        base = {
            **dict(row),
            "u_text": float(row["text_utility"]),
            "u_predicted": float(row["score"]),
            "residual_target": float(row.get("raw_residual_target", 0.0)),
            "residual_predicted": float(row.get("interaction_score", row["score"])),
        }
        normalized.append(base)
        candidate.append({**base, "u_text": float(row[target_key])})
    kwargs = {
        "ranking_ks": ranking_ks,
        "neutral_epsilon": neutral_epsilon,
        "best_tie_tolerance": best_tie_tolerance,
        "huber_delta": huber_delta,
    }
    raw = summarize_revised_predictions(normalized, **kwargs)
    revised = summarize_revised_predictions(candidate, **kwargs)
    raw.pop("per_state_rows", None)
    revised.pop("per_state_rows", None)
    raw_errors = [float(row["score"]) - float(row["text_utility"]) for row in rows]
    target_errors = [float(row["score"]) - float(row[target_key]) for row in rows]
    return {
        "count": len(rows),
        "raw_utility": raw,
        "candidate_target": revised,
        "raw_utility_huber": sum(
            float(custom_huber(torch.tensor(value), delta=huber_delta))
            for value in raw_errors
        ) / len(raw_errors),
        "candidate_target_huber": sum(
            float(custom_huber(torch.tensor(value), delta=huber_delta))
            for value in target_errors
        ) / len(target_errors),
        "gap_weighted_pairwise_accuracy": gap_weighted_pairwise_accuracy(
            normalized,
            threshold=pair_gap_threshold,
            weight_clip=pair_gap_weight_clip,
        ),
    }


def serialization_robustness(
    rows: Sequence[Mapping[str, Any]], *, gate: Mapping[str, float]
) -> dict[str, Any]:
    grouped_pair: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        grouped_pair[str(row["pair_id"])][str(row["template"])] = row
    complete = {
        pair_id: values for pair_id, values in grouped_pair.items()
        if {"template0", "canonical_json", "compact_tagged"}.issubset(values)
        and all(values[name].get("text_utility") is not None for name in values)
    }
    template_pairs = (
        ("template0", "canonical_json"),
        ("template0", "compact_tagged"),
        ("canonical_json", "compact_tagged"),
    )
    comparisons = {}
    sign_values = []
    per_state_top4 = []
    state_groups: dict[str, list[str]] = defaultdict(list)
    for pair_id, values in complete.items():
        state_groups[str(values["template0"]["state_example_id"])].append(pair_id)
    for left, right in template_pairs:
        left_values = [float(values[left]["text_utility"]) for values in complete.values()]
        right_values = [float(values[right]["text_utility"]) for values in complete.values()]
        non_neutral = [
            index for index, (a, b) in enumerate(zip(left_values, right_values))
            if abs(a) > 0.01 and abs(b) > 0.01
        ]
        sign = (
            sum((left_values[index] > 0.0) == (right_values[index] > 0.0) for index in non_neutral) / len(non_neutral)
            if non_neutral else None
        )
        state_spearman = []
        overlaps = {1: [], 4: [], 8: []}
        positive_mass_overlap = []
        for pair_ids in state_groups.values():
            if len(pair_ids) < 2:
                continue
            a = [float(complete[pair_id][left]["text_utility"]) for pair_id in pair_ids]
            b = [float(complete[pair_id][right]["text_utility"]) for pair_id in pair_ids]
            value = spearman(a, b)
            if value is not None:
                state_spearman.append(float(value))
            for k in overlaps:
                left_top = set(sorted(pair_ids, key=lambda pair_id: (-float(complete[pair_id][left]["text_utility"]), pair_id))[:k])
                right_top = set(sorted(pair_ids, key=lambda pair_id: (-float(complete[pair_id][right]["text_utility"]), pair_id))[:k])
                overlaps[k].append(len(left_top & right_top) / max(1, min(k, len(pair_ids))))
            left_gain = [max(value - 0.01, 0.0) for value in a]
            right_gain = [max(value - 0.01, 0.0) for value in b]
            left_total = sum(left_gain)
            right_total = sum(right_gain)
            if left_total > 0.0 and right_total > 0.0:
                positive_mass_overlap.append(sum(
                    min(left_gain[index] / left_total, right_gain[index] / right_total)
                    for index in range(len(pair_ids))
                ))
        comparison = {
            "pearson": pearson(left_values, right_values),
            "spearman": spearman(left_values, right_values),
            "sign_agreement": sign,
            "mean_absolute_utility_change": sum(abs(a - b) for a, b in zip(left_values, right_values)) / len(left_values),
            "per_state_spearman": distribution(state_spearman),
            "per_state_top1_overlap": distribution(overlaps[1]),
            "per_state_top4_overlap": distribution(overlaps[4]),
            "per_state_top8_overlap": distribution(overlaps[8]),
            "positive_utility_mass_overlap": distribution(positive_mass_overlap),
        }
        comparisons[f"{left}__{right}"] = comparison
        if sign is not None:
            sign_values.append(sign)
        per_state_top4.extend(overlaps[4])
    paired_length_deltas = []
    paired_utility_deltas = []
    template_means = {}
    for template in ("template0", "canonical_json", "compact_tagged"):
        selected = [values[template] for values in complete.values()]
        template_means[template] = {
            "utility": distribution([float(row["text_utility"]) for row in selected]),
            "combined_tokens": distribution([float(row["combined_prompt_tokens"]) for row in selected]),
        }
        if template != "template0":
            for values in complete.values():
                paired_length_deltas.append(
                    float(values[template]["combined_prompt_tokens"])
                    - float(values["template0"]["combined_prompt_tokens"])
                )
                paired_utility_deltas.append(
                    float(values[template]["text_utility"])
                    - float(values["template0"]["text_utility"])
                )
    pair_spearman = [float(value["spearman"]) for value in comparisons.values() if value["spearman"] is not None]
    median_spearman = distribution(pair_spearman).get("median")
    mean_sign = sum(sign_values) / len(sign_values) if sign_values else None
    mean_top4 = sum(per_state_top4) / len(per_state_top4) if per_state_top4 else None
    length_correlation = pearson(paired_length_deltas, paired_utility_deltas)
    stratified: dict[str, Any] = {}
    for feature, getter in (
        ("app", lambda row: ",".join(sorted(str(value) for value in row.get("transition_apps", []))) or "none"),
        ("action_type", lambda row: str(row.get("transition_action_type", "unknown"))),
        ("utility_magnitude", lambda row: (
            "small" if abs(float(row["text_utility"])) < 0.05 else
            "medium" if abs(float(row["text_utility"])) < 0.25 else "large"
        )),
    ):
        grouped: dict[str, list[float]] = defaultdict(list)
        for values in complete.values():
            base = values["template0"]
            for template in ("canonical_json", "compact_tagged"):
                grouped[getter(base)].append(
                    float(values[template]["text_utility"])
                    - float(base["text_utility"])
                )
        stratified[feature] = {
            key: distribution(changes) for key, changes in sorted(grouped.items())
        }
    checks = {
        "median_template_spearman": median_spearman is not None and median_spearman >= float(gate["median_template_spearman"]),
        "sign_agreement": mean_sign is not None and mean_sign >= float(gate["sign_agreement"]),
        "mean_per_state_top4_overlap": mean_top4 is not None and mean_top4 >= float(gate["mean_per_state_top4_overlap"]),
        "no_systematic_length_sign": length_correlation is None or abs(length_correlation) <= float(gate["maximum_length_utility_abs_correlation"]),
    }
    return {
        "format": SERIALIZATION_VERSION,
        "complete_pair_count": len(complete),
        "comparisons": comparisons,
        "median_pairwise_template_spearman": median_spearman,
        "mean_sign_agreement": mean_sign,
        "mean_per_state_top4_overlap": mean_top4,
        "length_utility_pearson": length_correlation,
        "templates": template_means,
        "stability_by_app_action_type_and_utility_magnitude": stratified,
        "gate_checks": checks,
        "gate_passed": all(checks.values()),
    }
