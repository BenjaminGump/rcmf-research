from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config, save_resolved_config
from rcmf.training.all_task_interaction_6d import (
    build_fixed_learning_curve_manifest,
    runtime_and_size_projection,
    select_all_task_query_manifest,
    validate_reusable_teacher_rows,
)
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
    utc_now,
)
from rcmf.training.transition_memory_6a import (
    TRANSITION_PREFLIGHT_VERSION,
    example_leakage_keys,
    example_task_id,
    is_legal_transition_pair,
    messages_with_transition_memory,
    state_example_id,
    transition_leakage_keys,
    transition_step_bucket,
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
from scripts.run_raw_text_teacher_pilot import _target_token_ids, _token_ids


PREFLIGHT_VERSION = "all_task_transition_teacher_preflight_6d_v1"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _assert_expected(name: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"Immutable {name} count differs: {actual} != {expected}")


def _render_base_context(
    tokenizer: Any,
    example: Any,
    prompt_profile: str,
) -> dict[str, Any]:
    messages = _appworld_messages_from_example(example, prompt_profile)
    prompt, prompt_metadata = _render_prompt_with_metadata(
        tokenizer, messages, prompt_profile
    )
    target_ids = _target_token_ids(tokenizer, example)
    target_text = _target_suffix(example)
    return {
        "base_messages": messages,
        "base_prompt": prompt,
        "base_prompt_sha256": sha256_text(prompt),
        "prompt_metadata": prompt_metadata,
        "state_prompt_tokens": len(_token_ids(tokenizer, prompt)),
        "target_ids": target_ids,
        "target_tokens": len(target_ids),
        "target_text": target_text,
        "target_sha256": sha256_text(target_text),
        "target_token_sha256": sha256_text(
            ",".join(str(value) for value in target_ids)
        ),
    }


def _build_preflight(
    *,
    tokenizer: Any,
    examples: list[Any],
    query_manifest: Mapping[str, Any],
    panel_rows: list[dict[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    renderer_version = appworld_renderer_metadata(prompt_profile)["renderer_version"]
    contexts: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(query_manifest["query_rows"], start=1):
        example_index = int(row["example_index"])
        example = examples[example_index]
        identity = state_example_id(example_index, example)
        if identity != str(row["state_example_id"]):
            raise ValueError(f"Query identity differs at position {position}: {identity}")
        contexts[identity] = _render_base_context(tokenizer, example, prompt_profile)
    preflight_rows: list[dict[str, Any]] = []
    illegal_rows: list[dict[str, Any]] = []
    baseline_over_context: list[str] = []
    for query_position, query in enumerate(query_manifest["query_rows"], start=1):
        example_index = int(query["example_index"])
        example = examples[example_index]
        state_id = str(query["state_example_id"])
        context = contexts[state_id]
        if int(context["state_prompt_tokens"]) + int(context["target_tokens"]) > context_limit:
            baseline_over_context.append(state_id)
        state_keys = example_leakage_keys(example)
        for transition in panel_rows:
            transition_id = str(transition["transition_id"])
            pair_id = f"{state_id}::transition::{transition_id}"
            transition_keys = transition_leakage_keys(transition)
            overlap = sorted(state_keys.intersection(transition_keys))
            if not is_legal_transition_pair(example, transition):
                illegal_rows.append(
                    {
                        "pair_id": pair_id,
                        "state_example_id": state_id,
                        "transition_id": transition_id,
                        "leakage_overlap": overlap,
                    }
                )
                continue
            messages = messages_with_transition_memory(
                context["base_messages"], transition, prompt_profile
            )
            teacher_prompt, _ = _render_prompt_with_metadata(
                tokenizer, messages, prompt_profile
            )
            combined_prompt_tokens = len(_token_ids(tokenizer, teacher_prompt))
            total_tokens = combined_prompt_tokens + int(context["target_tokens"])
            over_context = total_tokens > context_limit
            preflight_rows.append(
                {
                    "format": PREFLIGHT_VERSION,
                    "source_preflight_format": TRANSITION_PREFLIGHT_VERSION,
                    "pair_id": pair_id,
                    "pair_id_sha256": sha256_text(pair_id),
                    "query_manifest_position": query_position,
                    "state_example_id": state_id,
                    "example_index": example_index,
                    "task_id": example_task_id(example),
                    "episode_id": example.episode_id,
                    "step_id": int(example.step_id),
                    "split": query["split"],
                    "transition_id": transition_id,
                    "parent_memory_id": transition["parent_memory_id"],
                    "parent_task_id": transition["parent_task_id"],
                    "parent_episode_id": transition["parent_episode_id"],
                    "transition_step_index": int(transition["step_index"]),
                    "transition_step_count": int(transition["step_count"]),
                    "transition_step_bucket": transition_step_bucket(
                        int(transition["step_index"]), int(transition["step_count"])
                    ),
                    "transition_apps": list(transition.get("apps", [])),
                    "transition_api_names": list(transition.get("api_names", [])),
                    "transition_action_type": transition["action_type"],
                    "transition_completion_related": bool(
                        transition["completion_related"]
                    ),
                    "leakage_keys_state": sorted(state_keys),
                    "leakage_keys_transition": sorted(transition_keys),
                    "leakage_overlap": overlap,
                    "state_prompt_tokens": int(context["state_prompt_tokens"]),
                    "source_goal_tokens": int(transition["source_task_goal_tokens"]),
                    "source_state_tokens": int(
                        transition["canonical_pre_action_state_tokens"]
                    ),
                    "action_tokens": int(transition["complete_action_tokens"]),
                    "observation_tokens": int(
                        transition["complete_post_action_observation_tokens"]
                    ),
                    "transition_section_tokens": int(
                        transition["teacher_section_tokens"]
                    ),
                    "combined_prompt_tokens": combined_prompt_tokens,
                    "target_tokens": int(context["target_tokens"]),
                    "total_tokens_with_target": total_tokens,
                    "context_limit": int(context_limit),
                    "score_status": "over_context" if over_context else "pending",
                    "valid_for_loss": False,
                    "over_context": over_context,
                    "truncated": False,
                    "base_prompt_sha256": context["base_prompt_sha256"],
                    "teacher_prompt_sha256": sha256_text(teacher_prompt),
                    "target_sha256": context["target_sha256"],
                    "target_token_sha256": context["target_token_sha256"],
                    "transition_content_sha256": transition[
                        "transition_content_sha256"
                    ],
                    "teacher_section_sha256": transition["teacher_section_sha256"],
                    "renderer_version": renderer_version,
                    "transition_renderer_version": (
                        "decision_transition_teacher_section_v1"
                    ),
                    "model_name": str(getattr(tokenizer, "name_or_path", "unknown")),
                }
            )
        print(
            f"preflighted query {query_position}/{len(query_manifest['query_rows'])} "
            f"legal={len(preflight_rows)} illegal={len(illegal_rows)}",
            flush=True,
        )
    if baseline_over_context:
        raise RuntimeError(
            f"Baseline query prompts exceed context without a transition: {baseline_over_context}"
        )
    return preflight_rows, illegal_rows, contexts


def _report(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    runtime = summary["runtime_projection"]
    size = runtime["artifact_size_projection"]
    return "\n".join(
        [
            "# EXP-020 All-Task Query-Coverage Preflight",
            "",
            "## VERIFIED",
            "",
            f"- source commit: `{summary['source_commit']}`",
            f"- query states: `{counts['query_count']}` "
            f"(train `{counts['train_query_count']}`, held out `{counts['validation_query_count']}`)",
            f"- transitions: `{counts['transition_count']}`",
            f"- exact Cartesian pairs: `{counts['cartesian_pair_count']}`",
            f"- illegal leakage pairs: `{counts['illegal_pair_count']}`",
            f"- exact legal pairs: `{counts['legal_pair_count']}`",
            f"- scoreable pairs: `{counts['scoreable_pair_count']}`",
            f"- over-context pairs: `{counts['over_context_pair_count']}`",
            f"- validated reusable rows: `{counts['reusable_pair_count']}`",
            f"- newly scoreable rows: `{counts['new_scoreable_pair_count']}`",
            f"- no truncation: `{summary['hard_scope']['no_truncation']}`",
            "",
            "## Projection",
            "",
            f"- best case: `{runtime['best_case_h100_hours']:.3f}` H100-hours",
            f"- expected: `{runtime['expected_h100_hours']:.3f}` H100-hours",
            f"- conservative: `{runtime['conservative_h100_hours']:.3f}` H100-hours",
            f"- review threshold: `{runtime['review_threshold_h100_hours']:.3f}` H100-hours",
            f"- approval pause required: `{runtime['expected_runtime_review_required']}`",
            f"- expected artifact size: `{size['expected_gib']:.2f}` GiB",
            f"- conservative artifact size: `{size['conservative_gib']:.2f}` GiB",
            f"- persistent free space before run: `{summary['storage']['free_gib']:.2f}` GiB",
            "",
            "## Resume Plan",
            "",
            "- one immutable run UUID and append-only attempt ledger;",
            "- one atomic L0 entry per state and one unique pair key per teacher row;",
            "- old rows are read-only references and new rows use an fsync journal;",
            "- multi-view and cross-encoder representations are atomic per state/pair;",
            "- grouped-CV checkpoints carry config/data hashes and exact epoch state;",
            "- reconnects inspect heartbeat/tmux/process before any resume.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare EXP-020 all-task preflight")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="none-preflight")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    atomic_write_json(args.artifact_dir / "stage_c_6d_settings.json", settings)
    source_data = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    exp019 = Path(settings["exp019_artifact"])
    split_path = Path(settings["split_manifest"])
    decoder_path = Path(settings["decoder_manifest"])
    expected = settings["expected"]
    prior_summary = _load_json(exp017 / "preflight_summary.json")
    prior_query_manifest = _load_json(exp017 / "query_manifest.json")
    prior_preflight = _load_rows(exp017 / "pair_preflight.jsonl")
    prior_teacher = _load_rows(exp017 / "teacher_cache.jsonl")
    panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
    parent_split = _load_json(exp018 / "transition_parent_split_manifest.json")
    _assert_expected("transition panel", len(panel_rows), expected["transitions"])
    _assert_expected("transition parents", len({row["parent_memory_id"] for row in panel_rows}), expected["transition_parents"])
    _assert_expected("source query", prior_query_manifest["query_count"], expected["original_queries"])
    _assert_expected("source legal", len(prior_preflight), expected["original_legal_rows"])
    _assert_expected("source teacher", len(prior_teacher), expected["original_legal_rows"])
    _assert_expected("train transition parents", parent_split["train_parent_count"], expected["train_transition_parents"])
    _assert_expected("heldout transition parents", parent_split["heldout_parent_count"], expected["heldout_transition_parents"])
    data_hashes = {
        "decision_examples": sha256_file(source_data / "decision_examples.jsonl"),
        "memory_records": sha256_file(source_data / "memory_records.jsonl"),
        "split_manifest": sha256_file(split_path),
        "decoder_manifest": sha256_file(decoder_path),
        "exp017_query_manifest": sha256_file(exp017 / "query_manifest.json"),
        "exp017_transition_panel": sha256_file(exp017 / "transition_panel.jsonl"),
        "exp017_pair_preflight": sha256_file(exp017 / "pair_preflight.jsonl"),
        "exp017_teacher_cache": sha256_file(exp017 / "teacher_cache.jsonl"),
        "exp018_parent_split": sha256_file(exp018 / "transition_parent_split_manifest.json"),
    }
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "expanded_query_manifest",
            "teacher_preflight_and_reuse",
            "representations",
            "fixed_exp019_reproduction",
            "lc12_lc24_lc37",
            "controls_and_gate",
        ],
    )
    existing_attempt_ids = {
        str(row["attempt_id"])
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    } if (args.artifact_dir / "attempts.jsonl").exists() else set()
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="all_task_manifest_and_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        split_manifest = _load_json(split_path)
        decoder_manifest = _load_json(decoder_path)
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        tokenizer = AutoTokenizer.from_pretrained(cfg.model.name, trust_remote_code=True)
        if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        prompt_counts = []
        for index, example in enumerate(examples, start=1):
            context = _render_base_context(tokenizer, example, cfg.benchmark.prompt_profile)
            prompt_counts.append(int(context["state_prompt_tokens"]))
            if index % 100 == 0:
                print(f"rendered {index}/{len(examples)} base prompts", flush=True)
        query_manifest = select_all_task_query_manifest(
            examples=examples,
            prompt_token_counts=prompt_counts,
            split_manifest=split_manifest,
            decoder_manifest=decoder_manifest,
            original_query_manifest=prior_query_manifest,
            seed=int(settings["query_seed"]),
        )
        _assert_expected("expanded query", query_manifest["query_count"], expected["total_queries"])
        _assert_expected("expanded train query", query_manifest["train_query_count"], expected["train_queries"])
        _assert_expected("expanded validation query", query_manifest["validation_query_count"], expected["validation_queries"])
        if query_manifest["task_shortages"]:
            raise RuntimeError(
                "One or more locked tasks lack two legal states; manifest was preserved for review: "
                f"{query_manifest['task_shortages']}"
            )
        learning_manifest = build_fixed_learning_curve_manifest(
            query_manifest,
            prior_query_manifest,
            seed=int(settings["learning_curve_seed"]),
        )
        atomic_write_json(args.artifact_dir / "expanded_query_manifest.json", query_manifest)
        atomic_write_json(args.artifact_dir / "learning_curve_manifest.json", learning_manifest)
        attempt.progress(
            status="query_manifest_validated",
            query_count=query_manifest["query_count"],
            latest_validated_checkpoint=str(args.artifact_dir / "expanded_query_manifest.json"),
        )
        preflight_rows, illegal_rows, _ = _build_preflight(
            tokenizer=tokenizer,
            examples=examples,
            query_manifest=query_manifest,
            panel_rows=panel_rows,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["context_limit"]),
        )
        write_jsonl(args.artifact_dir / "pair_preflight.jsonl", preflight_rows)
        write_jsonl(args.artifact_dir / "illegal_pairs.jsonl", illegal_rows)
        reuse = validate_reusable_teacher_rows(
            expanded_preflight_rows=preflight_rows,
            source_preflight_rows=prior_preflight,
            source_teacher_rows=prior_teacher,
        )
        atomic_write_json(args.artifact_dir / "teacher_reuse_validation.json", reuse)
        if not reuse["passed"]:
            raise RuntimeError(f"EXP-017 reuse validation failed: {reuse['errors_first_50']}")
        cartesian = len(query_manifest["query_rows"]) * len(panel_rows)
        if len(preflight_rows) + len(illegal_rows) != cartesian:
            raise RuntimeError("Legal plus illegal pairs do not equal the exact Cartesian product")
        over_context = sum(bool(row["over_context"]) for row in preflight_rows)
        scoreable = len(preflight_rows) - over_context
        reused_scoreable = sum(
            not bool(row["over_context"])
            for row in prior_preflight
            if str(row["pair_id"]) in set(reuse["validated_reuse_pair_ids"])
        )
        runtime = runtime_and_size_projection(
            total_scoreable_pairs=scoreable,
            reused_scoreable_pairs=reused_scoreable,
            new_query_count=len(query_manifest["query_rows"]) - len(prior_query_manifest["query_rows"]),
            observed_teacher_seconds_per_pair=float(settings["runtime"]["observed_teacher_seconds_per_pair"]),
            observed_cross_encoder_seconds_per_pair=float(settings["runtime"]["observed_cross_encoder_seconds_per_pair"]),
            observed_multiview_seconds_per_state=float(settings["runtime"]["observed_multiview_seconds_per_state"]),
            observed_model_runtime_seconds=float(settings["runtime"]["observed_model_runtime_seconds"]),
            prior_artifact_bytes=_directory_bytes(exp019),
            prior_query_count=int(expected["original_queries"]),
            prior_scoreable_pairs=int(expected["original_scoreable_rows"]),
            review_threshold_h100_hours=float(settings["runtime"]["review_threshold_h100_hours"]),
        )
        storage = shutil.disk_usage(args.artifact_dir)
        counts = {
            "query_count": len(query_manifest["query_rows"]),
            "train_query_count": int(query_manifest["train_query_count"]),
            "validation_query_count": int(query_manifest["validation_query_count"]),
            "transition_count": len(panel_rows),
            "cartesian_pair_count": cartesian,
            "illegal_pair_count": len(illegal_rows),
            "legal_pair_count": len(preflight_rows),
            "scoreable_pair_count": scoreable,
            "over_context_pair_count": over_context,
            "reusable_pair_count": int(reuse["validated_reuse_count"]),
            "reusable_scoreable_pair_count": reused_scoreable,
            "reusable_over_context_pair_count": int(reuse["validated_reuse_count"]) - reused_scoreable,
            "new_pair_count": len(preflight_rows) - int(reuse["validated_reuse_count"]),
            "new_scoreable_pair_count": scoreable - reused_scoreable,
            "new_over_context_pair_count": over_context - (int(reuse["validated_reuse_count"]) - reused_scoreable),
        }
        summary = {
            "format": "all_task_transition_preflight_summary_6d_v1",
            "status": (
                "paused_projected_runtime_requires_explicit_approval"
                if runtime["expected_runtime_review_required"]
                else "passed_ready_for_gpu_scoring"
            ),
            "run_uuid": str(settings["run_uuid"]),
            "timestamp_utc": utc_now(),
            "source_commit": maybe_git_commit(),
            "counts": counts,
            "query_manifest": query_manifest,
            "learning_curve_manifest": learning_manifest,
            "reuse_validation": reuse,
            "runtime_projection": runtime,
            "storage": {
                "total_bytes": storage.total,
                "used_bytes": storage.used,
                "free_bytes": storage.free,
                "free_gib": storage.free / (1024.0**3),
            },
            "over_context_pair_ids": [
                str(row["pair_id"]) for row in preflight_rows if row["over_context"]
            ],
            "over_context_by_query": dict(
                Counter(
                    str(row["state_example_id"])
                    for row in preflight_rows
                    if row["over_context"]
                )
            ),
            "over_context_by_parent": dict(
                Counter(
                    str(row["parent_memory_id"])
                    for row in preflight_rows
                    if row["over_context"]
                )
            ),
            "hard_scope": {
                "branch": "research/v4-decision-transition-memory",
                "no_truncation": all(not bool(row["truncated"]) for row in preflight_rows),
                "same_task_episode_replay_lineage_excluded": all(
                    not row["leakage_overlap"] for row in preflight_rows
                ),
                "immutable_transition_panel_reused": (
                    sha256_file(exp017 / "transition_panel.jsonl")
                    == prior_summary["hashes"]["transition_panel_sha256"]
                ),
                "qwen_model_loaded": False,
                "gpu_scoring_started": False,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "appworld_generation_or_evaluation": False,
                "v4_tag_created_or_moved": False,
            },
            "hashes": {
                **data_hashes,
                "expanded_query_manifest": sha256_file(args.artifact_dir / "expanded_query_manifest.json"),
                "learning_curve_manifest": sha256_file(args.artifact_dir / "learning_curve_manifest.json"),
                "pair_preflight": sha256_file(args.artifact_dir / "pair_preflight.jsonl"),
                "illegal_pairs": sha256_file(args.artifact_dir / "illegal_pairs.jsonl"),
                "teacher_reuse_validation": sha256_file(args.artifact_dir / "teacher_reuse_validation.json"),
                "tokenizer_chat_template": sha256_text(str(getattr(tokenizer, "chat_template", ""))),
            },
            "resume_plan": {
                "teacher": "atomic L0 JSON plus append-fsync unique-pair journal",
                "representations": "one atomic state/pair tensor row followed by validated aggregate",
                "models": "atomic fold/candidate checkpoints with immutable LC manifest",
                "duplicate_run_prevention": "single run UUID plus append-only attempts and heartbeat",
            },
            "runtime_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "preflight_report.md", _report(summary))
        attempt.progress(
            status=summary["status"],
            counts=counts,
            expected_h100_hours=runtime["expected_h100_hours"],
            latest_validated_checkpoint=str(args.artifact_dir / "preflight_summary.json"),
        )
        print(json.dumps(counts, indent=2, sort_keys=True), flush=True)
        print(json.dumps(runtime, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
