from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    INSERTION_LAYERS,
    CalibratedFieldReaderHooks,
    CalibrationCandidate,
    compile_positive_field,
    preregistered_candidates,
    raw_compiled_field,
    read_confidence_field,
    read_positive_field,
    tau_for_median_confidence,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    assert_frozen_without_gradients,
    freeze_module,
    read_compiled_field,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.prepare_rcmf_benefit_preserving_calibration_9b import validate_immutable_inputs
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _attempt_ids,
    _build_backend,
    _build_components,
    _legal_field,
    _load_data,
    _paths as _parent_paths,
    _runtime_tensors,
)
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss

RUNNER_VERSION = "rcmf_benefit_preserving_cached_diagnostics_9b_v1"
API_PATTERN = re.compile(r"apis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/benchmark/stage_c_rcmf_benefit_preserving_calibration_9b.yaml"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("equivalence", "profile", "diagnose"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031b_stage8a")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    frozen = settings["immutable_exp031a"]
    return {
        "parent_root": Path(str(frozen["artifact_root"])),
        "checkpoint": Path(str(frozen["checkpoint"])),
        "deployment": Path(str(frozen["deployment_field"])),
        "critical_audit": Path("research/analysis/exp031b_gain_loss_audit.json"),
        "field_tensors": Path(str(settings["gain_loss_audit"]["field_tensor_bundle"])),
        "profile_root": artifact_dir / "stage_8a/profile",
        "profile_summary": artifact_dir / "stage_8a/profile/summary.json",
        "ratio_tensors": artifact_dir / "stage_8a/profile/heldout_token_ratios.pt",
        "critical_teachers": artifact_dir / "stage_8a/profile/critical_policy_teachers.pt",
        "calibration": artifact_dir / "stage_8a/calibration_lock.json",
        "equivalence": artifact_dir / "stage_8a/equivalence.json",
        "diagnostic_root": artifact_dir / "stage_8a/candidate_rows",
        "diagnostic_summary": artifact_dir / "stage_8a/candidate_summary.json",
    }


def _tensor_sha256(value: Tensor) -> str:
    work = value.detach().cpu().contiguous()
    return hashlib.sha256(work.view(torch.uint8).numpy().tobytes()).hexdigest()


def _row_path(root: Path, universe: str, state_id: str, candidate_id: str) -> Path:
    return root / universe / f"{sha256_text(f'{universe}::{state_id}::{candidate_id}')}.json"


def _critical_step(task_result: Mapping[str, Any], step_id: int) -> dict[str, Any]:
    rows = [dict(row) for row in task_result["steps"] if int(row["step_id"]) == step_id]
    if len(rows) != 1:
        raise ValueError(f"Critical task step is not unique: {step_id}")
    return rows[0]


def _state_task_id(state_id: str) -> str:
    parts = state_id.split(":")
    if len(parts) < 4 or parts[:2] != ["appworld", "trace"] or not parts[2]:
        raise ValueError(f"Malformed AppWorld state ID: {state_id}")
    return parts[2]


def _target_row_from_step(backend: Any, step: Mapping[str, Any], pair_id: str) -> dict[str, Any]:
    prompt = backend.tokenizer.apply_chat_template(
        [dict(row) for row in step["exact_model_message_array"]],
        tokenize=True,
        add_generation_prompt=True,
    )
    if isinstance(prompt, Tensor):
        prompt = prompt.flatten().tolist()
    prompt_ids = [int(value) for value in prompt]
    target_ids = [int(value) for value in step["generated_token_ids"]]
    return {
        "pair_id": pair_id,
        "input_ids": prompt_ids + target_ids,
        "labels": [-100] * len(prompt_ids) + target_ids,
        "last_user_token_indices": [],
        "pad_token_id": int(backend.tokenizer.pad_token_id or backend.tokenizer.eos_token_id),
        "target_len": len(target_ids),
        "response_cache": {"target_token_ids": target_ids},
    }


def _sparse_teacher(logits: Tensor, target_ids: Sequence[int], top_k: int = 64) -> dict[str, Any]:
    work = F.log_softmax(logits.detach().float(), dim=-1)
    values, indices = torch.topk(work, k=min(top_k, int(work.shape[-1])), dim=-1)
    positions = []
    for position in range(int(work.shape[0])):
        positions.append({
            "position": position,
            "teacher_token_id": int(target_ids[position]),
            "top_token_ids": [int(value) for value in indices[position].cpu()],
            "top_logprobs": [float(value) for value in values[position].cpu()],
            "other_probability": max(0.0, 1.0 - float(values[position].exp().sum().cpu())),
        })
    return {"generated_token_ids": [int(value) for value in target_ids], "positions": positions}


def _token_intervals(tokenizer: Any, token_ids: Sequence[int]) -> tuple[str, list[tuple[int, int]]]:
    prefixes = [""]
    for end in range(1, len(token_ids) + 1):
        prefixes.append(tokenizer.decode(list(token_ids[:end]), skip_special_tokens=True, clean_up_tokenization_spaces=False))
    return prefixes[-1], [(len(prefixes[i]), len(prefixes[i + 1])) for i in range(len(token_ids))]


def _target_log_probability_metrics(*, tokenizer: Any, logits: Tensor, target_ids: Sequence[int]) -> dict[str, Any]:
    log_probs = F.log_softmax(logits.float(), dim=-1)
    ids = torch.tensor(target_ids, dtype=torch.long, device=logits.device)
    selected = log_probs.gather(-1, ids[:, None]).squeeze(-1)
    text, intervals = _token_intervals(tokenizer, target_ids)
    code, _ = extract_code_and_fix_content(text)
    calls = list(API_PATTERN.finditer(text))
    api_positions: list[int] = []
    signature_positions: list[int] = []
    for call_index, match in enumerate(calls):
        positions = [i for i, (left, right) in enumerate(intervals) if right > match.start() and left < match.end()]
        signature_positions.extend(positions)
        if call_index == 0:
            api_positions = positions
    signature_positions = sorted(set(signature_positions))
    predicted_text = tokenizer.decode(logits.argmax(dim=-1).tolist(), skip_special_tokens=True)
    predicted_code, _ = extract_code_and_fix_content(predicted_text)
    try:
        if not predicted_code.strip():
            raise SyntaxError("empty code")
        ast.parse(predicted_code)
        valid_python = True
    except (SyntaxError, ValueError, TypeError):
        valid_python = False
    mean_at = lambda positions: float(selected[positions].mean().cpu()) if positions else None
    return {
        "target_mean_log_probability": float(selected.mean().cpu()),
        "exact_target_api_log_probability": mean_at(api_positions),
        "action_signature_log_probability": mean_at(signature_positions),
        "target_api_token_count": len(api_positions),
        "action_signature_token_count": len(signature_positions),
        "target_api_call_count": len(list(API_PATTERN.finditer(code))),
        "execution_token_validity": valid_python,
        "argmax_target_token_accuracy": float((logits.argmax(dim=-1) == ids).float().mean().cpu()),
    }


def _forward(*, backend: Any, reader: Any, slots: Tensor, policy_row: Mapping[str, Any], ground_truth_row: Mapping[str, Any] | None, teacher: Mapping[str, Any], layer_scales: Sequence[float], layer_caps: Mapping[int, float] | None) -> tuple[dict[str, Any], CalibratedFieldReaderHooks]:
    rows = [dict(policy_row)] + ([] if ground_truth_row is None else [dict(ground_truth_row)])
    batch = _collate(rows, device=backend.device, k=4)
    hooks = CalibratedFieldReaderHooks(model=backend.model, reader=reader, slots=slots, layer_scales=layer_scales, layer_caps=layer_caps)
    with torch.no_grad(), hooks, torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        _, logits = _bare_target_forward(backend=backend, batch=batch)
    policy_length = int(policy_row["target_len"])
    policy_logits = logits[:policy_length]
    policy_kl, policy_terms = _policy_loss(policy_logits, teacher)
    if ground_truth_row is None:
        target_logits = policy_logits
        target_ids = [int(value) for value in teacher["generated_token_ids"]]
    else:
        target_length = int(ground_truth_row["target_len"])
        target_logits = logits[policy_length:policy_length + target_length]
        target_ids = [int(value) for value in ground_truth_row["response_cache"]["target_token_ids"]]
    return {
        "policy_kl": float(policy_kl.cpu()),
        "target_nll": float(policy_terms["teacher_token_ce"].cpu()),
        "policy_teacher_token_top1": float(policy_terms["top1"].cpu()),
        **_target_log_probability_metrics(tokenizer=backend.tokenizer, logits=target_logits, target_ids=target_ids),
        "attention": hooks.audit.as_dict(),
    }, hooks


def _signed_contribution_stats(query: Tensor, keys: Tensor, rho: Tensor) -> dict[str, Any]:
    weights = torch.mv(keys.float(), query.float()) * rho.float()
    quantiles = torch.quantile(weights, torch.tensor([0.01, 0.10, 0.50, 0.90, 0.99], device=weights.device))
    return {
        "count": int(weights.numel()),
        "negative_count": int((weights < 0).sum()),
        "positive_count": int((weights > 0).sum()),
        "negative_absolute_mass": float(weights[weights < 0].abs().sum()),
        "positive_mass": float(weights[weights > 0].sum()),
        "mean": float(weights.mean()),
        "std": float(weights.std(unbiased=False)),
        "quantiles": {name: float(value) for name, value in zip(("p01", "p10", "p50", "p90", "p99"), quantiles, strict=True)},
    }


def _field_universes(*, data: Mapping[str, Any], tensors: Mapping[str, Any], writer: Any, deployment: Mapping[str, Any], device: torch.device) -> dict[str, Any]:
    with torch.no_grad():
        hA, hB, payloads = _legal_field(writer=writer, tensors=tensors, query_task_id="__heldout_not_in_model_train_bank__", shuffled_payloads=False)
        hsA, hsB, _ = _legal_field(writer=writer, tensors=tensors, query_task_id="__heldout_not_in_model_train_bank__", shuffled_payloads=True)
        hN, hZ = compile_positive_field(keys=tensors["keys"], payloads=payloads, rho=tensors["rho"])
        hsN, hsZ = compile_positive_field(keys=tensors["keys"], payloads=payloads[tensors["permutation"]], rho=tensors["rho"])
        source = data["source"]
        all_payloads = writer(source["memory_views"].to(device=device, dtype=torch.float32))
        all_keys = source["memory_keys"].to(device=device, dtype=torch.float32)
        rho_map = data["data_manifest"]["rho_by_transition_id"]
        all_rho = torch.tensor([float(rho_map[str(value)]) for value in source["ordered_transition_ids"]], device=device)
        full_N, full_Z = compile_positive_field(keys=all_keys, payloads=all_payloads, rho=all_rho)
    return {
        "heldout": {"A": hA, "B": hB, "shuffled_A": hsA, "shuffled_B": hsB, "positive_N": hN, "positive_Z": hZ, "shuffled_positive_N": hsN, "shuffled_positive_Z": hsZ, "keys": tensors["keys"], "rho": tensors["rho"], "memory_count": len(data["train_ids"])},
        "critical": {"A": deployment["A"].to(device), "B": deployment["B"].to(device), "shuffled_A": deployment["shuffled_A"].to(device), "shuffled_B": deployment["shuffled_B"].to(device), "positive_N": full_N, "positive_Z": full_Z, "keys": all_keys, "rho": all_rho, "memory_count": int(deployment["memory_count"])},
    }

def _candidate_slots(*, candidate: CalibrationCandidate, query: Tensor, field: Mapping[str, Any], calibration: Mapping[str, Any]) -> tuple[Tensor, tuple[float, ...], dict[int, float] | None, dict[str, Any]]:
    scales = tuple(float(value) for value in candidate.layer_scales)
    caps = None
    audit: dict[str, Any] = {"route": candidate.route}
    if candidate.field_control == "zero":
        return torch.zeros(8, 256, device=query.device), scales, caps, audit
    if candidate.field_control == "shuffled":
        return read_compiled_field(query=query, A=field["shuffled_A"], B=field["shuffled_B"], nonempty=True), scales, caps, audit
    if candidate.positive_kernel:
        return read_positive_field(query=query, numerator=field["positive_N"], normalizer=field["positive_Z"], nonempty=True), scales, caps, audit
    tau = None if candidate.confidence_target is None else float(calibration["taus"][candidate.candidate_id])
    slots, read_audit = read_confidence_field(query=query, A=field["A"], B=field["B"], tau=tau, nonempty=True)
    audit.update({"raw_field_rms": float(read_audit["raw_rms"].cpu()), "confidence": float(read_audit["confidence"].cpu())})
    if candidate.cap_quantile is not None:
        caps = {int(layer): float(value) for layer, value in calibration["caps"][candidate.candidate_id].items()}
    return slots, scales, caps, audit


def _load_runtime(cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    validate_immutable_inputs(settings)
    parent = _parent_paths(cfg.raw["stage_c_9a"], paths["parent_root"])
    data = _load_data(parent)
    backend = _build_backend(cfg)
    backend.model.eval()
    backend.model.config.use_cache = True
    tensors = _runtime_tensors(data, backend.device)
    checkpoint = torch.load(paths["checkpoint"], map_location=backend.device, weights_only=False)
    writer, reader = _build_components(backend.device)
    writer.load_state_dict(checkpoint["writer_state_dict"])
    reader.load_state_dict(checkpoint["reader_state_dict"])
    freeze_module(writer)
    freeze_module(reader)
    deployment = torch.load(paths["deployment"], map_location="cpu", weights_only=False)
    fields = _field_universes(data=data, tensors=tensors, writer=writer, deployment=deployment, device=backend.device)
    assert_frozen_without_gradients(writer)
    assert_frozen_without_gradients(reader)
    assert_frozen_without_gradients(backend.model)
    return {"backend": backend, "data": data, "tensors": tensors, "writer": writer, "reader": reader, "deployment": deployment, "fields": fields, "parent_paths": parent}


def _critical_inputs(runtime: Mapping[str, Any], settings: Mapping[str, Any], paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    audit = _json(paths["critical_audit"])
    tensors = torch.load(paths["field_tensors"], map_location="cpu", weights_only=False)["first37"]
    groups = {str(row["task_id"]): str(row["group"]) for row in audit["tasks"]}
    rows = []
    source_root = Path("runs/stage_c/rcmf_joint_full_bank_9a_20260826_001/first37/conditions/D1/task_results")
    for task_id, spec in sorted(settings["gain_loss_audit"]["critical_steps"].items()):
        step_id = int(spec["d1_critical_step"])
        step = _critical_step(_json(source_root / f"{task_id}.json"), step_id)
        tensor_row = tensors[f"D1:{task_id}:{step_id}"]
        rows.append({
            "universe": "critical",
            "state_id": f"critical:{task_id}:step:{step_id}",
            "task_id": task_id,
            "group": groups[task_id],
            "query": tensor_row["query"].to(runtime["backend"].device, torch.float32),
            "stored_slots": tensor_row["slots"].to(runtime["backend"].device, torch.float32),
            "policy_row": _target_row_from_step(runtime["backend"], step, f"critical::{task_id}::{step_id}"),
            "ground_truth_row": None,
            "step": step,
        })
    return rows


def _heldout_inputs(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    data, source = runtime["data"], runtime["data"]["source"]
    rows = []
    outcomes = sorted((row for row in data["outcomes"] if str(row["model_split"]) == "heldout_train_validation"), key=lambda row: str(row["state_example_id"]))
    for outcome in outcomes:
        state_id = str(outcome["state_example_id"])
        target = "raw" if str(outcome["label"]) == "POSITIVE" else "bare"
        rows.append({
            "universe": "heldout",
            "state_id": state_id,
            "task_id": str(outcome["state_task_id"]),
            "group": str(outcome["label"]),
            "query": source["state_queries"][data["state_position"][state_id]].to(runtime["backend"].device, torch.float32),
            "policy_row": data["teacher"]["policy_rows"][state_id][target],
            "ground_truth_row": data["teacher"]["ground_truth_rows"][state_id],
            "teacher": data["teacher"]["teacher_rows"][state_id][target],
            "policy_target": target,
        })
    if len(rows) != 98:
        raise ValueError(f"Expected 98 heldout states, found {len(rows)}")
    return rows


def _heldout_profile_inputs(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    data, source = runtime["data"], runtime["data"]["source"]
    state_ids = sorted(
        str(value)
        for value in data["teacher"]["ordered_state_ids"]
        if _state_task_id(str(value)) in data["heldout_tasks"]
    )
    rows = []
    for state_id in state_ids:
        policy_row = data["teacher"]["policy_rows"][state_id]["bare"]
        rows.append({
            "universe": "heldout",
            "state_id": state_id,
            "task_id": _state_task_id(state_id),
            "group": "unlabeled_heldout_profile",
            "query": source["state_queries"][data["state_position"][state_id]].to(
                runtime["backend"].device, torch.float32
            ),
            "policy_row": policy_row,
            "ground_truth_row": None,
            "teacher": data["teacher"]["teacher_rows"][state_id]["bare"],
            "policy_target": "fixed_bare_reference_for_prompt_only_profile",
            "prompt_length": len(policy_row["input_ids"]) - int(policy_row["target_len"]),
        })
    if len(rows) != 98:
        raise ValueError(f"Expected 98 unlabeled heldout profile states, found {len(rows)}")
    return rows


def _generate(*, backend: Any, messages: Sequence[Mapping[str, str]], reader: Any | None, slots: Tensor | None, calibrated: bool) -> tuple[list[int], str]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = None
    if reader is not None and slots is not None:
        hooks = CalibratedFieldReaderHooks(model=backend.model, reader=reader, slots=slots) if calibrated else FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        if hooks is None:
            output = backend.model.generate(input_ids=tokenized.input_ids, attention_mask=tokenized.attention_mask, max_new_tokens=512, do_sample=False, use_cache=True, pad_token_id=backend.tokenizer.eos_token_id, eos_token_id=backend.tokenizer.eos_token_id)
        else:
            with hooks:
                output = backend.model.generate(input_ids=tokenized.input_ids, attention_mask=tokenized.attention_mask, max_new_tokens=512, do_sample=False, use_cache=True, pad_token_id=backend.tokenizer.eos_token_id, eos_token_id=backend.tokenizer.eos_token_id)
    ids = [int(value) for value in output[0, prompt_length:].tolist()]
    return ids, backend.tokenizer.decode(ids, skip_special_tokens=True)


def _equivalence(runtime: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    backend, reader = runtime["backend"], runtime["reader"]
    task_id, step_id = "0d01c76_3", 1
    roots = Path("runs/stage_c/rcmf_joint_full_bank_9a_20260826_001/first37/conditions")
    d0 = _critical_step(_json(roots / "D0/task_results" / f"{task_id}.json"), step_id)
    d1 = _critical_step(_json(roots / "D1/task_results" / f"{task_id}.json"), step_id)
    tensors = torch.load(paths["field_tensors"], map_location="cpu", weights_only=False)["first37"]
    slots = tensors[f"D1:{task_id}:{step_id}"]["slots"].to(backend.device, torch.float32)
    target = _target_row_from_step(backend, d1, "equivalence-g100")
    batch = _collate([target], device=backend.device, k=4)
    original = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    calibrated = CalibratedFieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    with torch.no_grad(), original, torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        _, original_logits = _bare_target_forward(backend=backend, batch=batch)
    with torch.no_grad(), calibrated, torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        _, calibrated_logits = _bare_target_forward(backend=backend, batch=batch)
    bare_target = _target_row_from_step(backend, d0, "equivalence-bare")
    bare_batch = _collate([bare_target], device=backend.device, k=4)
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        _, no_hook_logits = _bare_target_forward(backend=backend, batch=bare_batch)
    zero = CalibratedFieldReaderHooks(model=backend.model, reader=reader, slots=torch.zeros_like(slots))
    with torch.no_grad(), zero, torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"), _attention_context(backend.device):
        _, zero_logits = _bare_target_forward(backend=backend, batch=bare_batch)
    g_ids, g_text = _generate(backend=backend, messages=d1["exact_model_message_array"], reader=reader, slots=slots, calibrated=True)
    b_ids, b_text = _generate(backend=backend, messages=d0["exact_model_message_array"], reader=None, slots=None, calibrated=False)
    z_ids, z_text = _generate(backend=backend, messages=d0["exact_model_message_array"], reader=reader, slots=torch.zeros_like(slots), calibrated=True)
    g_code, _ = extract_code_and_fix_content(g_text)
    b_code, _ = extract_code_and_fix_content(b_text)
    z_code, _ = extract_code_and_fix_content(z_text)
    result = {
        "format": RUNNER_VERSION,
        "task_id": task_id,
        "step_id": step_id,
        "g100_logits_max_abs_difference": float((original_logits - calibrated_logits).abs().max().cpu()),
        "g100_logits_exact": bool(torch.equal(original_logits, calibrated_logits)),
        "g100_attention_exact": all(torch.equal(original.probabilities[layer], calibrated.probabilities[layer]) for layer in INSERTION_LAYERS),
        "g100_generated_token_ids_match_stored": g_ids == [int(value) for value in d1["generated_token_ids"]],
        "g100_executed_code_match_stored": g_code == str(d1["exact_executed_code"]),
        "bare_logits_max_abs_difference": float((no_hook_logits - zero_logits).abs().max().cpu()),
        "bare_logits_exact": bool(torch.equal(no_hook_logits, zero_logits)),
        "bare_generated_token_ids_match_stored": b_ids == [int(value) for value in d0["generated_token_ids"]],
        "bare_executed_code_match_stored": b_code == str(d0["exact_executed_code"]),
        "zero_field_generated_token_ids_match_bare": z_ids == b_ids,
        "zero_field_executed_code_match_bare": z_code == b_code,
    }
    required = ("g100_logits_exact", "g100_attention_exact", "g100_generated_token_ids_match_stored", "g100_executed_code_match_stored", "bare_logits_exact", "bare_generated_token_ids_match_stored", "bare_executed_code_match_stored", "zero_field_generated_token_ids_match_bare", "zero_field_executed_code_match_bare")
    result["passed"] = all(bool(result[key]) for key in required)
    return result

def _profile(runtime: Mapping[str, Any], settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger) -> dict[str, Any]:
    heldout = _heldout_profile_inputs(runtime)
    critical = _critical_inputs(runtime, settings, paths)
    ratio_values: dict[int, list[Tensor]] = defaultdict(list)
    critical_teachers: dict[str, Any] = {}
    summaries = []
    total = len(heldout) + len(critical)
    for index, item in enumerate((*heldout, *critical), start=1):
        field, query = runtime["fields"][item["universe"]], item["query"]
        raw = raw_compiled_field(query=query, A=field["A"], B=field["B"])
        slots = read_compiled_field(query=query, A=field["A"], B=field["B"], nonempty=True)
        shuffled = read_compiled_field(query=query, A=field["shuffled_A"], B=field["shuffled_B"], nonempty=True)
        reconstruction = float((slots - item.get("stored_slots", slots)).abs().max().cpu())
        if reconstruction > 5.0e-5:
            raise RuntimeError(f"Stored slot reconstruction failed: {item['state_id']} {reconstruction}")
        teacher = item.get("teacher")
        if teacher is None:
            batch = _collate([item["policy_row"]], device=runtime["backend"].device, k=4)
            probe = CalibratedFieldReaderHooks(model=runtime["backend"].model, reader=runtime["reader"], slots=slots)
            with torch.no_grad(), probe, torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=runtime["backend"].device.type == "cuda"), _attention_context(runtime["backend"].device):
                _, baseline_logits = _bare_target_forward(backend=runtime["backend"], batch=batch)
            teacher = _sparse_teacher(baseline_logits, item["policy_row"]["response_cache"]["target_token_ids"])
            critical_teachers[item["state_id"]] = teacher
        metrics, hooks = _forward(
            backend=runtime["backend"], reader=runtime["reader"], slots=slots,
            policy_row=item["policy_row"], ground_truth_row=item["ground_truth_row"],
            teacher=teacher, layer_scales=(1.0, 1.0, 1.0, 1.0), layer_caps=None,
        )
        layers = {}
        for layer in INSERTION_LAYERS:
            prompt_length = int(item.get("prompt_length", hooks.token_ratios[layer].shape[1]))
            values = hooks.token_ratios[layer][0, :prompt_length].flatten()
            hidden_rms = hooks.token_hidden_rms[layer][0, :prompt_length].flatten()
            delta_rms = hooks.token_delta_rms[layer][0, :prompt_length].flatten()
            if item["universe"] == "heldout":
                ratio_values[layer].append(values)
            quantiles = torch.quantile(values, torch.tensor([0.50, 0.75, 0.90, 0.99]))
            layers[str(layer)] = {
                "mean_hidden_rms": float(hidden_rms.mean()),
                "mean_delta_rms": float(delta_rms.mean()),
                "mean_ratio": float(values.mean()), "maximum_ratio": float(values.max()),
                "p50_ratio": float(quantiles[0]), "p75_ratio": float(quantiles[1]),
                "p90_ratio": float(quantiles[2]), "p99_ratio": float(quantiles[3]),
            }
        row = {
            "format": RUNNER_VERSION, "universe": item["universe"],
            "state_id": item["state_id"], "task_id": item["task_id"], "group": item["group"],
            "outcome_used_for_calibration": False,
            "raw_field_rms": float(raw.float().square().mean().sqrt().cpu()),
            "slot_cosine_correct_vs_shuffle": float(F.cosine_similarity(slots.flatten(), shuffled.flatten(), dim=0).cpu()),
            "slot_distance_correct_vs_shuffle": float((slots - shuffled).float().norm().cpu()),
            "slot_reconstruction_max_abs_error": reconstruction,
            "signed_contributions": _signed_contribution_stats(query, field["keys"], field["rho"]),
            "layers": layers, "metrics": metrics,
            "checkpoint_sha256": sha256_file(paths["checkpoint"]),
            "field_sha256": sha256_file(paths["deployment"]),
        }
        atomic_write_json(_row_path(paths["profile_root"] / "rows", item["universe"], item["state_id"], "G100"), row)
        summaries.append(row)
        attempt.progress(status="stage_8a_profile", completed_states=index, total_states=total)
    ratio_payload = {"format": RUNNER_VERSION, "state_count": len(heldout), "layers": {str(layer): torch.cat(values) for layer, values in ratio_values.items()}}
    _atomic_torch_save(ratio_payload, paths["ratio_tensors"])
    _atomic_torch_save({"format": RUNNER_VERSION, "teachers": critical_teachers}, paths["critical_teachers"])
    raw_values = torch.tensor([float(row["raw_field_rms"]) for row in summaries if row["universe"] == "heldout"])
    caps = {
        name: {str(layer): float(torch.quantile(ratio_payload["layers"][str(layer)], float(quantile))) for layer in INSERTION_LAYERS}
        for name, quantile in settings["candidates"]["cap_quantiles"].items()
    }
    median = float(torch.median(raw_values))
    taus = {name: tau_for_median_confidence(median, float(target)) for name, target in settings["candidates"]["median_confidence_targets"].items()}
    p10, p90 = torch.quantile(raw_values, torch.tensor([0.10, 0.90]))
    cv, spread_ratio = float(raw_values.std(unbiased=False) / raw_values.mean()), float(p90 / p10)
    spread = settings["candidates"]["route_d_spread_gate"]
    route_d_viable = cv >= float(spread["minimum_coefficient_of_variation"]) and spread_ratio >= float(spread["minimum_p90_p10_ratio"])
    calibration = {
        "format": "rcmf_benefit_preserving_calibration_lock_9b_v1",
        "heldout_state_count": len(heldout), "outcomes_used": False, "caps": caps, "taus": taus,
        "raw_field_rms": {"minimum": float(raw_values.min()), "median": median, "maximum": float(raw_values.max()), "standard_deviation": float(raw_values.std(unbiased=False)), "coefficient_of_variation": cv, "p90_p10_ratio": spread_ratio},
        "route_d_viable": route_d_viable, "route_d_decision": "PROCEED" if route_d_viable else "STOP ROUTE",
        "ratio_tensor_sha256": sha256_file(paths["ratio_tensors"]),
        "critical_teacher_sha256": sha256_file(paths["critical_teachers"]),
        "locked_before_candidate_outcomes": True,
    }
    calibration["calibration_sha256"] = canonical_sha256(calibration)
    atomic_write_json(paths["calibration"], calibration)
    summary = {
        "format": RUNNER_VERSION, "heldout_state_count": len(heldout), "critical_state_count": len(critical),
        "profile_row_count": len(summaries), "calibration": calibration,
        "mean_correct_shuffle_slot_cosine": statistics.fmean(float(row["slot_cosine_correct_vs_shuffle"]) for row in summaries),
        "maximum_slot_reconstruction_error": max(float(row["slot_reconstruction_max_abs_error"]) for row in summaries),
    }
    atomic_write_json(paths["profile_summary"], summary)
    return summary


def _validate_locked_calibration(settings: Mapping[str, Any], calibration: Mapping[str, Any]) -> None:
    locked = settings["candidates"]["locked_derived_calibration"]
    if str(locked["calibration_sha256"]) != str(calibration["calibration_sha256"]):
        raise ValueError("Committed and atomic calibration hashes differ")
    if bool(locked["outcomes_used"]) or bool(calibration["outcomes_used"]):
        raise ValueError("Calibration lock unexpectedly uses outcomes")
    for candidate_id in ("C50", "C75", "C90"):
        expected = {int(layer): float(value) for layer, value in locked[candidate_id].items()}
        actual = {int(layer): float(value) for layer, value in calibration["caps"][candidate_id].items()}
        if expected != actual:
            raise ValueError(f"Committed and atomic {candidate_id} caps differ")
    for candidate_id in ("Q50", "Q75", "Q90"):
        if float(locked[f"{candidate_id}_tau"]) != float(calibration["taus"][candidate_id]):
            raise ValueError(f"Committed and atomic {candidate_id} tau differ")
    if str(locked["route_d_decision"]) != str(calibration["route_d_decision"]):
        raise ValueError("Committed and atomic Route-D decisions differ")


def _diagnose(runtime: Mapping[str, Any], settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger) -> dict[str, Any]:
    calibration = _json(paths["calibration"])
    _validate_locked_calibration(settings, calibration)
    heldout, critical = _heldout_inputs(runtime), _critical_inputs(runtime, settings, paths)
    critical_teachers = torch.load(paths["critical_teachers"], map_location="cpu", weights_only=False)["teachers"]
    candidates, rows = preregistered_candidates(), []
    total, completed = len(candidates) * (len(heldout) + len(critical)), 0
    for item in (*heldout, *critical):
        field = runtime["fields"][item["universe"]]
        teacher = item.get("teacher") or critical_teachers[item["state_id"]]
        cached_g100 = None
        for candidate in candidates:
            output = _row_path(paths["diagnostic_root"], item["universe"], item["state_id"], candidate.candidate_id)
            if output.exists():
                row = _json(output)
                if candidate.candidate_id == "R0-original":
                    cached_g100 = dict(row)
            elif candidate.candidate_id == "G100" and cached_g100 is not None:
                row = {
                    **cached_g100,
                    "candidate_id": "G100",
                    "route": candidate.route,
                    "reused_from": "R0-original",
                }
                atomic_write_json(output, row)
            else:
                slots, scales, caps, read_audit = _candidate_slots(candidate=candidate, query=item["query"], field=field, calibration=calibration)
                metrics, hooks = _forward(
                    backend=runtime["backend"], reader=runtime["reader"], slots=slots,
                    policy_row=item["policy_row"], ground_truth_row=item["ground_truth_row"], teacher=teacher,
                    layer_scales=scales, layer_caps=caps,
                )
                row = {
                    "format": RUNNER_VERSION, "universe": item["universe"], "state_id": item["state_id"],
                    "task_id": item["task_id"], "group": item["group"], "candidate_id": candidate.candidate_id,
                    "route": candidate.route, "critical_diagnostic_only": candidate.critical_diagnostic_only,
                    "policy_target": item.get("policy_target", "recorded_original_d1_policy"),
                    "metrics": metrics, "read_audit": read_audit, "slot_sha256": _tensor_sha256(slots),
                    "layer_scales": list(scales), "layer_caps": None if caps is None else {str(k): v for k, v in caps.items()},
                    "maximum_residual_ratio": max(hooks.audit.maximum_ratio.values()),
                    "mean_capped_fraction": statistics.fmean(value for values in hooks.audit.capped_fraction.values() for value in values),
                    "checkpoint_sha256": sha256_file(paths["checkpoint"]), "field_sha256": sha256_file(paths["deployment"]),
                    "calibration_sha256": str(calibration["calibration_sha256"]),
                    "runtime_retrieval": False, "runtime_per_memory_scoring": False, "student_prompt_contains_raw_memory": False,
                }
                atomic_write_json(output, row)
                if candidate.candidate_id == "R0-original":
                    cached_g100 = dict(row)
            rows.append(row)
            completed += 1
            attempt.progress(status="stage_8a_diagnose", completed_conditions=completed, total_conditions=total)
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_candidate[str(row["candidate_id"])].append(row)
    metric_names = (
        "target_nll",
        "policy_kl",
        "policy_teacher_token_top1",
        "target_mean_log_probability",
        "exact_target_api_log_probability",
        "action_signature_log_probability",
        "execution_token_validity",
        "argmax_target_token_accuracy",
    )

    def aggregate(metric_rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
        result = {}
        for metric in metric_names:
            selected = [row["metrics"][metric] for row in metric_rows]
            available = [float(value) for value in selected if value is not None]
            result[metric] = statistics.fmean(available) if available else None
        return result

    shuffle_rows = {
        str(row["state_id"]): row
        for row in by_candidate["R0-shuffled"]
        if row["universe"] == "heldout"
    }
    matrix = []
    for candidate in candidates:
        values = by_candidate[candidate.candidate_id]
        heldout_values = [row for row in values if row["universe"] == "heldout"]
        critical_values = [row for row in values if row["universe"] == "critical"]
        task_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in values:
            task_rows[str(row["task_id"])].append(row)
        matrix.append({
            "candidate_id": candidate.candidate_id,
            "route": candidate.route,
            "heldout_state_count": len(heldout_values),
            "critical_state_count": len(critical_values),
            "heldout": aggregate(heldout_values),
            "critical": aggregate(critical_values),
            "heldout_target_nll_margin_vs_key_payload_shuffle": statistics.fmean(
                float(shuffle_rows[str(row["state_id"])]["metrics"]["target_nll"])
                - float(row["metrics"]["target_nll"])
                for row in heldout_values
            ),
            "tasks": {
                task_id: {"state_count": len(task_values), "metrics": aggregate(task_values)}
                for task_id, task_values in sorted(task_rows.items())
            },
            "maximum_residual_ratio": max(float(row["maximum_residual_ratio"]) for row in values),
            "mean_capped_fraction": statistics.fmean(float(row["mean_capped_fraction"]) for row in values),
        })
    summary = {
        "format": RUNNER_VERSION, "candidate_count": len(candidates), "state_count": len(heldout) + len(critical),
        "condition_count": len(rows), "heldout_state_count": len(heldout), "critical_state_count": len(critical),
        "candidate_matrix": matrix, "calibration_sha256": str(calibration["calibration_sha256"]),
        "no_candidate_outcomes_used_to_define_formulas": True,
    }
    atomic_write_json(paths["diagnostic_summary"], summary)
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9b"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    seed_everything(int(settings["global_seed"]))
    paths = _paths(settings, args.artifact_dir)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    data_hashes = {
        "checkpoint": sha256_file(paths["checkpoint"]), "deployment_field": sha256_file(paths["deployment"]),
        "critical_audit": sha256_file(paths["critical_audit"]), "field_tensors": sha256_file(paths["field_tensors"]),
    }
    with AttemptLedger(
        args.artifact_dir, run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase=f"stage_8a_{args.phase}", command=[str(value) for value in sys.argv],
        local_head=args.local_head, github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        started = time.perf_counter()
        runtime = _load_runtime(cfg, settings, paths)
        if args.phase == "equivalence":
            payload = _equivalence(runtime, paths)
            if not payload["passed"]:
                raise RuntimeError(f"G100/bare equivalence failed: {payload}")
            atomic_write_json(paths["equivalence"], payload)
        elif args.phase == "profile":
            payload = _profile(runtime, settings, paths, attempt)
        else:
            if not paths["equivalence"].exists() or not _json(paths["equivalence"])["passed"]:
                raise RuntimeError("Exact equivalence gate has not passed")
            if not paths["calibration"].exists():
                raise RuntimeError("Unlabeled calibration lock is missing")
            payload = _diagnose(runtime, settings, paths, attempt)
        wall = time.perf_counter() - started
        attempt.progress(status="complete", phase=args.phase, wall_seconds=wall)
        print(json.dumps({**payload, "phase": args.phase, "wall_seconds": wall}, sort_keys=True))


if __name__ == "__main__":
    main()
