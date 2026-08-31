"""Export the Git-safe EXP-036A smoke-gate stop evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import sha256_file
from scripts.export_rcmf_appworld_testnormal_audit_13a import (
    register_task_secrets,
    safe_step,
    write_json,
    write_jsonl,
)
from scripts.export_rcmf_benefit_preserving_audit_9b import (
    strict_redact,
    strict_verify_tree,
)
from scripts.run_rcmf_q90_trajectory_common_9c import (
    deterministic_task_match,
    first_divergence,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def set_repr_order_only(left: str, right: str) -> bool:
    """Return true when matching labelled lines differ only by set ordering."""
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    if len(left_lines) != len(right_lines) or left == right:
        return False
    for left_line, right_line in zip(left_lines, right_lines, strict=True):
        if ": " not in left_line or ": " not in right_line:
            return False
        left_label, left_value = left_line.split(": ", 1)
        right_label, right_value = right_line.split(": ", 1)
        if left_label != right_label:
            return False
        try:
            left_set = ast.literal_eval(left_value)
            right_set = ast.literal_eval(right_value)
        except (SyntaxError, ValueError):
            return False
        if not isinstance(left_set, set) or not isinstance(right_set, set):
            return False
        if left_set != right_set:
            return False
    return True


def observation_sha(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def comparison(
    condition: str, left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    deterministic = deterministic_task_match(left, right)
    divergence = first_divergence(left, right)
    mechanism = None
    if divergence is not None and divergence.get("step_id") is not None:
        index = int(divergence["step_id"]) - 1
        left_observation = str(
            left["steps"][index]["complete_environment_observation"]
        )
        right_observation = str(
            right["steps"][index]["complete_environment_observation"]
        )
        mechanism = {
            "first_divergent_observation_left_sha256": observation_sha(
                left_observation
            ),
            "first_divergent_observation_right_sha256": observation_sha(
                right_observation
            ),
            "python_set_repr_order_only": set_repr_order_only(
                left_observation, right_observation
            ),
        }
    return strict_redact(
        {
            "condition": condition,
            "task_id": str(left["task_id"]),
            "determinism": deterministic,
            "first_divergence": divergence,
            "mechanism": mechanism,
            "left_success": bool(left["success"]),
            "right_success": bool(right["success"]),
            "left_step_count": int(left["step_count"]),
            "right_step_count": int(right["step_count"]),
        }
    )


def main() -> None:
    args = parse_args()
    run_manifest = read_json(args.artifact_dir / "run_manifest.json")
    run_uuid = str(run_manifest["run_uuid"])
    smoke_root = args.artifact_dir / "formal/smoke_v2"
    task_paths = sorted(smoke_root.glob("*/task_results/*.json"))
    if len(task_paths) != 15:
        raise RuntimeError(f"Expected 15 complete smoke rows, found {len(task_paths)}")

    args.audit_root.mkdir(parents=True, exist_ok=True)
    args.result_root.mkdir(parents=True, exist_ok=True)
    audit_rows = []
    row_index = []
    tasks: dict[tuple[str, str], dict[str, Any]] = {}
    for task_path in task_paths:
        task = read_json(task_path)
        register_task_secrets(task)
        condition = str(task["condition"])
        task_id = str(task["task_id"])
        output = args.audit_root / "smoke" / condition / f"{task_id}.jsonl"
        safe_rows = [safe_step(task_path, task, step) for step in task["steps"]]
        write_jsonl(output, safe_rows)
        tasks[(condition, task_id)] = task
        row_index.append(
            {
                "condition": condition,
                "task_id": task_id,
                "status": str(task["status"]),
                "success": bool(task["success"]),
                "step_count": int(task["step_count"]),
                "raw_task_path": str(task_path.resolve()),
                "raw_task_sha256": sha256_file(task_path),
                "git_safe_trace": str(output),
            }
        )
        audit_rows.extend(safe_rows)

    conditions = ("B0", "BEST-C", "BEST-S", "FULL1D-C", "FULL1D-S")
    repeat_task_id = "3d9a636_1"
    comparisons = []
    for condition in conditions:
        comparisons.append(
            comparison(
                condition,
                tasks[(condition, repeat_task_id)],
                tasks[(condition + "-REPEAT", repeat_task_id)],
            )
        )
    write_jsonl(args.result_root / "smoke_determinism.jsonl", comparisons)
    write_jsonl(args.result_root / "smoke_rows.jsonl", row_index)

    manifest_paths = sorted((args.artifact_dir / "manifests").glob("*.json"))
    manifest_index = {
        path.name: {
            "lambda_path": str(path.resolve()),
            "sha256": sha256_file(path),
        }
        for path in manifest_paths
    }
    summary = {
        "format": "rcmf_appworld_testnormal_stopped_summary_13a_v1",
        "run_uuid": run_uuid,
        "status": "STOPPED_BEFORE_FORMAL",
        "stop_gate": "complete_path_smoke_determinism",
        "stop_reason": "AppWorld executed Python set repr order varied across fresh identical worlds",
        "formal_trajectory_count": 0,
        "formal_expected_trajectory_count": 840,
        "formal_metrics": "NOT_RUN",
        "efficiency": "NOT_RUN",
        "reversibility": "NOT_RUN",
        "smoke_trajectory_count": len(task_paths),
        "smoke_is_scientific_evidence": False,
        "determinism": comparisons,
        "model_or_component_changed": False,
        "evaluation_seed": 25101,
        "lambda_artifact_root": str(args.artifact_dir.resolve()),
        "manifests": manifest_index,
    }
    summary["summary_sha256"] = canonical_sha256(summary)
    write_json(args.result_root / "summary.json", summary)

    index = {
        "format": "rcmf_appworld_testnormal_stopped_audit_index_13a_v1",
        "run_uuid": run_uuid,
        "independently_verified_formal_result": False,
        "formal_status": "NOT_RUN",
        "smoke_gate_status": "FAILED_DETERMINISM",
        "git_safe_smoke_trace_count": len(row_index),
        "git_safe_smoke_step_count": len(audit_rows),
        "raw_lambda_artifact_root": str(args.artifact_dir.resolve()),
        "row_index": row_index,
        "comparisons": comparisons,
        "manifests": manifest_index,
        "typed_redaction": True,
        "hidden_chain_of_thought_collected": False,
    }
    index["index_sha256"] = canonical_sha256(index)
    write_json(args.audit_root / "index.json", index)
    audit_scan = strict_verify_tree(args.audit_root)
    result_scan = strict_verify_tree(args.result_root)
    write_json(
        args.result_root / "secret_scan.json",
        {"audit": audit_scan, "results": result_scan, "passed": True},
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
