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
from rcmf.training.appworld_replay_clean_rebuild_7b import (
    ALLOWED_ROOT_JWT_PATHS,
    ALLOWED_TEMPORAL_CLAIMS,
    ALLOWED_TOKEN_FIELDS,
    SEMANTIC_NORMALIZATION_VERSION,
    SEMANTIC_REPLAY_CONTRACT_VERSION,
    SEMANTIC_REPLAY_RESULT_VERSION,
    canonical_hash,
    identity_hashes,
    semantic_replay_gate_v3,
    summarize_semantic_replays_v3,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


CHECKPOINT_VERSION = "replay_validated_clean_rebuild_checkpoint_7b_v1"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in value
    )


def _semantic_contract(
    base: Mapping[str, Any],
    *,
    settings: Mapping[str, Any],
    phase: str,
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
        "allowed_root_jwt_paths": sorted(ALLOWED_ROOT_JWT_PATHS),
        "legacy_python": str(settings["legacy"]["executable"]),
        "appworld_root": str(settings["legacy"]["appworld_root"]),
        "semantic_module_path": str(semantic_module),
        "semantic_module_sha256": sha256_file(semantic_module),
        "experiment_name": (
            f"exp025b_{_safe_name(str(settings['run_uuid']))}_{phase}_"
            f"{_safe_name(str(old['state_example_id']))}_{_safe_name(attempt_id)}_"
            f"repeat{repeat_index}"
        ),
        "attempt_id": str(attempt_id),
        "repeat_index": int(repeat_index),
        "random_seed": int(settings["replay"]["random_seed"]),
        "max_interactions": int(settings["replay"]["max_interactions"]),
        "max_api_calls_per_interaction": int(
            settings["replay"]["max_api_calls_per_interaction"]
        ),
        "expected_identity_field_sha256": identity_hashes(
            str(old["expected_task_query"])
        ),
        "source_contract_sha256": canonical_hash(old),
        "source_hashes": dict(old.get("source_hashes", {})),
        "actions": list(old["actions"]),
    }
    payload["actions_sha256"] = canonical_hash(payload["actions"])
    return payload


def _checkpoint_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": CHECKPOINT_VERSION, "rows": {}}
    payload = _load_json(path)
    if payload.get("format") != CHECKPOINT_VERSION:
        raise ValueError("Unexpected EXP-025B replay checkpoint format")
    return payload


