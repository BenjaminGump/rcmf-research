from __future__ import annotations

from pathlib import Path

from rcmf.config import load_config
from rcmf.training.state_conditioned_program_pair_behavior_7dg3 import (
    GLOBAL_SEED,
    build_pair_behavior_manifest,
    pair_behavior_gate,
    runtime_projection,
)


def _f3_rows() -> list[dict]:
    return [
        {
            "condition_name": "F3_deployment_e_field_raw",
            "state_example_id": f"state-{index}",
            "state_task_id": f"task-{index % 3}",
            "state_step_id": index,
            "transition_id": f"transition-{index % 3}",
            "audit_stratum": "A" if index % 2 else "B",
            "api_documentation_action": False,
            "procedural_tier": 4,
            "signature_class_id": f"class-{index % 3}",
        }
        for index in range(6)
    ]


def _comparison(
    *, signature: float, successor: float, execution: float = 0.0
) -> dict:
    return {
        "canonical_procedural_signature_match": {"difference": signature},
        "semantic_successor_match": {"difference": successor},
        "execution_success": {"difference": execution},
    }


def test_manifest_locks_one_seed_and_changes_only_program_pair_inputs() -> None:
    first = build_pair_behavior_manifest(_f3_rows())
    second = build_pair_behavior_manifest(_f3_rows())

    assert first == second
    assert first["global_seed"] == GLOBAL_SEED == 25101
    assert first["state_count"] == 6
    assert first["condition_count"] == 18
    assert first["condition_name_counts"] == {
        "P1_pairmlp_correct": 6,
        "P2_pairmlp_shuffled_transition": 6,
        "P3_pairmlp_shuffled_state": 6,
    }
    assert first["raw_transition_prompt_count"] == 0
    for row in first["conditions"]:
        assert not row["student_prompt_contains_raw_transition"]
        if row["condition_name"] == "P1_pairmlp_correct":
            assert row["program_state_id"] == row["state_example_id"]
            assert row["program_transition_id"] == row["selector_transition_id"]
        elif row["condition_name"] == "P2_pairmlp_shuffled_transition":
            assert row["program_state_id"] == row["state_example_id"]
            assert row["program_transition_id"] != row["selector_transition_id"]
        else:
            assert row["program_state_id"] != row["state_example_id"]
            assert row["program_transition_id"] == row["selector_transition_id"]


def test_pair_behavior_gate_requires_both_pairing_controls() -> None:
    passed = pair_behavior_gate(
        p1_minus_c0=_comparison(signature=0.20, successor=0.10, execution=-0.03),
        p1_minus_p2=_comparison(signature=0.10, successor=-0.03),
        p1_minus_p3=_comparison(signature=0.00, successor=0.10),
        f3_minus_c0=_comparison(signature=0.40, successor=0.20),
        positive_task_count=5,
    )
    assert passed["passed"]
    assert passed["decision_branch"] == (
        "direct_pair_behavior_valid_factorization_bottleneck"
    )
    failed = pair_behavior_gate(
        p1_minus_c0=_comparison(signature=0.20, successor=0.10),
        p1_minus_p2=_comparison(signature=0.10, successor=-0.06),
        p1_minus_p3=_comparison(signature=0.00, successor=0.10),
        f3_minus_c0=_comparison(signature=0.40, successor=0.20),
        positive_task_count=5,
    )
    assert not failed["passed"]
    assert failed["decision_branch"] == (
        "teacher_forced_objective_not_behaviorally_retained"
    )


def test_runtime_counts_exactly_135_generation_and_replays() -> None:
    report = runtime_projection(
        condition_count=135,
        generation_rates={
            name: {"generation": 8.0}
            for name in ("best", "expected", "conservative")
        },
        replay_rates={name: 4.0 for name in ("best", "expected", "conservative")},
        projected_bytes_per_condition=100,
    )
    assert report["condition_count"] == 135
    assert report["scenarios"]["expected"]["h100_hours"] == 0.3
    assert report["scenarios"]["expected"]["wall_hours"] == 0.45
    assert report["projected_artifact_bytes"] == 13_500


def test_config_and_runner_freeze_pairmlp_without_training() -> None:
    cfg = load_config(
        "configs/benchmark/"
        "stage_c_state_conditioned_program_pair_behavior_7dg3.yaml"
    )
    settings = cfg.raw["stage_c_7dg3"]
    assert settings["global_seed"] == GLOBAL_SEED
    assert settings["expected_pairmlp_checkpoint_sha256"] == (
        "80506a5d9b1c3031b5468fb59c0b6d9e01d7d50ddc1fee49115a88eb8b8b429d"
    )
    assert settings["one_step"]["total_conditions"] == 135
    assert settings["conditional"]["r64_review_threshold_h100_hours"] == 14.0
    source = Path(
        "scripts/run_state_conditioned_program_pair_behavior_7dg3.py"
    ).read_text(encoding="utf-8")
    assert "optimizer.step" not in source
    assert "loss.backward" not in source
    assert "_run_condition(" in source
    assert "per_metric_seed_offset=False" in source
