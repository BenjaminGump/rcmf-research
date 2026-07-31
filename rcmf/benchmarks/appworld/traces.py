from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from rcmf.schemas import DecisionExample, MemoryRecord


SYSTEM_RE = re.compile(r"^System Prompt:\s*(?P<content>.*)$", flags=re.DOTALL)
QUERY_RE = re.compile(r"^Query:\s*(?P<content>.*)$", flags=re.DOTALL)
STEP_RE = re.compile(
    r"^Step\s+(?P<index>\d+)\s+-\s+(?P<kind>Response|Observation):\s*(?P<content>.*)$",
    flags=re.DOTALL,
)
FINAL_RE = re.compile(r"^Final Answer:\s*(?P<content>.*)$", flags=re.DOTALL)
ENVIRONMENT_IO_RE = re.compile(
    r"^#+\s+(?:Execution|Environment Interaction)\s+(?P<number>\d+(?:\.\d+)?)\n"
    r"-+\n"
    r"```python\n(?P<input>.*?)\n```\n\n"
    r"```\n(?P<output>.*?)\n```",
    flags=re.MULTILINE | re.DOTALL,
)


@dataclass
class AppWorldTraceStep:
    index: int
    response: str = ""
    observation: str = ""


@dataclass
class AppWorldTrace:
    task_id: str
    query: str
    steps: list[AppWorldTraceStep]
    is_correct: bool
    system_prompt: str = ""
    final_answer: str = ""
    source_path: str = ""
    source_kind: str = "appworld_agent_trace"


def parse_appworld_trace_payload(payload: dict[str, Any], source_path: str = "") -> AppWorldTrace:
    trace_items = payload.get("trace")
    if not isinstance(trace_items, list):
        raise ValueError("Trace payload must contain a list field named 'trace'")

    query = ""
    system_prompt = str(payload.get("system_prompt", "") or "").strip()
    final_answer = ""
    steps_by_index: dict[int, AppWorldTraceStep] = {}
    for item in trace_items:
        text = str(item)
        system_match = SYSTEM_RE.match(text)
        if system_match:
            system_prompt = system_match.group("content").strip()
            continue
        query_match = QUERY_RE.match(text)
        if query_match:
            query = query_match.group("content").strip()
            continue
        final_match = FINAL_RE.match(text)
        if final_match:
            final_answer = final_match.group("content").strip()
            continue
        step_match = STEP_RE.match(text)
        if step_match:
            index = int(step_match.group("index"))
            kind = step_match.group("kind")
            content = step_match.group("content").strip()
            step = steps_by_index.setdefault(index, AppWorldTraceStep(index=index))
            if kind == "Response":
                step.response = content
            else:
                step.observation = content
            continue

    if not query:
        raise ValueError(f"Trace is missing Query entry: {source_path}")
    steps = [steps_by_index[index] for index in sorted(steps_by_index)]
    if not steps or not any(step.response for step in steps):
        raise ValueError(f"Trace has no response steps: {source_path}")

    return AppWorldTrace(
        task_id=str(payload.get("task_id", Path(source_path).stem)),
        query=query,
        steps=steps,
        is_correct=bool(payload.get("is_correct", False)),
        system_prompt=system_prompt,
        final_answer=final_answer,
        source_path=source_path,
        source_kind=str(payload.get("source_kind", "appworld_agent_trace")),
    )


def code_to_fenced_response(code: str) -> str:
    return f"```python\n{code.strip()}\n```"


def parse_environment_io_markdown(text: str, source_path: str = "") -> list[AppWorldTraceStep]:
    matches = list(ENVIRONMENT_IO_RE.finditer(text))
    if not matches:
        raise ValueError(f"No AppWorld environment interactions found: {source_path}")
    steps: list[AppWorldTraceStep] = []
    for sequential_index, match in enumerate(matches, start=1):
        steps.append(
            AppWorldTraceStep(
                index=sequential_index,
                response=code_to_fenced_response(match.group("input")),
                observation=match.group("output").strip(),
            )
        )
    return steps


def render_state_for_step(
    query: str,
    previous_steps: Iterable[AppWorldTraceStep],
    system_prompt: str = "",
) -> str:
    lines = []
    if system_prompt.strip():
        lines.extend(["[SYSTEM PROMPT]", system_prompt.strip()])
    lines.extend(["[QUERY]", query.strip()])
    previous_steps = list(previous_steps)
    if previous_steps:
        lines.append("[TRACE SO FAR]")
    for step in previous_steps:
        lines.extend(
            [
                f"Step {step.index} - Response:",
                step.response.strip(),
                f"Step {step.index} - Observation:",
                step.observation.strip(),
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_trace_experience(trace: AppWorldTrace) -> str:
    lines = ["[BENCHMARK]", "appworld", "[TASK_ID]", trace.task_id]
    if trace.system_prompt.strip():
        lines.extend(["[SYSTEM PROMPT]", trace.system_prompt.strip()])
    lines.extend(["[QUERY]", trace.query])
    lines.append("[TRAJECTORY]")
    for step in trace.steps:
        lines.extend(
            [
                f"Step {step.index} - Response:",
                step.response.strip(),
                f"Step {step.index} - Observation:",
                step.observation.strip(),
            ]
        )
    lines.extend(["[OUTCOME]", f"Correct: {trace.is_correct}", f"Final Answer: {trace.final_answer}"])
    return "\n".join(lines).strip() + "\n"


def memory_record_from_trace(trace: AppWorldTrace) -> MemoryRecord:
    episode_id = f"appworld:trace:{trace.task_id}"
    memory_id = str(uuid5(NAMESPACE_URL, episode_id))
    raw_steps = [
        {
            "step_id": step.index,
            "response": step.response,
            "observation": step.observation,
        }
        for step in trace.steps
    ]
    return MemoryRecord(
        memory_id=memory_id,
        benchmark="appworld",
        episode_id=episode_id,
        task_id=trace.task_id,
        raw_trajectory={
            "query": trace.query,
            "system_prompt": trace.system_prompt,
            "steps": raw_steps,
            "final_answer": trace.final_answer,
            "source_path": trace.source_path,
        },
        experience_text=render_trace_experience(trace),
        outcome=1.0 if trace.is_correct else 0.0,
        success=trace.is_correct,
        metadata={
            "source": trace.source_kind,
            "source_path": trace.source_path,
            "num_steps": len(trace.steps),
            "has_system_prompt": bool(trace.system_prompt.strip()),
        },
    )


def decision_examples_from_trace(trace: AppWorldTrace) -> list[DecisionExample]:
    examples: list[DecisionExample] = []
    previous_steps: list[AppWorldTraceStep] = []
    episode_id = f"appworld:trace:{trace.task_id}"
    for step in trace.steps:
        if not step.response.strip():
            previous_steps.append(step)
            continue
        examples.append(
            DecisionExample(
                benchmark="appworld",
                episode_id=episode_id,
                step_id=step.index,
                state_text=render_state_for_step(
                    trace.query,
                    previous_steps,
                    system_prompt=trace.system_prompt,
                ),
                target_text=step.response.strip(),
                target_type="code",
                candidate_memory_ids=None,
                metadata={
                    "task_id": trace.task_id,
                    "source": trace.source_kind,
                    "source_path": trace.source_path,
                    "is_correct_trace": trace.is_correct,
                    "has_observation": bool(step.observation.strip()),
                    "system_prompt": trace.system_prompt,
                    "system_prompt_in_state": bool(trace.system_prompt.strip()),
                },
            )
        )
        previous_steps.append(step)
    return examples
