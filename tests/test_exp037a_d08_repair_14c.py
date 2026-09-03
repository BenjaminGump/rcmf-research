from __future__ import annotations

import inspect
from pathlib import Path

import torch

from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _joint_source_contract_preflight,
)
from rcmf.benchmarks.appworld.transition_metadata_14c import (
    REQUIRED_TOKEN_FIELDS,
    SCHEMA_VERSION,
    enrich_transition_token_metadata,
    schema_definition,
)
from rcmf.pipeline.stage_graph import SHARED_STAGES, build_exp037a_stage_graph
from rcmf.utils.serialization import sha256_text, write_jsonl
from scripts.prepare_rcmf_joint_full_bank_9a import _section_contract
from scripts.run_rcmf_joint_full_bank_9a import _train


class CharacterTokenizer:
    name_or_path = "locked-character-tokenizer"
    vocab_size = 256
    model_max_length = 40960
    chat_template = ""
    special_tokens_map: dict[str, str] = {}
    init_kwargs = {"_commit_hash": "tokenizer-commit"}

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, text: str, **kwargs: object) -> dict[str, torch.Tensor]:
        self.calls.append({"text": text, **kwargs})
        values = torch.tensor([[ord(value) for value in text]], dtype=torch.long)
        offsets = torch.tensor(
            [[[index, index + 1] for index in range(len(text))]],
            dtype=torch.long,
        )
        return {
            "input_ids": values,
            "attention_mask": torch.ones_like(values),
            "offset_mapping": offsets,
        }

    def decode(self, token_ids: list[int], **_: object) -> str:
        return "".join(chr(value) for value in token_ids)


def _transition() -> dict[str, object]:
    sections = {
        "source_task_goal": "goal",
        "canonical_pre_action_state": "state",
        "complete_action": "action()",
        "complete_post_action_observation": "observation",
    }
    return {
        "transition_id": "transition-1",
        "parent_memory_id": "parent-1",
        "parent_task_id": "task-1",
        "parent_episode_ids": ["episode-1"],
        "parent_replay_ids": ["replay-1"],
        "parent_lineage_ids": ["lineage-1"],
        "parent_trajectory_sha256": "a" * 64,
        "transition_content_sha256": "b" * 64,
        **sections,
        **{
            f"{name}_sha256": sha256_text(str(value))
            for name, value in sections.items()
        },
    }


def test_schema_derives_all_counts_from_one_complete_render() -> None:
    tokenizer = CharacterTokenizer()
    source = _transition()
    rows, report, mismatches = enrich_transition_token_metadata([source], tokenizer)

    assert report["passed"]
    assert not mismatches
    assert report["schema"]["format"] == SCHEMA_VERSION
    assert all(field in rows[0] for field in REQUIRED_TOKEN_FIELDS)
    assert rows[0]["source_task_goal_tokens"] == len("goal")
    assert rows[0]["canonical_pre_action_state_tokens"] == len("state")
    assert rows[0]["complete_action_tokens"] == len("action()")
    assert rows[0]["complete_post_action_observation_tokens"] == len("observation")
    assert len(tokenizer.calls) == 1
    assert tokenizer.calls[0]["add_special_tokens"] is False
    assert tokenizer.calls[0]["truncation"] is False
    assert tokenizer.calls[0]["return_offsets_mapping"] is True
    assert tokenizer.calls[0]["return_tensors"] == "pt"
    assert all(rows[0][key] == value for key, value in source.items())


def test_repaired_row_satisfies_unchanged_historical_consumer() -> None:
    row = enrich_transition_token_metadata(
        [_transition()], CharacterTokenizer()
    )[0][0]
    cache_row = {"token_count": row["teacher_section_tokens"], "provenance": "fresh", "truncated": False}
    result = _section_contract(
        row,
        cache_row,
        torch.arange(16, dtype=torch.float32).reshape(8, 2),
        lineage="lineage",
    )
    assert [section["source_token_count"] for section in result["sections"]] == [
        row["source_task_goal_tokens"],
        row["canonical_pre_action_state_tokens"],
        row["complete_action_tokens"],
        row["complete_post_action_observation_tokens"],
    ]


def test_real_cache_preflight_calls_consumer_and_validates_span_counts(
    tmp_path: Path,
) -> None:
    tokenizer = CharacterTokenizer()
    source = _transition()
    rows, _, _ = enrich_transition_token_metadata([source], tokenizer)
    row = rows[0]

    shared = tmp_path / "preflight/shared"
    write_jsonl(shared / "transitions.jsonl", [row])
    cache_root = tmp_path / "shared/representation_cache/multiview"
    cache_root.mkdir(parents=True)
    from rcmf.benchmarks.appworld.transition_metadata_14c import (
        derive_transition_token_metadata,
        tokenizer_identity,
    )

    _, detail = derive_transition_token_metadata(
        source,
        CharacterTokenizer(),
        tokenizer_info=tokenizer_identity(CharacterTokenizer()),
    )
    cache_payload = {
        "transition_id": row["transition_id"],
        "teacher_section_sha256": row["teacher_section_sha256"],
        "token_count": row["teacher_section_tokens"],
        "span_rows": detail["span_rows"],
        "truncated": False,
    }
    (cache_root / "transition_rows").mkdir()
    torch.save(cache_payload, cache_root / "transition_rows/transition-1.pt")
    torch.save(
        {
            "ordered_ids": ["transition-1"],
            "rows": [
                {
                    "token_count": row["teacher_section_tokens"],
                    "provenance": "fresh",
                    "truncated": False,
                }
            ],
            "representations": {
                "final_layer": torch.arange(20, dtype=torch.float32).reshape(1, 10, 2)
            },
        },
        cache_root / "transition_multiview.pt",
    )
    config = {
        "pipeline": {
            "expected": {
                "train_transitions": 1,
                "structural_lineage_sha256": "lineage",
            }
        }
    }
    result = _joint_source_contract_preflight(config, tmp_path)
    assert result["passed"]
    assert result["checks"]["consumer_row_count_499"]


def test_early_contract_stage_precedes_cv_and_defaults_remain_scientific() -> None:
    assert SHARED_STAGES.index("S05B_joint_source_contract_preflight") == (
        SHARED_STAGES.index("S05_transition_representations") + 1
    )
    assert SHARED_STAGES.index("S05B_joint_source_contract_preflight") < (
        SHARED_STAGES.index("S06_cv_folds_and_sampling")
    )
    stages = build_exp037a_stage_graph()
    contract = next(
        row for row in stages if row.stage_id == "S05B_joint_source_contract_preflight"
    )
    assert not contract.scientific
    assert not contract.uses_gpu
    source = inspect.getsource(_train)
    assert 'os.environ.get("RCMF_DIAGNOSTIC_MAX_TRAINING_UNITS", "0")' in source
    assert "RCMF_DIAGNOSTIC_MAX_TRAINING_UNITS" not in Path(
        "configs/pipeline/rcmf_appworld_repro_14b.yaml"
    ).read_text(encoding="utf-8")


def test_schema_records_unchanged_consumer_contract() -> None:
    schema = schema_definition()
    assert schema["historical_consumer_semantics_changed"] is False
    assert schema["consumer"].endswith("._section_contract")
