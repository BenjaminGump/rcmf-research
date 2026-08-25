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
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    FieldReaderHooks,
    ReversibleRCMFField,
    RCMFFieldRecord,
    assert_frozen_without_gradients,
    compile_differentiable_field,
    read_compiled_field,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _attempt_ids,
    _build_backend,
    _build_components,
    _json,
    _load_data,
    _paths,
    _require,
    _runtime_tensors,
    _teacher_validate,
)

LIVE_RESULT_VERSION = "rcmf_full_field_live_result_9a_v1"
LIVE_MANIFEST_VERSION = "rcmf_full_field_live_manifest_9a_v1"
CONTROLS = ("L0_zero", "L1_correct", "L2_key_payload_shuffle", "L3_state_query_shuffle")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"))
    parser.add_argument("--replay-config", type=Path, default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("teacher-corrected", "manifest", "validate", "select", "instant-add"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031a")
    return parser.parse_args()


def _live_paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "heldout_validation/live_full_field"
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "condition_outputs": root / "condition_outputs",
        "worker_logs": root / "worker_logs",
        "slot_artifacts": root / "slot_artifacts",
        "field_artifacts": root / "field_artifacts",
        "summary": root / "validation_summary.json",
        "selection": root / "checkpoint_selection.json",
        "instant_add": artifact_dir / "deployment_field/instant_add_report.json",
        "deployment_field": artifact_dir / "deployment_field/complete_37_task_field.pt",
        "corrected_teacher_root": artifact_dir / "heldout_validation/teacher_forced_zero_exact",
        "corrected_teacher_summary": artifact_dir / "heldout_validation/teacher_forced_zero_exact_summary.json",
    }


def _tensor_sha256(value: Tensor) -> str:
    work = value.detach().to(device="cpu").contiguous()
    return hashlib.sha256(work.view(torch.uint8).numpy().tobytes()).hexdigest()


def _condition_key(epoch: int, state_id: str, control: str) -> str:
    digest = sha256_text(f"25101:exp031a-live:{epoch}:{state_id}:{control}")[:24]
    return f"exp031a-e{epoch:02d}-{digest}"


def build_live_manifest(*, outcomes: Sequence[Mapping[str, Any]], state_shuffle: Mapping[str, str]) -> dict[str, Any]:
    heldout = sorted(
        (dict(row) for row in outcomes if str(row["model_split"]) == "heldout_train_validation"),
        key=lambda row: (str(row["state_task_id"]), int(row["state_step_id"]), str(row["state_example_id"])),
    )
    if len(heldout) != 98:
        raise ValueError("EXP-031A live validation requires 98 heldout states")
    conditions = []
    for epoch in (1, 2):
        for row in heldout:
            state_id = str(row["state_example_id"])
            shuffled_query = str(state_shuffle[state_id])
            for control in CONTROLS:
                conditions.append({
                    "epoch": epoch,
                    "condition_key": _condition_key(epoch, state_id, control),
                    "control": control,
                    "source_state_id": state_id,
                    "source_task_id": str(row["state_task_id"]),
                    "source_step_id": int(row["state_step_id"]),
                    "world_state_id": state_id,
                    "field_query_state_id": shuffled_query if control == "L3_state_query_shuffle" else state_id,
                    "field_control": "zero" if control == "L0_zero" else "key_payload_shuffle" if control == "L2_key_payload_shuffle" else "correct",
                    "complete_bank_memory_count": 0 if control == "L0_zero" else 401,
                    "student_prompt_contains_raw_memory": False,
                    "runtime_memory_retrieval": False,
                })
    payload = {
        "format": LIVE_MANIFEST_VERSION,
        "global_seed": GLOBAL_SEED,
        "selection_split": "eight_heldout_train_tasks_only",
        "epoch_count": 2,
        "state_count_per_epoch": len(heldout),
        "condition_count": len(conditions),
        "controls": list(CONTROLS),
        "state_query_shuffle_changes_field_query_only": True,
        "world_and_prompt_remain_source_state": True,
        "test_normal_outcomes_used": False,
        "conditions": conditions,
    }
    if len(conditions) != 784 or len({str(row["condition_key"]) for row in conditions}) != 784:
        raise ValueError("EXP-031A live condition accounting differs")
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _resolve_manifest(
    *, data: Mapping[str, Any], paths: Mapping[str, Path], live: Mapping[str, Path]
) -> tuple[dict[str, Any], bool]:
    if live["manifest"].exists():
        payload = _json(live["manifest"])
        if payload.get("format") != LIVE_MANIFEST_VERSION:
            raise ValueError("Existing live manifest format differs")
        return payload, True
    state_shuffle = {
        str(row["query_state_id"]): str(row["shuffled_query_state_id"])
        for row in _json(paths["state_shuffle"])["rows"]
        if str(row["model_split"]) == "heldout_train_validation"
    }
    payload = build_live_manifest(outcomes=data["outcomes"], state_shuffle=state_shuffle)
    return payload, False


def summarize_live_controls(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["control"])].append(row)
    missing = [name for name in CONTROLS if not grouped[name]]
    if missing:
        raise ValueError(f"Missing full-field live controls: {missing}")
    metric = lambda row, name: float(row["metrics"][name])
    output: dict[str, Any] = {}
    for control in CONTROLS:
        values = grouped[control]
        output[control] = {
            "count": len(values),
            "exact_api": statistics.fmean(metric(row, "exact_primary_app_api_match") for row in values),
            "action_signature": statistics.fmean(metric(row, "canonical_procedural_signature_match") for row in values),
            "semantic_successor": statistics.fmean(metric(row, "semantic_successor_match") for row in values),
            "execution": statistics.fmean(metric(row, "execution_success") for row in values),
            "observation_similarity": statistics.fmean(metric(row, "normalized_observation_similarity") for row in values),
        }
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    task_by_state: dict[str, str] = {}
    for row in rows:
        state_id = str(row["source_state_id"])
        by_state[state_id][str(row["control"])] = row
        task_by_state[state_id] = str(row["source_task_id"])
    task_deltas: dict[str, list[float]] = defaultdict(list)
    per_state = {}
    for state_id, controls in sorted(by_state.items()):
        if set(controls) != set(CONTROLS):
            raise ValueError(f"Incomplete controls for {state_id}")
        correct, zero = controls["L1_correct"], controls["L0_zero"]
        signature = metric(correct, "canonical_procedural_signature_match") - metric(zero, "canonical_procedural_signature_match")
        successor = metric(correct, "semantic_successor_match") - metric(zero, "semantic_successor_match")
        task_deltas[task_by_state[state_id]].append(signature + successor)
        per_state[state_id] = {"task_id": task_by_state[state_id], "signature_correct_minus_zero": signature, "successor_correct_minus_zero": successor}
    output["per_state_correct_minus_zero"] = per_state
    output["per_task_correct_minus_zero"] = {task_id: statistics.fmean(values) for task_id, values in sorted(task_deltas.items())}
    output["positive_task_count"] = sum(value > 0.0 for value in output["per_task_correct_minus_zero"].values())
    output["task_count"] = len(task_deltas)
    return output


