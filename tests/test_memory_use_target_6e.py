from __future__ import annotations

import math
from pathlib import Path

import torch

from rcmf.benchmarks.appworld.prompt import get_initial_messages
from rcmf.training.memory_use_target_6e import (
    CachedArchitectureScorer,
    action_signature,
    add_relative_targets,
    intent_feature_vector,
    messages_with_serialized_transition,
    pairwise_coverage,
    relative_target_objective,
    select_serialization_audit_pairs,
    serialization_robustness,
    transition_teacher_section_for_template,
)


def _transition(index: int = 0) -> dict:
    return {
        "source_task_goal": f"Find record {index}",
        "canonical_pre_action_state": "OBS\nline two",
        "complete_action": "apis.phone.search_contacts(query='Ada')",
        "complete_post_action_observation": "{'name': 'Ada'}",
    }


def _pair(cell: str, index: int, category: str) -> dict:
    utility = {"positive": 0.2, "neutral": 0.0, "negative": -0.2}[category]
    return {
        "pair_id": f"{cell}-{index}",
        "cell": cell,
        "state_example_id": f"state-{index // 4}",
        "state_task_id": f"task-{index % 9}",
        "transition_id": f"transition-{index}",
        "transition_parent_id": f"parent-{index % 11}",
        "utility_category": category,
        "text_utility": utility + index * 1.0e-4,
        "state_apps": ["phone"],
        "transition_apps": ["phone" if index % 2 else "spotify"],
    }


def test_action_signature_extracts_deterministic_intent_and_ast_calls() -> None:
    signature = action_signature(
        "```python\nresult = apis.phone.search_contacts(query='Ada')\nprint(result)\n```"
    )
    assert signature["primary_app"] == "phone"
    assert signature["primary_api"] == "phone.search_contacts"
    assert signature["read_query_action"]
    assert signature["coarse_action_type"] == "read_query"
    assert "apis.phone.search_contacts" in signature["function_ast_call_names"]


def test_serializations_preserve_exact_field_strings_and_placement() -> None:
    transition = _transition()
    for template in ("canonical_json", "compact_tagged"):
        section = transition_teacher_section_for_template(transition, template)
        for value in transition.values():
            assert value in section or template == "canonical_json"
        messages = messages_with_serialized_transition(
            [*get_initial_messages("full_demo"), {"role": "user", "content": "current"}],
            transition,
            "full_demo",
            template,
        )
        assert messages[-1]["content"].startswith("[DECISION TRANSITION MEMORY]")
        assert "[CURRENT APPWORLD STATE START]\ncurrent" in messages[-1]["content"]


def test_audit_selection_is_exact_deterministic_and_category_balanced() -> None:
    rows = []
    for cell in ("A", "D"):
        for category_index, category in enumerate(("positive", "neutral", "negative")):
            rows.extend(_pair(cell, category_index * 50 + index, category) for index in range(40))
    first = select_serialization_audit_pairs(rows, seed=21)
    second = select_serialization_audit_pairs(list(reversed(rows)), seed=21)
    assert first["pair_count"] == 192
    assert [row["pair_id"] for row in first["rows"]] == [row["pair_id"] for row in second["rows"]]
    for cell in ("A", "D"):
        selected = [row for row in first["rows"] if row["cell"] == cell]
        assert len(selected) == 96
        assert sum(row["audit_selection_category"] == "random" for row in selected) == 24


def test_relative_targets_use_within_state_median_iqr_and_average_ties() -> None:
    rows = [
        {**_pair("A", index, "neutral"), "state_example_id": "s", "text_utility": value}
        for index, value in enumerate((-1.0, 0.0, 0.0, 3.0))
    ]
    output = add_relative_targets(rows, scale_epsilon=1.0e-6, robust_clip=8.0)
    assert [row["T1_median"] for row in output] == [-1.0, 0.0, 0.0, 3.0]
    assert output[1]["T3"] == output[2]["T3"]
    assert math.isclose(output[1]["T3"], 0.5)
    coverage = pairwise_coverage(output, thresholds=(0.02, 0.05, 0.10))
    assert coverage["0.05"]["pair_count"] == 5


