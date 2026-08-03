from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config
from rcmf.factory import build_backend, build_trainer
from rcmf.memory.state import MemoryState


def _tensor_stats(tensor: torch.Tensor) -> dict[str, float]:
    values = tensor.detach().to(torch.float32).flatten().cpu()
    if values.numel() == 0:
        return {"count": 0.0}
    return {
        "count": float(values.numel()),
        "mean": float(values.mean()),
        "std": float(values.std(unbiased=False)),
        "min": float(values.min()),
        "p50": float(values.quantile(0.50)),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def _row_norm_stats(tensor: torch.Tensor) -> dict[str, float]:
    return _tensor_stats(tensor.detach().to(torch.float32).norm(dim=-1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect RCMF memory_z and injector delta magnitudes.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--memory-snapshot", required=True)
    parser.add_argument("--state-representation-cache", required=True)
    parser.add_argument("--scale", type=float, action="append", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-rows", type=int)
    args = parser.parse_args()

    cfg = load_config(args.config)
    backend = build_backend(cfg, load_model=True)
    trainer = build_trainer(cfg, backend)
    checkpoint = trainer.load_checkpoint(args.checkpoint, map_location=backend.device)
    trainer.to(backend.device).eval()
    state = MemoryState.load(args.memory_snapshot)

    payload = torch.load(args.state_representation_cache, map_location="cpu")
    if payload.get("format") != "pooled_qwen_hidden_v1":
        raise ValueError("state representation cache must use format=pooled_qwen_hidden_v1")
    representations = payload["representations"].to(torch.float32)
    if args.max_rows is not None:
        representations = representations[: args.max_rows]

    embedding_weight = backend.model.get_input_embeddings().weight.detach().to(torch.float32)
    embedding_norms = embedding_weight.norm(dim=-1)
    output: dict[str, Any] = {
        "checkpoint": args.checkpoint,
        "checkpoint_step": int(checkpoint.get("step", 0)),
        "memory_snapshot": args.memory_snapshot,
        "state_rows": int(representations.shape[0]),
        "memory_V_norm": float(state.V.norm()),
        "memory_c_stats": _tensor_stats(state.c),
        "qwen_embedding_row_norm": _tensor_stats(embedding_norms),
    }
    prefix_scale = getattr(trainer.injector, "prefix_scale", None)
    if prefix_scale is not None:
        output["injector_prefix_scale"] = float(prefix_scale.detach().cpu())

    requested_scales = args.scale or [1.0]
    scales_to_check = list(dict.fromkeys(float(scale) for scale in requested_scales))
    scales: dict[str, Any] = {}
    with torch.no_grad():
        addresses = []
        z_by_scale = {scale: [] for scale in scales_to_check}
        prefix_by_scale = {scale: [] for scale in scales_to_check}
        max_abs_prefix_by_scale = {scale: [] for scale in scales_to_check}
        for start in range(0, representations.shape[0], args.batch_size):
            reps = representations[start : start + args.batch_size].to(backend.device)
            address = trainer.state_encoder(reps, None).detach().cpu()
            addresses.append(address)
            z = state.read(
                address,
                normalization=cfg.memory.normalization,
                eps=cfg.memory.eps,
            ).to(backend.device)
            for scale in scales_to_check:
                scaled_z = z * float(scale)
                prefix = trainer.injector(scaled_z).detach().to(torch.float32).cpu()
                z_by_scale[scale].append(scaled_z.detach().to(torch.float32).cpu())
                prefix_by_scale[scale].append(prefix.norm(dim=-1))
                max_abs_prefix_by_scale[scale].append(prefix.abs().amax(dim=(-1, -2)))
        output["address_row_norm"] = _row_norm_stats(torch.cat(addresses, dim=0))
        for scale in scales_to_check:
            z_tensor = torch.cat(z_by_scale[scale], dim=0)
            prefix_token_norms = torch.cat(prefix_by_scale[scale], dim=0).flatten()
            prefix_max_abs = torch.cat(max_abs_prefix_by_scale[scale], dim=0)
            scales[str(scale)] = {
                "memory_z_row_norm": _row_norm_stats(z_tensor),
                "prefix_token_norm": _tensor_stats(prefix_token_norms),
                "prefix_max_abs_per_example": _tensor_stats(prefix_max_abs),
            }
    output["scales"] = scales
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
