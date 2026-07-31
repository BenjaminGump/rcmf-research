from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.eval.scaling import measure_read_scaling


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure RCMF read scaling.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--counts", default="0,10,100,1000")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    cfg = load_config(args.config)
    counts = [int(item) for item in args.counts.split(",") if item]
    output = Path(args.output or Path(cfg.experiment.output_dir) / "scaling" / "read_scaling.jsonl")
    rows = measure_read_scaling(
        counts=counts,
        rank=cfg.memory.rank,
        program_dim=cfg.memory.program_dim,
        output_csv_jsonl=output,
    )
    print(f"Wrote {len(rows)} scaling rows to {output}")


if __name__ == "__main__":
    main()
