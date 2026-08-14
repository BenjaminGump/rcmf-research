from __future__ import annotations

from pathlib import Path

import pytest

from rcmf.training.data_sufficiency_6c import (
    build_nested_task_learning_curve_manifest,
    expanded_query_cache_projection,
    summarize_learning_curves,
)


def _rows() -> list[dict[str, str]]:
    return [
        {
            "pair_id": f"{task}:{parent}",
            "state_task_id": task,
            "state_example_id": f"state-{task}",
            "transition_parent_id": parent,
        }
        for task in ("t1", "t2", "t3", "t4")
        for parent in ("p1", "p2", "p3")
    ]


def test_learning_curve_manifest_is_nested_and_keeps_parent_coverage() -> None:
    manifest = build_nested_task_learning_curve_manifest(
        _rows(), task_counts=(1, 2, 4), folds=3, seed=19
    )
    assert manifest["fold_count"] == 3
    for fold in manifest["folds"]:
        levels = fold["levels"]
        assert set(levels[0]["task_ids"]).issubset(levels[1]["task_ids"])
        assert set(levels[1]["task_ids"]).issubset(levels[2]["task_ids"])
        assert all(level["all_parent_coverage"] for level in levels)


def test_learning_curve_summary_distinguishes_rising_and_saturated() -> None:
    rows = []
    for fold in range(3):
        for count, ndcg, residual in ((4, 0.2, 0.1), (8, 0.3, 0.2), (12, 0.36, 0.27)):
            rows.append(
                {
                    "model_kind": "rising",
                    "fold": fold,
                    "task_count": count,
                    "ndcg@4": ndcg,
                    "interaction_residual_spearman": residual,
                    "pooled_raw_spearman": residual,
                    "mean_per_state_spearman": residual,
                }
            )
        for count, ndcg in ((4, 0.2), (8, 0.31), (12, 0.311)):
            rows.append(
                {
                    "model_kind": "flat",
                    "fold": fold,
                    "task_count": count,
                    "ndcg@4": ndcg,
                    "interaction_residual_spearman": 0.2,
                    "pooled_raw_spearman": 0.2,
                    "mean_per_state_spearman": 0.2,
                }
            )
    summary = summarize_learning_curves(rows)
    assert summary["models"]["rising"]["still_materially_rising_at_maximum"]
    assert summary["models"]["flat"]["saturated"]


def test_expanded_query_projection_preserves_pair_accounting() -> None:
    projection = expanded_query_cache_projection(
        current_queries=32,
        legal_pairs=4640,
        scoreable_pairs=4579,
        over_context_pairs=61,
        query_counts=(64, 96),
        seconds_per_scoreable_pair=1.788982,
    )
    assert projection["rows"][0]["projected_legal_pairs"] == 9280
    assert projection["rows"][0]["projected_scoreable_pairs"] == 9158
    assert projection["rows"][0]["projected_over_context_pairs"] == 122
    with pytest.raises(ValueError, match="inconsistent"):
        expanded_query_cache_projection(
            current_queries=32,
            legal_pairs=10,
            scoreable_pairs=9,
            over_context_pairs=2,
            query_counts=(64,),
            seconds_per_scoreable_pair=1.0,
        )


def test_data_sufficiency_runner_has_no_qwen_or_behavioral_path() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_data_sufficiency_6c.py").read_text(
        encoding="utf-8"
    )
    assert "build_backend" not in source
    assert "forward_train(" not in source
    assert "generate(" not in source
    assert "AdditiveTokenMemoryInjector" not in source
    assert "transition_teacher_section" not in source
