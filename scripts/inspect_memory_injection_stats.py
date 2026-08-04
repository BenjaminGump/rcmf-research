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
from rcmf.utils.serialization import atomic_write_json


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
    if tensor.numel() == 0:
        return {"count": 0.0}
    return _tensor_stats(tensor.detach().to(torch.float32).norm(dim=-1))


def _pairwise_cosine_stats(
    tensor: torch.Tensor,
    sample: int,
    generator: torch.Generator,
) -> dict[str, float]:
    rows = torch.nn.functional.normalize(tensor.detach().to(torch.float32).cpu(), dim=-1)
    n = rows.shape[0]
    if n < 2:
        return {"count": 0.0}
    pair_count = min(sample, n * (n - 1))
    left = torch.randint(0, n, (pair_count,), generator=generator)
    right = torch.randint(0, n, (pair_count,), generator=generator)
    same = left == right
    while bool(same.any().item()):
        right[same] = torch.randint(0, n, (int(same.sum().item()),), generator=generator)
        same = left == right
    cosines = (rows[left] * rows[right]).sum(dim=-1)
    return _tensor_stats(cosines)


def _directional_stats(
    tensor: torch.Tensor,
    sample: int,
    generator: torch.Generator,
) -> dict[str, Any]:
    rows = tensor.detach().to(torch.float32).cpu()
    if rows.numel() == 0:
        return {"rows": 0}
    normalized = torch.nn.functional.normalize(rows, dim=-1)
    mean_direction = torch.nn.functional.normalize(normalized.mean(dim=0, keepdim=True), dim=-1)
    cos_to_mean = (normalized * mean_direction).sum(dim=-1)
    centered = normalized - normalized.mean(dim=0, keepdim=True)
    svd_rows = centered
    if svd_rows.shape[0] > sample:
        indices = torch.randperm(svd_rows.shape[0], generator=generator)[:sample]
        svd_rows = svd_rows[indices]
    singular_values = torch.linalg.svdvals(svd_rows) if svd_rows.numel() else torch.empty(0)
    spectral_energy = singular_values.square()
    total_energy = float(spectral_energy.sum().clamp_min(1.0e-12))
    return {
        "rows": int(rows.shape[0]),
        "dims": int(rows.shape[-1]),
        "mean_direction_norm": float(normalized.mean(dim=0).norm().item()),
        "cosine_to_mean_direction": _tensor_stats(cos_to_mean),
        "pairwise_cosine": _pairwise_cosine_stats(rows, sample, generator),
        "centered_singular_values_top5": [
            float(value) for value in singular_values[:5].tolist()
        ],
        "centered_top1_energy_fraction": float(
            spectral_energy[0].item() / total_energy
        )
        if spectral_energy.numel()
        else 0.0,
    }


def _address_stats(address: torch.Tensor) -> dict[str, Any]:
    values = address.detach().to(torch.float32).cpu()
    entropy = -(values.clamp_min(1.0e-12) * values.clamp_min(1.0e-12).log()).sum(dim=-1)
    top_values, top_indices = values.max(dim=-1)
    histogram = torch.bincount(top_indices, minlength=values.shape[-1]).to(torch.long)
    top_hist = sorted(
        ((index, int(count)) for index, count in enumerate(histogram.tolist()) if count),
        key=lambda item: item[1],
        reverse=True,
    )[:20]
    return {
        "row_norm": _row_norm_stats(values),
        "entropy": _tensor_stats(entropy),
        "top1_value": _tensor_stats(top_values),
        "top1_unique_slots": int((histogram > 0).sum().item()),
        "top1_max_load_fraction": float(histogram.max().item() / max(1, values.shape[0])),
        "top1_histogram_top20": {str(index): count for index, count in top_hist},
    }


