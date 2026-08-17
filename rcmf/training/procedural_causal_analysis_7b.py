from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import math
from statistics import mean
from typing import Any

from rcmf.training.procedural_causal_audit_6h import paired_task_bootstrap


PRIMARY_METRICS = (
    "exact_primary_app_api_match",
    "canonical_procedural_signature_match",
    "execution_success",
    "normalized_observation_similarity",
    "semantic_successor_match",
)


def _mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _pearson(_ranks(left), _ranks(right))


def condition_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["condition_name"])].append(row)
    output: dict[str, Any] = {}
    for condition, values in sorted(grouped.items()):
        metric_names = sorted(
            {
                key
                for value in values
                for key, item in value["metrics"].items()
                if isinstance(item, (bool, int, float))
            }
        )
        output[condition] = {
            "state_count": len(values),
            "task_count": len({str(value["state_task_id"]) for value in values}),
            "metrics": {
                metric: mean(float(value["metrics"][metric]) for value in values)
                for metric in metric_names
            },
            "audit_strata": dict(
                sorted(Counter(str(value["audit_stratum"]) for value in values).items())
            ),
            "api_documentation_fraction": mean(
                float(bool(value.get("api_documentation_action"))) for value in values
            ),
            "completion_tokens_mean": mean(int(value["completion_tokens"]) for value in values),
            "qwen_generation_seconds_mean": mean(
                float(value["generation_elapsed_seconds"]) for value in values
            ),
            "condition_elapsed_seconds_mean": mean(
                float(value["condition_elapsed_seconds"]) for value in values
            ),
        }
    return output


