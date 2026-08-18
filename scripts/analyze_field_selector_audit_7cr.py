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
from rcmf.training.selector_behavioral_missing_7cr import (
    BOUNDED_METRICS,
    one_missing_binary_bounds,
    paired_complete_case_comparison,
    predicted_intent_control_robustness,
    validate_result_keys,
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


def _metric_difference(comparison: Mapping[str, Any], metric: str) -> float:
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
        means = (
            {
                metric: statistics.fmean(value[metric] for value in effects)
                for metric in effects[0]
            }
            if effects
            else {}
        )
        output[task] = {
            "paired_state_count": len(effects),
            "metric_deltas": means,
            "positive": any(value > 0.0 for value in means.values()),
        }
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
    values = [
        _metric_difference(comparison, metric)
        for metric in (
            "exact_primary_app_api_match",
            "canonical_procedural_signature_match",
            "semantic_successor_match",
        )
    ]
    execution = _metric_difference(comparison, "execution_success")
    return max(values) >= 0.05 and min(values) >= -0.05 and execution >= -0.05


def _core_gate(
    *,
    comparison: Mapping[str, Any],
    retention: Mapping[str, Any],
    positive_tasks: int,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    retained = [
        row["retention"] is not None
        and float(row["retention"]) >= float(settings["oracle_retention"])
        for row in retention.values()
    ]
    execution_ok = _metric_difference(comparison, "execution_success") >= -float(
        settings["maximum_execution_drop"]
    )
    ci_ok = any(_ci_positive(comparison, metric) for metric in PRIMARY_METRICS)
    positive_ok = positive_tasks >= int(settings["positive_tasks"])
    direct_signal = any(
        _metric_difference(comparison, metric) > 0.0
        for metric in (
            "exact_primary_app_api_match",
            "canonical_procedural_signature_match",
            "semantic_successor_match",
        )
    )
    return {
        "retention_metric_pass_count": sum(retained),
        "retention_passed": sum(retained) >= 2,
        "execution_drop_within_limit": execution_ok,
        "positive_task_count": positive_tasks,
        "positive_task_requirement": int(settings["positive_tasks"]),
        "positive_task_gate": positive_ok,
        "primary_ci_excludes_zero": ci_ok,
        "direct_positive_signal": direct_signal,
        "passed": bool(
            sum(retained) >= 2 and execution_ok and positive_ok and ci_ok
        ),
    }


def _decision(
    *,
    field_signal: bool,
    deployment_core: Mapping[str, Any],
    other_controls_passed: bool,
    content_passed: bool,
    predicted_intent_robust: bool,
    strict_core: Mapping[str, Any],
) -> dict[str, Any]:
    if not field_signal:
        branch = "procedural_field_prediction_not_behaviorally_retained"
    elif not bool(deployment_core["retention_passed"]):
        branch = "automatic_selector_retains_insufficient_oracle_gain"
    elif not bool(deployment_core["passed"]) or not other_controls_passed:
        branch = "procedural_field_prediction_not_behaviorally_retained"
    elif not content_passed:
        branch = "field_selects_procedure_but_raw_content_advantage_not_retained"
    elif not predicted_intent_robust:
        branch = "selector_behavior_valid_predicted_intent_control_inconclusive"
    elif not bool(strict_core["passed"]):
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
    metrics = summary["primary_condition_metrics"]
    lines = [
        "# EXP-025C-R Missing-Control-Aware Selector Audit",
        "",
        f"- logical/executable/missing: "
        f"`{summary['manifest']['logical_slot_count']}/"
        f"{summary['manifest']['executable_slot_count']}/"
        f"{summary['manifest']['missing_slot_count']}`",
        f"- decision: `{summary['decision']['decision_branch']}`",
        f"- automatic selector behavior validated: "
        f"`{summary['decision']['automatic_field_selection_behaviorally_validated']}`",
        "",
        "| Condition | N | Exact API | Action signature | Execution | Semantic successor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in (
        "C0_bare",
        "C1_raw_oracle",
        "F1_strict_b_field_raw",
        "F3_deployment_e_field_raw",
        "F4_deployment_e_field_signature",
        "F5_predicted_intent_raw",
    ):
        values = metrics[name]
        row = values["metrics"]
        lines.append(
            f"| {name} | {values['state_count']} | "
            f"{row['exact_primary_app_api_match']:.4f} | "
            f"{row['canonical_procedural_signature_match']:.4f} | "
            f"{row['execution_success']:.4f} | "
            f"{row['semantic_successor_match']:.4f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_signature_balanced_field_7cr.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025cr")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7cr"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    paths = {
        "manifest": args.artifact_dir / "selector_condition_manifest.json",
        "selector_summary": parent_c / "selector/selector_summary.json",
        "generation_summary": args.artifact_dir / "selector_generation_summary.json",
        "smoke_summary": args.artifact_dir / "lifecycle_smoke/smoke_summary.json",
        "old_generation_summary": parent_b / "generation_summary.json",
        "transition_manifest": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "query_signatures": parent_b
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
    result_validation = validate_result_keys(
        manifest["conditions"],
        [str(row["condition_key"]) for row in new_rows],
    )
    if result_validation["result_count"] != 224:
        raise ValueError("Formal executable result count differs from 224")
    old_rows = _output_rows(parent_b / "condition_outputs")
    if len(old_rows) != int(settings["expected"]["parent_conditions"]):
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
    f5_primary = paired_complete_case_comparison(
        primary,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        metrics=PRIMARY_METRICS,
        bootstrap_samples=samples,
        seed=seed + 900,
    )
    f5_all = paired_complete_case_comparison(
        all_rows,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        metrics=PRIMARY_METRICS,
        bootstrap_samples=samples,
        seed=seed + 1000,
    )
    if int(f5_primary["denominators"]["paired_state_count"]) != 31:
        raise ValueError("Primary F3-F5 complete-case count differs from 31")
    if int(f5_all["denominators"]["paired_state_count"]) != 44:
        raise ValueError("All-state F3-F5 complete-case count differs from 44")
    comparisons[
        "F3_deployment_e_field_raw_minus_F5_predicted_intent_raw"
    ] = f5_primary["metrics"]
    missing_state = str(settings["missing_policy"]["state_example_id"])
    primary_bounds = one_missing_binary_bounds(
        primary,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        missing_state_id=missing_state,
    )
    all_bounds = one_missing_binary_bounds(
        all_rows,
        left="F3_deployment_e_field_raw",
        right="F5_predicted_intent_raw",
        missing_state_id=missing_state,
    )
    f5_robustness = predicted_intent_control_robustness(
        f5_primary,
        primary_bounds,
        primary_metrics=BOUNDED_METRICS,
    )

    field_bare = comparisons["F3_deployment_e_field_raw_minus_C0_bare"]
    strict_bare = comparisons["F1_strict_b_field_raw_minus_C0_bare"]
    oracle_bare = comparisons["C1_raw_oracle_minus_C0_bare"]
    field_card = comparisons[
        "F3_deployment_e_field_raw_minus_F4_deployment_e_field_signature"
    ]
    retention = _retention(field_vs_bare=field_bare, oracle_vs_bare=oracle_bare)
    strict_retention = _retention(
        field_vs_bare=strict_bare,
        oracle_vs_bare=oracle_bare,
    )
    positive_tasks, task_effects = _positive_task_count(
        primary, "F3_deployment_e_field_raw", "C0_bare"
    )
    strict_positive_tasks, strict_task_effects = _positive_task_count(
        primary, "F1_strict_b_field_raw", "C0_bare"
    )
    behavior_settings = settings["gates"]["behavior"]
    deployment_core = _core_gate(
        comparison=field_bare,
        retention=retention,
        positive_tasks=positive_tasks,
        settings=behavior_settings,
    )
    strict_core = _core_gate(
        comparison=strict_bare,
        retention=strict_retention,
        positive_tasks=strict_positive_tasks,
        settings=behavior_settings,
    )
    other_control_names = (
        "F3_deployment_e_field_raw_minus_C4_signature_popularity",
        "F3_deployment_e_field_raw_minus_C3_hard_negative",
        "F3_deployment_e_field_raw_minus_C5_unrelated",
    )
    other_control_checks = {
        name: _material_control_pass(comparisons[name])
        for name in other_control_names
    }
    content_passed = any(
        _metric_difference(field_card, metric)
        >= float(behavior_settings["content_gain"])
        and _ci_positive(field_card, metric)
        for metric in (
            "canonical_procedural_signature_match",
            "semantic_successor_match",
        )
    )
    behavioral_gates = {
        "deployment_e": deployment_core,
        "strict_b": strict_core,
        "deployment_retention": retention,
        "strict_b_retention": strict_retention,
        "deployment_task_effects": task_effects,
        "strict_b_task_effects": strict_task_effects,
        "other_control_checks": other_control_checks,
        "field_beats_other_controls": all(other_control_checks.values()),
        "content_beyond_metadata_gate_passed": content_passed,
        "predicted_intent_control": f5_robustness,
        "predicted_intent_control_comparison_inconclusive": not bool(
            f5_robustness["passed"]
        ),
    }
    behavioral_gates["all_non_f5_gates_passed"] = bool(
        deployment_core["passed"]
        and all(other_control_checks.values())
        and content_passed
    )
    behavioral_gates["deployment_behavioral_gate_passed"] = bool(
        behavioral_gates["all_non_f5_gates_passed"]
        and f5_robustness["passed"]
    )
    decision = _decision(
        field_signal=bool(deployment_core["direct_positive_signal"]),
        deployment_core=deployment_core,
        other_controls_passed=all(other_control_checks.values()),
        content_passed=content_passed,
        predicted_intent_robust=bool(f5_robustness["passed"]),
        strict_core=strict_core,
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
        phase="missing_control_behavioral_metrics_and_decision",
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
        missing_report = {
            "policy_version": str(manifest["missing_policy_version"]),
            "missing_record": next(
                row
                for row in manifest["conditions"]
                if not bool(row["valid_for_generation"])
            ),
            "all_state_complete_case": f5_all,
            "primary_complete_case": f5_primary,
            "all_state_one_row_bounds": all_bounds,
            "primary_one_row_bounds": primary_bounds,
            "robustness": f5_robustness,
            "scientific_imputation_performed": False,
        }
        summary = {
            "format": "missing_control_selector_final_summary_7cr_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "selector_gates": selector["gates"],
            "selector_metrics": selector["evaluation"],
            "selector_diversity": selector["selected_class_diversity"],
            "manifest": {
                "logical_slot_count": int(manifest["logical_slot_count"]),
                "executable_slot_count": int(manifest["executable_slot_count"]),
                "missing_slot_count": int(manifest["missing_slot_count"]),
                "reuse": manifest["reuse"],
                "manifest_sha256": str(manifest["manifest_sha256"]),
                "result_validation": result_validation,
            },
            "generation": generation,
            "smoke": smoke,
            "all_condition_metrics": condition_summary(all_rows),
            "primary_condition_metrics": condition_summary(primary),
            "documentation_condition_metrics": condition_summary(documentation),
            "per_task": per_task_summary(all_rows),
            "per_action_type": per_action_type,
            "primary_comparisons": comparisons,
            "missing_control_analysis": missing_report,
            "behavioral_gates": behavioral_gates,
            "oracle_retention": {
                "deployment_e": retention,
                "strict_b": strict_retention,
            },
            "strict_b_vs_deployment_e": comparisons[
                "F1_strict_b_field_raw_minus_F3_deployment_e_field_raw"
            ],
            "clean_raw_nll_outcome_relationship": relationship,
            "decision": decision,
            "analysis_elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(
            args.artifact_dir / "deployable_one_step_metrics.json", summary
        )
        atomic_write_json(
            args.artifact_dir / "complete_case_missing_bound_report.json",
            missing_report,
        )
        atomic_write_json(
            args.artifact_dir / "oracle_retention_report.json",
            summary["oracle_retention"],
        )
        atomic_write_json(
            args.artifact_dir / "deployable_causal_comparisons.json", comparisons
        )
        atomic_write_json(
            args.artifact_dir / "raw_vs_signature_card_report.json",
            {
                "comparison": field_card,
                "gate_passed": content_passed,
            },
        )
        atomic_write_json(
            args.artifact_dir / "strict_b_vs_deployment_e_report.json",
            {
                "comparison": summary["strict_b_vs_deployment_e"],
                "strict_b_gate": strict_core,
                "deployment_e_gate": deployment_core,
            },
        )
        atomic_write_json(
            args.artifact_dir / "clean_raw_nll_field_outcome.json", relationship
        )
        atomic_write_text(
            args.artifact_dir / "final_exp025cr_report.md", _markdown(summary)
        )
        atomic_write_json(
            args.artifact_dir / "final_exp025cr_summary.json", summary
        )
        attempt.progress(
            status="completed",
            decision_branch=decision["decision_branch"],
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp025cr_summary.json"
            ),
        )
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
