from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.continuation_14l import (
    validate_parent_artifact_manifest,
)
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _dev_task_ids,
    _run_task_set,
)
from rcmf.config import load_config
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    _build_components,
    _load_data,
    _paths,
    _runtime_tensors,
)
from scripts.run_rcmf_joint_full_bank_live_9a import (
    _field_bundle,
    _instant_add,
    _tensor_sha256,
)


FORMAL_SOURCE = "98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f"
FORMAL_UUID = "rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003"
FORMAL_CONFIG_SHA256 = (
    "6e4be2be11e608436f5b5ebfdeee45d94a61f2831c841be72cd97e8050107e01"
)
FORMAL_CONTRACT_SHA256 = (
    "e479889fba498401e50ff7f309668629c3745d8ea5d91d84d8600c62986e61c7"
)
EPOCH_CHECKPOINTS = {
    1: "c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c",
    2: "1db373ba6399aac1adcd6fd721a05e79f3a9acfc39d8a03f2e0a5d8c49ea1457",
}
FORMAL_DEPLOYMENT_SHA256 = (
    "3bda71b91f7e1f5147a8399c5fdcb41207869ffbd5f0236bfbdecaa88d4c4110"
)
PARENT_CLOSURE_SHA256 = (
    "f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6"
)
DIAGNOSTIC_UUID = "exp037a_r19_epoch2_forced_dev_20260908_001"
CONDITIONS = {
    "correct": ("EPOCH2_FRESH1D_C_1DDEPLOY", "D1"),
    "shuffle": ("EPOCH2_FRESH1D_S_1DDEPLOY", "D2"),
}
FORMAL_CONDITIONS = {
    "correct": "FRESH1D_C_1DDEPLOY",
    "shuffle": "FRESH1D_S_1DDEPLOY",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--pipeline-config", type=Path, required=True)
    parser.add_argument("--diagnostic-source", required=True)
    parser.add_argument(
        "--phase",
        choices=("preflight", "construct", "evaluate", "finalize", "all"),
        required=True,
    )
    parser.add_argument("--condition", choices=sorted(CONDITIONS))
    return parser.parse_args()


def _json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: str | Path) -> str:
    return sha256_file(Path(path))


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(path, dict(payload))


def _assert_roots(formal_root: Path, diagnostic_root: Path) -> None:
    formal = formal_root.resolve(strict=True)
    diagnostic = diagnostic_root.resolve(strict=False)
    if diagnostic == formal or formal in diagnostic.parents or diagnostic in formal.parents:
        raise ValueError("Diagnostic and formal roots must be disjoint")
    if DIAGNOSTIC_UUID not in diagnostic.name:
        raise ValueError("Diagnostic root does not contain the frozen diagnostic UUID")


def _formal_arm(formal_root: Path) -> Path:
    return formal_root / "arms/1d"


def _formal_key_paths(formal_root: Path) -> dict[str, Path]:
    arm = _formal_arm(formal_root)
    return {
        "epoch_01_checkpoint": arm / "joint_training/checkpoints/epoch_01.pt",
        "epoch_02_checkpoint": arm / "joint_training/checkpoints/epoch_02.pt",
        "checkpoint_selection": arm
        / "heldout_validation/live_full_field/checkpoint_selection.json",
        "live_validation_summary": arm
        / "heldout_validation/live_full_field/validation_summary.json",
        "teacher_forced_summary": arm
        / "heldout_validation/teacher_forced_zero_exact_summary.json",
        "full_trajectory_summary": arm
        / "heldout_validation/full_trajectory/summary.json",
        "shuffle_manifest": arm / "data/key_payload_shuffle_manifest.json",
        "source_cache": arm / "data/rcmf_source_cache.pt",
        "data_manifest": arm / "data/full_bank_data_manifest.json",
        "selected_401": arm / "deployment_field/selected_401_field.pt",
        "formal_deployment": arm / "deployment_field/complete_37_task_field.pt",
        "instant_add": arm / "deployment_field/instant_add_report.json",
        "correct_summary": formal_root
        / "evaluation/common_one_demo_dev/summaries/FRESH1D_C_1DDEPLOY.json",
        "shuffle_summary": formal_root
        / "evaluation/common_one_demo_dev/summaries/FRESH1D_S_1DDEPLOY.json",
        "orchestrator_result": formal_root / "orchestrator_result.json",
    }


def _formal_snapshot(formal_root: Path) -> dict[str, Any]:
    paths = _formal_key_paths(formal_root)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Formal key artifacts missing: {missing}")
    return {
        "format": "exp037a_r19_formal_immutability_snapshot_v1",
        "formal_root": str(formal_root.resolve(strict=True)),
        "files": {
            name: file_identity(path) for name, path in sorted(paths.items())
        },
    }


