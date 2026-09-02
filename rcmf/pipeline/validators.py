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


def validate_stage_completion(stage_dir: str | Path, source_commit: str) -> dict[str, Any]:
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
    }
    for row in manifest.get("outputs", []):
        path = Path(str(row["path"]))
        if not path.is_absolute():
            path = root / path
        if not path.exists() or sha256_file(path) != str(row["sha256"]):
            checks["output_hashes"] = False
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