def _validate_result(
    result: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> None:
    checks = {
        "format": result.get("format") == SEMANTIC_REPLAY_RESULT_VERSION,
        "contract": result.get("contract_sha256") == canonical_hash(contract),
        "python": Path(str(result.get("python_executable")))
        == Path(str(settings["legacy"]["executable"])),
        "root": Path(str(result.get("appworld_root", ""))).resolve()
        == Path(str(settings["legacy"]["appworld_root"])).resolve(),
        "appworld": result.get("appworld_version") == "0.1.0",
        "db": result.get("db_version") == "0.1.0",
        "normalization": result.get("normalization_version")
        == SEMANTIC_NORMALIZATION_VERSION,
        "token_fields": result.get("allowed_token_fields")
        == sorted(ALLOWED_TOKEN_FIELDS),
        "temporal_claims": result.get("allowed_temporal_claims")
        == sorted(ALLOWED_TEMPORAL_CLAIMS),
        "root_paths": result.get("allowed_root_jwt_paths")
        == sorted(ALLOWED_ROOT_JWT_PATHS),
        "state": result.get("state_example_id") == contract.get("state_example_id"),
        "order": [int(row["step_id"]) for row in result.get("steps", [])]
        == [int(row["step_id"]) for row in contract["actions"]],
    }
    if not all(checks.values()):
        raise ValueError(
            "Invalid EXP-025B replay result: "
            + ", ".join(key for key, value in checks.items() if not value)
        )
    result_without_hash = dict(result)
    result_sha256 = result_without_hash.pop("result_sha256", None)
    if result_sha256 != canonical_hash(result_without_hash):
        raise ValueError("EXP-025B replay result self-hash mismatch")


def _repeat_equivalence(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, Any]:
    first_steps = list(first["steps"])
    second_steps = list(second["steps"])
    step_rows = []
    for left, right in zip(first_steps, second_steps):
        step_rows.append(
            {
                "step_id": int(left["step_id"]),
                "semantic_observation_match": left["semantic_comparison"][
                    "actual_semantic_v3_sha256"
                ]
                == right["semantic_comparison"]["actual_semantic_v3_sha256"],
                "state_before_match": left["state_before"] == right["state_before"],
                "state_after_match": left["state_after"] == right["state_after"],
                "exception_match": left["exception"] == right["exception"],
            }
        )
    passed = bool(
        len(first_steps) == len(second_steps)
        and first["initial_task_files"] == second["initial_task_files"]
        and first["initial_state_fingerprint"] == second["initial_state_fingerprint"]
        and first["final_state_fingerprint"] == second["final_state_fingerprint"]
        and all(
            all(value for key, value in row.items() if key != "step_id")
            for row in step_rows
        )
    )
    return {
        "state_example_id": first["state_example_id"],
        "step_count_match": len(first_steps) == len(second_steps),
        "task_files_match": first["initial_task_files"] == second["initial_task_files"],
        "initial_state_match": first["initial_state_fingerprint"]
        == second["initial_state_fingerprint"],
        "final_state_match": first["final_state_fingerprint"]
        == second["final_state_fingerprint"],
        "step_rows": step_rows,
        "semantic_repeat_match": passed,
    }


def _phase_inputs(
    artifact_dir: Path, settings: Mapping[str, Any], phase: str
) -> tuple[Path, Path]:
    parent = Path(str(settings["parent_exp025a"]))
    contracts = parent / "reconciled_replay_contract_manifest.json"
    if phase == "sentinel":
        return contracts, parent / "reconciled_sentinel_manifest.json"
    if phase == "root_jwt":
        return contracts, artifact_dir / "root_jwt_schema_extension_sentinel_manifest.json"
    return contracts, parent / "reconciled_one_step_manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("sentinel", "root_jwt", "full"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp025b")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7b"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _load_json(args.artifact_dir / "preflight_replay_report.json")
    if not bool(preflight["passed"]):
        raise RuntimeError("EXP-025B replay preflight failed")
    if args.phase in {"root_jwt", "full"}:
        sentinel = args.artifact_dir / "replay" / "sentinel_summary.json"
        if not sentinel.is_file() or not bool(_load_json(sentinel)["gate"]["passed"]):
            raise RuntimeError("Root-JWT/full replay requires a passed sentinel")
    if args.phase == "full":
        root_jwt = args.artifact_dir / "replay" / "root_jwt_summary.json"
        if not root_jwt.is_file() or not bool(_load_json(root_jwt)["gate"]["passed"]):
            raise RuntimeError("Full replay requires a passed root-JWT schema sentinel")

    contracts_path, selected_path = _phase_inputs(
        args.artifact_dir, settings, args.phase
    )
    contracts = _load_json(contracts_path)
    selected = _load_json(selected_path)
    contract_by_id = {str(row["state_example_id"]): row for row in contracts["rows"]}
    selected_ids = [str(row["state_example_id"]) for row in selected["rows"]]
    if len(selected_ids) != len(set(selected_ids)) or any(
        state_id not in contract_by_id for state_id in selected_ids
    ):
        raise ValueError("Replay manifests contain duplicate or missing state identities")

    config_hash = sha256_file(args.config)
    data_hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "preflight": sha256_file(args.artifact_dir / "preflight_replay_report.json"),
        "contracts": sha256_file(contracts_path),
        "selected": sha256_file(selected_path),
    }
    checkpoint_path = args.artifact_dir / "replay" / "checkpoint_index.json"
    checkpoint = _checkpoint_index(checkpoint_path)
    checkpoint_rows = dict(checkpoint["rows"])
    superseded = list(checkpoint.get("superseded_rows", []))
    repeat_rows: list[list[dict[str, Any]]] = []
    repeat_summaries = []

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"root_login_jwt_semantic_replay_{args.phase}",
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
        for repeat_index in range(1, int(settings["replay"]["repeats"]) + 1):
            rows = []
            for position, state_id in enumerate(selected_ids, start=1):
                key = f"{args.phase}:repeat_{repeat_index}:{state_id}"
                base = _load_json(Path(str(contract_by_id[state_id]["contract_path"])))
                existing = checkpoint_rows.get(key)
                if existing is not None:
                    contract = _load_json(Path(str(existing["contract_path"])))
                    if str(contract.get("source_contract_sha256")) != canonical_hash(
                        upgrade_replay_contract(base)
                    ):
                        superseded.append(
                            {"key": key, "reason": "source_contract_hash_changed", "row": existing}
                        )
                        checkpoint_rows.pop(key)
                        existing = None
                if existing is not None:
                    contract = _load_json(Path(str(existing["contract_path"])))
                    result = _load_json(Path(str(existing["output_path"])))
                    _validate_result(result, contract=contract, settings=settings)
                    if canonical_hash(result) != str(existing["result_sha256"]):
                        raise ValueError(f"Replay checkpoint hash changed: {key}")
                    rows.append(result)
                    continue

                contract = _semantic_contract(
                    base,
                    settings=settings,
                    phase=args.phase,
                    attempt_id=args.attempt_id,
                    repeat_index=repeat_index,
                )
                attempt_root = (
                    args.artifact_dir
                    / "replay"
                    / "attempts"
                    / _safe_name(args.attempt_id)
                    / args.phase
                    / f"repeat_{repeat_index}"
                )
                contract_dir = attempt_root / "contracts"
                output_dir = attempt_root / "states"
                log_dir = attempt_root / "logs"
                for path in (contract_dir, output_dir, log_dir):
                    path.mkdir(parents=True, exist_ok=True)
                filename = f"{_safe_name(state_id)}.json"
                contract_path = contract_dir / filename
                output_path = output_dir / filename
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
                if completed.returncode != 0 or not output_path.is_file():
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
                    "source_contract_sha256": contract["source_contract_sha256"],
                    "output_path": str(output_path),
                    "result_sha256": canonical_hash(result),
                }
                atomic_write_json(
                    checkpoint_path,
                    {
                        "format": CHECKPOINT_VERSION,
                        "rows": checkpoint_rows,
                        "superseded_rows": superseded,
                    },
                )
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
            summary = summarize_semantic_replays_v3(rows)
            summary["repeat_index"] = repeat_index
            repeat_rows.append(rows)
            repeat_summaries.append(summary)
            atomic_write_json(
                args.artifact_dir
                / "replay"
                / args.phase
                / f"repeat_{repeat_index}"
                / "summary.json",
                summary,
            )

        first = {row["state_example_id"]: row for row in repeat_rows[0]}
        second = {row["state_example_id"]: row for row in repeat_rows[1]}
        repeat_checks = [
            _repeat_equivalence(first[state_id], second[state_id])
            for state_id in sorted(first)
        ]
        gate = semantic_replay_gate_v3(
            repeat_summaries,
            expected_states=int(selected["state_count"]),
            expected_tasks=int(selected["task_count"]),
            expected_prior_observations=int(selected["prior_observation_count"]),
        )
        gate["repeat_equivalence"] = all(
            bool(row["semantic_repeat_match"]) for row in repeat_checks
        )
        gate["passed"] = bool(gate["passed"] and gate["repeat_equivalence"])
        combined = {
            "format": f"replay_validated_clean_rebuild_{args.phase}_summary_7b_v1",
            "repeat_summaries": repeat_summaries,
            "repeat_checks": repeat_checks,
            "gate": gate,
        }
        output = args.artifact_dir / "replay" / f"{args.phase}_summary.json"
        atomic_write_json(output, combined)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(combined, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
