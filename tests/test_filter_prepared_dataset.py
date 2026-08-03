from __future__ import annotations

import json

from rcmf.utils.serialization import read_jsonl, write_jsonl
from scripts.filter_prepared_dataset import filter_prepared_dataset


def test_filter_prepared_dataset_removes_task_and_records_audit(tmp_path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "filtered"
    source.mkdir()
    write_jsonl(
        source / "decision_examples.jsonl",
        [
            {
                "benchmark": "appworld",
                "episode_id": "appworld:trace:a_1",
                "step_id": 1,
                "state_text": "state a",
                "target_text": "target a",
                "target_type": "code",
                "candidate_memory_ids": None,
                "metadata": {"task_id": "a_1", "source_path": "/raw/a/environment_io.md"},
            },
            {
                "benchmark": "appworld",
                "episode_id": "appworld:trace:b_1",
                "step_id": 1,
                "state_text": "state b1",
                "target_text": "target b1",
                "target_type": "code",
                "candidate_memory_ids": None,
                "metadata": {"task_id": "b_1", "source_path": "/raw/b/environment_io.md"},
            },
            {
                "benchmark": "appworld",
                "episode_id": "appworld:trace:b_1",
                "step_id": 2,
                "state_text": "state b2",
                "target_text": "target b2",
                "target_type": "code",
                "candidate_memory_ids": None,
                "metadata": {"task_id": "b_1", "source_path": "/raw/b/environment_io.md"},
            },
        ],
    )
    write_jsonl(
        source / "memory_records.jsonl",
        [
            {
                "memory_id": "m-a",
                "benchmark": "appworld",
                "episode_id": "appworld:trace:a_1",
                "task_id": "a_1",
                "raw_trajectory": {"steps": [{"response": "x", "observation": "y"}]},
                "experience_text": "experience a",
                "outcome": 1.0,
                "success": True,
                "metadata": {"source_path": "/raw/a/environment_io.md"},
            },
            {
                "memory_id": "m-b",
                "benchmark": "appworld",
                "episode_id": "appworld:trace:b_1",
                "task_id": "b_1",
                "raw_trajectory": {"steps": []},
                "experience_text": "experience b",
                "outcome": 1.0,
                "success": True,
                "metadata": {"source_path": "/raw/b/environment_io.md"},
            },
        ],
    )
    (source / "summary.json").write_text(
        json.dumps({"source": "fixture", "records": 2, "examples": 3}),
        encoding="utf-8",
    )
    (source / "resolved_config.yaml").write_text("benchmark:\n  prompt_profile: full_demo\n", encoding="utf-8")

    summary = filter_prepared_dataset(
        source_dir=source,
        output_dir=output,
        excluded_episode_ids={"appworld:trace:b_1"},
        excluded_task_ids=set(),
        reason="fixture pathological context",
    )

    kept_examples = list(read_jsonl(output / "decision_examples.jsonl"))
    kept_records = list(read_jsonl(output / "memory_records.jsonl"))
    assert [row["episode_id"] for row in kept_examples] == ["appworld:trace:a_1"]
    assert [row["episode_id"] for row in kept_records] == ["appworld:trace:a_1"]
    assert summary["counts"]["removed_decision_examples"] == 2
    assert summary["counts"]["removed_memory_records"] == 1
    assert summary["removed_decision_example_line_ranges"] == [{"start": 2, "end": 3}]
    assert summary["removed_memory_record_line_ranges"] == [{"start": 2, "end": 2}]

    filter_summary = json.loads((output / "filter_summary.json").read_text(encoding="utf-8"))
    assert filter_summary["source_summary"]["source"] == "fixture"
    rewritten_summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert rewritten_summary["records"] == 1
    assert rewritten_summary["examples"] == 1
    assert rewritten_summary["source_filter"]["reason"] == "fixture pathological context"
    assert (output / "resolved_config.yaml").exists()
