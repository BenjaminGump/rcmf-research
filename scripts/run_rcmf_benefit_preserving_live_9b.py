from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    condition_checkpoint_name,
)
from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    CalibratedFieldReaderHooks,
    CalibrationCandidate,
    preregistered_candidates,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_procedural_causal_audit_7b import _prepare_message
from scripts.run_rcmf_benefit_preserving_cached_9b import (
    _candidate_slots,
    _critical_step,
    _json,
    _load_runtime,
    _paths as _cached_paths,
)
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _attempt_ids,
    assert_frozen_without_gradients,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import LiveFieldQueryEncoder


LIVE_VERSION = "rcmf_benefit_preserving_critical_live_9b_v2"
MANIFEST_VERSION = "rcmf_benefit_preserving_critical_manifest_9b_v2"
SUMMARY_VERSION = "rcmf_benefit_preserving_critical_summary_9b_v2"
GLOBAL_SEED = 25101
SMOKE_CANDIDATES = ("R0-bare", "C50")
SMOKE_TASK = "8749218_1"


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
        "--phase",
        choices=("manifest", "smoke", "baseline", "run", "summarize"),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031b_stage8b")
    return parser.parse_args()


def _paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "stage_8b_exact_prompt_v2"
    return {
        "root": root,
        "manifest": root / "critical_condition_manifest.json",
        "condition_outputs": root / "condition_outputs",
        "condition_tensors": root / "condition_tensors",
        "worker_logs": root / "worker_logs",
        "summary": root / "critical_live_summary.json",
        "smoke_root": root / "lifecycle_smoke",
    }


def _tensor_sha256(value: Tensor) -> str:
    work = value.detach().to(device="cpu").contiguous()
    return hashlib.sha256(work.view(torch.uint8).numpy().tobytes()).hexdigest()


def _condition_key(candidate_id: str, task_id: str, step_id: int) -> str:
    digest = sha256_text(
        f"25101:exp031b-stage8b-exact-prompt-v2:{candidate_id}:{task_id}:{step_id}"
    )[:24]
    return f"exp031b-8b-{candidate_id.lower()}-{digest}"


def build_critical_manifest(settings: Mapping[str, Any]) -> dict[str, Any]:
    candidates = preregistered_candidates()
    critical = settings["gain_loss_audit"]["critical_steps"]
    conditions = []
    for task_id, spec in sorted(critical.items()):
        step_id = int(spec["d1_critical_step"])
        for candidate in candidates:
            conditions.append(
                {
                    "condition_key": _condition_key(
                        candidate.candidate_id, str(task_id), step_id
                    ),
                    "candidate_id": candidate.candidate_id,
                    "candidate": candidate.as_dict(),
                    "task_id": str(task_id),
                    "step_id": step_id,
                    "group": str(spec["group"]),
                    "mechanism": str(spec["mechanism"]),
                    "fresh_isolated_world": True,
                    "same_world_execution": True,
                    "runtime_retrieval": False,
                    "runtime_per_memory_scoring": False,
                    "student_prompt_contains_raw_memory": False,
                }
            )
    payload = {
        "format": MANIFEST_VERSION,
        "global_seed": GLOBAL_SEED,
        "candidate_count": len(candidates),
        "state_count": len(critical),
        "condition_count": len(conditions),
        "conditions": conditions,
        "frozen_before_generation": True,
        "candidate_outcomes_used": False,
        "first37_outcomes_used": False,
        "critical_choice_preservation_definition": (
            "exact_primary_app_api_and_canonical_action_signature_and_execution_"
            "success_and_semantic_successor"
        ),
        "prompt_contract": "exact_exp031a_stored_model_message_array",
        "live_observations_used_for_prompt": False,
    }
    if len(conditions) != 308:
        raise ValueError(f"Expected 308 critical live conditions, found {len(conditions)}")
    keys = {str(row["condition_key"]) for row in conditions}
    if len(keys) != len(conditions):
        raise ValueError("Duplicate EXP-031B Stage 8B condition keys")
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def critical_contract(task_result: Mapping[str, Any], step_id: int) -> dict[str, Any]:
    target = _critical_step(task_result, step_id)
    source_steps = sorted(
        (dict(row) for row in task_result["steps"] if int(row["step_id"]) < step_id),
        key=lambda row: int(row["step_id"]),
    )
    if [int(row["step_id"]) for row in source_steps] != list(range(1, step_id)):
        raise ValueError("Critical D1 history is not a complete ordered prefix")
    query = str(target["current_task_message"])
    if any(str(row["current_task_message"]) != query for row in source_steps):
        raise ValueError("Critical D1 task message changed within the trajectory")
    history = [
        {
            "step_id": int(row["step_id"]),
            "code": str(row["exact_executed_code"]),
            "expected_observation": str(row["complete_environment_observation"]),
            "historical_state_observation": str(
                row["complete_environment_observation"]
            ),
        }
        for row in source_steps
    ]
    target_action = str(target.get("raw_model_response", target["exact_executed_code"]))
    target_observation = str(target["complete_environment_observation"])
    exact_model_messages = target.get("exact_model_message_array")
    if not isinstance(exact_model_messages, list) or not exact_model_messages:
        raise ValueError("Critical D1 step is missing its exact stored model messages")
    if any(not isinstance(value, Mapping) for value in exact_model_messages):
        raise ValueError("Critical D1 exact model messages are malformed")
    return {
        "query": query,
        "history_steps": history,
        "target_action": target_action,
        "target_code": str(target["exact_executed_code"]),
        "target_observation": target_observation,
        "exact_model_messages": [dict(value) for value in exact_model_messages],
        "target_action_sha256": sha256_text(target_action),
        "target_code_sha256": sha256_text(str(target["exact_executed_code"])),
        "target_observation_sha256": sha256_text(target_observation),
        "source_rendered_messages_sha256": str(target["rendered_message_sha256"]),
    }


