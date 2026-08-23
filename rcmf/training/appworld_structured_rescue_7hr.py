from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
import statistics
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F


GLOBAL_SEED = 25101
LABELS = ("POSITIVE", "NEUTRAL", "HARMFUL")
LABEL_TO_INDEX = {value: index for index, value in enumerate(LABELS)}


def stable_key(seed: int, *parts: Any) -> str:
    payload = ":".join(str(value) for value in (seed, *parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_paired_outcome(
    bare: Mapping[str, Any], raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply the preregistered one-step label rules with conservative overlap handling."""

    b_successor = bool(bare["semantic_successor_match"])
    r_successor = bool(raw["semantic_successor_match"])
    b_signature = bool(bare["action_signature_match"])
    r_signature = bool(raw["action_signature_match"])
    b_execution = bool(bare["execution_success"])
    r_execution = bool(raw["execution_success"])
    positive_rules = {
        "successor_better": r_successor and not b_successor,
        "signature_better_on_successor_tie": (
            r_successor == b_successor
            and r_signature
            and not b_signature
            and (r_execution or not b_execution)
        ),
    }
    harmful_rules = {
        "successor_worse": b_successor and not r_successor,
        "execution_worse": b_execution and not r_execution,
        "signature_worse_on_successor_tie": (
            r_successor == b_successor and b_signature and not r_signature
        ),
    }
    positive = any(positive_rules.values())
    harmful = any(harmful_rules.values())
    # The written rules overlap when successor improves but execution regresses.
    # The fixed prospective policy treats that overlap as harmful.
    if harmful:
        label = "HARMFUL"
    elif positive:
        label = "POSITIVE"
    else:
        label = "NEUTRAL"
    return {
        "label": label,
        "positive_rules": positive_rules,
        "harmful_rules": harmful_rules,
        "rule_overlap": positive and harmful,
        "overlap_resolution": "harmful_precedence_for_execution_safety",
    }


def quantile_buckets(values: Sequence[float], bucket_count: int) -> list[int]:
    if bucket_count <= 0:
        raise ValueError("bucket_count must be positive")
    if not values:
        return []
    ordered = sorted(range(len(values)), key=lambda index: (float(values[index]), index))
    output = [0] * len(values)
    for rank, index in enumerate(ordered):
        output[index] = min(bucket_count - 1, rank * bucket_count // len(values))
    return output


def select_diverse_panel(
    rows: Sequence[Mapping[str, Any]],
    *,
    count: int,
    seed: int = GLOBAL_SEED,
) -> list[str]:
    """Outcome-blind greedy coverage over task, stage, intent, and score strata."""

    if len({str(row["state_example_id"]) for row in rows}) != len(rows):
        raise ValueError("Panel candidates contain duplicate state identities")
    if count <= 0 or count > len(rows):
        raise ValueError("Panel size is outside the candidate range")
    dimensions = (
        "state_task_id",
        "step_bucket",
        "predicted_action_stratum",
        "selector_score_quantile",
        "selector_margin_quantile",
    )
    counters = {name: Counter() for name in dimensions}
    selected: list[Mapping[str, Any]] = []
    remaining = list(rows)
    while len(selected) < count:
        def priority(row: Mapping[str, Any]) -> tuple[float, str]:
            coverage = 0.0
            for name in dimensions:
                value = str(row[name])
                weight = 8.0 if name == "state_task_id" else 1.0
                coverage += weight / (1.0 + counters[name][value])
            return (-coverage, stable_key(seed, "panel", row["state_example_id"]))

        chosen = min(remaining, key=priority)
        selected.append(chosen)
        remaining.remove(chosen)
        for name in dimensions:
            counters[name][str(chosen[name])] += 1
    if len({str(row["state_task_id"]) for row in selected}) != len(
        {str(row["state_task_id"]) for row in rows}
    ):
        raise RuntimeError("Initial panel failed complete train-task coverage")
    return [str(row["state_example_id"]) for row in selected]


def expansion_order(
    rows: Sequence[Mapping[str, Any]],
    selected_ids: Sequence[str],
    *,
    seed: int = GLOBAL_SEED,
) -> list[str]:
    selected = set(selected_ids)
    return [
        str(row["state_example_id"])
        for row in sorted(
            (row for row in rows if str(row["state_example_id"]) not in selected),
            key=lambda row: stable_key(seed, "panel-expansion", row["state_example_id"]),
        )
    ]


def required_expansion(label_counts: Mapping[str, int], minimum: int) -> bool:
    return any(int(label_counts.get(label, 0)) < minimum for label in LABELS)


@dataclass(frozen=True)
class FeatureSchema:
    app_vocabulary: tuple[str, ...]
    api_vocabulary: tuple[str, ...]
    action_vocabulary: tuple[str, ...]
    control_vocabulary: tuple[str, ...]
    version: str = "appworld_deployment_structured_features_7hr_v1"

    @property
    def names(self) -> tuple[str, ...]:
        scalar = (
            "state.step_index",
            "state.history_turn_count",
            "state.prompt_token_fraction",
            "state.context_headroom_fraction",
            "state.intent_app_confidence",
            "state.intent_app_margin",
            "state.intent_api_confidence",
            "state.intent_api_margin",
            "state.intent_action_confidence",
            "state.intent_action_margin",
            "state.completion_probability",
            "selector.top1_score",
            "selector.top1_top2_margin",
            "selector.score_entropy",
            "selector.score_mean",
            "selector.score_std",
            "selector.score_min",
            "selector.score_max",
            "memory.log_class_size",
            "memory.token_length_fraction",
            "memory.parent_step_early",
            "memory.parent_step_middle",
            "memory.parent_step_late",
            "memory.api_call_count",
            "memory.authentication_flag",
            "memory.read_flag",
            "memory.write_flag",
            "memory.documentation_flag",
            "memory.completion_flag",
            "pair.predicted_memory_app_mass",
            "pair.predicted_memory_api_mass",
            "pair.predicted_action_type_compatibility",
            "pair.documentation_compatibility",
            "pair.context_overhead_fraction",
            "pair.stage_compatibility_score",
            "pair.stage_compatible",
            "pair.stage_conflict_fraction",
        )
        return (
            scalar
            + tuple(f"state.app_probability[{value}]" for value in self.app_vocabulary)
            + tuple(f"state.api_probability[{value}]" for value in self.api_vocabulary)
            + tuple(f"state.action_probability[{value}]" for value in self.action_vocabulary)
            + tuple(f"memory.app_present[{value}]" for value in self.app_vocabulary)
            + tuple(f"memory.api_present[{value}]" for value in self.api_vocabulary)
            + tuple(f"memory.action_type[{value}]" for value in self.action_vocabulary)
            + tuple(f"memory.control_flow[{value}]" for value in self.control_vocabulary)
        )


def _confidence_and_margin(distribution: Mapping[str, float]) -> tuple[float, float]:
    values = sorted((float(value) for value in distribution.values()), reverse=True)
    if not values:
        return 0.0, 0.0
    return values[0], values[0] - (values[1] if len(values) > 1 else 0.0)


def _entropy(values: Sequence[float]) -> float:
    probabilities = torch.softmax(torch.tensor(values, dtype=torch.float64), dim=0)
    return float(-(probabilities * probabilities.clamp_min(1.0e-12).log()).sum())


def build_feature_vector(
    schema: FeatureSchema,
    source: Mapping[str, Any],
) -> tuple[list[float], list[str]]:
    intent = source["intent_distributions"]
    app = {str(key): float(value) for key, value in intent["target_app"].items()}
    api = {str(key): float(value) for key, value in intent["target_api"].items()}
    action = {str(key): float(value) for key, value in intent["action_type"].items()}
    completion = {str(key): float(value) for key, value in intent["completion_action"].items()}
    app_confidence, app_margin = _confidence_and_margin(app)
    api_confidence, api_margin = _confidence_and_margin(api)
    action_confidence, action_margin = _confidence_and_margin(action)
    class_scores = [float(value) for value in source["selector_class_scores"]]
    if len(class_scores) < 2:
        raise ValueError("Structured features require at least two selector classes")
    ordered_scores = sorted(class_scores, reverse=True)
    memory_apps = {str(value) for value in source["memory_apps"]}
    memory_apis = {str(value) for value in source["memory_apis"]}
    memory_action = str(source["memory_action_type"])
    controls = {str(value) for value in source["memory_control_flow"]}
    flags = source["memory_flags"]
    stage = source["stage_compatibility"]
    context_limit = float(source["context_limit"])
    parent_step = int(source["memory_parent_step"])
    values = [
        float(source["state_step_index"]),
        float(source["history_turn_count"]),
        float(source["prompt_tokens"]) / context_limit,
        float(source["context_headroom"]) / context_limit,
        app_confidence,
        app_margin,
        api_confidence,
        api_margin,
        action_confidence,
        action_margin,
        float(completion.get("true", 0.0)),
        ordered_scores[0],
        ordered_scores[0] - ordered_scores[1],
        _entropy(class_scores),
        statistics.fmean(class_scores),
        statistics.pstdev(class_scores),
        min(class_scores),
        max(class_scores),
        math.log1p(float(source["memory_class_size"])),
        float(source["memory_token_length"]) / context_limit,
        float(parent_step <= 3),
        float(3 < parent_step <= 8),
        float(parent_step > 8),
        float(source["memory_api_call_count"]),
        float(flags["authentication"]),
        float(flags["read"]),
        float(flags["write"]),
        float(flags["documentation"]),
        float(flags["completion"]),
        sum(app.get(value, 0.0) for value in memory_apps),
        sum(api.get(value, 0.0) for value in memory_apis),
        action.get(memory_action, 0.0),
        (
            action.get("api_documentation", 0.0)
            if bool(flags["documentation"])
            else 1.0 - action.get("api_documentation", 0.0)
        ),
        float(source["projected_prompt_overhead"]) / context_limit,
        float(stage["score"]),
        float(stage["compatible"]),
        float(stage["conflict_count"]) / 8.0,
    ]
    values.extend(app.get(value, 0.0) for value in schema.app_vocabulary)
    values.extend(api.get(value, 0.0) for value in schema.api_vocabulary)
    values.extend(action.get(value, 0.0) for value in schema.action_vocabulary)
    values.extend(float(value in memory_apps) for value in schema.app_vocabulary)
    values.extend(float(value in memory_apis) for value in schema.api_vocabulary)
    values.extend(float(value == memory_action) for value in schema.action_vocabulary)
    values.extend(float(value in controls) for value in schema.control_vocabulary)
    names = list(schema.names)
    if len(values) != len(names):
        raise RuntimeError(f"Feature vector/name mismatch: {len(values)} != {len(names)}")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Structured feature vector contains non-finite values")
    return values, names


def leakage_audit(
    names: Sequence[str], forbidden_fields: Sequence[str]
) -> dict[str, Any]:
    lowered = {str(value).lower() for value in forbidden_fields}
    violations = [
        name
        for name in names
        if any(forbidden in name.lower() for forbidden in lowered)
    ]
    return {
        "feature_count": len(names),
        "unique_feature_count": len(set(names)),
        "forbidden_fields": list(forbidden_fields),
        "violations": violations,
        "deployment_available": not violations and len(set(names)) == len(names),
        "target_or_outcome_features": False,
        "free_identity_embeddings": False,
    }


class MemoryUseGate(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 64) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, len(LABELS)),
        )

    def forward(self, values: Tensor) -> Tensor:
        return self.network(values)


def standardize_fit(values: Tensor) -> tuple[Tensor, Tensor]:
    mean = values.mean(dim=0)
    std = values.std(dim=0, unbiased=False).clamp_min(1.0e-6)
    return mean, std


def class_balanced_weights(labels: Tensor) -> Tensor:
    counts = torch.bincount(labels, minlength=len(LABELS)).to(torch.float32)
    if (counts == 0).any():
        raise ValueError("Gate training split does not contain every label")
    weights = counts.sum() / (len(LABELS) * counts)
    return weights


def paired_policy_metrics(
    rows: Sequence[Mapping[str, Any]],
    positive_probability: Sequence[float],
    harmful_probability: Sequence[float],
    threshold: float,
    maximum_harmful_probability: float,
) -> dict[str, Any]:
    if len(rows) != len(positive_probability) or len(rows) != len(harmful_probability):
        raise ValueError("Gate probability and validation row counts differ")
    on = [
        float(pos) >= threshold and float(harm) <= maximum_harmful_probability
        for pos, harm in zip(positive_probability, harmful_probability, strict=True)
    ]
    selected = [row["raw_metrics"] if active else row["bare_metrics"] for row, active in zip(rows, on, strict=True)]
    bare = [row["bare_metrics"] for row in rows]
    total_positive = statistics.fmean(float(row["label"] == "POSITIVE") for row in rows)
    active_positive = (
        statistics.fmean(
            float(row["label"] == "POSITIVE")
            for row, active in zip(rows, on, strict=True)
            if active
        )
        if any(on)
        else 0.0
    )
    def mean(metric_rows: Sequence[Mapping[str, Any]], key: str) -> float:
        return statistics.fmean(float(row[key]) for row in metric_rows)
    harmful_on = sum(
        active and row["label"] == "HARMFUL"
        for row, active in zip(rows, on, strict=True)
    )
    return {
        "threshold": float(threshold),
        "activation_count": sum(on),
        "activation_rate": statistics.fmean(map(float, on)),
        "harmful_activation_count": harmful_on,
        "harmful_activation_rate": harmful_on / max(1, sum(on)),
        "total_positive_prevalence": total_positive,
        "active_positive_prevalence": active_positive,
        "positive_prevalence_lift": active_positive - total_positive,
        "bare_successor": mean(bare, "semantic_successor_match"),
        "gated_successor": mean(selected, "semantic_successor_match"),
        "bare_signature": mean(bare, "action_signature_match"),
        "gated_signature": mean(selected, "action_signature_match"),
        "bare_execution": mean(bare, "execution_success"),
        "gated_execution": mean(selected, "execution_success"),
    }


def select_gate_threshold(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        raise ValueError("No gate-threshold candidates")
    return max(
        (dict(row) for row in candidates),
        key=lambda row: (
            float(row["gated_successor"]),
            float(row["gated_signature"]),
            float(row["gated_execution"]),
            -int(row["harmful_activation_count"]),
            -float(row["activation_rate"]),
            float(row["threshold"]),
        ),
    )


def gate_validation(
    metrics: Mapping[str, Any],
    *,
    minimum_activation_rate: float,
    maximum_activation_rate: float,
    maximum_harmful_activation_rate: float,
    minimum_positive_prevalence_lift: float,
    maximum_execution_drop: float,
) -> dict[str, Any]:
    checks = {
        "successor_noninferior": float(metrics["gated_successor"]) >= float(metrics["bare_successor"]),
        "signature_noninferior": float(metrics["gated_signature"]) >= float(metrics["bare_signature"]),
        "execution_noninferior": float(metrics["gated_execution"]) >= float(metrics["bare_execution"]) - maximum_execution_drop,
        "activation_rate": minimum_activation_rate <= float(metrics["activation_rate"]) <= maximum_activation_rate,
        "harmful_activation_rate": float(metrics["harmful_activation_rate"]) <= maximum_harmful_activation_rate,
        "positive_prevalence_lift": float(metrics["positive_prevalence_lift"]) >= minimum_positive_prevalence_lift,
    }
    return {"checks": checks, "passed": all(checks.values())}


class StructuredCorrectionNetwork(nn.Module):
    def __init__(self, feature_dim: int, hidden_dim: int = 128, program_dim: int = 256) -> None:
        super().__init__()
        self.input = nn.Linear(feature_dim, hidden_dim)
        self.output = nn.Linear(hidden_dim, program_dim)
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    def forward(self, features: Tensor) -> Tensor:
        return self.output(F.gelu(self.input(features)))


class StructuredLatentComposer(nn.Module):
    def __init__(self, feature_dim: int, hidden_dim: int = 128, program_dim: int = 256) -> None:
        super().__init__()
        self.structured = StructuredCorrectionNetwork(feature_dim, hidden_dim, program_dim)
        self.beta = nn.Parameter(torch.zeros(()))

    def forward(self, features: Tensor, base_latent: Tensor, gate_probability: Tensor) -> Tensor:
        if gate_probability.ndim == 1:
            gate_probability = gate_probability.unsqueeze(1)
        return gate_probability * (self.beta * base_latent + self.structured(features))


def compiler_checkpoint_score(metrics: Mapping[str, Any]) -> dict[str, Any]:
    eligible = (
        (
            float(metrics["correct_successor"]) > float(metrics["zero_successor"])
            or float(metrics["correct_signature"]) > float(metrics["zero_signature"])
        )
        and (
            float(metrics["correct_successor"]) > float(metrics["transition_shuffle_successor"])
            or float(metrics["correct_signature"]) > float(metrics["transition_shuffle_signature"])
            or float(metrics["correct_successor"]) > float(metrics["state_shuffle_successor"])
            or float(metrics["correct_signature"]) > float(metrics["state_shuffle_signature"])
        )
        and float(metrics["correct_execution"]) >= float(metrics["zero_execution"]) - 0.05
        and float(metrics["maximum_ratio"]) <= 1.0 + 1.0e-4
        and all(math.isfinite(float(value)) for value in metrics.values() if isinstance(value, (int, float)))
    )
    score = (
        float(metrics["correct_successor"])
        - max(float(metrics["transition_shuffle_successor"]), float(metrics["state_shuffle_successor"]))
        + 0.5
        * (
            float(metrics["correct_signature"])
            - max(float(metrics["transition_shuffle_signature"]), float(metrics["state_shuffle_signature"]))
        )
        + 0.25 * (float(metrics["correct_successor"]) - float(metrics["zero_successor"]))
        - max(0.0, float(metrics["zero_execution"]) - float(metrics["correct_execution"]))
    )
    return {"eligible": eligible, "selection_score": score}


def select_compiler_checkpoint(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidates = []
    for row in rows:
        scored = compiler_checkpoint_score(row["validation_metrics"])
        candidates.append({**dict(row), **scored})
    eligible = [row for row in candidates if row["eligible"]]
    if not eligible:
        return {"passed": False, "candidates": candidates, "selected": None}
    selected = max(
        eligible,
        key=lambda row: (
            float(row["selection_score"]),
            -float(row["validation_metrics"]["raw_policy_kl"]),
            -int(row["updates_per_pair"]),
        ),
    )
    return {"passed": True, "candidates": candidates, "selected": selected}


def no_fixed_point_permutation(ids: Sequence[str], *, purpose: str, seed: int = GLOBAL_SEED) -> dict[str, str]:
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError("Permutation requires at least two unique identities")
    ordered = sorted(ids, key=lambda value: stable_key(seed, purpose, value))
    return {value: ordered[(index + 1) % len(ordered)] for index, value in enumerate(ordered)}

