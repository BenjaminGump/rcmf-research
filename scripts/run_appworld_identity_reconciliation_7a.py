from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_identity_reconciliation_7a import (
    AFFECTED_TASK_IDS,
    select_task_remediation,
    task_replay_gate,
)
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
from scripts.run_appworld_semantic_replay_6h2 import _repeat_equivalence, _validate_result


CHECKPOINT_VERSION = "identity_reconciliation_replay_checkpoint_7a_v1"


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
        "legacy_python": str(settings["legacy"]["executable"]),
        "appworld_root": str(settings["legacy"]["appworld_root"]),
        "semantic_module_path": str(semantic_module),
        "semantic_module_sha256": sha256_file(semantic_module),
        "experiment_name": (
            f"exp025a_{_safe_name(str(settings['run_uuid']))}_{phase}_"
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
        return {"format": CHECKPOINT_VERSION, "rows": {}}
    payload = _load_json(path)
    if payload.get("format") != CHECKPOINT_VERSION:
        raise ValueError("Unexpected EXP-025A replay checkpoint format")
    return payload


def _checkpoint_contract_matches_base(
    generated_contract: Mapping[str, Any], base_contract: Mapping[str, Any]
) -> bool:
    return str(generated_contract.get("source_contract_sha256", "")) == canonical_hash(
        upgrade_replay_contract(base_contract)
    )


def _manifest_paths(artifact_dir: Path, phase: str) -> tuple[Path, Path, range]:
    if phase == "affected":
        return (
            artifact_dir / "affected_replay_contract_manifest.json",
            artifact_dir / "affected_replay_contract_manifest.json",
            range(1, 2),
        )
    if phase == "sentinel":
        return (
            artifact_dir / "reconciled_replay_contract_manifest.json",
            artifact_dir / "reconciled_sentinel_manifest.json",
            range(1, 3),
        )
    return (
        artifact_dir / "reconciled_replay_contract_manifest.json",
        artifact_dir / "reconciled_one_step_manifest.json",
        range(1, 2),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("affected", "sentinel", "full"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp025a")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7a"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    if args.phase == "affected" and not bool(preflight["candidate_replay_allowed"]):
        raise RuntimeError("EXP-025A preflight blocks affected-task replay")
    if args.phase in {"sentinel", "full"}:
        policy = _load_json(args.artifact_dir / "remediation_policy_manifest.json")
        if not bool(policy["structural_corpus_build_allowed"]):
            raise RuntimeError("Remediation policy blocks reconciled replay")
    if args.phase == "full":
        sentinel_path = args.artifact_dir / "replay" / "reconciled_sentinel_summary.json"
        if not sentinel_path.is_file() or not bool(_load_json(sentinel_path)["gate"]["passed"]):
            raise RuntimeError("Full reconciled replay requires a passed repeated sentinel")

    contracts_path, selected_path, repeats = _manifest_paths(args.artifact_dir, args.phase)
    contracts = _load_json(contracts_path)
    selected = _load_json(selected_path)
    contract_by_id = {str(row["state_example_id"]): row for row in contracts["rows"]}
    selected_rows = list(selected["rows"])
    selected_ids = [str(row["state_example_id"]) for row in selected_rows]
    if len(selected_ids) != len(set(selected_ids)) or any(
        state_id not in contract_by_id for state_id in selected_ids
    ):
        raise ValueError("Replay manifests contain duplicate or missing state identities")

    config_hash = sha256_file(args.config)
    data_hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "preflight": sha256_file(args.artifact_dir / "preflight_decision.json"),
        "contracts": sha256_file(contracts_path),
        "selected": sha256_file(selected_path),
    }
    checkpoint_path = args.artifact_dir / "replay" / "checkpoint_index.json"
    checkpoint = _checkpoint_index(checkpoint_path)
    checkpoint_rows = dict(checkpoint["rows"])
    superseded_checkpoint_rows = list(checkpoint.get("superseded_rows", []))
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"identity_reconciliation_semantic_replay_{args.phase}",
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
            rows = []
            for position, state_id in enumerate(selected_ids, start=1):
                key = f"{args.phase}:repeat_{repeat_index}:{state_id}"
                base = _load_json(Path(str(contract_by_id[state_id]["contract_path"])))
                existing = checkpoint_rows.get(key)
                if existing is not None:
                    contract = _load_json(Path(str(existing["contract_path"])))
                    if not _checkpoint_contract_matches_base(contract, base):
                        superseded_checkpoint_rows.append(
                            {
                                "key": key,
                                "reason": "source_contract_hash_changed",
                                "row": dict(existing),
                            }
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
                attempt_root = args.artifact_dir / "replay" / "attempts" / _safe_name(args.attempt_id)
                contract_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "contracts"
                output_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "states"
                log_dir = attempt_root / args.phase / f"repeat_{repeat_index}" / "logs"
                for path in (contract_dir, output_dir, log_dir):
                    path.mkdir(parents=True, exist_ok=True)
                contract_path = contract_dir / f"{_safe_name(state_id)}.json"
                output_path = output_dir / f"{_safe_name(state_id)}.json"
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
                        "superseded_rows": superseded_checkpoint_rows,
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
            summary = summarize_semantic_replays(rows)
            summary["repeat_index"] = repeat_index
            all_repeat_rows.append(rows)
            repeat_summaries.append(summary)
            atomic_write_json(
                args.artifact_dir / "replay" / args.phase / f"repeat_{repeat_index}" / "summary.json",
                summary,
            )

        if args.phase == "affected":
            forensic = _load_json(args.artifact_dir / "affected_task_behavioral_provenance.json")
            classification = {
                str(row["task_id"]): str(row["classification_before_replay"])
                for row in forensic["rows"]
            }
            candidate = _load_json(args.artifact_dir / "candidate_repair_structural_validation.json")
            candidate_by_task = {
                str(row["task_id"]): row for row in candidate["validation_rows"]
            }
            rows_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in all_repeat_rows[0]:
                rows_by_task[str(row["task_id"])].append(row)
            task_results = {}
            remediation = {}
            for task_id in AFFECTED_TASK_IDS:
                summary = summarize_semantic_replays(rows_by_task[task_id])
                expected_task = settings["expected"]["affected_tasks"][task_id]
                gate = task_replay_gate(
                    summary,
                    expected_states=int(expected_task["decisions"]),
                    expected_prior_observations=int(expected_task["candidate_prior_observations"]),
                )
                record_checks = candidate_by_task[task_id]["record_validation"]["checks"]
                action = select_task_remediation(
                    classification=classification[task_id],
                    replay_gate_passed=bool(gate["passed"]),
                    actions_unchanged=bool(record_checks["actions_unchanged"]),
                    observations_unchanged=bool(record_checks["observations_unchanged"]),
                )
                remediation[task_id] = action
                task_results[task_id] = {
                    "classification": classification[task_id],
                    "summary": summary,
                    "gate": gate,
                    "remediation": action,
                }
            combined = {
                "format": "affected_task_candidate_semantic_replay_summary_7a_v1",
                "summary": repeat_summaries[0],
                "task_results": task_results,
            }
            policy = {
                "format": "identity_reconciliation_remediation_policy_7a_v1",
                "selection_timing": "before_any_downstream_metric_recomputation",
                "policy_rule": "repair_only_header_only_tasks_with_all_state_semantic_replay_and_unchanged_actions_observations",
                "task_remediations": remediation,
                "all_affected_tasks_replay_passed": all(
                    bool(row["gate"]["passed"]) for row in task_results.values()
                ),
                "structural_corpus_build_allowed": True,
                "downstream_performance_consulted": False,
            }
            atomic_write_json(args.artifact_dir / "affected_task_semantic_replay_summary.json", combined)
            atomic_write_json(args.artifact_dir / "remediation_policy_manifest.json", policy)
            output = args.artifact_dir / "remediation_policy_manifest.json"
        else:
            repeat_checks = []
            if args.phase == "sentinel":
                first = {row["state_example_id"]: row for row in all_repeat_rows[0]}
                second = {row["state_example_id"]: row for row in all_repeat_rows[1]}
                repeat_checks = [
                    _repeat_equivalence(first[state_id], second[state_id])
                    for state_id in sorted(first)
                ]
            gate = semantic_replay_gate(
                repeat_summaries,
                expected_states=int(selected["state_count"]),
                expected_tasks=int(selected["task_count"]),
                expected_prior_observations=int(selected["prior_observation_count"]),
                require_repeat_equivalence=args.phase == "sentinel",
                repeat_checks=repeat_checks,
            )
            combined = {
                "format": f"identity_reconciled_{args.phase}_semantic_replay_summary_7a_v1",
                "repeat_summaries": repeat_summaries,
                "repeat_checks": repeat_checks,
                "gate": gate,
            }
            output = args.artifact_dir / "replay" / f"reconciled_{args.phase}_summary.json"
            atomic_write_json(output, combined)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(_load_json(output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
