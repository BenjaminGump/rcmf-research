from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


RUN_UUID = "state_conditioned_program_direct_extend_7dg2_20260821_001"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--tmux-session", default="exp025dg2")
    return parser.parse_args()


def _timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _runtime(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    starts = {
        str(row["attempt_id"]): row
        for row in attempts
        if str(row.get("event")) == "start"
    }
    ends = {
        str(row["attempt_id"]): row
        for row in attempts
        if str(row.get("event")) == "end"
    }
    gpu_attempts = [
        attempt_id
        for attempt_id, row in starts.items()
        if attempt_id in ends
        and str(row.get("phase", "")).startswith(
            ("factorized_program_extension_train", "compiled_program_direct_one_step")
        )
        and not str(row.get("phase", "")).endswith(("preflight", "analyze"))
    ]
    seconds = sum(
        (
            _timestamp(str(ends[attempt_id]["end_timestamp_utc"]))
            - _timestamp(str(starts[attempt_id]["start_timestamp_utc"]))
        ).total_seconds()
        for attempt_id in gpu_attempts
    )
    return {
        "attempt_count_before_finalizer": len(starts),
        "closed_attempt_count_before_finalizer": len(ends),
        "gpu_attempt_ids": sorted(gpu_attempts),
        "gpu_attempt_seconds": seconds,
        "gpu_attempt_h100_hours": seconds / 3600.0,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7dg2"]
    teacher = _json(args.artifact_dir / "teacher_forced_summary.json")
    one_step_path = args.artifact_dir / "one_step/analysis.json"
    one_step = _json(one_step_path) if one_step_path.exists() else None
    if not bool(teacher["passed"]):
        branch = "converged_r16_factorization_failed"
    elif one_step is None:
        raise ValueError("Passed teacher-forced gate has no one-step analysis")
    else:
        branch = str(one_step["decision_branch"])
    attempts = [dict(row) for row in read_jsonl(args.artifact_dir / "attempts.jsonl")]
    runtime = _runtime(attempts)
    summary = {
        "format": "state_conditioned_program_direct_extension_final_7dg2_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "source_commit": args.lambda_head,
        "parent_checkpoint_sha256": str(
            settings["expected_parent_checkpoint_sha256"]
        ),
        "resume_integrity": _json(args.artifact_dir / "resume_integrity.json"),
        "calibration_audit": _json(args.artifact_dir / "calibration_audit_u16.json"),
        "runtime_preflight": _json(args.artifact_dir / "runtime_preflight.json"),
        "teacher_forced": teacher,
        "one_step": one_step,
        "runtime": runtime,
        "decision_branch": branch,
        "compiled_program_validated": branch
        == "compiled_transition_program_r16_validated",
        "full_bank_integration_started": False,
        "v4_tag_created_or_moved": False,
    }
    output = args.artifact_dir / "final_exp025dg2_summary.json"
    atomic_write_json(output, summary)
    atomic_write_text(
        args.artifact_dir / "final_exp025dg2_report.md",
        "\n".join(
            [
                "# EXP-025D-G2 Final Scientific Record",
                "",
                f"Run UUID: `{RUN_UUID}`  ",
                f"Global seed: `25101`  ",
                f"Selected checkpoint: `u{teacher['training']['selected_updates_per_pair']}`  ",
                f"Selected gamma: `{teacher['selection']['selected_gamma']}`  ",
                f"Teacher-forced gate: `{teacher['passed']}`  ",
                f"Decision: `{branch}`",
                "",
            ]
        ),
    )
    data_hashes = {
        "teacher_forced": sha256_file(
            args.artifact_dir / "teacher_forced_summary.json"
        ),
        "resume_integrity": sha256_file(args.artifact_dir / "resume_integrity.json"),
        "runtime_preflight": sha256_file(args.artifact_dir / "runtime_preflight.json"),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="factorized_program_extension_finalization",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(args.artifact_dir / "teacher_forced_summary.json"),
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        attempt.progress(
            status="scientific_record_finalized",
            latest_validated_checkpoint=str(output),
            decision_branch=branch,
        )
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
