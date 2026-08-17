from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _ratio(value: int, total: int) -> str:
    return f"{value}/{total} ({100.0 * value / total:.2f}%)" if total else "0/0"


def _replay_table(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "| Measure | Match |",
            "| --- | ---: |",
            f"| Identity | {_ratio(int(summary['identity_match_count']), int(summary['state_count']))} |",
            f"| Complete histories (semantic v2) | {_ratio(int(summary['complete_history_semantic_match_count']), int(summary['state_count']))} |",
            f"| Prior observations (raw) | {_ratio(int(summary['prior_raw_match_count']), int(summary['prior_observation_count']))} |",
            f"| Prior observations (v1) | {_ratio(int(summary['prior_v1_match_count']), int(summary['prior_observation_count']))} |",
            f"| Prior observations (semantic v2) | {_ratio(int(summary['prior_semantic_match_count']), int(summary['prior_observation_count']))} |",
            f"| Targets (raw) | {_ratio(int(summary['target_raw_match_count']), int(summary['state_count']))} |",
            f"| Targets (v1) | {_ratio(int(summary['target_v1_match_count']), int(summary['state_count']))} |",
            f"| Targets (semantic v2) | {_ratio(int(summary['target_semantic_match_count']), int(summary['state_count']))} |",
            f"| Complete semantic replay | {_ratio(int(summary['complete_semantic_replay_count']), int(summary['state_count']))} |",
            f"| Temporal-only JWT differences | {int(summary['temporal_only_jwt_count'])} |",
            f"| Non-temporal JWT mismatches | {int(summary['non_temporal_jwt_mismatch_count'])} |",
            f"| Non-token mismatches | {int(summary['non_token_mismatch_count'])} |",
            f"| Exceptions | {int(summary['exception_count'])} |",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
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
    settings = load_config(args.config).raw["stage_c_6h3"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    required = {
        "run_manifest": args.artifact_dir / "run_manifest.json",
        "corpus": args.artifact_dir / "corpus_identity_consistency.json",
        "decisions": args.artifact_dir / "decision_example_identity_rows.jsonl",
        "forensic": args.artifact_dir / "b0a8eae_2_forensic_provenance.json",
        "search": args.artifact_dir / "bounded_snapshot_search.json",
        "contamination": args.artifact_dir / "training_contamination_audit.json",
        "preflight": args.artifact_dir / "preflight_decision.json",
    }
    for name, path in required.items():
        if not path.exists():
            raise FileNotFoundError(f"Analysis prerequisite missing: {name}={path}")
    config_hash = sha256_file(args.config)
    data_hashes = {name: sha256_file(path) for name, path in required.items()}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="provenance_replay_analysis",
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
        corpus = _load_json(required["corpus"])
        forensic = _load_json(required["forensic"])
        search = _load_json(required["search"])
        contamination = _load_json(required["contamination"])
        preflight = _load_json(required["preflight"])
        preflight_branch = str(preflight["decision_branch"])
        sensitivity_path = args.artifact_dir / "prior_result_quarantine_sensitivity.json"
        if preflight_branch == "source_dataset_identity_consistency_failure":
            sensitivity = {
                "status": "not_run_source_dataset_identity_consistency_failure",
                "model_training_count": 0,
                "qwen_import_forward_generation_count": 0,
                "qualitative_conclusion": {
                    "any_prior_gate_point_status_changed": None,
                    "exp022_fixed_panel_coverage_flip_only": None,
                    "overall_research_conclusion_changed": None,
                    "original_metrics_replaced": False,
                    "interpretation": (
                        "Not recomputed: more than one source task has an unresolved "
                        "identity inconsistency, so a b0a8eae_2-only sensitivity analysis "
                        "would not define a provenance-valid corpus. Original metrics and "
                        "branches remain immutable; their provenance scope requires review."
                    ),
                },
            }
        else:
            if not sensitivity_path.exists():
                raise FileNotFoundError(
                    f"Analysis prerequisite missing: sensitivity={sensitivity_path}"
                )
            sensitivity = _load_json(sensitivity_path)
        quarantine_path = args.artifact_dir / "provenance_valid_one_step_manifest_v1.json"
        sentinel_manifest_path = args.artifact_dir / "provenance_valid_sentinel_manifest.json"
        sentinel_path = args.artifact_dir / "replay" / "provenance_valid_sentinel_summary.json"
        full_path = args.artifact_dir / "replay" / "provenance_valid_full_summary.json"
        quarantine = _load_json(quarantine_path) if quarantine_path.exists() else None
        sentinel_manifest = _load_json(sentinel_manifest_path) if sentinel_manifest_path.exists() else None
        sentinel = _load_json(sentinel_path) if sentinel_path.exists() else None
        full = _load_json(full_path) if full_path.exists() else None

        if full is not None:
            branch = str(full["decision"]["decision_branch"])
        elif sentinel is not None and not bool(sentinel["decision"]["sentinel_gate_passed"]):
            branch = str(sentinel["decision"]["decision_branch"])
        else:
            branch = str(preflight["decision_branch"])
        semantic_validated = branch in {
            "exact_historical_snapshot_replay_validated",
            "provenance_valid_subset_semantic_replay_validated",
        }

        corpus_report = "\n".join(
            [
                "# Corpus Identity Consistency Audit",
                "",
                f"- Successful task trajectories: `{corpus['memory_record_count']}`",
                f"- Decision examples: `{corpus['decision_example_count']}`",
                f"- Decision examples accounted for: `{corpus['decision_examples_accounted_for']}`",
                f"- EXP-017 transition parents: `{corpus['transition_parent_count']}`",
                f"- EXP-020 query states: `{corpus['exp020_query_state_count']}`",
                f"- EXP-024A audit states: `{corpus['exp024a_audit_state_count']}`",
                f"- Identity-mismatched tasks: `{corpus['identity_mismatch_count']}`: `{corpus['identity_mismatch_task_ids']}`",
                f"- Mismatched fields: `{corpus['mismatch_field_counts']}`",
                f"- Source files available at recorded paths: `{sum(bool(row.get('source_file_available')) for row in corpus['rows'])}/{corpus['task_count']}`",
                "",
                "All identity values in Git-safe outputs are represented by hashes. The 638-row decision identity ledger is complete and preserves source-line identities without exposing synthetic credentials.",
            ]
        )
        forensic_report = "\n".join(
            [
                "# b0a8eae_2 Forensic Provenance",
                "",
                f"- Failure classification: `{forensic['failure_classification']}`",
                f"- Source layers agree: `{forensic['source_layers_agree']}`",
                f"- Official capsule and immutable backup agree: `{forensic['official_and_backup_agree']}`",
                f"- Mismatched fields: `{forensic['mismatched_fields']}`",
                f"- Source-identity behavioral references: `{forensic['trajectory_identity_evidence']['source_identity_evidence_count']}`",
                f"- Official-identity behavioral references: `{forensic['trajectory_identity_evidence']['official_identity_evidence_count']}`",
                f"- Mixed-identity steps: `{forensic['trajectory_identity_evidence']['mixed_identity_step_count']}`",
                f"- Exact historical snapshot found: `{forensic['exact_snapshot_found']}`",
                f"- Snapshot search result: `{forensic['snapshot_search_result']}`",
            ]
        )
        search_report = "\n".join(
            [
                "# Bounded Historical Snapshot Search",
                "",
                f"- Result: `{search['search_result']}`",
                f"- Search complete: `{search['search_complete']}`",
                f"- Enumerated sources: `{search['enumerated_sources']}`",
                f"- Exact same-task snapshots: `{search['exact_task_snapshot_count']}`",
                f"- Other official-task identity matches: `{search['other_task_identity_match_count']}`",
                f"- Other source-corpus task identity matches: `{search['source_corpus_other_task_identity_match_count']}`",
                f"- Git-LFS objects searched: `{search['git_lfs']['searched_object_count']}`",
                f"- Transfer artifacts inspected: `{search['transfer_bundle_inventory']['artifact_count']}`",
                "",
                "The search was bounded to predeclared immutable local and Persistent Filesystem sources. No external replacement data was downloaded or invented.",
            ]
        )
        quarantine_report = "\n".join(
            [
                "# Whole-Task Provenance Quarantine",
                "",
                f"- Training contamination: `{contamination['contaminates_training']}`",
                f"- Held-out only: `{contamination['heldout_only']}`",
                f"- Stage-B split: `{contamination['stage_b_split']}`",
                f"- All mismatched task audits: `{contamination.get('mismatch_task_audits', {})}`",
                f"- Any mismatched task contaminates training: `{contamination.get('any_mismatch_task_contaminates_training')}`",
                f"- Quarantined task: `{settings['expected']['quarantined_task_id']}`",
                f"- Quarantined audit states: `{quarantine['quarantined_state_count'] if quarantine else 'not applicable'}`",
                f"- Retained states/tasks: `{quarantine['retained_state_count'] if quarantine else 'not applicable'}/{quarantine['retained_task_count'] if quarantine else 'not applicable'}`",
                f"- Replacement states: `{quarantine['replacement_state_count'] if quarantine else 'not applicable'}`",
                "",
                "The original 45-state manifest remains immutable and unresolved. The reduced manifest is a new provenance-valid scope, not a retroactive repair.",
            ]
        )
        if sentinel is None:
            sentinel_report = f"# Repeated Provenance-Valid Sentinel\n\nNot run. Branch: `{branch}`.\n"
        else:
            lines = ["# Repeated Provenance-Valid Sentinel", ""]
            for repeat in sentinel["repeat_summaries"]:
                lines.extend(
                    [
                        f"## Repeat {repeat['repeat_index']}",
                        "",
                        _replay_table(repeat),
                        "",
                    ]
                )
            lines.append(
                f"Repeat-to-repeat semantic equality: `{sentinel['repeat_semantic_match_count'] if 'repeat_semantic_match_count' in sentinel else sum(bool(row['semantic_repeat_match']) for row in sentinel['repeat_checks'])}/{len(sentinel['repeat_checks'])}`."
            )
            lines.append(f"Gate passed: `{sentinel['gate']['passed']}`.")
            sentinel_report = "\n".join(lines)
        if full is None:
            replay_report = f"# Provenance-Valid Semantic Replay\n\nNot run. Branch: `{branch}`.\n"
        else:
            replay_report = "\n".join(
                [
                    "# Provenance-Valid Semantic Replay",
                    "",
                    _replay_table(full["summary"]),
                    "",
                    f"Decision: `{full['decision']['decision_branch']}`.",
                    "The original 45-state replay is not retroactively passed.",
                ]
            )
        sensitivity_report = "\n".join(
            [
                "# Prior-Result Provenance-Quarantine Sensitivity",
                "",
                f"- Existing predictions only; retraining count: `{sensitivity['model_training_count']}`",
                f"- Any historical point-gate status change: `{sensitivity['qualitative_conclusion']['any_prior_gate_point_status_changed']}`",
                f"- EXP-022 fixed-panel coverage flip only: `{sensitivity['qualitative_conclusion']['exp022_fixed_panel_coverage_flip_only']}`",
                f"- Overall research conclusion changed: `{sensitivity['qualitative_conclusion']['overall_research_conclusion_changed']}`",
                f"- Interpretation: {sensitivity['qualitative_conclusion']['interpretation']}",
            ]
        )
        if branch == "source_dataset_identity_consistency_failure":
            future_contract = """# Future Behavioral Audit Contract

No EXP-024A-Q behavioral audit is preregistered from this branch. Multiple source tasks have unresolved identity inconsistencies, so the corpus-wide provenance scope must be resolved before a valid quarantine set can be defined.

- Do not run Qwen generation or memory conditions.
- Audit every mismatched task and determine train-side contamination first.
- Preserve the original 45-state manifest and all failed replay artifacts.
- Define a new behavioral contract only after a separately reviewed corpus-level provenance decision.
"""
        else:
            future_contract = """# Future EXP-024A-Q Behavioral Audit Contract

This contract is preregistered but not executed in EXP-024R3. It applies only if the provenance-valid 40-state replay passes.

- Use exactly the 40 retained states and eight tasks in `provenance_valid_one_step_manifest_v1`.
- Preserve the immutable EXP-024A conditions for those states; add no replacement task or state.
- Preserve signature classes, controls, deterministic generation, and one-step execution semantics.
- Report all claims over eight tasks, require positive relative behavior on at least 6/8 tasks, and use task-grouped confidence intervals.
- State the exclusion and provenance limitation explicitly; make no claim about `b0a8eae_2`.
- Qwen generation remains blocked until this continuation is separately reviewed.
"""
        final_summary = {
            "format": "appworld_provenance_replay_final_summary_6h3_v1",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "parent_exp024r2": settings["parent_exp024r2"],
            "decision_branch": branch,
            "corpus_identity_mismatch_count": corpus["identity_mismatch_count"],
            "corpus_identity_mismatch_task_ids": corpus["identity_mismatch_task_ids"],
            "failure_classification": forensic["failure_classification"],
            "snapshot_search_result": search["search_result"],
            "exact_snapshot_found": search["exact_historical_snapshot_found"],
            "training_contaminated": contamination["contaminates_training"],
            "mismatch_task_training_audits": contamination.get(
                "mismatch_task_audits", {}
            ),
            "any_mismatch_task_contaminates_training": contamination.get(
                "any_mismatch_task_contaminates_training"
            ),
            "quarantine_manifest": quarantine,
            "sentinel_manifest": sentinel_manifest,
            "sentinel": sentinel if sentinel is not None else "not_run",
            "full_replay": full if full is not None else "not_run",
            "semantic_replay_validated": semantic_validated,
            "original_45_replay_resolved": branch == "exact_historical_snapshot_replay_validated",
            "provenance_valid_40_replay_validated": branch
            == "provenance_valid_subset_semantic_replay_validated",
            "sensitivity_status": sensitivity.get("status", "completed"),
            "sensitivity_conclusion": sensitivity["qualitative_conclusion"],
            "generation_remains_blocked_in_this_milestone": True,
            "recommended_next_milestone": (
                "separately_reviewed_EXP_024A_Q_40_state_8_task_causal_audit"
                if branch == "provenance_valid_subset_semantic_replay_validated"
                else "corpus_level_source_identity_reconciliation_and_training_contamination_review"
                if branch == "source_dataset_identity_consistency_failure"
                else "resolve_provenance_or_replay_failure_before_generation"
            ),
            "qwen_import_forward_generation_count": 0,
            "memory_condition_execution_count": 0,
            "model_training_count": 0,
            "scientific_parameter_changed": False,
        }
        final_report = "\n".join(
            [
                "# EXP-024R3 Final Report",
                "",
                f"Decision branch: `{branch}`.",
                f"Corpus-wide mismatch: `{corpus['identity_mismatch_count']}` task(s).",
                f"Forensic classification: `{forensic['failure_classification']}`.",
                f"Snapshot result: `{search['search_result']}`.",
                f"Train-side contamination: `{contamination['contaminates_training']}`.",
                f"Any mismatched-task train-side contamination: `{contamination.get('any_mismatch_task_contaminates_training')}`.",
                f"Semantic replay validated: `{semantic_validated}`.",
                f"Generation remains blocked in EXP-024R3: `True`.",
                "",
                "No Qwen model was imported or run, no memory condition was executed, and no model or AppWorld task evaluation was performed.",
            ]
        )
        outputs = {
            "corpus_identity_consistency_report.md": corpus_report,
            "b0a8eae_2_forensic_provenance_report.md": forensic_report,
            "bounded_snapshot_search_report.md": search_report,
            "task_quarantine_report.md": quarantine_report,
            "repeated_sentinel_report.md": sentinel_report,
            "provenance_valid_semantic_replay_report.md": replay_report,
            "prior_result_sensitivity_report.md": sensitivity_report,
            "future_behavioral_audit_contract.md": future_contract,
            "final_exp024r3_report.md": final_report,
        }
        for name, content in outputs.items():
            atomic_write_text(args.artifact_dir / name, content.rstrip() + "\n")
        output = args.artifact_dir / "final_exp024r3_summary.json"
        atomic_write_json(output, final_summary)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(final_summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
