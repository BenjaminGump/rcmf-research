from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.injection.base import build_position_ids
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.deep_residual_amortization_7f import (
    GLOBAL_SEED,
    K_TOKENS,
    LAYER_INDICES,
    build_amortized_one_step_manifest,
    classify_one_step_behavior,
    differentiable_layer_ratio_projection,
)
from rcmf.training.deep_residual_carrier_7e import capture_original_layer_states
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.state_conditioned_program_direct_7dg import (
    _load_representations,
    seed_everything,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_deep_residual_carrier_7e import _generate_residual
from scripts.run_deep_residual_compiler_7f import _build_decoder, _build_model
from scripts.run_direct_injection_channel_7dh import _build_backend_from_generation
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_state_conditioned_program_fast_one_step_7df import (
    _f3_rows,
    _load_parent_rows,
)


RESULT_FORMAT = "deep_residual_amortized_one_step_result_7f_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_deep_residual_amortization_7f.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--model", choices=("pairmlp", "factorized"), required=True)
    parser.add_argument("--phase", choices=("preflight", "formal", "analyze"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027a_one_step")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path, kind: str) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "compiler" / kind / "one_step"
    return {
        "root": root,
        "training_summary": artifact_dir / "compiler" / kind / "training_summary.json",
        "final_evaluation": artifact_dir
        / "compiler"
        / kind
        / "final_evaluation_summary.json",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "parent_smoke": parent_b / "lifecycle_smoke/smoke_summary.json",
        "primary_manifest": Path(str(settings["parent_exp026b"])) / "pair_manifest.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "condition_manifest": root / "condition_manifest.json",
        "deltas": root / "program_deltas.pt",
        "preflight": root / "preflight.json",
        "generation": root / "generation_summary.json",
        "analysis": root / "analysis.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-027A one-step inputs: {missing}")


def _attempt_elapsed_seconds(path: Path) -> float:
    starts: dict[str, dt.datetime] = {}
    total = 0.0
    if not path.exists():
        return total
    for row in read_jsonl(path):
        attempt_id = str(row.get("attempt_id", ""))
        timestamp = row.get("timestamp_utc")
        if not timestamp:
            continue
        moment = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if row.get("event") == "start":
            starts[attempt_id] = moment
        elif row.get("event") == "end" and attempt_id in starts:
            total += max(0.0, (moment - starts.pop(attempt_id)).total_seconds())
    return total


def _compile_deltas(
    *,
    kind: str,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    representations = _load_representations(
        {"state_cache": paths["state_cache"], "transition_cache": paths["transition_cache"]},
        torch.device("cpu"),
    )
    training = _json(paths["training_summary"])
    checkpoint = Path(str(training["selected_checkpoint"]))
    if sha256_file(checkpoint) != str(training["selected_checkpoint_sha256"]):
        raise ValueError("Selected compiler checkpoint hash changed")
    model = _build_model(
        kind=kind,
        settings=settings,
        view_names=representations["transition_view_names"],
        device=torch.device("cpu"),
    )
    decoder = _build_decoder(settings, 4096, torch.device("cpu"))
    checkpoint_payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint_payload["model_state_dict"])
    decoder.load_state_dict(checkpoint_payload["decoder_state_dict"])
    model.eval()
    decoder.eval()
    state_positions = representations["state_position"]
    transition_positions = representations["transition_position"]
    values = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["program_state_example_id"])
            transition_id = str(condition["program_transition_id"])
            state = representations["state_values"][state_positions[state_id]].unsqueeze(0)
            transition = representations["transition_values"][
                transition_positions[transition_id]
            ].unsqueeze(0)
            name = str(condition["condition_name"])
            if name in {"P0_zero_program", "H4_zero_program"}:
                z = torch.zeros(1, int(settings["compiler"]["program_dim"]))
            elif kind == "factorized" and name == "H2_factorized_static_only":
                z = model.components(state, transition)["static"]
            else:
                z = model(state, transition)
            values.append(decoder(z).squeeze(0).cpu())
    tensor = torch.stack(values)
    payload = {
        "format": "deep_residual_amortized_one_step_deltas_7f_v1",
        "global_seed": GLOBAL_SEED,
        "model_kind": kind,
        "condition_keys": [str(row["condition_key"]) for row in manifest["conditions"]],
        "deltas": tensor,
        "runtime_projection_required": True,
        "selected_checkpoint": str(checkpoint),
        "selected_checkpoint_sha256": sha256_file(checkpoint),
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "selected_layers": list(LAYER_INDICES),
        "selected_token_count": K_TOKENS,
        "student_prompt_contains_raw_transition": False,
    }
    atomic_torch_save(payload, paths["deltas"])
    return payload


def _preflight(
    *,
    kind: str,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir, kind)
    _require(
        paths,
        (
            "training_summary",
            "final_evaluation",
            "state_cache",
            "transition_cache",
            "selector",
            "selector_conditions",
            "parent_c0_outputs",
            "parent_f3_outputs",
            "parent_smoke",
            "primary_manifest",
            "decisions",
            "memories",
            "semantic_module",
            "bridge_script",
        ),
    )
    if sha256_file(paths["selector"]) != str(settings["expected_selector_sha256"]):
        raise ValueError("Frozen selector hash changed")
    if not bool(_json(paths["parent_smoke"]).get("passed")):
        raise RuntimeError("Validated EXP-025B live-bridge smoke is unavailable")
    f3 = _f3_rows(paths["selector_conditions"])
    manifest = build_amortized_one_step_manifest(f3, model_kind=kind, seed=GLOBAL_SEED)
    paths["root"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["condition_manifest"], manifest)
    deltas = _compile_deltas(
        kind=kind, settings=settings, paths=paths, manifest=manifest
    )
    expected_generation_hours = (
        int(manifest["condition_count"])
        * (
            float(settings["runtime"]["one_step_generation_seconds_expected"])
            + float(settings["runtime"]["pair_evaluation_seconds_expected"])
        )
        / 3600.0
    )
    actual_before = _attempt_elapsed_seconds(artifact_dir / "attempts.jsonl") / 3600.0
    projected_total = actual_before + expected_generation_hours
    report = {
        "format": "deep_residual_amortized_one_step_preflight_7f_v1",
        "global_seed": GLOBAL_SEED,
        "model_kind": kind,
        "state_count": int(manifest["state_count"]),
        "condition_count": int(manifest["condition_count"]),
        "qwen_generation_count": int(manifest["condition_count"]),
        "appworld_reconstruction_execution_count": int(manifest["condition_count"]),
        "reused_c0_count": 45,
        "reused_f3_count": 45,
        "actual_h100_hours_before_one_step": actual_before,
        "expected_one_step_h100_hours": expected_generation_hours,
        "projected_total_h100_hours": projected_total,
        "review_threshold_h100_hours": float(
            settings["runtime"]["review_threshold_h100_hours"]
        ),
        "automatic_launch_allowed": projected_total
        <= float(settings["runtime"]["review_threshold_h100_hours"]),
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "program_deltas_sha256": sha256_file(paths["deltas"]),
        "selected_checkpoint_sha256": str(deltas["selected_checkpoint_sha256"]),
        "runtime_projection_required": True,
        "parent_lifecycle_smoke_reused": True,
        "student_prompt_contains_raw_transition": False,
        "passed": bool(torch.isfinite(deltas["deltas"]).all()),
    }
    atomic_write_json(paths["preflight"], report)
    return report


def _run_condition(
    *,
    condition: Mapping[str, Any],
    delta: Tensor,
    checkpoint_sha256: str,
    deltas_sha256: str,
    output_path: Path,
    stderr_path: Path,
    ordinal: int,
    attempt_id: str,
    replay: Mapping[str, Any],
    manifest: Mapping[str, Any],
    example: Any,
    record: Any,
    backend: Any,
    semantic_path: Path,
    bridge_script: Path,
) -> tuple[dict[str, Any], bool]:
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "condition": str(row.get("condition_key")) == str(condition["condition_key"]),
            "manifest": str(row.get("condition_manifest_sha256"))
            == str(manifest["manifest_sha256"]),
            "checkpoint": str(row.get("compiler_checkpoint_sha256"))
            == checkpoint_sha256,
            "deltas": str(row.get("program_deltas_sha256")) == deltas_sha256,
            "complete": str(row.get("status")) == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing amortized one-step row differs: {checks}")
        return row, True
    started = time.perf_counter()
    contract = _state_contract(example, record)
    prepare = _prepare_message(
        condition=condition,
        contract=contract,
        settings={"legacy": replay["legacy"], "replay": replay["replay"]},
        semantic_path=semantic_path,
        bridge_attempt=f"{attempt_id}-{ordinal:04d}-{time.time_ns()}",
    )
    client = LiveBridgeClient(
        executable=Path(str(replay["legacy"]["executable"])),
        bridge_script=bridge_script,
        appworld_root=Path(str(replay["legacy"]["appworld_root"])),
        stderr_path=stderr_path,
        timeout_seconds=float(replay["replay"]["subprocess_timeout_seconds"]),
    )
    try:
        ready = client.prepare(prepare)
        generation = replay["causal_audit"]["generation"]
        messages = build_live_appworld_messages(
            example,
            list(ready["actual_observations"]),
            prompt_profile=str(generation["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        prompt_tokens = int(tokenized.attention_mask.sum().item())
        remaining = int(generation["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Compiled prompt is over context: {condition['condition_key']}")
        user_indices = [
            int(value) for value in tokenized.metadata["last_user_token_indices"]
        ]
        if len(user_indices) < K_TOKENS:
            raise RuntimeError("Live compiled prompt has fewer than four user tokens")
        selected_indices = torch.tensor(
            [user_indices[-K_TOKENS:]], device=backend.device, dtype=torch.long
        )
        original_states = capture_original_layer_states(
            model=backend.model,
            input_ids=tokenized.input_ids,
            attention_mask=tokenized.attention_mask.to(torch.long),
            selected_token_indices=selected_indices,
            layer_indices=LAYER_INDICES,
            position_ids=build_position_ids(tokenized.attention_mask.to(torch.long)),
        ).to(backend.device)
        projected, projection = differentiable_layer_ratio_projection(
            delta.to(backend.device).unsqueeze(0),
            original_states,
            maximum_ratio=1.0,
        )
        generation_started = time.perf_counter()
        output, hook = _generate_residual(
            backend=backend,
            messages=messages,
            delta=projected.squeeze(0),
            layer_indices=LAYER_INDICES,
            max_new_tokens=min(int(generation["max_new_tokens"]), remaining),
        )
        generation_seconds = time.perf_counter() - generation_started
        if max(float(value) for value in hook["layer_ratios"]) > 1.0001:
            raise RuntimeError("Live compiled residual exceeded the locked layer-ratio budget")
        code, fixed_response = extract_code_and_fix_content(output.text)
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
        output.text,
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
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        **{key: value for key, value in condition.items() if key != "format"},
        "raw_model_response": output.text,
        "fixed_model_response": fixed_response,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "metrics": metrics,
        "target_action_sha256": contract["target_action_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "live_worker": {
            "same_world_execution": bool(executed["same_world_execution"]),
            "same_python_namespace": bool(executed["same_python_namespace"]),
            "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
            "execution_exception": executed["execution_exception"],
            "state_before": executed["state_before"],
            "state_after": executed["state_after"],
        },
        "injection_location": "decoder_block_input_residual",
        "selected_layer_indices": list(LAYER_INDICES),
        "selected_token_indices": hook["selected_token_indices"][0],
        "layer_ratios": hook["layer_ratios"],
        "global_ratio": hook["global_ratio"],
        "runtime_projection_raw_layer_ratio": projection["raw_layer_ratio"][0]
        .detach()
        .cpu()
        .tolist(),
        "hook_audit": hook,
        "compiler_checkpoint_sha256": checkpoint_sha256,
        "program_deltas_sha256": deltas_sha256,
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "model_name": str(replay["causal_audit"]["generation"]["model_name"]),
        "prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": int(output.usage["completion_tokens"]),
        "generation_elapsed_seconds": generation_seconds,
        "condition_elapsed_seconds": time.perf_counter() - started,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, row)
    return row, False


def _formal(
    *,
    kind: str,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir, kind)
    preflight = _json(paths["preflight"])
    if not bool(preflight["passed"] and preflight["automatic_launch_allowed"]):
        raise RuntimeError("EXP-027A one-step preflight did not authorize generation")
    manifest = _json(paths["condition_manifest"])
    deltas = torch.load(paths["deltas"], map_location="cpu", weights_only=False)
    keys = [str(row["condition_key"]) for row in manifest["conditions"]]
    if keys != list(deltas["condition_keys"]):
        raise ValueError("Amortized condition and delta order differs")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("One-step audit loaded trainable Qwen parameters")
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    output_dir = paths["root"] / "condition_outputs"
    started = time.perf_counter()
    generation_seconds = 0.0
    resumed = 0
    completed = []
    delta_sha = sha256_file(paths["deltas"])
    checkpoint_sha = str(deltas["selected_checkpoint_sha256"])
    for ordinal, condition in enumerate(manifest["conditions"], start=1):
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            delta=deltas["deltas"][ordinal - 1],
            checkpoint_sha256=checkpoint_sha,
            deltas_sha256=delta_sha,
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=paths["root"] / f"worker_logs/{key}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            semantic_path=paths["semantic_module"],
            bridge_script=paths["bridge_script"],
        )
        completed.append(row)
        resumed += int(reused)
        generation_seconds += 0.0 if reused else float(row["generation_elapsed_seconds"])
        attempt.progress(
            status=f"deep_residual_{kind}_one_step",
            completed_conditions=len(completed),
            total_conditions=len(manifest["conditions"]),
            latest_validated_checkpoint=str(output_dir / condition_checkpoint_name(key)),
        )
        print(
            f"amortized one-step {kind} {len(completed)}/{len(manifest['conditions'])} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": "deep_residual_amortized_one_step_generation_7f_v1",
        "global_seed": GLOBAL_SEED,
        "model_kind": kind,
        "condition_count": len(completed),
        "unique_condition_count": len({str(row["condition_key"]) for row in completed}),
        "resumed_condition_count": resumed,
        "new_condition_count": len(completed) - resumed,
        "qwen_generation_seconds": generation_seconds,
        "qwen_generation_h100_hours": generation_seconds / 3600.0,
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(row["live_worker"]["same_world_execution"] for row in completed),
        "same_namespace_count": sum(
            row["live_worker"]["same_python_namespace"] for row in completed
        ),
        "exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "maximum_live_layer_ratio": max(
            max(float(value) for value in row["layer_ratios"]) for row in completed
        ),
        "passed": len(completed) == 180
        and len({str(row["condition_key"]) for row in completed}) == 180
        and all(row["live_worker"]["same_world_execution"] for row in completed)
        and all(row["live_worker"]["same_python_namespace"] for row in completed),
    }
    atomic_write_json(paths["generation"], summary)
    return summary


def _positive_tasks(
    rows: Sequence[Mapping[str, Any]], *, correct_name: str
) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_task_id"])].append(row)
    details = {}
    for task_id, values in sorted(grouped.items()):
        correct = {
            str(row["state_example_id"]): row
            for row in values
            if str(row["condition_name"]) == correct_name
        }
        bare = {
            str(row["state_example_id"]): row
            for row in values
            if str(row["condition_name"]) == "C0_bare"
        }
        shared = sorted(set(correct) & set(bare))
        signature = statistics.fmean(
            float(correct[key]["metrics"]["canonical_procedural_signature_match"])
            - float(bare[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(correct[key]["metrics"]["semantic_successor_match"])
            - float(bare[key]["metrics"]["semantic_successor_match"])
            for key in shared
        )
        details[task_id] = {
            "paired_state_count": len(shared),
            "action_signature_difference": signature,
            "semantic_successor_difference": successor,
            "positive_relative_behavior": signature > 0.0 or successor > 0.0,
        }
    return sum(value["positive_relative_behavior"] for value in details.values()), details


def _analyze(
    *, kind: str, settings: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir, kind)
    generation = _json(paths["generation"])
    if not bool(generation["passed"]):
        raise RuntimeError("Deep residual amortized one-step infrastructure is invalid")
    outputs = [
        _json(path)
        for path in sorted((paths["root"] / "condition_outputs").glob("*.json"))
    ]
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    combined = outputs + c0 + f3
    primary_ids = {
        str(row["state_example_id"]) for row in _json(paths["primary_manifest"])["pairs"]
    }
    primary = [row for row in combined if str(row["state_example_id"]) in primary_ids]
    if len(outputs) != 180 or len(primary_ids) != 32:
        raise ValueError("EXP-027A one-step row or primary-state count differs")
    if kind == "pairmlp":
        correct, transition_shuffle, state_shuffle = (
            "P1_pairmlp_correct",
            "P2_pairmlp_transition_shuffle",
            "P3_pairmlp_state_shuffle",
        )
    else:
        correct, transition_shuffle, state_shuffle = (
            "H1_factorized_correct",
            "H3_factorized_transition_shuffle",
            "H2_factorized_static_only",
        )
    samples = int(settings["bootstrap_samples"])
    comparisons = {
        "correct_minus_c0": comparison_set(
            primary,
            left=correct,
            right="C0_bare",
            bootstrap_samples=samples,
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "correct_minus_f3": comparison_set(
            primary,
            left=correct,
            right="F3_deployment_e_field_raw",
            bootstrap_samples=samples,
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "correct_minus_transition_shuffle": comparison_set(
            primary,
            left=correct,
            right=transition_shuffle,
            bootstrap_samples=samples,
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "correct_minus_state_or_static": comparison_set(
            primary,
            left=correct,
            right=state_shuffle,
            bootstrap_samples=samples,
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "f3_minus_c0": comparison_set(
            primary,
            left="F3_deployment_e_field_raw",
            right="C0_bare",
            bootstrap_samples=samples,
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
    }
    c0_gap = comparisons["correct_minus_c0"]
    transition_gap = comparisons["correct_minus_transition_shuffle"]
    state_gap = comparisons["correct_minus_state_or_static"]
    positive_count, task_details = _positive_tasks(primary, correct_name=correct)
    classification = classify_one_step_behavior(
        p1_minus_c0={
            "action_signature": float(
                c0_gap["canonical_procedural_signature_match"]["difference"]
            ),
            "semantic_successor": float(
                c0_gap["semantic_successor_match"]["difference"]
            ),
        },
        p1_minus_p2={
            "action_signature": float(
                transition_gap["canonical_procedural_signature_match"]["difference"]
            ),
            "semantic_successor": float(
                transition_gap["semantic_successor_match"]["difference"]
            ),
        },
        p1_minus_p3={
            "action_signature": float(
                state_gap["canonical_procedural_signature_match"]["difference"]
            ),
            "semantic_successor": float(
                state_gap["semantic_successor_match"]["difference"]
            ),
        },
        execution_drop=-float(c0_gap["execution_success"]["difference"]),
        positive_task_count=positive_count,
    )
    retention = {}
    for metric in (
        "canonical_procedural_signature_match",
        "semantic_successor_match",
    ):
        denominator = float(comparisons["f3_minus_c0"][metric]["difference"])
        numerator = float(c0_gap[metric]["difference"])
        retention[metric] = None if abs(denominator) <= 1.0e-12 else numerator / denominator
    if kind == "pairmlp":
        branch = {
            "STRONG_POSITIVE": "pairmlp_deep_residual_strong_positive",
            "PARTIAL_POSITIVE": "deep_residual_amortization_partial",
            "CLEAR_FAILURE": "deep_residual_amortization_failed",
        }[classification["classification"]]
    else:
        branch = (
            "deep_residual_factorized_program_validated"
            if classification["classification"] == "STRONG_POSITIVE"
            else "deep_residual_field_factorization_bottleneck"
        )
    summary = {
        "format": "deep_residual_amortized_one_step_analysis_7f_v1",
        "global_seed": GLOBAL_SEED,
        "model_kind": kind,
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "raw_gain_retention": retention,
        "positive_task_count": positive_count,
        "positive_task_details": task_details,
        "classification": classification,
        "decision_branch": branch,
        "generation": generation,
    }
    atomic_write_json(paths["analysis"], summary)
    atomic_write_text(
        paths["root"] / "one_step_report.md",
        "\n".join(
            [
                f"# EXP-027A {kind} deep-residual one-step audit",
                "",
                f"- classification: `{classification['classification']}`",
                f"- branch: `{branch}`",
                f"- primary states: `{len(primary_ids)}`",
                f"- positive tasks: `{positive_count}/9`",
                f"- raw-gain retention: `{retention}`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7f"]
    replay = replay_cfg.raw["stage_c_7b"]
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir, args.model)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "selector": paths["selector"],
            "training_summary": paths["training_summary"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"deep_residual_{args.model}_one_step_{args.phase}",
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
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                kind=args.model,
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "formal":
            result = _formal(
                kind=args.model,
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                kind=args.model, settings=settings, artifact_dir=args.artifact_dir
            )
        checkpoint = {
            "preflight": paths["preflight"],
            "formal": paths["generation"],
            "analyze": paths["analysis"],
        }[args.phase]
        attempt.progress(
            status=f"deep_residual_{args.model}_one_step_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoint),
            result_passed=bool(result.get("passed", True)),
        )


if __name__ == "__main__":
    main()
