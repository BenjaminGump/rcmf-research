from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_encoder_6c import cross_encoder_tensor_hash
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    utc_now,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _validate_attempts(
    artifact_dir: Path, run_uuid: str, errors: list[str]
) -> dict[str, Any]:
    rows = _rows(artifact_dir / "attempts.jsonl")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["attempt_id"])].append(row)
        _require(str(row["run_uuid"]) == run_uuid, "attempt run UUID differs", errors)
        _require(
            not bool(row.get("scientific_parameter_changed")),
            f"scientific parameter changed in {row['attempt_id']}",
            errors,
        )
    summaries = []
    for attempt_id, events in sorted(grouped.items()):
        starts = [row for row in events if row.get("event") == "start"]
        ends = [row for row in events if row.get("event") == "end"]
        _require(len(starts) == 1, f"attempt {attempt_id} start count != 1", errors)
        _require(len(ends) == 1, f"attempt {attempt_id} end count != 1", errors)
        summaries.append(
            {
                "attempt_id": attempt_id,
                "phase": starts[0].get("phase") if starts else None,
                "start_timestamp_utc": starts[0].get("start_timestamp_utc") if starts else None,
                "end_timestamp_utc": ends[0].get("end_timestamp_utc") if ends else None,
                "exit_code": ends[0].get("exit_code") if ends else None,
                "parent_attempt_id": starts[0].get("parent_attempt_id") if starts else None,
                "resume_checkpoint": starts[0].get("resume_checkpoint") if starts else None,
                "lambda_head": starts[0].get("lambda_head") if starts else None,
            }
        )
    return {
        "event_count": len(rows),
        "attempt_count": len(grouped),
        "attempts": summaries,
        "all_terminal": all(row["exit_code"] is not None for row in summaries),
        "successful_attempt_count": sum(row["exit_code"] == 0 for row in summaries),
        "failed_attempt_count": sum(row["exit_code"] != 0 for row in summaries),
    }


def _validate_teacher(
    artifact_dir: Path, expected: Mapping[str, Any], errors: list[str]
) -> dict[str, Any]:
    preflight = _rows(artifact_dir / "pair_preflight.jsonl")
    teacher = _rows(artifact_dir / "teacher_cache.jsonl")
    preflight_by_id = {str(row["pair_id"]): row for row in preflight}
    teacher_by_id = {str(row["pair_id"]): row for row in teacher}
    _require(len(preflight_by_id) == len(preflight), "duplicate preflight pair key", errors)
    _require(len(teacher_by_id) == len(teacher), "duplicate teacher pair key", errors)
    _require(set(preflight_by_id) == set(teacher_by_id), "teacher/preflight keys differ", errors)
    _require(len(teacher) == 13320, "legal teacher count != 13320", errors)
    scoreable = []
    over_context = []
    leakage = []
    truncated = []
    nonfinite = []
    for pair_id, row in teacher_by_id.items():
        source = preflight_by_id[pair_id]
        if source.get("leakage_overlap"):
            leakage.append(pair_id)
        if bool(row.get("truncated")):
            truncated.append(pair_id)
        if bool(row.get("valid_for_loss")):
            scoreable.append(row)
            if not all(
                math.isfinite(float(row[key]))
                for key in ("L0", "Lj_transition", "text_utility")
            ):
                nonfinite.append(pair_id)
        else:
            over_context.append(row)
            if not (
                row.get("score_status") == "over_context"
                and row.get("text_utility") is None
                and row.get("Lj_transition") is None
            ):
                errors.append(f"invalid masked over-context row: {pair_id}")
    _require(len(scoreable) == 13128, "scoreable teacher count != 13128", errors)
    _require(len(over_context) == 192, "over-context teacher count != 192", errors)
    _require(not leakage, f"teacher leakage pairs: {leakage[:10]}", errors)
    _require(not truncated, f"truncated teacher pairs: {truncated[:10]}", errors)
    _require(not nonfinite, f"nonfinite teacher pairs: {nonfinite[:10]}", errors)
    summary = _load_json(artifact_dir / "teacher_summary.json")
    _require(summary["validation"]["passed"], "teacher validation failed", errors)
    _require(summary["reproducibility"]["passed"], "teacher reproducibility failed", errors)
    _require(summary["counts"]["reused_pairs"] == 4640, "reused pair count != 4640", errors)
    _require(
        summary["counts"]["newly_scored_pairs"] == 8549,
        "newly scored pair count != 8549",
        errors,
    )
    _require(
        summary["counts"]["new_over_context_pairs"] == 131,
        "new over-context pair count != 131",
        errors,
    )
    return {
        "legal_pairs": len(teacher),
        "scoreable_pairs": len(scoreable),
        "over_context_pairs": len(over_context),
        "reused_pairs": int(summary["counts"]["reused_pairs"]),
        "newly_scored_pairs": int(summary["counts"]["newly_scored_pairs"]),
        "new_over_context_pairs": int(summary["counts"]["new_over_context_pairs"]),
        "teacher_cache_sha256": sha256_file(artifact_dir / "teacher_cache.jsonl"),
        "runtime_seconds": float(summary["runtime_seconds"]),
    }


