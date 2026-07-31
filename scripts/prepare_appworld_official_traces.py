from __future__ import annotations

import argparse
import re
from pathlib import Path
from statistics import mean, median
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.prompt import build_task_message, get_system_prompt
from rcmf.benchmarks.appworld.traces import (
    AppWorldTrace,
    decision_examples_from_trace,
    memory_record_from_trace,
    parse_environment_io_markdown,
)
from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import save_decision_examples, save_memory_records
from rcmf.utils.serialization import atomic_write_json


FAILED_RE = re.compile(r"^Num Failed Tests\s*:\s*(?P<count>\d+)\s*$", re.MULTILINE)
PASSED_RE = re.compile(r"^Num Passed Tests\s*:\s*(?P<count>\d+)\s*$", re.MULTILINE)


def supervisor_payload(supervisor: Any) -> dict[str, str]:
    return {
        "first_name": str(getattr(supervisor, "first_name", "")),
        "last_name": str(getattr(supervisor, "last_name", "")),
        "email": str(getattr(supervisor, "email", "")),
        "phone_number": str(getattr(supervisor, "phone_number", "")),
    }


def load_task_query(task_id: str, prompt_profile: str) -> str:
    from appworld.task import Task

    task = Task.load(
        task_id,
        storage_type="memory",
        load_ground_truth=False,
        include_api_response_schemas=False,
    )
    try:
        return build_task_message(
            task.instruction,
            supervisor_payload(task.supervisor),
            profile=prompt_profile,
        )
    finally:
        task.close()


def report_stats(report_path: Path) -> dict[str, int | bool]:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    failed_match = FAILED_RE.search(text)
    passed_match = PASSED_RE.search(text)
    if failed_match is None or passed_match is None:
        raise ValueError(f"Could not parse AppWorld evaluation report: {report_path}")
    failed = int(failed_match.group("count"))
    passed = int(passed_match.group("count"))
    return {"passed_tests": passed, "failed_tests": failed, "success": failed == 0}


def infer_dataset_name(experiment_output: Path) -> str | None:
    name = experiment_output.name
    return name if name in {"train", "dev", "test_normal", "test_challenge"} else None


def iter_task_dirs(experiment_output: Path, dataset_name: str | None) -> list[Path]:
    tasks_dir = experiment_output / "tasks"
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Missing tasks directory: {tasks_dir}")
    task_dirs = {path.name: path for path in tasks_dir.iterdir() if path.is_dir()}
    if dataset_name is None:
        return [task_dirs[name] for name in sorted(task_dirs)]

    from appworld import load_task_ids

    ordered_task_ids = load_task_ids(dataset_name=dataset_name)
    missing = [task_id for task_id in ordered_task_ids if task_id not in task_dirs]
    if missing:
        raise ValueError(
            f"{len(missing)} {dataset_name} task(s) are missing from {experiment_output}; "
            f"first missing task: {missing[0]}"
        )
    return [task_dirs[task_id] for task_id in ordered_task_ids]


def build_trace_from_task_dir(
    task_dir: Path,
    system_prompt: str,
    prompt_profile: str,
) -> tuple[AppWorldTrace, dict[str, Any]]:
    task_id = task_dir.name
    environment_io_path = task_dir / "logs" / "environment_io.md"
    report_path = task_dir / "evaluation" / "report.md"
    if not environment_io_path.exists():
        raise FileNotFoundError(environment_io_path)
    if not report_path.exists():
        raise FileNotFoundError(report_path)

    stats = report_stats(report_path)
    steps = parse_environment_io_markdown(
        environment_io_path.read_text(encoding="utf-8", errors="replace"),
        source_path=str(environment_io_path),
    )
    trace = AppWorldTrace(
        task_id=task_id,
        query=load_task_query(task_id, prompt_profile=prompt_profile),
        steps=steps,
        is_correct=bool(stats["success"]),
        system_prompt=system_prompt,
        final_answer="",
        source_path=str(environment_io_path),
        source_kind="official_appworld_experiment_output",
    )
    metadata = {
        "task_id": task_id,
        "source_path": str(environment_io_path),
        "report_path": str(report_path),
        "num_steps": len(steps),
        **stats,
    }
    return trace, metadata


def summarize_steps(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "median": None, "total": 0}
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(mean(values), 3),
        "median": median(values),
        "total": sum(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare AppWorld per-step training data from official downloaded experiment outputs."
    )
    parser.add_argument("--config", default="configs/benchmark/appworld_mvp_experiment.yaml")
    parser.add_argument(
        "--experiment-output",
        required=True,
        help="Path like experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train.",
    )
    parser.add_argument("--dataset-name", default=None, help="Optional AppWorld split name for canonical order.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Include official trajectories whose per-task evaluation report has failed tests.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    experiment_output = Path(args.experiment_output)
    dataset_name = args.dataset_name or infer_dataset_name(experiment_output)
    task_dirs = iter_task_dirs(experiment_output, dataset_name)
    if args.start_index:
        task_dirs = task_dirs[args.start_index :]
    if args.limit is not None:
        task_dirs = task_dirs[: args.limit]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_profile = cfg.benchmark.prompt_profile
    system_prompt = get_system_prompt(prompt_profile)

    records = []
    examples = []
    used = []
    skipped = []
    candidate_step_counts = []
    used_step_counts = []
    for offset, task_dir in enumerate(task_dirs, start=args.start_index):
        try:
            trace, metadata = build_trace_from_task_dir(
                task_dir,
                system_prompt=system_prompt,
                prompt_profile=prompt_profile,
            )
        except Exception as exc:
            skipped.append({"task_id": task_dir.name, "index": offset, "reason": str(exc)})
            print(f"skip {task_dir.name}: {exc}", flush=True)
            continue
        candidate_step_counts.append(len(trace.steps))
        if not trace.is_correct and not args.include_failed:
            skipped.append(
                {
                    "task_id": trace.task_id,
                    "index": offset,
                    "reason": "failed_official_report",
                    **metadata,
                }
            )
            print(
                f"skip {trace.task_id}: failed_tests={metadata['failed_tests']} steps={len(trace.steps)}",
                flush=True,
            )
            continue
        records.append(memory_record_from_trace(trace))
        new_examples = decision_examples_from_trace(trace)
        examples.extend(new_examples)
        used_step_counts.append(len(trace.steps))
        used.append(
            {
                "task_id": trace.task_id,
                "index": offset,
                "steps": len(trace.steps),
                "examples": len(new_examples),
                "success": trace.is_correct,
                "passed_tests": metadata["passed_tests"],
                "failed_tests": metadata["failed_tests"],
            }
        )
        print(
            f"used {trace.task_id}: steps={len(trace.steps)} examples_total={len(examples)}",
            flush=True,
        )

    save_memory_records(output_dir / "memory_records.jsonl", records)
    save_decision_examples(output_dir / "decision_examples.jsonl", examples)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    atomic_write_json(
        output_dir / "summary.json",
        {
            "source": "official_appworld_experiment_output",
            "experiment_output": str(experiment_output),
            "dataset_name": dataset_name,
            "task_dirs_requested": len(task_dirs),
            "used_tasks": len(used),
            "skipped_tasks": len(skipped),
            "records": len(records),
            "examples": len(examples),
            "include_failed": args.include_failed,
            "candidate_step_stats": summarize_steps(candidate_step_counts),
            "used_step_stats": summarize_steps(used_step_counts),
            "used": used,
            "skipped": skipped,
        },
    )
    print(
        f"Prepared {len(records)} records and {len(examples)} per-step examples "
        f"from official AppWorld output {experiment_output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
