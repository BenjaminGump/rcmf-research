from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.schemas import DecisionExample
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.oracle_convergence_5fa import (
    ConvergenceObjective,
    IndependentPairTensorTable,
    apply_independent_optimizer_step,
    atomic_torch_save,
    update_count_summary,
)
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    apply_latent_inversion_step,
    flatten_delta,
    module_state_sha256,
    project_latents_to_output_ratio_,
    validate_direct_checkpoint,
)
from rcmf.training.pair_grounding_5d import add_teacher_delta_fields, spearman
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_fast_7df import (
    FactorizedProgramFast,
    FreeIDProgramFast,
    PairMLPProgramFast,
    StaticProgramFast,
    decoded_effect_stability,
    select_transition_program_inputs,
    transition_boundary_invariance,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import (
    TRANSITION_RESPONSE_CACHE_VERSION,
    messages_with_transition_memory,
    utility_category,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.build_stage_c1_response_cache import (
    _build_position_rows,
    _score_target_logits,
)
from scripts.prepare_state_conditioned_program_7d import (
    _context_builder,
    _json,
    _rows,
)
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend
from scripts.run_stage_c_oracle_capacity_5e import (
    _collate,
    _forward_direct_delta,
    _precompute_direct_base_norms,
)
from scripts.run_stage_c_oracle_convergence_5fa import (
    _evaluate_direct_tensor,
    _training_loss,
)
from scripts.run_stage_c_pair_grounding_5d import (
    _build_tokenized_pair_rows,
    _score_pair_response_row,
)
from scripts.run_transition_behavior_6a import _build_tokenized_rows


RUN_FORMAT = "state_conditioned_program_fast_gpu_7df_v1"
K_TOKENS = 4
LATENT_DIM = 128


def _behavioral_objective(
    settings: Mapping[str, Any], section: str
) -> ConvergenceObjective:
    values = settings[section]
    return ConvergenceObjective(
        name=f"fast_{section}_sequence_utility_sparse_kl_target_delta",
        target_delta_weight=float(values["target_delta_huber_weight"]),
        sequence_utility_weight=float(values.get("sequence_utility_weight", 1.0)),
        sparse_teacher_kl_weight=float(values["sparse_teacher_kl_weight"]),
        huber_delta=float(values["sequence_huber_delta"]),
    )


def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_fast_7df.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025df")
    parser.add_argument(
        "--stop-after",
        choices=("teacher", "decoder", "latents", "program", "teacher_forced"),
        default="teacher_forced",
    )
    return parser.parse_args()


def _file_rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _row_file(root: Path, pair_id: str) -> Path:
    return root / f"{sha256_text(pair_id)}.json"


def _build_backend(cfg: Any) -> Any:
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    return backend


def _teacher_response_row(
    *,
    backend: Any,
    context: Mapping[str, Any],
    pair: Mapping[str, Any],
    transition: Mapping[str, Any],
    state_index: int,
    transition_index: int,
    baseline: dict[str, Any],
    prompt_profile: str,
    context_limit: int,
    top_k: int,
    renderer_metadata: Mapping[str, Any],
    lineage: str,
) -> dict[str, Any]:
    teacher_messages = messages_with_transition_memory(
        context["base_messages"], transition, prompt_profile
    )
    teacher_prompt = backend.render_messages(
        teacher_messages, add_generation_prompt=True
    )
    if sha256_text(teacher_prompt) != str(pair["teacher_prompt_sha256"]):
        raise ValueError(f"Teacher prompt hash differs for {pair['pair_id']}")
    teacher = _score_target_logits(
        backend,
        prompt_text=teacher_prompt,
        target_ids=list(context["target_ids"]),
        context_limit=context_limit,
        top_k=top_k,
    )
    positions = add_teacher_delta_fields(
        _build_position_rows(baseline=baseline, teacher=teacher, top_k=top_k)
    )
    utility = float(baseline["mean_target_nll"]) - float(
        teacher["mean_target_nll"]
    )
    example: DecisionExample = context["example"]
    row = {
        "format": TRANSITION_RESPONSE_CACHE_VERSION,
        "scoring_definition": (
            "single_raw_transition_target_top64_delta_clean_fast_7df_v1"
        ),
        "cache_source": "new_clean_fast_pair_teacher_scoring",
        "pair_id": str(pair["pair_id"]),
        "pair_key": str(pair["pair_id"]),
        "state_index": int(state_index),
        "state_example_id": str(pair["state_example_id"]),
        "task_id": str(pair["state_task_id"]),
        "episode_id": str(example.episode_id),
        "step_id": int(example.step_id),
        "split": "train" if str(pair["cell"]) in {"A", "C"} else "validation",
        "entity_type": "transition",
        "entity_id": str(pair["transition_id"]),
        "entity_task_id": str(pair["transition_parent_task_id"]),
        "transition_id": str(pair["transition_id"]),
        "parent_memory_id": str(pair["transition_parent_id"]),
        "parent_task_id": str(pair["transition_parent_task_id"]),
        "memory_id": str(pair["transition_id"]),
        "memory_task_id": str(pair["transition_parent_task_id"]),
        "memory_stage_index": int(transition_index),
        "memory_index": int(transition_index),
        "selection_category": str(pair["pair_role"]),
        "utility_category": utility_category(utility),
        "text_utility": utility,
        "L0": float(baseline["mean_target_nll"]),
        "Lj_text": float(teacher["mean_target_nll"]),
        "baseline_mean_target_nll": float(baseline["mean_target_nll"]),
        "teacher_mean_target_nll": float(teacher["mean_target_nll"]),
        "prompt_tokens": int(baseline["prompt_tokens"]),
        "teacher_prompt_tokens": int(teacher["prompt_tokens"]),
        "raw_memory_tokens": int(teacher["prompt_tokens"])
        - int(baseline["prompt_tokens"]),
        "target_tokens": int(baseline["target_tokens"]),
        "total_tokens_with_target": int(baseline["prompt_tokens"])
        + int(baseline["target_tokens"]),
        "teacher_total_tokens_with_target": int(teacher["prompt_tokens"])
        + int(teacher["target_tokens"]),
        "context_limit": int(context_limit),
        "truncated": False,
        "target_sha256": str(context["target_sha256"]),
        "target_token_sha256": str(context["target_token_sha256"]),
        "prompt_sha256": sha256_text(str(context["base_prompt"])),
        "teacher_prompt_sha256": sha256_text(teacher_prompt),
        "transition_content_sha256": str(
            transition["transition_content_sha256"]
        ),
        "target_token_ids": list(context["target_ids"]),
        "target_positions": positions,
        "last_user_token_indices": list(
            context["prompt_metadata"].get("last_user_token_indices", [])
        ),
        "renderer_version": str(renderer_metadata["renderer_version"]),
        "renderer_metadata": dict(renderer_metadata),
        "model_name": str(backend.model_name),
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
        "source_commit_sha": maybe_git_commit(),
        "clean_lineage_sha256": str(lineage),
        "student_prompt_contains_raw_memory": False,
        "scoring_timestamp_utc": utc_now(),
    }
    del teacher["logits"]
    return row


def _validate_cached_teacher_row(
    row: Mapping[str, Any], pair: Mapping[str, Any], settings: Mapping[str, Any]
) -> None:
    checks = {
        "pair": str(row.get("pair_id")) == str(pair["pair_id"]),
        "prompt": str(row.get("prompt_sha256")) == str(pair["prompt_sha256"]),
        "teacher_prompt": str(row.get("teacher_prompt_sha256"))
        == str(pair["teacher_prompt_sha256"]),
        "target": str(row.get("target_sha256")) == str(pair["target_sha256"]),
        "transition": str(row.get("transition_content_sha256"))
        == str(pair["transition_content_sha256"]),
        "lineage": str(row.get("clean_lineage_sha256"))
        == str(settings["expected_structural_lineage_sha256"]),
        "model": str(row.get("model_name"))
        == str(settings["teacher_cache"]["model_name"]),
        "raw_absent": not bool(row.get("student_prompt_contains_raw_memory")),
        "positions": bool(row.get("target_positions")),
    }
    if not all(checks.values()):
        raise ValueError(
            f"Cached fast teacher row differs for {pair['pair_id']}: {checks}"
        )


def _build_teacher_cache(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    examples: Sequence[DecisionExample],
    contexts: Mapping[str, Mapping[str, Any]],
    index_by_state: Mapping[str, int],
    transitions: Mapping[str, Mapping[str, Any]],
    ordered_transition_ids: Sequence[str],
    pairs: Sequence[dict[str, Any]],
    attempt: AttemptLedger,
) -> list[dict[str, Any]]:
    root = artifact_dir / "teacher_cache"
    rows_root = root / "rows"
    rows_root.mkdir(parents=True, exist_ok=True)
    context_limit = _context_limit_for_backend(backend)
    if context_limit != int(settings["teacher_cache"]["context_limit"]):
        raise ValueError("Runtime context limit differs from the fast preflight")
    top_k = int(settings["teacher_cache"]["top_k"])
    renderer_metadata = appworld_renderer_metadata(
        cfg.benchmark.prompt_profile, add_generation_prompt=True
    )
    transition_positions = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        grouped[str(pair["state_example_id"])].append(pair)
    completed = 0
    baseline_cache: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for state_number, state_id in enumerate(sorted(grouped), start=1):
        pending = []
        for pair in grouped[state_id]:
            path = _row_file(rows_root, str(pair["pair_id"]))
            if path.exists():
                cached = _json(path)
                _validate_cached_teacher_row(cached, pair, settings)
                completed += 1
            else:
                pending.append(pair)
        if not pending:
            continue
        context = contexts[state_id]
        baseline = baseline_cache.get(state_id)
        if baseline is None:
            baseline = _score_target_logits(
                backend,
                prompt_text=str(context["base_prompt"]),
                target_ids=list(context["target_ids"]),
                context_limit=context_limit,
                top_k=top_k,
            )
            baseline_cache[state_id] = baseline
        for pair in sorted(pending, key=lambda value: str(value["pair_id"])):
            transition_id = str(pair["transition_id"])
            row = _teacher_response_row(
                backend=backend,
                context=context,
                pair=pair,
                transition=transitions[transition_id],
                state_index=int(index_by_state[state_id]),
                transition_index=int(transition_positions[transition_id]),
                baseline=baseline,
                prompt_profile=cfg.benchmark.prompt_profile,
                context_limit=context_limit,
                top_k=top_k,
                renderer_metadata=renderer_metadata,
                lineage=str(settings["expected_structural_lineage_sha256"]),
            )
            atomic_write_json(_row_file(rows_root, str(pair["pair_id"])), row)
            completed += 1
        attempt.progress(
            status="teacher_cache_scoring",
            completed_teacher_rows=completed,
            total_teacher_rows=len(pairs),
            completed_states=state_number,
            total_states=len(grouped),
        )
        print(
            f"fast teacher state={state_number}/{len(grouped)} "
            f"rows={completed}/{len(pairs)} elapsed={(time.perf_counter()-started)/60:.1f}m",
            flush=True,
        )
    ordered = []
    for pair in pairs:
        row = _json(_row_file(rows_root, str(pair["pair_id"])))
        _validate_cached_teacher_row(row, pair, settings)
        ordered.append(row)
    pair_ids = [str(row["pair_id"]) for row in ordered]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("Fast teacher cache contains duplicate pair IDs")
    write_jsonl(root / "response_cache.jsonl", ordered)
    summary = {
        "format": "fast_pair_teacher_cache_summary_7df_v1",
        "pair_count": len(ordered),
        "state_count": len(grouped),
        "response_cache_sha256": sha256_file(root / "response_cache.jsonl"),
        "model_name": str(backend.model_name),
        "context_limit": context_limit,
        "top_k": top_k,
        "runtime_seconds": time.perf_counter() - started,
        "student_prompt_contains_raw_transition": False,
        "passed": len(ordered) == len(pairs),
    }
    atomic_write_json(root / "summary.json", summary)
    return ordered


def _cached_prefix_forward(
    *, backend: Any, batch: Mapping[str, Any], delta_slots: Tensor
) -> dict[str, Tensor]:
    if int(batch["input_ids"].shape[0]) != 1:
        raise ValueError("Prefix path currently requires batch size one")
    selected = [int(value) for value in batch["selected_indices"][0].tolist()]
    cut = min(value for value in selected if value >= 0)
    input_ids: Tensor = batch["input_ids"]
    labels: Tensor = batch["labels"]
    attention_mask: Tensor = batch["attention_mask"].to(torch.long)
    base_model = backend.model.model
    lm_head = backend.model.lm_head
    with torch.no_grad():
        prefix = base_model(
            input_ids=input_ids[:, :cut],
            attention_mask=attention_mask[:, :cut],
            use_cache=True,
            return_dict=True,
        )
    suffix_ids = input_ids[:, cut:]
    suffix = backend.model.get_input_embeddings()(suffix_ids)
    suffix_delta = torch.zeros_like(suffix)
    for slot, token_index in enumerate(selected):
        if token_index >= cut:
            suffix_delta[:, token_index - cut] += delta_slots[:, slot].to(
                suffix.dtype
            )
    positions = torch.arange(
        cut, input_ids.shape[1], device=input_ids.device, dtype=torch.long
    ).unsqueeze(0)
    outputs = base_model(
        inputs_embeds=suffix + suffix_delta,
        attention_mask=attention_mask,
        position_ids=positions,
        past_key_values=prefix.past_key_values,
        use_cache=False,
        return_dict=True,
    )
    target_positions = labels[0].ne(-100).nonzero(as_tuple=False).flatten()
    prediction_positions = target_positions - 1
    local = prediction_positions - cut
    if bool((local < 0).any()):
        raise ValueError("Prefix cut falls after a target prediction position")
    target_logits = lm_head(outputs.last_hidden_state[0, local])
    target_labels = labels[0, target_positions]
    loss = F.cross_entropy(target_logits.to(torch.float32), target_labels)
    return {"loss": loss, "target_logits": target_logits}


def _prefix_equivalence(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    settings: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    lengths = sorted(rows, key=lambda row: len(row["input_ids"]))
    positions = (0, len(lengths) // 2, max(0, len(lengths) - 2), len(lengths) - 1)
    selected_rows = [lengths[index] for index in positions]
    atol = float(settings["prefix_cache"]["equivalence_atol"])
    rtol = float(settings["prefix_cache"]["equivalence_rtol"])
    reports = []
    passed = True
    for row_number, row in enumerate(selected_rows):
        batch = _collate([row], device=device, k=K_TOKENS)
        generator = torch.Generator(device=device).manual_seed(25090 + row_number)
        initial = torch.randn(
            1,
            K_TOKENS,
            int(backend.model.config.hidden_size),
            generator=generator,
            device=device,
            dtype=torch.float32,
        ) * 1.0e-3
        full_delta = nn.Parameter(initial.clone())
        cached_delta = nn.Parameter(initial.clone())
        full = _forward_direct_delta(
            backend=backend, batch=batch, delta_slots=full_delta
        )
        cached = _cached_prefix_forward(
            backend=backend, batch=batch, delta_slots=cached_delta
        )
        full_loss, full_terms = _training_loss(
            logits=full["target_logits"],
            batch=batch,
            objective=__import__(
                "rcmf.training.oracle_convergence_5fa",
                fromlist=["OBJECTIVES_5FA"],
            ).OBJECTIVES_5FA["sequence_utility_plus_sparse_kl"],
        )
        cached_loss, cached_terms = _training_loss(
            logits=cached["target_logits"],
            batch=batch,
            objective=__import__(
                "rcmf.training.oracle_convergence_5fa",
                fromlist=["OBJECTIVES_5FA"],
            ).OBJECTIVES_5FA["sequence_utility_plus_sparse_kl"],
        )
        full_loss.backward()
        cached_loss.backward()
        full_grad = full_delta.grad.detach().clone()
        cached_grad = cached_delta.grad.detach().clone()
        full_optimizer = torch.optim.Adam([full_delta], lr=0.05)
        cache_optimizer = torch.optim.Adam([cached_delta], lr=0.05)
        full_optimizer.step()
        cache_optimizer.step()
        checks = {
            "logits": torch.allclose(
                full["target_logits"], cached["target_logits"], atol=atol, rtol=rtol
            ),
            "target_nll": math.isclose(
                float(full["loss"].detach().cpu()),
                float(cached["loss"].detach().cpu()),
                abs_tol=atol,
                rel_tol=rtol,
            ),
            "sequence_utility": torch.allclose(
                full_terms["student_utility"],
                cached_terms["student_utility"],
                atol=atol,
                rtol=rtol,
            ),
            "latent_gradient": torch.allclose(
                full_grad, cached_grad, atol=atol, rtol=rtol
            ),
            "adam_update": torch.allclose(
                full_delta, cached_delta, atol=atol, rtol=rtol
            ),
        }
        passed = passed and all(checks.values())
        reports.append(
            {
                "pair_id": str(row["pair_id"]),
                "tokens": len(row["input_ids"]),
                "checks": checks,
                "logits_max_abs": float(
                    (
                        full["target_logits"].detach().to(torch.float32)
                        - cached["target_logits"].detach().to(torch.float32)
                    )
                    .abs()
                    .max()
                    .cpu()
                ),
                "gradient_max_abs": float(
                    (full_grad - cached_grad).abs().max().cpu()
                ),
                "update_max_abs": float(
                    (full_delta.detach() - cached_delta.detach()).abs().max().cpu()
                ),
            }
        )
        del full, cached, full_delta, cached_delta
        torch.cuda.empty_cache()
    output = {
        "format": "prefix_kv_equivalence_7df_v1",
        "representative_pair_count": len(reports),
        "atol": atol,
        "rtol": rtol,
        "reports": reports,
        "passed": bool(passed),
        "selected_training_path": "cached_prefix" if passed else "full_forward",
        "timebox_respected": True,
    }
    atomic_write_json(artifact_dir / "prefix_cache_equivalence.json", output)
    return output


def _student_forward(
    *,
    backend: Any,
    batch: Mapping[str, Any],
    delta: Tensor,
    prefix_enabled: bool,
) -> dict[str, Tensor]:
    if prefix_enabled:
        return _cached_prefix_forward(backend=backend, batch=batch, delta_slots=delta)
    return _forward_direct_delta(backend=backend, batch=dict(batch), delta_slots=delta)


def _score_missing_clean_memory_row(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    source_row: Mapping[str, Any],
    examples: Sequence[DecisionExample],
    records_by_id: Mapping[str, Any],
) -> dict[str, Any]:
    selected = dict(source_row)
    selected.setdefault("state_index", int(source_row["state_index"]))
    selected.setdefault("L0", float(source_row["baseline_mean_target_nll"]))
    selected.setdefault("selection_category", "decoder_row_repair")
    selected.setdefault("utility_category", str(source_row["utility_category"]))
    baseline_cache: dict[str, dict[str, Any]] = {}
    return _score_pair_response_row(
        backend=backend,
        tokenizer=backend.tokenizer,
        selected=selected,
        example=examples[int(selected["state_index"])],
        record=records_by_id[str(selected["memory_id"])],
        prompt_profile=cfg.benchmark.prompt_profile,
        renderer_metadata=appworld_renderer_metadata(
            cfg.benchmark.prompt_profile, add_generation_prompt=True
        ),
        source_commit=maybe_git_commit(),
        model_config_commit_hash=getattr(backend.model.config, "_commit_hash", None),
        context_limit=int(settings["teacher_cache"]["context_limit"]),
        top_k=int(settings["teacher_cache"]["top_k"]),
        baseline_cache=baseline_cache,
        corpus_lineage_sha256=str(settings["expected_structural_lineage_sha256"]),
    )


def _heldout_decoder_indices(pair_ids: Sequence[str], count: int = 16) -> tuple[list[int], list[int]]:
    by_state: dict[str, list[int]] = defaultdict(list)
    for index, pair_id in enumerate(pair_ids):
        by_state[str(pair_id).split("::memory::", 1)[0]].append(index)
    heldout_states = []
    selected_count = 0
    for state_id in sorted(by_state, key=lambda value: stable_key(25092, "decoder", value)):
        heldout_states.append(state_id)
        selected_count += len(by_state[state_id])
        if selected_count >= int(count):
            break
    heldout_candidates = [index for state in heldout_states for index in by_state[state]]
    heldout = sorted(
        heldout_candidates,
        key=lambda index: stable_key(25092, "decoder-pair", pair_ids[index]),
    )[: int(count)]
    heldout_state_set = set(heldout_states)
    calibration = [
        index
        for index, pair_id in enumerate(pair_ids)
        if str(pair_id).split("::memory::", 1)[0] not in heldout_state_set
    ]
    if len(heldout) != int(count) or not calibration:
        raise ValueError("Could not construct grouped decoder heldout split")
    return calibration, heldout


def _decoder_gate(
    reconstruction: Mapping[str, Any], zero: Mapping[str, Any]
) -> dict[str, Any]:
    summary = reconstruction["summary"]
    zero_summary = zero["summary"]
    huber = float(summary["sequence_utility_huber"]["mean"])
    zero_huber = float(zero_summary["sequence_utility_huber"]["mean"])
    reduction = 1.0 - huber / max(zero_huber, 1.0e-12)
    checks = {
        "heldout_spearman_gte_0_65": float(
            summary.get("u_text_vs_u_student_spearman") or -1.0
        )
        >= 0.65,
        "sign_agreement_gte_0_75": float(
            summary.get("positive_negative_sign_agreement") or 0.0
        )
        >= 0.75,
        "huber_reduction_gte_0_35": reduction >= 0.35,
        "ratio_lte_1": float(summary["delta_ratio"]["max"]) <= 1.0001,
    }
    return {"passed": all(checks.values()), "checks": checks, "huber_reduction": reduction}


def _decoder_evaluation_delta(
    values: Tensor, *, device: torch.device, model_dim: int
) -> Tensor:
    return values.view(-1, K_TOKENS, int(model_dim)).to(device=device)


def _repair_decoder(
    *,
    backend: Any,
    cfg: Any,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    examples: Sequence[DecisionExample],
    prefix_enabled: bool,
    attempt: AttemptLedger,
) -> tuple[LinearDeltaDecoder, dict[str, Any]]:
    root = artifact_dir / "decoder"
    root.mkdir(parents=True, exist_ok=True)
    final_path = root / "repaired_rank128_decoder.pt"
    if final_path.exists() and (root / "summary.json").exists():
        payload = torch.load(final_path, map_location="cpu", weights_only=False)
        decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * int(backend.model.config.hidden_size)).to(backend.device)
        decoder.load_state_dict(payload["decoder_state_dict"])
        decoder.eval()
        for parameter in decoder.parameters():
            parameter.requires_grad_(False)
        return decoder, _json(root / "summary.json")

    decoder_cfg = settings["decoder"]
    source_checkpoint = Path(str(decoder_cfg["source_checkpoint"]))
    source_payload = torch.load(source_checkpoint, map_location="cpu", weights_only=False)
    pair_ids = [str(value) for value in source_payload["pair_ids"]]
    validation = validate_direct_checkpoint(
        source_payload,
        expected_pair_ids=pair_ids,
        expected_updates=112,
        model_dim=int(backend.model.config.hidden_size),
    )
    if not validation["passed"] or len(pair_ids) != 192:
        raise ValueError(f"Direct u112 source validation failed: {validation}")
    source_delta = validation.pop("tensor").to(torch.float32)
    source_rows = {
        str(row["pair_id"]): row
        for row in _file_rows(Path(str(decoder_cfg["source_pair_cache"])))
    }
    clean_rows = {
        str(row["pair_id"]): row
        for row in _file_rows(Path(str(decoder_cfg["clean_pair_cache"])))
    }
    affected = [
        pair_id
        for pair_id in pair_ids
        if str(source_rows[pair_id].get("memory_task_id"))
        == str(decoder_cfg["affected_memory_task"])
    ]
    if len(affected) != int(decoder_cfg["expected_repair_rows"]):
        raise ValueError("Direct decoder affected-row count changed")
    records = {
        record.memory_id: record
        for record in load_memory_records(
            Path(str(settings["reconciled_corpus_dir"])) / "memory_records.jsonl"
        )
    }
    cached_new_path = root / "new_clean_missing_response_row.json"
    repair_responses = []
    for pair_id in affected:
        if pair_id in clean_rows:
            repair_responses.append(clean_rows[pair_id])
        elif cached_new_path.exists():
            cached_new = _json(cached_new_path)
            checks = {
                "pair_id": str(cached_new.get("pair_id")) == pair_id,
                "state": str(cached_new.get("state_example_id"))
                == str(source_rows[pair_id]["state_example_id"]),
                "memory": str(cached_new.get("memory_id"))
                == str(source_rows[pair_id]["memory_id"]),
                "target": str(cached_new.get("target_sha256"))
                == str(source_rows[pair_id]["target_sha256"]),
                "lineage": str(cached_new.get("corpus_lineage_sha256"))
                == str(settings["expected_structural_lineage_sha256"]),
                "model": str(cached_new.get("model_name"))
                == str(settings["teacher_cache"]["model_name"]),
            }
            if not all(checks.values()):
                raise ValueError(f"Cached clean decoder row differs: {checks}")
            repair_responses.append(cached_new)
        else:
            repaired = _score_missing_clean_memory_row(
                backend=backend,
                cfg=cfg,
                settings=settings,
                source_row=source_rows[pair_id],
                examples=examples,
                records_by_id=records,
            )
            atomic_write_json(cached_new_path, repaired)
            repair_responses.append(repaired)
    repair_tokenized = _build_tokenized_pair_rows(
        backend=backend,
        examples=list(examples),
        pair_rows=repair_responses,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(settings["teacher_cache"]["context_limit"]),
    )
    device = backend.device
    model_dim = int(backend.model.config.hidden_size)
    table = IndependentPairTensorTable(
        affected, (K_TOKENS, model_dim), init_std=0.0
    ).to(device)
    source_positions = {value: index for index, value in enumerate(pair_ids)}
    with torch.no_grad():
        for index, pair_id in enumerate(affected):
            table.rows[index].copy_(source_delta[source_positions[pair_id]].to(device))
    optimizer = torch.optim.AdamW(
        table.parameters(), lr=float(decoder_cfg["learning_rate"]), weight_decay=0.0
    )
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=repair_tokenized, device=device, k=K_TOKENS
    ).to(device)
    update_counts = [0] * len(affected)
    objective = _behavioral_objective(settings, "decoder")
    history = []
    completed_round = 0
    available_checkpoints = sorted((root / "checkpoints").glob("repair_u*.pt"))
    if available_checkpoints:
        resume_path = available_checkpoints[-1]
        resume = torch.load(resume_path, map_location="cpu", weights_only=False)
        resumed_counts = [int(value) for value in resume["update_counts"]]
        completed_round = min(resumed_counts)
        checks = {
            "format": str(resume.get("format"))
            == "direct_row_repair_checkpoint_7df_v1",
            "pair_ids": list(resume.get("pair_ids", [])) == affected,
            "counts_equal": len(set(resumed_counts)) == 1,
            "counts_match_checkpoint": completed_round
            == int(resume_path.stem.rsplit("u", 1)[-1]),
            "within_schedule": completed_round
            <= max(int(value) for value in decoder_cfg["repair_updates"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Decoder row-repair resume differs: {checks}")
        table.load_state_dict(resume["table_state_dict"])
        optimizer.load_state_dict(resume["optimizer_state_dict"])
        update_counts = resumed_counts
        history = list(resume["history"])
    for update_round in range(
        completed_round + 1, max(decoder_cfg["repair_updates"]) + 1
    ):
        order = list(range(len(affected)))
        random.Random(25091 * 1_000_000 + update_round).shuffle(order)
        for index in order:
            batch = _collate([repair_tokenized[index]], device=device, k=K_TOKENS)
            delta = table.forward_indices([index])
            student = _student_forward(
                backend=backend,
                batch=batch,
                delta=delta,
                prefix_enabled=prefix_enabled,
            )
            loss, _ = _training_loss(
                logits=student["target_logits"], batch=batch, objective=objective
            )
            apply_independent_optimizer_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                selected_indices=[index],
                update_counts=update_counts,
                base_norms=base_norms,
                ratio_budget=1.0,
                max_grad_norm=1.0,
            )
        if update_round in {int(value) for value in decoder_cfg["repair_updates"]}:
            evaluation = _evaluate_direct_tensor(
                backend=backend,
                rows=repair_tokenized,
                delta_tensor=table.stacked().detach(),
                pair_ids=affected,
                device=device,
                k=K_TOKENS,
                batch_size=1,
                huber_delta=float(decoder_cfg["sequence_huber_delta"]),
                control=f"clean_direct_repair_u{update_round}",
            )
            write_jsonl(
                root / f"repair_evaluation_u{update_round:03d}.jsonl",
                evaluation["rows"],
            )
            history.append(
                {
                    "updates_per_pair": update_round,
                    "summary": evaluation["summary"],
                    "update_accounting": update_count_summary(affected, update_counts),
                }
            )
            atomic_torch_save(
                {
                    "format": "direct_row_repair_checkpoint_7df_v1",
                    "pair_ids": affected,
                    "table_state_dict": {
                        key: value.detach().cpu()
                        for key, value in table.state_dict().items()
                    },
                    "optimizer_state_dict": optimizer.state_dict(),
                    "update_counts": update_counts,
                    "history": history,
                },
                root / "checkpoints" / f"repair_u{update_round:03d}.pt",
            )
            attempt.progress(
                status="decoder_row_repair",
                latest_validated_checkpoint=str(
                    root / "checkpoints" / f"repair_u{update_round:03d}.pt"
                ),
                updates_per_repair_pair=update_round,
            )
    repaired_delta = source_delta.clone()
    for local_index, pair_id in enumerate(affected):
        repaired_delta[source_positions[pair_id]] = table.rows[local_index].detach().cpu()

    all_responses = []
    for pair_id in pair_ids:
        if pair_id in affected:
            all_responses.append(repair_responses[affected.index(pair_id)])
        elif pair_id in clean_rows:
            all_responses.append(clean_rows[pair_id])
        else:
            all_responses.append(source_rows[pair_id])
    all_tokenized = _build_tokenized_pair_rows(
        backend=backend,
        examples=list(examples),
        pair_rows=all_responses,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(settings["teacher_cache"]["context_limit"]),
    )
    calibration, heldout = _heldout_decoder_indices(pair_ids, 16)
    _, singular_train, vh_train = torch.linalg.svd(
        flatten_delta(repaired_delta[calibration]).to(torch.float64),
        full_matrices=False,
    )
    basis_train = vh_train[:LATENT_DIM].to(torch.float32)
    flat_heldout = flatten_delta(repaired_delta[heldout])
    reconstructed = (flat_heldout @ basis_train.T) @ basis_train
    heldout_rows = [all_tokenized[index] for index in heldout]
    heldout_ids = [pair_ids[index] for index in heldout]
    reconstructed_eval = _evaluate_direct_tensor(
        backend=backend,
        rows=heldout_rows,
        delta_tensor=_decoder_evaluation_delta(
            reconstructed, device=device, model_dim=model_dim
        ),
        pair_ids=heldout_ids,
        device=device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=float(decoder_cfg["sequence_huber_delta"]),
        control="repaired_rank128_grouped_heldout",
    )
    zero_eval = _evaluate_direct_tensor(
        backend=backend,
        rows=heldout_rows,
        delta_tensor=_decoder_evaluation_delta(
            torch.zeros_like(reconstructed), device=device, model_dim=model_dim
        ),
        pair_ids=heldout_ids,
        device=device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=float(decoder_cfg["sequence_huber_delta"]),
        control="repaired_decoder_grouped_heldout_zero",
    )
    gate = _decoder_gate(reconstructed_eval, zero_eval)
    if not gate["passed"]:
        raise RuntimeError(f"Repaired rank128 decoder gate failed: {gate}")
    _, singular_full, vh_full = torch.linalg.svd(
        flatten_delta(repaired_delta).to(torch.float64), full_matrices=False
    )
    basis = vh_full[:LATENT_DIM].to(torch.float32)
    decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * model_dim).to(device)
    decoder.initialize_from_basis(basis.to(device))
    decoder.eval()
    for parameter in decoder.parameters():
        parameter.requires_grad_(False)
    payload = {
        "format": "clean_repaired_rank128_svd_decoder_7df_v1",
        "decoder_state_dict": {
            key: value.detach().cpu() for key, value in decoder.state_dict().items()
        },
        "basis": basis,
        "source_checkpoint": str(source_checkpoint),
        "source_checkpoint_sha256": sha256_file(source_checkpoint),
        "source_pair_ids": pair_ids,
        "affected_pair_ids": affected,
        "repaired_delta": repaired_delta,
        "singular_values": singular_full.cpu(),
        "source_commit": maybe_git_commit(),
    }
    atomic_torch_save(payload, final_path)
    write_jsonl(root / "grouped_heldout_reconstruction_rows.jsonl", reconstructed_eval["rows"])
    write_jsonl(root / "grouped_heldout_zero_rows.jsonl", zero_eval["rows"])
    summary = {
        "format": "clean_repaired_rank128_decoder_summary_7df_v1",
        "source_checkpoint": str(source_checkpoint),
        "source_checkpoint_sha256": sha256_file(source_checkpoint),
        "affected_pair_count": len(affected),
        "clean_cached_repair_rows": sum(pair_id in clean_rows for pair_id in affected),
        "new_clean_repair_rows": sum(pair_id not in clean_rows for pair_id in affected),
        "calibration_pair_count": len(calibration),
        "heldout_pair_count": len(heldout),
        "heldout_pair_ids": heldout_ids,
        "rank": LATENT_DIM,
        "decoder_sha256": module_state_sha256(decoder),
        "repair_history": history,
        "heldout_reconstruction": reconstructed_eval["summary"],
        "heldout_zero": zero_eval["summary"],
        "gate": gate,
        "final_checkpoint": str(final_path),
        "passed": gate["passed"],
    }
    atomic_write_json(root / "summary.json", summary)
    return decoder, summary


def _latent_preservation_loss(
    terms: Mapping[str, Tensor], response: Mapping[str, Any], settings: Mapping[str, Any]
) -> Tensor:
    utility = float(response["text_utility"])
    category = str(response["utility_category"])
    weight = 0.0
    if category == "neutral":
        weight = float(settings["pair_latents"]["neutral_preservation_weight"])
    elif utility < 0.0:
        weight = float(settings["pair_latents"]["harmful_preservation_weight"])
    return terms["student_utility"].pow(2).mean() * weight


def _latent_checkpoint_payload(
    *,
    table: IndependentPairTensorTable,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    decoder_hash: str,
) -> dict[str, Any]:
    return {
        "format": "canonical_pair_latent_checkpoint_7df_v1",
        "pair_ids": list(pair_ids),
        "completed_rounds": int(completed_rounds),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "table_state_dict": {
            key: value.detach().cpu() for key, value in table.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "decoder_sha256": str(decoder_hash),
        "source_commit": maybe_git_commit(),
    }


def _optimize_latent_table(
    *,
    name: str,
    backend: Any,
    decoder: LinearDeltaDecoder,
    rows: Sequence[dict[str, Any]],
    settings: Mapping[str, Any],
    output_dir: Path,
    seed: int,
    maximum_round: int,
    checkpoints: set[int],
    prefix_enabled: bool,
    attempt: AttemptLedger | None,
) -> tuple[Tensor, list[dict[str, Any]], dict[int, Tensor]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pair_ids = [str(row["pair_id"]) for row in rows]
    device = backend.device
    table = IndependentPairTensorTable(pair_ids, (LATENT_DIM,), init_std=0.0).to(device)
    optimizer = torch.optim.AdamW(
        table.parameters(),
        lr=float(settings["pair_latents"]["learning_rate"]),
        weight_decay=0.0,
    )
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=K_TOKENS
    ).to(device)
    update_counts = [0] * len(pair_ids)
    objective = _behavioral_objective(settings, "pair_latents")
    history: list[dict[str, Any]] = []
    snapshots: dict[int, Tensor] = {}
    latest = output_dir / "latest_checkpoint.json"
    completed = 0
    if latest.exists():
        checkpoint_path = Path(_json(latest)["checkpoint"])
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        checks = {
            "pair_ids": payload["pair_ids"] == pair_ids,
            "decoder": payload["decoder_sha256"] == module_state_sha256(decoder),
        }
        if not all(checks.values()):
            raise ValueError(f"Latent resume identity differs: {checks}")
        table.load_state_dict(payload["table_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        history = list(payload["history"])
        completed = int(payload["completed_rounds"])
    started = time.perf_counter()
    for update_round in range(completed + 1, int(maximum_round) + 1):
        order = list(range(len(rows)))
        random.Random(seed * 1_000_000 + update_round).shuffle(order)
        interval_losses = []
        for index in order:
            batch = _collate([rows[index]], device=device, k=K_TOKENS)
            z = table.forward_indices([index])
            delta = decoder(z).view(1, K_TOKENS, -1)
            student = _student_forward(
                backend=backend,
                batch=batch,
                delta=delta,
                prefix_enabled=prefix_enabled,
            )
            loss, terms = _training_loss(
                logits=student["target_logits"], batch=batch, objective=objective
            )
            loss = (
                loss
                + _latent_preservation_loss(terms, rows[index]["response_cache"], settings)
                + float(settings["pair_latents"]["ratio_restraint_weight"])
                * z.pow(2).mean()
            )
            step = apply_latent_inversion_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                decoder=decoder,
                selected_indices=[index],
                update_counts=update_counts,
                base_norms=base_norms,
                ratio_budget=1.0,
                max_grad_norm=1.0,
            )
            interval_losses.append(
                {
                    "loss": float(loss.detach().cpu()),
                    "gradient_norm": float(step["gradient_norm"]),
                }
            )
        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or int(
            accounting["minimum_updates_per_pair"]
        ) != update_round:
            raise RuntimeError(f"Unequal latent updates after u{update_round}")
        if update_round not in checkpoints:
            continue
        latents = table.stacked().detach()
        snapshots[update_round] = latents.cpu().clone()
        with torch.no_grad():
            delta = decoder(latents).view(len(rows), K_TOKENS, -1)
        evaluation = _evaluate_direct_tensor(
            backend=backend,
            rows=rows,
            delta_tensor=delta,
            pair_ids=pair_ids,
            device=device,
            k=K_TOKENS,
            batch_size=1,
            huber_delta=float(settings["pair_latents"]["sequence_huber_delta"]),
            control=f"{name}_u{update_round}",
        )
        write_jsonl(output_dir / f"evaluation_u{update_round:03d}.jsonl", evaluation["rows"])
        entry = {
            "updates_per_pair": update_round,
            "summary": evaluation["summary"],
            "update_accounting": accounting,
            "mean_train_loss": statistics.fmean(value["loss"] for value in interval_losses),
            "mean_gradient_norm": statistics.fmean(
                value["gradient_norm"] for value in interval_losses
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint_path = output_dir / "checkpoints" / f"latents_u{update_round:03d}.pt"
        atomic_torch_save(
            _latent_checkpoint_payload(
                table=table,
                optimizer=optimizer,
                pair_ids=pair_ids,
                update_counts=update_counts,
                completed_rounds=update_round,
                history=history,
                decoder_hash=module_state_sha256(decoder),
            ),
            checkpoint_path,
        )
        atomic_write_json(
            latest,
            {"checkpoint": str(checkpoint_path), "updates_per_pair": update_round},
        )
        if attempt is not None:
            attempt.progress(
                status=f"{name}_optimization",
                latest_validated_checkpoint=str(checkpoint_path),
                updates_per_pair=update_round,
            )
        print(
            f"{name} u{update_round} rho="
            f"{evaluation['summary'].get('u_text_vs_u_student_spearman')} "
            f"huber={evaluation['summary']['sequence_utility_huber']['mean']:.6f}",
            flush=True,
        )
    return table.stacked().detach().cpu(), history, snapshots


def _material_u32_improvement(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_update = {int(row["updates_per_pair"]): row for row in history}
    if 16 not in by_update or 32 not in by_update:
        return {"assessable": False, "continue_to_64": False}
    h16 = float(by_update[16]["summary"]["sequence_utility_huber"]["mean"])
    h32 = float(by_update[32]["summary"]["sequence_utility_huber"]["mean"])
    s16 = float(by_update[16]["summary"].get("u_text_vs_u_student_spearman") or 0.0)
    s32 = float(by_update[32]["summary"].get("u_text_vs_u_student_spearman") or 0.0)
    relative = (h16 - h32) / max(abs(h16), 1.0e-12)
    return {
        "assessable": True,
        "relative_huber_improvement": relative,
        "spearman_improvement": s32 - s16,
        "continue_to_64": relative >= 0.01 or (s32 - s16) >= 0.01,
    }


def _optimize_canonical_latents(
    *,
    backend: Any,
    decoder: LinearDeltaDecoder,
    rows: Sequence[dict[str, Any]],
    settings: Mapping[str, Any],
    artifact_dir: Path,
    prefix_enabled: bool,
    attempt: AttemptLedger,
    run_started: float,
) -> tuple[Tensor, dict[str, Any]]:
    root = artifact_dir / "pair_latents"
    updates = {int(value) for value in settings["pair_latents"]["updates"]}
    primary, history, snapshots = _optimize_latent_table(
        name="canonical_pair_latents",
        backend=backend,
        decoder=decoder,
        rows=rows,
        settings=settings,
        output_dir=root / "primary",
        seed=int(settings["pair_latents"]["primary_seed"]),
        maximum_round=max(updates),
        checkpoints=updates,
        prefix_enabled=prefix_enabled,
        attempt=attempt,
    )
    continuation = _material_u32_improvement(history)
    observed_seconds_per_update = (
        time.perf_counter() - run_started
    ) / max(1, len(rows) * max(updates))
    projected_u64_hours = (
        time.perf_counter()
        - run_started
        + observed_seconds_per_update * len(rows) * 32
    ) / 3600.0
    continuation["projected_total_h100_hours"] = projected_u64_hours
    continuation["runtime_allows_u64"] = projected_u64_hours <= float(
        settings["runtime"]["review_threshold_h100_hours"]
    )
    if continuation["continue_to_64"] and continuation["runtime_allows_u64"]:
        primary, history, more_snapshots = _optimize_latent_table(
            name="canonical_pair_latents",
            backend=backend,
            decoder=decoder,
            rows=rows,
            settings=settings,
            output_dir=root / "primary",
            seed=int(settings["pair_latents"]["primary_seed"]),
            maximum_round=64,
            checkpoints={64},
            prefix_enabled=prefix_enabled,
            attempt=attempt,
        )
        snapshots.update(more_snapshots)
    stability_count = int(settings["pair_latents"]["stability_pair_count"])
    stability_indices = sorted(
        range(len(rows)),
        key=lambda index: stable_key(25093, "stability", rows[index]["pair_id"]),
    )[:stability_count]
    stability_rows = [rows[index] for index in stability_indices]
    repeat, repeat_history, _ = _optimize_latent_table(
        name="canonical_pair_latents_repeat_seed",
        backend=backend,
        decoder=decoder,
        rows=stability_rows,
        settings=settings,
        output_dir=root / "repeat_seed",
        seed=int(settings["pair_latents"]["repeat_seed"]),
        maximum_round=int(settings["pair_latents"]["stability_updates"]),
        checkpoints={int(settings["pair_latents"]["stability_updates"])},
        prefix_enabled=prefix_enabled,
        attempt=attempt,
    )
    primary_u16 = snapshots.get(16)
    if primary_u16 is None:
        primary_checkpoint = torch.load(
            root / "primary/checkpoints/latents_u016.pt",
            map_location="cpu",
            weights_only=False,
        )
        temp = IndependentPairTensorTable(
            [str(row["pair_id"]) for row in rows], (LATENT_DIM,), init_std=0.0
        )
        temp.load_state_dict(primary_checkpoint["table_state_dict"])
        primary_u16 = temp.stacked().detach().cpu()
    primary_subset = primary_u16[stability_indices]
    with torch.no_grad():
        primary_delta = decoder(primary_subset.to(backend.device)).view(
            stability_count, K_TOKENS, -1
        )
        repeat_delta = decoder(repeat.to(backend.device)).view(
            stability_count, K_TOKENS, -1
        )
    effect = decoded_effect_stability(primary_delta.cpu(), repeat_delta.cpu())
    primary_eval = _evaluate_direct_tensor(
        backend=backend,
        rows=stability_rows,
        delta_tensor=primary_delta,
        pair_ids=[str(row["pair_id"]) for row in stability_rows],
        device=backend.device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=float(settings["pair_latents"]["sequence_huber_delta"]),
        control="stability_primary_u16",
    )
    repeat_eval = _evaluate_direct_tensor(
        backend=backend,
        rows=stability_rows,
        delta_tensor=repeat_delta,
        pair_ids=[str(row["pair_id"]) for row in stability_rows],
        device=backend.device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=float(settings["pair_latents"]["sequence_huber_delta"]),
        control="stability_repeat_u16",
    )
    primary_util = [float(row["u_student"]) for row in primary_eval["rows"]]
    repeat_util = [float(row["u_student"]) for row in repeat_eval["rows"]]
    utility_rho = spearman(primary_util, repeat_util)
    sign = statistics.fmean(
        float((left >= 0.0) == (right >= 0.0))
        for left, right in zip(primary_util, repeat_util)
    )
    stability = {
        **effect,
        "repeat_utility_spearman": utility_rho,
        "repeat_sign_agreement": sign,
        "checks": {
            "decoded_delta_cosine_gte_0_85": effect["mean_cosine"] >= 0.85,
            "repeat_utility_spearman_gte_0_90": utility_rho >= 0.90,
            "repeat_sign_agreement_gte_0_90": sign >= 0.90,
        },
    }
    stability["passed"] = all(stability["checks"].values())
    write_jsonl(root / "stability_primary_rows.jsonl", primary_eval["rows"])
    write_jsonl(root / "stability_repeat_rows.jsonl", repeat_eval["rows"])
    if not stability["passed"]:
        raise RuntimeError(f"Canonical pair targets are nonidentifiable: {stability}")
    final_update = int(history[-1]["updates_per_pair"])
    summary = {
        "format": "canonical_pair_latent_summary_7df_v1",
        "pair_count": len(rows),
        "final_updates_per_pair": final_update,
        "history": history,
        "u32_continuation": continuation,
        "stability": stability,
        "repeat_history": repeat_history,
        "target_space": "latent_z",
        "passed": stability["passed"],
    }
    atomic_write_json(root / "summary.json", summary)
    atomic_torch_save(
        {
            "format": "canonical_pair_latent_targets_7df_v1",
            "pair_ids": [str(row["pair_id"]) for row in rows],
            "latents": primary,
            "updates_per_pair": final_update,
            "decoder_sha256": module_state_sha256(decoder),
            "summary_sha256": canonical_sha256(summary),
        },
        root / "canonical_targets.pt",
    )
    return primary, summary


def _program_model(
    name: str,
    *,
    settings: Mapping[str, Any],
    transition_view_names: Sequence[str],
    train_transition_ids: Sequence[str],
) -> nn.Module:
    program = settings["program"]
    common = {
        "transition_view_names": transition_view_names,
        "representation_dim": int(settings["representations"]["representation_dim"]),
        "program_dim": int(program["program_dim"]),
        "hidden_dim": int(program["hidden_dim"]),
        "dropout": float(program["dropout"]),
    }
    if name in {
        "full_factorized_r16_observation_excluded",
        "shuffled_transition",
    }:
        return FactorizedProgramFast(
            state_vector_count=int(settings["representations"]["state_vector_count"]),
            controller_rank=int(program["controller_rank"]),
            include_outcome=False,
            **common,
        )
    if name == "full_factorized_r16_action_plus_outcome":
        return FactorizedProgramFast(
            state_vector_count=int(settings["representations"]["state_vector_count"]),
            controller_rank=int(program["controller_rank"]),
            include_outcome=True,
            **common,
        )
    if name == "static_only_observation_excluded":
        return StaticProgramFast(**common)
    if name == "pair_mlp_observation_excluded":
        return PairMLPProgramFast(
            state_vector_count=int(settings["representations"]["state_vector_count"]),
            **common,
        )
    if name == "free_id":
        return FreeIDProgramFast(train_transition_ids, int(program["program_dim"]))
    raise ValueError(f"No trainable program model for {name}")


def _program_predictions(
    model: nn.Module,
    name: str,
    state_values: Tensor,
    transition_values: Tensor,
    transition_ids: Sequence[str],
) -> Tensor:
    if name == "free_id":
        return model.forward_ids(transition_ids, device=state_values.device)
    return model(state_values, transition_values)


def _tensor_metrics(predicted: Tensor, target: Tensor) -> dict[str, Any]:
    predicted = predicted.to(torch.float32)
    target = target.to(torch.float32)
    mse = float(F.mse_loss(predicted, target).detach().cpu())
    zero_mse = float(target.pow(2).mean().detach().cpu())
    cosine = F.cosine_similarity(predicted, target, dim=-1)
    return {
        "mse": mse,
        "zero_mse": zero_mse,
        "mse_reduction_vs_zero": 1.0 - mse / max(zero_mse, 1.0e-12),
        "huber": float(F.smooth_l1_loss(predicted, target, beta=0.1).detach().cpu()),
        "mean_cosine": float(cosine.mean().detach().cpu()),
        "median_cosine": float(cosine.median().detach().cpu()),
        "relative_frobenius_error": float(
            (predicted - target).norm().div(target.norm().clamp_min(1.0e-12)).cpu()
        ),
    }


def _program_split(rows: Sequence[Mapping[str, Any]]) -> tuple[list[int], list[int], dict[str, Any]]:
    tasks = sorted(
        {str(row["state_task_id"]) for row in rows},
        key=lambda value: stable_key(25094, "program-task", value),
    )
    parents = sorted(
        {str(row["transition_parent_id"]) for row in rows},
        key=lambda value: stable_key(25094, "program-parent", value),
    )
    val_tasks = set(tasks[:8])
    val_parents = set(parents[:6])
    validation = [
        index
        for index, row in enumerate(rows)
        if str(row["state_task_id"]) in val_tasks
        or str(row["transition_parent_id"]) in val_parents
    ]
    training = [index for index in range(len(rows)) if index not in set(validation)]
    if not training or not validation:
        raise ValueError("A-only grouped program split is empty")
    return training, validation, {
        "train_pairs": len(training),
        "validation_pairs": len(validation),
        "heldout_task_ids": sorted(val_tasks),
        "heldout_parent_ids": sorted(val_parents),
        "task_overlap": 0,
        "parent_overlap": 0,
    }


def _train_one_program(
    *,
    name: str,
    seed: int,
    settings: Mapping[str, Any],
    state_values: Tensor,
    transition_values: Tensor,
    transition_ids: Sequence[str],
    target: Tensor,
    train_indices: Sequence[int],
    validation_indices: Sequence[int],
    transition_view_names: Sequence[str],
    output_dir: Path,
) -> tuple[nn.Module, dict[str, Any]]:
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model = _program_model(
        name,
        settings=settings,
        transition_view_names=transition_view_names,
        train_transition_ids=[transition_ids[index] for index in train_indices],
    ).to(state_values.device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(settings["program"]["learning_rate"]),
        weight_decay=float(settings["program"]["weight_decay"]),
    )
    batch_size = int(settings["program"]["batch_size"])
    max_epochs = int(settings["program"]["maximum_epochs"])
    patience = int(settings["program"]["early_stop_patience"])
    checkpoint_epochs = {int(value) for value in settings["program"]["checkpoint_epochs"]}
    shuffled_order = sorted(
        range(len(transition_ids)),
        key=lambda index: stable_key(seed, "shuffled-input", transition_ids[index], index),
    )
    shuffled_transition_values = transition_values[shuffled_order]
    best_loss = math.inf
    best_epoch = 0
    best_state: dict[str, Tensor] | None = None
    history = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, max_epochs + 1):
        model.train()
        order = list(train_indices)
        random.Random(seed * 1_000_000 + epoch).shuffle(order)
        losses = []
        for start in range(0, len(order), batch_size):
            indices = order[start : start + batch_size]
            index_tensor = torch.tensor(indices, device=state_values.device)
            transition_batch = transition_values[index_tensor]
            if name == "shuffled_transition":
                transition_batch = shuffled_transition_values[index_tensor]
            prediction = _program_predictions(
                model,
                name,
                state_values[index_tensor],
                transition_batch,
                [transition_ids[index] for index in indices],
            )
            target_batch = target[index_tensor]
            loss = F.smooth_l1_loss(prediction, target_batch, beta=0.1)
            loss = loss + 0.1 * (
                1.0 - F.cosine_similarity(prediction, target_batch, dim=-1)
            ).mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        model.eval()
        with torch.no_grad():
            val_tensor = torch.tensor(validation_indices, device=state_values.device)
            val_transition = transition_values[val_tensor]
            if name == "shuffled_transition":
                val_transition = shuffled_transition_values[val_tensor]
            val_prediction = _program_predictions(
                model,
                name,
                state_values[val_tensor],
                val_transition,
                [transition_ids[index] for index in validation_indices],
            )
            metrics = _tensor_metrics(val_prediction, target[val_tensor])
        history.append(
            {
                "epoch": epoch,
                "train_loss": statistics.fmean(losses),
                "validation": metrics,
            }
        )
        if metrics["mse"] < best_loss - 1.0e-9:
            best_loss = metrics["mse"]
            best_epoch = epoch
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
        if epoch in checkpoint_epochs:
            atomic_torch_save(
                {
                    "format": "tensor_program_checkpoint_7df_v1",
                    "name": name,
                    "seed": seed,
                    "epoch": epoch,
                    "model_state_dict": {
                        key: value.detach().cpu()
                        for key, value in model.state_dict().items()
                    },
                    "optimizer_state_dict": optimizer.state_dict(),
                    "history": history,
                },
                output_dir / "checkpoints" / f"epoch_{epoch:03d}.pt",
            )
        if epoch - best_epoch >= patience:
            break
    if best_state is None:
        raise RuntimeError(f"No best checkpoint for {name}")
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        train_tensor = torch.tensor(train_indices, device=state_values.device)
        val_tensor = torch.tensor(validation_indices, device=state_values.device)
        train_transition = transition_values[train_tensor]
        val_transition = transition_values[val_tensor]
        if name == "shuffled_transition":
            train_transition = shuffled_transition_values[train_tensor]
            val_transition = shuffled_transition_values[val_tensor]
        train_metrics = _tensor_metrics(
            _program_predictions(
                model,
                name,
                state_values[train_tensor],
                train_transition,
                [transition_ids[index] for index in train_indices],
            ),
            target[train_tensor],
        )
        val_metrics = _tensor_metrics(
            _program_predictions(
                model,
                name,
                state_values[val_tensor],
                val_transition,
                [transition_ids[index] for index in validation_indices],
            ),
            target[val_tensor],
        )
    checkpoint = output_dir / "best.pt"
    atomic_torch_save(
        {
            "format": "tensor_program_best_checkpoint_7df_v1",
            "name": name,
            "seed": seed,
            "best_epoch": best_epoch,
            "model_state_dict": best_state,
            "train_transition_ids": sorted(
                {transition_ids[index] for index in train_indices}
            ),
            "input_provenance": model.input_provenance()
            if isinstance(model, FactorizedProgramFast)
            else None,
            "source_commit": maybe_git_commit(),
        },
        checkpoint,
    )
    summary = {
        "name": name,
        "seed": seed,
        "best_epoch": best_epoch,
        "epochs_run": len(history),
        "train": train_metrics,
        "validation": val_metrics,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha256_file(checkpoint),
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    return model, summary


def _train_programs(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    a_pairs: Sequence[dict[str, Any]],
    targets: Tensor,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    root = artifact_dir / "program"
    state_cache = torch.load(
        Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/state_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition_cache = torch.load(
        Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/transition_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    state_all = state_cache["representations"]["final_layer"].to(
        device=device, dtype=torch.float32
    )
    transition_all = transition_cache["representations"]["final_layer"].to(
        device=device, dtype=torch.float32
    )
    state_position = {
        str(value): index for index, value in enumerate(state_cache["ordered_ids"])
    }
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_cache["ordered_ids"])
    }
    state_indices = [state_position[str(row["state_example_id"])] for row in a_pairs]
    transition_indices = [
        transition_position[str(row["transition_id"])] for row in a_pairs
    ]
    state_values = state_all[state_indices]
    transition_values = transition_all[transition_indices]
    transition_ids = [str(row["transition_id"]) for row in a_pairs]
    target = targets.to(device=device, dtype=torch.float32)
    train_indices, validation_indices, split = _program_split(a_pairs)
    summaries: dict[str, Any] = {}
    models: dict[str, nn.Module] = {}
    for name in settings["program"]["architectures"]:
        if name == "zero":
            zero = torch.zeros_like(target[validation_indices])
            summaries[name] = {
                "name": name,
                "validation": _tensor_metrics(zero, target[validation_indices]),
                "parameter_count": 0,
            }
            continue
        model, summary = _train_one_program(
            name=str(name),
            seed=int(settings["program"]["primary_seed"]),
            settings=settings,
            state_values=state_values,
            transition_values=transition_values,
            transition_ids=transition_ids,
            target=target,
            train_indices=train_indices,
            validation_indices=validation_indices,
            transition_view_names=transition_cache["view_names"],
            output_dir=root / str(name) / f"seed_{settings['program']['primary_seed']}",
        )
        summaries[str(name)] = summary
        models[str(name)] = model
        attempt.progress(
            status="tensor_program_training",
            latest_validated_checkpoint=summary["checkpoint"],
            completed_architecture=str(name),
        )
    primary = summaries["full_factorized_r16_observation_excluded"]["validation"]
    pair_mlp = summaries["pair_mlp_observation_excluded"]["validation"]
    static = summaries["static_only_observation_excluded"]["validation"]
    shuffled = summaries["shuffled_transition"]["validation"]
    latent_gate = {
        "pair_mlp": {
            "checks": {
                "cosine_gte_0_25": pair_mlp["mean_cosine"] >= 0.25,
                "mse_reduction_gte_0_10": pair_mlp["mse_reduction_vs_zero"] >= 0.10,
            }
        },
        "primary": {
            "checks": {
                "cosine_gte_0_40": primary["mean_cosine"] >= 0.40,
                "mse_reduction_gte_0_20": primary["mse_reduction_vs_zero"] >= 0.20,
                "beats_static_mse": primary["mse"] < static["mse"],
                "beats_shuffled_mse": primary["mse"] < shuffled["mse"],
            }
        },
    }
    for value in latent_gate.values():
        value["passed"] = all(value["checks"].values())
    optional_seed = None
    if latent_gate["primary"]["passed"]:
        _, optional_seed = _train_one_program(
            name="full_factorized_r16_observation_excluded",
            seed=int(settings["program"]["optional_primary_seed"]),
            settings=settings,
            state_values=state_values,
            transition_values=transition_values,
            transition_ids=transition_ids,
            target=target,
            train_indices=train_indices,
            validation_indices=validation_indices,
            transition_view_names=transition_cache["view_names"],
            output_dir=root
            / "full_factorized_r16_observation_excluded"
            / f"seed_{settings['program']['optional_primary_seed']}",
        )
    primary_model = models["full_factorized_r16_observation_excluded"]
    invariance = transition_boundary_invariance(
        primary_model,
        state_views=state_values[: min(8, len(state_values))],
        transition_views=transition_values[: min(8, len(transition_values))],
        observation_permutation=torch.arange(
            min(8, len(transition_values)) - 1, -1, -1, device=device
        ),
    )
    summary = {
        "format": "tensor_program_training_summary_7df_v1",
        "split": split,
        "architectures": summaries,
        "optional_primary_seed": optional_seed,
        "latent_gate": latent_gate,
        "observation_invariance": invariance,
        "qwen_forward_or_backward_in_training_loop": False,
        "passed": latent_gate["pair_mlp"]["passed"],
    }
    atomic_write_json(root / "summary.json", summary)
    if not latent_gate["pair_mlp"]["passed"]:
        raise RuntimeError("PairMLP tensor-space latent gate failed")
    return summary


def _load_program_checkpoint(
    *,
    name: str,
    seed: int,
    settings: Mapping[str, Any],
    transition_view_names: Sequence[str],
    artifact_dir: Path,
    train_transition_ids: Sequence[str],
    device: torch.device,
) -> nn.Module:
    checkpoint = (
        artifact_dir / "program" / name / f"seed_{seed}" / "best.pt"
    )
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model = _program_model(
        name,
        settings=settings,
        transition_view_names=transition_view_names,
        train_transition_ids=payload.get("train_transition_ids", train_transition_ids),
    ).to(device)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    return model


def _project_prediction_ratio(
    *, decoder: LinearDeltaDecoder, z: Tensor, base_norms: Tensor
) -> tuple[Tensor, Tensor]:
    projected = z.detach().clone()
    project_latents_to_output_ratio_(
        projected, decoder, base_norms, max_ratio=1.0
    )
    delta = decoder(projected).view(len(projected), K_TOKENS, -1)
    return projected, delta


def _teacher_forced_gate(
    evaluations: Mapping[str, Mapping[str, Mapping[str, Any]]],
    invariance: Mapping[str, Any],
    selector_hash_unchanged: bool,
) -> dict[str, Any]:
    def summary(cell: str, control: str) -> Mapping[str, Any]:
        return evaluations[cell][control]["summary"]

    def rho(cell: str, control: str) -> float:
        return float(summary(cell, control).get("u_text_vs_u_student_spearman") or 0.0)

    def huber(cell: str, control: str) -> float:
        return float(summary(cell, control)["sequence_utility_huber"]["mean"])

    def reduction(cell: str) -> float:
        return 1.0 - huber(cell, "primary") / max(huber(cell, "zero"), 1.0e-12)

    def sign(cell: str) -> float:
        return float(summary(cell, "primary").get("positive_negative_sign_agreement") or 0.0)

    checks = {
        "B_spearman_gte_0_25": rho("B", "primary") >= 0.25,
        "B_sign_gte_0_60": sign("B") >= 0.60,
        "B_huber_reduction_positive": reduction("B") > 0.0,
        "C_spearman_positive": rho("C", "primary") > 0.0,
        "C_huber_reduction_positive": reduction("C") > 0.0,
        "D_spearman_positive": rho("D", "primary") > 0.0,
        "D_beats_shuffled": huber("D", "primary") < huber("D", "shuffled_transition"),
        "D_beats_memory_swap": huber("D", "primary") < huber("D", "memory_swap"),
        "beats_zero_all_cells": all(
            huber(cell, "primary") < huber(cell, "zero") for cell in "BCDE"
        ),
        "beats_shuffled_all_cells": all(
            huber(cell, "primary") < huber(cell, "shuffled_transition")
            for cell in "BCDE"
        ),
        "beats_static_one_axis": any(
            huber(cell, "primary") < huber(cell, "static") for cell in "BCD"
        ),
        "ratio_lte_1": all(
            float(summary(cell, "primary")["delta_ratio"]["max"]) <= 1.0001
            for cell in "BCDE"
        ),
        "observation_invariance": bool(invariance["static_program_unchanged"])
        and bool(invariance["conditional_basis_unchanged"]),
        "selector_hash_unchanged": bool(selector_hash_unchanged),
    }
    return {
        "checks": checks,
        "huber_reduction": {cell: reduction(cell) for cell in "BCDE"},
        "passed": all(checks.values()),
    }


def _evaluate_programs(
    *,
    backend: Any,
    decoder: LinearDeltaDecoder,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    rows_by_cell: Mapping[str, Sequence[dict[str, Any]]],
    pair_manifest_by_cell: Mapping[str, Sequence[dict[str, Any]]],
    a_pairs: Sequence[dict[str, Any]],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    root = artifact_dir / "teacher_forced"
    root.mkdir(parents=True, exist_ok=True)
    state_cache = torch.load(
        Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/state_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition_cache = torch.load(
        Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/transition_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    device = backend.device
    state_all = state_cache["representations"]["final_layer"].to(
        device=device, dtype=torch.float32
    )
    transition_all = transition_cache["representations"]["final_layer"].to(
        device=device, dtype=torch.float32
    )
    state_position = {
        str(value): index for index, value in enumerate(state_cache["ordered_ids"])
    }
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_cache["ordered_ids"])
    }
    train_transition_ids = [str(row["transition_id"]) for row in a_pairs]
    primary_seeds = [int(settings["program"]["primary_seed"])]
    optional_path = (
        artifact_dir
        / "program/full_factorized_r16_observation_excluded"
        / f"seed_{settings['program']['optional_primary_seed']}"
        / "best.pt"
    )
    if optional_path.exists():
        primary_seeds.append(int(settings["program"]["optional_primary_seed"]))
    primary_models = [
        _load_program_checkpoint(
            name="full_factorized_r16_observation_excluded",
            seed=seed,
            settings=settings,
            transition_view_names=transition_cache["view_names"],
            artifact_dir=artifact_dir,
            train_transition_ids=train_transition_ids,
            device=device,
        )
        for seed in primary_seeds
    ]
    models = {
        name: _load_program_checkpoint(
            name=name,
            seed=int(settings["program"]["primary_seed"]),
            settings=settings,
            transition_view_names=transition_cache["view_names"],
            artifact_dir=artifact_dir,
            train_transition_ids=train_transition_ids,
            device=device,
        )
        for name in (
            "static_only_observation_excluded",
            "pair_mlp_observation_excluded",
            "free_id",
            "full_factorized_r16_action_plus_outcome",
        )
    }
    evaluations: dict[str, dict[str, Any]] = {}
    for cell in "BCDE":
        rows = list(rows_by_cell[cell])
        manifest = list(pair_manifest_by_cell[cell])
        state_indices = [state_position[str(row["state_example_id"])] for row in manifest]
        transition_indices = [
            transition_position[str(row["transition_id"])] for row in manifest
        ]
        state_values = state_all[state_indices]
        transition_values = transition_all[transition_indices]
        transition_ids = [str(row["transition_id"]) for row in manifest]
        permutation = sorted(
            range(len(rows)),
            key=lambda index: stable_key(25095, f"shuffle-{cell}", rows[index]["pair_id"]),
        )
        swap = list(range(1, len(rows))) + [0]
        with torch.no_grad():
            primary = torch.stack(
                [model(state_values, transition_values) for model in primary_models]
            ).mean(0)
            predictions = {
                "primary": primary,
                "zero": torch.zeros_like(primary),
                "static": models["static_only_observation_excluded"](
                    state_values, transition_values
                ),
                "pair_mlp": models["pair_mlp_observation_excluded"](
                    state_values, transition_values
                ),
                "free_id": models["free_id"].forward_ids(
                    transition_ids, device=device
                ),
                "action_plus_outcome": models[
                    "full_factorized_r16_action_plus_outcome"
                ](state_values, transition_values),
                "shuffled_transition": torch.stack(
                    [
                        model(state_values, transition_values[permutation])
                        for model in primary_models
                    ]
                ).mean(0),
                "memory_swap": torch.stack(
                    [
                        model(state_values, transition_values[swap])
                        for model in primary_models
                    ]
                ).mean(0),
            }
        base_norms = _precompute_direct_base_norms(
            backend=backend, rows=rows, device=device, k=K_TOKENS
        ).to(device)
        evaluations[cell] = {}
        for control, z in predictions.items():
            _, delta = _project_prediction_ratio(
                decoder=decoder, z=z, base_norms=base_norms
            )
            evaluation = _evaluate_direct_tensor(
                backend=backend,
                rows=rows,
                delta_tensor=delta,
                pair_ids=[str(row["pair_id"]) for row in rows],
                device=device,
                k=K_TOKENS,
                batch_size=1,
                huber_delta=float(settings["pair_latents"]["sequence_huber_delta"]),
                control=f"{cell}_{control}",
            )
            path = root / cell / f"{control}_rows.jsonl"
            write_jsonl(path, evaluation["rows"])
            evaluations[cell][control] = {
                "summary": evaluation["summary"],
                "rows_path": str(path),
                "rows_sha256": sha256_file(path),
            }
        attempt.progress(
            status="teacher_forced_validation",
            completed_cell=cell,
            latest_validated_checkpoint=str(root / cell),
        )
    invariance = _json(artifact_dir / "program/summary.json")[
        "observation_invariance"
    ]
    selector_hash = sha256_file(
        Path(str(settings["parent_exp025c"])) / "selector/ensemble_scores.pt"
    )
    gate = _teacher_forced_gate(
        evaluations,
        invariance,
        selector_hash == str(settings["expected_selector_ensemble_sha256"]),
    )
    summary = {
        "format": "bounded_teacher_forced_program_validation_7df_v1",
        "evaluations": evaluations,
        "gate": gate,
        "primary_seed_count": len(primary_seeds),
        "primary_seeds": primary_seeds,
        "selector_sha256": selector_hash,
        "selector_unchanged": selector_hash
        == str(settings["expected_selector_ensemble_sha256"]),
        "passed": gate["passed"],
    }
    atomic_write_json(root / "summary.json", summary)
    return summary


def _load_pair_manifests(artifact_dir: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        cell: _file_rows(artifact_dir / "preflight" / f"pairs_{cell}.jsonl")
        for cell in "ABCDE"
    }


def _phase_data_hashes(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, str]:
    paths = {
        "config_preflight": artifact_dir / "preflight_summary.json",
        "run_manifest": artifact_dir / "run_manifest.json",
        "state_multiview": Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/state_multiview.pt",
        "transition_multiview": Path(str(settings["parent_exp025c"]))
        / "representation_cache/multiview/transition_multiview.pt",
        "selector": Path(str(settings["parent_exp025c"]))
        / "selector/ensemble_scores.pt",
        "direct_u112": Path(str(settings["decoder"]["source_checkpoint"])),
    }
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing fast GPU inputs: {missing}")
    return {name: sha256_file(path) for name, path in paths.items()}


def _final_report(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# EXP-025D-Fast teacher-forced result",
            "",
            f"- run UUID: `{summary['run_uuid']}`",
            f"- source commit: `{summary['source_commit']}`",
            f"- prefix path: `{summary['prefix_cache']['selected_training_path']}`",
            f"- decoder passed: `{summary['decoder']['passed']}`",
            f"- pair targets stable: `{summary['pair_latents']['stability']['passed']}`",
            f"- tensor program gate: `{summary['program']['latent_gate']}`",
            f"- teacher-forced gate: `{summary['teacher_forced']['gate']['passed']}`",
            "",
        ]
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7df"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    preflight = _json(args.artifact_dir / "preflight_summary.json")
    if preflight["status"] != "completed_ready_for_gpu":
        raise RuntimeError("Fast GPU launch is not authorized by the preflight")
    if not bool(preflight["runtime_projection"]["automatic_launch_allowed"]):
        raise RuntimeError("Expected H100 runtime exceeds the automatic launch threshold")
    data_hashes = _phase_data_hashes(settings, args.artifact_dir)
    run_started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="gpu_teacher_decoder_latent_program_teacher_forced",
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
        pair_manifests = _load_pair_manifests(args.artifact_dir)
        unique_pairs = {
            str(row["pair_id"]): row
            for rows in pair_manifests.values()
            for row in rows
        }
        ordered_pairs = sorted(unique_pairs.values(), key=lambda row: str(row["pair_id"]))
        examples = load_decision_examples(
            Path(str(settings["reconciled_corpus_dir"])) / "decision_examples.jsonl"
        )
        transitions_list = _file_rows(
            Path(str(settings["parent_exp025b"]))
            / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
        transitions = {
            str(row["transition_id"]): row
            for row in transitions_list
            if str(row["transition_id"])
            in {str(pair["transition_id"]) for pair in ordered_pairs}
        }
        backend = _build_backend(cfg)
        contexts, index_by_state = _context_builder(
            tokenizer=backend.tokenizer,
            examples=examples,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        teacher_rows = _build_teacher_cache(
            backend=backend,
            cfg=cfg,
            settings=settings,
            artifact_dir=args.artifact_dir,
            examples=examples,
            contexts=contexts,
            index_by_state=index_by_state,
            transitions=transitions,
            ordered_transition_ids=sorted(
                str(row["transition_id"])
                for row in transitions_list
                if str(row.get("parent_task_id"))
                in {
                    str(value)
                    for value in _json(
                        Path(str(settings["reconciled_corpus_dir"]))
                        / "train_validation_task_manifest.json"
                    )["train_task_ids"]
                }
            ),
            pairs=ordered_pairs,
            attempt=attempt,
        )
        tokenized = _build_tokenized_rows(
            backend=backend,
            examples=examples,
            response_rows=teacher_rows,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["teacher_cache"]["context_limit"]),
        )
        try:
            prefix = _prefix_equivalence(
                backend=backend,
                rows=tokenized,
                device=backend.device,
                settings=settings,
                artifact_dir=args.artifact_dir,
            )
        except Exception as error:
            prefix = {
                "format": "prefix_kv_equivalence_7df_v1",
                "representative_pair_count": 0,
                "passed": False,
                "selected_training_path": "full_forward",
                "fallback_reason": "prefix_equivalence_error",
                "error_type": type(error).__name__,
                "error": str(error),
                "timebox_respected": True,
            }
            atomic_write_json(
                args.artifact_dir / "prefix_cache_equivalence.json", prefix
            )
            gc.collect()
            torch.cuda.empty_cache()
        if args.stop_after == "teacher":
            return
        decoder, decoder_summary = _repair_decoder(
            backend=backend,
            cfg=cfg,
            settings=settings,
            artifact_dir=args.artifact_dir,
            examples=examples,
            prefix_enabled=bool(prefix["passed"]),
            attempt=attempt,
        )
        if args.stop_after == "decoder":
            return
        canonical_latents, latent_summary = _optimize_canonical_latents(
            backend=backend,
            decoder=decoder,
            rows=tokenized,
            settings=settings,
            artifact_dir=args.artifact_dir,
            prefix_enabled=bool(prefix["passed"]),
            attempt=attempt,
            run_started=run_started,
        )
        if args.stop_after == "latents":
            return
        del backend, decoder
        gc.collect()
        torch.cuda.empty_cache()
        pair_position = {
            str(pair_id): index
            for index, pair_id in enumerate(
                torch.load(
                    args.artifact_dir / "pair_latents/canonical_targets.pt",
                    map_location="cpu",
                    weights_only=False,
                )["pair_ids"]
            )
        }
        a_pairs = pair_manifests["A"]
        a_targets = torch.stack(
            [canonical_latents[pair_position[str(row["pair_id"])]] for row in a_pairs]
        )
        program_summary = _train_programs(
            settings=settings,
            artifact_dir=args.artifact_dir,
            a_pairs=a_pairs,
            targets=a_targets,
            attempt=attempt,
        )
        if args.stop_after == "program":
            return
        gc.collect()
        torch.cuda.empty_cache()
        backend = _build_backend(cfg)
        decoder_payload = torch.load(
            args.artifact_dir / "decoder/repaired_rank128_decoder.pt",
            map_location="cpu",
            weights_only=False,
        )
        decoder = LinearDeltaDecoder(
            LATENT_DIM, K_TOKENS * int(backend.model.config.hidden_size)
        ).to(backend.device)
        decoder.load_state_dict(decoder_payload["decoder_state_dict"])
        decoder.eval()
        for parameter in decoder.parameters():
            parameter.requires_grad_(False)
        tokenized_by_id = {str(row["pair_id"]): row for row in tokenized}
        rows_by_cell = {
            cell: [tokenized_by_id[str(row["pair_id"])] for row in pair_manifests[cell]]
            for cell in "BCDE"
        }
        teacher_forced = _evaluate_programs(
            backend=backend,
            decoder=decoder,
            settings=settings,
            artifact_dir=args.artifact_dir,
            rows_by_cell=rows_by_cell,
            pair_manifest_by_cell=pair_manifests,
            a_pairs=a_pairs,
            attempt=attempt,
        )
        summary = {
            "format": RUN_FORMAT,
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "logical_pair_count": sum(len(rows) for rows in pair_manifests.values()),
            "unique_pair_count": len(ordered_pairs),
            "teacher_cache": _json(args.artifact_dir / "teacher_cache/summary.json"),
            "prefix_cache": prefix,
            "decoder": decoder_summary,
            "pair_latents": latent_summary,
            "program": program_summary,
            "teacher_forced": teacher_forced,
            "actual_h100_hours_through_teacher_forced": (
                time.perf_counter() - run_started
            )
            / 3600.0,
            "one_step_authorized": bool(teacher_forced["gate"]["passed"]),
            "completed_at_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "teacher_forced_summary.json", summary)
        atomic_write_text(args.artifact_dir / "teacher_forced_report.md", _final_report(summary))
        attempt.progress(
            status="teacher_forced_completed",
            latest_validated_checkpoint=str(
                args.artifact_dir / "teacher_forced_summary.json"
            ),
            teacher_forced_gate_passed=bool(teacher_forced["gate"]["passed"]),
        )
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
