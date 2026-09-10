from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rcmf.benchmarks.webshop.adapter import (
    MAX_ROUNDS,
    PROMPT_PROFILE,
    WebShopPortableAdapterV2,
    action_to_tool_call,
    format_turn,
    format_visible_state,
    parse_environment_action,
)
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    to_jsonable,
)

RAW_TASK_FORMAT = "rcmf_agentbench_fc_webshop_raw_task_v1"
BLOCK_SUMMARY_FORMAT = "rcmf_agentbench_fc_webshop_construction_block_v1"
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


class ToolGenerator(Protocol):
    identity: Mapping[str, Any]

    def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        *,
        max_new_tokens: int,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ParsedGeneratedToolCall:
    action: str
    tool_call: Mapping[str, Any]
    assistant_content: str
    payload: Mapping[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_generated_tool_call(text: str, *, call_id: str) -> ParsedGeneratedToolCall:
    matches = list(TOOL_CALL_RE.finditer(text))
    if len(matches) != 1:
        raise ValueError("generation must contain exactly one <tool_call> block")
    try:
        payload = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("generated tool-call payload is not valid JSON") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"name", "arguments"}:
        raise ValueError("generated tool call must contain only name and arguments")
    name = str(payload["name"])
    arguments = payload["arguments"]
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("generated tool-call arguments are not valid JSON") from exc
    if not isinstance(arguments, Mapping):
        raise TypeError("generated tool-call arguments must be an object")
    if name == "search_action" and set(arguments) == {"keywords"}:
        action = f"search[{arguments['keywords']}]"
    elif name == "click_action" and set(arguments) == {"value"}:
        action = f"click[{arguments['value']}]"
    else:
        raise ValueError("generated tool call does not match the frozen action schema")
    parse_environment_action(action)
    outside = text[: matches[0].start()] + text[matches[0].end() :]
    tool_call = action_to_tool_call(action, call_id=call_id)
    return ParsedGeneratedToolCall(
        action=action,
        tool_call=tool_call,
        assistant_content=outside.strip(),
        payload=dict(payload),
    )


def _initial_messages(
    adapter: WebShopPortableAdapterV2,
    task: TaskRecord,
    runtime: Any,
) -> list[Mapping[str, Any]]:
    state = DecisionStateRecord(
        state_id=f"construction:{task.task_id}:initial",
        task_id=task.task_id,
        trajectory_prefix=(),
        current_observation=format_visible_state(runtime.observation, runtime.available_actions()),
        target_action_reference={"action": "MODEL_GENERATED_TRAIN_ONLY"},
        model_split="train",
        provenance=ProvenanceClass.AGENT_GENERATED,
        prompt_profile=PROMPT_PROFILE,
        environment_replay_reference={
            "trajectory_id": "construction-pending",
            "step_index": 0,
            "task_index": task.metadata["index"],
        },
        metadata={
            "instruction": task.instruction,
            "initial_observation": runtime.observation,
            "initial_available_actions": runtime.available_actions(),
        },
    )
    state.validate()
    return list(adapter.generation_request(state, PROMPT_PROFILE)["messages"])


def _terminal_status(*, reward: float, done: bool, exhausted: bool) -> TerminalStatus:
    if reward == 1.0:
        return TerminalStatus.SUCCESS
    if done:
        return TerminalStatus.FAILURE
    if exhausted:
        return TerminalStatus.TRUNCATED
    return TerminalStatus.TERMINATED


def _page_type(available_actions: Mapping[str, Any], *, done: bool) -> str:
    if done:
        return "terminal"
    clickables = {str(value).lower() for value in available_actions.get("clickables", ())}
    if "buy now" in clickables:
        return "item"
    if clickables.intersection({"back to search", "next >", "< prev"}):
        return "results"
    if bool(available_actions.get("has_search_bar")) and clickables <= {"search"}:
        return "search"
    return "unknown"


