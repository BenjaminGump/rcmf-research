from __future__ import annotations

from rcmf.benchmarks.appworld.prompt import build_task_message, get_initial_messages, split_role_prompt
from rcmf.benchmarks.appworld.traces import (
    decision_examples_from_trace,
    memory_record_from_trace,
    parse_environment_io_markdown,
    parse_appworld_trace_payload,
)
from scripts.prepare_appworld_ground_truth_traces import request_call_to_code, split_top_level_statements


def test_appworld_trace_to_per_step_examples() -> None:
    payload = {
        "task_id": "abc_1",
        "is_correct": True,
        "system_prompt": "Use code.",
        "trace": [
            "System Prompt: Use code.",
            "Query: Solve the task",
            "Step 1 - Response: Think\n```python\nprint('one')\n```",
            "Step 1 - Observation: one",
            "Step 2 - Response: Finish\n```python\napis.supervisor.complete_task(answer=1)\n```",
            "Step 2 - Observation: done",
            "Final Answer: task completed.",
        ],
    }

    trace = parse_appworld_trace_payload(payload, source_path="trace.json")
    examples = decision_examples_from_trace(trace)
    record = memory_record_from_trace(trace)

    assert len(examples) == 2
    assert examples[0].state_text == "[SYSTEM PROMPT]\nUse code.\n[QUERY]\nSolve the task\n"
    assert examples[0].metadata["system_prompt"] == "Use code."
    assert "print('one')" in examples[0].target_text
    assert "Step 1 - Response" in examples[1].state_text
    assert "Step 1 - Observation" in examples[1].state_text
    assert "complete_task" not in examples[1].state_text
    assert "complete_task" in examples[1].target_text
    assert examples[0].target_type == "code"
    assert record.success is True
    assert "Step 2 - Observation" in record.experience_text


def test_request_call_to_code_replays_raw_request() -> None:
    code = request_call_to_code(
        {
            "method": "post",
            "url": "/spotify/auth",
            "data": {"username": "user", "password": "pw"},
        }
    )

    assert code.startswith("print(requester.post(")
    assert "'/spotify/auth'" in code
    assert "'username': 'user'" in code


def test_split_top_level_statements_keeps_compound_blocks() -> None:
    chunks = split_top_level_statements(
        """
# get data
items = []
for value in range(3):
    items.append(value)

# finish
apis.supervisor.complete_task()
""".strip()
    )

    assert len(chunks) == 3
    assert chunks[0].startswith("# get data")
    assert "for value in range(3):" in chunks[1]
    assert chunks[2].startswith("# finish")


def test_parse_official_environment_io_markdown() -> None:
    text = """
### Environment Interaction 1
----------------------------------------------------------------------------
```python
print(requester.get('/spotify/profile'))
```

```
{"id": 1}
```


### Environment Interaction 2
----------------------------------------------------------------------------
```python
apis.supervisor.complete_task()
```

```
done
```
""".strip()

    steps = parse_environment_io_markdown(text, source_path="environment_io.md")

    assert len(steps) == 2
    assert steps[0].index == 1
    assert steps[0].response.startswith("```python")
    assert "requester.get" in steps[0].response
    assert steps[0].observation == '{"id": 1}'
    assert "complete_task" in steps[1].response


def test_split_role_prompt_matches_original_chat_transcript_shape() -> None:
    messages = split_role_prompt("USER:\nhello\nASSISTANT:\nhi\nSYSTEM:\nrules")

    assert messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "system", "content": "rules"},
    ]


def test_split_role_prompt_preserves_final_message_trailing_newline() -> None:
    messages = split_role_prompt("USER:\nhello\nASSISTANT:\nhi\n")

    assert messages[-1]["content"] == "hi\n"


def test_full_demo_prompt_profile_uses_original_appworld_templates() -> None:
    initial_messages = get_initial_messages("full_demo")
    query = build_task_message(
        "Finish the example task.",
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone_number": "555-0100",
        },
        profile="full_demo",
    )

    assert len(initial_messages) > 2
    assert initial_messages[0]["role"] == "user"
    assert initial_messages[0]["content"].startswith("I am your supervisor")
    assert query.startswith("Now here is another task in a different environment")
    assert "My name is: Ada Lovelace." in query
    assert "Task: Finish the example task." in query
