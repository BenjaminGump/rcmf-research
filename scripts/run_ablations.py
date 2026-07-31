from __future__ import annotations

import argparse
import itertools
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

import yaml

from rcmf.config import load_config, set_by_dotted_key
from rcmf.utils.serialization import atomic_write_json, maybe_git_commit


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an ablation job matrix.")
    parser.add_argument("--config", default="configs/ablation/mvp.yaml")
    parser.add_argument("--output", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    sweep: dict[str, list[Any]] = cfg.raw.get("sweep", {})
    keys = list(sweep)
    jobs = []
    for values in itertools.product(*(sweep[key] for key in keys)):
        overrides: dict[str, Any] = {}
        for key, value in zip(keys, values):
            set_by_dotted_key(overrides, key, value)
        jobs.append({"overrides": overrides})
    output = Path(args.output or Path(cfg.experiment.output_dir) / "ablations" / "jobs.json")
    atomic_write_json(
        output,
        {
            "config": cfg.to_dict(),
            "jobs": jobs,
            "dry_run": args.dry_run,
            "git_commit": maybe_git_commit(),
        },
    )
    print(f"Wrote {len(jobs)} ablation jobs to {output}")
    if args.dry_run:
        print(yaml.safe_dump({"jobs": jobs[:5], "total": len(jobs)}, sort_keys=False))


if __name__ == "__main__":
    main()
