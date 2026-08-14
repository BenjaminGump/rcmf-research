from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import statistics
from typing import Any

from rcmf.training.state_conditioned_transition_6b import canonical_json_sha256


LEARNING_CURVE_VERSION = "task_grouped_query_coverage_learning_curve_6c_v1"


def build_nested_task_learning_curve_manifest(
    rows: Sequence[Mapping[str, Any]],
    *,
    task_counts: Sequence[int],
    folds: int,
    seed: int,
) -> dict[str, Any]:
    tasks = sorted({str(row["state_task_id"]) for row in rows})
    parents = sorted({str(row["transition_parent_id"]) for row in rows})
    normalized_counts = sorted({int(value) for value in task_counts})
    if not tasks or not parents:
        raise ValueError("Learning-curve manifest requires tasks and parents")
    if normalized_counts[-1] != len(tasks):
        raise ValueError("Largest learning-curve task count must use every task")
    if normalized_counts[0] <= 0 or int(folds) <= 0:
        raise ValueError("Learning-curve task counts and folds must be positive")
    output_folds = []
    for fold in range(int(folds)):
        order = sorted(
            tasks,
            key=lambda task: (
                hashlib.sha256(f"{seed}:{fold}:{task}".encode("utf-8")).hexdigest(),
                task,
            ),
        )
        levels = []
        previous: set[str] = set()
        for count in normalized_counts:
            selected = set(order[:count])
            if not previous.issubset(selected):
                raise RuntimeError("Learning-curve task sets are not nested")
            selected_rows = [
                row for row in rows if str(row["state_task_id"]) in selected
            ]
            parent_coverage = sorted(
                {str(row["transition_parent_id"]) for row in selected_rows}
            )
            levels.append(
                {
                    "task_count": count,
                    "task_ids": sorted(selected),
                    "pair_count": len(selected_rows),
                    "state_count": len(
                        {str(row["state_example_id"]) for row in selected_rows}
                    ),
                    "transition_parent_count": len(parent_coverage),
                    "transition_parent_ids": parent_coverage,
                    "all_parent_coverage": parent_coverage == parents,
                    "pair_ids_sha256": hashlib.sha256(
                        "\n".join(
                            sorted(str(row["pair_id"]) for row in selected_rows)
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            )
            previous = selected
        output_folds.append({"fold": fold, "task_order": order, "levels": levels})
    manifest = {
        "format": LEARNING_CURVE_VERSION,
        "seed": int(seed),
        "fold_count": int(folds),
        "available_task_count": len(tasks),
        "available_task_ids": tasks,
        "available_transition_parent_count": len(parents),
        "available_transition_parent_ids": parents,
        "task_counts": normalized_counts,
        "folds": output_folds,
        "selection": "sha256-ordered nested task sets; all legal cell-A transitions retained",
    }
    manifest["manifest_sha256"] = canonical_json_sha256(manifest)
    return manifest


def summarize_learning_curves(
    rows: Sequence[Mapping[str, Any]],
    *,
    ndcg_rising_threshold: float = 0.03,
    residual_rising_threshold: float = 0.05,
    ndcg_instability_threshold: float = 0.08,
    residual_instability_threshold: float = 0.10,
) -> dict[str, Any]:
    grouped: dict[str, dict[int, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row["model_kind"])][int(row["task_count"])].append(row)
    models = {}
    for kind, levels in sorted(grouped.items()):
        summaries = {}
        for count, selected in sorted(levels.items()):
            ndcg = [float(row["ndcg@4"]) for row in selected]
            residual = [float(row["interaction_residual_spearman"]) for row in selected]
            raw = [float(row["pooled_raw_spearman"]) for row in selected]
            per_state = [float(row["mean_per_state_spearman"]) for row in selected]
            summaries[count] = {
                "fold_count": len(selected),
                "ndcg@4_mean": statistics.fmean(ndcg),
                "ndcg@4_std": statistics.pstdev(ndcg) if len(ndcg) > 1 else 0.0,
                "interaction_residual_spearman_mean": statistics.fmean(residual),
                "interaction_residual_spearman_std": (
                    statistics.pstdev(residual) if len(residual) > 1 else 0.0
                ),
                "pooled_raw_spearman_mean": statistics.fmean(raw),
                "pooled_raw_spearman_std": (
                    statistics.pstdev(raw) if len(raw) > 1 else 0.0
                ),
                "mean_per_state_spearman_mean": statistics.fmean(per_state),
                "mean_per_state_spearman_std": (
                    statistics.pstdev(per_state) if len(per_state) > 1 else 0.0
                ),
            }
        counts = sorted(summaries)
        if len(counts) < 2:
            raise ValueError("Learning curve needs at least two task-count levels")
        previous = summaries[counts[-2]]
        final = summaries[counts[-1]]
        ndcg_gain = float(final["ndcg@4_mean"]) - float(previous["ndcg@4_mean"])
        residual_gain = float(final["interaction_residual_spearman_mean"]) - float(
            previous["interaction_residual_spearman_mean"]
        )
        still_rising = (
            ndcg_gain >= float(ndcg_rising_threshold)
            or residual_gain >= float(residual_rising_threshold)
        )
        unstable = (
            float(final["ndcg@4_std"]) >= float(ndcg_instability_threshold)
            or float(final["interaction_residual_spearman_std"])
            >= float(residual_instability_threshold)
        )
        models[kind] = {
            "levels": {str(key): value for key, value in summaries.items()},
            "final_interval": f"{counts[-2]}_to_{counts[-1]}_tasks",
            "final_ndcg@4_gain": ndcg_gain,
            "final_interaction_residual_spearman_gain": residual_gain,
            "still_materially_rising_at_maximum": still_rising,
            "unstable_across_folds": unstable,
            "saturated": not still_rising and not unstable,
        }
    return {
        "format": LEARNING_CURVE_VERSION,
        "thresholds": {
            "ndcg_rising": float(ndcg_rising_threshold),
            "residual_spearman_rising": float(residual_rising_threshold),
            "ndcg_instability_std": float(ndcg_instability_threshold),
            "residual_spearman_instability_std": float(
                residual_instability_threshold
            ),
        },
        "models": models,
    }


def expanded_query_cache_projection(
    *,
    current_queries: int,
    legal_pairs: int,
    scoreable_pairs: int,
    over_context_pairs: int,
    query_counts: Sequence[int],
    seconds_per_scoreable_pair: float,
) -> dict[str, Any]:
    if legal_pairs != scoreable_pairs + over_context_pairs:
        raise ValueError("Current legal pair accounting is inconsistent")
    rows = []
    for query_count in query_counts:
        factor = int(query_count) / int(current_queries)
        legal = round(legal_pairs * factor)
        scoreable = round(scoreable_pairs * factor)
        over_context = round(over_context_pairs * factor)
        rows.append(
            {
                "query_count": int(query_count),
                "projected_legal_pairs": legal,
                "projected_scoreable_pairs": scoreable,
                "projected_over_context_pairs": over_context,
                "projected_h100_hours": scoreable
                * float(seconds_per_scoreable_pair)
                / 3600.0,
            }
        )
    return {
        "format": "expanded_query_teacher_cache_projection_6c_v1",
        "method": "linear projection from immutable EXP-017 exact pair counts",
        "seconds_per_scoreable_pair": float(seconds_per_scoreable_pair),
        "rows": rows,
        "launched": False,
    }

