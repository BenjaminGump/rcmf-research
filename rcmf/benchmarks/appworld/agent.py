from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import torch

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import (
    build_task_message,
    get_initial_messages,
    get_system_prompt,
    uses_chat_history_prompt,
)
from rcmf.benchmarks.appworld.traces import AppWorldTraceStep, render_state_for_step
from rcmf.config import RCMFConfig
from rcmf.injection.base import MemoryInjector
from rcmf.memory.state import MemoryState
from rcmf.model.backends.base import ModelBackend
from rcmf.schemas import BenchmarkResult
from rcmf.utils.logging import redact
from rcmf.utils.serialization import atomic_write_json


class RCMFAppWorldAgent:
    def __init__(
        self,
        config: RCMFConfig,
        backend: ModelBackend,
        memory_state: MemoryState | None = None,
        state_encoder: Any | None = None,
        injector: MemoryInjector | None = None,
        experiment_name: str | None = None,
        root: str | Path = ".",
        max_new_tokens: int = 512,
        temperature: float = 0.3,
        top_p: float = 0.95,
        memory_scale: float = 1.0,
    ) -> None:
        self.config = config
        self.backend = backend
        self.memory_state = memory_state
        self.state_encoder = state_encoder
        self.injector = injector
        self.experiment_name = experiment_name or config.experiment.name
        self.root = Path(root)
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.memory_scale = float(memory_scale)

    def _accumulate_usage(self, left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        return {
            "prompt_tokens": left.get("prompt_tokens", 0) + right.get("prompt_tokens", 0),
            "completion_tokens": left.get("completion_tokens", 0) + right.get("completion_tokens", 0),
            "total_tokens": left.get("total_tokens", 0) + right.get("total_tokens", 0),
        }

    def _recent_history(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        max_context = self.config.benchmark.max_context_turns
        if max_context is None or max_context <= 0 or len(history) <= max_context:
            return history
        return [history[0], *history[-(max_context - 1) :]]

    def _messages_to_state_text(self, messages: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for message in messages:
            lines.extend([f"[{message['role'].upper()}]", message["content"].strip()])
        return "\n".join(lines).strip() + "\n"

    def _memory_z_for_turn(self, state_text: str) -> torch.Tensor | None:
        if self.memory_state is None or self.state_encoder is None:
            return None
        device = next(self.state_encoder.parameters()).device
        with torch.no_grad():
            if self.config.encoder.type == "qwen_hidden":
                if not hasattr(self.backend, "encode_texts"):
                    raise TypeError("Backend must implement encode_texts for qwen_hidden evaluation")
                representation = self.backend.encode_texts([state_text], batch_size=1).to(device)
                b = self.state_encoder(representation, None)
            else:
                tokenizer = getattr(self.backend, "tokenizer", None)
                if tokenizer is None:
                    return None
                tokenized = tokenizer(state_text, return_tensors="pt")
                input_ids = tokenized["input_ids"]
                attention_mask = tokenized.get("attention_mask", torch.ones_like(input_ids))
                b = self.state_encoder(input_ids.to(device), attention_mask.to(device))
            z = self.memory_state.read(
                b.cpu(),
                normalization=self.config.memory.normalization,
                eps=self.config.memory.eps,
            )
        if self.memory_scale != 1.0:
            z = z * self.memory_scale
        return z

    def _evaluate_with_appworld(self, task_id: str) -> bool:
        appworld_cli = Path(sys.executable).parent / "appworld"
        cmd = [str(appworld_cli), "evaluate", self.experiment_name, "--task-id", task_id]
        if str(self.root) != ".":
            cmd.extend(["--root", str(self.root)])
        try:
            subprocess.run(cmd, cwd=self.root, check=True)
        except subprocess.CalledProcessError:
            return False
        result_path = (
            self.root
            / "experiments"
            / "outputs"
            / self.experiment_name
            / "evaluations"
            / f"on_only_{task_id}.json"
        )
        if not result_path.exists():
            return False
        import json

        data = json.loads(result_path.read_text(encoding="utf-8"))
        acc = data.get("aggregate", {}).get("task_goal_completion", 0.0)
        return float(acc) == 100.0

    def run_task(self, task_id: str) -> BenchmarkResult:
        from appworld import AppWorld

        start = time.perf_counter()
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        trace: list[dict[str, str]] = []
        steps = 0
        ttft_ms = 0.0
        with AppWorld(task_id=task_id, experiment_name=self.experiment_name) as world:
            supervisor = {
                "first_name": getattr(world.task.supervisor, "first_name", ""),
                "last_name": getattr(world.task.supervisor, "last_name", ""),
                "email": getattr(world.task.supervisor, "email", ""),
                "phone_number": getattr(world.task.supervisor, "phone_number", ""),
            }
            prompt_profile = self.config.benchmark.prompt_profile
            system_prompt = get_system_prompt(prompt_profile)
            initial_messages = get_initial_messages(prompt_profile)
            use_chat_history_prompt = uses_chat_history_prompt(prompt_profile)
            task_message = build_task_message(
                world.task.instruction,
                supervisor,
                profile=prompt_profile,
                world=world,
            )
            conversation_history = [{"role": "user", "content": task_message}]
            trace_steps: list[AppWorldTraceStep] = []
            trace.append({"role": "user", "content": task_message})
            last_invalid_code = ""
            repeated_invalid_code = 0
            for step in range(self.config.benchmark.max_steps):
                steps = step + 1
                if use_chat_history_prompt:
                    messages = [dict(message) for message in initial_messages]
                    messages.extend(dict(message) for message in self._recent_history(conversation_history))
                    state_text = self._messages_to_state_text(messages)
                else:
                    user_state = render_state_for_step(task_message, trace_steps)
                    messages = [
                        {
                            "role": "system",
                            "content": system_prompt,
                        }
                    ]
                    messages.append({"role": "user", "content": user_state})
                    state_text = render_state_for_step(
                        task_message,
                        trace_steps,
                        system_prompt=system_prompt,
                    )
                memory_z = self._memory_z_for_turn(state_text)
                output = self.backend.generate(
                    messages=messages,
                    max_new_tokens=self.max_new_tokens,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    injector=self.injector,
                    memory_z=memory_z,
                )
                ttft_ms += output.ttft_ms
                usage = self._accumulate_usage(usage, output.usage)
                code, fixed_text = extract_code_and_fix_content(output.text)
                trace.append({"role": "assistant", "content": redact(fixed_text)})
                conversation_history.append({"role": "assistant", "content": fixed_text})
                observation = world.execute(code)
                raw_observation_content = f"Output:\n```\n{str(observation)}\n```"
                trace.append({"role": "user", "content": redact(raw_observation_content)})
                conversation_history.append({"role": "user", "content": raw_observation_content})
                trace_steps.append(
                    AppWorldTraceStep(
                        index=steps,
                        response=fixed_text,
                        observation=raw_observation_content,
                    )
                )
                observation_text = str(observation)
                invalid_code_key = code.strip()
                is_invalid_repeat_candidate = (
                    not invalid_code_key
                    or "Syntax error" in observation_text
                    or "No code available" in observation_text
                )
                if is_invalid_repeat_candidate:
                    invalid_code_key = invalid_code_key or "<empty>"
                    if invalid_code_key == last_invalid_code:
                        repeated_invalid_code += 1
                    else:
                        last_invalid_code = invalid_code_key
                        repeated_invalid_code = 1
                    if repeated_invalid_code >= 3:
                        break
                else:
                    last_invalid_code = ""
                    repeated_invalid_code = 0
                if world.task_completed():
                    break
            success = world.task_completed() and self._evaluate_with_appworld(task_id)

        wall_time = time.perf_counter() - start
        result = BenchmarkResult(
            task_id=task_id,
            success=success,
            score=100.0 if success else 0.0,
            steps=steps,
            prompt_tokens=usage["prompt_tokens"],
            generated_tokens=usage["completion_tokens"],
            ttft_ms=ttft_ms,
            wall_time_s=wall_time,
            extra_metrics={"trace": trace},
        )
        log_path = (
            self.root
            / "experiments"
            / "outputs"
            / self.experiment_name
            / "evaluations"
            / f"{task_id}.json"
        )
        atomic_write_json(log_path, result.to_dict())
        return result
