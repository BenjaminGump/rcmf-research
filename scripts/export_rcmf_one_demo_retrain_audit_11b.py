"""Export Git-safe EXP-034A N1/N2 traces and compact scientific records."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import sha256_file
from scripts.export_rcmf_benefit_preserving_audit_9b import (
    strict_redact,
    strict_verify_tree,
)
from scripts.export_rcmf_joint_full_bank_audit_9a import (
    atomic_json,
    atomic_jsonl,
    first37_record,
    materialized_step,
    register_sensitive_observation,
    verify_git_safe_redaction,
)
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence


RUN_UUID = "rcmf_exp031a_one_demo_retrain_11b_20260829_001"
FORMAT = "rcmf_one_demo_retrain_detailed_audit_11b_v1"
SINGLE_SCIENTIFIC_CHANGE = "training_prompt_full_demo_to_full_demo_first_only"
NEW_CONDITIONS = ("N1", "N2")
ALL_CONDITIONS = ("D0", "old_D1", "N1", "N2")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _stage_existing_tree(source: Path, destination: Path) -> None:
    """Copy committed checkpoint records into the final export staging tree."""
    if not source.exists():
        return
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Result checkpoint tree contains a symlink: {path}")
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise ValueError(f"Unsupported result checkpoint entry: {path}")


def _publish_staged_tree(staged: Path, destination: Path) -> None:
    """Publish staged files atomically while preserving earlier checkpoint files."""
    if not destination.exists():
        staged.replace(destination)
        return
    for path in sorted(staged.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(staged)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
    directories = sorted(
        (item for item in staged.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for path in directories:
        path.rmdir()
    staged.rmdir()


def _task_path(root: Path, condition: str, task_id: str) -> Path:
    actual = "D1" if condition == "old_D1" else condition
    return root / f"dev/conditions/{actual}/task_results/{task_id}.json"


def _register_task(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step["exact_executed_code"], step["complete_environment_observation"]
        )
        for turn in step["complete_trajectory_so_far"]:
            register_sensitive_observation(
                turn.get("response", ""), turn.get("observation", "")
            )


def _safe_step(task_path: Path, task: Mapping[str, Any], step: Mapping[str, Any]) -> dict[str, Any]:
    tensor_key = (
        "lambda-only:"
        + str(step["field"]["tensor_artifact"])
        + ":"
        + str(step["field"]["tensor_artifact_sha256"])
    )
    row = first37_record(task_path, task, step, tensor_key, [])
    row["format"] = FORMAT
    row["audit_scope"] = "official_appworld_dev_one_demo_retrained_complete_agent"
    row["prompt_profile"] = "full_demo_first_only"
    row["field"]["top_memory_contributions"] = {
        "status": "not_computed_runtime_prohibited",
        "not_used_by_model_or_field_read": True,
        "ranking": [],
    }
    row["lambda_only_field_tensor"] = {
        "path": str(Path(str(step["field"]["tensor_artifact"])).resolve()),
        "sha256": str(step["field"]["tensor_artifact_sha256"]),
        "query_shape": step["field"]["query"]["shape"],
        "slot_shape": step["field"]["slots"]["shape"],
        "slot_dtype": step["field"]["slots"]["dtype"],
    }
    return strict_redact(row)


def _comparison_markdown(task_id: str, tasks: Mapping[str, Mapping[str, Any]]) -> str:
    pairs = (("D0", "N1"), ("N2", "N1"), ("old_D1", "N1"))
    divergences = {
        f"{left}_vs_{right}": first_divergence(tasks[left], tasks[right])
        for left, right in pairs
    }
    lines = [
        f"# EXP-034A dev comparison: {task_id}", "",
        "Git-safe trace materialization. D0 and old_D1 are immutable EXP-033A references; N1/N2 are EXP-034A rows.", "",
        "## Outcomes", "",
    ]
    for condition in ALL_CONDITIONS:
        task = tasks[condition]
        lines.append(
            f"- {condition}: success={bool(task['success'])}, steps={int(task['step_count'])}, wall_seconds={float(task['wall_seconds']):.3f}"
        )
    lines.extend(["", "## First Divergences", "", "```json", json.dumps(strict_redact(divergences), indent=2, sort_keys=True), "```", ""])
    selected = {
        int(value["step_id"])
        for value in divergences.values()
        if value is not None and value.get("step_id") is not None
    }
    selected.update(
        int(task["steps"][-1]["step_id"]) for task in tasks.values() if task["steps"]
    )
    for step_id in sorted(selected):
        lines.extend([f"## Materialized Step {step_id}", ""])
        for condition in ALL_CONDITIONS:
            step = next(
                (row for row in tasks[condition]["steps"] if int(row["step_id"]) == step_id),
                None,
            )
            lines.extend([f"### {condition}", ""])
            if step is None:
                lines.extend(["Condition terminated before this step.", ""])
            else:
                lines.extend(["```json", json.dumps(strict_redact(materialized_step(step)), ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    lines.extend([
        "## Interpretation", "",
        "- **VERIFIED:** task outcomes, actions, observations, and first divergences are from immutable raw rows.",
        "- **INFERENCE:** N1-N2 differences are consistent with a memory-specific effect of the one-demo-trained whole-bank model.",
        "- **UNVERIFIED:** mechanisms downstream of the first recorded behavioral divergence.", "",
    ])
    return "\n".join(lines)


def _attempt_rows(artifact_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (artifact_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _paired_outcome_summary(artifact_dir: Path, old_root: Path) -> dict[str, Any]:
    paired_outcomes_path = artifact_dir / "paired_causal/paired_outcomes.json"
    condition_manifest_path = artifact_dir / "paired_causal/condition_manifest.json"
    new_payload = _json(paired_outcomes_path)
    new = new_payload["rows"]
    old = _json(old_root / "../appworld_structured_gate_compiler_7hr_20260823_001/paired_causal/paired_outcomes.json")["rows"]
    old_by_id = {str(row["state_example_id"]): row for row in old}
    changed = [
        str(row["state_example_id"])
        for row in new
        if str(row["label"]) != str(old_by_id[str(row["state_example_id"])]["label"])
    ]
    return {
        "format": "rcmf_one_demo_retrain_paired_outcome_summary_11b_v1",
        "state_count": len(new),
        "labels": {
            split: dict(sorted(Counter(str(row["label"]) for row in new if str(row["model_split"]) == split).items()))
            for split in ("model_train", "heldout_train_validation")
        },
        "old_labels": {
            split: dict(sorted(Counter(str(row["label"]) for row in old if str(row["model_split"]) == split).items()))
            for split in ("model_train", "heldout_train_validation")
        },
        "changed_label_count": len(changed),
        "changed_state_ids": changed,
        "condition_manifest_sha256": sha256_file(condition_manifest_path),
        "elapsed_seconds": float(new_payload["elapsed_seconds"]),
        "paired_outcomes_sha256": sha256_file(paired_outcomes_path),
        "dev_used": False,
    }


def export(artifact_dir: Path, old_root: Path, audit_root: Path, result_root: Path) -> dict[str, Any]:
    if audit_root.exists():
        raise FileExistsError("Refusing to overwrite an EXP-034A Git-safe export")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-034A export temporary root")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)
    _stage_existing_tree(result_root, result_tmp)

    final = _json(artifact_dir / "dev/final_summary.json")
    analysis = _json(artifact_dir / "analysis/paired_analysis.json")
    task_ids = [str(value) for value in final["task_ids"]]
    if len(task_ids) != 57:
        raise ValueError("EXP-034A export requires all 57 dev tasks")
    tasks_by_id = {
        task_id: {
            "D0": _json(_task_path(old_root, "D0", task_id)),
            "old_D1": _json(_task_path(old_root, "old_D1", task_id)),
            "N1": _json(_task_path(artifact_dir, "N1", task_id)),
            "N2": _json(_task_path(artifact_dir, "N2", task_id)),
        }
        for task_id in task_ids
    }
    for tasks in tasks_by_id.values():
        for task in tasks.values():
            _register_task(task)

    per_task = []
    comparisons = []
    step_count = 0
    for task_id in task_ids:
        tasks = tasks_by_id[task_id]
        for condition in NEW_CONDITIONS:
            source = _task_path(artifact_dir, condition, task_id)
            task = tasks[condition]
            safe = [_safe_step(source, task, step) for step in task["steps"]]
            step_count += len(safe)
            atomic_jsonl(audit_tmp / condition / f"{task_id}.jsonl", safe)
        markdown = _comparison_markdown(task_id, tasks)
        path = audit_tmp / "comparisons" / f"{task_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        row = {
            "task_id": task_id,
            "success": {condition: bool(tasks[condition]["success"]) for condition in ALL_CONDITIONS},
            "comparison_markdown": f"comparisons/{task_id}.md",
        }
        comparisons.append(strict_redact(row))
        for condition in ALL_CONDITIONS:
            root = old_root if condition in {"D0", "old_D1"} else artifact_dir
            source = _task_path(root, condition, task_id)
            task = tasks[condition]
            per_task.append(strict_redact({
                "task_id": task_id, "condition": condition,
                "success": bool(task["success"]), "step_count": int(task["step_count"]),
                "usage": task["usage"], "counts": task["counts"],
                "wall_seconds": float(task["wall_seconds"]),
                "raw_lambda_task_path": str(source.resolve()),
                "raw_lambda_task_sha256": sha256_file(source),
            }))

    assets = _json(artifact_dir / "audits/static_prompt_assets.json")
    atomic_json(audit_tmp / "static_prompt_assets.json", strict_redact(assets))
    attempts = _attempt_rows(artifact_dir)
    open_attempts = sorted(
        {str(row["attempt_id"]) for row in attempts if row["event"] == "start"}
        - {str(row["attempt_id"]) for row in attempts if row["event"] == "end"}
    )
    summary = {
        "format": "rcmf_one_demo_retrain_result_summary_11b_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "formal": strict_redact(final),
        "analysis": strict_redact(analysis),
        "attempt_count": len({str(row["attempt_id"]) for row in attempts}),
        "open_attempt_ids": open_attempts,
        "artifact_root": str(artifact_dir.resolve()),
        "artifact_bytes": sum(path.stat().st_size for path in artifact_dir.rglob("*") if path.is_file()),
        "single_scientific_change": SINGLE_SCIENTIFIC_CHANGE,
    }
    atomic_json(result_tmp / "summary.json", summary)
    source_files = {
        "dependency_manifest.json": artifact_dir / "dependency_manifest.json",
        "one_demo_state_cache_summary.json": artifact_dir / "prompt_dependent/one_demo_state_cache_summary.json",
        "training_unit_manifest.json": artifact_dir / "joint_training/training_unit_manifest.json",
        "training_summary.json": artifact_dir / "joint_training/training_summary.json",
        "heldout_selection.json": artifact_dir / "heldout_validation/live_full_field/checkpoint_selection.json",
        "deployment_field_summary.json": artifact_dir / "deployment_field/instant_add_report.json",
        "paired_analysis.json": artifact_dir / "analysis/paired_analysis.json",
        "trajectory_metrics.json": artifact_dir / "analysis/trajectory_metrics.json",
        "runtime_preflight.json": artifact_dir / "runtime/formal_gpu_preflight.json",
    }
    for name, source in source_files.items():
        atomic_json(result_tmp / name, strict_redact(_json(source)))
    atomic_json(result_tmp / "paired_outcomes_summary.json", strict_redact(_paired_outcome_summary(artifact_dir, old_root)))
    atomic_jsonl(result_tmp / "dev_per_task.jsonl", per_task)
    atomic_jsonl(result_tmp / "comparisons.jsonl", comparisons)
    atomic_jsonl(result_tmp / "attempts.jsonl", [strict_redact(row) for row in attempts])

    verification = verify_git_safe_redaction(audit_tmp)
    strict_audit = strict_verify_tree(audit_tmp)
    strict_results = strict_verify_tree(result_tmp)
    if verification["registered_sensitive_observation_leak_count"] != 0:
        raise RuntimeError("EXP-034A Git-safe audit redaction failed")
    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "task_count": len(task_ids),
        "new_conditions": list(NEW_CONDITIONS),
        "new_task_condition_count": len(task_ids) * 2,
        "new_step_row_count": step_count,
        "immutable_exp033a_reference_conditions": ["D0", "D1"],
        "immutable_exp033a_audit_index": str((old_root.parent.parent.parent / "research/audits/rcmf_exp031a_one_demo_dev_11a_20260829_001/index.json").resolve()),
        "static_prompt_assets": "static_prompt_assets.json",
        "raw_lambda_artifact_root": str(artifact_dir.resolve()),
        "lambda_only_tensors_recorded_per_step_by_path_sha_shape_dtype": True,
        "secret_verification": verification,
        "strict_audit_tree": strict_audit,
        "strict_result_tree": strict_results,
        "independently_verified": True,
    }
    atomic_json(audit_tmp / "index.json", index)
    strict_verify_tree(audit_tmp)
    audit_tmp.replace(audit_root)
    _publish_staged_tree(result_tmp, result_root)
    if strict_verify_tree(result_root) != strict_results:
        raise RuntimeError("Published EXP-034A result tree differs from verified staging")
    return index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--old-artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, default=Path("research/audits") / RUN_UUID)
    parser.add_argument("--result-root", type=Path, default=Path("research/results/exp034a_rcmf_one_demo_retrain"))
    args = parser.parse_args()
    result = export(args.artifact_dir, args.old_artifact_dir, args.audit_root, args.result_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
