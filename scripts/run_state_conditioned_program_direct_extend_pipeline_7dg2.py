from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_direct_extend_7dg2.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--attempt-suffix", default="001")
    parser.add_argument("--parent-attempt-id", default="exp025dg2-preflight-001")
    parser.add_argument("--tmux-session", default="exp025dg2")
    return parser.parse_args()


def _run(command: list[str]) -> None:
    print("launching:", " ".join(command), flush=True)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"EXP-025D-G2 pipeline command failed ({result.returncode}): {command}"
        )


def _common(args: argparse.Namespace) -> list[str]:
    return [
        "--config",
        str(args.config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--local-head",
        args.local_head,
        "--github-head",
        args.github_head,
        "--lambda-head",
        args.lambda_head,
        "--tmux-session",
        args.tmux_session,
    ]


def main() -> None:
    args = _parse_args()
    suffix = str(args.attempt_suffix)
    train_attempt = f"exp025dg2-train-{suffix}"
    _run(
        [
            sys.executable,
            "scripts/run_state_conditioned_program_direct_extend_7dg2.py",
            *_common(args),
            "--phase",
            "train",
            "--attempt-id",
            train_attempt,
            "--parent-attempt-id",
            args.parent_attempt_id,
            "--resume-checkpoint",
            str(
                args.artifact_dir
                / "factorized/latest_checkpoint.json"
                if (args.artifact_dir / "factorized/latest_checkpoint.json").exists()
                else args.artifact_dir / "preflight_summary.json"
            ),
        ]
    )
    teacher_path = args.artifact_dir / "teacher_forced_summary.json"
    teacher = json.loads(teacher_path.read_text(encoding="utf-8"))
    parent_attempt = train_attempt
    if bool(teacher["passed"]):
        for phase in ("preflight", "formal", "analyze"):
            attempt = f"exp025dg2-one-step-{phase}-{suffix}"
            resume = {
                "preflight": teacher_path,
                "formal": args.artifact_dir / "one_step/preflight.json",
                "analyze": args.artifact_dir / "one_step/generation_summary.json",
            }[phase]
            _run(
                [
                    sys.executable,
                    "scripts/run_state_conditioned_program_direct_one_step_7dg.py",
                    *_common(args),
                    "--phase",
                    phase,
                    "--attempt-id",
                    attempt,
                    "--parent-attempt-id",
                    parent_attempt,
                    "--resume-checkpoint",
                    str(resume),
                ]
            )
            parent_attempt = attempt
    else:
        (args.artifact_dir / "one_step").mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            args.artifact_dir / "one_step/not_run.json",
            {
                "format": "compiled_program_one_step_not_run_7dg2_v1",
                "reason": "factorized_teacher_forced_gate_failed",
                "decision_branch": "converged_r16_factorization_failed",
                "teacher_forced_summary": str(teacher_path),
            },
        )
    _run(
        [
            sys.executable,
            "scripts/finalize_state_conditioned_program_direct_extend_7dg2.py",
            "--config",
            str(args.config),
            "--artifact-dir",
            str(args.artifact_dir),
            "--attempt-id",
            f"exp025dg2-finalize-{suffix}",
            "--local-head",
            args.local_head,
            "--github-head",
            args.github_head,
            "--lambda-head",
            args.lambda_head,
            "--parent-attempt-id",
            parent_attempt,
            "--tmux-session",
            args.tmux_session,
        ]
    )


if __name__ == "__main__":
    main()
