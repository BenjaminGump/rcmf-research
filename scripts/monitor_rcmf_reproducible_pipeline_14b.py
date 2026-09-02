#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import _bootstrap  # noqa: F401

from rcmf.pipeline.monitor import (
    MONITOR_INTERVAL_SECONDS,
    gpu_snapshot,
    read_health_snapshot,
    watchdog_capabilities,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        print(
            json.dumps(
                {
                    "capabilities": watchdog_capabilities(),
                    "health": read_health_snapshot(args.run_root),
                    "gpu": gpu_snapshot(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if args.once:
            return
        time.sleep(MONITOR_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
