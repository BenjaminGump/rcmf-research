from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import upgrade_replay_contract
from rcmf.training.appworld_provenance_replay_6h3 import semantic_replay_gate
from rcmf.training.appworld_semantic_replay_6h2 import (
    ALLOWED_TEMPORAL_CLAIMS,
    ALLOWED_TOKEN_FIELDS,
    SEMANTIC_NORMALIZATION_VERSION,
    SEMANTIC_REPLAY_CONTRACT_VERSION,
    canonical_hash,
    identity_hashes,
    summarize_semantic_replays,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file
from scripts.run_appworld_semantic_replay_6h2 import (
    _repeat_equivalence,
    _validate_result,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


def _semantic_contract(
    base: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
    attempt_id: str,
    repeat_index: int,
) -> dict[str, Any]:
    old = upgrade_replay_contract(base)
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
            f"exp024r3_{_safe_name(str(settings['run_uuid']))}_"
            f"{_safe_name(str(old['state_example_id']))}_{_safe_name(attempt_id)}_r{repeat_index}"
        ),
        "attempt_id": str(attempt_id),
        "repeat_index": int(repeat_index),
        "random_seed": int(settings["replay"]["random_seed"]),
        "max_interactions": int(settings["replay"]["max_interactions"]),
        "max_api_calls_per_interaction": int(settings["replay"]["max_api_calls_per_interaction"]),
        "expected_identity_field_sha256": identity_hashes(str(old["expected_task_query"])),
        "source_contract_sha256": canonical_hash(old),
        "source_hashes": dict(old.get("source_hashes", {})),
        "actions": list(old["actions"]),
    }
    payload["actions_sha256"] = canonical_hash(payload["actions"])
    return payload


def _checkpoint_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": "appworld_provenance_replay_checkpoint_index_6h3_v1", "rows": {}}
    payload = _load_json(path)
    if payload.get("format") != "appworld_provenance_replay_checkpoint_index_6h3_v1":
        raise ValueError("Unexpected replay checkpoint index format")
    return payload


