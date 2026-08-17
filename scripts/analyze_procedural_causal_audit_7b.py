from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_causal_analysis_7b import (
    comparison_set,
    condition_summary,
    per_task_summary,
    primary_comparisons,
    relationship_analysis,
    same_signature_consistency,
    select_decision,
    validate_formal_rows,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _artifact_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _markdown(summary: Mapping[str, Any]) -> str:
    decision = summary["decision"]
    lines = [
        "# EXP-025B Clean-Corpus Oracle One-Step Causal Audit",
        "",
        f"- Run UUID: `{summary['run_uuid']}`",
        f"- Conditions: {summary['condition_count']}",
        f"- States/tasks: {summary['state_count']} / {summary['task_count']}",
        f"- Qwen generation H100 hours: {summary['actual_qwen_h100_hours']:.6f}",
        f"- Decision branch: `{decision['decision_branch']}`",
        "- Field/program training remains blocked pending separate review.",
        "",
        "## Primary non-documentation comparisons",
        "",
        "| Comparison | Exact API delta | Signature delta | Execution delta | Exact API CI |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, comparison in summary["primary_comparisons"].items():
        if not isinstance(comparison, Mapping):
            continue
        exact = comparison.get("exact_primary_app_api_match")
        signature = comparison.get("canonical_procedural_signature_match")
        execution = comparison.get("execution_success")
        if not all(isinstance(value, Mapping) for value in (exact, signature, execution)):
            continue
        lines.append(
            f"| {name} | {exact['difference']:.4f} | {signature['difference']:.4f} | "
            f"{execution['difference']:.4f} | [{exact['ci95_low']:.4f}, {exact['ci95_high']:.4f}] |"
        )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            f"- Procedural oracle behavioral gate: {decision['procedural_oracle_behavioral_gate']}",
            f"- Content beyond metadata gate: {decision['content_beyond_metadata_gate']}",
            f"- Same-signature consistency gate: {decision['same_signature_consistency_gate']}",
            f"- Positive held-out tasks: {decision['positive_task_count']} / 9",
            "",
            "Complete condition metrics, task-grouped bootstrap intervals, documentation strata, "
            "alternate-exemplar diagnostics, and clean raw-NLL relationships are stored in the "
            "adjacent JSON reports.",
        ]
    )
    return "\n".join(lines) + "\n"


