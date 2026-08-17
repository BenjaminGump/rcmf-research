from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import upgrade_replay_contract
from rcmf.training.appworld_semantic_replay_6h2 import (
    ALLOWED_TEMPORAL_CLAIMS,
    ALLOWED_TOKEN_FIELDS,
    SEMANTIC_NORMALIZATION_VERSION,
    SEMANTIC_REPLAY_CONTRACT_VERSION,
    SEMANTIC_REPLAY_RESULT_VERSION,
    canonical_hash,
    identity_hashes,
    summarize_semantic_replays,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _semantic_contract(
    base: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
    attempt_id: str,
    repeat_index: int,
) -> dict[str, Any]:
    old = upgrade_replay_contract(base)
    expected_fields = identity_hashes(str(old["expected_task_query"]))
    semantic_module = Path(str(settings["replay"]["semantic_module"])).resolve()
    payload = {
        "format": SEMANTIC_REPLAY_CONTRACT_VERSION,
        "state_example_id": str(old["state_example_id"]),
        "task_id": str(old["task_id"]),
        "target_step": int(old["target_step"]),
        "history_step_count": int(old["history_step_count"]),
        "normalization_version": SEMANTIC_NORMALIZATION_VERSION,
        "allowed_token_fields": sorted(ALLOWED_TOKEN_FIELDS),
        "allowed_temporal_claims": sorted(ALLOWED_TEMPORAL_CLAIMS),
        "legacy_python": str(settings["legacy"]["executable"]),
        "appworld_root": str(settings["legacy"]["appworld_root"]),
        "semantic_module_path": str(semantic_module),
        "semantic_module_sha256": sha256_file(semantic_module),
        "experiment_name": (
            f"exp024r2_{_safe_name(str(settings['run_uuid']))}_{_safe_name(str(old['state_example_id']))}_"
            f"{_safe_name(attempt_id)}_repeat{int(repeat_index)}"
        ),
        "attempt_id": str(attempt_id),
        "repeat_index": int(repeat_index),
        "random_seed": int(settings["replay"]["random_seed"]),
        "max_interactions": int(settings["replay"]["max_interactions"]),
        "max_api_calls_per_interaction": int(settings["replay"]["max_api_calls_per_interaction"]),
        "expected_identity_field_sha256": expected_fields,
        "source_contract_sha256": canonical_hash(old),
        "source_hashes": dict(old.get("source_hashes", {})),
        "actions": list(old["actions"]),
    }
    payload["actions_sha256"] = canonical_hash(payload["actions"])
    return payload


def _validate_result(
    result: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> None:
    if result.get("format") != SEMANTIC_REPLAY_RESULT_VERSION:
        raise ValueError("Unexpected semantic replay result version")
    if result.get("contract_sha256") != canonical_hash(contract):
        raise ValueError("Semantic replay contract hash mismatch")
    if Path(str(result["python_executable"])) != Path(str(settings["legacy"]["executable"])):
        raise ValueError("Semantic bridge used the wrong Python executable")
    if Path(str(result["appworld_root"])).resolve() != Path(
        str(settings["legacy"]["appworld_root"])
    ).resolve():
        raise ValueError("Semantic bridge used the wrong APPWORLD_ROOT")
    if result.get("appworld_version") != "0.1.0" or result.get("db_version") != "0.1.0":
        raise ValueError("Semantic bridge used the wrong AppWorld version")
    if result.get("normalization_version") != SEMANTIC_NORMALIZATION_VERSION:
        raise ValueError("Semantic bridge changed normalization")
    if result.get("allowed_token_fields") != sorted(ALLOWED_TOKEN_FIELDS):
        raise ValueError("Semantic bridge changed allowed token fields")
    if result.get("allowed_temporal_claims") != sorted(ALLOWED_TEMPORAL_CLAIMS):
        raise ValueError("Semantic bridge changed temporal claims")
    if result.get("state_example_id") != contract.get("state_example_id"):
        raise ValueError("Semantic bridge changed state identity")
    if [int(row["step_id"]) for row in result.get("steps", [])] != [
        int(row["step_id"]) for row in contract["actions"]
    ]:
        raise ValueError("Semantic bridge changed replay order")


def _repeat_equivalence(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    first_steps = list(first["steps"])
    second_steps = list(second["steps"])
    step_rows = []
    for left, right in zip(first_steps, second_steps):
        step_rows.append(
            {
                "step_id": int(left["step_id"]),
                "semantic_observation_match": (
                    left["semantic_comparison"]["actual_semantic_sha256"]
                    == right["semantic_comparison"]["actual_semantic_sha256"]
                ),
                "non_token_differences_match": (
                    left["semantic_comparison"]["non_token_differences"]
                    == right["semantic_comparison"]["non_token_differences"]
                ),
                "state_before_match": left["state_before"] == right["state_before"],
                "state_after_match": left["state_after"] == right["state_after"],
                "exception_match": left["exception"] == right["exception"],
            }
        )
    return {
        "state_example_id": first["state_example_id"],
        "step_count_match": len(first_steps) == len(second_steps),
        "task_files_match": first["initial_task_files"] == second["initial_task_files"],
        "initial_state_match": first["initial_state_fingerprint"] == second["initial_state_fingerprint"],
        "final_state_match": first["final_state_fingerprint"] == second["final_state_fingerprint"],
        "step_rows": step_rows,
        "semantic_repeat_match": bool(
            len(first_steps) == len(second_steps)
            and first["initial_task_files"] == second["initial_task_files"]
            and first["initial_state_fingerprint"] == second["initial_state_fingerprint"]
            and first["final_state_fingerprint"] == second["final_state_fingerprint"]
            and all(all(value for key, value in row.items() if key != "step_id") for row in step_rows)
        ),
    }


def _sentinel_decision(
    repeats: list[Mapping[str, Any]], repeat_checks: list[Mapping[str, Any]]
) -> dict[str, Any]:
    required = []
    for summary in repeats:
        required.extend(
            [
                int(summary["state_count"]) == 13,
                int(summary["identity_match_count"]) == 13,
                int(summary["complete_history_semantic_match_count"]) == 13,
                int(summary["prior_observation_count"]) == 102,
                int(summary["prior_semantic_match_count"]) == 102,
                int(summary["target_semantic_match_count"]) == 13,
                int(summary["complete_semantic_replay_count"]) == 13,
                int(summary["exception_count"]) == 0,
                int(summary["non_temporal_jwt_mismatch_count"]) == 0,
                int(summary["non_token_mismatch_count"]) == 0,
            ]
        )
    required.append(all(bool(row["semantic_repeat_match"]) for row in repeat_checks))
    passed = all(required)
    return {
        "decision_branch": (
            "semantic_sentinel_validated"
            if passed
            else "appworld_010_non_jwt_replay_mismatch"
        ),
        "sentinel_gate_passed": passed,
        "full_replay_allowed": passed,
    }


def _full_decision(summary: Mapping[str, Any]) -> dict[str, Any]:
    required = [
        int(summary["state_count"]) == 45,
        int(summary["identity_match_count"]) == 45,
        int(summary["complete_history_semantic_match_count"]) == 45,
        int(summary["prior_observation_count"]) == 372,
        int(summary["prior_semantic_match_count"]) == 372,
        int(summary["target_semantic_match_count"]) == 45,
        int(summary["complete_semantic_replay_count"]) == 45,
        int(summary["exception_count"]) == 0,
        int(summary["non_temporal_jwt_mismatch_count"]) == 0,
        int(summary["non_token_mismatch_count"]) == 0,
    ]
    passed = all(required)
    return {
        "decision_branch": (
            "appworld_010_semantic_replay_validated"
            if passed
            else "appworld_010_partial_semantic_replay_failure"
        ),
        "semantic_replay_validated": passed,
        "generation_allowed_in_this_milestone": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_semantic_replay_6h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("sentinel", "full"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r2")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h2"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    if not bool(preflight.get("identity_gate_passed")):
        raise RuntimeError(f"Identity gate blocks semantic replay: {preflight['decision_branch']}")
    parent = Path(settings["parent_exp024r"])
    environment = _load_json(parent / "environment_provenance.json")
    contract_manifest_path = Path(str(environment["active_contract_manifest"]))
    contract_manifest = _load_json(contract_manifest_path)
    base_rows = {str(row["state_example_id"]): row for row in contract_manifest["rows"]}
    if len(base_rows) != int(settings["expected"]["state_count"]):
        raise ValueError("Parent replay contract count changed")
    sentinel_manifest_path = parent / "sentinel_manifest.json"
    sentinel_manifest = _load_json(sentinel_manifest_path)
    if args.phase == "sentinel":
        selected_ids = [str(row["state_example_id"]) for row in sentinel_manifest["rows"]]
        repeats = range(1, int(settings["replay"]["repeats"]) + 1)
    else:
        sentinel_summary_path = args.artifact_dir / "replay" / "semantic_sentinel_summary.json"
        if not sentinel_summary_path.exists():
            raise FileNotFoundError("Full semantic replay requires sentinel summary")
        sentinel_summary = _load_json(sentinel_summary_path)
        if not bool(sentinel_summary["decision"]["full_replay_allowed"]):
            raise RuntimeError("Semantic sentinel gate blocks full replay")
        selected_ids = sorted(base_rows)
        repeats = range(1, 2)

    config_hash = sha256_file(args.config)
    data_hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "preflight_decision": sha256_file(args.artifact_dir / "preflight_decision.json"),
        "identity_audit": sha256_file(args.artifact_dir / "identity_provenance_audit.json"),
        "jwt_audit": sha256_file(args.artifact_dir / "jwt_stable_claim_audit.json"),
        "contract_manifest": sha256_file(contract_manifest_path),
        "sentinel_manifest": sha256_file(sentinel_manifest_path),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"semantic_replay_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        all_repeat_rows: list[list[dict[str, Any]]] = []
        repeat_summaries = []
        for repeat_index in repeats:
            rows: list[dict[str, Any]] = []
            for position, state_id in enumerate(selected_ids, start=1):
                output_dir = args.artifact_dir / "replay" / args.phase / f"repeat_{repeat_index}" / "states"
                contract_dir = args.artifact_dir / "replay" / args.phase / f"repeat_{repeat_index}" / "contracts"
                log_dir = args.artifact_dir / "replay" / args.phase / f"repeat_{repeat_index}" / "logs"
                for path in (output_dir, contract_dir, log_dir):
                    path.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"{_safe_name(state_id)}.json"
                contract_path = contract_dir / f"{_safe_name(state_id)}.json"
                base = _load_json(Path(str(base_rows[state_id]["contract_path"])))
                contract = _semantic_contract(
                    base,
                    settings=settings,
                    attempt_id=args.attempt_id,
                    repeat_index=repeat_index,
                )
                if contract_path.exists() and _load_json(contract_path) != contract:
                    raise ValueError(f"Atomic semantic contract changed: {contract_path}")
                atomic_write_json(contract_path, contract)
                if output_path.exists():
                    result = _load_json(output_path)
                    _validate_result(result, contract=contract, settings=settings)
                    rows.append(result)
                    continue
                env = dict(os.environ)
                env.update(
                    {
                        "APPWORLD_ROOT": str(settings["legacy"]["appworld_root"]),
                        "APPWORLD_CACHE": str(settings["legacy"]["appworld_cache"]),
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONPATH": "",
                        "PYTHONUNBUFFERED": "1",
                    }
                )
                command = [
                    str(settings["legacy"]["executable"]),
                    str(settings["replay"]["semantic_bridge"]),
                    "--input",
                    str(contract_path),
                    "--output",
                    str(output_path),
                ]
                completed = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=int(settings["replay"]["subprocess_timeout_seconds"]),
                    check=False,
                )
                atomic_write_text(
                    log_dir / f"{_safe_name(state_id)}.log", completed.stdout
                )
                if completed.returncode != 0 or not output_path.exists():
                    raise RuntimeError(f"Semantic replay subprocess failed: {state_id}")
                result = _load_json(output_path)
                _validate_result(result, contract=contract, settings=settings)
                rows.append(result)
                attempt.progress(
                    phase=args.phase,
                    repeat_index=repeat_index,
                    completed_states=position,
                    total_states=len(selected_ids),
                    latest_validated_checkpoint=str(output_path),
                )
                print(
                    json.dumps(
                        {
                            "phase": args.phase,
                            "repeat": repeat_index,
                            "completed": position,
                            "total": len(selected_ids),
                            "state_example_id": state_id,
                            "passed": result["passed"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            summary = summarize_semantic_replays(rows)
            summary["repeat_index"] = repeat_index
            repeat_summary_path = (
                args.artifact_dir / "replay" / args.phase / f"repeat_{repeat_index}" / "summary.json"
            )
            atomic_write_json(repeat_summary_path, summary)
            all_repeat_rows.append(rows)
            repeat_summaries.append(summary)

        if args.phase == "sentinel":
            first = {row["state_example_id"]: row for row in all_repeat_rows[0]}
            second = {row["state_example_id"]: row for row in all_repeat_rows[1]}
            repeat_checks = [
                _repeat_equivalence(first[state_id], second[state_id])
                for state_id in sorted(first)
            ]
            decision = _sentinel_decision(repeat_summaries, repeat_checks)
            combined = {
                "format": "appworld_semantic_sentinel_summary_6h2_v1",
                "repeat_count": len(repeat_summaries),
                "repeat_summaries": repeat_summaries,
                "repeat_checks": repeat_checks,
                "repeat_semantic_match_count": sum(
                    bool(row["semantic_repeat_match"]) for row in repeat_checks
                ),
                "decision": decision,
            }
            summary_path = args.artifact_dir / "replay" / "semantic_sentinel_summary.json"
        else:
            decision = _full_decision(repeat_summaries[0])
            combined = {
                "format": "appworld_full_semantic_replay_summary_6h2_v1",
                "summary": repeat_summaries[0],
                "decision": decision,
            }
            summary_path = args.artifact_dir / "replay" / "full_semantic_replay_summary.json"
        atomic_write_json(summary_path, combined)
        attempt.progress(latest_validated_checkpoint=str(summary_path))
        print(json.dumps(combined, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