def critical_choice_preserved(metrics: Mapping[str, Any]) -> bool:
    return bool(
        metrics["exact_primary_app_api_match"]
        and metrics["canonical_procedural_signature_match"]
        and metrics["execution_success"]
        and metrics["semantic_successor_match"]
    )


def benefit_gate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_task = {str(row["task_id"]): row for row in rows}
    expected = {
        "0d01c76_3",
        "325d6ec_2",
        "325d6ec_3",
        "634f342_1",
        "634f342_2",
        "634f342_3",
        "8749218_2",
        "8749218_3",
    }
    if not expected.issubset(by_task):
        raise ValueError("Benefit gate is missing gain or retained critical states")
    preserved = {
        task_id: critical_choice_preserved(by_task[task_id]["metrics"])
        for task_id in sorted(expected)
    }
    gain_tasks = [
        "0d01c76_3",
        "325d6ec_2",
        "325d6ec_3",
        "634f342_1",
        "634f342_2",
        "634f342_3",
    ]
    exact_set = ["634f342_1", "634f342_2", "634f342_3"]
    family_checks = {
        "cross_app_import": preserved["0d01c76_3"],
        "spotify_state_machine": preserved["325d6ec_2"]
        and preserved["325d6ec_3"],
        "exact_set_migration": sum(int(preserved[value]) for value in exact_set) >= 2,
    }
    retained = preserved["8749218_2"] and preserved["8749218_3"]
    gain_count = sum(int(preserved[value]) for value in gain_tasks)
    passed = all(family_checks.values()) and retained and gain_count >= 5
    return {
        "passed": passed,
        "preserved_by_task": preserved,
        "preserved_gain_count": gain_count,
        "required_gain_count": 5,
        "family_checks": family_checks,
        "both_retained_successes": retained,
    }


