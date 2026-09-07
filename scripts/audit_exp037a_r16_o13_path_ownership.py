from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import torch

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.reproducible_config_14b import arm_root
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _heldout_query_overrides,
    _resolved_prompt_dependent_input,
)
from rcmf.training.rcmf_joint_full_bank_9a import tensor_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--arm", choices=("3d", "1d"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(
    overrides: Mapping[str, tuple[torch.Tensor, torch.Tensor]]
) -> dict[str, dict[str, Any]]:
    return {
        task_id: {
            "state_shape": list(state.shape),
            "state_dtype": str(state.dtype),
            "state_sha256": tensor_sha256(state),
            "state_finite": bool(torch.isfinite(state).all()),
            "query_shape": list(query.shape),
            "query_dtype": str(query.dtype),
            "query_sha256": tensor_sha256(query),
            "query_finite": bool(torch.isfinite(query).all()),
        }
        for task_id, (state, query) in sorted(overrides.items())
    }


def main() -> None:
    args = _args()
    run_root = args.run_root.resolve(strict=True)
    target = arm_root(run_root, args.arm)
    data = _json(target / "data/full_bank_data_manifest.json")
    task_ids = [str(value) for value in data["heldout_task_ids"]]
    if len(task_ids) != 8 or len(set(task_ids)) != 8:
        raise ValueError("Expected exactly eight unique heldout task IDs")

    state_cache = _resolved_prompt_dependent_input(
        run_root, args.arm, "state_cache"
    )
    cache_sha_before = sha256_file(state_cache)
    first = _identity(_heldout_query_overrides(target, state_cache, task_ids))
    second = _identity(_heldout_query_overrides(target, state_cache, task_ids))
    cache_sha_after = sha256_file(state_cache)
    checks = {
        "heldout_task_count": len(task_ids) == 8,
        "all_tasks_mapped": set(first) == set(task_ids),
        "all_tensors_finite": all(
            row["state_finite"] and row["query_finite"]
            for row in first.values()
        ),
        "repeat_identity_exact": first == second,
        "state_cache_unchanged": cache_sha_before == cache_sha_after,
        "local_o00_cache_not_required": state_cache
        != (target / "representation_cache/multiview/state_multiview.pt").resolve(
            strict=False
        )
        or (target / "representation_cache/multiview/state_multiview.pt").is_file(),
    }
    result = {
        "format": "exp037a_r16_o13_no_generation_path_diagnostic_v1",
        "run_root": str(run_root),
        "arm": args.arm,
        "task_ids": task_ids,
        "state_cache": {
            "path": str(state_cache),
            "sha256_before": cache_sha_before,
            "sha256_after": cache_sha_after,
            "opened_for_writing": False,
        },
        "overrides": first,
        "checks": checks,
        "qwen_generation_count": 0,
        "appworld_trajectory_count": 0,
        "backward_count": 0,
        "optimizer_step_count": 0,
        "passed": all(checks.values()),
    }
    atomic_write_json(args.output, result)
    if not result["passed"]:
        raise RuntimeError(f"O13 path diagnostic failed: {checks}")


if __name__ == "__main__":
    main()