def _validate_data(
    artifact_dir: Path, settings: Mapping[str, Any], errors: list[str]
) -> dict[str, Any]:
    queries = _load_json(artifact_dir / "expanded_query_manifest.json")
    learning = _load_json(artifact_dir / "learning_curve_manifest.json")
    rows = _rows(artifact_dir / "two_axis_pair_rows.jsonl")
    query_ids = [str(row["state_example_id"]) for row in queries["query_rows"]]
    _require(len(query_ids) == 92, "query count != 92", errors)
    _require(len(query_ids) == len(set(query_ids)), "duplicate query ID", errors)
    split_counts = Counter(str(row["split"]) for row in queries["query_rows"])
    _require(split_counts == {"train": 74, "validation": 18}, "query split count differs", errors)
    _require(
        not queries["task_shortages"], "query manifest has task shortages", errors
    )
    levels = {str(row["name"]): row for row in learning["levels"]}
    task_sets = {name: set(row["task_ids"]) for name, row in levels.items()}
    _require(task_sets["LC12"] < task_sets["LC24"] < task_sets["LC37"], "LC task sets are not strictly nested", errors)
    _require(
        [len(task_sets[name]) for name in ("LC12", "LC24", "LC37")] == [12, 24, 37],
        "LC task counts differ",
        errors,
    )
    pair_ids = [str(row["pair_id"]) for row in rows]
    _require(len(pair_ids) == 13128, "two-axis row count != 13128", errors)
    _require(len(pair_ids) == len(set(pair_ids)), "duplicate two-axis pair ID", errors)
    cells = Counter(str(row["cell"]) for row in rows)
    validation_tasks = {str(value) for value in _load_json(Path(settings["split_manifest"]))["validation_task_ids"]}
    leaked = [
        str(row["pair_id"])
        for row in rows
        if row["cell"] in {CELL_A, CELL_C}
        and str(row["state_task_id"]) in validation_tasks
    ]
    _require(not leaked, f"validation state leaked into A/C: {leaked[:10]}", errors)
    return {
        "queries": len(query_ids),
        "train_queries": split_counts["train"],
        "validation_queries": split_counts["validation"],
        "learning_curve_tasks": {
            name: len(task_sets[name]) for name in ("LC12", "LC24", "LC37")
        },
        "scoreable_pairs": len(rows),
        "cells": dict(cells),
        "manifest_hashes": {
            "queries": sha256_file(artifact_dir / "expanded_query_manifest.json"),
            "learning_curves": sha256_file(artifact_dir / "learning_curve_manifest.json"),
            "two_axis": sha256_file(artifact_dir / "two_axis_pair_rows.jsonl"),
        },
    }


def _validate_representations(artifact_dir: Path, errors: list[str]) -> dict[str, Any]:
    summary = _load_json(artifact_dir / "representation_summary.json")
    _require(summary["status"] == "completed", "representations incomplete", errors)
    _require(summary["state_multiview"]["state_count"] == 92, "state multiview count != 92", errors)
    _require(summary["transition_multiview"]["transition_count"] == 148, "transition multiview count != 148", errors)
    _require(summary["cross_encoder"]["pair_count"] == 13128, "cross pair count != 13128", errors)
    _require(summary["cross_encoder"]["immutable_exp019_reused"] == 4579, "cross reuse count != 4579", errors)
    _require(summary["cross_encoder"]["newly_computed"] == 8549, "new cross count != 8549", errors)
    paths = {
        "state": artifact_dir / "representation_cache/multiview/state_multiview.pt",
        "transition": artifact_dir / "representation_cache/multiview/transition_multiview.pt",
        "cross": artifact_dir / "representation_cache/cross_encoder/cross_encoder_representations.pt",
    }
    for name, path in paths.items():
        _require(path.exists(), f"missing representation aggregate: {name}", errors)
    cross = torch.load(paths["cross"], map_location="cpu", weights_only=False)
    _require(len(cross["ordered_pair_ids"]) == 13128, "cross aggregate IDs != 13128", errors)
    _require(
        cross_encoder_tensor_hash(cross["representations"])
        == cross["tensor_sha256"],
        "cross aggregate tensor hash differs",
        errors,
    )
    return {
        "summary": summary,
        "hashes": {name: sha256_file(path) for name, path in paths.items()},
    }


