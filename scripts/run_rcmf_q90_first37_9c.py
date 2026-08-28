"""Run the conditional EXP-031C Q90 first37 trajectories."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_q90_full_trajectory_9c import (
    GLOBAL_SEED,
    first37_scientific_decision,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_first37_9a import _attempt_ids
from scripts.run_rcmf_q90_trajectory_common_9c import (
    CONDITION_SPECS,
    FrozenTrajectoryFieldRuntime,
    condition_summary_path,
    first_divergence,
    load_frozen_backend,
    load_json,
    run_condition_tasks,
    summarize_condition,
    task_output,
    trajectory_paths,
)


CONDITION_ORDER = ("Q1", "Q2")
RESULT_FORMAT = "rcmf_q90_first37_full_trajectory_summary_9c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_q90_full_trajectory_9c.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "run", "finalize"), required=True)
    parser.add_argument("--condition", choices=CONDITION_ORDER)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031c_first37")
    return parser.parse_args()


def _paths(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Path]:
    immutable = settings["immutable_exp031a"]
    return trajectory_paths(
        args.artifact_dir,
        scope="first37",
        field_path=Path(str(immutable["deployment_field"])),
        field_provenance_path=Path(str(immutable["data_manifest"])),
    )


def _validate(args: argparse.Namespace, settings: dict[str, Any]) -> None:
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-031C requires seed 25101")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    heldout = load_json(args.artifact_dir / "heldout/final_summary.json")
    if not bool(heldout["first37_authorized"]):
        raise RuntimeError("Heldout trajectory decision did not authorize first37")


def _ledger(
    args: argparse.Namespace,
    settings: dict[str, Any],
    phase: str,
    hashes: dict[str, str],
) -> AttemptLedger:
    return AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=phase,
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    )


def _hashes(settings: dict[str, Any], manifest: Path) -> dict[str, str]:
    immutable = settings["immutable_exp031a"]
    paths = {
        "checkpoint": Path(str(immutable["checkpoint"])),
        "deployment_field": Path(str(immutable["deployment_field"])),
        "data_manifest": Path(str(immutable["data_manifest"])),
        "calibration_lock": Path(str(settings["immutable_exp031b"]["calibration_lock"])),
        "condition_manifest": manifest,
        "heldout_final": manifest.parent.parent / "heldout/final_summary.json",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def task_ids_from_condition_manifest(
    manifest: dict[str, Any], *, condition: str
) -> list[str]:
    task_ids = [
        str(row["task_id"])
        for row in manifest.get("rows", [])
        if str(row.get("condition")) == condition
    ]
    if not task_ids:
        raise ValueError(f"Parent manifest has no rows for condition {condition}")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError(f"Parent manifest has duplicate tasks for condition {condition}")
    return task_ids


def validate_reused_controls(settings: dict[str, Any], task_ids: list[str]) -> dict[str, Any]:
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    manifest_path = parent_root / "first37/condition_manifest.json"
    manifest = load_json(manifest_path)
    if task_ids_from_condition_manifest(manifest, condition="D0") != task_ids:
        raise ValueError("Immutable first37 task order differs")
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    condition_hashes = {}
    for condition in ("D0", "D1", "D2"):
        if task_ids_from_condition_manifest(manifest, condition=condition) != task_ids:
            raise ValueError(f"Immutable first37 task order differs for {condition}")
        rows[condition] = {}
        for task_id in task_ids:
            path = parent_root / f"first37/conditions/{condition}/task_results/{task_id}.json"
            row = load_json(path)
            checks = {
                "complete": row["status"] == "complete",
                "condition": row["condition"] == condition,
                "task": row["task_id"] == task_id,
                "seed": int(row["global_seed"]) == GLOBAL_SEED,
                "success_source": row["success_source"] == "evaluation.success",
                "deployment": row["deployment_field_sha256"]
                == str(settings["immutable_exp031a"]["deployment_field_sha256"]),
                "no_raw_memory": not bool(row["student_prompt_contains_raw_memory"]),
                "no_retrieval": not bool(row["runtime_memory_retrieval"]),
                "no_per_memory_scoring": not bool(row["runtime_per_memory_scoring"]),
                "audit_complete": bool(row["raw_audit_complete"]),
            }
            if not all(checks.values()):
                raise ValueError(f"Immutable {condition}/{task_id} differs: {checks}")
            rows[condition][task_id] = row
        condition_hashes[condition] = {
            "summary": sha256_file(parent_root / f"first37/conditions/{condition}/summary.json"),
            "success_count": sum(bool(row["success"]) for row in rows[condition].values()),
        }
    if {key: value["success_count"] for key, value in condition_hashes.items()} != {
        "D0": 8,
        "D1": 8,
        "D2": 5,
    }:
        raise ValueError("Immutable EXP-031A control outcomes differ")
    l1_path = Path(str(settings["immutable_exp031b"]["first37_final"]))
    l1 = load_json(l1_path)
    if l1["scientific_decision"] != "STOP_ROUTE":
        raise ValueError("Immutable EXP-031B L1 decision differs")
    return {
        "passed": True,
        "parent_manifest": str(manifest_path),
        "parent_manifest_sha256": sha256_file(manifest_path),
        "condition": condition_hashes,
        "L1_final": str(l1_path),
        "L1_final_sha256": sha256_file(l1_path),
        "L1_success_count": l1["success_count"],
    }


def build_preflight(
    args: argparse.Namespace,
    settings: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    heldout = load_json(args.artifact_dir / "heldout/final_summary.json")
    task_ids = [str(value) for value in manifest["task_ids"]]
    controls = validate_reused_controls(settings, task_ids)
    heldout_times = [
        float(heldout["summaries"][condition]["total_wall_seconds"]) / 8.0
        for condition in ("H3", "H4")
    ]
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    historical_times = [
        float(
            load_json(parent_root / f"first37/conditions/{condition}/summary.json")[
                "total_wall_seconds"
            ]
        )
        / 37.0
        for condition in ("D1", "D2")
    ]
    expected_seconds = max(statistics.fmean(heldout_times), statistics.fmean(historical_times)) * 74
    conservative_seconds = max(heldout_times + historical_times) * 74 * 1.25
    parent_size = sum(
        path.stat().st_size for path in (parent_root / "first37").rglob("*") if path.is_file()
    )
    report = {
        "format": "rcmf_q90_first37_runtime_preflight_9c_v1",
        "purpose": "Q90 correct and key-payload-shuffle complete first37 trajectories",
        "heldout_decision": heldout["decision"],
        "task_ids": task_ids,
        "condition_order": list(CONDITION_ORDER),
        "task_condition_count": 74,
        "source_head": settings["source_head"],
        "config_sha256": sha256_file(args.config),
        "checkpoint_sha256": settings["immutable_exp031a"]["checkpoint_sha256"],
        "deployment_field_sha256": settings["immutable_exp031a"]["deployment_field_sha256"],
        "q90_tau": settings["candidate"]["tau"],
        "q90_calibration_sha256": settings["candidate"]["calibration_sha256"],
        "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "heldout_task_seconds": heldout_times,
        "historical_task_seconds": historical_times,
        "expected_wall_hours": expected_seconds / 3600.0,
        "conservative_wall_hours": conservative_seconds / 3600.0,
        "expected_h100_hours": expected_seconds / 3600.0,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "expected_artifact_bytes": int(parent_size * (74.0 / 111.0)),
        "restart_plan": "atomic per-task outputs; exact validated rows resume without duplication",
        "sequential_conditions": True,
        "reused_controls": controls,
        "automatic_launch_allowed": conservative_seconds <= 18.0 * 3600.0,
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("First37 conservative runtime exceeds 18 hours")
    atomic_write_json(args.artifact_dir / "first37/runtime_preflight.json", report)
    return report


def _runtime(cfg: Any, settings: dict[str, Any], backend: Any) -> FrozenTrajectoryFieldRuntime:
    return FrozenTrajectoryFieldRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings_9c=settings,
        backend=backend,
        memory_count=499,
        condition_specs=CONDITION_SPECS,
    )


def run_formal_condition(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    paths: dict[str, Path],
    manifest: dict[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    if args.condition is None:
        raise ValueError("--condition is required for run")
    condition = args.condition
    if condition == "Q2" and not condition_summary_path(paths, "Q1").exists():
        raise RuntimeError("Q1 must complete and be audited before Q2")
    task_ids = [str(value) for value in manifest["task_ids"]]
    backend = load_frozen_backend(cfg)
    runtime = _runtime(cfg, settings, backend)
    rows, resumed = [], 0
    immutable = settings["immutable_exp031a"]
    for task_id in task_ids:
        batch, reused = run_condition_tasks(
            task_ids=[task_id],
            condition=condition,
            settings_9a=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            paths=paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            memory_count=499,
            field_provenance_path=Path(str(immutable["data_manifest"])),
        )
        row = batch[0]
        rows.append(row)
        resumed += int(reused)
        attempt.progress(
            status=f"first37_{condition.lower()}",
            completed_tasks=len(rows),
            total_tasks=len(task_ids),
            resumed_tasks=resumed,
            latest_validated_checkpoint=str(task_output(paths, condition, task_id)),
        )
        print(
            f"{condition} task={task_id} success={row['success']} "
            f"steps={row['step_count']} reused={reused}",
            flush=True,
        )
    summary = summarize_condition(rows, condition)
    summary.update(
        {
            "run_uuid": settings["run_uuid"],
            "condition_manifest_sha256": manifest["manifest_sha256"],
            "new_task_count": len(rows) - resumed,
            "resumed_task_count": resumed,
            "exposed_development_only": True,
        }
    )
    atomic_write_json(condition_summary_path(paths, condition), summary)
    return summary


def finalize(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    paths: dict[str, Path],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    task_ids = [str(value) for value in manifest["task_ids"]]
    summaries = {
        condition: load_json(condition_summary_path(paths, condition))
        for condition in CONDITION_ORDER
    }
    q_tasks = {
        condition: {
            task_id: load_json(task_output(paths, condition, task_id)) for task_id in task_ids
        }
        for condition in CONDITION_ORDER
    }
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    parent = {
        condition: {
            task_id: load_json(
                parent_root / f"first37/conditions/{condition}/task_results/{task_id}.json"
            )
            for task_id in task_ids
        }
        for condition in ("D0", "D1", "D2")
    }
    success = {
        condition: sorted(task_id for task_id, row in tasks.items() if bool(row["success"]))
        for condition, tasks in {**parent, **q_tasks}.items()
    }
    stage9b = cfg.raw["stage_c_9b"]
    gains = [
        str(value) for values in stage9b["first37"]["gain_families"].values() for value in values
    ]
    retained = [str(value) for value in stage9b["critical_states"]["retained"]]
    losses = [str(value) for value in stage9b["critical_states"]["losses"]]
    heldout = load_json(args.artifact_dir / "heldout/final_summary.json")
    q90_only_suppresses = (
        set(success["Q1"]) == set(success["D0"]) and len(set(gains) & set(success["Q1"])) < 5
    )
    decision = first37_scientific_decision(
        q1_success_ids=success["Q1"],
        q2_success_ids=success["Q2"],
        d0_success_ids=success["D0"],
        d1_success_ids=success["D1"],
        original_gain_ids=gains,
        retained_success_ids=retained,
        original_loss_ids=losses,
        gain_families={
            str(key): [str(value) for value in values]
            for key, values in stage9b["first37"]["gain_families"].items()
        },
        contract_valid=all(
            bool(summary["passed_infrastructure"]) for summary in summaries.values()
        ),
        heldout_contradicts_q90=(
            int(heldout["H3_minus_H1"]) < 0 and int(heldout["H3_minus_H4"]) <= 0
        ),
        q90_only_suppresses_useful_memory=q90_only_suppresses,
    )
    comparisons = {}
    for task_id in task_ids:
        comparisons[task_id] = {
            "success": {
                condition: task_id in success[condition]
                for condition in ("D0", "D1", "D2", "Q1", "Q2")
            },
            "D0_vs_Q1": first_divergence(parent["D0"][task_id], q_tasks["Q1"][task_id]),
            "D1_vs_Q1": first_divergence(parent["D1"][task_id], q_tasks["Q1"][task_id]),
            "Q1_vs_Q2": first_divergence(q_tasks["Q1"][task_id], q_tasks["Q2"][task_id]),
        }
    result = {
        "format": RESULT_FORMAT,
        "run_uuid": settings["run_uuid"],
        "global_seed": GLOBAL_SEED,
        "heldout_decision": heldout["decision"],
        "summaries": summaries,
        "success_ids": success,
        "comparisons": comparisons,
        **decision,
    }
    atomic_write_json(paths["final"], result)
    atomic_write_json(args.artifact_dir / "first37/comparisons.json", comparisons)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9c"]
    _validate(args, settings)
    paths = _paths(args, settings)
    if not paths["manifest"].exists():
        raise RuntimeError("Frozen first37 manifest is missing")
    manifest = load_json(paths["manifest"])
    hashes = _hashes(settings, paths["manifest"])
    phase = f"exp031c_first37_{args.phase}"
    if args.condition:
        phase += f"_{args.condition.lower()}"
    with _ledger(args, settings, phase, hashes) as attempt:
        if args.phase == "preflight":
            result = build_preflight(args, settings, manifest)
        elif args.phase == "run":
            preflight = load_json(paths["preflight"])
            if not bool(preflight["automatic_launch_allowed"]):
                raise RuntimeError("First37 launch is not authorized")
            result = run_formal_condition(args, cfg, settings, paths, manifest, attempt)
        else:
            result = finalize(args, cfg, settings, paths, manifest)
        attempt.progress(
            status=f"first37_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths["final"]
                if args.phase == "finalize"
                else paths["preflight"]
                if args.phase == "preflight"
                else condition_summary_path(paths, str(args.condition))
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
