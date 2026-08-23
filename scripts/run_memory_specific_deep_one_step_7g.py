from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.memory_specific_deep_amortization_7g import GLOBAL_SEED
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_deep_residual_amortized_one_step_7f import (
    _analyze as _analyze_7f,
    _formal,
    _paths,
    _preflight,
)
from scripts.run_state_conditioned_program_fast_one_step_7df import _load_parent_rows


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_memory_specific_deep_amortization_7g.yaml"
        ),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "formal", "analyze"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027b_one_step")
    return parser.parse_args()


def _zero_equivalence(settings: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    paths = _paths(settings, artifact_dir, "pairmlp")
    outputs = [
        _json(path)
        for path in sorted((paths["root"] / "condition_outputs").glob("*.json"))
        if str(_json(path).get("condition_name")) == "P0_zero_program"
    ]
    bare = _load_parent_rows(paths["parent_c0_outputs"], "C0_bare")
    by_state = {str(row["state_example_id"]): row for row in bare}
    rows = []
    for zero in outputs:
        state_id = str(zero["state_example_id"])
        source = by_state[state_id]
        checks = {
            "prompt_hash": str(zero.get("prompt_sha256"))
            == str(source.get("prompt_sha256")),
            "raw_response": str(zero.get("raw_model_response"))
            == str(source.get("raw_model_response")),
            "extracted_code": str(zero.get("extracted_code"))
            == str(source.get("extracted_code")),
            "metrics": dict(zero.get("metrics", {})) == dict(source.get("metrics", {})),
        }
        rows.append({"state_example_id": state_id, "checks": checks, "passed": all(checks.values())})
    report = {
        "format": "memory_specific_zero_program_equivalence_7g_v1",
        "state_count": len(rows),
        "rows": rows,
        "passed": len(rows) == 45 and all(row["passed"] for row in rows),
    }
    atomic_write_json(paths["root"] / "zero_program_equivalence.json", report)
    return report


def _analyze(settings: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    summary = _analyze_7f(kind="pairmlp", settings=settings, artifact_dir=artifact_dir)
    zero = _zero_equivalence(settings, artifact_dir)
    if not zero["passed"]:
        raise RuntimeError("P0 zero program did not reproduce immutable C0")
    classification = str(summary["classification"]["classification"])
    branch = {
        "STRONG_POSITIVE": "memory_specific_deep_amortization_validated",
        "PARTIAL_POSITIVE": "memory_specific_deep_amortization_partial",
        "CLEAR_FAILURE": "memory_specific_deep_amortization_failed",
    }[classification]
    summary.update(
        {
            "format": "memory_specific_deep_one_step_analysis_7g_v1",
            "decision_branch": branch,
            "zero_program_equivalence": zero,
            "next_action": {
                "STRONG_POSITIVE": (
                    "separately review one field-compatible r32 model, then full-bank integration"
                ),
                "PARTIAL_POSITIVE": "stop for review",
                "CLEAR_FAILURE": (
                    "stop generic PairMLP/program work; review only an AppWorld-structured compiler"
                ),
            }[classification],
        }
    )
    paths = _paths(settings, artifact_dir, "pairmlp")
    atomic_write_json(paths["analysis"], summary)
    atomic_write_text(
        paths["root"] / "one_step_report.md",
        "\n".join(
            [
                "# EXP-027B memory-specific deep PairMLP one-step audit",
                "",
                f"- classification: `{classification}`",
                f"- decision branch: `{branch}`",
                f"- positive tasks: `{summary['positive_task_count']}/9`",
                f"- raw-gain retention: `{summary['raw_gain_retention']}`",
                f"- P0/C0 exact equivalence: `{str(zero['passed']).lower()}`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7g"]
    replay = replay_cfg.raw["stage_c_7b"]
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir, "pairmlp")
    hashes = {
        "config": sha256_file(args.config),
        "replay_config": sha256_file(args.replay_config),
        "selector": sha256_file(paths["selector"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"phase_c_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=hashes["config"],
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                kind="pairmlp",
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
            )
        elif args.phase == "formal":
            result = _formal(
                kind="pairmlp",
                settings=settings,
                replay=replay,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            result = _analyze(settings, args.artifact_dir)
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
