from __future__ import annotations

import hashlib
import re
from typing import Any

PROMPT_RENDERER_VERSION = "appworld_messages_v2"

MINIMAL_SYSTEM_PROMPT = """You are an autonomous AppWorld assistant.

You solve tasks by writing one small Python code block per turn. The environment
will execute the code and return an observation. The variables apis and
requester are already defined; do not import appworld, do not instantiate
AppWorld, and do not write a bare import/from statement. Return exactly one
complete fenced python code block and no prose outside the fence. You may call
APIs either through apis.<app>.<api>(...) or through
requester.<method>(url, data=...). Use variables from earlier turns when useful.
Handle pagination by looping over page_index when an API uses pages. After
completing the task, call apis.supervisor.complete_task() or the equivalent
supervisor requester call, passing answer=<answer> only when the task asks for
information.
"""

TEACHER_SYSTEM_PROMPT = """You are an AppWorld teacher used for offline labels.
You may inspect relevant raw trajectories supplied in the prompt. Do not emit
credentials or private tokens. Return only the requested label or action.
"""


def _full_demo_query_fallback(
    task_instruction: str,
    supervisor: dict[str, str] | None = None,
) -> str:
    supervisor = supervisor or {}
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {supervisor.get('first_name', '')} {supervisor.get('last_name', '')}. "
        f"My personal email is {supervisor.get('email', '')} and phone number is "
        f"{supervisor.get('phone_number', '')}.\n"
        f"Task: {task_instruction}"
    )


def build_task_message(
    task_instruction: str,
    supervisor: dict[str, str] | None = None,
    profile: str = "minimal",
    world: Any | None = None,
) -> str:
    if profile == "full_demo":
        try:
            from jinja2 import Template
            from prompt import AGENT_QUERY_PROMPT_TEMPLATE_AW

            if world is not None:
                return Template(AGENT_QUERY_PROMPT_TEMPLATE_AW.lstrip()).render({"world": world})
        except Exception:
            pass
        return _full_demo_query_fallback(task_instruction, supervisor)

    if supervisor:
        identity = (
            f"My name is: {supervisor.get('first_name', '')} {supervisor.get('last_name', '')}. "
            f"My personal email is {supervisor.get('email', '')} and phone number is "
            f"{supervisor.get('phone_number', '')}."
        )
        return f"Now here is the task:\n{identity}\nTask: {task_instruction}"
    return f"Now here is the task:\nTask: {task_instruction}"


PROMPT_PROFILES = {
    "minimal": MINIMAL_SYSTEM_PROMPT,
    "teacher": TEACHER_SYSTEM_PROMPT,
}


def get_system_prompt(profile: str = "minimal") -> str:
    if profile == "full_demo":
        try:
            from prompt import AGENT_SYSTEM_PROMPT_TEMPLATE_AW

            return AGENT_SYSTEM_PROMPT_TEMPLATE_AW
        except Exception:
            return MINIMAL_SYSTEM_PROMPT
    return PROMPT_PROFILES.get(profile, MINIMAL_SYSTEM_PROMPT)


def split_role_prompt(prompt_text: str) -> list[dict[str, str]]:
    """Split the original AppWorld few-shot prompt into chat messages."""
    messages: list[dict[str, str]] = []
    last_start = 0
    for match in re.finditer(r"(USER|ASSISTANT|SYSTEM):\n", prompt_text):
        last_end = match.span()[0]
        if not messages:
            if last_end != 0 and prompt_text[:last_end].strip():
                raise ValueError(f"Start of the prompt has no assigned role: {prompt_text[:last_end]}")
        else:
            messages[-1]["content"] = prompt_text[last_start:last_end].rstrip()
        messages.append({"role": match.group(1).lower(), "content": ""})
        last_start = match.span()[1]
    if not messages:
        return [{"role": "system", "content": prompt_text}]
    messages[-1]["content"] = prompt_text[last_start:]
    return messages


def get_initial_messages(profile: str = "minimal") -> list[dict[str, str]]:
    system_prompt = get_system_prompt(profile)
    if profile == "full_demo":
        return split_role_prompt(system_prompt)
    return [{"role": "system", "content": system_prompt}]


def uses_chat_history_prompt(profile: str = "minimal") -> bool:
    return profile == "full_demo"


def observation_to_chat_content(observation: str) -> str:
    stripped = observation.strip()
    if stripped.startswith("Output:\n```"):
        return stripped
    return f"Output:\n```\n{stripped}\n```"


def _step_response_observation(step: Any) -> tuple[str, str]:
    if isinstance(step, dict):
        return str(step.get("response", "")), str(step.get("observation", ""))
    response = getattr(step, "response", "")
    observation = getattr(step, "observation", "")
    return str(response), str(observation)


def build_appworld_messages(
    task_message: str,
    trajectory_so_far: list[Any] | None = None,
    prompt_profile: str = "full_demo",
    target_type: str = "code",
    system_prompt: str | None = None,
    max_context_turns: int | None = None,
) -> list[dict[str, str]]:
    """Build the canonical structured AppWorld messages for action and state encoding."""
    trajectory_so_far = trajectory_so_far or []
    task_content = task_message
    if target_type == "answer":
        task_content = f"{task_content}\n\nRespond with the final answer only."

    if uses_chat_history_prompt(prompt_profile):
        messages = [dict(message) for message in get_initial_messages(prompt_profile)]
        conversation = [{"role": "user", "content": task_content}]
    else:
        messages = [
            {
                "role": "system",
                "content": system_prompt or get_system_prompt(prompt_profile),
            },
        ]
        conversation = [{"role": "user", "content": task_content}]
    for step in trajectory_so_far:
        response, observation = _step_response_observation(step)
        conversation.append({"role": "assistant", "content": response})
        conversation.append({"role": "user", "content": observation_to_chat_content(observation)})
    if max_context_turns is not None and max_context_turns > 0 and len(conversation) > max_context_turns:
        conversation = [conversation[0], *conversation[-(max_context_turns - 1) :]]
    messages.extend(conversation)
    return messages


def appworld_renderer_metadata(prompt_profile: str, add_generation_prompt: bool = True) -> dict[str, Any]:
    initial_messages = get_initial_messages(prompt_profile)
    payload = "\n".join(f"{message['role']}:{message['content']}" for message in initial_messages)
    return {
        "renderer_version": PROMPT_RENDERER_VERSION,
        "prompt_profile": prompt_profile,
        "add_generation_prompt": bool(add_generation_prompt),
        "initial_messages_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "initial_message_count": len(initial_messages),
        "chat_history_prompt": uses_chat_history_prompt(prompt_profile),
    }
