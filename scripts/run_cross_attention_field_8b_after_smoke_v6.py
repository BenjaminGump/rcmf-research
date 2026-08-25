from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_cross_attention_field_8b_until_reader_decision import _run


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b_verified.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--attempt-prefix", default="exp030a-reader-a4")
    parser.add_argument(
        "--parent-attempt-id",
        default="exp030a-reader-a3-03-implementation_validation_v2",
    )
    parser.add_argument("--tmux-session", default="exp030a_reader_a4")
    return parser.parse_args()


def _live_base(args: argparse.Namespace) -> list[str]:
    return [
        "--config",
        str(args.config),
        "--replay-config",
        str(args.replay_config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--local-head",
        args.head,
        "--github-head",
        args.head,
        "--lambda-head",
        args.head,
        "--tmux-session",
        args.tmux_session,
    ]


def _measured_runtime_gate(args: argparse.Namespace) -> tuple[str, dict]:
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    preflight_path = args.artifact_dir / "runtime_preflight.json"
    smoke_path = args.artifact_dir / "reader/offload_backward_smoke.json"
    implementation_path = args.artifact_dir / "reader/implementation_validation.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    implementation = json.loads(implementation_path.read_text(encoding="utf-8"))
    if not bool(smoke["passed"]) or not bool(implementation["passed"]):
        raise RuntimeError("Measured backward or implementation gate did not pass")
    backward_count = int(preflight["execution_counts"]["backwards"])
    old_backward_hours = (
        backward_count
        * float(settings["runtime"]["reader_backward_seconds_expected"])
        / 3600.0
    )
    non_backward_hours = (
        float(preflight["expected"]["phase_a_c_h100_hours"])
        - old_backward_hours
    )
    measured_total = (
        float(smoke["projected_backward_h100_hours"]) + non_backward_hours
    )
    threshold = float(settings["runtime"]["review_threshold_h100_hours"])
    report = {
        "format": "cross_attention_reader_measured_runtime_gate_8b_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": int(settings["global_seed"]),
        "smoke_sha256": sha256_file(smoke_path),
        "implementation_sha256": sha256_file(implementation_path),
        "preflight_sha256": sha256_file(preflight_path),
        "measured_mean_backward_seconds": float(smoke["mean_backward_seconds"]),
        "measured_projected_backward_h100_hours": float(
            smoke["projected_backward_h100_hours"]
        ),
        "expected_non_backward_h100_hours": non_backward_hours,
        "measured_expected_phase_a_c_h100_hours": measured_total,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": measured_total <= threshold,
        "scientific_coverage_changed": False,
    }
    if not bool(report["automatic_launch_allowed"]):
        raise RuntimeError("Measured expected runtime exceeds the review threshold")
    attempt_id = f"{args.attempt_prefix}-04-measured-runtime-gate"
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=attempt_id,
        phase="measured_backward_runtime_gate",
        command=[sys.argv[0], *sys.argv[1:]],
        local_head=args.head,
        github_head=args.head,
        lambda_head=args.head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "implementation": sha256_file(implementation_path),
            "preflight": sha256_file(preflight_path),
            "smoke": sha256_file(smoke_path),
        },
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="none",
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        output = args.artifact_dir / "reader/measured_runtime_gate.json"
        atomic_write_json(output, report)
        attempt.progress(
            status="measured_runtime_gate_passed",
            latest_validated_checkpoint=str(output),
            measured_expected_h100_hours=measured_total,
        )
    return attempt_id, report


def main() -> None:
    args = _parse_args()
    if os.name != "nt" and not os.path.ismount("/lambda/nfs/rcmf-persist"):
        raise RuntimeError("Persistent filesystem is not mounted")
    parent, runtime = _measured_runtime_gate(args)
    phases: list[tuple[str, str, list[str]]] = [
        (
            "phase1_utilization_bounded",
            "scripts/run_cross_attention_reader_8b_v6.py",
            ["--phase", "phase1"],
        ),
        (
            "phase1_posttrain_validation",
            "scripts/validate_cross_attention_reader_posttrain_8b_v4.py",
            [],
        ),
        (
            "phase2_specificity_bounded",
            "scripts/run_cross_attention_reader_8b_v6.py",
            ["--phase", "phase2"],
        ),
        (
            "policy_evaluation",
            "scripts/evaluate_cross_attention_reader_policy_8b_v3.py",
            [],
        ),
    ]
    for index, (name, script, extra) in enumerate(phases, start=5):
        attempt = f"{args.attempt_prefix}-{index:02d}-{name}"
        _run(
            args=args,
            name=name,
            script=script,
            attempt_id=attempt,
            parent_id=parent,
            extra=extra,
        )
        parent = attempt
    base = _live_base(args)
    manifest_attempt = f"{args.attempt_prefix}-09-live-manifest"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_cross_attention_reader_live_8b.py",
            *base,
            "--phase",
            "manifest",
            "--attempt-id",
            manifest_attempt,
            "--parent-attempt-id",
            parent,
        ],
        check=True,
    )
    live_attempt = f"{args.attempt_prefix}-10-heldout-live"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_cross_attention_reader_live_8b.py",
            *base,
            "--phase",
            "validate",
            "--attempt-id",
            live_attempt,
            "--parent-attempt-id",
            manifest_attempt,
        ],
        check=True,
    )
    select_attempt = f"{args.attempt_prefix}-11-select"
    subprocess.run(
        [
            sys.executable,
            "scripts/run_cross_attention_reader_live_8b.py",
            *base,
            "--phase",
            "select",
            "--attempt-id",
            select_attempt,
            "--parent-attempt-id",
            live_attempt,
        ],
        check=True,
    )
    selection_path = args.artifact_dir / "reader/phase2/heldout_live/checkpoint_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    atomic_write_json(
        args.artifact_dir / "orchestration_state.json",
        {
            "format": "cross_attention_field_orchestration_8b_v6",
            "status": (
                "reader_valid_field_required"
                if bool(selection["run_reversible_field"])
                else "reader_failed_stop_before_field"
            ),
            "decision_branch": selection["decision_branch"],
            "measured_runtime_gate": runtime,
            "selection_path": str(selection_path),
            "updated_unix": time.time(),
        },
    )
    print(json.dumps(selection, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