def _compact_live_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    metrics = row["metrics"]
    return {
        "epoch": int(row["epoch"]),
        "checkpoint": str(row["checkpoint"]),
        "checkpoint_sha256": str(row["checkpoint_sha256"]),
        "classification": str(row["classification"]),
        "eligible": bool(row["eligible"]),
        "selection_score": float(row["selection_score"]),
        "heldout_correct_policy_kl": float(row["heldout_correct_policy_kl"]),
        "positive_task_count": int(metrics["positive_task_count"]),
        "task_count": int(metrics["task_count"]),
        "stable_generation": bool(row["stable_generation"]),
        "every_nonzero_condition_used_complete_field": bool(
            row["every_nonzero_condition_used_complete_field"]
        ),
        "metrics": {
            key: dict(metrics[key])
            for key in (
                "L0_zero",
                "L1_correct",
                "L2_key_payload_shuffle",
                "L3_state_query_shuffle",
            )
        },
    }


def _checkpoint_selection_evidence(formal_root: Path) -> dict[str, Any]:
    paths = _formal_key_paths(formal_root)
    selection = _json(paths["checkpoint_selection"])
    validation = _json(paths["live_validation_summary"])
    teacher = _json(paths["teacher_forced_summary"])
    trajectory = _json(paths["full_trajectory_summary"])
    candidates = [_compact_live_candidate(row) for row in selection["candidates"]]
    teacher_reports = []
    for row in teacher["reports"]:
        teacher_reports.append(
            {
                "epoch": int(row["epoch"]),
                "checkpoint_sha256": str(row["checkpoint_sha256"]),
                "state_count": int(row["state_count"]),
                "correct_minus_zero_target_nll": float(
                    row["correct_minus_zero_target_nll"]
                ),
                "correct_minus_shuffle_target_nll": float(
                    row["correct_minus_shuffle_target_nll"]
                ),
                "metrics": dict(row["metrics"]),
            }
        )
    trajectory_reports = [
        {
            "condition": str(row["condition"]),
            "condition_name": str(row["condition_name"]),
            "task_count": int(row["task_count"]),
            "success_count": int(row["success_count"]),
            "success_ids": list(row["success_ids"]),
            "counts": dict(row.get("counts", {})),
            "total_steps": int(row["total_steps"]),
            "stable_prompt_profile": str(row["prompt_profile"])
            == "full_demo_first_only",
        }
        for row in trajectory["summaries"]
    ]
    selected = selection["selected"]
    checks = {
        "selected_epoch_is_1": int(selected["epoch"]) == 1,
        "epoch_1_strong": candidates[0]["classification"] == "STRONG",
        "epoch_2_partial": candidates[1]["classification"] == "PARTIAL",
        "both_eligible": all(row["eligible"] for row in candidates),
        "heldout_train_only": selection.get("heldout_train_only_selection") is True,
        "dev_not_used": selection.get("test_normal_outcomes_used") is False,
        "trajectory_complete": trajectory.get("complete") is True,
        "trajectory_did_not_change_selection": selection.get(
            "complete_heldout_trajectory_evidence", {}
        ).get("checkpoint_selection_changed")
        is False,
        "validation_has_two_epochs": len(validation.get("reports", [])) == 2,
        "teacher_has_two_epochs": len(teacher_reports) == 2,
    }
    return {
        "format": "exp037a_r19_formal_checkpoint_selection_evidence_v1",
        "candidates": candidates,
        "teacher_forced": teacher_reports,
        "full_trajectory": trajectory_reports,
        "selected": {
            "epoch": int(selected["epoch"]),
            "checkpoint_sha256": str(selected["checkpoint_sha256"]),
            "classification": str(selected["classification"]),
        },
        "selection_reason": (
            "The frozen selector prioritizes STRONG over PARTIAL before comparing "
            "selection score, policy KL, or epoch. Epoch 1 is STRONG and epoch 2 "
            "is PARTIAL, so epoch 1 wins without official-dev evidence."
        ),
        "checks": checks,
        "passed": all(checks.values()),
    }


def _tensor_equal(left: torch.Tensor, right: torch.Tensor) -> bool:
    return tuple(left.shape) == tuple(right.shape) and _tensor_sha256(left) == _tensor_sha256(right)


