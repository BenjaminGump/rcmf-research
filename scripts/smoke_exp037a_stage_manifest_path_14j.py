#!/usr/bin/env python3
"""Exercise the real S00-S04 scheduler/subprocess/manifest validation path."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

import yaml

import _bootstrap  # noqa: F401
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.manifests import file_identity
from rcmf.pipeline.scheduler import (
    EventDrivenScheduler,
    subprocess_stage_runner,
)
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved


STAGE_IDS = (
    "S00_environment_manifest",
    "S01_authoritative_corpus",
    "S02_task_and_parent_splits",
    "S03_transition_records",
    "S04_selector_supervision",
)
AUTHORIZATION_VERSION = "exp037a_r10_s00_s04_diagnostic_authorization_v1"
AUTHORIZATION_SCOPE = "non_scientific_s00_s04_manifest_validation"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--fixture-preflight", type=Path, required=True)
    parser.add_argument("--python", required=True)
    return parser.parse_args()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_fixture_preflight(source: Path, target: Path) -> dict[str, Any]:
    relative_paths = (
        Path("environment_manifest.json"),
        Path("authoritative_source_manifest.json"),
        Path("shared/parent_split.json"),
        Path("shared/transitions.jsonl"),
        Path("shared/transition_signatures.jsonl"),
        Path("shared/signature_equivalence.json"),
        Path("shared/labels.jsonl"),
        Path("shared/illegal_pairs.jsonl"),
    )
    rows = []
    for relative in relative_paths:
        source_path = source / relative
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target_path = target / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        rows.append(
            {
                "relative_path": relative.as_posix(),
                "source": file_identity(source_path),
                "copy": file_identity(target_path),
                "exact_copy": sha256_file(source_path) == sha256_file(target_path),
            }
        )
    if not all(row["exact_copy"] for row in rows):
        raise ValueError("Diagnostic fixture copy differs")
    return {
        "role": "non_scientific_read_only_stage_input_fixture",
        "scientific_input": False,
        "rows": rows,
    }


def _diagnostic_config(
    source: Path, run_root: Path, run_uuid: str
) -> Path:
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    config_path = run_root / "diagnostic_config.yaml"
    raw["pipeline"]["schema_version"] = (
        "rcmf_exp037a_r10_s00_s04_diagnostic_v1"
    )
    raw["pipeline"]["run_uuid"] = run_uuid
    raw["pipeline"]["config_path"] = str(config_path)
    raw["pipeline"]["strict_stage_identity"] = True
    raw["pipeline"]["roots"]["run_root"] = str(run_root)
    raw["pipeline"]["conditional_runtime_authorization"][
        "authorization_version"
    ] = AUTHORIZATION_VERSION
    for arm_id, pointer in raw["arms"].items():
        include = source.parent / str(pointer["include"])
        target_name = f"diagnostic_arm_{arm_id}.yaml"
        arm = yaml.safe_load(include.read_text(encoding="utf-8"))
        arm["run_id"] = f"{run_uuid}-{arm_id}"
        (run_root / target_name).write_text(
            yaml.safe_dump(arm, sort_keys=False), encoding="utf-8"
        )
        pointer["include"] = target_name
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )
    return config_path


def _contract(
    config_path: Path,
    run_root: Path,
    run_uuid: str,
    source_commit: str,
) -> tuple[PipelineContract, Path]:
    config = load_resolved(config_path)
    stage_by_id = {
        stage.stage_id: stage for stage in build_exp037a_stage_graph()
    }
    stages = tuple(stage_by_id[stage_id] for stage_id in STAGE_IDS)
    arms = {
        arm_id: ArmContract(
            arm_id=arm_id,
            task_conditioned_prompt_profile=str(
                row["task_conditioned_prompt_profile"]
            ),
            artifact_prefix=str(row["artifact_prefix"]),
            run_id=str(row["run_id"]),
        )
        for arm_id, row in config["arms"].items()
    }
    contract = PipelineContract(
        schema_version="rcmf_exp037a_r10_s00_s04_diagnostic_v1",
        run_uuid=run_uuid,
        source_commit=source_commit,
        global_seed=25101,
        hard_cap_hours=1.0,
        stages=stages,
        arms=arms,
        metadata={
            "pipeline_config_path": str(config_path),
            "pipeline_config_sha256": sha256_file(config_path),
            "canonical_run_root": str(run_root),
            "authorization_scope": AUTHORIZATION_SCOPE,
            "authorization_version": AUTHORIZATION_VERSION,
            "require_run_bound_authorization": True,
            "strict_stage_identity": True,
            "maximum_recoverable_attempts_per_stage": 1,
            "recoverable_retry_delay_seconds": 0.0,
        },
    )
    contract_path = run_root / "preflight/stage_dag.json"
    atomic_write_json(contract_path, contract.as_dict())
    return contract, contract_path


def _authorize(
    contract: PipelineContract,
    contract_path: Path,
    config_path: Path,
    run_root: Path,
) -> None:
    now = _utc_now()
    atomic_write_json(
        run_root / "runtime_authorization.json",
        {
            "format": AUTHORIZATION_VERSION,
            "authorization_version": AUTHORIZATION_VERSION,
            "authorization_status": "AUTHORIZED",
            "authorized": True,
            "authorized_to_launch": True,
            "granted_by_user": True,
            "full_pipeline_authorized": True,
            "d06_or_later_authorized": True,
            "one_demo_authorized": True,
            "previous_200_hour_authorization_inherited": False,
            "authorization_source": "explicit_run_bound_user_authorization",
            "authorized_at_utc": now,
            "run_started_utc": now,
            "run_uuid": contract.run_uuid,
            "run_root": str(run_root),
            "source_commit": contract.source_commit,
            "contract_sha256": sha256_file(contract_path),
            "pipeline_config_sha256": sha256_file(config_path),
            "hard_cap_hours": contract.hard_cap_hours,
            "recommended_hard_cap_hours": contract.hard_cap_hours,
            "scope": AUTHORIZATION_SCOPE,
            "diagnostic_only": True,
            "scientific_stage_authorized": False,
        },
    )


def _stage_rows(
    contract: PipelineContract,
    run_root: Path,
    config_path: Path,
    contract_path: Path,
) -> list[dict[str, Any]]:
    rows = []
    config_sha = sha256_file(config_path)
    contract_sha = sha256_file(contract_path)
    for stage in contract.stages:
        stage_dir = run_root / "stages" / stage.stage_id
        completion = json.loads(
            (stage_dir / "completion.json").read_text(encoding="utf-8")
        )
        validation = validate_stage_completion(
            stage_dir,
            contract.source_commit,
            expected_run_uuid=contract.run_uuid,
            expected_pipeline_config_sha256=config_sha,
            expected_contract_sha256=contract_sha,
            expected_run_root=run_root,
        )
        manifest_path = stage_dir / "output_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "stage_id": stage.stage_id,
                "exit_code": int(completion["exit_code"]),
                "manifest_sha256": sha256_file(manifest_path),
                "strict_validator_passed": bool(validation["passed"]),
                "completion_passed": bool(completion["passed"]),
                "identity": {
                    key: manifest[key]
                    for key in (
                        "source_commit",
                        "run_uuid",
                        "run_root",
                        "pipeline_config_sha256",
                        "contract_sha256",
                        "stage_id",
                        "attempt_id",
                    )
                },
            }
        )
    return rows


def main() -> None:
    args = parse_args()
    run_root = args.run_root.resolve(strict=False)
    if run_root.exists():
        raise FileExistsError(f"Diagnostic root is not fresh: {run_root}")
    run_root.mkdir(parents=True)
    run_uuid = run_root.name
    config_path = _diagnostic_config(args.config, run_root, run_uuid)
    fixture = _copy_fixture_preflight(
        args.fixture_preflight, run_root / "preflight"
    )
    contract, contract_path = _contract(
        config_path, run_root, run_uuid, args.source_commit
    )
    _authorize(contract, contract_path, config_path, run_root)

    started = time.perf_counter()
    scheduler = EventDrivenScheduler(
        contract,
        run_root,
        python_executable=args.python,
        config_path=config_path,
        contract_sha256=sha256_file(contract_path),
    )
    first = scheduler.run()
    first_elapsed = time.perf_counter() - started
    rows = _stage_rows(contract, run_root, config_path, contract_path)
    if first.status != "complete" or not all(
        row["strict_validator_passed"] and row["completion_passed"]
        for row in rows
    ):
        raise RuntimeError(f"Real stage-path smoke failed: {first} {rows}")

    second_subprocess_calls: list[str] = []

    def counting_runner(stage, command, stage_dir, environment):
        second_subprocess_calls.append(stage.stage_id)
        return subprocess_stage_runner(
            stage, command, stage_dir, environment
        )

    second = EventDrivenScheduler(
        contract,
        run_root,
        python_executable=args.python,
        config_path=config_path,
        runner=counting_runner,
        contract_sha256=sha256_file(contract_path),
    ).run()
    if second.status != "complete" or second_subprocess_calls:
        raise RuntimeError(
            f"Resume did not skip all valid stages: {second_subprocess_calls}"
        )

    with tempfile.TemporaryDirectory(dir=run_root.parent) as temporary:
        copied_stage = Path(temporary) / "S00_environment_manifest"
        shutil.copytree(
            run_root / "stages/S00_environment_manifest", copied_stage
        )
        manifest_path = copied_stage / "output_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_uuid"] = "tampered-run"
        atomic_write_json(manifest_path, manifest)
        tamper_validation = validate_stage_completion(
            copied_stage,
            contract.source_commit,
            expected_run_uuid=contract.run_uuid,
            expected_pipeline_config_sha256=sha256_file(config_path),
            expected_contract_sha256=sha256_file(contract_path),
            expected_run_root=run_root,
        )
    if tamper_validation["passed"]:
        raise RuntimeError("Tampered manifest passed strict validation")

    summary = {
        "format": "exp037a_r10_real_s00_s04_stage_path_smoke_v1",
        "passed": True,
        "scientific_stage_count": 0,
        "h100_scientific_active_hours": 0,
        "run_uuid": run_uuid,
        "run_root": str(run_root),
        "source_commit": args.source_commit,
        "config": file_identity(config_path),
        "contract": file_identity(contract_path),
        "fixture": fixture,
        "first_run": {
            "status": first.status,
            "elapsed_seconds": first_elapsed,
            "stage_count": len(rows),
            "stages": rows,
        },
        "second_run": {
            "status": second.status,
            "hash_valid_stages_skipped": len(second.completed),
            "subprocess_stage_execution_count": len(
                second_subprocess_calls
            ),
        },
        "tampered_copy": {
            "strict_validator_passed": tamper_validation["passed"],
            "checks": tamper_validation["checks"],
        },
        "stopped_before": "S05_transition_representations",
    }
    atomic_write_json(run_root / "diagnostic_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
