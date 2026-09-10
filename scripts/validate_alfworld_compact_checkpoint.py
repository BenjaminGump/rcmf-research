from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path

import torch

from rcmf.benchmarks.alfworld.compact_rcmf import (
    CompactMemoryContribution,
    ReversibleCompactField,
)
from rcmf.benchmarks.alfworld.task_manifest import canonical_sha256
from rcmf.benchmarks.alfworld.training import (
    deployment_memory_query,
    load_deployment_checkpoint,
    load_ledger,
    sha256_file,
)


def _max_abs(first: torch.Tensor, second: torch.Tensor) -> float:
    return float((first.to(torch.float64) - second.to(torch.float64)).abs().max())


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a complete ALFWorld compact RCMF checkpoint")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--contribution-audit", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--engineering-limit", type=int, default=0)
    args = parser.parse_args()
    if args.engineering_limit < 0:
        raise ValueError("--engineering-limit must be zero or positive")
    checkpoint_path = Path(args.checkpoint).resolve(strict=True)
    audit_path = Path(args.contribution_audit).resolve(strict=True)
    ledger_path = Path(args.ledger).resolve(strict=True)
    full_ledger = load_ledger(ledger_path)
    ledger = (
        full_ledger[: args.engineering_limit]
        if args.engineering_limit
        else full_ledger
    )
    deployment = load_deployment_checkpoint(checkpoint_path, device=torch.device("cpu"))
    audit = torch.load(audit_path, map_location="cpu", weights_only=False)
    if audit.get("format") != "alfworld_compact_rcmf_contribution_audit_v1":
        raise ValueError("ALFWorld contribution audit format differs")
    ids = list(audit["contribution_ids"])
    parent_ids = list(audit["contribution_parent_ids"])
    if ids != [row["memory_id"] for row in ledger]:
        raise ValueError("checkpoint contribution IDs do not close over the complete ledger")
    if parent_ids != [row["parent_id"] for row in ledger]:
        raise ValueError("checkpoint contribution parents do not close over the complete ledger")
    count = len(ids)
    if deployment["compiled_memory_count"] != count:
        raise ValueError("fixed deployment field memory count differs from audit closure")
    tensors = {
        name: audit[name]
        for name in (
            "contribution_keys",
            "contribution_values",
            "contribution_mu",
            "contribution_rho",
            "field_A",
            "field_B",
        )
    }
    if any(not torch.isfinite(tensor).all() for tensor in tensors.values()):
        raise ValueError("checkpoint audit contains a non-finite tensor")
    config = deployment["config"]
    field = ReversibleCompactField(
        key_dim=int(config["key_dim"]),
        program_dim=int(config["program_dim"]),
    )
    contributions = []
    for index, memory_id in enumerate(ids):
        contribution = CompactMemoryContribution(
            memory_id=str(memory_id),
            parent_id=str(parent_ids[index]),
            key=tensors["contribution_keys"][index],
            value=tensors["contribution_values"][index],
            mu=float(tensors["contribution_mu"][index]),
            rho=float(tensors["contribution_rho"][index]),
        )
        field.add(contribution)
        contributions.append(contribution)
    rebuild_a_max_abs = _max_abs(field.A, tensors["field_A"])
    rebuild_b_max_abs = _max_abs(field.B, tensors["field_B"])
    deployment_a_max_abs = _max_abs(deployment["field_A"], tensors["field_A"])
    deployment_b_max_abs = _max_abs(deployment["field_B"], tensors["field_B"])
    baseline_a, baseline_b = field.A.clone(), field.B.clone()
    removed = field.remove(ids[0])
    field.restore(removed)
    restore_max_abs = max(_max_abs(field.A, baseline_a), _max_abs(field.B, baseline_b))
    reverse_a, reverse_b = field.rebuild(reversed(ids))
    permutation_max_abs = max(_max_abs(reverse_a, baseline_a), _max_abs(reverse_b, baseline_b))
    if max(
        rebuild_a_max_abs,
        rebuild_b_max_abs,
        deployment_a_max_abs,
        deployment_b_max_abs,
        restore_max_abs,
    ) > 1e-12 or permutation_max_abs > 1e-10:
        raise RuntimeError("ALFWorld compact field closure/reversibility audit failed")
    query = deployment_memory_query(
        deployment,
        task_instruction=str(ledger[0]["goal"]),
        device=torch.device("cpu"),
    )
    memory_z = query(str(ledger[0]["pre_action_state"]), [])
    injected = deployment["modules"]["injector"](memory_z)
    if tuple(memory_z.shape) != (1, int(config["program_dim"])):
        raise RuntimeError("fixed field read shape differs")
    if tuple(injected.shape) != (
        1,
        int(config["injection_tokens"]),
        int(config["model_dim"]),
    ) or not torch.isfinite(injected).all() or float(injected.abs().max()) == 0.0:
        raise RuntimeError("fixed reader injection is absent, malformed, or non-finite")
    tree = ast.parse(inspect.getsource(deployment_memory_query))
    runtime_names = {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    prohibited_runtime_names = {
        "contribution_ids",
        "contribution_keys",
        "contribution_values",
        "topk",
        "faiss",
        "nearest",
    }
    if runtime_names.intersection(prohibited_runtime_names):
        raise RuntimeError("deployment memory query references prohibited per-memory retrieval state")
    metadata = deployment["metadata"]
    expected_engineering_limit = args.engineering_limit or None
    if (
        int(metadata.get("full_ledger_count", -1)) != len(full_ledger)
        or int(metadata.get("trained_transition_count", -1)) != len(ledger)
        or metadata.get("engineering_limit") != expected_engineering_limit
    ):
        raise ValueError("checkpoint engineering/full-ledger closure differs")
    checks = {
        "complete_ledger_closure": count == len(ledger),
        "all_contributions_finite": True,
        "independent_contribution_rows": len(ids) == len(set(ids)),
        "add_remove_restore_max_abs": restore_max_abs,
        "permutation_max_abs": permutation_max_abs,
        "field_shape": {key: list(value) for key, value in field.field_shape.items()},
        "read_shape": list(memory_z.shape),
        "injection_shape": list(injected.shape),
        "injection_max_abs": float(injected.abs().max()),
        "runtime_retrieval": False,
        "raw_memory_in_prompt": False,
        "deployment_contains_per_memory_state": False,
        "qwen_frozen": bool(metadata.get("qwen_frozen")),
        "checkpoint_policy": config["checkpoint_policy"],
    }
    if not checks["qwen_frozen"] or checks["checkpoint_policy"] != "terminal_completed_epoch":
        raise RuntimeError("frozen-Qwen or terminal-checkpoint invariant differs")
    result = {
        "schema_version": "alfworld_compact_rcmf_checkpoint_validation_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint": {"path": str(checkpoint_path), "sha256": sha256_file(checkpoint_path)},
        "contribution_audit": {"path": str(audit_path), "sha256": sha256_file(audit_path)},
        "ledger": {"path": str(ledger_path), "sha256": sha256_file(ledger_path)},
        "memory_count": count,
        "full_ledger_count": len(full_ledger),
        "engineering_limit": expected_engineering_limit,
        "checks": checks,
        "field_closure_max_abs": {
            "rebuilt_A": rebuild_a_max_abs,
            "rebuilt_B": rebuild_b_max_abs,
            "deployment_A": deployment_a_max_abs,
            "deployment_B": deployment_b_max_abs,
        },
        "passed": True,
    }
    result["canonical_sha256"] = canonical_sha256(result)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
