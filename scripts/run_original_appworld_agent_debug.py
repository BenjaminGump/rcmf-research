from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from time import perf_counter as real_perf_counter
from typing import Any

import _bootstrap  # noqa: F401
import torch
from jinja2 import Template

from prompt import AGENT_QUERY_PROMPT_TEMPLATES, AGENT_SYSTEM_PROMPT_TEMPLATES
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.utils.serialization import atomic_write_json


def real_wall_time_s() -> float:
    try:
        return float(os.times().elapsed)
    except Exception:
        return real_perf_counter()


def split_original_system_prompt(system_prompt: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    last_start = 0
    for match in re.finditer(r"(USER|ASSISTANT|SYSTEM):\n", system_prompt):
        last_end = match.span()[0]
        if not messages:
            if last_end != 0 and system_prompt[:last_end].strip():
                raise ValueError(f"Start of the prompt has no assigned role: {system_prompt[:last_end]}")
        else:
            messages[-1]["content"] = system_prompt[last_start:last_end].rstrip()
        messages.append({"role": match.group(1).lower(), "content": ""})
        last_start = match.span()[1]
    messages[-1]["content"] = system_prompt[last_start:]
    return messages


def extract_code_and_fix_content(text: str | None) -> tuple[str, str]:
    if text is None:
        return "", ""
    partial_code_regex = r".*```python\n(.*)"
    full_code_regex = r"```python\n(.*?)```"
    output_code = ""
    match_end = 0
    original_text = text
    for re_match in re.finditer(full_code_regex, original_text, flags=re.DOTALL):
        code = re_match.group(1).strip()
        text = original_text[: re_match.end()]
        return code, text
    partial_match = re.match(partial_code_regex, original_text[match_end:], flags=re.DOTALL)
    if partial_match:
        output_code += partial_match.group(1).strip()
        if not text.endswith("\n"):
            text = text + "\n"
        text = text + "```"
    if len(output_code) == 0:
        return "", text
    return output_code, text


def evaluate_task(experiment_name: str, task_id: str, root: Path) -> bool:
    cmd = [
        sys.executable,
        "-m",
        "appworld.cli",
        "evaluate",
        experiment_name,
        "--task-id",
        task_id,
        "--root",
        str(root),
    ]
    try:
        subprocess.run(cmd, cwd=root, check=True)
    except subprocess.CalledProcessError:
        return False
    result_path = root / "experiments" / "outputs" / experiment_name / "evaluations" / f"on_only_{task_id}.json"
    if not result_path.exists():
        return False
    data = json.loads(result_path.read_text(encoding="utf-8"))
    return float(data.get("aggregate", {}).get("task_goal_completion", 0.0)) == 100.0


def message_stats(backend: HFQwenBackend, messages: list[dict[str, str]]) -> dict[str, Any]:
    rendered_prompt = backend._apply_chat_template(messages, add_generation_prompt=True)
    tokenized = backend.tokenizer(rendered_prompt, return_tensors="pt")
    return {
        "message_count": len(messages),
        "rendered_chars": len(rendered_prompt),
        "prompt_tokens": int(tokenized["input_ids"].shape[-1]),
        "roles": [message["role"] for message in messages],
        "rendered_prompt": rendered_prompt,
    }


def run_task(args: argparse.Namespace) -> dict[str, Any]:
    from appworld import AppWorld

    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    root = Path(args.root).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backend = HFQwenBackend(
        model_name=args.model_name,
        dtype=args.dtype,
        freeze_backbone=True,
        enable_thinking=args.enable_thinking,
        load_model=True,
    )

    debug: dict[str, Any] = {
        "task_id": args.task_id,
        "experiment_name": args.experiment_name,
        "model_name": args.model_name,
        "seed": args.seed,
        "max_context": args.max_context,
        "max_steps": args.max_steps,
        "max_new_tokens": args.max_new_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "steps": [],
        "success": False,
        "task_completed": False,
    }
    accumulated_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    run_start_time = real_wall_time_s()

    with AppWorld(task_id=args.task_id, experiment_name=args.experiment_name) as world:
        system_template = AGENT_SYSTEM_PROMPT_TEMPLATES["appworld"]
        system_messages = split_original_system_prompt(system_template)
        query_template = AGENT_QUERY_PROMPT_TEMPLATES["appworld"]
        query_prompt = Template(query_template.lstrip()).render({"world": world})
        conversation_history = [{"role": "user", "content": query_prompt}]

        debug["task_instruction"] = world.task.instruction
        debug["query_prompt"] = query_prompt
        debug["system_message_count"] = len(system_messages)
        debug["system_message_roles"] = [message["role"] for message in system_messages]
        atomic_write_json(output_path, debug)

        for step_index in range(1, args.max_steps + 1):
            messages = [dict(message) for message in system_messages]
            if len(conversation_history) > args.max_context:
                recent_history = [conversation_history[0], *conversation_history[-(args.max_context - 1) :]]
            else:
                recent_history = conversation_history
            messages.extend({"role": msg["role"], "content": msg["content"]} for msg in recent_history)

            stats = message_stats(backend, messages)
            turn_start = real_wall_time_s()
            output = backend.generate(
                messages=messages,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
            )
            generation_time_s = real_wall_time_s() - turn_start
            usage = output.usage
            accumulated_usage = {
                "prompt_tokens": accumulated_usage["prompt_tokens"] + usage.get("prompt_tokens", 0),
                "completion_tokens": accumulated_usage["completion_tokens"] + usage.get("completion_tokens", 0),
                "total_tokens": accumulated_usage["total_tokens"] + usage.get("total_tokens", 0),
            }
            code, fixed_text = extract_code_and_fix_content(output.text)
            observation = world.execute(code)
            observation_text = str(observation)
            conversation_history.append({"role": "assistant", "content": fixed_text})
            conversation_history.append({"role": "user", "content": f"Output:\n```\n{observation_text}\n```"})

            step_record = {
                "step": step_index,
                "messages": messages,
                "message_stats": stats,
                "raw_model_output": output.text,
                "fixed_model_output": fixed_text,
                "extracted_code": code,
                "observation": observation_text,
                "usage": usage,
                "generation_time_s": generation_time_s,
                "task_completed_after_step": world.task_completed(),
            }
            debug["steps"].append(step_record)
            debug["usage"] = accumulated_usage
            debug["elapsed_s"] = real_wall_time_s() - run_start_time
            debug["task_completed"] = world.task_completed()
            atomic_write_json(output_path, debug)

            print(
                f"step={step_index} completed={world.task_completed()} "
                f"prompt_tokens={stats['prompt_tokens']} generated={usage.get('completion_tokens', 0)} "
                f"time_s={generation_time_s:.1f}",
                flush=True,
            )
            if world.task_completed():
                break

        debug["task_completed"] = world.task_completed()
        debug["success"] = bool(world.task_completed() and evaluate_task(args.experiment_name, args.task_id, root))
        debug["elapsed_s"] = real_wall_time_s() - run_start_time
        atomic_write_json(output_path, debug)
    return debug


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the original AppWorldAgent loop with local Qwen and save exact LLM inputs/outputs."
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--model-name", default="Qwen/Qwen3-8B")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--max-context", type=int, default=40)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--enable-thinking", action="store_true")
    args = parser.parse_args()

    debug = run_task(args)
    print(
        {
            "task_id": debug["task_id"],
            "success": debug["success"],
            "task_completed": debug["task_completed"],
            "steps": len(debug["steps"]),
            "usage": debug.get("usage", {}),
            "elapsed_s": debug.get("elapsed_s"),
            "output": str(Path(args.output).resolve()),
        }
    )


if __name__ == "__main__":
    main()
