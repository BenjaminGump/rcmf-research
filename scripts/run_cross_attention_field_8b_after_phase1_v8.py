from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import _bootstrap  # noqa: F401

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
    parser.add_argument("--attempt-prefix", default="exp030a-reader-a6")
    parser.add_argument(
        "--parent-attempt-id",
        default="exp030a-reader-a5-05-phase1_utilization_bounded",
    )
    parser.add_argument("--tmux-session", default="exp030a_reader_a6")
    return parser.parse_args()


def _require_phase1(artifact_dir: Path) -> dict:
    selection_path = artifact_dir / "reader/phase1/checkpoint_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    checkpoint = Path(str(selection["selected_checkpoint"]))
    if not checkpoint.exists():
        raise FileNotFoundError("Selected Phase-1 checkpoint is absent")
    if sha256_file(checkpoint) != str(selection["selected_checkpoint_sha256"]):
        raise ValueError("Selected Phase-1 checkpoint hash differs")
    if int(selection["selected_epoch"]) != 1:
        raise ValueError("Deterministic EXP-030A Phase-1 selection changed")
    return selection


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


def main() -> None:
    args = _parse_args()
    if os.name != "nt" and not os.path.ismount("/lambda/nfs/rcmf-persist"):
        raise RuntimeError("Persistent filesystem is not mounted")
    phase1 = _require_phase1(args.artifact_dir)
    phases: list[tuple[str, str, list[str]]] = [
        (
            "phase1_posttrain_validation_bounded",
            "scripts/validate_cross_attention_reader_posttrain_8b_v5.py",
            [],
        ),
        (
            "phase2_specificity_bounded",
            "scripts/run_cross_attention_reader_8b_v7.py",
            ["--phase", "phase2"],
        ),
        (
            "policy_evaluation",
            "scripts/evaluate_cross_attention_reader_policy_8b_v3.py",
            [],
        ),
    ]
    parent = args.parent_attempt_id
    for index, (name, script, extra) in enumerate(phases, start=6):
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
            "format": "cross_attention_field_orchestration_8b_v8",
            "status": (
                "reader_valid_field_required"
                if bool(selection["run_reversible_field"])
                else "reader_failed_stop_before_field"
            ),
            "decision_branch": selection["decision_branch"],
            "phase1_selection": phase1,
            "selection_path": str(selection_path),
            "updated_unix": time.time(),
        },
    )
    print(json.dumps(selection, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
