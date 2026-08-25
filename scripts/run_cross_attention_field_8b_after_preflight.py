from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json
from scripts.run_cross_attention_field_8b_until_reader_decision import _common, _run


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b_runtime.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--attempt-prefix", default="exp030a-reader-a1")
    parser.add_argument("--preflight-attempt-id", required=True)
    parser.add_argument("--tmux-session", default="exp030a_reader")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if os.name != "nt" and not os.path.ismount("/lambda/nfs/rcmf-persist"):
        raise RuntimeError("Persistent filesystem is not mounted")
    preflight_path = args.artifact_dir / "runtime_preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("EXP-030A runtime exceeds the authorized 18 H100 hours")
    phases: list[tuple[str, str, list[str]]] = [
        ("memory_slot_cache", "scripts/cache_cross_attention_memory_8b.py", []),
        (
            "implementation_validation",
            "scripts/run_cross_attention_reader_8b.py",
            ["--phase", "implementation"],
        ),
        (
            "phase1_utilization",
            "scripts/run_cross_attention_reader_8b.py",
            ["--phase", "phase1"],
        ),
        (
            "phase1_posttrain_validation",
            "scripts/validate_cross_attention_reader_posttrain_8b.py",
            [],
        ),
        (
            "phase2_specificity",
            "scripts/run_cross_attention_reader_8b.py",
            ["--phase", "phase2"],
        ),
        (
            "policy_evaluation",
            "scripts/evaluate_cross_attention_reader_policy_8b.py",
            [],
        ),
    ]
    parent = args.preflight_attempt_id
    for index, (name, script, extra) in enumerate(phases, start=2):
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
    base = [
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
    subprocess.run(
        [
            sys.executable,
            "scripts/run_cross_attention_reader_live_8b.py",
            *base,
            "--phase",
            "manifest",
            "--attempt-id",
            f"{args.attempt_prefix}-08-live-manifest",
            "--parent-attempt-id",
            parent,
        ],
        check=True,
    )
    live_attempt = f"{args.attempt_prefix}-09-heldout-live"
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
            parent,
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/run_cross_attention_reader_live_8b.py",
            *base,
            "--phase",
            "select",
            "--attempt-id",
            f"{args.attempt_prefix}-10-select",
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
            "format": "cross_attention_field_orchestration_8b_v1",
            "status": (
                "reader_valid_field_required"
                if bool(selection["run_reversible_field"])
                else "reader_failed_stop_before_field"
            ),
            "decision_branch": selection["decision_branch"],
            "selection_path": str(selection_path),
            "updated_unix": time.time(),
        },
    )
    print(json.dumps(selection, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
