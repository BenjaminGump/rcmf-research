from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_semantic_replay_6h2 import decode_jwt_strict
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    return {str(row["attempt_id"]) for row in read_jsonl(path)} if path.exists() else set()


def _classify_root_temporal_jwt_difference(step: dict[str, Any]) -> dict[str, Any]:
    comparison = dict(step.get("semantic_comparison", {}))
    try:
        expected = decode_jwt_strict(str(step["expected_raw_observation"]).strip())
        actual = decode_jwt_strict(str(step["actual_raw_observation"]).strip())
    except (KeyError, ValueError):
        return {"temporal_only_root_jwt": False}
    expected_stable = {key: value for key, value in expected.payload.items() if key != "exp"}
    actual_stable = {key: value for key, value in actual.payload.items() if key != "exp"}
    differing_claims = sorted(
        key
        for key in set(expected.payload) | set(actual.payload)
        if expected.payload.get(key) != actual.payload.get(key)
    )
    temporal_only = bool(
        comparison.get("jwt_field_count") == 0
        and expected.header == actual.header
        and expected_stable == actual_stable
        and differing_claims == ["exp"]
    )
    left_exp = expected.payload.get("exp")
    right_exp = actual.payload.get("exp")
    return {
        "temporal_only_root_jwt": temporal_only,
        "header_sha256": comparison.get("expected_semantic_sha256"),
        "stable_claim_names": sorted(expected_stable),
        "stable_claims_match": expected_stable == actual_stable,
        "differing_claims": differing_claims,
        "exp_delta_seconds": (
            int(right_exp - left_exp)
            if isinstance(left_exp, (int, float)) and isinstance(right_exp, (int, float))
            else None
        ),
        "schema_location": "$",
        "allowed_token_field_matched": False,
        "raw_values_recorded_in_github_safe_output": False,
    }


