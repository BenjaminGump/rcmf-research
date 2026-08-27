from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    CalibratedFieldReaderHooks,
    CalibrationCandidate,
    preregistered_candidates,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.run_procedural_causal_audit_7b import _examples_by_state, _records_by_task
from scripts.run_rcmf_benefit_preserving_cached_9b import (
    _candidate_slots,
    _json,
    _load_runtime,
    _paths as _cached_paths,
)
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _attempt_ids,
    assert_frozen_without_gradients,
)
from scripts.run_rcmf_joint_full_bank_live_9a import (
    _run_condition,
    selection_score,
    summarize_live_controls,
)


GLOBAL_SEED = 25101
MANIFEST_VERSION = "rcmf_benefit_preserving_heldout_manifest_9b_v1"
RESULT_VERSION = "rcmf_benefit_preserving_heldout_live_9b_v1"
SUMMARY_VERSION = "rcmf_benefit_preserving_heldout_summary_9b_v1"
CONTROLS = (
    "L0_zero",
    "L1_correct",
    "L2_key_payload_shuffle",
    "L3_state_query_shuffle",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("manifest", "smoke", "run", "summarize"), required=True
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031b_stage8c")
    return parser.parse_args()


def _paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "stage_8c_heldout_live"
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "condition_outputs": root / "condition_outputs",
        "condition_tensors": root / "condition_tensors",
        "worker_logs": root / "worker_logs",
        "smoke_outputs": root / "lifecycle_smoke/condition_outputs",
        "smoke_tensors": root / "lifecycle_smoke/condition_tensors",
        "smoke_logs": root / "lifecycle_smoke/worker_logs",
        "smoke_summary": root / "lifecycle_smoke/summary.json",
        "summary": root / "heldout_live_summary.json",
    }


def _tensor_sha256(value: Tensor) -> str:
    work = value.detach().cpu().contiguous()
    return hashlib.sha256(work.view(torch.uint8).numpy().tobytes()).hexdigest()


def _condition_key(candidate_id: str, state_id: str, control: str) -> str:
    digest = sha256_text(
        f"25101:exp031b-stage8c:{candidate_id}:{state_id}:{control}"
    )[:24]
    return f"exp031b-8c-{candidate_id.lower()}-{digest}"


def _candidate_index() -> dict[str, CalibrationCandidate]:
    return {row.candidate_id: row for row in preregistered_candidates()}


def _parent_live_paths(settings: Mapping[str, Any]) -> tuple[Path, Path]:
    root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    live = root / "heldout_validation/live_full_field"
    return live / "condition_manifest.json", live / "condition_outputs"


