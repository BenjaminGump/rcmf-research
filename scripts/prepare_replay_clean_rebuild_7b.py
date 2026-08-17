from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_replay_clean_rebuild_7b import (
    ROOT_JWT_SCHEMA_SENTINEL_IDS,
    canonical_hash,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _write_once_or_validate(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if _load_json(path) != payload:
            raise ValueError(f"Immutable output differs: {path}")
        return
    atomic_write_json(path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path)
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
    artifact_dir = args.artifact_dir or Path(str(settings["artifact_dir"]))
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    parent = Path(str(settings["parent_exp025a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    required = {
        "parent_run_manifest": parent / "run_manifest.json",
        "parent_structural_finalization": parent / "structural_finalization_summary.json",
        "parent_replay_contracts": parent / "reconciled_replay_contract_manifest.json",
        "parent_sentinel": parent / "reconciled_sentinel_manifest.json",
        "parent_audit": parent / "reconciled_one_step_manifest.json",
        "corpus_summary": corpus / "summary.json",
        "corpus_structural_validation": corpus / "structural_validation.json",
        "corpus_memory_records": corpus / "memory_records.jsonl",
        "corpus_decision_examples": corpus / "decision_examples.jsonl",
        "corpus_transitions": corpus / "transition_manifest.jsonl",
    }
    missing = [f"{name}={path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing immutable inputs: " + ", ".join(missing))
    summary = _load_json(required["corpus_summary"])
    structural = _load_json(required["corpus_structural_validation"])
    expected = settings["expected"]
    corpus_checks = {
        "lineage": summary.get("lineage_sha256")
        == settings["expected_corpus_lineage_sha256"],
        "structural_validation": bool(structural.get("passed")),
        "train_tasks": int(summary["train_task_count"]) == int(expected["train_tasks"]),
        "validation_tasks": int(summary["validation_task_count"])
        == int(expected["validation_tasks"]),
        "decisions": int(summary["decision_count"])
        == int(expected["train_decisions"]) + int(expected["validation_decisions"]),
        "transitions": int(summary["transition_count"]) == int(expected["transitions"]),
    }
    if not all(corpus_checks.values()):
        raise RuntimeError(f"Reconciled corpus contract failed: {corpus_checks}")

    contracts = _load_json(required["parent_replay_contracts"])
    sentinel = _load_json(required["parent_sentinel"])
    audit = _load_json(required["parent_audit"])
    contract_ids = {str(row["state_example_id"]) for row in contracts["rows"]}
    schema_rows = [
        row
        for row in audit["rows"]
        if str(row["state_example_id"]) in set(ROOT_JWT_SCHEMA_SENTINEL_IDS)
    ]
    schema_ids = tuple(str(row["state_example_id"]) for row in schema_rows)
    if set(schema_ids) != set(ROOT_JWT_SCHEMA_SENTINEL_IDS) or any(
        state_id not in contract_ids for state_id in schema_ids
    ):
        raise ValueError("The root-JWT schema sentinel does not match the immutable audit")
    schema_manifest = {
        "format": "root_login_jwt_schema_extension_sentinel_7b_v1",
        "selection_rule": "exact_three_EXP025A_82e2fac_3_root_login_jwt_failure_states",
        "state_count": len(schema_rows),
        "task_count": len({str(row["task_id"]) for row in schema_rows}),
        "prior_observation_count": sum(int(row["step_id"]) - 1 for row in schema_rows),
        "rows": schema_rows,
    }
    schema_manifest["manifest_sha256"] = canonical_hash(schema_manifest)
    expected_schema = {
        "state_count": int(expected["root_jwt_sentinel_states"]),
        "task_count": int(expected["root_jwt_sentinel_tasks"]),
        "prior_observation_count": int(expected["root_jwt_sentinel_prior_observations"]),
    }
    if any(schema_manifest[key] != value for key, value in expected_schema.items()):
        raise ValueError("Root-JWT schema sentinel counts changed")

    input_hashes = {name: sha256_file(path) for name, path in required.items()}
    run_manifest = {
        "format": "replay_validated_clean_rebuild_run_manifest_7b_v1",
        "run_uuid": str(settings["run_uuid"]),
        "milestone": "7B",
        "experiment": "EXP-025B",
        "branch": str(settings["source"]["branch"]),
        "starting_head": str(settings["source"]["starting_head"]),
        "source_head": args.lambda_head,
        "parent_run_uuid": _load_json(required["parent_run_manifest"])["run_uuid"],
        "parent_artifact": str(parent),
        "corpus_path": str(corpus),
        "corpus_lineage_sha256": str(summary["lineage_sha256"]),
        "config_sha256": sha256_file(args.config),
        "input_hashes": input_hashes,
        "scientific_parameters_changed": False,
        "historical_artifacts_rewritten": False,
    }
    run_manifest["manifest_sha256"] = canonical_hash(run_manifest)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_once_or_validate(artifact_dir / "run_manifest.json", run_manifest)
    _write_once_or_validate(
        artifact_dir / "root_jwt_schema_extension_sentinel_manifest.json",
        schema_manifest,
    )

    with AttemptLedger(
        artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="preflight_and_replay_contract_freeze",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=input_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        preflight = {
            "format": "replay_validated_clean_rebuild_preflight_7b_v1",
            "passed": True,
            "corpus_checks": corpus_checks,
            "corpus_lineage_sha256": summary["lineage_sha256"],
            "sentinel": {
                "states": int(sentinel["state_count"]),
                "tasks": int(sentinel["task_count"]),
                "prior_observations": int(sentinel["prior_observation_count"]),
            },
            "root_jwt_schema_sentinel": expected_schema,
            "full": {
                "states": int(audit["state_count"]),
                "tasks": int(audit["task_count"]),
                "prior_observations": sum(int(row["step_id"]) - 1 for row in audit["rows"]),
            },
            "repeat_count": int(settings["replay"]["repeats"]),
            "historical_artifacts_rewritten": False,
            "qwen_work_allowed": False,
        }
        output = artifact_dir / "preflight_replay_report.json"
        atomic_write_json(output, preflight)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(preflight, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
