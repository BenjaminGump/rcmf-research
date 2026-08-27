from __future__ import annotations

from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    preregistered_candidates,
)
from scripts.run_rcmf_benefit_preserving_live_9b import (
    benefit_gate,
    build_critical_manifest,
    critical_choice_preserved,
    critical_contract,
    summarize_critical_rows,
)


def _settings() -> dict:
    critical = {
        task_id: {
            "d1_critical_step": index + 1,
            "group": "gain" if index < 6 else "retained" if index < 8 else "loss",
            "mechanism": "test",
        }
        for index, task_id in enumerate(
            (
                "0d01c76_3",
                "325d6ec_2",
                "325d6ec_3",
                "634f342_1",
                "634f342_2",
                "634f342_3",
                "8749218_2",
                "8749218_3",
                "0d01c76_1",
                "0d01c76_2",
                "29a7b7e_3",
                "325d6ec_1",
                "8749218_1",
                "d6ac34d_2",
            )
        )
    }
    return {"gain_loss_audit": {"critical_steps": critical}}


def _metrics(value: bool) -> dict:
    return {
        "exact_primary_app_api_match": value,
        "canonical_procedural_signature_match": value,
        "execution_success": value,
        "semantic_successor_match": value,
        "normalized_observation_similarity": float(value),
    }


def test_manifest_is_frozen_complete_and_contains_no_memory_prompt() -> None:
    manifest = build_critical_manifest(_settings())
    assert manifest["candidate_count"] == 22
    assert manifest["state_count"] == 14
    assert manifest["condition_count"] == 308
    assert len({row["condition_key"] for row in manifest["conditions"]}) == 308
    assert all(not row["student_prompt_contains_raw_memory"] for row in manifest["conditions"])
    assert all(not row["runtime_retrieval"] for row in manifest["conditions"])


def test_critical_contract_uses_complete_ordered_prefix() -> None:
    steps = []
    for step_id in (1, 2, 3):
        steps.append(
            {
                "step_id": step_id,
                "current_task_message": "query",
                "exact_executed_code": f"print({step_id})",
                "complete_environment_observation": f"obs-{step_id}",
                "raw_model_response": f"```python\nprint({step_id})\n```",
                "rendered_message_sha256": f"rendered-{step_id}",
            }
        )
    contract = critical_contract({"steps": steps}, 3)
    assert [row["step_id"] for row in contract["history_steps"]] == [1, 2]
    assert contract["target_code"] == "print(3)"
    assert contract["target_observation"] == "obs-3"


def test_benefit_gate_enforces_all_families_and_retained() -> None:
    rows = [
        {"task_id": task_id, "metrics": _metrics(True)}
        for task_id in (
            "0d01c76_3",
            "325d6ec_2",
            "325d6ec_3",
            "634f342_1",
            "634f342_2",
            "634f342_3",
            "8749218_2",
            "8749218_3",
        )
    ]
    assert benefit_gate(rows)["passed"]
    rows[1]["metrics"] = _metrics(False)
    assert not benefit_gate(rows)["passed"]


def test_critical_choice_requires_all_four_behavioral_checks() -> None:
    assert critical_choice_preserved(_metrics(True))
    for key in (
        "exact_primary_app_api_match",
        "canonical_procedural_signature_match",
        "execution_success",
        "semantic_successor_match",
    ):
        values = _metrics(True)
        values[key] = False
        assert not critical_choice_preserved(values)


def test_summary_does_not_make_loss_fix_claims() -> None:
    candidates = preregistered_candidates()[:1]
    task_rows = []
    for task_id, spec in _settings()["gain_loss_audit"]["critical_steps"].items():
        task_rows.append(
            {
                "candidate_id": candidates[0].candidate_id,
                "task_id": task_id,
                "group": spec["group"],
                "metrics": _metrics(True),
                "execution_exception": None,
                "reader_audit": {
                    "maximum_ratio": {
                        "7": 0.1,
                        "14": 0.1,
                        "21": 0.1,
                        "28": 0.1,
                    }
                },
            }
        )
    summary = summarize_critical_rows(task_rows, candidates)
    secondary = summary["candidate_matrix"][0]["loss_secondary"]
    assert secondary["fixed_count_not_claimed_from_one_step"] is None
    assert secondary["worsened_count_not_claimed_from_one_step"] is None
