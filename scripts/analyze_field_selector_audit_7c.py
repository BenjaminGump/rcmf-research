from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_causal_analysis_7b import (
    PRIMARY_METRICS,
    comparison_set,
    condition_summary,
    per_task_summary,
    relationship_analysis,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _attempt_ids(path: Path) -> set[str]:
    return (
        {str(row["attempt_id"]) for row in read_jsonl(path)}
        if path.exists()
        else set()
    )


def _output_rows(root: Path) -> list[dict[str, Any]]:
    paths = sorted(root.glob("*.json"))
    if not paths:
        raise ValueError(f"No condition checkpoints found: {root}")
    return [_json(path) for path in paths]


def _comparison_bundle(
    rows: Sequence[Mapping[str, Any]],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    pairs = (
        ("F3_deployment_e_field_raw", "C0_bare"),
        ("F3_deployment_e_field_raw", "C1_raw_oracle"),
        ("F3_deployment_e_field_raw", "F4_deployment_e_field_signature"),
        ("F3_deployment_e_field_raw", "F5_predicted_intent_raw"),
        ("F3_deployment_e_field_raw", "C3_hard_negative"),
        ("F3_deployment_e_field_raw", "C4_signature_popularity"),
        ("F3_deployment_e_field_raw", "C5_unrelated"),
        ("F1_strict_b_field_raw", "F3_deployment_e_field_raw"),
        ("F1_strict_b_field_raw", "C0_bare"),
        ("C1_raw_oracle", "C0_bare"),
    )
    return {
        f"{left}_minus_{right}": comparison_set(
            rows,
            left=left,
            right=right,
            bootstrap_samples=samples,
            seed=seed + index * 100,
        )
        for index, (left, right) in enumerate(pairs)
    }


def _metric_difference(
    comparison: Mapping[str, Any], metric: str
) -> float:
    return float(comparison[metric]["difference"])


def _ci_positive(comparison: Mapping[str, Any], metric: str) -> bool:
    value = comparison[metric]
    return value.get("ci95_low") is not None and float(value["ci95_low"]) > 0.0


def _positive_task_count(
    rows: Sequence[Mapping[str, Any]], left: str, right: str
) -> tuple[int, dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Mapping[str, Any]]]] = {}
    for row in rows:
        grouped.setdefault(str(row["state_task_id"]), {}).setdefault(
            str(row["state_example_id"]), {}
        )[str(row["condition_name"])] = row
    output = {}
    for task, states in sorted(grouped.items()):
        effects = []
        for values in states.values():
            if left not in values or right not in values:
                continue
            effects.append(
                {
                    metric: float(values[left]["metrics"][metric])
                    - float(values[right]["metrics"][metric])
                    for metric in (
                        "exact_primary_app_api_match",
                        "canonical_procedural_signature_match",
                        "semantic_successor_match",
                    )
                }
            )
        means = {
            metric: statistics.fmean(value[metric] for value in effects)
            for metric in effects[0]
        } if effects else {}
        positive = any(value > 0.0 for value in means.values())
        output[task] = {"state_count": len(effects), "metric_deltas": means, "positive": positive}
    return sum(bool(row["positive"]) for row in output.values()), output


def _retention(
    *,
    field_vs_bare: Mapping[str, Any],
    oracle_vs_bare: Mapping[str, Any],
) -> dict[str, Any]:
    output = {}
    for metric in (
        "exact_primary_app_api_match",
        "canonical_procedural_signature_match",
        "semantic_successor_match",
    ):
        numerator = _metric_difference(field_vs_bare, metric)
        denominator = _metric_difference(oracle_vs_bare, metric)
        output[metric] = {
            "field_gain": numerator,
            "oracle_gain": denominator,
            "retention": numerator / denominator if denominator != 0.0 else None,
        }
    return output


def _material_control_pass(comparison: Mapping[str, Any]) -> bool:
    main = (
        "exact_primary_app_api_match",
        "canonical_procedural_signature_match",
        "semantic_successor_match",
    )
    values = [_metric_difference(comparison, metric) for metric in main]
    execution = _metric_difference(comparison, "execution_success")
    return max(values) >= 0.05 and min(values) >= -0.05 and execution >= -0.05


def _decision(
    *,
    comparisons: Mapping[str, Any],
    selector_gates: Mapping[str, Any],
    behavior_gate: Mapping[str, Any],
) -> dict[str, Any]:
    if not bool(selector_gates["deployment_e"]["passed"]):
        branch = "procedural_oracle_valid_field_selector_failed"
    elif not bool(behavior_gate["core_behavioral_relevance_passed"]):
        branch = "procedural_field_prediction_not_behaviorally_retained"
    elif not bool(behavior_gate["content_beyond_metadata_gate_passed"]):
        branch = "field_selects_procedure_but_raw_content_advantage_not_retained"
    elif not bool(behavior_gate["field_beats_predicted_intent"]):
        branch = "field_collapses_to_action_intent_metadata"
    elif not bool(behavior_gate["field_beats_other_controls"]):
        branch = "procedural_field_prediction_not_behaviorally_retained"
    elif not bool(selector_gates["strict_b"]["passed"]):
        branch = "deployment_field_valid_strict_parent_generalization_weak"
    else:
        branch = "signature_balanced_field_selector_behaviorally_validated"
    return {
        "decision_branch": branch,
        "automatic_field_selection_behaviorally_validated": branch
        == "signature_balanced_field_selector_behaviorally_validated",
        "p_s_m_transition_remains_blocked": True,
    }


