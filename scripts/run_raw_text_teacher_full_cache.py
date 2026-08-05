from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import time
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
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
from scripts.run_raw_text_teacher_audit3b import (
    AUDIT3B_CACHE_VERSION,
    _preflight_pair,
    _run_reproducibility_check,
)
from scripts.run_raw_text_teacher_pilot import (
    RAW_TEXT_TEACHER_CACHE_VERSION,
    TEACHER_MEMORY_SECTION_VERSION,
    UTILITY_NEUTRAL_EPS,
    _category,
    _context_limit_for_backend,
    _distribution,
    _example_id,
    _pearson,
    _score_mean_target_nll,
    _target_token_ids,
    _token_ids,
    apps_for_example,
    apps_for_record,
    legal_memory_indices,
    messages_with_teacher_memory,
)
from scripts.train import _example_task_id, _leakage_keys_for_example, _leakage_keys_for_record


FULL_CACHE_VERSION = "raw_text_memory_teacher_full_cache_v1"
FULL_SCORING_DEFINITION = "frozen_qwen_full_demo_raw_memory_mean_target_nll_v1"
FULL_VALIDATION_VERSION = "raw_text_memory_teacher_full_cache_validation_v1"
FULL_REPORT_VERSION = "raw_text_memory_teacher_full_cache_report_v1"
COMPATIBLE_CACHE_FORMATS = {RAW_TEXT_TEACHER_CACHE_VERSION, AUDIT3B_CACHE_VERSION}
EXPECTED_STATE_COUNT = 638
EXPECTED_MEMORY_COUNT = 46
EXPECTED_LEGAL_PAIR_COUNT = 28_710
EXPECTED_SCOREABLE_PAIR_COUNT = 27_054
EXPECTED_OVER_CONTEXT_PAIR_COUNT = 1_656
UTILITY_THRESHOLDS = (0.01, 0.05, 0.10, 0.25)
REPRO_TOLERANCE = 1.0e-5
API_RE = re.compile(r"\bapis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pair_key(example_index: int, memory_index: int) -> str:
    return f"e{example_index}:m{memory_index}"


def state_memory_pair_id(example: DecisionExample, record: MemoryRecord, example_index: int, memory_index: int) -> str:
    return f"{_example_id(example_index, example)}||{record.memory_id}"


def finite_float(value: Any) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def extract_apis(text: str) -> set[str]:
    return {f"{match.group(1).lower()}.{match.group(2).lower()}" for match in API_RE.finditer(text)}


def identifier_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in IDENT_RE.findall(text)
        if len(token) > 1 and token.lower() not in {"true", "false", "none", "null"}
    }


def normalize_for_substring(text: str) -> str:
    return " ".join(text.lower().split())


def overlap_features(example: DecisionExample, record: MemoryRecord) -> dict[str, Any]:
    target_text = _target_suffix(example)
    state_text = example.state_text
    memory_text = record.experience_text
    target_apps = apps_for_example(example)
    memory_apps = apps_for_record(record)
    target_apis = extract_apis(target_text)
    state_apis = extract_apis(state_text)
    memory_apis = extract_apis(memory_text)
    target_tokens = identifier_tokens(target_text)
    memory_tokens = identifier_tokens(memory_text)
    shared_code_tokens = target_tokens.intersection(memory_tokens)
    union_code_tokens = target_tokens.union(memory_tokens)
    normalized_target = normalize_for_substring(target_text)
    normalized_memory = normalize_for_substring(memory_text)
    return {
        "state_apps": sorted(target_apps),
        "memory_apps": sorted(memory_apps),
        "same_app": bool(target_apps.intersection(memory_apps)),
        "target_apis": sorted(target_apis),
        "state_apis": sorted(state_apis),
        "memory_apis": sorted(memory_apis),
        "shared_api_count": len(target_apis.intersection(memory_apis)),
        "shared_state_api_count": len(state_apis.intersection(memory_apis)),
        "normalized_target_substring_in_memory": bool(normalized_target and normalized_target in normalized_memory),
        "target_code_token_count": len(target_tokens),
        "memory_code_token_count": len(memory_tokens),
        "shared_code_token_count": len(shared_code_tokens),
        "code_token_jaccard": len(shared_code_tokens) / len(union_code_tokens) if union_code_tokens else 0.0,
    }


def quantile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * fraction))))
    return int(sorted_values[index])


def bucket_by_quantiles(value: int, q1: int, q2: int) -> str:
    if value <= q1:
        return "short"
    if value <= q2:
        return "medium"
    return "long"


def step_bucket(example: DecisionExample, max_step_by_episode: dict[str, int]) -> str:
    max_step = max_step_by_episode.get(example.episode_id, example.step_id)
    if max_step <= 1:
        return "early"
    ratio = (max(1, example.step_id) - 1) / max(1, max_step - 1)
    if ratio <= 1.0 / 3.0:
        return "early"
    if ratio <= 2.0 / 3.0:
        return "middle"
    return "later"


def source_priority(row: dict[str, Any]) -> int:
    fmt = row.get("format")
    if fmt == AUDIT3B_CACHE_VERSION:
        return 2
    if fmt == RAW_TEXT_TEACHER_CACHE_VERSION:
        return 1
    return 0


def validate_source_row(
    row: dict[str, Any],
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    backend_model_name: str,
    renderer_version: str,
    expected_checkpoint_identity: str,
    target_token_hashes: dict[int, str],
) -> tuple[bool, str | None]:
    if row.get("format") not in COMPATIBLE_CACHE_FORMATS:
        return False, "incompatible_format"
    try:
        example_index = int(row["example_index"])
        memory_index = int(row["candidate_memory_index"])
    except (KeyError, TypeError, ValueError):
        return False, "missing_or_invalid_indices"
    if not (0 <= example_index < len(examples)) or not (0 <= memory_index < len(records)):
        return False, "index_out_of_range"
    example = examples[example_index]
    record = records[memory_index]
    if row.get("state_example_id") != _example_id(example_index, example):
        return False, "state_id_mismatch"
    if row.get("candidate_memory_id") != record.memory_id:
        return False, "memory_id_mismatch"
    if row.get("model_name") != backend_model_name:
        return False, "model_name_mismatch"
    if row.get("checkpoint_identity") != expected_checkpoint_identity:
        return False, "checkpoint_identity_mismatch"
    if row.get("renderer_version") != renderer_version:
        return False, "renderer_version_mismatch"
    if row.get("teacher_memory_section_version") != TEACHER_MEMORY_SECTION_VERSION:
        return False, "teacher_section_version_mismatch"
    if row.get("target_sha256") != sha256_text(_target_suffix(example)):
        return False, "target_hash_mismatch"
    if row.get("target_token_sha256") != target_token_hashes[example_index]:
        return False, "target_token_hash_mismatch"
    if row.get("memory_text_sha256") != sha256_text(record.experience_text):
        return False, "memory_hash_mismatch"
    if row.get("over_context"):
        if row.get("text_utility") is not None or row.get("Lj_text") is not None:
            return False, "over_context_has_loss"
    else:
        if not (finite_float(row.get("L0")) and finite_float(row.get("Lj_text")) and finite_float(row.get("text_utility"))):
            return False, "missing_finite_loss"
        expected_utility = float(row["L0"]) - float(row["Lj_text"])
        if abs(expected_utility - float(row["text_utility"])) > REPRO_TOLERANCE:
            return False, "utility_inconsistent"
    return True, None


