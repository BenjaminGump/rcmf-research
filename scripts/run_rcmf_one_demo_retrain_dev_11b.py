"""Run EXP-034A N1/N2 dev trajectories with immutable EXP-033A D0 reuse."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import assert_frozen_without_gradients
from rcmf.training.state_conditioned_program_7d import canonical_sha256
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
from scripts.run_rcmf_one_demo_dev_11a import summarize_condition


GLOBAL_SEED = 25101
RUN_UUID = "rcmf_exp031a_one_demo_retrain_11b_20260829_001"
CONDITIONS = ("N1", "N2")
FIELD_CONTROLS = {"N1": "D1", "N2": "D2"}
CONDITION_NAMES = {
    "N1": "one_demo_retrained_correct_499_memory_field",
    "N2": "one_demo_retrained_key_payload_shuffle_499_memory_field",
}
TASK_RESULT_FORMAT = "rcmf_one_demo_retrain_dev_task_11b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_retrain_11b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "run", "finalize"), required=True)
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp034a_dev")
    return parser.parse_args()


def _paths(artifact_dir: Path, settings: Mapping[str, Any]) -> dict[str, Path]:
    old_root = Path(str(settings["immutable"]["exp033a_root"]))
    return {
        "root": artifact_dir / "dev",
        "manifest": artifact_dir / "dev/condition_manifest.json",
        "preflight": artifact_dir / "runtime/dev_runtime_preflight.json",
        "static_assets": artifact_dir / "audits/static_prompt_assets.json",
        "deployment": artifact_dir / "deployment_field/complete_37_task_field.pt",
        "instant_add": artifact_dir / "deployment_field/instant_add_report.json",
        "data_manifest": artifact_dir / "data/full_bank_data_manifest.json",
        "selection": artifact_dir / "heldout_validation/live_full_field/checkpoint_selection.json",
        "final": artifact_dir / "dev/final_summary.json",
        "d0_reuse": artifact_dir / "dev/d0_reuse_proof.json",
        "old_root": old_root,
        "old_dev": old_root / "dev_manifest.json",
        "old_prompt": old_root / "prompt_manifest.json",
        "old_final": old_root / "dev/final_summary.json",
        "old_audit": Path(str(settings["immutable"]["exp033a_audit_index"])),
    }


def _settings_9a(cfg: Any) -> dict[str, Any]:
    value = copy.deepcopy(cfg.raw["stage_c_9a"])
    value["appworld"]["prompt_profile"] = "full_demo_first_only"
    return value


def _load_backend(cfg: Any) -> Any:
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("EXP-034A loaded trainable Qwen parameters")
    return backend


def _runtime(settings_9a: Mapping[str, Any], backend: Any, paths: Mapping[str, Path]) -> CompleteFieldRuntime:
    runtime = CompleteFieldRuntime(
        settings=settings_9a,
        backend=backend,
        deployment_path=paths["deployment"],
        instant_add_path=paths["instant_add"],
    )
    if runtime.memory_count != 499:
        raise ValueError("EXP-034A must use the new complete 499-memory field")
    if tuple(runtime.A.shape) != (960, 8, 256) or tuple(runtime.B.shape) != (8, 256):
        raise ValueError("EXP-034A deployment field shape differs")
    if any(parameter.requires_grad for parameter in runtime.reader.parameters()):
        raise RuntimeError("EXP-034A loaded trainable reader parameters")
    return runtime


def _old_task_path(old_root: Path, condition: str, task_id: str) -> Path:
    return old_root / f"dev/conditions/{condition}/task_results/{task_id}.json"


def _build_manifest(task_ids: Sequence[str], paths: Mapping[str, Path]) -> dict[str, Any]:
    payload = {
        "format": "rcmf_one_demo_retrain_dev_condition_manifest_11b_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "task_ids": list(task_ids),
        "task_count": len(task_ids),
        "conditions": [
            {"task_id": task_id, "condition": condition, "field_control": FIELD_CONTROLS[condition]}
            for condition in CONDITIONS
            for task_id in task_ids
        ],
        "logical_new_condition_count": len(task_ids) * len(CONDITIONS),
        "d0_reused_from_exp033a": True,
        "deployment_field_sha256": sha256_file(paths["deployment"]),
        "selected_checkpoint_sha256": sha256_file(
            Path(str(_json(paths["selection"])["selected"]["checkpoint"]))
        ),
        "prompt_profile": "full_demo_first_only",
        "no_success_based_early_stopping": True,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _reuse_d0(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    expected = settings["expected"]
    dev = _json(paths["old_dev"])
    prompt = _json(paths["old_prompt"])
    final = _json(paths["old_final"])
    task_ids = [str(value) for value in dev["task_ids"]]
    rows = [_json(_old_task_path(paths["old_root"], "D0", task_id)) for task_id in task_ids]
    checks = {
        "task_count": len(task_ids) == int(expected["dev_task_count"]) == 57,
        "task_list": str(dev["ordered_task_ids_sha256"]) == str(expected["dev_task_list_sha256"]),
        "prompt_profile": str(prompt["profile"]) == "full_demo_first_only",
        "prompt_asset": str(prompt["one_demo_initial_messages_sha256"])
        == str(expected["initial_prompt_asset_sha256"]),
        "audit_index": sha256_file(paths["old_audit"]) == str(expected["old_dev_audit_sha256"]),
        "evaluation_complete": bool(final["evaluation_complete"]),
        "d0_count": sum(bool(row["success"]) for row in rows) == 12,
        "d0_profile": all(str(row["steps"][0]["prompt_profile"]) == "full_demo_first_only" for row in rows if row["steps"]),
        "d0_fresh_world": all(bool(step["same_world_execution"]) for row in rows for step in row["steps"]),
        "authoritative_success": all(str(row["success_source"]) == "evaluation.success" for row in rows),
    }
    if not all(checks.values()):
        raise RuntimeError(f"EXP-033A D0 cannot be reused: {checks}")
    proof = {
        "format": "rcmf_exp033a_d0_reuse_proof_11b_v1",
        "checks": checks,
        "task_ids": task_ids,
        "success_ids": sorted(str(row["task_id"]) for row in rows if bool(row["success"])),
        "task_row_sha256": {
            task_id: sha256_file(_old_task_path(paths["old_root"], "D0", task_id))
            for task_id in task_ids
        },
        "old_final_sha256": sha256_file(paths["old_final"]),
        "old_audit_index_sha256": sha256_file(paths["old_audit"]),
        "rerun_required": False,
        "passed": True,
    }
    proof["manifest_sha256"] = canonical_sha256(proof)
    atomic_write_json(paths["d0_reuse"], proof)
    return proof


def preflight(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    required = ("deployment", "instant_add", "data_manifest", "selection", "old_dev", "old_prompt", "old_final", "old_audit")
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"EXP-034A dev preflight inputs missing: {missing}")
    selection = _json(paths["selection"])
    if selection.get("selected") is None:
        raise RuntimeError("Original EXP-031A heldout selection has no eligible one-demo checkpoint")
    proof = _reuse_d0(settings, paths)
    task_ids = list(proof["task_ids"])
    manifest = _build_manifest(task_ids, paths)
    atomic_write_json(paths["manifest"], manifest)
    old_seconds = []
    for condition in ("D1", "D2"):
        old_seconds.extend(
            float(_json(_old_task_path(paths["old_root"], condition, task_id))["wall_seconds"])
            for task_id in task_ids
        )
    expected = sum(old_seconds)
    conservative = expected * 1.35
    report = {
        "format": "rcmf_one_demo_retrain_dev_preflight_11b_v1",
        "purpose": "all 57 N1 plus all 57 N2 frozen one-demo dev trajectories",
        "task_count": len(task_ids),
        "new_condition_count": len(task_ids) * 2,
        "d0_reuse": proof,
        "measured_exp033a_n1_n2_equivalent_seconds": expected,
        "expected_wall_hours": expected / 3600.0,
        "conservative_wall_hours": conservative / 3600.0,
        "automatic_launch_allowed": conservative <= float(settings["runtime"]["review_threshold_hours"]) * 3600.0,
        "checkpoint_restart_plan": {
            "atomic_task_outputs": True,
            "hash_valid_rows_reused": True,
            "incomplete_task_restarts_in_fresh_world": True,
            "sequential_conditions": list(CONDITIONS),
            "no_success_based_early_stopping": True,
        },
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("EXP-034A formal N1/N2 batch may exceed 18 hours")
    atomic_write_json(paths["preflight"], report)
    return report


def run_condition(args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger) -> dict[str, Any]:
    if args.condition is None:
        raise ValueError("--condition is required")
    condition = args.condition
    for prior in CONDITIONS[: CONDITIONS.index(condition)]:
        if not (_condition_root(paths, prior, False) / "summary.json").exists():
            raise RuntimeError(f"Sequential prior condition is missing: {prior}")
    manifest = _json(paths["manifest"])
    backend = _load_backend(cfg)
    runtime = _runtime(_settings_9a(cfg), backend, paths)
    rows = []
    resumed = 0
    for task_id in manifest["task_ids"]:
        row, reused = _run_task(
            task_id=str(task_id), condition=condition, settings=_settings_9a(cfg),
            backend=backend, runtime=runtime, paths=paths, manifest=manifest,
            config_sha256=sha256_file(args.config), attempt_id=args.attempt_id,
            smoke=False, result_version=TASK_RESULT_FORMAT,
            extra_result_fields={
                "run_uuid": RUN_UUID, "prompt_profile": "full_demo_first_only",
                "trained_with_prompt_profile": "full_demo_first_only",
                "dev_outcomes_used_for_selection": False,
            },
            bare_condition=False, condition_name=CONDITION_NAMES[condition],
            memory_count=499, field_artifact_path=paths["deployment"],
            field_provenance_path=paths["data_manifest"],
            experiment_prefix="exp034a", field_control_condition=FIELD_CONTROLS[condition],
        )
        rows.append(row)
        resumed += int(reused)
        attempt.progress(
            status=f"exp034a_dev_{condition.lower()}", completed_tasks=len(rows),
            total_tasks=len(manifest["task_ids"]), resumed_tasks=resumed,
            latest_validated_checkpoint=str(_task_output(paths, condition, str(task_id), False)),
        )
        print(f"{condition} task={task_id} success={row['success']} steps={row['step_count']} reused={reused}", flush=True)
    assert_frozen_without_gradients(backend.model)
    assert_frozen_without_gradients(runtime.reader)
    summary = summarize_condition(rows, condition, expected_count=len(manifest["task_ids"]))
    summary.update({"run_uuid": RUN_UUID, "new_task_count": len(rows) - resumed, "resumed_task_count": resumed})
    atomic_write_json(_condition_root(paths, condition, False) / "summary.json", summary)
    return summary


def finalize(paths: Mapping[str, Path]) -> dict[str, Any]:
    manifest = _json(paths["manifest"])
    d0 = _json(paths["d0_reuse"])
    summaries = {condition: _json(_condition_root(paths, condition, False) / "summary.json") for condition in CONDITIONS}
    result = {
        "format": "rcmf_one_demo_retrain_dev_final_11b_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "task_ids": list(manifest["task_ids"]),
        "d0_reuse": d0,
        "summaries": summaries,
        "per_task_success": {
            task_id: {
                "D0": task_id in set(d0["success_ids"]),
                **{
                    condition: bool(_json(_task_output(paths, condition, task_id, False))["success"])
                    for condition in CONDITIONS
                },
            }
            for task_id in manifest["task_ids"]
        },
        "evaluation_complete": all(int(summary["task_count"]) == 57 for summary in summaries.values()),
        "dev_used_for_training_or_selection": False,
        "no_success_based_early_stopping": True,
    }
    if not result["evaluation_complete"]:
        raise RuntimeError("EXP-034A dev manifest is incomplete")
    atomic_write_json(paths["final"], result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_11b"]
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    paths = _paths(args.artifact_dir, settings)
    hashes = {"config": sha256_file(args.config)}
    with AttemptLedger(
        args.artifact_dir, run_uuid=RUN_UUID, attempt_id=args.attempt_id,
        phase=f"exp034a_dev_{args.phase}" + (f"_{args.condition.lower()}" if args.condition else ""),
        command=[str(value) for value in sys.argv], local_head=args.local_head,
        github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = preflight(settings, paths)
        elif args.phase == "run":
            if not bool(_json(paths["preflight"])["automatic_launch_allowed"]):
                raise RuntimeError("Formal dev launch is not authorized")
            result = run_condition(args, cfg, settings, paths, attempt)
        else:
            result = finalize(paths)
        attempt.progress(status=f"exp034a_dev_{args.phase}_complete", result=result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
