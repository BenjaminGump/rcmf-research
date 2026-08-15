from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rcmf.training.all_task_interaction_6d import (
    build_fixed_learning_curve_manifest,
    classify_learning_curve,
    runtime_and_size_projection,
    select_all_task_query_manifest,
    validate_reusable_teacher_rows,
)
from scripts.run_action_intent_probe_6d import _intent_labels, _task_folds
from scripts.prepare_all_task_data_6d import _ensure_backend_tokenizer


def _example(task_id: str, step_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        episode_id=f"appworld:trace:{task_id}",
        step_id=step_id,
        target_text="apis.phone.search_contacts(query='Ada')",
        metadata={"task_id": task_id},
    )


def _small_manifest_fixture() -> tuple[list[SimpleNamespace], dict, dict, dict]:
    train_tasks = [f"train_{index}" for index in range(37)]
    validation_tasks = [f"valid_{index}" for index in range(9)]
    examples = [
        _example(task, step)
        for task in [*train_tasks, *validation_tasks]
        for step in (1, 2, 3, 4)
    ]
    source_rows = []
    for split, tasks in (("train", train_tasks[:12]), ("validation", validation_tasks[:4])):
        for task in tasks:
            indices = [
                index
                for index, example in enumerate(examples)
                if example.metadata["task_id"] == task
            ]
            for role, index in zip(("earlier", "later"), (indices[1], indices[2])):
                example = examples[index]
                source_rows.append(
                    {
                        "example_index": index,
                        "state_example_id": (
                            f"{example.episode_id}:step:{example.step_id}:line:{index + 1}"
                        ),
                        "step_id": example.step_id,
                        "step_count": 4,
                        "step_ratio": (example.step_id - 1) / 3,
                        "prompt_tokens": index + 100,
                        "prompt_length_bucket": "medium",
                        "selection_role": role,
                        "split": split,
                        "task_id": task,
                        "task_family": task,
                        "apps": ["phone"],
                    }
                )
    split = {
        "train_task_ids": train_tasks,
        "validation_task_ids": validation_tasks,
    }
    decoder = {"ordered_pair_ids": [], "state_count": 0}
    original = {"query_rows": source_rows}
    return examples, split, decoder, original


def test_all_task_manifest_preserves_original_rows_and_covers_every_task() -> None:
    examples, split, decoder, original = _small_manifest_fixture()
    prompt_counts = list(range(100, 100 + len(examples)))
    manifest = select_all_task_query_manifest(
        examples=examples,
        prompt_token_counts=prompt_counts,
        split_manifest=split,
        decoder_manifest=decoder,
        original_query_manifest=original,
        seed=20,
    )
    assert manifest["query_count"] == 92
    assert manifest["train_query_count"] == 74
    assert manifest["validation_query_count"] == 18
    assert manifest["task_shortages"] == []
    old_ids = {row["state_example_id"] for row in original["query_rows"]}
    new_ids = {row["state_example_id"] for row in manifest["query_rows"]}
    assert old_ids.issubset(new_ids)


def test_fixed_learning_curve_manifest_is_nested_and_uses_same_heldout_set() -> None:
    examples, split, decoder, original = _small_manifest_fixture()
    manifest = select_all_task_query_manifest(
        examples=examples,
        prompt_token_counts=list(range(100, 100 + len(examples))),
        split_manifest=split,
        decoder_manifest=decoder,
        original_query_manifest=original,
        seed=20,
    )
    curve = build_fixed_learning_curve_manifest(manifest, original, seed=21)
    levels = curve["levels"]
    assert [level["task_count"] for level in levels] == [12, 24, 37]
    assert [level["state_count"] for level in levels] == [24, 48, 74]
    assert set(levels[0]["task_ids"]).issubset(levels[1]["task_ids"])
    assert set(levels[1]["task_ids"]).issubset(levels[2]["task_ids"])
    assert curve["heldout_task_count"] == 9
    assert curve["heldout_state_count"] == 18


def _preflight(pair_id: str) -> dict:
    return {
        "pair_id": pair_id,
        "state_example_id": "s1",
        "example_index": 0,
        "task_id": "t1",
        "episode_id": "e1",
        "step_id": 1,
        "transition_id": "m1",
        "parent_memory_id": "p1",
        "parent_task_id": "pt1",
        "parent_episode_id": "pe1",
        "leakage_keys_state": ["task:t1"],
        "leakage_keys_transition": ["task:pt1"],
        "leakage_overlap": [],
        "state_prompt_tokens": 10,
        "transition_section_tokens": 5,
        "combined_prompt_tokens": 15,
        "target_tokens": 2,
        "total_tokens_with_target": 17,
        "context_limit": 100,
        "over_context": False,
        "truncated": False,
        "base_prompt_sha256": "base",
        "teacher_prompt_sha256": "teacher",
        "target_sha256": "target",
        "target_token_sha256": "tokens",
        "transition_content_sha256": "memory",
        "teacher_section_sha256": "section",
        "renderer_version": "renderer",
        "transition_renderer_version": "transition-renderer",
        "model_name": "Qwen/Qwen3-8B",
    }


