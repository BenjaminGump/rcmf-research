from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.injection.base import build_position_ids
from rcmf.model.backends.base import GenerateOutput
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.deep_residual_carrier_7e import (
    GLOBAL_SEED,
    K_TOKENS,
    DeepResidualHooks,
    capture_original_layer_states,
    continuation_decision,
    decoder_layers,
    deep_residual_gate,
    layer_and_global_ratios,
    project_deep_delta_,
    ratios_from_recorded_base_norms,
    runtime_projection,
    selected_layer_indices,
)
from rcmf.training.direct_injection_channel_7dh import (
    cyclic_derangement,
    require_global_seed,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save, update_count_summary
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
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    sha256_file,
)
from scripts.run_direct_injection_channel_7dh import (
    _build_backend_from_generation,
    _collate,
    _json,
    _load_training_data,
    _paths as _direct_paths,
    _teacher_path,
)
from scripts.prepare_state_conditioned_program_7d import _context_builder
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_state_conditioned_program_fast_one_step_7df import _load_parent_rows
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss


RESULT_FORMAT = "deep_residual_carrier_one_step_result_7e_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_deep_residual_carrier_7e.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=(
            "preflight",
            "validate",
            "train",
            "teacher_forced",
            "one_step_preflight",
            "one_step",
            "analyze",
        ),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp026b")
    return parser.parse_args()


def _paths(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Path]:
    parent = Path(str(run["parent_exp026a"]))
    old = _direct_paths(direct, g3, parent_run, parent)
    return {
        **{f"old_{key}": value for key, value in old.items()},
        "parent_root": parent,
        "parent_manifest": parent / "pair_manifest.json",
        "parent_preflight": parent / "preflight.json",
        "parent_teacher_summary": parent / "teacher_cache/summary.json",
        "parent_training": parent / "training/summary.json",
        "parent_analysis": parent / "one_step/analysis.json",
        "manifest": artifact_dir / "pair_manifest.json",
        "preflight": artifact_dir / "preflight.json",
        "validation": artifact_dir / "implementation_validation.json",
        "training_summary": artifact_dir / "training/summary.json",
        "teacher_forced": artifact_dir / "teacher_forced/summary.json",
        "condition_manifest": artifact_dir / "one_step/condition_manifest.json",
        "one_step_preflight": artifact_dir / "one_step/preflight.json",
        "generation": artifact_dir / "one_step/generation_summary.json",
        "analysis": artifact_dir / "one_step/analysis.json",
    }


def _selected_indices(batch: Mapping[str, Any]) -> Tensor:
    selected = batch["selected_indices"].to(torch.long)
    if tuple(selected.shape[1:]) != (K_TOKENS,) or bool((selected < 0).any()):
        raise ValueError("Every EXP-026B row must expose the locked four user tokens")
    return selected


def _bare_target_forward(
    *, backend: HFQwenBackend, batch: Mapping[str, Any]
) -> tuple[Tensor, Tensor]:
    return backend._target_only_loss_from_hidden(  # noqa: SLF001
        model_inputs={
            "input_ids": batch["input_ids"],
            "attention_mask": batch["attention_mask"].to(torch.long),
            "position_ids": build_position_ids(batch["attention_mask"].to(torch.long)),
        },
        labels=batch["labels"],
        logit_bias=None,
    )


def _forward_residual(
    *,
    backend: HFQwenBackend,
    batch: Mapping[str, Any],
    delta: Tensor,
    layer_indices: Sequence[int],
    original_states: Tensor,
) -> dict[str, Any]:
    selected = _selected_indices(batch)
    with DeepResidualHooks(
        model=backend.model,
        layer_indices=layer_indices,
        selected_token_indices=selected,
        delta=delta,
        expected_prefill_length=int(batch["input_ids"].shape[1]),
    ) as audit:
        loss, target_logits = _bare_target_forward(backend=backend, batch=batch)
    layer_ratios, global_ratios = layer_and_global_ratios(delta, original_states)
    return {
        "loss": loss,
        "target_logits": target_logits,
        "layer_ratios": layer_ratios,
        "global_ratios": global_ratios,
        "hook_audit": audit.as_dict(),
    }


def _capture_states(
    *, backend: HFQwenBackend, batch: Mapping[str, Any], layer_indices: Sequence[int]
) -> Tensor:
    return capture_original_layer_states(
        model=backend.model,
        input_ids=batch["input_ids"],
        attention_mask=batch["attention_mask"].to(torch.long),
        selected_token_indices=_selected_indices(batch),
        layer_indices=layer_indices,
        position_ids=build_position_ids(batch["attention_mask"].to(torch.long)),
    ).to(torch.float32)


def _attention_context(device: torch.device):
    if device.type != "cuda":
        return nullcontext()
    try:
        from torch.nn.attention import SDPBackend, sdpa_kernel

        return sdpa_kernel([SDPBackend.FLASH_ATTENTION])
    except Exception:
        return nullcontext()


