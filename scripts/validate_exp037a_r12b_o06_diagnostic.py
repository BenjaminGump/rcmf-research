from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.audit_exp037a_14j_first_divergence import _inventory_hash


DISCRETE_SELECTION_FIELDS = (
    "scoreable",
    "selected_transition_id",
    "over_context",
    "same_class_substitution",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _selection_comparison(
    formal_root: Path, arm_root: Path
) -> dict[str, Any]:
    sealed = {
        str(row["state_example_id"]): row
        for row in _rows(
            formal_root / "arms/1d/preflight/frozen_train_selections.jsonl"
        )
    }
    repaired = {
        str(row["state_example_id"]): row
        for row in _rows(arm_root / "preflight/frozen_train_selections.jsonl")
    }
    if set(sealed) != set(repaired) or len(repaired) != 499:
        raise RuntimeError("Repaired O05 state universe differs")
    discrete_mismatches = []
    token_mismatches = []
    for state_id in sorted(sealed):
        old = sealed[state_id]
        new = repaired[state_id]
        changed = [
            field for field in DISCRETE_SELECTION_FIELDS if old.get(field) != new.get(field)
        ]
        if changed:
            discrete_mismatches.append({"state_id": state_id, "fields": changed})
        if int(new["base_prompt_tokens"]) != int(old["base_prompt_tokens"]) + 4:
            token_mismatches.append({"state_id": state_id, "field": "base"})
        for index, (old_attempt, new_attempt) in enumerate(
            zip(old["attempts"], new["attempts"], strict=True)
        ):
            if old_attempt["transition_id"] != new_attempt["transition_id"]:
                token_mismatches.append(
                    {"state_id": state_id, "field": f"attempt_{index}_identity"}
                )
            if int(new_attempt["prompt_tokens"]) != int(old_attempt["prompt_tokens"]) + 4:
                token_mismatches.append(
                    {"state_id": state_id, "field": f"attempt_{index}_tokens"}
                )
    if discrete_mismatches or token_mismatches:
        raise RuntimeError(
            "Repaired O05 differs beyond the preregistered four-token alignment"
        )
    sealed_panel = _json(formal_root / "arms/1d/preflight/initial_panel.json")
    repaired_panel = _json(arm_root / "preflight/initial_panel.json")
    if sealed_panel != repaired_panel:
        raise RuntimeError("Repaired O05 panel order changed")
    return {
        "state_count": len(repaired),
        "discrete_match_count": len(repaired),
        "base_plus_four_count": len(repaired),
        "attempt_plus_four_count": sum(len(row["attempts"]) for row in repaired.values()),
        "panel_exact": True,
        "scoreable_count": sum(bool(row["scoreable"]) for row in repaired.values()),
        "static_over_context_count": sum(
            not bool(row["scoreable"]) for row in repaired.values()
        ),
    }


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    diagnostic_root = args.diagnostic_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == formal_root or formal_root in output_root.parents:
        raise ValueError("Validation output cannot be inside the sealed formal root")
    before = _inventory_hash(formal_root)
    arm_root = diagnostic_root / "arm_1d"
    selection = _selection_comparison(formal_root, arm_root)
    effective_path = arm_root / "paired_causal/effective_runtime_config.json"
    manifest_path = arm_root / "paired_causal/condition_manifest.json"
    outcomes_path = arm_root / "paired_causal/paired_outcomes.json"
    effective = _json(effective_path)
    manifest = _json(manifest_path)
    outcomes = _json(outcomes_path)
    provenance = manifest["paired_causal_runtime"]
    checks: dict[str, bool] = {
        "effective_profile": effective["effective_runtime_prompt_profile"]
        == "full_demo_first_only",
        "arm_profile": effective["arm_resolved_prompt_profile"]
        == "full_demo_first_only",
        "legacy_profile_preserved_as_audit": effective[
            "legacy_replay_prompt_profile"
        ]
        == "full_demo",
        "only_prompt_execution_diff": effective["changed_execution_fields"]
        == ["prompt_profile"],
        "manifest_profile": provenance["effective_runtime_prompt_profile"]
        == "full_demo_first_only",
        "manifest_effective_artifact_hash": provenance[
            "effective_runtime_artifact_sha256"
        ]
        == sha256_file(effective_path),
        "outcome_profile": outcomes["paired_causal_runtime"] == provenance,
        "minimum_label_gate_or_exhausted": bool(
            outcomes["minimum_label_gate_passed"]
            or outcomes["maximum_state_space_exhausted"]
        ),
        "outcomes_finite": _finite(outcomes),
        "generated_only": int(outcomes["reused_conditions"]) == 0,
    }
    rows = list(outcomes["rows"])
    if len({str(row["state_example_id"]) for row in rows}) != len(rows):
        raise RuntimeError("O06 outcomes contain duplicate state IDs")
    label_counts = dict(sorted(Counter(str(row["label"]) for row in rows).items()))
    checks["label_counts"] = label_counts == {
        str(key): int(value) for key, value in outcomes["label_counts"].items()
    }
    condition_by_key = {
        str(row["condition_key"]): row for row in manifest["conditions"]
    }
    if len(condition_by_key) != len(manifest["conditions"]):
        raise RuntimeError("O06 condition manifest contains duplicate keys")
    output_dir = arm_root / "paired_causal/condition_outputs"
    output_rows = []
    for paired in rows:
        for key_name in ("bare_condition_key", "raw_condition_key"):
            condition_key = str(paired[key_name])
            condition = condition_by_key[condition_key]
            output_path = output_dir / condition_checkpoint_name(condition_key)
            output = _json(output_path)
            row_checks = {
                "status": output.get("status") == "complete",
                "condition_key": output.get("condition_key") == condition_key,
                "state": output.get("state_example_id")
                == paired["state_example_id"],
                "profile": output.get("paired_causal_runtime") == provenance,
                "within_context": int(output["prompt_tokens"]) <= 40960,
                "finite": _finite(output),
            }
            if not all(row_checks.values()):
                raise RuntimeError(
                    f"Invalid O06 condition output {condition_key}: {row_checks}"
                )
            output_rows.append(
                {
                    "condition_key": condition_key,
                    "condition_name": condition["condition_name"],
                    "state_example_id": paired["state_example_id"],
                    "path": str(output_path),
                    "sha256": sha256_file(output_path),
                }
            )
    checks["complete_bare_raw_pairs"] = len(output_rows) == 2 * len(rows)
    checks["generated_count"] = int(outcomes["generated_conditions"]) == len(
        output_rows
    )
    checks["executed_count"] = int(
        outcomes["executed_condition_output_count"]
    ) == len(output_rows)
    checks["state_count"] = int(outcomes["state_count"]) == len(rows)
    checks["static_missing_count"] = int(outcomes["over_context_missing_count"]) == selection[
        "static_over_context_count"
    ]
    checks["replay_missing_typed"] = all(
        row.get("condition_status") == "replay_semantic_mismatch_missing"
        for row in outcomes["replay_semantic_missing_rows"]
    )
    if not all(checks.values()):
        raise RuntimeError(f"Strict repaired O06 validation failed: {checks}")
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed 14j root changed during O06 validation")
    output_root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(
        output_root / "o06_validation.json",
        {
            "format": "exp037a_r12b_o06_diagnostic_validation_v1",
            "passed": True,
            "strict_output_validation": True,
            "selection_contract": selection,
            "checks": checks,
            "paired_state_count": len(rows),
            "model_train_count": sum(
                row["model_split"] == "model_train" for row in rows
            ),
            "heldout_count": sum(
                row["model_split"] == "heldout_train_validation" for row in rows
            ),
            "label_counts": label_counts,
            "static_over_context_count": int(
                outcomes["over_context_missing_count"]
            ),
            "replay_missing_count": int(outcomes["replay_semantic_missing_count"]),
            "generated_condition_count": int(
                outcomes["generated_conditions"]
            ),
            "reused_condition_count": int(outcomes["reused_conditions"]),
            "initial_completed_state_count": int(
                outcomes["initial_completed_state_count"]
            ),
            "expanded_state_count": int(outcomes["expanded_state_count"]),
            "minimum_label_gate_passed": bool(
                outcomes["minimum_label_gate_passed"]
            ),
            "maximum_state_space_exhausted": bool(
                outcomes["maximum_state_space_exhausted"]
            ),
            "effective_runtime": effective,
            "artifacts": {
                "effective_runtime": {
                    "path": str(effective_path),
                    "sha256": sha256_file(effective_path),
                },
                "condition_manifest": {
                    "path": str(manifest_path),
                    "sha256": sha256_file(manifest_path),
                },
                "paired_outcomes": {
                    "path": str(outcomes_path),
                    "sha256": sha256_file(outcomes_path),
                },
            },
            "condition_output_hashes": output_rows,
            "formal_root_before": before,
            "formal_root_after": after,
            "formal_root_unchanged": True,
            "scientific_result_eligible": False,
        },
    )
    print(
        json.dumps(
            {
                "passed": True,
                "states": len(rows),
                "labels": label_counts,
                "conditions": len(output_rows),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
