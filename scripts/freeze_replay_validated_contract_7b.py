from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_replay_clean_rebuild_7b import canonical_hash
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_replay_clean_rebuild_7b import CHECKPOINT_VERSION


REPLAY_VALIDATED_CORPUS_VERSION = "appworld_identity_reconciled_replay_validated_v1"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _attempt_lifecycle(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        by_id.setdefault(str(row["attempt_id"]), []).append(row)
    errors = []
    for attempt_id, rows in by_id.items():
        starts = [row for row in rows if row.get("event") == "start"]
        ends = [row for row in rows if row.get("event") == "end"]
        if len(starts) != 1 or len(ends) != 1 or int(ends[0].get("exit_code", 1)) != 0:
            errors.append(attempt_id)
    return {
        "attempt_count": len(by_id),
        "open_or_failed_attempt_ids": sorted(errors),
        "passed": not errors,
    }


def build_replay_validated_manifest(
    *,
    settings: dict[str, Any],
    artifact_dir: Path,
    source_head: str,
) -> dict[str, Any]:
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    corpus_summary = _load_json(corpus / "summary.json")
    structural = _load_json(corpus / "structural_validation.json")
    replay_paths = {
        "sentinel": artifact_dir / "replay" / "sentinel_summary.json",
        "root_jwt_schema_sentinel": artifact_dir / "replay" / "root_jwt_summary.json",
        "full": artifact_dir / "replay" / "full_summary.json",
    }
    summaries = {name: _load_json(path) for name, path in replay_paths.items()}
    if not all(bool(summary["gate"]["passed"]) for summary in summaries.values()):
        raise RuntimeError("Cannot freeze a replay-validated corpus before every replay gate passes")
    checkpoint_path = artifact_dir / "replay" / "checkpoint_index.json"
    checkpoint = _load_json(checkpoint_path)
    if checkpoint.get("format") != CHECKPOINT_VERSION:
        raise ValueError("Unexpected replay checkpoint index format")
    expected_checkpoint_rows = 13 * 2 + 3 * 2 + 45 * 2
    if len(checkpoint["rows"]) != expected_checkpoint_rows:
        raise ValueError("Replay checkpoint row count differs from the fixed phase contract")

    output_hashes = {
        name: sha256_file(path)
        for name, path in {
            "corpus_summary": corpus / "summary.json",
            "corpus_structural_validation": corpus / "structural_validation.json",
            "corpus_memory_records": corpus / "memory_records.jsonl",
            "corpus_decision_examples": corpus / "decision_examples.jsonl",
            "corpus_transitions": corpus / "transition_manifest.jsonl",
            "sentinel_summary": replay_paths["sentinel"],
            "root_jwt_summary": replay_paths["root_jwt_schema_sentinel"],
            "full_summary": replay_paths["full"],
            "replay_checkpoint_index": checkpoint_path,
        }.items()
    }
    full_repeats = summaries["full"]["repeat_summaries"]
    manifest = {
        "format": REPLAY_VALIDATED_CORPUS_VERSION,
        "run_uuid": str(settings["run_uuid"]),
        "source_head": source_head,
        "structural_corpus_path": str(corpus),
        "structural_corpus_lineage_sha256": corpus_summary["lineage_sha256"],
        "semantic_normalization_version": "appworld_observation_semantic_normalization_7b_v1",
        "appworld_capsule": {
            "python": str(settings["legacy"]["executable"]),
            "root": str(settings["legacy"]["appworld_root"]),
            "package_code_data_evaluation_versions": ["0.1.0", "0.1.0", "0.1.0"],
        },
        "counts": {
            "train_tasks": int(corpus_summary["train_task_count"]),
            "validation_tasks": int(corpus_summary["validation_task_count"]),
            "decisions": int(corpus_summary["decision_count"]),
            "transitions": int(corpus_summary["transition_count"]),
            "audit_states": 45,
            "audit_tasks": 9,
            "audit_prior_observations": 372,
            "full_repeats": 2,
            "checkpoint_rows": len(checkpoint["rows"]),
        },
        "full_replay": {
            "repeat_count": len(full_repeats),
            "identity": [int(row["identity_match_count"]) for row in full_repeats],
            "history_v3": [
                int(row["complete_history_v3_match_count"]) for row in full_repeats
            ],
            "prior_v3": [int(row["prior_v3_match_count"]) for row in full_repeats],
            "target_v3": [int(row["target_v3_match_count"]) for row in full_repeats],
            "complete_v3": [
                int(row["complete_v3_replay_count"]) for row in full_repeats
            ],
            "root_jwt_extensions": [
                int(row["root_jwt_extension_count"]) for row in full_repeats
            ],
            "exceptions": [int(row["exception_count"]) for row in full_repeats],
            "non_temporal_root_jwt_mismatches": [
                int(row["non_temporal_root_jwt_mismatch_count"])
                for row in full_repeats
            ],
            "repeat_equivalence": bool(summaries["full"]["gate"]["repeat_equivalence"]),
        },
        "repaired_tasks": ["b0a8eae_2", "b0a8eae_3"],
        "actions_and_observations_unchanged": True,
        "historical_artifacts_rewritten": False,
        "replay_validated": bool(
            structural["passed"]
            and corpus_summary["lineage_sha256"]
            == settings["expected_corpus_lineage_sha256"]
            and all(bool(summary["gate"]["passed"]) for summary in summaries.values())
        ),
        "decision": "identity_reconciled_replay_validated",
        "qwen_incremental_rebuild_allowed": True,
        "model_training_allowed": False,
        "output_hashes": output_hashes,
    }
    manifest["lineage_sha256"] = canonical_hash(manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
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
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    attempts_before = list(read_jsonl(args.artifact_dir / "attempts.jsonl"))
    lifecycle = _attempt_lifecycle(attempts_before)
    if not lifecycle["passed"]:
        raise RuntimeError(f"Prior attempt lifecycle is not clean: {lifecycle}")
    input_hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "sentinel_summary": sha256_file(args.artifact_dir / "replay" / "sentinel_summary.json"),
        "root_jwt_summary": sha256_file(args.artifact_dir / "replay" / "root_jwt_summary.json"),
        "full_summary": sha256_file(args.artifact_dir / "replay" / "full_summary.json"),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="freeze_replay_validated_clean_data_contract",
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
        manifest = build_replay_validated_manifest(
            settings=settings,
            artifact_dir=args.artifact_dir,
            source_head=args.lambda_head,
        )
        output = args.artifact_dir / "replay_validated_corpus_manifest.json"
        atomic_write_json(output, manifest)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
