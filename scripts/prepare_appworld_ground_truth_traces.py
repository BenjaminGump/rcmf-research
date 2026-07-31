from __future__ import annotations

import argparse
import ast
from pathlib import Path
from pprint import pformat
from typing import Any

try:
    import _bootstrap  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    from scripts import _bootstrap  # type: ignore  # noqa: F401

from rcmf.benchmarks.appworld.prompt import build_task_message, get_system_prompt
from rcmf.benchmarks.appworld.traces import (
    AppWorldTrace,
    AppWorldTraceStep,
    decision_examples_from_trace,
    memory_record_from_trace,
)
from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import save_decision_examples, save_memory_records
from rcmf.utils.serialization import atomic_write_json


ALLOWED_METHODS = {"get", "post", "put", "patch", "delete"}


def request_call_to_code(call: dict[str, Any]) -> str:
    method = str(call.get("method", "")).lower()
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Unsupported request method: {method}")
    url = str(call.get("url", ""))
    if not url.startswith("/"):
        raise ValueError(f"Expected AppWorld request URL to start with '/': {url}")
    data = call.get("data") or {}
    if not isinstance(data, dict):
        raise TypeError(f"Expected request data to be a dict, got {type(data).__name__}")
    data_literal = pformat(data, width=120, sort_dicts=True)
    return f"print(requester.{method}({url!r}, data={data_literal}))"


def code_to_response(code: str) -> str:
    return f"```python\n{code.strip()}\n```"


