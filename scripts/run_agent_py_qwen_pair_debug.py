from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.benchmarks.appworld.traces import parse_environment_io_markdown
from rcmf.factory import build_backend, build_trainer
from rcmf.memory.state import MemoryState
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.utils.serialization import atomic_write_json


def install_ctypes_windows_time_shim() -> None:
    """Let the original Windows-oriented agent.py import on Linux."""
    import ctypes

    if hasattr(ctypes, "windll"):
        return

    class _Kernel32:
        @staticmethod
        def GetTickCount64() -> int:
            return int(float(os.times().elapsed) * 1000)

    class _Windll:
        kernel32 = _Kernel32()

    ctypes.windll = _Windll()  # type: ignore[attr-defined]


def real_wall_time_s() -> float:
    return float(os.times().elapsed)


def messages_to_state_text(messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for message in messages:
        lines.extend([f"[{message['role'].upper()}]", message.get("content", "").strip()])
    return "\n".join(lines).strip() + "\n"


def extract_code_and_fix_content(text: str | None) -> tuple[str, str]:
    if text is None:
        return "", ""
    full_code_regex = r"```python\n(.*?)```"
    partial_code_regex = r".*```python\n(.*)"
    for match in re.finditer(full_code_regex, text, flags=re.DOTALL):
        return match.group(1).strip(), text[: match.end()]
    partial_match = re.match(partial_code_regex, text, flags=re.DOTALL)
    if partial_match:
        fixed = text if text.endswith("\n") else text + "\n"
        return partial_match.group(1).strip(), fixed + "```"
    return "", text


def parse_agent_trace(trace: list[str]) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for index in range(1, len(trace), 2):
        assistant = trace[index] if index < len(trace) else ""
        observation = trace[index + 1] if index + 1 < len(trace) else ""
        assistant_content = assistant.removeprefix("ASSISTANT:\n")
        observation_content = observation.removeprefix("USER:\n")
        code, fixed = extract_code_and_fix_content(assistant_content)
        steps.append(
            {
                "fixed_model_output_from_agent_trace": fixed,
                "extracted_code_from_agent_trace": code,
                "appworld_observation_from_agent_trace": observation_content,
            }
        )
    return steps


def parse_environment_io(root: Path, experiment_name: str, task_id: str) -> list[dict[str, str]]:
    env_path = (
        root
        / "experiments"
        / "outputs"
        / experiment_name
        / "tasks"
        / task_id
        / "logs"
        / "environment_io.md"
    )
    if not env_path.exists():
        return []
    try:
        parsed = parse_environment_io_markdown(
            env_path.read_text(encoding="utf-8"),
            source_path=str(env_path),
        )
    except Exception:
        return []
    return [
        {
            "extracted_code_from_appworld_io": step.response,
            "appworld_observation_from_environment_io": step.observation,
        }
        for step in parsed
    ]


def robust_appworld_evaluate(self: Any, experiment_name: str, task_id: str, root: str = ".") -> bool:
    root_path = Path(root).resolve()
    cmd = [
        sys.executable,
        "-m",
        "appworld.cli",
        "evaluate",
        experiment_name,
        "--task-id",
        task_id,
    ]
    if str(root) != ".":
        cmd.extend(["--root", str(root_path)])
    try:
        subprocess.run(cmd, cwd=root_path, check=True)
    except subprocess.CalledProcessError:
        return False
    result_path = (
        root_path
        / "experiments"
        / "outputs"
        / experiment_name
        / "evaluations"
        / f"on_only_{task_id}.json"
    )
    if not result_path.exists():
        return False
    data = json.loads(result_path.read_text(encoding="utf-8"))
    return float(data.get("aggregate", {}).get("task_goal_completion", 0.0)) == 100.0


class ForwardRecorder:
    def __init__(
        self,
        label: str,
        backend: HFQwenBackend,
        max_new_tokens: int,
        top_p: float,
        memory_state: MemoryState | None = None,
        state_encoder: Any | None = None,
        injector: Any | None = None,
        config: Any | None = None,
    ) -> None:
        self.label = label
        self.backend = backend
        self.max_new_tokens = max_new_tokens
        self.top_p = top_p
        self.memory_state = memory_state
        self.state_encoder = state_encoder
        self.injector = injector
        self.config = config
        self.calls: list[dict[str, Any]] = []

    def _memory_z_for_messages(self, messages: list[dict[str, str]]) -> tuple[torch.Tensor | None, dict[str, Any]]:
        if self.memory_state is None or self.state_encoder is None or self.injector is None:
            return None, {"enabled": False}
        state_text = messages_to_state_text(messages)
        tokenized = self.backend.tokenizer(state_text, return_tensors="pt")
        device = next(self.state_encoder.parameters()).device
        with torch.no_grad():
            if self.config.encoder.type == "qwen_hidden":
                state_repr = self.backend.encode_texts([state_text], batch_size=1).to(device)
                address = self.state_encoder(state_repr, None)
                token_count = int(tokenized["input_ids"].shape[-1])
            else:
                input_ids = tokenized["input_ids"]
                attention_mask = tokenized.get("attention_mask", torch.ones_like(input_ids))
                address = self.state_encoder(input_ids.to(device), attention_mask.to(device))
                token_count = int(input_ids.shape[-1])
            memory_z = self.memory_state.read(
                address.cpu(),
                normalization=self.config.memory.normalization,
                eps=self.config.memory.eps,
            )
        top_values, top_indices = torch.topk(address.detach().cpu()[0], k=min(8, address.shape[-1]))
        return memory_z, {
            "enabled": True,
            "state_text_chars": len(state_text),
            "state_text_tokens": token_count,
            "address_top_indices": top_indices.tolist(),
            "address_top_values": [float(value) for value in top_values.tolist()],
            "memory_z_norm": float(memory_z.norm().item()),
            "memory_z_abs_max": float(memory_z.abs().max().item()),
        }

    def forward(
        self,
        query: str | None = None,
        messages: list[dict[str, str]] | None = None,
        sys_prompt: str | None = None,
        model_name: str = "",
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
        result_format: str = "message",
        **kwargs: Any,
    ) -> tuple[str, dict[str, int]]:
        del model_name, api_key, max_tokens, enable_thinking, result_format, kwargs
        if messages is None:
            messages = []
            if sys_prompt:
                messages.append({"role": "system", "content": sys_prompt})
            if query:
                messages.append({"role": "user", "content": query})
        rendered_prompt = self.backend._apply_chat_template(messages, add_generation_prompt=True)
        prompt_tokens = int(
            self.backend.tokenizer(rendered_prompt, return_tensors="pt")["input_ids"].shape[-1]
        )
        memory_z, memory_debug = self._memory_z_for_messages(messages)
        start = real_wall_time_s()
        output = self.backend.generate(
            messages=messages,
            max_new_tokens=self.max_new_tokens,
            temperature=temperature,
            top_p=self.top_p,
            injector=self.injector,
            memory_z=memory_z,
        )
        elapsed_s = real_wall_time_s() - start
        call = {
            "call_index": len(self.calls) + 1,
            "label": self.label,
            "messages": messages,
            "rendered_prompt": rendered_prompt,
            "message_count": len(messages),
            "prompt_tokens_before_generate": prompt_tokens,
            "temperature": temperature,
            "top_p": self.top_p,
            "max_new_tokens": self.max_new_tokens,
            "raw_model_output": output.text,
            "usage": output.usage,
            "generation_time_s": elapsed_s,
            "memory": memory_debug,
        }
        self.calls.append(call)
        return output.text, output.usage


def load_trained_modules(args: argparse.Namespace, backend: HFQwenBackend) -> tuple[Any, Any, MemoryState, Any]:
    cfg = load_config(args.config)
    trainer = build_trainer(cfg, backend)
    trainer.load_checkpoint(args.checkpoint, map_location=backend.device)
    trainer.to(backend.device).eval()
    memory_state = MemoryState.load(args.memory_snapshot)
    return cfg, trainer.state_encoder, memory_state, trainer.injector


def run_one(
    label: str,
    args: argparse.Namespace,
    backend: HFQwenBackend,
    experiment_name: str,
    output_path: Path,
    recorder: ForwardRecorder,
) -> dict[str, Any]:
    from agent import AppWorldAgent
    from model import MODEL

    AppWorldAgent._evaluate = robust_appworld_evaluate
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    recorder.calls.clear()
    MODEL.forward = staticmethod(recorder.forward)
    root = Path(args.root).resolve()
    agent = AppWorldAgent(
        dataset_name="appworld",
        experiment_name=experiment_name,
        root=str(root),
        max_context=args.max_context,
        max_steps=args.max_steps,
    )
    run_start = real_wall_time_s()
    trace: list[str] = []
    error: dict[str, str] | None = None
    try:
        is_correct, trace = agent.execute(task_id=args.task_id)
    except BaseException as exc:
        is_correct = False
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    elapsed_s = real_wall_time_s() - run_start
    trace_steps = parse_agent_trace(trace) if trace else []
    environment_steps = parse_environment_io(root, experiment_name, args.task_id)
    steps: list[dict[str, Any]] = []
    for index, call in enumerate(recorder.calls):
        trace_step = trace_steps[index] if index < len(trace_steps) else {}
        environment_step = environment_steps[index] if index < len(environment_steps) else {}
        code_from_raw, fixed_from_raw = extract_code_and_fix_content(call["raw_model_output"])
        steps.append(
            {
                "step": index + 1,
                "llm_input_messages": call["messages"],
                "llm_input_rendered_prompt": call["rendered_prompt"],
                "prompt_tokens": call["prompt_tokens_before_generate"],
                "raw_model_output": call["raw_model_output"],
                "fixed_model_output_from_raw": fixed_from_raw,
                "extracted_code_from_raw": code_from_raw,
                "usage": call["usage"],
                "generation_time_s": call["generation_time_s"],
                "memory": call["memory"],
                **environment_step,
                **trace_step,
            }
        )
    payload = {
        "label": label,
        "task_id": args.task_id,
        "experiment_name": experiment_name,
        "is_correct": is_correct,
        "num_steps": len(steps),
        "elapsed_s": elapsed_s,
        "model_name": args.model_name,
        "seed": args.seed,
        "max_context": args.max_context,
        "max_steps": args.max_steps,
        "max_new_tokens": args.max_new_tokens,
        "top_p": args.top_p,
        "agent_class": "agent.AppWorldAgent",
        "error": error,
        "trace": trace,
        "steps": steps,
    }
    atomic_write_json(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the same agent.py AppWorldAgent with base Qwen and trained RCMF Qwen."
    )
    parser.add_argument("--task-id", default="325d6ec_2")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-experiment-name", required=True)
    parser.add_argument("--trained-experiment-name", required=True)
    parser.add_argument("--model-name", default="Qwen/Qwen3-8B")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--max-context", type=int, default=40)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--memory-snapshot", required=True)
    args = parser.parse_args()

    install_ctypes_windows_time_shim()
    python_bin = str(Path(sys.executable).resolve().parent)
    os.environ["PATH"] = python_bin + os.pathsep + os.environ.get("PATH", "")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = build_backend(load_config(args.config), load_model=True)
    if backend.model_name != args.model_name:
        raise ValueError(f"Config model {backend.model_name} does not match --model-name {args.model_name}")

    base_recorder = ForwardRecorder(
        label="base_qwen",
        backend=backend,
        max_new_tokens=args.max_new_tokens,
        top_p=args.top_p,
    )
    base = run_one(
        "base_qwen",
        args,
        backend,
        args.base_experiment_name,
        output_dir / "base_qwen.json",
        base_recorder,
    )

    cfg, state_encoder, memory_state, injector = load_trained_modules(args, backend)
    trained_recorder = ForwardRecorder(
        label="trained_qwen_rcmf",
        backend=backend,
        max_new_tokens=args.max_new_tokens,
        top_p=args.top_p,
        memory_state=memory_state,
        state_encoder=state_encoder,
        injector=injector,
        config=cfg,
    )
    trained = run_one(
        "trained_qwen_rcmf",
        args,
        backend,
        args.trained_experiment_name,
        output_dir / "trained_qwen_rcmf.json",
        trained_recorder,
    )
    summary = {
        "task_id": args.task_id,
        "base": {
            "experiment_name": args.base_experiment_name,
            "is_correct": base["is_correct"],
            "num_steps": base["num_steps"],
            "error": base.get("error"),
            "log": str((output_dir / "base_qwen.json").resolve()),
        },
        "trained": {
            "experiment_name": args.trained_experiment_name,
            "is_correct": trained["is_correct"],
            "num_steps": trained["num_steps"],
            "error": trained.get("error"),
            "log": str((output_dir / "trained_qwen_rcmf.json").resolve()),
        },
    }
    atomic_write_json(output_dir / "summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