def _compact_report(title: str, payload: Mapping[str, Any]) -> str:
    return (
        f"# {title}\n\n"
        "This GitHub-safe report summarizes the immutable Lambda artifact.\n\n"
        "```json\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n```\n"
    )


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
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025b")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7b"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    clean_audit = args.artifact_dir / "clean_procedural_audit"
    cache_root = Path(str(settings["cache_rebuild"]["output_root"]))
    paths = {
        "condition_manifest": clean_audit / "clean_condition_manifest.json",
        "audit_strata": clean_audit / "clean_audit_state_strata.json",
        "condition_preflight": clean_audit / "clean_causal_audit_preflight_summary.json",
        "condition_comparison": clean_audit / "clean_condition_manifest_comparison.json",
        "transition_manifest": cache_root / "transition_preflight/transition_manifest.jsonl",
        "cache_validation": cache_root / "postrun_validation.json",
        "replay_manifest": args.artifact_dir / "replay_validated_corpus_manifest.json",
        "smoke_summary": args.artifact_dir / "lifecycle_smoke/smoke_summary.json",
        "generation_summary": args.artifact_dir / "generation_summary.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing analysis input: {name}={path}")
    condition_manifest = _json(paths["condition_manifest"])
    strata = _json(paths["audit_strata"])
    generation = _json(paths["generation_summary"])
    smoke = _json(paths["smoke_summary"])
    cache_validation = _json(paths["cache_validation"])
    if not bool(smoke.get("passed")) or not bool(cache_validation.get("passed")):
        raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
    output_paths = sorted((args.artifact_dir / "condition_outputs").glob("*.json"))
    rows = [_json(path) for path in output_paths]
    validation = validate_formal_rows(rows, condition_manifest, generation)

    config_sha256 = sha256_file(args.config)
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    data_hashes["condition_outputs"] = _condition_output_hash(output_paths)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="clean_corpus_causal_metrics_and_scientific_gate",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        metric_settings = settings["causal_audit"]["metrics"]
        samples = int(metric_settings["bootstrap_samples"])
        seed = int(metric_settings["bootstrap_seed"])
        primary_rows = [row for row in rows if str(row["audit_stratum"]) in {"A", "B"}]
        documentation_rows = [row for row in rows if str(row["audit_stratum"]) == "C"]
        diagnostic_rows = [row for row in rows if str(row["audit_stratum"]) in {"D", "E"}]
        comparisons = primary_comparisons(primary_rows, bootstrap_samples=samples, seed=seed)
        documentation_comparisons = {}
        if documentation_rows:
            pairs = (
                ("C1_raw_oracle", "C0_bare"),
                ("C1_raw_oracle", "C2_signature_only"),
                ("C1_raw_oracle", "C3_hard_negative"),
                ("C1_raw_oracle", "C5_unrelated"),
            )
            for index, (left, right) in enumerate(pairs):
                documentation_comparisons[f"{left}_minus_{right}"] = comparison_set(
                    documentation_rows,
                    left=left,
                    right=right,
                    bootstrap_samples=samples,
                    seed=seed + 2000 + index * 100,
                )
        consistency = same_signature_consistency(rows)
        transition_tokens = {
            str(row["transition_id"]): int(row["teacher_section_tokens"])
            for row in _rows(paths["transition_manifest"])
        }
        relationship = relationship_analysis(rows, transition_tokens)
        decision = select_decision(
            primary=comparisons,
            documentation=documentation_comparisons,
            consistency=consistency,
            gates=settings["causal_audit"]["gates"],
        )
        summary = {
            "format": "identity_reconciled_causal_audit_summary_7b_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "replay_validated_corpus_lineage_sha256": _json(paths["replay_manifest"])[
                "lineage_sha256"
            ],
            "condition_count": len(rows),
            "state_count": validation["state_count"],
            "task_count": validation["task_count"],
            "audit_strata": strata.get("stratum_state_counts"),
            "actual_qwen_generation_seconds": float(generation["qwen_generation_seconds"]),
            "actual_qwen_h100_hours": float(generation["qwen_generation_h100_hours"]),
            "actual_formal_wall_seconds": float(generation["elapsed_seconds"]),
            "raw_nll_utility_available_count": int(generation["raw_nll_utility_available_count"]),
            "infrastructure_validation": validation,
            "smoke": smoke,
            "generation": generation,
            "condition_metrics_all": condition_summary(rows),
            "condition_metrics_primary_non_documentation": condition_summary(primary_rows),
            "condition_metrics_api_documentation_only": condition_summary(documentation_rows),
            "condition_metrics_diagnostic_D_E": condition_summary(diagnostic_rows),
            "per_task": per_task_summary(rows),
            "primary_comparisons": comparisons,
            "documentation_comparisons": documentation_comparisons,
            "same_signature_consistency": consistency,
            "clean_raw_nll_outcome_relationship": relationship,
            "decision": decision,
            "analysis_elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "causal_audit_validation.json", validation)
        atomic_write_json(
            args.artifact_dir / "one_step_causal_metrics.json",
            {
                "all": summary["condition_metrics_all"],
                "primary_non_documentation": summary["condition_metrics_primary_non_documentation"],
                "api_documentation_only": summary["condition_metrics_api_documentation_only"],
                "diagnostic_D_E": summary["condition_metrics_diagnostic_D_E"],
                "per_task": summary["per_task"],
            },
        )
        atomic_write_json(
            args.artifact_dir / "causal_comparisons.json",
            {"primary": comparisons, "documentation": documentation_comparisons},
        )
        atomic_write_json(args.artifact_dir / "same_signature_consistency.json", consistency)
        atomic_write_json(
            args.artifact_dir / "clean_raw_nll_outcome_relationship.json",
            relationship,
        )
        atomic_write_json(args.artifact_dir / "final_exp025b_summary.json", summary)
        atomic_write_text(args.artifact_dir / "final_exp025b_report.md", _markdown(summary))
        atomic_write_text(
            args.artifact_dir / "raw_vs_signature_only_report.md",
            _compact_report(
                "Raw Transition Versus Signature-Only",
                {
                    "comparison": comparisons["C1_raw_oracle_minus_C2_signature_only"],
                    "decision": decision,
                },
            ),
        )
        atomic_write_text(
            args.artifact_dir / "same_signature_consistency_report.md",
            _compact_report("Same-Signature Consistency", consistency),
        )
        atomic_write_text(
            args.artifact_dir / "clean_raw_nll_outcome_report.md",
            _compact_report(
                "Clean Raw-NLL Versus One-Step Outcome",
                {
                    "available_pair_count": relationship["available_pair_count"],
                    "available_state_count": relationship["available_state_count"],
                    "correlations": relationship["correlations"],
                },
            ),
        )
        summary["artifact_bytes_after_analysis"] = _artifact_size(args.artifact_dir)
        atomic_write_json(args.artifact_dir / "final_exp025b_summary.json", summary)
        attempt.progress(
            status="completed",
            decision_branch=decision["decision_branch"],
            raw_transition_content_behaviorally_validated=decision[
                "raw_transition_content_behaviorally_validated"
            ],
            latest_validated_checkpoint=str(args.artifact_dir / "final_exp025b_summary.json"),
        )
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


def _condition_output_hash(paths: list[Path]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


if __name__ == "__main__":
    main()
