from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from rcmf.benchmarks.webshop.adapter import (
    PROMPT_PROFILE,
    QwenToolTokenCounter,
    WebShopPortableAdapterV2,
    format_visible_state,
    load_task_catalog,
)
from rcmf.benchmarks.webshop.runtime_client import WebShopHTTPRuntime
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.schemas import DecisionStateRecord, ProvenanceClass
from rcmf.utils.serialization import atomic_write_json


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--index", type=int, default=1500)
    args = parser.parse_args()
    if args.index < 1500 or args.index >= 12000:
        raise ValueError("live adapter engineering probe must be train-only")

    tasks = load_task_catalog(args.task_catalog)
    task = next(row for row in tasks["train"] if int(row.metadata["index"]) == args.index)
    counter = QwenToolTokenCounter(args.model)

    def token_counter(messages: Any, profile: str, tools: Any) -> int:
        return counter(messages, profile, tools)

    adapter = WebShopPortableAdapterV2(
        task_records=tasks,
        trajectory_records={"train": ()},
        trajectory_source_identity={
            "classification": "LIVE_ADAPTER_ENGINEERING_PROBE_NO_CORPUS",
            "source_commit": "runtime-bound-by-caller",
        },
        runtime_identity={
            "bridge": "agentbench_fc_webshop_http_bridge_v1",
            "endpoint": args.endpoint,
        },
        token_counter=token_counter,
        runtime_factory=lambda row: WebShopHTTPRuntime(
            int(row.metadata["index"]),
            endpoint=args.endpoint,
            session_namespace="rcmf-adapter-live-probe",
        ),
    )
    runtime = adapter.create_runtime(task)
    try:
        first = {
            "instruction_sha256": text_sha256(runtime.instruction),
            "observation_sha256": text_sha256(runtime.observation),
            "available_actions_sha256": content_sha256(runtime.available_actions()),
        }
        if runtime.instruction != task.instruction:
            raise RuntimeError("runtime instruction differs from frozen task catalog")
        state = DecisionStateRecord(
            state_id=f"adapter-live-probe:{args.index}:state:0",
            task_id=task.task_id,
            trajectory_prefix=(),
            current_observation=format_visible_state(
                runtime.observation, runtime.available_actions()
            ),
            target_action_reference={"action": "search[train-visible-instruction]"},
            model_split="train",
            provenance=ProvenanceClass.AGENT_GENERATED,
            prompt_profile=PROMPT_PROFILE,
            environment_replay_reference={
                "trajectory_id": "engineering-probe-no-corpus",
                "step_index": 0,
                "task_index": args.index,
            },
            metadata={
                "instruction": task.instruction,
                "initial_observation": runtime.observation,
                "initial_available_actions": runtime.available_actions(),
            },
        )
        state.validate()
        request = adapter.generation_request(state, PROMPT_PROFILE)
        token_count = adapter.count_runtime_tokens(request["messages"], PROMPT_PROFILE)
        action = f"search[{task.instruction}]"
        validation = adapter.validate_action(action, runtime)
        if not validation.get("valid") or not validation.get("environment_accepted"):
            raise RuntimeError("train-visible search action was rejected")
        step = adapter.execute_action(runtime, action)
        second_reset = runtime.reset()
        second = {
            "instruction_sha256": text_sha256(runtime.instruction),
            "observation_sha256": text_sha256(runtime.observation),
            "available_actions_sha256": content_sha256(runtime.available_actions()),
        }
        payload = {
            "format": "rcmf_agentbench_fc_webshop_live_adapter_probe_v1",
            "classification": "ENGINEERING_ONLY_NO_MODEL_GENERATION_NO_CORPUS",
            "task_id": task.task_id,
            "index": args.index,
            "prompt_profile": PROMPT_PROFILE,
            "prompt_tokens_with_tools": token_count,
            "tool_count": len(request["tools"]),
            "tool_choice": request["tool_choice"],
            "first_reset": first,
            "second_reset": second,
            "reset_deterministic": first == second,
            "search": {
                "action_sha256": text_sha256(action),
                "observation_sha256": text_sha256(str(step["observation"])),
                "available_actions_sha256": content_sha256(step["available_actions"]),
                "raw_reward": float(step["raw_reward"]),
                "done": bool(step["done"]),
                "environment_accepted": bool(step["environment_accepted"]),
            },
            "second_reset_task_id": second_reset["task_id"],
            "standard200_outcome_inspected": false,
            "model_generation_count": 0,
        }
        payload["probe_sha256"] = content_sha256(payload)
        atomic_write_json(args.output, payload)
        print(json.dumps(payload, sort_keys=True))
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
