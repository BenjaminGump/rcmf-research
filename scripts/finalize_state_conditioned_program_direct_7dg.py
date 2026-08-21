from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
)


RUN_UUID = "state_conditioned_program_direct_7dg_20260821_001"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def decision_branch(
    direct: Mapping[str, Any], one_step: Mapping[str, Any] | None
) -> str:
    branch = str(direct["decision_branch"])
    if branch != "factorized_teacher_forced_passed_one_step_pending":
        return branch
    if one_step is None:
        raise ValueError("A passed factorized gate requires completed one-step analysis")
    return str(one_step["decision_branch"])


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
    gpu_attempts = sorted(
        attempt_id
        for attempt_id, row in starts.items()
        if str(row.get("phase", "")).startswith(
            ("direct_pairmlp", "compiled_program_direct_one_step")
        )
        and attempt_id in ends
        and not str(row.get("phase", "")).endswith(("preflight", "analyze"))
    )
    seconds = sum(
        (
            _timestamp(str(ends[attempt_id]["end_timestamp_utc"]))
            - _timestamp(str(starts[attempt_id]["start_timestamp_utc"]))
        ).total_seconds()
        for attempt_id in gpu_attempts
    )
    nonfinalizer_starts = {
        attempt_id
        for attempt_id, row in starts.items()
        if str(row.get("phase")) != "cpu_scientific_finalization"
    }
    all_closed = nonfinalizer_starts <= set(ends)
    wall_seconds = 0.0
    if gpu_attempts:
        wall_seconds = (
            max(
                _timestamp(str(ends[attempt_id]["end_timestamp_utc"]))
                for attempt_id in gpu_attempts
            )
            - min(
                _timestamp(str(starts[attempt_id]["start_timestamp_utc"]))
                for attempt_id in gpu_attempts
            )
        ).total_seconds()
    return {
        "gpu_attempt_ids": gpu_attempts,
        "gpu_attempt_seconds": seconds,
        "gpu_attempt_h100_hours": seconds / 3600.0,
        "gpu_wall_span_seconds": wall_seconds,
        "gpu_wall_span_hours": wall_seconds / 3600.0,
        "attempt_count": len(starts),
        "all_attempts_closed_before_finalizer": all_closed,
    }


def _effective_config_sha256(root: Path, manifest: Mapping[str, Any]) -> str:
    path = root / "run_manifest_supersessions.jsonl"
    if not path.exists():
        return str(manifest["config_sha256"])
    rows = list(read_jsonl(path))
    return str(rows[-1]["replacement_config_sha256"])


def _markdown(summary: Mapping[str, Any]) -> str:
    pair = summary["pairmlp"]
    factor = summary.get("factorized")
    lines = [
        "# EXP-025D-Direct Final Scientific Record",
        "",
        f"Run UUID: `{summary['run_uuid']}`  ",
        f"Global seed: `{summary['global_seed']}`  ",
        f"Source commit: `{summary['source_commit']}`  ",
        f"Decision: `{summary['decision_branch']}`",
        "",
        "The immutable clean selector, frozen Qwen3-8B, K=4 last-user injection,",
        "observation-excluded program boundary, canonical prompt, and all three",
        "demonstrations were preserved.",
        "",
        "## Exact data and runtime",
        "",
        f"A/B/C/D/E scoreable pairs are `{summary['cell_pair_counts']}`. The",
        f"task-grouped A split contains `{summary['a_split']['train_pair_count']}`",
        f"training and `{summary['a_split']['validation_pair_count']}` validation pairs.",
        f"Teacher rows reused/new are `{summary['teacher_cache']['reusable_top64_rows']}`/",
        f"`{summary['teacher_cache']['new_top64_rows']}`. Measured GPU-attempt time is",
        f"`{summary['runtime']['gpu_attempt_h100_hours']:.6f}` H100 hours.",
        "",
        "## Direct PairMLP",
        "",
        f"Selected updates per pair: `{pair['training']['selected_updates_per_pair']}`. ",
        f"Gate passed: `{pair['passed']}`.",
    ]
    if factor is not None:
        lines.extend(
            [
                "",
                "## Direct factorized program",
                "",
                f"Selected updates per pair: `{factor['training']['selected_updates_per_pair']}`. ",
                f"Teacher-forced gate passed: `{factor['passed']}`.",
            ]
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"Reached branch: `{summary['decision_branch']}`.",
            f"Compiled program behavior passed: `{summary['compiled_program_behavior_passed']}`.",
            "No full bank, selector retraining, program compiler, injector training,",
            "Stage C2, end-to-end RCMF, full AppWorld evaluation, or V4 tag was started.",
            "",
            f"Artifact root: `{summary['artifact_root']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _build_summary(root: Path) -> dict[str, Any]:
    preflight = _json(root / "preflight_summary.json")
    direct = _json(root / "direct_behavior_summary.json")
    teacher = _json(root / "teacher_cache/summary.json")
    one_step_path = root / "one_step/analysis.json"
    one_step = _json(one_step_path) if one_step_path.exists() else None
    attempts = list(read_jsonl(root / "attempts.jsonl"))
    branch = decision_branch(direct, one_step)
    return {
        "format": "state_conditioned_program_direct_final_summary_7dg_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "source_commit": str(preflight["source_commit"]),
        "finalizer_commit": maybe_git_commit(),
        "decision_branch": branch,
        "compiled_program_behavior_passed": branch
        == "compiled_transition_program_direct_pilot_passed",
        "cell_pair_counts": preflight["cell_pair_counts"],
        "a_split": preflight["a_split"],
        "teacher_cache": {
            **preflight["teacher_cache"],
            "completed_summary": teacher,
        },
        "quick_failure_audit": preflight["quick_failure_audit"],
        "runtime_projection": preflight["runtime_projection"],
        "pairmlp": direct["pairmlp"],
        "factorized": direct.get("factorized"),
        "one_step": one_step,
        "runtime": _runtime(attempts),
        "artifact_root": str(root),
        "artifact_size_bytes_before_finalization": sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()
        ),
        "selector_ensemble_sha256": preflight["immutable_validation"][
            "selector_sha256"
        ],
        "clean_decoder_sha256": preflight["immutable_validation"][
            "clean_decoder_state_sha256"
        ],
        "hard_scope": {
            "qwen_frozen": True,
            "selector_unchanged": True,
            "global_seed_count": 1,
            "student_prompt_contains_raw_transition": False,
            "full_bank_trained": False,
            "v4_tag_created": False,
        },
    }


