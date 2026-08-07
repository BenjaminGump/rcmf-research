from __future__ import annotations

import argparse
import copy
from collections import defaultdict
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.pair_grounding_5d import (
    PAIR_GROUNDING_VERSION,
    PAIR_RESPONSE_CACHE_VERSION,
    PAIR_RESPONSE_SCORING_DEFINITION,
    POSITIVE_UTILITY_EPS,
    PairGroundingLossWeights,
    PairSelectionConfig,
    SingleMemoryProgramModel,
    add_teacher_delta_fields,
    category_coverage,
    deterministic_memory_folds,
    make_fixed_random_programs,
    paired_bootstrap_ci,
    parameter_count,
    program_geometry,
    representation_program_similarity,
    select_stratified_pair_set,
    summarize_pair_eval_rows,
    validate_pair_response_cache,
)
from rcmf.training.addressing_4b import distribution, mean_std
from rcmf.training.stage_c1 import load_teacher_rows, sparse_bucket_kl
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.build_stage_c1_response_cache import _build_position_rows, _score_target_logits
from scripts.run_raw_text_teacher_pilot import (
    TEACHER_MEMORY_SECTION_VERSION,
    _context_limit_for_backend,
    _target_token_ids,
    _token_ids,
    messages_with_teacher_memory,
)
from scripts.run_stage_c1_signed_program import _load_representation_cache, _initialize_injector, _forward_student


RUN_VERSION = PAIR_GROUNDING_VERSION
PROB_EPS = 1.0e-12


def utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _stage_c1_rows_by_best_pair(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    output: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        key = row.get("best_pair_key")
        if key and row.get("teacher_condition") == "positive_teacher":
            output[str(key)] = row
    return output


def _base_prompt_and_target(
    *,
    tokenizer: Any,
    example: DecisionExample,
    prompt_profile: str,
) -> tuple[str, dict[str, Any], list[int], str, list[dict[str, str]]]:
    messages = _appworld_messages_from_example(example, prompt_profile)
    prompt_text, prompt_metadata = _render_prompt_with_metadata(tokenizer, messages, prompt_profile)
    target_text = _target_suffix(example)
    target_ids = _target_token_ids(tokenizer, example)
    return prompt_text, prompt_metadata, target_ids, target_text, messages


def _reuse_stage_c1_row(
    *,
    selected: dict[str, Any],
    stage_c1_row: dict[str, Any],
    renderer_metadata: dict[str, Any],
    source_commit: str | None,
    model_config_commit_hash: str | None,
    context_limit: int,
) -> dict[str, Any]:
    positions = add_teacher_delta_fields(copy.deepcopy(stage_c1_row["target_positions"]))
    row = {
        "format": PAIR_RESPONSE_CACHE_VERSION,
        "scoring_definition": PAIR_RESPONSE_SCORING_DEFINITION,
        "cache_source": "reused_stage_c1_best_memory_response_cache",
        "state_index": int(selected["state_index"]),
        "state_example_id": selected["state_example_id"],
        "task_id": selected["task_id"],
        "episode_id": selected["episode_id"],
        "step_id": int(selected["step_id"]),
        "split": selected["split"],
        "memory_stage_index": int(selected["memory_stage_index"]),
        "memory_index": int(selected["memory_index"]),
        "memory_id": selected["memory_id"],
        "memory_task_id": selected["memory_task_id"],
        "memory_episode_id": selected["memory_episode_id"],
        "pair_key": selected["pair_key"],
        "pair_id": selected["pair_id"],
        "selection_category": selected["selection_category"],
        "utility_category": selected["utility_category"],
        "L0": selected["L0"],
        "Lj_text": stage_c1_row["teacher_mean_target_nll"],
        "text_utility": float(selected["L0"]) - float(stage_c1_row["teacher_mean_target_nll"]),
        "baseline_mean_target_nll": stage_c1_row["baseline_mean_target_nll"],
        "teacher_mean_target_nll": stage_c1_row["teacher_mean_target_nll"],
        "prompt_tokens": stage_c1_row["prompt_tokens"],
        "teacher_prompt_tokens": stage_c1_row["teacher_prompt_tokens"],
        "raw_memory_tokens": stage_c1_row["raw_memory_tokens"],
        "target_tokens": stage_c1_row["target_tokens"],
        "total_tokens_with_target": stage_c1_row["total_tokens_with_target"],
        "teacher_total_tokens_with_target": stage_c1_row["teacher_total_tokens_with_target"],
        "context_limit": context_limit,
        "truncated": False,
        "target_sha256": stage_c1_row["target_sha256"],
        "target_token_sha256": stage_c1_row["target_token_sha256"],
        "prompt_sha256": stage_c1_row["prompt_sha256"],
        "teacher_prompt_sha256": stage_c1_row["teacher_prompt_sha256"],
        "memory_text_sha256": stage_c1_row["memory_text_sha256"],
        "target_token_ids": stage_c1_row["target_token_ids"],
        "target_positions": positions,
        "last_user_token_indices": stage_c1_row.get("last_user_token_indices", []),
        "renderer_version": renderer_metadata["renderer_version"],
        "renderer_metadata": renderer_metadata,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "top_k": stage_c1_row.get("top_k", 64),
        "model_name": stage_c1_row["model_name"],
        "checkpoint_identity": stage_c1_row["checkpoint_identity"],
        "model_config_commit_hash": model_config_commit_hash,
        "source_commit_sha": source_commit,
        "source_stage_c1_response_state": stage_c1_row.get("state_example_id"),
        "source_stage_c1_best_pair_key": stage_c1_row.get("best_pair_key"),
        "scoring_timestamp_utc": utc_now(),
    }
    return row


def _score_pair_response_row(
    *,
    backend: Any,
    tokenizer: Any,
    selected: dict[str, Any],
    example: DecisionExample,
    record: MemoryRecord,
    prompt_profile: str,
    renderer_metadata: dict[str, Any],
    source_commit: str | None,
    model_config_commit_hash: str | None,
    context_limit: int,
    top_k: int,
    baseline_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base_prompt, prompt_metadata, target_ids, target_text, base_messages = _base_prompt_and_target(
        tokenizer=tokenizer,
        example=example,
        prompt_profile=prompt_profile,
    )
    state_id = str(selected["state_example_id"])
    baseline = baseline_cache.get(state_id)
    if baseline is None:
        baseline = _score_target_logits(
            backend,
            prompt_text=base_prompt,
            target_ids=target_ids,
            context_limit=context_limit,
            top_k=top_k,
        )
        baseline_cache[state_id] = baseline
    teacher_messages = messages_with_teacher_memory(base_messages, record, prompt_profile)
    teacher_prompt = backend.render_messages(teacher_messages, add_generation_prompt=True)
    teacher = _score_target_logits(
        backend,
        prompt_text=teacher_prompt,
        target_ids=target_ids,
        context_limit=context_limit,
        top_k=top_k,
    )
    positions = add_teacher_delta_fields(_build_position_rows(baseline=baseline, teacher=teacher, top_k=top_k))
    row = {
        "format": PAIR_RESPONSE_CACHE_VERSION,
        "scoring_definition": PAIR_RESPONSE_SCORING_DEFINITION,
        "cache_source": "new_pair_teacher_scoring",
        "state_index": int(selected["state_index"]),
        "state_example_id": state_id,
        "task_id": selected["task_id"],
        "episode_id": selected["episode_id"],
        "step_id": int(selected["step_id"]),
        "split": selected["split"],
        "memory_stage_index": int(selected["memory_stage_index"]),
        "memory_index": int(selected["memory_index"]),
        "memory_id": selected["memory_id"],
        "memory_task_id": selected["memory_task_id"],
        "memory_episode_id": selected["memory_episode_id"],
        "pair_key": selected["pair_key"],
        "pair_id": selected["pair_id"],
        "selection_category": selected["selection_category"],
        "utility_category": selected["utility_category"],
        "L0": selected["L0"],
        "Lj_text": teacher["mean_target_nll"],
        "text_utility": float(baseline["mean_target_nll"]) - float(teacher["mean_target_nll"]),
        "baseline_mean_target_nll": baseline["mean_target_nll"],
        "teacher_mean_target_nll": teacher["mean_target_nll"],
        "prompt_tokens": baseline["prompt_tokens"],
        "teacher_prompt_tokens": teacher["prompt_tokens"],
        "raw_memory_tokens": len(_token_ids(tokenizer, record.experience_text, add_special_tokens=False)),
        "target_tokens": baseline["target_tokens"],
        "total_tokens_with_target": baseline["prompt_tokens"] + baseline["target_tokens"],
        "teacher_total_tokens_with_target": teacher["prompt_tokens"] + teacher["target_tokens"],
        "context_limit": context_limit,
        "truncated": False,
        "target_sha256": sha256_text(target_text),
        "target_token_sha256": sha256_text(",".join(str(item) for item in target_ids)),
        "prompt_sha256": sha256_text(base_prompt),
        "teacher_prompt_sha256": sha256_text(teacher_prompt),
        "memory_text_sha256": sha256_text(record.experience_text),
        "target_token_ids": target_ids,
        "target_positions": positions,
        "last_user_token_indices": prompt_metadata.get("last_user_token_indices", []),
        "renderer_version": renderer_metadata["renderer_version"],
        "renderer_metadata": renderer_metadata,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "top_k": top_k,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": model_config_commit_hash,
        "source_commit_sha": source_commit,
        "scoring_timestamp_utc": utc_now(),
    }
    del teacher["logits"]
    return row


def build_pair_response_cache(
    *,
    backend: Any,
    data_dir: Path,
    labels_dir: Path,
    teacher_cache_dir: Path,
    stage_c1_response_dir: Path | None,
    output_dir: Path,
    prompt_profile: str,
    top_k: int,
    progress_interval_s: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    tokenizer = backend.tokenizer
    context_limit = _context_limit_for_backend(backend)
    source_commit = maybe_git_commit()
    model_hash = getattr(getattr(backend.model, "config", None), "_commit_hash", None)
    output_dir.mkdir(parents=True, exist_ok=True)

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    label_rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    teacher_rows = load_teacher_rows(read_jsonl(teacher_cache_dir / "teacher_cache_full_rows.jsonl"))
    selection_config = PairSelectionConfig()
    selected_pairs, selection_summary = select_stratified_pair_set(label_rows, memory_bank, config=selection_config)
    write_jsonl(output_dir / "selected_pairs.jsonl", selected_pairs)
    atomic_write_json(output_dir / "pair_selection_summary.json", selection_summary)

    rows_path = output_dir / "pair_response_cache.jsonl"
    completed = {
        str(row["pair_id"]): row
        for row in read_jsonl(rows_path)
        if row.get("format") == PAIR_RESPONSE_CACHE_VERSION
    }
    stage_c1_by_pair = _stage_c1_rows_by_best_pair(
        None if stage_c1_response_dir is None else stage_c1_response_dir / "response_cache.jsonl"
    )
    renderer_metadata = appworld_renderer_metadata(prompt_profile, add_generation_prompt=True)
    baseline_cache: dict[str, dict[str, Any]] = {}
    last_state_id: str | None = None
    reuse_count = 0
    new_count = 0
    last_progress = 0.0
    compute_order = sorted(selected_pairs, key=lambda item: (str(item["state_example_id"]), int(item["memory_stage_index"]), str(item["selection_category"])))
    for index, selected in enumerate(compute_order, start=1):
        pid = str(selected["pair_id"])
        if pid in completed:
            continue
        current_state_id = str(selected["state_example_id"])
        if current_state_id != last_state_id:
            baseline_cache.clear()
            last_state_id = current_state_id
        stage_row = stage_c1_by_pair.get(str(selected["pair_key"]))
        if stage_row is not None and str(stage_row.get("memory_text_sha256")) == str(selected.get("memory_text_sha256")):
            row = _reuse_stage_c1_row(
                selected=selected,
                stage_c1_row=stage_row,
                renderer_metadata=renderer_metadata,
                source_commit=source_commit,
                model_config_commit_hash=model_hash,
                context_limit=context_limit,
            )
            reuse_count += 1
        else:
            example = examples[int(selected["state_index"])]
            record = records[int(selected["memory_index"])]
            row = _score_pair_response_row(
                backend=backend,
                tokenizer=tokenizer,
                selected=selected,
                example=example,
                record=record,
                prompt_profile=prompt_profile,
                renderer_metadata=renderer_metadata,
                source_commit=source_commit,
                model_config_commit_hash=model_hash,
                context_limit=context_limit,
                top_k=top_k,
                baseline_cache=baseline_cache,
            )
            new_count += 1
        completed[pid] = row
        append_jsonl(rows_path, row)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        now = time.perf_counter()
        if now - last_progress >= progress_interval_s:
            last_progress = now
            print(
                f"pair-response-cache progress {len(completed)}/{len(selected_pairs)} "
                f"reuse={reuse_count} new={new_count} elapsed={(now - started) / 3600.0:.2f}h current={pid}",
                flush=True,
            )
            atomic_write_json(
                output_dir / "pair_cache_progress.json",
                {
                    "completed": len(completed),
                    "total": len(selected_pairs),
                    "reused_stage_c1_rows": reuse_count,
                    "newly_scored_rows": new_count,
                    "elapsed_s": now - started,
                    "current_pair_id": pid,
                },
            )
    rows = [completed[str(row["pair_id"])] for row in selected_pairs]
    write_jsonl(rows_path, rows)
    validation = validate_pair_response_cache(rows, selected_pairs=selected_pairs, teacher_rows=teacher_rows)
    metrics = {
        "teacher_utility": summarize_pair_eval_rows(
            [
                {
                    "u_text": row["text_utility"],
                    "u_program": row["text_utility"],
                    "behavioral_delta_huber": 0.0,
                    "behavioral_delta_mse": 0.0,
                    "sparse_teacher_kl": 0.0,
                    "student_target_nll": row["teacher_mean_target_nll"],
                    "target_token_delta_error": 0.0,
                    "utility_category": row["utility_category"],
                }
                for row in rows
            ]
        ),
        "category_coverage": category_coverage(rows),
    }
    summary = {
        "format": PAIR_RESPONSE_CACHE_VERSION,
        "scoring_definition": PAIR_RESPONSE_SCORING_DEFINITION,
        "source_commit": source_commit,
        "output_dir": str(output_dir),
        "data_dir": str(data_dir),
        "labels_dir": str(labels_dir),
        "teacher_cache_dir": str(teacher_cache_dir),
        "stage_c1_response_dir": None if stage_c1_response_dir is None else str(stage_c1_response_dir),
        "pair_count": len(rows),
        "selected_pair_count": len(selected_pairs),
        "reused_stage_c1_rows": reuse_count,
        "newly_scored_rows": new_count,
        "existing_completed_rows": sum(1 for row in completed.values() if row.get("format") == PAIR_RESPONSE_CACHE_VERSION) - reuse_count - new_count,
        "context_limit": context_limit,
        "top_k": top_k,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": model_hash,
        "selection_summary": selection_summary,
        "metrics": metrics,
        "runtime_s": time.perf_counter() - started,
        "cache_size_bytes": rows_path.stat().st_size if rows_path.exists() else 0,
        "pair_response_cache_sha256": sha256_file(rows_path),
        "validation": validation,
    }
    atomic_write_json(output_dir / "pair_response_cache_summary.json", summary)
    atomic_write_json(output_dir / "pair_response_cache_validation.json", validation)
    atomic_write_text(output_dir / "pair_response_cache_report.md", _pair_cache_report(summary))
    if not validation["passed"]:
        raise SystemExit(f"Pair-response cache validation failed: {validation['errors_first_50']}")
    return rows, summary


def _pair_cache_report(summary: dict[str, Any]) -> str:
    validation = summary["validation"]
    selection = summary["selection_summary"]
    lines = [
        "# Milestone 5D Pair-Level Teacher-Response Cache",
        "",
        f"- format: `{summary['format']}`",
        f"- scoring definition: `{summary['scoring_definition']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- validation passed: `{validation['passed']}`",
        f"- selected pairs: `{summary['selected_pair_count']}`",
        f"- reused Stage-C1 rows: `{summary['reused_stage_c1_rows']}`",
        f"- newly scored rows: `{summary['newly_scored_rows']}`",
        f"- runtime seconds: `{summary['runtime_s']:.2f}`",
        "",
        "## Selection Coverage",
        "",
        f"```json\n{json.dumps(selection['by_split_category'], indent=2, sort_keys=True)}\n```",
        "",
        f"- missing category slots: `{selection['missing_category_slot_count']}`",
        "",
        "## Validation",
        "",
        f"```json\n{json.dumps(validation, indent=2, sort_keys=True)}\n```",
    ]
    return "\n".join(lines) + "\n"


def _build_tokenized_pair_rows(
    *,
    backend: Any,
    examples: list[DecisionExample],
    pair_rows: Sequence[dict[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> list[dict[str, Any]]:
    tokenizer = backend.tokenizer
    pad_id = int(getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", 0) or 0)
    rows = []
    for pair in pair_rows:
        example = examples[int(pair["state_index"])]
        prompt_text, prompt_metadata, target_ids, target_text, _ = _base_prompt_and_target(
            tokenizer=tokenizer,
            example=example,
            prompt_profile=prompt_profile,
        )
        prompt_ids = _token_ids(tokenizer, prompt_text, add_special_tokens=False)
        if target_ids != [int(item) for item in pair["target_token_ids"]]:
            raise ValueError(f"target ids differ from pair cache: {pair['pair_id']}")
        if sha256_text(target_text) != str(pair["target_sha256"]):
            raise ValueError(f"target text hash differs from pair cache: {pair['pair_id']}")
        full_ids = prompt_ids + target_ids
        if len(full_ids) > context_limit:
            raise ValueError(f"student prompt+target exceeds context for {pair['pair_id']}: {len(full_ids)}")
        rows.append(
            {
                "format": "stage_c_pair_tokenized_row_5d_v1",
                "pair_id": pair["pair_id"],
                "pair_key": pair["pair_key"],
                "state_index": int(pair["state_index"]),
                "state_example_id": pair["state_example_id"],
                "task_id": pair["task_id"],
                "episode_id": pair["episode_id"],
                "step_id": int(pair["step_id"]),
                "split": pair["split"],
                "memory_stage_index": int(pair["memory_stage_index"]),
                "memory_index": int(pair["memory_index"]),
                "memory_id": pair["memory_id"],
                "memory_task_id": pair["memory_task_id"],
                "selection_category": pair["selection_category"],
                "utility_category": pair["utility_category"],
                "u_text": float(pair["text_utility"]),
                "L0": float(pair["baseline_mean_target_nll"]),
                "Lj_text": float(pair["teacher_mean_target_nll"]),
                "response_cache": pair,
                "input_ids": full_ids,
                "labels": [-100] * len(prompt_ids) + target_ids,
                "attention_mask": [1] * len(full_ids),
                "target_len": len(target_ids),
                "prompt_len": len(prompt_ids),
                "last_user_token_indices": list(prompt_metadata.get("last_user_token_indices", [])),
                "pad_token_id": pad_id,
            }
        )
    return rows


def _collate_pair_rows(rows: Sequence[dict[str, Any]], *, device: torch.device) -> dict[str, Any]:
    if not rows:
        raise ValueError("empty pair batch")
    max_len = max(len(row["input_ids"]) for row in rows)
    pad_id = int(rows[0]["pad_token_id"])
    input_ids = torch.full((len(rows), max_len), pad_id, dtype=torch.long, device=device)
    labels = torch.full((len(rows), max_len), -100, dtype=torch.long, device=device)
    attention_mask = torch.zeros((len(rows), max_len), dtype=torch.long, device=device)
    max_user = max(1, max(len(row["last_user_token_indices"]) for row in rows))
    injection_indices = torch.full((len(rows), max_user), -1, dtype=torch.long, device=device)
    memory_stage_indices = torch.empty(len(rows), dtype=torch.long, device=device)
    for row_index, row in enumerate(rows):
        length = len(row["input_ids"])
        input_ids[row_index, :length] = torch.tensor(row["input_ids"], dtype=torch.long, device=device)
        labels[row_index, :length] = torch.tensor(row["labels"], dtype=torch.long, device=device)
        attention_mask[row_index, :length] = 1
        memory_stage_indices[row_index] = int(row["memory_stage_index"])
        indices = row["last_user_token_indices"]
        if indices:
            injection_indices[row_index, : len(indices)] = torch.tensor(indices, dtype=torch.long, device=device)
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": attention_mask,
        "injection_token_indices": injection_indices,
        "memory_stage_indices": memory_stage_indices,
        "target_lengths": [int(row["target_len"]) for row in rows],
        "response_rows": [row["response_cache"] for row in rows],
        "pair_rows": list(rows),
    }


def _custom_huber(error: Tensor, *, delta: float) -> Tensor:
    abs_error = error.abs()
    d = torch.as_tensor(float(delta), device=error.device, dtype=error.dtype)
    return torch.where(abs_error <= d, 0.5 * error.pow(2) / d.clamp_min(1.0e-12), abs_error - 0.5 * d)


def _student_pair_terms(
    logits: Tensor,
    labels: Tensor,
    response_rows: Sequence[dict[str, Any]],
    *,
    target_lengths: Sequence[int],
    huber_delta: float,
    return_rows: bool = False,
    pair_rows: Sequence[dict[str, Any]] | None = None,
) -> tuple[dict[str, Tensor], list[dict[str, Any]]]:
    losses: dict[str, list[Tensor]] = defaultdict(list)
    per_pair_output: list[dict[str, Any]] = []
    target_mask = labels[..., 1:].ne(-100)
    target_labels = labels[..., 1:][target_mask].to(logits.device)
    if target_labels.numel() != logits.shape[0]:
        raise ValueError(f"logit/label target count mismatch {logits.shape[0]} != {target_labels.numel()}")
    cursor = 0
    for row_index, (response, target_len) in enumerate(zip(response_rows, target_lengths)):
        target_len = int(target_len)
        row_logits = logits[cursor : cursor + target_len].to(torch.float32)
        row_target_labels = target_labels[cursor : cursor + target_len]
        row_log_probs = F.log_softmax(row_logits, dim=-1)
        row_nll_tokens = -row_log_probs[torch.arange(target_len, device=logits.device), row_target_labels]
        row_delta_losses = []
        row_delta_sq = []
        row_teacher_kl = []
        row_target_delta_errors = []
        row_student_delta_sq = []
        row_delta_student_flat: list[float] = []
        row_delta_teacher_flat: list[float] = []
        for pos, item in enumerate(response["target_positions"]):
            position_logits = row_logits[pos].to(torch.float64)
            logsumexp = torch.logsumexp(position_logits, dim=-1)
            union_ids = torch.tensor(item["union_token_ids"], dtype=torch.long, device=logits.device)
            student_log_probs = position_logits[union_ids] - logsumexp
            union_prob = student_log_probs.exp().sum().clamp(min=0.0, max=1.0 - 1.0e-8)
            student_other_log_prob = torch.log1p(-union_prob)
            baseline_log_probs = torch.tensor(item["baseline_union_logprobs"], dtype=torch.float64, device=logits.device)
            teacher_log_probs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_teacher = torch.tensor(item["delta_teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_student = student_log_probs - baseline_log_probs
            baseline_other = torch.tensor(float(item["baseline_other_logprob"]), dtype=torch.float64, device=logits.device)
            teacher_other = torch.tensor(float(item["teacher_other_logprob"]), dtype=torch.float64, device=logits.device)
            delta_student_other = student_other_log_prob - baseline_other
            delta_teacher_other = teacher_other - baseline_other
            delta_error = torch.cat([(delta_student - delta_teacher), (delta_student_other - delta_teacher_other).view(1)])
            row_delta_losses.append(_custom_huber(delta_error.to(torch.float32), delta=huber_delta).mean())
            row_delta_sq.append(delta_error.to(torch.float32).pow(2).mean())
            row_student_delta_sq.append(torch.cat([delta_student.to(torch.float32), delta_student_other.to(torch.float32).view(1)]).pow(2).mean())
            row_teacher_kl.append(
                sparse_bucket_kl(
                    student_log_probs,
                    student_other_log_prob,
                    teacher_log_probs,
                    torch.tensor(float(item["teacher_other_probability"]), dtype=torch.float64, device=logits.device),
                )
            )
            target_id = int(item["target_token_id"])
            target_log_prob = row_log_probs[pos, target_id].to(torch.float64)
            target_delta_student = target_log_prob - float(item["baseline_target_logprob"])
            target_delta_teacher = float(item["teacher_target_logprob"]) - float(item["baseline_target_logprob"])
            row_target_delta_errors.append((target_delta_student - target_delta_teacher).to(torch.float32).abs())
            if return_rows:
                row_delta_student_flat.extend([float(value) for value in delta_student.detach().cpu().tolist()])
                row_delta_student_flat.append(float(delta_student_other.detach().cpu()))
                row_delta_teacher_flat.extend([float(value) for value in delta_teacher.detach().cpu().tolist()])
                row_delta_teacher_flat.append(float(delta_teacher_other.detach().cpu()))
        cursor += target_len
        delta_huber = torch.stack(row_delta_losses).mean()
        delta_mse = torch.stack(row_delta_sq).mean()
        teacher_kl = torch.stack(row_teacher_kl).mean()
        target_delta_error = torch.stack(row_target_delta_errors).mean()
        target_nll = row_nll_tokens.mean()
        utility = float(response["text_utility"])
        category = str(response["utility_category"])
        losses["delta_huber"].append(delta_huber)
        losses["delta_mse"].append(delta_mse)
        losses["teacher_kl"].append(teacher_kl)
        losses["target_delta_error"].append(target_delta_error)
        losses["target_nll"].append(target_nll)
        if utility > POSITIVE_UTILITY_EPS:
            losses["positive_ce"].append(target_nll)
        if category == "neutral":
            losses["neutral_preservation"].append(torch.stack(row_student_delta_sq).mean())
        if return_rows:
            from rcmf.training.pair_grounding_5d import _pearson, spearman  # local import keeps test surface small

            pair_row = pair_rows[row_index] if pair_rows is not None else {}
            per_pair_output.append(
                {
                    "pair_id": response["pair_id"],
                    "pair_key": response["pair_key"],
                    "state_example_id": response["state_example_id"],
                    "memory_id": response["memory_id"],
                    "split": response["split"],
                    "selection_category": response["selection_category"],
                    "utility_category": response["utility_category"],
                    "memory_stage_index": int(response["memory_stage_index"]),
                    "u_text": utility,
                    "L0": float(response["baseline_mean_target_nll"]),
                    "teacher_Lj_text": float(response["teacher_mean_target_nll"]),
                    "student_target_nll": float(target_nll.detach().cpu()),
                    "u_program": float(response["baseline_mean_target_nll"]) - float(target_nll.detach().cpu()),
                    "sparse_teacher_kl": float(teacher_kl.detach().cpu()),
                    "behavioral_delta_huber": float(delta_huber.detach().cpu()),
                    "behavioral_delta_mse": float(delta_mse.detach().cpu()),
                    "behavioral_delta_pearson": _pearson(row_delta_teacher_flat, row_delta_student_flat),
                    "behavioral_delta_spearman": spearman(row_delta_teacher_flat, row_delta_student_flat),
                    "target_token_delta_error": float(target_delta_error.detach().cpu()),
                    "prompt_tokens": response["prompt_tokens"],
                    "teacher_prompt_tokens": response["teacher_prompt_tokens"],
                    "raw_memory_tokens": response["raw_memory_tokens"],
                    "target_tokens": response["target_tokens"],
                    "source_state_task_id": pair_row.get("task_id"),
                    "memory_task_id": pair_row.get("memory_task_id"),
                }
            )
    if cursor != logits.shape[0]:
        raise ValueError(f"target logits row count mismatch: cursor={cursor} logits={logits.shape[0]}")
    out = {}
    for key in ("delta_huber", "delta_mse", "teacher_kl", "target_delta_error", "target_nll", "positive_ce", "neutral_preservation"):
        values = losses.get(key, [])
        out[key] = torch.stack(values).mean() if values else logits.sum() * 0.0
    return out, per_pair_output


def _select_programs_for_rows(
    *,
    model: SingleMemoryProgramModel,
    memory_representations: Tensor,
    rows: Sequence[dict[str, Any]],
    control: str,
    seed: int,
    stage_to_model_index: dict[int, int] | None = None,
    heldout_fallback: str = "mean",
) -> tuple[Tensor, Tensor]:
    device = memory_representations.device
    programs = model.programs(memory_representations)
    if control == "fixed_random_program":
        programs = make_fixed_random_programs(programs.shape[0], programs.shape[1], seed=seed + 17000).to(device)
    elif control == "shuffled_program":
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + 18000)
        order = torch.randperm(programs.shape[0], generator=generator).to(device)
        programs = programs.index_select(0, order)
    elif control == "mean_program":
        programs = programs.mean(dim=0, keepdim=True).expand_as(programs)
    elif control == "zero_program":
        programs = torch.zeros_like(programs)

    z_rows = []
    selected_model_indices = []
    mean_program = programs.mean(dim=0)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 19000)
    random_fallbacks = make_fixed_random_programs(max(1, len(rows)), programs.shape[1], seed=seed + 19000).to(device)
    for row_index, row in enumerate(rows):
        stage_index = int(row["memory_stage_index"])
        if control == "memory_swap":
            stage_index = (stage_index + 17) % max(1, model.memory_count)
        model_index = stage_index
        if stage_to_model_index is not None:
            if stage_index in stage_to_model_index:
                model_index = stage_to_model_index[stage_index]
            else:
                selected_model_indices.append(-1)
                if heldout_fallback == "zero":
                    z_rows.append(torch.zeros_like(mean_program))
                elif heldout_fallback == "random":
                    z_rows.append(random_fallbacks[row_index])
                else:
                    z_rows.append(mean_program)
                continue
        selected_model_indices.append(int(model_index))
        z_rows.append(programs[int(model_index)])
    return torch.stack(z_rows, dim=0), torch.tensor(selected_model_indices, dtype=torch.long, device=device)


def _loss_for_pair_batch(
    *,
    backend: Any,
    model: SingleMemoryProgramModel,
    injector: AdditiveTokenMemoryInjector,
    rows: Sequence[dict[str, Any]],
    memory_representations: Tensor,
    weights: PairGroundingLossWeights,
    device: torch.device,
    seed: int,
    control: str = "correct",
    stage_to_model_index: dict[int, int] | None = None,
    heldout_fallback: str = "mean",
) -> tuple[Tensor, dict[str, Any]]:
    batch = _collate_pair_rows(rows, device=device)
    z, selected_model_indices = _select_programs_for_rows(
        model=model,
        memory_representations=memory_representations,
        rows=rows,
        control=control,
        seed=seed,
        stage_to_model_index=stage_to_model_index,
        heldout_fallback=heldout_fallback,
    )
    student = _forward_student(backend=backend, injector=injector, batch=batch, memory_z=z)
    terms, _ = _student_pair_terms(
        student["target_logits"],
        batch["labels"],
        batch["response_rows"],
        target_lengths=batch["target_lengths"],
        huber_delta=weights.huber_delta,
    )
    ratio_penalty = F.relu(student["delta_ratio"].to(torch.float32) - float(weights.ratio_target)).pow(2)
    total = (
        weights.delta_huber * terms["delta_huber"]
        + weights.teacher_kl * terms["teacher_kl"]
        + weights.positive_ce * terms["positive_ce"]
        + weights.neutral_preservation * terms["neutral_preservation"]
        + weights.ratio_penalty * ratio_penalty
    )
    return total, {
        "loss": float(total.detach().cpu()),
        "delta_huber": float(terms["delta_huber"].detach().cpu()),
        "teacher_kl": float(terms["teacher_kl"].detach().cpu()),
        "positive_ce": float(terms["positive_ce"].detach().cpu()),
        "neutral_preservation": float(terms["neutral_preservation"].detach().cpu()),
        "ratio_penalty": float(ratio_penalty.detach().cpu()),
        "delta_norm": float(student["delta_norm"].detach().cpu()),
        "delta_ratio": float(student["delta_ratio"].detach().cpu()),
        "selected_model_index_missing": int((selected_model_indices < 0).sum().detach().cpu()),
    }


def _grad_norms(module: nn.Module) -> dict[str, float]:
    output: dict[str, float] = {}
    for name, param in module.named_parameters():
        if param.grad is not None:
            output[name] = float(param.grad.detach().to(torch.float32).norm().cpu())
    return output


def _batch_indices(length: int, *, batch_size: int, rng: random.Random) -> list[list[int]]:
    indices = list(range(length))
    rng.shuffle(indices)
    return [indices[start : start + batch_size] for start in range(0, length, batch_size)]


def _train_pair_epoch(
    *,
    backend: Any,
    model: SingleMemoryProgramModel,
    injector: AdditiveTokenMemoryInjector,
    rows: list[dict[str, Any]],
    memory_representations: Tensor,
    optimizer: torch.optim.Optimizer,
    weights: PairGroundingLossWeights,
    device: torch.device,
    seed: int,
    batch_size: int,
    epoch: int,
    max_steps: int | None = None,
    stage_to_model_index: dict[int, int] | None = None,
) -> dict[str, Any]:
    model.train()
    injector.train()
    metrics = []
    rng = random.Random(seed * 1_000_000 + epoch)
    steps = 0
    for batch_index in _batch_indices(len(rows), batch_size=batch_size, rng=rng):
        batch_rows = [rows[index] for index in batch_index]
        loss, report = _loss_for_pair_batch(
            backend=backend,
            model=model,
            injector=injector,
            rows=batch_rows,
            memory_representations=memory_representations,
            weights=weights,
            device=device,
            seed=seed,
            stage_to_model_index=stage_to_model_index,
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        trainable = [param for module in (model, injector) for param in module.parameters() if param.requires_grad]
        if trainable:
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        optimizer.step()
        metrics.append(report)
        steps += 1
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if max_steps is not None and steps >= max_steps:
            break
    keys = ("loss", "delta_huber", "teacher_kl", "positive_ce", "neutral_preservation", "ratio_penalty", "delta_norm", "delta_ratio")
    return {"steps": steps, "metrics": {key: mean_std(report[key] for report in metrics if key in report) for key in keys}}


def _evaluate_pair_model(
    *,
    backend: Any,
    model: SingleMemoryProgramModel,
    injector: AdditiveTokenMemoryInjector,
    rows: Sequence[dict[str, Any]],
    memory_representations: Tensor,
    device: torch.device,
    seed: int,
    batch_size: int,
    weights: PairGroundingLossWeights,
    control: str = "correct",
    stage_to_model_index: dict[int, int] | None = None,
    heldout_fallback: str = "mean",
) -> dict[str, Any]:
    model.eval()
    injector.eval()
    out_rows = []
    z_rows = []
    delta_norms = []
    delta_ratios = []
    selected_token_report = None
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch_rows = list(rows[start : start + batch_size])
            batch = _collate_pair_rows(batch_rows, device=device)
            z, selected_model_indices = _select_programs_for_rows(
                model=model,
                memory_representations=memory_representations,
                rows=batch_rows,
                control=control,
                seed=seed,
                stage_to_model_index=stage_to_model_index,
                heldout_fallback=heldout_fallback,
            )
            student = _forward_student(backend=backend, injector=injector, batch=batch, memory_z=z)
            _, pair_outputs = _student_pair_terms(
                student["target_logits"],
                batch["labels"],
                batch["response_rows"],
                target_lengths=batch["target_lengths"],
                huber_delta=weights.huber_delta,
                return_rows=True,
                pair_rows=batch_rows,
            )
            for item, model_index in zip(pair_outputs, selected_model_indices.detach().cpu().tolist()):
                item["control"] = control
                item["selected_model_memory_index"] = int(model_index)
                out_rows.append(item)
            z_rows.append(z.detach().cpu())
            delta_norms.append(float(student["delta_norm"].detach().cpu()))
            delta_ratios.append(float(student["delta_ratio"].detach().cpu()))
            if selected_token_report is None:
                selected = student["memory_metadata"]["selected_token_indices"][0]
                ids = [int(batch["input_ids"][0, index].detach().cpu()) for index in selected if int(index) >= 0]
                selected_token_report = {
                    "selected_token_indices": selected,
                    "selected_token_ids": ids,
                    "selected_token_text": [backend.tokenizer.decode([token_id]) for token_id in ids],
                }
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    z_all = torch.cat(z_rows, dim=0) if z_rows else torch.empty(0, getattr(model, "program_dim", 128))
    return {
        "rows": out_rows,
        "summary": summarize_pair_eval_rows(out_rows),
        "delta_norm": distribution(delta_norms),
        "delta_ratio": distribution(delta_ratios),
        "z_norm": distribution(z_all.norm(dim=1).tolist()) if z_all.numel() else {"count": 0},
        "selected_token_report": selected_token_report,
    }


def _baseline_pair_eval(rows: Sequence[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    out = []
    for row in rows:
        response = row["response_cache"]
        if mode == "teacher_oracle":
            nll = float(response["teacher_mean_target_nll"])
            kl = 0.0
            delta_huber = 0.0
            delta_mse = 0.0
            target_delta_error = 0.0
        elif mode == "bare_qwen_zero_field":
            nll = float(response["baseline_mean_target_nll"])
            kl_values = []
            delta_sq = []
            delta_abs = []
            target_errors = []
            for item in response["target_positions"]:
                teacher_probs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64).exp()
                baseline_logprobs = torch.tensor(item["baseline_union_logprobs"], dtype=torch.float64)
                teacher_logprobs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64)
                other_teacher = torch.tensor(float(item["teacher_other_probability"]), dtype=torch.float64).clamp_min(PROB_EPS)
                other_base = torch.tensor(float(item["baseline_other_probability"]), dtype=torch.float64).clamp_min(PROB_EPS)
                kl_values.append(
                    float((teacher_probs * (teacher_logprobs - baseline_logprobs)).sum().item())
                    + float(other_teacher.item() * (other_teacher.log() - other_base.log()).item())
                )
                deltas = [float(value) for value in item["delta_teacher_union_logprobs"]] + [float(item["delta_teacher_other_logprob"])]
                delta_sq.extend(value * value for value in deltas)
                delta_abs.extend(abs(value) for value in deltas)
                target_errors.append(abs(float(item["teacher_target_logprob"]) - float(item["baseline_target_logprob"])))
            kl = sum(kl_values) / len(kl_values) if kl_values else 0.0
            delta_mse = sum(delta_sq) / len(delta_sq) if delta_sq else 0.0
            delta_huber = sum(delta_abs) / len(delta_abs) if delta_abs else 0.0
            target_delta_error = sum(target_errors) / len(target_errors) if target_errors else 0.0
        else:
            raise ValueError(f"unknown baseline mode: {mode}")
        out.append(
            {
                "pair_id": row["pair_id"],
                "pair_key": row["pair_key"],
                "state_example_id": row["state_example_id"],
                "memory_id": row["memory_id"],
                "split": row["split"],
                "selection_category": row["selection_category"],
                "utility_category": row["utility_category"],
                "memory_stage_index": int(row["memory_stage_index"]),
                "u_text": float(row["u_text"]),
                "L0": float(row["L0"]),
                "teacher_Lj_text": float(row["Lj_text"]),
                "student_target_nll": nll,
                "u_program": float(row["L0"]) - nll,
                "sparse_teacher_kl": kl,
                "behavioral_delta_huber": delta_huber,
                "behavioral_delta_mse": delta_mse,
                "target_token_delta_error": target_delta_error,
                "control": mode,
            }
        )
    return {"rows": out, "summary": summarize_pair_eval_rows(out)}


def _make_pair_model(
    *,
    program_kind: str,
    memory_dim: int,
    memory_count: int,
    program_dim: int,
    seed: int,
    matched_parameter_count: int | None = None,
) -> SingleMemoryProgramModel:
    torch.manual_seed(seed + 5000)
    if program_kind == "fixed_random":
        fixed = make_fixed_random_programs(memory_count, program_dim, seed=seed + 6000)
        return SingleMemoryProgramModel(
            memory_dim=memory_dim,
            memory_count=memory_count,
            program_dim=program_dim,
            program_kind="fixed_random",
            fixed_programs=fixed,
        )
    return SingleMemoryProgramModel(
        memory_dim=memory_dim,
        memory_count=memory_count,
        program_dim=program_dim,
        program_kind=program_kind,
        matched_parameter_count=matched_parameter_count,
    )


def _zero_program_equivalence(
    *,
    backend: Any,
    memory_representations: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seed: int,
    weights: PairGroundingLossWeights,
) -> dict[str, Any]:
    subset = rows[: min(4, len(rows))]
    model = _make_pair_model(
        program_kind="content",
        memory_dim=int(memory_representations.shape[1]),
        memory_count=int(memory_representations.shape[0]),
        program_dim=128,
        seed=seed,
    ).to(device)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    evaluated = _evaluate_pair_model(
        backend=backend,
        model=model,
        injector=injector,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
        weights=weights,
        control="zero_program",
    )
    diffs = [abs(float(row["student_target_nll"]) - float(row["L0"])) for row in evaluated["rows"]]
    return {
        "format": "stage_c_pair_zero_program_equivalence_5d_v1",
        "pair_count": len(subset),
        "max_abs_nll_delta_vs_bare": max(diffs, default=0.0),
        "delta_norm": evaluated["delta_norm"],
        "delta_ratio": evaluated["delta_ratio"],
        "selected_token_report": evaluated["selected_token_report"],
        "passed": bool(max(diffs, default=0.0) <= 2.0e-4 and (evaluated["delta_norm"].get("max") or 0.0) == 0.0),
    }


def _tiny_overfit(
    *,
    backend: Any,
    memory_representations: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seed: int,
    weights: PairGroundingLossWeights,
    steps: int,
    lr: float,
) -> dict[str, Any]:
    positives = [row for row in rows if row["utility_category"] == "positive"]
    neutrals = [row for row in rows if row["utility_category"] == "neutral"]
    negatives = [row for row in rows if row["utility_category"] == "negative"]
    subset = positives[:4] + neutrals[:4] + negatives[:4]
    if len(subset) < 4:
        return {"passed": False, "reason": "insufficient_rows", "subset_size": len(subset)}
    model = _make_pair_model(
        program_kind="content",
        memory_dim=int(memory_representations.shape[1]),
        memory_count=int(memory_representations.shape[0]),
        program_dim=128,
        seed=seed,
    ).to(device)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    before = _evaluate_pair_model(
        backend=backend,
        model=model,
        injector=injector,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
        weights=weights,
    )
    optimizer = torch.optim.AdamW(list(model.parameters()) + list(injector.parameters()), lr=lr, weight_decay=1.0e-4)
    for step in range(1, steps + 1):
        _train_pair_epoch(
            backend=backend,
            model=model,
            injector=injector,
            rows=subset,
            memory_representations=memory_representations,
            optimizer=optimizer,
            weights=weights,
            device=device,
            seed=seed,
            batch_size=1,
            epoch=step,
            max_steps=1,
        )
    loss, _ = _loss_for_pair_batch(
        backend=backend,
        model=model,
        injector=injector,
        rows=subset[:1],
        memory_representations=memory_representations,
        weights=weights,
        device=device,
        seed=seed,
    )
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    grad_report = {"program": _grad_norms(model), "injector": _grad_norms(injector)}
    after = _evaluate_pair_model(
        backend=backend,
        model=model,
        injector=injector,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
        weights=weights,
    )
    before_delta = before["summary"]["behavioral_delta_huber"]["mean"] or 0.0
    after_delta = after["summary"]["behavioral_delta_huber"]["mean"] or 0.0
    before_kl = before["summary"]["sparse_teacher_kl"]["mean"] or 0.0
    after_kl = after["summary"]["sparse_teacher_kl"]["mean"] or 0.0
    program_grad = max(grad_report["program"].values(), default=0.0)
    injector_grad = max(grad_report["injector"].values(), default=0.0)
    return {
        "format": "stage_c_pair_tiny_overfit_5d_v1",
        "subset_size": len(subset),
        "steps": steps,
        "before": before["summary"],
        "after": after["summary"],
        "gradient_norms_last_batch": grad_report,
        "program_grad_max_norm": program_grad,
        "injector_grad_max_norm": injector_grad,
        "passed": bool(after_delta < before_delta and after_kl <= before_kl + 0.05 and program_grad > 0.0 and injector_grad > 0.0),
    }


def _train_pair_run(
    *,
    backend: Any,
    program_kind: str,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
    output_dir: Path,
    epochs: int,
    batch_size: int,
    eval_batch_size: int,
    lr: float,
    patience: int,
    weights: PairGroundingLossWeights,
    matched_parameter_count: int | None = None,
    stage_to_model_index: dict[int, int] | None = None,
    heldout_fallback: str = "mean",
    max_train_steps: int | None = None,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    model = _make_pair_model(
        program_kind=program_kind,
        memory_dim=int(memory_representations.shape[1]),
        memory_count=int(memory_representations.shape[0]),
        program_dim=128,
        seed=seed,
        matched_parameter_count=matched_parameter_count,
    ).to(device)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    trainable = [param for module in (model, injector) for param in module.parameters() if param.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=lr, weight_decay=1.0e-4)
    best_metric = float("inf")
    best_epoch = 0
    best_model = copy.deepcopy(model.state_dict())
    best_injector = copy.deepcopy(injector.state_dict())
    history = []
    bad = 0
    safe_name = program_kind.replace("/", "_")
    for epoch in range(1, epochs + 1):
        train_report = _train_pair_epoch(
            backend=backend,
            model=model,
            injector=injector,
            rows=train_rows,
            memory_representations=memory_representations,
            optimizer=optimizer,
            weights=weights,
            device=device,
            seed=seed,
            batch_size=batch_size,
            epoch=epoch,
            max_steps=max_train_steps,
            stage_to_model_index=stage_to_model_index,
        )
        validation_eval = _evaluate_pair_model(
            backend=backend,
            model=model,
            injector=injector,
            rows=validation_rows,
            memory_representations=memory_representations,
            device=device,
            seed=seed,
            batch_size=eval_batch_size,
            weights=weights,
            stage_to_model_index=stage_to_model_index,
            heldout_fallback=heldout_fallback,
        )
        metric = float(validation_eval["summary"]["behavioral_delta_huber"]["mean"] or float("inf"))
        history.append({"epoch": epoch, "train": train_report, "validation": validation_eval["summary"]})
        atomic_write_json(output_dir / f"{safe_name}_seed_{seed}_history.json", history)
        print(
            f"stage-5d {program_kind} seed={seed} epoch={epoch} "
            f"val_delta_huber={metric:.6f} val_kl={validation_eval['summary']['sparse_teacher_kl'].get('mean')}",
            flush=True,
        )
        if metric < best_metric - 1.0e-6:
            best_metric = metric
            best_epoch = epoch
            best_model = copy.deepcopy(model.state_dict())
            best_injector = copy.deepcopy(injector.state_dict())
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_model)
    injector.load_state_dict(best_injector)
    train_eval = _evaluate_pair_model(
        backend=backend,
        model=model,
        injector=injector,
        rows=train_rows,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=eval_batch_size,
        weights=weights,
        stage_to_model_index=stage_to_model_index,
        heldout_fallback=heldout_fallback,
    )
    validation_eval = _evaluate_pair_model(
        backend=backend,
        model=model,
        injector=injector,
        rows=validation_rows,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=eval_batch_size,
        weights=weights,
        stage_to_model_index=stage_to_model_index,
        heldout_fallback=heldout_fallback,
    )
    controls = {
        "bare_qwen_zero_field": _baseline_pair_eval(validation_rows, mode="bare_qwen_zero_field"),
        "teacher_oracle": _baseline_pair_eval(validation_rows, mode="teacher_oracle"),
    }
    for control in ("shuffled_program", "fixed_random_program", "mean_program", "zero_program", "memory_swap"):
        controls[control] = _evaluate_pair_model(
            backend=backend,
            model=model,
            injector=injector,
            rows=validation_rows,
            memory_representations=memory_representations,
            device=device,
            seed=seed,
            batch_size=eval_batch_size,
            weights=weights,
            control=control,
            stage_to_model_index=stage_to_model_index,
            heldout_fallback=heldout_fallback,
        )
    ci_inputs = {"correct": validation_eval["rows"], **{key: value["rows"] for key, value in controls.items() if "rows" in value}}
    validation_eval["bootstrap_ci"] = paired_bootstrap_ci(
        ci_inputs,
        baseline_name="correct",
        metrics=("student_target_nll", "sparse_teacher_kl", "behavioral_delta_huber"),
        seed=seed,
    )
    validation_eval["control_deltas"] = _control_deltas(validation_eval, controls)
    with torch.no_grad():
        programs = model.programs(memory_representations.to(device=device, dtype=torch.float32)).detach().cpu()
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{safe_name}_seed_{seed}.pt"
    torch.save(
        {
            "format": RUN_VERSION,
            "program_kind": program_kind,
            "seed": seed,
            "best_epoch": best_epoch,
            "model_state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
            "injector_state_dict": {key: value.detach().cpu() for key, value in injector.state_dict().items()},
            "loss_weights": weights,
            "source_commit": maybe_git_commit(),
            "program_parameter_count": parameter_count(model),
            "injector_parameter_count": parameter_count(injector),
            "stage_to_model_index": stage_to_model_index,
            "heldout_fallback": heldout_fallback,
        },
        checkpoint_path,
    )
    return {
        "format": "stage_c_pair_grounding_seed_run_5d_v1",
        "program_kind": program_kind,
        "seed": seed,
        "best_epoch": best_epoch,
        "epochs_ran": len(history),
        "checkpoint": str(checkpoint_path),
        "history": history,
        "train": {"correct": train_eval},
        "validation": {"correct": validation_eval, "controls": controls, "control_deltas": validation_eval["control_deltas"]},
        "program_geometry": program_geometry(programs),
        "program_representation_similarity": representation_program_similarity(memory_representations.detach().cpu(), programs),
        "program_parameter_count": parameter_count(model),
        "injector_parameter_count": parameter_count(injector),
    }


def _control_deltas(correct: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    correct_rows = {row["pair_id"]: row for row in correct["rows"]}
    output = {}
    for name, payload in controls.items():
        if "rows" not in payload:
            continue
        values: dict[str, list[float]] = {"student_target_nll": [], "sparse_teacher_kl": [], "behavioral_delta_huber": []}
        for row in payload["rows"]:
            pair = row["pair_id"]
            if pair not in correct_rows:
                continue
            for metric in values:
                values[metric].append(float(correct_rows[pair][metric]) - float(row[metric]))
        output[name] = {metric: mean_std(items) for metric, items in values.items()}
    return output


def _summarize_program_runs(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for split in ("train", "validation"):
        output[split] = {}
        for metric in (
            "target_nll",
            "sparse_teacher_kl",
            "behavioral_delta_huber",
            "behavioral_delta_mse",
            "u_text_vs_u_program_spearman",
            "positive_negative_sign_agreement",
            "improved_fraction",
        ):
            values = []
            for run in runs:
                summary = run.get(split, {}).get("correct", {}).get("summary", {})
                value = summary.get(metric)
                if isinstance(value, dict):
                    value = value.get("mean")
                if value is not None:
                    values.append(float(value))
            output[split][metric] = mean_std(values)
    for control in ("shuffled_program", "fixed_random_program", "mean_program", "zero_program", "memory_swap", "bare_qwen_zero_field"):
        output[f"correct_minus_{control}"] = {
            metric: mean_std(
                run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean")
                for run in runs
                if run.get("validation", {}).get("control_deltas", {}).get(control, {}).get(metric, {}).get("mean") is not None
            )
            for metric in ("student_target_nll", "sparse_teacher_kl", "behavioral_delta_huber")
        }
    output["program_centered_effective_rank"] = mean_std(
        run.get("program_geometry", {}).get("centered_spectrum", {}).get("effective_rank")
        for run in runs
        if run.get("program_geometry", {}).get("centered_spectrum", {}).get("effective_rank") is not None
    )
    return output


def _choose_ratio_target_smoke(
    *,
    backend: Any,
    train_rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
    lr: float,
    batch_size: int,
    eval_batch_size: int,
    candidates: Sequence[float],
    smoke_pairs: int,
    smoke_steps: int,
    output_dir: Path,
) -> dict[str, Any]:
    positives = [row for row in train_rows if row["utility_category"] == "positive"]
    neutrals = [row for row in train_rows if row["utility_category"] == "neutral"]
    negatives = [row for row in train_rows if row["utility_category"] == "negative"]
    subset = []
    for index in range(max(len(positives), len(neutrals), len(negatives))):
        for bucket in (positives, neutrals, negatives):
            if index < len(bucket):
                subset.append(bucket[index])
        if len(subset) >= smoke_pairs:
            break
    subset = subset[:smoke_pairs]
    split = max(1, int(0.8 * len(subset)))
    smoke_train = subset[:split]
    smoke_eval = subset[split:] or subset[: min(8, len(subset))]
    outputs = []
    for target in candidates:
        weights = PairGroundingLossWeights(ratio_target=float(target))
        run = _train_pair_run(
            backend=backend,
            program_kind="content",
            train_rows=smoke_train,
            validation_rows=smoke_eval,
            memory_representations=memory_representations,
            model_dim=model_dim,
            device=device,
            seed=seed + int(float(target) * 100),
            output_dir=output_dir / f"ratio_smoke_{str(target).replace('.', 'p')}",
            epochs=1,
            batch_size=batch_size,
            eval_batch_size=eval_batch_size,
            lr=lr,
            patience=1,
            weights=weights,
            max_train_steps=smoke_steps,
        )
        score = float(run["validation"]["correct"]["summary"]["behavioral_delta_huber"].get("mean") or float("inf"))
        delta_ratio = float(run["validation"]["correct"]["delta_ratio"].get("mean") or 0.0)
        outputs.append({"ratio_target": float(target), "score": score, "delta_ratio_mean": delta_ratio, "run": run})
        atomic_write_json(output_dir / f"ratio_smoke_{str(target).replace('.', 'p')}_summary.json", outputs[-1])
    outputs.sort(key=lambda item: (item["score"], abs(item["delta_ratio_mean"] - item["ratio_target"])))
    selected = outputs[0]["ratio_target"] if outputs else 1.0
    return {
        "format": "stage_c_pair_ratio_target_smoke_5d_v1",
        "train_only": True,
        "candidate_targets": [float(item) for item in candidates],
        "selected_ratio_target": selected,
        "smoke_train_pairs": len(smoke_train),
        "smoke_eval_pairs": len(smoke_eval),
        "results": outputs,
        "selection_rule": "lowest train-only heldout behavioral_delta_huber, tie-broken by delta_ratio closeness",
    }


def _run_memory_heldout_cv(
    *,
    backend: Any,
    train_rows: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
    output_dir: Path,
    epochs: int,
    batch_size: int,
    eval_batch_size: int,
    lr: float,
    patience: int,
    weights: PairGroundingLossWeights,
    matched_parameter_count: int,
) -> dict[str, Any]:
    folds = deterministic_memory_folds(int(memory_representations.shape[0]), folds=5, seed=41)
    fold_reports = []
    for fold in folds:
        fold_index = int(fold["fold"])
        train_memory_indices = set(int(item) for item in fold["train_memory_stage_indices"])
        heldout_memory_indices = set(int(item) for item in fold["heldout_memory_stage_indices"])
        fold_train_rows = [row for row in train_rows if int(row["memory_stage_index"]) in train_memory_indices]
        fold_eval_rows = [row for row in all_rows if int(row["memory_stage_index"]) in heldout_memory_indices]
        if not fold_train_rows or not fold_eval_rows:
            fold_reports.append({"fold": fold_index, "skipped": True, "reason": "empty_train_or_eval", "fold": fold})
            continue
        fold_dir = output_dir / f"memory_fold_{fold_index}"
        fold_dir.mkdir(parents=True, exist_ok=True)
        content_run = _train_pair_run(
            backend=backend,
            program_kind="content",
            train_rows=fold_train_rows,
            validation_rows=fold_eval_rows,
            memory_representations=memory_representations,
            model_dim=model_dim,
            device=device,
            seed=seed + fold_index,
            output_dir=fold_dir / "content",
            epochs=epochs,
            batch_size=batch_size,
            eval_batch_size=eval_batch_size,
            lr=lr,
            patience=patience,
            weights=weights,
        )
        train_indices = sorted(train_memory_indices)
        stage_to_model_index = {stage_index: pos for pos, stage_index in enumerate(train_indices)}
        free_train_reps = memory_representations[train_indices].contiguous()
        free_id_run = _train_pair_run(
            backend=backend,
            program_kind="free_id",
            train_rows=fold_train_rows,
            validation_rows=fold_eval_rows,
            memory_representations=free_train_reps,
            model_dim=model_dim,
            device=device,
            seed=seed + 100 + fold_index,
            output_dir=fold_dir / "free_id_mean_fallback",
            epochs=max(1, min(epochs, 1)),
            batch_size=batch_size,
            eval_batch_size=eval_batch_size,
            lr=lr,
            patience=1,
            weights=weights,
            matched_parameter_count=matched_parameter_count,
            stage_to_model_index=stage_to_model_index,
            heldout_fallback="mean",
        )
        fold_report = {
            "fold": fold_index,
            "train_memory_stage_indices": sorted(train_memory_indices),
            "heldout_memory_stage_indices": sorted(heldout_memory_indices),
            "train_pairs": len(fold_train_rows),
            "heldout_eval_pairs": len(fold_eval_rows),
            "content": content_run,
            "free_id_mean_fallback": free_id_run,
            "content_minus_free_id_mean_fallback": paired_bootstrap_ci(
                {
                    "content": content_run["validation"]["correct"]["rows"],
                    "free_id_mean_fallback": free_id_run["validation"]["correct"]["rows"],
                },
                baseline_name="content",
                metrics=("student_target_nll", "sparse_teacher_kl", "behavioral_delta_huber"),
                seed=seed + fold_index,
            ),
        }
        atomic_write_json(fold_dir / "fold_summary.json", fold_report)
        fold_reports.append(fold_report)
    content_runs = [fold["content"] for fold in fold_reports if not fold.get("skipped")]
    free_runs = [fold["free_id_mean_fallback"] for fold in fold_reports if not fold.get("skipped")]
    positive_folds = sum(
        1
        for run in content_runs
        if (run["validation"]["correct"]["summary"].get("u_text_vs_u_program_spearman") or 0.0) > 0.0
    )
    return {
        "format": "stage_c_pair_memory_heldout_cv_5d_v1",
        "folds": folds,
        "fold_reports": fold_reports,
        "content_summary": _summarize_program_runs(content_runs),
        "free_id_mean_fallback_summary": _summarize_program_runs(free_runs),
        "content_positive_spearman_fold_count": positive_folds,
        "content_passed_minimum": bool(positive_folds >= 4),
        "free_id_no_heldout_embeddings": True,
    }


def _oracle_raw_utility_composition(
    *,
    backend: Any,
    model_run: dict[str, Any],
    memory_representations: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seed: int,
    weights: PairGroundingLossWeights,
    max_states: int,
) -> dict[str, Any]:
    checkpoint = torch.load(model_run["checkpoint"], map_location=device)
    model = _make_pair_model(
        program_kind="content",
        memory_dim=int(memory_representations.shape[1]),
        memory_count=int(memory_representations.shape[0]),
        program_dim=128,
        seed=seed,
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    injector.load_state_dict(checkpoint["injector_state_dict"])
    by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["utility_category"] == "positive":
            by_state[str(row["state_example_id"])].append(row)
    selected_states = sorted(by_state)[:max_states]
    synthetic_rows = []
    with torch.no_grad():
        programs = model.programs(memory_representations.to(device=device, dtype=torch.float32))
    for state_id in selected_states:
        state_rows = sorted(by_state[state_id], key=lambda item: float(item["u_text"]), reverse=True)
        base = dict(state_rows[0])
        weights_tensor = torch.tensor([max(float(item["u_text"]), 0.0) for item in state_rows], dtype=torch.float32, device=device)
        if float(weights_tensor.sum().item()) <= 0.0:
            continue
        weights_tensor = weights_tensor / weights_tensor.sum()
        indices = torch.tensor([int(item["memory_stage_index"]) for item in state_rows], dtype=torch.long, device=device)
        oracle_z = (weights_tensor.view(-1, 1) * programs.index_select(0, indices)).sum(dim=0, keepdim=True)
        synthetic = dict(base)
        synthetic["_oracle_z"] = oracle_z.detach().cpu()
        synthetic["pair_id"] = f"{state_id}::oracle_raw_utility_field"
        synthetic_rows.append(synthetic)
    if not synthetic_rows:
        return {"state_count": 0, "skipped": True}
    out_rows = []
    model.eval()
    injector.eval()
    with torch.no_grad():
        for row in synthetic_rows:
            batch = _collate_pair_rows([row], device=device)
            student = _forward_student(
                backend=backend,
                injector=injector,
                batch=batch,
                memory_z=row["_oracle_z"].to(device),
            )
            _, pair_outputs = _student_pair_terms(
                student["target_logits"],
                batch["labels"],
                batch["response_rows"],
                target_lengths=batch["target_lengths"],
                huber_delta=weights.huber_delta,
                return_rows=True,
                pair_rows=[row],
            )
            pair_outputs[0]["control"] = "oracle_raw_utility_coefficients"
            out_rows.extend(pair_outputs)
    return {
        "format": "stage_c_pair_oracle_raw_utility_composition_5d_v1",
        "state_count": len(synthetic_rows),
        "rows": out_rows,
        "summary": summarize_pair_eval_rows(out_rows),
        "deployable": False,
    }


def _decision_gate(
    *,
    content_summary: dict[str, Any],
    free_id_summary: dict[str, Any],
    fixed_random_summary: dict[str, Any],
    memory_cv: dict[str, Any],
) -> dict[str, Any]:
    content_val = content_summary.get("validation", {})
    free_val = free_id_summary.get("validation", {})
    fixed_val = fixed_random_summary.get("validation", {})
    content_spearman = content_val.get("u_text_vs_u_program_spearman", {}).get("mean")
    content_sign = content_val.get("positive_negative_sign_agreement", {}).get("mean")
    content_vs_shuffled_nll = content_summary.get("correct_minus_shuffled_program", {}).get("student_target_nll", {}).get("mean")
    content_vs_swap_nll = content_summary.get("correct_minus_memory_swap", {}).get("student_target_nll", {}).get("mean")
    content_vs_random_kl = content_summary.get("correct_minus_fixed_random_program", {}).get("sparse_teacher_kl", {}).get("mean")
    free_delta = free_val.get("behavioral_delta_huber", {}).get("mean")
    content_delta = content_val.get("behavioral_delta_huber", {}).get("mean")
    fixed_delta = fixed_val.get("behavioral_delta_huber", {}).get("mean")
    channel_capacity = bool(
        (free_delta is not None and fixed_delta is not None and free_delta < fixed_delta)
        or (content_delta is not None and fixed_delta is not None and content_delta < fixed_delta)
    )
    content_grounded = bool(
        content_spearman is not None
        and content_spearman >= 0.30
        and content_sign is not None
        and content_sign >= 0.65
        and content_vs_shuffled_nll is not None
        and content_vs_shuffled_nll < 0.0
        and content_vs_swap_nll is not None
        and content_vs_swap_nll < 0.0
        and content_vs_random_kl is not None
        and content_vs_random_kl < 0.0
    )
    compiler_generalizes = bool(memory_cv.get("content_passed_minimum"))
    if not channel_capacity:
        branch = "program_injector_behavioral_channel_insufficient"
        passed = False
    elif not content_grounded:
        branch = "memory_representation_or_content_compiler_bottleneck"
        passed = False
    elif not compiler_generalizes:
        branch = "content_compiler_memorizes_seen_memories_without_unseen_generalization"
        passed = False
    else:
        branch = "pair_level_memory_grounding_passed"
        passed = True
    return {
        "format": "stage_c_pair_grounding_decision_gate_5d_v1",
        "passed": passed,
        "branch": branch,
        "stage_c2_allowed": False,
        "values": {
            "channel_capacity": channel_capacity,
            "content_spearman": content_spearman,
            "content_sign_agreement": content_sign,
            "content_minus_shuffled_program_nll": content_vs_shuffled_nll,
            "content_minus_memory_swap_nll": content_vs_swap_nll,
            "content_minus_random_program_kl": content_vs_random_kl,
            "memory_heldout_positive_spearman_folds": memory_cv.get("content_positive_spearman_fold_count"),
        },
    }


def _write_report(summary: dict[str, Any]) -> str:
    decision = summary["decision_gate"]
    lines = [
        "# Milestone 5D / EXP-014 Pair-Level and Single-Memory Behavioral Grounding",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- hard scope: selector bypassed, no selector training, no full-bank aggregation, no AppWorld generation/evaluation.",
        f"- pair cache: `{summary['pair_cache_dir']}`",
        f"- pair-cache validation passed: `{summary['pair_cache_validation']['passed']}`",
        f"- selected pair count: `{summary['pair_cache_summary']['selected_pair_count']}`",
        f"- train pairs: `{summary['pair_counts']['train']}`",
        f"- validation pairs: `{summary['pair_counts']['validation']}`",
        f"- perturbation ratio target selected from train-only smoke: `{summary['ratio_smoke']['selected_ratio_target']}`",
        f"- zero-program equivalence passed: `{summary['zero_program_equivalence']['passed']}`",
        f"- tiny overfit passed: `{summary['tiny_overfit']['passed']}`",
        "",
        "## State-Held-Out Validation",
        "",
    ]
    for name in ("content", "free_id", "fixed_random"):
        aggregate = summary["state_heldout_runs"].get(name, {}).get("aggregate", {})
        validation = aggregate.get("validation", {})
        lines.extend(
            [
                f"### {name}",
                "",
                f"- target NLL: `{validation.get('target_nll', {})}`",
                f"- sparse teacher KL: `{validation.get('sparse_teacher_kl', {})}`",
                f"- behavioral delta Huber: `{validation.get('behavioral_delta_huber', {})}`",
                f"- u_text vs u_program Spearman: `{validation.get('u_text_vs_u_program_spearman', {})}`",
                f"- positive/negative sign agreement: `{validation.get('positive_negative_sign_agreement', {})}`",
                f"- correct-minus-shuffled program: `{aggregate.get('correct_minus_shuffled_program', {})}`",
                f"- correct-minus-memory swap: `{aggregate.get('correct_minus_memory_swap', {})}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Memory-Held-Out CV",
            "",
            f"- content positive-Spearman folds: `{summary['memory_heldout_cv']['content_positive_spearman_fold_count']}/5`",
            f"- content summary: `{summary['memory_heldout_cv']['content_summary'].get('validation', {})}`",
            f"- free-ID mean fallback summary: `{summary['memory_heldout_cv']['free_id_mean_fallback_summary'].get('validation', {})}`",
            "",
            "## Decision",
            "",
            f"```json\n{json.dumps(decision, indent=2, sort_keys=True)}\n```",
            "",
            "## Artifacts",
            "",
            f"- checkpoint dir: `{summary['checkpoint_dir']}`",
            f"- summary: `{summary['output_dir']}/summary.json`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Milestone 5D pair-level/single-memory behavioral grounding.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--stage-c1-response-cache-dir", default=None)
    parser.add_argument("--pair-cache-dir", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top-k", type=int, default=64)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--memory-cv-epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--lr", type=float, default=2.0e-4)
    parser.add_argument("--patience", type=int, default=1)
    parser.add_argument("--tiny-overfit-steps", type=int, default=24)
    parser.add_argument("--ratio-smoke-pairs", type=int, default=48)
    parser.add_argument("--ratio-smoke-steps", type=int, default=12)
    parser.add_argument("--ratio-targets", type=float, nargs="+", default=[0.5, 1.0, 2.0])
    parser.add_argument("--progress-interval-s", type=float, default=120.0)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Milestone 5D requires hf_qwen")
    data_dir = Path(args.data)
    teacher_cache_dir = Path(args.teacher_cache_dir)
    labels_dir = Path(args.labels_dir)
    repr_dir = Path(args.representation_cache_dir)
    output_dir = Path(args.output_dir)
    pair_cache_dir = Path(args.pair_cache_dir) if args.pair_cache_dir else output_dir / "pair_response_cache"
    stage_c1_response_dir = Path(args.stage_c1_response_cache_dir) if args.stage_c1_response_cache_dir else None
    output_dir.mkdir(parents=True, exist_ok=True)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    device = torch.device(args.device)
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for param in backend.model.parameters():
        param.requires_grad_(False)
    model_dim = int(getattr(getattr(backend.model, "config", None), "hidden_size"))
    context_limit = _context_limit_for_backend(backend)

    pair_rows, pair_cache_summary = build_pair_response_cache(
        backend=backend,
        data_dir=data_dir,
        labels_dir=labels_dir,
        teacher_cache_dir=teacher_cache_dir,
        stage_c1_response_dir=stage_c1_response_dir,
        output_dir=pair_cache_dir,
        prompt_profile=cfg.benchmark.prompt_profile,
        top_k=args.top_k,
        progress_interval_s=args.progress_interval_s,
    )
    if args.cache_only:
        print(f"pair cache built and validated at {pair_cache_dir}", flush=True)
        return

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    label_rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    memory_indices = [int(row["memory_index"]) for row in memory_bank]
    all_memory_reps, memory_meta = _load_representation_cache(
        repr_dir / "memory_record_representations.pt",
        expected_count=46,
        expected_source_path=data_dir / "memory_records.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"chunked_qwen_hidden_v1", "record_qwen_hidden_v2"},
    )
    memory_reps = all_memory_reps[memory_indices].to(device=device, dtype=torch.float32)
    token_rows = _build_tokenized_pair_rows(
        backend=backend,
        examples=examples,
        pair_rows=pair_rows,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=context_limit,
    )
    train_rows = [row for row in token_rows if row["split"] == "train"]
    validation_rows = [row for row in token_rows if row["split"] == "validation"]
    if not train_rows or not validation_rows:
        raise ValueError("5D requires non-empty train and validation pair rows")

    base_weights = PairGroundingLossWeights(ratio_target=1.0)
    zero_equivalence = _zero_program_equivalence(
        backend=backend,
        memory_representations=memory_reps,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seed=args.seed,
        weights=base_weights,
    )
    atomic_write_json(output_dir / "zero_program_equivalence.json", zero_equivalence)
    if not zero_equivalence["passed"]:
        raise SystemExit(f"Zero-program equivalence failed: {zero_equivalence}")

    ratio_smoke = _choose_ratio_target_smoke(
        backend=backend,
        train_rows=train_rows,
        memory_representations=memory_reps,
        model_dim=model_dim,
        device=device,
        seed=args.seed,
        lr=args.lr,
        batch_size=args.batch_size,
        eval_batch_size=args.eval_batch_size,
        candidates=args.ratio_targets,
        smoke_pairs=args.ratio_smoke_pairs,
        smoke_steps=args.ratio_smoke_steps,
        output_dir=output_dir / "ratio_smoke",
    )
    atomic_write_json(output_dir / "ratio_smoke.json", ratio_smoke)
    weights = PairGroundingLossWeights(ratio_target=float(ratio_smoke["selected_ratio_target"]))
    tiny = _tiny_overfit(
        backend=backend,
        memory_representations=memory_reps,
        rows=train_rows,
        model_dim=model_dim,
        device=device,
        seed=args.seed,
        weights=weights,
        steps=args.tiny_overfit_steps,
        lr=args.lr,
    )
    atomic_write_json(output_dir / "tiny_overfit.json", tiny)
    if not tiny["passed"]:
        raise SystemExit(f"Tiny overfit failed: {tiny}")

    content_param_count = parameter_count(
        _make_pair_model(
            program_kind="content",
            memory_dim=int(memory_reps.shape[1]),
            memory_count=int(memory_reps.shape[0]),
            program_dim=128,
            seed=args.seed,
        )
    )
    state_runs: dict[str, Any] = {}
    for program_kind in ("content", "free_id", "fixed_random"):
        print(f"stage-5d state-heldout training program_kind={program_kind}", flush=True)
        run = _train_pair_run(
            backend=backend,
            program_kind=program_kind,
            train_rows=train_rows,
            validation_rows=validation_rows,
            memory_representations=memory_reps,
            model_dim=model_dim,
            device=device,
            seed=args.seed,
            output_dir=output_dir / "state_heldout" / program_kind,
            epochs=args.epochs,
            batch_size=args.batch_size,
            eval_batch_size=args.eval_batch_size,
            lr=args.lr,
            patience=args.patience,
            weights=weights,
            matched_parameter_count=content_param_count,
        )
        atomic_write_json(output_dir / f"{program_kind}_state_heldout_run.json", run)
        state_runs[program_kind] = {"runs": [run], "aggregate": _summarize_program_runs([run])}

    memory_cv = _run_memory_heldout_cv(
        backend=backend,
        train_rows=train_rows,
        all_rows=token_rows,
        memory_representations=memory_reps,
        model_dim=model_dim,
        device=device,
        seed=args.seed,
        output_dir=output_dir / "memory_heldout_cv",
        epochs=args.memory_cv_epochs,
        batch_size=args.batch_size,
        eval_batch_size=args.eval_batch_size,
        lr=args.lr,
        patience=1,
        weights=weights,
        matched_parameter_count=content_param_count,
    )
    atomic_write_json(output_dir / "memory_heldout_cv_summary.json", memory_cv)

    decision = _decision_gate(
        content_summary=state_runs["content"]["aggregate"],
        free_id_summary=state_runs["free_id"]["aggregate"],
        fixed_random_summary=state_runs["fixed_random"]["aggregate"],
        memory_cv=memory_cv,
    )
    oracle = None
    if decision["passed"]:
        oracle = _oracle_raw_utility_composition(
            backend=backend,
            model_run=state_runs["content"]["runs"][0],
            memory_representations=memory_reps,
            rows=validation_rows,
            model_dim=model_dim,
            device=device,
            seed=args.seed,
            weights=weights,
            max_states=16,
        )
        atomic_write_json(output_dir / "oracle_raw_utility_composition.json", oracle)

    summary = {
        "format": RUN_VERSION,
        "source_commit": maybe_git_commit(),
        "output_dir": str(output_dir),
        "pair_cache_dir": str(pair_cache_dir),
        "pair_cache_summary": pair_cache_summary,
        "pair_cache_validation": pair_cache_summary["validation"],
        "data_dir": str(data_dir),
        "teacher_cache_dir": str(teacher_cache_dir),
        "labels_dir": str(labels_dir),
        "representation_cache_dir": str(repr_dir),
        "memory_representation_metadata": memory_meta,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "context_limit": context_limit,
        "trainable_modules": ["content/free_id/fixed_random program path as configured", "additive_token_injector"],
        "frozen_modules": ["Qwen3-8B", "signed selector not loaded or used", "empirical_mu not used", "selector_gate not used"],
        "primary_read": "z(s,i)=p_i; no selector score, no gate, no mu, no full-bank aggregation",
        "student_prompt_contains_raw_memory_text": False,
        "pair_counts": {
            "total": len(token_rows),
            "train": len(train_rows),
            "validation": len(validation_rows),
        },
        "zero_program_equivalence": zero_equivalence,
        "ratio_smoke": ratio_smoke,
        "loss_weights": weights,
        "tiny_overfit": tiny,
        "state_heldout_runs": state_runs,
        "memory_heldout_cv": memory_cv,
        "oracle_raw_utility_composition": oracle,
        "decision_gate": decision,
        "checkpoint_dir": str(output_dir / "state_heldout"),
        "runtime_s": time.perf_counter() - started,
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _write_report(summary))
    print(json.dumps({"summary": str(output_dir / "summary.json"), "decision": decision}, indent=2), flush=True)


if __name__ == "__main__":
    main()
