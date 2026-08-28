from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    RCMFFieldRecord,
    ReversibleRCMFField,
    compile_differentiable_field,
    tensor_sha256,
)
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    GLOBAL_SEED,
    first37_decision,
    strict_no_progress_loops,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _build_components,
    _load_data,
    _paths as parent_paths,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task
from scripts.run_rcmf_q90_trajectory_common_9c import (
    first_divergence,
    load_frozen_backend,
)
from scripts.run_rcmf_trajectory_union_heldout_10a import (
    CandidateFieldRuntime,
    module_state_sha256_from_state,
)


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
RESULT_FORMAT = "rcmf_trajectory_union_first37_task_10a_v1"


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
    parser.add_argument("--phase", choices=("prepare", "run", "finalize"), required=True)
    parser.add_argument("--condition", choices=("N1", "N2"))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_first37")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in read_jsonl(path)
        if row.get("attempt_id") is not None
    }


def paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "first37"
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "field": root / "final_candidate_499_field.pt",
        "field_report": root / "final_candidate_499_field_report.json",
        "migration": root / "instant_memory_recompilation.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": root / "final_candidate_499_field.pt",
        "instant_add": root / "instant_memory_recompilation.json",
        "field_provenance": root / "final_candidate_499_field_report.json",
        "final": root / "final_summary.json",
        "selection": artifact_dir / "heldout/candidate_selection.json",
    }


def _first37_ids(settings: Mapping[str, Any]) -> list[str]:
    payload = load_config(Path(str(settings["first37"]["task_manifest_config"])))
    values = [str(value) for value in payload.raw["stage_c_7f"]["first37"]["task_ids"]]
    if len(values) != 37 or len(set(values)) != 37:
        raise ValueError("Locked first37 task manifest differs")
    return values


def _complete_permutation(
    *, shuffle_manifest: Mapping[str, Any], ordered_ids: Sequence[str]
) -> torch.Tensor:
    rows = shuffle_manifest["complete_deployment_bank"]["rows"]
    mapping = {
        str(row["key_transition_id"]): str(row["payload_transition_id"])
        for row in rows
    }
    positions = {str(value): index for index, value in enumerate(ordered_ids)}
    permutation = torch.tensor([positions[mapping[str(value)]] for value in ordered_ids])
    if bool((permutation == torch.arange(len(ordered_ids))).any()):
        raise ValueError("Complete deployment shuffle has a fixed point")
    return permutation


