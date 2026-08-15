from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import _render_prompt_with_metadata, load_decision_examples
from rcmf.training.memory_use_target_6e import (
    messages_with_serialized_transition,
    serialization_robustness,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    append_jsonl_fsync,
    utc_now,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend, _score_mean_target_nll
from scripts.run_transition_teacher_6a import _query_contexts


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _load_journal(path: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return output
    for row in read_jsonl(path):
        key = f"{row['pair_id']}::{row['template']}"
        if key in output:
            raise ValueError(f"Duplicate serialization journal key: {key}")
        output[key] = row
    return output


def _validate_cached_row(row: Mapping[str, Any], preflight: Mapping[str, Any]) -> None:
    for key in (
        "pair_id", "state_example_id", "transition_id", "template",
        "base_prompt_sha256", "teacher_prompt_sha256", "target_token_sha256",
        "transition_content_sha256", "combined_prompt_tokens", "target_tokens",
        "over_context", "truncated",
    ):
        if row.get(key) != preflight.get(key):
            raise ValueError(f"Resumed serialization row differs for {row.get('pair_id')}: {key}")
    if bool(preflight["over_context"]):
        if row.get("text_utility") is not None or row.get("valid_for_loss") is not False:
            raise ValueError("Over-context serialization row has a utility")
    elif not all(math.isfinite(float(row[key])) for key in ("L0", "Lj_transition", "text_utility")):
        raise ValueError("Scoreable serialization row has non-finite loss")


def _report(summary: Mapping[str, Any]) -> str:
    robust = summary["robustness"]
    return "\n".join(
        [
            "# EXP-021 Teacher-Target Serialization Robustness",
            "",
            "## VERIFIED",
            "",
            f"- audit pairs: `{summary['audit_pair_count']}`",
            f"- newly scored frozen-Qwen forwards: `{summary['newly_scored_forwards']}`",
            f"- resumed rows: `{summary['resumed_rows']}`",
            f"- runtime: `{summary['runtime_seconds'] / 3600.0:.4f}` H100-hours",
            f"- complete three-template pairs: `{robust['complete_pair_count']}`",
            f"- median pairwise-template Spearman: `{robust['median_pairwise_template_spearman']}`",
            f"- mean positive/negative sign agreement: `{robust['mean_sign_agreement']}`",
            f"- mean per-state top-4 overlap: `{robust['mean_per_state_top4_overlap']}`",
            f"- length/utility Pearson: `{robust['length_utility_pearson']}`",
            f"- robustness gate passed: `{robust['gate_passed']}`",
            "",
            "No prompt, target, or raw transition was truncated. Template 0 was reused "
            "from the immutable EXP-020 cache and was not rewritten.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EXP-021 serialization audit")
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_memory_use_target_6e.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp021")
    parser.add_argument("--progress-interval-s", type=float, default=240.0)
    parser.add_argument("--approve-runtime-over-review-threshold", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6e"]
    preflight_summary = _load_json(args.artifact_dir / "preflight_summary.json")
    projection = preflight_summary["serialization_runtime_projection"]
    if projection["review_required"] and not args.approve_runtime_over_review_threshold:
        raise ValueError("Projected serialization runtime exceeds review threshold without approval")
    if preflight_summary["status"] not in {"ready_for_serialization_scoring", "requires_runtime_approval"}:
        raise ValueError(f"Invalid preflight status: {preflight_summary['status']}")
    exp017 = Path(settings["exp017_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    source = Path(settings["source_data"])
    audit = _load_json(args.artifact_dir / "serialization_audit_manifest.json")
    preflight_rows = _load_rows(args.artifact_dir / "serialization_preflight.jsonl")
    query_manifest = _load_json(exp020 / "expanded_query_manifest.json")
    panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
    locked_rows = _load_rows(exp020 / "two_axis_pair_rows.jsonl")
    locked_by_pair = {str(row["pair_id"]): row for row in locked_rows}
    transitions = {str(row["transition_id"]): row for row in panel_rows}
    examples = load_decision_examples(source / "decision_examples.jsonl")
    preflight_by_key = {
        f"{row['pair_id']}::{row['template']}": row for row in preflight_rows
    }
    if len(preflight_by_key) != len(preflight_rows):
        raise ValueError("Duplicate serialization preflight key")
    data_hashes = _load_json(args.artifact_dir / "run_manifest.json")["data_manifest_hashes"]
    journal_path = args.artifact_dir / "serialization_score_journal.jsonl"
    completed = _load_journal(journal_path)
    for key, row in completed.items():
        if key not in preflight_by_key:
            raise ValueError(f"Unexpected serialization journal key: {key}")
        _validate_cached_row(row, preflight_by_key[key])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="serialization_robustness_qwen_scoring",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head, github_head=args.github_head,
        lambda_head=args.lambda_head, tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config), data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        backend = build_backend(cfg, load_model=True)
        for parameter in backend.model.parameters():
            parameter.requires_grad_(False)
        backend.model.eval()
        context_limit = _context_limit_for_backend(backend)
        if context_limit != int(settings["context_limit"]):
            raise ValueError(f"Qwen context limit differs: {context_limit}")
        contexts = _query_contexts(
            backend=backend, examples=examples, query_manifest=query_manifest,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        newly_scored = 0
        newly_over_context = 0
        last_progress = time.perf_counter()
        for index, preflight in enumerate(preflight_rows, start=1):
            key = f"{preflight['pair_id']}::{preflight['template']}"
            if key in completed:
                continue
            pair_id = str(preflight["pair_id"])
            locked = locked_by_pair[pair_id]
            context = contexts[str(preflight["state_example_id"])]
            transition = transitions[str(preflight["transition_id"])]
            messages = messages_with_serialized_transition(
                context["base_messages"], transition,
                cfg.benchmark.prompt_profile, str(preflight["template"]),
            )
            prompt, _ = _render_prompt_with_metadata(
                backend.tokenizer, messages, cfg.benchmark.prompt_profile
            )
            if sha256_text(prompt) != str(preflight["teacher_prompt_sha256"]):
                raise ValueError(f"Serialization teacher prompt hash differs: {key}")
            row = {
                **dict(preflight),
                "L0": float(locked["L0"]),
                "model_name": str(backend.model_name),
                "scoring_definition": "frozen_qwen_full_demo_plus_exact_single_transition_serialization_target_nll_6e_v1",
                "scoring_timestamp_utc": utc_now(),
            }
            if bool(preflight["over_context"]):
                row.update({
                    "Lj_transition": None, "text_utility": None,
                    "score_status": "over_context", "valid_for_loss": False,
                    "score_time_s": 0.0,
                })
                newly_over_context += 1
            else:
                score_started = time.perf_counter()
                lj, prompt_tokens, target_tokens = _score_mean_target_nll(
                    backend, prompt, list(context["target_ids"]),
                    str(context["target_text"]), context_limit,
                )
                if prompt_tokens != int(preflight["combined_prompt_tokens"]):
                    raise ValueError(f"Prompt token count differs: {key}")
                if target_tokens != int(preflight["target_tokens"]):
                    raise ValueError(f"Target token count differs: {key}")
                utility = float(locked["L0"]) - float(lj)
                row.update({
                    "Lj_transition": float(lj), "text_utility": utility,
                    "score_status": "scored", "valid_for_loss": True,
                    "score_time_s": time.perf_counter() - score_started,
                })
                newly_scored += 1
            append_jsonl_fsync(journal_path, row)
            completed[key] = row
            now = time.perf_counter()
            if now - last_progress >= float(args.progress_interval_s):
                done = len(completed)
                rate = (newly_scored + newly_over_context) / max(now - started, 1.0)
                remaining = len(preflight_rows) - done
                status = {
                    "completed_template_rows": done,
                    "total_template_rows": len(preflight_rows),
                    "newly_scored_forwards": newly_scored,
                    "resumed_rows": done - newly_scored - newly_over_context,
                    "elapsed_seconds": now - started,
                    "eta_seconds": remaining / max(rate, 1.0e-9),
                    "latest_validated_checkpoint": str(journal_path),
                }
                attempt.progress(status="serialization_scoring", **status)
                print(json.dumps(status, sort_keys=True), flush=True)
                last_progress = now

        output = []
        for selected in audit["rows"]:
            locked = locked_by_pair[str(selected["pair_id"])]
            output.append({
                **dict(selected),
                "template": "template0",
                "combined_prompt_tokens": int(locked["combined_prompt_tokens"]),
                "target_tokens": int(locked["target_tokens"]),
                "L0": float(locked["L0"]),
                "Lj_transition": float(locked["Lj_transition"]),
                "text_utility": float(locked["text_utility"]),
                "score_status": "reused_locked_exp020",
                "valid_for_loss": True,
                "over_context": False,
                "truncated": False,
            })
            for template in ("canonical_json", "compact_tagged"):
                output.append(completed[f"{selected['pair_id']}::{template}"])
        keys = {(str(row["pair_id"]), str(row["template"])) for row in output}
        if len(keys) != len(output) or len(output) != int(audit["pair_count"]) * 3:
            raise ValueError("Final serialization cache key/count validation failed")
        for row in output:
            if bool(row.get("truncated")):
                raise ValueError("A serialization audit row was truncated")
            if bool(row["valid_for_loss"]):
                if abs(float(row["L0"]) - float(row["Lj_transition"]) - float(row["text_utility"])) > 1.0e-6:
                    raise ValueError("Serialization utility identity failed")
        write_jsonl(args.artifact_dir / "serialization_teacher_cache.jsonl", output)
        robustness = serialization_robustness(
            output, gate=settings["serialization"]["gate"]
        )
        atomic_write_json(args.artifact_dir / "serialization_robustness.json", robustness)
        summary = {
            "format": "serialization_robustness_summary_6e_v1",
            "status": "passed" if robustness["gate_passed"] else "failed_serialization_gate",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "audit_pair_count": int(audit["pair_count"]),
            "template_row_count": len(output),
            "newly_scored_forwards": newly_scored,
            "new_over_context_rows": newly_over_context,
            "resumed_rows": len(preflight_rows) - newly_scored - newly_over_context,
            "runtime_seconds": time.perf_counter() - started,
            "robustness": robustness,
            "hashes": {
                "teacher_cache": sha256_file(args.artifact_dir / "serialization_teacher_cache.jsonl"),
                "journal": sha256_file(journal_path),
                "preflight": sha256_file(args.artifact_dir / "serialization_preflight.jsonl"),
            },
        }
        atomic_write_json(args.artifact_dir / "serialization_summary.json", summary)
        atomic_write_text(args.artifact_dir / "serialization_robustness_report.md", _report(summary))
        attempt.progress(
            status=summary["status"],
            latest_validated_checkpoint=str(args.artifact_dir / "serialization_summary.json"),
            gate_passed=robustness["gate_passed"],
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
