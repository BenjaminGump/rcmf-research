from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    module_state_sha256,
    project_latents_to_output_ratio_,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
)
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LIVE_GENERATION_RESULT_VERSION,
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_program_fast_7df import (
    build_compiled_one_step_manifest,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _json,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_state_conditioned_program_fast_7df import (
    K_TOKENS,
    LATENT_DIM,
    _load_program_checkpoint,
)


RESULT_FORMAT = "compiled_program_one_step_result_7df_v1"
ONE_STEP_ROOT = "one_step"
PRIMARY_METRICS = (
    "exact_primary_app_api_match",
    "canonical_procedural_signature_match",
    "execution_success",
    "semantic_successor_match",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "smoke", "formal", "analyze"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025df")
    return parser.parse_args()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _attempt_ids(path: Path) -> set[str]:
    return (
        {str(row["attempt_id"]) for row in read_jsonl(path)}
        if path.exists()
        else set()
    )


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_cr = Path(str(settings["parent_exp025cr"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "teacher_forced": artifact_dir / "teacher_forced_summary.json",
        "program_summary": artifact_dir / "program/summary.json",
        "decoder": artifact_dir / "decoder/repaired_rank128_decoder.pt",
        "pairs_a": artifact_dir / "preflight/pairs_A.jsonl",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "selector_conditions": parent_cr / "selector_condition_manifest.json",
        "parent_c0_outputs": parent_b / "condition_outputs",
        "parent_f3_outputs": parent_cr / "selector_condition_outputs",
        "parent_smoke": parent_b / "lifecycle_smoke/smoke_summary.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
    }


def _require_paths(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing compiled one-step inputs: {missing}")


def _f3_rows(path: Path) -> list[dict[str, Any]]:
    manifest = _json(path)
    rows = [
        dict(row)
        for row in manifest["conditions"]
        if str(row.get("condition_name")) == "F3_deployment_e_field_raw"
        and bool(row.get("valid_for_generation"))
    ]
    if len(rows) != 45 or len({str(row["state_example_id"]) for row in rows}) != 45:
        raise ValueError("Frozen EXP-025C-R F3 selections do not contain 45 states")
    return rows


def _load_program_latents(
    *, settings: Mapping[str, Any], artifact_dir: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    state_cache = torch.load(paths["state_cache"], map_location="cpu", weights_only=False)
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    state_values = state_cache["representations"]["final_layer"].to(torch.float32)
    transition_values = transition_cache["representations"]["final_layer"].to(
        torch.float32
    )
    state_position = {
        str(value): index for index, value in enumerate(state_cache["ordered_ids"])
    }
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_cache["ordered_ids"])
    }
    train_transition_ids = [str(row["transition_id"]) for row in _rows(paths["pairs_a"])]
    primary_seeds = [int(settings["program"]["primary_seed"])]
    optional_seed = int(settings["program"]["optional_primary_seed"])
    optional_path = (
        artifact_dir
        / "program/full_factorized_r16_observation_excluded"
        / f"seed_{optional_seed}/best.pt"
    )
    if optional_path.exists():
        primary_seeds.append(optional_seed)
    primary_models = [
        _load_program_checkpoint(
            name="full_factorized_r16_observation_excluded",
            seed=seed,
            settings=settings,
            transition_view_names=transition_cache["view_names"],
            artifact_dir=artifact_dir,
            train_transition_ids=train_transition_ids,
            device=torch.device("cpu"),
        )
        for seed in primary_seeds
    ]
    static = _load_program_checkpoint(
        name="static_only_observation_excluded",
        seed=int(settings["program"]["primary_seed"]),
        settings=settings,
        transition_view_names=transition_cache["view_names"],
        artifact_dir=artifact_dir,
        train_transition_ids=train_transition_ids,
        device=torch.device("cpu"),
    )
    output = []
    with torch.no_grad():
        for condition in manifest["conditions"]:
            state_id = str(condition["state_example_id"])
            transition_id = str(condition["program_transition_id"])
            state = state_values[state_position[state_id]].unsqueeze(0)
            transition = transition_values[transition_position[transition_id]].unsqueeze(0)
            name = str(condition["condition_name"])
            if name == "H1_compiled_full_factorized" or name == "H3_compiled_shuffled_transition":
                z = torch.stack([model(state, transition) for model in primary_models]).mean(0)
            elif name == "H2_compiled_static_only":
                z = static(state, transition)
            elif name == "H4_zero_program":
                z = torch.zeros(1, LATENT_DIM)
            else:
                raise ValueError(f"Unknown compiled condition: {name}")
            output.append(z.squeeze(0).cpu())
    latents = torch.stack(output)
    if not bool(torch.isfinite(latents).all()):
        raise ValueError("Compiled one-step latents contain nonfinite values")
    checkpoint_paths = sorted((artifact_dir / "program").glob("**/best.pt"))
    return {
        "format": "compiled_program_one_step_latents_7df_v1",
        "condition_keys": [str(row["condition_key"]) for row in manifest["conditions"]],
        "latents": latents,
        "primary_seeds": primary_seeds,
        "program_checkpoint_sha256": {
            str(path.relative_to(artifact_dir)): sha256_file(path)
            for path in checkpoint_paths
        },
        "student_prompt_contains_raw_transition": False,
    }


def _preflight(
    *, settings: Mapping[str, Any], replay: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir)
    _require_paths(
        paths,
        (
            "teacher_forced",
            "program_summary",
            "decoder",
            "pairs_a",
            "state_cache",
            "transition_cache",
            "selector",
            "selector_conditions",
            "parent_smoke",
        ),
    )
    teacher_forced = _json(paths["teacher_forced"])
    if not bool(teacher_forced["teacher_forced"]["gate"]["passed"]):
        raise RuntimeError("Teacher-forced gate did not authorize one-step generation")
    if sha256_file(paths["selector"]) != str(settings["expected_selector_ensemble_sha256"]):
        raise ValueError("Frozen selector hash changed")
    f3 = _f3_rows(paths["selector_conditions"])
    manifest = build_compiled_one_step_manifest(f3)
    if int(manifest["state_count"]) != int(settings["one_step"]["audit_states"]):
        raise ValueError("Compiled one-step state count differs")
    root = artifact_dir / ONE_STEP_ROOT
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "condition_manifest.json", manifest)
    latent_payload = _load_program_latents(
        settings=settings, artifact_dir=artifact_dir, manifest=manifest
    )
    atomic_torch_save(latent_payload, root / "program_latents.pt")
    parent_smoke = _json(paths["parent_smoke"])
    if not bool(parent_smoke.get("passed")):
        raise RuntimeError("Parent live-bridge lifecycle smoke is invalid")
    condition_count = int(manifest["condition_count"])
    rates = settings["runtime"]["rates"]
    replay_rates = replay["causal_audit"]["runtime"]
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        h100_seconds = condition_count * float(rates[name]["generation"])
        wall_seconds = condition_count * (
            float(rates[name]["generation"])
            + float(replay_rates["replay_seconds_per_condition"][name])
        )
        scenarios[name] = {
            "h100_hours": h100_seconds / 3600.0,
            "wall_hours": wall_seconds / 3600.0,
        }
    report = {
        "format": "compiled_program_one_step_preflight_7df_v1",
        "state_count": manifest["state_count"],
        "condition_count": condition_count,
        "qwen_generation_count": condition_count,
        "appworld_reconstruction_execution_count": condition_count,
        "runtime": scenarios,
        "review_threshold_h100_hours": float(
            settings["runtime"]["review_threshold_h100_hours"]
        ),
        "automatic_launch_allowed": scenarios["expected"]["h100_hours"]
        <= float(settings["runtime"]["review_threshold_h100_hours"]),
        "projected_artifact_bytes": condition_count * 2_359_296,
        "parent_lifecycle_smoke_reused": True,
        "selector_sha256": sha256_file(paths["selector"]),
        "decoder_sha256": sha256_file(paths["decoder"]),
        "manifest_sha256": manifest["manifest_sha256"],
        "latents_sha256": sha256_file(root / "program_latents.pt"),
        "passed": True,
    }
    atomic_write_json(root / "preflight.json", report)
    return report


def _build_injector(
    *, backend: HFQwenBackend, decoder_path: Path
) -> tuple[AdditiveTokenMemoryInjector, LinearDeltaDecoder, str]:
    payload = torch.load(decoder_path, map_location="cpu", weights_only=False)
    model_dim = int(backend.model.config.hidden_size)
    decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * model_dim).to(backend.device)
    decoder.load_state_dict(payload["decoder_state_dict"])
    decoder.eval()
    injector = AdditiveTokenMemoryInjector(
        program_dim=LATENT_DIM,
        model_dim=model_dim,
        num_tokens=K_TOKENS,
        position="last_user_k",
        initial_scale=1.0,
    ).to(backend.device)
    injector.mlp = decoder.linear
    with torch.no_grad():
        injector.prefix_scale.fill_(1.0)
    injector.eval()
    for parameter in injector.parameters():
        parameter.requires_grad_(False)
    return injector, decoder, module_state_sha256(decoder)


def _project_live_latent(
    *, backend: HFQwenBackend, injector: AdditiveTokenMemoryInjector, decoder: LinearDeltaDecoder,
    messages: Sequence[Mapping[str, str]], z: torch.Tensor
) -> tuple[torch.Tensor, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    input_ids = tokenized.input_ids.to(backend.device)
    attention_mask = tokenized.attention_mask.to(backend.device)
    values = tokenized.metadata.get("last_user_token_indices") or []
    explicit = torch.tensor([values], device=backend.device, dtype=torch.long)
    selected, metadata = injector._select_indices(
        input_ids, attention_mask, None, explicit
    )
    with torch.no_grad():
        embeddings = backend.model.get_input_embeddings()(input_ids)
        valid = selected[0][selected[0] >= 0]
        if int(valid.numel()) != K_TOKENS:
            raise ValueError("Live prompt does not expose four last-user injection tokens")
        base_norm = embeddings[0, valid].to(torch.float32).flatten().norm().view(1)
    projected = z.to(backend.device, dtype=torch.float32).view(1, -1).clone()
    ratio = project_latents_to_output_ratio_(
        projected, decoder, base_norm, max_ratio=1.0
    )
    ratio["selected_token_indices"] = metadata["selected_token_indices"][0]
    ratio["base_norm"] = float(base_norm.item())
    return projected, ratio


def _validate_result(
    row: Mapping[str, Any], *, condition: Mapping[str, Any], manifest_sha256: str,
    decoder_sha256: str, model_name: str
) -> None:
    checks = {
        "format": str(row.get("format")) == RESULT_FORMAT,
        "complete": str(row.get("status")) == "complete",
        "condition": str(row.get("condition_key")) == str(condition["condition_key"]),
        "manifest": str(row.get("condition_manifest_sha256")) == manifest_sha256,
        "decoder": str(row.get("decoder_state_sha256")) == decoder_sha256,
        "model": str(row.get("model_name")) == model_name,
        "raw_absent": not bool(row.get("student_prompt_contains_raw_transition")),
        "same_world": bool(row.get("live_worker", {}).get("same_world_execution")),
    }
    if not all(checks.values()):
        raise ValueError(f"Compiled one-step checkpoint differs: {checks}")


def _run_condition(
    *, condition: Mapping[str, Any], z: torch.Tensor, output_path: Path,
    stderr_path: Path, ordinal: int, attempt_id: str, settings: Mapping[str, Any],
    replay: Mapping[str, Any], manifest: Mapping[str, Any], example: Any, record: Any,
    backend: HFQwenBackend, injector: AdditiveTokenMemoryInjector,
    decoder: LinearDeltaDecoder, decoder_sha256: str, semantic_path: Path,
    bridge_script: Path
) -> tuple[dict[str, Any], bool]:
    model_name = str(replay["causal_audit"]["generation"]["model_name"])
    if output_path.exists():
        row = _json(output_path)
        _validate_result(
            row,
            condition=condition,
            manifest_sha256=str(manifest["manifest_sha256"]),
            decoder_sha256=decoder_sha256,
            model_name=model_name,
        )
        return row, True
    started = time.perf_counter()
    contract = _state_contract(example, record)
    runtime_settings = {
        "legacy": replay["legacy"],
        "replay": replay["replay"],
    }
    prepare = _prepare_message(
        condition=condition,
        contract=contract,
        settings=runtime_settings,
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
        generation_settings = replay["causal_audit"]["generation"]
        messages = build_live_appworld_messages(
            example,
            list(ready["actual_observations"]),
            prompt_profile=str(generation_settings["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        prompt_tokens = len(
            backend.tokenizer(rendered, add_special_tokens=True, truncation=False)[
                "input_ids"
            ]
        )
        remaining = int(generation_settings["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Compiled live prompt is over context: {condition['condition_key']}")
        projected, ratio = _project_live_latent(
            backend=backend,
            injector=injector,
            decoder=decoder,
            messages=messages,
            z=z,
        )
        generation_started = time.perf_counter()
        output = backend.generate(
            messages=list(messages),
            max_new_tokens=min(int(generation_settings["max_new_tokens"]), remaining),
            temperature=0.0,
            top_p=1.0,
            injector=injector,
            memory_z=projected,
        )
        generation_seconds = time.perf_counter() - generation_started
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
        **{key: condition[key] for key in condition if key != "format"},
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
            "target_semantic_comparison": executed["target_semantic_comparison"],
        },
        "student_prompt_contains_raw_transition": False,
        "injection_position": "last_user_k",
        "injection_k": K_TOKENS,
        "delta_ratio": ratio,
        "decoder_state_sha256": decoder_sha256,
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "model_name": model_name,
        "prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": int(output.usage["completion_tokens"]),
        "generation_elapsed_seconds": generation_seconds,
        "condition_elapsed_seconds": time.perf_counter() - started,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, row)
    _validate_result(
        row,
        condition=condition,
        manifest_sha256=str(manifest["manifest_sha256"]),
        decoder_sha256=decoder_sha256,
        model_name=model_name,
    )
    return row, False


def _program_phase(
    *, phase: str, settings: Mapping[str, Any], replay: Mapping[str, Any],
    artifact_dir: Path, attempt: AttemptLedger, attempt_id: str
) -> dict[str, Any]:
    root = artifact_dir / ONE_STEP_ROOT
    preflight = _json(root / "preflight.json")
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Compiled one-step work exceeds the H100 review threshold")
    manifest = _json(root / "condition_manifest.json")
    latent_payload = torch.load(
        root / "program_latents.pt", map_location="cpu", weights_only=False
    )
    if latent_payload["condition_keys"] != [
        str(row["condition_key"]) for row in manifest["conditions"]
    ]:
        raise ValueError("Compiled latent ordering differs from the condition manifest")
    conditions = list(manifest["conditions"])
    if phase == "smoke":
        by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conditions:
            by_state[str(row["state_example_id"])].append(row)
        selected_states = sorted(by_state)[:2]
        conditions = [
            row
            for state_id in selected_states
            for row in by_state[state_id]
            if str(row["condition_name"])
            in {"H1_compiled_full_factorized", "H4_zero_program"}
        ]
    elif phase != "formal":
        raise ValueError(f"Unknown generation phase: {phase}")
    paths = _paths(settings, artifact_dir)
    _require_paths(
        paths,
        ("decoder", "decisions", "memories", "semantic_module", "bridge_script"),
    )
    generation = replay["causal_audit"]["generation"]
    backend = HFQwenBackend(
        model_name=str(generation["model_name"]),
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=False,
        load_model=True,
    )
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Frozen-Qwen contract failed")
    injector, decoder, decoder_sha256 = _build_injector(
        backend=backend, decoder_path=paths["decoder"]
    )
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    position = {
        key: index for index, key in enumerate(latent_payload["condition_keys"])
    }
    output_dir = root / ("lifecycle_smoke/condition_outputs" if phase == "smoke" else "condition_outputs")
    completed = []
    resumed = 0
    started = time.perf_counter()
    for ordinal, condition in enumerate(conditions, start=1):
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            z=latent_payload["latents"][position[key]],
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=root / f"worker_logs/{phase}/{key}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            settings=settings,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            injector=injector,
            decoder=decoder,
            decoder_sha256=decoder_sha256,
            semantic_path=paths["semantic_module"],
            bridge_script=paths["bridge_script"],
        )
        completed.append(row)
        resumed += int(reused)
        attempt.progress(
            status=f"compiled_one_step_{phase}",
            completed_conditions=len(completed),
            total_conditions=len(conditions),
            latest_validated_checkpoint=str(output_dir / condition_checkpoint_name(key)),
        )
        print(
            f"compiled one-step {phase} {len(completed)}/{len(conditions)} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": f"compiled_program_one_step_{phase}_summary_7df_v1",
        "phase": phase,
        "condition_count": len(completed),
        "unique_condition_count": len({row["condition_key"] for row in completed}),
        "resumed_condition_count": resumed,
        "qwen_generation_seconds": sum(
            float(row["generation_elapsed_seconds"]) for row in completed
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(
            bool(row["live_worker"]["same_world_execution"]) for row in completed
        ),
        "exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "passed": len(completed) == len(conditions)
        and len({row["condition_key"] for row in completed}) == len(conditions)
        and all(row["live_worker"]["same_world_execution"] for row in completed),
    }
    summary_path = root / ("lifecycle_smoke/smoke_summary.json" if phase == "smoke" else "generation_summary.json")
    atomic_write_json(summary_path, summary)
    return summary


def _load_parent_rows(path: Path, condition_name: str) -> list[dict[str, Any]]:
    rows = []
    for output in sorted(path.glob("*.json")):
        row = _json(output)
        if str(row.get("condition_name")) == condition_name:
            rows.append(row)
    if len(rows) != 45:
        raise ValueError(f"Expected 45 parent {condition_name} rows, found {len(rows)}")
    return rows


def _positive_task_count(rows: Sequence[Mapping[str, Any]]) -> int:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row["state_task_id"])][str(row["condition_name"])].append(row)
    positive = 0
    for values in grouped.values():
        h1 = values.get("H1_compiled_full_factorized", [])
        c0 = values.get("C0_bare", [])
        if not h1 or not c0:
            continue
        by_h1 = {str(row["state_example_id"]): row for row in h1}
        by_c0 = {str(row["state_example_id"]): row for row in c0}
        shared = sorted(set(by_h1) & set(by_c0))
        signature = statistics.fmean(
            float(by_h1[key]["metrics"]["canonical_procedural_signature_match"])
            - float(by_c0[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(by_h1[key]["metrics"]["semantic_successor_match"])
            - float(by_c0[key]["metrics"]["semantic_successor_match"])
            for key in shared
        )
        positive += int(signature > 0.0 or successor > 0.0)
    return positive


def _analyze(
    *, settings: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Any]:
    root = artifact_dir / ONE_STEP_ROOT
    generation = _json(root / "generation_summary.json")
    if not bool(generation["passed"]):
        raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
    paths = _paths(settings, artifact_dir)
    h_rows = [_json(path) for path in sorted((root / "condition_outputs").glob("*.json"))]
    if len(h_rows) != 180:
        raise ValueError(f"Expected 180 compiled rows, found {len(h_rows)}")
    c0 = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    f3 = _load_parent_rows(paths["parent_f3_outputs"], "F3_deployment_e_field_raw")
    combined = h_rows + c0 + f3
    primary = [row for row in combined if str(row["audit_stratum"]) in {"A", "B"}]
    samples = int(settings["bootstrap_samples"])
    comparisons = {}
    pairs = (
        ("H1_compiled_full_factorized", "C0_bare"),
        ("H1_compiled_full_factorized", "F3_deployment_e_field_raw"),
        ("H1_compiled_full_factorized", "H2_compiled_static_only"),
        ("H1_compiled_full_factorized", "H3_compiled_shuffled_transition"),
        ("H1_compiled_full_factorized", "H4_zero_program"),
    )
    for index, (left, right) in enumerate(pairs):
        comparisons[f"{left}_minus_{right}"] = comparison_set(
            primary,
            left=left,
            right=right,
            bootstrap_samples=samples,
            seed=25097 + index * 100,
        )
    h1_c0 = comparisons["H1_compiled_full_factorized_minus_C0_bare"]
    h1_h2 = comparisons[
        "H1_compiled_full_factorized_minus_H2_compiled_static_only"
    ]
    h1_h3 = comparisons[
        "H1_compiled_full_factorized_minus_H3_compiled_shuffled_transition"
    ]
    f3_c0 = comparison_set(
        primary,
        left="F3_deployment_e_field_raw",
        right="C0_bare",
        bootstrap_samples=samples,
        seed=25597,
    )
    retention = {}
    for metric in ("canonical_procedural_signature_match", "semantic_successor_match"):
        denominator = float(f3_c0[metric]["difference"])
        numerator = float(h1_c0[metric]["difference"])
        retention[metric] = None if abs(denominator) <= 1.0e-12 else numerator / denominator
    positive_tasks = _positive_task_count(primary)
    checks = {
        "improves_signature_or_successor": any(
            float(h1_c0[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "retains_40_percent_one_metric": any(
            value is not None and value >= 0.40 for value in retention.values()
        ),
        "beats_static": any(
            float(h1_h2[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "beats_shuffled": any(
            float(h1_h3[name]["difference"]) > 0.0
            for name in (
                "canonical_procedural_signature_match",
                "semantic_successor_match",
            )
        ),
        "execution_drop_lte_0_05": float(
            h1_c0["execution_success"]["difference"]
        )
        >= -0.05,
        "positive_majority_tasks": positive_tasks >= 5,
    }
    passed = all(checks.values())
    decision = (
        "compiled_transition_program_fast_pilot_passed"
        if passed
        else "compiled_program_not_behaviorally_retained"
    )
    summary = {
        "format": "compiled_program_one_step_analysis_7df_v1",
        "condition_metrics_all": condition_summary(combined),
        "condition_metrics_primary": condition_summary(primary),
        "per_task": per_task_summary(combined),
        "comparisons_primary": comparisons,
        "f3_raw_minus_c0": f3_c0,
        "oracle_gain_retention": retention,
        "positive_task_count": positive_tasks,
        "gate": {"checks": checks, "passed": passed},
        "decision_branch": decision,
        "compiled_program_works": passed,
        "field_program_training_remains_blocked": True,
        "actual_qwen_h100_hours": float(generation["qwen_generation_seconds"])
        / 3600.0,
        "actual_wall_hours": float(generation["elapsed_seconds"]) / 3600.0,
    }
    atomic_write_json(root / "analysis.json", summary)
    atomic_write_text(
        root / "one_step_report.md",
        "\n".join(
            [
                "# EXP-025D-Fast compiled one-step audit",
                "",
                f"- conditions: `{len(h_rows)}`",
                f"- primary states: `{len({row['state_example_id'] for row in primary})}`",
                f"- positive tasks: `{positive_tasks}/9`",
                f"- decision: `{decision}`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7df"]
    replay = replay_cfg.raw["stage_c_7b"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    paths = _paths(settings, args.artifact_dir)
    data_paths = {
        "config": args.config,
        "replay_config": args.replay_config,
        "selector": paths["selector"],
        "teacher_forced": paths["teacher_forced"],
    }
    data_hashes = {
        name: sha256_file(path) for name, path in data_paths.items() if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"compiled_program_one_step_{args.phase}",
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
                settings=settings, replay=replay, artifact_dir=args.artifact_dir
            )
        elif args.phase in {"smoke", "formal"}:
            if args.phase == "formal":
                smoke = _json(args.artifact_dir / ONE_STEP_ROOT / "lifecycle_smoke/smoke_summary.json")
                if not bool(smoke["passed"]):
                    raise RuntimeError("Compiled lifecycle smoke did not pass")
            result = _program_phase(
                phase=args.phase,
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(settings=settings, artifact_dir=args.artifact_dir)
        checkpoint_names = {
            "preflight": "preflight.json",
            "smoke": "lifecycle_smoke/smoke_summary.json",
            "formal": "generation_summary.json",
            "analyze": "analysis.json",
        }
        checkpoint = args.artifact_dir / ONE_STEP_ROOT / checkpoint_names[args.phase]
        attempt.progress(
            status=f"compiled_program_one_step_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoint),
            result_passed=bool(result.get("passed", result.get("gate", {}).get("passed", True))),
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

