from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7a"]
    required = [
        "run_manifest.json", "attempts.jsonl", "corpus_builder_root_cause.json",
        "affected_task_behavioral_provenance.json", "affected_task_semantic_replay_summary.json",
        "remediation_policy_manifest.json", "structural_finalization_summary.json",
        "artifact_dependency_graph.json", "minimum_recompute_estimate.json",
        "contaminated_checkpoint_sensitivity.json", "reconciled_one_step_manifest.json",
        "reconciled_sentinel_manifest.json", "reconciled_replay_contract_manifest.json",
        "replay/reconciled_sentinel_summary.json", "replay/reconciled_full_summary.json",
        "final_exp025a_summary.json",
    ]
    errors = []
    hashes = {}
    for name in required:
        path = args.artifact_dir / name
        if not path.is_file():
            errors.append(f"missing:{name}")
        else:
            hashes[name] = sha256_file(path)
    if errors:
        raise SystemExit("; ".join(errors))
    attempts = list(read_jsonl(args.artifact_dir / "attempts.jsonl"))
    if {str(row["run_uuid"]) for row in attempts} != {str(settings["run_uuid"])}:
        errors.append("run_uuid_mismatch")
    started = {str(row["attempt_id"]) for row in attempts if row.get("event") == "start"}
    ended = {
        str(row["attempt_id"])
        for row in attempts
        if row.get("event") == "end" and int(row.get("exit_code", 1)) == 0
    }
    if started != ended:
        errors.append("unfinished_or_failed_attempt")
    for attempt_id in started:
        events = [str(row.get("event")) for row in attempts if str(row["attempt_id"]) == attempt_id]
        if events.count("start") != 1 or events.count("end") != 1:
            errors.append(f"attempt_event_count:{attempt_id}")
    final = _load(args.artifact_dir / "final_exp025a_summary.json")
    structural = _load(args.artifact_dir / "structural_finalization_summary.json")
    sentinel = _load(args.artifact_dir / "replay/reconciled_sentinel_summary.json")
    full = _load(args.artifact_dir / "replay/reconciled_full_summary.json")
    if not bool(final["clean_corpus_ready"]):
        errors.append("clean_corpus_not_ready")
    if not bool(structural["structural_validation"]["passed"]):
        errors.append("structural_validation_failed")
    if not bool(sentinel["gate"]["passed"] and full["gate"]["passed"]):
        errors.append("semantic_replay_failed")
    if int(final["qwen_import_forward_representation_count"]) != 0:
        errors.append("qwen_scope_violation")
    if float(final["h100_hours"]) != 0.0 or int(final["model_training_count"]) != 0:
        errors.append("compute_or_training_scope_violation")
    corpus_dir = Path(settings["reconciled_corpus_dir"])
    corpus_files = [
        "memory_records.jsonl", "decision_examples.jsonl", "transition_manifest.jsonl",
        "train_validation_task_manifest.json", "memory_record_manifest.json",
        "decision_example_manifest.json", "leakage_lineage_manifest.json",
        "structural_validation.json", "summary.json",
    ]
    for name in corpus_files:
        path = corpus_dir / name
        if not path.is_file():
            errors.append(f"missing_corpus:{name}")
    result = {
        "format": "appworld_identity_reconciliation_postrun_validation_7a_v1",
        "passed": not errors, "error_count": len(errors), "errors": errors,
        "artifact_hashes": hashes, "attempt_count": len(attempts),
        "corpus_dir": str(corpus_dir),
    }
    atomic_write_json(args.artifact_dir / "postrun_validation.json", result)
    if errors:
        raise SystemExit(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
