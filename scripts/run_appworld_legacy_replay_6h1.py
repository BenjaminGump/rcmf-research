from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import (
    summarize_replay_results,
    upgrade_replay_contract,
    validate_bridge_result,
    validate_legacy_runtime,
    validate_replay_contract,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file

REPLAY_SCHEMA_NAMESPACE = "v2"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _effective_contract(
    base: Mapping[str, Any], *, attempt_id: str, run_uuid: str
) -> dict[str, Any]:
    payload = upgrade_replay_contract(base)
    payload["experiment_name"] = (
        f"exp024r_{_safe_name(run_uuid)}_{_safe_name(str(base['state_example_id']))}_"
        f"{_safe_name(attempt_id)}"
    )
    payload["attempt_id"] = attempt_id
    validate_replay_contract(payload)
    return payload


def _load_completed(
    *,
    output_path: Path,
    contract_dir: Path,
    state_id: str,
    executable: Path,
    root: Path,
) -> dict[str, Any] | None:
    if not output_path.exists():
        return None
    candidates = sorted(contract_dir.rglob(f"{_safe_name(state_id)}.json"))
    result = _load_json(output_path)
    for path in candidates:
        contract = _load_json(path)
        try:
            validate_bridge_result(
                result,
                contract=contract,
                executable=executable,
                root=root,
            )
            return result
        except ValueError:
            continue
    raise ValueError(f"Completed replay row lacks its validated contract: {output_path}")


def _decision(summary: Mapping[str, Any], *, phase: str, expected_states: int) -> dict[str, Any]:
    rows = int(summary["state_count"])
    passes = int(summary["complete_replay_pass_count"])
    initial = int(summary["initial_identity_match_count"])
    no_history = list(summary.get("no_history_rows", []))
    if phase == "sentinel":
        if not no_history or not all(bool(row["passed"]) for row in no_history):
            return {
                "decision_branch": "appworld_010_initial_data_or_task_snapshot_mismatch",
                "full_replay_allowed": False,
            }
        if passes != rows:
            return {
                "decision_branch": "appworld_010_execution_semantics_or_normalization_mismatch",
                "full_replay_allowed": False,
            }
        return {"decision_branch": "sentinel_replay_validated", "full_replay_allowed": True}
    if rows != expected_states:
        raise ValueError(f"Full replay result count differs: {rows} != {expected_states}")
    if passes == expected_states:
        return {
            "decision_branch": "appworld_010_replay_validated",
            "full_replay_allowed": False,
        }
    if initial != expected_states:
        branch = "appworld_010_data_or_task_snapshot_mismatch"
    else:
        branch = "appworld_010_partial_replay_failure"
    return {"decision_branch": branch, "full_replay_allowed": False}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_legacy_replay_6h1.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("sentinel", "full"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h1"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    required = {
        "run_manifest": args.artifact_dir / "run_manifest.json",
        "environment": args.artifact_dir / "environment_provenance.json",
        "sentinel": args.artifact_dir / "sentinel_manifest.json",
    }
    for name, path in required.items():
        if not path.exists():
            raise FileNotFoundError(f"Replay prerequisite missing: {name}={path}")
    environment = _load_json(required["environment"])
    active_contract_manifest = Path(str(environment["active_contract_manifest"]))
    if not active_contract_manifest.exists():
        raise FileNotFoundError(
            f"Active replay contract manifest missing: {active_contract_manifest}"
        )
    required["contracts"] = active_contract_manifest
    contract_manifest = _load_json(active_contract_manifest)
    sentinel_manifest = _load_json(required["sentinel"])
    legacy = settings["legacy"]
    executable = Path(str(environment["legacy_python"]))
    root = Path(str(environment["legacy_root"]))
    validate_legacy_runtime(
        executable=executable,
        root=root,
        current_executable=Path(sys.executable),
    )
    if environment["wheel"]["sha256"] != legacy["wheel_sha256"]:
        raise ValueError("Legacy replay wheel provenance changed")
    if environment["probe"]["appworld_version"] != "0.1.0":
        raise ValueError("Legacy AppWorld package version changed")
    if environment["probe"]["db_version"] != "0.1.0":
        raise ValueError("Legacy AppWorld DB version changed")

    base_rows = {str(row["state_example_id"]): row for row in contract_manifest["rows"]}
    if len(base_rows) != int(settings["expected"]["state_count"]):
        raise ValueError("Replay contract manifest state count changed")
    if args.phase == "sentinel":
        selected_ids = [str(row["state_example_id"]) for row in sentinel_manifest["rows"]]
    else:
        sentinel_summary_path = (
            args.artifact_dir / "replay" / f"sentinel_summary_{REPLAY_SCHEMA_NAMESPACE}.json"
        )
        if not sentinel_summary_path.exists():
            raise FileNotFoundError("Full replay requires the sentinel summary")
        sentinel_summary = _load_json(sentinel_summary_path)
        if not bool(sentinel_summary["decision"]["full_replay_allowed"]):
            raise RuntimeError("Full replay is blocked by sentinel validation")
        selected_ids = sorted(base_rows)

    data_hashes = {name: sha256_file(path) for name, path in required.items()}
    config_hash = sha256_file(args.config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"legacy_exact_replay_{args.phase}",
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
        output_dir = args.artifact_dir / "replay" / f"states_{REPLAY_SCHEMA_NAMESPACE}"
        contract_dir = (
            args.artifact_dir / "replay" / f"attempt_contracts_{REPLAY_SCHEMA_NAMESPACE}"
        )
        log_dir = args.artifact_dir / "replay" / f"logs_{REPLAY_SCHEMA_NAMESPACE}"
        for path in (output_dir, contract_dir, log_dir):
            path.mkdir(parents=True, exist_ok=True)
        results = []
        reused = 0
        computed = 0
        for position, state_id in enumerate(selected_ids, start=1):
            output_path = output_dir / f"{_safe_name(state_id)}.json"
            completed = _load_completed(
                output_path=output_path,
                contract_dir=contract_dir,
                state_id=state_id,
                executable=executable,
                root=root,
            )
            if completed is not None:
                results.append(completed)
                reused += 1
                continue
            base_contract_path = Path(str(base_rows[state_id]["contract_path"]))
            base_contract = _load_json(base_contract_path)
            contract = _effective_contract(
                base_contract,
                attempt_id=args.attempt_id,
                run_uuid=str(settings["run_uuid"]),
            )
            attempt_contract_path = (
                contract_dir / _safe_name(args.attempt_id) / f"{_safe_name(state_id)}.json"
            )
            attempt_contract_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(attempt_contract_path, contract)
            env = dict(os.environ)
            env.update(
                {
                    "APPWORLD_ROOT": str(root),
                    "APPWORLD_CACHE": str(environment["legacy_cache"]),
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONPATH": "",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            command = [
                str(executable),
                str(Path(settings["replay"]["bridge"])),
                "--input",
                str(attempt_contract_path),
                "--output",
                str(output_path),
            ]
            subprocess_result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=int(settings["replay"]["subprocess_timeout_seconds"]),
                check=False,
            )
            log_path = log_dir / f"{_safe_name(args.attempt_id)}_{_safe_name(state_id)}.log"
            atomic_write_text(log_path, subprocess_result.stdout)
            if subprocess_result.returncode != 0 or not output_path.exists():
                raise RuntimeError(
                    f"Legacy replay subprocess failed for {state_id}; see {log_path}"
                )
            row = _load_json(output_path)
            validate_bridge_result(
                row,
                contract=contract,
                executable=executable,
                root=root,
            )
            results.append(row)
            computed += 1
            attempt.progress(
                completed_states=len(results),
                total_states=len(selected_ids),
                latest_validated_checkpoint=str(output_path),
            )
            print(
                json.dumps(
                    {
                        "phase": args.phase,
                        "completed": len(results),
                        "total": len(selected_ids),
                        "state_example_id": state_id,
                        "passed": row["passed"],
                        "first_divergence_step": row["first_divergence_step"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        summary = summarize_replay_results(results)
        no_history_ids = {
            state_id for state_id, row in base_rows.items() if int(row["history_step_count"]) == 0
        }
        summary.update(
            {
                "format": f"appworld_legacy_{args.phase}_summary_6h1_v2",
                "phase": args.phase,
                "reused_state_count": reused,
                "new_state_count": computed,
                "elapsed_seconds": time.perf_counter() - started,
                "no_history_rows": [
                    {
                        "state_example_id": row["state_example_id"],
                        "passed": row["passed"],
                        "target_observation_match": row["target_observation_match"],
                    }
                    for row in results
                    if str(row["state_example_id"]) in no_history_ids
                ],
            }
        )
        summary["decision"] = _decision(
            summary,
            phase=args.phase,
            expected_states=int(settings["expected"]["state_count"]),
        )
        output_summary = (
            args.artifact_dir
            / "replay"
            / (
                f"sentinel_summary_{REPLAY_SCHEMA_NAMESPACE}.json"
                if args.phase == "sentinel"
                else f"replay_summary_{REPLAY_SCHEMA_NAMESPACE}.json"
            )
        )
        atomic_write_json(output_summary, summary)
        attempt.progress(latest_validated_checkpoint=str(output_summary))
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
