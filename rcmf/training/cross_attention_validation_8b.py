from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import math
import statistics
from typing import Any


CONTROL_NAMES = (
    "X0_no_memory",
    "X1_correct_memory",
    "X2_transition_shuffle",
    "X3_state_shuffle",
)


def _metric(row: Mapping[str, Any], name: str) -> float:
    metrics = row["metrics"]
    if not isinstance(metrics, Mapping):
        raise TypeError("Live validation metrics must be a mapping")
    return float(metrics[name])


def summarize_live_controls(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["control"])].append(row)
    missing = [name for name in CONTROL_NAMES if not grouped[name]]
    if missing:
        raise ValueError(f"Missing cross-attention validation controls: {missing}")
    output: dict[str, Any] = {}
    for control in CONTROL_NAMES:
        values = grouped[control]
        output[control] = {
            "count": len(values),
            "exact_api": statistics.fmean(
                _metric(row, "exact_primary_app_api_match") for row in values
            ),
            "action_signature": statistics.fmean(
                _metric(row, "canonical_procedural_signature_match") for row in values
            ),
            "semantic_successor": statistics.fmean(
                _metric(row, "semantic_successor_match") for row in values
            ),
            "execution": statistics.fmean(
                _metric(row, "execution_success") for row in values
            ),
            "observation_similarity": statistics.fmean(
                _metric(row, "normalized_observation_similarity") for row in values
            ),
        }

    by_source: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    task_by_source: dict[str, str] = {}
    for row in rows:
        source_state = str(row["source_state_id"])
        by_source[source_state][str(row["control"])] = row
        task_by_source[source_state] = str(row["source_task_id"])
    task_deltas: dict[str, list[float]] = defaultdict(list)
    for source_state, controls in by_source.items():
        if "X0_no_memory" not in controls or "X1_correct_memory" not in controls:
            continue
        correct = controls["X1_correct_memory"]
        zero = controls["X0_no_memory"]
        task_deltas[task_by_source[source_state]].append(
            _metric(correct, "canonical_procedural_signature_match")
            + _metric(correct, "semantic_successor_match")
            - _metric(zero, "canonical_procedural_signature_match")
            - _metric(zero, "semantic_successor_match")
        )
    output["positive_task_count"] = sum(
        statistics.fmean(values) > 0.0 for values in task_deltas.values()
    )
    output["task_count"] = len(task_deltas)
    output["per_task_correct_minus_zero"] = {
        task_id: statistics.fmean(values)
        for task_id, values in sorted(task_deltas.items())
    }
    return output


def classify_live_reader(summary: Mapping[str, Any]) -> str:
    x0 = summary["X0_no_memory"]
    x1 = summary["X1_correct_memory"]
    x2 = summary["X2_transition_shuffle"]
    x3 = summary["X3_state_shuffle"]
    signature_improves = float(x1["action_signature"]) > float(x0["action_signature"])
    successor_improves = float(x1["semantic_successor"]) > float(
        x0["semantic_successor"]
    )
    signature_beats_both = float(x1["action_signature"]) > max(
        float(x2["action_signature"]), float(x3["action_signature"])
    )
    successor_beats_both = float(x1["semantic_successor"]) > max(
        float(x2["semantic_successor"]), float(x3["semantic_successor"])
    )
    signature_beats_one = float(x1["action_signature"]) > min(
        float(x2["action_signature"]), float(x3["action_signature"])
    )
    successor_beats_one = float(x1["semantic_successor"]) > min(
        float(x2["semantic_successor"]), float(x3["semantic_successor"])
    )
    execution_ok = float(x1["execution"]) >= float(x0["execution"]) - 0.05 - 1e-12
    companion_ok = not (
        float(x1["action_signature"]) + 0.05
        < min(float(x2["action_signature"]), float(x3["action_signature"]))
        or float(x1["semantic_successor"]) + 0.05
        < min(float(x2["semantic_successor"]), float(x3["semantic_successor"]))
    )
    task_ok = int(summary["positive_task_count"]) * 2 >= int(summary["task_count"])
    if (
        (signature_improves or successor_improves)
        and (signature_beats_both or successor_beats_both)
        and companion_ok
        and execution_ok
        and task_ok
    ):
        return "STRONG"
    if (
        (signature_improves or successor_improves)
        and (signature_beats_one or successor_beats_one)
        and execution_ok
    ):
        return "PARTIAL"
    return "CLEAR_FAILURE"


def live_specificity_score(summary: Mapping[str, Any]) -> float:
    x0 = summary["X0_no_memory"]
    x1 = summary["X1_correct_memory"]
    x2 = summary["X2_transition_shuffle"]
    x3 = summary["X3_state_shuffle"]
    return (
        float(x1["semantic_successor"])
        - max(float(x2["semantic_successor"]), float(x3["semantic_successor"]))
        + 0.5
        * (
            float(x1["action_signature"])
            - max(float(x2["action_signature"]), float(x3["action_signature"]))
        )
        + 0.25
        * (float(x1["semantic_successor"]) - float(x0["semantic_successor"]))
        - max(0.0, float(x0["execution"]) - float(x1["execution"]))
    )


def policy_gate_passes(policy: Mapping[str, Any]) -> bool:
    values = policy["positive_raw_teacher_policy_kl"]
    required = (
        "X0_no_memory",
        "X1_correct_memory",
        "X2_transition_shuffle",
        "X3_state_shuffle",
    )
    if not all(name in values and math.isfinite(float(values[name])) for name in required):
        return False
    correct = float(values["X1_correct_memory"])
    return (
        correct < float(values["X0_no_memory"])
        and correct < float(values["X2_transition_shuffle"])
        and correct < float(values["X3_state_shuffle"])
    )


def select_reader_checkpoint(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    audited = []
    for candidate in candidates:
        row = dict(candidate)
        row["classification"] = classify_live_reader(row["live_summary"])
        row["live_specificity_score"] = live_specificity_score(row["live_summary"])
        row["policy_gate_passed"] = policy_gate_passes(row["policy_evaluation"])
        row["eligible"] = bool(
            row["policy_gate_passed"]
            and math.isfinite(float(row["live_specificity_score"]))
            and row["classification"] != "CLEAR_FAILURE"
            and bool(row.get("stable_generation", True))
        )
        audited.append(row)
    for classification in ("STRONG", "PARTIAL"):
        values = [
            row
            for row in audited
            if row["eligible"] and row["classification"] == classification
        ]
        if values:
            return max(
                values,
                key=lambda row: (
                    float(row["live_specificity_score"]),
                    -float(
                        row["policy_evaluation"]["positive_raw_teacher_policy_kl"][
                            "X1_correct_memory"
                        ]
                    ),
                    -int(row["epoch"]),
                ),
            )
    return None
