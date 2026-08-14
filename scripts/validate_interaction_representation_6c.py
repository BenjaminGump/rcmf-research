from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.training.cross_encoder_6c import cross_encoder_tensor_hash
from rcmf.training.state_conditioned_transition_6b import CELL_NAMES
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


EXPECTED_CELLS = {
    "train_state__train_transition": 2667,
    "heldout_state__train_transition": 904,
    "train_state__heldout_transition": 752,
    "heldout_state__heldout_transition": 256,
}
EXPECTED_PAIR_COUNT = 4579
EXPECTED_CROSS_VIEWS = 3
EXPECTED_HIDDEN_DIM = 4096
EXPECTED_LEARNING_MODELS = {
    "decomposed_signed_bilinear",
    "decomposed_concat_interaction",
    "multiview_signed_bilinear",
    "multiview_lowrank_tensor",
    "multiview_pair_mlp",
    "structured_feature_interaction",
    "prompt_only_cross_encoder",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_attempt_events(
    rows: Sequence[Mapping[str, Any]], *, expected_run_uuid: str
) -> dict[str, Any]:
    errors: list[str] = []
    starts = Counter(
        str(row.get("attempt_id")) for row in rows if row.get("event") == "start"
    )
    ends = Counter(
        str(row.get("attempt_id")) for row in rows if row.get("event") == "end"
    )
    _check(bool(starts), "attempt_ledger_has_no_start", errors)
    _check(
        starts == ends,
        f"attempt_start_end_mismatch:{dict(starts)}:{dict(ends)}",
        errors,
    )
    _check(
        all(count == 1 for count in starts.values()),
        f"duplicate_attempt_id:{dict(starts)}",
        errors,
    )
    _check(
        all(str(row.get("run_uuid")) == expected_run_uuid for row in rows),
        "attempt_run_uuid_mismatch",
        errors,
    )
    event_positions: dict[str, dict[str, int]] = {}
    for position, row in enumerate(rows):
        attempt_id = str(row.get("attempt_id"))
        event_positions.setdefault(attempt_id, {})[str(row.get("event"))] = position
    for attempt_id, positions in event_positions.items():
        if "start" in positions and "end" in positions:
            _check(
                positions["start"] < positions["end"],
                f"attempt_end_precedes_start:{attempt_id}",
                errors,
            )
    end_rows = [row for row in rows if row.get("event") == "end"]
    return {
        "passed": not errors,
        "errors": errors,
        "attempt_count": len(starts),
        "failed_attempt_ids": [
            str(row["attempt_id"])
            for row in end_rows
            if int(row.get("exit_code", 1)) != 0
        ],
        "completed_attempt_ids": [
            str(row["attempt_id"])
            for row in end_rows
            if int(row.get("exit_code", 1)) == 0
        ],
    }


def validate_learning_manifest(
    manifest: Mapping[str, Any], rows_a: Sequence[Mapping[str, Any]]
) -> list[str]:
    errors: list[str] = []
    _check(int(manifest.get("fold_count", -1)) == 5, "learning_fold_count", errors)
    _check(
        [int(value) for value in manifest.get("task_counts", [])] == [4, 8, 12],
        "learning_task_counts",
        errors,
    )
    by_task: dict[str, list[Mapping[str, Any]]] = {}
    all_parents = {str(row["transition_parent_id"]) for row in rows_a}
    for row in rows_a:
        by_task.setdefault(str(row["state_task_id"]), []).append(row)
    for fold in manifest.get("folds", []):
        previous: set[str] = set()
        levels = fold.get("levels", [])
        _check(len(levels) == 3, f"learning_level_count:{fold.get('fold')}", errors)
        for level in levels:
            tasks = {str(value) for value in level.get("task_ids", [])}
            selected = [row for task in tasks for row in by_task.get(task, [])]
            expected_hash = hashlib.sha256(
                "\n".join(sorted(str(row["pair_id"]) for row in selected)).encode(
                    "utf-8"
                )
            ).hexdigest()
            _check(
                previous.issubset(tasks),
                f"learning_not_nested:{fold.get('fold')}",
                errors,
            )
            _check(
                len(tasks) == int(level.get("task_count", -1)),
                f"learning_task_count:{fold.get('fold')}:{level.get('task_count')}",
                errors,
            )
            _check(
                len(selected) == int(level.get("pair_count", -1)),
                f"learning_pair_count:{fold.get('fold')}:{level.get('task_count')}",
                errors,
            )
            _check(
                expected_hash == str(level.get("pair_ids_sha256")),
                f"learning_pair_hash:{fold.get('fold')}:{level.get('task_count')}",
                errors,
            )
            selected_parents = {str(row["transition_parent_id"]) for row in selected}
            _check(
                selected_parents == all_parents
                and bool(level.get("all_parent_coverage")),
                f"learning_parent_coverage:{fold.get('fold')}:{level.get('task_count')}",
                errors,
            )
            previous = tasks
    return errors


def _validate_cross_encoder_cache(
    root: Path, pair_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    errors: list[str] = []
    ordered_ids = sorted(str(row["pair_id"]) for row in pair_rows)
    row_dir = root / "part_e/cross_encoder_cache/rows"
    row_paths = sorted(row_dir.glob("*.pt"))
    _check(
        len(row_paths) == EXPECTED_PAIR_COUNT,
        f"cross_row_count:{len(row_paths)}",
        errors,
    )
    seen: set[str] = set()
    for pair_id in ordered_ids:
        path = row_dir / f"{hashlib.sha256(pair_id.encode('utf-8')).hexdigest()}.pt"
        if not path.exists():
            errors.append(f"missing_cross_row:{pair_id}")
            if len(errors) >= 100:
                break
            continue
        payload = torch.load(path, map_location="cpu", weights_only=False)
        values = payload.get("representations")
        _check(
            str(payload.get("pair_id")) == pair_id,
            f"cross_pair_identity:{pair_id}",
            errors,
        )
        _check(pair_id not in seen, f"duplicate_cross_pair:{pair_id}", errors)
        seen.add(pair_id)
        _check(
            isinstance(values, torch.Tensor)
            and tuple(values.shape) == (EXPECTED_CROSS_VIEWS, EXPECTED_HIDDEN_DIM),
            f"cross_row_shape:{pair_id}",
            errors,
        )
        if isinstance(values, torch.Tensor):
            _check(
                cross_encoder_tensor_hash(values) == str(payload.get("tensor_sha256")),
                f"cross_row_tensor_hash:{pair_id}",
                errors,
            )
        _check(
            not bool(payload.get("truncated")), f"cross_row_truncated:{pair_id}", errors
        )
        _check(
            not bool(payload.get("target_action_accessed")),
            f"cross_row_target_access:{pair_id}",
            errors,
        )
        if len(errors) >= 100:
            break

    aggregate_path = root / "part_e/cross_encoder_representations.pt"
    aggregate = torch.load(aggregate_path, map_location="cpu", weights_only=False)
    matrix = aggregate.get("representations")
    _check(
        isinstance(matrix, torch.Tensor)
        and tuple(matrix.shape)
        == (EXPECTED_PAIR_COUNT, EXPECTED_CROSS_VIEWS * EXPECTED_HIDDEN_DIM),
        f"cross_aggregate_shape:{getattr(matrix, 'shape', None)}",
        errors,
    )
    _check(
        [str(value) for value in aggregate.get("ordered_pair_ids", [])] == ordered_ids,
        "cross_aggregate_pair_order",
        errors,
    )
    if isinstance(matrix, torch.Tensor):
        _check(
            cross_encoder_tensor_hash(matrix) == str(aggregate.get("tensor_sha256")),
            "cross_aggregate_tensor_hash",
            errors,
        )
    report = _load_json(root / "part_e/cross_encoder_cache_report.json")
    _check(
        int(report.get("pair_count", -1)) == EXPECTED_PAIR_COUNT,
        "cross_report_count",
        errors,
    )
    _check(report.get("no_truncation") is True, "cross_report_truncation", errors)
    _check(
        report.get("target_action_accessed") is False,
        "cross_report_target_access",
        errors,
    )
    _check(
        sha256_file(aggregate_path) == str(report.get("aggregate_sha256")),
        "cross_aggregate_file_hash",
        errors,
    )
    return {
        "passed": not errors,
        "errors": errors,
        "row_count": len(row_paths),
        "aggregate_shape": (
            list(matrix.shape) if isinstance(matrix, torch.Tensor) else None
        ),
        "aggregate_sha256": sha256_file(aggregate_path),
    }


def _validate_prediction_paths(
    part_e: Mapping[str, Any], expected_by_cell: Mapping[str, set[str]]
) -> list[str]:
    errors: list[str] = []
    for cell, cell_payload in part_e.get("cells", {}).items():
        expected = expected_by_cell.get(str(cell), set())
        for control, payload in cell_payload.get("controls", {}).items():
            path_value = payload.get("rows_path")
            if not path_value:
                errors.append(f"prediction_path_missing:{cell}:{control}")
                continue
            rows = _load_rows(Path(str(path_value)))
            pair_ids = [str(row["pair_id"]) for row in rows]
            _check(
                len(pair_ids) == len(set(pair_ids)),
                f"prediction_duplicate:{cell}:{control}",
                errors,
            )
            _check(
                set(pair_ids) == expected,
                f"prediction_pair_set:{cell}:{control}",
                errors,
            )
    return errors


def validate_artifact(root: Path, exp018: Path) -> dict[str, Any]:
    errors: list[str] = []
    required = (
        "run_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
        "exp018_immutable_snapshot.json",
        "parts_a_b_summary.json",
        "parts_c_d_summary.json",
        "parts_c_d/multiview_cache/state_multiview.pt",
        "parts_c_d/multiview_cache/transition_multiview.pt",
        "part_e/input_validation.json",
        "part_e/cross_encoder_token_preflight.json",
        "part_e/cross_encoder_cache_report.json",
        "part_e/cross_encoder_representations.pt",
        "part_e_summary.json",
        "part_f/learning_curve_manifest.json",
        "part_f/learning_curve_progress.json",
        "part_f_summary.json",
    )
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        return {"passed": False, "errors": [f"missing:{name}" for name in missing]}

    run_manifest = _load_json(root / "run_manifest.json")
    run_uuid = str(run_manifest["run_uuid"])
    heartbeat = _load_json(root / "heartbeat.json")
    attempts = _load_rows(root / "attempts.jsonl")
    ledger = validate_attempt_events(attempts, expected_run_uuid=run_uuid)
    errors.extend(ledger["errors"])

    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    pair_ids = [str(row["pair_id"]) for row in pair_rows]
    _check(
        len(pair_rows) == EXPECTED_PAIR_COUNT, f"pair_count:{len(pair_rows)}", errors
    )
    _check(len(pair_ids) == len(set(pair_ids)), "duplicate_pair_ids", errors)
    cell_counts = Counter(str(row["cell"]) for row in pair_rows)
    _check(
        dict(cell_counts) == EXPECTED_CELLS, f"cell_counts:{dict(cell_counts)}", errors
    )
    _check(set(cell_counts) == set(CELL_NAMES), "cell_names", errors)
    _check(
        all(bool(row.get("valid_for_loss")) for row in pair_rows),
        "invalid_pair_row",
        errors,
    )
    _check(
        all(not bool(row.get("truncated")) for row in pair_rows),
        "truncated_pair_row",
        errors,
    )

    cross_cache = _validate_cross_encoder_cache(root, pair_rows)
    errors.extend(cross_cache["errors"])
    part_e = _load_json(root / "part_e_summary.json")
    expected_by_cell = {
        cell: {str(row["pair_id"]) for row in pair_rows if str(row["cell"]) == cell}
        for cell in EXPECTED_CELLS
    }
    errors.extend(_validate_prediction_paths(part_e, expected_by_cell))
    _check(
        part_e.get("normalization_estimated_from") == "train_state__train_transition",
        "part_e_normalization_cell",
        errors,
    )
    _check(
        "cell-A-only"
        in str(part_e.get("selected_configuration", {}).get("selection_rule")),
        "part_e_selection_rule",
        errors,
    )

    rows_a = [
        row for row in pair_rows if str(row["cell"]) == "train_state__train_transition"
    ]
    manifest = _load_json(root / "part_f/learning_curve_manifest.json")
    errors.extend(validate_learning_manifest(manifest, rows_a))
    part_f = _load_json(root / "part_f_summary.json")
    raw_rows = part_f.get("raw_rows", [])
    expected_keys = {
        (model, fold, tasks)
        for model in EXPECTED_LEARNING_MODELS
        for fold in range(5)
        for tasks in (4, 8, 12)
    }
    actual_keys = {
        (str(row["model_kind"]), int(row["fold"]), int(row["task_count"]))
        for row in raw_rows
    }
    _check(len(raw_rows) == 105, f"learning_result_count:{len(raw_rows)}", errors)
    _check(actual_keys == expected_keys, "learning_result_keys", errors)
    for row in raw_rows:
        checkpoint = Path(str(row["checkpoint"]))
        _check(checkpoint.exists(), f"learning_checkpoint_missing:{checkpoint}", errors)
        if checkpoint.exists():
            _check(
                sha256_file(checkpoint) == str(row["checkpoint_sha256"]),
                f"learning_checkpoint_hash:{checkpoint}",
                errors,
            )

    expected_decision = (
        "independent_encoding_or_field_factorization_bottleneck"
        if bool(part_e["gate"]["passed"])
        else (
            "query_task_coverage_insufficient"
            if bool(
                part_f["learning_curves"]["models"]["prompt_only_cross_encoder"][
                    "still_materially_rising_at_maximum"
                ]
            )
            or bool(
                part_f["learning_curves"]["models"]["prompt_only_cross_encoder"][
                    "unstable_across_folds"
                ]
            )
            else "teacher_utility_not_predictable_from_available_prompt_only_features"
        )
    )
    _check(str(part_f.get("decision")) == expected_decision, "final_decision", errors)
    _check(
        part_f.get("behavioral_program_remains_blocked") is True,
        "behavior_not_blocked",
        errors,
    )
    _check(
        part_f.get("representation_gate_repaired") is False,
        "representation_gate_flag",
        errors,
    )
    hard_scope = part_f.get("hard_scope") or {}
    for key in (
        "qwen_behavioral_backpropagation",
        "behavioral_program_training",
        "injector_training",
        "selector_training",
        "appworld_generation",
        "stage_c2",
        "v4_tag_created_or_moved",
    ):
        _check(hard_scope.get(key) is False, f"hard_scope:{key}", errors)
    _check(
        int(hard_scope.get("qwen_forward_calls", -1)) == 0,
        "part_f_qwen_forward",
        errors,
    )
    _check(
        heartbeat.get("status") == "completed",
        f"heartbeat:{heartbeat.get('status')}",
        errors,
    )

    return {
        "format": "interaction_representation_postrun_validation_6c_v1",
        "passed": not errors,
        "error_count": len(errors),
        "errors_first_100": errors[:100],
        "run_uuid": run_uuid,
        "attempt_ledger": ledger,
        "pair_count": len(pair_rows),
        "cell_counts": dict(cell_counts),
        "cross_encoder_cache": cross_cache,
        "learning_curve_result_count": len(raw_rows),
        "decision": part_f.get("decision"),
        "behavioral_program_remains_blocked": part_f.get(
            "behavioral_program_remains_blocked"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the complete EXP-019 artifact."
    )
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--exp018-artifact", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_artifact(args.artifact_dir, args.exp018_artifact)
    atomic_write_json(args.artifact_dir / "postrun_validation.json", report)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-019 Post-Run Validation\n\n"
        f"- passed: `{report['passed']}`\n"
        f"- errors: `{report.get('error_count', len(report.get('errors', [])))}`\n"
        f"- decision: `{report.get('decision')}`\n"
        f"- behavioral p(s,m_transition) blocked: "
        f"`{report.get('behavioral_program_remains_blocked')}`\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
