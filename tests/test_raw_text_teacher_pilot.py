from __future__ import annotations

import torch

from rcmf.schemas import DecisionExample, MemoryRecord
from scripts.run_raw_text_teacher_pilot import (
    apps_for_example,
    apps_for_record,
    legal_memory_indices,
    messages_with_teacher_memory,
    propose_candidates,
    select_pilot_examples,
    teacher_memory_section,
)


def _example(task_id: str, episode_id: str, step_id: int, state: str = "") -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id=episode_id,
        step_id=step_id,
        state_text=state or "[QUERY]\nprint spotify playlists",
        target_text="```python\nprint(1)\n```",
        target_type="code",
        candidate_memory_ids=None,
        metadata={"task_id": task_id, "lineage_id": f"lineage-{task_id}"},
    )


def _record(memory_id: str, task_id: str, episode_id: str, text: str = "") -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="appworld",
        episode_id=episode_id,
        task_id=task_id,
        raw_trajectory={},
        experience_text=text or "apis.spotify.show_playlist(name='x')",
        outcome=1.0,
        success=True,
        metadata={"lineage_id": f"lineage-{task_id}"},
    )


def test_raw_text_teacher_legal_indices_exclude_task_episode_and_lineage() -> None:
    example = _example("task-a", "episode-a", 1)
    records = [
        _record("same-task", "task-a", "episode-x"),
        _record("same-episode", "task-b", "episode-a"),
        _record("same-lineage", "task-a", "episode-y"),
        _record("legal", "task-c", "episode-c"),
    ]

    assert legal_memory_indices(records, example) == [3]


def test_teacher_memory_section_is_delimited_and_inserted_in_current_task_user() -> None:
    record = _record("m1", "task-b", "episode-b", text="raw trajectory text")
    messages = [
        {"role": "user", "content": "few shot"},
        {"role": "assistant", "content": "demo"},
        {"role": "user", "content": "current task"},
    ]

    section = teacher_memory_section(record)
    with_memory = messages_with_teacher_memory(messages, record, prompt_profile="minimal")

    assert "[TEACHER-ONLY RAW MEMORY START]" in section
    assert "[RAW MEMORY TEXT START]" in with_memory[1]["content"] or "[RAW MEMORY TEXT START]" in with_memory[2]["content"]
    assert "current task" in with_memory[-1]["content"]


def test_candidate_proposal_unions_cosine_same_app_and_low_similarity() -> None:
    example = _example(
        "task-a",
        "episode-a",
        1,
        state="[QUERY]\napis.spotify.search_song()",
    )
    records = [
        _record("m0", "task-b", "episode-b", "apis.spotify.search_song()"),
        _record("m1", "task-c", "episode-c", "apis.gmail.search_email()"),
        _record("m2", "task-d", "episode-d", "apis.spotify.create_playlist()"),
        _record("m3", "task-e", "episode-e", "apis.slack.search()"),
    ]
    state_rep = torch.tensor([1.0, 0.0])
    memory_reps = torch.tensor(
        [
            [1.0, 0.0],
            [0.9, 0.1],
            [0.0, 1.0],
            [-1.0, 0.0],
        ]
    )

    candidates = propose_candidates(
        example=example,
        state_representation=state_rep,
        memory_representations=memory_reps,
        records=records,
        record_apps=[apps_for_record(record) for record in records],
        example_apps=apps_for_example(example),
        seed=7,
    )

    sources = {source for values in candidates.values() for source in values}
    assert "cosine_top2" in sources
    assert "same_app" in sources
    assert "random_low_similarity" in sources
    assert 0 in candidates


def test_select_pilot_examples_is_deterministic_and_stratified() -> None:
    examples = []
    lengths = []
    for task in range(6):
        for step in range(1, 4):
            examples.append(_example(f"task-{task}", f"episode-{task}", step))
            lengths.append(100 * step + task)

    first = select_pilot_examples(examples, lengths, pilot_size=9)
    second = select_pilot_examples(examples, lengths, pilot_size=9)

    assert first == second
    assert len(first) == 9
    assert len({examples[index].metadata["task_id"] for index in first}) > 1
