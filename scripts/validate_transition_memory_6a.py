from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl


EXPECTED_COUNTS = {
    "parent_trajectory_count": 37,
    "transition_count": 499,
    "panel_transition_count": 148,
    "query_count": 32,
    "train_query_count": 24,
    "validation_query_count": 8,
    "legal_pair_count": 4640,
    "scoreable_pair_count": 4579,
    "over_context_pair_count": 61,
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _check(condition: bool, name: str, errors: list[str]) -> None:
    if not condition:
        errors.append(name)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _validate_teacher_rows(
    rows: Sequence[Mapping[str, Any]], preflight_rows: Sequence[Mapping[str, Any]]
) -> list[str]:
    errors: list[str] = []
    actual = {str(row["pair_id"]): row for row in rows}
    expected = {str(row["pair_id"]): row for row in preflight_rows}
    _check(len(rows) == len(actual), "duplicate teacher pair IDs", errors)
    _check(set(actual) == set(expected), "teacher/preflight pair ID set mismatch", errors)
    for pair_id in sorted(set(actual).intersection(expected)):
        row = actual[pair_id]
        preflight = expected[pair_id]
        _check(not row.get("leakage_overlap"), f"teacher leakage: {pair_id}", errors)
        _check(not row.get("truncated"), f"teacher truncation: {pair_id}", errors)
        if preflight.get("over_context"):
            _check(
                row.get("score_status") == "over_context"
                and row.get("valid_for_loss") is False
                and row.get("Lj_transition") is None
                and row.get("text_utility") is None,
                f"invalid over-context teacher row: {pair_id}",
                errors,
            )
        else:
            _check(
                row.get("score_status") == "scored"
                and row.get("valid_for_loss") is True
                and _finite(row.get("L0"))
                and _finite(row.get("Lj_transition"))
                and _finite(row.get("text_utility")),
                f"invalid scoreable teacher row: {pair_id}",
                errors,
            )
            if _finite(row.get("text_utility")):
                _check(
                    abs(
                        float(row["L0"])
                        - float(row["Lj_transition"])
                        - float(row["text_utility"])
                    )
                    <= 2.0e-4,
                    f"teacher utility identity: {pair_id}",
                    errors,
                )
        if len(errors) >= 100:
            break
    return errors


def _response_cache_checks(path: Path, expected_count: int | None = None) -> dict[str, Any]:
    rows = _load_rows(path)
    pair_ids = [str(row["pair_id"]) for row in rows]
    errors = []
    _check(len(rows) == len(set(pair_ids)), f"duplicate response pair IDs: {path}", errors)
    if expected_count is not None:
        _check(len(rows) == expected_count, f"response count differs: {path}", errors)
    _check(
        all(not row.get("truncated") for row in rows),
        f"truncated response cache row: {path}",
        errors,
    )
    _check(
        all(not row.get("student_prompt_contains_raw_memory") for row in rows),
        f"student prompt contains raw memory: {path}",
        errors,
    )
    return {"path": str(path), "count": len(rows), "errors": errors, "passed": not errors}


def _ratio_checks(run: Mapping[str, Any]) -> list[str]:
    errors = []
    evaluations = run.get("evaluations") or {}
    for name, evaluation in evaluations.items():
        maximum = evaluation.get("summary", {}).get("delta_ratio", {}).get("max")
        if maximum is not None and float(maximum) > 1.0001:
            errors.append(f"{run.get('name')} {name} ratio exceeds 1.0: {maximum}")
    return errors


def validate_artifact(artifact_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    preflight = _load_json(artifact_dir / "preflight_summary.json")
    teacher = _load_json(artifact_dir / "teacher_summary.json")
    behavior = _load_json(artifact_dir / "behavior_summary.json")
    preflight_rows = _load_rows(artifact_dir / "pair_preflight.jsonl")
    teacher_rows = _load_rows(artifact_dir / "teacher_cache.jsonl")
    _check(preflight.get("status") == "passed_ready_for_gpu_review", "preflight status", errors)
    for key, expected in EXPECTED_COUNTS.items():
        _check(int(preflight["counts"][key]) == expected, f"preflight count {key}", errors)
    _check(bool(preflight["extraction_validation"]["passed"]), "extraction validation", errors)
    _check(bool(preflight["field_algebra"]["passed"]), "field algebra", errors)
    errors.extend(_validate_teacher_rows(teacher_rows, preflight_rows))
    _check(bool(teacher["validation"]["passed"]), "teacher summary validation", errors)
    _check(bool(teacher["reproducibility"]["passed"]), "teacher reproducibility", errors)
    _check(
        bool(teacher["representative_inspection"]["passed"]),
        "teacher representative inspection",
        errors,
    )
    response_checks = {
        "pair_oracle": _response_cache_checks(
            artifact_dir / "pair_oracle_response_cache" / "response_cache.jsonl", 64
        ),
        "static_transition": _response_cache_checks(
            artifact_dir / "static_transition_response_cache" / "response_cache.jsonl"
        ),
        "trajectory_baseline": _response_cache_checks(
            artifact_dir / "trajectory_baseline_response_cache" / "response_cache.jsonl"
        ),
    }
    for report in response_checks.values():
        errors.extend(report["errors"])
    _check(
        behavior.get("status") in {"completed", "stopped_at_teacher_validity_gate"},
        "behavior terminal status",
        errors,
    )
    if behavior.get("status") == "completed":
        decoder = behavior["decoder_validation"]
        _check(bool(decoder["passed"]), "decoder validation", errors)
        _check(not decoder["source_query_state_overlap"], "decoder/query overlap", errors)
        pair = behavior["pair_oracle"]
        _check(int(pair["pair_count"]) == 64, "pair-oracle count", errors)
        accounting = pair["history"][-1]["update_accounting"]
        _check(bool(accounting["all_pairs_equal"]), "pair-oracle unequal updates", errors)
        _check(bool(pair["decoder_unchanged"]), "pair-oracle decoder changed", errors)
        maximum = pair["final_evaluation"]["summary"]["delta_ratio"]["max"]
        _check(float(maximum) <= 1.0001, "pair-oracle ratio", errors)
        if pair["gate"]["passed"]:
            for key in ("static_transition", "trajectory_baseline"):
                run = behavior[key]
                _check(run is not None, f"missing {key} run", errors)
                if run is None:
                    continue
                _check(bool(run["update_accounting"]["all_pairs_equal"]), f"{key} unequal updates", errors)
                _check(bool(run["decoder_unchanged"]), f"{key} decoder changed", errors)
                errors.extend(_ratio_checks(run))
    hard_scope = behavior.get("hard_scope") or {}
    for key in (
        "signed_selector_used_or_trained",
        "content_compiler_used_or_trained",
        "appworld_generation_or_evaluation",
        "exp016d_launched",
    ):
        _check(not hard_scope.get(key), f"hard-scope violation: {key}", errors)
    return {
        "format": "decision_transition_postrun_validation_6a_v1",
        "artifact_dir": str(artifact_dir),
        "expected_counts": EXPECTED_COUNTS,
        "teacher_pair_count": len(teacher_rows),
        "response_cache_checks": response_checks,
        "behavior_status": behavior.get("status"),
        "decision": behavior.get("decision"),
        "error_count": len(errors),
        "errors_first_100": errors[:100],
        "passed": not errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the complete EXP-017 artifact.")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_artifact(args.artifact_dir)
    atomic_write_json(args.artifact_dir / "postrun_validation.json", report)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-017 Post-Run Validation\n\n"
        f"- passed: `{report['passed']}`\n"
        f"- errors: `{report['error_count']}`\n"
        f"- behavior status: `{report['behavior_status']}`\n"
        f"- decision: `{json.dumps(report['decision'], sort_keys=True)}`\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
