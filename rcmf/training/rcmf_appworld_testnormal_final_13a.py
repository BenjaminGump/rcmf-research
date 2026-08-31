"""Frozen identities and paired analysis helpers for EXP-036A."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import random
from typing import Any

import torch
from torch import Tensor

from rcmf.training.state_conditioned_program_7d import canonical_sha256


GLOBAL_SEED = 25101
CONDITIONS = ("B0", "BEST-C", "BEST-S", "FULL1D-C", "FULL1D-S")
CONDITION_NAMES = {
    "B0": "one_demo_bare_zero_memory",
    "BEST-C": "primary_best_correct_499_memory_field",
    "BEST-S": "primary_best_key_payload_shuffle_499_memory_field",
    "FULL1D-C": "secondary_full1d_correct_499_memory_field",
    "FULL1D-S": "secondary_full1d_key_payload_shuffle_499_memory_field",
}
PAIRED_COMPARISONS = (
    ("BEST-C", "B0"),
    ("BEST-C", "BEST-S"),
    ("FULL1D-C", "B0"),
    ("FULL1D-C", "FULL1D-S"),
    ("BEST-C", "FULL1D-C"),
)


def condition_parts(condition: str) -> tuple[str | None, str]:
    if condition == "B0":
        return None, "zero"
    package, binding = condition.split("-", maxsplit=1)
    if package not in {"BEST", "FULL1D"} or binding not in {"C", "S"}:
        raise ValueError(f"Unknown EXP-036A condition: {condition}")
    return package, "correct" if binding == "C" else "key_payload_shuffle"


def ordered_sha256(values: Sequence[str]) -> str:
    encoded = json.dumps(
        [str(value) for value in values], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tensor_sha256(value: Tensor) -> str:
    tensor = value.detach().cpu().contiguous()
    header = json.dumps(
        {"dtype": str(tensor.dtype), "shape": list(tensor.shape)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(header + b"\0" + tensor.numpy().tobytes()).hexdigest()


def tensor_identity(value: Tensor) -> dict[str, Any]:
    tensor = value.detach().cpu().to(torch.float32)
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "finite": bool(torch.isfinite(tensor).all().item()),
        "norm": float(tensor.norm().item()),
        "rms": float(tensor.square().mean().sqrt().item()),
        "sha256": tensor_sha256(value),
        "bytes": int(value.numel() * value.element_size()),
    }


def validate_field_payload(
    payload: Mapping[str, Any],
    *,
    expected_checkpoint_sha256: str,
    memory_count: int = 499,
) -> dict[str, Any]:
    required = ("A", "B", "shuffled_A", "shuffled_B")
    if any(name not in payload for name in required):
        raise ValueError("Deployment field is missing a required tensor")
    checks = {
        "memory_count": int(payload.get("memory_count", -1)) == memory_count,
        "memory_id_count": len(payload.get("memory_ids", [])) == memory_count,
        "memory_ids_unique": len(set(payload.get("memory_ids", []))) == memory_count,
        "checkpoint": str(payload.get("checkpoint_sha256"))
        == expected_checkpoint_sha256,
        "A_shape": tuple(payload["A"].shape) == (960, 8, 256),
        "B_shape": tuple(payload["B"].shape) == (8, 256),
        "shuffled_A_shape": tuple(payload["shuffled_A"].shape) == (960, 8, 256),
        "shuffled_B_shape": tuple(payload["shuffled_B"].shape) == (8, 256),
        "runtime_retrieval_disabled": not bool(
            payload.get("runtime_memory_retrieval", False)
        ),
    }
    tensors = {name: tensor_identity(payload[name]) for name in required}
    checks["finite"] = all(row["finite"] for row in tensors.values())
    if not all(checks.values()):
        raise ValueError(f"Deployment field identity differs: {checks}")
    return {
        "checks": checks,
        "memory_count": memory_count,
        "memory_ids": [str(value) for value in payload["memory_ids"]],
        "memory_ids_sha256": ordered_sha256(
            [str(value) for value in payload["memory_ids"]]
        ),
        "tensors": tensors,
        "active_field_bytes": sum(tensors[name]["bytes"] for name in ("A", "B")),
    }


def build_condition_manifest(
    *, run_uuid: str, task_ids: Sequence[str], package_manifest_sha256: str
) -> dict[str, Any]:
    rows = []
    for condition in CONDITIONS:
        package, binding = condition_parts(condition)
        for task_index, task_id in enumerate(task_ids):
            rows.append(
                {
                    "row_index": len(rows),
                    "task_index": task_index,
                    "task_id": str(task_id),
                    "condition": condition,
                    "condition_name": CONDITION_NAMES[condition],
                    "package": package,
                    "binding": binding,
                    "memory_count": 0 if condition == "B0" else 499,
                    "prompt_profile": "full_demo_first_only",
                    "runtime_memory_retrieval": False,
                    "runtime_per_memory_scoring": False,
                    "student_prompt_contains_raw_memory": False,
                }
            )
    if len(rows) != 840 or len({(row["task_id"], row["condition"]) for row in rows}) != 840:
        raise ValueError("EXP-036A requires exactly 840 unique task-condition rows")
    payload = {
        "format": "rcmf_appworld_testnormal_condition_manifest_13a_v1",
        "run_uuid": run_uuid,
        "global_seed": GLOBAL_SEED,
        "task_ids": [str(value) for value in task_ids],
        "task_count": len(task_ids),
        "task_list_sha256": ordered_sha256([str(value) for value in task_ids]),
        "conditions": list(CONDITIONS),
        "logical_condition_count": len(rows),
        "package_manifest_sha256": package_manifest_sha256,
        "frozen_before_generation": True,
        "test_outcomes_used": False,
        "rows": rows,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Cannot calculate a quantile from no values")
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def paired_bootstrap(
    left: Sequence[bool], right: Sequence[bool], *, replicates: int = 100_000
) -> dict[str, Any]:
    if len(left) != len(right) or not left:
        raise ValueError("Paired bootstrap requires equal nonempty inputs")
    rng = random.Random(GLOBAL_SEED)
    samples = []
    for _ in range(replicates):
        delta = sum(
            int(left[index]) - int(right[index])
            for index in (rng.randrange(len(left)) for _ in left)
        ) / len(left)
        samples.append(delta)
    return {
        "observed": (sum(left) - sum(right)) / len(left),
        "lower_95": quantile(samples, 0.025),
        "upper_95": quantile(samples, 0.975),
        "replicates": replicates,
        "analysis_seed": GLOBAL_SEED,
    }
