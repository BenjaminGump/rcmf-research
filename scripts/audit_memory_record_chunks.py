from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.datasets import load_memory_records
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _load_tokenizer_and_model_limit(model_name: str) -> tuple[Any, int | None]:
    try:
        from transformers import AutoConfig, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "transformers is required for tokenizer-only memory chunk audits"
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model_limit = None
    try:
        model_config = AutoConfig.from_pretrained(model_name, trust_remote_code=True)
        max_positions = getattr(model_config, "max_position_embeddings", None)
        if max_positions is not None:
            model_limit = int(max_positions)
    except Exception:
        model_limit = None
    return tokenizer, model_limit


def _default_chunk_limit(tokenizer: Any, model_limit: int | None, requested: int | None) -> int:
    if requested is not None:
        if requested <= 0:
            raise ValueError("--max-chunk-tokens must be positive")
        return int(requested)
    if model_limit is not None:
        return int(model_limit)
    tokenizer_limit = getattr(tokenizer, "model_max_length", None)
    if tokenizer_limit is not None and int(tokenizer_limit) < 1_000_000_000:
        return int(tokenizer_limit)
    return 32768


def _token_count(tokenizer: Any, text: str) -> int:
    ids = tokenizer(text, truncation=False, add_special_tokens=True)["input_ids"]
    return max(1, len(ids))


def _markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Memory Record Chunk Audit",
        "",
        f"- records: {summary['records']}",
        f"- total chunks: {summary['total_chunks']}",
        f"- chunk limit: {summary['chunk_limit']}",
        f"- multi-chunk records: {summary['multi_chunk_records']}",
        f"- maximum chunks per record: {summary['maximum_chunks']}",
        f"- maximum raw tokens: {summary['maximum_raw_tokens']}",
        "",
        "## Chunk Count Histogram",
        "",
    ]
    for chunk_count, count in summary["chunk_count_histogram"].items():
        lines.append(f"- {chunk_count}: {count}")
    multi = [row for row in summary["record_rows"] if int(row["chunk_count"]) > 1]
    if multi:
        lines.extend(["", "## Multi-Chunk Records", ""])
        for row in multi[:50]:
            lines.append(
                f"- line {row['jsonl_line']} `{row['memory_id']}` "
                f"task={row['task_id']} episode={row['episode_id']} "
                f"tokens={row['raw_token_count']} chunks={row['chunk_count']}"
            )
        if len(multi) > 50:
            lines.append(f"- ... {len(multi) - 50} additional multi-chunk records omitted from markdown")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit MemoryRecord tokenizer chunking without loading Qwen weights.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--records", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--max-chunk-tokens", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    records_path = Path(args.records)
    records = load_memory_records(records_path)
    tokenizer, model_limit = _load_tokenizer_and_model_limit(cfg.model.name)
    chunk_limit = _default_chunk_limit(tokenizer, model_limit, args.max_chunk_tokens)

    rows: list[dict[str, Any]] = []
    chunk_counts: list[int] = []
    for index, record in enumerate(records):
        raw_tokens = _token_count(tokenizer, record.experience_text)
        chunks = max(1, math.ceil(raw_tokens / chunk_limit))
        chunk_counts.append(chunks)
        rows.append(
            {
                "index": index,
                "jsonl_line": index + 1,
                "memory_id": record.memory_id,
                "task_id": record.task_id,
                "episode_id": record.episode_id,
                "raw_token_count": raw_tokens,
                "chunk_count": chunks,
                "aggregation_policy": "one MemoryRecord -> token-weighted mean representation -> one compiled delta",
            }
        )

    histogram = Counter(chunk_counts)
    summary = {
        "format": "memory_record_chunk_audit_v1",
        "config": args.config,
        "records_path": str(records_path),
        "records_sha256": sha256_file(records_path),
        "model_name": cfg.model.name,
        "model_max_position_embeddings": model_limit,
        "chunk_limit": chunk_limit,
        "records": len(records),
        "total_chunks": int(sum(chunk_counts)),
        "single_chunk_records": int(sum(1 for value in chunk_counts if value == 1)),
        "multi_chunk_records": int(sum(1 for value in chunk_counts if value > 1)),
        "maximum_chunks": int(max(chunk_counts) if chunk_counts else 0),
        "maximum_raw_tokens": int(max((row["raw_token_count"] for row in rows), default=0)),
        "chunk_count_histogram": {str(key): value for key, value in sorted(histogram.items())},
        "record_rows": rows,
    }
    atomic_write_json(args.output_json, summary)
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).write_text(_markdown_report(summary), encoding="utf-8")
    print(f"Wrote memory chunk audit to {args.output_json} and {args.output_md}")


if __name__ == "__main__":
    main()
