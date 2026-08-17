from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.clean_cache_rebuild_7b import (
    PREFLIGHT_VERSION,
    audit_jsonl_cache,
    canonical_json_sha256,
    source_identity_audit,
    transition_change_manifest,
    validate_unaffected_cache_rows,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# EXP-025B Incremental Clean-Cache Preflight",
        "",
        f"- Replay-validated lineage: `{report['replay_validated_lineage_sha256']}`",
        f"- Qwen scoring rows: **{report['totals']['affected_scoring_rows']:,}**",
        f"- State/memory/transition representations: **{report['totals']['state_representations']} / {report['totals']['memory_representations']} / {report['totals']['transition_representations']}**",
        f"- Projected H100 hours (best/expected/conservative): **{report['runtime']['h100_hours']['best']:.3f} / {report['runtime']['h100_hours']['expected']:.3f} / {report['runtime']['h100_hours']['conservative']:.3f}**",
        f"- Projected wall hours (best/expected/conservative): **{report['runtime']['wall_time_hours']['best']:.3f} / {report['runtime']['wall_time_hours']['expected']:.3f} / {report['runtime']['wall_time_hours']['conservative']:.3f}**",
        f"- Expected artifact size: **{report['runtime']['storage_bytes']:,} bytes**",
        f"- 12-H100-hour review required: **{report['runtime']['review_required']}**",
        "",
        "## Cache Rows",
        "",
        "| Cache | Total | Reusable | Recompute |",
        "|---|---:|---:|---:|",
    ]
    for name, row in report["caches"].items():
        lines.append(
            f"| {name} | {row['row_count']:,} | {row['reusable_row_count']:,} | {row['affected_row_count']:,} |"
        )
    lines.extend(
        [
            "",
            "## Resume Contract",
            "",
            "Each recomputed row is written atomically under its clean cache version. The immutable affected-key manifest is the sole work queue; completed rows are accepted only when their input/config/model hashes match. Aggregate JSONL/tensor caches are finalized atomically after exact key, count, leakage, truncation, and unchanged-row identity checks.",
            "",
            "No Qwen model or tokenizer was loaded by this preflight.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"))
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
    rebuild = settings["cache_rebuild"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    replay_manifest_path = args.artifact_dir / "replay_validated_corpus_manifest.json"
    replay = _load_json(replay_manifest_path)
    if not replay.get("replay_validated") or not replay.get("qwen_incremental_rebuild_allowed"):
        raise RuntimeError("Replay-validated data contract does not permit the incremental rebuild")
    clean_root = Path(str(settings["reconciled_corpus_dir"]))
    old_root = Path(str(rebuild["historical_source_data"]))
    old_examples = load_decision_examples(old_root / "decision_examples.jsonl")
    clean_examples = load_decision_examples(clean_root / "decision_examples.jsonl")
    old_records = load_memory_records(old_root / "memory_records.jsonl")
    clean_records = load_memory_records(clean_root / "memory_records.jsonl")
    old_transitions = list(read_jsonl(Path(str(rebuild["historical_caches"]["transition_manifest"]))))
    clean_transitions = list(read_jsonl(clean_root / "transition_manifest.jsonl"))
    clean_train_tasks = set(_load_json(clean_root / "train_validation_task_manifest.json")["train_task_ids"])
    clean_train_transitions = [row for row in clean_transitions if str(row["parent_task_id"]) in clean_train_tasks]
    identity = source_identity_audit(
        old_examples=old_examples,
        clean_examples=clean_examples,
        old_records=old_records,
        clean_records=clean_records,
    )
    transition_changes = transition_change_manifest(
        old_transitions=old_transitions, clean_transitions=clean_train_transitions
    )
    affected_memory_ids = {str(row["memory_id"]) for row in identity["changed_memories"]}
    affected_old_transition_ids = {str(row["old_transition_id"]) for row in transition_changes["mapping"]}
    cache_names = ("raw_text_teacher", "stage_c1_response", "pair_response_5d", "transition_teacher")
    audits = {
        name: audit_jsonl_cache(
            cache_name=name,
            path=Path(str(rebuild["historical_caches"][name])),
            affected_memory_ids=affected_memory_ids,
            affected_old_transition_ids=affected_old_transition_ids,
        )
        for name in cache_names
    }
    expected_totals = rebuild["expected_cache_rows"]
    expected_affected = rebuild["expected_affected_rows"]
    for name, audit in audits.items():
        if int(audit["row_count"]) != int(expected_totals[name]):
            raise ValueError(f"{name} row count differs from the preregistered contract")
        if int(audit["affected_row_count"]) != int(expected_affected[name]):
            raise ValueError(f"{name} affected count differs from the preregistered contract")
    validation = validate_unaffected_cache_rows(
        audits=audits,
        clean_examples=clean_examples,
        clean_records=clean_records,
        clean_transitions=clean_train_transitions,
    )
    affected_scoring_rows = sum(int(row["affected_row_count"]) for row in audits.values())
    if affected_scoring_rows != int(rebuild["expected_scoring_rows"]):
        raise ValueError("Total Qwen scoring rows differ from the preregistered contract")
    representation_counts = {
        "state_representations": int(identity["changed_decision_count"]),
        "memory_representations": int(identity["changed_memory_count"]),
        "transition_representations": int(transition_changes["changed_transition_count"]),
    }
    for key in representation_counts:
        expected_key = "expected_" + key
        if representation_counts[key] != int(rebuild[expected_key]):
            raise ValueError(f"{key} count differs from the preregistered contract")
    runtime = {
        "h100_hours": dict(rebuild["projected_h100_hours"]),
        "wall_time_hours": dict(rebuild["projected_wall_time_hours"]),
        "storage_bytes": int(rebuild["projected_storage_bytes"]),
        "review_threshold_h100_hours": float(rebuild["review_threshold_h100_hours"]),
    }
    runtime["review_required"] = float(runtime["h100_hours"]["expected"]) > float(runtime["review_threshold_h100_hours"])
    preflight_dir = Path(str(rebuild["output_root"])) / "preflight"
    input_hashes = {
        "replay_validated_manifest": sha256_file(replay_manifest_path),
        "dependency_graph": sha256_file(Path(str(rebuild["dependency_graph"]))),
        "minimum_recompute_estimate": sha256_file(Path(str(rebuild["minimum_recompute_estimate"]))),
        "clean_decisions": sha256_file(clean_root / "decision_examples.jsonl"),
        "clean_memories": sha256_file(clean_root / "memory_records.jsonl"),
        "clean_transitions": sha256_file(clean_root / "transition_manifest.jsonl"),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="exact_incremental_clean_cache_preflight",
        command=[str(value) for value in sys.argv], local_head=args.local_head,
        github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=sha256_file(args.config),
        data_manifest_hashes=input_hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        preflight_dir.mkdir(parents=True, exist_ok=True)
        affected_manifest = {
            "format": "identity_reconciled_affected_cache_keys_7b_v1",
            "replay_validated_lineage_sha256": replay["lineage_sha256"],
            "affected_memory_ids": sorted(affected_memory_ids),
            "transition_changes": transition_changes,
            "caches": audits,
        }
        affected_manifest["manifest_sha256"] = canonical_json_sha256(affected_manifest)
        atomic_write_json(preflight_dir / "affected_cache_keys.json", affected_manifest)
        atomic_write_json(preflight_dir / "source_identity_audit.json", identity)
        report = {
            "format": PREFLIGHT_VERSION,
            "source_head": args.lambda_head,
            "replay_validated_lineage_sha256": replay["lineage_sha256"],
            "structural_corpus_lineage_sha256": replay["structural_corpus_lineage_sha256"],
            "input_hashes": input_hashes,
            "source_identity": identity,
            "transition_changes": transition_changes,
            "caches": {
                name: {key: value for key, value in audit.items() if key != "affected_rows"}
                for name, audit in audits.items()
            },
            "unchanged_row_validation": validation,
            "totals": {"affected_scoring_rows": affected_scoring_rows, **representation_counts},
            "runtime": runtime,
            "resume_plan": {
                "work_queue": str(preflight_dir / "affected_cache_keys.json"),
                "row_outputs": "atomic one-row JSON/PT checkpoints with exact input/model/config hashes",
                "resume_rule": "accept a completed row only after key and all recorded hashes match",
                "aggregate_finalization": "atomic after exact count, duplicate, leakage, truncation, and unchanged-row identity validation",
                "duplicate_run_prevention": "one run UUID and append-only attempt ledger",
            },
            "qwen_loaded": False,
            "passed": True,
        }
        report["preflight_sha256"] = canonical_json_sha256(report)
        atomic_write_json(preflight_dir / "incremental_cache_preflight.json", report)
        atomic_write_text(preflight_dir / "incremental_cache_preflight.md", _markdown(report))
        attempt.progress(latest_validated_checkpoint=str(preflight_dir / "incremental_cache_preflight.json"))
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
