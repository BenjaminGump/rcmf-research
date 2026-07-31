from __future__ import annotations

import re
from typing import Any

from rcmf.utils.serialization import to_jsonable


def extract_code_and_fix_content(text: str | None) -> tuple[str, str]:
    if not text:
        return "", ""
    full_code_regex = r"```python\n(.*?)```"
    partial_code_regex = r".*```python\n(.*)"
    output_code = ""
    match_end = 0
    for match in re.finditer(full_code_regex, text, flags=re.DOTALL):
        code = match.group(1).strip()
        return code, text[: match.end()]
    partial_match = re.match(partial_code_regex, text, flags=re.DOTALL)
    if partial_match:
        output_code = partial_match.group(1).strip()
        fixed = text if text.endswith("\n") else text + "\n"
        fixed += "```"
        return output_code, fixed
    return "", text


def summarize_step(step: dict[str, Any], index: int) -> str:
    observation = step.get("observation") or step.get("output") or step.get("result") or ""
    action = step.get("action") or step.get("code") or step.get("tool_call") or ""
    return f"Step {index}\nObservation: {observation}\nAction: {action}"


def render_appworld_experience(trajectory: dict[str, Any]) -> str:
    lines = [
        "[BENCHMARK]",
        "appworld",
        "[TASK]",
        str(trajectory.get("task_instruction", trajectory.get("task", ""))),
        "[INITIAL STATE]",
        str(trajectory.get("initial_state", "")),
        "[STEPS]",
    ]
    steps = trajectory.get("steps", [])
    if isinstance(steps, list):
        for idx, step in enumerate(steps, start=1):
            if isinstance(step, dict):
                lines.append(summarize_step(step, idx))
            else:
                lines.append(f"Step {idx}\n{step}")
    else:
        lines.append(str(steps))
    lines.extend(
        [
            "[OUTCOME]",
            f"Success: {trajectory.get('success', False)}, reward: {trajectory.get('reward')}",
        ]
    )
    lesson = trajectory.get("lesson") or trajectory.get("reflection") or ""
    if lesson:
        lines.extend(["[LESSON]", str(lesson)])
    return "\n".join(lines).strip() + "\n"


def probe_object_fields(obj: Any, names: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name in names:
        if hasattr(obj, name):
            try:
                found[name] = to_jsonable(getattr(obj, name))
            except Exception as exc:
                found[name] = f"<error reading {name}: {exc}>"
    return found