def build_heldout_manifest(settings: Mapping[str, Any]) -> dict[str, Any]:
    parent_manifest_path, parent_outputs = _parent_live_paths(settings)
    parent = _json(parent_manifest_path)
    epoch_rows = [
        dict(row) for row in parent["conditions"] if int(row["epoch"]) == 2
    ]
    by_state: dict[str, dict[str, dict[str, Any]]] = {}
    for row in epoch_rows:
        by_state.setdefault(str(row["source_state_id"]), {})[str(row["control"])] = row
    if len(by_state) != 98 or any(set(rows) != set(CONTROLS) for rows in by_state.values()):
        raise ValueError("Immutable EXP-031A heldout state accounting differs")

    candidates = _candidate_index()
    selected_ids = [str(value) for value in settings["heldout"]["selected_candidates"]]
    if len(selected_ids) > int(settings["candidates"]["maximum_heldout_live_candidates"]):
        raise ValueError("Heldout candidate limit exceeded")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("Duplicate heldout candidate")
    if any(value not in candidates for value in selected_ids):
        raise ValueError("Unknown heldout candidate")
    if any(candidates[value].critical_diagnostic_only for value in selected_ids):
        raise ValueError("Diagnostic-only candidate entered heldout live evaluation")

    conditions = []
    unique_zero_references: set[str] = set()
    for candidate_id in selected_ids:
        candidate = candidates[candidate_id]
        for state_id, parent_rows in sorted(by_state.items()):
            source = parent_rows["L1_correct"]
            state_shuffle = parent_rows["L3_state_query_shuffle"]
            zero_parent = parent_rows["L0_zero"]
            reference_path = parent_outputs / condition_checkpoint_name(
                str(zero_parent["condition_key"])
            )
            if not reference_path.exists():
                raise FileNotFoundError(reference_path)
            unique_zero_references.add(str(reference_path))
            for control in CONTROLS:
                conditions.append(
                    {
                        "condition_key": _condition_key(candidate_id, state_id, control),
                        "candidate_id": candidate_id,
                        "candidate": candidate.as_dict(),
                        "control": control,
                        "source_state_id": state_id,
                        "source_task_id": str(source["source_task_id"]),
                        "source_step_id": int(source["source_step_id"]),
                        "world_state_id": state_id,
                        "field_query_state_id": (
                            str(state_shuffle["field_query_state_id"])
                            if control == "L3_state_query_shuffle"
                            else state_id
                        ),
                        "field_control": (
                            "zero"
                            if control == "L0_zero"
                            else "key_payload_shuffle"
                            if control == "L2_key_payload_shuffle"
                            else "correct"
                        ),
                        "executable": control != "L0_zero",
                        "immutable_exp031a_zero_reference": (
                            {
                                "condition_key": str(zero_parent["condition_key"]),
                                "path": str(reference_path),
                                "sha256": sha256_file(reference_path),
                            }
                            if control == "L0_zero"
                            else None
                        ),
                        "complete_bank_memory_count": 0 if control == "L0_zero" else 401,
                        "student_prompt_contains_raw_memory": False,
                        "runtime_memory_retrieval": False,
                        "runtime_per_memory_scoring": False,
                    }
                )
    payload = {
        "format": MANIFEST_VERSION,
        "global_seed": GLOBAL_SEED,
        "candidate_ids": selected_ids,
        "candidate_count": len(selected_ids),
        "state_count": len(by_state),
        "controls": list(CONTROLS),
        "logical_condition_count": len(conditions),
        "executable_condition_count": sum(bool(row["executable"]) for row in conditions),
        "logical_zero_reference_count": sum(not bool(row["executable"]) for row in conditions),
        "unique_zero_reference_count": len(unique_zero_references),
        "conditions": conditions,
        "candidate_selection_source": "stage8b_hard_gate_plus_stage8a_cached_diagnostics",
        "candidate_selection_frozen_before_first37_outcomes": True,
        "first37_outcomes_used": False,
        "parent_exp031a_manifest": str(parent_manifest_path),
        "parent_exp031a_manifest_sha256": sha256_file(parent_manifest_path),
        "state_query_shuffle_changes_field_query_only": True,
        "world_and_prompt_remain_source_state": True,
    }
    if (
        len(conditions) != 1176
        or int(payload["executable_condition_count"]) != 882
        or int(payload["unique_zero_reference_count"]) != 98
    ):
        raise ValueError("EXP-031B Stage 8C condition accounting differs")
    if len({str(row["condition_key"]) for row in conditions}) != len(conditions):
        raise ValueError("Duplicate EXP-031B Stage 8C condition key")
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def candidate_gate(
    summary: Mapping[str, Any], *, original_d1_execution_count: int
) -> dict[str, Any]:
    zero = summary["L0_zero"]
    correct = summary["L1_correct"]
    shuffle = summary["L2_key_payload_shuffle"]
    checks = {
        "correct_exact_api_exceeds_key_payload_shuffle": (
            float(correct["exact_api"]) > float(shuffle["exact_api"])
        ),
        "correct_action_signature_exceeds_key_payload_shuffle": (
            float(correct["action_signature"]) > float(shuffle["action_signature"])
        ),
        "execution_within_one_state_of_original_d1": (
            round(float(correct["execution"]) * 98)
            >= int(original_d1_execution_count) - 1
        ),
        "positive_on_at_least_four_tasks": int(summary["positive_task_count"]) >= 4,
        "not_merely_moving_to_bare": (
            float(correct["exact_api"]) > float(zero["exact_api"])
            or float(correct["action_signature"]) > float(zero["action_signature"])
            or float(correct["semantic_successor"]) > float(zero["semantic_successor"])
        ),
        "prohibited_mechanism_absent": True,
    }
    return {"passed": all(checks.values()), "checks": checks}


def _condition_path(root: Path, condition_key: str) -> Path:
    return root / condition_checkpoint_name(condition_key)


