"""Export Git-safe EXP-036B determinism success and runtime-gate stop evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (
    compare_complete_smoke_rows,
)
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


CONDITIONS = ("B0", "BEST-C", "BEST-S", "FULL1D-C", "FULL1D-S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def not_run_record(artifact: str) -> dict[str, Any]:
    return {
        "artifact": artifact,
        "reason": "conservative_complete_total_exceeds_approved_42_hours",
        "status": "NOT_RUN",
    }


def main() -> None:
    args = parse_args()
    run_manifest = read_json(args.artifact_dir / "run_manifest.json")
    run_uuid = str(run_manifest["run_uuid"])
    smoke_root = args.artifact_dir / "final_smoke"
    sources = [
        ("primary", path)
        for path in sorted((smoke_root / "primary/smoke_v2").glob("*/task_results/*.json"))
    ] + [
        ("repeat", path)
        for path in sorted((smoke_root / "repeat/smoke_v2").glob("*/task_results/*.json"))
    ]
    if len(sources) != 15:
        raise RuntimeError(f"Expected 15 complete EXP-036B smoke rows, found {len(sources)}")

    args.audit_root.mkdir(parents=True, exist_ok=True)
    args.result_root.mkdir(parents=True, exist_ok=True)
    tasks: dict[tuple[str, str, str], dict[str, Any]] = {}
    row_index, safe_steps = [], []
    for role, task_path in sources:
        task = read_json(task_path)
        register_task_secrets(task)
        condition, task_id = str(task["condition"]), str(task["task_id"])
        output = args.audit_root / "smoke" / role / condition / f"{task_id}.jsonl"
        rows = [safe_step(task_path, task, step) for step in task["steps"]]
        write_jsonl(output, rows)
        tasks[(role, condition, task_id)] = task
        row_index.append(
            {
                "process_role": role,
                "condition": condition,
                "task_id": task_id,
                "status": str(task["status"]),
                "success": bool(task["success"]),
                "step_count": int(task["step_count"]),
                "wall_seconds": float(task["wall_seconds"]),
                "raw_task_path": str(task_path.resolve()),
                "raw_task_sha256": sha256_file(task_path),
                "git_safe_trace": str(output),
            }
        )
        safe_steps.extend(rows)

    repeat_task_id = read_json(smoke_root / "summary.json")["task_ids"][0]
    comparisons = [
        {
            "condition": condition,
            "task_id": repeat_task_id,
            **compare_complete_smoke_rows(
                tasks[("primary", condition, repeat_task_id)],
                tasks[("repeat", condition, repeat_task_id)],
            ),
        }
        for condition in CONDITIONS
    ]
    if not all(row["passed"] for row in comparisons):
        raise RuntimeError("EXP-036B exported smoke comparison differs")
    write_jsonl(args.result_root / "smoke_determinism.jsonl", comparisons)
    write_jsonl(args.result_root / "smoke_rows.jsonl", row_index)

    attempts = [
        strict_redact(row)
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    ]
    write_jsonl(args.result_root / "attempts.jsonl", attempts)
    attempt_ids = sorted({str(row["attempt_id"]) for row in attempts})
    ended = {
        str(row["attempt_id"]) for row in attempts if row.get("event") == "end"
    }
    failed = sorted(
        str(row["attempt_id"])
        for row in attempts
        if row.get("event") == "end" and int(row.get("exit_code", 0)) != 0
    )

    manifest_copies = {
        "condition_manifest.json": "condition_manifest.json",
        "package_manifest.json": "frozen_model_manifest.json",
        "test_normal_manifest.json": "test_normal_manifest.json",
        "formal_manifest.json": "formal_manifest.json",
        "determinism_mode.json": "determinism_mode.json",
        "leakage_audit.json": "leakage_audit.json",
    }
    for source_name, output_name in manifest_copies.items():
        write_json(
            args.result_root / output_name,
            strict_redact(read_json(args.artifact_dir / "manifests" / source_name)),
        )
    for source_name in ("determinism_root_cause.json",):
        source = args.result_root / source_name
        if not source.exists():
            raise FileNotFoundError(source)

    preflight = read_json(args.artifact_dir / "preflight/runtime_preflight.json")
    write_json(args.result_root / "runtime_preflight.json", preflight)
    write_json(args.result_root / "smoke_summary.json", read_json(smoke_root / "summary.json"))
    write_jsonl(
        args.result_root / "per_task.jsonl",
        [
            {
                **not_run_record("formal_per_task_results"),
                "completed_rows": 0,
                "expected_rows": 840,
            }
        ],
    )
    for name in (
        "paired_analysis",
        "trajectory_metrics",
        "formal_efficiency",
        "scaling_results",
        "compilation_results",
        "ttft_results",
        "serving_state_results",
        "reversibility_results",
        "paper_table_values",
    ):
        write_json(args.result_root / f"{name}.json", not_run_record(name))
    (args.result_root / "paper_table_values.tex").write_text(
        "% EXP-036B stopped at the 42-hour runtime gate before formal evaluation.\n"
        "\\newcommand{\\EXPThirtySixBBare}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBBest}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBBestShuffle}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBFullOneDemo}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBFullOneDemoShuffle}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBScaling}{NOT\\_RUN}\n"
        "\\newcommand{\\EXPThirtySixBReversibility}{NOT\\_RUN}\n",
        encoding="utf-8",
    )
    (args.result_root / "claims_boundary.md").write_text(
        "# EXP-036B Claims Boundary\n\n"
        "EXP-036B validated deterministic AppWorld observation rendering with "
        "process-level hash seeding, then stopped at the preregistered runtime "
        "gate before formal Test-Normal generation.\n\n"
        "1. The full Test-Normal manifest was frozen before EXP-036B generation.\n"
        "2. A Test-Normal subset had been used in earlier exploratory development.\n"
        "3. Set canonicalization was not enabled; model-visible observations are exact raw observations.\n"
        "4. Raw world state and evaluator behavior were unchanged.\n"
        "5. BEST remained the predeclared primary method.\n"
        "6. FULL1D remained the predeclared secondary configuration.\n"
        "7. Q90 was not part of the primary method.\n"
        "8. The final AppWorld prompt uses one complete demonstration.\n"
        "9. Active compiled field size is constant in N, while archival storage is linear; scaling was NOT_RUN here.\n"
        "10. ALFWorld, WebShop, TTR, ReMe, Experience-LoRA, behavioral deletion, and unsupported ablations remain NOT_RUN.\n",
        encoding="utf-8",
    )

    manifest_index = {
        path.name: {
            "lambda_path": str(path.resolve()),
            "sha256": sha256_file(path),
        }
        for path in sorted((args.artifact_dir / "manifests").glob("*.json"))
    }
    start_times = [
        str(row["timestamp_utc"]) for row in attempts if row.get("event") == "start"
    ]
    end_times = [
        str(row["timestamp_utc"]) for row in attempts if row.get("event") == "end"
    ]
    summary = {
        "format": "rcmf_appworld_testnormal_stopped_summary_13b_v1",
        "run_uuid": run_uuid,
        "status": "STOPPED_BEFORE_FORMAL",
        "stop_gate": "runtime_preflight_42_hour_cap",
        "stop_reason": "conservative complete total exceeds approved 42 hours",
        "formal_trajectory_count": 0,
        "formal_expected_trajectory_count": 840,
        "formal_metrics": "NOT_RUN",
        "efficiency": "NOT_RUN",
        "reversibility": "NOT_RUN",
        "determinism_mode": "hash_seed_only",
        "canonicalizer": "disabled",
        "smoke_trajectory_count": 15,
        "smoke_is_scientific_evidence": False,
        "smoke_determinism_passed": True,
        "attempt_count": len(attempt_ids),
        "attempt_ids": attempt_ids,
        "failed_attempt_ids": failed,
        "open_attempt_ids": sorted(set(attempt_ids) - ended),
        "expected_total_wall_hours": float(preflight["expected_total_wall_hours"]),
        "conservative_total_wall_hours": float(
            preflight["conservative_total_wall_hours"]
        ),
        "approved_wall_hours": float(preflight["approved_wall_hours"]),
        "run_start_timestamp_utc": min(start_times),
        "run_end_timestamp_utc": max(end_times),
        "model_or_component_changed": False,
        "evaluation_seed": 25101,
        "lambda_artifact_root": str(args.artifact_dir.resolve()),
        "manifests": manifest_index,
    }
    summary["summary_sha256"] = canonical_sha256(summary)
    write_json(args.result_root / "summary.json", summary)

    index = {
        "format": "rcmf_appworld_testnormal_stopped_audit_index_13b_v1",
        "run_uuid": run_uuid,
        "independently_verified_formal_result": False,
        "formal_status": "NOT_RUN",
        "smoke_gate_status": "PASSED_DETERMINISM",
        "runtime_gate_status": "STOPPED_OVER_42_HOURS",
        "git_safe_smoke_trace_count": len(row_index),
        "git_safe_smoke_step_count": len(safe_steps),
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