def convert_cached_row(
    row: dict[str, Any],
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    cache_generation_commit_sha: str,
    source_path: str,
) -> dict[str, Any]:
    example_index = int(row["example_index"])
    memory_index = int(row["candidate_memory_index"])
    example = examples[example_index]
    record = records[memory_index]
    over_context = bool(row["over_context"])
    score_status = "over_context" if over_context else "scored"
    converted = dict(row)
    converted.update(
        {
            "format": FULL_CACHE_VERSION,
            "source_cache_format": row.get("format"),
            "source_cache_path": source_path,
            "source_row_origin": row.get("audit3b_row_origin") or "cached_milestone3",
            "scoring_definition": FULL_SCORING_DEFINITION,
            "pair_key": pair_key(example_index, memory_index),
            "state_memory_pair_id": state_memory_pair_id(example, record, example_index, memory_index),
            "pair_key_sha256": sha256_text(state_memory_pair_id(example, record, example_index, memory_index)),
            "score_status": score_status,
            "valid_for_loss": score_status == "scored",
            "truncated": False,
            "leakage_keys_state": sorted(_leakage_keys_for_example(example)),
            "leakage_keys_memory": sorted(_leakage_keys_for_record(record)),
            "leakage_overlap": sorted(_leakage_keys_for_example(example).intersection(_leakage_keys_for_record(record))),
            "source_commit_sha": row.get("commit_sha"),
            "cache_generation_commit_sha": cache_generation_commit_sha,
            "scoring_timestamp_utc": row.get("scoring_timestamp_utc") or utc_now(),
            "scoring_timestamp_source": "source_row" if row.get("scoring_timestamp_utc") else "cache_validation_time_original_missing",
            "skipped_reason": "over_context" if score_status == "over_context" else None,
        }
    )
    converted.update(overlap_features(example, record))
    if score_status == "scored":
        converted["utility_category"] = _category(float(converted["text_utility"]))
    else:
        converted["Lj_text"] = None
        converted["text_utility"] = None
        converted["utility_category"] = None
    return converted