def comparison_set(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    return {
        metric: paired_task_bootstrap(
            rows,
            left_condition=left,
            right_condition=right,
            metric=metric,
            samples=bootstrap_samples,
            seed=seed + index,
        )
        for index, metric in enumerate(PRIMARY_METRICS)
    }


def primary_comparisons(
    rows: Sequence[Mapping[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    pairs = (
        ("C1_raw_oracle", "C0_bare"),
        ("C1_raw_oracle", "C2_signature_only"),
        ("C1_raw_oracle", "C3_hard_negative"),
        ("C1_raw_oracle", "C4_signature_popularity"),
        ("C1_raw_oracle", "C5_unrelated"),
        ("C2_signature_only", "C0_bare"),
    )
    output = {}
    for index, (left, right) in enumerate(pairs):
        output[f"{left}_minus_{right}"] = comparison_set(
            rows,
            left=left,
            right=right,
            bootstrap_samples=bootstrap_samples,
            seed=seed + index * 100,
        )
    by_task: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_task[str(row["state_task_id"])][str(row["condition_name"])].append(row)
    task_deltas = {}
    for task, values in sorted(by_task.items()):
        baseline = values.get("C0_bare", [])
        oracle = values.get("C1_raw_oracle", [])
        exact_delta = _paired_group_delta(oracle, baseline, "exact_primary_app_api_match")
        signature_delta = _paired_group_delta(
            oracle, baseline, "canonical_procedural_signature_match"
        )
        positive = bool(
            (exact_delta is not None and exact_delta > 0)
            or (signature_delta is not None and signature_delta > 0)
        )
        task_deltas[task] = {
            "exact_api_delta": exact_delta,
            "signature_delta": signature_delta,
            "positive_relative_behavior": positive,
        }
    output["positive_task_count"] = sum(
        bool(value["positive_relative_behavior"]) for value in task_deltas.values()
    )
    output["task_deltas"] = task_deltas
    return output


def _paired_group_delta(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    metric: str,
) -> float | None:
    left_values = {str(row["state_example_id"]): float(row["metrics"][metric]) for row in left}
    right_values = {str(row["state_example_id"]): float(row["metrics"][metric]) for row in right}
    shared = sorted(set(left_values).intersection(right_values))
    return _mean([left_values[key] - right_values[key] for key in shared])


def per_task_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_task_id"])].append(row)
    return {task: condition_summary(values) for task, values in sorted(grouped.items())}


def same_signature_consistency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    pairs = []
    for state_id, values in sorted(by_state.items()):
        required = (
            "C0_bare",
            "C1_raw_oracle",
            "C6_alternate_same_signature",
        )
        if not all(name in values for name in required):
            continue
        bare, canonical, alternate = (values[name] for name in required)
        metric_effects = {}
        for metric in PRIMARY_METRICS:
            canonical_effect = float(canonical["metrics"][metric]) - float(bare["metrics"][metric])
            alternate_effect = float(alternate["metrics"][metric]) - float(bare["metrics"][metric])
            metric_effects[metric] = {
                "canonical_effect": canonical_effect,
                "alternate_effect": alternate_effect,
                "same_direction": _same_direction(canonical_effect, alternate_effect),
            }
        gate_effects = metric_effects["normalized_observation_similarity"]
        pairs.append(
            {
                "state_example_id": state_id,
                "task_id": str(bare["state_task_id"]),
                "signature_class_id": canonical.get("signature_class_id"),
                "canonical_transition_id": canonical.get("transition_id"),
                "alternate_transition_id": alternate.get("transition_id"),
                "gate_metric": "normalized_observation_similarity",
                "same_effect_direction": gate_effects["same_direction"],
                "metric_effects": metric_effects,
                "exact_api_match_agreement": canonical["metrics"]["exact_primary_app_api_match"]
                == alternate["metrics"]["exact_primary_app_api_match"],
                "execution_success_agreement": canonical["metrics"]["execution_success"]
                == alternate["metrics"]["execution_success"],
            }
        )
    gate_left = [
        float(row["metric_effects"]["normalized_observation_similarity"]["canonical_effect"])
        for row in pairs
    ]
    gate_right = [
        float(row["metric_effects"]["normalized_observation_similarity"]["alternate_effect"])
        for row in pairs
    ]
    class_effects: dict[str, list[float]] = defaultdict(list)
    for row, left, right in zip(pairs, gate_left, gate_right, strict=True):
        class_effects[str(row["signature_class_id"])].extend((left, right))
    class_means = [mean(values) for values in class_effects.values()]
    return {
        "pair_count": len(pairs),
        "task_count": len({str(row["task_id"]) for row in pairs}),
        "gate_metric": "normalized_observation_similarity",
        "same_effect_direction_fraction": _mean(
            [float(row["same_effect_direction"]) for row in pairs]
        ),
        "exact_api_match_agreement": _mean(
            [float(row["exact_api_match_agreement"]) for row in pairs]
        ),
        "execution_success_agreement": _mean(
            [float(row["execution_success_agreement"]) for row in pairs]
        ),
        "effect_size_pearson": _pearson(gate_left, gate_right),
        "effect_size_spearman": _spearman(gate_left, gate_right),
        "mean_within_class_variance": _mean(
            [((left - right) ** 2) / 2.0 for left, right in zip(gate_left, gate_right)]
        ),
        "between_class_mean_variance": (
            mean((value - mean(class_means)) ** 2 for value in class_means)
            if len(class_means) > 1
            else 0.0
        ),
        "per_metric_direction_agreement": {
            metric: _mean([float(row["metric_effects"][metric]["same_direction"]) for row in pairs])
            for metric in PRIMARY_METRICS
        },
        "rows": pairs,
    }


def _same_direction(left: float, right: float) -> bool:
    if left == 0 or right == 0:
        return left == right
    return math.copysign(1.0, left) == math.copysign(1.0, right)


def relationship_analysis(
    rows: Sequence[Mapping[str, Any]],
    transition_tokens: Mapping[str, int],
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    data = []
    for state_id, values in sorted(by_state.items()):
        baseline = values.get("C0_bare")
        if baseline is None:
            continue
        for condition, row in sorted(values.items()):
            utility = row.get("raw_nll_text_utility")
            if condition == "C0_bare" or utility is None:
                continue
            data.append(
                {
                    "state_example_id": state_id,
                    "condition_name": condition,
                    "raw_nll_text_utility": float(utility),
                    "procedural_tier": float(row.get("procedural_tier") or 0),
                    "signature_class_size": float(row.get("signature_class_size") or 1),
                    "transition_tokens": float(
                        transition_tokens.get(str(row.get("transition_id")), 0)
                    ),
                    "exact_api_effect": _metric_effect(
                        row, baseline, "exact_primary_app_api_match"
                    ),
                    "signature_effect": _metric_effect(
                        row, baseline, "canonical_procedural_signature_match"
                    ),
                    "execution_effect": _metric_effect(row, baseline, "execution_success"),
                    "semantic_successor_effect": _metric_effect(
                        row, baseline, "semantic_successor_match"
                    ),
                }
            )
    features = (
        "raw_nll_text_utility",
        "procedural_tier",
        "signature_class_size",
        "transition_tokens",
    )
    effects = (
        "exact_api_effect",
        "signature_effect",
        "execution_effect",
        "semantic_successor_effect",
    )
    return {
        "available_pair_count": len(data),
        "available_state_count": len({str(row["state_example_id"]) for row in data}),
        "raw_nll_source": "identity_reconciled_transition_teacher_7b_v1",
        "availability_note": (
            "Only selected conditions whose clean transition pair is present in the "
            "locked 148-panel comparator cache have raw-NLL utility."
        ),
        "correlations": {
            feature: {
                effect: {
                    "pearson": _pearson(
                        [float(row[feature]) for row in data],
                        [float(row[effect]) for row in data],
                    ),
                    "spearman": _spearman(
                        [float(row[feature]) for row in data],
                        [float(row[effect]) for row in data],
                    ),
                }
                for effect in effects
            }
            for feature in features
        },
        "rows": data,
    }


def _metric_effect(row: Mapping[str, Any], baseline: Mapping[str, Any], metric: str) -> float:
    return float(row["metrics"][metric]) - float(baseline["metrics"][metric])


def select_decision(
    *,
    primary: Mapping[str, Any],
    documentation: Mapping[str, Any],
    consistency: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    c1_c0 = primary["C1_raw_oracle_minus_C0_bare"]
    c1_c2 = primary["C1_raw_oracle_minus_C2_signature_only"]
    c1_c3 = primary["C1_raw_oracle_minus_C3_hard_negative"]
    c1_c5 = primary["C1_raw_oracle_minus_C5_unrelated"]
    c2_c0 = primary["C2_signature_only_minus_C0_bare"]
    exact_gain = float(c1_c0["exact_primary_app_api_match"]["difference"])
    signature_gain = float(c1_c0["canonical_procedural_signature_match"]["difference"])
    execution_gain = float(c1_c0["execution_success"]["difference"])
    action_ci = _positive_ci(c1_c0)
    threshold = float(gates["material_control_gain"])
    beats_hard = _best_action_gain(c1_c3) >= threshold
    beats_unrelated = _best_action_gain(c1_c5) >= threshold
    positive_tasks = int(primary["positive_task_count"])
    oracle_gate = bool(
        exact_gain >= float(gates["oracle_exact_api_gain"])
        and signature_gain > 0
        and execution_gain >= -float(gates["maximum_execution_drop"])
        and beats_hard
        and beats_unrelated
        and positive_tasks >= int(gates["minimum_positive_tasks"])
        and action_ci
    )
    raw_card_gain = _best_action_gain(c1_c2)
    raw_card_ci = _positive_ci(c1_c2)
    content_gate = bool(
        raw_card_gain >= float(gates["raw_content_gain_over_signature_card"]) and raw_card_ci
    )
    oracle_primary_gain = max(exact_gain, signature_gain)
    card_primary_gain = max(
        float(c2_c0["exact_primary_app_api_match"]["difference"]),
        float(c2_c0["canonical_procedural_signature_match"]["difference"]),
    )
    retention = card_primary_gain / oracle_primary_gain if oracle_primary_gain > 0 else None
    metadata_sufficient = bool(
        retention is not None
        and retention >= float(gates["metadata_gain_retention"])
        and not content_gate
    )
    direction = consistency.get("same_effect_direction_fraction")
    consistency_gate = bool(
        direction is not None
        and float(direction) >= float(gates["same_signature_direction_agreement"])
    )
    documentation_gain = _documentation_gain(documentation)
    documentation_dominates = bool(
        not oracle_gate
        and documentation_gain is not None
        and documentation_gain >= float(gates["oracle_exact_api_gain"])
        and documentation_gain > max(exact_gain, signature_gain)
    )
    if not oracle_gate:
        branch = (
            "api_documentation_prompting_dominates"
            if documentation_dominates
            else "procedural_oracle_not_behaviorally_helpful"
        )
    elif metadata_sufficient or not content_gate:
        branch = "procedural_metadata_sufficient_raw_transition_content_not_validated"
    elif not consistency_gate:
        branch = "canonical_procedural_signature_too_coarse"
    else:
        branch = "raw_transition_content_behaviorally_validated_on_clean_corpus"
    return {
        "procedural_oracle_behavioral_gate": oracle_gate,
        "content_beyond_metadata_gate": content_gate,
        "same_signature_consistency_gate": consistency_gate,
        "exact_api_gain_over_bare": exact_gain,
        "signature_gain_over_bare": signature_gain,
        "execution_gain_over_bare": execution_gain,
        "beats_hard_negative": beats_hard,
        "beats_unrelated": beats_unrelated,
        "primary_action_ci_excludes_zero": action_ci,
        "positive_task_count": positive_tasks,
        "raw_content_gain_over_signature_card": raw_card_gain,
        "raw_content_ci_excludes_zero": raw_card_ci,
        "signature_card_gain_retention": retention,
        "metadata_sufficient": metadata_sufficient,
        "documentation_primary_gain": documentation_gain,
        "documentation_dominates": documentation_dominates,
        "same_signature_direction_fraction": direction,
        "decision_branch": branch,
        "raw_transition_content_behaviorally_validated": branch
        == "raw_transition_content_behaviorally_validated_on_clean_corpus",
        "field_program_training_remains_blocked": True,
    }


def _positive_ci(comparison: Mapping[str, Any]) -> bool:
    return any(
        comparison[metric]["ci95_low"] is not None and float(comparison[metric]["ci95_low"]) > 0
        for metric in (
            "exact_primary_app_api_match",
            "canonical_procedural_signature_match",
        )
    )


def _best_action_gain(comparison: Mapping[str, Any]) -> float:
    return max(
        float(comparison["exact_primary_app_api_match"]["difference"]),
        float(comparison["canonical_procedural_signature_match"]["difference"]),
    )


def _documentation_gain(documentation: Mapping[str, Any]) -> float | None:
    comparison = documentation.get("C1_raw_oracle_minus_C0_bare")
    if not isinstance(comparison, Mapping):
        return None
    exact = comparison["exact_primary_app_api_match"].get("difference")
    signature = comparison["canonical_procedural_signature_match"].get("difference")
    if exact is None or signature is None:
        return None
    return max(float(exact), float(signature))


def validate_formal_rows(
    rows: Sequence[Mapping[str, Any]],
    condition_manifest: Mapping[str, Any],
    generation_summary: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {str(row["condition_key"]): row for row in condition_manifest["conditions"]}
    if len(expected) != int(condition_manifest["condition_count"]):
        raise ValueError("Condition manifest contains duplicate keys")
    observed = {str(row["condition_key"]): row for row in rows}
    if len(observed) != len(rows):
        raise ValueError("Formal output contains duplicate condition keys")
    if set(observed) != set(expected):
        raise ValueError("Formal output keys differ from the frozen manifest")
    infrastructure_failures = []
    by_state: dict[str, set[str]] = defaultdict(set)
    for key, row in observed.items():
        condition = expected[key]
        if str(row["condition_name"]) != str(condition["condition_name"]):
            infrastructure_failures.append(f"condition_name:{key}")
        worker = row.get("live_worker", {})
        checks = {
            "complete": bool(worker.get("complete")),
            "same_world": bool(worker.get("same_world_execution")),
            "same_namespace": bool(worker.get("same_python_namespace")),
            "history": bool(worker.get("history_semantic_v3_match")),
            "identity": all(
                bool(value) for value in worker.get("task_identity_checks", {}).values()
            ),
        }
        infrastructure_failures.extend(
            f"{name}:{key}" for name, passed in checks.items() if not passed
        )
        by_state[str(row["state_example_id"])].add(str(row["condition_name"]))
    required_core = {
        "C0_bare",
        "C1_raw_oracle",
        "C2_signature_only",
        "C3_hard_negative",
        "C4_signature_popularity",
        "C5_unrelated",
    }
    missing_core = {
        state: sorted(required_core - names)
        for state, names in by_state.items()
        if not required_core.issubset(names)
    }
    if infrastructure_failures or missing_core:
        raise ValueError("clean_corpus_behavioral_audit_infrastructure_invalid")
    if not bool(generation_summary.get("passed")):
        raise ValueError("Formal generation summary did not pass")
    if int(generation_summary["condition_count"]) != len(rows):
        raise ValueError("Formal generation summary count differs")
    return {
        "format": "identity_reconciled_causal_audit_validation_7b_v1",
        "condition_count": len(rows),
        "unique_condition_count": len(observed),
        "state_count": len(by_state),
        "task_count": len({str(row["state_task_id"]) for row in rows}),
        "required_core_conditions_per_state": sorted(required_core),
        "all_condition_keys_match_manifest": True,
        "all_live_workers_same_world": True,
        "all_live_workers_same_namespace": True,
        "all_histories_semantic_v3_match": True,
        "all_task_identities_match": True,
        "duplicate_condition_key_count": 0,
        "passed": True,
    }
