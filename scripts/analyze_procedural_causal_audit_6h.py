from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
from statistics import mean, median
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_causal_audit_6h import paired_task_bootstrap
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


PRIMARY_METRICS = (
    "exact_primary_app_api_match",
    "canonical_procedural_signature_match",
    "execution_success",
    "normalized_observation_similarity",
    "exact_successor_match",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + end - 1) / 2.0
        for position in range(start, end):
            ranks[ordered[position]] = rank
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _pearson(_ranks(left), _ranks(right))


def _condition_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["condition_name"])].append(row)
    output: dict[str, Any] = {}
    for condition, values in sorted(grouped.items()):
        metrics: dict[str, Any] = {}
        metric_names = sorted(
            {
                key
                for value in values
                for key, item in value["metrics"].items()
                if isinstance(item, (bool, int, float))
            }
        )
        for metric in metric_names:
            metrics[metric] = mean(
                float(value["metrics"][metric]) for value in values
            )
        output[condition] = {
            "state_count": len(values),
            "task_count": len({str(value["state_task_id"]) for value in values}),
            "metrics": metrics,
            "audit_strata": dict(
                Counter(str(value["audit_stratum"]) for value in values)
            ),
            "completion_tokens_mean": mean(
                int(value["completion_tokens"]) for value in values
            ),
            "generation_seconds_mean": mean(
                float(value["generation_elapsed_ms"]) / 1000.0
                for value in values
            ),
        }
    return output


def _comparison_set(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    return {
        metric: paired_task_bootstrap(
            rows,
            left_condition=left,
            right_condition=right,
            metric=metric,
            samples=bootstrap_samples,
            seed=seed + index,
        )
        for index, metric in enumerate(PRIMARY_METRICS)
    }


def _per_task(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["state_task_id"])].append(row)
    return {
        task: _condition_summary(values)
        for task, values in sorted(grouped.items())
    }


