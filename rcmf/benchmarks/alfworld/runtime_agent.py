from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any, Callable, Mapping

import torch
from torch import Tensor

from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    ACTION_CAP,
    EFFECTIVE_CONTEXT_LIMIT,
    GENERATION_IDENTITY_SHA256,
    MODEL_REVISION,
    ALFWorldPortableAdapterV2,
)
from rcmf.benchmarks.alfworld.prompt_profile import PROFILE_NAME, render_react_messages, render_react_trajectory
from rcmf.benchmarks.alfworld.task_manifest import canonical_sha256
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.pipeline.portable_v2.schemas import TaskRecord


MODEL_FILE_HASHES = {
    "config.json": "f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30",
    "generation_config.json": "2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2",
    "model.safetensors.index.json": "f9fdbcb91c23971c13ec5d5f2573d2349e8f61f2f049371ec699281748fdb1bc",
    "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    "tokenizer_config.json": "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_exact_model_snapshot(path: str | Path) -> Path:
    snapshot = Path(path).resolve(strict=True)
    if snapshot.name != MODEL_REVISION:
        raise ValueError("Qwen snapshot path is not the frozen exact revision")
    for relative, expected in MODEL_FILE_HASHES.items():
        source = snapshot / relative
        if not source.is_file() or sha256_file(source) != expected:
            raise ValueError(f"frozen Qwen file identity differs: {relative}")
    index = json.loads((snapshot / "model.safetensors.index.json").read_text(encoding="utf-8"))
    shards = sorted(set(index["weight_map"].values()))
    if len(shards) != 5 or any(not (snapshot / shard).is_file() for shard in shards):
        raise ValueError("frozen Qwen weight shard closure differs")
    return snapshot


def load_frozen_qwen(path: str | Path) -> HFQwenBackend:
    snapshot = validate_exact_model_snapshot(path)
    random.seed(25101)
    torch.manual_seed(25101)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(25101)
    backend = HFQwenBackend(
        model_name=str(snapshot),
        dtype="bfloat16",
        device_map=None,
        freeze_backbone=True,
        enable_thinking=False,
        load_model=True,
    )
    if backend.model is None or backend.tokenizer is None:
        raise RuntimeError("frozen Qwen load did not produce model/tokenizer")
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen backbone is not frozen")
    return backend


MemoryQuery = Callable[[str, list[dict[str, str]]], Tensor]


def first_decoded_line(text: str) -> str:
    lines = str(text).split("\n")
    return lines[0].strip() if lines else ""


def run_alfworld_episode(
    *,
    adapter: ALFWorldPortableAdapterV2,
    task: TaskRecord,
    backend: HFQwenBackend,
    condition: str,
    run_identity: Mapping[str, Any],
    injector: Any | None = None,
    memory_query: MemoryQuery | None = None,
) -> dict[str, Any]:
    if condition not in {"bare", "rcmf"}:
        raise ValueError("unknown ALFWorld evaluation condition")
    if (injector is None) != (memory_query is None):
        raise ValueError("RCMF injector and memory query must be provided together")
    if condition == "bare" and injector is not None:
        raise ValueError("bare ALFWorld condition rejects memory injection")
    if condition == "rcmf" and injector is None:
        raise ValueError("RCMF ALFWorld condition requires memory injection")

    runtime = adapter.create_runtime(task)
    history: list[dict[str, str]] = []
    steps: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    started = time.time()
    try:
        for step_index in range(ACTION_CAP):
            trajectory_text = render_react_trajectory(runtime.initial_observation, history)
            messages = list(
                render_react_messages(
                    profile_root=adapter.prompt_root,
                    gamefile=str(task.metadata["game_path"]),
                    current_trajectory=trajectory_text,
                )
            )
            prompt_tokens = adapter.count_runtime_tokens(messages, PROFILE_NAME)
            if prompt_tokens + 512 > EFFECTIVE_CONTEXT_LIMIT:
                error = {
                    "type": "CONTEXT_BUDGET_EXCEEDED",
                    "prompt_tokens": prompt_tokens,
                    "generation_reserve": 512,
                    "effective_context_limit": EFFECTIVE_CONTEXT_LIMIT,
                }
                break
            memory_z = None
            if memory_query is not None:
                memory_z = memory_query(trajectory_text, history)
                if memory_z.ndim == 1:
                    memory_z = memory_z.unsqueeze(0)
            try:
                generated = backend.generate(
                    messages,
                    max_new_tokens=512,
                    temperature=0.0,
                    top_p=1.0,
                    injector=injector,
                    memory_z=memory_z,
                )
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc), "step": step_index}
                break
            action = first_decoded_line(generated.text)
            validation = adapter.validate_action(action, runtime)
            if not validation["valid"]:
                error = {"type": "EMPTY_DECODED_ACTION", "step": step_index}
                steps.append(
                    {
                        "step_index": step_index,
                        "message_array_sha256": canonical_sha256(messages),
                        "prompt_tokens": prompt_tokens,
                        "raw_model_text": generated.text,
                        "generated_token_ids": generated.token_ids,
                        "usage": generated.usage,
                        "parsed_action": action,
                        "action_valid": False,
                    }
                )
                break
            outcome = adapter.execute_action(runtime, action)
            prompt_observation = "OK." if action.startswith("think:") else str(outcome["observation"])
            history.append({"action": action, "observation": prompt_observation})
            steps.append(
                {
                    "step_index": step_index,
                    "message_array_sha256": canonical_sha256(messages),
                    "prompt_tokens": prompt_tokens,
                    "raw_model_text": generated.text,
                    "generated_token_ids": generated.token_ids,
                    "usage": generated.usage,
                    "parsed_action": action,
                    "action_valid": True,
                    "environment_observation": outcome["observation"],
                    "prompt_observation": prompt_observation,
                    "environment_reward": outcome["raw_reward"],
                    "environment_done": outcome["done"],
                    "official_won": outcome["official_won"],
                    "memory_metadata": generated.extra.get("memory"),
                }
            )
            if outcome["done"]:
                break
    finally:
        if error is not None:
            runtime.error = error
        evaluation = adapter.evaluate_task(runtime, task)
        runtime.close()
    result = {
        "schema_version": "alfworld_agent_episode_v1",
        "condition": condition,
        "task_id": task.task_id,
        "split": task.split,
        "task_family": task.metadata["task_family"],
        "game_path": task.metadata["game_path"],
        "run_identity": dict(run_identity),
        "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
        "steps": steps,
        "step_count": len(steps),
        "raw_reward": evaluation.raw_reward,
        "official_success": evaluation.binary_success,
        "terminal_status": evaluation.terminal_status.value,
        "error": error,
        "elapsed_seconds": round(time.time() - started, 6),
    }
    result["episode_sha256"] = canonical_sha256(result)
    return result