def _full_replay_failure_diagnostic(checkpoint_path: Path) -> dict[str, Any]:
    checkpoint = _load(checkpoint_path)
    failures = []
    for key, entry in sorted(dict(checkpoint.get("rows", {})).items()):
        if not str(key).startswith("full:"):
            continue
        result = _load(Path(str(entry["output_path"])))
        for step in result.get("steps", []):
            if bool(step.get("semantic_v2_match")):
                continue
            failures.append(
                {
                    "state_example_id": str(result["state_example_id"]),
                    "task_id": str(result["task_id"]),
                    "step_id": int(step["step_id"]),
                    "is_target": bool(step["is_target"]),
                    "action_sha256": str(step["action_sha256"]),
                    "expected_raw_sha256": step["semantic_comparison"].get(
                        "expected_raw_sha256"
                    ),
                    "actual_raw_sha256": step["semantic_comparison"].get(
                        "actual_raw_sha256"
                    ),
                    **_classify_root_temporal_jwt_difference(dict(step)),
                }
            )
    return {
        "format": "identity_reconciled_replay_failure_diagnostic_7a_v1",
        "failure_count": len(failures),
        "state_count": len({row["state_example_id"] for row in failures}),
        "task_count": len({row["task_id"] for row in failures}),
        "all_failures_temporal_only_root_jwt": bool(failures)
        and all(row["temporal_only_root_jwt"] for row in failures),
        "semantic_contract_broadened": False,
        "rows": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
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
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    paths = {
        "builder": args.artifact_dir / "corpus_builder_root_cause.json",
        "forensic": args.artifact_dir / "affected_task_behavioral_provenance.json",
        "affected_replay": args.artifact_dir / "affected_task_semantic_replay_summary.json",
        "policy": args.artifact_dir / "remediation_policy_manifest.json",
        "structural": args.artifact_dir / "structural_finalization_summary.json",
        "dependency": args.artifact_dir / "artifact_dependency_graph.json",
        "estimate": args.artifact_dir / "minimum_recompute_estimate.json",
        "sensitivity": args.artifact_dir / "contaminated_checkpoint_sensitivity.json",
        "sentinel": args.artifact_dir / "replay" / "reconciled_sentinel_summary.json",
        "full": args.artifact_dir / "replay" / "reconciled_full_summary.json",
        "checkpoint": args.artifact_dir / "replay" / "checkpoint_index.json",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"EXP-025A analysis input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    with AttemptLedger(
        args.artifact_dir, run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="final_analysis_and_reports", command=[str(value) for value in sys.argv],
        local_head=args.local_head, github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        payload = {name: _load(path) for name, path in paths.items()}
        replay_failure = _full_replay_failure_diagnostic(paths["checkpoint"])
        atomic_write_json(
            args.artifact_dir / "reconciled_replay_failure_diagnostic.json",
            replay_failure,
        )
        structural_branch = str(payload["structural"]["decision_branch"])
        replay_passed = bool(payload["sentinel"]["gate"]["passed"] and payload["full"]["gate"]["passed"])
        branch = structural_branch if replay_passed else "identity_reconciled_corpus_replay_failure"
        clean_ready = bool(payload["structural"]["clean_corpus_ready"] and replay_passed)
        b3 = next(
            row for row in payload["forensic"]["rows"] if row["task_id"] == "b0a8eae_3"
        )
        task_replay = payload["affected_replay"]["task_results"]
        corpus = payload["structural"]["structural_validation"]
        final = {
            "format": "appworld_identity_reconciliation_final_summary_7a_v1",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "starting_head": settings["branches"]["starting_head"],
            "archive_branch": settings["branches"]["archive"],
            "working_branch": settings["branches"]["working"],
            "root_cause": payload["builder"]["root_cause"],
            "root_cause_exactly_reproduced": payload["builder"]["exact_root_cause_reproduced"],
            "b0a8eae_3_classification": b3["classification_before_replay"],
            "affected_task_replay": {
                task_id: {
                    "states": row["summary"]["state_count"],
                    "prior_observations": row["summary"]["prior_observation_count"],
                    "complete_replays": row["summary"]["complete_semantic_replay_count"],
                    "non_temporal_mismatches": row["summary"]["non_temporal_jwt_mismatch_count"],
                    "exceptions": row["summary"]["exception_count"],
                    "gate_passed": row["gate"]["passed"],
                    "remediation": row["remediation"],
                }
                for task_id, row in task_replay.items()
            },
            "remediations": payload["policy"]["task_remediations"],
            "task_count": corpus["task_count"],
            "train_task_count": corpus["train_task_count"],
            "validation_task_count": corpus["validation_task_count"],
            "decision_count": corpus["decision_count"],
            "transition_count": corpus["transition_count"],
            "corpus_lineage_sha256": corpus["lineage_sha256"],
            "structural_validation_passed": corpus["passed"],
            "structural_corpus_candidate_ready": bool(
                payload["structural"]["clean_corpus_ready"]
            ),
            "sentinel_gate_passed": payload["sentinel"]["gate"]["passed"],
            "full_replay_gate_passed": payload["full"]["gate"]["passed"],
            "full_replay_metrics": payload["full"]["repeat_summaries"][0],
            "full_replay_failure_diagnostic": replay_failure,
            "decision_branch": branch,
            "clean_corpus_ready": clean_ready,
            "dependency_artifact_count": payload["dependency"]["artifact_count"],
            "dependency_classification_counts": payload["dependency"]["classification_counts"],
            "recompute_estimate": payload["estimate"],
            "sensitivity_conclusion": payload["sensitivity"]["qualitative_conclusion"],
            "generation_and_training_remain_blocked_in_exp025a": True,
            "recommended_next_milestone": (
                "EXP-025B_minimum_v4_rebuild_chain_from_identity_reconciled_corpus"
                if replay_passed
                else "EXP-025B_with_blocking_root_jwt_semantic_replay_preflight"
            ),
            "qwen_import_forward_representation_count": 0,
            "h100_hours": 0.0,
            "model_training_count": 0,
            "historical_artifacts_rewritten": False,
        }
        reports = {
            "corpus_builder_root_cause_report.md": "\n".join([
                "# Corpus-Builder Root Cause", "",
                f"- Exact reproduction: `{payload['builder']['exact_root_cause_reproduced']}`",
                f"- Root cause: `{payload['builder']['root_cause']}`",
                "- The old builder combined archived environment I/O with an unpinned active task-spec lookup.",
                "- The prospective builder now requires an explicit pinned task-spec root.",
            ]),
            "b0a8eae_3_forensic_report.md": "\n".join([
                "# b0a8eae_3 Behavioral Provenance", "",
                f"- Classification: `{b3['classification_before_replay']}`",
                f"- Official-identity references: `{b3['identity_evidence']['official_identity_evidence_count']}`",
                f"- Source-header references: `{b3['identity_evidence']['source_identity_evidence_count']}`",
                f"- Third-identity references: `{b3['third_identity_evidence']['third_identity_evidence_count']}`",
                f"- Mixed steps: `{b3['identity_evidence']['mixed_identity_step_count']}`",
                "- Raw identity values are redacted; reports contain hashes and counts only.",
            ]),
            "affected_task_semantic_replay_report.md": "\n".join([
                "# Affected-Task Candidate Replay", "",
                *[
                    f"- `{task_id}`: {row['summary']['complete_semantic_replay_count']}/{row['summary']['state_count']} complete; remediation `{row['remediation']}`."
                    for task_id, row in task_replay.items()
                ],
            ]),
            "remediation_policy_report.md": "\n".join([
                "# Pre-Registered Remediation Policy", "",
                f"- Decisions: `{payload['policy']['task_remediations']}`",
                f"- Downstream performance consulted: `{payload['policy']['downstream_performance_consulted']}`",
                "- Only query identity fields may change for repaired tasks; actions and observations remain unchanged.",
            ]),
            "structural_corpus_report.md": "\n".join([
                "# Identity-Reconciled Structural Corpus", "",
                f"- Tasks train/validation: `{corpus['train_task_count']}/{corpus['validation_task_count']}`",
                f"- Decisions/transitions: `{corpus['decision_count']}/{corpus['transition_count']}`",
                f"- Lineage: `{corpus['lineage_sha256']}`",
                f"- Validation passed: `{corpus['passed']}`",
            ]),
            "artifact_dependency_report.md": "\n".join([
                "# Artifact Dependency and Contamination Graph", "",
                f"- Artifacts classified: `{payload['dependency']['artifact_count']}`",
                f"- Class counts: `{payload['dependency']['classification_counts']}`",
                "- A checkpoint is never called clean merely because invalid evaluation rows are removed.",
            ]),
            "contamination_sensitivity_report.md": "\n".join([
                "# Contaminated-Checkpoint Sensitivity", "",
                f"- Conclusion: `{payload['sensitivity']['qualitative_conclusion']}`",
                "- Existing predictions only; no model was retrained or forwarded.",
            ]),
            "reconciled_semantic_replay_report.md": "\n".join([
                "# Reconciled Semantic Replay", "",
                f"- Repeated sentinel: `{payload['sentinel']['gate']['passed']}`",
                f"- Full manifest: `{payload['full']['gate']['passed']}`",
                f"- Prior semantic matches: `{payload['full']['repeat_summaries'][0]['prior_semantic_match_count']}/{payload['full']['repeat_summaries'][0]['prior_observation_count']}`",
                f"- Target semantic matches: `{payload['full']['repeat_summaries'][0]['target_semantic_match_count']}/{payload['full']['repeat_summaries'][0]['target_observation_count']}`",
                f"- Root-level temporal JWT failures: `{replay_failure['failure_count']}` across `{replay_failure['state_count']}` states.",
                "- Those values are outside the frozen access_token field allowlist; the contract was not broadened.",
                "- JWT semantic-v2 remains limited to access_token.exp.",
            ]),
            "EXP-025B_minimum_rebuild_plan.md": "\n".join([
                "# Proposed EXP-025B Minimum Rebuild", "",
                "0. Resolve and preregister the root-level login-token semantic replay contract; no model work starts while replay remains failed.",
                "1. Regenerate affected structural, state, memory, and transition representations.",
                "2. Recompute only invalid raw-teacher rows.",
                "3. Rebuild labels and manifests.",
                "4. Retrain only models required by the current V4 hypothesis.",
                "5. Re-run procedural coverage.",
                "6. Resume the one-step causal audit only after semantic replay passes.",
                "", "Do not automatically rerun every historical V3 experiment.",
            ]),
            "final_exp025a_report.md": "\n".join([
                "# EXP-025A Final Report", "",
                f"Decision branch: `{branch}`.",
                f"Clean corpus ready: `{clean_ready}`.",
                f"Structurally reconciled corpus candidate ready: `{payload['structural']['clean_corpus_ready']}`.",
                f"Generation/training blocked in this milestone: `True`.",
                "No Qwen or H100 was used, and no historical artifact was rewritten.",
            ]),
        }
        for name, content in reports.items():
            atomic_write_text(args.artifact_dir / name, content.rstrip() + "\n")
        output = args.artifact_dir / "final_exp025a_summary.json"
        atomic_write_json(output, final)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(final, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
