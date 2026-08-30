"""Export Git-safe EXP-034B traces through the verified EXP-034A exporter."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

import export_rcmf_one_demo_retrain_audit_11b as base

from rcmf.utils.serialization import sha256_file


RUN_UUID = "rcmf_one_demo_selector_retrain_11c_20260830_001"
_BASE_COMPARISON_MARKDOWN = base._comparison_markdown


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _comparison_markdown(
    task_id: str, tasks: Mapping[str, Mapping[str, Any]]
) -> str:
    text = _BASE_COMPARISON_MARKDOWN(task_id, tasks)
    text = text.replace("EXP-034A dev comparison", "EXP-034B dev comparison")
    text = text.replace(
        "N1/N2 are EXP-034A rows.",
        "N1/N2 are frozen EXP-034B fresh-selector rows.",
    )
    text = text.replace(
        "N1-N2 differences are consistent with a memory-specific effect of the one-demo-trained whole-bank model.",
        "N1-N2 differences are reported directionally; they are not presumed to be beneficial or memory-specific.",
    )
    return text


def _paired_outcome_summary(
    artifact_dir: Path, exp034a_root: Path
) -> dict[str, Any]:
    new_path = artifact_dir / "paired_causal/paired_outcomes.json"
    manifest_path = artifact_dir / "paired_causal/condition_manifest.json"
    old_path = exp034a_root / "paired_causal/paired_outcomes.json"
    new_payload = _json(new_path)
    old_payload = _json(old_path)
    new = new_payload["rows"]
    old = old_payload["rows"]
    old_by_id = {str(row["state_example_id"]): row for row in old}
    changed = [
        str(row["state_example_id"])
        for row in new
        if str(row["label"])
        != str(old_by_id[str(row["state_example_id"])]["label"])
    ]
    return {
        "format": "rcmf_one_demo_selector_retrain_paired_outcome_summary_11c_v1",
        "state_count": len(new),
        "labels": dict(sorted(Counter(str(row["label"]) for row in new).items())),
        "exp034a_labels": dict(
            sorted(Counter(str(row["label"]) for row in old).items())
        ),
        "changed_label_count": len(changed),
        "changed_state_ids": changed,
        "condition_manifest_sha256": sha256_file(manifest_path),
        "paired_outcomes_sha256": sha256_file(new_path),
        "exp034a_paired_outcomes_sha256": sha256_file(old_path),
        "dev_used": False,
    }


def _result_source_files(artifact_dir: Path) -> dict[str, Path]:
    return {
        "dependency_manifest.json": artifact_dir / "dependency_manifest.json",
        "selector_state_manifest.json": artifact_dir / "selector_state_manifest.json",
        "selector_recipe.json": artifact_dir / "selector/locked_recipe.json",
        "selector_training.json": artifact_dir / "selector/selector_training.json",
        "selector_diagnostics.json": artifact_dir / "selector/selector_diagnostics.json",
        "selector_factor_summary.json": artifact_dir / "selector/selector_factor_summary.json",
        "selection_manifest.json": artifact_dir / "preflight/selection_manifest.json",
        "selection_comparison.json": artifact_dir / "preflight/selection_comparison.json",
        "training_unit_manifest.json": artifact_dir / "joint_training/training_unit_manifest.json",
        "training_summary.json": artifact_dir / "joint_training/training_summary.json",
        "heldout_live_summary.json": artifact_dir / "heldout_validation/live_full_field/validation_summary.json",
        "heldout_selection.json": artifact_dir / "heldout_validation/live_full_field/checkpoint_selection.json",
        "deployment_field_summary.json": artifact_dir / "deployment_field/instant_add_report.json",
        "dev_condition_manifest.json": artifact_dir / "dev/condition_manifest.json",
        "dev_runtime_preflight.json": artifact_dir / "runtime/dev_runtime_preflight.json",
        "d0_reuse_proof.json": artifact_dir / "dev/d0_reuse_proof.json",
        "dev_final_summary.json": artifact_dir / "dev/final_summary.json",
        "n1_summary.json": artifact_dir / "dev/conditions/N1/summary.json",
        "n2_summary.json": artifact_dir / "dev/conditions/N2/summary.json",
        "paired_analysis.json": artifact_dir / "analysis/paired_analysis.json",
        "trajectory_metrics.json": artifact_dir / "analysis/trajectory_metrics.json",
        "runtime_preflight.json": artifact_dir / "runtime/formal_gpu_preflight.json",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--exp033a-artifact-dir", type=Path, required=True)
    parser.add_argument("--exp034a-artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    args = parser.parse_args()

    base.RUN_UUID = RUN_UUID
    base.FORMAT = "rcmf_one_demo_selector_retrain_detailed_audit_11c_v1"
    base.SINGLE_SCIENTIFIC_CHANGE = (
        "fresh_same_recipe_selector_parameters_trained_on_one_demo_states"
    )
    base._comparison_markdown = _comparison_markdown
    base._result_source_files = _result_source_files
    base._paired_outcome_summary = (
        lambda artifact_dir, _old_root: _paired_outcome_summary(
            artifact_dir, args.exp034a_artifact_dir
        )
    )
    result = base.export(
        args.artifact_dir,
        args.exp033a_artifact_dir,
        args.audit_root,
        args.result_root,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