def _same_signature_consistency(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    pairs: list[dict[str, Any]] = []
    for state_id, values in sorted(by_state.items()):
        if not all(
            name in values
            for name in ("C0_bare", "C1_raw_oracle", "C6_alternate_same_signature")
        ):
            continue
        bare = values["C0_bare"]
        canonical = values["C1_raw_oracle"]
        alternate = values["C6_alternate_same_signature"]
        canonical_effect = float(
            canonical["metrics"]["normalized_observation_similarity"]
        ) - float(bare["metrics"]["normalized_observation_similarity"])
        alternate_effect = float(
            alternate["metrics"]["normalized_observation_similarity"]
        ) - float(bare["metrics"]["normalized_observation_similarity"])
        pairs.append(
            {
                "state_example_id": state_id,
                "task_id": str(bare["state_task_id"]),
                "signature_class_id": canonical["signature_class_id"],
                "canonical_transition_id": canonical["transition_id"],
                "alternate_transition_id": alternate["transition_id"],
                "canonical_effect": canonical_effect,
                "alternate_effect": alternate_effect,
                "same_effect_direction": (
                    math.copysign(1.0, canonical_effect)
                    == math.copysign(1.0, alternate_effect)
                    if canonical_effect != 0 and alternate_effect != 0
                    else canonical_effect == alternate_effect
                ),
                "exact_api_match_agreement": canonical["metrics"][
                    "exact_primary_app_api_match"
                ]
                == alternate["metrics"]["exact_primary_app_api_match"],
                "execution_success_agreement": canonical["metrics"][
                    "execution_success"
                ]
                == alternate["metrics"]["execution_success"],
            }
        )
    canonical_effects = [float(row["canonical_effect"]) for row in pairs]
    alternate_effects = [float(row["alternate_effect"]) for row in pairs]
    within_variances = [
        ((left - right) ** 2) / 2.0
        for left, right in zip(canonical_effects, alternate_effects)
    ]
    class_means: dict[str, list[float]] = defaultdict(list)
    for row in pairs:
        class_means[str(row["signature_class_id"])].extend(
            [float(row["canonical_effect"]), float(row["alternate_effect"])]
        )
    class_mean_values = [mean(values) for values in class_means.values()]
    between_variance = (
        mean((value - mean(class_mean_values)) ** 2 for value in class_mean_values)
        if len(class_mean_values) > 1
        else 0.0
    )
    return {
        "pair_count": len(pairs),
        "task_count": len({row["task_id"] for row in pairs}),
        "same_effect_direction_fraction": _mean(
            [float(row["same_effect_direction"]) for row in pairs]
        ),
        "exact_api_match_agreement": _mean(
            [float(row["exact_api_match_agreement"]) for row in pairs]
        ),
        "execution_success_agreement": _mean(
            [float(row["execution_success_agreement"]) for row in pairs]
        ),
        "effect_size_pearson": _pearson(canonical_effects, alternate_effects),
        "effect_size_spearman": _spearman(canonical_effects, alternate_effects),
        "mean_within_class_variance": _mean(within_variances),
        "between_class_mean_variance": between_variance,
        "rows": pairs,
    }


def _relationship_analysis(
    rows: Sequence[Mapping[str, Any]],
    transition_tokens: Mapping[str, int],
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_state[str(row["state_example_id"])][str(row["condition_name"])] = row
    data: list[dict[str, Any]] = []
    for state_id, values in by_state.items():
        baseline = values.get("C0_bare")
        if baseline is None:
            continue
        for condition, row in values.items():
            if condition == "C0_bare":
                continue
            utility = row.get("raw_nll_text_utility")
            if utility is None:
                continue
            data.append(
                {
                    "state_example_id": state_id,
                    "condition_name": condition,
                    "raw_nll_text_utility": float(utility),
                    "procedural_tier": float(row.get("procedural_tier") or 0),
                    "signature_class_size": float(
                        row.get("signature_class_size") or 1
                    ),
                    "transition_tokens": float(
                        transition_tokens.get(str(row["transition_id"]), 0)
                    ),
                    "exact_api_effect": float(
                        row["metrics"]["exact_primary_app_api_match"]
                    )
                    - float(
                        baseline["metrics"]["exact_primary_app_api_match"]
                    ),
                    "signature_effect": float(
                        row["metrics"]["canonical_procedural_signature_match"]
                    )
                    - float(
                        baseline["metrics"][
                            "canonical_procedural_signature_match"
                        ]
                    ),
                    "execution_effect": float(
                        row["metrics"]["execution_success"]
                    )
                    - float(baseline["metrics"]["execution_success"]),
                    "observation_similarity_effect": float(
                        row["metrics"]["normalized_observation_similarity"]
                    )
                    - float(
                        baseline["metrics"]["normalized_observation_similarity"]
                    ),
                }
            )
    features = (
        "raw_nll_text_utility",
        "procedural_tier",
        "signature_class_size",
        "transition_tokens",
    )
    effects = (
        "exact_api_effect",
        "signature_effect",
        "execution_effect",
        "observation_similarity_effect",
    )
    correlations = {}
    for feature in features:
        correlations[feature] = {}
        x = [float(row[feature]) for row in data]
        for effect in effects:
            y = [float(row[effect]) for row in data]
            correlations[feature][effect] = {
                "pearson": _pearson(x, y),
                "spearman": _spearman(x, y),
            }
    return {
        "available_pair_count": len(data),
        "available_state_count": len({row["state_example_id"] for row in data}),
        "note": "Raw-NLL values are reused only for selected transitions present in the immutable EXP-020 148-panel cache.",
        "correlations": correlations,
        "rows": data,
    }


def _artifact_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _select_decision(
    *,
    primary_comparisons: Mapping[str, Any],
    documentation_comparisons: Mapping[str, Any],
    consistency: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    gate = settings["gates"]
    c1_c0 = primary_comparisons["C1_raw_oracle_minus_C0_bare"]
    c1_c2 = primary_comparisons["C1_raw_oracle_minus_C2_signature_only"]
    c1_c3 = primary_comparisons["C1_raw_oracle_minus_C3_hard_negative"]
    c1_c5 = primary_comparisons["C1_raw_oracle_minus_C5_unrelated"]
    exact_gain = c1_c0["exact_primary_app_api_match"]["difference"]
    signature_gain = c1_c0["canonical_procedural_signature_match"][
        "difference"
    ]
    execution_gain = c1_c0["execution_success"]["difference"]
    positive_ci = any(
        c1_c0[name]["ci95_low"] is not None
        and c1_c0[name]["ci95_low"] > 0
        for name in (
            "exact_primary_app_api_match",
            "canonical_procedural_signature_match",
        )
    )
    control_threshold = float(gate["material_control_gain"])
    beats_hard = max(
        c1_c3["exact_primary_app_api_match"]["difference"],
        c1_c3["canonical_procedural_signature_match"]["difference"],
    ) >= control_threshold
    beats_unrelated = max(
        c1_c5["exact_primary_app_api_match"]["difference"],
        c1_c5["canonical_procedural_signature_match"]["difference"],
    ) >= control_threshold
    positive_tasks = int(primary_comparisons["positive_task_count"])
    gate2 = (
        exact_gain >= float(gate["oracle_exact_api_gain"])
        and signature_gain > 0
        and execution_gain >= -float(gate["maximum_execution_drop"])
        and beats_hard
        and beats_unrelated
        and positive_tasks >= int(gate["minimum_positive_tasks"])
        and positive_ci
    )
    raw_card_exact = c1_c2["exact_primary_app_api_match"]["difference"]
    raw_card_signature = c1_c2["canonical_procedural_signature_match"][
        "difference"
    ]
    raw_card_ci = any(
        c1_c2[name]["ci95_low"] is not None
        and c1_c2[name]["ci95_low"] > 0
        for name in (
            "exact_primary_app_api_match",
            "canonical_procedural_signature_match",
        )
    )
    gate3 = (
        max(raw_card_exact, raw_card_signature)
        >= float(gate["raw_content_gain_over_signature_card"])
        and raw_card_ci
    )
    card_exact_gain = (
        primary_comparisons["C2_signature_only_minus_C0_bare"][
            "exact_primary_app_api_match"
        ]["difference"]
    )
    retention = (
        card_exact_gain / exact_gain if exact_gain > 0 else None
    )
    metadata_sufficient = (
        retention is not None
        and retention >= float(gate["metadata_gain_retention"])
        and not gate3
    )
    direction = consistency.get("same_effect_direction_fraction")
    gate4 = (
        direction is not None
        and direction >= float(gate["same_signature_direction_agreement"])
    )
    doc_exact_gain = documentation_comparisons.get(
        "C1_raw_oracle_minus_C0_bare", {}
    ).get("exact_primary_app_api_match", {}).get("difference")
    doc_dominates = (
        not gate2
        and doc_exact_gain is not None
        and doc_exact_gain >= float(gate["oracle_exact_api_gain"])
        and doc_exact_gain > exact_gain
    )
    if not gate2:
        branch = (
            "api_documentation_prompting_dominates"
            if doc_dominates
            else "procedural_oracle_not_behaviorally_helpful"
        )
    elif metadata_sufficient:
        branch = "procedural_metadata_sufficient_raw_transition_content_not_validated"
    elif not gate3:
        branch = "procedural_metadata_sufficient_raw_transition_content_not_validated"
    elif not gate4:
        branch = "canonical_procedural_signature_too_coarse"
    else:
        branch = "raw_transition_content_behaviorally_validated"
    return {
        "gate_2_procedural_oracle_behavioral_relevance": gate2,
        "gate_3_content_beyond_structured_metadata": gate3,
        "gate_4_signature_class_consistency": gate4,
        "exact_api_gain_over_bare": exact_gain,
        "signature_gain_over_bare": signature_gain,
        "execution_gain_over_bare": execution_gain,
        "beats_hard_negative": beats_hard,
        "beats_unrelated": beats_unrelated,
        "primary_action_ci_excludes_zero": positive_ci,
        "positive_task_count": positive_tasks,
        "raw_minus_card_exact_api": raw_card_exact,
        "raw_minus_card_signature": raw_card_signature,
        "raw_minus_card_ci_excludes_zero": raw_card_ci,
        "signature_card_gain_retention": retention,
        "metadata_sufficient": metadata_sufficient,
        "documentation_exact_api_gain": doc_exact_gain,
        "documentation_dominates": doc_dominates,
        "same_signature_direction_fraction": direction,
        "decision_branch": branch,
        "raw_transition_content_behaviorally_validated": branch
        == "raw_transition_content_behaviorally_validated",
        "field_training_remains_blocked": True,
    }


def _markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-024A Signature-Balanced Oracle One-Step Causal Audit",
        "",
        f"- Run UUID: `{summary['run_uuid']}`",
        f"- Conditions: {summary['condition_count']}",
        f"- Actual Qwen generation H100 hours: {summary['actual_h100_hours']:.4f}",
        f"- Decision branch: `{summary['decision']['decision_branch']}`",
        f"- Raw transition content validated: {summary['decision']['raw_transition_content_behaviorally_validated']}",
        "- Field training remains blocked pending review.",
        "",
        "## Primary non-documentation comparisons",
        "",
        "| Comparison | Exact API delta | Signature delta | Execution delta |",
        "|---|---:|---:|---:|",
    ]
    for name, comparison in summary["primary_comparisons"].items():
        if not isinstance(comparison, dict) or "exact_primary_app_api_match" not in comparison:
            continue
        lines.append(
            f"| {name} | {comparison['exact_primary_app_api_match']['difference']:.4f} | "
            f"{comparison['canonical_procedural_signature_match']['difference']:.4f} | "
            f"{comparison['execution_success']['difference']:.4f} |"
        )
    lines.extend(
        [
            "",
            "All confidence intervals, per-task rows, documentation strata, same-signature "
            "checks, and raw-NLL relationships are stored in the JSON reports beside this file.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_procedural_causal_audit_6h.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024a")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    config_sha256 = sha256_file(args.config)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    condition_manifest = _load_json(args.artifact_dir / "condition_manifest.json")
    strata = _load_json(args.artifact_dir / "audit_state_strata.json")
    replay = _load_json(args.artifact_dir / "replay" / "replay_summary.json")
    generation = _load_json(args.artifact_dir / "generation_summary.json")
    if not bool(replay["all_states_passed"]):
        raise RuntimeError("appworld_one_step_replay_invalid")
    output_paths = sorted((args.artifact_dir / "condition_outputs").glob("*.json"))
    rows = [_load_json(path) for path in output_paths]
    if len(rows) != int(condition_manifest["condition_count"]):
        raise ValueError(
            f"Generation outputs incomplete: {len(rows)} != "
            f"{condition_manifest['condition_count']}"
        )
    if len({row["condition_key"] for row in rows}) != len(rows):
        raise ValueError("Duplicate generation condition keys")
    data_hashes = {
        "condition_manifest": sha256_file(args.artifact_dir / "condition_manifest.json"),
        "strata": sha256_file(args.artifact_dir / "audit_state_strata.json"),
        "replay": sha256_file(args.artifact_dir / "replay" / "replay_summary.json"),
        "generation": sha256_file(args.artifact_dir / "generation_summary.json"),
    }

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="causal_metrics_and_scientific_gate",
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
        bootstrap_samples = int(settings["metrics"]["bootstrap_samples"])
        bootstrap_seed = int(settings["metrics"]["bootstrap_seed"])
        primary_rows = [row for row in rows if row["audit_stratum"] in {"A", "B"}]
        documentation_rows = [row for row in rows if row["audit_stratum"] == "C"]
        diagnostics_rows = [row for row in rows if row["audit_stratum"] in {"D", "E"}]

        comparisons = {}
        pairs = (
            ("C1_raw_oracle", "C0_bare"),
            ("C1_raw_oracle", "C2_signature_only"),
            ("C1_raw_oracle", "C3_hard_negative"),
            ("C1_raw_oracle", "C4_signature_popularity"),
            ("C1_raw_oracle", "C5_unrelated"),
            ("C2_signature_only", "C0_bare"),
        )
        for index, (left, right) in enumerate(pairs):
            comparisons[f"{left}_minus_{right}"] = _comparison_set(
                primary_rows,
                left=left,
                right=right,
                bootstrap_samples=bootstrap_samples,
                seed=bootstrap_seed + index * 100,
            )
        by_task_state: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in primary_rows:
            by_task_state[str(row["state_task_id"])][
                str(row["condition_name"])
            ].append(row)
        positive_tasks = 0
        task_deltas = {}
        for task, values in sorted(by_task_state.items()):
            baseline = values.get("C0_bare", [])
            oracle = values.get("C1_raw_oracle", [])
            baseline_exact = _mean(
                [float(row["metrics"]["exact_primary_app_api_match"]) for row in baseline]
            )
            oracle_exact = _mean(
                [float(row["metrics"]["exact_primary_app_api_match"]) for row in oracle]
            )
            baseline_signature = _mean(
                [float(row["metrics"]["canonical_procedural_signature_match"]) for row in baseline]
            )
            oracle_signature = _mean(
                [float(row["metrics"]["canonical_procedural_signature_match"]) for row in oracle]
            )
            exact_delta = (
                oracle_exact - baseline_exact
                if oracle_exact is not None and baseline_exact is not None
                else None
            )
            signature_delta = (
                oracle_signature - baseline_signature
                if oracle_signature is not None and baseline_signature is not None
                else None
            )
            positive = bool(
                (exact_delta is not None and exact_delta > 0)
                or (signature_delta is not None and signature_delta > 0)
            )
            positive_tasks += positive
            task_deltas[task] = {
                "exact_api_delta": exact_delta,
                "signature_delta": signature_delta,
                "positive_relative_behavior": positive,
            }
        comparisons["positive_task_count"] = positive_tasks
        comparisons["task_deltas"] = task_deltas

        documentation_comparisons = {}
        if documentation_rows:
            for index, (left, right) in enumerate(pairs):
                documentation_comparisons[f"{left}_minus_{right}"] = _comparison_set(
                    documentation_rows,
                    left=left,
                    right=right,
                    bootstrap_samples=bootstrap_samples,
                    seed=bootstrap_seed + 1000 + index * 100,
                )
        consistency = _same_signature_consistency(rows)

        transition_manifest = _load_rows(
            Path(settings["exp017_artifact"]) / "transition_manifest.jsonl"
        )
        transition_tokens = {
            str(row["transition_id"]): int(row["teacher_section_tokens"])
            for row in transition_manifest
        }
        relationship = _relationship_analysis(rows, transition_tokens)
        decision = _select_decision(
            primary_comparisons=comparisons,
            documentation_comparisons=documentation_comparisons,
            consistency=consistency,
            settings=settings,
        )
        actual_h100_hours = sum(
            float(row["generation_elapsed_ms"]) for row in rows
        ) / 3_600_000.0
        summary = {
            "format": "procedural_causal_audit_summary_6h_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "condition_count": len(rows),
            "state_count": int(strata["state_count"]),
            "task_count": int(strata["task_count"]),
            "strata": strata["stratum_state_counts"],
            "replay": replay,
            "generation": generation,
            "actual_h100_hours": actual_h100_hours,
            "condition_metrics_all": _condition_summary(rows),
            "condition_metrics_primary_non_documentation": _condition_summary(
                primary_rows
            ),
            "condition_metrics_api_documentation_only": _condition_summary(
                documentation_rows
            ),
            "condition_metrics_diagnostic_D_E": _condition_summary(
                diagnostics_rows
            ),
            "per_task": _per_task(rows),
            "primary_comparisons": comparisons,
            "documentation_comparisons": documentation_comparisons,
            "same_signature_consistency": consistency,
            "raw_nll_relationship": relationship,
            "decision": decision,
            "analysis_elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(args.artifact_dir / "one_step_metrics.json", {
            "all": summary["condition_metrics_all"],
            "primary": summary["condition_metrics_primary_non_documentation"],
            "documentation": summary["condition_metrics_api_documentation_only"],
            "diagnostic": summary["condition_metrics_diagnostic_D_E"],
            "per_task": summary["per_task"],
        })
        atomic_write_json(args.artifact_dir / "causal_comparisons.json", {
            "primary": comparisons,
            "documentation": documentation_comparisons,
        })
        atomic_write_json(args.artifact_dir / "same_signature_consistency.json", consistency)
        atomic_write_json(args.artifact_dir / "raw_nll_behavior_relationship.json", relationship)
        atomic_write_json(args.artifact_dir / "final_exp024a_summary.json", summary)
        atomic_write_text(args.artifact_dir / "final_exp024a_report.md", _markdown(summary))
        atomic_write_text(
            args.artifact_dir / "signature_only_vs_raw_content_report.md",
            _markdown(summary),
        )
        atomic_write_text(
            args.artifact_dir / "same_signature_consistency_report.md",
            "# Same-Signature Consistency\n\n"
            f"Pairs: {consistency['pair_count']}\n\n"
            f"Same direction: {consistency['same_effect_direction_fraction']}\n\n"
            f"Exact API agreement: {consistency['exact_api_match_agreement']}\n\n"
            f"Execution agreement: {consistency['execution_success_agreement']}\n",
        )
        atomic_write_text(
            args.artifact_dir / "raw_nll_vs_one_step_outcome_report.md",
            "# Raw-NLL Versus One-Step Outcome\n\n"
            f"Immutable comparator pairs available: {relationship['available_pair_count']}.\n\n"
            "See `raw_nll_behavior_relationship.json` for correlations.\n",
        )
        summary["artifact_bytes"] = _artifact_size(args.artifact_dir)
        atomic_write_json(args.artifact_dir / "final_exp024a_summary.json", summary)
        attempt.progress(
            phase="causal_metrics_and_scientific_gate_complete",
            decision_branch=decision["decision_branch"],
            raw_content_validated=decision[
                "raw_transition_content_behaviorally_validated"
            ],
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp024a_summary.json"
            ),
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
