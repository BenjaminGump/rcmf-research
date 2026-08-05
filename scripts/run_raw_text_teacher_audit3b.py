from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
import statistics
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
from rcmf.utils.serialization import append_jsonl, atomic_write_json, maybe_git_commit, sha256_file, sha256_text
from scripts.run_raw_text_teacher_pilot import (
    RAW_TEXT_TEACHER_CACHE_VERSION,
    TEACHER_MEMORY_SECTION_VERSION,
    UTILITY_NEUTRAL_EPS,
    _category,
    _context_limit_for_backend,
    _distribution,
    _example_id,
    _load_or_compute_representations,
    _pearson,
    _score_mean_target_nll,
    _target_token_ids,
    _token_ids,
    apps_for_example,
    apps_for_record,
    legal_memory_indices,
    messages_with_teacher_memory,
    propose_candidates,
)
from scripts.train import _example_task_id, _leakage_keys_for_example, _leakage_keys_for_record


AUDIT3B_CACHE_VERSION = "raw_text_memory_teacher_audit3b_v1"
AUDIT3B_PREFLIGHT_VERSION = "raw_text_memory_teacher_full_cache_preflight_v1"
RECALL_KS = (1, 2, 4, 8)
UTILITY_THRESHOLDS = (0.05, 0.10, 0.25)
PROPOSAL_SOURCES = ("cosine_top2", "same_app", "random_low_similarity")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                import json

                rows.append(json.loads(line))
    return rows


def _union_sources(*source_lists: list[str] | tuple[str, ...] | None) -> list[str]:
    output: list[str] = []
    for sources in source_lists:
        for source in sources or []:
            if source not in output:
                output.append(source)
    return output


def _proposal_sources(row: dict[str, Any]) -> list[str]:
    if "proposal_sources" in row and row["proposal_sources"] is not None:
        return list(row["proposal_sources"])
    return [source for source in row.get("candidate_source", []) if source != "audit_all_memory"]


