from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from typing import Any

import yaml

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _joint_prepare,
    _joint_source_contract_preflight,
    _run,
    _runner_args,
    initialize_runtime_layout,
)
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.utils.serialization import atomic_write_json, sha256_file, write_jsonl
from scripts.prepare_rcmf_reproducible_pipeline_14b import rebuild_shared_cpu


FORMAT = "rcmf_exp037a_d08_repair_diagnostic_14c_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--failed-run-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--artifact-index", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _files(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        rows.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def _verify_published_index(index_path: Path, raw_root: Path) -> dict[str, Any]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if Path(str(payload["raw_run_root"])) != raw_root:
        raise ValueError("Published artifact index references a different failed run")
    mismatches = []
    for row in payload["items"]:
        path = raw_root / str(row["run_relative_path"])
        observed = None if not path.is_file() else sha256_file(path)
        if observed != str(row["sha256"]):
            mismatches.append(
                {
                    "relative_path": row["run_relative_path"],
                    "expected_sha256": row["sha256"],
                    "observed_sha256": observed,
                }
            )
    result = {
        "artifact_index": file_identity(index_path),
        "artifact_index_content_sha256": content_sha256(payload),
        "checked_count": len(payload["items"]),
        "mismatches": mismatches,
        "passed": not mismatches,
    }
    if not result["passed"]:
        raise RuntimeError(f"Failed-run artifact index changed: {mismatches[:3]}")
    return result


def _copy_fixture(source: Path, target: Path, source_stage: str) -> list[dict[str, Any]]:
    if not source.exists():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, copy_function=shutil.copy2)
        source_rows = _files(source)
        target_rows = _files(target)
        if source_rows != target_rows:
            raise RuntimeError(f"Copied diagnostic fixture differs: {source}")
        rows = source_rows
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        if sha256_file(source) != sha256_file(target):
            raise RuntimeError(f"Copied diagnostic fixture differs: {source}")
        rows = [
            {
                "relative_path": source.name,
                "size_bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        ]
    return [
        {
            **row,
            "source_root": str(source),
            "diagnostic_root": str(target),
            "source_stage": source_stage,
            "source_commit": "02ef94726ea0fe566f7eea4fa137fb91da92977f",
            "read_only_diagnostic_fixture": True,
            "scientific_input_for_future_run": False,
        }
        for row in rows
    ]


def _copy_d00_d07_fixtures(failed: Path, diagnostic: Path) -> dict[str, Any]:
    specifications = (
        (
            failed / "shared/representation_cache/multiview",
            diagnostic / "shared/representation_cache/multiview",
            "S05_transition_representations",
        ),
        (
            failed / "arms/3d/representation_cache/multiview",
            diagnostic / "arms/3d/representation_cache/multiview",
            "D00_selector_inputs",
        ),
        (failed / "arms/3d/selector", diagnostic / "arms/3d/selector", "D01-D04"),
        (
            failed / "arms/3d/paired_causal/paired_outcomes.json",
            diagnostic / "arms/3d/paired_causal/paired_outcomes.json",
            "D06_paired_causal",
        ),
        (
            failed / "arms/3d/structured_compiler/policy_teacher_cache.pt",
            diagnostic / "arms/3d/structured_compiler/policy_teacher_cache.pt",
            "D07_policy_teacher",
        ),
        (
            failed / "preflight/initialization_snapshots",
            diagnostic / "preflight/initialization_snapshots",
            "S07_initial_parameter_snapshots",
        ),
    )
    rows = []
    for source, target, stage in specifications:
        rows.extend(_copy_fixture(source, target, stage))
    manifest = {
        "format": "rcmf_exp037a_read_only_diagnostic_fixtures_14c_v1",
        "failed_run_root": str(failed),
        "diagnostic_root": str(diagnostic),
        "file_count": len(rows),
        "total_bytes": sum(int(row["size_bytes"]) for row in rows),
        "rows": rows,
        "rows_sha256": content_sha256(rows),
        "scientific_input_for_future_run": False,
    }
    atomic_write_json(diagnostic / "fixture_manifest.json", manifest)
    return manifest


def _resolved_diagnostic_config(source: Path, root: Path, source_commit: str) -> dict[str, Any]:
    config = copy.deepcopy(yaml.safe_load(source.read_text(encoding="utf-8")))
    pipeline = config["pipeline"]
    pipeline["run_uuid"] = root.name
    pipeline["roots"]["run_root"] = str(root)
    pipeline["working_branch"] = "research/v6-rcmf-reproducible-pipeline-d08-repair"
    pipeline["conditional_runtime_authorization"] = {
        "status": "DIAGNOSTIC_ONLY_NOT_SCIENTIFICALLY_AUTHORIZED",
        "granted_by_user": False,
        "source_commit": source_commit,
        "automatic_3d_launch": False,
        "automatic_conditional_1d_launch": False,
    }
    return config


def _run_d09_probe(root: Path, source_commit: str) -> dict[str, Any]:
    target = root / "arms/3d"
    config = root / "resolved_configs/arm_3d.yaml"
    command = _runner_args(
        "scripts/run_rcmf_joint_full_bank_9a.py",
        config=config,
        artifact_dir=target,
        attempt_id="exp037a-r1-d09-one-unit",
        source_commit=source_commit,
        parent_attempt_id="exp037a-r1-d08",
    )
    command.extend(["--phase", "train"])
    initial = root / "preflight/initialization_snapshots"
    environment = {
        "RCMF_TRAIN_STOP_AFTER_EPOCH": "1",
        "RCMF_DIAGNOSTIC_MAX_TRAINING_UNITS": "1",
        "RCMF_WRITER_INITIAL_PATH": str(initial / "writer_initial.pt"),
        "RCMF_READER_INITIAL_PATH": str(initial / "reader_initial.pt"),
    }
    started = time.perf_counter()
    _run(command, environment=environment)
    first_seconds = time.perf_counter() - started
    summary_path = target / "joint_training/checkpoints/diagnostic_one_unit_summary_14c.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    resume_command = list(command)
    resume_command[resume_command.index("exp037a-r1-d09-one-unit")] = (
        "exp037a-r1-d09-one-unit-resume"
    )
    resume_started = time.perf_counter()
    _run(resume_command, environment=environment)
    resume_seconds = time.perf_counter() - resume_started
    resume_path = target / "joint_training/checkpoints/diagnostic_resume_validation_14c.json"
    resume = json.loads(resume_path.read_text(encoding="utf-8"))
    return {
        "format": "rcmf_exp037a_one_production_unit_probe_14c_v1",
        "scientific_result": False,
        "command": command,
        "environment": {**environment, "PYTHONHASHSEED": "25101"},
        "first_attempt_seconds": first_seconds,
        "resume_attempt_seconds": resume_seconds,
        "summary": summary,
        "resume": resume,
        "summary_identity": file_identity(summary_path),
        "resume_identity": file_identity(resume_path),
        "passed": bool(summary["passed"] and resume["resume_validation_passed"]),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.diagnostic_root.exists():
        raise FileExistsError(
            f"Diagnostic root already exists; allocate a new suffix: {args.diagnostic_root}"
        )
    if not args.failed_run_root.is_dir():
        raise FileNotFoundError(args.failed_run_root)
    args.diagnostic_root.mkdir(parents=True)
    started = time.perf_counter()
    before = _verify_published_index(args.artifact_index, args.failed_run_root)
    config = _resolved_diagnostic_config(
        args.config, args.diagnostic_root, args.source_commit
    )
    config_path = args.diagnostic_root / "diagnostic_pipeline_config.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
    )
    shared = rebuild_shared_cpu(config, args.diagnostic_root / "preflight")
    fixtures = _copy_d00_d07_fixtures(args.failed_run_root, args.diagnostic_root)
    layout = initialize_runtime_layout(config, args.diagnostic_root)
    early = _joint_source_contract_preflight(config, args.diagnostic_root)
    d08_started = time.perf_counter()
    d08 = _joint_prepare(
        args.diagnostic_root,
        "3d",
        args.source_commit,
        "exp037a-r1-d08",
    )
    d08_seconds = time.perf_counter() - d08_started
    d09 = _run_d09_probe(args.diagnostic_root, args.source_commit)
    after = _verify_published_index(args.artifact_index, args.failed_run_root)
    fixture_sources_after = []
    for row in fixtures["rows"]:
        source = Path(str(row["source_root"])) / str(row["relative_path"])
        fixture_sources_after.append(
            {
                "path": str(source),
                "sha256": sha256_file(source),
                "expected_sha256": row["sha256"],
                "passed": sha256_file(source) == str(row["sha256"]),
            }
        )
    result = {
        "format": FORMAT,
        "run_uuid": args.diagnostic_root.name,
        "scientific_result": False,
        "source_commit": args.source_commit,
        "created_at": _now(),
        "failed_run_immutability_before": before,
        "failed_run_immutability_after": after,
        "fixture_manifest": file_identity(args.diagnostic_root / "fixture_manifest.json"),
        "fixture_source_recheck": fixture_sources_after,
        "shared_preflight": shared,
        "runtime_layout": layout,
        "early_contract_gate": early,
        "d08": d08,
        "d08_seconds": d08_seconds,
        "d09": d09,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": (
            before["passed"]
            and after["passed"]
            and all(row["passed"] for row in fixture_sources_after)
            and early["passed"]
            and all(d08["phases"].values())
            and d09["passed"]
        ),
    }
    atomic_write_json(args.diagnostic_root / "diagnostic_summary.json", result)
    write_jsonl(
        args.diagnostic_root / "diagnostic_attempts.jsonl",
        [
            {
                "attempt_id": "exp037a-r1-d08",
                "status": "complete" if all(d08["phases"].values()) else "failed",
            },
            {
                "attempt_id": "exp037a-r1-d09-one-unit",
                "status": "complete" if d09["summary"]["passed"] else "failed",
            },
            {
                "attempt_id": "exp037a-r1-d09-one-unit-resume",
                "status": "complete" if d09["resume"]["resume_validation_passed"] else "failed",
            },
        ],
    )
    if not result["passed"]:
        raise RuntimeError("EXP-037A-R1 diagnostic did not pass")
    return result


def main() -> None:
    args = _parse_args()
    try:
        result = run(args)
    except Exception as error:
        args.diagnostic_root.mkdir(parents=True, exist_ok=True)
        failure = {
            "format": FORMAT,
            "scientific_result": False,
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "created_at": _now(),
        }
        atomic_write_json(args.diagnostic_root / "diagnostic_failure.json", failure)
        raise
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