def test_intent_feature_vector_uses_predicted_probability_mass() -> None:
    probabilities = {
        "target_app": {"phone": 0.8},
        "target_api": {"phone.search_contacts": 0.7},
        "action_type": {"api_read_or_login": 0.6},
        "completion_action": {"false": 0.9},
    }
    signature = action_signature("apis.phone.search_contacts(query='Ada')")
    assert intent_feature_vector(probabilities, signature) == [0.8, 0.7, 0.6, 0.9]


def test_relative_objective_has_pairwise_gradient_for_positive_and_negative_gaps() -> None:
    rows = add_relative_targets(
        [
            {**_pair("A", 0, "positive"), "state_example_id": "s", "text_utility": 0.3, "transition_signature": action_signature("apis.phone.search_contacts()")},
            {**_pair("A", 1, "negative"), "state_example_id": "s", "text_utility": -0.3, "transition_signature": action_signature("apis.phone.search_contacts()")},
        ],
        scale_epsilon=1.0e-6,
        robust_clip=8.0,
    )
    scores = torch.zeros(2, requires_grad=True)
    loss, parts = relative_target_objective(
        scores, rows, target_name="T7", pair_gap_threshold=0.05,
        pair_gap_weight_clip=0.25, teacher_temperature=0.1,
        student_temperature=0.1, huber_delta=0.1,
        loss_weights={"percentile_regression": 0.2, "listwise": 0.4, "pairwise": 1.0},
        matched_intent_only=True,
    )
    loss.backward()
    assert parts["pairwise"] > 0
    assert scores.grad is not None
    assert scores.grad[0] < 0 < scores.grad[1]


def test_cached_field_and_cross_scorers_have_expected_shapes() -> None:
    field = CachedArchitectureScorer(
        "field", state_views=2, transition_views=3, input_dim=8, cross_dim=12,
        projection_dim=4, interaction_rank=3, hidden_dim=5, dropout=0.0,
    )
    cross = CachedArchitectureScorer(
        "cross", state_views=2, transition_views=3, input_dim=8, cross_dim=12,
        projection_dim=4, interaction_rank=3, hidden_dim=5, dropout=0.0,
    )
    assert field(torch.randn(7, 2, 8), torch.randn(7, 3, 8), None).shape == (7,)
    assert cross(None, None, torch.randn(7, 12)).shape == (7,)


def test_serialization_gate_uses_all_three_templates() -> None:
    rows = []
    for state in range(4):
        for memory in range(8):
            utility = (memory - 3.5) / 10.0
            for offset, template in enumerate(("template0", "canonical_json", "compact_tagged")):
                rows.append({
                    "pair_id": f"s{state}-m{memory}",
                    "state_example_id": f"s{state}",
                    "template": template,
                    "text_utility": utility + offset * 1.0e-4,
                    "combined_prompt_tokens": 100 + memory + offset,
                })
    result = serialization_robustness(
        rows,
        gate={
            "median_template_spearman": 0.70,
            "sign_agreement": 0.75,
            "mean_per_state_top4_overlap": 0.50,
            "maximum_length_utility_abs_correlation": 1.0,
        },
    )
    assert result["complete_pair_count"] == 32
    assert result["gate_passed"]


def test_entrypoints_preserve_hard_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    scoring = (root / "scripts/run_serialization_robustness_6e.py").read_text(encoding="utf-8")
    models = (root / "scripts/run_memory_use_target_models_6e.py").read_text(encoding="utf-8")
    assert "_score_mean_target_nll" in scoring
    assert ".generate(" not in scoring
    assert "forward_train(" not in scoring
    assert "build_backend" not in models
    assert "AdditiveTokenMemoryInjector" not in models
    assert ".generate(" not in models


def test_preflight_uses_actual_exp019_record_and_can_log_bootstrap_failure() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/prepare_memory_use_target_6e.py").read_text(
        encoding="utf-8"
    )
    assert 'exp019 / "postrun_validation.json"' in source
    assert 'exp019 / "final_summary.json"' not in source
    assert 'parser.add_argument("--prior-bootstrap-failure-id"' in source
    assert "append_jsonl_fsync(" in source