def _generate(
    *,
    backend: Any,
    reader: Any,
    messages: Sequence[Mapping[str, str]],
    slots: Tensor,
    candidate: CalibrationCandidate,
    caps: Mapping[int, float] | None,
    max_new_tokens: int,
) -> tuple[list[int], str, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = CalibratedFieldReaderHooks(
        model=backend.model,
        reader=reader,
        slots=slots,
        layer_scales=candidate.layer_scales,
        layer_caps=caps,
    )
    with (
        torch.no_grad(),
        hooks,
        torch.autocast(
            device_type="cuda",
            dtype=torch.bfloat16,
            enabled=backend.device.type == "cuda",
        ),
        _attention_context(backend.device),
    ):
        output = backend.model.generate(
            input_ids=tokenized.input_ids,
            attention_mask=tokenized.attention_mask,
            max_new_tokens=int(max_new_tokens),
            do_sample=False,
            use_cache=True,
            pad_token_id=backend.tokenizer.eos_token_id,
            eos_token_id=backend.tokenizer.eos_token_id,
        )
    ids = [int(value) for value in output[0, prompt_length:].tolist()]
    text = backend.tokenizer.decode(ids, skip_special_tokens=True)
    return ids, text, hooks.audit.as_dict()


def _condition_path(root: Path, condition_key: str) -> Path:
    return root / condition_checkpoint_name(condition_key)


def _write_or_validate_tensor(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.exists():
        _atomic_torch_save(dict(payload), path)
        return
    existing = torch.load(path, map_location="cpu", weights_only=False)
    checks = {
        "format": existing.get("format") == payload["format"],
        "condition": str(existing.get("condition_key"))
        == str(payload["condition_key"]),
        "candidate": str(existing.get("candidate_id"))
        == str(payload["candidate_id"]),
        "query": _tensor_sha256(existing["query"])
        == _tensor_sha256(payload["query"]),
        "slots": _tensor_sha256(existing["slots"])
        == _tensor_sha256(payload["slots"]),
        "checkpoint": str(existing.get("checkpoint_sha256"))
        == str(payload["checkpoint_sha256"]),
        "field": str(existing.get("field_sha256"))
        == str(payload["field_sha256"]),
        "calibration": str(existing.get("calibration_sha256"))
        == str(payload["calibration_sha256"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Existing Stage 8B tensor differs: {checks}")


def _run_condition(
    *,
    condition: Mapping[str, Any],
    candidate: CalibrationCandidate,
    output_root: Path,
    tensor_root: Path,
    worker_root: Path,
    manifest_sha256: str,
    config_sha256: str,
    replay_config_sha256: str,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    runtime: Mapping[str, Any],
    query_encoder: LiveFieldQueryEncoder,
    calibration: Mapping[str, Any],
    attempt_id: str,
    ordinal: int,
    non_scientific_smoke: bool,
) -> tuple[dict[str, Any], bool]:
    key = str(condition["condition_key"])
    output_path = _condition_path(output_root, key)
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "format": row.get("format") == LIVE_VERSION,
            "condition": str(row.get("condition_key")) == key,
            "manifest": str(row.get("condition_manifest_sha256"))
            == manifest_sha256,
            "checkpoint": str(row.get("checkpoint_sha256"))
            == str(settings["immutable_exp031a"]["checkpoint_sha256"]),
            "field": str(row.get("field_sha256"))
            == str(settings["immutable_exp031a"]["deployment_field_sha256"]),
            "complete": str(row.get("status")) == "complete",
            "smoke": bool(row.get("non_scientific_smoke"))
            == non_scientific_smoke,
        }
        if not all(checks.values()):
            raise ValueError(f"Existing Stage 8B row differs: {checks}")
        return row, True

    task_id = str(condition["task_id"])
    task_path = (
        Path(str(settings["immutable_exp031a"]["artifact_root"]))
        / "first37/conditions/D1/task_results"
        / f"{task_id}.json"
    )
    task_result = _json(task_path)
    contract = critical_contract(task_result, int(condition["step_id"]))
    bridge_condition = {
        "condition_key": key,
        "state_example_id": f"critical:{task_id}:step:{condition['step_id']}",
        "state_task_id": task_id,
    }
    live_replay = {
        **dict(replay["replay"]),
        "random_seed": GLOBAL_SEED,
        "max_interactions": int(settings["appworld"]["max_steps"]),
        "max_api_calls_per_interaction": int(
            settings["appworld"]["max_api_calls_per_interaction"]
        ),
    }
    prepare = _prepare_message(
        condition=bridge_condition,
        contract=contract,
        settings={"legacy": replay["legacy"], "replay": live_replay},
        semantic_path=Path(str(replay["replay"]["semantic_module"])),
        bridge_attempt=f"{attempt_id}-{ordinal:04d}-{time.time_ns()}",
    )
    stderr_path = worker_root / f"{condition_checkpoint_name(key)}.stderr.log"
    client = LiveBridgeClient(
        executable=Path(str(replay["legacy"]["executable"])),
        bridge_script=Path(str(settings["appworld"]["one_step_bridge_script"])),
        appworld_root=Path(str(replay["legacy"]["appworld_root"])),
        stderr_path=stderr_path,
        timeout_seconds=float(replay["replay"]["subprocess_timeout_seconds"]),
    )
    started = time.perf_counter()
    try:
        ready = client.prepare(prepare)
        actual_observations = [str(value) for value in ready["actual_observations"]]
        messages = [dict(value) for value in contract["exact_model_messages"]]
        rendered = runtime["backend"].render_messages(
            messages, add_generation_prompt=True
        )
        rendered_sha256 = sha256_text(rendered)
        if rendered_sha256 != str(contract["source_rendered_messages_sha256"]):
            raise RuntimeError(
                "Exact stored critical prompt does not reproduce its source hash: "
                f"{key}"
            )
        tokenized = runtime["backend"].tokenize_messages(
            messages, add_generation_prompt=True
        )
        prompt_tokens = int(tokenized.attention_mask.sum().item())
        remaining = int(settings["appworld"]["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Critical live prompt over context: {key}")
        views, query = query_encoder.query(messages)
        slots, _, caps, read_audit = _candidate_slots(
            candidate=candidate,
            query=query,
            field=runtime["fields"]["critical"],
            calibration=calibration,
        )
        tensor_path = _condition_path(tensor_root, key).with_suffix(".pt")
        tensor_payload = {
            "format": "rcmf_benefit_preserving_critical_tensor_9b_v1",
            "condition_key": key,
            "candidate_id": candidate.candidate_id,
            "query": query.detach().cpu(),
            "slots": slots.detach().cpu(),
            "checkpoint_sha256": str(
                settings["immutable_exp031a"]["checkpoint_sha256"]
            ),
            "field_sha256": str(
                settings["immutable_exp031a"]["deployment_field_sha256"]
            ),
            "calibration_sha256": str(calibration["calibration_sha256"]),
        }
        _write_or_validate_tensor(tensor_path, tensor_payload)
        generation_started = time.perf_counter()
        ids, raw_response, reader_audit = _generate(
            backend=runtime["backend"],
            reader=runtime["reader"],
            messages=messages,
            slots=slots,
            candidate=candidate,
            caps=caps,
            max_new_tokens=min(int(settings["appworld"]["max_new_tokens"]), remaining),
        )
        generation_seconds = time.perf_counter() - generation_started
        code, fixed_response = extract_code_and_fix_content(raw_response)
        executed = client.execute(
            condition_key=key,
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(contract["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise

    metrics = evaluate_generated_action(
        raw_response,
        code,
        str(contract["target_action"]),
        str(executed["raw_observation"]),
        str(contract["target_observation"]),
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
        metrics["exception_category"] = str(
            executed["execution_exception"].get("type", "exception")
        ).lower()
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    metrics["critical_choice_preserved"] = critical_choice_preserved(metrics)
    row = {
        "format": LIVE_VERSION,
        "status": "complete",
        **dict(condition),
        "non_scientific_smoke": non_scientific_smoke,
        "condition_manifest_sha256": manifest_sha256,
        "config_sha256": config_sha256,
        "replay_config_sha256": replay_config_sha256,
        "checkpoint_sha256": str(
            settings["immutable_exp031a"]["checkpoint_sha256"]
        ),
        "field_sha256": str(
            settings["immutable_exp031a"]["deployment_field_sha256"]
        ),
        "calibration_sha256": str(calibration["calibration_sha256"]),
        "appworld_random_seed": GLOBAL_SEED,
        "max_interactions": int(settings["appworld"]["max_steps"]),
        "max_api_calls_per_interaction": int(
            settings["appworld"]["max_api_calls_per_interaction"]
        ),
        "task_result_sha256": sha256_file(task_path),
        "source_rendered_messages_sha256": contract[
            "source_rendered_messages_sha256"
        ],
        "target_action_sha256": contract["target_action_sha256"],
        "target_code_sha256": contract["target_code_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "actual_replay_observations": actual_observations,
        "model_messages": [dict(value) for value in messages],
        "rendered_messages_sha256": rendered_sha256,
        "exact_stored_prompt_hash_match": True,
        "prompt_source": "exact_exp031a_stored_model_message_array",
        "live_observations_used_for_prompt": False,
        "prompt_tokens": prompt_tokens,
        "context_limit": int(settings["appworld"]["context_limit"]),
        "truncation_applied": False,
        "raw_model_response": raw_response,
        "generated_token_ids": ids,
        "fixed_model_response": fixed_response,
        "extracted_code": code,
        "execution_exception": executed["execution_exception"],
        "complete_environment_observation": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "metrics": metrics,
        "field": {
            "query_sha256": _tensor_sha256(query),
            "state_views_sha256": _tensor_sha256(views),
            "slots_sha256": _tensor_sha256(slots),
            "tensor_artifact": str(tensor_path),
            "tensor_artifact_sha256": sha256_file(tensor_path),
            "read_audit": read_audit,
            "layer_scales": list(candidate.layer_scales),
            "layer_caps": None
            if caps is None
            else {str(key): float(value) for key, value in caps.items()},
            "memory_count": int(runtime["fields"]["critical"]["memory_count"]),
        },
        "reader_audit": reader_audit,
        "live_ready": ready,
        "live_executed": executed,
        "same_world_execution": bool(executed["same_world_execution"]),
        "same_python_namespace": bool(executed["same_python_namespace"]),
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
        "student_prompt_contains_raw_memory": False,
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
        "generation_elapsed_seconds": generation_seconds,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output_path, row)
    return row, False


def summarize_critical_rows(
    rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[CalibrationCandidate],
) -> dict[str, Any]:
    by_candidate: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_candidate[str(row["candidate_id"])].append(row)
    matrix = []
    for candidate in candidates:
        values = by_candidate[candidate.candidate_id]
        if len(values) != 14:
            raise ValueError(
                f"Candidate {candidate.candidate_id} has {len(values)} live rows"
            )
        gate = benefit_gate(values)
        losses = [row for row in values if str(row["group"]) == "loss"]
        unchanged = sum(
            int(critical_choice_preserved(row["metrics"])) for row in losses
        )
        matrix.append(
            {
                "candidate_id": candidate.candidate_id,
                "route": candidate.route,
                "critical_diagnostic_only": candidate.critical_diagnostic_only,
                "condition_count": len(values),
                "benefit_preservation": gate,
                "loss_secondary": {
                    "d1_harm_choice_unchanged": unchanged,
                    "changed_outcome_unresolved_until_full_task": len(losses)
                    - unchanged,
                    "fixed_count_not_claimed_from_one_step": None,
                    "worsened_count_not_claimed_from_one_step": None,
                },
                "metrics": {
                    name: statistics.fmean(
                        float(row["metrics"][name]) for row in values
                    )
                    for name in (
                        "exact_primary_app_api_match",
                        "canonical_procedural_signature_match",
                        "execution_success",
                        "semantic_successor_match",
                        "normalized_observation_similarity",
                    )
                },
                "exception_count": sum(
                    int(row["execution_exception"] is not None) for row in values
                ),
                "maximum_residual_ratio": max(
                    float(layer_value)
                    for row in values
                    for layer_value in row["reader_audit"]["maximum_ratio"].values()
                ),
                "eligible_for_heldout_live": bool(
                    gate["passed"]
                    and not candidate.critical_diagnostic_only
                    and candidate.candidate_id
                    not in {"R0-original", "R0-bare", "R0-shuffled", "G100"}
                ),
            }
        )
    return {
        "format": SUMMARY_VERSION,
        "global_seed": GLOBAL_SEED,
        "candidate_count": len(candidates),
        "state_count": 14,
        "condition_count": len(rows),
        "candidate_matrix": matrix,
        "hard_gate_definition": {
            "minimum_original_gains": 5,
            "cross_app_import": ["0d01c76_3"],
            "spotify_state_machine": ["325d6ec_2", "325d6ec_3"],
            "exact_set_migration_minimum_two_of": [
                "634f342_1",
                "634f342_2",
                "634f342_3",
            ],
            "both_retained": ["8749218_2", "8749218_3"],
        },
        "loss_rows_are_secondary": True,
        "first37_outcomes_used": False,
    }


def _manifest_or_fail(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError("Stage 8B manifest must be frozen before live work")
    actual = _json(path)
    if actual != dict(expected):
        raise ValueError("Frozen Stage 8B manifest differs from source definition")
    return actual


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9b"]
    parent_settings = cfg.raw["stage_c_9a"]
    replay = load_config(args.replay_config).raw["stage_c_7b"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
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
    expected_manifest = build_critical_manifest(settings)
    data_hashes = {
        "checkpoint": sha256_file(cached["checkpoint"]),
        "deployment_field": sha256_file(cached["deployment"]),
        "critical_audit": sha256_file(cached["critical_audit"]),
        "calibration": sha256_file(cached["calibration"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"stage_8b_{args.phase}",
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
            if paths["manifest"].exists() and _json(paths["manifest"]) != expected_manifest:
                raise ValueError("Existing Stage 8B manifest differs")
            if not paths["manifest"].exists():
                atomic_write_json(paths["manifest"], expected_manifest)
            payload = expected_manifest
        elif args.phase == "summarize":
            manifest = _manifest_or_fail(paths["manifest"], expected_manifest)
            rows = [
                _json(_condition_path(paths["condition_outputs"], str(row["condition_key"])))
                for row in manifest["conditions"]
            ]
            payload = summarize_critical_rows(rows, preregistered_candidates())
            payload["condition_manifest_sha256"] = str(manifest["manifest_sha256"])
            payload["summary_sha256"] = canonical_sha256(payload)
            atomic_write_json(paths["summary"], payload)
        else:
            manifest = _manifest_or_fail(paths["manifest"], expected_manifest)
            calibration = _json(cached["calibration"])
            runtime = _load_runtime(cfg, settings, cached)
            query_encoder = LiveFieldQueryEncoder(
                settings=parent_settings, backend=runtime["backend"]
            )
            candidate_map = {
                candidate.candidate_id: candidate
                for candidate in preregistered_candidates()
            }
            conditions = list(manifest["conditions"])
            output_root = paths["condition_outputs"]
            tensor_root = paths["condition_tensors"]
            worker_root = paths["worker_logs"]
            non_scientific = args.phase == "smoke"
            if non_scientific:
                conditions = [
                    {
                        **row,
                        "condition_key": "smoke-" + str(row["condition_key"]),
                    }
                    for row in conditions
                    if str(row["task_id"]) == SMOKE_TASK
                    and str(row["candidate_id"]) in SMOKE_CANDIDATES
                ]
                output_root = paths["smoke_root"] / "condition_outputs"
                tensor_root = paths["smoke_root"] / "condition_tensors"
                worker_root = paths["smoke_root"] / "worker_logs"
            elif args.phase == "baseline":
                conditions = [
                    row
                    for row in conditions
                    if str(row["candidate_id"]) == "R0-original"
                ]
            completed = 0
            resumed = 0
            rows = []
            for ordinal, condition in enumerate(conditions, start=1):
                row, was_reused = _run_condition(
                    condition=condition,
                    candidate=candidate_map[str(condition["candidate_id"])],
                    output_root=output_root,
                    tensor_root=tensor_root,
                    worker_root=worker_root,
                    manifest_sha256=str(manifest["manifest_sha256"]),
                    config_sha256=sha256_file(args.config),
                    replay_config_sha256=sha256_file(args.replay_config),
                    settings={**settings, "appworld": parent_settings["appworld"]},
                    replay=replay,
                    runtime=runtime,
                    query_encoder=query_encoder,
                    calibration=calibration,
                    attempt_id=args.attempt_id,
                    ordinal=ordinal,
                    non_scientific_smoke=non_scientific,
                )
                rows.append(row)
                completed += 1
                resumed += int(was_reused)
                attempt.progress(
                    status=(
                        "stage_8b_smoke"
                        if non_scientific
                        else "stage_8b_baseline"
                        if args.phase == "baseline"
                        else "stage_8b_live"
                    ),
                    completed_conditions=completed,
                    total_conditions=len(conditions),
                    resumed_conditions=resumed,
                    latest_validated_checkpoint=str(
                        _condition_path(output_root, str(condition["condition_key"]))
                    ),
                )
            assert_frozen_without_gradients(runtime["backend"].model)
            assert_frozen_without_gradients(runtime["writer"])
            assert_frozen_without_gradients(runtime["reader"])
            payload = {
                "format": LIVE_VERSION,
                "phase": args.phase,
                "condition_count": len(rows),
                "new_condition_count": len(rows) - resumed,
                "resumed_condition_count": resumed,
                "non_scientific_smoke": non_scientific,
                "infrastructure_passed": all(
                    bool(row["same_world_execution"])
                    and bool(row["same_python_namespace"])
                    and bool(row["history_semantic_v3_match"])
                    for row in rows
                ),
                "scientific_metrics_consumed_for_selection": False
                if non_scientific
                else None,
            }
        wall = time.perf_counter() - started
        attempt.progress(status="complete", phase=args.phase, wall_seconds=wall)
        print(json.dumps({**payload, "wall_seconds": wall}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