def test_teacher_reuse_requires_exact_compatible_rows() -> None:
    row = _preflight("s1::transition::m1")
    teacher = {
        **row,
        "scoring_definition": (
            "frozen_qwen_full_demo_plus_single_raw_decision_transition_target_nll_v1"
        ),
        "score_status": "scored",
    }
    validation = validate_reusable_teacher_rows(
        expanded_preflight_rows=[row],
        source_preflight_rows=[row],
        source_teacher_rows=[teacher],
    )
    assert validation["passed"]
    changed = {**row, "target_sha256": "different"}
    invalid = validate_reusable_teacher_rows(
        expanded_preflight_rows=[changed],
        source_preflight_rows=[row],
        source_teacher_rows=[teacher],
    )
    assert not invalid["passed"]


def test_runtime_projection_uses_new_rows_and_review_threshold() -> None:
    projection = runtime_and_size_projection(
        total_scoreable_pairs=13000,
        reused_scoreable_pairs=4500,
        new_query_count=60,
        additional_intent_state_count=546,
        observed_teacher_seconds_per_pair=1.3,
        observed_cross_encoder_seconds_per_pair=1.6,
        observed_multiview_seconds_per_state=2.0,
        observed_model_runtime_seconds=3000,
        prior_artifact_bytes=20_000_000_000,
        prior_query_count=32,
        prior_scoreable_pairs=4579,
        review_threshold_h100_hours=12.0,
    )
    assert projection["inputs"]["new_scoreable_pairs"] == 8500
    assert projection["inputs"]["additional_intent_state_count"] == 546
    assert projection["phase_expected_seconds"][
        "additional_action_intent_multiview_cache"
    ] == 1092.0
    assert projection["artifact_size_projection"]["action_intent_artifact_bytes"] > 0
    assert projection["expected_h100_hours"] < 12
    assert not projection["expected_runtime_review_required"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((0.2, 0.25, 0.31), "materially_increasing"),
        ((0.2, 0.201, 0.202), "flat_saturated"),
        ((0.3, 0.2, 0.34), "unstable"),
        ((0.4, 0.35, 0.3), "degrading"),
    ],
)
def test_learning_curve_classification(values: tuple[float, ...], expected: str) -> None:
    assert classify_learning_curve(values) == expected


def test_teacher_runner_is_scoring_only_and_uses_fsync_journal() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_all_task_transition_teacher_6d.py").read_text(
        encoding="utf-8"
    )
    assert "append_jsonl_fsync" in source
    assert "_score_mean_target_nll" in source
    assert "forward_train(" not in source
    assert ".generate(" not in source
    assert "AdditiveTokenMemoryInjector" not in source
    assert "selector" not in source.lower() or '"selector_training": False' in source


def test_model_runner_uses_uniform_control_result_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_all_task_models_6d.py").read_text(
        encoding="utf-8"
    )
    assert 'setdefault(f"{axis}_only", {"cells": {}})' in source
    assert '"controls": {' in source
    assert '"correct": {' in source


def test_action_intent_labels_are_deterministic() -> None:
    labels = _intent_labels(
        "```python\nprint(apis.spotify.search_tracks(query='x'))\n```\n"
    )
    assert labels == {
        "target_app": "spotify",
        "target_api": "spotify.search_tracks",
        "action_type": "api_read_or_login",
        "completion_action": "false",
    }
    complete = _intent_labels(
        "```python\napis.supervisor.complete_task(answer='done')\n```\n"
    )
    assert complete["completion_action"] == "true"


def test_action_intent_task_folds_are_grouped_and_deterministic() -> None:
    tasks = [f"task_{index}" for index in range(13)]
    first = _task_folds(tasks, folds=5, seed=20020)
    second = _task_folds(list(reversed(tasks)), folds=5, seed=20020)
    assert first == second
    assert set().union(*first) == set(tasks)
    assert sum(len(fold) for fold in first) == len(tasks)


def test_action_intent_probe_preserves_hard_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_action_intent_probe_6d.py").read_text(
        encoding="utf-8"
    )
    assert "target_not_encoded" in source
    assert "parameter.requires_grad_(False)" in source
    assert "AdditiveTokenMemoryInjector" not in source
    assert ".generate(" not in source
    assert "forward_train(" not in source


def test_postrun_validator_checks_full_pair_and_cache_counts() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/validate_all_task_interaction_6d.py").read_text(
        encoding="utf-8"
    )
    for expected in ("13320", "13128", "192", "4640", "8549", "131"):
        assert expected in source
    assert "cross_encoder_tensor_hash" in source
    assert "scientific_parameter_changed" in source
    assert "validation state leaked into A/C" in source
    assert '"failed_attempt_count"' in source


def test_preflight_entrypoint_supports_append_only_resume_provenance() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/prepare_all_task_interaction_6d.py").read_text(
        encoding="utf-8"
    )
    assert 'parser.add_argument("--parent-attempt-id", default=None)' in source
    assert 'parser.add_argument("--resume-checkpoint", default=None)' in source
    assert "parent_attempt_id=args.parent_attempt_id" in source
    assert "resume_checkpoint=args.resume_checkpoint" in source


def test_data_preparation_loads_canonical_tokenizer_without_qwen_model() -> None:
    sentinel = object()
    calls = []
    backend = SimpleNamespace(
        tokenizer=None,
        model=None,
        model_name="Qwen/Qwen3-8B",
    )

    def loader(model_name: str, **kwargs):
        calls.append((model_name, kwargs))
        return sentinel

    result = _ensure_backend_tokenizer(backend, tokenizer_loader=loader)
    assert result is sentinel
    assert backend.tokenizer is sentinel
    assert backend.model is None
    assert calls == [("Qwen/Qwen3-8B", {"trust_remote_code": True})]
