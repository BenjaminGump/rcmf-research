"""Run the frozen EXP-031A one-demo AppWorld dev evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import copy
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
from rcmf.training.rcmf_joint_full_bank_9a import assert_frozen_without_gradients
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    CompleteFieldRuntime,
    _attempt_ids,
    _build_backend,
    _condition_root,
    _run_task,
    _task_output,
)
from scripts.run_rcmf_q90_trajectory_common_9c import deterministic_task_match


GLOBAL_SEED = 25101
CONDITION_ORDER = ("D0", "D1", "D2")
CONDITION_NAMES = {
    "D0": "one_demo_bare_zero_memory",
    "D1": "one_demo_correct_499_memory_field",
    "D2": "one_demo_key_payload_shuffle_499_memory_field",
}
TASK_RESULT_FORMAT = "rcmf_one_demo_dev_task_11a_v1"
CONDITION_SUMMARY_FORMAT = "rcmf_one_demo_dev_condition_summary_11a_v1"
FINAL_FORMAT = "rcmf_one_demo_dev_final_11a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_dev_11a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("smoke", "determinism", "preflight", "run", "finalize"), required=True
    )
    parser.add_argument("--condition", choices=CONDITION_ORDER)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp033a_dev")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(artifact_dir: Path, settings: Mapping[str, Any]) -> dict[str, Path]:
    immutable = settings["immutable_exp031a"]
    return {
        "root": artifact_dir / "dev",
        "manifest": artifact_dir / "condition_manifest.json",
        "preflight": artifact_dir / "runtime_preflight.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": Path(str(immutable["deployment_field"])),
        "instant_add": Path(str(immutable["instant_add_report"])),
        "data_manifest": Path(str(immutable["data_manifest"])),
        "final": artifact_dir / "dev/final_summary.json",
    }


def _scope_paths(
    artifact_dir: Path, settings: Mapping[str, Any], scope: str
) -> dict[str, Path]:
    paths = _paths(artifact_dir, settings)
    paths["root"] = artifact_dir / "validation" / scope
    return paths


def _settings_9a(cfg: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(cfg.raw["stage_c_9a"])
    value["appworld"]["prompt_profile"] = str(settings["prompt"]["profile"])
    return value


def _load_backend(cfg: Any) -> Any:
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("EXP-033A loaded trainable Qwen parameters")
    return backend


def _runtime(
    settings_9a: Mapping[str, Any], settings: Mapping[str, Any], backend: Any
) -> CompleteFieldRuntime:
    immutable = settings["immutable_exp031a"]
    runtime = CompleteFieldRuntime(
        settings=settings_9a,
        backend=backend,
        deployment_path=Path(str(immutable["deployment_field"])),
        instant_add_path=Path(str(immutable["instant_add_report"])),
    )
    if runtime.memory_count != 499:
        raise ValueError("EXP-033A must read the frozen complete 499-memory field")
    if tuple(runtime.A.shape) != (960, 8, 256) or tuple(runtime.B.shape) != (8, 256):
        raise ValueError("EXP-033A field shape differs")
    if any(parameter.requires_grad for parameter in runtime.reader.parameters()):
        raise RuntimeError("EXP-033A loaded trainable reader parameters")
    return runtime


def _validate(args: argparse.Namespace, settings: Mapping[str, Any]) -> None:
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-033A requires seed 25101")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")


def _hashes(
    args: argparse.Namespace, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, str]:
    immutable = settings["immutable_exp031a"]
    values = {
        "config": sha256_file(args.config),
        "checkpoint": sha256_file(Path(str(immutable["checkpoint"]))),
        "deployment_field": sha256_file(paths["deployment"]),
        "instant_add_report": sha256_file(paths["instant_add"]),
        "data_manifest": sha256_file(paths["data_manifest"]),
        "condition_manifest": sha256_file(paths["manifest"]),
        "prompt_manifest": sha256_file(args.artifact_dir / "prompt_manifest.json"),
        "dev_manifest": sha256_file(args.artifact_dir / "dev_manifest.json"),
        "leakage_audit": sha256_file(args.artifact_dir / "dev_leakage_audit.json"),
    }
    if values["checkpoint"] != str(immutable["checkpoint_sha256"]):
        raise ValueError("Frozen EXP-031A checkpoint SHA differs")
    if values["deployment_field"] != str(immutable["deployment_field_sha256"]):
        raise ValueError("Frozen 499-memory field SHA differs")
    return values


def _ledger(
    args: argparse.Namespace,
    settings: Mapping[str, Any],
    phase: str,
    hashes: Mapping[str, str],
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
        data_manifest_hashes=dict(hashes),
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    )


def _run_rows(
    *,
    task_ids: Sequence[str],
    condition: str,
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    attempt: AttemptLedger,
    smoke: bool,
    max_steps_override: int | None,
) -> tuple[list[dict[str, Any]], int]:
    settings_9a = _settings_9a(cfg, settings)
    backend = _load_backend(cfg)
    runtime = None if condition == "D0" else _runtime(settings_9a, settings, backend)
    rows: list[dict[str, Any]] = []
    resumed = 0
    for task_id in task_ids:
        row, reused = _run_task(
            task_id=task_id,
            condition=condition,
            settings=settings_9a,
            backend=backend,
            runtime=runtime,
            paths=paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            smoke=smoke,
            result_version=TASK_RESULT_FORMAT,
            extra_result_fields={
                "exp033a_prompt_profile": str(settings["prompt"]["profile"]),
                "evaluation_split": "train_smoke" if smoke else "dev",
                "evaluation_only": True,
                "optimizer_steps": 0,
            },
            bare_condition=condition == "D0",
            condition_name=CONDITION_NAMES[condition],
            memory_count=0 if condition == "D0" else 499,
            field_artifact_path=paths["deployment"],
            field_provenance_path=paths["data_manifest"],
            max_steps_override=max_steps_override,
            experiment_prefix="exp033a",
        )
        rows.append(row)
        resumed += int(reused)
        attempt.progress(
            status=f"exp033a_{'smoke' if smoke else 'dev'}_{condition.lower()}",
            completed_tasks=len(rows),
            total_tasks=len(task_ids),
            resumed_tasks=resumed,
            latest_validated_checkpoint=str(
                _task_output(paths, condition, task_id, smoke)
            ),
        )
        print(
            f"{condition} task={task_id} success={row['success']} "
            f"steps={row['step_count']} reused={reused}",
            flush=True,
        )
    assert_frozen_without_gradients(backend.model)
    if runtime is not None:
        assert_frozen_without_gradients(runtime.reader)
    return rows, resumed


def _flatten_metric(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    output: list[float] = []
    for row in rows:
        for step in row["steps"]:
            for values in step["reader_audit"].get(key, {}).values():
                output.extend(float(value) for value in values)
    return output


def summarize_condition(
    rows: Sequence[Mapping[str, Any]], condition: str, *, expected_count: int
) -> dict[str, Any]:
    success_ids = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    counts: Counter[str] = Counter()
    prompt_tokens: list[int] = []
    generated_tokens: list[int] = []
    query_seconds: list[float] = []
    read_seconds: list[float] = []
    slot_norms: list[float] = []
    per_task_steps: list[int] = []
    per_task_wall: list[float] = []
    for row in rows:
        counts.update(row["counts"])
        per_task_steps.append(int(row["step_count"]))
        per_task_wall.append(float(row["wall_seconds"]))
        prompt_tokens.append(int(row["usage"].get("prompt_tokens", 0)))
        generated_tokens.append(int(row["usage"].get("completion_tokens", 0)))
        for step in row["steps"]:
            query_seconds.append(float(step["field"]["query_seconds"]))
            read_seconds.append(float(step["field"]["field_read_seconds"]))
            slot_norms.append(float(step["field"]["slots"]["norm"]))
    residuals = _flatten_metric(rows, "delta_norms")
    entropies = _flatten_metric(rows, "attention_entropy")
    return {
        "format": CONDITION_SUMMARY_FORMAT,
        "condition": condition,
        "condition_name": CONDITION_NAMES[condition],
        "task_count": len(rows),
        "success_count": len(success_ids),
        "success_ids": success_ids,
        "total_steps": sum(per_task_steps),
        "mean_steps": statistics.fmean(per_task_steps) if per_task_steps else 0.0,
        "median_steps": statistics.median(per_task_steps) if per_task_steps else 0.0,
        "total_prompt_tokens": sum(prompt_tokens),
        "total_generated_tokens": sum(generated_tokens),
        "counts": dict(counts),
        "total_wall_seconds": sum(per_task_wall),
        "mean_task_wall_seconds": statistics.fmean(per_task_wall) if per_task_wall else 0.0,
        "mean_query_seconds": statistics.fmean(query_seconds) if query_seconds else 0.0,
        "mean_field_read_seconds": statistics.fmean(read_seconds) if read_seconds else 0.0,
        "mean_slot_norm": statistics.fmean(slot_norms) if slot_norms else 0.0,
        "mean_reader_residual_norm": statistics.fmean(residuals) if residuals else 0.0,
        "mean_reader_attention_entropy": statistics.fmean(entropies) if entropies else None,
        "student_prompt_contains_raw_memory": False,
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
        "passed_infrastructure": len(rows) == expected_count
        and all(
            row["status"] == "complete"
            and row["success_source"] == "evaluation.success"
            and row["raw_audit_complete"]
            for row in rows
        ),
    }


def _smoke_checks(
    rows: Mapping[str, Sequence[Mapping[str, Any]]], settings: Mapping[str, Any]
) -> dict[str, bool]:
    all_rows = [row for values in rows.values() for row in values]
    checks = {
        "all_complete": all(row["status"] == "complete" for row in all_rows),
        "one_demo_profile": all(
            step["prompt_profile"] == str(settings["prompt"]["profile"])
            for row in all_rows
            for step in row["steps"]
        ),
        "no_truncation": all(
            not bool(step["truncation_applied"])
            for row in all_rows
            for step in row["steps"]
        ),
        "D0_no_reader": all(
            not bool(step["reader_audit"]["active"])
            and step["field"]["query"] is None
            for row in rows["D0"]
            for step in row["steps"]
        ),
        "D1_correct_field": all(
            step["field"]["field_control"] == "correct"
            for row in rows["D1"]
            for step in row["steps"]
        ),
        "D2_shuffle_field": all(
            step["field"]["field_control"] == "key_payload_shuffle"
            for row in rows["D2"]
            for step in row["steps"]
        ),
        "eight_slots": all(
            step["field"]["slots"]["shape"] == [8, 256]
            for condition in ("D1", "D2")
            for row in rows[condition]
            for step in row["steps"]
        ),
        "no_runtime_retrieval": all(
            not bool(row["runtime_memory_retrieval"])
            and not bool(row["runtime_per_memory_scoring"])
            for row in all_rows
        ),
        "no_raw_memory_prompt": all(
            not bool(row["student_prompt_contains_raw_memory"]) for row in all_rows
        ),
    }
    return checks


def run_smoke(
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    manifest = load_json(args.artifact_dir / "smoke_manifest.json")
    task_ids = [str(value) for value in manifest["task_ids"]]
    paths = _scope_paths(args.artifact_dir, settings, "smoke")
    rows: dict[str, list[dict[str, Any]]] = {}
    for condition in CONDITION_ORDER:
        rows[condition], _ = _run_rows(
            task_ids=task_ids,
            condition=condition,
            args=args,
            cfg=cfg,
            settings=settings,
            paths=paths,
            manifest=manifest,
            attempt=attempt,
            smoke=True,
            max_steps_override=int(settings["smoke"]["max_steps"]),
        )
    checks = _smoke_checks(rows, settings)
    result = {
        "format": "rcmf_one_demo_train_smoke_11a_v1",
        "task_ids": task_ids,
        "task_condition_count": sum(len(value) for value in rows.values()),
        "max_steps": int(settings["smoke"]["max_steps"]),
        "wall_seconds": sum(float(row["wall_seconds"]) for values in rows.values() for row in values),
        "checks": checks,
        "passed": all(checks.values()),
        "non_scientific_train_smoke": True,
        "outcomes_used_to_modify_science": False,
    }
    if not result["passed"]:
        raise RuntimeError(f"EXP-033A engineering smoke failed: {checks}")
    atomic_write_json(args.artifact_dir / "validation/smoke.json", result)
    return result


def run_determinism(
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    smoke_manifest = load_json(args.artifact_dir / "smoke_manifest.json")
    task_id = str(smoke_manifest["task_ids"][0])
    manifest = {
        **smoke_manifest,
        "task_ids": [task_id],
        "task_count": 1,
    }
    from rcmf.training.state_conditioned_program_7d import canonical_sha256

    manifest["manifest_sha256"] = canonical_sha256(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for repetition in ("repeat_a", "repeat_b"):
        paths = _scope_paths(args.artifact_dir, settings, f"determinism/{repetition}")
        results[repetition] = {}
        for condition in CONDITION_ORDER:
            rows, _ = _run_rows(
                task_ids=[task_id],
                condition=condition,
                args=args,
                cfg=cfg,
                settings=settings,
                paths=paths,
                manifest=manifest,
                attempt=attempt,
                smoke=True,
                max_steps_override=int(settings["smoke"]["max_steps"]),
            )
            results[repetition][condition] = rows
    matches = {
        condition: deterministic_task_match(
            results["repeat_a"][condition][0], results["repeat_b"][condition][0]
        )
        for condition in CONDITION_ORDER
    }
    result = {
        "format": "rcmf_one_demo_fresh_world_determinism_11a_v1",
        "task_id": task_id,
        "condition_repetitions": 6,
        "matches": matches,
        "wall_seconds": sum(
            float(row["wall_seconds"])
            for repetitions in results.values()
            for rows in repetitions.values()
            for row in rows
        ),
        "passed": all(value["passed"] for value in matches.values()),
    }
    if not result["passed"]:
        raise RuntimeError(f"EXP-033A determinism failed: {matches}")
    atomic_write_json(args.artifact_dir / "validation/determinism.json", result)
    return result


def build_preflight(
    args: argparse.Namespace, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    smoke = load_json(args.artifact_dir / "validation/smoke.json")
    determinism = load_json(args.artifact_dir / "validation/determinism.json")
    manifest = load_json(paths["manifest"])
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    historical = {
        condition: load_json(
            parent_root / f"first37/conditions/{condition}/summary.json"
        )["total_wall_seconds"]
        / 37.0
        for condition in CONDITION_ORDER
    }
    expected_seconds = sum(float(historical[c]) * int(manifest["task_count"]) for c in CONDITION_ORDER)
    measured_smoke_seconds = float(smoke["wall_seconds"]) + float(determinism["wall_seconds"])
    conservative_seconds = max(expected_seconds * 1.35, max(historical.values()) * 171 * 1.10)
    threshold_seconds = float(settings["runtime"]["review_threshold_hours"]) * 3600.0
    report = {
        "format": "rcmf_one_demo_dev_runtime_preflight_11a_v1",
        "purpose": "complete official AppWorld dev under one-demo D0/D1/D2",
        "dev_task_count": int(manifest["task_count"]),
        "formal_condition_count": int(manifest["logical_condition_count"]),
        "smoke_task_count": int(load_json(args.artifact_dir / "smoke_manifest.json")["task_count"]),
        "smoke_condition_count": 6,
        "measured_smoke_and_determinism_seconds": measured_smoke_seconds,
        "historical_full_trajectory_seconds_per_task": historical,
        "expected_wall_hours": expected_seconds / 3600.0,
        "conservative_wall_hours": conservative_seconds / 3600.0,
        "expected_h100_active_hours": expected_seconds / 3600.0,
        "conservative_h100_active_hours": conservative_seconds / 3600.0,
        "estimated_cost": {
            "unit": "H100 active hours",
            "expected": expected_seconds / 3600.0,
            "conservative": conservative_seconds / 3600.0,
            "provider_currency_rate": "not recorded; no fabricated currency estimate",
        },
        "expected_git_safe_audit_bytes": int(settings["runtime"]["expected_git_safe_audit_bytes"]),
        "expected_lambda_raw_artifact_bytes": int(settings["runtime"]["expected_lambda_raw_audit_bytes"]),
        "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "unavailable",
        "hardware_required": str(settings["runtime"]["hardware_required"]),
        "checkpoint_restart_plan": {
            "sequential_conditions": True,
            "atomic_task_outputs": True,
            "atomic_step_outputs": True,
            "append_only_attempts": True,
            "hash_valid_complete_rows_reused": True,
            "incomplete_task_restarts_in_fresh_world": True,
            "no_success_based_early_stopping": True,
        },
        "smoke_passed": bool(smoke["passed"]),
        "determinism_passed": bool(determinism["passed"]),
        "automatic_launch_allowed": conservative_seconds <= threshold_seconds,
        "review_threshold_hours": float(settings["runtime"]["review_threshold_hours"]),
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("EXP-033A conservative formal batch may exceed 18 hours")
    atomic_write_json(paths["preflight"], report)
    return report


def run_formal(
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    if args.condition is None:
        raise ValueError("--condition is required for formal run")
    condition = args.condition
    for prior in CONDITION_ORDER[: CONDITION_ORDER.index(condition)]:
        if not (_condition_root(paths, prior, False) / "summary.json").exists():
            raise RuntimeError(f"Sequential prior dev condition is missing: {prior}")
    manifest = load_json(paths["manifest"])
    rows, resumed = _run_rows(
        task_ids=[str(value) for value in manifest["task_ids"]],
        condition=condition,
        args=args,
        cfg=cfg,
        settings=settings,
        paths=paths,
        manifest=manifest,
        attempt=attempt,
        smoke=False,
        max_steps_override=None,
    )
    summary = summarize_condition(rows, condition, expected_count=len(manifest["task_ids"]))
    summary.update(
        {
            "run_uuid": str(settings["run_uuid"]),
            "condition_manifest_sha256": str(manifest["manifest_sha256"]),
            "new_task_count": len(rows) - resumed,
            "resumed_task_count": resumed,
        }
    )
    atomic_write_json(_condition_root(paths, condition, False) / "summary.json", summary)
    return summary


def finalize(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    manifest = load_json(paths["manifest"])
    task_ids = [str(value) for value in manifest["task_ids"]]
    summaries = {
        condition: load_json(_condition_root(paths, condition, False) / "summary.json")
        for condition in CONDITION_ORDER
    }
    tasks = {
        condition: {
            task_id: load_json(_task_output(paths, condition, task_id, False))
            for task_id in task_ids
        }
        for condition in CONDITION_ORDER
    }
    infrastructure = all(bool(value["passed_infrastructure"]) for value in summaries.values())
    result = {
        "format": FINAL_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "task_ids": task_ids,
        "summaries": summaries,
        "per_task_success": {
            task_id: {
                condition: bool(tasks[condition][task_id]["success"])
                for condition in CONDITION_ORDER
            }
            for task_id in task_ids
        },
        "infrastructure_valid": infrastructure,
        "evaluation_complete": infrastructure and all(
            int(summary["task_count"]) == len(task_ids) for summary in summaries.values()
        ),
        "scientific_interpretation": "reserved_for_paired_analysis_and_user_review",
        "no_training": True,
        "no_interim_scientific_stopping": True,
    }
    if not result["evaluation_complete"]:
        raise RuntimeError("EXP-033A formal dev manifest is incomplete")
    atomic_write_json(paths["final"], result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_11a"]
    _validate(args, settings)
    paths = _paths(args.artifact_dir, settings)
    required = (
        args.artifact_dir / "run_manifest.json",
        args.artifact_dir / "prompt_manifest.json",
        args.artifact_dir / "dev_manifest.json",
        args.artifact_dir / "condition_manifest.json",
        args.artifact_dir / "dev_leakage_audit.json",
    )
    if any(not path.exists() for path in required):
        raise RuntimeError("EXP-033A frozen preparation manifests are missing")
    hashes = _hashes(args, settings, paths)
    phase = f"exp033a_{args.phase}"
    if args.condition:
        phase += f"_{args.condition.lower()}"
    with _ledger(args, settings, phase, hashes) as attempt:
        if args.phase == "smoke":
            result = run_smoke(args, cfg, settings, attempt)
        elif args.phase == "determinism":
            result = run_determinism(args, cfg, settings, attempt)
        elif args.phase == "preflight":
            result = build_preflight(args, settings, paths)
        elif args.phase == "run":
            preflight = load_json(paths["preflight"])
            if not bool(preflight["automatic_launch_allowed"]):
                raise RuntimeError("EXP-033A formal launch is not authorized")
            result = run_formal(args, cfg, settings, paths, attempt)
        else:
            result = finalize(settings, paths)
        attempt.progress(
            status=f"{phase}_complete",
            latest_validated_checkpoint=str(
                paths["final"]
                if args.phase == "finalize"
                else paths["preflight"]
                if args.phase == "preflight"
                else args.artifact_dir / "validation"
                if args.phase in {"smoke", "determinism"}
                else _condition_root(paths, str(args.condition), False) / "summary.json"
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