def classify_live_checkpoint(summary: Mapping[str, Any]) -> str:
    zero, correct = summary["L0_zero"], summary["L1_correct"]
    transition, state = summary["L2_key_payload_shuffle"], summary["L3_state_query_shuffle"]
    improves = float(correct["action_signature"]) > float(zero["action_signature"]) or float(correct["semantic_successor"]) > float(zero["semantic_successor"])
    beats_both = float(correct["action_signature"]) > max(float(transition["action_signature"]), float(state["action_signature"])) or float(correct["semantic_successor"]) > max(float(transition["semantic_successor"]), float(state["semantic_successor"]))
    beats_one = float(correct["action_signature"]) > min(float(transition["action_signature"]), float(state["action_signature"])) or float(correct["semantic_successor"]) > min(float(transition["semantic_successor"]), float(state["semantic_successor"]))
    companion_ok = not (float(correct["action_signature"]) + 0.05 < min(float(transition["action_signature"]), float(state["action_signature"])) or float(correct["semantic_successor"]) + 0.05 < min(float(transition["semantic_successor"]), float(state["semantic_successor"])))
    execution_ok = float(correct["execution"]) >= float(zero["execution"]) - 0.05 - 1e-12
    task_ok = int(summary["positive_task_count"]) * 2 >= int(summary["task_count"])
    if improves and beats_both and companion_ok and execution_ok and task_ok:
        return "STRONG"
    if improves and beats_one and execution_ok:
        return "PARTIAL"
    return "CLEAR_FAILURE"


def selection_score(summary: Mapping[str, Any]) -> float:
    zero, correct = summary["L0_zero"], summary["L1_correct"]
    transition, state = summary["L2_key_payload_shuffle"], summary["L3_state_query_shuffle"]
    return (
        float(correct["semantic_successor"]) - max(float(transition["semantic_successor"]), float(state["semantic_successor"]))
        + 0.5 * (float(correct["action_signature"]) - max(float(transition["action_signature"]), float(state["action_signature"])))
        + 0.5 * (float(correct["semantic_successor"]) - float(zero["semantic_successor"]))
        + 0.25 * (float(correct["action_signature"]) - float(zero["action_signature"]))
        - max(0.0, float(zero["execution"]) - float(correct["execution"]))
    )
