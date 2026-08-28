"""Export Git-safe reconstructible EXP-032A trajectory audits and result records."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
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
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
FORMAT = "rcmf_onpolicy_trajectory_detailed_audit_10a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--audit-root", type=Path, default=Path("research/audits") / RUN_UUID
    )
    parser.add_argument(
        "--result-root",
        type=Path,
        default=Path(
            "research/results/exp032a_rcmf_onpolicy_trajectory_distillation"
        ),
    )
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _task_roots(artifact_dir: Path) -> list[tuple[str, str, Path]]:
    roots = []
    for condition in ("T0", "T1", "T2"):
        root = artifact_dir / "rollouts/conditions" / condition / "task_results"
        if root.exists():
            roots.append(("rollouts", condition, root))
    heldout = artifact_dir / "heldout"
    if heldout.exists():
        for candidate in sorted(path for path in heldout.iterdir() if path.is_dir()):
            conditions_root = candidate / "conditions"
            if not conditions_root.exists():
                continue
            for condition_root in sorted(path for path in conditions_root.iterdir() if path.is_dir()):
                root = condition_root / "task_results"
                if root.exists():
                    roots.append(
                        (
                            f"heldout/{candidate.name}",
                            condition_root.name,
                            root,
                        )
                    )
    for condition in ("N1", "N2"):
        root = artifact_dir / "first37/conditions" / condition / "task_results"
        if root.exists():
            roots.append(("first37", condition, root))
    return roots


def _register_task_secrets(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            step["exact_executed_code"],
            step["complete_environment_observation"],
        )
        for turn in step["complete_trajectory_so_far"]:
            register_sensitive_observation(
                turn.get("response", ""), turn.get("observation", "")
            )


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
    *,
    task_path: Path,
    task: Mapping[str, Any],
    step: Mapping[str, Any],
    tensor_key: str,
    scope: str,
) -> dict[str, Any]:
    row = first37_record(task_path, task, step, tensor_key, [])
    row["format"] = FORMAT
    row["audit_scope"] = scope
    row["trajectory_teacher_condition"] = task.get("condition")
    row["field"]["top_memory_contributions"] = {
        "status": "not_computed_runtime_prohibited",
        "not_used_by_model_or_field_read": True,
        "ranking": [],
    }
    return strict_redact(row)


def _comparison_markdown(
    *,
    scope: str,
    task_id: str,
    tasks: Mapping[str, Mapping[str, Any]],
) -> str:
    conditions = sorted(tasks)
    pairs = list(zip(conditions, conditions[1:]))
    divergences = {
        f"{left}_vs_{right}": first_divergence(tasks[left], tasks[right])
        for left, right in pairs
    }
    lines = [
        f"# EXP-032A trajectory comparison: {scope}/{task_id}",
        "",
        "Git-safe materialization. Credentials and JWTs use typed SHA256 "
        "placeholders; exact unredacted logs remain on Lambda.",
        "",
        "## Outcomes",
        "",
    ]
    for condition in conditions:
        task = tasks[condition]
        lines.append(
            f"- {condition}: success={bool(task['success'])}, "
            f"steps={int(task['step_count'])}, "
            f"wall_seconds={float(task['wall_seconds']):.3f}"
        )
    lines.extend(
        [
            "",
            "## Divergences",
            "",
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
    selected.update(
        int(task["steps"][-1]["step_id"])
        for task in tasks.values()
        if task["steps"]
    )
    for step_id in sorted(selected):
        lines.extend([f"## Materialized Step {step_id}", ""])
        for condition in conditions:
            task = tasks[condition]
            step = next(
                (row for row in task["steps"] if int(row["step_id"]) == step_id),
                None,
            )
            lines.extend([f"### {condition}", ""])
            if step is None:
                lines.extend(["Condition terminated before this step.", ""])
            else:
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
            "- **VERIFIED:** outcomes, prompts, actions, observations, and first "
            "divergences come from immutable atomic task records.",
            "- **INFERENCE:** correct-versus-shuffle differences are consistent "
            "with whole-bank memory-specific effects.",
            "- **UNVERIFIED:** causes beyond the recorded first causal divergence.",
            "",
        ]
    )
    return "\n".join(lines)


def _copy_json_if_exists(source: Path, destination: Path) -> None:
    if source.exists():
        atomic_json(destination, strict_redact(_json(source)))


def export(
    artifact_dir: Path, audit_root: Path, result_root: Path
) -> dict[str, Any]:
    if audit_root.exists() or result_root.exists():
        raise FileExistsError("Refusing to overwrite an EXP-032A export")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-032A export temporary root")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)

    roots = _task_roots(artifact_dir)
    if not roots:
        raise ValueError("No EXP-032A task results found")
    tasks_by_scope: dict[str, dict[str, dict[str, Any]]] = {}
    source_paths: dict[tuple[str, str, str], Path] = {}
    for scope, condition, root in roots:
        for path in sorted(root.glob("*.json")):
            task = _json(path)
            _register_task_secrets(task)
            task_id = str(task["task_id"])
            tasks_by_scope.setdefault(scope, {}).setdefault(task_id, {})[
                condition
            ] = task
            source_paths[(scope, condition, task_id)] = path

    tensor_bundle: dict[str, Any] = {
        "format": "rcmf_onpolicy_trajectory_compact_field_tensors_10a_v1",
        "scopes": {},
    }
    per_task_rows = []
    for scope, tasks in sorted(tasks_by_scope.items()):
        tensor_bundle["scopes"][scope] = {}
        for task_id, conditions in sorted(tasks.items()):
            for condition, task in sorted(conditions.items()):
                safe_steps = []
                for step in task["steps"]:
                    key = f"{scope}:{condition}:{task_id}:{int(step['step_id'])}"
                    tensor_bundle["scopes"][scope][key] = _tensor_payload(step)
                    safe_steps.append(
                        _safe_step(
                            task_path=source_paths[(scope, condition, task_id)],
                            task=task,
                            step=step,
                            tensor_key=key,
                            scope=scope,
                        )
                    )
                atomic_jsonl(
                    audit_tmp / scope / condition / f"{task_id}.jsonl",
                    safe_steps,
                )
                per_task_rows.append(
                    strict_redact(
                        {
                            "scope": scope,
                            "task_id": task_id,
                            "condition": condition,
                            "success": bool(task["success"]),
                            "step_count": int(task["step_count"]),
                            "usage": task["usage"],
                            "counts": task["counts"],
                            "wall_seconds": float(task["wall_seconds"]),
                            "raw_task_path": str(
                                source_paths[(scope, condition, task_id)]
                            ),
                            "raw_task_sha256": sha256_file(
                                source_paths[(scope, condition, task_id)]
                            ),
                        }
                    )
                )
            comparison = _comparison_markdown(
                scope=scope, task_id=task_id, tasks=conditions
            )
            path = audit_tmp / "comparisons" / f"{scope.replace('/', '_')}_{task_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(comparison, encoding="utf-8")

    raw_assets = _json(artifact_dir / "raw_audit/static_prompt_assets.json")
    atomic_json(audit_tmp / "static_prompt_assets.json", strict_redact(raw_assets))
    atomic_torch(audit_tmp / "field_tensors/query_and_slots.pt", tensor_bundle)

    attempts = _jsonl(artifact_dir / "attempts.jsonl")
    atomic_jsonl(
        result_tmp / "attempts.jsonl", [strict_redact(row) for row in attempts]
    )
    atomic_jsonl(result_tmp / "per_task.jsonl", per_task_rows)
    for name, source in (
        ("rollout_manifest.json", artifact_dir / "rollouts/rollout_manifest.json"),
        (
            "trajectory_union_manifest.json",
            artifact_dir / "trajectory_union/trajectory_union_manifest.json",
        ),
        (
            "reader_training.json",
            artifact_dir / "reader_training/epoch_02/summary.json",
        ),
        (
            "writer_training.json",
            artifact_dir / "writer_training/epoch_01/summary.json",
        ),
        (
            "heldout_selection.json",
            artifact_dir / "heldout/candidate_selection.json",
        ),
        ("first37_summary.json", artifact_dir / "first37/final_summary.json"),
        ("complexity.json", artifact_dir / "first37/instant_memory_recompilation.json"),
    ):
        _copy_json_if_exists(source, result_tmp / name)
    for name in ("training_rows.jsonl", "preference_rows.jsonl", "loop_negative_rows.jsonl"):
        source = artifact_dir / "trajectory_union" / name
        if source.exists():
            atomic_jsonl(
                result_tmp / name,
                [strict_redact(row) for row in _jsonl(source)],
            )

    open_attempts = sorted(
        {str(row["attempt_id"]) for row in attempts if row["event"] == "start"}
        - {str(row["attempt_id"]) for row in attempts if row["event"] == "end"}
    )
    summary = {
        "format": "rcmf_onpolicy_trajectory_result_summary_10a_v1",
        "run_uuid": RUN_UUID,
        "global_seed": 25101,
        "scope_counts": {
            scope: {
                "task_count": len(tasks),
                "condition_count": len(
                    {condition for values in tasks.values() for condition in values}
                ),
                "step_count": sum(
                    len(task["steps"])
                    for values in tasks.values()
                    for task in values.values()
                ),
            }
            for scope, tasks in tasks_by_scope.items()
        },
        "attempt_count": len({str(row["attempt_id"]) for row in attempts}),
        "open_attempt_ids": open_attempts,
        "artifact_root": str(artifact_dir),
        "artifact_bytes": sum(
            path.stat().st_size for path in artifact_dir.rglob("*") if path.is_file()
        ),
        "test_time_online_training": False,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "first37_exposed_development_only": True,
    }
    atomic_json(result_tmp / "summary.json", summary)

    verification = verify_git_safe_redaction(audit_tmp)
    strict_audit = strict_verify_tree(audit_tmp)
    strict_result = strict_verify_tree(result_tmp)
    if verification["registered_sensitive_observation_leak_count"] != 0:
        raise RuntimeError("EXP-032A audit redaction failed")
    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "scopes": {
            scope: {
                "task_count": len(tasks),
                "conditions": sorted(
                    {condition for values in tasks.values() for condition in values}
                ),
            }
            for scope, tasks in tasks_by_scope.items()
        },
        "step_tensor_bundle": "field_tensors/query_and_slots.pt",
        "step_tensor_bundle_sha256": sha256_file(
            audit_tmp / "field_tensors/query_and_slots.pt"
        ),
        "static_prompt_assets": "static_prompt_assets.json",
        "secret_verification": verification,
        "strict_audit_tree": strict_audit,
        "strict_result_tree": strict_result,
        "raw_lambda_artifact_root": str(artifact_dir),
        "independently_verified": not open_attempts,
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
