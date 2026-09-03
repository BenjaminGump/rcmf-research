from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from rcmf.pipeline.contracts import deep_diff
from rcmf.pipeline.manifests import content_sha256, sha256_file


DEFAULT_ARM_DIFF_ALLOWLIST = frozenset(
    {
        "arm_id",
        "task_conditioned_prompt_profile",
        "artifact_dir",
        "run_uuid",
        "prompt_assets.initial_messages_sha256",
        "prompt_assets.renderer_version",
    }
)

DEFAULT_ARM_DIFF_PREFIXES = (
    "prompt_dependent.",
    "outputs.",
    "runtime_estimate.",
)


def validate_resolved_arm_diff(
    arm_3d: Mapping[str, Any],
    arm_1d: Mapping[str, Any],
    *,
    allowlist: Iterable[str] = DEFAULT_ARM_DIFF_ALLOWLIST,
    allowed_prefixes: Iterable[str] = DEFAULT_ARM_DIFF_PREFIXES,
) -> dict[str, Any]:
    allowed = set(allowlist)
    prefixes = tuple(allowed_prefixes)
    differences = deep_diff(arm_3d, arm_1d)
    prohibited = [
        row
        for row in differences
        if row["path"] not in allowed
        and not any(str(row["path"]).startswith(prefix) for prefix in prefixes)
    ]
    return {
        "format": "resolved_arm_diff_validation_14b_v1",
        "passed": not prohibited,
        "differences": differences,
        "prohibited_differences": prohibited,
        "allowlist": sorted(allowed),
        "allowed_prefixes": list(prefixes),
    }


def leave_one_out_effects(
    correct: Mapping[str, bool], comparator: Mapping[str, bool]
) -> dict[str, Any]:
    task_ids = sorted(set(correct) | set(comparator))
    if set(correct) != set(comparator):
        raise ValueError("Paired task identities differ")
    differences = {
        task_id: int(bool(correct[task_id])) - int(bool(comparator[task_id]))
        for task_id in task_ids
    }
    total = sum(differences.values())
    leave_one_out = {
        task_id: (total - differences[task_id]) / max(1, len(task_ids) - 1)
        for task_id in task_ids
    }
    return {
        "task_count": len(task_ids),
        "effect_count": total,
        "effect_rate": total / max(1, len(task_ids)),
        "leave_one_out": leave_one_out,
        "minimum_leave_one_out_effect": min(leave_one_out.values()) if leave_one_out else None,
        "maximum_leave_one_out_effect": max(leave_one_out.values()) if leave_one_out else None,
    }


def evaluate_three_demo_reproduction_gate(evidence: Mapping[str, Any]) -> dict[str, Any]:
    structural_checks = dict(evidence.get("structural_checks", {}))
    invalid_reasons = list(evidence.get("invalid_reasons", []))
    structural_valid = bool(structural_checks) and all(bool(value) for value in structural_checks.values())
    complete = bool(evidence.get("complete_evaluation", False))
    deployable = bool(evidence.get("deployable_checkpoint_selected", False))
    no_infrastructure_errors = int(evidence.get("infrastructure_exceptions", 0)) == 0
    if not structural_valid or not no_infrastructure_errors or invalid_reasons:
        decision = "THREE_DEMO_REPRODUCTION_INVALID"
    elif not deployable:
        decision = "THREE_DEMO_REPRODUCTION_NOT_ESTABLISHED"
    elif not complete:
        decision = "THREE_DEMO_REPRODUCTION_INVALID"
    else:
        bare = {str(key): bool(value) for key, value in evidence["bare"].items()}
        correct = {str(key): bool(value) for key, value in evidence["correct"].items()}
        shuffled = {str(key): bool(value) for key, value in evidence["shuffled"].items()}
        absolute = leave_one_out_effects(correct, bare)
        specificity = leave_one_out_effects(correct, shuffled)
        positive = absolute["effect_count"] > 0 and specificity["effect_count"] > 0
        robust = (
            float(absolute["minimum_leave_one_out_effect"]) > 0
            and float(specificity["minimum_leave_one_out_effect"]) > 0
        )
        if positive and robust:
            decision = "THREE_DEMO_REPRODUCTION_PASS"
        elif positive:
            decision = "THREE_DEMO_REPRODUCTION_INCONCLUSIVE"
        else:
            decision = "THREE_DEMO_REPRODUCTION_NOT_ESTABLISHED"
    result = {
        "format": "three_demo_reproduction_gate_14b_v1",
        "decision": decision,
        "continue_to_one_demo": decision == "THREE_DEMO_REPRODUCTION_PASS",
        "structural_checks": structural_checks,
        "invalid_reasons": invalid_reasons,
        "complete_evaluation": complete,
        "deployable_checkpoint_selected": deployable,
        "infrastructure_exceptions": int(evidence.get("infrastructure_exceptions", 0)),
        "historical_comparison": dict(evidence.get("historical_comparison", {})),
        "exact_evidence_paths": list(evidence.get("exact_evidence_paths", [])),
    }
    if structural_valid and complete and deployable and no_infrastructure_errors and not invalid_reasons:
        result["behavioral_effects"] = {
            "absolute": leave_one_out_effects(evidence["correct"], evidence["bare"]),
            "specificity": leave_one_out_effects(evidence["correct"], evidence["shuffled"]),
        }
        result["LOO_ranges"] = {
            name: {
                "minimum": values["minimum_leave_one_out_effect"],
                "maximum": values["maximum_leave_one_out_effect"],
            }
            for name, values in result["behavioral_effects"].items()
        }
    result["gate_implementation_sha256"] = content_sha256(
        {key: value for key, value in result.items() if key != "gate_implementation_sha256"}
    )
    return result