def _validate_models(artifact_dir: Path, errors: list[str]) -> dict[str, Any]:
    summary = _load_json(artifact_dir / "model_summary.json")
    _require(summary["status"] == "completed", "model reproduction incomplete", errors)
    _require(set(summary["levels"]) == {"LC12", "LC24", "LC37"}, "model LC levels differ", errors)
    pair_rows = {str(row["pair_id"]): row for row in _rows(artifact_dir / "two_axis_pair_rows.jsonl")}
    required_models = {
        "state_only",
        "transition_only",
        "decomposed_signed_bilinear",
        "multiview_lowrank_tensor",
        "multiview_pair_mlp",
        "structured_feature_interaction",
        "prompt_only_cross_encoder",
    }
    prediction_files = 0
    for level_name, level in summary["levels"].items():
        _require(required_models.issubset(level["models"]), f"{level_name} missing models", errors)
        for fold in level["cv_manifest"]["folds"]:
            for key in ("train_pair_ids", "validation_pair_ids"):
                invalid = [
                    pair_id
                    for pair_id in fold[key]
                    if pair_rows[str(pair_id)]["cell"] != CELL_A
                ]
                _require(not invalid, f"{level_name} CV used non-A labels: {invalid[:10]}", errors)
        for model in required_models:
            for cell in (CELL_B, CELL_C, CELL_D):
                controls = level["models"][model]["cells"][cell]["controls"]
                expected_controls = {"correct"} if model in {"state_only", "transition_only"} else {
                    "correct",
                    "shuffled_state",
                    "shuffled_transition",
                    "both_shuffled",
                    "mean_state",
                    "mean_transition",
                    "zero_interaction",
                }
                _require(set(controls) == expected_controls, f"{level_name}/{model}/{cell} controls differ", errors)
                for payload in controls.values():
                    path = Path(payload["rows_path"])
                    _require(path.exists(), f"missing prediction file: {path}", errors)
                    if path.exists():
                        _require(sha256_file(path) == payload["rows_sha256"], f"prediction hash differs: {path}", errors)
                    prediction_files += 1
    return {
        "gate": summary["gate"],
        "runtime_seconds": float(summary["runtime_seconds"]),
        "prediction_files_validated": prediction_files,
        "summary_sha256": sha256_file(artifact_dir / "model_summary.json"),
    }


def _validate_intent(artifact_dir: Path, errors: list[str]) -> dict[str, Any]:
    summary = _load_json(artifact_dir / "action_intent_summary.json")
    _require(summary["status"] == "completed", "action-intent probe incomplete", errors)
    _require(summary["cache"]["state_count"] == 638, "action-intent state count != 638", errors)
    _require(summary["probe"]["train_count"] == 499, "action-intent train count != 499", errors)
    _require(summary["probe"]["validation_count"] == 139, "action-intent validation count != 139", errors)
    _require(summary["hard_scope"]["qwen_frozen"], "action-intent Qwen not frozen", errors)
    return summary


def _artifact_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _final_branch(model: Mapping[str, Any], intent: Mapping[str, Any]) -> dict[str, str]:
    primary = str(model["gate"]["decision_branch"])
    if primary in {
        "all_task_query_coverage_representation_gate_passed",
        "field_compatible_factorization_bottleneck",
    }:
        final = primary
    elif bool(intent["probe"]["succeeded"]):
        final = "state_intent_available_but_memory_utility_target_not_generalizing"
    else:
        final = "state_decision_representation_generalization_failure"
    return {
        "primary_interaction_branch": primary,
        "final_decision_branch": final,
        "representation_gate_passed": str(
            primary == "all_task_query_coverage_representation_gate_passed"
        ).lower(),
        "behavioral_program_remains_blocked": "true",
    }


