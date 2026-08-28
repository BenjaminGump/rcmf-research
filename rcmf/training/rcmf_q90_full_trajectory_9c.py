"""Frozen Q90 full-trajectory contract for EXP-031C."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from torch import Tensor

from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    preregistered_candidates,
    read_confidence_field,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256


GLOBAL_SEED = 25101
Q90_CANDIDATE_ID = "Q90"
Q90_FORMULA = "pre_rms_confidence"
Q90_TAU = 4.606291029188367
Q90_CALIBRATION_SHA256 = "f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c"
Q90_CONTRACT_FORMAT = "rcmf_q90_full_trajectory_contract_9c_v1"


def q90_identity() -> dict[str, Any]:
    payload = {
        "format": Q90_CONTRACT_FORMAT,
        "candidate_id": Q90_CANDIDATE_ID,
        "formula": Q90_FORMULA,
        "tau": Q90_TAU,
        "calibration_sha256": Q90_CALIBRATION_SHA256,
        "outcomes_used": False,
        "global_seed": GLOBAL_SEED,
        "runtime_retrieval": False,
        "runtime_top_k": False,
        "runtime_per_memory_scoring": False,
        "raw_memory_prompt": False,
        "optimizer_steps": False,
    }
    payload["contract_sha256"] = canonical_sha256(payload)
    return payload


def validate_q90_contract(
    settings: Mapping[str, Any], calibration_lock: Mapping[str, Any]
) -> dict[str, Any]:
    candidate = settings["candidate"]
    locked_candidate = next(
        row for row in preregistered_candidates() if row.candidate_id == Q90_CANDIDATE_ID
    )
    checks = {
        "candidate_id": str(candidate["candidate_id"]) == Q90_CANDIDATE_ID,
        "formula": str(candidate["formula"]) == Q90_FORMULA,
        "tau_exact": float(candidate["tau"]) == Q90_TAU,
        "calibration_sha": (
            str(candidate["calibration_sha256"]) == Q90_CALIBRATION_SHA256
            and str(calibration_lock["calibration_sha256"]) == Q90_CALIBRATION_SHA256
        ),
        "lock_tau_exact": float(calibration_lock["taus"]["Q90"]) == Q90_TAU,
        "lock_outcomes_unused": (
            not bool(calibration_lock["outcomes_used"])
            and bool(calibration_lock["locked_before_candidate_outcomes"])
        ),
        "preregistered_formula": locked_candidate.route == Q90_FORMULA,
        "preregistered_target": locked_candidate.confidence_target == 0.90,
        "no_outcome_recomputation": not bool(candidate["outcome_dependent_recomputation"]),
        "seed": int(settings["global_seed"]) == GLOBAL_SEED,
    }
    if not all(checks.values()):
        raise ValueError(f"Frozen Q90 identity differs: {checks}")
    return {"identity": q90_identity(), "checks": checks, "passed": True}


def read_original_slots(*, query: Tensor, A: Tensor, B: Tensor) -> tuple[Tensor, dict[str, Any]]:
    slots, tensors = read_confidence_field(query=query, A=A, B=B, tau=None, nonempty=True)
    return slots, _read_audit(tensors, tau=None, candidate_id="G100")


def read_q90_slots(*, query: Tensor, A: Tensor, B: Tensor) -> tuple[Tensor, dict[str, Any]]:
    slots, tensors = read_confidence_field(query=query, A=A, B=B, tau=Q90_TAU, nonempty=True)
    return slots, _read_audit(tensors, tau=Q90_TAU, candidate_id=Q90_CANDIDATE_ID)


def _read_audit(
    tensors: Mapping[str, Tensor], *, tau: float | None, candidate_id: str
) -> dict[str, Any]:
    raw_rms = tensors["raw_rms"].detach().cpu().reshape(-1)
    confidence = tensors["confidence"].detach().cpu().reshape(-1)
    if raw_rms.numel() != 1 or confidence.numel() != 1:
        raise ValueError("Full-trajectory field read must be unbatched")
    return {
        "candidate_id": candidate_id,
        "formula": "original_rms_norm" if tau is None else Q90_FORMULA,
        "tau": tau,
        "raw_field_rms": float(raw_rms.item()),
        "q90_confidence": None if tau is None else float(confidence.item()),
        "calibration_sha256": None if tau is None else Q90_CALIBRATION_SHA256,
        "outcomes_used": False,
    }


def heldout_full_trajectory_decision(
    success_ids: Mapping[str, Sequence[str]],
    *,
    infrastructure_valid: bool = True,
    prohibited_runtime_mechanism: bool = False,
) -> dict[str, Any]:
    required = {"H0", "H1", "H2", "H3", "H4"}
    if set(success_ids) != required:
        raise ValueError(f"Heldout conditions differ: {sorted(success_ids)}")
    sets = {key: set(values) for key, values in success_ids.items()}
    counts = {key: len(values) for key, values in sets.items()}
    q90_collapses_to_bare = sets["H3"] == sets["H0"] and sets["H1"] != sets["H0"]
    stop_reasons = []
    if counts["H3"] < counts["H4"]:
        stop_reasons.append("q90_correct_below_q90_shuffle")
    if counts["H3"] <= counts["H1"] - 2:
        stop_reasons.append("q90_lost_two_or_more_vs_original_correct")
    if not infrastructure_valid:
        stop_reasons.append("infrastructure_or_evaluator_mismatch")
    if prohibited_runtime_mechanism:
        stop_reasons.append("prohibited_runtime_mechanism")
    if stop_reasons:
        decision = "STOP"
    elif counts["H3"] > counts["H4"] and counts["H3"] >= counts["H1"] and not q90_collapses_to_bare:
        decision = "PROCEED"
    else:
        decision = "INCONCLUSIVE"
    return {
        "decision": decision,
        "counts": counts,
        "success_ids": {key: sorted(values) for key, values in sets.items()},
        "H3_minus_H4": counts["H3"] - counts["H4"],
        "H3_minus_H1": counts["H3"] - counts["H1"],
        "H3_minus_H0": counts["H3"] - counts["H0"],
        "q90_collapses_exactly_to_bare": q90_collapses_to_bare,
        "per_task_changes": {
            "H3_gained_vs_H1": sorted(sets["H3"] - sets["H1"]),
            "H3_lost_vs_H1": sorted(sets["H1"] - sets["H3"]),
            "H3_gained_vs_H4": sorted(sets["H3"] - sets["H4"]),
            "H3_lost_vs_H4": sorted(sets["H4"] - sets["H3"]),
        },
        "stop_reasons": stop_reasons,
        "first37_authorized": decision in {"PROCEED", "INCONCLUSIVE"},
        "single_seed_eight_task_result_not_statistical": True,
    }


def first37_scientific_decision(
    *,
    q1_success_ids: Sequence[str],
    q2_success_ids: Sequence[str],
    d0_success_ids: Sequence[str],
    d1_success_ids: Sequence[str],
    original_gain_ids: Sequence[str],
    retained_success_ids: Sequence[str],
    original_loss_ids: Sequence[str],
    gain_families: Mapping[str, Sequence[str]],
    contract_valid: bool = True,
    heldout_contradicts_q90: bool = False,
    q90_only_suppresses_useful_memory: bool = False,
) -> dict[str, Any]:
    q1, q2 = set(q1_success_ids), set(q2_success_ids)
    d0, d1 = set(d0_success_ids), set(d1_success_ids)
    gains, retained, losses = (
        set(original_gain_ids),
        set(retained_success_ids),
        set(original_loss_ids),
    )
    preserved = q1 & gains
    lost = gains - q1
    retained_ok = retained <= q1
    recovered_losses = q1 & losses
    equivalent_new = (q1 - d0) - gains
    represented_families = {
        family: sorted(set(task_ids) & q1) for family, task_ids in gain_families.items()
    }
    all_families = all(represented_families.values())
    new_gain_families = {task.split("_", 1)[0] for task in equivalent_new}
    recovery_ok = len(recovered_losses) >= 2 or len(new_gain_families) >= 2
    proceed_checks = {
        "q1_at_least_10": len(q1) >= 10,
        "q1_at_least_two_above_d0": len(q1) >= len(d0) + 2,
        "q1_at_least_two_above_q2": len(q1) >= len(q2) + 2,
        "at_least_five_original_gains": len(preserved) >= 5,
        "all_gain_families": all_families,
        "both_retained_successes": retained_ok,
        "recovered_losses_or_equivalent_new_families": recovery_ok,
        "contract_valid": contract_valid,
    }
    stop_reasons = []
    if len(q1) <= len(q2):
        stop_reasons.append("q90_correct_not_above_shuffle")
    if len(lost) >= 2:
        stop_reasons.append("two_or_more_original_gains_lost")
    if not retained_ok:
        stop_reasons.append("original_retained_success_lost")
    if q90_only_suppresses_useful_memory:
        stop_reasons.append("q90_only_suppresses_useful_memory")
    if heldout_contradicts_q90 and len(q1) <= len(d1):
        stop_reasons.append("heldout_and_first37_contradict_q90")
    if not contract_valid:
        stop_reasons.append("scientific_contract_violation")
    if stop_reasons:
        decision = "STOP_ROUTE"
    elif all(proceed_checks.values()):
        decision = "PROCEED"
    else:
        decision = "INCONCLUSIVE"
    return {
        "scientific_decision": decision,
        "mechanical_label": (
            "PRELIMINARY_POSITIVE"
            if len(q1) > len(d0) and len(q1) >= len(q2) + 2
            else "LIVE_MEMORY_SPECIFIC_SIGNAL"
            if len(q1) > len(q2)
            else "CLEAR_FAILURE"
        ),
        "success_count": {
            "D0": len(d0),
            "D1": len(d1),
            "Q1": len(q1),
            "Q2": len(q2),
        },
        "Q1_minus_D0": len(q1) - len(d0),
        "Q1_minus_D1": len(q1) - len(d1),
        "Q1_minus_Q2": len(q1) - len(q2),
        "preserved_original_gains": sorted(preserved),
        "lost_original_gains": sorted(lost),
        "preserved_retained_successes": sorted(retained & q1),
        "recovered_original_losses": sorted(recovered_losses),
        "equivalent_new_gains": sorted(equivalent_new),
        "gain_family_coverage": represented_families,
        "proceed_checks": proceed_checks,
        "stop_reasons": stop_reasons,
        "single_seed_exposed_development_result": True,
    }