def _sequence_payload(steps: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        {
            "step_index": int(step["step_index"]),
            "action": str(step["action"]),
            "pre_observation": str(step["pre_observation"]),
            "pre_available_actions": dict(step["pre_available_actions"]),
            "post_observation": str(step["post_observation"]),
            "post_available_actions": dict(step["post_available_actions"]),
            "raw_reward": float(step["raw_reward"]),
            "done": bool(step["done"]),
        }
        for step in steps
    ]


def replay_generated_steps(
    *,
    adapter: WebShopPortableAdapterV2,
    task: TaskRecord,
    initial: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    runtime = adapter.create_runtime(task)
    mismatch: Mapping[str, Any] | None = None
    replay_steps: list[Mapping[str, Any]] = []
    try:
        observed_initial = {
            "instruction": runtime.instruction,
            "observation": runtime.observation,
            "available_actions": runtime.available_actions(),
        }
        if observed_initial != dict(initial):
            mismatch = {
                "type": "WEBSHOP_REPLAY_INITIAL_STATE_MISMATCH",
                "expected_sha256": content_sha256(initial),
                "observed_sha256": content_sha256(observed_initial),
            }
        for expected in steps:
            if mismatch is not None:
                break
            before = {
                "observation": runtime.observation,
                "available_actions": runtime.available_actions(),
            }
            expected_before = {
                "observation": expected["pre_observation"],
                "available_actions": expected["pre_available_actions"],
            }
            if before != expected_before:
                mismatch = {
                    "type": "WEBSHOP_REPLAY_PRE_ACTION_STATE_MISMATCH",
                    "step_index": expected["step_index"],
                    "expected_sha256": content_sha256(expected_before),
                    "observed_sha256": content_sha256(before),
                }
                break
            result = adapter.execute_action(runtime, str(expected["action"]))
            observed = {
                "step_index": int(result["step_index"]),
                "action": str(result["action"]),
                "post_observation": str(result["observation"]),
                "post_available_actions": dict(result["available_actions"]),
                "raw_reward": float(result["raw_reward"]),
                "done": bool(result["done"]),
            }
            expected_after = {
                "step_index": int(expected["step_index"]),
                "action": str(expected["action"]),
                "post_observation": str(expected["post_observation"]),
                "post_available_actions": dict(expected["post_available_actions"]),
                "raw_reward": float(expected["raw_reward"]),
                "done": bool(expected["done"]),
            }
            replay_steps.append(observed)
            if observed != expected_after:
                mismatch = {
                    "type": "WEBSHOP_REPLAY_STEP_MISMATCH",
                    "step_index": expected["step_index"],
                    "expected_sha256": content_sha256(expected_after),
                    "observed_sha256": content_sha256(observed),
                }
        final = {
            "raw_reward": float(runtime.raw_reward),
            "done": bool(runtime.done),
            "observation": runtime.observation,
            "available_actions": runtime.available_actions(),
        }
    finally:
        runtime.close()
    status = ReplayStatus.VALIDATED if mismatch is None else ReplayStatus.FAILED
    return {
        "status": status.value,
        "mismatch": mismatch,
        "steps_replayed": len(replay_steps),
        "sequence_sha256": content_sha256(replay_steps),
        "final": final,
    }


def _admitted_trajectory(
    *,
    task: TaskRecord,
    steps: Sequence[Mapping[str, Any]],
    rounds: Sequence[Mapping[str, Any]],
    source_identity: Mapping[str, Any],
    environment_identity: Mapping[str, Any],
    generator_identity: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> TrajectoryRecord:
    source_sha = content_sha256(source_identity)
    trajectory_id = f"webshop-agent-generated:{source_sha[:16]}:{int(task.metadata['index']):05d}"
    converted: list[TrajectoryStep] = []
    for index, step in enumerate(steps):
        final = index == len(steps) - 1
        converted.append(
            TrajectoryStep(
                step_index=index,
                pre_action_state=format_visible_state(
                    str(step["pre_observation"]), step["pre_available_actions"]
                ),
                action=str(step["action"]),
                post_action_observation=str(step["post_observation"]),
                raw_reward=float(step["raw_reward"]),
                terminal_status=(TerminalStatus.SUCCESS if final else TerminalStatus.NOT_TERMINAL),
                metadata={
                    "call_id": step["call_id"],
                    "model_round_index": step["model_round_index"],
                    "pre_observation": step["pre_observation"],
                    "pre_available_actions": step["pre_available_actions"],
                    "post_observation": step["post_observation"],
                    "post_available_actions": step["post_available_actions"],
                    "environment_accepted": step["environment_accepted"],
                    "page_type": step.get("page_type", "unknown"),
                },
            )
        )
    trajectory = TrajectoryRecord(
        trajectory_id=trajectory_id,
        task_id=task.task_id,
        provenance=ProvenanceClass.AGENT_GENERATED,
        source_identity=dict(source_identity),
        steps=tuple(converted),
        raw_reward=1.0,
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity=dict(environment_identity),
        agent_identity=dict(generator_identity),
        metadata={
            "index": int(task.metadata["index"]),
            "goal": task.instruction,
            "model_round_count": len(rounds),
            "environment_step_count": len(steps),
            "sequence_sha256": content_sha256(_sequence_payload(steps)),
            "replay_sequence_sha256": replay["sequence_sha256"],
            "provenance_class": ProvenanceClass.AGENT_GENERATED.value,
        },
    )
    trajectory.validate()
    return trajectory


def run_construction_task(
    *,
    adapter: WebShopPortableAdapterV2,
    task: TaskRecord,
    generator: ToolGenerator,
    source_identity: Mapping[str, Any],
    environment_identity: Mapping[str, Any],
    max_new_tokens: int,
    admissible: bool,
) -> Mapping[str, Any]:
    if task.split != "train" or int(task.metadata["index"]) not in range(1500, 12000):
        raise ValueError("construction generation is restricted to the frozen train split")
    if max_new_tokens <= 0:
        raise ValueError("max_new_tokens must be positive")
    runtime = adapter.create_runtime(task)
    rounds: list[Mapping[str, Any]] = []
    steps: list[Mapping[str, Any]] = []
    started = time.perf_counter()
    error: Mapping[str, Any] | None = None
    try:
        if runtime.instruction != task.instruction:
            raise RuntimeError("WEBSHOP_RUNTIME_TASK_INSTRUCTION_MISMATCH")
        initial = {
            "instruction": runtime.instruction,
            "observation": runtime.observation,
            "available_actions": runtime.available_actions(),
        }
        messages = _initial_messages(adapter, task, runtime)
        for round_index in range(MAX_ROUNDS):
            before = {
                "observation": runtime.observation,
                "available_actions": runtime.available_actions(),
                "raw_reward": float(runtime.raw_reward),
                "done": bool(runtime.done),
            }
            generated = dict(
                generator.generate(
                    messages,
                    adapter.tools,
                    max_new_tokens=max_new_tokens,
                )
            )
            assistant_text = str(generated.get("assistant_text", ""))
            call_id = f"call-{round_index}"
            audit: dict[str, Any] = {
                "round_index": round_index,
                "pre_state_sha256": content_sha256(before),
                "assistant_text": assistant_text,
                "assistant_text_sha256": text_sha256(assistant_text),
                "raw_decoded_with_special_tokens": str(
                    generated.get("raw_decoded_with_special_tokens", assistant_text)
                ),
                "token_ids": [int(value) for value in generated.get("token_ids", ())],
                "usage": dict(generated.get("usage", {})),
                "elapsed_ms": float(generated.get("elapsed_ms", 0.0)),
                "input_ids_sha256": generated.get("input_ids_sha256"),
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
                "valid_syntax": bool(result.get("valid_syntax", True)),
                "environment_accepted": bool(
                    result.get("environment_accepted", validation["environment_accepted"])
                ),
                "failure_type": result.get("failure_type"),
                "page_type": _page_type(before["available_actions"], done=bool(before["done"])),
            }
            steps.append(step)
            audit.update(
                {
                    "parse_status": "VALID_TOOL_CALL",
                    "parsed_payload": parsed.payload,
                    "action": parsed.action,
                    "environment_step_executed": True,
                    "environment_accepted": step["environment_accepted"],
                    "post_state_sha256": content_sha256(
                        {
                            "observation": step["post_observation"],
                            "available_actions": step["post_available_actions"],
                            "raw_reward": step["raw_reward"],
                            "done": step["done"],
                        }
                    ),
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
        final = {
            "raw_reward": float(runtime.raw_reward),
            "done": bool(runtime.done),
            "observation": runtime.observation,
            "available_actions": runtime.available_actions(),
        }
    except Exception as exc:  # noqa: BLE001 - task artifacts must retain typed failures
        initial = locals().get("initial", {})
        final = {
            "raw_reward": float(getattr(runtime, "raw_reward", 0.0)),
            "done": bool(getattr(runtime, "done", False)),
            "observation": str(getattr(runtime, "observation", "")),
            "available_actions": dict(runtime.available_actions()),
        }
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        runtime.close()
    elapsed = time.perf_counter() - started
    generated_exact_success = error is None and final["raw_reward"] == 1.0
    replay: Mapping[str, Any] = {
        "status": ReplayStatus.NOT_REPLAYED.value,
        "mismatch": None,
        "steps_replayed": 0,
    }
    if generated_exact_success and steps:
        try:
            replay = replay_generated_steps(
                adapter=adapter,
                task=task,
                initial=initial,
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001 - replay failures are typed evidence
            replay = {
                "status": ReplayStatus.FAILED.value,
                "mismatch": {
                    "type": "WEBSHOP_REPLAY_EXCEPTION",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                },
                "steps_replayed": 0,
            }
    admitted = bool(
        admissible
        and generated_exact_success
        and steps
        and replay["status"] == ReplayStatus.VALIDATED.value
    )
    trajectory = None
    if admitted:
        trajectory = _admitted_trajectory(
            task=task,
            steps=steps,
            rounds=rounds,
            source_identity=source_identity,
            environment_identity=environment_identity,
            generator_identity=generator.identity,
            replay=replay,
        )
    terminal = (
        TerminalStatus.ERROR
        if error is not None
        else _terminal_status(
            reward=float(final["raw_reward"]),
            done=bool(final["done"]),
            exhausted=len(rounds) >= MAX_ROUNDS,
        )
    )
    payload = {
        "format": RAW_TASK_FORMAT,
        "task_id": task.task_id,
        "index": int(task.metadata["index"]),
        "split": "train",
        "instruction": task.instruction,
        "instruction_sha256": text_sha256(task.instruction),
        "source_identity": dict(source_identity),
        "source_identity_sha256": content_sha256(source_identity),
        "environment_identity_sha256": content_sha256(environment_identity),
        "generator_identity": dict(generator.identity),
        "admission_enabled": bool(admissible),
        "rounds": rounds,
        "environment_steps": steps,
        "model_round_count": len(rounds),
        "environment_step_count": len(steps),
        "initial_state_sha256": content_sha256(initial),
        "sequence_sha256": content_sha256(_sequence_payload(steps)),
        "final": final,
        "terminal_status": terminal.value,
        "generated_exact_1_success": generated_exact_success,
        "replay": replay,
        "admitted": admitted,
        "admitted_trajectory": trajectory.as_dict() if trajectory else None,
        "error": error,
        "elapsed_seconds": elapsed,
        "standard200_outcome_inspected": False,
        "validation_outcome_used_for_selection": False,
    }
    payload["task_artifact_sha256"] = content_sha256(payload)
    return payload


def _validate_existing_task(
    path: Path,
    *,
    task: TaskRecord,
    source_identity: Mapping[str, Any],
    admissible: bool,
) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("format") != RAW_TASK_FORMAT
        or payload.get("task_id") != task.task_id
        or payload.get("source_identity_sha256") != content_sha256(source_identity)
        or payload.get("admission_enabled") != bool(admissible)
    ):
        raise RuntimeError(f"existing task artifact identity mismatch: {path}")
    body = dict(payload)
    recorded = body.pop("task_artifact_sha256", None)
    if recorded != content_sha256(body):
        raise RuntimeError(f"existing task artifact hash mismatch: {path}")
    return payload


def run_construction_block(
    *,
    adapter: WebShopPortableAdapterV2,
    tasks: Sequence[TaskRecord],
    generator: ToolGenerator,
    source_identity: Mapping[str, Any],
    environment_identity: Mapping[str, Any],
    output_root: str | Path,
    max_new_tokens: int,
    admissible: bool,
    max_consecutive_errors: int = 3,
) -> Mapping[str, Any]:
    root = Path(output_root).resolve()
    task_root = root / "raw_tasks"
    task_root.mkdir(parents=True, exist_ok=True)
    ordered = sorted(tasks, key=lambda row: int(row.metadata["index"]))
    if len({task.task_id for task in ordered}) != len(ordered):
        raise ValueError("construction block contains duplicate task IDs")
    rows: list[Mapping[str, Any]] = []
    consecutive_errors = 0
    started = time.perf_counter()
    for task in ordered:
        path = task_root / f"task_{int(task.metadata['index']):05d}.json"
        if path.exists():
            row = _validate_existing_task(
                path,
                task=task,
                source_identity=source_identity,
                admissible=admissible,
            )
        else:
            row = run_construction_task(
                adapter=adapter,
                task=task,
                generator=generator,
                source_identity=source_identity,
                environment_identity=environment_identity,
                max_new_tokens=max_new_tokens,
                admissible=admissible,
            )
            atomic_write_json(path, row)
        rows.append(row)
        consecutive_errors = consecutive_errors + 1 if row["error"] else 0
        if consecutive_errors >= max_consecutive_errors:
            raise RuntimeError("construction block reached the consecutive error cap")
    admitted_rows = []
    for row in rows:
        trajectory = row.get("admitted_trajectory")
        if trajectory is not None:
            admitted_rows.append({**trajectory, "split": "train"})
    corpus_path = root / "admitted_trajectories.jsonl"
    corpus_text = "".join(
        json.dumps(to_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in admitted_rows
    )
    if corpus_path.exists() and corpus_path.read_text(encoding="utf-8") != corpus_text:
        raise RuntimeError("existing admitted corpus differs from reconstructed task artifacts")
    if not corpus_path.exists():
        atomic_write_text(corpus_path, corpus_text)
    elapsed = time.perf_counter() - started
    summary = {
        "format": BLOCK_SUMMARY_FORMAT,
        "task_count": len(rows),
        "index_start": int(ordered[0].metadata["index"]) if ordered else None,
        "index_end": int(ordered[-1].metadata["index"]) + 1 if ordered else None,
        "admission_enabled": bool(admissible),
        "admitted_count": len(admitted_rows),
        "generated_exact_1_count": sum(bool(row["generated_exact_1_success"]) for row in rows),
        "replay_validated_count": sum(
            row["replay"]["status"] == ReplayStatus.VALIDATED.value for row in rows
        ),
        "error_count": sum(row["error"] is not None for row in rows),
        "model_round_count": sum(int(row["model_round_count"]) for row in rows),
        "environment_step_count": sum(int(row["environment_step_count"]) for row in rows),
        "search_transition_count": sum(
            str(step["action"]).startswith("search[")
            for row in rows
            for step in row["environment_steps"]
        ),
        "click_transition_count": sum(
            str(step["action"]).startswith("click[")
            for row in rows
            for step in row["environment_steps"]
        ),
        "invalid_tool_round_count": sum(
            round_row["parse_status"] == "INVALID_TOOL_CALL"
            for row in rows
            for round_row in row["rounds"]
        ),
        "environment_noop_count": sum(
            not bool(step["environment_accepted"])
            for row in rows
            for step in row["environment_steps"]
        ),
        "elapsed_seconds": elapsed,
        "source_identity_sha256": content_sha256(source_identity),
        "environment_identity_sha256": content_sha256(environment_identity),
        "generator_identity": dict(generator.identity),
        "task_artifact_sha256s": [row["task_artifact_sha256"] for row in rows],
        "corpus_sha256": sha256_file(corpus_path),
        "standard200_outcome_inspected": False,
        "scientific_evaluation": False,
    }
    summary["summary_sha256"] = content_sha256(summary)
    atomic_write_json(root / "summary.json", summary)
    return summary


def merge_construction_blocks(
    *,
    block_roots: Sequence[str | Path],
    source_identity: Mapping[str, Any],
    construction_config: Mapping[str, Any],
    output_root: str | Path,
) -> Mapping[str, Any]:
    if not block_roots:
        raise ValueError("at least one construction block is required")
    if construction_config.get("format") != ("rcmf_agentbench_fc_webshop_construction_config_v1"):
        raise ValueError("unexpected WebShop construction config format")
    population = construction_config["construction_population"]
    coverage = construction_config["coverage_gate"]
    population_start = int(population["index_start"])
    population_end = int(population["index_end"])
    block_size = int(population["block_size"])
    summaries = []
    for value in block_roots:
        root = Path(value).resolve(strict=True)
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        body = dict(summary)
        recorded = body.pop("summary_sha256", None)
        if recorded != content_sha256(body):
            raise RuntimeError(f"construction block summary hash differs: {summary_path}")
        corpus_path = root / "admitted_trajectories.jsonl"
        if summary.get("corpus_sha256") != sha256_file(corpus_path):
            raise RuntimeError(f"construction block corpus hash differs: {corpus_path}")
        if (
            summary.get("format") != BLOCK_SUMMARY_FORMAT
            or not bool(summary.get("admission_enabled"))
            or bool(summary.get("standard200_outcome_inspected"))
            or summary.get("source_identity_sha256") != content_sha256(source_identity)
        ):
            raise RuntimeError(f"construction block identity differs: {summary_path}")
        summaries.append((summary, corpus_path, root))
    summaries.sort(key=lambda item: int(item[0]["index_start"]))
    expected_start = population_start
    for summary, _corpus_path, _root in summaries:
        start = int(summary["index_start"])
        end = int(summary["index_end"])
        if (
            start != expected_start
            or end <= start
            or end > population_end
            or (end - start) % block_size != 0
        ):
            raise RuntimeError("construction blocks are not contiguous frozen increments")
        expected_start = end
    trajectories: list[Mapping[str, Any]] = []
    seen_tasks: set[str] = set()
    for summary, corpus_path, _root in summaries:
        rows = [
            json.loads(line)
            for line in corpus_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(rows) != int(summary["admitted_count"]):
            raise RuntimeError("construction block admitted count differs from its corpus")
        for row in rows:
            if row.get("split") != "train":
                raise RuntimeError("merged WebShop trajectory is not train-only")
            trajectory = TrajectoryRecord.from_dict(row)
            if dict(trajectory.source_identity) != dict(source_identity):
                raise RuntimeError("merged WebShop trajectory source identity differs")
            if trajectory.task_id in seen_tasks:
                raise RuntimeError("merged WebShop corpus contains a duplicate task")
            seen_tasks.add(trajectory.task_id)
            trajectories.append(row)
    trajectories.sort(key=lambda row: int(row["metadata"]["index"]))
    transition_count = sum(len(row["steps"]) for row in trajectories)
    search_count = sum(
        str(step["action"]).startswith("search[") for row in trajectories for step in row["steps"]
    )
    click_count = sum(
        str(step["action"]).startswith("click[") for row in trajectories for step in row["steps"]
    )
    initial_complete = expected_start >= int(population["initial_index_end"])
    criteria = {
        "successful_trajectories": len(trajectories)
        >= int(coverage["minimum_successful_trajectories"]),
        "total_transitions": transition_count >= int(coverage["minimum_total_transitions"]),
        "search_transitions": search_count >= int(coverage["minimum_search_transitions"]),
        "click_transitions": click_count >= int(coverage["minimum_click_transitions"]),
    }
    coverage_met = initial_complete and all(criteria.values())
    next_block = None
    if not coverage_met and expected_start < population_end:
        next_block = {
            "index_start": expected_start,
            "index_end": min(expected_start + block_size, population_end),
        }
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    corpus_path = root / "successful_trajectories.jsonl"
    corpus_text = "".join(
        json.dumps(to_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in trajectories
    )
    if corpus_path.exists() and corpus_path.read_text(encoding="utf-8") != corpus_text:
        raise RuntimeError("existing merged construction corpus differs")
    if not corpus_path.exists():
        atomic_write_text(corpus_path, corpus_text)
    manifest = {
        "format": "rcmf_agentbench_fc_webshop_corpus_manifest_v1",
        "source_identity": dict(source_identity),
        "source_identity_sha256": content_sha256(source_identity),
        "processed_index_start": population_start,
        "processed_index_end": expected_start,
        "processed_task_count": sum(int(item[0]["task_count"]) for item in summaries),
        "successful_trajectory_count": len(trajectories),
        "transition_count": transition_count,
        "search_transition_count": search_count,
        "click_transition_count": click_count,
        "initial_population_complete": initial_complete,
        "coverage_criteria": criteria,
        "coverage_met": coverage_met,
        "next_block": next_block,
        "block_summary_sha256s": [item[0]["summary_sha256"] for item in summaries],
        "block_roots": [str(item[2]) for item in summaries],
        "corpus_sha256": sha256_file(corpus_path),
        "standard200_outcome_inspected": False,
        "validation_outcome_used_for_selection": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    atomic_write_json(root / "corpus_manifest.json", manifest)
    return manifest


class HFQwenToolGenerator:
    def __init__(self, model_snapshot: str | Path, *, dtype: str = "bfloat16") -> None:
        import os

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if os.environ.get("PYTHONHASHSEED") != "25101":
            raise RuntimeError("PYTHONHASHSEED must be fixed to 25101 at process start")
        if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != ":4096:8":
            raise RuntimeError("CUBLAS_WORKSPACE_CONFIG must be fixed to :4096:8")
        snapshot = Path(model_snapshot).resolve(strict=True)
        dtype_value = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }.get(dtype)
        if dtype_value is None:
            raise ValueError("dtype must be bfloat16, float16, or float32")
        torch.manual_seed(25101)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(25101)
        torch.use_deterministic_algorithms(True)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            snapshot, trust_remote_code=True, local_files_only=True
        )
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        self.model = AutoModelForCausalLM.from_pretrained(
            snapshot,
            torch_dtype=dtype_value,
            trust_remote_code=True,
            local_files_only=True,
            low_cpu_mem_usage=True,
        )
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(device)
        self.model.eval()
        self.identity = {
            "model": "Qwen/Qwen3-8B",
            "model_snapshot": str(snapshot),
            "model_snapshot_commit": snapshot.name,
            "dtype": dtype,
            "device": str(device),
            "generation_seed": 25101,
            "enable_thinking": False,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "tool_rendering": "qwen_apply_chat_template_tools_v1",
        }

    def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        *,
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
            input_ids = self.torch.tensor([input_ids], dtype=self.torch.long)
        if input_ids.ndim == 1:
            input_ids = input_ids.unsqueeze(0)
        input_ids = input_ids.to(next(self.model.parameters()).device)
        attention_mask = self.torch.ones_like(input_ids)
        started = time.perf_counter()
        with self.torch.inference_mode():
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
            "input_ids_sha256": content_sha256(input_ids[0].tolist()),
        }
