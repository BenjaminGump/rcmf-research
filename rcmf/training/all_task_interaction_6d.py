from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import math
import statistics
from typing import Any

from rcmf.benchmarks.appworld.transitions import API_CALL_RE, _action_type
from rcmf.schemas import DecisionExample
from rcmf.training.state_conditioned_transition_6b import canonical_json_sha256
from rcmf.training.transition_memory_6a import (
    decoder_manifest_state_ids,
    example_task_id,
    state_example_id,
)


ALL_TASK_QUERY_MANIFEST_VERSION = "all_task_transition_query_manifest_6d_v1"
LEARNING_CURVE_MANIFEST_VERSION = "nested_all_task_query_learning_curve_6d_v1"
REUSE_VALIDATION_VERSION = "exp017_transition_teacher_reuse_validation_6d_v1"
RUNTIME_PROJECTION_VERSION = "all_task_interaction_runtime_projection_6d_v1"


def _stable_key(seed: int, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{namespace}:{value}".encode("utf-8")).hexdigest()


def _quantile(values: Sequence[int], fraction: float) -> int:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        raise ValueError("Cannot compute a prompt-length quantile from no values")
    index = int(math.floor((len(ordered) - 1) * float(fraction) + 0.5))
    return ordered[index]


def _length_bucket(value: int, q1: int, q2: int) -> str:
    if int(value) <= int(q1):
        return "short"
    if int(value) <= int(q2):
        return "medium"
    return "long"


def _task_family(task_id: str) -> str:
    return str(task_id).rsplit("_", 1)[0]


def _task_apps(examples: Sequence[DecisionExample]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = defaultdict(set)
    for example in examples:
        task_id = example_task_id(example)
        values[task_id].update(
            app
            for app, _ in API_CALL_RE.findall(example.target_text)
            if app not in {"api_docs", "supervisor"}
        )
    return {
        task_id: sorted(apps) if apps else ["unknown"]
        for task_id, apps in values.items()
    }


def _candidate_rows_by_task(
    *,
    examples: Sequence[DecisionExample],
    prompt_token_counts: Sequence[int],
    allowed_task_ids: set[str],
    excluded_state_ids: set[str],
    q1: int,
    q2: int,
) -> dict[str, list[dict[str, Any]]]:
    max_step: dict[str, int] = defaultdict(int)
    for example in examples:
        task_id = example_task_id(example)
        max_step[task_id] = max(max_step[task_id], int(example.step_id))
    apps = _task_apps(examples)
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, example in enumerate(examples):
        task_id = example_task_id(example)
        identity = state_example_id(index, example)
        if task_id not in allowed_task_ids or identity in excluded_state_ids:
            continue
        calls = [f"{app}.{api}" for app, api in API_CALL_RE.findall(example.target_text)]
        step_count = int(max_step[task_id])
        output[task_id].append(
            {
                "example_index": int(index),
                "state_example_id": identity,
                "task_id": task_id,
                "task_family": _task_family(task_id),
                "apps": list(apps.get(task_id, ["unknown"])),
                "step_id": int(example.step_id),
                "step_count": step_count,
                "step_ratio": (
                    0.0
                    if step_count <= 1
                    else (int(example.step_id) - 1) / (step_count - 1)
                ),
                "prompt_tokens": int(prompt_token_counts[index]),
                "prompt_length_bucket": _length_bucket(
                    int(prompt_token_counts[index]), q1, q2
                ),
                "target_action_type": _action_type(example.target_text),
                "target_api_names": sorted(set(calls)),
            }
        )
    return {
        task_id: sorted(rows, key=lambda row: (row["step_id"], row["example_index"]))
        for task_id, rows in output.items()
    }


def _pick_early_late(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    if len(rows) == 1:
        return [{**dict(rows[0]), "selection_role": "available_single"}]
    early = min(
        rows,
        key=lambda row: (
            abs(float(row["step_ratio"]) - 1.0 / 3.0),
            int(row["step_id"]),
            int(row["example_index"]),
        ),
    )
    remaining = [row for row in rows if row["state_example_id"] != early["state_example_id"]]
    later = min(
        remaining,
        key=lambda row: (
            abs(float(row["step_ratio"]) - 2.0 / 3.0),
            -int(row["step_id"]),
            int(row["example_index"]),
        ),
    )
    ordered = sorted((early, later), key=lambda row: int(row["step_id"]))
    return [
        {**dict(ordered[0]), "selection_role": "earlier"},
        {**dict(ordered[1]), "selection_role": "later"},
    ]


def select_all_task_query_manifest(
    *,
    examples: Sequence[DecisionExample],
    prompt_token_counts: Sequence[int],
    split_manifest: Mapping[str, Any],
    decoder_manifest: Mapping[str, Any],
    original_query_manifest: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    if len(examples) != len(prompt_token_counts):
        raise ValueError("Prompt token counts must align one-to-one with examples")
    train_tasks = {str(value) for value in split_manifest["train_task_ids"]}
    validation_tasks = {str(value) for value in split_manifest["validation_task_ids"]}
    if len(train_tasks) != 37 or len(validation_tasks) != 9:
        raise ValueError(
            f"Locked task split differs: train={len(train_tasks)} validation={len(validation_tasks)}"
        )
    original_rows = [dict(row) for row in original_query_manifest["query_rows"]]
    if len(original_rows) != 32:
        raise ValueError(f"Expected 32 immutable source query rows, found {len(original_rows)}")
    original_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in original_rows:
        original_by_task[str(row["task_id"])].append(row)
    excluded = decoder_manifest_state_ids(decoder_manifest)
    q1 = _quantile(prompt_token_counts, 1.0 / 3.0)
    q2 = _quantile(prompt_token_counts, 2.0 / 3.0)
    candidates = _candidate_rows_by_task(
        examples=examples,
        prompt_token_counts=prompt_token_counts,
        allowed_task_ids=train_tasks.union(validation_tasks),
        excluded_state_ids=excluded,
        q1=q1,
        q2=q2,
    )
    rows: list[dict[str, Any]] = []
    shortages: list[dict[str, Any]] = []
    for split, task_ids in (("train", train_tasks), ("validation", validation_tasks)):
        for task_id in sorted(task_ids):
            if task_id in original_by_task:
                selected = []
                candidate_by_id = {
                    str(row["state_example_id"]): row for row in candidates.get(task_id, [])
                }
                for source in sorted(
                    original_by_task[task_id],
                    key=lambda row: (int(row["step_id"]), int(row["example_index"])),
                ):
                    identity = str(source["state_example_id"])
                    if identity not in candidate_by_id:
                        raise ValueError(
                            f"Immutable EXP-017 state is not an eligible candidate: {identity}"
                        )
                    candidate = candidate_by_id[identity]
                    for key in ("example_index", "step_id", "prompt_tokens"):
                        if int(candidate[key]) != int(source[key]):
                            raise ValueError(f"Immutable EXP-017 query differs for {identity}: {key}")
                    selected.append(
                        {
                            **candidate,
                            "selection_role": str(source["selection_role"]),
                            "selection_source": "immutable_exp017_subset",
                        }
                    )
            else:
                selected = [
                    {**row, "selection_source": "all_task_early_late_rule"}
                    for row in _pick_early_late(candidates.get(task_id, []))
                ]
            if len(selected) < 2:
                shortages.append(
                    {
                        "split": split,
                        "task_id": task_id,
                        "available_legal_candidate_count": len(candidates.get(task_id, [])),
                        "selected_count": len(selected),
                        "reason": "fewer_than_two_states_after_locked_exp016c_exclusion",
                    }
                )
            rows.extend({**row, "split": split} for row in selected)
    rows.sort(key=lambda row: (str(row["split"]), str(row["task_id"]), int(row["step_id"])))
    selected_ids = {str(row["state_example_id"]) for row in rows}
    original_ids = {str(row["state_example_id"]) for row in original_rows}
    if not original_ids.issubset(selected_ids):
        raise ValueError("The immutable EXP-017 query set is not a subset of EXP-020")
    if len(selected_ids) != len(rows):
        raise ValueError("Expanded query manifest contains duplicate state IDs")
    payload = {
        "format": ALL_TASK_QUERY_MANIFEST_VERSION,
        "seed": int(seed),
        "selection_definition": (
            "all_locked_37_train_9_validation_tasks_two_states_when_available; "
            "immutable_exp017_rows_preserved; remaining tasks use closest one-third/two-thirds "
            "steps after locked EXP-016C exclusion; no utility or model output used"
        ),
        "query_rows": rows,
        "query_count": len(rows),
        "train_query_count": sum(row["split"] == "train" for row in rows),
        "validation_query_count": sum(row["split"] == "validation" for row in rows),
        "train_task_ids": sorted(train_tasks),
        "validation_task_ids": sorted(validation_tasks),
        "task_shortages": shortages,
        "original_query_count": len(original_rows),
        "original_query_ids_sha256": canonical_json_sha256(sorted(original_ids)),
        "original_query_subset_exact": original_ids.issubset(selected_ids),
        "original_query_manifest_sha256": canonical_json_sha256(original_query_manifest),
        "excluded_exp016c_state_count": len(excluded),
        "excluded_exp016c_state_ids_sha256": canonical_json_sha256(sorted(excluded)),
        "selected_exp016c_overlap": sorted(selected_ids.intersection(excluded)),
        "prompt_length_quantiles": {"q1": q1, "q2": q2},
        "prompt_length_bucket_counts": dict(
            Counter(str(row["prompt_length_bucket"]) for row in rows)
        ),
        "selection_role_counts": dict(Counter(str(row["selection_role"]) for row in rows)),
        "target_action_type_counts": dict(
            Counter(str(row["target_action_type"]) for row in rows)
        ),
        "app_counts": dict(Counter(app for row in rows for app in row.get("apps", []))),
        "source_split_manifest_sha256": canonical_json_sha256(split_manifest),
        "source_decoder_manifest_sha256": canonical_json_sha256(decoder_manifest),
    }
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    return payload


def build_fixed_learning_curve_manifest(
    query_manifest: Mapping[str, Any],
    original_query_manifest: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    train_rows = [row for row in query_manifest["query_rows"] if row["split"] == "train"]
    validation_rows = [
        row for row in query_manifest["query_rows"] if row["split"] == "validation"
    ]
    all_train_tasks = sorted({str(row["task_id"]) for row in train_rows})
    original_train_tasks = sorted(
        {
            str(row["task_id"])
            for row in original_query_manifest["query_rows"]
            if row["split"] == "train"
        }
    )
    if len(all_train_tasks) != 37 or len(original_train_tasks) != 12:
        raise ValueError("LC37/LC12 task counts differ from the locked definitions")
    remaining = sorted(
        set(all_train_tasks) - set(original_train_tasks),
        key=lambda task: (_stable_key(seed, "lc24-addition", task), task),
    )
    lc24_tasks = sorted([*original_train_tasks, *remaining[:12]])
    levels = []
    previous: set[str] = set()
    for name, tasks in (
        ("LC12", original_train_tasks),
        ("LC24", lc24_tasks),
        ("LC37", all_train_tasks),
    ):
        selected = set(tasks)
        if not previous.issubset(selected):
            raise RuntimeError("All-task learning-curve levels are not nested")
        selected_rows = [row for row in train_rows if str(row["task_id"]) in selected]
        levels.append(
            {
                "name": name,
                "task_count": len(selected),
                "state_count": len(selected_rows),
                "task_ids": sorted(selected),
                "state_example_ids": sorted(str(row["state_example_id"]) for row in selected_rows),
                "state_example_ids_sha256": canonical_json_sha256(
                    sorted(str(row["state_example_id"]) for row in selected_rows)
                ),
                "app_counts": dict(
                    Counter(app for row in selected_rows for app in row.get("apps", []))
                ),
            }
        )
        previous = selected
    payload = {
        "format": LEARNING_CURVE_MANIFEST_VERSION,
        "seed": int(seed),
        "selection": (
            "LC12 is the immutable EXP-017 train-task set; LC24 adds 12 tasks by seeded "
            "SHA256 order; LC37 contains all locked train tasks"
        ),
        "levels": levels,
        "heldout_task_count": len({str(row["task_id"]) for row in validation_rows}),
        "heldout_state_count": len(validation_rows),
        "heldout_task_ids": sorted({str(row["task_id"]) for row in validation_rows}),
        "heldout_state_example_ids": sorted(
            str(row["state_example_id"]) for row in validation_rows
        ),
    }
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    return payload


PREFLIGHT_REUSE_KEYS = (
    "pair_id",
    "state_example_id",
    "example_index",
    "task_id",
    "episode_id",
    "step_id",
    "transition_id",
    "parent_memory_id",
    "parent_task_id",
    "parent_episode_id",
    "leakage_keys_state",
    "leakage_keys_transition",
    "leakage_overlap",
    "state_prompt_tokens",
    "transition_section_tokens",
    "combined_prompt_tokens",
    "target_tokens",
    "total_tokens_with_target",
    "context_limit",
    "over_context",
    "truncated",
    "base_prompt_sha256",
    "teacher_prompt_sha256",
    "target_sha256",
    "target_token_sha256",
    "transition_content_sha256",
    "teacher_section_sha256",
    "renderer_version",
    "transition_renderer_version",
    "model_name",
)


def validate_reusable_teacher_rows(
    *,
    expanded_preflight_rows: Sequence[Mapping[str, Any]],
    source_preflight_rows: Sequence[Mapping[str, Any]],
    source_teacher_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expanded = {str(row["pair_id"]): row for row in expanded_preflight_rows}
    source_preflight = {str(row["pair_id"]): row for row in source_preflight_rows}
    source_teacher = {str(row["pair_id"]): row for row in source_teacher_rows}
    duplicate_counts = {
        "expanded_preflight": len(expanded_preflight_rows) - len(expanded),
        "source_preflight": len(source_preflight_rows) - len(source_preflight),
        "source_teacher": len(source_teacher_rows) - len(source_teacher),
    }
    errors: list[dict[str, Any]] = []
    if any(duplicate_counts.values()):
        errors.append({"type": "duplicate_keys", "counts": duplicate_counts})
    reusable_ids = sorted(set(expanded).intersection(source_preflight).intersection(source_teacher))
    validated: list[str] = []
    for pair_id in reusable_ids:
        new = expanded[pair_id]
        old_preflight = source_preflight[pair_id]
        teacher = source_teacher[pair_id]
        mismatches = [
            key for key in PREFLIGHT_REUSE_KEYS if new.get(key) != old_preflight.get(key)
        ]
        if mismatches:
            errors.append(
                {"type": "preflight_mismatch", "pair_id": pair_id, "keys": mismatches}
            )
            continue
        teacher_mismatches = [
            key
            for key in PREFLIGHT_REUSE_KEYS
            if key in teacher and teacher.get(key) != new.get(key)
        ]
        if teacher_mismatches:
            errors.append(
                {"type": "teacher_mismatch", "pair_id": pair_id, "keys": teacher_mismatches}
            )
            continue
        if teacher.get("scoring_definition") != (
            "frozen_qwen_full_demo_plus_single_raw_decision_transition_target_nll_v1"
        ):
            errors.append({"type": "scoring_definition", "pair_id": pair_id})
            continue
        expected_status = "over_context" if bool(new["over_context"]) else "scored"
        if teacher.get("score_status") != expected_status:
            errors.append({"type": "score_status", "pair_id": pair_id})
            continue
        validated.append(pair_id)
    return {
        "format": REUSE_VALIDATION_VERSION,
        "expanded_pair_count": len(expanded),
        "source_preflight_pair_count": len(source_preflight),
        "source_teacher_pair_count": len(source_teacher),
        "candidate_reuse_count": len(reusable_ids),
        "validated_reuse_count": len(validated),
        "validated_reuse_pair_ids": validated,
        "validated_reuse_pair_ids_sha256": canonical_json_sha256(validated),
        "duplicate_counts": duplicate_counts,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "passed": not errors and len(validated) == len(source_teacher),
    }


def runtime_and_size_projection(
    *,
    total_scoreable_pairs: int,
    reused_scoreable_pairs: int,
    new_query_count: int,
    observed_teacher_seconds_per_pair: float,
    observed_cross_encoder_seconds_per_pair: float,
    observed_multiview_seconds_per_state: float,
    observed_model_runtime_seconds: float,
    prior_artifact_bytes: int,
    prior_query_count: int,
    prior_scoreable_pairs: int,
    review_threshold_h100_hours: float,
) -> dict[str, Any]:
    new_scoreable = int(total_scoreable_pairs) - int(reused_scoreable_pairs)
    if new_scoreable < 0:
        raise ValueError("Reusable scoreable rows exceed total scoreable rows")
    teacher_seconds = (
        new_scoreable * float(observed_teacher_seconds_per_pair)
        + int(new_query_count) * float(observed_teacher_seconds_per_pair)
    )
    cross_seconds = new_scoreable * float(observed_cross_encoder_seconds_per_pair)
    multiview_seconds = int(new_query_count) * float(observed_multiview_seconds_per_state)
    # EXP-020 performs three independently selected LC levels. Historical EXP-019
    # model time included one full selection plus a selected-epoch three-level curve.
    model_seconds = float(observed_model_runtime_seconds) * 1.75
    expected_seconds = teacher_seconds + cross_seconds + multiview_seconds + model_seconds
    best_seconds = expected_seconds * 0.90
    conservative_seconds = expected_seconds * 1.35
    scale = max(
        float(total_scoreable_pairs) / max(int(prior_scoreable_pairs), 1),
        (int(prior_query_count) + int(new_query_count)) / max(int(prior_query_count), 1),
    )
    expected_bytes = round(float(prior_artifact_bytes) * min(scale, 3.0) * 1.15)
    payload = {
        "format": RUNTIME_PROJECTION_VERSION,
        "inputs": {
            "total_scoreable_pairs": int(total_scoreable_pairs),
            "reused_scoreable_pairs": int(reused_scoreable_pairs),
            "new_scoreable_pairs": new_scoreable,
            "new_query_count": int(new_query_count),
            "observed_teacher_seconds_per_pair": float(observed_teacher_seconds_per_pair),
            "observed_cross_encoder_seconds_per_pair": float(
                observed_cross_encoder_seconds_per_pair
            ),
            "observed_multiview_seconds_per_state": float(
                observed_multiview_seconds_per_state
            ),
            "historical_model_runtime_seconds": float(observed_model_runtime_seconds),
        },
        "phase_expected_seconds": {
            "teacher_scoring_and_new_l0": teacher_seconds,
            "prompt_cross_encoder_cache": cross_seconds,
            "new_state_multiview_cache": multiview_seconds,
            "model_cv_learning_curves_controls_reports": model_seconds,
        },
        "best_case_h100_hours": best_seconds / 3600.0,
        "expected_h100_hours": expected_seconds / 3600.0,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "review_threshold_h100_hours": float(review_threshold_h100_hours),
        "expected_runtime_review_required": (
            expected_seconds / 3600.0 > float(review_threshold_h100_hours)
        ),
        "artifact_size_projection": {
            "method": (
                "EXP-019 on-disk bytes scaled by the larger of scoreable-pair and query-count "
                "growth, capped at 3x, then 15% atomic-checkpoint headroom"
            ),
            "prior_artifact_bytes": int(prior_artifact_bytes),
            "scale_before_headroom": min(scale, 3.0),
            "expected_bytes": expected_bytes,
            "expected_gib": expected_bytes / (1024.0**3),
            "conservative_bytes": round(expected_bytes * 1.35),
            "conservative_gib": expected_bytes * 1.35 / (1024.0**3),
        },
    }
    return payload


def classify_learning_curve(
    values: Sequence[float],
    *,
    material_gain: float = 0.03,
    instability: float = 0.08,
) -> str:
    if len(values) != 3:
        raise ValueError("LC12/LC24/LC37 classification requires exactly three values")
    first, middle, final = (float(value) for value in values)
    if max(abs(middle - first), abs(final - middle)) >= float(instability) and (
        (middle - first) * (final - middle) < 0
    ):
        return "unstable"
    if final - first >= float(material_gain) and final >= middle - 0.01:
        return "materially_increasing"
    if final < first - float(material_gain):
        return "degrading"
    return "flat_saturated"


def mean_std(values: Sequence[float]) -> dict[str, float | int]:
    data = [float(value) for value in values]
    return {
        "count": len(data),
        "mean": statistics.fmean(data) if data else 0.0,
        "std": statistics.pstdev(data) if len(data) > 1 else 0.0,
    }
