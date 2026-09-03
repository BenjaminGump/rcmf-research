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
from rcmf.pipeline.manifests import stage_identity_payload
from rcmf.utils.serialization import sha256_file
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


def _verified_stage_identity(
    args: argparse.Namespace, config: dict[str, object]
) -> dict[str, str]:
    pipeline = dict(config["pipeline"])  # type: ignore[arg-type]
    actual_config_sha = sha256_file(args.config)
    configured_run_uuid = str(pipeline["run_uuid"])
    configured_run_root = Path(
        str(dict(pipeline["roots"])["run_root"])  # type: ignore[arg-type]
    ).resolve(strict=False)
    actual_run_root = args.run_root.resolve(strict=False)
    expected = {
        "run_uuid": os.environ.get("RCMF_PIPELINE_RUN_UUID"),
        "run_root": os.environ.get("RCMF_PIPELINE_RUN_ROOT"),
        "pipeline_config_sha256": os.environ.get(
            "RCMF_PIPELINE_CONFIG_SHA256"
        ),
        "contract_sha256": os.environ.get(
            "RCMF_PIPELINE_CONTRACT_SHA256"
        ),
    }
    strict = bool(pipeline.get("strict_stage_identity", False)) or any(
        value is not None for value in expected.values()
    )
    if strict:
        missing = [key for key, value in expected.items() if not value]
        if missing:
            raise PermissionError(
                f"Formal scheduler identity is incomplete: {missing}"
            )
    if expected["pipeline_config_sha256"] and (
        actual_config_sha != expected["pipeline_config_sha256"]
    ):
        raise PermissionError("Stage config SHA differs from scheduler contract")
    if expected["run_uuid"] and configured_run_uuid != expected["run_uuid"]:
        raise PermissionError("Stage run UUID differs from scheduler contract")
    if expected["run_root"] and actual_run_root != Path(
        expected["run_root"]
    ).resolve(strict=False):
        raise PermissionError("Stage run root differs from scheduler contract")
    if strict and configured_run_root != actual_run_root:
        raise PermissionError("Configured stage run root differs from actual root")
    return stage_identity_payload(
        source_commit=args.source_commit,
        run_uuid=str(expected["run_uuid"] or configured_run_uuid),
        run_root=actual_run_root,
        pipeline_config_sha256=str(
            expected["pipeline_config_sha256"] or actual_config_sha
        ),
        contract_sha256=str(expected["contract_sha256"] or ""),
        stage_id=args.stage,
        attempt_id=str(
            os.environ.get("RCMF_PIPELINE_ATTEMPT_ID", "manual")
        ),
        require_complete=strict,
    )


def _failure_payload(
    identity: dict[str, str],
    exc: BaseException,
    *,
    recoverable: bool,
) -> dict[str, object]:
    return {
        "format": "rcmf_reproducible_stage_failure_14b_v1",
        **identity,
        "classification": (
            "recoverable_infrastructure" if recoverable else "fatal"
        ),
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    started_utc = datetime.now(timezone.utc).isoformat()
    config = load_resolved(args.config)
    identity = _verified_stage_identity(args, config)
    stage_dir = args.run_root / "stages" / args.stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    arm_id = _arm_from_stage(args.stage)
    prompt_profile = None
    if arm_id:
        prompt_profile = str(config["arms"][arm_id]["task_conditioned_prompt_profile"])
    try:
        if not (args.run_root / "runtime_layout.json").exists():
            initialize_runtime_layout(config, args.run_root)
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
            _failure_payload(identity, exc, recoverable=recoverable),
        )
        raise SystemExit(75 if recoverable else 65)
    manifest = write_stage_manifest(
        stage_id=args.stage,
        stage_dir=stage_dir,
        stage_identity=identity,
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
