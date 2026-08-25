from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json


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
    parser.add_argument("--tmux-session", default="exp030a_reader")
    return parser.parse_args()


def _common(args: argparse.Namespace, attempt_id: str, parent_id: str) -> list[str]:
    return [
        "--config",
        str(args.config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--attempt-id",
        attempt_id,
        "--parent-attempt-id",
        parent_id,
        "--resume-checkpoint",
        "none",
        "--local-head",
        args.head,
        "--github-head",
        args.head,
        "--lambda-head",
        args.head,
        "--tmux-session",
        args.tmux_session,
    ]


def _run(
    *,
    args: argparse.Namespace,
    name: str,
    script: str,
    attempt_id: str,
    parent_id: str,
    extra: list[str] | None = None,
) -> None:
    command = [sys.executable, script, *_common(args, attempt_id, parent_id)]
    if extra:
        command.extend(extra)
    state_path = args.artifact_dir / "orchestration_state.json"
    atomic_write_json(
        state_path,
        {
            "format": "cross_attention_field_orchestration_8b_v1",
            "status": "running",
            "phase": name,
            "attempt_id": attempt_id,
            "parent_attempt_id": parent_id,
            "command": command,
            "updated_unix": time.time(),
        },
    )
    print(json.dumps({"phase": name, "status": "starting"}, sort_keys=True), flush=True)
    subprocess.run(command, check=True)
    atomic_write_json(
        state_path,
        {
            "format": "cross_attention_field_orchestration_8b_v1",
            "status": "phase_complete",
            "phase": name,
            "attempt_id": attempt_id,
            "parent_attempt_id": parent_id,
            "updated_unix": time.time(),
        },
    )
    print(json.dumps({"phase": name, "status": "complete"}, sort_keys=True), flush=True)


def main() -> None:
    args = _parse_args()
    persistent = Path("/lambda/nfs/rcmf-persist")
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.attempt_prefix
    phases: list[tuple[str, str, list[str]]] = [
        ("runtime_preflight", "scripts/prepare_cross_attention_field_8b.py", []),
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
    parent = "none"
    for index, (name, script, extra) in enumerate(phases, start=1):
        attempt = f"{prefix}-{index:02d}-{name}"
        _run(
            args=args,
            name=name,
            script=script,
            attempt_id=attempt,
            parent_id=parent,
            extra=extra,
        )
        parent = attempt
        if name == "runtime_preflight":
            preflight = json.loads(
                (args.artifact_dir / "runtime_preflight.json").read_text(encoding="utf-8")
            )
            if not bool(preflight["automatic_launch_allowed"]):
                raise RuntimeError("EXP-030A runtime exceeds the authorized 18 H100 hours")

    manifest_command = [
        sys.executable,
        "scripts/run_cross_attention_reader_live_8b.py",
        "--config",
        str(args.config),
        "--replay-config",
        str(args.replay_config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--phase",
        "manifest",
        "--attempt-id",
        f"{prefix}-08-live-manifest",
        "--parent-attempt-id",
        parent,
        "--local-head",
        args.head,
        "--github-head",
        args.head,
        "--lambda-head",
        args.head,
        "--tmux-session",
        args.tmux_session,
    ]
    subprocess.run(manifest_command, check=True)
    live_attempt = f"{prefix}-09-heldout-live"
    command = [
        sys.executable,
        "scripts/run_cross_attention_reader_live_8b.py",
        "--replay-config",
        str(args.replay_config),
        *_common(args, live_attempt, parent),
        "--phase",
        "validate",
    ]
    subprocess.run(command, check=True)
    select_command = [
        sys.executable,
        "scripts/run_cross_attention_reader_live_8b.py",
        "--config",
        str(args.config),
        "--replay-config",
        str(args.replay_config),
        "--artifact-dir",
        str(args.artifact_dir),
        "--phase",
        "select",
        "--attempt-id",
        f"{prefix}-10-select",
        "--parent-attempt-id",
        live_attempt,
        "--local-head",
        args.head,
        "--github-head",
        args.head,
        "--lambda-head",
        args.head,
        "--tmux-session",
        args.tmux_session,
    ]
    subprocess.run(select_command, check=True)
    selection_path = args.artifact_dir / "reader/phase2/heldout_live/checkpoint_selection.json"
    selection: dict[str, Any] = json.loads(selection_path.read_text(encoding="utf-8"))
    final_status = (
        "reader_valid_field_required"
        if bool(selection["run_reversible_field"])
        else "reader_failed_stop_before_field"
    )
    atomic_write_json(
        args.artifact_dir / "orchestration_state.json",
        {
            "format": "cross_attention_field_orchestration_8b_v1",
            "status": final_status,
            "decision_branch": selection["decision_branch"],
            "selection_path": str(selection_path),
            "updated_unix": time.time(),
        },
    )
    print(json.dumps(selection, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
