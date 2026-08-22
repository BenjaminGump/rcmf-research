from __future__ import annotations

import pytest
import torch

from rcmf.training.direct_injection_channel_7dh import (
    DirectDeltaInjector,
    build_channel_pair_manifest,
    channel_gate,
    continuation_decision,
    cyclic_derangement,
    require_global_seed,
    runtime_projection,
)


def _condition(index: int) -> dict[str, object]:
    return {
        "condition_name": "P1_pairmlp_correct",
        "audit_stratum": "A" if index % 2 == 0 else "B",
        "state_example_id": f"state-{index:02d}",
        "state_task_id": f"task-{index % 9}",
        "program_transition_id": f"transition-{index:02d}",
        "procedural_tier": 4,
        "signature_class_id": f"signature-{index:02d}",
    }


def _pair(index: int) -> dict[str, object]:
    return {
        "pair_id": f"state-{index:02d}::transition::transition-{index:02d}",
        "state_example_id": f"state-{index:02d}",
        "state_task_id": f"task-{index % 9}",
        "transition_id": f"transition-{index:02d}",
        "transition_parent_id": f"parent-{index % 5}",
        "cell": "E",
    }


def test_direct_injector_is_identity_reshape_without_parameters() -> None:
    injector = DirectDeltaInjector(model_dim=3, num_tokens=2)
    values = torch.arange(12, dtype=torch.float32).view(2, 6)
    result = injector(values)
    assert result.shape == (2, 2, 3)
    assert torch.equal(result.flatten(start_dim=1), values)
    assert list(injector.parameters()) == []


def test_direct_injector_rejects_wrong_or_nonfinite_delta() -> None:
    injector = DirectDeltaInjector(model_dim=3, num_tokens=2)
    with pytest.raises(ValueError):
        injector(torch.zeros(1, 5))
    bad = torch.zeros(1, 6)
    bad[0, 0] = torch.nan
    with pytest.raises(ValueError):
        injector(bad)


def test_manifest_freezes_exact_primary_pairs_and_k_missingness() -> None:
    conditions = [_condition(index) for index in range(32)]
    pairs = [_pair(index) for index in range(32)]
    counts = {
        str(row["pair_id"]): (15 if index == 0 else 20)
        for index, row in enumerate(pairs)
    }
    cached = {str(row["pair_id"]) for row in pairs[:12]}
    first = build_channel_pair_manifest(
        conditions=conditions,
        e_pairs=pairs,
        last_user_counts=counts,
        cached_teacher_pair_ids=cached,
    )
    second = build_channel_pair_manifest(
        conditions=list(reversed(conditions)),
        e_pairs=list(reversed(pairs)),
        last_user_counts=counts,
        cached_teacher_pair_ids=cached,
    )
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["cached_teacher_count"] == 12
    assert first["new_teacher_count"] == 20
    assert first["feasibility"]["4"]["feasible_count"] == 32
    assert first["feasibility"]["16"]["feasible_count"] == 31
    assert len(first["cyclic_controls"]["16"]) == 31


def test_cyclic_control_has_no_fixed_points() -> None:
    values = [f"pair-{index}" for index in range(12)]
    permutation = cyclic_derangement(values, namespace="test")
    assert sorted(permutation) == list(range(12))
    assert all(index != value for index, value in enumerate(permutation))


def test_continuation_requires_material_improvement_and_valid_ratio() -> None:
    decision = continuation_decision(
        {"teacher_policy_kl": 1.0, "teacher_token_ce": 2.0},
        {
            "teacher_policy_kl": 0.94,
            "teacher_token_ce": 1.99,
            "delta_ratio_max": 1.0,
        },
    )
    assert decision["continue_to_u16"] is True
    bad = continuation_decision(
        {"teacher_policy_kl": 1.0, "teacher_token_ce": 2.0},
        {
            "teacher_policy_kl": 0.99,
            "teacher_token_ce": 1.99,
            "delta_ratio_max": 1.01,
        },
    )
    assert bad["continue_to_u16"] is False


def _comparison(signature: float, successor: float, execution: float = 0.0):
    return {
        "canonical_procedural_signature_match": {"mean_difference": signature},
        "semantic_successor_match": {"mean_difference": successor},
        "execution_success": {"mean_difference": execution},
    }


def test_channel_gate_requires_retention_shuffle_and_task_coverage() -> None:
    result = channel_gate(
        o_minus_c0=_comparison(0.28, 0.20, -0.03),
        o_minus_s=_comparison(0.06, 0.00),
        f3_minus_c0=_comparison(0.40, 0.40),
        positive_task_count=6,
    )
    assert result["passed"] is True
    failed = channel_gate(
        o_minus_c0=_comparison(0.28, 0.20, -0.03),
        o_minus_s=_comparison(0.00, 0.00),
        f3_minus_c0=_comparison(0.40, 0.40),
        positive_task_count=6,
    )
    assert failed["passed"] is False


def test_global_seed_is_locked() -> None:
    require_global_seed(25101)
    with pytest.raises(ValueError):
        require_global_seed(1)


def test_runtime_projection_includes_teacher_forced_checkpoint_forwards() -> None:
    result = runtime_projection(
        feasible_counts={"4": 1, "8": 1, "16": 1},
        new_teacher_count=0,
        rates={
            name: {"forward": 1.0, "backward": 1.0, "generation": 1.0}
            for name in ("best", "expected", "conservative")
        },
        maximum_updates_per_pair=16,
    )
    expected = result["scenarios"]["expected"]
    assert expected["teacher_forced_evaluation_hours"] == pytest.approx(24 / 3600)
    assert expected["maximum_h100_hours"] == pytest.approx((192 + 24 + 6) / 3600)
