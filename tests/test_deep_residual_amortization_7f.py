from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import torch
import yaml

from rcmf.training.deep_residual_amortization_7f import (
    SharedDeepResidualDecoder,
    aggregate_and_select_class,
    best_visited_checkpoint,
    build_amortized_one_step_manifest,
    classify_one_step_behavior,
    continue_after_u8,
    deterministic_mismatch_indices,
    differentiable_layer_ratio_projection,
    revised_u16_runtime_authorization,
)
from scripts.run_raw_memory_first37_7f import (
    RESULT_VERSION,
    SOURCE_RESULT_VERSION,
    _promote_v2_task_row,
)


def test_first37_task_ids_are_loaded_as_exact_strings() -> None:
    config_path = Path("configs/benchmark/stage_c_deep_residual_amortization_7f.yaml")
    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))["stage_c_7f"]
    task_ids = settings["first37"]["task_ids"]
    assert len(task_ids) == 37
    assert len(set(task_ids)) == 37
    assert all(isinstance(task_id, str) for task_id in task_ids)
    assert task_ids[24:27] == ["8749218_1", "8749218_2", "8749218_3"]
    assert settings["expected_selector_sha256"] == (
        "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    )


def test_full_agent_uses_original_appworld_evaluation_contract() -> None:
    bridge_source = Path("scripts/appworld_full_agent_bridge_7f.py").read_text(encoding="utf-8")
    assert "load_ground_truth=False" not in bridge_source
    assert "world.evaluate(suppress_errors=True)" in bridge_source
    assert 'evaluation.get("success")' in bridge_source


@pytest.mark.parametrize("success", [False, True])
def test_v2_phase_a_rows_are_promoted_without_rewriting_source(
    tmp_path: Path, success: bool
) -> None:
    task_id = "task_1"
    source = tmp_path / "phase_a_first37_v2" / "task_results" / f"{task_id}.json"
    source.parent.mkdir(parents=True)
    source_row = {
        "format": SOURCE_RESULT_VERSION,
        "status": "complete",
        "task_id": task_id,
        "config_sha256": "config",
        "selector_sha256": "selector",
        "success": False,
        "evaluation": {"difficulty": 2, "num_tests": 6, "success": success},
    }
    source.write_text(json.dumps(source_row, sort_keys=True), encoding="utf-8")
    source_bytes = source.read_bytes()

    promoted = _promote_v2_task_row(
        artifact_dir=tmp_path,
        task_id=task_id,
        config_sha256="config",
        selector_sha256="selector",
    )

    assert promoted is not None
    assert promoted["format"] == RESULT_VERSION
    assert promoted["success"] is success
    assert promoted["success_source"] == "evaluation.success"
    assert promoted["source_result"]["format"] == SOURCE_RESULT_VERSION
    assert source.read_bytes() == source_bytes


def test_compiler_backward_remains_inside_deep_residual_hook() -> None:
    source = Path("scripts/run_deep_residual_compiler_7f.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    matching = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        uses_hook = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "DeepResidualHooks"
            for item in node.items
        )
        has_backward = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "backward"
            for child in ast.walk(node)
        )
        matching.append(uses_hook and has_backward)
    assert any(matching)


def test_compiler_runtime_authorization_uses_rectified_phase_a_summary() -> None:
    source = Path("scripts/run_deep_residual_compiler_7f.py").read_text(
        encoding="utf-8"
    )
    assert 'phase_a_first37_v3"' in source
    assert 'phase_a_first37_v2"' not in source


def test_shared_decoder_has_locked_shape_and_no_bias() -> None:
    decoder = SharedDeepResidualDecoder(program_dim=8, model_dim=16)
    output = decoder(torch.ones(2, 8))
    assert output.shape == (2, 4, 4, 16)
    assert decoder.linear.bias is None


def test_layer_projection_is_differentiable_and_bounded() -> None:
    raw = torch.full((2, 4, 4, 8), 10.0, requires_grad=True)
    base = torch.ones_like(raw)
    projected, ratios = differentiable_layer_ratio_projection(raw, base)
    assert float(ratios["maximum_ratio"].detach()) == pytest.approx(1.0, abs=1.0e-6)
    projected.sum().backward()
    assert raw.grad is not None
    assert torch.isfinite(raw.grad).all()


def test_best_checkpoint_uses_huber_and_positive_spearman() -> None:
    history = [
        {"updates_per_pair": 4, "a_validation_huber": 0.2, "a_validation_spearman": 0.3, "maximum_ratio": 1.0},
        {"updates_per_pair": 8, "a_validation_huber": 0.1, "a_validation_spearman": -0.1, "maximum_ratio": 1.0},
        {"updates_per_pair": 16, "a_validation_huber": 0.15, "a_validation_spearman": 0.4, "maximum_ratio": 1.0},
    ]
    assert best_visited_checkpoint(history)["updates_per_pair"] == 16