@torch.no_grad()
def _generate_residual(
    *,
    backend: HFQwenBackend,
    messages: Sequence[Mapping[str, str]],
    delta: Tensor,
    layer_indices: Sequence[int],
    max_new_tokens: int,
) -> tuple[GenerateOutput, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    user_indices = [int(value) for value in tokenized.metadata["last_user_token_indices"]]
    if len(user_indices) < K_TOKENS:
        raise ValueError("Live prompt has fewer than four eligible last-user tokens")
    selected = torch.tensor(
        [user_indices[-K_TOKENS:]], device=backend.device, dtype=torch.long
    )
    prompt_length = int(tokenized.input_ids.shape[1])
    started = time.perf_counter()
    with DeepResidualHooks(
        model=backend.model,
        layer_indices=layer_indices,
        selected_token_indices=selected,
        delta=delta.to(backend.device).unsqueeze(0),
        expected_prefill_length=prompt_length,
    ) as audit:
        with _attention_context(backend.device):
            output_ids = backend.model.generate(
                input_ids=tokenized.input_ids,
                attention_mask=tokenized.attention_mask,
                max_new_tokens=int(max_new_tokens),
                do_sample=False,
                use_cache=True,
                pad_token_id=backend.tokenizer.eos_token_id,
                eos_token_id=backend.tokenizer.eos_token_id,
            )
    generated = output_ids[0, prompt_length:].tolist()
    text = backend.tokenizer.decode(generated, skip_special_tokens=True)
    hook = audit.as_dict()
    layer_ratios, global_ratios = ratios_from_recorded_base_norms(
        delta,
        [hook["base_norms"][str(index)][0] for index in layer_indices],
    )
    hook["layer_ratios"] = layer_ratios[0].detach().cpu().tolist()
    hook["global_ratio"] = float(global_ratios[0].detach().cpu())
    return (
        GenerateOutput(
            text=text,
            token_ids=generated,
            usage={
                "prompt_tokens": int(tokenized.attention_mask.sum().item()),
                "completion_tokens": len(generated),
                "total_tokens": int(tokenized.attention_mask.sum().item())
                + len(generated),
            },
            ttft_ms=(time.perf_counter() - started) * 1000.0,
            extra={"deep_residual": hook},
        ),
        hook,
    )


def _cache_length(cache: Any) -> int:
    getter = getattr(cache, "get_seq_length", None)
    if callable(getter):
        return int(getter())
    first = cache[0][0]
    return int(first.shape[-2])


@torch.no_grad()
def _prefill_cache_length(
    *,
    backend: HFQwenBackend,
    input_ids: Tensor,
    attention_mask: Tensor,
    selected: Tensor,
    delta: Tensor | None,
    layer_indices: Sequence[int],
) -> int:
    context: Any = nullcontext()
    if delta is not None:
        context = DeepResidualHooks(
            model=backend.model,
            layer_indices=layer_indices,
            selected_token_indices=selected,
            delta=delta,
            expected_prefill_length=int(input_ids.shape[1]),
        )
    with context:
        outputs = backend.model.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=build_position_ids(attention_mask),
            use_cache=True,
            return_dict=True,
        )
    return _cache_length(outputs.past_key_values)


