from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import torch
import yaml

from rcmf.benchmarks.alfworld.execution_lock import load_execution_lock
from rcmf.benchmarks.alfworld.runtime_agent import load_frozen_qwen
from rcmf.benchmarks.alfworld.training import (
    checkpoint_payload,
    compile_field,
    contribution_audit_payload,
    create_modules,
    load_ledger,
    set_seed,
    sha256_file,
    train_reader_epoch,
    train_writer_epoch,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the ALFWorld compact RCMF field")
    parser.add_argument("--config", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--model-snapshot", required=True)
    parser.add_argument("--benchmark-lock", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--engineering-limit", type=int, default=0)
    return parser.parse_args()


def _load_config(path: Path) -> dict[str, object]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "alfworld_compact_rcmf_training_config_v1":
        raise ValueError("ALFWorld compact RCMF config schema differs")
    for name, value in {
        "checkpoint_policy": "terminal_completed_epoch",
        "model_frozen": True,
        "runtime_retrieval": False,
        "raw_memory_in_query_prompt": False,
        "injection_position": "last_user_k",
    }.items():
        if config.get(name) != value:
            raise ValueError(f"required ALFWorld RCMF config field differs: {name}")
    return dict(config)


def _atomic_torch_save(payload: dict[str, object], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, temporary)
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    if args.engineering_limit < 0:
        raise ValueError("--engineering-limit must be zero or positive")
    config_path = Path(args.config).resolve(strict=True)
    ledger_path = Path(args.ledger).resolve(strict=True)
    config = _load_config(config_path)
    benchmark_lock = load_execution_lock(args.benchmark_lock)
    rows = load_ledger(ledger_path)
    full_ledger_count = len(rows)
    if args.engineering_limit:
        rows = rows[: args.engineering_limit]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "terminal_checkpoint.pt"
    if final_path.exists():
        raise FileExistsError(f"refusing to overwrite terminal checkpoint: {final_path}")
    set_seed(int(config["seed"]))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    started = time.time()
    started_utc = datetime.now(timezone.utc).isoformat()
    modules = create_modules(config)
    for module in modules.values():
        module.to(device)

    writer_optimizer = torch.optim.AdamW(
        list(modules["writer"].parameters()) + list(modules["query_encoder"].parameters()),
        lr=float(config["writer_learning_rate"]),
        weight_decay=float(config["weight_decay"]),
    )
    writer_metrics = []
    for epoch in range(1, int(config["writer_epochs"]) + 1):
        metrics = train_writer_epoch(
            rows=rows,
            writer=modules["writer"],
            query_encoder=modules["query_encoder"],
            optimizer=writer_optimizer,
            config=config,
            epoch=epoch,
            device=device,
        )
        writer_metrics.append({"epoch": epoch, **metrics})
        print(json.dumps({"stage": "writer", **writer_metrics[-1]}, sort_keys=True), flush=True)
        _atomic_torch_save(
            {
                "format": "alfworld_compact_rcmf_writer_checkpoint_v1",
                "epoch": epoch,
                "config": config,
                "writer": modules["writer"].state_dict(),
                "query_encoder": modules["query_encoder"].state_dict(),
                "optimizer": writer_optimizer.state_dict(),
                "metrics": writer_metrics,
            },
            output_dir / f"writer_epoch_{epoch}.pt",
        )

    field, contributions = compile_field(
        rows=rows,
        writer=modules["writer"],
        config=config,
        device=device,
    )
    baseline_a, baseline_b = field.A.clone(), field.B.clone()
    probe_id = contributions[0].memory_id
    removed = field.remove(probe_id)
    field.restore(removed)
    reversible_max_abs = max(
        float((field.A - baseline_a).abs().max()),
        float((field.B - baseline_b).abs().max()),
    )
    reverse_a, reverse_b = field.rebuild(reversed(sorted(field.records)))
    permutation_max_abs = max(
        float((reverse_a - baseline_a).abs().max()),
        float((reverse_b - baseline_b).abs().max()),
    )
    if reversible_max_abs > 1e-12 or permutation_max_abs > 1e-10:
        raise RuntimeError("compact field reversibility/permutation audit failed")

    flash_identity = benchmark_lock["payload"]["runtime_execution"][
        "flash_attn_installation_manifest_sha256"
    ]
    backend = load_frozen_qwen(
        args.model_snapshot,
        flash_attn_installation_sha256=flash_identity,
    )
    embedding = backend.model.get_input_embeddings()
    reader_optimizer = torch.optim.AdamW(
        list(modules["query_encoder"].parameters())
        + list(modules["reader"].parameters())
        + list(modules["injector"].parameters()),
        lr=float(config["reader_learning_rate"]),
        weight_decay=float(config["weight_decay"]),
    )
    reader_metrics = []
    for epoch in range(1, int(config["reader_epochs"]) + 1):
        metrics = train_reader_epoch(
            rows=rows,
            field=field,
            query_encoder=modules["query_encoder"],
            reader=modules["reader"],
            injector=modules["injector"],
            tokenizer=backend.tokenizer,
            embedding=embedding,
            optimizer=reader_optimizer,
            config=config,
            epoch=epoch,
            device=device,
        )
        reader_metrics.append({"epoch": epoch, **metrics})
        print(json.dumps({"stage": "reader", **reader_metrics[-1]}, sort_keys=True), flush=True)

    metadata = {
        "source_commit": args.source_commit,
        "run_uuid": args.run_uuid,
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": sha256_file(ledger_path),
        "benchmark_lock_sha256": benchmark_lock["file_sha256"],
        "benchmark_lock_identity_sha256": benchmark_lock["lock_identity_sha256"],
        "full_ledger_count": full_ledger_count,
        "trained_transition_count": len(rows),
        "engineering_limit": args.engineering_limit or None,
        "writer_metrics": writer_metrics,
        "reader_metrics": reader_metrics,
        "field_shape": field.field_shape,
        "reversible_max_abs": reversible_max_abs,
        "permutation_max_abs": permutation_max_abs,
        "qwen_frozen": not any(parameter.requires_grad for parameter in backend.model.parameters()),
        "terminal_completed_epoch": int(config["reader_epochs"]),
        "started_utc": started_utc,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - started, 6),
    }
    payload = checkpoint_payload(
        modules=modules,
        field=field,
        contributions=contributions,
        config=config,
        metadata=metadata,
    )
    contribution_audit_path = output_dir / "contribution_audit.pt"
    _atomic_torch_save(
        contribution_audit_payload(
            contributions=contributions,
            field=field,
            metadata=metadata,
        ),
        contribution_audit_path,
    )
    _atomic_torch_save(payload, final_path)
    summary = {
        "schema_version": "alfworld_compact_rcmf_training_summary_v1",
        **metadata,
        "checkpoint": {
            "path": str(final_path),
            "bytes": final_path.stat().st_size,
            "sha256": sha256_file(final_path),
        },
        "contribution_audit": {
            "path": str(contribution_audit_path),
            "bytes": contribution_audit_path.stat().st_size,
            "sha256": sha256_file(contribution_audit_path),
        },
        "peak_cuda_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0,
        "passed": True,
    }
    summary_path = output_dir / "training_summary.json"
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, summary_path)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
