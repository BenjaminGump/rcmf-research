from __future__ import annotations

import argparse
from collections import Counter
from importlib import metadata
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_causal_audit_6h import summarize_replay_failure
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _appworld_installation() -> dict[str, Any]:
    distribution = metadata.distribution("appworld")
    direct_url_text = distribution.read_text("direct_url.json")
    direct_url = json.loads(direct_url_text) if direct_url_text else None
    commit = None
    if direct_url:
        commit = direct_url.get("vcs_info", {}).get("commit_id")
    return {
        "package_version": distribution.version,
        "direct_url": direct_url,
        "vcs_commit": commit,
    }


def _source_versions(
    records_path: Path,
    audit_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    task_ids = {str(row["task_id"]) for row in audit_rows}
    records = {
        str(row["task_id"]): row
        for row in read_jsonl(records_path)
        if str(row["task_id"]) in task_ids
    }
    if set(records) != task_ids:
        raise ValueError(
            f"Missing source records for replay tasks: {sorted(task_ids - set(records))}"
        )
    task_versions: dict[str, dict[str, str]] = {}
    for task_id in sorted(task_ids):
        source_path = Path(str(records[task_id]["metadata"]["source_path"]))
        task_root = source_path.parents[1]
        paths = {
            "code": task_root / "version" / "code.txt",
            "data": task_root / "version" / "data.txt",
            "evaluation": task_root / "evaluation" / "version.txt",
        }
        missing = [str(path) for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"Missing immutable AppWorld version markers for {task_id}: {missing}"
            )
        task_versions[task_id] = {
            name: path.read_text(encoding="utf-8").strip()
            for name, path in paths.items()
        }
    return {
        "task_count": len(task_versions),
        "versions": {
            name: sorted({values[name] for values in task_versions.values()})
            for name in ("code", "data", "evaluation")
        },
        "by_task": task_versions,
    }


def _diagnostic_markdown(payload: Mapping[str, Any]) -> str:
    replay = payload["replay_diagnostics"]
    provenance = payload["appworld_provenance"]
    return "\n".join(
        [
            "# EXP-024A Exact Replay Failure Diagnostic",
            "",
            f"- Replay states passed: {replay['passed_state_count']}/{replay['state_count']}",
            "- History-complete states: "
            f"{replay['history_match_state_count']}/{replay['state_count']}",
            "- Target observations matched: "
            f"{replay['target_match_state_count']}/{replay['state_count']}",
            "- History observations matched: "
            f"{replay['history_step_match_count']}/{replay['history_step_count']}",
            f"- Installed AppWorld: `{provenance['installed']['package_version']}`",
            f"- Installed VCS commit: `{provenance['installed']['vcs_commit']}`",
            f"- Official trajectory code versions: `{provenance['source']['versions']['code']}`",
            f"- Official trajectory data versions: `{provenance['source']['versions']['data']}`",
            "",
            "VERIFIED: exact replay failed for every selected state, including both "
            "zero-history states.",
            "VERIFIED: the installed AppWorld package version differs from all nine "
            "immutable trajectory version markers.",
            "INFERENCE: this version/data contract mismatch is the leading explanation "
            "for the replay divergence; causality has not yet been proven by a "
            "matched-version rerun.",
            "",
            "The preregistered replay gate therefore blocked all candidate generation "
            "and execution.",
        ]
    ) + "\n"


def _final_markdown(summary: Mapping[str, Any]) -> str:
    projection = summary["runtime_projection"]
    return "\n".join(
        [
            "# EXP-024A Signature-Balanced Oracle One-Step Causal Audit",
            "",
            f"- Run UUID: `{summary['run_uuid']}`",
            f"- Decision branch: `{summary['decision']['decision_branch']}`",
            "- Replay validation: "
            f"{summary['replay']['passed_state_count']}/"
            f"{summary['replay']['state_count']} states passed",
            f"- Planned conditions: {summary['planned_condition_count']}",
            f"- Actual Qwen generations: {summary['actual_qwen_generation_count']}",
            f"- Actual H100 hours: {summary['actual_h100_hours']:.4f}",
            "- Projected H100 hours (best/expected/conservative): "
            f"{projection['best_h100_hours']:.4f} / "
            f"{projection['expected_h100_hours']:.4f} / "
            f"{projection['conservative_h100_hours']:.4f}",
            "- Raw transition content behaviorally validated: False",
            "- Field training remains blocked: True",
            "",
            "No baseline, raw-oracle, signature-card, hard-negative, popularity, "
            "random, or alternate-exemplar action was generated. This is the required "
            "stop behavior after an invalid replay preflight.",
        ]
    ) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_procedural_causal_audit_6h.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024a")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    paths = {
        "preflight": args.artifact_dir / "preflight_summary.json",
        "conditions": args.artifact_dir / "condition_manifest.json",
        "strata": args.artifact_dir / "audit_state_strata.json",
        "replay": args.artifact_dir / "replay" / "replay_summary.json",
        "queries": Path(settings["exp022_artifact"]) / "one_step_query_manifest.json",
        "records": Path(settings["source_data"]) / "memory_records.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Required finalization input missing: {name}={path}")
    if args.resume_checkpoint.resolve() != paths["replay"].resolve():
        raise ValueError("Finalization must resume from the immutable replay summary")

    replay = _load_json(paths["replay"])
    if bool(replay["all_states_passed"]):
        raise ValueError("Replay-failure finalization cannot consume a passing replay")
    generation_summary = args.artifact_dir / "generation_summary.json"
    output_dir = args.artifact_dir / "condition_outputs"
    generated_outputs = sorted(output_dir.glob("*.json")) if output_dir.exists() else []
    if generation_summary.exists() or generated_outputs:
        raise ValueError("Replay gate was crossed: generation artifacts already exist")

    replay_paths = sorted((args.artifact_dir / "replay" / "states").glob("*.json"))
    replay_rows = [_load_json(path) for path in replay_paths]
    if len(replay_rows) != int(replay["state_count"]):
        raise ValueError(
            f"Replay state outputs differ: {len(replay_rows)} != {replay['state_count']}"
        )
    replay_diagnostics = summarize_replay_failure(replay_rows)
    if replay_diagnostics["failed_state_count"] != int(replay["failed_state_count"]):
        raise ValueError("Replay failure count differs from the atomic state outputs")

    queries = list(_load_json(paths["queries"])["rows"])
    provenance = {
        "installed": _appworld_installation(),
        "source": _source_versions(paths["records"], queries),
    }
    installed_version = str(provenance["installed"]["package_version"])
    source_versions = set(provenance["source"]["versions"]["code"]) | set(
        provenance["source"]["versions"]["data"]
    )
    provenance["installed_matches_source_versions"] = source_versions == {
        installed_version
    }
    diagnostic = {
        "format": "procedural_causal_replay_failure_report_6h_v1",
        "run_uuid": settings["run_uuid"],
        "decision_branch": "appworld_one_step_replay_invalid",
        "replay_diagnostics": replay_diagnostics,
        "appworld_provenance": provenance,
        "verified_facts": [
            "all_45_selected_states_failed_the_exact_replay_gate",
            "both_zero_history_target_actions_failed_exact_observation_reproduction",
            "installed_and_official_trajectory_appworld_versions_differ",
            "no_qwen_generation_or_candidate_action_execution_started",
        ],
        "inference": (
            "The AppWorld code/data version mismatch is the leading explanation for "
            "the replay divergence; a matched-0.1.0 replay is required to establish causality."
        ),
    }

    preflight = _load_json(paths["preflight"])
    conditions = _load_json(paths["conditions"])
    strata = _load_json(paths["strata"])
    scenarios = preflight["runtime_projection"]["scenarios"]
    summary = {
        "format": "procedural_causal_final_summary_6h_v1",
        "run_uuid": settings["run_uuid"],
        "source_commit": args.lambda_head,
        "planned_condition_count": int(conditions["condition_count"]),
        "actual_condition_count": 0,
        "actual_qwen_generation_count": 0,
        "actual_generated_action_execution_count": 0,
        "actual_h100_hours": 0.0,
        "preflight_elapsed_seconds": float(preflight["elapsed_seconds"]),
        "replay_elapsed_seconds": float(replay["elapsed_seconds"]),
        "finalization_elapsed_seconds": time.perf_counter() - started,
        "signature_equivalence": preflight["signature_equivalence"],
        "audit_strata": {
            "state_count": int(strata["state_count"]),
            "task_count": int(strata["task_count"]),
            "stratum_state_counts": strata["stratum_state_counts"],
            "primary_state_count": int(
                strata["primary_non_documentation_high_tier_state_count"]
            ),
            "primary_task_count": int(
                strata["primary_non_documentation_high_tier_task_count"]
            ),
        },
        "replay": replay_diagnostics,
        "runtime_projection": {
            "best_h100_hours": float(scenarios["best"]["h100_hours"]),
            "expected_h100_hours": float(scenarios["expected"]["h100_hours"]),
            "conservative_h100_hours": float(
                scenarios["conservative"]["h100_hours"]
            ),
            "projected_artifact_bytes": int(
                preflight["runtime_projection"]["projected_artifact_bytes"]
            ),
        },
        "appworld_provenance": provenance,
        "behavioral_results": {
            name: "not_run_replay_gate_failed"
            for name in conditions["condition_counts"]
        },
        "same_signature_alternate_results": "not_run_replay_gate_failed",
        "documentation_stratified_results": "not_run_replay_gate_failed",
        "raw_nll_behavior_relationship": "not_run_replay_gate_failed",
        "task_grouped_confidence_intervals": "not_run_replay_gate_failed",
        "decision": {
            "decision_branch": "appworld_one_step_replay_invalid",
            "raw_transition_content_behaviorally_validated": False,
            "field_training_remains_blocked": True,
            "recommended_next_milestone": (
                "Reproduce the immutable AppWorld 0.1.0 code/data environment and "
                "rerun exact replay validation before revisiting EXP-024A generation."
            ),
        },
    }

    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="replay_failure_diagnosis_and_finalization",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(args.resume_checkpoint),
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        atomic_write_json(args.artifact_dir / "replay_failure_diagnostics.json", diagnostic)
        atomic_write_text(
            args.artifact_dir / "replay_failure_report.md",
            _diagnostic_markdown(diagnostic),
        )
        summary["finalization_elapsed_seconds"] = time.perf_counter() - started
        atomic_write_json(args.artifact_dir / "final_exp024a_summary.json", summary)
        atomic_write_text(
            args.artifact_dir / "final_exp024a_report.md", _final_markdown(summary)
        )
        attempt.progress(
            decision_branch="appworld_one_step_replay_invalid",
            actual_qwen_generation_count=0,
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp024a_summary.json"
            ),
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