def _checkpoint_key(phase: str, repeat_index: int, state_id: str) -> str:
    return f"{phase}:repeat_{repeat_index}:{state_id}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("sentinel", "full"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r3")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h3"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    if not bool(preflight["replay_allowed"]):
        raise RuntimeError(f"Provenance preflight blocks replay: {preflight['decision_branch']}")
    mode = str(preflight["replay_mode"])
    if mode != "provenance_valid_40_state_quarantine":
        raise RuntimeError(
            "Recovered-snapshot replay requires a separately materialized isolated root; "
            "the quarantine runner cannot silently substitute it"
        )

    contract_manifest_path = args.artifact_dir / "provenance_valid_replay_contract_manifest.json"
    full_manifest_path = args.artifact_dir / "provenance_valid_one_step_manifest_v1.json"
    sentinel_manifest_path = args.artifact_dir / "provenance_valid_sentinel_manifest.json"
    contracts = _load_json(contract_manifest_path)
    contract_by_id = {str(row["state_example_id"]): row for row in contracts["rows"]}
    if len(contract_by_id) != int(settings["expected"]["provenance_valid_states"]):
        raise ValueError("Provenance-valid contract count changed")
    if args.phase == "sentinel":
        selected_manifest = _load_json(sentinel_manifest_path)
        repeats = range(1, int(settings["replay"]["sentinel_repeats"]) + 1)
    else:
        sentinel_summary_path = args.artifact_dir / "replay" / "provenance_valid_sentinel_summary.json"
        if not sentinel_summary_path.exists():
            raise FileNotFoundError("Full replay requires a completed provenance-valid sentinel")
        sentinel_summary = _load_json(sentinel_summary_path)
        if not bool(sentinel_summary["decision"]["full_replay_allowed"]):
            raise RuntimeError("Provenance-valid sentinel blocks full replay")
        selected_manifest = _load_json(full_manifest_path)
        repeats = range(1, 2)
    selected_rows = list(selected_manifest["rows"])
    selected_ids = [str(row["state_example_id"]) for row in selected_rows]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Replay manifest contains duplicate state IDs")
    if any(state_id not in contract_by_id for state_id in selected_ids):
        raise ValueError("Replay manifest references a missing contract")

    config_hash = sha256_file(args.config)
    data_hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "preflight_decision": sha256_file(args.artifact_dir / "preflight_decision.json"),
        "contract_manifest": sha256_file(contract_manifest_path),
        "full_manifest": sha256_file(full_manifest_path),
        "sentinel_manifest": sha256_file(sentinel_manifest_path),
    }
    checkpoint_path = args.artifact_dir / "replay" / "checkpoint_index.json"
    checkpoint = _checkpoint_index(checkpoint_path)
    checkpoint_rows = dict(checkpoint["rows"])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"provenance_valid_semantic_replay_{args.phase}",
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
                key = _checkpoint_key(args.phase, repeat_index, state_id)
                existing = checkpoint_rows.get(key)
                if existing is not None:
                    contract = _load_json(Path(str(existing["contract_path"])))
                    result = _load_json(Path(str(existing["output_path"])))
                    _validate_result(result, contract=contract, settings=settings)
                    if canonical_hash(result) != str(existing["result_sha256"]):
                        raise ValueError(f"Replay checkpoint result hash changed: {key}")
                    rows.append(result)
                    continue

                attempt_root = args.artifact_dir / "replay" / "attempts" / _safe_name(args.attempt_id)
                contract_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "contracts"
                output_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "states"
                log_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "logs"
                for path in (contract_dir, output_dir, log_dir):
                    path.mkdir(parents=True, exist_ok=True)
                contract_path = contract_dir / f"{_safe_name(state_id)}.json"
                output_path = output_dir / f"{_safe_name(state_id)}.json"
                base = _load_json(Path(str(contract_by_id[state_id]["contract_path"])))
                contract = _semantic_contract(
                    base,
                    settings=settings,
                    attempt_id=args.attempt_id,
                    repeat_index=repeat_index,
                )
                atomic_write_json(contract_path, contract)
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
                atomic_write_text(log_dir / f"{_safe_name(state_id)}.log", completed.stdout)
                if completed.returncode != 0 or not output_path.exists():
                    raise RuntimeError(f"Semantic replay subprocess failed: {state_id}")
                result = _load_json(output_path)
                _validate_result(result, contract=contract, settings=settings)
                checkpoint_rows[key] = {
                    "phase": args.phase,
                    "repeat_index": repeat_index,
                    "state_example_id": state_id,
                    "attempt_id": args.attempt_id,
                    "contract_path": str(contract_path),
                    "contract_sha256": canonical_hash(contract),
                    "output_path": str(output_path),
                    "result_sha256": canonical_hash(result),
                }
                checkpoint["rows"] = checkpoint_rows
                checkpoint["latest_key"] = key
                atomic_write_json(checkpoint_path, checkpoint)
                rows.append(result)
                attempt.progress(
                    phase=args.phase,
                    repeat_index=repeat_index,
                    completed_states=position,
                    total_states=len(selected_ids),
                    latest_validated_checkpoint=str(checkpoint_path),
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
            all_repeat_rows.append(rows)
            repeat_summaries.append(summary)

        repeat_checks: list[dict[str, Any]] = []
        if args.phase == "sentinel":
            first = {row["state_example_id"]: row for row in all_repeat_rows[0]}
            second = {row["state_example_id"]: row for row in all_repeat_rows[1]}
            repeat_checks = [
                _repeat_equivalence(first[state_id], second[state_id])
                for state_id in sorted(first)
            ]
        gate = semantic_replay_gate(
            repeat_summaries,
            expected_states=int(selected_manifest["state_count"] if "state_count" in selected_manifest else selected_manifest["retained_state_count"]),
            expected_tasks=int(selected_manifest["task_count"] if "task_count" in selected_manifest else selected_manifest["retained_task_count"]),
            expected_prior_observations=int(selected_manifest["prior_observation_count"] if "prior_observation_count" in selected_manifest else selected_manifest["retained_prior_observation_count"]),
            require_repeat_equivalence=args.phase == "sentinel",
            repeat_checks=repeat_checks,
        )
        if args.phase == "sentinel":
            decision = {
                "decision_branch": "provenance_valid_sentinel_validated" if gate["passed"] else "appworld_010_non_identity_semantic_replay_mismatch",
                "sentinel_gate_passed": gate["passed"],
                "full_replay_allowed": gate["passed"],
            }
            combined = {
                "format": "provenance_valid_semantic_sentinel_summary_6h3_v1",
                "repeat_count": len(repeat_summaries),
                "repeat_summaries": repeat_summaries,
                "repeat_checks": repeat_checks,
                "gate": gate,
                "decision": decision,
            }
            summary_path = args.artifact_dir / "replay" / "provenance_valid_sentinel_summary.json"
        else:
            decision = {
                "decision_branch": "provenance_valid_subset_semantic_replay_validated" if gate["passed"] else "appworld_010_non_identity_semantic_replay_mismatch",
                "semantic_replay_validated": gate["passed"],
                "original_45_retroactively_passed": False,
                "generation_allowed_in_this_milestone": False,
            }
            combined = {
                "format": "provenance_valid_full_semantic_replay_summary_6h3_v1",
                "summary": repeat_summaries[0],
                "gate": gate,
                "decision": decision,
            }
            summary_path = args.artifact_dir / "replay" / "provenance_valid_full_summary.json"
        atomic_write_json(summary_path, combined)
        attempt.progress(latest_validated_checkpoint=str(summary_path))
        print(json.dumps(combined, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