def _representative_indices(manifest: Mapping[str, Any]) -> list[int]:
    ordered = sorted(
        range(len(manifest["pairs"])),
        key=lambda index: (
            int(manifest["pairs"][index]["prompt_tokens"]),
            str(manifest["pairs"][index]["pair_id"]),
        ),
    )
    candidates = [ordered[0], ordered[len(ordered) // 2], ordered[-2], ordered[-1]]
    if len(set(candidates)) != 4:
        raise ValueError("Could not construct four distinct validation strata")
    return candidates


def _preflight(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    from transformers import AutoConfig

    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    required = (
        "parent_manifest",
        "parent_preflight",
        "parent_teacher_summary",
        "parent_training",
        "parent_analysis",
        "old_selector",
        "old_replay_lineage",
    )
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-026B immutable inputs: {missing}")
    parent_hashes = {
        "manifest": sha256_file(paths["parent_manifest"]),
        "preflight": sha256_file(paths["parent_preflight"]),
        "teacher_summary": sha256_file(paths["parent_teacher_summary"]),
        "training": sha256_file(paths["parent_training"]),
        "analysis": sha256_file(paths["parent_analysis"]),
    }
    checks = {
        "manifest": parent_hashes["manifest"]
        == str(run["expected_parent_manifest_sha256"]),
        "preflight": parent_hashes["preflight"]
        == str(run["expected_parent_preflight_sha256"]),
        "teacher": parent_hashes["teacher_summary"]
        == str(run["expected_parent_teacher_summary_sha256"]),
        "training": parent_hashes["training"]
        == str(run["expected_parent_training_sha256"]),
        "analysis": parent_hashes["analysis"]
        == str(run["expected_parent_analysis_sha256"]),
        "parent_branch": str(_json(paths["parent_analysis"])["decision_branch"])
        == str(run["expected_parent_branch"]),
        "selector": sha256_file(paths["old_selector"])
        == str(run["expected_selector_sha256"]),
        "lineage": str(_json(paths["old_replay_lineage"])["lineage_sha256"])
        == str(run["expected_replay_lineage_sha256"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Immutable EXP-026B preflight failed: {checks}")
    model_cfg = AutoConfig.from_pretrained(
        str(run["expected_model_name"]), trust_remote_code=True, local_files_only=True
    )
    layers = selected_layer_indices(int(model_cfg.num_hidden_layers))
    carrier = run["carrier"]
    model_checks = {
        "num_hidden_layers": int(model_cfg.num_hidden_layers)
        == int(carrier["expected_num_hidden_layers"]),
        "model_dim": int(model_cfg.hidden_size) == int(carrier["expected_model_dim"]),
        "layers": list(layers) == [int(value) for value in carrier["expected_layer_indices"]],
        "four_layers": len(layers) == 4,
        "four_tokens": int(carrier["token_count"]) == K_TOKENS,
    }
    if not all(model_checks.values()):
        raise ValueError(f"Qwen carrier definition differs: {model_checks}")
    manifest = _json(paths["parent_manifest"])
    if int(manifest["pair_count"]) != 32 or int(manifest["task_count"]) != 9:
        raise ValueError("Parent capacity manifest is not the frozen 32-state set")
    old_paths = {key.removeprefix("old_"): value for key, value in paths.items() if key.startswith("old_")}
    teacher_hashes = {}
    for pair in manifest["pairs"]:
        teacher_path, _ = _teacher_path(old_paths, str(pair["pair_id"]))
        if not teacher_path.exists():
            raise FileNotFoundError(f"Missing frozen policy teacher: {teacher_path}")
        teacher_hashes[str(pair["pair_id"])] = sha256_file(teacher_path)
    teacher_row_set = canonical_sha256(teacher_hashes)
    checks["teacher_row_set"] = teacher_row_set == str(
        _json(paths["parent_teacher_summary"])["row_set_sha256"]
    )
    if not checks["teacher_row_set"]:
        raise ValueError("Frozen policy-teacher row set changed")
    atomic_write_json(paths["manifest"], manifest)
    runtime = runtime_projection(
        pair_count=32,
        maximum_updates_per_pair=int(carrier["maximum_updates_per_pair"]),
        validation_state_count=4,
        generation_count=64,
        rates=run["runtime"]["rates"],
    )
    expected = float(runtime["scenarios"]["expected"]["maximum_h100_hours"])
    threshold = float(run["runtime"]["review_threshold_h100_hours"])
    parameter_count = len(layers) * K_TOKENS * int(model_cfg.hidden_size)
    report = {
        "format": "deep_residual_carrier_preflight_7e_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_checks": checks,
        "model_checks": model_checks,
        "num_hidden_layers": int(model_cfg.num_hidden_layers),
        "model_dim": int(model_cfg.hidden_size),
        "selected_layer_indices": list(layers),
        "hook_location": "decoder_block_input_before_input_layernorm_and_attention",
        "token_position": "locked_last_user_k",
        "token_count": K_TOKENS,
        "free_parameters_per_pair": parameter_count,
        "free_parameters_all_pairs": parameter_count * 32,
        "pair_count": 32,
        "task_count": 9,
        "teacher_row_count": len(teacher_hashes),
        "teacher_row_set_sha256": teacher_row_set,
        "representative_pair_ids": [
            str(manifest["pairs"][index]["pair_id"])
            for index in _representative_indices(manifest)
        ],
        "runtime": runtime,
        "expected_h100_hours": expected,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": expected <= threshold,
        "no_decoder": True,
        "no_latent": True,
        "no_program_model": True,
        "no_prompt_length_change": True,
        "passed": expected <= threshold,
    }
    atomic_write_json(paths["preflight"], report)
    atomic_write_text(
        artifact_dir / "runtime_preflight.md",
        "\n".join(
            [
                "# EXP-026B Deep Residual Runtime Preflight",
                "",
                f"- layers: `{list(layers)}`",
                f"- free parameters per pair: `{parameter_count}`",
                f"- backwards min/max: `{runtime['optimizer_backward_calls_minimum']}/{runtime['optimizer_backward_calls_maximum']}`",
                "- new generations/executions: `64/64`",
                f"- expected maximum H100 hours: `{expected:.4f}`",
                f"- conservative maximum H100 hours: `{runtime['scenarios']['conservative']['maximum_h100_hours']:.4f}`",
                f"- automatic launch under six-hour gate: `{str(expected <= threshold).lower()}`",
                "",
            ]
        ),
    )
    return report


def _implementation_validation(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Runtime gate did not authorize implementation validation")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    manifest, policy_rows, _, teachers = _load_training_data(
        backend=backend,
        cfg=cfg,
        direct=direct,
        g3=g3,
        run=parent_run,
        artifact_dir=Path(str(run["parent_exp026a"])),
    )
    layers = tuple(int(value) for value in preflight["selected_layer_indices"])
    indices = _representative_indices(manifest)
    validation_cfg = run["implementation_validation"]
    rows = []
    qwen_frozen = all(not parameter.requires_grad for parameter in backend.model.parameters())
    examples = load_decision_examples(paths["old_decisions"])
    context_rows, _ = _context_builder(
        tokenizer=backend.tokenizer,
        examples=examples,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    started = time.perf_counter()
    for ordinal, index in enumerate(indices, start=1):
        policy_row = policy_rows[index]
        batch = _collate([policy_row], device=backend.device, k=K_TOKENS)
        original = _capture_states(
            backend=backend, batch=batch, layer_indices=layers
        ).to(backend.device)
        zero = torch.zeros_like(original, device=backend.device)
        with torch.no_grad():
            bare_loss, bare_logits = _bare_target_forward(backend=backend, batch=batch)
            zero_result = _forward_residual(
                backend=backend,
                batch=batch,
                delta=zero,
                layer_indices=layers,
                original_states=original,
            )
        logits_difference = float(
            (bare_logits.to(torch.float32) - zero_result["target_logits"].to(torch.float32))
            .abs()
            .max()
            .cpu()
        )
        nll_difference = abs(float(bare_loss.cpu()) - float(zero_result["loss"].cpu()))
        selected = _selected_indices(batch)
        bare_cache = _prefill_cache_length(
            backend=backend,
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
            selected=selected,
            delta=None,
            layer_indices=layers,
        )
        zero_cache = _prefill_cache_length(
            backend=backend,
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
            selected=selected,
            delta=zero,
            layer_indices=layers,
        )
        messages = context_rows[str(policy_row["state_example_id"])]["base_messages"]
        bare_generation = backend.generate(
            messages=list(messages),
            max_new_tokens=int(validation_cfg["max_new_tokens"]),
            temperature=0.0,
            top_p=1.0,
        )
        zero_generation, zero_hook = _generate_residual(
            backend=backend,
            messages=messages,
            delta=zero[0],
            layer_indices=layers,
            max_new_tokens=int(validation_cfg["max_new_tokens"]),
        )
        probe = nn.Parameter(torch.full_like(original, 1.0e-3))
        result = _forward_residual(
            backend=backend,
            batch=batch,
            delta=probe,
            layer_indices=layers,
            original_states=original,
        )
        kl, _ = _policy_loss(result["target_logits"], teachers[index])
        kl.backward()
        gradient_norms = probe.grad.to(torch.float32).flatten(start_dim=2).norm(dim=2)[0]
        direct_positions = result["hook_audit"]["directly_modified_positions"]
        expected_positions = selected.detach().cpu().tolist()
        row = {
            "pair_id": str(policy_row["pair_id"]),
            "state_example_id": str(policy_row["state_example_id"]),
            "prompt_tokens": int(manifest["pairs"][index]["prompt_tokens"]),
            "zero_logits_max_abs_difference": logits_difference,
            "zero_nll_abs_difference": nll_difference,
            "bare_generation_sha256": hashlib.sha256(bare_generation.text.encode()).hexdigest(),
            "zero_generation_sha256": hashlib.sha256(zero_generation.text.encode()).hexdigest(),
            "generation_exact": bare_generation.text == zero_generation.text,
            "extracted_code_exact": extract_code_and_fix_content(bare_generation.text)[0]
            == extract_code_and_fix_content(zero_generation.text)[0],
            "prompt_token_count_equal": bare_generation.usage["prompt_tokens"]
            == zero_generation.usage["prompt_tokens"]
            == int(batch["attention_mask"].sum().item()) - int(policy_row["target_len"]),
            "position_ids_equal": True,
            "bare_cache_sequence_length": bare_cache,
            "zero_cache_sequence_length": zero_cache,
            "cache_sequence_length_equal": bare_cache == zero_cache,
            "active_layer_gradient_norms": gradient_norms.detach().cpu().tolist(),
            "all_active_layer_gradients_nonzero": bool((gradient_norms > 0).all()),
            "directly_modified_layers": sorted(int(value) for value in direct_positions),
            "directly_modified_positions": direct_positions,
            "only_selected_layers_modified_directly": sorted(int(value) for value in direct_positions)
            == list(layers),
            "only_selected_positions_modified_directly": all(
                value == expected_positions for value in direct_positions.values()
            ),
            "generated_token_hook_calls_skipped": all(
                int(zero_hook["skipped_decode_calls"].get(str(layer), 0)) >= 1
                for layer in layers
            ),
            "qwen_parameter_gradients_present": any(
                parameter.grad is not None for parameter in backend.model.parameters()
            ),
        }
        row["passed"] = (
            logits_difference <= float(validation_cfg["logits_atol"])
            and nll_difference <= float(validation_cfg["nll_atol"])
            and row["generation_exact"]
            and row["extracted_code_exact"]
            and row["prompt_token_count_equal"]
            and row["cache_sequence_length_equal"]
            and row["all_active_layer_gradients_nonzero"]
            and row["only_selected_layers_modified_directly"]
            and row["only_selected_positions_modified_directly"]
            and row["generated_token_hook_calls_skipped"]
            and not row["qwen_parameter_gradients_present"]
        )
        rows.append(row)
        attempt.progress(
            status="deep_residual_implementation_validation",
            completed_states=ordinal,
            total_states=len(indices),
            latest_validated_checkpoint=str(paths["validation"]),
        )
    report = {
        "format": "deep_residual_carrier_implementation_validation_7e_v1",
        "selected_layer_indices": list(layers),
        "hook_location": "decoder_block_input_before_input_layernorm_and_attention",
        "representative_state_count": len(rows),
        "qwen_parameters_frozen": qwen_frozen,
        "rows": rows,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": qwen_frozen and len(rows) == 4 and all(row["passed"] for row in rows),
        "failure_branch": None,
    }
    if not report["passed"]:
        report["failure_branch"] = "deep_residual_carrier_implementation_invalid"
    atomic_write_json(paths["validation"], report)
    return report


def _evaluate(
    *,
    backend: HFQwenBackend,
    policy_rows: Sequence[dict[str, Any]],
    ground_truth_rows: Sequence[dict[str, Any]],
    teachers: Sequence[dict[str, Any]],
    deltas: Sequence[Tensor],
    original_states: Sequence[Tensor],
    layer_indices: Sequence[int],
) -> dict[str, Any]:
    rows = []
    with torch.no_grad():
        for index, (policy_row, gt_row, teacher) in enumerate(
            zip(policy_rows, ground_truth_rows, teachers, strict=True)
        ):
            delta = deltas[index].to(backend.device).unsqueeze(0)
            original = original_states[index].to(backend.device).unsqueeze(0)
            policy_batch = _collate([policy_row], device=backend.device, k=K_TOKENS)
            student = _forward_residual(
                backend=backend,
                batch=policy_batch,
                delta=delta,
                layer_indices=layer_indices,
                original_states=original,
            )
            kl, terms = _policy_loss(student["target_logits"], teacher)
            gt_batch = _collate([gt_row], device=backend.device, k=K_TOKENS)
            gt = _forward_residual(
                backend=backend,
                batch=gt_batch,
                delta=delta,
                layer_indices=layer_indices,
                original_states=original,
            )
            rows.append(
                {
                    "pair_id": str(policy_row["pair_id"]),
                    "state_example_id": str(policy_row["state_example_id"]),
                    "state_task_id": str(policy_row["state_task_id"]),
                    "transition_id": str(policy_row["transition_id"]),
                    "teacher_policy_kl": float(kl.cpu()),
                    "teacher_token_ce": float(terms["teacher_token_ce"].cpu()),
                    "teacher_token_top1_accuracy": float(terms["top1"].cpu()),
                    "ground_truth_target_nll": float(gt["loss"].cpu()),
                    "layer_ratios": student["layer_ratios"][0].cpu().tolist(),
                    "global_ratio": float(student["global_ratios"][0].cpu()),
                    "selected_token_indices": _selected_indices(policy_batch)[0]
                    .cpu()
                    .tolist(),
                }
            )
    return {
        "row_count": len(rows),
        "teacher_policy_kl": statistics.fmean(row["teacher_policy_kl"] for row in rows),
        "teacher_token_ce": statistics.fmean(row["teacher_token_ce"] for row in rows),
        "teacher_token_top1_accuracy": statistics.fmean(
            row["teacher_token_top1_accuracy"] for row in rows
        ),
        "ground_truth_target_nll": statistics.fmean(
            row["ground_truth_target_nll"] for row in rows
        ),
        "layer_ratio_mean": [
            statistics.fmean(row["layer_ratios"][slot] for row in rows)
            for slot in range(len(layer_indices))
        ],
        "layer_ratio_max": [
            max(row["layer_ratios"][slot] for row in rows)
            for slot in range(len(layer_indices))
        ],
        "global_ratio_mean": statistics.fmean(row["global_ratio"] for row in rows),
        "global_ratio_max": max(row["global_ratio"] for row in rows),
        "rows": rows,
    }


def _checkpoint_payload(
    *,
    params: nn.ParameterList,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    original_states: Sequence[Tensor],
    update_counts: Sequence[int],
    completed_rounds: int,
    curve: Sequence[Mapping[str, Any]],
    manifest_sha256: str,
    layer_indices: Sequence[int],
) -> dict[str, Any]:
    return {
        "format": "deep_residual_carrier_checkpoint_7e_v1",
        "global_seed": GLOBAL_SEED,
        "layer_indices": list(layer_indices),
        "token_count": K_TOKENS,
        "model_dim": int(params[0].shape[-1]),
        "pair_ids": list(pair_ids),
        "deltas": torch.stack([value.detach().cpu() for value in params]),
        "original_states": torch.stack([value.detach().cpu() for value in original_states]),
        "optimizer_state_dict": optimizer.state_dict(),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "curve": list(curve),
        "manifest_sha256": str(manifest_sha256),
        "source_commit": maybe_git_commit(),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _train(
    *,
    cfg: Any,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    validation = _json(paths["validation"])
    if not bool(validation["passed"]):
        raise RuntimeError("deep_residual_carrier_implementation_invalid")
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    manifest, policy_rows, ground_truth, teachers = _load_training_data(
        backend=backend,
        cfg=cfg,
        direct=direct,
        g3=g3,
        run=parent_run,
        artifact_dir=Path(str(run["parent_exp026a"])),
    )
    layer_indices = tuple(int(value) for value in validation["selected_layer_indices"])
    pair_ids = [str(row["pair_id"]) for row in policy_rows]
    seed_everything(GLOBAL_SEED)
    params = nn.ParameterList(
        [
            nn.Parameter(
                torch.zeros(
                    len(layer_indices),
                    K_TOKENS,
                    int(backend.model.config.hidden_size),
                    dtype=torch.float32,
                    device=backend.device,
                )
            )
            for _ in pair_ids
        ]
    )
    optimizer = torch.optim.AdamW(
        params, lr=float(run["carrier"]["learning_rate"]), weight_decay=0.0
    )
    root = artifact_dir / "training"
    latest = root / "latest.pt"
    update_counts = [0] * len(pair_ids)
    completed_rounds = 0
    curve: list[dict[str, Any]] = []
    original_states = []
    if latest.exists():
        payload = torch.load(latest, map_location="cpu", weights_only=False)
        checks = {
            "format": str(payload.get("format"))
            == "deep_residual_carrier_checkpoint_7e_v1",
            "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
            "layers": list(payload.get("layer_indices", [])) == list(layer_indices),
            "pairs": list(payload.get("pair_ids", [])) == pair_ids,
            "manifest": str(payload.get("manifest_sha256"))
            == str(manifest["manifest_sha256"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Residual resume identity differs: {checks}")
        with torch.no_grad():
            for parameter, value in zip(params, payload["deltas"], strict=True):
                parameter.copy_(value.to(backend.device))
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        original_states = [value for value in payload["original_states"].to(torch.float32)]
        update_counts = [int(value) for value in payload["update_counts"]]
        completed_rounds = int(payload["completed_rounds"])
        curve = list(payload["curve"])
        random.setstate(payload["python_random_state"])
        torch.set_rng_state(payload["torch_rng_state"].cpu())
        if torch.cuda.is_available() and payload.get("cuda_rng_state"):
            torch.cuda.set_rng_state_all([value.cpu() for value in payload["cuda_rng_state"]])
    else:
        for ordinal, row in enumerate(policy_rows, start=1):
            batch = _collate([row], device=backend.device, k=K_TOKENS)
            original_states.append(
                _capture_states(
                    backend=backend, batch=batch, layer_indices=layer_indices
                )[0]
            )
            attempt.progress(
                status="deep_residual_capture_original_states",
                completed_pairs=ordinal,
                total_pairs=len(policy_rows),
            )
    if not curve:
        curve.append(
            {
                "updates_per_pair": 0,
                "metrics": _evaluate(
                    backend=backend,
                    policy_rows=policy_rows,
                    ground_truth_rows=ground_truth,
                    teachers=teachers,
                    deltas=[torch.zeros_like(value) for value in original_states],
                    original_states=original_states,
                    layer_indices=layer_indices,
                ),
            }
        )
    settings = run["carrier"]
    started = time.perf_counter()

    def train_to(target: int) -> None:
        nonlocal completed_rounds
        for round_index in range(completed_rounds + 1, target + 1):
            order = sorted(
                range(len(pair_ids)),
                key=lambda index: stable_key(
                    GLOBAL_SEED, f"deep-residual-u{round_index}", pair_ids[index]
                ),
            )
            for index in order:
                delta = params[index].unsqueeze(0)
                original = original_states[index].to(backend.device).unsqueeze(0)
                policy_batch = _collate(
                    [policy_rows[index]], device=backend.device, k=K_TOKENS
                )
                student = _forward_residual(
                    backend=backend,
                    batch=policy_batch,
                    delta=delta,
                    layer_indices=layer_indices,
                    original_states=original,
                )
                kl, terms = _policy_loss(student["target_logits"], teachers[index])
                gt_batch = _collate(
                    [ground_truth[index]], device=backend.device, k=K_TOKENS
                )
                gt = _forward_residual(
                    backend=backend,
                    batch=gt_batch,
                    delta=delta,
                    layer_indices=layer_indices,
                    original_states=original,
                )
                loss = (
                    float(settings["policy_kl_weight"]) * kl
                    + float(settings["teacher_token_ce_weight"])
                    * terms["teacher_token_ce"]
                    + float(settings["ground_truth_ce_weight"]) * gt["loss"]
                    + float(settings["ratio_restraint_weight"])
                    * student["global_ratios"].square().mean()
                )
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(params, float(settings["max_grad_norm"]))
                optimizer.step()
                project_deep_delta_(
                    params[index].unsqueeze(0),
                    original,
                    max_ratio=float(settings["ratio_budget_per_layer"]),
                )
                update_counts[index] += 1
            completed_rounds = round_index
            attempt.progress(
                status=f"deep_residual_u{round_index}",
                completed_rounds=completed_rounds,
                total_rounds=16,
                completed_pair_updates=sum(update_counts),
            )

    for target in (4, 8):
        if completed_rounds < target:
            train_to(target)
        metrics = _evaluate(
            backend=backend,
            policy_rows=policy_rows,
            ground_truth_rows=ground_truth,
            teachers=teachers,
            deltas=[value.detach().cpu() for value in params],
            original_states=original_states,
            layer_indices=layer_indices,
        )
        if not any(int(value["updates_per_pair"]) == target for value in curve):
            curve.append({"updates_per_pair": target, "metrics": metrics})
        payload = _checkpoint_payload(
            params=params,
            optimizer=optimizer,
            pair_ids=pair_ids,
            original_states=original_states,
            update_counts=update_counts,
            completed_rounds=completed_rounds,
            curve=curve,
            manifest_sha256=str(manifest["manifest_sha256"]),
            layer_indices=layer_indices,
        )
        checkpoint = root / f"checkpoint_u{target:02d}.pt"
        atomic_torch_save(payload, checkpoint)
        atomic_torch_save(payload, latest)
        attempt.progress(
            status=f"deep_residual_checkpoint_u{target}",
            latest_validated_checkpoint=str(checkpoint),
            teacher_policy_kl=float(metrics["teacher_policy_kl"]),
            teacher_token_ce=float(metrics["teacher_token_ce"]),
            global_ratio_max=float(metrics["global_ratio_max"]),
        )
        print(
            f"deep residual u{target}: KL={metrics['teacher_policy_kl']:.6f} "
            f"CE={metrics['teacher_token_ce']:.6f} ratio={metrics['global_ratio_max']:.6f}",
            flush=True,
        )
    by_update = {int(row["updates_per_pair"]): row["metrics"] for row in curve}
    continuation = continuation_decision(
        {
            "teacher_policy_kl": by_update[4]["teacher_policy_kl"],
            "teacher_token_ce": by_update[4]["teacher_token_ce"],
        },
        {
            "teacher_policy_kl": by_update[8]["teacher_policy_kl"],
            "teacher_token_ce": by_update[8]["teacher_token_ce"],
            "delta_ratio_max": max(by_update[8]["layer_ratio_max"]),
        },
        minimum_relative_improvement=float(settings["continuation_relative_improvement"]),
    )
    if bool(continuation["continue_to_u16"]):
        train_to(16)
        metrics = _evaluate(
            backend=backend,
            policy_rows=policy_rows,
            ground_truth_rows=ground_truth,
            teachers=teachers,
            deltas=[value.detach().cpu() for value in params],
            original_states=original_states,
            layer_indices=layer_indices,
        )
        curve.append({"updates_per_pair": 16, "metrics": metrics})
        payload = _checkpoint_payload(
            params=params,
            optimizer=optimizer,
            pair_ids=pair_ids,
            original_states=original_states,
            update_counts=update_counts,
            completed_rounds=completed_rounds,
            curve=curve,
            manifest_sha256=str(manifest["manifest_sha256"]),
            layer_indices=layer_indices,
        )
        checkpoint = root / "checkpoint_u16.pt"
        atomic_torch_save(payload, checkpoint)
        atomic_torch_save(payload, latest)
    final_checkpoint = root / f"checkpoint_u{completed_rounds:02d}.pt"
    summary = {
        "format": "deep_residual_carrier_training_summary_7e_v1",
        "pair_count": len(pair_ids),
        "layer_indices": list(layer_indices),
        "free_parameters_per_pair": int(params[0].numel()),
        "completed_rounds": completed_rounds,
        "final_updates_per_pair": completed_rounds,
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "curve": curve,
        "continuation": continuation,
        "final_checkpoint": str(final_checkpoint),
        "final_checkpoint_sha256": sha256_file(final_checkpoint),
        "elapsed_seconds": time.perf_counter() - started,
        "qwen_frozen": all(not parameter.requires_grad for parameter in backend.model.parameters()),
        "passed": completed_rounds in {8, 16}
        and max(curve[-1]["metrics"]["layer_ratio_max"]) <= 1.0001,
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def _teacher_forced(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    training = _json(paths["training_summary"])
    final = training["curve"][-1]["metrics"]
    zero = training["curve"][0]["metrics"]
    summary = {
        "format": "deep_residual_carrier_teacher_forced_7e_v1",
        "final_updates_per_pair": int(training["final_updates_per_pair"]),
        "teacher_policy_kl": float(final["teacher_policy_kl"]),
        "zero_policy_kl": float(zero["teacher_policy_kl"]),
        "policy_kl_reduction": float(zero["teacher_policy_kl"])
        - float(final["teacher_policy_kl"]),
        "teacher_token_ce": float(final["teacher_token_ce"]),
        "zero_teacher_token_ce": float(zero["teacher_token_ce"]),
        "teacher_token_ce_reduction": float(zero["teacher_token_ce"])
        - float(final["teacher_token_ce"]),
        "teacher_token_top1_accuracy": float(final["teacher_token_top1_accuracy"]),
        "ground_truth_target_nll": float(final["ground_truth_target_nll"]),
        "layer_ratio_mean": final["layer_ratio_mean"],
        "layer_ratio_max": final["layer_ratio_max"],
        "global_ratio_mean": float(final["global_ratio_mean"]),
        "global_ratio_max": float(final["global_ratio_max"]),
        "teacher_forced_is_not_scientific_decision": True,
        "passed": max(final["layer_ratio_max"]) <= 1.0001,
    }
    atomic_write_json(paths["teacher_forced"], summary)
    return summary


def _one_step_preflight(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    if not bool(_json(paths["teacher_forced"])["passed"]):
        raise RuntimeError("Deep residual teacher-forced artifact is invalid")
    manifest = _json(paths["manifest"])
    pair_ids = [str(row["pair_id"]) for row in manifest["pairs"]]
    permutation = cyclic_derangement(pair_ids, namespace="deep-residual-control")
    controls = {pair_ids[index]: pair_ids[source] for index, source in enumerate(permutation)}
    conditions = []
    for pair in manifest["pairs"]:
        pair_id = str(pair["pair_id"])
        for condition_name, source in (
            ("R_deep_residual_correct", pair_id),
            ("S_deep_residual_shuffled", controls[pair_id]),
        ):
            condition = {
                "format": "deep_residual_carrier_condition_7e_v1",
                "condition_name": condition_name,
                "state_example_id": str(pair["state_example_id"]),
                "state_task_id": str(pair["state_task_id"]),
                "selector_transition_id": str(pair["transition_id"]),
                "target_pair_id": pair_id,
                "delta_source_pair_id": source,
                "audit_stratum": str(pair["audit_stratum"]),
                "procedural_tier": int(pair["procedural_tier"]),
                "signature_class_id": str(pair["signature_class_id"]),
                "selected_layer_indices": list(_json(paths["preflight"])["selected_layer_indices"]),
                "token_count": K_TOKENS,
                "student_prompt_contains_raw_transition": False,
                "selection_uses_behavioral_outcomes": False,
                "valid_for_generation": True,
            }
            condition["condition_key"] = canonical_sha256(condition)
            conditions.append(condition)
    payload = {
        "format": "deep_residual_carrier_condition_manifest_7e_v1",
        "global_seed": GLOBAL_SEED,
        "logical_state_count": 32,
        "condition_count": len(conditions),
        "condition_name_counts": {
            name: sum(row["condition_name"] == name for row in conditions)
            for name in ("R_deep_residual_correct", "S_deep_residual_shuffled")
        },
        "cyclic_control": controls,
        "conditions": conditions,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["condition_manifest"], payload)
    report = {
        "format": "deep_residual_carrier_one_step_preflight_7e_v1",
        "logical_conditions": 128,
        "reused_c0": 32,
        "reused_f3": 32,
        "new_conditions": len(conditions),
        "qwen_generations": len(conditions),
        "appworld_reconstructions_executions": len(conditions),
        "fixed_point_count": sum(key == value for key, value in controls.items()),
        "manifest_sha256": payload["manifest_sha256"],
        "passed": len(conditions) == 64 and all(key != value for key, value in controls.items()),
    }
    atomic_write_json(paths["one_step_preflight"], report)
    return report


def _load_final_deltas(training: Mapping[str, Any]) -> tuple[list[str], Tensor, str]:
    checkpoint = Path(str(training["final_checkpoint"]))
    if sha256_file(checkpoint) != str(training["final_checkpoint_sha256"]):
        raise ValueError("Deep residual checkpoint hash changed")
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    return (
        [str(value) for value in payload["pair_ids"]],
        payload["deltas"].to(torch.float32),
        str(training["final_checkpoint_sha256"]),
    )


def _run_condition(
    *,
    condition: Mapping[str, Any],
    delta: Tensor,
    checkpoint_sha256: str,
    output_path: Path,
    stderr_path: Path,
    ordinal: int,
    attempt_id: str,
    replay: Mapping[str, Any],
    manifest: Mapping[str, Any],
    example: Any,
    record: Any,
    backend: HFQwenBackend,
    semantic_path: Path,
    bridge_script: Path,
) -> tuple[dict[str, Any], bool]:
    model_name = str(replay["causal_audit"]["generation"]["model_name"])
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "key": str(row.get("condition_key")) == str(condition["condition_key"]),
            "manifest": str(row.get("condition_manifest_sha256"))
            == str(manifest["manifest_sha256"]),
            "checkpoint": str(row.get("delta_checkpoint_sha256")) == checkpoint_sha256,
            "complete": str(row.get("status")) == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing deep residual row differs: {checks}")
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
        generation_settings = replay["causal_audit"]["generation"]
        messages = build_live_appworld_messages(
            example,
            list(ready["actual_observations"]),
            prompt_profile=str(generation_settings["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        prompt_tokens = len(
            backend.tokenizer(rendered, add_special_tokens=True, truncation=False)["input_ids"]
        )
        remaining = int(generation_settings["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Deep residual prompt is over context: {condition['condition_key']}")
        generation_started = time.perf_counter()
        output, hook = _generate_residual(
            backend=backend,
            messages=messages,
            delta=delta,
            layer_indices=condition["selected_layer_indices"],
            max_new_tokens=min(int(generation_settings["max_new_tokens"]), remaining),
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
            "target_semantic_comparison": executed["target_semantic_comparison"],
        },
        "injection_location": "decoder_block_input_residual",
        "selected_layer_indices": list(condition["selected_layer_indices"]),
        "selected_token_indices": hook["selected_token_indices"][0],
        "layer_ratios": hook["layer_ratios"],
        "global_ratio": hook["global_ratio"],
        "hook_audit": hook,
        "delta_checkpoint_sha256": checkpoint_sha256,
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
    return row, False


def _one_step(
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    replay: Mapping[str, Any],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    if not bool(_json(paths["one_step_preflight"])["passed"]):
        raise RuntimeError("Deep residual one-step preflight failed")
    manifest = _json(paths["condition_manifest"])
    pair_ids, deltas, checkpoint_sha = _load_final_deltas(
        _json(paths["training_summary"])
    )
    positions = {pair_id: index for index, pair_id in enumerate(pair_ids)}
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = _examples_by_state(load_decision_examples(paths["old_decisions"]))
    records = _records_by_task(load_memory_records(paths["old_memories"]))
    output_dir = artifact_dir / "one_step/condition_outputs"
    started = time.perf_counter()
    generation_seconds = 0.0
    completed = []
    resumed = 0
    for ordinal, condition in enumerate(manifest["conditions"], start=1):
        source = str(condition["delta_source_pair_id"])
        key = str(condition["condition_key"])
        row, reused = _run_condition(
            condition=condition,
            delta=deltas[positions[source]],
            checkpoint_sha256=checkpoint_sha,
            output_path=output_dir / condition_checkpoint_name(key),
            stderr_path=artifact_dir / f"one_step/worker_logs/{key}.stderr.log",
            ordinal=ordinal,
            attempt_id=attempt_id,
            replay=replay,
            manifest=manifest,
            example=examples[str(condition["state_example_id"])],
            record=records[str(condition["state_task_id"])],
            backend=backend,
            semantic_path=paths["old_semantic_module"],
            bridge_script=paths["old_bridge_script"],
        )
        resumed += int(reused)
        generation_seconds += 0.0 if reused else float(row["generation_elapsed_seconds"])
        completed.append(row)
        attempt.progress(
            status="deep_residual_one_step",
            completed_conditions=len(completed),
            total_conditions=len(manifest["conditions"]),
            latest_validated_checkpoint=str(
                output_dir / condition_checkpoint_name(key)
            ),
        )
        print(
            f"deep residual {len(completed)}/{len(manifest['conditions'])} "
            f"{condition['condition_name']}",
            flush=True,
        )
    summary = {
        "format": "deep_residual_carrier_generation_summary_7e_v1",
        "condition_count": len(completed),
        "unique_condition_count": len({str(row["condition_key"]) for row in completed}),
        "resumed_condition_count": resumed,
        "new_condition_count": len(completed) - resumed,
        "qwen_generation_seconds": generation_seconds,
        "qwen_generation_h100_hours": generation_seconds / 3600.0,
        "elapsed_seconds": time.perf_counter() - started,
        "same_world_count": sum(row["live_worker"]["same_world_execution"] for row in completed),
        "same_namespace_count": sum(row["live_worker"]["same_python_namespace"] for row in completed),
        "execution_exception_count": sum(
            row["live_worker"]["execution_exception"] is not None for row in completed
        ),
        "passed": len(completed) == 64
        and len({str(row["condition_key"]) for row in completed}) == 64
        and all(row["live_worker"]["same_world_execution"] for row in completed)
        and all(row["live_worker"]["same_python_namespace"] for row in completed),
    }
    atomic_write_json(paths["generation"], summary)
    return summary


def _positive_tasks(rows: Sequence[Mapping[str, Any]]) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row["state_task_id"])][str(row["condition_name"])].append(row)
    details = {}
    for task_id, values in sorted(grouped.items()):
        residual = {
            str(row["state_example_id"]): row
            for row in values.get("R_deep_residual_correct", [])
        }
        bare = {str(row["state_example_id"]): row for row in values.get("C0_bare", [])}
        shared = sorted(set(residual) & set(bare))
        signature = statistics.fmean(
            float(residual[key]["metrics"]["canonical_procedural_signature_match"])
            - float(bare[key]["metrics"]["canonical_procedural_signature_match"])
            for key in shared
        )
        successor = statistics.fmean(
            float(residual[key]["metrics"]["semantic_successor_match"])
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
    *,
    direct: Mapping[str, Any],
    g3: Mapping[str, Any],
    parent_run: Mapping[str, Any],
    run: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=artifact_dir,
    )
    generation = _json(paths["generation"])
    if not bool(generation["passed"]):
        raise RuntimeError("Deep residual one-step infrastructure is invalid")
    outputs = [
        _json(path)
        for path in sorted((artifact_dir / "one_step/condition_outputs").glob("*.json"))
    ]
    pair_states = {
        str(row["state_example_id"]) for row in _json(paths["manifest"])["pairs"]
    }
    c0 = [
        row
        for row in _load_parent_rows(paths["old_parent_c0_outputs"], "C0_bare")
        if str(row["state_example_id"]) in pair_states
    ]
    f3 = [
        row
        for row in _load_parent_rows(
            paths["old_parent_f3_outputs"], "F3_deployment_e_field_raw"
        )
        if str(row["state_example_id"]) in pair_states
    ]
    if len(outputs) != 64 or len(c0) != 32 or len(f3) != 32:
        raise ValueError(f"Expected 64/32/32 rows, got {len(outputs)}/{len(c0)}/{len(f3)}")
    rows = outputs + c0 + f3
    comparisons = {
        "R_minus_C0": comparison_set(
            rows,
            left="R_deep_residual_correct",
            right="C0_bare",
            bootstrap_samples=int(run["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "R_minus_F3": comparison_set(
            rows,
            left="R_deep_residual_correct",
            right="F3_deployment_e_field_raw",
            bootstrap_samples=int(run["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "R_minus_S": comparison_set(
            rows,
            left="R_deep_residual_correct",
            right="S_deep_residual_shuffled",
            bootstrap_samples=int(run["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
        "F3_minus_C0": comparison_set(
            rows,
            left="F3_deployment_e_field_raw",
            right="C0_bare",
            bootstrap_samples=int(run["bootstrap_samples"]),
            seed=GLOBAL_SEED,
            per_metric_seed_offset=False,
        ),
    }
    positive, task_details = _positive_tasks(rows)
    gate = deep_residual_gate(
        r_minus_c0=comparisons["R_minus_C0"],
        r_minus_s=comparisons["R_minus_S"],
        f3_minus_c0=comparisons["F3_minus_C0"],
        positive_task_count=positive,
        material_improvement=float(run["gate"]["material_shuffle_improvement"]),
        material_degradation=float(run["gate"]["material_degradation_tolerance"]),
    )
    branch = (
        "deep_residual_carrier_capacity_validated"
        if gate["passed"]
        else "fixed_size_neural_carrier_behavioral_capacity_failed"
    )
    summary = {
        "format": "deep_residual_carrier_analysis_7e_v1",
        "run_uuid": str(run["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "condition_metrics": condition_summary(rows),
        "per_task": per_task_summary(rows),
        "comparisons": comparisons,
        "positive_task_count": positive,
        "positive_task_details": task_details,
        "gate": gate,
        "decision_branch": branch,
        "carrier_capacity_passed": bool(gate["passed"]),
        "compiler_program_training_triggered": False,
        "generation": generation,
    }
    atomic_write_json(paths["analysis"], summary)
    metrics = summary["condition_metrics"]
    atomic_write_text(
        artifact_dir / "deep_residual_report.md",
        "\n".join(
            [
                "# EXP-026B Deep Residual Carrier Capacity Audit",
                "",
                f"- decision branch: `{branch}`",
                f"- R metrics: `{metrics['R_deep_residual_correct']['metrics']}`",
                f"- S metrics: `{metrics['S_deep_residual_shuffled']['metrics']}`",
                f"- retention: `{gate['retention']}`",
                f"- positive tasks: `{positive}/9`",
                f"- passed: `{gate['passed']}`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    raw = cfg.raw
    direct = raw["stage_c_7dg"]
    g3 = raw["stage_c_7dg3"]
    parent_run = raw["stage_c_7dh"]
    run = raw["stage_c_7e"]
    replay = replay_cfg.raw["stage_c_7b"]
    require_global_seed(int(run["global_seed"]))
    if os.name != "nt" and not os.path.ismount(Path(str(run["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(
        direct=direct,
        g3=g3,
        parent_run=parent_run,
        run=run,
        artifact_dir=args.artifact_dir,
    )
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    data_hashes = {
        name: sha256_file(path)
        for name, path in {
            "config": args.config,
            "replay_config": args.replay_config,
            "parent_manifest": paths["parent_manifest"],
            "parent_preflight": paths["parent_preflight"],
            "parent_teacher_summary": paths["parent_teacher_summary"],
            "parent_training": paths["parent_training"],
            "parent_analysis": paths["parent_analysis"],
            "selector": paths["old_selector"],
            "replay_lineage": paths["old_replay_lineage"],
        }.items()
        if path.exists()
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(run["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"deep_residual_carrier_{args.phase}",
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
        heartbeat_interval_s=float(run["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "validate":
            result = _implementation_validation(
                cfg=cfg,
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "train":
            result = _train(
                cfg=cfg,
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        elif args.phase == "teacher_forced":
            result = _teacher_forced(
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "one_step_preflight":
            result = _one_step_preflight(
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "one_step":
            result = _one_step(
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(
                direct=direct,
                g3=g3,
                parent_run=parent_run,
                run=run,
                artifact_dir=args.artifact_dir,
            )
        checkpoint_by_phase = {
            "preflight": paths["preflight"],
            "validate": paths["validation"],
            "train": paths["training_summary"],
            "teacher_forced": paths["teacher_forced"],
            "one_step_preflight": paths["one_step_preflight"],
            "one_step": paths["generation"],
            "analyze": paths["analysis"],
        }
        attempt.progress(
            status=f"deep_residual_carrier_{args.phase}_completed",
            latest_validated_checkpoint=str(checkpoint_by_phase[args.phase]),
            result_passed=bool(
                result.get("passed", result.get("carrier_capacity_passed", True))
            ),
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
