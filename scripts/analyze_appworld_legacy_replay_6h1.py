from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import (
    paired_environment_comparison,
    sentinel_failure_diagnostics,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _report(summary: dict[str, Any]) -> str:
    replay = summary["legacy_replay"]
    old = summary["paired_comparison"]["appworld_0_2_dev0"]
    new = summary["paired_comparison"]["appworld_0_1_0"]
    lines = [
        "# EXP-024R Exact AppWorld 0.1.0 Replay Validation",
        "",
        f"- Run UUID: `{summary['run_uuid']}`",
        f"- Decision branch: `{summary['decision_branch']}`",
        f"- Legacy package/code/data/evaluation: `{summary['version_triple']}`",
        f"- Sentinel states passed: {summary['sentinel']['complete_replay_pass_count']}/{summary['sentinel']['state_count']}",
        f"- Full 45-state replay: {summary['full_replay_status']}",
        f"- Evaluated legacy states passed: {replay['complete_replay_pass_count']}/{replay['state_count']}",
        f"- Prior observations matched: {new['history_observation_match_count']}/{summary['paired_comparison']['history_observation_count']}",
        f"- Target observations matched: {new['target_observation_match_count']}/{replay['state_count']}",
        "",
        "## Paired comparison",
        "",
        "| Environment | Complete | Histories | Prior observations | Targets |",
        "|---|---:|---:|---:|---:|",
        f"| 0.2.0.dev0 | {old['complete_replay_pass_count']} | {old['complete_history_match_count']} | {old['history_observation_match_count']} | {old['target_observation_match_count']} |",
        f"| 0.1.0 | {new['complete_replay_pass_count']} | {new['complete_history_match_count']} | {new['history_observation_match_count']} | {new['target_observation_match_count']} |",
        "",
        f"Version mismatch causally confirmed: {summary['version_mismatch_causally_confirmed']}.",
        f"EXP-024A generation remains blocked: {summary['exp024a_generation_remains_blocked']}.",
        f"Normalized sentinel differences: {summary['sentinel_diagnostics']['normalized_mismatch_categories']}.",
        f"Initial identity failures: {summary['sentinel_diagnostics']['identity_failure_count']}.",
        "",
        "No Qwen model was imported or run, no memory condition was executed, and no AppWorld candidate action was generated.",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_legacy_replay_6h1.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h1"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    parent = Path(settings["parent_exp024a"])
    paths = {
        "environment": args.artifact_dir / "environment_provenance.json",
        "sentinel": args.artifact_dir / "replay" / "sentinel_summary_v2.json",
        "contracts": args.artifact_dir / "replay_contract_manifest.json",
        "old_replay": parent / "replay" / "replay_summary.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Finalization input missing: {name}={path}")
    replay_path = args.artifact_dir / "replay" / "replay_summary_v2.json"
    selected_summary_path = replay_path if replay_path.exists() else paths["sentinel"]
    if Path(args.resume_checkpoint).resolve() != selected_summary_path.resolve():
        raise ValueError("Finalization resume checkpoint is not the latest replay summary")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    data_hashes["selected_replay"] = sha256_file(selected_summary_path)

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="legacy_replay_analysis",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        environment = _load_json(paths["environment"])
        sentinel = _load_json(paths["sentinel"])
        replay = _load_json(selected_summary_path)
        state_paths = sorted((args.artifact_dir / "replay" / "states_v2").glob("*.json"))
        legacy_rows = [_load_json(path) for path in state_paths]
        selected_ids = {str(row["state_example_id"]) for row in legacy_rows}
        old_paths = sorted((parent / "replay" / "states").glob("*.json"))
        old_rows = [
            row
            for row in (_load_json(path) for path in old_paths)
            if str(row["state_example_id"]) in selected_ids
        ]
        paired = paired_environment_comparison(old_rows, legacy_rows)
        paired["history_observation_count"] = sum(
            int(row["history_step_count"]) for row in legacy_rows
        )
        branch = str(replay["decision"]["decision_branch"])
        full_pass = branch == "appworld_010_replay_validated" and int(
            replay["complete_replay_pass_count"]
        ) == int(settings["expected"]["state_count"])
        source_versions = environment["source_versions"]["versions"]
        version_triple = "/".join(
            [
                str(environment["probe"]["appworld_version"]),
                str(environment["probe"]["db_version"]),
                str(source_versions["evaluation"][0]),
            ]
        )
        diagnostics = sentinel_failure_diagnostics(legacy_rows)
        old_full_summary = _load_json(paths["old_replay"])
        summary = {
            "format": "appworld_legacy_replay_final_summary_6h1_v1",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "decision_branch": branch,
            "version_triple": version_triple,
            "environment": environment,
            "sentinel": sentinel,
            "legacy_replay": replay,
            "paired_comparison": paired,
            "old_appworld_0_2_dev0_full_45_reference": old_full_summary,
            "sentinel_diagnostics": diagnostics,
            "full_replay_status": (
                "completed" if replay_path.exists() else "not_run_blocked_by_sentinel"
            ),
            "version_mismatch_causally_confirmed": full_pass,
            "exp024a_generation_remains_blocked": not full_pass,
            "recommended_next_milestone": (
                "separately_reviewed_exp024a_generation_under_appworld_0_1_0"
                if full_pass
                else "resolve_historical_auth_token_timing_and_source_identity_before_generation"
            ),
            "qwen_import_count": 0,
            "qwen_forward_count": 0,
            "qwen_generation_count": 0,
            "memory_condition_execution_count": 0,
            "superseded_identity_validation": {
                "attempt_id": "exp024r-sentinel-001",
                "reason": "v1 compared the full current-task query to world.task.instruction",
                "preserved_summary": "replay/sentinel_summary.json",
            },
            "analysis_elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "final_exp024r_summary.json", summary)
        atomic_write_text(args.artifact_dir / "final_exp024r_report.md", _report(summary))
        atomic_write_json(args.artifact_dir / "paired_0_2_vs_0_1_comparison.json", paired)
        atomic_write_text(
            args.artifact_dir / "environment_provenance_report.md",
            "\n".join(
                [
                    "# AppWorld 0.1.0 Environment Provenance",
                    "",
                    f"- Python: `{environment['legacy_python']}`",
                    f"- CLI: `{environment['legacy_cli']}`",
                    f"- APPWORLD_ROOT: `{environment['legacy_root']}`",
                    f"- Wheel SHA256: `{environment['wheel']['sha256']}`",
                    f"- Dependency wheel manifest: `{environment['wheel_manifest']['manifest_sha256']}`",
                    f"- Root manifest: `{environment['root_manifest']['manifest_sha256']}`",
                    f"- Version triple: `{version_triple}`",
                ]
            )
            + "\n",
        )
        atomic_write_text(
            args.artifact_dir / "sentinel_replay_report.md",
            f"# Sentinel Replay\n\nDecision: `{sentinel['decision']['decision_branch']}`. "
            f"Passed {sentinel['complete_replay_pass_count']}/{sentinel['state_count']} states.\n",
        )
        atomic_write_text(
            args.artifact_dir / "legacy_45_state_replay_report.md",
            "# Immutable 45-State Replay\n\n"
            + (
                f"Decision: `{branch}`. Passed "
                f"{replay['complete_replay_pass_count']}/{replay['state_count']} states.\n"
                if replay_path.exists()
                else f"Not run because sentinel decision `{branch}` blocked the full replay.\n"
            ),
        )
        attempt.progress(latest_validated_checkpoint="final_exp024r_summary.json")
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
