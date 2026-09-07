from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.reproducible_config_14b import arm_root
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _arm_config,
    _heldout_query_overrides,
    _resolved_prompt_dependent_input,
    _run_task_set,
)
from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-run-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--attempt-id", required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = _args()
    fixture_root = args.fixture_run_root.resolve(strict=True)
    diagnostic_root = args.diagnostic_root.resolve(strict=False)
    if diagnostic_root == fixture_root or fixture_root in diagnostic_root.parents:
        raise ValueError("Diagnostic output must be outside the sealed fixture root")
    if diagnostic_root.exists() and any(diagnostic_root.iterdir()):
        raise FileExistsError(
            f"Diagnostic output root is not empty: {diagnostic_root}"
        )
    diagnostic_root.mkdir(parents=True, exist_ok=True)

    arm_id = "1d"
    target = arm_root(fixture_root, arm_id)
    config_path = _arm_config(fixture_root, arm_id)
    config = load_config(config_path)
    prompt_profile = str(config.benchmark.prompt_profile)
    if prompt_profile != "full_demo_first_only":
        raise ValueError(f"Unexpected one-demo prompt profile: {prompt_profile}")

    data_manifest = target / "data/full_bank_data_manifest.json"
    data = _json(data_manifest)
    heldout_task_ids = [str(value) for value in data["heldout_task_ids"]]
    if len(heldout_task_ids) != 8 or len(set(heldout_task_ids)) != 8:
        raise ValueError("Expected exactly eight unique heldout task IDs")
    if args.task_id not in heldout_task_ids:
        raise ValueError(f"Task is not in the sealed heldout set: {args.task_id}")

    state_cache = _resolved_prompt_dependent_input(
        fixture_root, arm_id, "state_cache"
    )
    correct_field = (
        target
        / "heldout_validation/live_full_field/field_artifacts/epoch_02_correct.pt"
    )
    shuffled_field = (
        target
        / "heldout_validation/live_full_field/field_artifacts/"
        "epoch_02_key_payload_shuffle.pt"
    )
    checkpoint = target / "joint_training/checkpoints/epoch_02.pt"
    source_cache = target / "data/rcmf_source_cache.pt"
    fixtures = {
        "arm_config": config_path,
        "data_manifest": data_manifest,
        "state_cache": state_cache,
        "source_cache": source_cache,
        "correct_field": correct_field,
        "shuffled_field": shuffled_field,
        "checkpoint": checkpoint,
    }
    missing = [name for name, path in fixtures.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Required smoke fixtures are missing: {missing}")
    before = {name: sha256_file(path) for name, path in fixtures.items()}

    overrides = _heldout_query_overrides(target, state_cache, heldout_task_ids)
    if set(overrides) != set(heldout_task_ids):
        raise ValueError("Heldout query override population differs")

    os.environ["RCMF_PIPELINE_RUN_UUID"] = "diagnostic_exp037a_r16_o13_smoke"
    os.environ["RCMF_PIPELINE_CONFIG_SHA256"] = sha256_file(config_path)
    os.environ["RCMF_PIPELINE_CONTRACT_SHA256"] = "diagnostic_only_not_formal"
    started = time.perf_counter()
    summary = _run_task_set(
        run_root=fixture_root,
        arm_id=arm_id,
        task_ids=[args.task_id],
        output_root=diagnostic_root / "trajectory",
        condition_id="R16_E2_H3",
        condition_name="r16_epoch_2_state_query_shuffle_smoke",
        field_control="D3",
        prompt_profile=prompt_profile,
        correct_field=correct_field,
        shuffled_field=shuffled_field,
        checkpoint=checkpoint,
        provenance=data_manifest,
        memory_count=401,
        source_commit=args.source_commit,
        attempt_id=args.attempt_id,
        deployment_bundle=False,
        query_overrides={args.task_id: overrides[args.task_id]},
    )
    elapsed = time.perf_counter() - started
    task_result = (
        diagnostic_root
        / "trajectory/conditions/R16_E2_H3/task_results"
        / f"{args.task_id}.json"
    )
    row = _json(task_result)
    after = {name: sha256_file(path) for name, path in fixtures.items()}
    steps = list(row.get("steps", []))
    query_override_used = bool(steps) and all(
        bool(step.get("field", {}).get("state_query_shuffled")) for step in steps
    )
    checks = {
        "one_task_completed": summary.get("task_count") == 1
        and row.get("status") == "complete",
        "fresh_diagnostic_output": summary.get("reused_task_rows") == 0,
        "trajectory_executed": int(row.get("step_count", 0)) >= 1,
        "state_query_override_used": query_override_used,
        "one_demo_prompt": summary.get("prompt_profile")
        == "full_demo_first_only",
        "fixture_hashes_unchanged": before == after,
        "parent_state_cache_unchanged": before["state_cache"]
        == after["state_cache"],
    }
    result = {
        "format": "exp037a_r16_o13_integration_smoke_v1",
        "scientific_eligibility": "diagnostic_only_never_formal_input",
        "fixture_run_root": str(fixture_root),
        "diagnostic_root": str(diagnostic_root),
        "source_commit": args.source_commit,
        "arm": arm_id,
        "task_id": args.task_id,
        "heldout_task_ids": heldout_task_ids,
        "condition": "R16_E2_H3",
        "prompt_profile": prompt_profile,
        "fixture_paths": {name: str(path) for name, path in fixtures.items()},
        "fixture_sha256_before": before,
        "fixture_sha256_after": after,
        "summary_path": str(
            diagnostic_root / "trajectory/summaries/R16_E2_H3.json"
        ),
        "task_result_path": str(task_result),
        "task_success": bool(row.get("success")),
        "step_count": int(row.get("step_count", 0)),
        "terminal_error": row.get("terminal_error"),
        "counts": dict(row.get("counts", {})),
        "elapsed_seconds": elapsed,
        "appworld_full_trajectory_count": 1,
        "qwen_trajectory_count": 1,
        "backward_count": 0,
        "optimizer_step_count": 0,
        "checks": checks,
        "passed": all(checks.values()),
    }
    atomic_write_json(diagnostic_root / "integration_smoke_summary.json", result)
    if not result["passed"]:
        raise RuntimeError(f"O13 integration smoke failed: {checks}")


if __name__ == "__main__":
    main()
