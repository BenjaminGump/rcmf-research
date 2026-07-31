from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend, build_trainer
from rcmf.memory.compiler import HashingMemoryCompiler
from rcmf.memory.ledger import MemoryLedger
from rcmf.memory.state import MemoryDelta, MemoryState
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
    parser.add_argument("--representation-cache", default=None)
    parser.add_argument("--representation-batch-size", type=int, default=1)
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
        representations = None
        owner_indices = None
        if cfg.encoder.type == "qwen_hidden":
            if args.representation_cache and Path(args.representation_cache).exists():
                payload = torch.load(args.representation_cache, map_location="cpu")
                if payload.get("format") != "chunked_qwen_hidden_v1":
                    raise ValueError("Representation cache must use format=chunked_qwen_hidden_v1")
                representations = payload["representations"].to(torch.float32)
                owner_indices = payload["owner_indices"].to(torch.long)
                if int(payload.get("text_count", -1)) != len(records):
                    raise ValueError("Representation cache text_count does not match records")
            else:
                representations, owner_indices = backend.encode_text_chunks(
                    [record.experience_text for record in records],
                    batch_size=args.representation_batch_size,
                )
                representations = representations.to(torch.float32)
                owner_indices = owner_indices.to(torch.long)
        with torch.no_grad():
            for index, record in enumerate(records):
                if representations is None:
                    tokenized = tokenizer(
                        record.experience_text,
                        padding=False,
                        truncation=False,
                        return_tensors="pt",
                    )
                    input_ids = tokenized["input_ids"].to(backend.device)
                    attention_mask = tokenized.get("attention_mask", torch.ones_like(input_ids)).to(
                        backend.device
                    )
                    if (
                        cfg.encoder.max_experience_tokens is not None
                        and input_ids.shape[-1] > cfg.encoder.max_experience_tokens
                    ):
                        raise ValueError(
                            f"Experience text for {record.memory_id} has {input_ids.shape[-1]} tokens, "
                            f"exceeding max_experience_tokens={cfg.encoder.max_experience_tokens}. "
                            "No truncation is applied."
                        )
                    compiler_input = input_ids
                    compiler_mask = attention_mask
                    delta = trainer.compiler.compile_one(
                        record.memory_id,
                        input_ids=compiler_input,
                        attention_mask=compiler_mask,
                        metadata={"compiler": "checkpoint", "checkpoint": str(args.checkpoint)},
                    )
                else:
                    if owner_indices is None:
                        raise ValueError("Chunked representation cache is missing owner_indices")
                    chunk_rows = (owner_indices == index).nonzero(as_tuple=False).flatten()
                    if chunk_rows.numel() == 0:
                        raise ValueError(f"No representation chunks found for memory record {record.memory_id}")
                    support = trainer.compiler(representations[chunk_rows].to(backend.device), None)
                    delta = MemoryDelta(
                        memory_id=record.memory_id,
                        delta_v=support.delta_v.sum(dim=0),
                        delta_c=support.delta_c.sum(dim=0),
                        metadata={
                            "compiler": "checkpoint",
                            "checkpoint": str(args.checkpoint),
                            "representation_chunks": int(chunk_rows.numel()),
                        },
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
            "representation_chunks": int(owner_indices.numel()) if owner_indices is not None else None,
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