def _generate(*, backend: Any, reader: Any, messages: Sequence[Mapping[str, str]], slots: Tensor, max_new_tokens: int) -> tuple[list[int], str, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    with torch.no_grad(), hooks, torch.autocast(
        device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"
    ), _attention_context(backend.device):
        output = backend.model.generate(
            input_ids=tokenized.input_ids,
            attention_mask=tokenized.attention_mask,
            max_new_tokens=int(max_new_tokens),
            do_sample=False,
            use_cache=True,
            pad_token_id=backend.tokenizer.eos_token_id,
            eos_token_id=backend.tokenizer.eos_token_id,
        )
    generated = [int(value) for value in output[0, prompt_length:].tolist()]
    return generated, backend.tokenizer.decode(generated, skip_special_tokens=True), hooks.audit.as_dict()


def _offline_contributions(*, data: Mapping[str, Any], tensors: Mapping[str, Any], payloads: Tensor, query: Tensor, shuffled_payloads: bool) -> list[dict[str, Any]]:
    coefficients = tensors["rho"] * (tensors["keys"] @ query.to(tensors["keys"].dtype))
    payload_index = tensors["permutation"] if shuffled_payloads else torch.arange(len(data["train_ids"]), device=coefficients.device)
    payload_norms = payloads[payload_index].to(torch.float32).flatten(start_dim=1).norm(dim=1)
    contribution = coefficients.abs().to(torch.float32) * payload_norms
    top = torch.topk(contribution, k=min(8, int(contribution.numel())))
    rows = []
    for score, index in zip(top.values.tolist(), top.indices.tolist(), strict=True):
        payload_position = int(payload_index[index])
        rows.append({
            "key_transition_id": str(data["train_ids"][index]),
            "payload_transition_id": str(data["train_ids"][payload_position]),
            "absolute_pre_norm_contribution": float(score),
            "signed_address_coefficient": float(coefficients[index].detach().cpu()),
        })
    return rows


def _field_bundle(*, writer: Any, tensors: Mapping[str, Any], data: Mapping[str, Any], epoch: int, live: Mapping[str, Path], checkpoint_sha256: str) -> dict[str, Any]:
    with torch.no_grad():
        payloads = writer(tensors["memory_views"])
        A, B = compile_differentiable_field(keys=tensors["keys"], payloads=payloads, rho=tensors["rho"])
        shuffled_A, shuffled_B = compile_differentiable_field(
            keys=tensors["keys"], payloads=payloads[tensors["permutation"]], rho=tensors["rho"]
        )
    output: dict[str, Any] = {}
    for name, field_A, field_B in (("correct", A, B), ("key_payload_shuffle", shuffled_A, shuffled_B)):
        path = live["field_artifacts"] / f"epoch_{epoch:02d}_{name}.pt"
        payload = {
            "format": "rcmf_full_field_artifact_9a_v1",
            "epoch": epoch,
            "control": name,
            "A": field_A.detach().cpu(),
            "B": field_B.detach().cpu(),
            "shape_A": list(field_A.shape),
            "shape_B": list(field_B.shape),
            "memory_count": len(data["train_ids"]),
            "checkpoint_sha256": checkpoint_sha256,
            "runtime_retrieval": False,
        }
        if path.exists():
            existing = torch.load(path, map_location="cpu", weights_only=False)
            checks = {
                "format": existing.get("format") == payload["format"],
                "epoch": int(existing.get("epoch", -1)) == epoch,
                "control": str(existing.get("control")) == name,
                "checkpoint": str(existing.get("checkpoint_sha256")) == checkpoint_sha256,
                "memory_count": int(existing.get("memory_count", -1)) == len(data["train_ids"]),
                "A": _tensor_sha256(existing["A"]) == _tensor_sha256(payload["A"]),
                "B": _tensor_sha256(existing["B"]) == _tensor_sha256(payload["B"]),
            }
            if not all(checks.values()):
                raise ValueError(f"Existing full-field artifact differs: {checks}")
        else:
            _atomic_torch_save(payload, path)
        output[name] = {"A": field_A, "B": field_B, "path": path, "sha256": sha256_file(path)}
    output["payloads"] = payloads
    return output


def _slot_for_condition(*, condition: Mapping[str, Any], fields: Mapping[str, Any], tensors: Mapping[str, Any], data: Mapping[str, Any], live: Mapping[str, Path], checkpoint_sha256: str) -> tuple[Tensor, dict[str, Any]]:
    query_state = str(condition["field_query_state_id"])
    query = tensors["queries"][data["state_position"][query_state]]
    control = str(condition["field_control"])
    if control == "zero":
        slots = torch.zeros(8, 256, device=query.device, dtype=torch.float32)
        field_path = None
        field_sha = None
        contributions: list[dict[str, Any]] = []
    else:
        bundle = fields[control]
        slots = read_compiled_field(query=query, A=bundle["A"], B=bundle["B"], nonempty=True)
        field_path = str(bundle["path"])
        field_sha = str(bundle["sha256"])
        contributions = _offline_contributions(
            data=data,
            tensors=tensors,
            payloads=fields["payloads"],
            query=query,
            shuffled_payloads=control == "key_payload_shuffle",
        )
    slot_path = live["slot_artifacts"] / f"{condition_checkpoint_name(str(condition['condition_key']))}.pt"
    slot_payload = {
        "format": "rcmf_state_conditioned_field_slots_9a_v1",
        "condition_key": str(condition["condition_key"]),
        "checkpoint_sha256": checkpoint_sha256,
        "query": query.detach().cpu(),
        "slots": slots.detach().cpu(),
        "field_artifact": field_path,
        "field_artifact_sha256": field_sha,
    }
    if slot_path.exists():
        existing = torch.load(slot_path, map_location="cpu", weights_only=False)
        checks = {
            "format": existing.get("format") == slot_payload["format"],
            "condition": str(existing.get("condition_key")) == str(condition["condition_key"]),
            "checkpoint": str(existing.get("checkpoint_sha256")) == checkpoint_sha256,
            "query": _tensor_sha256(existing["query"]) == _tensor_sha256(slot_payload["query"]),
            "slots": _tensor_sha256(existing["slots"]) == _tensor_sha256(slot_payload["slots"]),
            "field": str(existing.get("field_artifact_sha256")) == str(field_sha),
        }
        if not all(checks.values()):
            raise ValueError(f"Existing state-conditioned slot artifact differs: {checks}")
    else:
        _atomic_torch_save(slot_payload, slot_path)
    return slots, {
        "query_sha256": _tensor_sha256(query),
        "query_values": [float(value) for value in query.detach().cpu()],
        "slots_sha256": _tensor_sha256(slots),
        "slots_shape": list(slots.shape),
        "slots_dtype": str(slots.dtype),
        "slot_wise_norms": [float(value) for value in slots.to(torch.float32).norm(dim=-1).cpu()],
        "slot_artifact": str(slot_path),
        "slot_artifact_sha256": sha256_file(slot_path),
        "field_artifact": field_path,
        "field_artifact_sha256": field_sha,
        "top_memory_contributions_offline": contributions,
    }
def _run_condition(
    *, condition: Mapping[str, Any], output_path: Path, stderr_path: Path,
    slot_info: Mapping[str, Any], slots: Tensor, checkpoint: Path,
    checkpoint_sha256: str, manifest_sha256: str, config_sha256: str,
    replay: Mapping[str, Any], settings: Mapping[str, Any], examples: Mapping[str, Any],
    records: Mapping[str, Any], backend: Any, reader: Any, attempt_id: str, ordinal: int,
) -> tuple[dict[str, Any], bool]:
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "format": row.get("format") == LIVE_RESULT_VERSION,
            "condition": str(row.get("condition_key")) == str(condition["condition_key"]),
            "manifest": str(row.get("condition_manifest_sha256")) == manifest_sha256,
            "checkpoint": str(row.get("checkpoint_sha256")) == checkpoint_sha256,
            "config": str(row.get("config_sha256")) == config_sha256,
            "complete": str(row.get("status")) == "complete",
            "slot": str(row.get("field", {}).get("slot_artifact_sha256")) == str(slot_info["slot_artifact_sha256"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Existing full-field live row differs: {checks}")
        return row, True

    state_id = str(condition["world_state_id"])
    task_id = str(condition["source_task_id"])
    example = examples[state_id]
    contract = _state_contract(example, records[task_id])
    bridge_condition = {
        "condition_key": str(condition["condition_key"]),
        "state_example_id": state_id,
        "state_task_id": task_id,
    }
    prepare = _prepare_message(
        condition=bridge_condition,
        contract=contract,
        settings={"legacy": replay["legacy"], "replay": replay["replay"]},
        semantic_path=Path(str(replay["replay"]["semantic_module"])),
        bridge_attempt=f"{attempt_id}-{ordinal:05d}-{time.time_ns()}",
    )
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
        actual_observations = list(ready["actual_observations"])
        messages = build_live_appworld_messages(
            example,
            actual_observations,
            prompt_profile=str(settings["appworld"]["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        prompt_tokens = int(tokenized.attention_mask.sum().item())
        remaining = int(settings["appworld"]["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Full-field live prompt over context: {state_id}")
        maximum = min(int(settings["appworld"]["max_new_tokens"]), remaining)
        generation_started = time.perf_counter()
        token_ids, text, hook = _generate(
            backend=backend, reader=reader, messages=messages, slots=slots, max_new_tokens=maximum
        )
        generation_seconds = time.perf_counter() - generation_started
        code, fixed = extract_code_and_fix_content(text)
        executed = client.execute(
            condition_key=str(condition["condition_key"]),
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(contract["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise
    metrics = evaluate_generated_action(
        text, code, str(contract["target_action"]), str(executed["raw_observation"]), str(contract["target_observation"])
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
        metrics["exception_category"] = str(executed["execution_exception"].get("type", "exception")).lower()
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    trajectory = [
        {"response": str(step["response"]), "observation": str(observation)}
        for step, observation in zip(contract["history_steps"], actual_observations, strict=True)
    ]
    static_messages = list(messages[:-1])
    row = {
        "format": LIVE_RESULT_VERSION,
        "status": "complete",
        **dict(condition),
        "condition_manifest_sha256": manifest_sha256,
        "config_sha256": config_sha256,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": checkpoint_sha256,
        "model_name": "Qwen/Qwen3-8B",
        "tokenizer_identity": str(getattr(backend.tokenizer, "name_or_path", "unknown")),
        "seed": GLOBAL_SEED,
        "temperature": float(settings["appworld"]["temperature"]),
        "top_p": float(settings["appworld"]["top_p"]),
        "max_new_tokens": maximum,
        "do_sample": False,
        "enable_thinking": False,
        "prompt_profile": str(settings["appworld"]["prompt_profile"]),
        "model_messages": list(messages),
        "task_message": str(contract["query"]),
        "trajectory_so_far": trajectory,
        "actual_replay_observations": actual_observations,
        "static_prompt_asset_sha256": sha256_text(json.dumps(static_messages, sort_keys=True, ensure_ascii=False)),
        "rendered_messages_sha256": sha256_text(rendered),
        "prompt_tokens": prompt_tokens,
        "context_limit": int(settings["appworld"]["context_limit"]),
        "truncation_applied": False,
        "raw_model_response": text,
        "generated_token_ids": token_ids,
        "fixed_model_response": fixed,
        "extracted_code": code,
        "automatically_repaired_response": fixed,
        "automatically_repaired_code": code,
        "executed_code": code,
        "execution_exception": executed["execution_exception"],
        "complete_environment_observation": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "task_completed_status": executed.get("task_completed"),
        "metrics": metrics,
        "target_action_sha256": contract["target_action_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "field": dict(slot_info),
        "reader_audit": hook,
        "live_ready": ready,
        "live_executed": executed,
        "same_world_execution": bool(executed["same_world_execution"]),
        "same_python_namespace": bool(executed["same_python_namespace"]),
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
        "student_prompt_contains_raw_memory": False,
        "runtime_memory_retrieval": False,
        "memory_slots_in_self_attention_kv": False,
        "generation_elapsed_seconds": generation_seconds,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output_path, row)
    return row, False
def _validate_live(
    *, cfg: Any, settings: Mapping[str, Any], replay: Mapping[str, Any],
    paths: Mapping[str, Path], live: Mapping[str, Path], data: Mapping[str, Any],
    manifest: Mapping[str, Any], attempt: AttemptLedger, attempt_id: str, config_path: Path,
) -> dict[str, Any]:
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen must remain frozen during live validation")
    tensors = _runtime_tensors(data, backend.device)
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    examples = _examples_by_state(load_decision_examples(corpus / "decision_examples.jsonl"))
    records = _records_by_task(load_memory_records(corpus / "memory_records.jsonl"))
    conditions_by_epoch: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for condition in manifest["conditions"]:
        conditions_by_epoch[int(condition["epoch"])].append(condition)
    reports = []
    completed = 0
    resumed = 0
    for epoch in (1, 2):
        checkpoint_path = paths["checkpoints"] / f"epoch_{epoch:02d}.pt"
        checkpoint_sha = sha256_file(checkpoint_path)
        checkpoint = torch.load(checkpoint_path, map_location=backend.device, weights_only=False)
        writer, reader = _build_components(backend.device)
        writer.load_state_dict(checkpoint["writer_state_dict"])
        reader.load_state_dict(checkpoint["reader_state_dict"])
        writer.eval()
        reader.eval()
        fields = _field_bundle(
            writer=writer, tensors=tensors, data=data, epoch=epoch, live=live, checkpoint_sha256=checkpoint_sha
        )
        epoch_rows = []
        for condition in conditions_by_epoch[epoch]:
            slots, slot_info = _slot_for_condition(
                condition=condition, fields=fields, tensors=tensors, data=data,
                live=live, checkpoint_sha256=checkpoint_sha,
            )
            output = live["condition_outputs"] / condition_checkpoint_name(str(condition["condition_key"]))
            stderr = live["worker_logs"] / (condition_checkpoint_name(str(condition["condition_key"])) + ".stderr.log")
            row, was_reused = _run_condition(
                condition=condition,
                output_path=output,
                stderr_path=stderr,
                slot_info=slot_info,
                slots=slots,
                checkpoint=checkpoint_path,
                checkpoint_sha256=checkpoint_sha,
                manifest_sha256=str(manifest["manifest_sha256"]),
                config_sha256=sha256_file(config_path),
                replay=replay,
                settings=settings,
                examples=examples,
                records=records,
                backend=backend,
                reader=reader,
                attempt_id=attempt_id,
                ordinal=completed + 1,
            )
            epoch_rows.append(row)
            completed += 1
            resumed += int(was_reused)
            attempt.progress(
                status=f"full_field_live_epoch_{epoch}",
                completed_conditions=completed,
                total_conditions=int(manifest["condition_count"]),
                latest_validated_checkpoint=str(output),
            )
        summary = summarize_live_controls(epoch_rows)
        report = {
            "format": "rcmf_full_field_live_checkpoint_report_9a_v1",
            "epoch": epoch,
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": checkpoint_sha,
            "state_count": 98,
            "condition_count": len(epoch_rows),
            "metrics": summary,
            "classification": classify_live_checkpoint(summary),
            "selection_score": selection_score(summary),
            "stable_generation": all(
                bool(row["same_world_execution"]) and bool(row["same_python_namespace"])
                and bool(row["history_semantic_v3_match"]) for row in epoch_rows
            ),
            "every_nonzero_condition_used_complete_field": all(
                int(row["complete_bank_memory_count"]) == 401
                for row in epoch_rows if str(row["control"]) != "L0_zero"
            ),
            "runtime_memory_retrieval_used": False,
            "test_normal_outcomes_used": False,
        }
        reports.append(report)
        report_path = live["root"] / f"epoch_{epoch:02d}/summary.json"
        atomic_write_json(report_path, report)
        attempt.progress(
            status=f"full_field_live_epoch_{epoch}_complete",
            latest_validated_checkpoint=str(report_path),
            classification=report["classification"],
            metrics=summary,
        )
        print(json.dumps(report, sort_keys=True), flush=True)
    assert_frozen_without_gradients(backend.model)
    result = {
        "format": "rcmf_full_field_live_summary_9a_v1",
        "global_seed": GLOBAL_SEED,
        "checkpoint_count": len(reports),
        "new_condition_count": completed - resumed,
        "resumed_condition_count": resumed,
        "reports": reports,
        "checkpoint_selection_permitted": True,
        "passed_infrastructure": len(reports) == 2 and all(bool(row["stable_generation"]) for row in reports),
        "test_normal_outcomes_used": False,
    }
    atomic_write_json(live["summary"], result)
    return result


def select_checkpoint(*, live_summary: Mapping[str, Any], teacher_summary: Mapping[str, Any]) -> dict[str, Any]:
    teacher_by_epoch = {int(row["epoch"]): row for row in teacher_summary["reports"]}
    candidates = []
    for source in live_summary["reports"]:
        row = dict(source)
        epoch = int(row["epoch"])
        row["heldout_correct_policy_kl"] = float(teacher_by_epoch[epoch]["metrics"]["V1_correct"]["policy_kl"])
        row["eligible"] = bool(row["classification"] != "CLEAR_FAILURE" and row["stable_generation"])
        candidates.append(row)
    selected = None
    for classification in ("STRONG", "PARTIAL"):
        values = [row for row in candidates if row["eligible"] and row["classification"] == classification]
        if values:
            selected = max(
                values,
                key=lambda row: (float(row["selection_score"]), -float(row["heldout_correct_policy_kl"]), -int(row["epoch"])),
            )
            break
    return {
        "format": "rcmf_full_field_checkpoint_selection_9a_v1",
        "global_seed": GLOBAL_SEED,
        "candidates": candidates,
        "selected": selected,
        "classification": "CLEAR_FAILURE" if selected is None else selected["classification"],
        "decision_branch": "rcmf_full_field_joint_training_failed" if selected is None else "rcmf_full_field_checkpoint_selected",
        "heldout_train_only_selection": True,
        "test_normal_outcomes_used": False,
        "run_instant_add": selected is not None,
    }
def _instant_add(*, paths: Mapping[str, Path], live: Mapping[str, Path], data: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    selected = selection.get("selected")
    if not isinstance(selected, Mapping):
        raise RuntimeError("Instant add requires a selected positive checkpoint")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint_path = Path(str(selected["checkpoint"]))
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    writer, _ = _build_components(device)
    writer.load_state_dict(checkpoint["writer_state_dict"])
    writer.eval()
    source = data["source"]
    ordered_ids = [str(value) for value in source["ordered_transition_ids"]]
    positions = {value: index for index, value in enumerate(ordered_ids)}
    transition_by_id = data["transition_by_id"]
    rho_map = data["data_manifest"]["rho_by_transition_id"]
    train_ids = list(data["train_ids"])
    train_set = set(train_ids)
    heldout_ids = [value for value in ordered_ids if value not in train_set]
    if len(heldout_ids) != 98:
        raise ValueError("Heldout instant-add memory count differs")
    field = ReversibleRCMFField(device=device)
    compile_seconds: list[float] = []
    add_seconds: list[float] = []
    payload_by_id: dict[str, Tensor] = {}

    def compile_one(memory_id: str) -> Tensor:
        view = source["memory_views"][positions[memory_id]].to(device, torch.float32)
        if device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.no_grad():
            payload = writer(view.unsqueeze(0))[0]
        if device.type == "cuda":
            torch.cuda.synchronize()
        compile_seconds.append(time.perf_counter() - started)
        return payload

    def record(memory_id: str, payload: Tensor) -> RCMFFieldRecord:
        row = transition_by_id[memory_id]
        return RCMFFieldRecord(
            memory_id=memory_id,
            parent_id=str(row["parent_memory_id"]),
            parent_task_id=str(row["parent_task_id"]),
            key=source["memory_keys"][positions[memory_id]].to(device, torch.float32),
            payload=payload,
            rho=float(rho_map[memory_id]),
            mu=0.0,
        )

    train_positions = torch.tensor([positions[value] for value in train_ids], dtype=torch.long)
    with torch.no_grad():
        train_payloads = writer(source["memory_views"][train_positions].to(device, torch.float32))
    for memory_id, payload in zip(train_ids, train_payloads, strict=True):
        payload_by_id[memory_id] = payload
        field.add_memory_fast(record(memory_id, payload))
    shape_before = field.field_shape
    started_total = time.perf_counter()
    for memory_id in heldout_ids:
        payload = compile_one(memory_id)
        payload_by_id[memory_id] = payload
        if device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        field.add_memory_fast(record(memory_id, payload))
        if device.type == "cuda":
            torch.cuda.synchronize()
        add_seconds.append(time.perf_counter() - started)
    total_seconds = time.perf_counter() - started_total
    rebuilt_A, rebuilt_B = field.audit_rebuild()
    rebuild_max_abs = max(float((field.A - rebuilt_A).abs().max().cpu()), float((field.B - rebuilt_B).abs().max().cpu()))
    parent_id = str(transition_by_id[heldout_ids[0]]["parent_memory_id"])
    before_remove_A, before_remove_B = field.A.clone(), field.B.clone()
    removed = field.remove_parent_fast(parent_id)
    field.restore_parent_fast(removed)
    restore_max_abs = max(float((field.A - before_remove_A).abs().max().cpu()), float((field.B - before_remove_B).abs().max().cpu()))

    complete_shuffle = _json(paths["shuffle"])["complete_deployment_bank"]
    payload_target = {str(row["key_transition_id"]): str(row["payload_transition_id"]) for row in complete_shuffle["rows"]}
    all_keys = torch.stack([source["memory_keys"][positions[value]].to(device, torch.float32) for value in ordered_ids])
    all_payloads = torch.stack([payload_by_id[value] for value in ordered_ids])
    payload_position = {value: index for index, value in enumerate(ordered_ids)}
    permutation = torch.tensor([payload_position[payload_target[value]] for value in ordered_ids], device=device)
    all_rho = torch.tensor([float(rho_map[value]) for value in ordered_ids], device=device)
    shuffled_A, shuffled_B = compile_differentiable_field(
        keys=all_keys, payloads=all_payloads[permutation], rho=all_rho
    )
    deployment_payload = {
        "format": "rcmf_complete_37_task_deployment_field_9a_v1",
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "A": field.A.detach().cpu(),
        "B": field.B.detach().cpu(),
        "shuffled_A": shuffled_A.detach().cpu(),
        "shuffled_B": shuffled_B.detach().cpu(),
        "memory_count": len(field.records),
        "memory_ids": sorted(field.records),
        "field_shape": field.field_shape,
        "writer_state_dict": checkpoint["writer_state_dict"],
        "reader_state_dict": checkpoint["reader_state_dict"],
        "runtime_memory_retrieval": False,
    }
    _atomic_torch_save(deployment_payload, live["deployment_field"])
    result = {
        "format": "rcmf_instant_heldout_memory_addition_9a_v1",
        "global_seed": GLOBAL_SEED,
        "selected_checkpoint": str(checkpoint_path),
        "selected_checkpoint_sha256": sha256_file(checkpoint_path),
        "new_memory_count": len(heldout_ids),
        "field_memory_count_before": len(train_ids),
        "field_memory_count_after": len(field.records),
        "field_shape_before": shape_before,
        "field_shape_after": field.field_shape,
        "mean_compile_seconds_per_memory": statistics.fmean(compile_seconds),
        "max_compile_seconds_per_memory": max(compile_seconds),
        "mean_add_seconds_per_memory": statistics.fmean(add_seconds),
        "max_add_seconds_per_memory": max(add_seconds),
        "total_heldout_compile_add_seconds": total_seconds,
        "peak_cuda_memory_bytes": int(torch.cuda.max_memory_allocated()) if device.type == "cuda" else 0,
        "production_add_scans_existing_records": False,
        "audit_rebuild_max_abs": rebuild_max_abs,
        "removed_parent_id": parent_id,
        "removed_parent_memory_count": len(removed),
        "remove_restore_max_abs": restore_max_abs,
        "no_retraining_or_optimizer_step": True,
        "deployment_field": str(live["deployment_field"]),
        "deployment_field_sha256": sha256_file(live["deployment_field"]),
        "complete_shuffle_fixed_points": int(complete_shuffle["fixed_point_count"]),
        "passed": len(field.records) == 499 and rebuild_max_abs <= 1e-4 and restore_max_abs <= 1e-4,
    }
    atomic_write_json(live["instant_add"], result)
    if not result["passed"]:
        raise RuntimeError(f"Instant heldout-memory addition invariant failed: {result}")
    return result


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_9a"]
    replay = replay_cfg.raw["stage_c_7b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-031A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    paths = _paths(settings, args.artifact_dir)
    live = _live_paths(args.artifact_dir)
    required = (
        "source_cache", "data_manifest", "source_audit", "selector_audit", "shuffle",
        "static_counts", "outcomes", "teacher", "transitions", "state_shuffle",
        "training_summary", "validation_summary",
    )
    _require(paths, required)
    data = _load_data(paths)
    manifest, manifest_exists = _resolve_manifest(data=data, paths=paths, live=live)
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    source_hashes["live_manifest_content"] = str(manifest["manifest_sha256"])
    source_hashes["replay_config"] = sha256_file(args.replay_config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"joint_full_bank_{args.phase}",
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
        if not manifest_exists:
            atomic_write_json(live["manifest"], manifest)
        if args.phase == "manifest":
            result, latest = manifest, live["manifest"]
        elif args.phase == "teacher-corrected":
            corrected = dict(paths)
            corrected["validation_root"] = live["corrected_teacher_root"]
            corrected["validation_summary"] = live["corrected_teacher_summary"]
            result = _teacher_validate(cfg=cfg, paths=corrected, attempt=attempt)
            result["supersedes_for_zero_control_only"] = str(paths["validation_summary"])
            result["zero_field_bypass_version"] = "trained_affine_memory_norm_zero_bypass_9a_v1"
            atomic_write_json(live["corrected_teacher_summary"], result)
            latest = live["corrected_teacher_summary"]
        elif args.phase == "validate":
            result = _validate_live(
                cfg=cfg, settings=settings, replay=replay, paths=paths, live=live,
                data=data, manifest=manifest, attempt=attempt, attempt_id=args.attempt_id,
                config_path=args.config,
            )
            latest = live["summary"]
        elif args.phase == "select":
            _require(live, ("summary", "corrected_teacher_summary"))
            result = select_checkpoint(
                live_summary=_json(live["summary"]), teacher_summary=_json(live["corrected_teacher_summary"])
            )
            atomic_write_json(live["selection"], result)
            latest = live["selection"]
        else:
            _require(live, ("selection",))
            result = _instant_add(paths=paths, live=live, data=data, selection=_json(live["selection"]))
            latest = live["instant_add"]
        attempt.progress(
            status=f"joint_full_bank_{args.phase}_complete",
            latest_validated_checkpoint=str(latest),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()