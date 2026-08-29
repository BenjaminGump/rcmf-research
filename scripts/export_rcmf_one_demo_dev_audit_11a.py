"""Export Git-safe reconstructible EXP-033A dev traces and result records."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
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


RUN_UUID = "rcmf_exp031a_one_demo_dev_11a_20260829_001"
FORMAT = "rcmf_one_demo_dev_detailed_audit_11a_v1"
CONDITIONS = ("D0", "D1", "D2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--audit-root", type=Path, default=Path("research/audits") / RUN_UUID
    )
    parser.add_argument(
        "--result-root",
        type=Path,
        default=Path("research/results/exp033a_rcmf_one_demo_dev"),
    )
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_path(artifact_dir: Path, condition: str, task_id: str) -> Path:
    return artifact_dir / f"dev/conditions/{condition}/task_results/{task_id}.json"


def _register_task_secrets(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step["exact_executed_code"], step["complete_environment_observation"]
        )
        for turn in step["complete_trajectory_so_far"]:
            register_sensitive_observation(
                turn.get("response", ""), turn.get("observation", "")
            )


def _safe_step(
    task_path: Path, task: Mapping[str, Any], step: Mapping[str, Any]
) -> dict[str, Any]:
    tensor_key = (
        "lambda-only:"
        + str(step["field"]["tensor_artifact"])
        + ":"
        + str(step["field"]["tensor_artifact_sha256"])
    )
    row = first37_record(task_path, task, step, tensor_key, [])
    row["format"] = FORMAT
    row["audit_scope"] = "official_appworld_dev_complete_agent"
    row["prompt_profile"] = "full_demo_first_only"
    row["evaluation_only"] = True
    row["field"]["top_memory_contributions"] = {
        "status": "not_computed_runtime_prohibited",
        "not_used_by_model_or_field_read": True,
        "ranking": [],
    }
    row["lambda_only_field_tensor"] = {
        "path": str(Path(str(step["field"]["tensor_artifact"])).resolve()),
        "sha256": str(step["field"]["tensor_artifact_sha256"]),
        "query_shape": None
        if step["field"]["query"] is None
        else step["field"]["query"]["shape"],
        "slot_shape": step["field"]["slots"]["shape"],
        "slot_dtype": step["field"]["slots"]["dtype"],
    }
    return strict_redact(row)


def _comparison_markdown(
    task_id: str, tasks: Mapping[str, Mapping[str, Any]]
) -> str:
    pairs = (("D0", "D1"), ("D2", "D1"))
    divergences = {
        f"{left}_vs_{right}": first_divergence(tasks[left], tasks[right])
        for left, right in pairs
    }
    lines = [
        f"# EXP-033A dev comparison: {task_id}",
        "",
        "Git-safe trace materialization. Credentials and JWTs use typed SHA256 "
        "placeholders; exact unredacted rows and field tensors remain on Lambda.",
        "",
        "## Outcomes",
        "",
    ]
    for condition in CONDITIONS:
        task = tasks[condition]
        lines.append(
            f"- {condition}: success={bool(task['success'])}, "
            f"steps={int(task['step_count'])}, wall_seconds={float(task['wall_seconds']):.3f}"
        )
    lines.extend(
        ["", "## First Divergences", "", "```json", json.dumps(strict_redact(divergences), indent=2, sort_keys=True), "```", ""]
    )
    selected = {
        int(value["step_id"])
        for value in divergences.values()
        if value is not None and value.get("step_id") is not None
    }
    selected.update(
        int(task["steps"][-1]["step_id"])
        for task in tasks.values()
        if task["steps"]
    )
    for step_id in sorted(selected):
        lines.extend([f"## Materialized Step {step_id}", ""])
        for condition in CONDITIONS:
            step = next(
                (
                    row
                    for row in tasks[condition]["steps"]
                    if int(row["step_id"]) == step_id
                ),
                None,
            )
            lines.extend([f"### {condition}", ""])
            if step is None:
                lines.extend(["Condition terminated before this step.", ""])
            else:
                lines.extend(
                    [
                        "```json",
                        json.dumps(
                            strict_redact(materialized_step(step)),
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        ),
                        "```",
                        "",
                    ]
                )
    lines.extend(
        [
            "## Interpretation",
            "",
            "- **VERIFIED:** outcomes, actions, observations, and first divergences above come from the immutable paired task rows.",
            "- **INFERENCE:** a D1-D2 difference is consistent with a memory-specific whole-bank effect under the one-demo prompt.",
            "- **UNVERIFIED:** downstream mechanisms beyond the first recorded behavioral divergence.",
            "",
        ]
    )
    return "\n".join(lines)


def _attempt_rows(artifact_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (artifact_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def export(artifact_dir: Path, audit_root: Path, result_root: Path) -> dict[str, Any]:
    if audit_root.exists() or result_root.exists():
        raise FileExistsError("Refusing to overwrite an EXP-033A Git-safe export")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-033A export temporary root")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)

    final = _json(artifact_dir / "dev/final_summary.json")
    analysis = _json(artifact_dir / "analysis/paired_analysis.json")
    task_ids = [str(value) for value in final["task_ids"]]
    if len(task_ids) != 57:
        raise ValueError("EXP-033A export requires all 57 official dev tasks")
    tasks_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    per_task_rows = []
    step_count = 0
    for task_id in task_ids:
        tasks_by_id[task_id] = {}
        for condition in CONDITIONS:
            source = _task_path(artifact_dir, condition, task_id)
            task = _json(source)
            _register_task_secrets(task)
            tasks_by_id[task_id][condition] = task
            safe_steps = [_safe_step(source, task, step) for step in task["steps"]]
            step_count += len(safe_steps)
            atomic_jsonl(audit_tmp / condition / f"{task_id}.jsonl", safe_steps)
            per_task_rows.append(
                strict_redact(
                    {
                        "task_id": task_id,
                        "condition": condition,
                        "success": bool(task["success"]),
                        "step_count": int(task["step_count"]),
                        "usage": task["usage"],
                        "counts": task["counts"],
                        "wall_seconds": float(task["wall_seconds"]),
                        "raw_lambda_task_path": str(source.resolve()),
                        "raw_lambda_task_sha256": sha256_file(source),
                    }
                )
            )
        comparison = _comparison_markdown(task_id, tasks_by_id[task_id])
        comparison_path = audit_tmp / "comparisons" / f"{task_id}.md"
        comparison_path.parent.mkdir(parents=True, exist_ok=True)
        comparison_path.write_text(comparison, encoding="utf-8")

    raw_assets = _json(artifact_dir / "raw_audit/static_prompt_assets.json")
    atomic_json(audit_tmp / "static_prompt_assets.json", strict_redact(raw_assets))
    attempts = _attempt_rows(artifact_dir)
    open_attempts = sorted(
        {str(row["attempt_id"]) for row in attempts if row["event"] == "start"}
        - {str(row["attempt_id"]) for row in attempts if row["event"] == "end"}
    )
    summary = {
        "format": "rcmf_one_demo_dev_result_summary_11a_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "formal": strict_redact(final),
        "analysis": strict_redact(analysis),
        "attempt_count": len({str(row["attempt_id"]) for row in attempts}),
        "open_attempt_ids": open_attempts,
        "artifact_root": str(artifact_dir.resolve()),
        "artifact_bytes": sum(
            path.stat().st_size for path in artifact_dir.rglob("*") if path.is_file()
        ),
        "no_training": True,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "official_dev_only": True,
    }
    atomic_json(result_tmp / "summary.json", summary)
    for name in (
        "dev_manifest.json",
        "prompt_manifest.json",
        "condition_manifest.json",
        "runtime_preflight.json",
    ):
        atomic_json(result_tmp / name, strict_redact(_json(artifact_dir / name)))
    atomic_json(result_tmp / "paired_analysis.json", strict_redact(analysis))
    atomic_json(
        result_tmp / "trajectory_metrics.json", strict_redact(analysis["trajectory_metrics"])
    )
    atomic_jsonl(result_tmp / "per_task.jsonl", per_task_rows)
    atomic_jsonl(result_tmp / "attempts.jsonl", [strict_redact(row) for row in attempts])

    verification = verify_git_safe_redaction(audit_tmp)
    strict_audit = strict_verify_tree(audit_tmp)
    strict_results = strict_verify_tree(result_tmp)
    if verification["registered_sensitive_observation_leak_count"] != 0:
        raise RuntimeError("EXP-033A Git-safe audit redaction failed")
    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "task_count": len(task_ids),
        "conditions": list(CONDITIONS),
        "task_condition_count": len(task_ids) * len(CONDITIONS),
        "step_row_count": step_count,
        "per_condition_jsonl_count": {condition: len(task_ids) for condition in CONDITIONS},
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
    result_tmp.replace(result_root)
    return index


def main() -> None:
    args = parse_args()
    result = export(args.artifact_dir, args.audit_root, args.result_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
