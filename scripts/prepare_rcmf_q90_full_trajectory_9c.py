"""Prepare immutable manifests and identities for EXP-031C."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_q90_full_trajectory_9c import (
    GLOBAL_SEED,
    validate_q90_contract,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_first37_9a import _attempt_ids, _task_ids
from scripts.run_rcmf_q90_trajectory_common_9c import (
    build_manifest,
    load_json,
    trajectory_paths,
    write_or_validate_json,
)


RUN_MANIFEST_FORMAT = "rcmf_q90_full_trajectory_run_manifest_9c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_q90_full_trajectory_9c.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("heldout-manifest", "first37-manifest"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031c_prepare")
    return parser.parse_args()


def _verify_head_and_mount(args: argparse.Namespace, settings: dict[str, Any]) -> None:
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-031C requires seed 25101")


def _immutable_hashes(
    settings: dict[str, Any], config_path: Path
) -> dict[str, str]:
    immutable = settings["immutable_exp031a"]
    paths = {
        "checkpoint": Path(str(immutable["checkpoint"])),
        "heldout_correct_field": Path(str(immutable["heldout_correct_field"])),
        "heldout_shuffle_field": Path(str(immutable["heldout_shuffle_field"])),
        "deployment_field": Path(str(immutable["deployment_field"])),
        "data_manifest": Path(str(immutable["data_manifest"])),
        "calibration_lock": Path(str(settings["immutable_exp031b"]["calibration_lock"])),
    }
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    expected = {
        "checkpoint": str(immutable["checkpoint_sha256"]),
        "heldout_correct_field": str(immutable["heldout_correct_field_sha256"]),
        "heldout_shuffle_field": str(immutable["heldout_shuffle_field_sha256"]),
        "deployment_field": str(immutable["deployment_field_sha256"]),
    }
    checks = {name: hashes[name] == value for name, value in expected.items()}
    if not all(checks.values()):
        raise ValueError(f"Immutable EXP-031A hashes differ: {checks}")
    hashes["config"] = sha256_file(config_path)
    return hashes


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


def _validate_field_payload(path: Path, *, expected_count: int, control: str) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if int(payload["memory_count"]) != expected_count:
        raise ValueError(f"{control} field memory count differs")
    if expected_count == 401:
        if tuple(payload["A"].shape) != (960, 8, 256):
            raise ValueError("Heldout field A shape differs")
        if tuple(payload["B"].shape) != (8, 256):
            raise ValueError("Heldout field B shape differs")
    else:
        required = {"A", "B", "shuffled_A", "shuffled_B", "reader_state_dict"}
        if not required <= set(payload):
            raise ValueError("Deployment field keys differ")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "memory_count": int(payload["memory_count"]),
        "control": control,
    }


def _run_manifest(
    *,
    args: argparse.Namespace,
    settings: dict[str, Any],
    hashes: dict[str, str],
    q90: dict[str, Any],
    heldout_tasks: list[str],
) -> dict[str, Any]:
    payload = {
        "format": RUN_MANIFEST_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "source_head": str(settings["source_head"]),
        "source_branch": str(settings["source_branch"]),
        "working_branch": str(settings["working_branch"]),
        "archive_branch": str(settings["archive_branch"]),
        "archive_tag": str(settings["archive_tag"]),
        "preparation_head": args.local_head,
        "config": str(args.config),
        "config_sha256": sha256_file(args.config),
        "immutable_hashes": hashes,
        "q90_contract": q90,
        "heldout_task_ids": heldout_tasks,
        "heldout_task_count": len(heldout_tasks),
        "optimizer_steps": 0,
        "qwen_trainable_parameters": 0,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "raw_memory_prompt": False,
        "first37_exposed_development_only": True,
    }
    from rcmf.training.state_conditioned_program_7d import canonical_sha256

    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def prepare_heldout(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    hashes: dict[str, str],
) -> dict[str, Any]:
    immutable = settings["immutable_exp031a"]
    data_manifest = load_json(Path(str(immutable["data_manifest"])))
    heldout_tasks = [str(value) for value in data_manifest["heldout_task_ids"]]
    if len(heldout_tasks) != 8 or len(set(heldout_tasks)) != 8:
        raise ValueError("Immutable 29/8 heldout task split differs")
    if set(heldout_tasks) & set(map(str, data_manifest["train_task_ids"])):
        raise ValueError("Heldout tasks entered the 401-memory bank")
    calibration = load_json(Path(str(settings["immutable_exp031b"]["calibration_lock"])))
    q90 = validate_q90_contract(settings, calibration)
    correct = _validate_field_payload(
        Path(str(immutable["heldout_correct_field"])),
        expected_count=401,
        control="correct",
    )
    shuffled = _validate_field_payload(
        Path(str(immutable["heldout_shuffle_field"])),
        expected_count=401,
        control="key_payload_shuffle",
    )
    paths = trajectory_paths(
        args.artifact_dir,
        scope="heldout",
        field_path=Path(str(immutable["heldout_correct_field"])),
        field_provenance_path=Path(str(immutable["data_manifest"])),
    )
    manifest = build_manifest(
        scope="heldout_train_complete_trajectories",
        task_ids=heldout_tasks,
        conditions=list(settings["heldout"]["condition_order"]),
        memory_count=401,
        config_sha256=sha256_file(args.config),
        field_sha256={
            "correct": correct["sha256"],
            "key_payload_shuffle": shuffled["sha256"],
        },
        data_manifest_sha256=hashes["data_manifest"],
    )
    run_manifest = _run_manifest(
        args=args,
        settings=settings,
        hashes=hashes,
        q90=q90,
        heldout_tasks=heldout_tasks,
    )
    write_or_validate_json(args.artifact_dir / "run_manifest.json", run_manifest)
    write_or_validate_json(paths["manifest"], manifest)
    report = {
        "phase": "heldout_manifest",
        "heldout_task_ids": heldout_tasks,
        "condition_count": 5,
        "task_condition_count": 40,
        "memory_count": 401,
        "field": {"correct": correct, "key_payload_shuffle": shuffled},
        "q90": q90,
        "manifest_sha256": manifest["manifest_sha256"],
        "passed": True,
    }
    atomic_write_json(args.artifact_dir / "heldout/manifest_report.json", report)
    return report


def prepare_first37(
    args: argparse.Namespace,
    cfg: Any,
    settings: dict[str, Any],
    hashes: dict[str, str],
) -> dict[str, Any]:
    heldout_final = args.artifact_dir / "heldout/final_summary.json"
    if not heldout_final.exists():
        raise RuntimeError("Heldout full-trajectory decision is missing")
    heldout = load_json(heldout_final)
    if not bool(heldout["first37_authorized"]):
        raise RuntimeError("Heldout decision did not authorize first37")
    tasks = _task_ids(cfg.raw["stage_c_9a"])
    immutable = settings["immutable_exp031a"]
    deployment = _validate_field_payload(
        Path(str(immutable["deployment_field"])),
        expected_count=499,
        control="correct_and_key_payload_shuffle",
    )
    paths = trajectory_paths(
        args.artifact_dir,
        scope="first37",
        field_path=Path(str(immutable["deployment_field"])),
        field_provenance_path=Path(str(immutable["data_manifest"])),
    )
    manifest = build_manifest(
        scope="exposed_first37_q90_complete_trajectories",
        task_ids=tasks,
        conditions=list(settings["first37"]["condition_order"]),
        memory_count=499,
        config_sha256=sha256_file(args.config),
        field_sha256={
            "correct": deployment["sha256"],
            "key_payload_shuffle": deployment["sha256"],
        },
        data_manifest_sha256=hashes["data_manifest"],
    )
    write_or_validate_json(paths["manifest"], manifest)
    report = {
        "phase": "first37_manifest",
        "heldout_decision": heldout["decision"],
        "task_ids": tasks,
        "task_count": len(tasks),
        "condition_count": 2,
        "task_condition_count": 74,
        "memory_count": 499,
        "deployment": deployment,
        "manifest_sha256": manifest["manifest_sha256"],
        "frozen_after_heldout_before_first37_generation": True,
        "passed": True,
    }
    atomic_write_json(args.artifact_dir / "first37/manifest_report.json", report)
    return report


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9c"]
    _verify_head_and_mount(args, settings)
    hashes = _immutable_hashes(settings, args.config)
    with _ledger(args, settings, f"exp031c_{args.phase.replace('-', '_')}", hashes) as attempt:
        result = (
            prepare_heldout(args, cfg, settings, hashes)
            if args.phase == "heldout-manifest"
            else prepare_first37(args, cfg, settings, hashes)
        )
        attempt.progress(
            status=f"{args.phase}_complete",
            latest_validated_checkpoint=str(
                args.artifact_dir
                / ("heldout" if args.phase == "heldout-manifest" else "first37")
                / "condition_manifest.json"
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
