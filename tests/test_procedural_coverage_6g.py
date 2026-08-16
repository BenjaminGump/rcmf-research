from __future__ import annotations

import json
from pathlib import Path

import pytest

from rcmf.training.procedural_coverage_6g import (
    candidate_space_summary,
    context_preflight_summary,
    future_runtime_projection,
    missing_state_diagnostics,
    select_decision_branch,
    signature_redundancy_summary,
    two_axis_cell,
)
from scripts.prepare_procedural_coverage_6g import (
    _atomic_write_jsonl,
    _one_step_condition_preflight,
    _validate_preflight_checkpoint,
)
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _label(
    state: str,
    transition: str,
    tier: int,
    *,
    parent: str = "parent-1",
    signature: str = "signature-1",
    scoreable: bool = True,
    action_type: str = "read_query",
) -> dict[str, object]:
    return {
        "pair_id": f"{state}::transition::{transition}",
        "cell": "B",
        "state_example_id": state,
        "state_task_id": f"task-{state}",
        "transition_id": transition,
        "transition_parent_id": parent,
        "transition_parent_task_id": f"task-{parent}",
        "transition_split": "train",
        "transition_signature_sha256": signature,
        "query_coarse_action_type": action_type,
        "transition_coarse_action_type": action_type,
        "transition_api_documentation_action": False,
        "procedural_tier": tier,
        "exact_api_sequence": tier >= 3,
        "same_primary_app": tier >= 2,
        "canonical_action_schema_match": tier >= 4,
        "state_stage_compatible": tier >= 3,
        "scoreable_under_context": scoreable,
        "over_context": not scoreable,
    }


def test_two_axis_cell_is_strict() -> None:
    assert two_axis_cell("train", "train") == "A"
    assert two_axis_cell("validation", "train") == "B"
    assert two_axis_cell("train", "heldout") == "C"
    assert two_axis_cell("validation", "heldout") == "D"
    with pytest.raises(ValueError):
        two_axis_cell("validation", "validation")


def test_candidate_space_reports_nominal_and_diverse_coverage() -> None:
    rows = [
        _label("s1", "m1", 4, parent="p1", signature="x"),
        _label("s1", "m2", 3, parent="p2", signature="y"),
        _label("s2", "m3", 4, parent="p1", signature="x"),
    ]
    summary = candidate_space_summary(
        rows,
        state_ids=["s1", "s2", "s3"],
        state_task_by_id={"s1": "t1", "s2": "t2", "s3": "t3"},
    )
    assert summary["states_with_tier3_or_4"] == 2
    assert summary["states_with_diverse_tier3_or_4"] == 1
    assert summary["state_rows"][2]["candidate_count"] == 0
    assert summary["state_rows"][0]["best_candidate_ids"] == ["m1"]


def test_context_summary_masks_over_context_without_truncation() -> None:
    labels = [
        _label("s1", "m1", 4),
        _label("s1", "m2", 3, scoreable=False),
        _label("s2", "m3", 4, scoreable=False),
    ]
    preflight = []
    for index, label in enumerate(labels):
        over = bool(label["over_context"])
        preflight.append(
            {
                "pair_id": label["pair_id"],
                "state_example_id": label["state_example_id"],
                "transition_id": label["transition_id"],
                "parent_memory_id": label["transition_parent_id"],
                "state_prompt_tokens": 10,
                "transition_section_tokens": 20,
                "combined_prompt_tokens": 30,
                "target_tokens": 2,
                "total_tokens_with_target": 32 + index,
                "over_context": over,
                "truncated": False,
            }
        )
    summary = context_preflight_summary(preflight, label_rows=labels)
    assert summary["legal_pair_count"] == 3
    assert summary["scoreable_pair_count"] == 1
    assert summary["over_context_pair_count"] == 2
    assert summary["truncated_pair_count"] == 0
    assert (
        summary["states_whose_only_tier3_or_4_candidates_are_over_context_count"]
        == 1
    )


def test_missing_state_reports_absent_api_sequence() -> None:
    labels = [_label("s1", "m1", 1)]
    queries = {
        "s1": {
            "task_id": "t1",
            "target_signature": {
                "signature_sha256": "query-hash",
                "primary_app": "spotify",
                "primary_api": "search_tracks",
                "ordered_api_sequence": ["spotify.search_tracks"],
                "coarse_action_type": "read_query",
            },
        }
    }
    transitions = [
        {
            "action_signature": {
                "ordered_api_sequence": ["gmail.search_emails"]
            }
        }
    ]
    rows = missing_state_diagnostics(
        labels,
        state_ids=["s1"],
        query_signatures=queries,
        transition_signatures=transitions,
        scoreable_only=True,
    )
    assert rows[0]["procedure_absent_from_complete_corpus"] is True
    assert "ordered_api_sequence" in rows[0]["missing_signature_components"]


