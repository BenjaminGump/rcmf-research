from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend, build_trainer
from rcmf.memory.compiler import HashingMemoryCompiler
from rcmf.memory.ledger import MemoryLedger
from rcmf.memory.state import MemoryState
from rcmf.training.datasets import load_memory_records
from rcmf.utils.serialization import atomic_write_json, maybe_git_commit


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile MemoryRecords into an RCMF snapshot.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--records", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ledger-dir", default=None)
    parser.add_argument("--compiler", choices=["hashing", "checkpoint"], default="hashing")
    parser.add_argument("--checkpoint", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    records = load_memory_records(args.records)
    state = MemoryState(rank=cfg.memory.rank, program_dim=cfg.memory.program_dim)
    ledger_dir = Path(args.ledger_dir or Path(args.output).with_suffix("").as_posix() + "_ledger")
    ledger = MemoryLedger(ledger_dir)
    if args.compiler == "hashing":
        compiler = HashingMemoryCompiler(
            rank=cfg.memory.rank,
            program_dim=cfg.memory.program_dim,
            topk=cfg.address.topk,
        )
        for record in records:
            delta = compiler.compile_text(record.memory_id, record.experience_text)
            ledger.add_record(record, delta, state=state, compiler_version=cfg.compiler.version)
    else:
        if not args.checkpoint:
            raise ValueError("--checkpoint is required when --compiler checkpoint")
        backend = build_backend(cfg, load_model=True)
        trainer = build_trainer(cfg, backend)
        trainer.load_checkpoint(args.checkpoint, map_location=backend.device)
        trainer.to(backend.device).eval()
        tokenizer = backend.tokenizer
        with torch.no_grad():
            for record in records:
                tokenized = tokenizer(
                    record.experience_text,
                    padding=False,
                    truncation=True,
                    max_length=cfg.encoder.max_experience_tokens,
                    return_tensors="pt",
                )
                input_ids = tokenized["input_ids"].to(backend.device)
                attention_mask = tokenized.get("attention_mask", torch.ones_like(input_ids)).to(
                    backend.device
                )
                delta = trainer.compiler.compile_one(
                    record.memory_id,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    metadata={"compiler": "checkpoint", "checkpoint": str(args.checkpoint)},
                )
                ledger.add_record(record, delta, state=state, compiler_version=cfg.compiler.version)
    state.snapshot(
        args.output,
        metadata={
            "normalization": cfg.memory.normalization,
            "records": str(len(records)),
            "git_commit": maybe_git_commit() or "",
        },
    )
    save_resolved_config(cfg, Path(args.output).with_suffix(".config.yaml"))
    atomic_write_json(
        Path(args.output).with_suffix(".summary.json"),
        {
            "records": len(records),
            "snapshot": str(Path(args.output)),
            "ledger_dir": str(ledger_dir),
            "compiler": args.compiler,
            "checkpoint": args.checkpoint,
            "git_commit": maybe_git_commit(),
        },
    )
    print(f"Compiled {len(records)} memories to {args.output}")


if __name__ == "__main__":
    main()
