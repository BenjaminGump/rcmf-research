from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _report(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# EXP-021 Post-run Validation",
            "",
            f"- passed: `{result['passed']}`",
            f"- checks: `{sum(result['checks'].values())}/{len(result['checks'])}`",
            f"- decision branch: `{result.get('decision_branch')}`",
            f"- candidate-target rows: `{result['counts'].get('candidate_rows')}`",
            f"- serialization rows: `{result['counts'].get('serialization_rows')}`",
            f"- attempts: `{result['counts'].get('attempts')}`",
            "",
            "## Errors",
            "",
            *([f"- {value}" for value in result["errors"]] or ["- none"]),
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate EXP-021 artifacts")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifact_dir
    required = [
        "run_manifest.json", "preflight_summary.json",
        "candidate_target_rows.jsonl", "locked_raw_utility_decomposition.json",
        "serialization_audit_manifest.json", "serialization_preflight.jsonl",
        "serialization_teacher_cache.jsonl", "serialization_summary.json",
        "intent_probe_calibration.json", "a_only_grouped_cv_selection.json",
        "model_audit_summary.json", "final_target_audit_report.md",
        "attempts.jsonl", "heartbeat.json",
    ]
    errors = [f"missing:{name}" for name in required if not (root / name).exists()]
    checks: dict[str, bool] = {"required_artifacts": not errors}
    counts: dict[str, int] = {}
    decision = None
    if not errors:
        preflight = _load_json(root / "preflight_summary.json")
        candidate = list(read_jsonl(root / "candidate_target_rows.jsonl"))
        serialization_preflight = list(read_jsonl(root / "serialization_preflight.jsonl"))
        serialization = list(read_jsonl(root / "serialization_teacher_cache.jsonl"))
        serialization_summary = _load_json(root / "serialization_summary.json")
        cv = _load_json(root / "a_only_grouped_cv_selection.json")
        model = _load_json(root / "model_audit_summary.json")
        attempts = list(read_jsonl(root / "attempts.jsonl"))
        counts = {
            "candidate_rows": len(candidate),
            "serialization_preflight_rows": len(serialization_preflight),
            "serialization_rows": len(serialization),
            "attempts": len(attempts),
        }
        checks.update(
            {
                "immutable_exp020_contract": (
                    preflight["contract"]["query_states"] == 92
                    and preflight["contract"]["transitions"] == 148
                    and preflight["contract"]["scoreable_rows"] == 13128
                    and preflight["contract"]["legal_rows"] == 13320
                    and preflight["contract"]["over_context_rows"] == 192
                    and preflight["contract"]["cells"] == {"A": 8205, "B": 2051, "C": 2296, "D": 576}
                ),
                "candidate_count_and_unique_keys": (
                    len(candidate) == 13128
                    and len({str(row["pair_id"]) for row in candidate}) == 13128
                ),
                "candidate_targets_present": all(
                    all(key in row for key in ("T0", "T1_median", "T1_mean", "T2", "T3", "query_action_signature", "transition_signature"))
                    for row in candidate
                ),
                "serialization_manifest_counts": (
                    preflight["serialization_counts"]["audit_pairs"] == 192
                    and len(serialization_preflight) == 384
                    and len(serialization) == 576
                ),
                "serialization_unique_keys": len({(str(row["pair_id"]), str(row["template"])) for row in serialization}) == 576,
                "serialization_no_truncation": not any(bool(row.get("truncated")) for row in serialization),
                "serialization_utility_identity": all(
                    (not bool(row["valid_for_loss"]))
                    or abs(float(row["L0"]) - float(row["Lj_transition"]) - float(row["text_utility"])) <= 1.0e-6
                    for row in serialization
                ),
                "serialization_gate_passed": bool(serialization_summary["robustness"]["gate_passed"]),
                "a_only_selection_frozen": bool(cv["selection_frozen_before_bcd"]),
                "all_fixed_target_candidates": set(cv["targets"]) == {"T3", "T4", "T6", "T7"},
                "all_bcd_cells_present": all(
                    set(architectures[kind]["cells"]) == {"B", "C", "D"}
                    for architectures in model["final_results"]["models"].values()
                    for kind in ("field", "cross")
                ),
                "hard_scope": all(
                    not bool(model["hard_scope"][key])
                    for key in (
                        "qwen_behavioral_backpropagation", "behavioral_program_training",
                        "injector_training", "selector_training", "production_field_training",
                        "appworld_generation_or_evaluation", "stage_c2", "end_to_end_rcmf",
                        "demo_changed", "query_or_transition_added", "v4_tag_created_or_moved",
                    )
                ) and int(model["hard_scope"]["qwen_forward_calls"]) == 0,
                "attempt_ids_unique": len({str(row["attempt_id"]) for row in attempts}) == len(attempts),
                "attempts_have_terminal_rows": all(
                    str(row.get("status")) in {"completed", "failed"} for row in attempts
                ),
            }
        )
        decision = str(model["scientific_gate"]["decision_branch"])
        checks["recognized_decision_branch"] = decision in {
            "raw_nll_teacher_serialization_instability",
            "coarse_action_intent_explains_memory_use_signal",
            "query_intent_prediction_or_calibration_bottleneck",
            "revised_target_learnable_but_field_factorization_insufficient",
            "relative_intent_conditioned_memory_use_target_validated",
            "raw_nll_memory_use_target_not_deployably_predictable",
        }
        checks["source_hashes_preserved"] = all(
            sha256_file(Path(path)) == digest
            for path, digest in (
                (root / "candidate_target_rows.jsonl", preflight["hashes"]["candidate_targets"]),
                (root / "serialization_preflight.jsonl", preflight["hashes"]["preflight"]),
            )
        )
        errors.extend(key for key, passed in checks.items() if not passed)
    result = {
        "format": "memory_use_target_postrun_validation_6e_v1",
        "passed": not errors,
        "checks": checks,
        "counts": counts,
        "decision_branch": decision,
        "errors": errors,
    }
    atomic_write_json(root / "postrun_validation.json", result)
    atomic_write_text(root / "postrun_validation.md", _report(result))
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
