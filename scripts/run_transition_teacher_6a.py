from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
from pathlib import Path
import re
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.oracle_capacity_5e import validate_target_token_utility_identity
from rcmf.training.pair_grounding_5d import add_teacher_delta_fields
from rcmf.training.transition_memory_6a import (
    TRAJECTORY_STATIC_BASELINE_VERSION,
    TRANSITION_RESPONSE_CACHE_VERSION,
    TRANSITION_TEACHER_CACHE_VERSION,
    canonical_json_sha256,
    messages_with_transition_memory,
    select_pair_oracle_subset,
    select_static_transitions,
    state_example_id,
    summarize_utility_rows,
    utility_category,
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
from scripts.build_stage_c1_response_cache import (
    _build_position_rows,
    _score_target_logits,
)
from scripts.run_raw_text_teacher_pilot import (
    _context_limit_for_backend,
    _score_mean_target_nll,
    _target_token_ids,
    _token_ids,
    messages_with_teacher_memory,
)


REPRO_TOLERANCE = 2.0e-4
CODE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")
API_RE = re.compile(r"\bapis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")


def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _load_unique_journal(path: Path, key: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    output: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in read_jsonl(path):
        value = str(row[key])
        if value in output:
            duplicates.append(value)
        output[value] = row
    if duplicates:
        raise ValueError(f"Duplicate journal keys in {path}: {duplicates[:20]}")
    return output


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _code_token_jaccard(left: str, right: str) -> float:
    left_tokens = {value.lower() for value in CODE_TOKEN_RE.findall(left)}
    right_tokens = {value.lower() for value in CODE_TOKEN_RE.findall(right)}
    union = left_tokens.union(right_tokens)
    return 0.0 if not union else len(left_tokens.intersection(right_tokens)) / len(union)


def _overlap_features(
    *,
    example: DecisionExample,
    query_manifest_row: Mapping[str, Any],
    transition: Mapping[str, Any],
) -> dict[str, Any]:
    target = example.target_text
    transition_text = "\n".join(
        [
            str(transition["source_task_goal"]),
            str(transition["canonical_pre_action_state"]),
            str(transition["complete_action"]),
            str(transition["complete_post_action_observation"]),
        ]
    )
    target_apis = {f"{app}.{api}" for app, api in API_RE.findall(target)}
    transition_apis = {str(value) for value in transition.get("api_names", [])}
    query_apps = {str(value) for value in query_manifest_row.get("apps", [])}
    transition_apps = {str(value) for value in transition.get("apps", [])}
    normalized_target = _normalize_text(target)
    normalized_transition = _normalize_text(transition_text)
    return {
        "query_apps": sorted(query_apps),
        "target_api_names": sorted(target_apis),
        "transition_api_names": sorted(transition_apis),
        "same_app": bool(query_apps.intersection(transition_apps)),
        "shared_api_count": len(target_apis.intersection(transition_apis)),
        "normalized_target_exact_substring_in_transition": bool(
            normalized_target and normalized_target in normalized_transition
        ),
        "target_transition_code_token_jaccard": _code_token_jaccard(
            target, transition_text
        ),
    }


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _query_contexts(
    *,
    backend: Any,
    examples: Sequence[DecisionExample],
    query_manifest: Mapping[str, Any],
    prompt_profile: str,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in query_manifest["query_rows"]:
        example_index = int(row["example_index"])
        example = examples[example_index]
        state_id = str(row["state_example_id"])
        if state_id != state_example_id(example_index, example):
            raise ValueError(f"Query state identity mismatch: {state_id}")
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt, prompt_metadata = _render_prompt_with_metadata(
            backend.tokenizer, messages, prompt_profile
        )
        target_ids = _target_token_ids(backend.tokenizer, example)
        output[state_id] = {
            "manifest": row,
            "example": example,
            "example_index": example_index,
            "base_messages": messages,
            "base_prompt": prompt,
            "prompt_metadata": prompt_metadata,
            "target_ids": target_ids,
            "target_text": _target_suffix(example),
        }
    return output


def _ensure_l0(
    *,
    backend: Any,
    context: Mapping[str, Any],
    context_limit: int,
    cache: dict[str, dict[str, Any]],
    cache_path: Path,
) -> float:
    state_id = str(context["manifest"]["state_example_id"])
    cached = cache.get(state_id)
    if cached is not None and _finite(cached.get("L0")):
        return float(cached["L0"])
    started = time.perf_counter()
    loss, prompt_tokens, target_tokens = _score_mean_target_nll(
        backend,
        str(context["base_prompt"]),
        list(context["target_ids"]),
        str(context["target_text"]),
        context_limit,
    )
    cache[state_id] = {
        "state_example_id": state_id,
        "L0": loss,
        "prompt_tokens": prompt_tokens,
        "target_tokens": target_tokens,
        "target_sha256": sha256_text(str(context["target_text"])),
        "target_token_sha256": sha256_text(
            ",".join(str(value) for value in context["target_ids"])
        ),
        "score_time_s": time.perf_counter() - started,
        "scoring_timestamp_utc": utc_now(),
    }
    atomic_write_json(cache_path, cache)
    return loss


def _validate_teacher_cache(
    *,
    rows: Sequence[dict[str, Any]],
    preflight_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    expected = {str(row["pair_id"]): row for row in preflight_rows}
    actual = {str(row["pair_id"]): row for row in rows}
    duplicate_count = len(rows) - len(actual)
    if duplicate_count:
        errors.append({"type": "duplicate_pair_keys", "count": duplicate_count})
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    if missing:
        errors.append({"type": "missing_pairs", "count": len(missing), "first": missing[:10]})
    if unexpected:
        errors.append(
            {"type": "unexpected_pairs", "count": len(unexpected), "first": unexpected[:10]}
        )
    for pair_id in sorted(set(expected).intersection(actual)):
        preflight = expected[pair_id]
        row = actual[pair_id]
        if row.get("leakage_overlap"):
            errors.append({"type": "leakage", "pair_id": pair_id})
        if row.get("truncated"):
            errors.append({"type": "truncated", "pair_id": pair_id})
        if bool(preflight["over_context"]):
            if not (
                row.get("score_status") == "over_context"
                and row.get("text_utility") is None
                and row.get("Lj_transition") is None
                and row.get("valid_for_loss") is False
            ):
                errors.append({"type": "invalid_over_context_row", "pair_id": pair_id})
        else:
            if not (
                row.get("score_status") == "scored"
                and row.get("valid_for_loss") is True
                and _finite(row.get("L0"))
                and _finite(row.get("Lj_transition"))
                and _finite(row.get("text_utility"))
            ):
                errors.append({"type": "invalid_scored_row", "pair_id": pair_id})
                continue
            expected_utility = float(row["L0"]) - float(row["Lj_transition"])
            if abs(expected_utility - float(row["text_utility"])) > REPRO_TOLERANCE:
                errors.append({"type": "utility_identity", "pair_id": pair_id})
        for key in (
            "state_prompt_tokens",
            "combined_prompt_tokens",
            "target_tokens",
            "total_tokens_with_target",
        ):
            if int(row[key]) != int(preflight[key]):
                errors.append(
                    {"type": f"preflight_{key}_mismatch", "pair_id": pair_id}
                )
    scoreable = sum(row.get("score_status") == "scored" for row in rows)
    over_context = sum(row.get("score_status") == "over_context" for row in rows)
    return {
        "format": "decision_transition_teacher_cache_validation_6a_v1",
        "expected_pair_count": len(preflight_rows),
        "actual_pair_count": len(rows),
        "scoreable_pair_count": scoreable,
        "over_context_pair_count": over_context,
        "duplicate_pair_count": duplicate_count,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "passed": not errors,
    }


def _reproducibility_check(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> dict[str, Any]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    selected = {
        "positive": max(scored, key=lambda row: float(row["text_utility"])),
        "neutral": min(scored, key=lambda row: abs(float(row["text_utility"]))),
        "negative": min(scored, key=lambda row: float(row["text_utility"])),
    }
    repeats = []
    for category, row in selected.items():
        context = contexts[str(row["state_example_id"])]
        transition = transitions[str(row["transition_id"])]
        messages = messages_with_transition_memory(
            context["base_messages"], transition, prompt_profile
        )
        prompt = backend.render_messages(messages, add_generation_prompt=True)
        loss, prompt_tokens, target_tokens = _score_mean_target_nll(
            backend,
            prompt,
            list(context["target_ids"]),
            str(context["target_text"]),
            context_limit,
        )
        repeats.append(
            {
                "category": category,
                "pair_id": row["pair_id"],
                "cached_Lj": row["Lj_transition"],
                "repeated_Lj": loss,
                "absolute_loss_difference": abs(loss - float(row["Lj_transition"])),
                "cached_utility": row["text_utility"],
                "repeated_utility": float(row["L0"]) - loss,
                "prompt_tokens": prompt_tokens,
                "target_tokens": target_tokens,
            }
        )
    return {
        "format": "decision_transition_teacher_reproducibility_6a_v1",
        "tolerance": REPRO_TOLERANCE,
        "rows": repeats,
        "passed": all(
            float(row["absolute_loss_difference"]) <= REPRO_TOLERANCE
            for row in repeats
        ),
    }


def _aggregate_by(
    rows: Sequence[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            grouped[str(item)].append(float(row["text_utility"]))
    output = {}
    for item, utilities in sorted(grouped.items()):
        output[item] = {
            "count": len(utilities),
            "mean": statistics.fmean(utilities),
            "median": statistics.median(utilities),
            "positive": sum(value > 0.01 for value in utilities),
            "neutral": sum(abs(value) <= 0.01 for value in utilities),
            "negative": sum(value < -0.01 for value in utilities),
        }
    return output


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(right) != len(left):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    return None if denominator == 0 else numerator / denominator


def _teacher_analysis(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    utilities = [float(row["text_utility"]) for row in scored]
    return {
        "format": "decision_transition_teacher_analysis_6a_v1",
        "utility": summarize_utility_rows(rows),
        "by_split": _aggregate_by(scored, "split"),
        "by_step_bucket": _aggregate_by(scored, "transition_step_bucket"),
        "by_app": _aggregate_by(scored, "transition_apps"),
        "by_api": _aggregate_by(scored, "transition_api_names"),
        "by_action_type": _aggregate_by(scored, "transition_action_type"),
        "same_app": _aggregate_by(scored, "same_app"),
        "exact_target_substring": _aggregate_by(
            scored, "normalized_target_exact_substring_in_transition"
        ),
        "correlations": {
            key: _pearson(utilities, [float(row[key]) for row in scored])
            for key in (
                "source_state_tokens",
                "action_tokens",
                "observation_tokens",
                "transition_section_tokens",
                "combined_prompt_tokens",
                "shared_api_count",
                "target_transition_code_token_jaccard",
            )
        },
        "target_exact_substring_count": sum(
            bool(row["normalized_target_exact_substring_in_transition"])
            for row in scored
        ),
    }


def _load_parent_rows(
    path: Path,
    *,
    state_ids: set[str],
    memory_ids: set[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    output = {}
    for row in read_jsonl(path):
        state_id = str(row.get("state_example_id"))
        memory_id = str(row.get("candidate_memory_id"))
        if state_id in state_ids and memory_id in memory_ids:
            output[(state_id, memory_id)] = row
    return output


def _parent_comparison(
    transition_rows: Sequence[dict[str, Any]],
    parent_rows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in transition_rows:
        if row.get("valid_for_loss"):
            grouped[(str(row["state_example_id"]), str(row["parent_memory_id"]))].append(row)
    comparisons = []
    for key, rows in sorted(grouped.items()):
        parent = parent_rows.get(key)
        parent_valid = bool(parent and parent.get("valid_for_loss"))
        best = max(rows, key=lambda row: float(row["text_utility"]))
        utilities = [float(row["text_utility"]) for row in rows]
        positive = [value for value in utilities if value > 0.01]
        parent_utility = float(parent["text_utility"]) if parent_valid else None
        comparisons.append(
            {
                "state_example_id": key[0],
                "parent_memory_id": key[1],
                "parent_task_id": str(best["parent_task_id"]),
                "transition_count": len(rows),
                "best_transition_id": best["transition_id"],
                "best_transition_utility": float(best["text_utility"]),
                "parent_whole_trajectory_valid": parent_valid,
                "parent_whole_trajectory_utility": parent_utility,
                "best_transition_minus_parent": (
                    None
                    if parent_utility is None
                    else float(best["text_utility"]) - parent_utility
                ),
                "helpful_transition_count": sum(value > 0.01 for value in utilities),
                "harmful_transition_count": sum(value < -0.01 for value in utilities),
                "harmful_inside_helpful_parent": bool(
                    parent_utility is not None
                    and parent_utility > 0.01
                    and any(value < -0.01 for value in utilities)
                ),
                "positive_utility_concentration_top1": (
                    None if not positive else max(positive) / sum(positive)
                ),
            }
        )
    matched = [row for row in comparisons if row["parent_whole_trajectory_valid"]]
    return (
        {
            "format": "decision_transition_parent_utility_comparison_6a_v1",
            "state_parent_group_count": len(comparisons),
            "matched_parent_teacher_count": len(matched),
            "unmatched_parent_teacher_count": len(comparisons) - len(matched),
            "best_transition_beats_parent_count": sum(
                float(row["best_transition_minus_parent"]) > 0
                for row in matched
            ),
            "best_transition_minus_parent_mean": (
                None
                if not matched
                else statistics.fmean(
                    float(row["best_transition_minus_parent"]) for row in matched
                )
            ),
            "helpful_parent_with_harmful_child_count": sum(
                bool(row["harmful_inside_helpful_parent"]) for row in comparisons
            ),
            "mean_helpful_transitions_per_group": statistics.fmean(
                int(row["helpful_transition_count"]) for row in comparisons
            ),
            "median_positive_top1_concentration": (
                statistics.median(
                    float(row["positive_utility_concentration_top1"])
                    for row in comparisons
                    if row["positive_utility_concentration_top1"] is not None
                )
                if any(
                    row["positive_utility_concentration_top1"] is not None
                    for row in comparisons
                )
                else None
            ),
        },
        comparisons,
    )


def _compact_inspection(row: Mapping[str, Any], transition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pair_id": row["pair_id"],
        "state_example_id": row["state_example_id"],
        "transition_id": row["transition_id"],
        "parent_memory_id": row["parent_memory_id"],
        "parent_task_id": row["parent_task_id"],
        "transition_step_index": row["transition_step_index"],
        "text_utility": row["text_utility"],
        "same_app": row["same_app"],
        "shared_api_count": row["shared_api_count"],
        "target_exact_substring": row[
            "normalized_target_exact_substring_in_transition"
        ],
        "code_token_jaccard": row["target_transition_code_token_jaccard"],
        "source_goal": str(transition["source_task_goal"]),
        "source_action": str(transition["complete_action"]),
        "source_observation_excerpt": str(
            transition["complete_post_action_observation"]
        )[:2000],
        "leakage_overlap": row["leakage_overlap"],
        "transition_section_marker_count": 1,
    }


def _representative_inspection(
    *,
    rows: Sequence[dict[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    parent_comparisons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    high_positive = sorted(scored, key=lambda row: -float(row["text_utility"]))[:10]
    high_negative = sorted(scored, key=lambda row: float(row["text_utility"]))[:10]
    high_overlap_nonpositive = sorted(
        [
            row
            for row in scored
            if (
                int(row["shared_api_count"]) > 0
                or float(row["target_transition_code_token_jaccard"]) >= 0.25
            )
            and float(row["text_utility"]) <= 0.01
        ],
        key=lambda row: (-float(row["target_transition_code_token_jaccard"]), float(row["text_utility"])),
    )[:10]
    low_overlap_positive = sorted(
        [
            row
            for row in scored
            if int(row["shared_api_count"]) == 0
            and float(row["target_transition_code_token_jaccard"]) <= 0.05
            and float(row["text_utility"]) > 0.01
        ],
        key=lambda row: -float(row["text_utility"]),
    )[:10]
    disagreements = sorted(
        [
            row
            for row in parent_comparisons
            if row.get("best_transition_minus_parent") is not None
        ],
        key=lambda row: -abs(float(row["best_transition_minus_parent"])),
    )[:10]

    def compact_many(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            _compact_inspection(row, transitions[str(row["transition_id"])])
            for row in items
        ]

    malformed = [
        row["pair_id"]
        for row in scored
        if row["leakage_overlap"]
        or row["truncated"]
        or transition_teacher_section(transitions[str(row["transition_id"])]).count(
            "[DECISION TRANSITION MEMORY]"
        )
        != 1
    ]
    return {
        "format": "decision_transition_representative_inspection_6a_v1",
        "high_positive": compact_many(high_positive),
        "high_negative": compact_many(high_negative),
        "high_overlap_nonpositive": compact_many(high_overlap_nonpositive),
        "low_overlap_high_positive": compact_many(low_overlap_positive),
        "anomalous_parent_transition_disagreements": disagreements,
        "malformed_or_leaking_pair_ids": malformed,
        "passed": not malformed,
    }


def _response_row(
    *,
    backend: Any,
    context: Mapping[str, Any],
    teacher_prompt: str,
    teacher_cache_row: Mapping[str, Any],
    entity_type: str,
    entity_id: str,
    entity_task_id: str,
    selection_category: str,
    context_limit: int,
    top_k: int,
    baseline: dict[str, Any],
    renderer_metadata: Mapping[str, Any],
    source_commit: str | None,
) -> dict[str, Any]:
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
    row = {
        "format": TRANSITION_RESPONSE_CACHE_VERSION,
        "scoring_definition": "single_raw_transition_or_trajectory_target_top64_delta_6a_v1",
        "pair_id": str(teacher_cache_row["pair_id"]),
        "pair_key": str(teacher_cache_row["pair_id"]),
        "state_index": int(context["example_index"]),
        "state_example_id": str(context["manifest"]["state_example_id"]),
        "task_id": str(teacher_cache_row["task_id"]),
        "episode_id": str(teacher_cache_row["episode_id"]),
        "step_id": int(teacher_cache_row["step_id"]),
        "split": str(teacher_cache_row["split"]),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_task_id": entity_task_id,
        "transition_id": teacher_cache_row.get("transition_id"),
        "parent_memory_id": teacher_cache_row.get(
            "parent_memory_id", entity_id if entity_type == "trajectory" else None
        ),
        "parent_task_id": teacher_cache_row.get("parent_task_id", entity_task_id),
        "selection_category": selection_category,
        "utility_category": utility_category(float(teacher_cache_row["text_utility"])),
        "text_utility": float(teacher_cache_row["text_utility"]),
        "L0": float(teacher_cache_row["L0"]),
        "Lj_text": float(teacher_cache_row.get("Lj_transition", teacher_cache_row.get("Lj_text"))),
        "baseline_mean_target_nll": float(baseline["mean_target_nll"]),
        "teacher_mean_target_nll": float(teacher["mean_target_nll"]),
        "prompt_tokens": int(baseline["prompt_tokens"]),
        "teacher_prompt_tokens": int(teacher["prompt_tokens"]),
        "target_tokens": int(baseline["target_tokens"]),
        "total_tokens_with_target": int(baseline["prompt_tokens"] + baseline["target_tokens"]),
        "teacher_total_tokens_with_target": int(teacher["prompt_tokens"] + teacher["target_tokens"]),
        "context_limit": context_limit,
        "truncated": False,
        "target_sha256": sha256_text(str(context["target_text"])),
        "target_token_sha256": sha256_text(
            ",".join(str(value) for value in context["target_ids"])
        ),
        "prompt_sha256": sha256_text(str(context["base_prompt"])),
        "teacher_prompt_sha256": sha256_text(teacher_prompt),
        "target_token_ids": list(context["target_ids"]),
        "target_positions": positions,
        "last_user_token_indices": list(
            context["prompt_metadata"].get("last_user_token_indices", [])
        ),
        "renderer_version": renderer_metadata["renderer_version"],
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
        "source_commit_sha": source_commit,
        "scoring_timestamp_utc": utc_now(),
        "student_prompt_contains_raw_memory": False,
    }
    return row


def _build_response_cache(
    *,
    backend: Any,
    contexts: Mapping[str, Mapping[str, Any]],
    requests: Sequence[dict[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, MemoryRecord],
    prompt_profile: str,
    context_limit: int,
    top_k: int,
    output_dir: Path,
    renderer_metadata: Mapping[str, Any],
    source_commit: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / "response_cache_journal.jsonl"
    completed = _load_unique_journal(journal_path, "pair_id")
    by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        by_state[str(request["state_example_id"])].append(request)
    for state_position, state_id in enumerate(sorted(by_state), start=1):
        context = contexts[state_id]
        baseline = _score_target_logits(
            backend,
            prompt_text=str(context["base_prompt"]),
            target_ids=list(context["target_ids"]),
            context_limit=context_limit,
            top_k=top_k,
        )
        for request in sorted(by_state[state_id], key=lambda row: str(row["pair_id"])):
            pair_id = str(request["pair_id"])
            if pair_id in completed:
                continue
            if request["entity_type"] == "transition":
                entity_id = str(request["transition_id"])
                transition = transitions[entity_id]
                messages = messages_with_transition_memory(
                    context["base_messages"], transition, prompt_profile
                )
                entity_task_id = str(transition["parent_task_id"])
            else:
                entity_id = str(request["parent_memory_id"])
                record = records[entity_id]
                messages = messages_with_teacher_memory(
                    context["base_messages"], record, prompt_profile
                )
                entity_task_id = record.task_id
            teacher_prompt = backend.render_messages(messages, add_generation_prompt=True)
            row = _response_row(
                backend=backend,
                context=context,
                teacher_prompt=teacher_prompt,
                teacher_cache_row=request,
                entity_type=str(request["entity_type"]),
                entity_id=entity_id,
                entity_task_id=entity_task_id,
                selection_category=str(request["selection_category"]),
                context_limit=context_limit,
                top_k=top_k,
                baseline=baseline,
                renderer_metadata=renderer_metadata,
                source_commit=source_commit,
            )
            nll_tolerance = REPRO_TOLERANCE
            if abs(float(row["baseline_mean_target_nll"]) - float(row["L0"])) > nll_tolerance:
                raise ValueError(f"Response-cache L0 mismatch for {pair_id}")
            if abs(float(row["teacher_mean_target_nll"]) - float(row["Lj_text"])) > nll_tolerance:
                raise ValueError(f"Response-cache teacher NLL mismatch for {pair_id}")
            append_jsonl(journal_path, row)
            completed[pair_id] = row
        print(
            f"response cache {output_dir.name}: state {state_position}/{len(by_state)} "
            f"completed={len(completed)}/{len(requests)}",
            flush=True,
        )
    ordered = [completed[str(request["pair_id"])] for request in requests]
    pair_ids = [str(row["pair_id"]) for row in ordered]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError(f"Duplicate response-cache pair IDs in {output_dir}")
    identity = validate_target_token_utility_identity(ordered)
    errors = []
    if not identity["passed"]:
        errors.append("target_token_utility_identity")
    if any(row["student_prompt_contains_raw_memory"] for row in ordered):
        errors.append("raw_memory_in_student_prompt")
    validation = {
        "format": "decision_transition_response_cache_validation_6a_v1",
        "pair_count": len(ordered),
        "unique_pair_count": len(set(pair_ids)),
        "target_token_utility_identity": identity,
        "no_truncation": all(not row["truncated"] for row in ordered),
        "errors": errors,
        "passed": not errors,
    }
    write_jsonl(output_dir / "response_cache.jsonl", ordered)
    atomic_write_json(output_dir / "response_cache_validation.json", validation)
    summary = {
        "format": "decision_transition_response_cache_summary_6a_v1",
        "pair_count": len(ordered),
        "entity_type_counts": dict(Counter(row["entity_type"] for row in ordered)),
        "split_counts": dict(Counter(row["split"] for row in ordered)),
        "selection_category_counts": dict(
            Counter(row["selection_category"] for row in ordered)
        ),
        "response_cache_sha256": sha256_file(output_dir / "response_cache.jsonl"),
        "validation": validation,
    }
    atomic_write_json(output_dir / "response_cache_summary.json", summary)
    return ordered, summary


def _report(summary: Mapping[str, Any]) -> str:
    utility = summary["teacher_analysis"]["utility"]
    parent = summary["parent_comparison"]
    return "\n".join(
        [
            "# EXP-017 Raw Decision-Transition Teacher",
            "",
            "## VERIFIED",
            "",
            f"- source commit: `{summary['source_commit']}`",
            f"- cache validation passed: `{summary['validation']['passed']}`",
            f"- reproducibility passed: `{summary['reproducibility']['passed']}`",
            f"- legal rows: `{summary['validation']['actual_pair_count']}`",
            f"- scored / over-context: `{summary['validation']['scoreable_pair_count']}` / "
            f"`{summary['validation']['over_context_pair_count']}`",
            f"- positive / neutral / negative: `{json.dumps(utility['category_counts'], sort_keys=True)}`",
            f"- mean utility: `{utility['mean_std']['mean']:.6f}`",
            f"- utility range: `{utility['min']:.6f}` to `{utility['max']:.6f}`",
            f"- target exact-substring rows: `{summary['teacher_analysis']['target_exact_substring_count']}`",
            "",
            "## Parent Comparison",
            "",
            f"- state-parent groups: `{parent['state_parent_group_count']}`",
            f"- matched whole-trajectory teachers: `{parent['matched_parent_teacher_count']}`",
            f"- best transition beats parent: `{parent['best_transition_beats_parent_count']}`",
            f"- helpful parents containing harmful child transitions: "
            f"`{parent['helpful_parent_with_harmful_child_count']}`",
            "",
            "## Behavioral Inputs",
            "",
            f"- pair-oracle response rows: `{summary['response_caches']['pair_oracle']['pair_count']}`",
            f"- static-transition response rows: "
            f"`{summary['response_caches']['static_transition']['pair_count']}`",
            f"- whole-trajectory baseline response rows: "
            f"`{summary['response_caches']['trajectory_baseline']['pair_count']}`",
            "",
            "No Qwen parameters were trained, no selector/compiler/full field was used, and no "
            "AppWorld generation or environment evaluation was run.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score EXP-017 raw transition teachers and build behavior caches."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_transition_memory_6a.yaml"),
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--preflight-dir", type=Path, required=True)
    parser.add_argument("--parent-teacher-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--progress-interval-s", type=float, default=300.0)
    parser.add_argument(
        "--approve-runtime-over-review-threshold",
        action="store_true",
        help="Record explicit approval to run an unchanged design above the preflight review threshold.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6a"]
    preflight_summary = _load_json(args.preflight_dir / "preflight_summary.json")
    runtime_projection = preflight_summary["runtime_projection"]
    review_required = bool(
        runtime_projection.get(
            "expected_runtime_review_required",
            not runtime_projection.get("expected_runtime_gate_passed", True),
        )
    )
    allowed_statuses = {"passed_ready_for_gpu_review"}
    if args.approve_runtime_over_review_threshold:
        allowed_statuses.add("paused_projected_runtime_requires_explicit_approval")
    if preflight_summary["status"] not in allowed_statuses:
        raise ValueError(f"Preflight is not approved for GPU scoring: {preflight_summary['status']}")
    if review_required and not args.approve_runtime_over_review_threshold:
        raise ValueError(
            "Preflight runtime exceeds the review threshold and requires explicit approval"
        )
    query_manifest = _load_json(args.preflight_dir / "query_manifest.json")
    panel_rows = _load_rows(args.preflight_dir / "transition_panel.jsonl")
    preflight_rows = _load_rows(args.preflight_dir / "pair_preflight.jsonl")
    transitions = {str(row["transition_id"]): row for row in panel_rows}
    if len(transitions) != int(preflight_summary["counts"]["panel_transition_count"]):
        raise ValueError("Transition panel identity differs from preflight summary")
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    memory_records = load_memory_records(args.data / "memory_records.jsonl")
    records = {record.memory_id: record for record in memory_records}
    for key, path in (
        ("decision_examples_sha256", args.data / "decision_examples.jsonl"),
        ("memory_records_sha256", args.data / "memory_records.jsonl"),
        ("transition_panel_sha256", args.preflight_dir / "transition_panel.jsonl"),
        ("query_manifest_sha256", args.preflight_dir / "query_manifest.json"),
        ("pair_preflight_sha256", args.preflight_dir / "pair_preflight.jsonl"),
    ):
        if sha256_file(path) != preflight_summary["hashes"][key]:
            raise ValueError(f"Preflight source hash differs: {key}")

    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    context_limit = _context_limit_for_backend(backend)
    if context_limit != int(settings["context_limit"]):
        raise ValueError(
            f"Runtime context limit differs from preflight: {context_limit} != {settings['context_limit']}"
        )
    prompt_profile = cfg.benchmark.prompt_profile
    renderer_metadata = appworld_renderer_metadata(prompt_profile)
    source_commit = maybe_git_commit()
    contexts = _query_contexts(
        backend=backend,
        examples=examples,
        query_manifest=query_manifest,
        prompt_profile=prompt_profile,
    )

    l0_cache_path = args.output_dir / "l0_cache.json"
    l0_cache = _load_json(l0_cache_path) if l0_cache_path.exists() else {}
    journal_path = args.output_dir / "teacher_cache_journal.jsonl"
    completed = _load_unique_journal(journal_path, "pair_id")
    last_progress = time.perf_counter()
    new_scored = 0
    new_over_context = 0
    for index, preflight in enumerate(preflight_rows, start=1):
        pair_id = str(preflight["pair_id"])
        if pair_id in completed:
            continue
        state_id = str(preflight["state_example_id"])
        transition_id = str(preflight["transition_id"])
        context = contexts[state_id]
        transition = transitions[transition_id]
        l0 = _ensure_l0(
            backend=backend,
            context=context,
            context_limit=context_limit,
            cache=l0_cache,
            cache_path=l0_cache_path,
        )
        row = {
            **preflight,
            "format": TRANSITION_TEACHER_CACHE_VERSION,
            "scoring_definition": "frozen_qwen_full_demo_plus_single_raw_decision_transition_target_nll_v1",
            "L0": l0,
            "Lj_transition": None,
            "text_utility": None,
            "utility_category": None,
            "score_status": "over_context" if preflight["over_context"] else "pending",
            "valid_for_loss": False,
            "source_commit_sha": source_commit,
            "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
            "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
            "scoring_timestamp_utc": utc_now(),
            "score_time_s": 0.0,
        }
        example = context["example"]
        row.update(
            _overlap_features(
                example=example,
                query_manifest_row=context["manifest"],
                transition=transition,
            )
        )
        if preflight["over_context"]:
            row["score_status"] = "over_context"
            row["skipped_reason"] = "over_context_no_truncation"
            new_over_context += 1
        else:
            messages = messages_with_transition_memory(
                context["base_messages"], transition, prompt_profile
            )
            prompt = backend.render_messages(messages, add_generation_prompt=True)
            if sha256_text(prompt) != preflight["teacher_prompt_sha256"]:
                raise ValueError(f"Teacher prompt hash differs from preflight for {pair_id}")
            score_started = time.perf_counter()
            lj, prompt_tokens, target_tokens = _score_mean_target_nll(
                backend,
                prompt,
                list(context["target_ids"]),
                str(context["target_text"]),
                context_limit,
            )
            if prompt_tokens != int(preflight["combined_prompt_tokens"]):
                raise ValueError(f"Teacher prompt token count differs for {pair_id}")
            if target_tokens != int(preflight["target_tokens"]):
                raise ValueError(f"Target token count differs for {pair_id}")
            utility = l0 - lj
            row.update(
                {
                    "Lj_transition": lj,
                    "text_utility": utility,
                    "utility_category": utility_category(utility),
                    "score_status": "scored",
                    "valid_for_loss": True,
                    "score_time_s": time.perf_counter() - score_started,
                    "skipped_reason": None,
                }
            )
            new_scored += 1
        append_jsonl(journal_path, row)
        completed[pair_id] = row
        now = time.perf_counter()
        if now - last_progress >= float(args.progress_interval_s):
            elapsed = now - started
            done = len(completed)
            rate = done / max(elapsed, 1.0)
            remaining = len(preflight_rows) - done
            print(
                f"teacher progress completed={done}/{len(preflight_rows)} "
                f"new_scored={new_scored} over_context={new_over_context} "
                f"elapsed_h={elapsed / 3600:.3f} eta_h={remaining / max(rate, 1e-9) / 3600:.3f}",
                flush=True,
            )
            last_progress = now
    ordered_rows = [completed[str(row["pair_id"])] for row in preflight_rows]
    write_jsonl(args.output_dir / "teacher_cache.jsonl", ordered_rows)
    validation = _validate_teacher_cache(
        rows=ordered_rows, preflight_rows=preflight_rows
    )
    atomic_write_json(args.output_dir / "teacher_cache_validation.json", validation)
    if not validation["passed"]:
        raise RuntimeError(f"Teacher cache validation failed: {validation['errors_first_50']}")

    reproducibility = _reproducibility_check(
        backend=backend,
        rows=ordered_rows,
        contexts=contexts,
        transitions=transitions,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
    )
    atomic_write_json(args.output_dir / "teacher_reproducibility.json", reproducibility)
    if not reproducibility["passed"]:
        raise RuntimeError("Teacher cache reproducibility failed")
    teacher_analysis = _teacher_analysis(ordered_rows)
    atomic_write_json(args.output_dir / "teacher_analysis.json", teacher_analysis)

    state_ids = {str(row["state_example_id"]) for row in ordered_rows}
    parent_memory_ids = {str(row["parent_memory_id"]) for row in ordered_rows}
    parent_rows = _load_parent_rows(
        args.parent_teacher_cache,
        state_ids=state_ids,
        memory_ids=parent_memory_ids,
    )
    parent_summary, parent_comparison_rows = _parent_comparison(
        ordered_rows, parent_rows
    )
    write_jsonl(
        args.output_dir / "parent_transition_comparison.jsonl",
        parent_comparison_rows,
    )
    atomic_write_json(args.output_dir / "parent_comparison_summary.json", parent_summary)
    inspection = _representative_inspection(
        rows=ordered_rows,
        transitions=transitions,
        parent_comparisons=parent_comparison_rows,
    )
    atomic_write_json(args.output_dir / "representative_inspection.json", inspection)
    if not inspection["passed"]:
        raise RuntimeError(
            f"Representative teacher inspection found malformed/leaking rows: "
            f"{inspection['malformed_or_leaking_pair_ids'][:20]}"
        )

    pair_oracle_rows, pair_oracle_manifest = select_pair_oracle_subset(
        ordered_rows,
        seed=int(settings["pair_oracle_seed"]),
        per_category=int(settings["pair_oracle"]["pairs_per_category"]),
    )
    atomic_write_json(args.output_dir / "pair_oracle_manifest.json", pair_oracle_manifest)
    selected_transition_ids, static_selection = select_static_transitions(
        ordered_rows,
        transitions,
        seed=int(settings["static_selection_seed"]),
        count=int(settings["static_transition"]["transition_count"]),
        minimum_parents=int(settings["static_transition"]["minimum_parent_count"]),
    )
    atomic_write_json(args.output_dir / "static_transition_manifest.json", static_selection)

    pair_category = {
        str(row["pair_id"]): str(row["selection_category"])
        for row in pair_oracle_rows
    }
    pair_oracle_requests = [
        {
            **row,
            "entity_type": "transition",
            "selection_category": pair_category[str(row["pair_id"])],
        }
        for row in pair_oracle_rows
    ]
    static_transition_set = set(selected_transition_ids)
    static_requests = [
        {
            **row,
            "entity_type": "transition",
            "selection_category": utility_category(float(row["text_utility"])),
        }
        for row in ordered_rows
        if row.get("valid_for_loss")
        and str(row["transition_id"]) in static_transition_set
    ]

    selected_parent_ids = set(static_selection["parent_memory_ids"])
    trajectory_requests = []
    query_manifest_by_state = {
        str(row["state_example_id"]): row for row in query_manifest["query_rows"]
    }
    for (state_id, parent_memory_id), parent in sorted(parent_rows.items()):
        if parent_memory_id not in selected_parent_ids or not parent.get("valid_for_loss"):
            continue
        record = records[parent_memory_id]
        trajectory_requests.append(
            {
                "format": TRAJECTORY_STATIC_BASELINE_VERSION,
                "pair_id": f"{state_id}::trajectory::{parent_memory_id}",
                "state_example_id": state_id,
                "task_id": str(parent["task_id"]),
                "episode_id": str(parent["episode_id"]),
                "step_id": int(parent["step_id"]),
                "split": str(query_manifest_by_state[state_id]["split"]),
                "parent_memory_id": parent_memory_id,
                "parent_task_id": record.task_id,
                "L0": float(parent["L0"]),
                "Lj_text": float(parent["Lj_text"]),
                "text_utility": float(parent["text_utility"]),
                "entity_type": "trajectory",
                "selection_category": utility_category(float(parent["text_utility"])),
                "valid_for_loss": True,
            }
        )

    top_k = int(settings["teacher"]["top_k"])
    pair_response_rows, pair_response_summary = _build_response_cache(
        backend=backend,
        contexts=contexts,
        requests=pair_oracle_requests,
        transitions=transitions,
        records=records,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
        top_k=top_k,
        output_dir=args.output_dir / "pair_oracle_response_cache",
        renderer_metadata=renderer_metadata,
        source_commit=source_commit,
    )
    static_response_rows, static_response_summary = _build_response_cache(
        backend=backend,
        contexts=contexts,
        requests=static_requests,
        transitions=transitions,
        records=records,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
        top_k=top_k,
        output_dir=args.output_dir / "static_transition_response_cache",
        renderer_metadata=renderer_metadata,
        source_commit=source_commit,
    )
    trajectory_response_rows, trajectory_response_summary = _build_response_cache(
        backend=backend,
        contexts=contexts,
        requests=trajectory_requests,
        transitions=transitions,
        records=records,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
        top_k=top_k,
        output_dir=args.output_dir / "trajectory_baseline_response_cache",
        renderer_metadata=renderer_metadata,
        source_commit=source_commit,
    )
    del pair_response_rows, static_response_rows, trajectory_response_rows

    summary = {
        "format": "decision_transition_teacher_run_summary_6a_v1",
        "status": "completed",
        "timestamp_utc": utc_now(),
        "source_commit": source_commit,
        "config": str(args.config),
        "data": str(args.data),
        "preflight_dir": str(args.preflight_dir),
        "parent_teacher_cache": str(args.parent_teacher_cache),
        "output_dir": str(args.output_dir),
        "model_name": backend.model_name,
        "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
        "renderer_metadata": renderer_metadata,
        "context_limit": context_limit,
        "validation": validation,
        "reproducibility": reproducibility,
        "teacher_analysis": teacher_analysis,
        "parent_comparison": parent_summary,
        "representative_inspection": {
            "passed": inspection["passed"],
            "path": str(args.output_dir / "representative_inspection.json"),
        },
        "pair_oracle_manifest": pair_oracle_manifest,
        "static_transition_manifest": static_selection,
        "response_caches": {
            "pair_oracle": pair_response_summary,
            "static_transition": static_response_summary,
            "trajectory_baseline": trajectory_response_summary,
        },
        "newly_scored_rows": new_scored,
        "new_over_context_rows": new_over_context,
        "reused_rows": len(preflight_rows) - new_scored - new_over_context,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "teacher_forced_target_scoring_only": True,
            "no_selector_or_compiler_training": True,
            "no_full_bank_field_training": True,
            "no_appworld_generation_or_evaluation": True,
            "no_truncation": True,
            "exp016d_launched": False,
        },
        "artifacts": {
            "teacher_cache": str(args.output_dir / "teacher_cache.jsonl"),
            "teacher_cache_sha256": sha256_file(args.output_dir / "teacher_cache.jsonl"),
            "pair_oracle_response_cache": str(
                args.output_dir / "pair_oracle_response_cache" / "response_cache.jsonl"
            ),
            "static_transition_response_cache": str(
                args.output_dir
                / "static_transition_response_cache"
                / "response_cache.jsonl"
            ),
            "trajectory_baseline_response_cache": str(
                args.output_dir
                / "trajectory_baseline_response_cache"
                / "response_cache.jsonl"
            ),
        },
    }
    atomic_write_json(args.output_dir / "teacher_summary.json", summary)
    atomic_write_text(args.output_dir / "teacher_report.md", _report(summary))
    print(json.dumps(summary["validation"], indent=2, sort_keys=True), flush=True)
    print(json.dumps(summary["teacher_analysis"]["utility"], indent=2, sort_keys=True), flush=True)
    print(f"completed transition teacher in {summary['runtime_s'] / 3600:.3f}h", flush=True)


if __name__ == "__main__":
    main()