def _is_scoreable(row: dict[str, Any]) -> bool:
    return not row.get("over_context") and row.get("text_utility") is not None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def compute_expanded_audit_metrics(
    rows: list[dict[str, Any]],
    proposal_order_by_state: dict[str, list[str]],
) -> dict[str, Any]:
    rows_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_state[str(row["state_example_id"])].append(row)

    per_state: list[dict[str, Any]] = []
    for state_id in sorted(rows_by_state):
        state_rows = rows_by_state[state_id]
        scored = [row for row in state_rows if _is_scoreable(row)]
        proposed_scored = [row for row in scored if row.get("is_proposed_candidate")]
        over_context_count = sum(1 for row in state_rows if row.get("over_context"))
        categories = Counter(row.get("utility_category") for row in scored)
        proposal_order = proposal_order_by_state.get(state_id, [])
        best_legal = max(scored, key=lambda row: float(row["text_utility"])) if scored else None
        best_proposed = (
            max(proposed_scored, key=lambda row: float(row["text_utility"]))
            if proposed_scored
            else None
        )
        regret = (
            float(best_legal["text_utility"]) - float(best_proposed["text_utility"])
            if best_legal is not None and best_proposed is not None
            else None
        )
        recall_at_k = {}
        for k in RECALL_KS:
            if best_legal is None:
                recall_at_k[f"recall@{k}"] = None
            else:
                recall_at_k[f"recall@{k}"] = best_legal["candidate_memory_id"] in proposal_order[:k]
        total_positive_mass = sum(max(0.0, float(row["text_utility"])) for row in scored)
        proposed_positive_mass = sum(max(0.0, float(row["text_utility"])) for row in proposed_scored)
        coverage = proposed_positive_mass / total_positive_mass if total_positive_mass > 0 else None
        first_row = state_rows[0]
        per_state.append(
            {
                "state_example_id": state_id,
                "task_id": first_row.get("task_id"),
                "episode_id": first_row.get("episode_id"),
                "step_id": first_row.get("step_id"),
                "L0": first_row.get("L0"),
                "legal_pair_count": len(state_rows),
                "legal_scored_memories": len(scored),
                "over_context_pair_count": over_context_count,
                "positive_legal_count": int(categories.get("positive", 0)),
                "neutral_legal_count": int(categories.get("neutral", 0)),
                "negative_legal_count": int(categories.get("negative", 0)),
                "best_legal_memory_id": best_legal.get("candidate_memory_id") if best_legal else None,
                "best_legal_utility": _safe_float(best_legal.get("text_utility")) if best_legal else None,
                "best_proposed_memory_id": best_proposed.get("candidate_memory_id") if best_proposed else None,
                "best_proposed_utility": _safe_float(best_proposed.get("text_utility")) if best_proposed else None,
                "proposal_regret": regret,
                "proposal_order_memory_ids": proposal_order,
                "proposed_scoreable_count": len(proposed_scored),
                "positive_utility_mass": total_positive_mass,
                "proposed_positive_utility_mass": proposed_positive_mass,
                "positive_utility_mass_coverage": coverage,
                **recall_at_k,
            }
        )

    def aggregate(states: list[dict[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {"state_count": len(states)}
        for k in RECALL_KS:
            values = [state[f"recall@{k}"] for state in states if state[f"recall@{k}"] is not None]
            output[f"recall@{k}"] = (sum(1 for value in values if value) / len(values)) if values else None
        regrets = [float(state["proposal_regret"]) for state in states if state["proposal_regret"] is not None]
        output["mean_regret"] = float(statistics.fmean(regrets)) if regrets else None
        output["median_regret"] = float(statistics.median(regrets)) if regrets else None
        output["max_regret"] = max(regrets) if regrets else None
        total_mass = sum(float(state["positive_utility_mass"]) for state in states)
        proposed_mass = sum(float(state["proposed_positive_utility_mass"]) for state in states)
        output["positive_utility_mass_coverage"] = proposed_mass / total_mass if total_mass > 0 else None
        coverages = [
            float(state["positive_utility_mass_coverage"])
            for state in states
            if state["positive_utility_mass_coverage"] is not None
        ]
        output["mean_state_positive_utility_mass_coverage"] = (
            float(statistics.fmean(coverages)) if coverages else None
        )
        return output

    threshold_metrics = {}
    for threshold in UTILITY_THRESHOLDS:
        states = [
            state
            for state in per_state
            if state["best_legal_utility"] is not None and float(state["best_legal_utility"]) >= threshold
        ]
        threshold_metrics[f">={threshold:.2f}"] = aggregate(states)

    source_ablations = {}
    for source in PROPOSAL_SOURCES:
        source_states = []
        for state in per_state:
            state_rows = rows_by_state[state["state_example_id"]]
            scored = [row for row in state_rows if _is_scoreable(row)]
            source_rows = [row for row in scored if source in _proposal_sources(row)]
            if not source_rows:
                source_states.append(
                    {
                        **state,
                        "source_best_utility": None,
                        "source_regret": None,
                        "source_hit_best": False,
                        "source_positive_mass": 0.0,
                    }
                )
                continue
            source_best = max(source_rows, key=lambda row: float(row["text_utility"]))
            source_positive_mass = sum(max(0.0, float(row["text_utility"])) for row in source_rows)
            source_states.append(
                {
                    **state,
                    "source_best_utility": float(source_best["text_utility"]),
                    "source_regret": (
                        float(state["best_legal_utility"]) - float(source_best["text_utility"])
                        if state["best_legal_utility"] is not None
                        else None
                    ),
                    "source_hit_best": source_best["candidate_memory_id"] == state["best_legal_memory_id"],
                    "source_positive_mass": source_positive_mass,
                }
            )
        source_regrets = [
            float(state["source_regret"]) for state in source_states if state["source_regret"] is not None
        ]
        total_mass = sum(float(state["positive_utility_mass"]) for state in source_states)
        source_mass = sum(float(state["source_positive_mass"]) for state in source_states)
        state_count_with_candidates = sum(1 for state in source_states if state["source_best_utility"] is not None)
        source_ablations[source] = {
            "state_count": len(source_states),
            "state_count_with_candidates": state_count_with_candidates,
            "hit_best_legal_count": sum(1 for state in source_states if state["source_hit_best"]),
            "hit_best_legal_rate": (
                sum(1 for state in source_states if state["source_hit_best"]) / len(source_states)
                if source_states
                else None
            ),
            "mean_regret": float(statistics.fmean(source_regrets)) if source_regrets else None,
            "median_regret": float(statistics.median(source_regrets)) if source_regrets else None,
            "positive_utility_mass_coverage": source_mass / total_mass if total_mass > 0 else None,
        }

    return {
        "per_state": per_state,
        "overall": aggregate(per_state),
        "thresholds": threshold_metrics,
        "source_ablations": source_ablations,
    }


def _write_per_state_csv(path: Path, per_state: list[dict[str, Any]]) -> None:
    fieldnames = [
        "state_example_id",
        "task_id",
        "step_id",
        "L0",
        "best_legal_memory_id",
        "best_legal_utility",
        "best_proposed_memory_id",
        "best_proposed_utility",
        "positive_legal_count",
        "neutral_legal_count",
        "negative_legal_count",
        "over_context_pair_count",
        "proposal_regret",
        "recall@1",
        "recall@2",
        "recall@4",
        "recall@8",
        "positive_utility_mass_coverage",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in per_state:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _base_row(
    *,
    example_index: int,
    memory_index: int,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    l0: float,
    preflight: dict[str, Any],
    target_ids: list[int],
    renderer_metadata: dict[str, Any],
    backend: Any,
    commit_sha: str,
    proposal_sources: list[str],
    proposal_rank: int | None,
    row_origin: str,
) -> dict[str, Any]:
    example = examples[example_index]
    record = records[memory_index]
    return {
        "format": AUDIT3B_CACHE_VERSION,
        "source_teacher_cache_version": RAW_TEXT_TEACHER_CACHE_VERSION,
        "audit3b_row_origin": row_origin,
        "state_example_id": _example_id(example_index, example),
        "example_index": example_index,
        "example_jsonl_line": example_index + 1,
        "episode_id": example.episode_id,
        "step_id": example.step_id,
        "task_id": _example_task_id(example),
        "candidate_memory_id": record.memory_id,
        "candidate_memory_index": memory_index,
        "candidate_memory_jsonl_line": memory_index + 1,
        "candidate_memory_task_id": record.task_id,
        "candidate_memory_episode_id": record.episode_id,
        "candidate_source": _union_sources(proposal_sources, ["audit_all_memory"]),
        "proposal_sources": proposal_sources,
        "proposal_rank": proposal_rank,
        "is_proposed_candidate": bool(proposal_sources),
        "is_audit_all_memory_row": True,
        "L0": l0,
        "Lj_text": None,
        "text_utility": None,
        "utility_category": None,
        "state_prompt_tokens": preflight["state_prompt_tokens"],
        "raw_memory_tokens": preflight["raw_memory_tokens"],
        "combined_prompt_tokens": preflight["combined_prompt_tokens"],
        "target_tokens": preflight["target_tokens"],
        "total_tokens_with_target": preflight["total_tokens_with_target"],
        "context_limit": preflight["context_limit"],
        "over_context": preflight["over_context"],
        "target_sha256": sha256_text(_target_suffix(example)),
        "target_token_sha256": sha256_text(",".join(str(item) for item in target_ids)),
        "memory_text_sha256": sha256_text(record.experience_text),
        "renderer_version": renderer_metadata["renderer_version"],
        "renderer_metadata": renderer_metadata,
        "teacher_memory_section_version": TEACHER_MEMORY_SECTION_VERSION,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "model_config_commit_hash": getattr(getattr(backend.model, "config", None), "_commit_hash", None),
        "commit_sha": commit_sha,
        "skipped_reason": None,
    }


def _preflight_pair(
    *,
    backend: Any,
    tokenizer: Any,
    base_messages: list[dict[str, str]],
    prompt_profile: str,
    example_index: int,
    memory_index: int,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    state_prompt_tokens: int,
    raw_memory_tokens: int,
    target_tokens: int,
    context_limit: int,
) -> tuple[dict[str, Any], str]:
    example = examples[example_index]
    record = records[memory_index]
    memory_messages = messages_with_teacher_memory(base_messages, record, prompt_profile)
    memory_prompt = backend.render_messages(memory_messages, add_generation_prompt=True)
    combined_prompt_tokens = len(_token_ids(tokenizer, memory_prompt, add_special_tokens=False))
    total_tokens = combined_prompt_tokens + target_tokens
    preflight = {
        "state_example_id": _example_id(example_index, example),
        "example_index": example_index,
        "task_id": _example_task_id(example),
        "episode_id": example.episode_id,
        "step_id": example.step_id,
        "candidate_memory_id": record.memory_id,
        "memory_index": memory_index,
        "memory_task_id": record.task_id,
        "state_prompt_tokens": state_prompt_tokens,
        "raw_memory_tokens": raw_memory_tokens,
        "combined_prompt_tokens": combined_prompt_tokens,
        "target_tokens": target_tokens,
        "total_tokens_with_target": total_tokens,
        "context_limit": context_limit,
        "over_context": total_tokens > context_limit,
    }
    return preflight, memory_prompt


def _copy_cached_row(
    row: dict[str, Any],
    proposal_sources: list[str],
    proposal_rank: int | None,
) -> dict[str, Any]:
    copied = dict(row)
    copied["source_format"] = row.get("format")
    copied["format"] = AUDIT3B_CACHE_VERSION
    copied["source_teacher_cache_version"] = RAW_TEXT_TEACHER_CACHE_VERSION
    copied["audit3b_row_origin"] = "cached_milestone3"
    copied["candidate_source"] = _union_sources(proposal_sources, ["audit_all_memory"])
    copied["proposal_sources"] = proposal_sources
    copied["proposal_rank"] = proposal_rank
    copied["is_proposed_candidate"] = bool(proposal_sources)
    copied["is_audit_all_memory_row"] = True
    return copied


def _select_reproducibility_rows(scored_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    positives = [row for row in scored_rows if row.get("utility_category") == "positive"]
    neutrals = [row for row in scored_rows if row.get("utility_category") == "neutral"]
    negatives = [row for row in scored_rows if row.get("utility_category") == "negative"]
    return {
        "positive": max(positives, key=lambda row: float(row["text_utility"])) if positives else None,
        "neutral": min(neutrals, key=lambda row: abs(float(row["text_utility"]))) if neutrals else None,
        "negative": min(negatives, key=lambda row: float(row["text_utility"])) if negatives else None,
    }


def _run_reproducibility_check(
    *,
    rows_by_name: dict[str, dict[str, Any] | None],
    backend: Any,
    base_prompt_texts: dict[int, str],
    base_messages_by_index: dict[int, list[dict[str, str]]],
    prompt_profile: str,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    target_ids_by_index: dict[int, list[int]],
    target_text_by_index: dict[int, str],
    context_limit: int,
    tolerance: float,
) -> dict[str, Any]:
    checks = []
    for category, row in rows_by_name.items():
        if row is None:
            checks.append({"category": category, "skipped_reason": "no_row"})
            continue
        example_index = int(row["example_index"])
        memory_index = int(row["candidate_memory_index"])
        l0_started = time.perf_counter()
        l0_repeat, _prompt_tokens, _target_tokens = _score_mean_target_nll(
            backend,
            base_prompt_texts[example_index],
            target_ids_by_index[example_index],
            target_text_by_index[example_index],
            context_limit,
        )
        l0_score_s = time.perf_counter() - l0_started
        memory_prompt = backend.render_messages(
            messages_with_teacher_memory(
                base_messages_by_index[example_index],
                records[memory_index],
                prompt_profile,
            ),
            add_generation_prompt=True,
        )
        lj_started = time.perf_counter()
        lj_repeat, _prompt_tokens, _target_tokens = _score_mean_target_nll(
            backend,
            memory_prompt,
            target_ids_by_index[example_index],
            target_text_by_index[example_index],
            context_limit,
        )
        lj_score_s = time.perf_counter() - lj_started
        utility_repeat = l0_repeat - lj_repeat
        check = {
            "category": category,
            "state_example_id": row["state_example_id"],
            "candidate_memory_id": row["candidate_memory_id"],
            "original_L0": row["L0"],
            "repeat_L0": l0_repeat,
            "L0_abs_diff": abs(float(row["L0"]) - l0_repeat),
            "original_Lj_text": row["Lj_text"],
            "repeat_Lj_text": lj_repeat,
            "Lj_text_abs_diff": abs(float(row["Lj_text"]) - lj_repeat),
            "original_text_utility": row["text_utility"],
            "repeat_text_utility": utility_repeat,
            "text_utility_abs_diff": abs(float(row["text_utility"]) - utility_repeat),
            "repeat_L0_score_s": l0_score_s,
            "repeat_Lj_score_s": lj_score_s,
        }
        check["within_tolerance"] = (
            check["L0_abs_diff"] <= tolerance
            and check["Lj_text_abs_diff"] <= tolerance
            and check["text_utility_abs_diff"] <= tolerance
        )
        checks.append(check)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    l0_times = [float(check["repeat_L0_score_s"]) for check in checks if "repeat_L0_score_s" in check]
    lj_times = [float(check["repeat_Lj_score_s"]) for check in checks if "repeat_Lj_score_s" in check]
    return {
        "format": "raw_text_teacher_audit3b_reproducibility_v1",
        "tolerance": tolerance,
        "checks": checks,
        "mean_repeat_L0_score_s": float(statistics.fmean(l0_times)) if l0_times else None,
        "mean_repeat_Lj_score_s": float(statistics.fmean(lj_times)) if lj_times else None,
        "all_within_tolerance": all(check.get("within_tolerance", True) for check in checks),
    }


def _inspect_representative_prompts(
    *,
    rows: list[dict[str, Any]],
    backend: Any,
    base_messages_by_index: dict[int, list[dict[str, str]]],
    prompt_profile: str,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
) -> dict[str, Any]:
    scored = [row for row in rows if _is_scoreable(row)]
    positives = sorted(
        [row for row in scored if float(row["text_utility"]) > UTILITY_NEUTRAL_EPS],
        key=lambda row: (-float(row["text_utility"]), row["state_example_id"], row["candidate_memory_id"]),
    )[:3]
    negatives = sorted(
        [row for row in scored if float(row["text_utility"]) < -UTILITY_NEUTRAL_EPS],
        key=lambda row: (float(row["text_utility"]), row["state_example_id"], row["candidate_memory_id"]),
    )[:3]
    inspected = []
    for label, selected_rows in (("high_positive", positives), ("high_negative", negatives)):
        for row in selected_rows:
            example_index = int(row["example_index"])
            memory_index = int(row["candidate_memory_index"])
            example = examples[example_index]
            record = records[memory_index]
            prompt = backend.render_messages(
                messages_with_teacher_memory(base_messages_by_index[example_index], record, prompt_profile),
                add_generation_prompt=True,
            )
            markers = [
                "[TEACHER-ONLY RAW MEMORY START]",
                "[RAW MEMORY TEXT START]",
                "[RAW MEMORY TEXT END]",
                "[TEACHER-ONLY RAW MEMORY END]",
                "[CURRENT APPWORLD STATE START]",
                "[CURRENT APPWORLD STATE END]",
            ]
            counts = {marker: prompt.count(marker) for marker in markers}
            positions = {marker: prompt.find(marker) for marker in markers}
            order_ok = (
                positions["[TEACHER-ONLY RAW MEMORY START]"]
                < positions["[RAW MEMORY TEXT START]"]
                < positions["[RAW MEMORY TEXT END]"]
                < positions["[TEACHER-ONLY RAW MEMORY END]"]
                < positions["[CURRENT APPWORLD STATE START]"]
                < positions["[CURRENT APPWORLD STATE END]"]
            )
            leakage_overlap = sorted(
                _leakage_keys_for_example(example).intersection(_leakage_keys_for_record(record))
            )
            row_audit = {
                "label": label,
                "state_example_id": row["state_example_id"],
                "candidate_memory_id": row["candidate_memory_id"],
                "text_utility": row["text_utility"],
                "candidate_source": row.get("candidate_source"),
                "leakage_overlap": leakage_overlap,
                "delimiter_counts": counts,
                "delimiter_counts_ok": all(count == 1 for count in counts.values()),
                "section_order_ok": order_ok,
                "memory_id_present": row["candidate_memory_id"] in prompt,
                "memory_task_id_present": str(row.get("candidate_memory_task_id")) in prompt,
                "target_hash_matches": row["target_sha256"] == sha256_text(_target_suffix(example)),
                "memory_text_hash_matches": row["memory_text_sha256"] == sha256_text(record.experience_text),
            }
            row_audit["obvious_issue"] = bool(
                row_audit["leakage_overlap"]
                or not row_audit["delimiter_counts_ok"]
                or not row_audit["section_order_ok"]
                or not row_audit["memory_id_present"]
                or not row_audit["memory_task_id_present"]
                or not row_audit["target_hash_matches"]
                or not row_audit["memory_text_hash_matches"]
            )
            inspected.append(row_audit)
    return {
        "format": "raw_text_teacher_audit3b_representative_prompt_inspection_v1",
        "inspected_row_count": len(inspected),
        "obvious_issue_count": sum(1 for row in inspected if row["obvious_issue"]),
        "rows": inspected,
    }


def _run_full_cache_preflight(
    *,
    output_dir: Path,
    backend: Any,
    tokenizer: Any,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    prompt_profile: str,
    context_limit: int,
    raw_memory_tokens: list[int],
    seconds_per_scored_pair: float | None,
    seconds_per_l0_score: float | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rows_path = output_dir / "full_cache_preflight_rows.jsonl"
    if rows_path.exists():
        rows_path.unlink()
    over_context_pairs = []
    per_state_rows = []
    pair_count = 0
    over_context_count = 0
    scoreable_count = 0
    for example_index, example in enumerate(examples):
        base_messages = _appworld_messages_from_example(example, prompt_profile)
        base_prompt = backend.render_messages(base_messages, add_generation_prompt=True)
        state_prompt_tokens = len(_token_ids(tokenizer, base_prompt, add_special_tokens=False))
        target_tokens = len(_target_token_ids(tokenizer, example))
        legal = legal_memory_indices(records, example)
        state_over_context = 0
        for memory_index in legal:
            preflight, _memory_prompt = _preflight_pair(
                backend=backend,
                tokenizer=tokenizer,
                base_messages=base_messages,
                prompt_profile=prompt_profile,
                example_index=example_index,
                memory_index=memory_index,
                examples=examples,
                records=records,
                state_prompt_tokens=state_prompt_tokens,
                raw_memory_tokens=raw_memory_tokens[memory_index],
                target_tokens=target_tokens,
                context_limit=context_limit,
            )
            pair_count += 1
            if preflight["over_context"]:
                over_context_count += 1
                state_over_context += 1
                over_context_pairs.append(preflight)
            else:
                scoreable_count += 1
            append_jsonl(rows_path, {**preflight, "format": AUDIT3B_PREFLIGHT_VERSION})
        per_state_rows.append(
            {
                "state_example_id": _example_id(example_index, example),
                "example_index": example_index,
                "task_id": _example_task_id(example),
                "episode_id": example.episode_id,
                "step_id": example.step_id,
                "legal_pair_count": len(legal),
                "over_context_pair_count": state_over_context,
                "scoreable_pair_count": len(legal) - state_over_context,
                "state_prompt_tokens": state_prompt_tokens,
                "target_tokens": target_tokens,
            }
        )
    runtime_s = time.perf_counter() - started
    estimated_wall_time_s = None
    estimated_h100_hours = None
    if seconds_per_scored_pair is not None:
        estimated_wall_time_s = scoreable_count * seconds_per_scored_pair
        if seconds_per_l0_score is not None:
            estimated_wall_time_s += len(examples) * seconds_per_l0_score
        estimated_h100_hours = estimated_wall_time_s / 3600.0
    summary = {
        "format": AUDIT3B_PREFLIGHT_VERSION,
        "total_state_count": len(examples),
        "total_memory_record_count": len(records),
        "context_limit": context_limit,
        "exact_legal_pair_count": pair_count,
        "exact_scoreable_pair_count": scoreable_count,
        "exact_over_context_pair_count": over_context_count,
        "over_context_fraction": over_context_count / pair_count if pair_count else None,
        "preflight_runtime_s": runtime_s,
        "preflight_rows_path": str(rows_path),
        "per_state": per_state_rows,
        "over_context_pairs": over_context_pairs,
        "seconds_per_scored_pair_assumption": seconds_per_scored_pair,
        "seconds_per_l0_score_assumption": seconds_per_l0_score,
        "estimated_scoring_wall_time_s": estimated_wall_time_s,
        "estimated_h100_hours": estimated_h100_hours,
    }
    atomic_write_json(output_dir / "full_cache_preflight_summary.json", summary)
    return summary


def _recommend(summary: dict[str, Any]) -> dict[str, Any]:
    utility = summary["utility_distribution"]
    full_preflight = summary["full_cache_preflight"]
    reproducibility_ok = summary["reproducibility_check"]["all_within_tolerance"]
    prompt_issues = summary["representative_prompt_inspection"]["obvious_issue_count"]
    positive_count = summary["utility_counts"].get("positive", 0)
    projected_hours = full_preflight.get("estimated_h100_hours")
    if prompt_issues or not reproducibility_ok:
        choice = "B"
        rationale = (
            "Repair teacher/prompt semantics first because reproducibility or representative prompt "
            "inspection found an issue."
        )
    elif positive_count > 0 and utility.get("max") is not None and float(utility["max"]) > 0.25:
        choice = "A"
        rationale = (
            "Generate the complete all-legal teacher cache after review: the local-Qwen target-loss "
            "teacher is reproducible, representative prompts did not show obvious leakage or delimiter "
            "errors, positive and negative utility signal exists, and all-legal scoring removes the "
            "candidate-recall bottleneck instead of depending on it."
        )
        if projected_hours is not None:
            rationale += f" Estimated scoring cost is {projected_hours:.2f} H100 hours."
    else:
        choice = "C"
        rationale = (
            "Abandon the local-Qwen raw-text teacher only if the expanded audit shows no useful positive "
            "utility signal; this condition was not met in the current pilot."
        )
    return {"choice": choice, "rationale": rationale}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Milestone 3B Expanded Raw-Text Teacher Audit",
        "",
        f"- version: `{summary['format']}`",
        f"- commit: `{summary['commit_sha']}`",
        f"- model: `{summary['model_name']}`",
        f"- selected pilot states: {summary['selected_state_count']}",
        f"- legal pair count: {summary['legal_pair_count']}",
        f"- scored rows: {summary['scored_row_count']}",
        f"- over-context rows masked: {summary['over_context_pair_count']}",
        f"- reused cached rows: {summary['cached_row_count']}",
        f"- newly scored rows: {summary['newly_scored_row_count']}",
        f"- runtime seconds: {summary['runtime_s']:.2f}",
        "",
        "## Utility",
        "",
        f"- positive: {summary['utility_counts'].get('positive', 0)}",
        f"- neutral: {summary['utility_counts'].get('neutral', 0)}",
        f"- negative: {summary['utility_counts'].get('negative', 0)}",
        f"- mean: {summary['utility_distribution']['mean']}",
        f"- std: {summary['utility_distribution']['std']}",
        f"- min: {summary['utility_distribution']['min']}",
        f"- p50: {summary['utility_distribution']['p50']}",
        f"- max: {summary['utility_distribution']['max']}",
        "",
        "## Existing Proposal Recall And Regret",
        "",
    ]
    overall = summary["expanded_audit_metrics"]["overall"]
    for k in RECALL_KS:
        lines.append(f"- recall@{k}: {overall[f'recall@{k}']}")
    lines.extend(
        [
            f"- mean regret: {overall['mean_regret']}",
            f"- median regret: {overall['median_regret']}",
            f"- positive utility mass coverage: {overall['positive_utility_mass_coverage']}",
            "",
            "## Thresholded Metrics",
            "",
        ]
    )
    for threshold, metrics in summary["expanded_audit_metrics"]["thresholds"].items():
        lines.append(
            f"- best legal utility {threshold}: states={metrics['state_count']} "
            f"recall@4={metrics['recall@4']} mean_regret={metrics['mean_regret']}"
        )
    lines.extend(["", "## Candidate Source Ablations", ""])
    for source, metrics in summary["expanded_audit_metrics"]["source_ablations"].items():
        lines.append(
            f"- {source}: hit_rate={metrics['hit_best_legal_rate']} "
            f"mean_regret={metrics['mean_regret']} "
            f"positive_mass_coverage={metrics['positive_utility_mass_coverage']}"
        )
    lines.extend(
        [
            "",
            "## Full 638-State Preflight",
            "",
            f"- exact legal pairs: {summary['full_cache_preflight']['exact_legal_pair_count']}",
            f"- exact scoreable pairs: {summary['full_cache_preflight']['exact_scoreable_pair_count']}",
            f"- exact over-context pairs: {summary['full_cache_preflight']['exact_over_context_pair_count']}",
            f"- estimated H100 hours: {summary['full_cache_preflight']['estimated_h100_hours']}",
            "",
            "## Recommendation",
            "",
            f"- choice: {summary['recommendation']['choice']}",
            f"- rationale: {summary['recommendation']['rationale']}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand the raw-text teacher audit to all existing pilot states.")
    parser.add_argument("--config", default="configs/benchmark/appworld_rcmf_full_prompt.yaml")
    parser.add_argument("--data", required=True)
    parser.add_argument("--pilot-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--representation-batch-size", type=int, default=1)
    parser.add_argument("--repro-tolerance", type=float, default=1.0e-5)
    parser.add_argument("--skip-full-cache-preflight", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Expanded raw-text teacher audit requires hf_qwen backend")
    data_dir = Path(args.data)
    pilot_dir = Path(args.pilot_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    backend = build_backend(cfg, load_model=True)
    tokenizer = backend.tokenizer
    context_limit = _context_limit_for_backend(backend)
    prompt_profile = cfg.benchmark.prompt_profile
    renderer_metadata = appworld_renderer_metadata(prompt_profile, add_generation_prompt=True)
    commit_sha = maybe_git_commit() or "unknown"

    pilot_states = atomic_read_json(pilot_dir / "pilot_states.json")
    selected_indices = [int(row["example_index"]) for row in pilot_states]
    selected_set = set(selected_indices)
    if len(selected_indices) != 24:
        raise ValueError(f"Milestone 3B expects 24 existing pilot states, got {len(selected_indices)}")

    base_messages_by_index = {
        index: _appworld_messages_from_example(examples[index], prompt_profile)
        for index in selected_indices
    }
    base_prompt_texts = {
        index: backend.render_messages(messages, add_generation_prompt=True)
        for index, messages in base_messages_by_index.items()
    }
    prompt_token_counts = {
        index: len(_token_ids(tokenizer, base_prompt_texts[index], add_special_tokens=False))
        for index in selected_indices
    }
    target_ids_by_index = {
        index: _target_token_ids(tokenizer, examples[index])
        for index in selected_indices
    }
    target_text_by_index = {
        index: _target_suffix(examples[index])
        for index in selected_indices
    }
    target_token_counts = {index: len(target_ids_by_index[index]) for index in selected_indices}
    raw_memory_tokens = [
        len(_token_ids(tokenizer, record.experience_text, add_special_tokens=False))
        for record in records
    ]

    existing_rows = read_jsonl(pilot_dir / "teacher_labels.jsonl")
    existing_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    l0_by_index: dict[int, float] = {}
    for row in existing_rows:
        example_index = int(row["example_index"])
        memory_index = int(row["candidate_memory_index"])
        if example_index in selected_set:
            existing_by_pair[(example_index, memory_index)] = row
            l0_by_index.setdefault(example_index, float(row["L0"]))
    l0_score_times: list[float] = []
    for example_index in selected_indices:
        if example_index in l0_by_index:
            continue
        l0_started = time.perf_counter()
        l0, _prompt_tokens, _target_tokens = _score_mean_target_nll(
            backend,
            base_prompt_texts[example_index],
            target_ids_by_index[example_index],
            target_text_by_index[example_index],
            context_limit,
        )
        l0_score_times.append(time.perf_counter() - l0_started)
        l0_by_index[example_index] = l0

    state_representations, memory_representations, chunk_audit = _load_or_compute_representations(
        backend=backend,
        examples=examples,
        records=records,
        selected_indices=selected_indices,
        prompt_profile=prompt_profile,
        output_dir=pilot_dir,
        batch_size=args.representation_batch_size,
    )
    selected_position = {example_index: offset for offset, example_index in enumerate(selected_indices)}
    record_apps = [apps_for_record(record) for record in records]
    proposal_sources_by_pair: dict[tuple[int, int], list[str]] = {}
    proposal_rank_by_pair: dict[tuple[int, int], int] = {}
    proposal_order_by_state: dict[str, list[str]] = {}
    proposal_mismatches = []
    for state_row in pilot_states:
        example_index = int(state_row["example_index"])
        example = examples[example_index]
        candidates = propose_candidates(
            example=example,
            state_representation=state_representations[selected_position[example_index]],
            memory_representations=memory_representations,
            records=records,
            record_apps=record_apps,
            example_apps=apps_for_example(example),
            seed=args.seed * 1000003 + example_index,
        )
        proposal_order = list(candidates)
        proposal_order_by_state[state_row["state_example_id"]] = [
            records[memory_index].memory_id for memory_index in proposal_order
        ]
        for rank, memory_index in enumerate(proposal_order, start=1):
            proposal_sources_by_pair[(example_index, memory_index)] = list(candidates[memory_index])
            proposal_rank_by_pair[(example_index, memory_index)] = rank
        expected_ids = set(state_row.get("candidate_memory_ids", []))
        actual_ids = {records[memory_index].memory_id for memory_index in proposal_order}
        if expected_ids and expected_ids != actual_ids:
            proposal_mismatches.append(
                {
                    "state_example_id": state_row["state_example_id"],
                    "expected": sorted(expected_ids),
                    "actual": sorted(actual_ids),
                }
            )

    labels_path = output_dir / "teacher_labels_audit3b.jsonl"
    if labels_path.exists():
        labels_path.unlink()
    all_rows: list[dict[str, Any]] = []
    newly_scored_count = 0
    cached_count = 0
    over_context_count = 0
    scoring_time_s = 0.0
    for example_index in selected_indices:
        legal = legal_memory_indices(records, examples[example_index])
        for memory_index in legal:
            proposal_sources = proposal_sources_by_pair.get((example_index, memory_index), [])
            proposal_rank = proposal_rank_by_pair.get((example_index, memory_index))
            cached = existing_by_pair.get((example_index, memory_index))
            if cached is not None:
                row = _copy_cached_row(cached, proposal_sources, proposal_rank)
                cached_count += 1
            else:
                preflight, memory_prompt = _preflight_pair(
                    backend=backend,
                    tokenizer=tokenizer,
                    base_messages=base_messages_by_index[example_index],
                    prompt_profile=prompt_profile,
                    example_index=example_index,
                    memory_index=memory_index,
                    examples=examples,
                    records=records,
                    state_prompt_tokens=prompt_token_counts[example_index],
                    raw_memory_tokens=raw_memory_tokens[memory_index],
                    target_tokens=target_token_counts[example_index],
                    context_limit=context_limit,
                )
                row = _base_row(
                    example_index=example_index,
                    memory_index=memory_index,
                    examples=examples,
                    records=records,
                    l0=l0_by_index[example_index],
                    preflight=preflight,
                    target_ids=target_ids_by_index[example_index],
                    renderer_metadata=renderer_metadata,
                    backend=backend,
                    commit_sha=commit_sha,
                    proposal_sources=proposal_sources,
                    proposal_rank=proposal_rank,
                    row_origin="scored_audit3b",
                )
                if row["over_context"]:
                    row["skipped_reason"] = "over_context"
                else:
                    score_started = time.perf_counter()
                    lj, _prompt_tokens, _target_tokens = _score_mean_target_nll(
                        backend,
                        memory_prompt,
                        target_ids_by_index[example_index],
                        target_text_by_index[example_index],
                        context_limit,
                    )
                    scoring_time_s += time.perf_counter() - score_started
                    utility = l0_by_index[example_index] - lj
                    row["Lj_text"] = lj
                    row["text_utility"] = utility
                    row["utility_category"] = _category(utility)
                    newly_scored_count += 1
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
            if row["over_context"]:
                over_context_count += 1
            all_rows.append(row)
            append_jsonl(labels_path, row)

    scored_rows = [row for row in all_rows if _is_scoreable(row)]
    utility_values = [float(row["text_utility"]) for row in scored_rows]
    memory_lengths = [float(row["raw_memory_tokens"]) for row in scored_rows]
    combined_lengths = [float(row["total_tokens_with_target"]) for row in scored_rows]
    utility_counts = Counter(row["utility_category"] for row in scored_rows)
    expanded_metrics = compute_expanded_audit_metrics(all_rows, proposal_order_by_state)
    per_state_path = output_dir / "per_state_table.json"
    atomic_write_json(per_state_path, expanded_metrics["per_state"])
    _write_per_state_csv(output_dir / "per_state_table.csv", expanded_metrics["per_state"])

    reproducibility_rows = _select_reproducibility_rows(scored_rows)
    reproducibility = _run_reproducibility_check(
        rows_by_name=reproducibility_rows,
        backend=backend,
        base_prompt_texts=base_prompt_texts,
        base_messages_by_index=base_messages_by_index,
        prompt_profile=prompt_profile,
        examples=examples,
        records=records,
        target_ids_by_index=target_ids_by_index,
        target_text_by_index=target_text_by_index,
        context_limit=context_limit,
        tolerance=args.repro_tolerance,
    )
    atomic_write_json(output_dir / "reproducibility_check.json", reproducibility)
    representative_prompt_inspection = _inspect_representative_prompts(
        rows=all_rows,
        backend=backend,
        base_messages_by_index=base_messages_by_index,
        prompt_profile=prompt_profile,
        examples=examples,
        records=records,
    )
    atomic_write_json(output_dir / "representative_prompt_inspection.json", representative_prompt_inspection)

    seconds_per_new_score = scoring_time_s / newly_scored_count if newly_scored_count else None
    if seconds_per_new_score is None:
        seconds_per_new_score = reproducibility.get("mean_repeat_Lj_score_s")
    seconds_per_l0 = statistics.fmean(l0_score_times) if l0_score_times else None
    if seconds_per_l0 is None:
        seconds_per_l0 = reproducibility.get("mean_repeat_L0_score_s")
    if args.skip_full_cache_preflight:
        full_preflight = {
            "format": AUDIT3B_PREFLIGHT_VERSION,
            "skipped": True,
            "reason": "--skip-full-cache-preflight",
        }
    else:
        full_preflight = _run_full_cache_preflight(
            output_dir=output_dir,
            backend=backend,
            tokenizer=tokenizer,
            examples=examples,
            records=records,
            prompt_profile=prompt_profile,
            context_limit=context_limit,
            raw_memory_tokens=raw_memory_tokens,
            seconds_per_scored_pair=seconds_per_new_score,
            seconds_per_l0_score=seconds_per_l0,
        )

    runtime_s = time.perf_counter() - started
    summary = {
        "format": AUDIT3B_CACHE_VERSION,
        "config": args.config,
        "data": str(data_dir),
        "pilot_dir": str(pilot_dir),
        "output_dir": str(output_dir),
        "decision_examples_sha256": sha256_file(data_dir / "decision_examples.jsonl"),
        "memory_records_sha256": sha256_file(data_dir / "memory_records.jsonl"),
        "commit_sha": commit_sha,
        "model_name": backend.model_name,
        "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
        "context_limit": context_limit,
        "selected_state_count": len(selected_indices),
        "legal_pair_count": len(all_rows),
        "scored_row_count": len(scored_rows),
        "cached_row_count": cached_count,
        "newly_scored_row_count": newly_scored_count,
        "over_context_pair_count": over_context_count,
        "utility_counts": dict(utility_counts),
        "utility_distribution": _distribution(utility_values),
        "correlations": {
            "utility_vs_memory_tokens": _pearson(utility_values, memory_lengths),
            "utility_vs_combined_context_tokens": _pearson(utility_values, combined_lengths),
        },
        "expanded_audit_metrics": expanded_metrics,
        "proposal_mismatches": proposal_mismatches,
        "reproducibility_check": reproducibility,
        "representative_prompt_inspection": representative_prompt_inspection,
        "full_cache_preflight": full_preflight,
        "seconds_per_newly_scored_pair": seconds_per_new_score,
        "seconds_per_l0_score": seconds_per_l0,
        "runtime_s": runtime_s,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "memory_chunk_audit": chunk_audit,
        "teacher_labels_path": str(labels_path),
        "per_state_table_json": str(per_state_path),
        "per_state_table_csv": str(output_dir / "per_state_table.csv"),
    }
    summary["recommendation"] = _recommend(summary)
    atomic_write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(_render_report(summary), encoding="utf-8")
    print(f"Wrote expanded raw-text teacher audit to {output_dir}")


def atomic_read_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
