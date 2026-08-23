from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from rcmf.training.state_conditioned_program_7d import stable_key


GLOBAL_SEED = 25101


def build_mismatch_manifest(
    rows: Sequence[Mapping[str, Any]], *, seed: int = GLOBAL_SEED
) -> dict[str, Any]:
    """Freeze state/transition mismatches without consulting evaluation outcomes."""

    if len({str(row["pair_id"]) for row in rows}) != len(rows):
        raise ValueError("Mismatch source rows contain duplicate pair IDs")
    output = []
    for index, row in enumerate(rows):
        transition_candidates = [
            other
            for other, candidate in enumerate(rows)
            if str(candidate["transition_id"]) != str(row["transition_id"])
        ]
        if not transition_candidates:
            raise ValueError("Transition mismatch requires a different transition")
        signature_candidates = [
            other
            for other in transition_candidates
            if str(rows[other].get("signature_class_id", ""))
            != str(row.get("signature_class_id", ""))
        ]
        transition_pool = signature_candidates or transition_candidates
        transition_index = min(
            transition_pool,
            key=lambda other: stable_key(
                seed,
                "7g-transition-mismatch",
                row["pair_id"],
                rows[other]["pair_id"],
            ),
        )

        state_candidates = [
            other
            for other, candidate in enumerate(rows)
            if str(candidate["state_example_id"]) != str(row["state_example_id"])
        ]
        if not state_candidates:
            raise ValueError("State mismatch requires a different state")
        task_candidates = [
            other
            for other in state_candidates
            if str(rows[other]["state_task_id"]) != str(row["state_task_id"])
        ]
        state_pool = task_candidates or state_candidates
        state_index = min(
            state_pool,
            key=lambda other: stable_key(
                seed,
                "7g-state-mismatch",
                row["pair_id"],
                rows[other]["pair_id"],
            ),
        )
        output.append(
            {
                "pair_id": str(row["pair_id"]),
                "transition_mismatch_pair_id": str(rows[transition_index]["pair_id"]),
                "transition_mismatch_transition_id": str(
                    rows[transition_index]["transition_id"]
                ),
                "transition_signature_differs": str(
                    rows[transition_index].get("signature_class_id", "")
                )
                != str(row.get("signature_class_id", "")),
                "state_mismatch_pair_id": str(rows[state_index]["pair_id"]),
                "state_mismatch_state_example_id": str(
                    rows[state_index]["state_example_id"]
                ),
                "state_task_differs": str(rows[state_index]["state_task_id"])
                != str(row["state_task_id"]),
            }
        )
    return {
        "format": "memory_specific_mismatch_manifest_7g_v1",
        "global_seed": int(seed),
        "row_count": len(output),
        "rows": output,
        "transition_signature_difference_count": sum(
            bool(row["transition_signature_differs"]) for row in output
        ),
        "state_task_difference_count": sum(
            bool(row["state_task_differs"]) for row in output
        ),
        "uses_training_rows_only": True,
        "uses_heldout_outcomes": False,
    }


def selection_diagnostics(metrics: Mapping[str, Any]) -> dict[str, float | bool]:
    correct = float(metrics["correct_raw_policy_kl"])
    zero = float(metrics["zero_raw_policy_kl"])
    transition = float(metrics["transition_mismatch_bare_policy_kl"])
    state = float(metrics["state_mismatch_bare_policy_kl"])
    raw_improvement = zero - correct
    transition_specificity = raw_improvement - transition
    state_specificity = raw_improvement - state
    score = raw_improvement - 0.5 * (transition + state)
    finite = all(
        math.isfinite(value)
        for value in (correct, zero, transition, state, score)
    )
    return {
        "raw_policy_kl_improvement": raw_improvement,
        "transition_specificity": transition_specificity,
        "state_specificity": state_specificity,
        "selection_score": score,
        "finite": finite,
    }


def select_checkpoint(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Select on A-validation policy specificity only, with a fixed fallback."""

    if not history:
        raise ValueError("Checkpoint history is empty")
    candidates = []
    for entry in history:
        diagnostics = selection_diagnostics(entry["a_validation"])
        valid = (
            bool(diagnostics["finite"])
            and float(entry["maximum_ratio"]) <= 1.0 + 1.0e-4
            and float(diagnostics["transition_specificity"]) > 0.0
            and float(diagnostics["state_specificity"]) > 0.0
        )
        candidates.append((entry, diagnostics, valid))
    valid = [value for value in candidates if value[2]]
    pool = valid or candidates
    selected, diagnostics, passed_constraints = max(
        pool,
        key=lambda value: (
            float(value[1]["selection_score"]),
            -float(value[0]["a_validation"]["correct_raw_policy_kl"]),
            -int(value[0]["updates_per_pair"]),
        ),
    )
    return {
        **dict(selected),
        "selection_diagnostics": diagnostics,
        "selection_constraints_passed": passed_constraints,
        "selection_rule": (
            "maximize (zero_raw_KL-correct_raw_KL) - "
            "0.5*(transition_mismatch_to_bare_KL+state_mismatch_to_bare_KL); "
            "require both specificity gaps positive, finite metrics, ratio<=1"
        ),
    }