def _formal_condition_integrity(
    formal_root: Path, condition: str, expected_tasks: Sequence[str]
) -> dict[str, Any]:
    condition_id = FORMAL_CONDITIONS[condition]
    summary_path = formal_root / (
        f"evaluation/common_one_demo_dev/summaries/{condition_id}.json"
    )
    summary = _json(summary_path)
    task_root = formal_root / (
        f"evaluation/common_one_demo_dev/conditions/{condition_id}/task_results"
    )
    rows = []
    row_identities = []
    checks = {
        "summary_condition": str(summary.get("condition")) == condition_id,
        "task_count": int(summary.get("task_count", -1)) == 57,
        "task_order": list(summary.get("manifest", {}).get("task_ids", []))
        == list(expected_tasks),
        "prompt_profile": str(summary.get("prompt_profile"))
        == "full_demo_first_only",
        "memory_count": int(summary.get("manifest", {}).get("memory_count", -1))
        == 499,
        "runtime_retrieval": summary.get("manifest", {}).get("runtime_retrieval")
        is False,
        "fresh_rows": int(summary.get("reused_task_rows", -1)) == 0,
        "all_task_files": True,
        "all_rows_complete": True,
        "row_condition": True,
        "row_task_identity": True,
        "field_control": True,
        "generation_contract": True,
        "model_identity": True,
    }
    expected_control = "correct" if condition == "correct" else "key_payload_shuffle"
    for task_id in expected_tasks:
        path = task_root / f"{task_id}.json"
        if not path.is_file():
            checks["all_task_files"] = False
            continue
        row = _json(path)
        rows.append(row)
        row_identities.append(file_identity(path))
        checks["all_rows_complete"] &= row.get("status") == "complete"
        checks["row_condition"] &= str(row.get("condition")) == condition_id
        checks["row_task_identity"] &= str(row.get("task_id")) == task_id
        for step in row.get("steps", []):
            checks["field_control"] &= (
                str(step.get("field", {}).get("field_control")) == expected_control
            )
            generation = step.get("generation_config", {})
            checks["generation_contract"] &= (
                int(generation.get("seed", -1)) == 25101
                and float(generation.get("temperature", -1.0)) == 0.0
                and float(generation.get("top_p", -1.0)) == 1.0
                and generation.get("do_sample") is False
                and generation.get("enable_thinking") is False
                and int(step.get("context_limit", -1)) == 40960
            )
            checks["model_identity"] &= (
                str(step.get("model_identity", {}).get("model_name"))
                == "Qwen/Qwen3-8B"
            )
    recomputed = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    checks["summary_success_recomputed"] = recomputed == sorted(summary["success_ids"])
    checks["summary_count_recomputed"] = len(recomputed) == int(summary["success_count"])
    return {
        "condition": condition,
        "condition_id": condition_id,
        "summary": file_identity(summary_path),
        "success_count": len(recomputed),
        "success_ids": recomputed,
        "task_outputs": row_identities,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _formal_shuffle_integrity(
    formal_root: Path, pipeline_config: Path
) -> dict[str, Any]:
    paths = _formal_key_paths(formal_root)
    cfg = load_config(pipeline_config)
    task_ids = _dev_task_ids(cfg.raw)
    selection = _json(paths["checkpoint_selection"])
    shuffle = _json(paths["shuffle_manifest"])
    complete = shuffle["complete_deployment_bank"]
    rows = list(complete["rows"])
    keys = [str(row["key_transition_id"]) for row in rows]
    payloads = [str(row["payload_transition_id"]) for row in rows]
    source = torch.load(paths["source_cache"], map_location="cpu", weights_only=False)
    ordered_ids = [str(value) for value in source["ordered_transition_ids"]]
    selected_401 = torch.load(paths["selected_401"], map_location="cpu", weights_only=False)
    deployment = torch.load(paths["formal_deployment"], map_location="cpu", weights_only=False)
    correct = _formal_condition_integrity(formal_root, "correct", task_ids)
    shuffled = _formal_condition_integrity(formal_root, "shuffle", task_ids)
    correct_outputs = {
        Path(str(row["path"])).name: Path(str(row["path"])).stat().st_ino
        for row in correct["task_outputs"]
    }
    shuffled_outputs = {
        Path(str(row["path"])).name: Path(str(row["path"])).stat().st_ino
        for row in shuffled["task_outputs"]
    }
    checks = {
        "formal_selected_epoch_1": int(selection["selected"]["epoch"]) == 1,
        "checkpoint_hash": str(deployment.get("checkpoint_sha256"))
        == EPOCH_CHECKPOINTS[1],
        "selected_401_checkpoint": str(selected_401.get("checkpoint_sha256"))
        == EPOCH_CHECKPOINTS[1],
        "selected_401_count": int(selected_401.get("memory_count", -1)) == 401,
        "deployment_count": int(deployment.get("memory_count", -1)) == 499,
        "deployment_ids": list(deployment.get("memory_ids", []))
        == sorted(ordered_ids),
        "deployment_ids_unique": len(set(deployment.get("memory_ids", []))) == 499,
        "shuffle_row_count": len(rows) == 499,
        "shuffle_key_bijection": len(set(keys)) == 499 and set(keys) == set(ordered_ids),
        "shuffle_payload_bijection": len(set(payloads)) == 499
        and set(payloads) == set(ordered_ids),
        "shuffle_fixed_points": sum(left == right for left, right in zip(keys, payloads, strict=True))
        == 0,
        "sealed_fixed_point_count": int(complete.get("fixed_point_count", -1)) == 0,
        "finite_fields": all(
            bool(torch.isfinite(deployment[name]).all())
            for name in ("A", "B", "shuffled_A", "shuffled_B")
        ),
        "same_B_normalizer": _tensor_equal(deployment["B"], deployment["shuffled_B"]),
        "correct_and_shuffle_differ": not _tensor_equal(
            deployment["A"], deployment["shuffled_A"]
        ),
        "correct_condition": bool(correct["passed"]),
        "shuffle_condition": bool(shuffled["passed"]),
        "condition_task_sets_match": correct["success_count"] == 8
        and shuffled["success_count"] == 18,
        "task_files_not_hardlinked": all(
            correct_outputs.get(name) != shuffled_outputs.get(name)
            for name in set(correct_outputs) & set(shuffled_outputs)
        ),
    }
    return {
        "format": "exp037a_r19_formal_epoch1_shuffle_integrity_v1",
        "checkpoint_sha256": EPOCH_CHECKPOINTS[1],
        "deployment_field": file_identity(paths["formal_deployment"]),
        "memory_count": 499,
        "memory_id_sha256": content_sha256(sorted(ordered_ids)),
        "key_sha256": content_sha256(keys),
        "payload_sha256": content_sha256(payloads),
        "permutation_file": file_identity(paths["shuffle_manifest"]),
        "permutation_sha256": content_sha256(rows),
        "fixed_point_count": sum(
            left == right for left, right in zip(keys, payloads, strict=True)
        ),
        "correct": correct,
        "shuffle": shuffled,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    formal_root = args.formal_root.resolve(strict=True)
    diagnostic_root = args.diagnostic_root.resolve(strict=False)
    _assert_roots(formal_root, diagnostic_root)
    diagnostic_root.mkdir(parents=True, exist_ok=True)
    (diagnostic_root / "preflight").mkdir(parents=True, exist_ok=True)
    formal_config = formal_root / "resolved_configs/arm_1d.yaml"
    resolved_target = diagnostic_root / "resolved_configs/arm_1d.yaml"
    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    if resolved_target.exists():
        if _sha(resolved_target) != _sha(formal_config):
            raise ValueError("Existing diagnostic arm config differs from formal source")
    else:
        shutil.copy2(formal_config, resolved_target)
    snapshot = _formal_snapshot(formal_root)
    _write(diagnostic_root / "preflight/formal_immutability_pre.json", snapshot)
    stages = {}
    for stage_dir in sorted((formal_root / "stages").iterdir()):
        if not stage_dir.is_dir():
            continue
        stages[stage_dir.name] = validate_stage_completion(
            stage_dir,
            FORMAL_SOURCE,
            expected_run_uuid=FORMAL_UUID,
            expected_pipeline_config_sha256=FORMAL_CONFIG_SHA256,
            expected_contract_sha256=FORMAL_CONTRACT_SHA256,
            expected_run_root=formal_root,
            write_validator=False,
        )
    parent_manifest = _json(formal_root / "preflight/parent_artifact_manifest.json")
    parent = validate_parent_artifact_manifest(
        parent_manifest, parent_root=parent_manifest["parent"]["run_root"]
    )
    checkpoint_hashes = {
        epoch: _sha(
            formal_root / f"arms/1d/joint_training/checkpoints/epoch_{epoch:02d}.pt"
        )
        for epoch in (1, 2)
    }
    selection = _checkpoint_selection_evidence(formal_root)
    integrity = _formal_shuffle_integrity(formal_root, args.pipeline_config)
    _write(diagnostic_root / "preflight/checkpoint_selection_evidence.json", selection)
    _write(diagnostic_root / "preflight/formal_epoch1_shuffle_integrity.json", integrity)
    contract = {
        "format": "exp037a_r19_epoch2_forced_dev_contract_v1",
        "diagnostic_uuid": DIAGNOSTIC_UUID,
        "formal_source": FORMAL_SOURCE,
        "formal_run_uuid": FORMAL_UUID,
        "formal_root": str(formal_root),
        "diagnostic_root": str(diagnostic_root),
        "diagnostic_source": args.diagnostic_source,
        "checkpoint": {str(epoch): checkpoint_hashes[epoch] for epoch in (1, 2)},
        "conditions": list(CONDITIONS),
        "official_dev_tasks": 57,
        "hard_cap_hours": 10,
        "expected_wall_hours": 5,
        "conservative_wall_hours": 8,
        "training_authorized": False,
        "backward_authorized": False,
        "optimizer_step_authorized": False,
        "formal_selection_remains_epoch": 1,
    }
    contract["contract_sha256"] = content_sha256(contract)
    _write(diagnostic_root / "preflight/diagnostic_contract.json", contract)
    checks = {
        "formal_source": snapshot["files"]["orchestrator_result"]["path"].startswith(
            str(formal_root)
        ),
        "formal_orchestrator_complete": _json(
            formal_root / "orchestrator_result.json"
        ).get("status")
        == "complete",
        "formal_stage_count": len(stages) == 18,
        "formal_stages_strict_valid": all(row["passed"] for row in stages.values()),
        "epoch_1_hash": checkpoint_hashes[1] == EPOCH_CHECKPOINTS[1],
        "epoch_2_hash": checkpoint_hashes[2] == EPOCH_CHECKPOINTS[2],
        "deployment_hash": _sha(
            formal_root / "arms/1d/deployment_field/complete_37_task_field.pt"
        )
        == FORMAL_DEPLOYMENT_SHA256,
        "parent_closure": parent["passed"]
        and parent["artifact_closure_sha256"] == PARENT_CLOSURE_SHA256,
        "checkpoint_selection": selection["passed"],
        "shuffle_integrity": integrity["passed"],
        "diagnostic_and_formal_disjoint": True,
    }
    result = {
        "format": "exp037a_r19_epoch2_preflight_v1",
        "checks": checks,
        "strict_stage_count": len(stages),
        "strict_stage_manifest_sha256": {
            key: value["output_manifest_sha256"] for key, value in stages.items()
        },
        "parent": parent,
        "checkpoint_hashes": checkpoint_hashes,
        "formal_snapshot_sha256": content_sha256(snapshot),
        "diagnostic_contract_sha256": contract["contract_sha256"],
        "training_operations": {"backward": 0, "optimizer_step": 0},
        "passed": all(checks.values()),
    }
    _write(diagnostic_root / "preflight/preflight_summary.json", result)
    if not result["passed"]:
        raise RuntimeError(f"R19 preflight failed: {checks}")
    return result


def _tensor_record(value: torch.Tensor) -> dict[str, Any]:
    work = value.detach().to(device="cpu", dtype=torch.float32)
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "sha256": _tensor_sha256(value),
        "norm": float(work.norm()),
        "finite": bool(torch.isfinite(work).all()),
    }


def _field_records(path: Path) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    names = [name for name in ("A", "B", "shuffled_A", "shuffled_B") if name in payload]
    return {
        "file": file_identity(path),
        "checkpoint_sha256": str(payload.get("checkpoint_sha256")),
        "memory_count": int(payload.get("memory_count", -1)),
        "memory_ids_sha256": content_sha256(list(payload.get("memory_ids", []))),
        "tensors": {name: _tensor_record(payload[name]) for name in names},
    }


def _construct(args: argparse.Namespace) -> dict[str, Any]:
    root = args.diagnostic_root.resolve(strict=True)
    preflight = _json(root / "preflight/preflight_summary.json")
    if not preflight.get("passed"):
        raise RuntimeError("R19 field construction requires passing preflight")
    formal_arm = _formal_arm(args.formal_root.resolve(strict=True))
    cfg = load_config(root / "resolved_configs/arm_1d.yaml")
    settings = cfg.raw["stage_c_9a"]
    paths = _paths(settings, formal_arm)
    data = _load_data(paths)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda" or "H100" not in torch.cuda.get_device_name(0):
        raise RuntimeError("R19 field construction requires the approved H100")
    tensors = _runtime_tensors(data, device)
    formal_live = formal_arm / "heldout_validation/live_full_field/field_artifacts"
    reports: dict[str, Any] = {}
    for epoch in (1, 2):
        epoch_root = root / f"fields/epoch_{epoch:02d}"
        field_dir = epoch_root / "heldout_401_fields"
        deployment_dir = epoch_root / "deployment_field"
        field_dir.mkdir(parents=True, exist_ok=True)
        deployment_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = formal_arm / f"joint_training/checkpoints/epoch_{epoch:02d}.pt"
        checkpoint_sha = _sha(checkpoint_path)
        if checkpoint_sha != EPOCH_CHECKPOINTS[epoch]:
            raise ValueError(f"Epoch {epoch} checkpoint SHA differs")
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
        writer, _ = _build_components(device)
        writer.load_state_dict(checkpoint["writer_state_dict"], strict=True)
        writer.eval()
        for parameter in writer.parameters():
            parameter.requires_grad_(False)
        with torch.no_grad():
            bundle = _field_bundle(
                writer=writer,
                tensors=tensors,
                data=data,
                epoch=epoch,
                live={"field_artifacts": field_dir},
                checkpoint_sha256=checkpoint_sha,
            )
        live = {
            "deployment_field": deployment_dir / "complete_37_task_field.pt",
            "instant_add": deployment_dir / "instant_add_report.json",
        }
        with torch.no_grad():
            instant = _instant_add(
                paths=paths,
                live=live,
                data=data,
                selection={
                    "selected": {
                        "epoch": epoch,
                        "checkpoint": str(checkpoint_path),
                        "checkpoint_sha256": checkpoint_sha,
                    }
                },
            )
        del bundle, checkpoint, writer
        torch.cuda.empty_cache()
        correct_path = field_dir / f"epoch_{epoch:02d}_correct.pt"
        shuffle_path = field_dir / f"epoch_{epoch:02d}_key_payload_shuffle.pt"
        report = {
            "epoch": epoch,
            "checkpoint": file_identity(checkpoint_path),
            "correct_401": _field_records(correct_path),
            "shuffle_401": _field_records(shuffle_path),
            "deployment_499": _field_records(live["deployment_field"]),
            "instant_add": dict(instant),
            "backward_count": 0,
            "optimizer_step_count": 0,
        }
        if epoch == 1:
            formal_correct = _field_records(formal_live / "epoch_01_correct.pt")
            formal_shuffle = _field_records(
                formal_live / "epoch_01_key_payload_shuffle.pt"
            )
            formal_deployment = _field_records(
                formal_arm / "deployment_field/complete_37_task_field.pt"
            )
            calibration = {
                "correct_401_A": report["correct_401"]["tensors"]["A"]["sha256"]
                == formal_correct["tensors"]["A"]["sha256"],
                "correct_401_B": report["correct_401"]["tensors"]["B"]["sha256"]
                == formal_correct["tensors"]["B"]["sha256"],
                "shuffle_401_A": report["shuffle_401"]["tensors"]["A"]["sha256"]
                == formal_shuffle["tensors"]["A"]["sha256"],
                "shuffle_401_B": report["shuffle_401"]["tensors"]["B"]["sha256"]
                == formal_shuffle["tensors"]["B"]["sha256"],
                **{
                    f"deployment_{name}": report["deployment_499"]["tensors"][name]["sha256"]
                    == formal_deployment["tensors"][name]["sha256"]
                    for name in ("A", "B", "shuffled_A", "shuffled_B")
                },
                "memory_ids": report["deployment_499"]["memory_ids_sha256"]
                == formal_deployment["memory_ids_sha256"],
                "checkpoint": report["deployment_499"]["checkpoint_sha256"]
                == EPOCH_CHECKPOINTS[1],
            }
            report["formal_calibration"] = {
                "checks": calibration,
                "formal_correct_401": formal_correct,
                "formal_shuffle_401": formal_shuffle,
                "formal_deployment_499": formal_deployment,
                "passed": all(calibration.values()),
            }
            if not report["formal_calibration"]["passed"]:
                _write(root / "field_construction_failure.json", report)
                raise RuntimeError("DIAGNOSTIC_FIELD_RECONSTRUCTION_MISMATCH")
        reports[str(epoch)] = report
    result = {
        "format": "exp037a_r19_epoch2_field_construction_v1",
        "device": str(device),
        "hardware": torch.cuda.get_device_name(0),
        "epochs": reports,
        "epoch_1_calibration_passed": bool(
            reports["1"]["formal_calibration"]["passed"]
        ),
        "epoch_2_checkpoint_forced_diagnostic_only": True,
        "formal_checkpoint_selection_modified": False,
        "training_operations": {"backward": 0, "optimizer_step": 0},
        "passed": True,
    }
    _write(root / "field_construction.json", result)
    return result


def _set_diagnostic_environment(args: argparse.Namespace) -> None:
    contract = _json(args.diagnostic_root / "preflight/diagnostic_contract.json")
    os.environ["RCMF_PIPELINE_RUN_UUID"] = DIAGNOSTIC_UUID
    os.environ["RCMF_PIPELINE_RUN_ROOT"] = str(
        args.diagnostic_root.resolve(strict=True)
    )
    os.environ["RCMF_PIPELINE_CONFIG_SHA256"] = _sha(
        args.diagnostic_root / "resolved_configs/arm_1d.yaml"
    )
    os.environ["RCMF_PIPELINE_CONTRACT_SHA256"] = str(
        contract["contract_sha256"]
    )
    os.environ["PYTHONHASHSEED"] = "25101"


def _evaluate(args: argparse.Namespace, condition: str) -> dict[str, Any]:
    if condition not in CONDITIONS:
        raise ValueError("R19 evaluation requires correct or shuffle")
    root = args.diagnostic_root.resolve(strict=True)
    construction = _json(root / "field_construction.json")
    if not construction.get("passed") or not construction.get(
        "epoch_1_calibration_passed"
    ):
        raise RuntimeError("R19 evaluation requires calibrated fields")
    _set_diagnostic_environment(args)
    pipeline = load_config(args.pipeline_config)
    task_ids = _dev_task_ids(pipeline.raw)
    if len(task_ids) != 57:
        raise ValueError("R19 official dev task count differs")
    condition_id, field_control = CONDITIONS[condition]
    epoch_root = root / "fields/epoch_02/deployment_field"
    started = time.perf_counter()
    result = _run_task_set(
        run_root=root,
        arm_id="1d",
        task_ids=task_ids,
        output_root=root / "evaluation/epoch2_dev",
        condition_id=condition_id,
        condition_name=condition_id,
        field_control=field_control,
        prompt_profile="full_demo_first_only",
        correct_field=epoch_root / "complete_37_task_field.pt",
        shuffled_field=None,
        checkpoint=None,
        provenance=epoch_root / "instant_add_report.json",
        memory_count=499,
        source_commit=args.diagnostic_source,
        attempt_id=f"r19-epoch2-{condition}-{time.time_ns()}",
        deployment_bundle=True,
    )
    result["diagnostic_wall_seconds"] = time.perf_counter() - started
    result["forced_checkpoint_epoch"] = 2
    result["forced_checkpoint_sha256"] = EPOCH_CHECKPOINTS[2]
    result["formal_checkpoint_selection_modified"] = False
    result["formal_task_outputs_reused"] = False
    result["training_operations"] = {"backward": 0, "optimizer_step": 0}
    _write(root / f"evaluation/epoch2_{condition}_summary.json", result)
    return result


def _mcnemar_exact_two_sided(left_only: int, right_only: int) -> float:
    n = left_only + right_only
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(left_only, right_only) + 1))
    return min(1.0, 2.0 * tail / (2**n))