def split_top_level_statements(code: str) -> list[str]:
    tree = ast.parse(code)
    lines = code.splitlines()
    chunks: list[str] = []
    cursor = 0
    for statement in tree.body:
        start = statement.lineno - 1
        while start > cursor and (
            not lines[start - 1].strip() or lines[start - 1].lstrip().startswith("#")
        ):
            start -= 1
        end = getattr(statement, "end_lineno", None)
        if end is None:
            raise ValueError("Python AST statement is missing end_lineno")
        if isinstance(statement, ast.Return):
            if statement.value is None:
                chunk = "pass"
            else:
                chunk = f"print({ast.unparse(statement.value)})"
        else:
            chunk = "\n".join(lines[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        cursor = end
    return chunks


def ground_truth_to_code_steps(ground_truth: Any, source: str) -> list[str]:
    if source == "compiled_solution":
        body = str(ground_truth.compiled_solution_code_body or "").strip()
        if not body:
            raise ValueError("Ground truth has no compiled_solution_code_body")
        return split_top_level_statements(body)
    if source == "api_calls":
        return [request_call_to_code(call) for call in list(ground_truth.api_calls or [])]
    raise ValueError(f"Unknown replay source: {source}")


def supervisor_payload(supervisor: Any) -> dict[str, str]:
    return {
        "first_name": str(getattr(supervisor, "first_name", "")),
        "last_name": str(getattr(supervisor, "last_name", "")),
        "email": str(getattr(supervisor, "email", "")),
        "phone_number": str(getattr(supervisor, "phone_number", "")),
    }


def build_trace_for_task(
    task_id: str,
    *,
    experiment_name: str,
    system_prompt: str,
    replay_source: str = "compiled_solution",
    max_calls_per_task: int | None = None,
) -> tuple[AppWorldTrace, dict[str, Any]]:
    from appworld import AppWorld

    steps: list[AppWorldTraceStep] = []
    metadata: dict[str, Any] = {"task_id": task_id, "errors": []}
    with AppWorld(
        task_id=task_id,
        experiment_name=experiment_name,
        load_ground_truth=True,
        ground_truth_mode="full",
    ) as world:
        ground_truth = world.task.ground_truth
        if ground_truth is None:
            raise ValueError(f"Task {task_id} has no ground truth")
        code_steps = ground_truth_to_code_steps(ground_truth, replay_source)
        if max_calls_per_task is not None:
            code_steps = code_steps[:max_calls_per_task]
        query = build_task_message(world.task.instruction, supervisor_payload(world.task.supervisor))
        for index, code in enumerate(code_steps, start=1):
            observation = world.execute(code)
            steps.append(
                AppWorldTraceStep(
                    index=index,
                    response=code_to_response(code),
                    observation=str(observation).rstrip(),
                )
            )
        completed = bool(world.task_completed())
        try:
            evaluation = world.evaluate(suppress_errors=True).to_dict(stats_only=True)
        except Exception as exc:
            evaluation = {"success": False, "error": str(exc)}
            metadata["errors"].append(f"evaluation_error: {exc}")
        is_correct = completed and bool(evaluation.get("success", False))
        metadata.update(
            {
                "completed": completed,
                "evaluation": evaluation,
                "replay_source": replay_source,
                "num_code_steps": len(code_steps),
                "ground_truth_api_calls": len(ground_truth.api_calls or []),
                "compiled_solution_statements": len(split_top_level_statements(ground_truth.compiled_solution_code_body)),
                "answer_type": type(ground_truth.answer).__name__,
            }
        )
        trace = AppWorldTrace(
            task_id=task_id,
            query=query,
            steps=steps,
            is_correct=is_correct,
            system_prompt=system_prompt,
            final_answer=str(ground_truth.answer),
            source_path=f"appworld_ground_truth:{task_id}",
        )
        return trace, metadata


def trace_to_payload(trace: AppWorldTrace, metadata: dict[str, Any]) -> dict[str, Any]:
    items = [f"System Prompt: {trace.system_prompt}", f"Query: {trace.query}"]
    for step in trace.steps:
        items.append(f"Step {step.index} - Response: {step.response}")
        items.append(f"Step {step.index} - Observation: {step.observation}")
    items.append(f"Final Answer: {trace.final_answer}")
    return {
        "task_id": trace.task_id,
        "is_correct": trace.is_correct,
        "system_prompt": trace.system_prompt,
        "trace": items,
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug helper for replaying AppWorld train/dev ground-truth artifacts. "
            "For official downloaded agent trajectories, use prepare_appworld_official_traces.py."
        )
    )
    parser.add_argument("--config", default="configs/benchmark/appworld_mvp_experiment.yaml")
    parser.add_argument("--split", default="train", help="Logical split name from the RCMF config.")
    parser.add_argument("--dataset-name", default=None, help="Raw AppWorld dataset name override.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--experiment-name", default="rcmf_ground_truth_replay")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-calls-per-task", type=int, default=None)
    parser.add_argument(
        "--replay-source",
        choices=["compiled_solution", "api_calls"],
        default="api_calls",
        help="Replay the official api_calls.json trace. compiled_solution is available only with an explicit guard flag.",
    )
    parser.add_argument(
        "--allow-compiled-solution-replay",
        action="store_true",
        help="Required when --replay-source=compiled_solution; this source is not an official step trajectory.",
    )
    parser.add_argument("--include-incomplete", action="store_true")
    parser.add_argument("--save-raw-traces", action="store_true")
    args = parser.parse_args()
    if args.replay_source == "compiled_solution" and not args.allow_compiled_solution_replay:
        parser.error(
            "--replay-source=compiled_solution is disabled by default because it synthesizes steps "
            "from solution code instead of using official trajectory logs. Pass "
            "--allow-compiled-solution-replay only for diagnostics."
        )

    from appworld import load_task_ids

    cfg = load_config(args.config)
    dataset_name = args.dataset_name or cfg.benchmark.splits.get(args.split, args.split)
    task_ids = load_task_ids(dataset_name=dataset_name)
    if args.start_index:
        task_ids = task_ids[args.start_index :]
    if args.limit is not None:
        task_ids = task_ids[: args.limit]

    output_dir = Path(args.output)
    raw_trace_dir = output_dir / "raw_traces"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.save_raw_traces:
        raw_trace_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = get_system_prompt(cfg.benchmark.prompt_profile)
    records = []
    examples = []
    used = []
    skipped = []
    for offset, task_id in enumerate(task_ids, start=args.start_index):
        try:
            trace, metadata = build_trace_for_task(
                task_id,
                experiment_name=args.experiment_name,
                system_prompt=system_prompt,
                replay_source=args.replay_source,
                max_calls_per_task=args.max_calls_per_task,
            )
        except Exception as exc:
            skipped.append({"task_id": task_id, "index": offset, "reason": str(exc)})
            print(f"skip {task_id}: {exc}", flush=True)
            continue
        if args.save_raw_traces:
            atomic_write_json(raw_trace_dir / f"{task_id}.json", trace_to_payload(trace, metadata))
        if not trace.is_correct and not args.include_incomplete:
            skipped.append(
                {
                    "task_id": task_id,
                    "index": offset,
                    "reason": "incomplete_or_failed_replay",
                    "metadata": metadata,
                }
            )
            print(f"skip {task_id}: replay did not pass evaluation", flush=True)
            continue
        records.append(memory_record_from_trace(trace))
        examples.extend(decision_examples_from_trace(trace))
        used.append(
            {
                "task_id": task_id,
                "index": offset,
                "steps": len(trace.steps),
                "is_correct": trace.is_correct,
            }
        )
        print(
            f"used {task_id}: steps={len(trace.steps)} examples={len(examples)}",
            flush=True,
        )

    save_memory_records(output_dir / "memory_records.jsonl", records)
    save_decision_examples(output_dir / "decision_examples.jsonl", examples)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    atomic_write_json(
        output_dir / "summary.json",
        {
            "split": args.split,
            "dataset_name": dataset_name,
            "task_ids_requested": len(task_ids),
            "used_tasks": len(used),
            "skipped_tasks": len(skipped),
            "records": len(records),
            "examples": len(examples),
            "include_incomplete": args.include_incomplete,
            "max_calls_per_task": args.max_calls_per_task,
            "replay_source": args.replay_source,
            "used": used,
            "skipped": skipped,
        },
    )
    print(
        f"Prepared {len(records)} records and {len(examples)} per-step examples "
        f"from {len(used)} AppWorld {dataset_name} tasks in {output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