def run_alfworld_episodes_batched(
    *,
    adapter: ALFWorldPortableAdapterV2,
    tasks: list[TaskRecord],
    backend: HFQwenBackend,
    condition: str,
    run_identity: Mapping[str, Any],
    batch_size: int,
    injector: Any | None = None,
    memory_queries: Mapping[str, MemoryQuery] | None = None,
) -> list[dict[str, Any]]:
    """Run complete independent environments with batched, masked greedy decode."""

    if batch_size <= 0:
        raise ValueError("ALFWorld generation batch size must be positive")
    if condition == "bare" and (injector is not None or memory_queries is not None):
        raise ValueError("bare batch rejects RCMF state")
    if condition == "rcmf" and (injector is None or memory_queries is None):
        raise ValueError("RCMF batch requires injector and fixed-field query closures")
    contexts = []
    for task in tasks:
        contexts.append(
            {
                "task": task,
                "runtime": adapter.create_runtime(task),
                "history": [],
                "steps": [],
                "error": None,
                "started": time.time(),
            }
        )
    try:
        for step_index in range(ACTION_CAP):
            active = [
                row
                for row in contexts
                if row["error"] is None and not row["runtime"].done
            ]
            if not active:
                break
            prepared = []
            for row in active:
                task = row["task"]
                runtime = row["runtime"]
                trajectory = render_react_trajectory(
                    runtime.initial_observation,
                    row["history"],
                )
                messages = list(
                    render_react_messages(
                        profile_root=adapter.prompt_root,
                        gamefile=str(task.metadata["game_path"]),
                        current_trajectory=trajectory,
                    )
                )
                try:
                    prompt_tokens = adapter.count_runtime_tokens(messages, PROFILE_NAME)
                except Exception as exc:
                    row["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "step": step_index,
                    }
                    continue
                prepared.append((row, messages, trajectory, prompt_tokens))
            # Flash SDPA cannot consume a non-null padding mask in this runtime.
            # Bucket by exact rendered token length so every microbatch is
            # padding-free and remains byte/token equivalent to scalar decode.
            buckets: dict[int, list[tuple[Any, list[Any], str, int]]] = {}
            for item in prepared:
                buckets.setdefault(item[3], []).append(item)
            for bucket in buckets.values():
                for start in range(0, len(bucket), batch_size):
                    chunk = bucket[start : start + batch_size]
                    legal = [item[0] for item in chunk]
                    messages_batch = [item[1] for item in chunk]
                    trajectories = [item[2] for item in chunk]
                    prompt_counts = [item[3] for item in chunk]
                    if not legal:
                        continue
                    memory_z = None
                    if memory_queries is not None:
                        memory_z = torch.cat(
                            [
                                memory_queries[row["task"].task_id](trajectory, row["history"])
                                for row, trajectory in zip(legal, trajectories, strict=True)
                            ],
                            dim=0,
                        )
                    try:
                        generated_rows = backend.generate_batch(
                            messages_batch,
                            max_new_tokens=512,
                            temperature=0.0,
                            top_p=1.0,
                            injector=injector,
                            memory_z=memory_z,
                        )
                    except Exception as exc:
                        for row in legal:
                            row["error"] = {
                                "type": type(exc).__name__,
                                "message": str(exc),
                                "step": step_index,
                                "batch_failure": True,
                            }
                        continue
                    for row, messages, prompt_tokens, generated in zip(
                        legal,
                        messages_batch,
                        prompt_counts,
                        generated_rows,
                        strict=True,
                    ):
                        action = first_decoded_line(generated.text)
                        validation = adapter.validate_action(action, row["runtime"])
                        step_record = {
                            "step_index": step_index,
                            "message_array_sha256": canonical_sha256(messages),
                            "prompt_tokens": prompt_tokens,
                            "raw_model_text": generated.text,
                            "generated_token_ids": generated.token_ids,
                            "usage": generated.usage,
                            "parsed_action": action,
                            "action_valid": bool(validation["valid"]),
                            "generation_batch_size": len(legal),
                            "requested_generation_batch_size": batch_size,
                            "homogeneous_prompt_tokens": True,
                            "memory_metadata": generated.extra.get("memory"),
                        }
                        row["steps"].append(step_record)
                        if not validation["valid"]:
                            row["error"] = {"type": "EMPTY_DECODED_ACTION", "step": step_index}
                            continue
                        try:
                            outcome = adapter.execute_action(row["runtime"], action)
                        except Exception as exc:
                            row["error"] = {
                                "type": type(exc).__name__,
                                "message": str(exc),
                                "step": step_index,
                            }
                            continue
                        prompt_observation = (
                            "OK." if action.startswith("think:") else str(outcome["observation"])
                        )
                        row["history"].append(
                            {"action": action, "observation": prompt_observation}
                        )
                        step_record.update(
                            {
                                "environment_observation": outcome["observation"],
                                "prompt_observation": prompt_observation,
                                "environment_reward": outcome["raw_reward"],
                                "environment_done": outcome["done"],
                                "official_won": outcome["official_won"],
                            }
                        )
    finally:
        results = []
        for row in contexts:
            runtime = row["runtime"]
            if row["error"] is not None:
                runtime.error = row["error"]
            evaluation = adapter.evaluate_task(runtime, row["task"])
            runtime.close()
            result = {
                "schema_version": "alfworld_agent_episode_v1",
                "condition": condition,
                "task_id": row["task"].task_id,
                "split": row["task"].split,
                "task_family": row["task"].metadata["task_family"],
                "game_path": row["task"].metadata["game_path"],
                "run_identity": dict(run_identity),
                "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
                "steps": row["steps"],
                "step_count": len(row["steps"]),
                "raw_reward": evaluation.raw_reward,
                "official_success": evaluation.binary_success,
                "terminal_status": evaluation.terminal_status.value,
                "error": row["error"],
                "elapsed_seconds": round(time.time() - row["started"], 6),
            }
            result["episode_sha256"] = canonical_sha256(result)
            results.append(result)
    return results