def _artifact_index(root: Path) -> dict[str, Any]:
    path = root / "artifact_index.json"
    rows = []
    for item in sorted(value for value in root.rglob("*") if value.is_file()):
        if item == path:
            continue
        rows.append(
            {
                "path": str(item.relative_to(root)),
                "size_bytes": item.stat().st_size,
                "sha256": _sha(item),
            }
        )
    return {
        "format": "exp037a_r19_lambda_artifact_index_v1",
        "diagnostic_uuid": DIAGNOSTIC_UUID,
        "artifact_count": len(rows),
        "artifacts": rows,
    }


def _finalize(args: argparse.Namespace) -> dict[str, Any]:
    root = args.diagnostic_root.resolve(strict=True)
    correct = _json(root / "evaluation/epoch2_correct_summary.json")
    shuffle = _json(root / "evaluation/epoch2_shuffle_summary.json")
    if int(correct["task_count"]) != 57 or int(shuffle["task_count"]) != 57:
        raise ValueError("R19 epoch-2 dev condition is incomplete")
    if int(correct.get("reused_task_rows", -1)) != 0 or int(
        shuffle.get("reused_task_rows", -1)
    ) != 0:
        raise ValueError("R19 primary evaluation reused diagnostic task outputs")
    correct_by = {str(k): bool(v) for k, v in correct["success_by_task"].items()}
    shuffle_by = {str(k): bool(v) for k, v in shuffle["success_by_task"].items()}
    if list(correct_by) != list(shuffle_by):
        raise ValueError("R19 condition task identities/order differ")
    correct_only = sorted(k for k in correct_by if correct_by[k] and not shuffle_by[k])
    shuffle_only = sorted(k for k in correct_by if shuffle_by[k] and not correct_by[k])
    both = sorted(k for k in correct_by if correct_by[k] and shuffle_by[k])
    neither = sorted(k for k in correct_by if not correct_by[k] and not shuffle_by[k])
    correct_count = int(correct["success_count"])
    shuffle_count = int(shuffle["success_count"])
    if correct_count > 12 and correct_count > shuffle_count:
        classification = "EPOCH2_DIAGNOSTIC_RESCUES_DIRECTION"
    elif correct_count <= 12 and correct_count <= shuffle_count:
        classification = "EPOCH2_DIAGNOSTIC_NO_RESCUE"
    else:
        classification = "EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE"
    comparison = {
        "correct_only": correct_only,
        "shuffle_only": shuffle_only,
        "both": both,
        "neither": neither,
        "mcnemar_exact_two_sided_p": _mcnemar_exact_two_sided(
            len(correct_only), len(shuffle_only)
        ),
        "epoch2_correct_minus_bare": correct_count - 12,
        "epoch2_correct_minus_epoch2_shuffle": correct_count - shuffle_count,
        "epoch2_correct_minus_epoch1_correct": correct_count - 8,
        "epoch2_shuffle_minus_epoch1_shuffle": shuffle_count - 18,
        "epoch2_correct_minus_three_demo_correct": correct_count - 17,
        "per_task": [
            {
                "task_id": task,
                "epoch2_correct": correct_by[task],
                "epoch2_shuffle": shuffle_by[task],
                "category": "both"
                if correct_by[task] and shuffle_by[task]
                else "correct_only"
                if correct_by[task]
                else "shuffle_only"
                if shuffle_by[task]
                else "neither",
            }
            for task in correct_by
        ],
    }
    condition_metrics = {}
    for name, row in (("correct", correct), ("shuffle", shuffle)):
        steps = int(row["total_steps"])
        condition_metrics[name] = {
            "condition": row["condition"],
            "success_count": int(row["success_count"]),
            "success_ids": list(row["success_ids"]),
            "total_steps": steps,
            "total_prompt_tokens": int(row["total_prompt_tokens"]),
            "total_completion_tokens": int(row["total_generated_tokens"]),
            "average_prompt_tokens_per_step": float(row["total_prompt_tokens"])
            / steps,
            "context_overflow_count": int(row.get("counts", {}).get("context_overflow", 0)),
            "repeated_action_count": int(row.get("counts", {}).get("repeated_action", 0)),
            "completion_action_count": int(row.get("counts", {}).get("completion_action", 0)),
            "execution_exception_count": int(row.get("counts", {}).get("execution_exception", 0)),
            "total_task_wall_seconds": float(row["total_wall_seconds"]),
            "diagnostic_wall_seconds": float(row["diagnostic_wall_seconds"]),
        }
    pre = _json(root / "preflight/formal_immutability_pre.json")
    post = _formal_snapshot(args.formal_root.resolve(strict=True))
    immutable = content_sha256(pre) == content_sha256(post)
    _write(root / "formal_immutability_post.json", post)
    result = {
        "format": "exp037a_r19_epoch2_forced_dev_result_v1",
        "classification": classification,
        "post_hoc_checkpoint_sensitivity_diagnostic": True,
        "formal_14n_result_remains_unchanged": True,
        "formal_selected_checkpoint_epoch": 1,
        "diagnostic_forced_checkpoint_epoch": 2,
        "checkpoint_sha256": EPOCH_CHECKPOINTS[2],
        "conditions": condition_metrics,
        "comparison": comparison,
        "sealed_comparators": {
            "shared_bare": 12,
            "three_demo_correct": 17,
            "three_demo_shuffle": 11,
            "formal_epoch1_correct": 8,
            "formal_epoch1_shuffle": 18,
        },
        "formal_epoch1_shuffle_integrity_passed": _json(
            root / "preflight/formal_epoch1_shuffle_integrity.json"
        )["passed"],
        "epoch1_field_reconstruction_calibration_passed": _json(
            root / "field_construction.json"
        )["epoch_1_calibration_passed"],
        "formal_root_immutable": immutable,
        "training_operations": {"backward": 0, "optimizer_step": 0},
        "follow_on_experiment_launched": False,
        "passed_infrastructure": immutable,
    }
    _write(root / "final_result.json", result)
    index = _artifact_index(root)
    _write(root / "artifact_index.json", index)
    if not immutable:
        raise RuntimeError("Formal 14n root changed during R19")
    return result


def main() -> None:
    args = _parse_args()
    args.formal_root = args.formal_root.resolve(strict=True)
    args.diagnostic_root = args.diagnostic_root.resolve(strict=False)
    args.pipeline_config = args.pipeline_config.resolve(strict=True)
    _assert_roots(args.formal_root, args.diagnostic_root)
    started = time.time()
    if args.phase in ("preflight", "all"):
        print(json.dumps(_preflight(args), sort_keys=True), flush=True)
    if args.phase in ("construct", "all"):
        print(json.dumps(_construct(args), sort_keys=True), flush=True)
    if args.phase == "evaluate":
        if args.condition is None:
            raise ValueError("--condition is required for evaluate")
        print(json.dumps(_evaluate(args, args.condition), sort_keys=True), flush=True)
    if args.phase == "all":
        for condition in CONDITIONS:
            print(json.dumps(_evaluate(args, condition), sort_keys=True), flush=True)
    if args.phase in ("finalize", "all"):
        print(json.dumps(_finalize(args), sort_keys=True), flush=True)
    print(
        json.dumps(
            {
                "status": "complete",
                "phase": args.phase,
                "wall_seconds": time.time() - started,
                "backward_count": 0,
                "optimizer_step_count": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
