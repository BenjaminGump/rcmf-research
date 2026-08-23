from __future__ import annotations

from pathlib import Path

import torch

from scripts import prepare_appworld_structured_rescue_7hr as prepare_script
from rcmf.training.appworld_structured_rescue_7hr import (
    FeatureSchema,
    StructuredLatentComposer,
    build_feature_vector,
    classify_paired_outcome,
    compiler_checkpoint_score,
    gate_validation,
    leakage_audit,
    no_fixed_point_permutation,
    quantile_buckets,
    select_diverse_panel,
    select_gate_threshold,
)


def test_prepare_script_import_contract() -> None:
    assert callable(prepare_script.main)
    source = prepare_script.__file__
    assert source is not None
    assert 'metadata["split"]' not in Path(source).read_text(encoding="utf-8")


def _metrics(successor: bool, signature: bool, execution: bool) -> dict[str, bool]:
    return {
        "semantic_successor_match": successor,
        "action_signature_match": signature,
        "execution_success": execution,
    }


def test_paired_outcome_labels_and_safety_overlap() -> None:
    assert classify_paired_outcome(_metrics(False, False, True), _metrics(True, False, True))["label"] == "POSITIVE"
    assert classify_paired_outcome(_metrics(True, True, True), _metrics(False, True, True))["label"] == "HARMFUL"
    assert classify_paired_outcome(_metrics(False, False, True), _metrics(False, False, True))["label"] == "NEUTRAL"
    overlap = classify_paired_outcome(_metrics(False, False, True), _metrics(True, False, False))
    assert overlap["rule_overlap"]
    assert overlap["label"] == "HARMFUL"


def test_panel_is_deterministic_and_covers_tasks() -> None:
    rows = []
    for task in range(6):
        for step in range(5):
            rows.append(
                {
                    "state_example_id": f"s-{task}-{step}",
                    "state_task_id": f"t-{task}",
                    "step_bucket": ("early", "middle", "late")[step % 3],
                    "predicted_action_stratum": ("read", "write")[step % 2],
                    "selector_score_quantile": step % 4,
                    "selector_margin_quantile": (step + task) % 4,
                }
            )
    first = select_diverse_panel(rows, count=18)
    second = select_diverse_panel(rows, count=18)
    assert first == second
    assert {value.split("-")[1] for value in first} == {str(value) for value in range(6)}
    assert quantile_buckets([4.0, 1.0, 3.0, 2.0], 2) == [1, 0, 1, 0]


def _feature_source() -> dict[str, object]:
    return {
        "state_step_index": 3,
        "history_turn_count": 2,
        "prompt_tokens": 1000,
        "context_headroom": 39960,
        "context_limit": 40960,
        "intent_distributions": {
            "target_app": {"spotify": 0.8, "phone": 0.2},
            "target_api": {"spotify.login": 0.7, "phone.login": 0.3},
            "action_type": {"authentication": 0.75, "read_query": 0.25},
            "completion_action": {"false": 0.9, "true": 0.1},
        },
        "selector_class_scores": [0.5, 0.3, -0.2],
        "memory_apps": ["spotify"],
        "memory_apis": ["spotify.login"],
        "memory_action_type": "authentication",
        "memory_control_flow": ["for"],
        "memory_flags": {
            "authentication": True,
            "read": False,
            "write": False,
            "documentation": False,
            "completion": False,
        },
        "memory_class_size": 2,
        "memory_token_length": 400,
        "memory_parent_step": 4,
        "memory_api_call_count": 1,
        "projected_prompt_overhead": 400,
        "stage_compatibility": {"score": 0.875, "compatible": True, "conflict_count": 1},
    }


def test_structured_features_are_identity_and_target_free() -> None:
    schema = FeatureSchema(
        app_vocabulary=("phone", "spotify", "UNK"),
        api_vocabulary=("phone.login", "spotify.login", "UNK"),
        action_vocabulary=("authentication", "read_query", "UNK"),
        control_vocabulary=("for", "while"),
    )
    values, names = build_feature_vector(schema, _feature_source())
    audit = leakage_audit(
        names,
        ("target_action", "target_observation", "task_id", "state_id", "transition_id", "procedural_tier"),
    )
    assert len(values) == len(names)
    assert audit["deployment_available"]


def test_structured_composer_starts_at_zero_and_uses_gate() -> None:
    model = StructuredLatentComposer(feature_dim=5)
    features = torch.randn(3, 5)
    base = torch.randn(3, 256)
    gate = torch.tensor([0.1, 0.5, 0.9])
    output = model(features, base, gate)
    assert torch.equal(output, torch.zeros_like(output))
    with torch.no_grad():
        model.beta.fill_(1.0)
    output = model(features, base, gate)
    assert torch.allclose(output, gate[:, None] * base)


def test_gate_selection_and_validation_contract() -> None:
    candidates = [
        {
            "threshold": 0.5,
            "gated_successor": 0.70,
            "gated_signature": 0.60,
            "gated_execution": 0.95,
            "harmful_activation_count": 2,
            "activation_rate": 0.40,
        },
        {
            "threshold": 0.7,
            "gated_successor": 0.70,
            "gated_signature": 0.60,
            "gated_execution": 0.95,
            "harmful_activation_count": 1,
            "activation_rate": 0.30,
        },
    ]
    assert select_gate_threshold(candidates)["threshold"] == 0.7
    result = gate_validation(
        {
            **candidates[1],
            "bare_successor": 0.65,
            "bare_signature": 0.55,
            "bare_execution": 0.96,
            "harmful_activation_rate": 0.10,
            "positive_prevalence_lift": 0.12,
        },
        minimum_activation_rate=0.05,
        maximum_activation_rate=0.60,
        maximum_harmful_activation_rate=0.20,
        minimum_positive_prevalence_lift=0.10,
        maximum_execution_drop=0.02,
    )
    assert result["passed"]


def test_compiler_checkpoint_and_permutation_contracts() -> None:
    metrics = {
        "correct_successor": 0.7,
        "zero_successor": 0.5,
        "transition_shuffle_successor": 0.55,
        "state_shuffle_successor": 0.50,
        "correct_signature": 0.6,
        "zero_signature": 0.4,
        "transition_shuffle_signature": 0.45,
        "state_shuffle_signature": 0.40,
        "correct_execution": 0.95,
        "zero_execution": 0.96,
        "maximum_ratio": 1.0,
        "raw_policy_kl": 0.3,
    }
    assert compiler_checkpoint_score(metrics)["eligible"]
    permutation = no_fixed_point_permutation(["a", "b", "c"], purpose="test")
    assert set(permutation) == {"a", "b", "c"}
    assert set(permutation.values()) == {"a", "b", "c"}
    assert all(source != target for source, target in permutation.items())