def _validation(summary: Mapping[str, Any]) -> dict[str, Any]:
    branch = str(summary["decision_branch"])
    checks = {
        "run_uuid": summary["run_uuid"] == RUN_UUID,
        "global_seed": int(summary["global_seed"]) == 25101,
        "cell_counts": summary["cell_pair_counts"]
        == {"A": 607, "B": 135, "C": 112, "D": 112, "E": 135},
        "a_split_complete": (
            int(summary["a_split"]["train_pair_count"])
            + int(summary["a_split"]["validation_pair_count"])
            == 607
        ),
        "teacher_unique_970": int(summary["teacher_cache"]["unique_scoreable_rows"])
        == 970,
        "pairmlp_present": bool(summary["pairmlp"]),
        "decision_complete": branch
        in {
            "direct_behavior_pair_upper_bound_failed",
            "direct_behavior_factorized_program_failed",
            "compiled_program_not_behaviorally_retained",
            "compiled_transition_program_direct_pilot_passed",
        },
        "one_step_consistency": (
            summary["one_step"] is not None
            if branch
            in {
                "compiled_program_not_behaviorally_retained",
                "compiled_transition_program_direct_pilot_passed",
            }
            else summary["one_step"] is None
        ),
        "selector_hash": summary["selector_ensemble_sha256"]
        == "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f",
    }
    return {
        "format": "state_conditioned_program_direct_postrun_validation_7dg_v1",
        "checks": checks,
        "error_count": sum(not bool(value) for value in checks.values()),
        "passed": all(checks.values()),
    }


def _parse_args() -> argparse.Namespace:
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
    args = _parse_args()
    manifest = _json(args.artifact_dir / "run_manifest.json")
    config_sha256 = _effective_config_sha256(args.artifact_dir, manifest)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="cpu_scientific_finalization",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(args.artifact_dir / "direct_behavior_summary.json"),
        scientific_parameter_changed=False,
    ) as attempt:
        summary = _build_summary(args.artifact_dir)
        atomic_write_json(args.artifact_dir / "final_exp025dg_summary.json", summary)
        atomic_write_text(
            args.artifact_dir / "final_exp025dg_report.md", _markdown(summary)
        )
        attempt.progress(
            status="scientific_finalization_completed",
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp025dg_summary.json"
            ),
            decision_branch=summary["decision_branch"],
        )
    validation = _validation(summary)
    validation["summary_sha256"] = sha256_file(
        args.artifact_dir / "final_exp025dg_summary.json"
    )
    atomic_write_json(args.artifact_dir / "postrun_validation.json", validation)
    if not validation["passed"]:
        raise RuntimeError(f"EXP-025D-Direct validation failed: {validation}")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
