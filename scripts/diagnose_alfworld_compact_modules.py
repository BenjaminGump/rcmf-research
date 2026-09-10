from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
import yaml

from rcmf.benchmarks.alfworld.compact_rcmf import (
    ReversibleCompactField,
    batch_hash_features,
    compile_contributions,
)
from rcmf.benchmarks.alfworld.task_manifest import canonical_sha256
from rcmf.benchmarks.alfworld.training import (
    checkpoint_payload,
    compile_field,
    create_modules,
    deployment_memory_query,
    load_ledger,
    set_seed,
    sha256_file,
)


def _gradient_status(module: torch.nn.Module) -> dict[str, Any]:
    parameters = list(module.parameters())
    gradients = [parameter.grad for parameter in parameters]
    finite = [gradient for gradient in gradients if gradient is not None]
    return {
        "parameter_count": sum(parameter.numel() for parameter in parameters),
        "parameters_with_gradient": len(finite),
        "all_gradients_finite": bool(finite)
        and all(bool(torch.isfinite(gradient).all()) for gradient in finite),
        "gradient_l1": sum(float(gradient.detach().abs().sum()) for gradient in finite),
    }


def _runtime_names() -> set[str]:
    tree = ast.parse(inspect.getsource(deployment_memory_query))
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


class _FrozenEmbeddingModel(torch.nn.Module):
    def __init__(self, vocabulary_size: int, model_dim: int) -> None:
        super().__init__()
        self.embedding = torch.nn.Embedding(vocabulary_size, model_dim)
        self.requires_grad_(False)

    def get_input_embeddings(self) -> torch.nn.Embedding:
        return self.embedding


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit ALFWorld compact RCMF module invariants")
    parser.add_argument("--config", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config_path = Path(args.config).resolve(strict=True)
    ledger_path = Path(args.ledger).resolve(strict=True)
    output_path = Path(args.output).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rows = load_ledger(ledger_path)
    set_seed(int(config["seed"]))
    device = torch.device("cpu")
    modules = create_modules(config)

    probe_rows = rows[:2]
    one_by_one = compile_contributions(
        modules["writer"],
        probe_rows,
        feature_dim=int(config["feature_dim"]),
        batch_size=1,
        device=device,
    )
    together = compile_contributions(
        modules["writer"],
        probe_rows,
        feature_dim=int(config["feature_dim"]),
        batch_size=2,
        device=device,
    )
    independent_max_abs = max(
        max(float((left.key - right.key).abs().max()), float((left.value - right.value).abs().max()))
        for left, right in zip(one_by_one, together, strict=True)
    )

    field, contributions = compile_field(
        rows=rows,
        writer=modules["writer"],
        config=config,
        device=device,
    )
    all_contributions_finite = all(
        bool(torch.isfinite(row.key).all())
        and bool(torch.isfinite(row.value).all())
        and torch.isfinite(torch.tensor([row.mu, row.rho])).all().item()
        for row in contributions
    )
    baseline_a, baseline_b = field.A.clone(), field.B.clone()
    removed = field.remove(contributions[0].memory_id)
    field.restore(removed)
    restore_max_abs = max(
        float((field.A - baseline_a).abs().max()),
        float((field.B - baseline_b).abs().max()),
    )
    reverse_a, reverse_b = field.rebuild(reversed(sorted(field.records)))
    permutation_max_abs = max(
        float((reverse_a - baseline_a).abs().max()),
        float((reverse_b - baseline_b).abs().max()),
    )
    empty_field = ReversibleCompactField(
        key_dim=int(config["key_dim"]),
        program_dim=int(config["program_dim"]),
    )
    field_shape_fixed = empty_field.field_shape == field.field_shape

    # Exercise the exact reader/injector path without loading Qwen.  The toy
    # embedding stands in only for the frozen input-embedding lookup; it is
    # explicitly frozen and must receive no gradient.
    frozen_embedding = _FrozenEmbeddingModel(32, int(config["model_dim"]))
    state = batch_hash_features(
        [str(row["state_text"]) for row in probe_rows],
        int(config["feature_dim"]),
    )
    fixed_a = field.A.to(torch.float32)
    fixed_b = field.B.to(torch.float32)
    query = modules["query_encoder"](state)
    field_value = F.normalize(fixed_b.unsqueeze(0) + query @ fixed_a, dim=-1)
    memory_z = modules["reader"](field_value)
    input_ids = torch.tensor([[1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]], dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    labels = torch.full_like(input_ids, -100)
    base_embeddings = frozen_embedding.get_input_embeddings()(input_ids).detach()
    prepared = modules["injector"].prepare_train_inputs(
        frozen_embedding,
        input_ids,
        attention_mask,
        labels,
        memory_z,
    )
    embedding_change_max_abs = float(
        (prepared.inputs["inputs_embeds"] - base_embeddings).detach().abs().max()
    )
    prepared.inputs["inputs_embeds"].square().mean().backward()
    gradients = {
        name: _gradient_status(module)
        for name, module in modules.items()
    }
    frozen_embedding_gradient_absent = all(
        parameter.grad is None for parameter in frozen_embedding.parameters()
    )
    intended_gradients_only = (
        gradients["writer"]["parameters_with_gradient"] == 0
        and all(
            gradients[name]["parameters_with_gradient"] > 0
            and gradients[name]["all_gradients_finite"]
            and gradients[name]["gradient_l1"] > 0
            for name in ("query_encoder", "reader", "injector")
        )
        and frozen_embedding_gradient_absent
    )

    deployment = checkpoint_payload(
        modules=modules,
        field=field,
        contributions=contributions,
        config=config,
        metadata={"diagnostic": True},
    )
    prohibited_checkpoint_keys = {
        "contribution_ids",
        "contribution_parent_ids",
        "contribution_keys",
        "contribution_values",
        "contribution_mu",
        "contribution_rho",
    }
    prohibited_runtime_names = {
        "contribution_ids",
        "contribution_keys",
        "contribution_values",
        "topk",
        "faiss",
        "nearest",
    }
    runtime_retrieval_absent = not bool(_runtime_names().intersection(prohibited_runtime_names))
    deployment_per_memory_state_absent = not bool(prohibited_checkpoint_keys.intersection(deployment))
    checks = {
        "all_ledger_rows_train": all(row["split"] == "train" for row in rows),
        "all_ledger_rows_official_expert": all(
            row["provenance"] == "OFFICIAL_EXPERT" for row in rows
        ),
        "all_contributions_finite": all_contributions_finite,
        "independent_compilation_max_abs": independent_max_abs,
        "independent_compilation_passed": independent_max_abs <= 1e-6,
        "add_remove_restore_max_abs": restore_max_abs,
        "add_remove_restore_passed": restore_max_abs <= 1e-12,
        "permutation_max_abs": permutation_max_abs,
        "permutation_passed": permutation_max_abs <= 1e-10,
        "fixed_field_shape": field_shape_fixed,
        "read_shape": list(field.read(torch.ones(int(config["key_dim"]))).shape),
        "embedding_change_max_abs": embedding_change_max_abs,
        "injection_changes_embedding_path": embedding_change_max_abs > 0,
        "gradients": gradients,
        "frozen_embedding_gradient_absent": frozen_embedding_gradient_absent,
        "intended_gradients_only": intended_gradients_only,
        "runtime_retrieval_absent": runtime_retrieval_absent,
        "deployment_per_memory_state_absent": deployment_per_memory_state_absent,
        "raw_memory_in_query_prompt": bool(config["raw_memory_in_query_prompt"]),
    }
    passed = (
        all_contributions_finite
        and checks["all_ledger_rows_train"]
        and checks["all_ledger_rows_official_expert"]
        and checks["independent_compilation_passed"]
        and checks["add_remove_restore_passed"]
        and checks["permutation_passed"]
        and field_shape_fixed
        and checks["read_shape"] == [int(config["program_dim"])]
        and checks["injection_changes_embedding_path"]
        and intended_gradients_only
        and runtime_retrieval_absent
        and deployment_per_memory_state_absent
        and not checks["raw_memory_in_query_prompt"]
    )
    result = {
        "schema_version": "alfworld_compact_rcmf_module_diagnostic_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "ledger": {
            "path": str(ledger_path),
            "sha256": sha256_file(ledger_path),
            "transition_count": len(rows),
            "trajectory_count": len({str(row["parent_id"]) for row in rows}),
        },
        "contribution_count": len(contributions),
        "field_shape": {name: list(shape) for name, shape in field.field_shape.items()},
        "checks": checks,
        "passed": passed,
    }
    result["canonical_sha256"] = canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    print(json.dumps(result, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
