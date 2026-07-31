from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid5, NAMESPACE_URL

from rcmf.benchmarks.appworld.data import probe_object_fields, render_appworld_experience
from rcmf.benchmarks.appworld.prompt import build_task_message
from rcmf.benchmarks.base import BenchmarkAdapter
from rcmf.config import RCMFConfig
from rcmf.schemas import BenchmarkResult, DecisionExample, MemoryRecord, TargetType


GROUND_TRUTH_FIELD_CANDIDATES = [
    "ground_truth",
    "ground_truth_api_calls",
    "ground_truth_compiled_solution",
    "compiled_solution",
    "solution",
    "answer",
    "api_calls",
    "steps",
]


class AppWorldAdapter(BenchmarkAdapter):
    def __init__(self, config: RCMFConfig) -> None:
        self.config = config
        self._splits: dict[str, list[str]] | None = None

    def load_splits(self, config: Any | None = None) -> dict[str, list[str]]:
        cfg = config or self.config
        from appworld import load_task_ids

        splits: dict[str, list[str]] = {}
        for logical_name, appworld_name in cfg.benchmark.splits.items():
            task_ids = load_task_ids(dataset_name=appworld_name)
            if cfg.benchmark.task_limit is not None:
                task_ids = task_ids[: cfg.benchmark.task_limit]
            splits[logical_name] = task_ids
        self._splits = splits
        return splits

    def _task_ids_for_split(self, split: str) -> list[str]:
        if self._splits is None:
            self.load_splits(self.config)
        assert self._splits is not None
        if split not in self._splits:
            raise KeyError(f"Unknown AppWorld split: {split}")
        return self._splits[split]

    def _load_task_payload(self, task_id: str, split: str) -> dict[str, Any]:
        from appworld import AppWorld

        allow_ground_truth = split not in {"test", "test_normal"}
        with AppWorld(
            task_id=task_id,
            experiment_name=f"rcmf_prepare_{split}",
            load_ground_truth=allow_ground_truth,
        ) as world:
            task = world.task
            supervisor = probe_object_fields(
                getattr(task, "supervisor", object()),
                ["first_name", "last_name", "email", "phone_number"],
            )
            payload: dict[str, Any] = {
                "task_id": task_id,
                "task_instruction": getattr(task, "instruction", ""),
                "supervisor": supervisor,
                "ground_truth": probe_object_fields(task, GROUND_TRUTH_FIELD_CANDIDATES)
                if allow_ground_truth
                else {},
                "ground_truth_available": allow_ground_truth,
            }
            return payload

    def _record_from_payload(self, payload: dict[str, Any], split: str) -> MemoryRecord:
        task_id = payload["task_id"]
        episode_id = f"appworld:{split}:{task_id}"
        memory_id = str(uuid5(NAMESPACE_URL, episode_id))
        trajectory = {
            "task_id": task_id,
            "task_instruction": payload.get("task_instruction", ""),
            "supervisor": payload.get("supervisor", {}),
            "ground_truth": payload.get("ground_truth", {}),
            "success": bool(payload.get("ground_truth")),
        }
        experience_text = render_appworld_experience(trajectory)
        metadata = {
            "split": split,
            "ground_truth_available": bool(payload.get("ground_truth")),
            "apps": self._extract_apps_from_ground_truth(payload.get("ground_truth", {})),
        }
        return MemoryRecord(
            memory_id=memory_id,
            benchmark="appworld",
            episode_id=episode_id,
            task_id=task_id,
            raw_trajectory=trajectory,
            experience_text=experience_text,
            outcome=1.0 if payload.get("ground_truth") else 0.0,
            success=bool(payload.get("ground_truth")),
            metadata=metadata,
        )

    def _extract_apps_from_ground_truth(self, ground_truth: dict[str, Any]) -> list[str]:
        text = json.dumps(ground_truth, default=str)
        apps: set[str] = set()
        for match in re_find_apps(text):
            apps.add(match)
        return sorted(apps)

    def build_memory_records(self, split: str) -> Iterable[MemoryRecord]:
        for task_id in self._task_ids_for_split(split):
            yield self._record_from_payload(self._load_task_payload(task_id, split), split)

    def build_decision_examples(self, split: str) -> Iterable[DecisionExample]:
        for task_id in self._task_ids_for_split(split):
            payload = self._load_task_payload(task_id, split)
            ground_truth = payload.get("ground_truth", {})
            target, target_type = self._extract_target(ground_truth)
            if not target:
                continue
            episode_id = f"appworld:{split}:{task_id}"
            supervisor = payload.get("supervisor", {})
            state = build_task_message(payload.get("task_instruction", ""), supervisor)
            yield DecisionExample(
                benchmark="appworld",
                episode_id=episode_id,
                step_id=0,
                state_text=state,
                target_text=target,
                target_type=target_type,
                candidate_memory_ids=None,
                metadata={
                    "task_id": task_id,
                    "split": split,
                    "source": "ground_truth_probe",
                    "apps": self._extract_apps_from_ground_truth(ground_truth),
                },
            )

    def _extract_target(self, ground_truth: dict[str, Any]) -> tuple[str, TargetType]:
        nested = ground_truth.get("ground_truth")
        if isinstance(nested, dict):
            target, target_type = self._extract_target(nested)
            if target:
                return target, target_type

        for key in [
            "ground_truth_compiled_solution",
            "compiled_solution",
            "compiled_solution_code",
            "solution_code",
            "solution",
            "api_calls",
            "steps",
        ]:
            value = ground_truth.get(key)
            if value:
                return self._target_value_to_text(value), "code"

        return "", "code"

    def _target_value_to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str, ensure_ascii=False)

    def render_state(self, env_state: Any, history: list[dict[str, str]]) -> str:
        task_instruction = getattr(getattr(env_state, "task", env_state), "instruction", "")
        lines = ["[TASK]", str(task_instruction), "[RECENT HISTORY]"]
        for msg in history[-self.config.state.history_steps :]:
            lines.append(f"{msg.get('role', '').upper()}: {msg.get('content', '')}")
        return "\n".join(lines)

    def render_experience(self, trajectory: dict[str, Any]) -> str:
        return render_appworld_experience(trajectory)

    def run_episode(self, policy: Any, task_id: str, config: Any | None = None) -> BenchmarkResult:
        start = time.perf_counter()
        result = policy.run_task(task_id)
        result.wall_time_s = time.perf_counter() - start
        return result

    def evaluate_episode(self, task_id: str, trace: dict[str, Any]) -> BenchmarkResult:
        return BenchmarkResult(
            task_id=task_id,
            success=bool(trace.get("success", False)),
            score=float(trace.get("score", 0.0)),
            steps=len(trace.get("steps", [])),
            prompt_tokens=int(trace.get("prompt_tokens", 0)),
            generated_tokens=int(trace.get("generated_tokens", 0)),
            ttft_ms=float(trace.get("ttft_ms", 0.0)),
            wall_time_s=float(trace.get("wall_time_s", 0.0)),
            extra_metrics=dict(trace.get("extra_metrics", {})),
        )


def re_find_apps(text: str) -> list[str]:
    import re

    return re.findall(r"apis\.([a-zA-Z_][a-zA-Z0-9_]*)\.", text)
