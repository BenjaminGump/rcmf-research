from __future__ import annotations

import inspect

import pytest
import torch

from rcmf.training.rcmf_joint_full_bank_9a import read_compiled_field
from rcmf.training.rcmf_q90_full_trajectory_9c import (
    Q90_CALIBRATION_SHA256,
    Q90_TAU,
    first37_scientific_decision,
    heldout_full_trajectory_decision,
    q90_identity,
    read_original_slots,
    read_q90_slots,
    validate_q90_contract,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task
from scripts.run_rcmf_q90_trajectory_common_9c import (
    CONDITION_SPECS,
    build_manifest,
    deterministic_task_match,
)


def _calibration_lock() -> dict[str, object]:
    return {
        "calibration_sha256": Q90_CALIBRATION_SHA256,
        "Q90_tau": Q90_TAU,
        "outcomes_used": False,
        "locked_before_candidate_outcomes": True,
    }


def _settings() -> dict[str, object]:
    return {
        "global_seed": 25101,
        "candidate": {
            "candidate_id": "Q90",
            "formula": "pre_rms_confidence",
            "tau": Q90_TAU,
            "calibration_sha256": Q90_CALIBRATION_SHA256,
            "outcome_dependent_recomputation": False,
        },
    }


def test_q90_identity_and_lock_are_exact() -> None:
    result = validate_q90_contract(_settings(), _calibration_lock())
    assert result["passed"]
    assert result["identity"]["tau"] == 4.606291029188367
    assert result["identity"]["calibration_sha256"] == Q90_CALIBRATION_SHA256
    assert q90_identity()["outcomes_used"] is False

    bad = _settings()
    bad["candidate"] = dict(bad["candidate"], tau=Q90_TAU + 1e-12)
    with pytest.raises(ValueError, match="Frozen Q90 identity differs"):
        validate_q90_contract(bad, _calibration_lock())


def test_original_read_is_exact_and_q90_formula_is_locked() -> None:
    generator = torch.Generator().manual_seed(25101)
    query = torch.randn(960, generator=generator)
    A = torch.randn(960, 8, 256, generator=generator)
    B = torch.randn(8, 256, generator=generator)

    expected = read_compiled_field(query=query, A=A, B=B, nonempty=True)
    original, original_audit = read_original_slots(query=query, A=A, B=B)
    q90, q90_audit = read_q90_slots(query=query, A=A, B=B)

    assert torch.equal(original, expected)
    raw = B + torch.einsum("k,ksp->sp", query, A)
    raw_rms = raw.float().square().mean().sqrt()
    expected_confidence = raw_rms / (raw_rms + Q90_TAU)
    assert q90_audit["q90_confidence"] == float(expected_confidence)
    assert torch.equal(q90, expected * expected_confidence)
    assert original_audit["q90_confidence"] is None
    assert not torch.equal(q90, original)


def test_manifest_accounts_for_heldout_and_first37_without_runtime_retrieval() -> None:
    heldout = build_manifest(
        scope="heldout",
        task_ids=[f"task_{index}" for index in range(8)],
        conditions=["H0", "H1", "H2", "H3", "H4"],
        memory_count=401,
        config_sha256="c" * 64,
        field_sha256={"correct": "a" * 64, "key_payload_shuffle": "b" * 64},
        data_manifest_sha256="d" * 64,
    )
    assert heldout["logical_task_condition_count"] == 40
    assert {row["candidate"] for row in heldout["rows"]} == {
        "zero",
        "G100",
        "Q90",
    }
    assert all(not row["runtime_memory_retrieval"] for row in heldout["rows"])
    assert all(not row["runtime_per_memory_scoring"] for row in heldout["rows"])

    first37 = build_manifest(
        scope="first37",
        task_ids=[f"task_{index}" for index in range(37)],
        conditions=["Q1", "Q2"],
        memory_count=499,
        config_sha256="c" * 64,
        field_sha256={"correct": "a" * 64, "key_payload_shuffle": "a" * 64},
        data_manifest_sha256="d" * 64,
    )
    assert first37["logical_task_condition_count"] == 74
    assert all(row["candidate"] == "Q90" for row in first37["rows"])
    assert CONDITION_SPECS["Q1"]["field_control"] == "correct"
    assert CONDITION_SPECS["Q2"]["field_control"] == "key_payload_shuffle"


def test_heldout_decision_proceed_inconclusive_and_stop() -> None:
    proceed = heldout_full_trajectory_decision(
        {
            "H0": ["a"],
            "H1": ["a", "b"],
            "H2": ["a"],
            "H3": ["a", "b", "c"],
            "H4": ["a", "b"],
        }
    )
    assert proceed["decision"] == "PROCEED"
    assert proceed["first37_authorized"]

    inconclusive = heldout_full_trajectory_decision(
        {
            "H0": ["a"],
            "H1": ["a", "b"],
            "H2": ["a"],
            "H3": ["a", "b"],
            "H4": ["a", "b"],
        }
    )
    assert inconclusive["decision"] == "INCONCLUSIVE"
    assert inconclusive["first37_authorized"]

    stop = heldout_full_trajectory_decision(
        {
            "H0": ["a"],
            "H1": ["a", "b", "c"],
            "H2": ["a"],
            "H3": ["a"],
            "H4": ["a", "b"],
        }
    )
    assert stop["decision"] == "STOP"
    assert not stop["first37_authorized"]


def test_first37_decision_enforces_benefit_families_and_controls() -> None:
    gains = ["g1_1", "g2_1", "g2_2", "g3_1", "g3_2", "g3_3"]
    retained = ["r1_1", "r2_1"]
    d0 = ["b1_1", "b2_1", "l1_1", "l2_1", "x1_1", "x2_1", "x3_1", "x4_1"]
    q1 = gains[:5] + retained + ["l1_1", "l2_1", "n1_1"]
    q2 = gains[:3] + retained
    result = first37_scientific_decision(
        q1_success_ids=q1,
        q2_success_ids=q2,
        d0_success_ids=d0,
        d1_success_ids=gains + retained,
        original_gain_ids=gains,
        retained_success_ids=retained,
        original_loss_ids=["l1_1", "l2_1", "l3_1"],
        gain_families={
            "cross": ["g1_1"],
            "spotify": ["g2_1", "g2_2"],
            "migration": ["g3_1", "g3_2", "g3_3"],
        },
    )
    assert result["scientific_decision"] == "PROCEED"
    assert result["proceed_checks"]["all_gain_families"]

    failed = first37_scientific_decision(
        q1_success_ids=q2,
        q2_success_ids=q1,
        d0_success_ids=d0,
        d1_success_ids=gains + retained,
        original_gain_ids=gains,
        retained_success_ids=retained,
        original_loss_ids=["l1_1", "l2_1", "l3_1"],
        gain_families={
            "cross": ["g1_1"],
            "spotify": ["g2_1", "g2_2"],
            "migration": ["g3_1", "g3_2", "g3_3"],
        },
    )
    assert failed["scientific_decision"] == "STOP_ROUTE"
    assert "q90_correct_not_above_shuffle" in failed["stop_reasons"]


def test_deterministic_task_match_requires_prompts_tokens_code_and_observations() -> None:
    base = {
        "task_id": "task",
        "success": True,
        "step_count": 1,
        "steps": [
            {
                "rendered_message_sha256": "p",
                "generated_token_ids": [1, 2],
                "exact_executed_code": "x()",
                "complete_environment_observation": "ok",
            }
        ],
    }
    assert deterministic_task_match(base, dict(base))["passed"]
    changed = {
        **base,
        "steps": [{**base["steps"][0], "generated_token_ids": [1, 3]}],
    }
    assert not deterministic_task_match(base, changed)["passed"]


def test_parent_runner_defaults_remain_historical() -> None:
    signature = inspect.signature(_run_task)
    assert signature.parameters["bare_condition"].default is None
    assert signature.parameters["condition_name"].default is None
    assert signature.parameters["memory_count"].default is None
    assert signature.parameters["field_artifact_path"].default is None
    assert signature.parameters["field_provenance_path"].default is None
    assert signature.parameters["max_steps_override"].default is None
    assert signature.parameters["experiment_prefix"].default == "exp031a"
