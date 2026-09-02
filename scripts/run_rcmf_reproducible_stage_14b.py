#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import traceback

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _arm_from_stage,
    execute_stage,
    initialize_runtime_layout,
    write_stage_manifest,
)
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def _recoverable_infrastructure_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {
        5,
        11,
        16,
        28,
        110,
        116,
    }:
        return True
    if isinstance(exc, __import__("subprocess").CalledProcessError):
        return int(exc.returncode) in {75, 137, 143}
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "connection reset",
            "temporarily unavailable",
            "stale file handle",
            "transport endpoint",
            "timed out",
        )
    )


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    started_utc = datetime.now(timezone.utc).isoformat()
    config = load_resolved(args.config)
    if not (args.run_root / "runtime_layout.json").exists():
        initialize_runtime_layout(config, args.run_root)
    stage_dir = args.run_root / "stages" / args.stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    arm_id = _arm_from_stage(args.stage)
    prompt_profile = None
    if arm_id:
        prompt_profile = str(config["arms"][arm_id]["task_conditioned_prompt_profile"])
    try:
        result = execute_stage(
            stage_id=args.stage,
            config=config,
            run_root=args.run_root,
            stage_dir=stage_dir,
            source_commit=args.source_commit,
            attempt_id=str(os.environ.get("RCMF_PIPELINE_ATTEMPT_ID", "manual")),
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        recoverable = _recoverable_infrastructure_error(exc)
        from rcmf.utils.serialization import atomic_write_json

        atomic_write_json(
            stage_dir / "failure.json",
            {
                "format": "rcmf_reproducible_stage_failure_14b_v1",
                "stage_id": args.stage,
                "attempt_id": str(
                    os.environ.get("RCMF_PIPELINE_ATTEMPT_ID", "manual")
                ),
                "source_commit": args.source_commit,
                "classification": (
                    "recoverable_infrastructure" if recoverable else "fatal"
                ),
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(75 if recoverable else 65)
    manifest = write_stage_manifest(
        stage_id=args.stage,
        stage_dir=stage_dir,
        source_commit=args.source_commit,
        arm=arm_id or ("shared" if args.stage.startswith("S") else "final"),
        prompt_profile=prompt_profile,
        result=result,
        command=sys.argv,
        started_utc=started_utc,
        elapsed_seconds=time.perf_counter() - started,
        run_root=args.run_root,
    )
    print(json.dumps({"stage": args.stage, "manifest": str(manifest)}, sort_keys=True))


if __name__ == "__main__":
    main()