def _markdown(summary: Mapping[str, Any]) -> str:
    gates = summary["behavioral_gates"]
    decision = summary["decision"]
    metrics = summary["primary_condition_metrics"]
    lines = [
        "# EXP-025C Deployable Procedural Field Audit",
        "",
        f"- selector strict-B passed: `{summary['selector_gates']['strict_b']['passed']}`",
        f"- selector deployment-E passed: `{summary['selector_gates']['deployment_e']['passed']}`",
        f"- behavioral retention passed: `{gates['deployment_behavioral_gate_passed']}`",
        f"- content beyond metadata passed: `{gates['content_beyond_metadata_gate_passed']}`",
        f"- decision: `{decision['decision_branch']}`",
        "",
        "| Condition | Exact API | Action signature | Execution | Semantic successor |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in (
        "C0_bare",
        "C1_raw_oracle",
        "F1_strict_b_field_raw",
        "F3_deployment_e_field_raw",
        "F4_deployment_e_field_signature",
        "F5_predicted_intent_raw",
    ):
        values = metrics[name]["metrics"]
        lines.append(
            f"| {name} | {values['exact_primary_app_api_match']:.4f} | "
            f"{values['canonical_procedural_signature_match']:.4f} | "
            f"{values['execution_success']:.4f} | "
            f"{values['semantic_successor_match']:.4f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_signature_balanced_field_7c.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025c")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7c"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    parent = Path(str(settings["parent_exp025b"]))
    paths = {
        "manifest": args.artifact_dir / "selector_condition_manifest.json",
        "selector_summary": args.artifact_dir / "selector/selector_summary.json",
        "generation_summary": args.artifact_dir / "selector_generation_summary.json",
        "smoke_summary": args.artifact_dir / "lifecycle_smoke/smoke_summary.json",
        "old_generation_summary": parent / "generation_summary.json",
        "transition_manifest": parent
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "query_signatures": parent
        / "clean_procedural_audit/clean_query_procedural_signatures.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Analysis input missing: {name}={path}")
    manifest = _json(paths["manifest"])
    generation = _json(paths["generation_summary"])
    smoke = _json(paths["smoke_summary"])
    selector = _json(paths["selector_summary"])
    if not bool(generation["passed"]) or not bool(smoke["passed"]):
        raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
    new_rows = _output_rows(args.artifact_dir / "selector_condition_outputs")
    expected_keys = {str(row["condition_key"]) for row in manifest["conditions"]}
    if {str(row["condition_key"]) for row in new_rows} != expected_keys:
        raise ValueError("Selector condition outputs do not exactly match the manifest")
    old_rows = _output_rows(parent / "condition_outputs")
    if len(old_rows) != 323:
        raise ValueError("EXP-025B condition outputs differ from 323")
    all_rows = [*old_rows, *new_rows]
    action_type_by_state = {
        str(row["state_example_id"]): str(
            row["target_signature"]["coarse_action_type"]
        )
        for row in _rows(paths["query_signatures"])
    }
    per_action_type = {
        action_type: condition_summary(
            [
                row
                for row in all_rows
                if action_type_by_state[str(row["state_example_id"])]
                == action_type
            ]
        )
        for action_type in sorted(set(action_type_by_state.values()))
    }
    primary = [row for row in all_rows if str(row["audit_stratum"]) in {"A", "B"}]
    documentation = [row for row in all_rows if str(row["audit_stratum"]) == "C"]
    samples = int(settings["selector"]["bootstrap_samples"])
    seed = int(settings["selector"]["bootstrap_seed"])
    comparisons = _comparison_bundle(primary, samples=samples, seed=seed)
    field_bare = comparisons["F3_deployment_e_field_raw_minus_C0_bare"]
    oracle_bare = comparisons["C1_raw_oracle_minus_C0_bare"]
    field_card = comparisons[
        "F3_deployment_e_field_raw_minus_F4_deployment_e_field_signature"
    ]
    retention = _retention(field_vs_bare=field_bare, oracle_vs_bare=oracle_bare)
    strict_retention = _retention(
        field_vs_bare=comparisons["F1_strict_b_field_raw_minus_C0_bare"],
        oracle_vs_bare=oracle_bare,
    )
    retained = [
        row["retention"] is not None
        and float(row["retention"]) >= float(settings["gates"]["behavior"]["oracle_retention"])
        for row in retention.values()
    ]
    positive_tasks, task_effects = _positive_task_count(
        primary, "F3_deployment_e_field_raw", "C0_bare"
    )
    ci_metric_pass = any(_ci_positive(field_bare, metric) for metric in PRIMARY_METRICS)
    control_names = (
        "F3_deployment_e_field_raw_minus_F5_predicted_intent_raw",
        "F3_deployment_e_field_raw_minus_C4_signature_popularity",
        "F3_deployment_e_field_raw_minus_C3_hard_negative",
        "F3_deployment_e_field_raw_minus_C5_unrelated",
    )
    control_checks = {
        name: _material_control_pass(comparisons[name]) for name in control_names
    }
    content_metric_pass = any(
        _metric_difference(field_card, metric)
        >= float(settings["gates"]["behavior"]["content_gain"])
        and _ci_positive(field_card, metric)
        for metric in (
            "canonical_procedural_signature_match",
            "semantic_successor_match",
        )
    )
    behavioral_gates = {
        "retention_by_metric": retention,
        "retention_metric_pass_count": sum(retained),
        "execution_drop_within_limit": _metric_difference(
            field_bare, "execution_success"
        )
        >= -float(settings["gates"]["behavior"]["maximum_execution_drop"]),
        "positive_task_count": positive_tasks,
        "positive_task_requirement": int(
            settings["gates"]["behavior"]["positive_tasks"]
        ),
        "task_effects": task_effects,
        "positive_task_gate": positive_tasks
        >= int(settings["gates"]["behavior"]["positive_tasks"]),
        "primary_ci_excludes_zero": ci_metric_pass,
        "control_checks": control_checks,
        "field_beats_all_controls": all(control_checks.values()),
        "field_beats_predicted_intent": control_checks[
            "F3_deployment_e_field_raw_minus_F5_predicted_intent_raw"
        ],
        "field_beats_other_controls": all(
            value
            for name, value in control_checks.items()
            if name
            != "F3_deployment_e_field_raw_minus_F5_predicted_intent_raw"
        ),
        "content_beyond_metadata_gate_passed": content_metric_pass,
    }
    behavioral_gates["core_behavioral_relevance_passed"] = bool(
        sum(retained) >= 2
        and behavioral_gates["execution_drop_within_limit"]
        and behavioral_gates["positive_task_gate"]
        and behavioral_gates["primary_ci_excludes_zero"]
    )
    behavioral_gates["deployment_behavioral_gate_passed"] = bool(
        behavioral_gates["core_behavioral_relevance_passed"]
        and behavioral_gates["field_beats_all_controls"]
        and behavioral_gates["content_beyond_metadata_gate_passed"]
    )
    decision = _decision(
        comparisons=comparisons,
        selector_gates=selector["gates"],
        behavior_gate=behavioral_gates,
    )
    transition_tokens = {
        str(row["transition_id"]): int(row["teacher_section_tokens"])
        for row in _rows(paths["transition_manifest"])
    }
    relationship = relationship_analysis(all_rows, transition_tokens)
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="deployable_selector_behavioral_metrics_and_decision",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        summary = {
            "format": "signature_balanced_field_selector_final_summary_7c_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "selector_gates": selector["gates"],
            "selector_metrics": selector["evaluation"],
            "selector_diversity": selector["selected_class_diversity"],
            "manifest": {
                "condition_count": int(manifest["condition_count"]),
                "reuse": manifest["reuse"],
                "manifest_sha256": str(manifest["manifest_sha256"]),
            },
            "generation": generation,
            "smoke": smoke,
            "all_condition_metrics": condition_summary(all_rows),
            "primary_condition_metrics": condition_summary(primary),
            "documentation_condition_metrics": condition_summary(documentation),
            "per_task": per_task_summary(all_rows),
            "per_action_type": per_action_type,
            "primary_comparisons": comparisons,
            "behavioral_gates": behavioral_gates,
            "strict_b_oracle_retention": strict_retention,
            "clean_raw_nll_outcome_relationship": relationship,
            "decision": decision,
            "analysis_elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "deployable_one_step_metrics.json", summary)
        atomic_write_json(
            args.artifact_dir / "oracle_retention_report.json",
            {"deployment_e": retention, "strict_b": strict_retention},
        )
        atomic_write_json(args.artifact_dir / "deployable_causal_comparisons.json", comparisons)
        atomic_write_json(args.artifact_dir / "clean_raw_nll_field_outcome.json", relationship)
        atomic_write_text(args.artifact_dir / "final_exp025c_report.md", _markdown(summary))
        atomic_write_json(args.artifact_dir / "final_exp025c_summary.json", summary)
        attempt.progress(
            status="completed",
            decision_branch=decision["decision_branch"],
            latest_validated_checkpoint=str(args.artifact_dir / "final_exp025c_summary.json"),
        )
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