def _compile_deployment(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    selection: Mapping[str, Any],
    output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = selection["selected_candidate"]
    checkpoint_path = Path(str(candidate["checkpoint"]))
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    reader_state = checkpoint["reader_state_dict"]
    writer_state = checkpoint["writer_state_dict"]
    stage = str(candidate["stage"])
    started = time.perf_counter()
    if stage == "reader_only":
        immutable_path = Path(str(settings["immutable_exp031a"]["deployment_field"]))
        if sha256_file(immutable_path) != str(
            settings["immutable_exp031a"]["deployment_field_sha256"]
        ):
            raise ValueError("Immutable 499-memory field differs")
        frozen = torch.load(immutable_path, map_location="cpu", weights_only=False)
        A, B = frozen["A"].float(), frozen["B"].float()
        shuffled_A, shuffled_B = frozen["shuffled_A"].float(), frozen["shuffled_B"].float()
        migration = {
            "format": "rcmf_trajectory_union_instant_recompilation_10a_v1",
            "writer_changed": False,
            "immutable_499_field_reused": True,
            "deployment_field_source_sha256": sha256_file(immutable_path),
            "optimizer_steps": 0,
            "runtime_old_record_scan": False,
            "memory_count_independent_read": True,
            "passed": True,
        }
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
        data = _load_data(parent_paths(cfg.raw["stage_c_9a"], parent_root))
        source = data["source"]
        ordered_ids = [str(value) for value in source["ordered_transition_ids"]]
        if len(ordered_ids) != 499:
            raise ValueError("Complete memory ledger count differs")
        transitions = data["transition_by_id"]
        manifest = data["data_manifest"]
        keys = source["memory_keys"].to(device, torch.float32)
        views = source["memory_views"].to(device, torch.float32)
        rho = torch.tensor(
            [float(manifest["rho_by_transition_id"][memory_id]) for memory_id in ordered_ids],
            device=device,
            dtype=torch.float32,
        )
        shuffle = _json(Path(str(settings["immutable_exp031a"]["shuffle_manifest"])))
        permutation = _complete_permutation(
            shuffle_manifest=shuffle, ordered_ids=ordered_ids
        ).to(device)
        writer, _ = _build_components(device)
        writer.load_state_dict(writer_state)
        writer.eval()
        train_tasks = set(str(value) for value in manifest["train_task_ids"])
        train_indices = [
            index
            for index, memory_id in enumerate(ordered_ids)
            if str(transitions[memory_id]["parent_task_id"]) in train_tasks
        ]
        heldout_indices = [
            index for index in range(len(ordered_ids)) if index not in set(train_indices)
        ]
        if len(train_indices) != 401 or len(heldout_indices) != 98:
            raise ValueError("401/98 memory migration split differs")
        with torch.no_grad():
            train_started = time.perf_counter()
            train_payloads = writer(views[train_indices])
            train_compile_seconds = time.perf_counter() - train_started
            heldout_started = time.perf_counter()
            heldout_payloads = writer(views[heldout_indices])
            if device.type == "cuda":
                torch.cuda.synchronize()
            heldout_compile_seconds = time.perf_counter() - heldout_started
            payloads = torch.empty(
                (499, 8, 256), device=device, dtype=torch.float32
            )
            payloads[train_indices] = train_payloads
            payloads[heldout_indices] = heldout_payloads
            shuffled_A, shuffled_B = compile_differentiable_field(
                keys=keys, payloads=payloads[permutation], rho=rho
            )
        field = ReversibleRCMFField(device=device)
        add_seconds = 0.0
        records = []
        for index, memory_id in enumerate(ordered_ids):
            parent = str(transitions[memory_id]["parent_task_id"])
            records.append(
                RCMFFieldRecord(
                    memory_id=memory_id,
                    parent_id=parent,
                    parent_task_id=parent,
                    key=keys[index],
                    payload=payloads[index],
                    rho=float(rho[index]),
                )
            )
        for index in train_indices:
            field.add_memory_fast(records[index])
        A401, B401 = field.A.clone(), field.B.clone()
        for index in heldout_indices:
            add_started = time.perf_counter()
            field.add_memory_fast(records[index])
            if device.type == "cuda":
                torch.cuda.synchronize()
            add_seconds += time.perf_counter() - add_started
        A, B = field.A.clone(), field.B.clone()
        explicit_A, explicit_B = compile_differentiable_field(
            keys=keys, payloads=payloads, rho=rho
        )
        add_error = max(
            float((A - explicit_A).abs().max().cpu()),
            float((B - explicit_B).abs().max().cpu()),
        )
        removed = [field.remove_memory_fast(ordered_ids[index]) for index in heldout_indices]
        remove_error = max(
            float((field.A - A401).abs().max().cpu()),
            float((field.B - B401).abs().max().cpu()),
        )
        field.restore_parent_fast([])  # validates no-op production path
        for record in removed:
            field.add_memory_fast(record)
        restore_error = max(
            float((field.A - A).abs().max().cpu()),
            float((field.B - B).abs().max().cpu()),
        )
        A, B = A.cpu(), B.cpu()
        shuffled_A, shuffled_B = shuffled_A.cpu(), shuffled_B.cpu()
        migration = {
            "format": "rcmf_trajectory_union_instant_recompilation_10a_v1",
            "writer_changed": True,
            "optimizer_steps": 0,
            "train_memory_count": 401,
            "new_memory_count": 98,
            "train_writer_compile_seconds": train_compile_seconds,
            "heldout_writer_compile_seconds": heldout_compile_seconds,
            "per_memory_writer_compile_seconds": heldout_compile_seconds / 98.0,
            "total_field_add_seconds": add_seconds,
            "per_memory_field_add_seconds": add_seconds / 98.0,
            "total_migration_seconds": train_compile_seconds
            + heldout_compile_seconds
            + add_seconds,
            "field_shape_before": [list(A401.shape), list(B401.shape)],
            "field_shape_after": [list(A.shape), list(B.shape)],
            "add_explicit_sum_max_abs": add_error,
            "remove_max_abs": remove_error,
            "restore_max_abs": restore_error,
            "runtime_old_record_scan": False,
            "memory_count_independent_read": True,
            "passed": max(add_error, remove_error, restore_error) <= 5.0e-5,
        }
        if not migration["passed"]:
            raise RuntimeError("Instant memory recompilation invariant failed")
    payload = {
        "format": "rcmf_trajectory_union_final_deployment_field_10a_v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_stage": stage,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "writer_sha256": module_state_sha256_from_state(writer_state),
        "reader_sha256": module_state_sha256_from_state(reader_state),
        "memory_count": 499,
        "A": A,
        "B": B,
        "shuffled_A": shuffled_A,
        "shuffled_B": shuffled_B,
        "reader_state_dict": reader_state,
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
    }
    _atomic_torch_save(payload, output)
    report = {
        "format": "rcmf_trajectory_union_final_field_report_10a_v1",
        "candidate_id": candidate["candidate_id"],
        "field_sha256": sha256_file(output),
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "writer_sha256": payload["writer_sha256"],
        "reader_sha256": payload["reader_sha256"],
        "memory_count": 499,
        "A_sha256": tensor_sha256(A),
        "B_sha256": tensor_sha256(B),
        "shuffled_A_sha256": tensor_sha256(shuffled_A),
        "shuffled_B_sha256": tensor_sha256(shuffled_B),
        "field_shape": {"A": list(A.shape), "B": list(B.shape)},
        "runtime_memory_scan": False,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": True,
    }
    return report, migration


def _manifest(
    *,
    task_ids: Sequence[str],
    selection: Mapping[str, Any],
    field_report: Mapping[str, Any],
    config_sha256: str,
) -> dict[str, Any]:
    candidate = selection["selected_candidate"]
    rows = [
        {
            "task_id": task_id,
            "condition": condition,
            "field_control": "correct" if condition == "N1" else "key_payload_shuffle",
            "candidate_id": candidate["candidate_id"],
            "memory_count": 499,
            "raw_memory_prompt": False,
            "runtime_retrieval": False,
            "outcomes_used_to_modify_model": False,
        }
        for condition in ("N1", "N2")
        for task_id in task_ids
    ]
    payload = {
        "format": "rcmf_trajectory_union_first37_manifest_10a_v1",
        "global_seed": GLOBAL_SEED,
        "task_ids": list(task_ids),
        "conditions": ["N1", "N2"],
        "task_condition_count": len(rows),
        "candidate_id": candidate["candidate_id"],
        "checkpoint_sha256": candidate["checkpoint_sha256"],
        "field_sha256": field_report["field_sha256"],
        "config_sha256": config_sha256,
        "rows": rows,
        "frozen_before_first37": True,
        "first37_outcomes_used_for_model_selection": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _result_path(p: Mapping[str, Path], condition: str, task_id: str) -> Path:
    return p["root"] / "conditions" / condition / "task_results" / f"{task_id}.json"


def _condition_summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    success_ids = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    loops = {
        str(row["task_id"]): strict_no_progress_loops(row)
        for row in rows
    }
    return {
        "format": "rcmf_trajectory_union_first37_condition_summary_10a_v1",
        "condition": condition,
        "task_count": len(rows),
        "success_count": len(success_ids),
        "success_ids": success_ids,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in rows),
        "total_generated_tokens": sum(int(row["usage"].get("completion_tokens", 0)) for row in rows),
        "strict_no_progress_loop_count": sum(len(value) for value in loops.values()),
        "strict_no_progress_loops": loops,
        "passed_infrastructure": len(rows) == 37
        and all(
            row["status"] == "complete"
            and row["success_source"] == "evaluation.success"
            and row["raw_audit_complete"]
            for row in rows
        ),
    }


def _parent_results(
    settings: Mapping[str, Any], task_ids: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    root = Path(str(settings["immutable_exp031a"]["artifact_root"])) / "first37"
    manifest = _json(root / "condition_manifest.json")
    if [str(value) for value in manifest["task_ids"]] != list(task_ids):
        raise ValueError("Immutable first37 task order differs")
    tasks = {}
    hashes = {}
    for condition in ("D0", "D1", "D2"):
        tasks[condition], hashes[condition] = {}, {}
        for task_id in task_ids:
            path = root / "conditions" / condition / "task_results" / f"{task_id}.json"
            row = _json(path)
            if (
                row["status"] != "complete"
                or row["success_source"] != "evaluation.success"
                or not row["raw_audit_complete"]
            ):
                raise ValueError("Immutable first37 control artifact is invalid")
            tasks[condition][task_id] = row
            hashes[condition][task_id] = sha256_file(path)
    return tasks, {
        "root": str(root),
        "manifest_sha256": sha256_file(root / "condition_manifest.json"),
        "task_hashes": hashes,
    }


def _finalize(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path]
) -> dict[str, Any]:
    manifest = _json(p["manifest"])
    task_ids = [str(value) for value in manifest["task_ids"]]
    candidate = {
        condition: {
            task_id: _json(_result_path(p, condition, task_id)) for task_id in task_ids
        }
        for condition in ("N1", "N2")
    }
    parent, parent_identity = _parent_results(settings, task_ids)
    success = {
        condition: sorted(task_id for task_id, row in rows.items() if bool(row["success"]))
        for condition, rows in {**parent, **candidate}.items()
    }
    stage9b = load_config(
        Path("configs/benchmark/stage_c_rcmf_benefit_preserving_calibration_9b.yaml")
    ).raw["stage_c_9b"]
    gain_families = {
        str(name): [str(value) for value in values]
        for name, values in stage9b["first37"]["gain_families"].items()
    }
    gain_ids = sorted({value for values in gain_families.values() for value in values})
    retained_ids = [str(value) for value in stage9b["critical_states"]["retained"]]
    loss_ids = [str(value) for value in stage9b["critical_states"]["losses"]]
    n1 = set(success["N1"])
    n2 = set(success["N2"])
    d0 = set(success["D0"])
    retained_gains = sorted(n1 & set(gain_ids))
    represented = sorted(
        name for name, values in gain_families.items() if n1 & set(values)
    )
    recovered = sorted(n1 & set(loss_ids))
    new_gains = sorted(n1 - d0 - set(gain_ids))
    new_gain_families = {task_id.split("_", 1)[0] for task_id in new_gains}
    summaries = {
        condition: _json(p["root"] / "conditions" / condition / "summary.json")
        for condition in ("N1", "N2")
    }
    original_d1_loops = sum(
        len(strict_no_progress_loops(parent["D1"][task_id])) for task_id in task_ids
    )
    n1_loops = int(summaries["N1"]["strict_no_progress_loop_count"])
    metrics = {
        "N1": len(n1),
        "N2": len(n2),
        "F0": len(d0),
        "retained_original_gain_count": len(retained_gains),
        "retained_original_success_count": len(n1 & set(retained_ids)),
        "gain_families": represented,
        "recovered_original_loss_count": len(recovered),
        "equivalent_new_gain_family_count": len(new_gain_families),
        "complexity_contract_valid": all(
            bool(summary["passed_infrastructure"]) for summary in summaries.values()
        ),
        "no_progress_loops_materially_increased": (
            n1_loops > 1.2 * original_d1_loops
            if original_d1_loops
            else n1_loops > 0
        ),
    }
    decision = first37_decision(metrics)
    comparisons = {
        task_id: {
            "success": {
                condition: task_id in success[condition]
                for condition in ("D0", "D1", "D2", "N1", "N2")
            },
            "D0_vs_N1": first_divergence(parent["D0"][task_id], candidate["N1"][task_id]),
            "D1_vs_N1": first_divergence(parent["D1"][task_id], candidate["N1"][task_id]),
            "N1_vs_N2": first_divergence(candidate["N1"][task_id], candidate["N2"][task_id]),
        }
        for task_id in task_ids
    }
    result = {
        "format": "rcmf_trajectory_union_first37_final_10a_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "summaries": summaries,
        "success_ids": success,
        "metrics": metrics,
        "retained_original_gain_ids": retained_gains,
        "lost_original_gain_ids": sorted(set(gain_ids) - n1),
        "represented_gain_families": represented,
        "retained_original_success_ids": sorted(n1 & set(retained_ids)),
        "recovered_original_loss_ids": recovered,
        "new_gain_ids": new_gains,
        "parent_identity": parent_identity,
        "comparisons": comparisons,
        **decision,
    }
    atomic_write_json(p["root"] / "comparisons.json", comparisons)
    atomic_write_json(p["final"], result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    p = paths(args.artifact_dir)
    selection = _json(p["selection"])
    if selection["selected_candidate"] is None:
        raise RuntimeError("No eligible heldout candidate")
    source_hashes = {
        "config": sha256_file(args.config),
        "selection": sha256_file(p["selection"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"exp032a_first37_{args.phase}_{args.condition or 'manifest'}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "prepare":
            report, migration = _compile_deployment(
                cfg=cfg, settings=settings, selection=selection, output=p["field"]
            )
            atomic_write_json(p["field_report"], report)
            atomic_write_json(p["migration"], migration)
            manifest = _manifest(
                task_ids=_first37_ids(settings),
                selection=selection,
                field_report=report,
                config_sha256=sha256_file(args.config),
            )
            atomic_write_json(p["manifest"], manifest)
            result, latest = {
                "field_report": report,
                "migration": migration,
                "manifest_sha256": manifest["manifest_sha256"],
            }, p["manifest"]
        elif args.phase == "run":
            if args.condition is None:
                raise ValueError("--condition is required")
            if args.condition == "N2" and not (
                p["root"] / "conditions/N1/summary.json"
            ).exists():
                raise RuntimeError("N1 must complete before N2")
            manifest = _json(p["manifest"])
            selected = selection["selected_candidate"]
            checkpoint = Path(str(selected["checkpoint"]))
            backend = load_frozen_backend(cfg)
            runtime = CandidateFieldRuntime(
                settings_9a=cfg.raw["stage_c_9a"],
                backend=backend,
                checkpoint_path=checkpoint,
                field_path=p["field"],
                condition_codes=("N1", "N2"),
            )
            rows = []
            for task_id in manifest["task_ids"]:
                row, _ = _run_task(
                    task_id=str(task_id),
                    condition=args.condition,
                    settings=cfg.raw["stage_c_9a"],
                    backend=backend,
                    runtime=runtime,
                    paths=p,
                    manifest=manifest,
                    config_sha256=sha256_file(args.config),
                    attempt_id=args.attempt_id,
                    smoke=False,
                    result_version=RESULT_FORMAT,
                    extra_result_fields={
                        "exp032a_final_candidate_id": selected["candidate_id"],
                        "model_frozen_before_first37": True,
                    },
                    bare_condition=False,
                    condition_name=(
                        "trajectory_union_final_correct_field"
                        if args.condition == "N1"
                        else "trajectory_union_final_key_payload_shuffle"
                    ),
                    memory_count=499,
                    field_artifact_path=p["field"],
                    field_provenance_path=p["field_report"],
                    experiment_prefix="exp032a_first37",
                )
                rows.append(row)
                attempt.progress(
                    status=f"first37_{args.condition.lower()}",
                    completed_tasks=len(rows),
                    total_tasks=37,
                    latest_validated_checkpoint=str(
                        _result_path(p, args.condition, str(task_id))
                    ),
                )
            result = _condition_summary(rows, args.condition)
            latest = p["root"] / "conditions" / args.condition / "summary.json"
            atomic_write_json(latest, result)
        else:
            result = _finalize(args=args, cfg=cfg, settings=settings, p=p)
            latest = p["final"]
        attempt.progress(status="phase_complete", latest_validated_checkpoint=str(latest))
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
