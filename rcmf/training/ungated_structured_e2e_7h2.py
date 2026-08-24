from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import math
import statistics
from typing import Any

import numpy as np


GLOBAL_SEED = 25101
QUANTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)


def stable_key(*values: object) -> str:
    payload = ":".join(str(value) for value in (GLOBAL_SEED, *values))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def freeze_transition_shuffle(
    transition_ids: Sequence[str], class_by_transition: Mapping[str, str]
) -> dict[str, str]:
    """Freeze an outcome-blind different-class transition control for every memory."""

    ids = sorted(map(str, transition_ids))
    if len(ids) != len(set(ids)):
        raise ValueError("Transition shuffle source contains duplicate identities")
    mapping: dict[str, str] = {}
    for transition_id in ids:
        own_class = str(class_by_transition[transition_id])
        candidates = [
            value
            for value in ids
            if value != transition_id
            and str(class_by_transition[value]) != own_class
        ]
        if not candidates:
            candidates = [value for value in ids if value != transition_id]
        if not candidates:
            raise ValueError("Transition shuffle requires at least two transitions")
        mapping[transition_id] = min(
            candidates,
            key=lambda value: stable_key(
                "ungated-live-transition-shuffle", transition_id, value
            ),
        )
    return mapping


def _quantiles(values: np.ndarray) -> dict[str, float]:
    return {
        f"p{int(100 * quantile):02d}": float(np.quantile(values, quantile))
        for quantile in QUANTILES
    }


def summarize_vector(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
        raise ValueError("Summary values must be a finite nonempty vector")
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "std": float(array.std()),
        "min": float(array.min()),
        "max": float(array.max()),
        **_quantiles(array),
    }


def feature_distribution_report(
    *,
    names: Sequence[str],
    train_values: Sequence[Sequence[float]],
    validation_values: Sequence[Sequence[float]],
    live_values: Sequence[Sequence[float]],
    standardizer_mean: Sequence[float],
    standardizer_std: Sequence[float],
) -> dict[str, Any]:
    train = np.asarray(train_values, dtype=np.float64)
    validation = np.asarray(validation_values, dtype=np.float64)
    live = np.asarray(live_values, dtype=np.float64)
    mean = np.asarray(standardizer_mean, dtype=np.float64)
    std = np.asarray(standardizer_std, dtype=np.float64)
    expected = (len(names),)
    if train.ndim != 2 or validation.ndim != 2 or live.ndim != 2:
        raise ValueError("Feature matrices must be rank two")
    if train.shape[1:] != expected or validation.shape[1:] != expected or live.shape[1:] != expected:
        raise ValueError("Feature matrix width differs from frozen feature order")
    if mean.shape != expected or std.shape != expected or np.any(std <= 0):
        raise ValueError("Frozen gate standardizer is invalid")
    if not all(np.isfinite(value).all() for value in (train, validation, live, mean, std)):
        raise ValueError("Feature audit contains non-finite values")

    train_min = train.min(axis=0)
    train_max = train.max(axis=0)
    rows = []
    for index, name in enumerate(names):
        validation_column = validation[:, index]
        live_column = live[:, index]
        pooled = math.sqrt(
            (float(validation_column.var()) + float(live_column.var())) / 2.0
        )
        standardized_mean_difference = (
            (float(live_column.mean()) - float(validation_column.mean())) / pooled
            if pooled > 1.0e-12
            else 0.0
        )
        rows.append(
            {
                "feature": str(name),
                "validation": summarize_vector(validation_column),
                "live": summarize_vector(live_column),
                "missing_rate_validation": 0.0,
                "missing_rate_live": 0.0,
                "standardized_mean_difference": standardized_mean_difference,
                "absolute_standardized_mean_difference": abs(
                    standardized_mean_difference
                ),
                "absolute_live_z_mean": abs(
                    (float(live_column.mean()) - float(mean[index]))
                    / float(std[index])
                ),
                "live_out_of_training_range_fraction": float(
                    np.mean(
                        (live_column < train_min[index])
                        | (live_column > train_max[index])
                    )
                ),
                "validation_out_of_training_range_fraction": float(
                    np.mean(
                        (validation_column < train_min[index])
                        | (validation_column > train_max[index])
                    )
                ),
            }
        )
    return {
        "feature_count": len(names),
        "train_row_count": int(train.shape[0]),
        "validation_row_count": int(validation.shape[0]),
        "live_row_count": int(live.shape[0]),
        "rows": rows,
        "top_absolute_standardized_mean_difference": sorted(
            rows,
            key=lambda row: (
                -float(row["absolute_standardized_mean_difference"]),
                str(row["feature"]),
            ),
        )[:20],
    }