@pytest.mark.parametrize(
    ("b", "diverse", "e", "expected"),
    [
        (0.8, 0.8, 0.9, "full_transition_bank_procedural_coverage_passed"),
        (0.8, 0.6, 0.9, "nominal_procedural_coverage_lacks_diversity"),
        (0.6, 0.6, 0.8, "procedural_coverage_depends_on_heldout_parent_transitions"),
        (0.6, 0.6, 0.6, "complete_training_transition_corpus_coverage_insufficient"),
    ],
)
def test_decision_tree(b: float, diverse: float, e: float, expected: str) -> None:
    assert (
        select_decision_branch(
            b_coverage=b,
            b_diverse_coverage=diverse,
            e_coverage=e,
            threshold=0.7,
        )
        == expected
    )


def test_signature_redundancy_preserves_all_rows() -> None:
    rows = []
    for transition_id, signature, parent in (
        ("m1", "x", "p1"),
        ("m2", "x", "p2"),
        ("m3", "y", "p2"),
    ):
        rows.append(
            {
                "transition_id": transition_id,
                "parent_id": parent,
                "parent_task_id": f"task-{parent}",
                "action_signature": {
                    "signature_sha256": signature,
                    "primary_app": "spotify",
                    "primary_api": "search_tracks",
                    "coarse_action_type": "read_query",
                    "api_documentation_action": False,
                },
            }
        )
    summary = signature_redundancy_summary(rows)
    assert summary["transition_count"] == 3
    assert summary["unique_signature_count"] == 2
    assert summary["duplicate_group_count"] == 1
    assert summary["largest_groups"][0]["parent_diversity"] == 2


def test_future_runtime_projection_keeps_optional_cross_encoder_separate() -> None:
    summary = future_runtime_projection(
        newly_added_transitions=10,
        representation_observed_seconds=100,
        representation_observed_transitions=10,
        representation_token_ratio=1.0,
        representation_quadratic_token_ratio=1.0,
        model_reference_seconds=3600,
        pair_scale=2.0,
        new_cross_encoder_pairs=100,
        cross_encoder_seconds_per_pair=2.0,
        one_step_condition_count=10,
        generation_seconds={"best": 1, "expected": 2, "conservative": 3},
        replay_execution_seconds={"best": 1, "expected": 1, "conservative": 1},
        storage_bytes={"one": 10, "two": 20},
        review_threshold_h100_hours=12,
    )
    assert summary["artifact_size_total_bytes"] == 30
    assert summary["optional_cross_encoder_expected_h100_hours"] == pytest.approx(
        200 / 3600
    )
    assert summary["required_expected_h100_hours"] == pytest.approx(
        (100 + 3600 + 30) / 3600
    )


def test_atomic_preflight_checkpoint_validates_partition(tmp_path: Path) -> None:
    legal_path = tmp_path / "state.legal.jsonl"
    illegal_path = tmp_path / "state.illegal.jsonl"
    meta_path = tmp_path / "state.meta.json"
    legal = [
        {
            "pair_id": "s::transition::m1",
            "state_example_id": "s",
            "transition_id": "m1",
        }
    ]
    illegal = [
        {
            "pair_id": "s::transition::m2",
            "state_example_id": "s",
            "transition_id": "m2",
        }
    ]
    _atomic_write_jsonl(legal_path, legal)
    _atomic_write_jsonl(illegal_path, illegal)
    atomic_write_json(
        meta_path,
        {
            "state_example_id": "s",
            "transition_manifest_sha256": "manifest",
            "context_limit": 40960,
            "legal_count": 1,
            "illegal_count": 1,
            "legal_rows_sha256": sha256_file(legal_path),
            "illegal_rows_sha256": sha256_file(illegal_path),
        },
    )
    loaded = _validate_preflight_checkpoint(
        legal_path=legal_path,
        illegal_path=illegal_path,
        meta_path=meta_path,
        state_id="s",
        transition_ids={"m1", "m2"},
        transition_manifest_sha256="manifest",
        context_limit=40960,
    )
    assert loaded == (legal, illegal)


def test_one_step_condition_counts_do_not_replace_missing_conditions() -> None:
    rows = [
        _label("s1", "m1", 4),
        _label("s1", "m2", 1),
        _label("s2", "m3", 1, action_type="write_mutation"),
    ]
    result = _one_step_condition_preflight(
        rows,
        one_step_rows=[
            {"state_example_id": "s1", "task_id": "t1"},
            {"state_example_id": "s2", "task_id": "t2"},
        ],
    )
    assert result["target_base_condition_count"] == 12
    assert result["missing_base_condition_count"] > 0
    assert result["state_rows"][1]["train_parent_tier3_or_4_candidates"] == 0


def test_exp023_entrypoint_has_no_model_or_appworld_execution() -> None:
    source = Path("scripts/prepare_procedural_coverage_6g.py").read_text(
        encoding="utf-8"
    )
    assert "load_model=True" not in source
    assert "forward_train" not in source
    assert "AppWorld(" not in source
    assert "generate(" not in source
    assert "canonical_procedure_signature" in source
