from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.datasets import load_memory_records
from rcmf.utils.serialization import atomic_write_json


SUPPORTED = {"no_memory", "full_context", "bm25", "fast_weight"}
EXTERNAL = {"dense_rag", "lora_memory", "awm", "ace", "amem", "mem0", "delta_mem"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or list controlled baselines.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--benchmark", default="appworld")
    parser.add_argument("--methods", default="no_memory,bm25,full_context,fast_weight")
    parser.add_argument("--memory-corpus", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    records = []
    if args.memory_corpus:
        records = load_memory_records(args.memory_corpus)
    plan = []
    for method in methods:
        status = "ready" if method in SUPPORTED else "requires_official_impl"
        if method in EXTERNAL and method not in SUPPORTED:
            status = "requires_official_impl"
        plan.append({"method": method, "status": status, "records": len(records)})
    output_dir = Path(cfg.experiment.output_dir) / "baselines"
    atomic_write_json(output_dir / "baseline_plan.json", {"benchmark": args.benchmark, "plan": plan})
    print({"benchmark": args.benchmark, "plan": plan})
    if not args.dry_run:
        print("Use scripts/evaluate.py with a concrete policy wiring for executable AppWorld runs.")


if __name__ == "__main__":
    main()
