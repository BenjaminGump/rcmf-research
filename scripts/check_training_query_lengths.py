from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from transformers import AutoConfig, AutoTokenizer

from rcmf.config import load_config
from rcmf.training.datasets import (
    _append_eos_token_id,
    _render_training_prompt,
    _target_suffix,
    load_decision_examples,
)


def _percentile(sorted_values: list[int], percentile: float) -> int:
    index = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * percentile / 100.0)))
    return sorted_values[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure full prompt+target token lengths without truncation.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()

    cfg = load_config(args.config)
    model_config = AutoConfig.from_pretrained(cfg.model.name, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(cfg.model.name, trust_remote_code=True)
    examples = load_decision_examples(Path(args.data) / "decision_examples.jsonl")
    lengths: list[int] = []
    target_lengths: list[int] = []
    top_rows: list[dict[str, object]] = []
    for index, example in enumerate(examples):
        prompt = _render_training_prompt(tokenizer, example, cfg.benchmark.prompt_profile)
        target = _target_suffix(example)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = _append_eos_token_id(
            tokenizer,
            list(tokenizer(target, add_special_tokens=False)["input_ids"]),
        )
        total = len(prompt_ids) + len(target_ids)
        lengths.append(total)
        target_lengths.append(len(target_ids))
        top_rows.append(
            {
                "total_tokens": total,
                "prompt_tokens": len(prompt_ids),
                "target_tokens": len(target_ids),
                "example_index": index,
                "episode_id": example.episode_id,
                "step_id": example.step_id,
            }
        )
    sorted_lengths = sorted(lengths)
    sorted_targets = sorted(target_lengths)
    model_max = getattr(tokenizer, "model_max_length", None)
    model_config_max = getattr(model_config, "max_position_embeddings", None)
    count_thresholds = [8192, 32768, 65536, 131072, 262144, 1_000_000]
    if isinstance(model_max, int) and model_max not in count_thresholds and model_max < 1_000_000_000:
        count_thresholds.append(model_max)
    if isinstance(model_config_max, int) and model_config_max not in count_thresholds:
        count_thresholds.append(model_config_max)
    count_thresholds = sorted(set(count_thresholds))
    effective_context_limit = None
    context_candidates = []
    if isinstance(model_config_max, int):
        context_candidates.append(model_config_max)
    if isinstance(model_max, int) and model_max < 1_000_000_000:
        context_candidates.append(model_max)
    if context_candidates:
        effective_context_limit = min(context_candidates)
    rows_over_model_max = []
    if effective_context_limit is not None:
        rows_over_model_max = [row for row in top_rows if int(row["total_tokens"]) > effective_context_limit]
    over_episode_counts = Counter(str(row["episode_id"]) for row in rows_over_model_max)
    summary = {
        "config": args.config,
        "data": args.data,
        "count": len(lengths),
        "model_name": cfg.model.name,
        "tokenizer_model_max_length": model_max,
        "model_config_max_position_embeddings": model_config_max,
        "effective_context_limit": effective_context_limit,
        "query_total": {
            "min": min(lengths),
            "median": _percentile(sorted_lengths, 50),
            "p90": _percentile(sorted_lengths, 90),
            "p95": _percentile(sorted_lengths, 95),
            "p99": _percentile(sorted_lengths, 99),
            "max": max(lengths),
        },
        "target": {
            "min": min(target_lengths),
            "median": _percentile(sorted_targets, 50),
            "p95": _percentile(sorted_targets, 95),
            "p99": _percentile(sorted_targets, 99),
            "max": max(target_lengths),
        },
        "counts_over_threshold": {
            str(threshold): sum(1 for length in lengths if length > threshold)
            for threshold in count_thresholds
        },
        "over_model_max": {
            "count": len(rows_over_model_max),
            "unique_episode_count": len(over_episode_counts),
            "episode_counts": dict(over_episode_counts.most_common()),
        },
        "top": sorted(top_rows, key=lambda row: int(row["total_tokens"]), reverse=True)[: args.top_k],
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