def test_u8_continuation_uses_u4_only() -> None:
    result = continue_after_u8(
        {
            "a_validation_huber": 0.95,
            "a_validation_spearman": 0.22,
            "maximum_ratio": 1.0,
            "previous": {"a_validation_huber": 1.0, "a_validation_spearman": 0.20},
        }
    )
    assert result["continue_to_u16"]


def test_revised_u16_runtime_gate_uses_measured_u8_throughput() -> None:
    allowed = revised_u16_runtime_authorization(
        phase_a_actual_h100_hours=1.0,
        pairmlp_elapsed_through_u8_hours=5.0,
        fixed_final_evaluation_hours=2.0,
        phase_c_one_step_hours=0.5,
        review_threshold_h100_hours=18.0,
    )
    assert allowed["projected_total_h100_hours_through_u16"] == pytest.approx(13.5)
    assert allowed["automatic_u16_authorized"] is True
    blocked = revised_u16_runtime_authorization(
        phase_a_actual_h100_hours=2.0,
        pairmlp_elapsed_through_u8_hours=7.0,
        fixed_final_evaluation_hours=2.0,
        phase_c_one_step_hours=0.5,
        review_threshold_h100_hours=18.0,
    )
    assert blocked["projected_total_h100_hours_through_u16"] == pytest.approx(18.5)
    assert blocked["automatic_u16_authorized"] is False


def test_class_selection_uses_mean_not_duplicate_sum() -> None:
    result = aggregate_and_select_class(
        [0.6, 0.6, 1.0],
        ["large", "large", "small"],
        legal_transition_ids=["a", "b", "c"],
        ordered_transition_ids=["a", "b", "c"],
    )
    assert result["selected_class_id"] == "small"


def test_control_indices_change_the_actual_entity() -> None:
    entities = ["state-a", "state-a", "state-b", "state-c"]
    indices = deterministic_mismatch_indices(
        entities,
        ["pair-0", "pair-1", "pair-2", "pair-3"],
        namespace="test-state-shuffle",
    )
    assert all(entities[index] != entities[source] for index, source in enumerate(indices))


def test_amortized_one_step_manifest_freezes_memory_specific_controls() -> None:
    rows = [
        {
            "condition_name": "F3_deployment_e_field_raw",
            "state_example_id": f"state-{index:02d}",
            "state_task_id": f"task-{index % 9}",
            "state_step_id": index,
            "audit_stratum": "A",
            "transition_id": f"transition-{index % 5}",
        }
        for index in range(45)
    ]
    manifest = build_amortized_one_step_manifest(rows, model_kind="pairmlp")
    assert manifest["state_count"] == 45
    assert manifest["condition_count"] == 180
    assert manifest["student_prompt_contains_raw_transition"] is False
    for row in manifest["conditions"]:
        if row["condition_name"] == "P2_pairmlp_transition_shuffle":
            assert row["program_transition_id"] != row["selector_transition_id"]
        if row["condition_name"] == "P3_pairmlp_state_shuffle":
            assert row["program_state_example_id"] != row["state_example_id"]


def test_behavior_classification_strong_and_partial() -> None:
    strong = classify_one_step_behavior(
        p1_minus_c0={"action_signature": 0.2, "semantic_successor": 0.1},
        p1_minus_p2={"action_signature": 0.1, "semantic_successor": 0.0},
        p1_minus_p3={"action_signature": 0.1, "semantic_successor": 0.0},
        execution_drop=0.0,
        positive_task_count=5,
    )
    assert strong["classification"] == "STRONG_POSITIVE"
    partial = classify_one_step_behavior(
        p1_minus_c0={"action_signature": 0.1, "semantic_successor": 0.0},
        p1_minus_p2={"action_signature": 0.02, "semantic_successor": 0.0},
        p1_minus_p3={"action_signature": -0.01, "semantic_successor": 0.0},
        execution_drop=0.0,
        positive_task_count=4,
    )
    assert partial["classification"] == "PARTIAL_POSITIVE"
    degraded_other = classify_one_step_behavior(
        p1_minus_c0={"action_signature": 0.2, "semantic_successor": 0.1},
        p1_minus_p2={"action_signature": 0.1, "semantic_successor": -0.1},
        p1_minus_p3={"action_signature": 0.1, "semantic_successor": -0.1},
        execution_drop=0.0,
        positive_task_count=5,
    )
    assert degraded_other["classification"] == "PARTIAL_POSITIVE"
