from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    compile_differentiable_field,
    freeze_module,
    tensor_sha256,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _build_components,
    _load_data,
    _paths as parent_paths,
    _runtime_tensors,
)


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
FORMAT = "rcmf_onpolicy_trajectory_preflight_10a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_prepare")
    return parser.parse_args()


def paths(artifact_dir: Path) -> dict[str, Path]:
    return {
        "root": artifact_dir,
        "attempts": artifact_dir / "attempts.jsonl",
        "manifest": artifact_dir / "run_manifest.json",
        "preflight": artifact_dir / "preflight/immutable_preflight.json",
        "task_fields": artifact_dir / "preflight/task_legal_fields.pt",
        "task_field_report": artifact_dir / "preflight/task_legal_field_report.json",
        "rollout_manifest": artifact_dir / "rollouts/rollout_manifest.json",
        "field_provenance": artifact_dir / "preflight/task_legal_field_report.json",
    }


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_mount_and_heads(args: argparse.Namespace, settings: Mapping[str, Any]) -> None:
    root = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(root):
        raise RuntimeError("Persistent filesystem is not mounted")
    heads = {args.local_head, args.github_head, args.lambda_head}
    if len(heads) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")


def _immutable_hashes(settings: Mapping[str, Any]) -> dict[str, str]:
    immutable = settings["immutable_exp031a"]
    paths_by_name = {
        "checkpoint": Path(str(immutable["checkpoint"])),
        "heldout_correct_field": Path(str(immutable["heldout_correct_field"])),
        "heldout_shuffle_field": Path(str(immutable["heldout_shuffle_field"])),
        "deployment_field": Path(str(immutable["deployment_field"])),
        "data_manifest": Path(str(immutable["data_manifest"])),
        "source_cache": Path(str(immutable["source_cache"])),
        "shuffle_manifest": Path(str(immutable["shuffle_manifest"])),
        "calibration_lock": Path(str(settings["immutable_exp031b"]["calibration_lock"])),
    }
    missing = {name: str(path) for name, path in paths_by_name.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(missing)
    hashes = {name: sha256_file(path) for name, path in paths_by_name.items()}
    expected = {
        "checkpoint": str(immutable["checkpoint_sha256"]),
        "heldout_correct_field": str(immutable["heldout_correct_field_sha256"]),
        "heldout_shuffle_field": str(immutable["heldout_shuffle_field_sha256"]),
        "deployment_field": str(immutable["deployment_field_sha256"]),
    }
    for name, value in expected.items():
        if hashes[name] != value:
            raise ValueError(f"Immutable {name} SHA differs")
    calibration = _json(paths_by_name["calibration_lock"])
    if str(calibration["calibration_sha256"]) != str(
        settings["immutable_exp031b"]["calibration_sha256"]
    ):
        raise ValueError("EXP-031B calibration semantic SHA differs")
    return hashes


def _compile_task_legal_fields(
    *, cfg: Any, settings_9a: Mapping[str, Any], settings: Mapping[str, Any], output: Path
) -> dict[str, Any]:
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    data = _load_data(parent_paths(settings_9a, parent_root))
    manifest = data["data_manifest"]
    train_tasks = [str(value) for value in manifest["train_task_ids"]]
    if len(train_tasks) != 29 or len(set(train_tasks)) != 29:
        raise ValueError("Immutable model-training task split differs")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tensors = _runtime_tensors(data, device)
    checkpoint_path = Path(str(settings["immutable_exp031a"]["checkpoint"]))
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    writer, _ = _build_components(device)
    writer.load_state_dict(checkpoint["writer_state_dict"])
    freeze_module(writer)
    started = time.perf_counter()
    with torch.no_grad():
        payloads = writer(tensors["memory_views"])
        fields: dict[str, Any] = {}
        max_error = 0.0
        for control, field_payloads in (
            ("correct", payloads),
            ("key_payload_shuffle", payloads[tensors["permutation"]]),
        ):
            total_A, total_B = compile_differentiable_field(
                keys=tensors["keys"], payloads=field_payloads, rho=tensors["rho"]
            )
            task_contributions: dict[str, Any] = {}
            for task_id in train_tasks:
                selected = tensors["task_indices"][task_id]
                task_A, task_B = compile_differentiable_field(
                    keys=tensors["keys"][selected],
                    payloads=field_payloads[selected],
                    rho=tensors["rho"][selected],
                )
                keep = torch.ones(len(data["train_ids"]), device=device, dtype=torch.bool)
                keep[selected] = False
                explicit_A, explicit_B = compile_differentiable_field(
                    keys=tensors["keys"][keep],
                    payloads=field_payloads[keep],
                    rho=tensors["rho"][keep],
                )
                legal_A, legal_B = total_A - task_A, total_B - task_B
                error = max(
                    float((legal_A - explicit_A).abs().max().cpu()),
                    float((legal_B - explicit_B).abs().max().cpu()),
                )
                max_error = max(max_error, error)
                task_contributions[task_id] = {
                    "A": task_A.cpu(),
                    "B": task_B.cpu(),
                    "memory_count": int(selected.numel()),
                }
            fields[control] = {
                "A_total": total_A.cpu(),
                "B_total": total_B.cpu(),
                "task_contributions": task_contributions,
                "A_total_sha256": tensor_sha256(total_A),
                "B_total_sha256": tensor_sha256(total_B),
            }
    payload = {
        "format": "rcmf_task_legal_fields_10a_v1",
        "global_seed": 25101,
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "train_task_ids": train_tasks,
        "train_memory_count": len(data["train_ids"]),
        "fields": fields,
        "maximum_explicit_sum_error": max_error,
        "runtime_unrelated_memory_iteration": False,
        "runtime_lookup": "task_contribution_dictionary",
    }
    if max_error > 5.0e-5:
        raise RuntimeError(f"Task field explicit-sum mismatch: {max_error}")
    _atomic_torch_save(payload, output)
    return {
        "format": "rcmf_task_legal_field_report_10a_v1",
        "task_count": len(train_tasks),
        "train_memory_count": len(data["train_ids"]),
        "task_field_sha256": sha256_file(output),
        "maximum_explicit_sum_error": max_error,
        "field_shapes": {"A": [960, 8, 256], "B": [8, 256]},
        "same_task_exclusion": "exact_task_accumulator_subtraction",
        "runtime_memory_scan": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": True,
    }


def _rollout_manifest(
    *, task_ids: list[str], config_sha256: str, field_sha256: str
) -> dict[str, Any]:
    rows = [
        {
            "task_id": task_id,
            "condition": condition,
            "field_control": {
                "T0": "zero",
                "T1": "correct_task_legal",
                "T2": "key_payload_shuffle_task_legal",
            }[condition],
            "same_task_memory_excluded": True,
            "student_prompt_contains_raw_memory": False,
            "runtime_retrieval": False,
            "outcomes_used_for_selection": False,
        }
        for condition in ("T0", "T1", "T2")
        for task_id in task_ids
    ]
    payload = {
        "format": "rcmf_onpolicy_rollout_manifest_10a_v1",
        "global_seed": 25101,
        "task_ids": task_ids,
        "task_count": len(task_ids),
        "conditions": ["T0", "T1", "T2"],
        "trajectory_count": len(rows),
        "config_sha256": config_sha256,
        "task_field_sha256": field_sha256,
        "frozen_before_outcomes": True,
        "rows": rows,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    settings_9a = cfg.raw["stage_c_9a"]
    _verify_mount_and_heads(args, settings)
    p = paths(args.artifact_dir)
    p["root"].mkdir(parents=True, exist_ok=True)
    if p["manifest"].exists():
        raise FileExistsError("EXP-032A run manifest already exists; use resume phases")
    immutable_hashes = _immutable_hashes(settings)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="exp032a_immutable_preflight_and_task_fields",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=immutable_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        field_report = _compile_task_legal_fields(
            cfg=cfg,
            settings_9a=settings_9a,
            settings=settings,
            output=p["task_fields"],
        )
        atomic_write_json(p["task_field_report"], field_report)
        data_manifest = _json(Path(str(settings["immutable_exp031a"]["data_manifest"])))
        task_ids = [str(value) for value in data_manifest["train_task_ids"]]
        rollout = _rollout_manifest(
            task_ids=task_ids,
            config_sha256=sha256_file(args.config),
            field_sha256=field_report["task_field_sha256"],
        )
        atomic_write_json(p["rollout_manifest"], rollout)
        preflight = {
            "format": FORMAT,
            "run_uuid": RUN_UUID,
            "global_seed": 25101,
            "heads": {
                "local": args.local_head,
                "github": args.github_head,
                "lambda": args.lambda_head,
            },
            "immutable_hashes": immutable_hashes,
            "task_field_report": field_report,
            "rollout_manifest_sha256": rollout["manifest_sha256"],
            "rollout_count": 87,
            "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
            "passed": True,
        }
        atomic_write_json(p["preflight"], preflight)
        run_manifest = {
            "format": "rcmf_onpolicy_trajectory_run_manifest_10a_v1",
            "run_uuid": RUN_UUID,
            "source_commit": args.local_head,
            "config": str(args.config),
            "config_sha256": sha256_file(args.config),
            "global_seed": 25101,
            "immutable_hashes": immutable_hashes,
            "task_field_sha256": field_report["task_field_sha256"],
            "rollout_manifest_sha256": rollout["manifest_sha256"],
            "no_duplicate_run": True,
        }
        run_manifest["manifest_sha256"] = canonical_sha256(run_manifest)
        atomic_write_json(p["manifest"], run_manifest)
        attempt.progress(
            status="immutable_preflight_complete",
            latest_validated_checkpoint=str(p["preflight"]),
        )
        print(json.dumps(preflight, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