def merge_cached_rows(
    paths: list[Path],
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    backend_model_name: str,
    renderer_version: str,
    expected_checkpoint_identity: str,
    target_token_hashes: dict[int, str],
    cache_generation_commit_sha: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    cached: dict[str, dict[str, Any]] = {}
    stats = {
        "candidate_rows_seen": 0,
        "validated_rows_seen": 0,
        "unique_validated_pairs": 0,
        "rejected_rows": Counter(),
        "source_paths": [str(path) for path in paths],
        "duplicate_compatible_rows": 0,
        "duplicate_inconsistent_rows": 0,
    }
    for path in paths:
        if not path.exists():
            stats["rejected_rows"][f"missing_path:{path}"] += 1
            continue
        for row in read_jsonl(path):
            stats["candidate_rows_seen"] += 1
            ok, reason = validate_source_row(
                row,
                examples=examples,
                records=records,
                backend_model_name=backend_model_name,
                renderer_version=renderer_version,
                expected_checkpoint_identity=expected_checkpoint_identity,
                target_token_hashes=target_token_hashes,
            )
            if not ok:
                stats["rejected_rows"][reason or "unknown"] += 1
                continue
            converted = convert_cached_row(
                row,
                examples=examples,
                records=records,
                cache_generation_commit_sha=cache_generation_commit_sha,
                source_path=str(path),
            )
            key = converted["pair_key"]
            stats["validated_rows_seen"] += 1
            if key not in cached:
                cached[key] = converted
                continue
            previous = cached[key]
            compatible = previous["score_status"] == converted["score_status"]
            if compatible and converted["score_status"] == "scored":
                compatible = (
                    abs(float(previous["L0"]) - float(converted["L0"])) <= REPRO_TOLERANCE
                    and abs(float(previous["Lj_text"]) - float(converted["Lj_text"])) <= REPRO_TOLERANCE
                    and abs(float(previous["text_utility"]) - float(converted["text_utility"])) <= REPRO_TOLERANCE
                )
            if compatible:
                stats["duplicate_compatible_rows"] += 1
                sources = set(previous.get("validated_source_cache_paths", [previous.get("source_cache_path")]))
                sources.add(str(path))
                converted["validated_source_cache_paths"] = sorted(source for source in sources if source)
                previous["validated_source_cache_paths"] = converted["validated_source_cache_paths"]
                if source_priority(row) >= source_priority(previous):
                    cached[key] = converted
            else:
                stats["duplicate_inconsistent_rows"] += 1
    stats["unique_validated_pairs"] = len(cached)
    stats["rejected_rows"] = dict(stats["rejected_rows"])
    return cached, stats


def load_existing_completed_rows(path: Path, expected_format: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    stats = {"rows_seen": 0, "invalid_rows": 0, "duplicate_rows": 0}
    if not path.exists():
        return rows, stats
    for row in read_jsonl(path):
        stats["rows_seen"] += 1
        if row.get("format") != expected_format or "pair_key" not in row:
            stats["invalid_rows"] += 1
            continue
        key = str(row["pair_key"])
        if key in rows:
            stats["duplicate_rows"] += 1
            continue
        rows[key] = row
    return rows, stats


def gpu_status() -> dict[str, Any]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return {"available": False}
    rows = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 3:
            rows.append({"index": int(parts[0]), "memory_used_mib": int(parts[1]), "utilization_gpu_percent": int(parts[2])})
    return {"available": True, "gpus": rows}


def progress_snapshot(
    *,
    started: float,
    expected_scoreable: int,
    expected_over_context: int,
    rows: dict[str, dict[str, Any]],
    reused_pairs: int,
    newly_scored_pairs: int,
    retried_pairs: int,
    failed_pairs: int,
    current_pair: str | None,
) -> dict[str, Any]:
    elapsed = time.perf_counter() - started
    completed_scoreable = sum(1 for row in rows.values() if row.get("score_status") == "scored")
    completed_over_context = sum(1 for row in rows.values() if row.get("score_status") == "over_context")
    remaining_scoreable = max(0, expected_scoreable - completed_scoreable)
    rate = completed_scoreable / elapsed if elapsed > 0 else None
    eta = remaining_scoreable / rate if rate and rate > 0 else None
    return {
        "timestamp_utc": utc_now(),
        "completed_scoreable_pairs": completed_scoreable,
        "completed_over_context_pairs": completed_over_context,
        "expected_scoreable_pairs": expected_scoreable,
        "expected_over_context_pairs": expected_over_context,
        "reused_pairs": reused_pairs,
        "newly_scored_pairs": newly_scored_pairs,
        "retried_pairs": retried_pairs,
        "failed_pairs": failed_pairs,
        "elapsed_s": elapsed,
        "estimated_remaining_s": eta,
        "current_pair": current_pair,
        "gpu": gpu_status(),
    }


def log_progress(progress: dict[str, Any]) -> None:
    eta = progress["estimated_remaining_s"]
    eta_text = "unknown" if eta is None else f"{eta / 3600.0:.2f}h"
    print(
        "[progress] "
        f"{progress['timestamp_utc']} completed_scoreable={progress['completed_scoreable_pairs']}/"
        f"{progress['expected_scoreable_pairs']} over_context={progress['completed_over_context_pairs']}/"
        f"{progress['expected_over_context_pairs']} reused={progress['reused_pairs']} "
        f"newly_scored={progress['newly_scored_pairs']} retried={progress['retried_pairs']} "
        f"failed={progress['failed_pairs']} elapsed={progress['elapsed_s'] / 3600.0:.2f}h eta={eta_text} "
        f"gpu={progress['gpu']}",
        flush=True,
    )


def build_state_context(
    *,
    backend: Any,
    tokenizer: Any,
    examples: list[DecisionExample],
    prompt_profile: str,
    context_limit: int,
    output_dir: Path,
) -> tuple[dict[int, dict[str, Any]], list[int], list[int], dict[int, list[int]], dict[int, str]]:
    contexts: dict[int, dict[str, Any]] = {}
    prompt_lengths: list[int] = []
    target_lengths: list[int] = []
    target_ids_by_index: dict[int, list[int]] = {}
    target_text_by_index: dict[int, str] = {}
    for example_index, example in enumerate(examples):
        base_messages = _appworld_messages_from_example(example, prompt_profile)
        base_prompt = backend.render_messages(base_messages, add_generation_prompt=True)
        target_ids = _target_token_ids(tokenizer, example)
        target_text = _target_suffix(example)
        prompt_tokens = len(_token_ids(tokenizer, base_prompt, add_special_tokens=False))
        target_tokens = len(target_ids)
        if prompt_tokens + target_tokens > context_limit:
            raise ValueError(
                f"Base state target exceeds context: {example_index} total={prompt_tokens + target_tokens}"
            )
        contexts[example_index] = {
            "base_messages": base_messages,
            "base_prompt": base_prompt,
            "prompt_tokens": prompt_tokens,
            "target_tokens": target_tokens,
            "target_sha256": sha256_text(target_text),
            "target_token_sha256": sha256_text(",".join(str(item) for item in target_ids)),
        }
        target_ids_by_index[example_index] = target_ids
        target_text_by_index[example_index] = target_text
        prompt_lengths.append(prompt_tokens)
        target_lengths.append(target_tokens)
    atomic_write_json(output_dir / "state_context_summary.json", {"state_count": len(contexts), "rows": contexts})
    return contexts, prompt_lengths, target_lengths, target_ids_by_index, target_text_by_index


def score_l0_for_state(
    *,
    backend: Any,
    contexts: dict[int, dict[str, Any]],
    target_ids_by_index: dict[int, list[int]],
    target_text_by_index: dict[int, str],
    context_limit: int,
    example_index: int,
    l0_cache: dict[str, dict[str, Any]],
    l0_cache_path: Path,
) -> float:
    key = str(example_index)
    cached = l0_cache.get(key)
    if cached is not None and finite_float(cached.get("L0")):
        return float(cached["L0"])
    started = time.perf_counter()
    loss, prompt_tokens, target_tokens = _score_mean_target_nll(
        backend,
        contexts[example_index]["base_prompt"],
        target_ids_by_index[example_index],
        target_text_by_index[example_index],
        context_limit,
    )
    row = {
        "example_index": example_index,
        "state_example_id": contexts[example_index].get("state_example_id"),
        "L0": loss,
        "prompt_tokens": prompt_tokens,
        "target_tokens": target_tokens,
        "score_time_s": time.perf_counter() - started,
        "scoring_timestamp_utc": utc_now(),
    }
    l0_cache[key] = row
    atomic_write_json(l0_cache_path, l0_cache)
    return loss


def make_new_row(
    *,
    backend: Any,
    renderer_metadata: dict[str, Any],
    cache_generation_commit_sha: str,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    contexts: dict[int, dict[str, Any]],
    target_ids_by_index: dict[int, list[int]],
    example_index: int,
    memory_index: int,
    preflight: dict[str, Any],
    l0: float,
) -> dict[str, Any]:
    example = examples[example_index]
    record = records[memory_index]
    over_context = bool(preflight["over_context"])
    row = {
        "format": FULL_CACHE_VERSION,
        "scoring_definition": FULL_SCORING_DEFINITION,
        "pair_key": pair_key(example_index, memory_index),
        "state_memory_pair_id": state_memory_pair_id(example, record, example_index, memory_index),
        "pair_key_sha256": sha256_text(state_memory_pair_id(example, record, example_index, memory_index)),
        "state_example_id": _example_id(example_index, example),
        "example_index": example_index,
        "example_jsonl_line": example_index + 1,
        "task_id": _example_task_id(example),
        "episode_id": example.episode_id,
        "step_id": example.step_id,
        "candidate_memory_id": record.memory_id,
        "candidate_memory_index": memory_index,
        "candidate_memory_jsonl_line": memory_index + 1,
        "candidate_memory_task_id": record.task_id,
        "candidate_memory_episode_id": record.episode_id,
        "leakage_keys_state": sorted(_leakage_keys_for_example(example)),
        "leakage_keys_memory": sorted(_leakage_keys_for_record(record)),
        "leakage_overlap": sorted(_leakage_keys_for_example(example).intersection(_leakage_keys_for_record(record))),
        "L0": l0,
        "Lj_text": None,
        "text_utility": None,
        "utility_category": None,
        "score_status": "over_context" if over_context else "pending",
        "valid_for_loss": False,
        "state_prompt_tokens": preflight["state_prompt_tokens"],
        "raw_memory_tokens": preflight["raw_memory_tokens"],
        "combined_prompt_tokens": preflight["combined_prompt_tokens"],
        "target_tokens": preflight["target_tokens"],
        "total_tokens_with_target": preflight["total_tokens_with_target"],
        "context_limit": preflight["context_limit"],
        "over_context": over_context,
        "truncated": False,
        "target_sha256": contexts[example_index]["target_sha256"],
        "target_token_sha256": contexts[example_index]["target_token_sha256"],
        "memory_text_sha256": sha256_text(record.experience_text),
        "renderer_version": renderer_metadata["renderer_version"],
        "renderer_metadata": renderer_metadata,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": getattr(getattr(backend.model, "config", None), "_commit_hash", None),
        "source_commit_sha": cache_generation_commit_sha,
        "cache_generation_commit_sha": cache_generation_commit_sha,
        "commit_sha": cache_generation_commit_sha,
        "source_cache_format": None,
        "source_cache_path": None,
        "source_row_origin": "scored_full_cache",
        "scoring_timestamp_utc": utc_now(),
        "scoring_timestamp_source": "full_cache_scoring_time",
        "skipped_reason": "over_context" if over_context else None,
    }
    row.update(overlap_features(example, record))
    return row


def validate_full_cache(
    rows: list[dict[str, Any]],
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    expected_counts: dict[str, int],
    context_limit: int,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("pair_key"))
        if key in by_key:
            errors.append({"type": "duplicate_pair_key", "pair_key": key})
        by_key[key] = row
    expected_keys = {
        pair_key(example_index, memory_index)
        for example_index, example in enumerate(examples)
        for memory_index in legal_memory_indices(records, example)
    }
    missing_keys = sorted(expected_keys.difference(by_key))
    unexpected_keys = sorted(set(by_key).difference(expected_keys))
    for key in missing_keys[:20]:
        errors.append({"type": "missing_expected_pair", "pair_key": key})
    for key in unexpected_keys[:20]:
        errors.append({"type": "unexpected_pair", "pair_key": key})

    scoreable = 0
    over_context = 0
    failed = 0
    for row in rows:
        try:
            example_index = int(row["example_index"])
            memory_index = int(row["candidate_memory_index"])
        except Exception:
            errors.append({"type": "bad_indices", "pair_key": row.get("pair_key")})
            continue
        example = examples[example_index]
        record = records[memory_index]
        leakage_overlap = _leakage_keys_for_example(example).intersection(_leakage_keys_for_record(record))
        if leakage_overlap:
            errors.append({"type": "illegal_leakage_pair", "pair_key": row.get("pair_key"), "overlap": sorted(leakage_overlap)})
        if row.get("truncated") is not False:
            errors.append({"type": "truncated_or_missing_truncated_false", "pair_key": row.get("pair_key")})
        total_tokens = int(row["total_tokens_with_target"])
        row_over_context = bool(row.get("over_context"))
        if row_over_context != (total_tokens > context_limit):
            errors.append({"type": "over_context_flag_mismatch", "pair_key": row.get("pair_key")})
        status = row.get("score_status")
        if status == "scored":
            scoreable += 1
            if row.get("valid_for_loss") is not True:
                errors.append({"type": "scored_not_valid_for_loss", "pair_key": row.get("pair_key")})
            if row_over_context:
                errors.append({"type": "scored_over_context", "pair_key": row.get("pair_key")})
            if not (finite_float(row.get("L0")) and finite_float(row.get("Lj_text")) and finite_float(row.get("text_utility"))):
                errors.append({"type": "nonfinite_scored_loss", "pair_key": row.get("pair_key")})
            else:
                expected_utility = float(row["L0"]) - float(row["Lj_text"])
                if abs(expected_utility - float(row["text_utility"])) > REPRO_TOLERANCE:
                    errors.append({"type": "utility_mismatch", "pair_key": row.get("pair_key")})
        elif status == "over_context":
            over_context += 1
            if row.get("valid_for_loss") is not False:
                errors.append({"type": "over_context_valid_for_loss", "pair_key": row.get("pair_key")})
            if row.get("Lj_text") is not None or row.get("text_utility") is not None:
                errors.append({"type": "over_context_has_utility", "pair_key": row.get("pair_key")})
            if not row_over_context:
                errors.append({"type": "over_context_status_but_in_context", "pair_key": row.get("pair_key")})
        else:
            failed += 1
            errors.append({"type": "unexpected_score_status", "pair_key": row.get("pair_key"), "status": status})
    count_errors = []
    actual_counts = {
        "state_count": len(examples),
        "memory_count": len(records),
        "legal_pair_count": len(rows),
        "scoreable_pair_count": scoreable,
        "over_context_pair_count": over_context,
        "failed_pair_count": failed,
    }
    for key, expected in expected_counts.items():
        actual = actual_counts.get(key)
        if actual != expected:
            count_errors.append({"count": key, "expected": expected, "actual": actual})
    return {
        "format": FULL_VALIDATION_VERSION,
        "passed": not errors and not count_errors,
        "actual_counts": actual_counts,
        "expected_counts": expected_counts,
        "count_errors": count_errors,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "missing_key_count": len(missing_keys),
        "unexpected_key_count": len(unexpected_keys),
    }


def aggregate_numeric(rows: list[dict[str, Any]], value_key: str = "text_utility") -> dict[str, Any]:
    values = [float(row[value_key]) for row in rows if finite_float(row.get(value_key))]
    return _distribution(values)


def sign_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(row.get("utility_category") for row in rows if row.get("score_status") == "scored"))


def aggregate_group(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if isinstance(value, list):
            values = value or ["<none>"]
        else:
            values = [value if value is not None else "<none>"]
        for item in values:
            groups[str(item)].append(row)
    output = []
    for value, group_rows in sorted(groups.items(), key=lambda item: item[0]):
        scored = [row for row in group_rows if row.get("score_status") == "scored"]
        output.append(
            {
                key: value,
                "row_count": len(group_rows),
                "scored_count": len(scored),
                "over_context_count": sum(1 for row in group_rows if row.get("score_status") == "over_context"),
                "utility": aggregate_numeric(scored),
                "sign_counts": sign_counts(scored),
            }
        )
    return output


def entropy_and_concentration(values: list[float]) -> dict[str, Any]:
    positives = [max(0.0, value) for value in values if value > 0.0]
    mass = sum(positives)
    if mass <= 0:
        return {"positive_utility_mass": 0.0, "positive_entropy": None, "positive_top1_concentration": None}
    probs = [value / mass for value in positives]
    entropy = -sum(prob * math.log(prob) for prob in probs if prob > 0)
    return {
        "positive_utility_mass": mass,
        "positive_entropy": entropy,
        "positive_normalized_entropy": entropy / math.log(len(probs)) if len(probs) > 1 else 0.0,
        "positive_top1_concentration": max(positives) / mass,
    }


def build_per_state(rows: list[dict[str, Any]], examples: list[DecisionExample]) -> list[dict[str, Any]]:
    by_state: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_state[int(row["example_index"])].append(row)
    output = []
    for example_index in sorted(by_state):
        state_rows = by_state[example_index]
        scored = [row for row in state_rows if row.get("score_status") == "scored"]
        utilities = [float(row["text_utility"]) for row in scored]
        counts = Counter(row.get("utility_category") for row in scored)
        threshold_counts = {
            f"utility_above_{threshold:.2f}": sum(1 for value in utilities if value >= threshold)
            for threshold in UTILITY_THRESHOLDS
        }
        output.append(
            {
                "example_index": example_index,
                "state_example_id": _example_id(example_index, examples[example_index]),
                "task_id": _example_task_id(examples[example_index]),
                "episode_id": examples[example_index].episode_id,
                "step_id": examples[example_index].step_id,
                "L0": state_rows[0].get("L0"),
                "legal_memory_count": len(state_rows),
                "valid_memory_count": len(scored),
                "over_context_memory_count": sum(1 for row in state_rows if row.get("score_status") == "over_context"),
                "positive_count": int(counts.get("positive", 0)),
                "neutral_count": int(counts.get("neutral", 0)),
                "negative_count": int(counts.get("negative", 0)),
                "best_memory_id": max(scored, key=lambda row: float(row["text_utility"]))["candidate_memory_id"] if scored else None,
                "best_utility": max(utilities) if utilities else None,
                "worst_memory_id": min(scored, key=lambda row: float(row["text_utility"]))["candidate_memory_id"] if scored else None,
                "worst_utility": min(utilities) if utilities else None,
                **entropy_and_concentration(utilities),
                **threshold_counts,
            }
        )
    return output


def build_per_memory(rows: list[dict[str, Any]], records: list[MemoryRecord]) -> list[dict[str, Any]]:
    by_memory: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_memory[int(row["candidate_memory_index"])].append(row)
    output = []
    for memory_index in sorted(by_memory):
        memory_rows = by_memory[memory_index]
        scored = [row for row in memory_rows if row.get("score_status") == "scored"]
        utilities = [float(row["text_utility"]) for row in scored]
        counts = Counter(row.get("utility_category") for row in scored)
        output.append(
            {
                "memory_index": memory_index,
                "memory_id": records[memory_index].memory_id,
                "memory_task_id": records[memory_index].task_id,
                "memory_episode_id": records[memory_index].episode_id,
                "valid_state_count": len(scored),
                "over_context_state_count": sum(1 for row in memory_rows if row.get("score_status") == "over_context"),
                "positive_count": int(counts.get("positive", 0)),
                "neutral_count": int(counts.get("neutral", 0)),
                "negative_count": int(counts.get("negative", 0)),
                "mean_utility": float(statistics.fmean(utilities)) if utilities else None,
                "median_utility": float(statistics.median(utilities)) if utilities else None,
                "max_utility": max(utilities) if utilities else None,
                "min_utility": min(utilities) if utilities else None,
                "states_helped": sum(1 for value in utilities if value > UTILITY_NEUTRAL_EPS),
                "states_harmed": sum(1 for value in utilities if value < -UTILITY_NEUTRAL_EPS),
            }
        )
    return output


def add_strata(rows: list[dict[str, Any]], examples: list[DecisionExample]) -> None:
    prompt_lengths = sorted(int(row["state_prompt_tokens"]) for row in rows)
    memory_lengths = sorted(int(row["raw_memory_tokens"]) for row in rows)
    target_lengths = sorted(int(row["target_tokens"]) for row in rows)
    prompt_q1, prompt_q2 = quantile(prompt_lengths, 1 / 3), quantile(prompt_lengths, 2 / 3)
    memory_q1, memory_q2 = quantile(memory_lengths, 1 / 3), quantile(memory_lengths, 2 / 3)
    target_q1, target_q2 = quantile(target_lengths, 1 / 3), quantile(target_lengths, 2 / 3)
    max_step_by_episode: dict[str, int] = defaultdict(int)
    for example in examples:
        max_step_by_episode[example.episode_id] = max(max_step_by_episode[example.episode_id], example.step_id)
    for row in rows:
        example = examples[int(row["example_index"])]
        row["step_bucket"] = step_bucket(example, max_step_by_episode)
        row["prompt_length_bucket"] = bucket_by_quantiles(int(row["state_prompt_tokens"]), prompt_q1, prompt_q2)
        row["memory_length_bucket"] = bucket_by_quantiles(int(row["raw_memory_tokens"]), memory_q1, memory_q2)
        row["target_length_bucket"] = bucket_by_quantiles(int(row["target_tokens"]), target_q1, target_q2)


def overlap_correlations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("score_status") == "scored"]
    utilities = [float(row["text_utility"]) for row in scored]
    output: dict[str, Any] = {}
    for key in (
        "same_app",
        "shared_api_count",
        "shared_state_api_count",
        "normalized_target_substring_in_memory",
        "shared_code_token_count",
        "code_token_jaccard",
        "target_code_token_count",
        "memory_code_token_count",
    ):
        values = [float(row.get(key) or 0.0) for row in scored]
        output[f"utility_vs_{key}"] = _pearson(utilities, values)
    return output


def missingness_analysis(rows: list[dict[str, Any]], per_state: list[dict[str, Any]], per_memory: list[dict[str, Any]]) -> dict[str, Any]:
    memory_rows = [
        {
            "memory_id": row["memory_id"],
            "over_context_state_count": row["over_context_state_count"],
            "valid_state_count": row["valid_state_count"],
            "missing_fraction": row["over_context_state_count"] / (row["over_context_state_count"] + row["valid_state_count"])
            if (row["over_context_state_count"] + row["valid_state_count"]) > 0
            else None,
        }
        for row in per_memory
    ]
    scored_rows = [row for row in rows if row.get("score_status") == "scored"]
    utilities = [float(row["text_utility"]) for row in scored_rows]
    memory_lengths = [float(row["raw_memory_tokens"]) for row in scored_rows]
    return {
        "over_context_by_state_top20": sorted(
            [
                {
                    "state_example_id": row["state_example_id"],
                    "task_id": row["task_id"],
                    "over_context_memory_count": row["over_context_memory_count"],
                    "valid_memory_count": row["valid_memory_count"],
                }
                for row in per_state
            ],
            key=lambda row: (-row["over_context_memory_count"], row["state_example_id"]),
        )[:20],
        "over_context_by_memory_top20": sorted(
            memory_rows,
            key=lambda row: (-(row["missing_fraction"] or 0.0), -row["over_context_state_count"], row["memory_id"]),
        )[:20],
        "memory_length_missingness": aggregate_group(rows, "memory_length_bucket"),
        "utility_vs_memory_tokens_for_valid_rows": _pearson(utilities, memory_lengths),
        "states_with_no_positive": [row["state_example_id"] for row in per_state if row["positive_count"] == 0],
        "states_with_no_negative": [row["state_example_id"] for row in per_state if row["negative_count"] == 0],
    }


def build_split_manifest(examples: list[DecisionExample], seed: int, validation_fraction: float) -> dict[str, Any]:
    import random

    by_task: dict[str, list[str]] = defaultdict(list)
    for index, example in enumerate(examples):
        by_task[_example_task_id(example)].append(_example_id(index, example))
    tasks = sorted(by_task)
    rng = random.Random(seed)
    shuffled = list(tasks)
    rng.shuffle(shuffled)
    validation_count = max(1, round(len(tasks) * validation_fraction))
    validation_tasks = sorted(shuffled[:validation_count])
    train_tasks = sorted(task for task in tasks if task not in set(validation_tasks))
    return {
        "format": "raw_text_teacher_future_student_split_manifest_v1",
        "seed": seed,
        "validation_fraction": validation_fraction,
        "task_grouping": "task_id",
        "train_task_ids": train_tasks,
        "validation_task_ids": validation_tasks,
        "train_example_ids": [example_id for task in train_tasks for example_id in by_task[task]],
        "validation_example_ids": [example_id for task in validation_tasks for example_id in by_task[task]],
        "task_count": len(tasks),
        "train_task_count": len(train_tasks),
        "validation_task_count": len(validation_tasks),
    }


def representative_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    scored = [row for row in rows if row.get("score_status") == "scored"]
    high_positive = sorted(scored, key=lambda row: (-float(row["text_utility"]), row["state_example_id"], row["candidate_memory_id"]))[:10]
    high_negative = sorted(scored, key=lambda row: (float(row["text_utility"]), row["state_example_id"], row["candidate_memory_id"]))[:10]
    high_positive_low_overlap = [
        row
        for row in high_positive + sorted(scored, key=lambda row: -float(row["text_utility"]))
        if not row["same_app"] and int(row["shared_api_count"]) == 0 and float(row["code_token_jaccard"]) <= 0.05
    ][:10]
    high_overlap_low_utility = [
        row
        for row in sorted(
            scored,
            key=lambda row: (
                -float(row["code_token_jaccard"]),
                -int(row["shared_api_count"]),
                float(row["text_utility"]),
            ),
        )
        if (float(row["code_token_jaccard"]) >= 0.20 or int(row["shared_api_count"]) > 0 or row["normalized_target_substring_in_memory"])
        and float(row["text_utility"]) <= UTILITY_NEUTRAL_EPS
    ][:10]
    anomalous = [
        row
        for row in sorted(scored, key=lambda row: (-abs(float(row["text_utility"])), row["state_example_id"]))
        if (abs(float(row["text_utility"])) >= 1.0 and not row["same_app"] and int(row["shared_api_count"]) == 0)
        or row["normalized_target_substring_in_memory"]
    ][:10]

    def compact(row: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "pair_key",
            "state_example_id",
            "task_id",
            "candidate_memory_id",
            "candidate_memory_task_id",
            "text_utility",
            "L0",
            "Lj_text",
            "same_app",
            "shared_api_count",
            "code_token_jaccard",
            "normalized_target_substring_in_memory",
            "leakage_overlap",
            "renderer_version",
            "teacher_memory_section_version",
        ]
        return {key: row.get(key) for key in keys}

    return {
        "highest_positive": [compact(row) for row in high_positive],
        "highest_negative": [compact(row) for row in high_negative],
        "high_positive_low_overlap": [compact(row) for row in high_positive_low_overlap],
        "high_overlap_low_or_negative_utility": [compact(row) for row in high_overlap_low_utility],
        "apparent_anomalous_rows": [compact(row) for row in anomalous],
    }


def inspect_representatives(
    rows: list[dict[str, Any]],
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    backend: Any,
    prompt_profile: str,
) -> dict[str, Any]:
    selected_pair_keys: set[str] = set()
    reps = representative_rows(rows)
    for group_rows in reps.values():
        for row in group_rows:
            selected_pair_keys.add(str(row["pair_key"]))
    inspected = []
    markers = [
        "[TEACHER-ONLY RAW MEMORY START]",
        "[RAW MEMORY TEXT START]",
        "[RAW MEMORY TEXT END]",
        "[TEACHER-ONLY RAW MEMORY END]",
        "[CURRENT APPWORLD STATE START]",
        "[CURRENT APPWORLD STATE END]",
    ]
    row_by_key = {row["pair_key"]: row for row in rows}
    for key in sorted(selected_pair_keys):
        row = row_by_key[key]
        example_index = int(row["example_index"])
        memory_index = int(row["candidate_memory_index"])
        example = examples[example_index]
        record = records[memory_index]
        prompt = backend.render_messages(
            messages_with_teacher_memory(_appworld_messages_from_example(example, prompt_profile), record, prompt_profile),
            add_generation_prompt=True,
        )
        counts = {marker: prompt.count(marker) for marker in markers}
        positions = {marker: prompt.find(marker) for marker in markers}
        section_order_ok = (
            positions["[TEACHER-ONLY RAW MEMORY START]"]
            < positions["[RAW MEMORY TEXT START]"]
            < positions["[RAW MEMORY TEXT END]"]
            < positions["[TEACHER-ONLY RAW MEMORY END]"]
            < positions["[CURRENT APPWORLD STATE START]"]
            < positions["[CURRENT APPWORLD STATE END]"]
        )
        leakage_overlap = sorted(_leakage_keys_for_example(example).intersection(_leakage_keys_for_record(record)))
        item = {
            "pair_key": key,
            "state_example_id": row["state_example_id"],
            "candidate_memory_id": row["candidate_memory_id"],
            "text_utility": row.get("text_utility"),
            "leakage_overlap": leakage_overlap,
            "delimiter_counts_ok": all(value == 1 for value in counts.values()),
            "section_order_ok": section_order_ok,
            "memory_id_present": row["candidate_memory_id"] in prompt,
            "memory_task_id_present": str(row.get("candidate_memory_task_id")) in prompt,
            "target_hash_matches": row["target_sha256"] == sha256_text(_target_suffix(example)),
            "memory_text_hash_matches": row["memory_text_sha256"] == sha256_text(record.experience_text),
        }
        item["obvious_issue"] = bool(
            item["leakage_overlap"]
            or not item["delimiter_counts_ok"]
            or not item["section_order_ok"]
            or not item["memory_id_present"]
            or not item["memory_task_id_present"]
            or not item["target_hash_matches"]
            or not item["memory_text_hash_matches"]
        )
        inspected.append(item)
    return {
        "format": "raw_text_memory_teacher_full_cache_representative_inspection_v1",
        "selected_pair_count": len(inspected),
        "obvious_issue_count": sum(1 for row in inspected if row["obvious_issue"]),
        "representative_groups": reps,
        "rows": inspected,
    }


def compare_with_audit3b(rows: list[dict[str, Any]], audit3b_summary_path: Path) -> dict[str, Any]:
    if not audit3b_summary_path.exists():
        return {"available": False, "reason": "audit3b_summary_missing"}
    audit3b = json.loads(audit3b_summary_path.read_text(encoding="utf-8"))
    scored = [row for row in rows if row.get("score_status") == "scored"]
    full_counts = sign_counts(scored)
    full_dist = aggregate_numeric(scored)
    full_total = max(1, len(scored))
    audit_total = max(1, int(audit3b["utility_distribution"]["count"]))
    full_props = {key: full_counts.get(key, 0) / full_total for key in ("positive", "neutral", "negative")}
    audit_props = {
        key: audit3b["utility_counts"].get(key, 0) / audit_total
        for key in ("positive", "neutral", "negative")
    }
    mean_diff = (
        float(full_dist["mean"]) - float(audit3b["utility_distribution"]["mean"])
        if full_dist["mean"] is not None and audit3b["utility_distribution"]["mean"] is not None
        else None
    )
    max_prop_diff = max(abs(full_props[key] - audit_props[key]) for key in full_props)
    audit_state_ids = {
        row["state_example_id"]
        for row in audit3b.get("expanded_audit_metrics", {}).get("per_state", [])
    }
    audit_subset = [
        row
        for row in scored
        if row["state_example_id"] in audit_state_ids
    ]

    def coverage(source_rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "state_count": len({row["state_example_id"] for row in source_rows}),
            "row_count": len(source_rows),
            "state_apps": sorted({app for row in source_rows for app in row.get("state_apps", [])}),
            "memory_apps": sorted({app for row in source_rows for app in row.get("memory_apps", [])}),
            "step_bucket_counts": dict(Counter(row.get("step_bucket") for row in source_rows)),
            "prompt_length_bucket_counts": dict(Counter(row.get("prompt_length_bucket") for row in source_rows)),
            "memory_length_bucket_counts": dict(Counter(row.get("memory_length_bucket") for row in source_rows)),
            "target_length_bucket_counts": dict(Counter(row.get("target_length_bucket") for row in source_rows)),
        }

    representative = bool(max_prop_diff <= 0.05 and mean_diff is not None and abs(mean_diff) <= 0.05)
    return {
        "available": True,
        "audit3b_summary_path": str(audit3b_summary_path),
        "full_sign_proportions": full_props,
        "audit3b_sign_proportions": audit_props,
        "full_distribution": full_dist,
        "audit3b_distribution": audit3b["utility_distribution"],
        "full_length_correlations": {
            "utility_vs_memory_tokens": _pearson([float(row["text_utility"]) for row in scored], [float(row["raw_memory_tokens"]) for row in scored]),
            "utility_vs_combined_context_tokens": _pearson([float(row["text_utility"]) for row in scored], [float(row["total_tokens_with_target"]) for row in scored]),
        },
        "audit3b_length_correlations": audit3b.get("correlations"),
        "coverage_comparison": {
            "full": coverage(scored),
            "audit3b_state_subset": coverage(audit_subset),
        },
        "max_sign_proportion_abs_diff": max_prop_diff,
        "mean_utility_diff_full_minus_audit3b": mean_diff,
        "pilot_was_representative": representative,
        "representativeness_statement": (
            "The 24-state audit was reasonably representative by the configured sign-proportion and mean-utility thresholds."
            if representative
            else "The 24-state audit was not fully representative by the configured sign-proportion and mean-utility thresholds."
        ),
    }


def build_report_summary(
    *,
    rows: list[dict[str, Any]],
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    runtime_s: float,
    source_commit: str,
    output_dir: Path,
    cache_stats: dict[str, Any],
    validation: dict[str, Any],
    representative_inspection: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    add_strata(rows, examples)
    scored = [row for row in rows if row.get("score_status") == "scored"]
    over_context = [row for row in rows if row.get("score_status") == "over_context"]
    utilities = [float(row["text_utility"]) for row in scored]
    per_state = build_per_state(rows, examples)
    per_memory = build_per_memory(rows, records)
    split_manifest = build_split_manifest(examples, seed=13, validation_fraction=0.20)
    stratified = {
        "state_app": aggregate_group(scored, "state_apps"),
        "memory_app": aggregate_group(scored, "memory_apps"),
        "step_bucket": aggregate_group(scored, "step_bucket"),
        "prompt_length_bucket": aggregate_group(scored, "prompt_length_bucket"),
        "memory_length_bucket": aggregate_group(scored, "memory_length_bucket"),
        "same_app": aggregate_group(scored, "same_app"),
        "target_length_bucket": aggregate_group(scored, "target_length_bucket"),
    }
    overlap = {
        "correlations": overlap_correlations(scored),
        "same_app": aggregate_group(scored, "same_app"),
        "shared_api_count": aggregate_group(scored, "shared_api_count"),
        "normalized_target_substring_in_memory": aggregate_group(scored, "normalized_target_substring_in_memory"),
        "code_token_overlap_bucket": aggregate_group(
            [
                {
                    **row,
                    "code_token_overlap_bucket": (
                        "zero"
                        if float(row.get("code_token_jaccard") or 0.0) == 0
                        else "low"
                        if float(row.get("code_token_jaccard") or 0.0) < 0.10
                        else "medium"
                        if float(row.get("code_token_jaccard") or 0.0) < 0.30
                        else "high"
                    ),
                }
                for row in scored
            ],
            "code_token_overlap_bucket",
        ),
    }
    summary = {
        "format": FULL_REPORT_VERSION,
        "cache_version": FULL_CACHE_VERSION,
        "scoring_definition": FULL_SCORING_DEFINITION,
        "source_commit": source_commit,
        "output_dir": str(output_dir),
        "global": {
            "state_count": len(examples),
            "memory_count": len(records),
            "legal_pair_count": len(rows),
            "scoreable_pair_count": len(scored),
            "over_context_pair_count": len(over_context),
            "positive_neutral_negative_counts": sign_counts(scored),
            "positive_neutral_negative_proportions": {
                key: sign_counts(scored).get(key, 0) / len(scored) if scored else None
                for key in ("positive", "neutral", "negative")
            },
            "utility_distribution": _distribution(utilities),
            "runtime_s": runtime_s,
            "actual_h100_hours": runtime_s / 3600.0,
        },
        "cache_stats": cache_stats,
        "validation": validation,
        "per_state": per_state,
        "per_memory": per_memory,
        "stratified": stratified,
        "overlap_diagnostics": overlap,
        "missingness": missingness_analysis(rows, per_state, per_memory),
        "future_student_split_manifest": split_manifest,
        "representative_inspection": representative_inspection,
        "audit3b_comparison": comparison,
        "artifacts": {
            "teacher_cache_jsonl": str(output_dir / "teacher_cache_full_rows.jsonl"),
            "summary_json": str(output_dir / "summary.json"),
            "validation_json": str(output_dir / "validation.json"),
            "report_md": str(output_dir / "report.md"),
            "progress_json": str(output_dir / "progress.json"),
            "student_split_manifest": str(output_dir / "student_split_manifest.json"),
        },
    }
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    global_summary = summary["global"]
    dist = global_summary["utility_distribution"]
    validation = summary["validation"]
    comparison = summary["audit3b_comparison"]
    lines = [
        "# Milestone 3C Complete All-Legal Raw-Text Teacher Cache",
        "",
        f"- cache version: `{summary['cache_version']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- scoring definition: `{summary['scoring_definition']}`",
        f"- legal pairs: {global_summary['legal_pair_count']}",
        f"- scoreable pairs: {global_summary['scoreable_pair_count']}",
        f"- over-context pairs: {global_summary['over_context_pair_count']}",
        f"- runtime seconds: {global_summary['runtime_s']:.2f}",
        f"- actual H100 hours: {global_summary['actual_h100_hours']:.2f}",
        f"- validation passed: {validation['passed']}",
        "",
        "## Utility",
        "",
        f"- counts: {global_summary['positive_neutral_negative_counts']}",
        f"- proportions: {global_summary['positive_neutral_negative_proportions']}",
        f"- mean/std: {dist['mean']} / {dist['std']}",
        f"- p05/p25/p50/p75/p95: {dist['p05']} / {dist['p25']} / {dist['p50']} / {dist['p75']} / {dist['p95']}",
        f"- min/max: {dist['min']} / {dist['max']}",
        "",
        "## Cache Reuse",
        "",
        f"- reused unique pairs: {summary['cache_stats']['reused_unique_pairs']}",
        f"- newly scored pairs: {summary['cache_stats']['newly_scored_pairs']}",
        f"- over-context rows generated new: {summary['cache_stats']['new_over_context_pairs']}",
        f"- retried pairs: {summary['cache_stats']['retried_pairs']}",
        f"- failed pairs: {summary['cache_stats']['failed_pairs']}",
        "",
        "## Missingness",
        "",
        f"- states with no positive valid memory: {len(summary['missingness']['states_with_no_positive'])}",
        f"- states with no negative valid memory: {len(summary['missingness']['states_with_no_negative'])}",
        f"- top missing memory: {summary['missingness']['over_context_by_memory_top20'][:1]}",
        "",
        "## Overlap Diagnostics",
        "",
        f"- correlations: {summary['overlap_diagnostics']['correlations']}",
        "",
        "## Audit3B Comparison",
        "",
        f"- representative: {comparison.get('pilot_was_representative')}",
        f"- statement: {comparison.get('representativeness_statement')}",
        "",
        "## Artifacts",
        "",
    ]
    for key, value in summary["artifacts"].items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the complete all-legal raw-text teacher cache.")
    parser.add_argument("--config", default="configs/benchmark/appworld_rcmf_full_prompt.yaml")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pilot-dir", required=True)
    parser.add_argument("--audit3b-dir", required=True)
    parser.add_argument("--progress-interval-s", type=float, default=300.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--expected-state-count", type=int, default=EXPECTED_STATE_COUNT)
    parser.add_argument("--expected-memory-count", type=int, default=EXPECTED_MEMORY_COUNT)
    parser.add_argument("--expected-legal-pairs", type=int, default=EXPECTED_LEGAL_PAIR_COUNT)
    parser.add_argument("--expected-scoreable-pairs", type=int, default=EXPECTED_SCOREABLE_PAIR_COUNT)
    parser.add_argument("--expected-over-context-pairs", type=int, default=EXPECTED_OVER_CONTEXT_PAIR_COUNT)
    args = parser.parse_args()

    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data)
    pilot_dir = Path(args.pilot_dir)
    audit3b_dir = Path(args.audit3b_dir)
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Complete raw-text teacher cache requires hf_qwen backend")
    backend = build_backend(cfg, load_model=True)
    tokenizer = backend.tokenizer
    prompt_profile = cfg.benchmark.prompt_profile
    context_limit = _context_limit_for_backend(backend)
    renderer_metadata = appworld_renderer_metadata(prompt_profile, add_generation_prompt=True)
    source_commit = maybe_git_commit() or "unknown"
    expected_checkpoint_identity = f"frozen_hf_pretrained:{backend.model_name}"

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    if len(examples) != args.expected_state_count or len(records) != args.expected_memory_count:
        raise ValueError(
            f"Dataset count mismatch: states={len(examples)} records={len(records)} "
            f"expected={args.expected_state_count}/{args.expected_memory_count}"
        )
    contexts, _prompt_lengths, _target_lengths, target_ids_by_index, target_text_by_index = build_state_context(
        backend=backend,
        tokenizer=tokenizer,
        examples=examples,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
        output_dir=output_dir,
    )
    for index, example in enumerate(examples):
        contexts[index]["state_example_id"] = _example_id(index, example)
    raw_memory_tokens = [
        len(_token_ids(tokenizer, record.experience_text, add_special_tokens=False))
        for record in records
    ]
    target_token_hashes = {
        index: contexts[index]["target_token_sha256"]
        for index in range(len(examples))
    }
    cache_sources, cache_validation_stats = merge_cached_rows(
        [
            pilot_dir / "teacher_labels.jsonl",
            audit3b_dir / "teacher_labels_audit3b.jsonl",
        ],
        examples=examples,
        records=records,
        backend_model_name=backend.model_name,
        renderer_version=renderer_metadata["renderer_version"],
        expected_checkpoint_identity=expected_checkpoint_identity,
        target_token_hashes=target_token_hashes,
        cache_generation_commit_sha=source_commit,
    )
    atomic_write_json(output_dir / "cache_validation_sources.json", cache_validation_stats)

    rows_path = output_dir / "teacher_cache_full_rows.jsonl"
    completed_rows, existing_stats = load_existing_completed_rows(rows_path, FULL_CACHE_VERSION)
    reusable_to_append = {
        key: row
        for key, row in cache_sources.items()
        if key not in completed_rows
    }
    for key in sorted(reusable_to_append):
        completed_rows[key] = reusable_to_append[key]
        append_jsonl(rows_path, reusable_to_append[key])
    l0_cache_path = output_dir / "l0_cache.json"
    l0_cache = json.loads(l0_cache_path.read_text(encoding="utf-8")) if l0_cache_path.exists() else {}
    for row in completed_rows.values():
        if finite_float(row.get("L0")):
            l0_cache.setdefault(str(row["example_index"]), {"L0": float(row["L0"]), "source": "completed_or_cached_row"})
    atomic_write_json(l0_cache_path, l0_cache)

    expected_counts = {
        "state_count": args.expected_state_count,
        "memory_count": args.expected_memory_count,
        "legal_pair_count": args.expected_legal_pairs,
        "scoreable_pair_count": args.expected_scoreable_pairs,
        "over_context_pair_count": args.expected_over_context_pairs,
    }
    all_pairs = [
        (example_index, memory_index)
        for example_index, example in enumerate(examples)
        for memory_index in legal_memory_indices(records, example)
    ]
    if len(all_pairs) != args.expected_legal_pairs:
        raise ValueError(f"Legal pair count mismatch: {len(all_pairs)} != {args.expected_legal_pairs}")

    reused_pairs = sum(1 for row in completed_rows.values() if row.get("source_cache_path"))
    newly_scored_pairs = sum(1 for row in completed_rows.values() if row.get("source_row_origin") == "scored_full_cache")
    new_over_context_pairs = sum(
        1
        for row in completed_rows.values()
        if row.get("score_status") == "over_context" and row.get("source_cache_path") is None
    )
    retried_pairs = 0
    failed_pairs = 0
    last_progress = 0.0
    progress = progress_snapshot(
        started=started,
        expected_scoreable=args.expected_scoreable_pairs,
        expected_over_context=args.expected_over_context_pairs,
        rows=completed_rows,
        reused_pairs=reused_pairs,
        newly_scored_pairs=newly_scored_pairs,
        retried_pairs=retried_pairs,
        failed_pairs=failed_pairs,
        current_pair=None,
    )
    atomic_write_json(output_dir / "progress.json", progress)
    log_progress(progress)

    for example_index, memory_index in all_pairs:
        key = pair_key(example_index, memory_index)
        if key in completed_rows and completed_rows[key].get("score_status") in {"scored", "over_context"}:
            continue
        l0 = score_l0_for_state(
            backend=backend,
            contexts=contexts,
            target_ids_by_index=target_ids_by_index,
            target_text_by_index=target_text_by_index,
            context_limit=context_limit,
            example_index=example_index,
            l0_cache=l0_cache,
            l0_cache_path=l0_cache_path,
        )
        preflight, memory_prompt = _preflight_pair(
            backend=backend,
            tokenizer=tokenizer,
            base_messages=contexts[example_index]["base_messages"],
            prompt_profile=prompt_profile,
            example_index=example_index,
            memory_index=memory_index,
            examples=examples,
            records=records,
            state_prompt_tokens=int(contexts[example_index]["prompt_tokens"]),
            raw_memory_tokens=raw_memory_tokens[memory_index],
            target_tokens=int(contexts[example_index]["target_tokens"]),
            context_limit=context_limit,
        )
        row = make_new_row(
            backend=backend,
            renderer_metadata=renderer_metadata,
            cache_generation_commit_sha=source_commit,
            examples=examples,
            records=records,
            contexts=contexts,
            target_ids_by_index=target_ids_by_index,
            example_index=example_index,
            memory_index=memory_index,
            preflight=preflight,
            l0=l0,
        )
        if row["score_status"] == "over_context":
            completed_rows[key] = row
            append_jsonl(rows_path, row)
            new_over_context_pairs += 1
        else:
            attempt = 0
            while True:
                try:
                    if attempt > 0:
                        retried_pairs += 1
                    lj, _prompt_tokens, _target_tokens = _score_mean_target_nll(
                        backend,
                        memory_prompt,
                        target_ids_by_index[example_index],
                        target_text_by_index[example_index],
                        context_limit,
                    )
                    utility = l0 - lj
                    row["Lj_text"] = lj
                    row["text_utility"] = utility
                    row["utility_category"] = _category(utility)
                    row["score_status"] = "scored"
                    row["valid_for_loss"] = True
                    row["skipped_reason"] = None
                    row["scoring_timestamp_utc"] = utc_now()
                    completed_rows[key] = row
                    append_jsonl(rows_path, row)
                    newly_scored_pairs += 1
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt > args.max_retries:
                        failed_pairs += 1
                        row["score_status"] = "failed"
                        row["valid_for_loss"] = False
                        row["skipped_reason"] = f"score_failed_after_{args.max_retries}_retries"
                        row["error"] = repr(exc)
                        completed_rows[key] = row
                        append_jsonl(rows_path, row)
                        break
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    time.sleep(min(30, 2**attempt))
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        now = time.perf_counter()
        if now - last_progress >= args.progress_interval_s:
            last_progress = now
            progress = progress_snapshot(
                started=started,
                expected_scoreable=args.expected_scoreable_pairs,
                expected_over_context=args.expected_over_context_pairs,
                rows=completed_rows,
                reused_pairs=reused_pairs,
                newly_scored_pairs=newly_scored_pairs,
                retried_pairs=retried_pairs,
                failed_pairs=failed_pairs,
                current_pair=key,
            )
            atomic_write_json(output_dir / "progress.json", progress)
            log_progress(progress)

    final_rows = [completed_rows[key] for key in sorted(completed_rows, key=lambda item: tuple(int(part[1:]) for part in item.split(":")))]
    add_strata(final_rows, examples)
    write_jsonl(rows_path, final_rows)
    validation = validate_full_cache(
        final_rows,
        examples=examples,
        records=records,
        expected_counts=expected_counts,
        context_limit=context_limit,
    )
    atomic_write_json(output_dir / "validation.json", validation)
    if not validation["passed"]:
        atomic_write_json(output_dir / "summary_failed_validation.json", {"validation": validation})
        raise RuntimeError(f"Full cache validation failed: {validation['errors_first_50'][:3]} {validation['count_errors']}")

    scored_rows = [row for row in final_rows if row.get("score_status") == "scored"]
    repro_rows = {
        "positive": max([row for row in scored_rows if row.get("utility_category") == "positive"], key=lambda row: float(row["text_utility"]), default=None),
        "neutral": min([row for row in scored_rows if row.get("utility_category") == "neutral"], key=lambda row: abs(float(row["text_utility"])), default=None),
        "negative": min([row for row in scored_rows if row.get("utility_category") == "negative"], key=lambda row: float(row["text_utility"]), default=None),
    }
    base_prompt_texts = {index: contexts[index]["base_prompt"] for index in range(len(examples))}
    base_messages = {index: contexts[index]["base_messages"] for index in range(len(examples))}
    reproducibility = _run_reproducibility_check(
        rows_by_name=repro_rows,
        backend=backend,
        base_prompt_texts=base_prompt_texts,
        base_messages_by_index=base_messages,
        prompt_profile=prompt_profile,
        examples=examples,
        records=records,
        target_ids_by_index=target_ids_by_index,
        target_text_by_index=target_text_by_index,
        context_limit=context_limit,
        tolerance=REPRO_TOLERANCE,
    )
    atomic_write_json(output_dir / "reproducibility_check.json", reproducibility)
    representative_inspection = inspect_representatives(
        final_rows,
        examples=examples,
        records=records,
        backend=backend,
        prompt_profile=prompt_profile,
    )
    atomic_write_json(output_dir / "representative_inspection.json", representative_inspection)
    split_manifest = build_split_manifest(examples, seed=13, validation_fraction=0.20)
    atomic_write_json(output_dir / "student_split_manifest.json", split_manifest)
    comparison = compare_with_audit3b(final_rows, audit3b_dir / "summary.json")
    runtime_s = time.perf_counter() - started
    cache_stats = {
        "cache_validation": cache_validation_stats,
        "existing_output_rows": existing_stats,
        "reused_unique_pairs": reused_pairs,
        "newly_scored_pairs": newly_scored_pairs,
        "new_over_context_pairs": new_over_context_pairs,
        "retried_pairs": retried_pairs,
        "failed_pairs": failed_pairs,
    }
    summary = build_report_summary(
        rows=final_rows,
        examples=examples,
        records=records,
        runtime_s=runtime_s,
        source_commit=source_commit,
        output_dir=output_dir,
        cache_stats=cache_stats,
        validation=validation,
        representative_inspection=representative_inspection,
        comparison=comparison,
    )
    summary["reproducibility_check"] = reproducibility
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", render_markdown(summary))
    final_progress = progress_snapshot(
        started=started,
        expected_scoreable=args.expected_scoreable_pairs,
        expected_over_context=args.expected_over_context_pairs,
        rows=completed_rows,
        reused_pairs=reused_pairs,
        newly_scored_pairs=newly_scored_pairs,
        retried_pairs=retried_pairs,
        failed_pairs=failed_pairs,
        current_pair=None,
    )
    final_progress["complete"] = True
    atomic_write_json(output_dir / "progress.json", final_progress)
    log_progress(final_progress)
    print(f"Wrote complete raw-text teacher cache to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
