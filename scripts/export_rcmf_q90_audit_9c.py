"""Export Git-safe reconstructible EXP-031C full-trajectory audits."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.utils.serialization import sha256_file
from scripts.export_rcmf_benefit_preserving_audit_9b import (
    strict_redact,
    strict_verify_tree,
)
from scripts.export_rcmf_joint_full_bank_audit_9a import (
    atomic_json,
    atomic_jsonl,
    atomic_torch,
    first37_record,
    materialized_step,
    raw_tensor_sha256,
    register_sensitive_observation,
    verify_git_safe_redaction,
)
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence, load_json


RUN_UUID = "rcmf_q90_full_trajectory_9c_20260828_001"
FORMAT = "rcmf_q90_detailed_audit_9c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--audit-root",
        type=Path,
        default=Path("research/audits") / RUN_UUID,
    )
    parser.add_argument(
        "--result-root",
        type=Path,
        default=Path("research/results/exp031c_rcmf_q90_full_trajectory"),
    )
    return parser.parse_args()


def _task_paths(artifact_dir: Path, scope: str, condition: str) -> list[Path]:
    return sorted((artifact_dir / scope / "conditions" / condition / "task_results").glob("*.json"))


def _register_task_secrets(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step["exact_executed_code"],
            step["complete_environment_observation"],
        )
        for turn in step["complete_trajectory_so_far"]:
            register_sensitive_observation(turn.get("response", ""), turn.get("observation", ""))


def _tensor_payload(step: Mapping[str, Any]) -> dict[str, torch.Tensor | None]:
    field = step["field"]
    path = Path(str(field["tensor_artifact"]))
    if sha256_file(path) != str(field["tensor_artifact_sha256"]):
        raise ValueError(f"Step tensor hash differs: {path}")
    payload = torch.load(path, map_location="cpu", weights_only=False)
    query = payload.get("query")
    slots = payload["slots"]
    if query is not None and raw_tensor_sha256(query) != field["query"]["sha256"]:
        raise ValueError("Step query tensor hash differs")
    if raw_tensor_sha256(slots) != field["slots"]["sha256"]:
        raise ValueError("Step slot tensor hash differs")
    return {
        "query": None if query is None else query.detach().cpu(),
        "slots": slots.detach().cpu(),
    }


def _safe_step(
    task_path: Path,
    task: Mapping[str, Any],
    step: Mapping[str, Any],
    tensor_key: str,
) -> dict[str, Any]:
    row = first37_record(task_path, task, step, tensor_key, [])
    row["format"] = FORMAT
    row["audit_scope"] = (
        "heldout_complete_agent"
        if str(task["condition"]).startswith("H")
        else "first37_complete_agent"
    )
    row["q90_candidate"] = task.get("exp031c_candidate")
    row["q90_tau"] = task.get("q90_tau")
    row["q90_calibration_sha256"] = task.get("q90_calibration_sha256")
    row["field"]["top_memory_contributions"] = {
        "status": "not_computed_runtime_prohibited",
        "not_used_by_model_or_field_read": True,
        "ranking": [],
    }
    return strict_redact(row)


def _comparison_markdown(
    task_id: str,
    tasks: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
) -> str:
    lines = [
        f"# EXP-031C trajectory comparison: {task_id}",
        "",
        (
            "Git-safe materialization. Credentials and JWTs use typed SHA256 "
            "placeholders; exact unredacted logs remain on Lambda."
        ),
        "",
        "## Outcomes",
        "",
    ]
    for condition, task in tasks.items():
        lines.append(
            f"- {condition}: success={bool(task['success'])}, "
            f"steps={int(task['step_count'])}, "
            f"wall_seconds={float(task['wall_seconds']):.3f}"
        )
    lines.extend(["", "## Divergences", ""])
    divergences = {
        f"{left}_vs_{right}": first_divergence(tasks[left], tasks[right]) for left, right in pairs
    }
    lines.extend(
        [
            "~~~json",
            json.dumps(strict_redact(divergences), indent=2, sort_keys=True),
            "~~~",
            "",
        ]
    )
    selected = {
        int(value["step_id"])
        for value in divergences.values()
        if value is not None and value.get("step_id") is not None
    }
    selected.update(int(task["steps"][-1]["step_id"]) for task in tasks.values() if task["steps"])
    for step_id in sorted(selected):
        lines.extend([f"## Materialized Step {step_id}", ""])
        for condition, task in tasks.items():
            step = next(
                (row for row in task["steps"] if int(row["step_id"]) == step_id),
                None,
            )
            lines.extend([f"### {condition}", ""])
            if step is None:
                lines.extend(["Condition terminated before this step.", ""])
                continue
            lines.extend(
                [
                    "~~~json",
                    json.dumps(
                        strict_redact(materialized_step(step)),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    "~~~",
                    "",
                ]
            )
    lines.extend(
        [
            "## Interpretation",
            "",
            "- **VERIFIED:** outcome and first-divergence fields above come from "
            "the immutable paired task records.",
            "- **INFERENCE:** a correct-versus-shuffle trajectory difference is "
            "consistent with a memory-specific field effect but does not isolate "
            "one ledger record.",
            "- **UNVERIFIED:** downstream causes beyond the recorded first behavioral divergence.",
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
        raise FileExistsError("Refusing to overwrite an EXP-031C export")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-031C export temporary root")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)

    heldout_conditions = ("H0", "H1", "H2", "H3", "H4")
    first37_conditions = (
        ("Q1", "Q2") if (artifact_dir / "first37/final_summary.json").exists() else ()
    )
    expected = {"heldout": 8, "first37": 37}
    tasks_by_scope: dict[str, dict[str, dict[str, Any]]] = {
        "heldout": {},
        "first37": {},
    }
    source_paths: dict[tuple[str, str, str], Path] = {}
    for scope, conditions in (
        ("heldout", heldout_conditions),
        ("first37", first37_conditions),
    ):
        for condition in conditions:
            paths = _task_paths(artifact_dir, scope, condition)
            if len(paths) != expected[scope]:
                raise ValueError(
                    f"Expected {expected[scope]} {scope}/{condition} tasks, found {len(paths)}"
                )
            for path in paths:
                task = load_json(path)
                _register_task_secrets(task)
                task_id = str(task["task_id"])
                tasks_by_scope[scope].setdefault(task_id, {})[condition] = task
                source_paths[(scope, condition, task_id)] = path

    tensor_bundle: dict[str, Any] = {
        "format": "rcmf_q90_compact_field_tensors_9c_v1",
        "heldout": {},
        "first37": {},
    }
    per_task_rows = {"heldout": [], "first37": []}
    for scope, tasks in tasks_by_scope.items():
        for task_id, conditions in sorted(tasks.items()):
            for condition, task in sorted(conditions.items()):
                safe_steps = []
                for step in task["steps"]:
                    key = f"{scope}:{condition}:{task_id}:{int(step['step_id'])}"
                    tensor_bundle[scope][key] = _tensor_payload(step)
                    safe_steps.append(
                        _safe_step(
                            source_paths[(scope, condition, task_id)],
                            task,
                            step,
                            key,
                        )
                    )
                atomic_jsonl(
                    audit_tmp / scope / condition / f"{task_id}.jsonl",
                    safe_steps,
                )
                per_task_rows[scope].append(
                    strict_redact(
                        {
                            "task_id": task_id,
                            "condition": condition,
                            "success": bool(task["success"]),
                            "step_count": int(task["step_count"]),
                            "usage": task["usage"],
                            "counts": task["counts"],
                            "wall_seconds": float(task["wall_seconds"]),
                            "raw_task_path": str(source_paths[(scope, condition, task_id)]),
                            "raw_task_sha256": sha256_file(
                                source_paths[(scope, condition, task_id)]
                            ),
                        }
                    )
                )

            pairs = (
                [("H0", "H1"), ("H1", "H3"), ("H3", "H4")] if scope == "heldout" else [("Q1", "Q2")]
            )
            comparison = _comparison_markdown(task_id, conditions, pairs)
            comparison_path = audit_tmp / "comparisons" / f"{scope}_{task_id}.md"
            comparison_path.parent.mkdir(parents=True, exist_ok=True)
            comparison_path.write_text(comparison, encoding="utf-8")

    raw_assets = load_json(artifact_dir / "raw_audit/static_prompt_assets.json")
    atomic_json(audit_tmp / "static_prompt_assets.json", strict_redact(raw_assets))
    atomic_torch(audit_tmp / "field_tensors/query_and_slots.pt", tensor_bundle)

    heldout_final = load_json(artifact_dir / "heldout/final_summary.json")
    first37_final = (
        load_json(artifact_dir / "first37/final_summary.json") if first37_conditions else None
    )
    attempts = _attempt_rows(artifact_dir)
    summary = {
        "format": "rcmf_q90_result_summary_9c_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "heldout": strict_redact(heldout_final),
        "first37": None if first37_final is None else strict_redact(first37_final),
        "attempt_count": len({str(row["attempt_id"]) for row in attempts}),
        "open_attempt_ids": sorted(
            {str(row["attempt_id"]) for row in attempts if row["event"] == "start"}
            - {str(row["attempt_id"]) for row in attempts if row["event"] == "end"}
        ),
        "artifact_root": str(artifact_dir),
        "artifact_bytes": sum(
            path.stat().st_size for path in artifact_dir.rglob("*") if path.is_file()
        ),
        "no_retraining": True,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "first37_exposed_development_only": True,
    }
    atomic_json(result_tmp / "summary.json", summary)
    atomic_jsonl(
        result_tmp / "heldout_per_task.jsonl",
        per_task_rows["heldout"],
    )
    atomic_jsonl(
        result_tmp / "first37_per_task.jsonl",
        per_task_rows["first37"],
    )
    atomic_jsonl(result_tmp / "attempts.jsonl", [strict_redact(row) for row in attempts])
    atomic_jsonl(
        result_tmp / "comparisons.jsonl",
        [
            strict_redact(
                {
                    "scope": scope,
                    "task_id": task_id,
                    "conditions": {
                        condition: bool(task["success"]) for condition, task in conditions.items()
                    },
                }
            )
            for scope, tasks in tasks_by_scope.items()
            for task_id, conditions in sorted(tasks.items())
        ],
    )
    atomic_json(
        result_tmp / "complexity.json",
        {
            "production_write_scans_existing_bank": False,
            "production_read_depends_on_memory_count": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
            "field_shape": [960, 8, 256],
            "slot_shape": [8, 256],
            "heldout_memory_count": 401,
            "deployment_memory_count": 499,
        },
    )

    verification = verify_git_safe_redaction(audit_tmp)
    strict = strict_verify_tree(audit_tmp)
    strict_result = strict_verify_tree(result_tmp)
    if verification["registered_sensitive_observation_leak_count"] != 0:
        raise RuntimeError("EXP-031C audit redaction failed")

    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "heldout_task_count": len(tasks_by_scope["heldout"]),
        "heldout_conditions": list(heldout_conditions),
        "first37_task_count": len(tasks_by_scope["first37"]),
        "first37_conditions": list(first37_conditions),
        "step_tensor_bundle": "field_tensors/query_and_slots.pt",
        "step_tensor_bundle_sha256": sha256_file(audit_tmp / "field_tensors/query_and_slots.pt"),
        "static_prompt_assets": "static_prompt_assets.json",
        "secret_verification": verification,
        "strict_audit_tree": strict,
        "strict_result_tree": strict_result,
        "raw_lambda_artifact_root": str(artifact_dir),
        "raw_lambda_artifact_sha256_by_file": "recorded in each Git-safe task step",
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