def _paired_rows_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {
        str(row["state_example_id"]): dict(row)
        for row in payload.get("rows", [])
    }
    if len(rows) != len(payload.get("rows", [])):
        raise ValueError("Paired outcomes contain duplicate state IDs")
    return rows


def _state_ids(rows: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for row in rows:
        if isinstance(row, Mapping):
            value = row.get("state_example_id", row.get("state_id"))
        else:
            value = row
        if value is not None:
            result.add(str(value))
    return result


def _over_context_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        str(row["state_example_id"])
        for row in rows
        if bool(row.get("over_context")) or row.get("scoreable") is False
    }


def evaluate_d06_reproduction_gate(
    *,
    fresh: Mapping[str, Any],
    historical: Mapping[str, Any],
    fresh_selections: Iterable[Mapping[str, Any]],
    historical_selections: Iterable[Mapping[str, Any]],
    expected_train_completed: int,
    expected_heldout_completed: int,
    expected_label_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Compare sealed fresh D06 outcomes with read-only historical evidence."""
    fresh_rows = _paired_rows_by_id(fresh)
    historical_rows = _paired_rows_by_id(historical)
    fresh_train = {
        state_id
        for state_id, row in fresh_rows.items()
        if str(row.get("model_split")) == "model_train"
    }
    fresh_heldout = {
        state_id
        for state_id, row in fresh_rows.items()
        if str(row.get("model_split")) == "heldout_train_validation"
    }
    historical_train = {
        state_id
        for state_id, row in historical_rows.items()
        if str(row.get("model_split")) == "model_train"
    }
    historical_heldout = {
        state_id
        for state_id, row in historical_rows.items()
        if str(row.get("model_split")) == "heldout_train_validation"
    }
    fresh_labels = {
        state_id: str(row.get("label")) for state_id, row in fresh_rows.items()
    }
    historical_labels = {
        state_id: str(row.get("label")) for state_id, row in historical_rows.items()
    }
    fresh_selection_rows = [dict(row) for row in fresh_selections]
    historical_selection_rows = [dict(row) for row in historical_selections]
    fresh_selection_ids = _state_ids(fresh_selection_rows)
    historical_selection_ids = _state_ids(historical_selection_rows)
    fresh_over_context = _over_context_ids(fresh_selection_rows)
    historical_over_context = _over_context_ids(historical_selection_rows)
    fresh_replay = _state_ids(fresh.get("replay_semantic_missing_rows", []))
    historical_replay = _state_ids(
        historical.get("replay_semantic_missing_rows", [])
    )
    fresh_label_counts = {
        str(key): int(value) for key, value in fresh.get("label_counts", {}).items()
    }
    historical_label_counts = {
        str(key): int(value)
        for key, value in historical.get("label_counts", {}).items()
    }
    expected_labels = {
        str(key): int(value) for key, value in expected_label_counts.items()
    }
    checks = {
        "fresh_train_completed_count": len(fresh_train)
        == int(expected_train_completed),
        "fresh_heldout_completed_count": len(fresh_heldout)
        == int(expected_heldout_completed),
        "historical_train_reference_count": len(historical_train)
        == int(expected_train_completed),
        "historical_heldout_reference_count": len(historical_heldout)
        == int(expected_heldout_completed),
        "completed_state_sets_exact": set(fresh_rows) == set(historical_rows),
        "train_state_set_exact": fresh_train == historical_train,
        "heldout_state_set_exact": fresh_heldout == historical_heldout,
        "paired_labels_exact": fresh_labels == historical_labels,
        "fresh_label_counts_expected": fresh_label_counts == expected_labels,
        "historical_label_counts_expected": historical_label_counts
        == expected_labels,
        "over_context_state_set_exact": fresh_over_context
        == historical_over_context,
        "replay_semantic_failure_set_exact": fresh_replay == historical_replay,
        "fresh_state_universe_count": len(fresh_selection_rows) == 499,
        "historical_state_universe_count": len(historical_selection_rows) == 499,
        "fresh_state_universe_unique": len(fresh_selection_ids) == 499,
        "historical_state_universe_unique": len(historical_selection_ids) == 499,
        "state_universe_exact": fresh_selection_ids == historical_selection_ids,
    }
    passed = all(checks.values())
    result = {
        "format": "d06_three_demo_reproduction_gate_14g_v1",
        "decision": (
            "D06_THREE_DEMO_REPRODUCTION_PASS"
            if passed
            else "D06_THREE_DEMO_REPRODUCTION_FAIL"
        ),
        "passed": passed,
        "historical_artifacts_role": "read_only_comparison_after_fresh_d06_seal",
        "historical_artifacts_used_for_generation": False,
        "checks": checks,
        "counts": {
            "fresh_train_completed": len(fresh_train),
            "fresh_heldout_completed": len(fresh_heldout),
            "historical_train_completed": len(historical_train),
            "historical_heldout_completed": len(historical_heldout),
            "fresh_over_context": len(fresh_over_context),
            "historical_over_context": len(historical_over_context),
            "fresh_replay_semantic_failures": len(fresh_replay),
            "historical_replay_semantic_failures": len(historical_replay),
        },
        "label_counts": {
            "expected": expected_labels,
            "fresh": fresh_label_counts,
            "historical": historical_label_counts,
        },
        "differences": {
            "fresh_only_completed": sorted(set(fresh_rows) - set(historical_rows)),
            "historical_only_completed": sorted(
                set(historical_rows) - set(fresh_rows)
            ),
            "fresh_only_state_universe": sorted(
                fresh_selection_ids - historical_selection_ids
            ),
            "historical_only_state_universe": sorted(
                historical_selection_ids - fresh_selection_ids
            ),
            "label_mismatches": [
                {
                    "state_example_id": state_id,
                    "fresh": fresh_labels.get(state_id),
                    "historical": historical_labels.get(state_id),
                }
                for state_id in sorted(set(fresh_labels) | set(historical_labels))
                if fresh_labels.get(state_id) != historical_labels.get(state_id)
            ],
            "fresh_only_over_context": sorted(
                fresh_over_context - historical_over_context
            ),
            "historical_only_over_context": sorted(
                historical_over_context - fresh_over_context
            ),
            "fresh_only_replay_semantic": sorted(fresh_replay - historical_replay),
            "historical_only_replay_semantic": sorted(
                historical_replay - fresh_replay
            ),
        },
    }
    result["gate_sha256"] = content_sha256(result)
    return result


def validate_stage_completion(
    stage_dir: str | Path,
    source_commit: str,
    *,
    expected_run_uuid: str | None = None,
    expected_pipeline_config_sha256: str | None = None,
    expected_contract_sha256: str | None = None,
    expected_run_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(stage_dir)
    output_manifest_path = root / "output_manifest.json"
    validator_path = root / "validator.json"
    if not output_manifest_path.exists():
        return {"passed": False, "reason": "missing_output_manifest"}
    manifest = json.loads(output_manifest_path.read_text(encoding="utf-8"))
    checks: dict[str, bool] = {
        "source_commit": str(manifest.get("source_commit")) == source_commit,
        "stage_id": str(manifest.get("stage_id")) == root.name,
        "output_hashes": True,
        "input_completion_hashes": True,
    }
    if expected_run_uuid is not None:
        checks["run_uuid"] = (
            str(manifest.get("run_uuid")) == str(expected_run_uuid)
        )
    if expected_pipeline_config_sha256 is not None:
        checks["pipeline_config_sha256"] = (
            str(manifest.get("pipeline_config_sha256"))
            == str(expected_pipeline_config_sha256)
        )
    if expected_contract_sha256 is not None:
        checks["contract_sha256"] = (
            str(manifest.get("contract_sha256")) == str(expected_contract_sha256)
        )
    if expected_run_root is not None:
        checks["run_root"] = Path(
            str(manifest.get("run_root", ""))
        ).resolve(strict=False) == Path(expected_run_root).resolve(strict=False)
    for row in manifest.get("outputs", []):
        path = Path(str(row["path"]))
        if not path.is_absolute():
            path = root / path
        if not path.exists() or sha256_file(path) != str(row["sha256"]):
            checks["output_hashes"] = False
    for row in manifest.get("input_completion_manifests", []):
        path = Path(str(row["path"]))
        if not path.is_absolute():
            path = root / path
        if (
            not path.exists()
            or path.stat().st_size != int(row["size_bytes"])
            or sha256_file(path) != str(row["sha256"])
        ):
            checks["input_completion_hashes"] = False
    passed = all(checks.values()) and bool(manifest.get("passed", False))
    result = {
        "format": "rcmf_stage_validator_14b_v1",
        "stage_id": root.name,
        "passed": passed,
        "checks": checks,
        "output_manifest_sha256": sha256_file(output_manifest_path),
    }
    validator_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
