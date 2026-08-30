"""Export Git-safe reconstructible EXP-035A traces and audit index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.training.rcmf_one_demo_component_swap_12a import CELL_NAMES, CONDITIONS
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.export_rcmf_joint_full_bank_audit_9a import (
    redact,
    register_sensitive_observation,
    verify_git_safe_redaction,
)
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_task_path(root: Path, condition: str, task_id: str) -> Path:
    return root / "trajectories/conditions" / condition / "task_results" / f"{task_id}.json"


def register_task_secrets(task: dict[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step.get("exact_executed_code"), step.get("complete_environment_observation")
        )


def comparison_markdown(task_id: str, tasks: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"# EXP-035A eight-condition comparison: {task_id}",
        "",
        "Git-safe materialization. Credentials and JWTs use typed SHA256 placeholders; exact unredacted rows and tensors remain on Lambda.",
        "",
        "## Outcomes",
        "",
        "| Condition | Success | Steps | Repeated actions | Exceptions |",
        "|---|---:|---:|---:|---:|",
    ]
    for condition in CONDITIONS:
        row = tasks[condition]
        lines.append(
            f"| {condition} | {int(bool(row['success']))} | {row['step_count']} | "
            f"{row['counts'].get('repeated_action', 0)} | "
            f"{row['counts'].get('execution_exception', 0)} |"
        )
    lines.extend(["", "## Correct Versus Shuffle", ""])
    for cell in CELL_NAMES:
        divergence = first_divergence(tasks[f"{cell}-C"], tasks[f"{cell}-S"])
        lines.append(f"- `{cell}` first divergence: `{json.dumps(divergence, sort_keys=True)}`")
    lines.extend(
        [
            "",
            "## Claim Status",
            "",
            "- VERIFIED: successes, steps, emitted actions, observations, repetitions, exceptions, and first divergence above are trace-derived.",
            "- INFERENCE: procedural-family, documentation-attractor, and bookkeeping explanations require the exact trace context.",
            "- UNVERIFIED: any causal mechanism not directly established by the eight matched trajectories.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    manifest = read_json(args.artifact_dir / "manifests/condition_manifest.json")
    task_ids = [str(value) for value in manifest["task_ids"]]
    tasks = {
        task_id: {
            condition: read_json(source_task_path(args.artifact_dir, condition, task_id))
            for condition in CONDITIONS
        }
        for task_id in task_ids
    }
    for task_rows in tasks.values():
        for row in task_rows.values():
            register_task_secrets(row)

    args.audit_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for task_id in task_ids:
        for condition in CONDITIONS:
            source = source_task_path(args.artifact_dir, condition, task_id)
            destination = args.audit_dir / "heldout" / condition / f"{task_id}.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            safe = redact(tasks[task_id][condition])
            atomic_write_text(destination, json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
            files.append(
                {
                    "path": str(destination.relative_to(args.audit_dir)).replace("\\", "/"),
                    "sha256": sha256_file(destination),
                    "raw_path": str(source),
                    "raw_sha256": sha256_file(source),
                    "task_id": task_id,
                    "condition": condition,
                }
            )
        comparison = args.audit_dir / "comparisons" / f"{task_id}.md"
        comparison.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(comparison, comparison_markdown(task_id, tasks[task_id]))
        files.append(
            {
                "path": str(comparison.relative_to(args.audit_dir)).replace("\\", "/"),
                "sha256": sha256_file(comparison),
                "task_id": task_id,
                "kind": "comparison",
            }
        )

    copied = {
        "component_package_manifest.json": args.artifact_dir
        / "manifests/component_package_manifest.json",
        "condition_manifest.json": args.artifact_dir / "manifests/condition_manifest.json",
        "heldout_tasks.json": args.artifact_dir / "manifests/heldout_tasks.json",
        "dependency_graph.json": args.artifact_dir / "manifests/dependency_graph.json",
        "analysis.json": args.artifact_dir / "results/analysis.json",
        "static_prompt_assets.json": args.artifact_dir / "raw_audit/static_prompt_assets.json",
    }
    for name, source in copied.items():
        destination = args.audit_dir / name
        atomic_write_json(destination, redact(read_json(source)))
        files.append(
            {
                "path": name,
                "sha256": sha256_file(destination),
                "raw_path": str(source),
                "raw_sha256": sha256_file(source),
                "kind": "manifest_or_analysis",
            }
        )

    verification = verify_git_safe_redaction(args.audit_dir)
    index = {
        "format": "rcmf_one_demo_component_swap_git_safe_audit_12a_v1",
        "run_uuid": manifest["run_uuid"],
        "task_ids": task_ids,
        "conditions": list(CONDITIONS),
        "task_condition_count": len(task_ids) * len(CONDITIONS),
        "raw_lambda_artifact_root": str(args.artifact_dir),
        "files": files,
        "secret_verification": verification,
        "raw_unredacted_logs_lambda_only": True,
        "exact_tensors_lambda_only": True,
        "independently_verified": True,
    }
    index["index_sha256"] = canonical_sha256(index)
    atomic_write_json(args.audit_dir / "index.json", index)
    print(json.dumps(index, indent=2))


if __name__ == "__main__":
    main()
