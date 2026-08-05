from __future__ import annotations

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import _target_suffix
from rcmf.utils.serialization import sha256_text
from scripts.run_raw_text_teacher_full_cache import (
    FULL_CACHE_VERSION,
    convert_cached_row,
    overlap_features,
    pair_key,
    validate_full_cache,
    validate_source_row,
)
from scripts.run_raw_text_teacher_pilot import RAW_TEXT_TEACHER_CACHE_VERSION, TEACHER_MEMORY_SECTION_VERSION, _example_id


def _example() -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id="appworld:trace:task-a_1",
        step_id=1,
        state_text="[QUERY]\nUse apis.spotify.search_song to find a track.",
        target_text="```python\napis.spotify.search_song(query='x')\n```",
        target_type="code",
        candidate_memory_ids=None,
        metadata={"task_id": "task-a", "lineage_id": "lineage-a"},
    )


def _record() -> MemoryRecord:
    return MemoryRecord(
        memory_id="memory-b",
        benchmark="appworld",
        episode_id="appworld:trace:task-b_1",
        task_id="task-b",
        raw_trajectory={},
        experience_text="Previous solution used apis.spotify.search_song(query='y').",
        outcome=1.0,
        success=True,
        metadata={"lineage_id": "lineage-b"},
    )


def _source_row(example: DecisionExample, record: MemoryRecord, *, over_context: bool = False) -> dict:
    target_token_hash = sha256_text("1,2,3")
    return {
        "format": RAW_TEXT_TEACHER_CACHE_VERSION,
        "example_index": 0,
        "candidate_memory_index": 0,
        "state_example_id": _example_id(0, example),
        "candidate_memory_id": record.memory_id,
        "model_name": "Qwen/Qwen3-8B",
        "checkpoint_identity": "frozen_hf_pretrained:Qwen/Qwen3-8B",
        "renderer_version": "appworld_messages_v2",
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "target_sha256": sha256_text(_target_suffix(example)),
        "target_token_sha256": target_token_hash,
        "memory_text_sha256": sha256_text(record.experience_text),
        "over_context": over_context,
        "L0": 2.0,
        "Lj_text": None if over_context else 1.5,
        "text_utility": None if over_context else 0.5,
        "state_prompt_tokens": 10,
        "raw_memory_tokens": 20,
        "combined_prompt_tokens": 30 if not over_context else 50,
        "target_tokens": 5,
        "total_tokens_with_target": 35 if not over_context else 55,
        "context_limit": 40,
    }


def test_full_cache_validates_and_converts_cached_rows() -> None:
    example = _example()
    record = _record()
    row = _source_row(example, record)
    ok, reason = validate_source_row(
        row,
        examples=[example],
        records=[record],
        backend_model_name="Qwen/Qwen3-8B",
        renderer_version="appworld_messages_v2",
        expected_checkpoint_identity="frozen_hf_pretrained:Qwen/Qwen3-8B",
        target_token_hashes={0: row["target_token_sha256"]},
    )

    assert ok, reason
    converted = convert_cached_row(
        row,
        examples=[example],
        records=[record],
        cache_generation_commit_sha="abc123",
        source_path="cache.jsonl",
    )

    assert converted["format"] == FULL_CACHE_VERSION
    assert converted["pair_key"] == pair_key(0, 0)
    assert converted["score_status"] == "scored"
    assert converted["valid_for_loss"] is True
    assert converted["truncated"] is False
    assert converted["leakage_overlap"] == []
    assert converted["same_app"] is True
    assert converted["shared_api_count"] == 1


def test_full_cache_over_context_rows_have_null_utility() -> None:
    example = _example()
    record = _record()
    row = _source_row(example, record, over_context=True)

    converted = convert_cached_row(
        row,
        examples=[example],
        records=[record],
        cache_generation_commit_sha="abc123",
        source_path="cache.jsonl",
    )

    assert converted["score_status"] == "over_context"
    assert converted["valid_for_loss"] is False
    assert converted["Lj_text"] is None
    assert converted["text_utility"] is None
    assert converted["utility_category"] is None


def test_validate_full_cache_accepts_exact_legal_scoreable_row() -> None:
    example = _example()
    record = _record()
    converted = convert_cached_row(
        _source_row(example, record),
        examples=[example],
        records=[record],
        cache_generation_commit_sha="abc123",
        source_path="cache.jsonl",
    )

    validation = validate_full_cache(
        [converted],
        examples=[example],
        records=[record],
        expected_counts={
            "state_count": 1,
            "memory_count": 1,
            "legal_pair_count": 1,
            "scoreable_pair_count": 1,
            "over_context_pair_count": 0,
        },
        context_limit=40,
    )

    assert validation["passed"]


def test_overlap_features_are_deterministic() -> None:
    features = overlap_features(_example(), _record())

    assert features["same_app"] is True
    assert features["shared_api_count"] == 1
    assert features["shared_code_token_count"] > 0
    assert features["code_token_jaccard"] > 0