def _roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    positive = scores[labels == 1]
    negative = scores[labels == 0]
    if positive.size == 0 or negative.size == 0:
        raise ValueError("AUC requires both domains")
    comparisons = (
        (positive[:, None] > negative[None, :]).mean()
        + 0.5 * (positive[:, None] == negative[None, :]).mean()
    )
    return float(comparisons)


def domain_classifier_audit(
    validation_values: Sequence[Sequence[float]],
    live_values: Sequence[Sequence[float]],
) -> dict[str, Any]:
    """Fit a deterministic balanced logistic domain classifier and report heldout AUC."""

    validation = np.asarray(validation_values, dtype=np.float64)
    live = np.asarray(live_values, dtype=np.float64)
    keyed = []
    for label, matrix in ((0, validation), (1, live)):
        for index, row in enumerate(matrix):
            keyed.append((stable_key("domain-split", label, index), label, row))
    train_rows = [row for key, label, row in keyed if int(key[:8], 16) % 5 != 0]
    train_labels = [label for key, label, row in keyed if int(key[:8], 16) % 5 != 0]
    test_rows = [row for key, label, row in keyed if int(key[:8], 16) % 5 == 0]
    test_labels = [label for key, label, row in keyed if int(key[:8], 16) % 5 == 0]
    x_train = np.asarray(train_rows, dtype=np.float64)
    y_train = np.asarray(train_labels, dtype=np.float64)
    x_test = np.asarray(test_rows, dtype=np.float64)
    y_test = np.asarray(test_labels, dtype=np.int64)
    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0)
    std[std < 1.0e-6] = 1.0
    x_train = (x_train - mean) / std
    x_test = (x_test - mean) / std
    weights = np.zeros(x_train.shape[1], dtype=np.float64)
    bias = 0.0
    class_weight = np.where(
        y_train == 1,
        0.5 / max(float((y_train == 1).mean()), 1.0e-12),
        0.5 / max(float((y_train == 0).mean()), 1.0e-12),
    )
    for _ in range(300):
        logits = np.clip(x_train @ weights + bias, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-logits))
        error = class_weight * (probability - y_train)
        weights -= 0.03 * (x_train.T @ error / len(x_train) + 1.0e-3 * weights)
        bias -= 0.03 * float(error.mean())
    scores = x_test @ weights + bias
    return {
        "method": "balanced_logistic_domain_classifier_7h2_v1",
        "train_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "heldout_auc": _roc_auc(y_test, scores),
        "weight_l2": float(np.linalg.norm(weights)),
    }


def classify_distribution_shift(
    *, domain_auc: float, feature_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    large = sum(
        float(row["absolute_standardized_mean_difference"]) >= 0.5
        for row in feature_rows
    )
    fraction = large / max(1, len(feature_rows))
    maximum = max(
        float(row["absolute_standardized_mean_difference"])
        for row in feature_rows
    )
    broad = domain_auc >= 0.75 or fraction >= 0.10 or maximum >= 1.5
    return {
        "classification": (
            "broad_feature_state_distribution_shift"
            if broad
            else "no_meaningful_shift_despite_zero_activation"
        ),
        "rules": {
            "domain_auc_at_least_0_75": domain_auc >= 0.75,
            "feature_fraction_abs_smd_at_least_0_5_at_least_0_10": fraction >= 0.10,
            "maximum_abs_smd_at_least_1_5": maximum >= 1.5,
        },
        "large_shift_feature_count": large,
        "large_shift_feature_fraction": fraction,
        "maximum_absolute_standardized_mean_difference": maximum,
    }


def freeze_fresh_test_manifest(
    *, all_task_ids: Sequence[str], exposed: Mapping[str, Sequence[str]], count: int = 37
) -> dict[str, Any]:
    all_ids = sorted(set(map(str, all_task_ids)))
    exposed_ids = sorted(set(exposed).intersection(all_ids))
    untouched = sorted(set(all_ids) - set(exposed_ids))
    selected = sorted(
        untouched,
        key=lambda task_id: hashlib.sha256(
            f"{GLOBAL_SEED}:fresh-final-test:{task_id}".encode("utf-8")
        ).hexdigest(),
    )[:count]
    status = "frozen" if len(selected) == count else "insufficient_untouched_tasks"
    return {
        "format": "fresh_test37_post_exp028b_manifest_v1",
        "global_seed": GLOBAL_SEED,
        "selection_rule": "ascending_sha256(25101:fresh-final-test:+task_id)",
        "all_test_normal_count": len(all_ids),
        "exposed_task_count": len(exposed_ids),
        "untouched_task_count": len(untouched),
        "requested_task_count": count,
        "selected_task_count": len(selected),
        "task_ids": selected,
        "status": status,
        "outcomes_inspected_for_selection": False,
        "exposure_sources": {
            task_id: sorted(map(str, exposed[task_id])) for task_id in exposed_ids
        },
    }
