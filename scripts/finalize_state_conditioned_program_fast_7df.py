from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    sha256_file,
)


RUN_UUID = "state_conditioned_program_fast_7df_20260819_001"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def decision_branch(
    latent: Mapping[str, Any], program: Mapping[str, Any]
) -> str:
    if not bool(latent.get("passed")):
        return "pair_behavioral_targets_nonidentifiable"
    gate = program.get("latent_gate", {})
    if not bool(gate.get("pair_mlp", {}).get("passed")):
        return "state_transition_representations_insufficient"
    if not bool(gate.get("primary", {}).get("passed")):
        primary = program["architectures"][
            "full_factorized_r16_observation_excluded"
        ]["validation"]
        outcome = program["architectures"][
            "full_factorized_r16_action_plus_outcome"
        ]["validation"]
        if (
            float(outcome["mean_cosine"]) >= 0.40
            and float(outcome["mse_reduction_vs_zero"]) >= 0.20
            and float(outcome["mse"]) < float(primary["mse"])
        ):
            return "current_program_requires_post_action_outcome"
        return "factorized_program_insufficient"
    return "tensor_program_gate_passed_requires_teacher_forced_validation"


def _runtime(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    starts = {
        row["attempt_id"]: row for row in attempts if row.get("event") == "start"
    }
    ends = {
        row["attempt_id"]: row for row in attempts if row.get("event") == "end"
    }
    gpu_ids = sorted(value for value in starts if value.startswith("exp025df-gpu-"))
    seconds = sum(
        (
            _timestamp(ends[value]["end_timestamp_utc"])
            - _timestamp(starts[value]["start_timestamp_utc"])
        ).total_seconds()
        for value in gpu_ids
    )
    first = min(_timestamp(starts[value]["start_timestamp_utc"]) for value in gpu_ids)
    last = max(_timestamp(ends[value]["end_timestamp_utc"]) for value in gpu_ids)
    return {
        "gpu_attempt_ids": gpu_ids,
        "gpu_attempt_seconds": seconds,
        "gpu_attempt_h100_hours": seconds / 3600.0,
        "gpu_wall_span_seconds": (last - first).total_seconds(),
        "gpu_wall_span_hours": (last - first).total_seconds() / 3600.0,
    }


def _markdown(summary: Mapping[str, Any]) -> str:
    pair_mlp = summary["program"]["architectures"][
        "pair_mlp_observation_excluded"
    ]["validation"]
    primary = summary["program"]["architectures"][
        "full_factorized_r16_observation_excluded"
    ]["validation"]
    return "\n".join(
        [
            "# EXP-025D-Fast Final Scientific Record",
            "",
            f"Run UUID: `{summary['run_uuid']}`  ",
            f"Decision: `{summary['decision_branch']}`  ",
            f"Source commit: `{summary['source_commit']}`",
            "",
            "The clean decoder and canonical pair-target gates passed, including",
            f"decoded-effect cosine `{summary['pair_targets']['stability']['decoded_delta_cosine_mean']:.6f}` ",
            f"and repeat utility Spearman `{summary['pair_targets']['stability']['repeat_utility_spearman']:.6f}`.",
            "",
            "The amortized representation gate failed. PairMLP heldout cosine was",
            f"`{pair_mlp['mean_cosine']:.6f}` and MSE reduction versus zero was",
            f"`{pair_mlp['mse_reduction_vs_zero']:.6f}`. The observation-excluded",
            f"factorized model had cosine `{primary['mean_cosine']:.6f}` and MSE",
            f"reduction `{primary['mse_reduction_vs_zero']:.6f}`.",
            "",
            "Per the preregistered stop rule, B/C/D/E Qwen validation and the",
            "one-step AppWorld audit were not run. No full bank, program compiler,",
            "injector, selector, Stage C2, end-to-end RCMF, or V4 tag was started.",
            "",
        ]
    )


def _build_summary(root: Path) -> dict[str, Any]:
    preflight = _load(root / "preflight_summary.json")
    prefix = _load(root / "prefix_cache_equivalence.json")
    decoder = _load(root / "decoder/summary.json")
    latent = _load(root / "pair_latents/summary.json")
    program = _load(root / "program/summary.json")
    primary_ratio = _load(root / "pair_latents/primary/final_ratio_projection.json")
    repeat_ratio = _load(root / "pair_latents/repeat_seed/final_ratio_projection.json")
    attempts = [
        json.loads(line)
        for line in (root / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    branch = decision_branch(latent, program)
    return {
        "format": "state_conditioned_program_fast_final_summary_7df_v1",
        "run_uuid": RUN_UUID,
        "source_commit": maybe_git_commit(),
        "decision_branch": branch,
        "compiled_program_validated": False,
        "pair_counts": preflight["context_preflight"],
        "pair_coverage": preflight["pair_coverage"],
        "field_validation": preflight["field_validation"],
        "prefix_cache": prefix,
        "decoder": decoder,
        "pair_targets": {
            **latent,
            "primary_final_ratio_projection": primary_ratio,
            "repeat_final_ratio_projection": repeat_ratio,
        },
        "program": program,
        "teacher_forced": {
            "status": "not_run_pair_mlp_tensor_gate_failed",
            "cells": {cell: None for cell in "BCDE"},
            "qwen_forward_count": 0,
        },
        "one_step": {
            "status": "not_run_teacher_forced_not_unlocked",
            "condition_count": 0,
            "qwen_generation_count": 0,
            "appworld_execution_count": 0,
        },
        "selector_ensemble_sha256": preflight["immutable_validation"][
            "selector_ensemble_sha256"
        ],
        "runtime": _runtime(attempts),
        "artifact_size_bytes_before_finalization": sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()
        ),
        "hard_scope": {
            "qwen_frozen": True,
            "selector_unchanged": True,
            "student_prompt_raw_transition_count": 0,
            "full_bank_trained": False,
            "appworld_generation_run": False,
            "v4_tag_created": False,
        },
    }


def _validation(root: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    pair_mlp = summary["program"]["latent_gate"]["pair_mlp"]
    checks = {
        "run_uuid": summary["run_uuid"] == RUN_UUID,
        "logical_pairs_232": summary["pair_counts"]["logical_pair_count"] == 232,
        "unique_pairs_224": summary["pair_counts"]["unique_pair_count"] == 224,
        "no_over_context_or_truncation": (
            summary["pair_counts"]["over_context_pair_count"] == 0
            and summary["pair_counts"]["truncation_count"] == 0
        ),
        "incremental_field_passed": summary["field_validation"]["passed"],
        "decoder_passed": summary["decoder"]["passed"],
        "pair_targets_passed": summary["pair_targets"]["passed"],
        "primary_ratio_lte_1": (
            summary["pair_targets"]["primary_final_ratio_projection"]["max_ratio"]
            <= 1.0
        ),
        "repeat_ratio_lte_1": (
            summary["pair_targets"]["repeat_final_ratio_projection"]["max_ratio"]
            <= 1.0
        ),
        "observation_invariance": all(
            summary["program"]["observation_invariance"].values()
        ),
        "pair_mlp_gate_failed": not pair_mlp["passed"],
        "decision_branch": (
            summary["decision_branch"]
            == "state_transition_representations_insufficient"
        ),
        "teacher_forced_not_run": summary["teacher_forced"]["qwen_forward_count"] == 0,
        "one_step_not_run": summary["one_step"]["qwen_generation_count"] == 0,
    }
    return {
        "format": "state_conditioned_program_fast_postrun_validation_7df_v1",
        "checks": checks,
        "error_count": sum(not bool(value) for value in checks.values()),
        "passed": all(checks.values()),
        "summary_sha256": sha256_file(root / "final_exp025df_summary.json"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--tmux-session", default="none")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = _load(args.artifact_dir / "run_manifest.json")
    command = [str(value) for value in __import__("sys").argv]
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="cpu_scientific_finalization",
        command=command,
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=manifest["config_sha256"],
        data_manifest_hashes=manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(args.artifact_dir / "program/summary.json"),
        scientific_parameter_changed=False,
    ) as attempt:
        summary = _build_summary(args.artifact_dir)
        atomic_write_json(args.artifact_dir / "final_exp025df_summary.json", summary)
        atomic_write_text(
            args.artifact_dir / "final_exp025df_report.md", _markdown(summary)
        )
        attempt.progress(
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp025df_summary.json"
            ),
            decision_branch=summary["decision_branch"],
        )
    validation = _validation(args.artifact_dir, summary)
    atomic_write_json(args.artifact_dir / "postrun_validation.json", validation)
    if not validation["passed"]:
        raise RuntimeError(f"EXP-025D-Fast final validation failed: {validation}")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
