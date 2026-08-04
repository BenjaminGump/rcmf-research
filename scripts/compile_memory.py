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
from rcmf.utils.serialization import atomic_write_json, maybe_git_commit, sha256_file
from scripts.train import _aggregate_chunk_representations


def _load_or_compute_record_representations(
    backend: object,
    records: list,
    cache_path: Path | None,
    records_path: Path,
    model_name: str,
    batch_size: int,
) -> tuple[torch.Tensor, dict[str, object]]:
    source_hash = sha256_file(records_path)
    if cache_path is not None and cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu")
        if payload.get("format") != "record_qwen_hidden_v2":
            raise ValueError("Representation cache must use format=record_qwen_hidden_v2")
        if payload.get("source_sha256") != source_hash:
            raise ValueError("Representation cache source_sha256 does not match records")
        if payload.get("model_name") != model_name:
            raise ValueError("Representation cache model_name does not match config")
        if int(payload.get("record_count", -1)) != len(records):
            raise ValueError("Representation cache record_count does not match records")
        if payload.get("aggregation_mode") != "token_weighted_mean":
            raise ValueError("Representation cache must use aggregation_mode=token_weighted_mean")
        return payload["representations"].to(torch.float32), dict(payload.get("chunk_audit") or {})

    if not hasattr(backend, "encode_text_chunks_with_metadata"):
        raise TypeError("Backend must implement encode_text_chunks_with_metadata for qwen_hidden records")
    chunk_representations, owner_indices, token_counts = backend.encode_text_chunks_with_metadata(
        [record.experience_text for record in records],
        batch_size=batch_size,
    )
    chunk_representations = chunk_representations.to(torch.float32).cpu()
    owner_indices = owner_indices.to(torch.long).cpu()
    token_counts = token_counts.to(torch.long).cpu()
    representations, chunk_audit = _aggregate_chunk_representations(
        chunk_representations,
        owner_indices,
        token_counts,
        records,
    )
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "format": "record_qwen_hidden_v2",
                "representations": representations,
                "chunk_representations": chunk_representations,
                "owner_indices": owner_indices,
                "chunk_token_counts": token_counts,
                "source_path": str(records_path),
                "source_sha256": source_hash,
                "model_name": model_name,
                "record_count": len(records),
                "chunk_count": int(owner_indices.numel()),
                "aggregation_mode": "token_weighted_mean",
                "chunk_audit": chunk_audit,
            },
            cache_path,
        )
    return representations, chunk_audit


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
        chunk_audit: dict[str, object] | None = None
        if cfg.encoder.type == "qwen_hidden":
            representations, chunk_audit = _load_or_compute_record_representations(
                backend=backend,
                records=records,
                cache_path=Path(args.representation_cache) if args.representation_cache else None,
                records_path=Path(args.records),
                model_name=cfg.model.name,
                batch_size=args.representation_batch_size,
            )
            audit_rows = {
                int(row["index"]): row
                for row in chunk_audit.get("records", [])
                if isinstance(row, dict) and "index" in row
            }
        else:
            audit_rows = {}
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
                    support = trainer.compiler(representations[index : index + 1].to(backend.device), None)
                    audit_row = audit_rows.get(index, {})
                    delta = MemoryDelta(
                        memory_id=record.memory_id,
                        delta_v=support.delta_v[0],
                        delta_c=support.delta_c[0],
                        metadata={
                            "compiler": "checkpoint",
                            "checkpoint": str(args.checkpoint),
                            "representation_level": "record",
                            "aggregation_mode": "token_weighted_mean",
                            "representation_chunks": int(audit_row.get("chunk_count", 1)),
                            "raw_token_count": int(audit_row.get("raw_token_count", 0)),
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
            "representation_level": "record" if representations is not None else None,
            "representation_chunks": int(chunk_audit.get("total_chunks", 0))
            if isinstance(chunk_audit, dict)
            else None,
            "record_chunk_audit": chunk_audit,
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
