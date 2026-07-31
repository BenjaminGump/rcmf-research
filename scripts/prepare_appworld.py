from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.adapter import AppWorldAdapter
from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import save_decision_examples, save_memory_records
from rcmf.utils.serialization import atomic_write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare AppWorld RCMF data.")
    parser.add_argument("--config", default="configs/benchmark/appworld.yaml")
    parser.add_argument("--split", default="train")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_dir = Path(args.output or cfg.raw.get("data", {}).get("output_dir", "runs/appworld/prepared"))
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = AppWorldAdapter(cfg)
    splits = adapter.load_splits(cfg)
    records = list(adapter.build_memory_records(args.split))
    examples = list(adapter.build_decision_examples(args.split))
    save_memory_records(output_dir / "memory_records.jsonl", records)
    save_decision_examples(output_dir / "decision_examples.jsonl", examples)
    atomic_write_json(
        output_dir / "splits.json",
        {"splits": splits, "prepared_split": args.split, "records": len(records), "examples": len(examples)},
    )
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    print(f"Prepared {len(records)} memory records and {len(examples)} examples in {output_dir}")


if __name__ == "__main__":
    main()