def _section_reports(artifact_dir: Path, summary: Mapping[str, Any]) -> None:
    gate = summary["models"]["gate"]
    levels = _load_json(artifact_dir / "model_summary.json")["levels"]
    curve_lines = [
        "# EXP-020 LC12/LC24/LC37 Learning Curves",
        "",
        f"- classification: `{gate['cross_encoder_learning_curve']['classification']}`",
        "",
        "| Level | Cross D NDCG@4 | Field D NDCG@4 |",
        "|---|---:|---:|",
    ]
    for name in ("LC12", "LC24", "LC37"):
        cross = levels[name]["models"]["prompt_only_cross_encoder"]["cells"][CELL_D]["controls"]["correct"]["metrics"]
        field = levels[name]["models"]["multiview_lowrank_tensor"]["cells"][CELL_D]["controls"]["correct"]["metrics"]
        curve_lines.append(
            f"| {name} | {float(cross['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{float(field['per_state']['ndcg@4']['mean'] or 0):.6f} |"
        )
    atomic_write_text(artifact_dir / "learning_curve_report.md", "\n".join(curve_lines) + "\n")
    atomic_write_text(
        artifact_dir / "cross_encoder_report.md",
        "# EXP-020 Cross-Encoder Upper Bound\n\n```json\n"
        + json.dumps(gate["cross_encoder"], indent=2, sort_keys=True)
        + "\n```\n",
    )
    atomic_write_text(
        artifact_dir / "field_compatible_report.md",
        "# EXP-020 Field-Compatible Retention\n\n```json\n"
        + json.dumps(gate["field_compatible"], indent=2, sort_keys=True)
        + "\n```\n",
    )
    atomic_write_text(
        artifact_dir / "shuffle_control_report.md",
        "# EXP-020 State/Transition Shuffle Controls\n\n"
        f"Cross state/transition drops: `{gate['cross_encoder']['state_shuffle_drop']:.6f}` / "
        f"`{gate['cross_encoder']['transition_shuffle_drop']:.6f}`.\n\n"
        f"Field state/transition drops: `{gate['field_compatible']['state_shuffle_drop']:.6f}` / "
        f"`{gate['field_compatible']['transition_shuffle_drop']:.6f}`.\n",
    )


def _report(summary: Mapping[str, Any]) -> str:
    decision = summary["decision"]
    return "\n".join(
        [
            "# EXP-020 Final Validation",
            "",
            f"- validation passed: `{summary['passed']}`",
            f"- run UUID: `{summary['run_uuid']}`",
            f"- query states: `{summary['data']['queries']}`",
            f"- legal/scoreable/over-context pairs: `{summary['teacher']['legal_pairs']}` / "
            f"`{summary['teacher']['scoreable_pairs']}` / `{summary['teacher']['over_context_pairs']}`",
            f"- actual H100 hours: `{summary['actual_h100_hours']:.6f}`",
            f"- artifact bytes: `{summary['artifact_bytes']}`",
            f"- primary interaction branch: `{decision['primary_interaction_branch']}`",
            f"- final decision branch: `{decision['final_decision_branch']}`",
            f"- representation gate passed: `{decision['representation_gate_passed']}`",
            f"- behavioral p(s,m_transition) remains blocked: `{decision['behavioral_program_remains_blocked']}`",
            "",
            "No behavioral program, injector, selector, AppWorld generation/evaluation, Stage C2, end-to-end RCMF training, demo change, or V4 tag occurred.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate completed EXP-020")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    errors: list[str] = []
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    run_uuid = str(settings["run_uuid"])
    _require(run_manifest["run_uuid"] == run_uuid, "run UUID differs", errors)
    attempts = _validate_attempts(args.artifact_dir, run_uuid, errors)
    teacher = _validate_teacher(args.artifact_dir, settings["expected"], errors)
    data = _validate_data(args.artifact_dir, settings, errors)
    representations = _validate_representations(args.artifact_dir, errors)
    models = _validate_models(args.artifact_dir, errors)
    intent = _validate_intent(args.artifact_dir, errors)
    decision = _final_branch(models, intent)
    h100_seconds = sum(
        (
            teacher["runtime_seconds"],
            float(representations["summary"]["runtime_seconds"]),
            float(models["runtime_seconds"]),
            float(intent["runtime_seconds"]),
        )
    )
    summary = {
        "format": "all_task_interaction_postrun_validation_6d_v1",
        "passed": not errors,
        "errors": errors,
        "run_uuid": run_uuid,
        "source_commit": args.source_commit,
        "run_manifest_sha256": sha256_file(args.artifact_dir / "run_manifest.json"),
        "attempts": attempts,
        "teacher": teacher,
        "data": data,
        "representations": representations,
        "models": models,
        "action_intent": intent,
        "decision": decision,
        "actual_h100_seconds": h100_seconds,
        "actual_h100_hours": h100_seconds / 3600.0,
        "artifact_bytes": _artifact_size(args.artifact_dir),
        "validated_at_utc": utc_now(),
    }
    atomic_write_json(args.artifact_dir / "postrun_validation.json", summary)
    atomic_write_text(args.artifact_dir / "postrun_validation.md", _report(summary))
    _section_reports(args.artifact_dir, summary)
    atomic_write_json(args.artifact_dir / "final_summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("passed", "errors", "decision", "actual_h100_hours", "artifact_bytes")}, indent=2), flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
