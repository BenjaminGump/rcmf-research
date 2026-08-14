from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from transformers import AutoTokenizer

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import (
    TRANSITION_MANIFEST_VERSION,
    DecisionTransition,
    extract_decision_transitions,
    select_transition_panel,
    transition_teacher_section,
    validate_transition_extraction,
)
from rcmf.config import load_config, save_resolved_config
from rcmf.schemas import DecisionExample
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.transition_memory_6a import (
    TRANSITION_PREFLIGHT_VERSION,
    canonical_json_sha256,
    example_leakage_keys,
    example_task_id,
    is_legal_transition_pair,
    messages_with_transition_memory,
    select_query_manifest,
    state_example_id,
    transition_field_algebra_validation,
    transition_leakage_keys,
    transition_step_bucket,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import _target_token_ids, _token_ids


def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _distribution(values: Sequence[int | float]) -> dict[str, Any]:
    data = sorted(float(value) for value in values)
    if not data:
        return {"count": 0}

    def percentile(fraction: float) -> float:
        index = int(math.floor((len(data) - 1) * fraction + 0.5))
        return data[index]

    return {
        "count": len(data),
        "min": data[0],
        "max": data[-1],
        "mean": statistics.fmean(data),
        "std": statistics.pstdev(data) if len(data) > 1 else 0.0,
        "p25": percentile(0.25),
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
    }


def _render_prompt(tokenizer: Any, messages: list[dict[str, str]], profile: str) -> tuple[str, dict[str, Any]]:
    return _render_prompt_with_metadata(tokenizer, messages, profile)


def _transition_manifest_rows(
    transitions: Sequence[DecisionTransition], tokenizer: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, transition in enumerate(transitions, start=1):
        row = transition.to_manifest_row()
        section = transition_teacher_section(transition)
        row.update(
            {
                "transition_jsonl_line": index,
                "source_task_goal_tokens": len(
                    _token_ids(tokenizer, transition.source_task_goal)
                ),
                "canonical_pre_action_state_tokens": len(
                    _token_ids(tokenizer, transition.canonical_pre_action_state)
                ),
                "complete_action_tokens": len(
                    _token_ids(tokenizer, transition.complete_action)
                ),
                "complete_post_action_observation_tokens": len(
                    _token_ids(
                        tokenizer, transition.complete_post_action_observation
                    )
                ),
                "teacher_section_tokens": len(_token_ids(tokenizer, section)),
                "teacher_section_sha256": sha256_text(section),
                "tokenizer_name_or_path": str(
                    getattr(tokenizer, "name_or_path", "unknown")
                ),
            }
        )
        rows.append(row)
        if index % 50 == 0:
            print(f"tokenized {index}/{len(transitions)} extracted transitions", flush=True)
    return rows


def _panel_summary(
    panel_rows: Sequence[dict[str, Any]], panel_selection: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "format": "decision_transition_panel_summary_6a_v1",
        "parent_trajectories_covered": len(
            {str(row["parent_memory_id"]) for row in panel_rows}
        ),
        "parent_tasks_covered": len({str(row["parent_task_id"]) for row in panel_rows}),
        "transitions_selected": len(panel_rows),
        "step_position_distribution": dict(
            Counter(
                transition_step_bucket(int(row["step_index"]), int(row["step_count"]))
                for row in panel_rows
            )
        ),
        "app_distribution": dict(
            Counter(app for row in panel_rows for app in row.get("apps", []))
        ),
        "api_distribution": dict(
            Counter(api for row in panel_rows for api in row.get("api_names", []))
        ),
        "action_type_distribution": dict(
            Counter(str(row["action_type"]) for row in panel_rows)
        ),
        "completion_transition_count": sum(
            bool(row["completion_related"]) for row in panel_rows
        ),
        "completion_transition_count_all_extracted": int(
            panel_selection["completion_transition_count_all_extracted"]
        ),
        "token_length_distribution": {
            key: _distribution([int(row[key]) for row in panel_rows])
            for key in (
                "source_task_goal_tokens",
                "canonical_pre_action_state_tokens",
                "complete_action_tokens",
                "complete_post_action_observation_tokens",
                "teacher_section_tokens",
            )
        },
    }


def _runtime_projection(
    *,
    scoreable_pairs: int,
    query_count: int,
    panel_count: int,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = settings["runtime_projection"]
    inference_s = (
        float(runtime["historical_teacher_runtime_s"])
        / float(runtime["historical_teacher_scoreable_pairs"])
        * float(runtime["inference_safety_multiplier"])
    )
    gradient_s = float(runtime["historical_gradient_update_s"])
    static_count = int(settings["static_transition"]["transition_count"])
    maximum_parent_count = static_count
    expected_parent_count = max(
        int(settings["static_transition"]["minimum_parent_count"]),
        round(static_count * 0.67),
    )
    pair_oracle_count = int(settings["pair_oracle"]["pairs_per_category"]) * 4
    validation_queries = int(settings["queries"]["validation_tasks"]) * int(
        settings["queries"]["states_per_validation_task"]
    )
    all_queries = query_count

    teacher_forwards = query_count + scoreable_pairs
    transition_response_forwards = query_count + pair_oracle_count + static_count * all_queries
    trajectory_response_forwards_expected = query_count + expected_parent_count * all_queries
    trajectory_response_forwards_conservative = query_count + maximum_parent_count * all_queries
    pair_updates_64 = pair_oracle_count * 64
    static_updates_64 = static_count * 64
    trajectory_updates_64_expected = expected_parent_count * 64
    trajectory_updates_64_conservative = maximum_parent_count * 64
    pair_controls = pair_oracle_count * 4
    static_controls = static_count * validation_queries * 6
    trajectory_controls_expected = expected_parent_count * validation_queries * 6
    trajectory_controls_conservative = maximum_parent_count * validation_queries * 6

    best_seconds = (
        teacher_forwards * inference_s
        + transition_response_forwards * inference_s
        + trajectory_response_forwards_expected * inference_s
        + (
            pair_updates_64
            + static_updates_64
            + trajectory_updates_64_expected
        )
        * gradient_s
        + (pair_controls + static_controls + trajectory_controls_expected)
        * inference_s
        + 1800.0
    )
    expected_seconds = best_seconds + 0.25 * (
        pair_updates_64 + static_updates_64 + trajectory_updates_64_expected
    ) * gradient_s + 900.0
    conservative_seconds = (
        teacher_forwards * inference_s
        + transition_response_forwards * inference_s
        + trajectory_response_forwards_conservative * inference_s
        + 2
        * (
            pair_updates_64
            + static_updates_64
            + trajectory_updates_64_conservative
        )
        * gradient_s
        + (pair_controls + static_controls + trajectory_controls_conservative)
        * inference_s
        + 3600.0
    )
    review_threshold = float(runtime["preflight_review_threshold_h100_hours"])
    expected_hours = expected_seconds / 3600.0
    return {
        "format": "decision_transition_runtime_projection_6a_v1",
        "basis": {
            "historical_teacher_seconds_per_pair": float(
                runtime["historical_teacher_runtime_s"]
            )
            / float(runtime["historical_teacher_scoreable_pairs"]),
            "projected_inference_seconds_per_forward_with_safety": inference_s,
            "historical_gradient_seconds_per_pair_update": gradient_s,
            "inference_safety_multiplier": float(
                runtime["inference_safety_multiplier"]
            ),
        },
        "assumptions": {
            "teacher_forwards": teacher_forwards,
            "transition_response_forwards": transition_response_forwards,
            "trajectory_response_forwards_expected": trajectory_response_forwards_expected,
            "trajectory_response_forwards_conservative": trajectory_response_forwards_conservative,
            "pair_oracle_pairs": pair_oracle_count,
            "static_transition_count": static_count,
            "expected_parent_count": expected_parent_count,
            "maximum_parent_count": maximum_parent_count,
            "updates_through_64": {
                "pair": pair_updates_64,
                "transition": static_updates_64,
                "trajectory_expected": trajectory_updates_64_expected,
                "trajectory_conservative": trajectory_updates_64_conservative,
            },
            "panel_count": panel_count,
        },
        "best_case_h100_hours": best_seconds / 3600.0,
        "expected_h100_hours": expected_hours,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "preflight_review_threshold_h100_hours": review_threshold,
        "expected_runtime_review_required": expected_hours > review_threshold,
    }


def _report(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    runtime = summary["runtime_projection"]
    panel = summary["panel_summary"]
    return "\n".join(
        [
            "# EXP-017 Decision-Transition Preflight",
            "",
            "## VERIFIED",
            "",
            f"- source commit: `{summary['source_commit']}`",
            f"- parent trajectories: `{counts['parent_trajectory_count']}`",
            f"- extracted transitions: `{counts['transition_count']}`",
            f"- panel transitions: `{counts['panel_transition_count']}`",
            f"- query states: `{counts['query_count']}` "
            f"(train `{counts['train_query_count']}`, validation `{counts['validation_query_count']}`)",
            f"- exact legal pairs: `{counts['legal_pair_count']}`",
            f"- scoreable pairs: `{counts['scoreable_pair_count']}`",
            f"- over-context pairs: `{counts['over_context_pair_count']}`",
            f"- extraction validation passed: `{summary['extraction_validation']['passed']}`",
            f"- transition-field algebra passed: `{summary['field_algebra']['passed']}`",
            f"- no truncation: `{summary['hard_scope']['no_truncation']}`",
            f"- query overlap with EXP-016C: `{len(summary['query_manifest']['selected_exp016c_overlap'])}`",
            "",
            "## Panel",
            "",
            f"- parent coverage: `{panel['parent_trajectories_covered']}`",
            f"- step buckets: `{json.dumps(panel['step_position_distribution'], sort_keys=True)}`",
            f"- action types: `{json.dumps(panel['action_type_distribution'], sort_keys=True)}`",
            f"- selected completion transitions: `{panel['completion_transition_count']}`",
            "",
            "## Runtime Projection",
            "",
            f"- best case: `{runtime['best_case_h100_hours']:.3f}` H100-hours",
            f"- expected: `{runtime['expected_h100_hours']:.3f}` H100-hours",
            f"- conservative: `{runtime['conservative_h100_hours']:.3f}` H100-hours",
            "- preflight review threshold: "
            f"`{runtime['preflight_review_threshold_h100_hours']:.3f}` H100-hours",
            "- explicit runtime approval required: "
            f"`{runtime['expected_runtime_review_required']}`",
            "",
            "The projection covers raw-teacher scoring, response-cache scoring, pair-oracle "
            "optimization, static transition and trajectory baselines, controls, validation, "
            "and reporting. Optional u128 continuation is represented in the conservative case.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and token-preflight EXP-017 decision-transition memory."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_transition_memory_6a.yaml"),
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--decoder-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context-limit", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6a"]
    save_resolved_config(cfg, args.output_dir / "resolved_config.yaml")
    atomic_write_json(args.output_dir / "stage_c_6a_settings.json", settings)

    split_manifest = _load_json(args.split_manifest)
    decoder_manifest = _load_json(args.decoder_manifest)
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    records = load_memory_records(args.data / "memory_records.jsonl")
    train_tasks = {str(value) for value in split_manifest["train_task_ids"]}
    validation_tasks = {str(value) for value in split_manifest["validation_task_ids"]}
    parent_records = [record for record in records if record.task_id in train_tasks]
    validation_parent_records = [
        record for record in records if record.task_id in validation_tasks
    ]
    if len(parent_records) != 37 or len(validation_parent_records) != 9:
        raise ValueError(
            f"Source parent split differs: train={len(parent_records)} "
            f"validation={len(validation_parent_records)}"
        )
    if any(not record.success for record in parent_records):
        raise ValueError("Transition source bank contains an unsuccessful parent")

    transitions = [
        transition
        for record in parent_records
        for transition in extract_decision_transitions(record)
    ]
    extraction_validation = validate_transition_extraction(parent_records, transitions)
    atomic_write_json(
        args.output_dir / "transition_extraction_validation.json",
        extraction_validation,
    )
    if not extraction_validation["passed"]:
        raise RuntimeError(
            f"Transition extraction validation failed: "
            f"{extraction_validation['errors_first_50']}"
        )
    panel, panel_selection = select_transition_panel(transitions)
    if len(panel) > int(settings["transition_panel"]["hard_stop_max"]):
        raise RuntimeError(
            f"Transition panel has {len(panel)} rows, exceeding the hard stop of "
            f"{settings['transition_panel']['hard_stop_max']}; no reduction was applied"
        )

    tokenizer = AutoTokenizer.from_pretrained(
        cfg.model.name, trust_remote_code=True
    )
    if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
        tokenizer.pad_token = tokenizer.eos_token
    context_limit = int(args.context_limit or settings["context_limit"])

    prompt_contexts: dict[int, dict[str, Any]] = {}
    prompt_token_counts: list[int] = []
    for index, example in enumerate(examples):
        messages = _appworld_messages_from_example(
            example, cfg.benchmark.prompt_profile
        )
        prompt, prompt_metadata = _render_prompt(
            tokenizer, messages, cfg.benchmark.prompt_profile
        )
        prompt_ids = _token_ids(tokenizer, prompt)
        target_ids = _target_token_ids(tokenizer, example)
        prompt_contexts[index] = {
            "base_messages": messages,
            "base_prompt": prompt,
            "base_prompt_sha256": sha256_text(prompt),
            "prompt_metadata": prompt_metadata,
            "state_prompt_tokens": len(prompt_ids),
            "target_ids": target_ids,
            "target_tokens": len(target_ids),
            "target_text": _target_suffix(example),
        }
        prompt_token_counts.append(len(prompt_ids))
        if (index + 1) % 100 == 0:
            print(f"rendered {index + 1}/{len(examples)} base prompts", flush=True)

    query_manifest = select_query_manifest(
        examples=examples,
        prompt_token_counts=prompt_token_counts,
        split_manifest=split_manifest,
        decoder_manifest=decoder_manifest,
        seed=int(settings["query_seed"]),
    )
    atomic_write_json(args.output_dir / "query_manifest.json", query_manifest)

    transition_rows = _transition_manifest_rows(transitions, tokenizer)
    transition_by_id = {str(row["transition_id"]): row for row in transition_rows}
    panel_ids = {item.transition_id for item in panel}
    reasons = panel_selection["selection_reasons_by_transition_id"]
    panel_rows = [
        {
            **transition_by_id[item.transition_id],
            "panel_selection_reasons": reasons[item.transition_id],
        }
        for item in panel
    ]
    if {str(row["transition_id"]) for row in panel_rows} != panel_ids:
        raise RuntimeError("Panel transition IDs do not match extracted transition manifest")
    write_jsonl(args.output_dir / "transition_manifest.jsonl", transition_rows)
    write_jsonl(args.output_dir / "transition_panel.jsonl", panel_rows)
    atomic_write_json(args.output_dir / "transition_panel_selection.json", panel_selection)
    panel_summary = _panel_summary(panel_rows, panel_selection)
    atomic_write_json(args.output_dir / "transition_panel_summary.json", panel_summary)

    query_rows = query_manifest["query_rows"]
    preflight_rows: list[dict[str, Any]] = []
    illegal_rows: list[dict[str, Any]] = []
    baseline_over_context: list[str] = []
    for query_position, query_row in enumerate(query_rows, start=1):
        example_index = int(query_row["example_index"])
        example = examples[example_index]
        context = prompt_contexts[example_index]
        baseline_total = int(context["state_prompt_tokens"]) + int(
            context["target_tokens"]
        )
        if baseline_total > context_limit:
            baseline_over_context.append(str(query_row["state_example_id"]))
        for transition in panel:
            pair_id = f"{query_row['state_example_id']}::transition::{transition.transition_id}"
            state_keys = example_leakage_keys(example)
            source_keys = transition_leakage_keys(transition)
            overlap = sorted(state_keys.intersection(source_keys))
            if not is_legal_transition_pair(example, transition):
                illegal_rows.append(
                    {
                        "pair_id": pair_id,
                        "state_example_id": query_row["state_example_id"],
                        "transition_id": transition.transition_id,
                        "leakage_overlap": overlap,
                    }
                )
                continue
            teacher_messages = messages_with_transition_memory(
                context["base_messages"], transition, cfg.benchmark.prompt_profile
            )
            teacher_prompt, _ = _render_prompt(
                tokenizer, teacher_messages, cfg.benchmark.prompt_profile
            )
            combined_prompt_tokens = len(_token_ids(tokenizer, teacher_prompt))
            total_tokens = combined_prompt_tokens + int(context["target_tokens"])
            transition_row = transition_by_id[transition.transition_id]
            over_context = total_tokens > context_limit
            preflight_rows.append(
                {
                    "format": TRANSITION_PREFLIGHT_VERSION,
                    "pair_id": pair_id,
                    "pair_id_sha256": sha256_text(pair_id),
                    "query_manifest_position": query_position,
                    "state_example_id": query_row["state_example_id"],
                    "example_index": example_index,
                    "task_id": example_task_id(example),
                    "episode_id": example.episode_id,
                    "step_id": int(example.step_id),
                    "split": query_row["split"],
                    "transition_id": transition.transition_id,
                    "parent_memory_id": transition.parent_memory_id,
                    "parent_task_id": transition.parent_task_id,
                    "parent_episode_id": transition.parent_episode_id,
                    "transition_step_index": transition.step_index,
                    "transition_step_count": transition.step_count,
                    "transition_step_bucket": transition_step_bucket(
                        transition.step_index, transition.step_count
                    ),
                    "transition_apps": list(transition.apps),
                    "transition_api_names": list(transition.api_names),
                    "transition_action_type": transition.action_type,
                    "transition_completion_related": transition.completion_related,
                    "leakage_keys_state": sorted(state_keys),
                    "leakage_keys_transition": sorted(source_keys),
                    "leakage_overlap": overlap,
                    "state_prompt_tokens": int(context["state_prompt_tokens"]),
                    "source_goal_tokens": int(
                        transition_row["source_task_goal_tokens"]
                    ),
                    "source_state_tokens": int(
                        transition_row["canonical_pre_action_state_tokens"]
                    ),
                    "action_tokens": int(transition_row["complete_action_tokens"]),
                    "observation_tokens": int(
                        transition_row[
                            "complete_post_action_observation_tokens"
                        ]
                    ),
                    "transition_section_tokens": int(
                        transition_row["teacher_section_tokens"]
                    ),
                    "combined_prompt_tokens": combined_prompt_tokens,
                    "target_tokens": int(context["target_tokens"]),
                    "total_tokens_with_target": total_tokens,
                    "context_limit": context_limit,
                    "score_status": "over_context" if over_context else "pending",
                    "valid_for_loss": False,
                    "over_context": over_context,
                    "truncated": False,
                    "base_prompt_sha256": context["base_prompt_sha256"],
                    "teacher_prompt_sha256": sha256_text(teacher_prompt),
                    "target_sha256": sha256_text(context["target_text"]),
                    "target_token_sha256": sha256_text(
                        ",".join(str(value) for value in context["target_ids"])
                    ),
                    "transition_content_sha256": transition.transition_content_sha256,
                    "teacher_section_sha256": transition_row[
                        "teacher_section_sha256"
                    ],
                    "renderer_version": appworld_renderer_metadata(
                        cfg.benchmark.prompt_profile
                    )["renderer_version"],
                    "transition_renderer_version": "decision_transition_teacher_section_v1",
                    "model_name": cfg.model.name,
                }
            )
        print(
            f"preflighted query {query_position}/{len(query_rows)} "
            f"pairs={len(preflight_rows)}",
            flush=True,
        )
    if baseline_over_context:
        raise RuntimeError(
            f"Selected baseline queries exceed context without transition: "
            f"{baseline_over_context}"
        )
    write_jsonl(args.output_dir / "pair_preflight.jsonl", preflight_rows)
    write_jsonl(args.output_dir / "illegal_pairs.jsonl", illegal_rows)
    legal_pair_count = len(preflight_rows)
    over_context_count = sum(bool(row["over_context"]) for row in preflight_rows)
    scoreable_pair_count = legal_pair_count - over_context_count
    expected_cartesian = len(query_rows) * len(panel_rows)
    if legal_pair_count + len(illegal_rows) != expected_cartesian:
        raise RuntimeError("Legal plus illegal pair count does not equal query-panel product")

    runtime_projection = _runtime_projection(
        scoreable_pairs=scoreable_pair_count,
        query_count=len(query_rows),
        panel_count=len(panel_rows),
        settings=settings,
    )
    field_algebra = transition_field_algebra_validation(seed=int(settings["seed"]))
    atomic_write_json(args.output_dir / "field_algebra_validation.json", field_algebra)
    atomic_write_json(args.output_dir / "runtime_projection.json", runtime_projection)
    summary = {
        "format": "decision_transition_preflight_summary_6a_v1",
        "status": (
            "paused_projected_runtime_requires_explicit_approval"
            if runtime_projection["expected_runtime_review_required"]
            else "passed_ready_for_gpu_review"
        ),
        "timestamp_utc": utc_now(),
        "source_commit": maybe_git_commit(),
        "config": str(args.config),
        "data": str(args.data),
        "split_manifest": str(args.split_manifest),
        "decoder_manifest": str(args.decoder_manifest),
        "output_dir": str(args.output_dir),
        "counts": {
            "parent_trajectory_count": len(parent_records),
            "excluded_validation_parent_count": len(validation_parent_records),
            "transition_count": len(transitions),
            "panel_transition_count": len(panel_rows),
            "query_count": len(query_rows),
            "train_query_count": int(query_manifest["train_query_count"]),
            "validation_query_count": int(
                query_manifest["validation_query_count"]
            ),
            "cartesian_pair_count": expected_cartesian,
            "illegal_pair_count": len(illegal_rows),
            "legal_pair_count": legal_pair_count,
            "scoreable_pair_count": scoreable_pair_count,
            "over_context_pair_count": over_context_count,
        },
        "over_context_pair_ids": [
            str(row["pair_id"]) for row in preflight_rows if row["over_context"]
        ],
        "over_context_by_parent": dict(
            Counter(
                str(row["parent_memory_id"])
                for row in preflight_rows
                if row["over_context"]
            )
        ),
        "over_context_by_query": dict(
            Counter(
                str(row["state_example_id"])
                for row in preflight_rows
                if row["over_context"]
            )
        ),
        "extraction_validation": extraction_validation,
        "panel_summary": panel_summary,
        "query_manifest": query_manifest,
        "field_algebra": field_algebra,
        "runtime_projection": runtime_projection,
        "hard_scope": {
            "branch": "research/v4-decision-transition-memory",
            "no_truncation": all(not row["truncated"] for row in preflight_rows),
            "validation_parent_trajectories_excluded": all(
                row["parent_task_id"] not in validation_tasks for row in panel_rows
            ),
            "same_task_episode_replay_lineage_excluded": all(
                not row["leakage_overlap"] for row in preflight_rows
            ),
            "qwen_forward_run": False,
            "appworld_generation_or_evaluation_run": False,
            "exp016d_launched": False,
        },
        "hashes": {
            "decision_examples_sha256": sha256_file(
                args.data / "decision_examples.jsonl"
            ),
            "memory_records_sha256": sha256_file(
                args.data / "memory_records.jsonl"
            ),
            "split_manifest_sha256": sha256_file(args.split_manifest),
            "decoder_manifest_sha256": sha256_file(args.decoder_manifest),
            "transition_manifest_sha256": sha256_file(
                args.output_dir / "transition_manifest.jsonl"
            ),
            "transition_panel_sha256": sha256_file(
                args.output_dir / "transition_panel.jsonl"
            ),
            "query_manifest_sha256": sha256_file(
                args.output_dir / "query_manifest.json"
            ),
            "pair_preflight_sha256": sha256_file(
                args.output_dir / "pair_preflight.jsonl"
            ),
            "tokenizer_chat_template_sha256": sha256_text(
                str(getattr(tokenizer, "chat_template", ""))
            ),
            "settings_sha256": canonical_json_sha256(settings),
        },
        "runtime_s": time.perf_counter() - started,
    }
    atomic_write_json(args.output_dir / "preflight_summary.json", summary)
    atomic_write_text(args.output_dir / "preflight_report.md", _report(summary))
    print(json.dumps(summary["counts"], indent=2, sort_keys=True), flush=True)
    print(json.dumps(runtime_projection, indent=2, sort_keys=True), flush=True)
    if runtime_projection["expected_runtime_review_required"]:
        raise SystemExit(
            "Projected expected GPU runtime exceeds the preflight review threshold; "
            "stopped before GPU work pending explicit approval"
        )


if __name__ == "__main__":
    main()
