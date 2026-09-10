from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import torch

from rcmf.benchmarks.webshop.adapter import (
    MAX_ROUNDS,
    PROMPT_PROFILE,
    WebShopPortableAdapterV2,
    format_turn,
)
from rcmf.benchmarks.webshop.generation import (
    HFQwenToolGenerator,
    parse_generated_tool_call,
)
from rcmf.benchmarks.webshop.method import (
    build_trainable_components,
    frozen_selector,
)
from rcmf.benchmarks.webshop.representations import (
    STATE_VIEW_NAMES,
    encode_structured_text,
    live_state_representation_text,
)
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    ProvenanceClass,
    TaskRecord,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    read_compiled_field,
    tensor_sha256,
)

CONDITIONS = ("B0", "RCMF-C", "RCMF-S")
EVALUATION_TASK_FORMAT = "rcmf_agentbench_fc_webshop_evaluation_task_v1"


class FrozenWebShopMethod:
    def __init__(
        self,
        *,
        package_path: str | Path,
        model_snapshot: str | Path,
    ) -> None:
        package = torch.load(
            Path(package_path).resolve(strict=True), map_location="cpu", weights_only=False
        )
        if package.get("format") != "rcmf_agentbench_fc_webshop_frozen_method_v1":
            raise ValueError("unexpected frozen WebShop method package")
        self.package = package
        self.generator = HFQwenToolGenerator(model_snapshot, dtype="bfloat16")
        self.model = self.generator.model
        self.tokenizer = self.generator.tokenizer
        self.device = next(self.model.parameters()).device
        self.selector = frozen_selector(package["selector"], device=self.device)
        self.writer, self.reader = build_trainable_components(self.device)
        self.writer.load_state_dict(package["writer"])
        self.reader.load_state_dict(package["reader"])
        model_dtype = next(self.model.parameters()).dtype
        if not model_dtype.is_floating_point:
            raise TypeError(f"WebShop generator model uses non-floating dtype: {model_dtype}")
        # The frozen reader is trained and stored in FP32, while the frozen Qwen
        # generator is loaded in BF16. Forward hooks receive generator hidden
        # states, so reader parameters must use the same inference dtype.
        self.reader.to(device=self.device, dtype=model_dtype)
        for module in (self.model, self.selector, self.writer, self.reader):
            module.eval()
            for parameter in module.parameters():
                parameter.requires_grad_(False)
        self.fields = {
            "B0": (
                package["correct_A"].to(self.device, torch.float32).clone().zero_(),
                package["correct_B"].to(self.device, torch.float32).clone().zero_(),
                False,
            ),
            "RCMF-C": (
                package["correct_A"].to(self.device, torch.float32),
                package["correct_B"].to(self.device, torch.float32),
                True,
            ),
            "RCMF-S": (
                package["shuffled_A"].to(self.device, torch.float32),
                package["shuffled_B"].to(self.device, torch.float32),
                True,
            ),
        }

    @torch.no_grad()
    def state_query_and_slots(
        self,
        *,
        condition: str,
        task: TaskRecord,
        history: Sequence[Mapping[str, Any]],
        observation: str,
        available_actions: Mapping[str, Any],
    ) -> tuple[torch.Tensor, torch.Tensor, Mapping[str, Any]]:
        if condition not in CONDITIONS:
            raise KeyError(f"unknown WebShop evaluation condition: {condition}")
        text, spans, metadata = live_state_representation_text(
            task_id=task.task_id,
            instruction=task.instruction,
            history=history,
            observation=observation,
            available_actions=available_actions,
        )
        views, span_rows = encode_structured_text(
            model=self.model,
            tokenizer=self.tokenizer,
            text=text,
            spans=spans,
            view_names=STATE_VIEW_NAMES,
            device=self.device,
        )
        query = self.selector.query(views.unsqueeze(0).to(self.device))[0]
        A, B, nonempty = self.fields[condition]
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=nonempty)
        return (
            query,
            slots,
            {
                **dict(metadata),
                "span_token_counts": {
                    name: int(row["token_count"]) for name, row in span_rows.items()
                },
                "representation_tokens": max(int(row["token_end"]) for row in span_rows.values()),
            },
        )

    def generate(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        slots: torch.Tensor,
        max_new_tokens: int,
    ) -> Mapping[str, Any]:
        input_ids = self.tokenizer.apply_chat_template(
            list(messages),
            tools=list(tools),
            add_generation_prompt=True,
            enable_thinking=False,
            tokenize=True,
            return_tensors="pt",
        )
        if not hasattr(input_ids, "to"):
            input_ids = torch.tensor([input_ids], dtype=torch.long)
        if input_ids.ndim == 1:
            input_ids = input_ids.unsqueeze(0)
        input_ids = input_ids.to(self.device)
        attention_mask = torch.ones_like(input_ids)
        hooks = FieldReaderHooks(model=self.model, reader=self.reader, slots=slots)
        started = time.perf_counter()
        with hooks, torch.inference_mode():
            output_ids = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        generated = output_ids[0, input_ids.shape[1] :].tolist()
        return {
            "assistant_text": self.tokenizer.decode(generated, skip_special_tokens=True),
            "raw_decoded_with_special_tokens": self.tokenizer.decode(
                generated, skip_special_tokens=False
            ),
            "token_ids": generated,
            "usage": {
                "prompt_tokens": int(input_ids.shape[1]),
                "completion_tokens": len(generated),
                "total_tokens": int(input_ids.shape[1]) + len(generated),
            },
            "elapsed_ms": elapsed_ms,
            "reader_audit": hooks.audit.as_dict(),
            "input_ids_sha256": content_sha256(input_ids[0].tolist()),
        }


