from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    append_jsonl_fsync,
    utc_now,
)
from rcmf.training.transition_memory_6a import (
    TRANSITION_TEACHER_CACHE_VERSION,
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
from scripts.run_raw_text_teacher_pilot import (
    _context_limit_for_backend,
    _score_mean_target_nll,
)
from scripts.run_transition_teacher_6a import (
    _ensure_l0,
    _load_unique_journal,
    _overlap_features,
    _query_contexts,
    _reproducibility_check,
    _teacher_analysis,
    _validate_teacher_cache,
)


TEACHER_CACHE_VERSION = "all_task_decision_transition_raw_teacher_cache_6d_v1"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _validate_source_l0(
    *,
    source: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    target_text = str(context["target_text"])
    target_ids = [int(value) for value in context["target_ids"]]
    checks = {
        "target_sha256": sha256_text(target_text),
        "target_token_sha256": sha256_text(
            ",".join(str(value) for value in target_ids)
        ),
    }
    for key, expected in checks.items():
        if source.get(key) != expected:
            raise ValueError(f"Reusable L0 differs for {context['manifest']['state_example_id']}: {key}")
    if not math.isfinite(float(source["L0"])):
        raise ValueError("Reusable L0 is not finite")


def _teacher_report(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    return "\n".join(
        [
            "# EXP-020 Expanded Raw-Transition Teacher Cache",
            "",
            "## VERIFIED",
            "",
            f"- source commit: `{summary['source_commit']}`",
            f"- cache version: `{summary['cache_version']}`",
            f"- legal rows: `{counts['legal_pairs']}`",
            f"- scoreable rows: `{counts['scoreable_pairs']}`",
            f"- over-context rows: `{counts['over_context_pairs']}`",
            f"- reused rows: `{counts['reused_pairs']}`",
            f"- newly scored rows: `{counts['newly_scored_pairs']}`",
            f"- new over-context rows: `{counts['new_over_context_pairs']}`",
            f"- validation passed: `{summary['validation']['passed']}`",
            f"- reproducibility passed: `{summary['reproducibility']['passed']}`",
            f"- runtime: `{summary['runtime_seconds'] / 3600.0:.3f}` H100-hours",
            "",
            "No prompt, target, or transition was truncated. Qwen was frozen and only "
            "teacher-forced target scoring was used.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score only new EXP-020 raw transition teacher pairs"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp020")
    parser.add_argument("--progress-interval-s", type=float, default=300.0)
    parser.add_argument(
        "--approve-runtime-over-review-threshold", action="store_true"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    preflight = _load_json(args.artifact_dir / "preflight_summary.json")
    allowed = {"passed_ready_for_gpu_scoring"}
    if args.approve_runtime_over_review_threshold:
        allowed.add("paused_projected_runtime_requires_explicit_approval")
    if preflight["status"] not in allowed:
        raise ValueError(f"EXP-020 preflight is not approved: {preflight['status']}")
    if (
        preflight["runtime_projection"]["expected_runtime_review_required"]
        and not args.approve_runtime_over_review_threshold
    ):
        raise ValueError("Projected runtime exceeds 12 H100-hours without explicit approval")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    if run_manifest["run_uuid"] != str(settings["run_uuid"]):
        raise ValueError("Run UUID differs from the immutable preflight")
    source_data = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    query_manifest = _load_json(args.artifact_dir / "expanded_query_manifest.json")
    preflight_rows = _load_rows(args.artifact_dir / "pair_preflight.jsonl")
    panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
    source_teacher_rows = _load_rows(exp017 / "teacher_cache.jsonl")
    source_l0 = _load_json(exp017 / "l0_cache.json")
    reuse_validation = _load_json(args.artifact_dir / "teacher_reuse_validation.json")
    if not reuse_validation["passed"]:
        raise ValueError("Teacher reuse validation did not pass")
    for key, path in (
        ("decision_examples", source_data / "decision_examples.jsonl"),
        ("memory_records", source_data / "memory_records.jsonl"),
        ("exp017_transition_panel", exp017 / "transition_panel.jsonl"),
        ("expanded_query_manifest", args.artifact_dir / "expanded_query_manifest.json"),
        ("pair_preflight", args.artifact_dir / "pair_preflight.jsonl"),
        ("teacher_reuse_validation", args.artifact_dir / "teacher_reuse_validation.json"),
    ):
        if sha256_file(path) != preflight["hashes"][key]:
            raise ValueError(f"Preflight source hash differs: {key}")
    existing_attempt_ids = {
        str(row["attempt_id"])
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    }
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="expanded_raw_transition_teacher_scoring",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        backend = build_backend(cfg, load_model=True)
        backend.model.eval()
        for parameter in backend.model.parameters():
            parameter.requires_grad_(False)
        context_limit = _context_limit_for_backend(backend)
        if context_limit != int(settings["context_limit"]):
            raise ValueError("Runtime context limit differs from preflight")
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        contexts = _query_contexts(
            backend=backend,
            examples=examples,
            query_manifest=query_manifest,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        transitions = {str(row["transition_id"]): row for row in panel_rows}
        reusable_ids = set(reuse_validation["validated_reuse_pair_ids"])
        reusable = {
            str(row["pair_id"]): row
            for row in source_teacher_rows
            if str(row["pair_id"]) in reusable_ids
        }
        if len(reusable) != int(reuse_validation["validated_reuse_count"]):
            raise ValueError("Reusable teacher row count differs")
        l0_path = args.artifact_dir / "l0_cache.json"
        l0_cache = _load_json(l0_path) if l0_path.exists() else {}
        for state_id, row in source_l0.items():
            if state_id not in contexts:
                continue
            _validate_source_l0(source=row, context=contexts[state_id])
            l0_cache.setdefault(state_id, row)
        atomic_write_json(l0_path, l0_cache)
        journal_path = args.artifact_dir / "teacher_new_rows_journal.jsonl"
        journal = _load_unique_journal(journal_path, "pair_id")
        overlap = sorted(set(journal).intersection(reusable))
        if overlap:
            raise ValueError(f"New journal duplicates immutable reused rows: {overlap[:20]}")
        completed = {**reusable, **journal}
        new_scored = sum(bool(row.get("valid_for_loss")) for row in journal.values())
        new_over_context = sum(
            row.get("score_status") == "over_context" for row in journal.values()
        )
        last_progress = time.perf_counter()
        for index, source in enumerate(preflight_rows, start=1):
            pair_id = str(source["pair_id"])
            if pair_id in completed:
                continue
            state_id = str(source["state_example_id"])
            transition_id = str(source["transition_id"])
            context = contexts[state_id]
            transition = transitions[transition_id]
            l0 = _ensure_l0(
                backend=backend,
                context=context,
                context_limit=context_limit,
                cache=l0_cache,
                cache_path=l0_path,
            )
            row = {
                **source,
                "format": TEACHER_CACHE_VERSION,
                "compatible_source_format": TRANSITION_TEACHER_CACHE_VERSION,
                "scoring_definition": (
                    "frozen_qwen_full_demo_plus_single_raw_decision_transition_target_nll_v1"
                ),
                "L0": l0,
                "Lj_transition": None,
                "text_utility": None,
                "utility_category": None,
                "score_status": "over_context" if source["over_context"] else "pending",
                "valid_for_loss": False,
                "source_commit_sha": maybe_git_commit(),
                "checkpoint_identity": f"frozen_hf_pretrained:{backend.model_name}",
                "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
                "scoring_timestamp_utc": utc_now(),
                "score_time_s": 0.0,
            }
            row.update(
                _overlap_features(
                    example=context["example"],
                    query_manifest_row=context["manifest"],
                    transition=transition,
                )
            )
            if source["over_context"]:
                row["skipped_reason"] = "over_context_no_truncation"
                new_over_context += 1
            else:
                messages = messages_with_transition_memory(
                    context["base_messages"], transition, cfg.benchmark.prompt_profile
                )
                prompt = backend.render_messages(messages, add_generation_prompt=True)
                if sha256_text(prompt) != source["teacher_prompt_sha256"]:
                    raise ValueError(f"Teacher prompt hash differs for {pair_id}")
                score_started = time.perf_counter()
                lj, prompt_tokens, target_tokens = _score_mean_target_nll(
                    backend,
                    prompt,
                    list(context["target_ids"]),
                    str(context["target_text"]),
                    context_limit,
                )
                if prompt_tokens != int(source["combined_prompt_tokens"]):
                    raise ValueError(f"Teacher prompt token count differs for {pair_id}")
                if target_tokens != int(source["target_tokens"]):
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
            append_jsonl_fsync(journal_path, row)
            completed[pair_id] = row
            now = time.perf_counter()
            if now - last_progress >= float(args.progress_interval_s):
                elapsed = now - started
                remaining = len(preflight_rows) - len(completed)
                newly_completed = new_scored + new_over_context
                rate = newly_completed / max(elapsed, 1.0)
                eta = remaining / max(rate, 1.0e-9)
                status = {
                    "completed_pairs": len(completed),
                    "total_pairs": len(preflight_rows),
                    "reused_pairs": len(reusable),
                    "newly_scored_pairs": new_scored,
                    "new_over_context_pairs": new_over_context,
                    "elapsed_hours": elapsed / 3600.0,
                    "eta_hours": eta / 3600.0,
                    "latest_validated_checkpoint": str(journal_path),
                }
                attempt.progress(status="teacher_scoring", **status)
                print(json.dumps(status, sort_keys=True), flush=True)
                last_progress = now
        ordered = [completed[str(row["pair_id"])] for row in preflight_rows]
        write_jsonl(args.artifact_dir / "teacher_cache.jsonl", ordered)
        validation = _validate_teacher_cache(rows=ordered, preflight_rows=preflight_rows)
        atomic_write_json(args.artifact_dir / "teacher_cache_validation.json", validation)
        if not validation["passed"]:
            raise RuntimeError(f"Teacher validation failed: {validation['errors_first_50']}")
        reproducibility = _reproducibility_check(
            backend=backend,
            rows=ordered,
            contexts=contexts,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=context_limit,
        )
        atomic_write_json(args.artifact_dir / "teacher_reproducibility.json", reproducibility)
        if not reproducibility["passed"]:
            raise RuntimeError("Teacher reproducibility check failed")
        analysis = _teacher_analysis(ordered)
        atomic_write_json(args.artifact_dir / "teacher_analysis.json", analysis)
        runtime_seconds = time.perf_counter() - started
        summary = {
            "format": "all_task_transition_teacher_summary_6d_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "cache_version": TEACHER_CACHE_VERSION,
            "source_commit": maybe_git_commit(),
            "model_name": backend.model_name,
            "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
            "renderer": appworld_renderer_metadata(cfg.benchmark.prompt_profile),
            "counts": {
                "legal_pairs": len(ordered),
                "scoreable_pairs": validation["scoreable_pair_count"],
                "over_context_pairs": validation["over_context_pair_count"],
                "reused_pairs": len(reusable),
                "newly_scored_pairs": new_scored,
                "new_over_context_pairs": new_over_context,
                "states": len(contexts),
            },
            "validation": validation,
            "reproducibility": reproducibility,
            "utility_analysis": analysis,
            "runtime_seconds": runtime_seconds,
            "actual_h100_hours": runtime_seconds / 3600.0,
            "artifacts": {
                "teacher_cache": str(args.artifact_dir / "teacher_cache.jsonl"),
                "teacher_cache_sha256": sha256_file(args.artifact_dir / "teacher_cache.jsonl"),
                "new_rows_journal": str(journal_path),
                "new_rows_journal_sha256": sha256_file(journal_path),
                "l0_cache": str(l0_path),
            },
            "hard_scope": {
                "qwen_frozen": True,
                "teacher_forced_scoring_only": True,
                "no_truncation": all(not bool(row["truncated"]) for row in ordered),
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "appworld_generation_or_evaluation": False,
            },
            "timestamp_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "teacher_summary.json", summary)
        atomic_write_text(args.artifact_dir / "teacher_report.md", _teacher_report(summary))
        attempt.progress(
            status="teacher_cache_completed",
            counts=summary["counts"],
            latest_validated_checkpoint=str(args.artifact_dir / "teacher_summary.json"),
        )
        print(json.dumps(summary["counts"], indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