def _write_or_validate_tensor(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        existing = torch.load(path, map_location="cpu", weights_only=False)
        checks = {
            "format": existing.get("format") == payload["format"],
            "condition": existing.get("condition_key") == payload["condition_key"],
            "query": _tensor_sha256(existing["query"]) == _tensor_sha256(payload["query"]),
            "slots": _tensor_sha256(existing["slots"]) == _tensor_sha256(payload["slots"]),
            "checkpoint": existing.get("checkpoint_sha256") == payload["checkpoint_sha256"],
            "field": existing.get("field_sha256") == payload["field_sha256"],
            "calibration": existing.get("calibration_sha256") == payload["calibration_sha256"],
        }
        if not all(checks.values()):
            raise ValueError(f"Existing Stage 8C tensor differs: {checks}")
        return
    _atomic_torch_save(payload, path)


def _slot_and_info(
    *,
    condition: Mapping[str, Any],
    candidate: CalibrationCandidate,
    runtime: Mapping[str, Any],
    calibration: Mapping[str, Any],
    tensor_root: Path,
    settings: Mapping[str, Any],
) -> tuple[Tensor, dict[str, Any], Mapping[int, float] | None]:
    query_state_id = str(condition["field_query_state_id"])
    query = runtime["tensors"]["queries"][
        runtime["data"]["state_position"][query_state_id]
    ]
    slot_candidate = (
        replace(candidate, field_control="shuffled")
        if str(condition["control"]) == "L2_key_payload_shuffle"
        else candidate
    )
    slots, _, caps, read_audit = _candidate_slots(
        candidate=slot_candidate,
        query=query,
        field=runtime["fields"]["heldout"],
        calibration=calibration,
    )
    tensor_path = _condition_path(
        tensor_root, str(condition["condition_key"])
    ).with_suffix(".pt")
    tensor_payload = {
        "format": "rcmf_benefit_preserving_heldout_tensor_9b_v1",
        "condition_key": str(condition["condition_key"]),
        "candidate_id": candidate.candidate_id,
        "control": str(condition["control"]),
        "query": query.detach().cpu(),
        "slots": slots.detach().cpu(),
        "checkpoint_sha256": str(settings["immutable_exp031a"]["checkpoint_sha256"]),
        "field_sha256": str(settings["immutable_exp031a"]["deployment_field_sha256"]),
        "calibration_sha256": str(calibration["calibration_sha256"]),
    }
    _write_or_validate_tensor(tensor_path, tensor_payload)
    info = {
        "query_sha256": _tensor_sha256(query),
        "slots_sha256": _tensor_sha256(slots),
        "slots_shape": list(slots.shape),
        "slot_artifact": str(tensor_path),
        "slot_artifact_sha256": sha256_file(tensor_path),
        "field_artifact": str(settings["immutable_exp031a"]["deployment_field"]),
        "field_artifact_sha256": str(settings["immutable_exp031a"]["deployment_field_sha256"]),
        "read_audit": read_audit,
        "layer_scales": list(candidate.layer_scales),
        "layer_caps": (
            None if caps is None else {str(key): float(value) for key, value in caps.items()}
        ),
        "memory_count": int(runtime["fields"]["heldout"]["memory_count"]),
        "top_memory_contributions_offline": [],
    }
    return slots, info, caps


def _validate_reference(condition: Mapping[str, Any]) -> dict[str, Any]:
    reference = condition["immutable_exp031a_zero_reference"]
    path = Path(str(reference["path"]))
    if sha256_file(path) != str(reference["sha256"]):
        raise ValueError("Immutable EXP-031A zero reference hash differs")
    row = _json(path)
    checks = {
        "format": row.get("format") == "rcmf_full_field_live_result_9a_v1",
        "condition": row.get("condition_key") == reference["condition_key"],
        "control": row.get("control") == "L0_zero",
        "state": row.get("source_state_id") == condition["source_state_id"],
        "checkpoint": row.get("checkpoint_sha256")
        == "d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1",
    }
    if not all(checks.values()):
        raise ValueError(f"Immutable EXP-031A zero reference differs: {checks}")
    return row


def _summarize(
    *,
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    parent_live_summary = _json(
        parent_root / "heldout_validation/live_full_field/validation_summary.json"
    )
    original = next(
        row for row in parent_live_summary["reports"] if int(row["epoch"]) == 2
    )
    original_execution_count = round(
        float(original["metrics"]["L1_correct"]["execution"]) * 98
    )
    cached = _json(Path("research/analysis/exp031b_stage8a_candidate_summary.json"))
    cached_by_candidate = {
        str(row["candidate_id"]): row for row in cached["candidate_matrix"]
    }
    stage8b = _json(
        paths["root"].parent / "stage_8b_exact_prompt_v2/critical_live_summary.json"
    )
    critical_by_candidate = {
        str(row["candidate_id"]): row for row in stage8b["candidate_matrix"]
    }
    reports = []
    for candidate_id in manifest["candidate_ids"]:
        rows = []
        for condition in manifest["conditions"]:
            if str(condition["candidate_id"]) != str(candidate_id):
                continue
            if bool(condition["executable"]):
                row = _json(
                    _condition_path(
                        paths["condition_outputs"], str(condition["condition_key"])
                    )
                )
            else:
                row = dict(_validate_reference(condition))
                row["candidate_id"] = candidate_id
            rows.append(row)
        metrics = summarize_live_controls(rows)
        gate = candidate_gate(
            metrics, original_d1_execution_count=original_execution_count
        )
        critical = critical_by_candidate[str(candidate_id)]
        eligible = bool(gate["passed"] and critical["benefit_preservation"]["passed"])
        cached_row = cached_by_candidate[str(candidate_id)]
        reports.append(
            {
                "candidate_id": candidate_id,
                "condition_count": len(rows),
                "new_condition_count": 294,
                "reused_zero_count": 98,
                "metrics": metrics,
                "selection_score": selection_score(metrics),
                "eligibility_gate": gate,
                "benefit_preservation": critical["benefit_preservation"],
                "cached_nll_kl_diagnostics": {
                    "heldout": cached_row["heldout"],
                    "target_nll_margin_vs_key_payload_shuffle": cached_row[
                        "heldout_target_nll_margin_vs_key_payload_shuffle"
                    ],
                },
                "eligible_for_first37": eligible,
            }
        )
    eligible = [row for row in reports if bool(row["eligible_for_first37"])]
    selected = (
        max(
            eligible,
            key=lambda row: (float(row["selection_score"]), str(row["candidate_id"])),
        )
        if eligible
        else None
    )
    infrastructure = []
    for condition in manifest["conditions"]:
        if not bool(condition["executable"]):
            continue
        row = _json(
            _condition_path(
                paths["condition_outputs"], str(condition["condition_key"])
            )
        )
        infrastructure.append(
            all(
                bool(row.get(name))
                for name in (
                    "same_world_execution",
                    "same_python_namespace",
                    "history_semantic_v3_match",
                )
            )
        )
    return {
        "format": SUMMARY_VERSION,
        "global_seed": GLOBAL_SEED,
        "candidate_count": len(reports),
        "state_count": 98,
        "logical_condition_count": int(manifest["logical_condition_count"]),
        "new_condition_count": int(manifest["executable_condition_count"]),
        "reused_zero_logical_count": int(manifest["logical_zero_reference_count"]),
        "reports": reports,
        "eligible_candidate_ids": [str(row["candidate_id"]) for row in eligible],
        "selected_first_candidate": (
            None if selected is None else str(selected["candidate_id"])
        ),
        "decision": (
            "INCONCLUSIVE_no_candidate_eligible_for_full_integration"
            if selected is None
            else "heldout_live_candidate_eligible_for_sequential_first37"
        ),
        "first37_outcomes_used": False,
        "original_exp031a_epoch2_reference": original,
        "passed_infrastructure": all(infrastructure),
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9b"]
    parent_settings = cfg.raw["stage_c_9a"]
    replay = load_config(args.replay_config).raw["stage_c_7b"]
    if os.name != "nt" and not os.path.ismount(
        Path(str(settings["persistent_root"]))
    ):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    torch.manual_seed(GLOBAL_SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(GLOBAL_SEED)

    paths = _paths(args.artifact_dir)
    cached = _cached_paths(settings, args.artifact_dir)
    expected_manifest = build_heldout_manifest(settings)
    manifest = expected_manifest
    if paths["manifest"].exists():
        manifest = _json(paths["manifest"])
        if manifest != expected_manifest:
            raise ValueError("Frozen Stage 8C manifest differs")
    data_hashes = {
        "checkpoint": sha256_file(cached["checkpoint"]),
        "deployment_field": sha256_file(cached["deployment"]),
        "calibration": sha256_file(cached["calibration"]),
        "stage8b_summary": sha256_file(
            paths["root"].parent
            / "stage_8b_exact_prompt_v2/critical_live_summary.json"
        ),
        "parent_live_manifest": str(manifest["parent_exp031a_manifest_sha256"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"stage_8c_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        started = time.perf_counter()
        if args.phase == "manifest":
            if not paths["manifest"].exists():
                atomic_write_json(paths["manifest"], expected_manifest)
            payload = expected_manifest
        elif args.phase == "summarize":
            payload = _summarize(
                manifest=manifest, paths=paths, settings=settings
            )
            atomic_write_json(paths["summary"], payload)
        else:
            if not paths["manifest"].exists():
                raise FileNotFoundError(
                    "Stage 8C manifest must be frozen before GPU work"
                )
            runtime = _load_runtime(cfg, settings, cached)
            calibration = _json(cached["calibration"])
            corpus = Path(str(parent_settings["reconciled_corpus_dir"]))
            examples = _examples_by_state(
                load_decision_examples(corpus / "decision_examples.jsonl")
            )
            records = _records_by_task(
                load_memory_records(corpus / "memory_records.jsonl")
            )
            executable = [
                row for row in manifest["conditions"] if bool(row["executable"])
            ]
            if args.phase == "smoke":
                first_candidate = str(manifest["candidate_ids"][0])
                state_ids = sorted(
                    {
                        str(row["source_state_id"])
                        for row in executable
                        if str(row["candidate_id"]) == first_candidate
                    }
                )[:2]
                executable = [
                    row
                    for row in executable
                    if str(row["candidate_id"]) == first_candidate
                    and str(row["source_state_id"]) in state_ids
                ]
                output_root = paths["smoke_outputs"]
                tensor_root = paths["smoke_tensors"]
                log_root = paths["smoke_logs"]
            else:
                output_root = paths["condition_outputs"]
                tensor_root = paths["condition_tensors"]
                log_root = paths["worker_logs"]

            candidates = _candidate_index()
            rows = []
            reused = 0
            for index, condition in enumerate(executable, start=1):
                candidate = candidates[str(condition["candidate_id"])]
                slots, slot_info, caps = _slot_and_info(
                    condition=condition,
                    candidate=candidate,
                    runtime=runtime,
                    calibration=calibration,
                    tensor_root=tensor_root,
                    settings=settings,
                )
                hook_factory = (
                    lambda *, model, reader, slots, c=candidate, x=caps:
                    CalibratedFieldReaderHooks(
                        model=model,
                        reader=reader,
                        slots=slots,
                        layer_scales=c.layer_scales,
                        layer_caps=x,
                    )
                )
                output = _condition_path(
                    output_root, str(condition["condition_key"])
                )
                stderr = log_root / (
                    condition_checkpoint_name(str(condition["condition_key"]))
                    + ".stderr.log"
                )
                row, was_reused = _run_condition(
                    condition=condition,
                    output_path=output,
                    stderr_path=stderr,
                    slot_info=slot_info,
                    slots=slots,
                    checkpoint=cached["checkpoint"],
                    checkpoint_sha256=str(
                        settings["immutable_exp031a"]["checkpoint_sha256"]
                    ),
                    manifest_sha256=str(manifest["manifest_sha256"]),
                    config_sha256=sha256_file(args.config),
                    replay=replay,
                    settings=parent_settings,
                    examples=examples,
                    records=records,
                    backend=runtime["backend"],
                    reader=runtime["reader"],
                    attempt_id=args.attempt_id,
                    ordinal=index,
                    hook_factory=hook_factory,
                    result_version=RESULT_VERSION,
                    extra_result_fields={
                        "non_scientific_smoke": args.phase == "smoke",
                        "calibration_sha256": str(
                            calibration["calibration_sha256"]
                        ),
                        "replay_config_sha256": sha256_file(
                            args.replay_config
                        ),
                    },
                )
                rows.append(row)
                reused += int(was_reused)
                attempt.progress(
                    status=f"stage_8c_{args.phase}",
                    completed_conditions=index,
                    total_conditions=len(executable),
                    latest_validated_checkpoint=str(output),
                )
            assert_frozen_without_gradients(runtime["backend"].model)
            payload = {
                "format": "rcmf_benefit_preserving_heldout_attempt_9b_v1",
                "phase": args.phase,
                "condition_count": len(rows),
                "new_condition_count": len(rows) - reused,
                "reused_condition_count": reused,
                "same_world_execution": all(
                    bool(row["same_world_execution"]) for row in rows
                ),
                "same_python_namespace": all(
                    bool(row["same_python_namespace"]) for row in rows
                ),
                "history_semantic_v3_match": all(
                    bool(row["history_semantic_v3_match"]) for row in rows
                ),
                "exception_count": sum(
                    int(row["execution_exception"] is not None) for row in rows
                ),
                "non_scientific_smoke": args.phase == "smoke",
            }
            if args.phase == "smoke":
                atomic_write_json(paths["smoke_summary"], payload)
        payload["elapsed_seconds_this_attempt"] = time.perf_counter() - started
        attempt.progress(
            status=f"stage_8c_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths["summary"]
                if args.phase == "summarize"
                else paths["manifest"]
            ),
            result=payload,
        )
        print(json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
