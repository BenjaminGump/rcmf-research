from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.benchmarks.alfworld.compact_rcmf import (
    COMPACT_RCMF_VERSION,
    FixedFieldReader,
    IndependentTransitionWriter,
    ReversibleCompactField,
    StateQueryEncoder,
    batch_hash_features,
    compile_contributions,
    signed_hash_features,
)
from rcmf.injection.prefix import AdditiveTokenMemoryInjector


def load_ledger(path: str | Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or any(row.get("split") != "train" for row in rows):
        raise ValueError("ALFWorld training ledger must be non-empty and TRAIN-only")
    ids = [str(row["memory_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("ALFWorld training ledger contains duplicate memories")
    if any(row.get("provenance") != "OFFICIAL_EXPERT" for row in rows):
        raise ValueError("ALFWorld training ledger contains non-official provenance")
    return rows


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_modules(config: Mapping[str, Any]) -> dict[str, Any]:
    feature_dim = int(config["feature_dim"])
    key_dim = int(config["key_dim"])
    program_dim = int(config["program_dim"])
    return {
        "writer": IndependentTransitionWriter(
            feature_dim=feature_dim,
            key_dim=key_dim,
            program_dim=program_dim,
            hidden_dim=int(config["writer_hidden_dim"]),
        ),
        "query_encoder": StateQueryEncoder(feature_dim=feature_dim, key_dim=key_dim),
        "reader": FixedFieldReader(program_dim=program_dim),
        "injector": AdditiveTokenMemoryInjector(
            program_dim=program_dim,
            model_dim=int(config["model_dim"]),
            num_tokens=int(config["injection_tokens"]),
            position="last_user_k",
            initial_scale=float(config["initial_injection_scale"]),
        ),
    }


def _epoch_order(size: int, *, seed: int, epoch: int) -> list[int]:
    generator = torch.Generator(device="cpu").manual_seed(seed + epoch)
    return torch.randperm(size, generator=generator).tolist()


def train_writer_epoch(
    *,
    rows: Sequence[Mapping[str, Any]],
    writer: IndependentTransitionWriter,
    query_encoder: StateQueryEncoder,
    optimizer: torch.optim.Optimizer,
    config: Mapping[str, Any],
    epoch: int,
    device: torch.device,
) -> dict[str, float]:
    writer.train()
    query_encoder.train()
    losses: list[float] = []
    batch_size = int(config["batch_size"])
    order = _epoch_order(len(rows), seed=int(config["seed"]), epoch=epoch)
    for start in range(0, len(order), batch_size):
        indices = order[start : start + batch_size]
        batch = [rows[index] for index in indices]
        transition = batch_hash_features(
            [str(row["transition_text"]) for row in batch], int(config["feature_dim"])
        ).to(device)
        state = batch_hash_features(
            [str(row["state_text"]) for row in batch], int(config["feature_dim"])
        ).to(device)
        action_target = torch.stack(
            [signed_hash_features(str(row["action"]), int(config["program_dim"])) for row in batch]
        ).to(device)
        key, value, mu = writer(transition)
        query = query_encoder(state)
        alignment = (1.0 - F.cosine_similarity(key, query, dim=-1)).mean()
        value_loss = F.mse_loss(value, action_target)
        mean_loss = F.mse_loss(mu, torch.full_like(mu, 0.5))
        loss = alignment + value_loss + 0.01 * mean_loss
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(writer.parameters()) + list(query_encoder.parameters()),
            float(config["gradient_clip_norm"]),
        )
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
    return {
        "mean_loss": sum(losses) / len(losses),
        "min_loss": min(losses),
        "max_loss": max(losses),
        "batches": float(len(losses)),
    }


def compile_field(
    *,
    rows: Sequence[Mapping[str, Any]],
    writer: IndependentTransitionWriter,
    config: Mapping[str, Any],
    device: torch.device,
) -> tuple[ReversibleCompactField, list[Any]]:
    contributions = compile_contributions(
        writer,
        rows,
        feature_dim=int(config["feature_dim"]),
        batch_size=int(config["batch_size"]),
        device=device,
    )
    field = ReversibleCompactField(
        key_dim=int(config["key_dim"]),
        program_dim=int(config["program_dim"]),
    )
    for record in contributions:
        field.add(record)
    return field, contributions


def _action_embedding_targets(
    *,
    actions: Sequence[str],
    tokenizer: Any,
    embedding: Any,
    injection_tokens: int,
    device: torch.device,
    target_scale: float,
) -> Tensor:
    tokenized = tokenizer(
        list(actions),
        padding=True,
        add_special_tokens=False,
        return_tensors="pt",
    )
    ids = tokenized["input_ids"].to(device)
    mask = tokenized["attention_mask"].to(device=device, dtype=torch.float32)
    with torch.no_grad():
        values = embedding(ids).to(torch.float32)
        pooled = (values * mask.unsqueeze(-1)).sum(dim=1) / mask.sum(dim=1, keepdim=True).clamp_min(1)
        pooled = pooled * float(target_scale)
    return pooled[:, None, :].expand(-1, injection_tokens, -1)


def train_reader_epoch(
    *,
    rows: Sequence[Mapping[str, Any]],
    field: ReversibleCompactField,
    query_encoder: StateQueryEncoder,
    reader: FixedFieldReader,
    injector: AdditiveTokenMemoryInjector,
    tokenizer: Any,
    embedding: Any,
    optimizer: torch.optim.Optimizer,
    config: Mapping[str, Any],
    epoch: int,
    device: torch.device,
) -> dict[str, float]:
    query_encoder.train()
    reader.train()
    injector.train()
    fixed_a = field.A.to(device=device, dtype=torch.float32)
    fixed_b = field.B.to(device=device, dtype=torch.float32)
    order = _epoch_order(len(rows), seed=int(config["seed"]) + 1000, epoch=epoch)
    batch_size = int(config["batch_size"])
    losses: list[float] = []
    for start in range(0, len(order), batch_size):
        indices = order[start : start + batch_size]
        batch = [rows[index] for index in indices]
        state = batch_hash_features(
            [str(row["state_text"]) for row in batch], int(config["feature_dim"])
        ).to(device)
        query = query_encoder(state)
        field_value = F.normalize(fixed_b.unsqueeze(0) + query @ fixed_a, dim=-1)
        memory_z = reader(field_value)
        predicted = injector(memory_z).to(torch.float32)
        target = _action_embedding_targets(
            actions=[str(row["action"]) for row in batch],
            tokenizer=tokenizer,
            embedding=embedding,
            injection_tokens=int(config["injection_tokens"]),
            device=device,
            target_scale=float(config["embedding_target_scale"]),
        )
        loss = F.mse_loss(predicted, target)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(query_encoder.parameters()) + list(reader.parameters()) + list(injector.parameters()),
            float(config["gradient_clip_norm"]),
        )
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
    return {
        "mean_loss": sum(losses) / len(losses),
        "min_loss": min(losses),
        "max_loss": max(losses),
        "batches": float(len(losses)),
    }


def checkpoint_payload(
    *,
    modules: Mapping[str, Any],
    field: ReversibleCompactField,
    contributions: Sequence[Any],
    config: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the fixed-size production deployment state.

    Per-memory contribution tensors are deliberately excluded and sealed in a
    separate audit artifact. Runtime loading therefore cannot accidentally
    scan or retrieve the authoritative ledger.
    """
    return {
        "format": "alfworld_compact_rcmf_deployment_checkpoint_v1",
        "rcmf_version": COMPACT_RCMF_VERSION,
        "config": dict(config),
        "metadata": dict(metadata),
        "writer": modules["writer"].state_dict(),
        "query_encoder": modules["query_encoder"].state_dict(),
        "reader": modules["reader"].state_dict(),
        "injector": modules["injector"].state_dict(),
        "field_A": field.A,
        "field_B": field.B,
        "compiled_memory_count": len(contributions),
    }


def contribution_audit_payload(
    *,
    contributions: Sequence[Any],
    field: ReversibleCompactField,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a non-deployment audit artifact for independent writes/reversibility."""

    return {
        "format": "alfworld_compact_rcmf_contribution_audit_v1",
        "rcmf_version": COMPACT_RCMF_VERSION,
        "metadata": dict(metadata),
        "field_A": field.A,
        "field_B": field.B,
        "contribution_ids": [record.memory_id for record in contributions],
        "contribution_parent_ids": [record.parent_id for record in contributions],
        "contribution_keys": torch.stack([record.key for record in contributions]),
        "contribution_values": torch.stack([record.value for record in contributions]),
        # These coefficients participate in the independently reconstructed
        # float64 field.  Preserve their Python-float precision in the audit
        # artifact; the default float32 tensor dtype is insufficient for the
        # checkpoint closure tolerance when rho is a non-dyadic fraction.
        "contribution_mu": torch.tensor(
            [record.mu for record in contributions], dtype=torch.float64
        ),
        "contribution_rho": torch.tensor(
            [record.rho for record in contributions], dtype=torch.float64
        ),
    }


def load_deployment_checkpoint(path: str | Path, *, device: torch.device) -> dict[str, Any]:
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if payload.get("format") != "alfworld_compact_rcmf_deployment_checkpoint_v1":
        raise ValueError("ALFWorld RCMF checkpoint format differs")
    prohibited = {
        "contribution_ids",
        "contribution_parent_ids",
        "contribution_keys",
        "contribution_values",
        "contribution_mu",
        "contribution_rho",
    }
    if prohibited.intersection(payload):
        raise ValueError("ALFWorld deployment checkpoint contains per-memory runtime state")
    modules = create_modules(payload["config"])
    for name in ("writer", "query_encoder", "reader", "injector"):
        modules[name].load_state_dict(payload[name], strict=True)
        modules[name].to(device)
        modules[name].eval()
    payload["modules"] = modules
    if not torch.isfinite(payload["field_A"]).all() or not torch.isfinite(payload["field_B"]).all():
        raise ValueError("ALFWorld RCMF deployment field is non-finite")
    return payload


def deployment_memory_query(
    payload: Mapping[str, Any],
    *,
    task_instruction: str,
    device: torch.device,
) -> Any:
    """Build a fixed-field query closure; it never accesses contribution rows."""

    modules = payload["modules"]
    config = payload["config"]
    fixed_a = payload["field_A"].to(device=device, dtype=torch.float32)
    fixed_b = payload["field_B"].to(device=device, dtype=torch.float32)

    def query(trajectory_text: str, history: list[dict[str, str]]) -> Tensor:
        current = history[-1]["observation"] if history else trajectory_text.removesuffix("\n>")
        features = signed_hash_features(
            f"Goal: {task_instruction}\nState: {current}",
            int(config["feature_dim"]),
        ).to(device)
        with torch.no_grad():
            key = modules["query_encoder"](features.unsqueeze(0))
            raw = F.normalize(fixed_b.unsqueeze(0) + key @ fixed_a, dim=-1)
            return modules["reader"](raw)

    return query


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