def _load_state_representations(path: str) -> tuple[torch.Tensor, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu")
    if payload.get("format") not in {"pooled_qwen_hidden_v1", "pooled_qwen_hidden_v2"}:
        raise ValueError("state representation cache must use format=pooled_qwen_hidden_v1 or pooled_qwen_hidden_v2")
    return payload["representations"].to(torch.float32), dict(payload)


def _load_memory_representations(path: str) -> tuple[torch.Tensor, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu")
    if payload.get("format") != "record_qwen_hidden_v2":
        raise ValueError("memory representation cache must use format=record_qwen_hidden_v2")
    return payload["representations"].to(torch.float32), dict(payload)


def _injector_delta_stats(trainer: Any, z: torch.Tensor) -> dict[str, Any]:
    delta = trainer.injector(z).detach().to(torch.float32).cpu()
    if delta.dim() == 3:
        token_norms = delta.norm(dim=-1)
        return {
            "shape": list(delta.shape),
            "row_norm": _row_norm_stats(delta.reshape(delta.shape[0], -1)),
            "token_norm": _tensor_stats(token_norms),
            "max_abs_per_example": _tensor_stats(delta.abs().amax(dim=(-1, -2))),
        }
    return {
        "shape": list(delta.shape),
        "row_norm": _row_norm_stats(delta),
        "max_abs_per_example": _tensor_stats(delta.abs().amax(dim=-1)),
    }


def _memory_read_controls(
    z: torch.Tensor,
    trainer: Any,
    scales: list[float],
    generator: torch.Generator,
) -> dict[str, Any]:
    controls: dict[str, torch.Tensor] = {"correct": z}
    controls["mean"] = z.mean(dim=0, keepdim=True).expand_as(z)
    if z.shape[0] > 1:
        controls["shuffled"] = z[torch.randperm(z.shape[0], generator=generator)]
    random_rows = torch.randn(z.shape, generator=generator, dtype=z.dtype)
    random_rows = torch.nn.functional.normalize(random_rows, dim=-1)
    controls["random_norm_matched"] = random_rows * z.norm(dim=-1, keepdim=True).cpu()

    output: dict[str, Any] = {}
    for name, control_z in controls.items():
        scale_rows: dict[str, Any] = {}
        for scale in scales:
            scaled = control_z.to(next(trainer.injector.parameters()).device) * float(scale)
            scale_rows[str(scale)] = {
                "memory_z_row_norm": _row_norm_stats(scaled.cpu()),
                "injector_delta": _injector_delta_stats(trainer, scaled),
            }
        output[name] = scale_rows
    return output


def _compiled_memory_stats(
    trainer: Any,
    representations: torch.Tensor,
    batch_size: int,
    pairwise_sample: int,
    generator: torch.Generator,
) -> dict[str, Any]:
    alphas: list[torch.Tensor] = []
    programs: list[torch.Tensor] = []
    rhos: list[torch.Tensor] = []
    delta_c: list[torch.Tensor] = []
    device = next(trainer.compiler.parameters()).device
    with torch.no_grad():
        for start in range(0, representations.shape[0], batch_size):
            reps = representations[start : start + batch_size].to(device)
            compiled = trainer.compiler(reps, None)
            alphas.append(compiled.alpha.detach().cpu())
            programs.append(compiled.program.detach().cpu())
            rhos.append(compiled.rho.detach().cpu())
            delta_c.append(compiled.delta_c.detach().cpu())
    alpha = torch.cat(alphas, dim=0)
    program = torch.cat(programs, dim=0)
    rho = torch.cat(rhos, dim=0)
    dc = torch.cat(delta_c, dim=0)
    return {
        "records": int(representations.shape[0]),
        "representation_row_norm": _row_norm_stats(representations),
        "representation_directional": _directional_stats(representations, pairwise_sample, generator),
        "alpha": _address_stats(alpha),
        "program_row_norm": _row_norm_stats(program),
        "program_directional": _directional_stats(program, pairwise_sample, generator),
        "rho": _tensor_stats(rho),
        "delta_c_row_norm": _row_norm_stats(dc),
    }


def _markdown_report(output: dict[str, Any]) -> str:
    lines = [
        "# Memory Injection Diagnostics",
        "",
        f"- checkpoint: `{output['checkpoint']}`",
        f"- checkpoint step: {output['checkpoint_step']}",
        f"- memory snapshot: `{output['memory_snapshot']}`",
        f"- state rows: {output['state_rows']}",
        f"- memory V norm: {output['memory_V_norm']:.6f}",
        "",
        "## Collapse Signals",
        "",
        f"- state pairwise cosine mean: {output['state_representation_directional']['pairwise_cosine'].get('mean', 0.0):.6f}",
        f"- address top1 max load fraction: {output['address']['top1_max_load_fraction']:.6f}",
        f"- memory_z pairwise cosine mean: {output['memory_z_directional']['pairwise_cosine'].get('mean', 0.0):.6f}",
        f"- memory_z mean direction norm: {output['memory_z_directional']['mean_direction_norm']:.6f}",
        "",
        "## Injector Controls",
        "",
    ]
    for control_name, scale_rows in output["controls"].items():
        for scale, row in scale_rows.items():
            token_norm = row["injector_delta"].get("token_norm", {}).get("mean")
            row_norm = row["injector_delta"]["row_norm"].get("mean")
            norm_value = token_norm if token_norm is not None else row_norm
            lines.append(f"- {control_name} scale={scale}: mean delta norm {norm_value:.6f}")
    if "compiled_memory" in output:
        lines.extend(["", "## Compiled Memory", ""])
        compiled = output["compiled_memory"]
        lines.append(f"- records: {compiled['records']}")
        lines.append(f"- alpha top1 max load fraction: {compiled['alpha']['top1_max_load_fraction']:.6f}")
        lines.append(f"- program pairwise cosine mean: {compiled['program_directional']['pairwise_cosine'].get('mean', 0.0):.6f}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect RCMF memory, address, read, and injector collapse signals.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--memory-snapshot", required=True)
    parser.add_argument("--state-representation-cache", required=True)
    parser.add_argument("--memory-representation-cache", default=None)
    parser.add_argument("--scale", type=float, action="append", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--pairwise-sample", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    backend = build_backend(cfg, load_model=True)
    trainer = build_trainer(cfg, backend)
    checkpoint = trainer.load_checkpoint(args.checkpoint, map_location=backend.device)
    trainer.to(backend.device).eval()
    state = MemoryState.load(args.memory_snapshot)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(args.seed)

    representations, state_payload = _load_state_representations(args.state_representation_cache)
    if args.max_rows is not None:
        representations = representations[: args.max_rows]

    embedding_weight = backend.model.get_input_embeddings().weight.detach().to(torch.float32)
    embedding_norms = embedding_weight.norm(dim=-1)
    output: dict[str, Any] = {
        "format": "memory_injection_diagnostics_v2",
        "checkpoint": args.checkpoint,
        "checkpoint_step": int(checkpoint.get("step", 0)),
        "memory_snapshot": args.memory_snapshot,
        "state_representation_cache": args.state_representation_cache,
        "state_cache_format": state_payload.get("format"),
        "state_cache_metadata": state_payload.get("cache_metadata"),
        "state_rows": int(representations.shape[0]),
        "memory_V_norm": float(state.V.norm()),
        "memory_c_stats": _tensor_stats(state.c),
        "qwen_embedding_row_norm": _tensor_stats(embedding_norms),
        "injector": {
            "class": trainer.injector.__class__.__name__,
            "position": getattr(trainer.injector, "position", None),
            "num_tokens": getattr(trainer.injector, "num_tokens", None),
            "prefix_scale": float(getattr(trainer.injector, "prefix_scale").detach().cpu())
            if hasattr(trainer.injector, "prefix_scale")
            else None,
        },
        "state_representation_row_norm": _row_norm_stats(representations),
        "state_representation_directional": _directional_stats(
            representations,
            args.pairwise_sample,
            generator,
        ),
    }

    requested_scales = args.scale or [1.0]
    scales_to_check = list(dict.fromkeys(float(scale) for scale in requested_scales))
    with torch.no_grad():
        addresses: list[torch.Tensor] = []
        raw_reads: list[torch.Tensor] = []
        masses: list[torch.Tensor] = []
        reads: list[torch.Tensor] = []
        for start in range(0, representations.shape[0], args.batch_size):
            reps = representations[start : start + args.batch_size].to(backend.device)
            address = trainer.state_encoder(reps, None).detach().cpu()
            addresses.append(address)
            raw_reads.append(address @ state.V.cpu())
            masses.append(address @ state.c.cpu())
            reads.append(
                state.read(
                    address,
                    normalization=cfg.memory.normalization,
                    eps=cfg.memory.eps,
                ).detach().cpu()
            )
        address_tensor = torch.cat(addresses, dim=0)
        raw_read_tensor = torch.cat(raw_reads, dim=0)
        mass_tensor = torch.cat(masses, dim=0)
        memory_z = torch.cat(reads, dim=0)

    output["address"] = _address_stats(address_tensor)
    output["raw_memory_read_row_norm"] = _row_norm_stats(raw_read_tensor)
    output["read_mass"] = _tensor_stats(mass_tensor)
    output["memory_z_row_norm"] = _row_norm_stats(memory_z)
    output["memory_z_directional"] = _directional_stats(memory_z, args.pairwise_sample, generator)
    output["controls"] = _memory_read_controls(memory_z, trainer, scales_to_check, generator)

    if args.memory_representation_cache:
        memory_representations, memory_payload = _load_memory_representations(args.memory_representation_cache)
        output["memory_representation_cache"] = args.memory_representation_cache
        output["memory_cache_metadata"] = {
            "format": memory_payload.get("format"),
            "record_count": memory_payload.get("record_count"),
            "chunk_count": memory_payload.get("chunk_count"),
            "aggregation_mode": memory_payload.get("aggregation_mode"),
        }
        output["compiled_memory"] = _compiled_memory_stats(
            trainer,
            memory_representations,
            args.batch_size,
            args.pairwise_sample,
            generator,
        )

    if args.output_json:
        atomic_write_json(args.output_json, output)
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(_markdown_report(output), encoding="utf-8")
    if not args.output_json and not args.output_md:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(
            "Wrote diagnostics"
            + (f" JSON={args.output_json}" if args.output_json else "")
            + (f" MD={args.output_md}" if args.output_md else "")
        )


if __name__ == "__main__":
    main()
