from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_status(path: Path) -> tuple[list[str], bool]:
    rows = list(read_jsonl(path))
    counts = Counter((str(row["attempt_id"]), str(row["event"])) for row in rows)
    attempt_ids = sorted({str(row["attempt_id"]) for row in rows})
    complete = bool(attempt_ids) and all(
        counts[(attempt_id, "start")] == 1
        and counts[(attempt_id, "end")] == 1
        for attempt_id in attempt_ids
    )
    return attempt_ids, complete


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_6h3"]
    expected = settings["expected"]
    base_required = [
        "run_manifest.json",
        "attempts.jsonl",
        "corpus_official_identity_probe.json",
        "corpus_identity_consistency.json",
        "decision_example_identity_rows.jsonl",
        "b0a8eae_2_forensic_provenance.json",
        "bounded_snapshot_search.json",
        "training_contamination_audit.json",
        "preflight_decision.json",
        "corpus_identity_consistency_report.md",
        "b0a8eae_2_forensic_provenance_report.md",
        "bounded_snapshot_search_report.md",
        "task_quarantine_report.md",
        "repeated_sentinel_report.md",
        "provenance_valid_semantic_replay_report.md",
        "prior_result_sensitivity_report.md",
        "future_behavioral_audit_contract.md",
        "final_exp024r3_summary.json",
        "final_exp024r3_report.md",
    ]
    missing = [
        name for name in base_required if not (args.artifact_dir / name).exists()
    ]
    if missing:
        raise FileNotFoundError(f"EXP-024R3 required artifacts missing: {missing}")

    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    corpus = _load_json(args.artifact_dir / "corpus_identity_consistency.json")
    decision_rows = list(read_jsonl(args.artifact_dir / "decision_example_identity_rows.jsonl"))
    forensic = _load_json(args.artifact_dir / "b0a8eae_2_forensic_provenance.json")
    search = _load_json(args.artifact_dir / "bounded_snapshot_search.json")
    contamination = _load_json(args.artifact_dir / "training_contamination_audit.json")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    final = _load_json(args.artifact_dir / "final_exp024r3_summary.json")
    attempt_ids, attempts_complete = _attempt_status(args.artifact_dir / "attempts.jsonl")
    branch = str(final["decision_branch"])
    common_checks = {
        "run_uuid": run_manifest["run_uuid"] == settings["run_uuid"],
        "attempt_ledger_complete": attempts_complete,
        "memory_records_46": int(corpus["memory_record_count"]) == int(expected["memory_records"]),
        "decision_examples_638": int(corpus["decision_example_count"]) == int(expected["decision_examples"]),
        "decision_rows_complete": len(decision_rows) == int(expected["decision_examples"]),
        "all_decisions_match_parent_query": all(bool(row["decision_matches_raw_trajectory"]) for row in decision_rows),
        "all_task_source_layers_agree": all(bool(row["source_layers_agree"]) for row in corpus["rows"]),
        "preregistered_task_mismatched": str(expected["quarantined_task_id"]) in set(corpus["identity_mismatch_task_ids"]),
        "official_backup_agree": bool(forensic["official_and_backup_agree"]),
        "snapshot_search_complete": bool(search["search_complete"]),
        "snapshot_coherence_explicit": "matching_identity_snapshot_coherence" in search,
        "contamination_audits_all_mismatches": set(contamination.get("mismatch_task_audits", {})) == set(corpus["identity_mismatch_task_ids"]),
        "preflight_final_branch_compatible": (
            str(preflight["decision_branch"]) == branch
            or (
                str(preflight["decision_branch"])
                == "provenance_valid_task_quarantine_ready"
                and branch
                == "provenance_valid_subset_semantic_replay_validated"
            )
            or (
                str(preflight["decision_branch"])
                == "exact_historical_snapshot_found_pending_replay"
                and branch == "exact_historical_snapshot_replay_validated"
            )
        ),
        "generation_blocked": bool(final["generation_remains_blocked_in_this_milestone"]),
        "qwen_zero": int(final["qwen_import_forward_generation_count"]) == 0,
        "memory_conditions_zero": int(final["memory_condition_execution_count"]) == 0,
        "training_zero": int(final["model_training_count"]) == 0,
        "scientific_parameters_unchanged": not bool(final["scientific_parameter_changed"]),
    }
    branch_required: list[str] = []
    if branch == "source_dataset_identity_consistency_failure":
        forbidden = [
            "provenance_valid_one_step_manifest_v1.json",
            "provenance_valid_sentinel_manifest.json",
            "provenance_valid_replay_contract_manifest.json",
            "replay/provenance_valid_sentinel_summary.json",
            "replay/provenance_valid_full_summary.json",
            "prior_result_quarantine_sensitivity.json",
        ]
        branch_checks = {
            "multiple_identity_mismatches": int(corpus["identity_mismatch_count"]) > 1,
            "mismatches_are_field_based": all(bool(row["mismatched_fields"]) for row in corpus["rows"] if not bool(row["identity_match"])),
            "preflight_hard_stop": str(preflight["decision_branch"]) == branch and not bool(preflight["replay_allowed"]),
            "no_quarantine_or_replay_artifacts": not any((args.artifact_dir / name).exists() for name in forbidden),
            "sentinel_not_run": final["sentinel"] == "not_run",
            "full_replay_not_run": final["full_replay"] == "not_run",
            "semantic_replay_not_validated": not bool(final["semantic_replay_validated"]),
            "sensitivity_hard_stop_recorded": final["sensitivity_status"] == "not_run_source_dataset_identity_consistency_failure",
            "original_45_unresolved": not bool(final["original_45_replay_resolved"]),
            "provenance_valid_40_not_claimed": not bool(final["provenance_valid_40_replay_validated"]),
        }
        sentinel_states = 0
        sentinel_priors = 0
        full_states = 0
        full_priors = 0
    else:
        branch_required = [
            "provenance_valid_one_step_manifest_v1.json",
            "provenance_valid_sentinel_manifest.json",
            "provenance_valid_replay_contract_manifest.json",
            "replay/checkpoint_index.json",
            "replay/provenance_valid_sentinel_summary.json",
            "replay/provenance_valid_full_summary.json",
            "prior_result_quarantine_sensitivity.json",
        ]
        branch_missing = [
            name for name in branch_required if not (args.artifact_dir / name).exists()
        ]
        if branch_missing:
            raise FileNotFoundError(
                f"EXP-024R3 branch artifacts missing: {branch_missing}"
            )
        quarantine = _load_json(args.artifact_dir / branch_required[0])
        sentinel_manifest = _load_json(args.artifact_dir / branch_required[1])
        contracts = _load_json(args.artifact_dir / branch_required[2])
        checkpoint = _load_json(args.artifact_dir / branch_required[3])
        sentinel = _load_json(args.artifact_dir / branch_required[4])
        full = _load_json(args.artifact_dir / branch_required[5])
        sensitivity = _load_json(args.artifact_dir / branch_required[6])
        sentinel_states = int(sentinel_manifest["state_count"])
        sentinel_priors = int(sentinel_manifest["prior_observation_count"])
        full_states = int(quarantine["retained_state_count"])
        full_priors = int(quarantine["retained_prior_observation_count"])
        phase_counts = Counter(
            str(row["phase"]) for row in checkpoint["rows"].values()
        )
        expected_sentinel_checkpoints = sentinel_states * int(
            settings["replay"]["sentinel_repeats"]
        )
        branch_checks = {
            "single_mismatch_task": corpus["identity_mismatch_task_ids"] == [str(expected["quarantined_task_id"])],
            "heldout_only": bool(contamination["heldout_only"]) and not bool(contamination["contaminates_training"]),
            "quarantine_entire_task": int(quarantine["quarantined_state_count"]) == int(expected["quarantined_state_count"]),
            "quarantine_40_states": full_states == int(expected["provenance_valid_states"]),
            "quarantine_8_tasks": int(quarantine["retained_task_count"]) == int(expected["provenance_valid_tasks"]),
            "contract_count_40": int(contracts["row_count"]) == int(expected["provenance_valid_states"]),
            "sentinel_gate": bool(sentinel["gate"]["passed"]),
            "full_gate": bool(full["gate"]["passed"]),
            "checkpoint_sentinel_count": phase_counts["sentinel"] == expected_sentinel_checkpoints,
            "checkpoint_full_count": phase_counts["full"] == full_states,
            "sensitivity_no_retraining": int(sensitivity["model_training_count"]) == 0,
        }
    checks = {**common_checks, **branch_checks}
    if not all(checks.values()):
        raise ValueError(f"EXP-024R3 postrun validation failed: {checks}")
    payload = {
        "format": "appworld_provenance_replay_postrun_validation_6h3_v1",
        "passed": True,
        "checks": checks,
        "attempt_ids": attempt_ids,
        "decision_branch": branch,
        "sentinel_state_count": sentinel_states,
        "sentinel_prior_observation_count": sentinel_priors,
        "full_state_count": full_states,
        "full_prior_observation_count": full_priors,
        "artifact_hashes": {
            name: sha256_file(args.artifact_dir / name)
            for name in base_required + branch_required
            if name != "attempts.jsonl"
        },
    }
    atomic_write_json(args.artifact_dir / "postrun_validation.json", payload)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-024R3 Postrun Validation\n\n"
        f"All `{len(checks)}` checks passed. Decision: `{final['decision_branch']}`.\n",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
