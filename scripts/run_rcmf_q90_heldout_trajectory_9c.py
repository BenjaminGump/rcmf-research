"""Run EXP-031C complete heldout-train trajectories."""

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

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    assert_frozen_without_gradients,
)
from rcmf.training.rcmf_q90_full_trajectory_9c import (
    GLOBAL_SEED,
    heldout_full_trajectory_decision,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    CompleteFieldRuntime,
    _attempt_ids,
    _generate,
)
from scripts.run_rcmf_q90_trajectory_common_9c import (
    CONDITION_SPECS,
    FrozenTrajectoryFieldRuntime,
    build_manifest,
    condition_summary_path,
    deterministic_task_match,
    first_divergence,
    load_frozen_backend,
    load_json,
    run_condition_tasks,
    summarize_condition,
    task_output,
    trajectory_paths,
    write_or_validate_json,
)


CONDITION_ORDER = ("H0", "H1", "H2", "H3", "H4")
RESULT_FORMAT = "rcmf_q90_heldout_full_trajectory_summary_9c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_q90_full_trajectory_9c.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("equivalence", "determinism", "preflight", "run", "finalize"),
        required=True,
    )
    parser.add_argument("--condition", choices=CONDITION_ORDER)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031c_heldout")
    return parser.parse_args()


def _base_paths(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, Path]:
    immutable = settings["immutable_exp031a"]
    return trajectory_paths(
        args.artifact_dir,
        scope="heldout",
        field_path=Path(str(immutable["heldout_correct_field"])),
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


def _hashes(settings: dict[str, Any], manifest_path: Path) -> dict[str, str]:
    immutable = settings["immutable_exp031a"]
    paths = {
        "checkpoint": Path(str(immutable["checkpoint"])),
        "heldout_correct_field": Path(str(immutable["heldout_correct_field"])),
        "heldout_shuffle_field": Path(str(immutable["heldout_shuffle_field"])),
        "data_manifest": Path(str(immutable["data_manifest"])),
        "calibration_lock": Path(str(settings["immutable_exp031b"]["calibration_lock"])),
        "condition_manifest": manifest_path,
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _runtime(
    cfg: Any,
    settings: dict[str, Any],
    backend: Any,
    specs: dict[str, dict[str, str]] | None = None,
) -> FrozenTrajectoryFieldRuntime:
    return FrozenTrajectoryFieldRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings_9c=settings,
        backend=backend,
        memory_count=401,
        condition_specs=CONDITION_SPECS if specs is None else specs,
    )


def _forward_logits(
    backend: Any, messages: list[dict[str, str]], reader: Any, slots: torch.Tensor
) -> torch.Tensor:
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    hooks = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    with (
        torch.no_grad(),
        torch.autocast(
            device_type="cuda",
            dtype=torch.bfloat16,
            enabled=backend.device.type == "cuda",
        ),
        _attention_context(backend.device),
        hooks,
    ):
        logits = (
            backend.model(
                input_ids=tokenized.input_ids,
                attention_mask=tokenized.attention_mask,
                use_cache=False,
            )
            .logits[:, -1]
            .detach()
            .cpu()
        )
    return logits


def first_task_id_from_condition_manifest(
    manifest: dict[str, Any], *, condition: str
) -> str:
    task_ids = [
        str(row["task_id"])
        for row in manifest.get("rows", [])
        if str(row.get("condition")) == condition
    ]
    if not task_ids:
        raise ValueError(f"Parent manifest has no rows for condition {condition}")
    return task_ids[0]


def run_equivalence(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    immutable = settings["immutable_exp031a"]
    parent_root = Path(str(immutable["artifact_root"]))
    parent_manifest = load_json(parent_root / "first37/condition_manifest.json")
    task_id = first_task_id_from_condition_manifest(parent_manifest, condition="D0")
    d1_path = parent_root / f"first37/conditions/D1/task_results/{task_id}.json"
    d0_path = parent_root / f"first37/conditions/D0/task_results/{task_id}.json"
    d1, d0 = load_json(d1_path), load_json(d0_path)
    messages = [dict(value) for value in d1["steps"][0]["exact_model_message_array"]]

    backend = load_frozen_backend(cfg)
    parent_runtime = CompleteFieldRuntime(
        settings=cfg.raw["stage_c_9a"],
        backend=backend,
        deployment_path=Path(str(immutable["deployment_field"])),
        instant_add_path=parent_root / "deployment_field/instant_add_report.json",
    )
    new_runtime = FrozenTrajectoryFieldRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings_9c=settings,
        backend=backend,
        memory_count=499,
        condition_specs={
            "G100": {"candidate": "G100", "field_control": "correct"},
            "Q90": {"candidate": "Q90", "field_control": "correct"},
        },
    )
    parent_slots, parent_info = parent_runtime.read(messages, "D1")
    new_slots, new_info = new_runtime.read(messages, "G100")
    slot_equal = torch.equal(parent_slots, new_slots)
    parent_logits = _forward_logits(backend, messages, parent_runtime.reader, parent_slots)
    new_logits = _forward_logits(backend, messages, new_runtime.reader, new_slots)
    ids, response, generation = _generate(
        backend=backend,
        messages=messages,
        max_new_tokens=int(cfg.raw["stage_c_9a"]["appworld"]["max_new_tokens"]),
        reader=new_runtime.reader,
        slots=new_slots,
    )
    code, fixed = extract_code_and_fix_content(response)
    d1_step = d1["steps"][0]

    bare_messages = [dict(value) for value in d0["steps"][0]["exact_model_message_array"]]
    bare_ids, bare_response, _ = _generate(
        backend=backend,
        messages=bare_messages,
        max_new_tokens=int(cfg.raw["stage_c_9a"]["appworld"]["max_new_tokens"]),
        reader=None,
        slots=None,
    )
    bare_code, bare_fixed = extract_code_and_fix_content(bare_response)
    d0_step = d0["steps"][0]
    q90_slots, q90_info = new_runtime.read(messages, "Q90")
    raw_rms_value = torch.tensor(
        float(q90_info["raw_field_rms"]), dtype=torch.float32
    )
    expected_confidence = float(
        raw_rms_value
        / (raw_rms_value + float(settings["candidate"]["tau"]))
    )
    checks = {
        "original_slots_exact": slot_equal,
        "original_logits_exact": torch.equal(parent_logits, new_logits),
        "original_token_ids_exact": ids == list(d1_step["generated_token_ids"]),
        "original_response_exact": response == d1_step["raw_model_response"],
        "original_fixed_response_exact": fixed == d1_step["automatically_repaired_response"],
        "original_action_exact": code == d1_step["exact_executed_code"],
        "zero_bare_token_ids_exact": bare_ids == list(d0_step["generated_token_ids"]),
        "zero_bare_response_exact": bare_response == d0_step["raw_model_response"],
        "zero_bare_fixed_response_exact": bare_fixed == d0_step["automatically_repaired_response"],
        "zero_bare_action_exact": bare_code == d0_step["exact_executed_code"],
        "q90_confidence_formula_exact": float(q90_info["q90_confidence"]) == expected_confidence,
        "q90_slots_differ_from_g100": not torch.equal(q90_slots, new_slots),
        "qwen_frozen": not any(parameter.requires_grad for parameter in backend.model.parameters()),
        "reader_frozen": not any(
            parameter.requires_grad for parameter in new_runtime.reader.parameters()
        ),
        "same_query": torch.equal(parent_info["query"], new_info["query"]),
        "no_runtime_scan": not bool(new_runtime.identity["runtime_memory_scan"]),
        "no_retrieval": not bool(new_runtime.identity["runtime_retrieval"]),
        "no_per_memory_scoring": not bool(new_runtime.identity["runtime_per_memory_scoring"]),
    }
    assert_frozen_without_gradients(backend.model)
    assert_frozen_without_gradients(new_runtime.reader)
    result = {
        "format": "rcmf_q90_equivalence_9c_v1",
        "task_id": task_id,
        "checks": checks,
        "passed": all(checks.values()),
        "parent_D1": str(d1_path),
        "parent_D0": str(d0_path),
        "runtime_identity": new_runtime.identity,
        "generation_seconds": generation["generation_seconds"],
        "q90_read": q90_info,
    }
    if not result["passed"]:
        raise RuntimeError(f"EXP-031C equivalence failed: {checks}")
    atomic_write_json(args.artifact_dir / "validation/equivalence.json", result)
    return result


def run_determinism(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    heldout_manifest: dict[str, Any],
) -> dict[str, Any]:
    task_ids = [
        str(heldout_manifest["task_ids"][0]),
        str(heldout_manifest["task_ids"][-1]),
    ]
    immutable = settings["immutable_exp031a"]
    det_paths = trajectory_paths(
        args.artifact_dir,
        scope="validation/determinism",
        field_path=Path(str(immutable["heldout_correct_field"])),
        field_provenance_path=Path(str(immutable["data_manifest"])),
    )
    manifest = build_manifest(
        scope="q90_complete_task_determinism",
        task_ids=task_ids,
        conditions=["E1", "E2"],
        memory_count=401,
        config_sha256=sha256_file(args.config),
        field_sha256={
            "correct": str(immutable["heldout_correct_field_sha256"]),
            "key_payload_shuffle": str(immutable["heldout_shuffle_field_sha256"]),
        },
        data_manifest_sha256=sha256_file(Path(str(immutable["data_manifest"]))),
    )
    write_or_validate_json(det_paths["manifest"], manifest)
    backend = load_frozen_backend(cfg)
    runtime = _runtime(
        cfg,
        settings,
        backend,
        specs={
            "E1": {"candidate": "Q90", "field_control": "correct"},
            "E2": {"candidate": "Q90", "field_control": "correct"},
        },
    )
    rows = {}
    for condition in ("E1", "E2"):
        rows[condition], _ = run_condition_tasks(
            task_ids=task_ids,
            condition=condition,
            settings_9a=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            paths=det_paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            memory_count=401,
            field_provenance_path=Path(str(immutable["data_manifest"])),
            max_steps_override=int(cfg.raw["stage_c_9a"]["appworld"]["max_steps"]),
        )
    matches = {
        task_id: deterministic_task_match(rows["E1"][index], rows["E2"][index])
        for index, task_id in enumerate(task_ids)
    }
    result = {
        "format": "rcmf_q90_complete_task_determinism_9c_v1",
        "task_ids": task_ids,
        "complete_task_repetitions": 4,
        "matches": matches,
        "wall_seconds": sum(
            float(row["wall_seconds"]) for condition in rows.values() for row in condition
        ),
        "passed": all(value["passed"] for value in matches.values()),
    }
    if not result["passed"]:
        raise RuntimeError(f"Q90 complete-task determinism failed: {matches}")
    atomic_write_json(args.artifact_dir / "validation/determinism.json", result)
    return result


def build_preflight(
    args: argparse.Namespace,
    settings: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    equivalence = load_json(args.artifact_dir / "validation/equivalence.json")
    determinism = load_json(args.artifact_dir / "validation/determinism.json")
    measured_per_task = float(determinism["wall_seconds"]) / 4.0
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    historical = []
    for condition in ("D0", "D1", "D2"):
        summary = load_json(parent_root / f"first37/conditions/{condition}/summary.json")
        historical.append(float(summary["total_wall_seconds"]) / 37.0)
    expected_seconds = max(measured_per_task, statistics.fmean(historical)) * 40
    conservative_seconds = max(measured_per_task, max(historical)) * 40 * 1.25
    old_size = sum(
        path.stat().st_size for path in (parent_root / "first37").rglob("*") if path.is_file()
    )
    artifact_estimate = int(old_size * (40.0 / 111.0))
    report = {
        "format": "rcmf_q90_heldout_runtime_preflight_9c_v1",
        "purpose": "five complete trajectory conditions on eight heldout train tasks",
        "task_ids": manifest["task_ids"],
        "conditions": list(CONDITION_ORDER),
        "task_condition_count": 40,
        "first37_conditional_task_condition_count": 74,
        "source_head": settings["source_head"],
        "config_sha256": sha256_file(args.config),
        "checkpoint_sha256": settings["immutable_exp031a"]["checkpoint_sha256"],
        "field_sha256": manifest["field_sha256"],
        "q90_tau": settings["candidate"]["tau"],
        "q90_calibration_sha256": settings["candidate"]["calibration_sha256"],
        "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "measured_complete_task_seconds": measured_per_task,
        "historical_condition_task_seconds": historical,
        "expected_wall_hours": expected_seconds / 3600.0,
        "conservative_wall_hours": conservative_seconds / 3600.0,
        "expected_h100_hours": expected_seconds / 3600.0,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "expected_artifact_bytes": artifact_estimate,
        "restart_plan": "atomic per-task results; resume skips only exact validated rows",
        "sequential_conditions": True,
        "automatic_launch_allowed": conservative_seconds <= 18.0 * 3600.0,
        "equivalence_passed": bool(equivalence["passed"]),
        "determinism_passed": bool(determinism["passed"]),
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("Heldout conservative runtime exceeds 18 hours")
    atomic_write_json(args.artifact_dir / "heldout/runtime_preflight.json", report)
    return report


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
    index = CONDITION_ORDER.index(condition)
    for prior in CONDITION_ORDER[:index]:
        if not condition_summary_path(paths, prior).exists():
            raise RuntimeError(f"Sequential prior condition is missing: {prior}")
    task_ids = [str(value) for value in manifest["task_ids"]]
    backend = load_frozen_backend(cfg)
    runtime = None if condition == "H0" else _runtime(cfg, settings, backend)
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
            memory_count=401,
            field_provenance_path=Path(str(immutable["data_manifest"])),
        )
        row = batch[0]
        rows.append(row)
        resumed += int(reused)
        attempt.progress(
            status=f"heldout_{condition.lower()}",
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
        }
    )
    atomic_write_json(condition_summary_path(paths, condition), summary)
    return summary


def finalize(
    args: argparse.Namespace,
    settings: dict[str, Any],
    paths: dict[str, Path],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    task_ids = [str(value) for value in manifest["task_ids"]]
    summaries = {
        condition: load_json(condition_summary_path(paths, condition))
        for condition in CONDITION_ORDER
    }
    tasks = {
        condition: {
            task_id: load_json(task_output(paths, condition, task_id)) for task_id in task_ids
        }
        for condition in CONDITION_ORDER
    }
    success_ids = {condition: summaries[condition]["success_ids"] for condition in CONDITION_ORDER}
    infrastructure = all(bool(summary["passed_infrastructure"]) for summary in summaries.values())
    decision = heldout_full_trajectory_decision(success_ids, infrastructure_valid=infrastructure)
    comparisons = {
        task_id: {
            "H0_vs_H1": first_divergence(tasks["H0"][task_id], tasks["H1"][task_id]),
            "H1_vs_H3": first_divergence(tasks["H1"][task_id], tasks["H3"][task_id]),
            "H3_vs_H4": first_divergence(tasks["H3"][task_id], tasks["H4"][task_id]),
            "success": {
                condition: bool(tasks[condition][task_id]["success"])
                for condition in CONDITION_ORDER
            },
        }
        for task_id in task_ids
    }
    result = {
        "format": RESULT_FORMAT,
        "run_uuid": settings["run_uuid"],
        "global_seed": GLOBAL_SEED,
        "task_ids": task_ids,
        "summaries": summaries,
        "comparisons": comparisons,
        "infrastructure_valid": infrastructure,
        **decision,
    }
    atomic_write_json(paths["final"], result)
    atomic_write_json(args.artifact_dir / "heldout/comparisons.json", comparisons)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9c"]
    _validate(args, settings)
    paths = _base_paths(args, settings)
    if not paths["manifest"].exists():
        raise RuntimeError("Heldout immutable manifest is missing")
    manifest = load_json(paths["manifest"])
    hashes = _hashes(settings, paths["manifest"])

    phase = f"exp031c_heldout_{args.phase}"
    if args.condition:
        phase += f"_{args.condition.lower()}"
    with _ledger(args, settings, phase, hashes) as attempt:
        if args.phase == "equivalence":
            result = run_equivalence(args, cfg, settings, paths)
        elif args.phase == "determinism":
            result = run_determinism(args, cfg, settings, manifest)
        elif args.phase == "preflight":
            result = build_preflight(args, settings, manifest)
        elif args.phase == "run":
            preflight = load_json(paths["preflight"])
            if not bool(preflight["automatic_launch_allowed"]):
                raise RuntimeError("Heldout formal launch is not authorized")
            result = run_formal_condition(args, cfg, settings, paths, manifest, attempt)
        else:
            result = finalize(args, settings, paths, manifest)
        attempt.progress(
            status=f"heldout_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths["final"]
                if args.phase == "finalize"
                else paths["preflight"]
                if args.phase == "preflight"
                else args.artifact_dir / "validation"
                if args.phase in {"equivalence", "determinism"}
                else condition_summary_path(paths, str(args.condition))
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
