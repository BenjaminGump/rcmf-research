from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.traces import (
    decision_examples_from_trace,
    memory_record_from_trace,
    parse_appworld_trace_payload,
)
from rcmf.config import load_config, save_resolved_config
from rcmf.benchmarks.appworld.prompt import get_system_prompt
from rcmf.training.datasets import save_decision_examples, save_memory_records
from rcmf.utils.serialization import atomic_write_json


def iter_trace_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare per-step AppWorld trajectory data.")
    parser.add_argument("--config", default="configs/benchmark/appworld_mvp_experiment.yaml")
    parser.add_argument("--input", nargs="+", required=True, help="Trace JSON file(s) or directories.")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--include-incorrect",
        action="store_true",
        help="Include incorrect traces. By default only is_correct=true traces are used.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    examples = []
    skipped = []
    used_files = []
    for trace_file in iter_trace_files(args.input):
        try:
            payload = json.loads(trace_file.read_text(encoding="utf-8"))
            trace = parse_appworld_trace_payload(payload, source_path=str(trace_file))
            if not trace.system_prompt:
                trace.system_prompt = get_system_prompt(cfg.benchmark.prompt_profile)
        except Exception as exc:
            skipped.append({"path": str(trace_file), "reason": str(exc)})
            continue
        if not trace.is_correct and not args.include_incorrect:
            skipped.append({"path": str(trace_file), "reason": "incorrect_trace"})
            continue
        records.append(memory_record_from_trace(trace))
        examples.extend(decision_examples_from_trace(trace))
        used_files.append(str(trace_file))

    save_memory_records(output_dir / "memory_records.jsonl", records)
    save_decision_examples(output_dir / "decision_examples.jsonl", examples)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    atomic_write_json(
        output_dir / "summary.json",
        {
            "input_files": len(iter_trace_files(args.input)),
            "used_files": len(used_files),
            "skipped_files": len(skipped),
            "records": len(records),
            "examples": len(examples),
            "include_incorrect": args.include_incorrect,
            "used": used_files,
            "skipped": skipped,
        },
    )
    print(
        f"Prepared {len(records)} memory records and {len(examples)} per-step examples "
        f"from {len(used_files)} traces in {output_dir}"
    )


if __name__ == "__main__":
    main()