def _initial_messages(
    adapter: WebShopPortableAdapterV2, task: TaskRecord, runtime: Any
) -> list[Mapping[str, Any]]:
    state = DecisionStateRecord(
        state_id=f"evaluation:{task.task_id}:initial",
        task_id=task.task_id,
        trajectory_prefix=(),
        current_observation=runtime.observation,
        target_action_reference={"action": "FROZEN_MODEL_GENERATION"},
        model_split=task.split,
        provenance=ProvenanceClass.AGENT_GENERATED,
        prompt_profile=PROMPT_PROFILE,
        environment_replay_reference={"task_index": task.metadata["index"]},
        metadata={
            "instruction": task.instruction,
            "initial_observation": runtime.observation,
            "initial_available_actions": runtime.available_actions(),
        },
    )
    state.validate()
    return list(adapter.render_messages(state, PROMPT_PROFILE))


def run_evaluation_task(
    *,
    adapter: WebShopPortableAdapterV2,
    method: FrozenWebShopMethod,
    task: TaskRecord,
    condition: str,
    max_new_tokens: int,
    evaluation_lock_sha256: str,
) -> Mapping[str, Any]:
    if condition not in CONDITIONS:
        raise KeyError(f"unknown WebShop condition: {condition}")
    runtime = adapter.create_runtime(task)
    messages = _initial_messages(adapter, task, runtime)
    history: list[Mapping[str, Any]] = []
    rounds: list[Mapping[str, Any]] = []
    steps: list[Mapping[str, Any]] = []
    started = time.perf_counter()
    initial = {
        "instruction": runtime.instruction,
        "observation": runtime.observation,
        "available_actions": runtime.available_actions(),
    }
    error: Mapping[str, Any] | None = None
    try:
        if runtime.instruction != task.instruction:
            raise RuntimeError("WEBSHOP_RUNTIME_TASK_INSTRUCTION_MISMATCH")
        for round_index in range(MAX_ROUNDS):
            query_started = time.perf_counter()
            query, slots, representation = method.state_query_and_slots(
                condition=condition,
                task=task,
                history=history,
                observation=runtime.observation,
                available_actions=runtime.available_actions(),
            )
            query_elapsed = time.perf_counter() - query_started
            generated = method.generate(
                messages=messages,
                tools=adapter.tools,
                slots=slots,
                max_new_tokens=max_new_tokens,
            )
            assistant_text = str(generated["assistant_text"])
            call_id = f"call-{round_index}"
            audit: dict[str, Any] = {
                "round_index": round_index,
                "assistant_text": assistant_text,
                "assistant_text_sha256": hashlib.sha256(assistant_text.encode("utf-8")).hexdigest(),
                "raw_decoded_with_special_tokens": generated["raw_decoded_with_special_tokens"],
                "token_ids": generated["token_ids"],
                "usage": generated["usage"],
                "generation_elapsed_ms": generated["elapsed_ms"],
                "query_elapsed_ms": query_elapsed * 1000.0,
                "input_ids_sha256": generated["input_ids_sha256"],
                "query_sha256": tensor_sha256(query),
                "slots_sha256": tensor_sha256(slots),
                "slot_norm": float(slots.to(torch.float32).norm().cpu()),
                "reader_audit": generated["reader_audit"],
                "representation": representation,
            }
            try:
                parsed = parse_generated_tool_call(assistant_text, call_id=call_id)
            except ValueError as exc:
                audit.update(
                    {
                        "parse_status": "INVALID_TOOL_CALL",
                        "failure_type": type(exc).__name__,
                        "failure_message": str(exc),
                        "environment_step_executed": False,
                    }
                )
                rounds.append(audit)
                history.append(
                    {
                        "round_index": round_index,
                        "invalid_model_output_sha256": audit["assistant_text_sha256"],
                    }
                )
                messages.append({"role": "assistant", "content": assistant_text})
                messages.append(
                    {
                        "role": "user",
                        "content": format_turn(
                            "No valid tool call found from agent.",
                            runtime.available_actions(),
                            initial=False,
                        ),
                    }
                )
                continue
            before = {
                "observation": runtime.observation,
                "available_actions": runtime.available_actions(),
            }
            validation = adapter.validate_action(parsed.action, runtime)
            result = adapter.execute_action(runtime, parsed.action)
            step = {
                "step_index": len(steps),
                "model_round_index": round_index,
                "call_id": call_id,
                "action": parsed.action,
                "pre_observation": before["observation"],
                "pre_available_actions": before["available_actions"],
                "post_observation": str(result["observation"]),
                "post_available_actions": dict(result["available_actions"]),
                "raw_reward": float(result["raw_reward"]),
                "done": bool(result["done"]),
                "environment_accepted": bool(
                    result.get("environment_accepted", validation["environment_accepted"])
                ),
            }
            steps.append(step)
            history.append(step)
            audit.update(
                {
                    "parse_status": "VALID_TOOL_CALL",
                    "action": parsed.action,
                    "environment_step_executed": True,
                    "environment_accepted": step["environment_accepted"],
                }
            )
            rounds.append(audit)
            messages.append(
                {
                    "role": "assistant",
                    "content": parsed.assistant_content,
                    "tool_calls": [parsed.tool_call],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": (
                        f"Action: {parsed.action}\n\n"
                        + format_turn(
                            step["post_observation"],
                            step["post_available_actions"],
                            initial=False,
                        )
                    ),
                }
            )
            if step["done"]:
                break
    except Exception as exc:  # noqa: BLE001 - formal task evidence retains typed errors
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        final = {
            "raw_reward": float(runtime.raw_reward),
            "done": bool(runtime.done),
            "observation": runtime.observation,
            "available_actions": runtime.available_actions(),
        }
        runtime.close()
    payload = {
        "format": EVALUATION_TASK_FORMAT,
        "evaluation_lock_sha256": evaluation_lock_sha256,
        "condition": condition,
        "task_id": task.task_id,
        "task_index": int(task.metadata["index"]),
        "split": task.split,
        "instruction_sha256": hashlib.sha256(task.instruction.encode("utf-8")).hexdigest(),
        "initial": initial,
        "rounds": rounds,
        "steps": steps,
        "final": final,
        "raw_reward": final["raw_reward"],
        "full_success": final["raw_reward"] == 1.0,
        "model_round_count": len(rounds),
        "environment_step_count": len(steps),
        "search_count": sum(str(row["action"]).startswith("search[") for row in steps),
        "click_count": sum(str(row["action"]).startswith("click[") for row in steps),
        "invalid_action_count": sum(row["parse_status"] == "INVALID_TOOL_CALL" for row in rounds),
        "environment_noop_count": sum(not bool(row["environment_accepted"]) for row in steps),
        "max_round_termination": not final["done"] and len(rounds) == MAX_ROUNDS,
        "prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rounds),
        "generated_tokens": sum(int(row["usage"]["completion_tokens"]) for row in rounds),
        "representation_tokens": sum(
            int(row["representation"]["representation_tokens"]) for row in rounds
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "error": error,
        "raw_memory_prompt_used": False,
        "runtime_memory_scan_used": False,
    }
    body_hash = content_sha256(payload)
    payload["task_artifact_sha256"] = body_hash
    return payload
