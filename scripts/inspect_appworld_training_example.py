from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.datasets import (
    _append_eos_token_id,
    _render_training_prompt,
    _target_suffix,
    load_decision_examples,
    load_memory_records,
)


STEP_RE = re.compile(
    r"^Step (?P<index>\d+) - Response:\n(?P<response>.*?)\n"
    r"Step (?P=index) - Observation:\n(?P<observation>.*?)(?=\nStep \d+ - Response:\n|\Z)",
    flags=re.MULTILINE | re.DOTALL,
)


def _clip(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(0, limit // 2)
    tail = max(0, limit - head)
    return text[:head] + "\n...[snip]...\n" + text[-tail:]


def _summarize_steps(state_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in STEP_RE.finditer(state_text):
        response = match.group("response").strip()
        observation = match.group("observation").strip()
        rows.append(
            {
                "step_id": int(match.group("index")),
                "response_chars": len(response),
                "observation_chars": len(observation),
                "observation_head": _clip(observation, 240),
            }
        )
    return rows


def _find_example(examples: list[Any], episode_id: str | None, step_id: int | None, index: int | None) -> Any:
    if index is not None:
        return examples[index]
    if episode_id is None or step_id is None:
        raise ValueError("Either --index or both --episode-id and --step-id are required")
    for example in examples:
        if example.episode_id == episode_id and int(example.step_id) == step_id:
            return example
    raise ValueError(f"Could not find example episode_id={episode_id!r} step_id={step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect one AppWorld per-step training example.")
    parser.add_argument("--config", default="configs/benchmark/appworld_rcmf_full_prompt.yaml")
    parser.add_argument("--data", required=True, help="Directory containing decision_examples.jsonl.")
    parser.add_argument("--episode-id", default=None)
    parser.add_argument("--step-id", type=int, default=None)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--snippet-chars", type=int, default=2000)
    parser.add_argument("--no-tokenizer", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data)
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    example = _find_example(examples, args.episode_id, args.step_id, args.index)
    step_summaries = _summarize_steps(example.state_text)
    records_path = data_dir / "memory_records.jsonl"
    record_summary = None
    if records_path.exists():
        for record in load_memory_records(records_path):
            if record.episode_id == example.episode_id:
                raw_steps = record.raw_trajectory.get("steps", [])
                record_summary = {
                    "experience_chars": len(record.experience_text),
                    "raw_step_count": len(raw_steps),
                    "raw_observation_char_max": max(
                        [len(str(step.get("observation", ""))) for step in raw_steps] or [0]
                    ),
                    "raw_response_char_max": max([len(str(step.get("response", ""))) for step in raw_steps] or [0]),
                }
                break

    report: dict[str, Any] = {
        "example": {
            "episode_id": example.episode_id,
            "step_id": example.step_id,
            "target_type": example.target_type,
            "task_id": example.metadata.get("task_id"),
            "source_path": example.metadata.get("source_path"),
        },
        "chars": {
            "state_text": len(example.state_text),
            "target_text": len(example.target_text),
        },
        "previous_steps": {
            "count": len(step_summaries),
            "total_response_chars": sum(row["response_chars"] for row in step_summaries),
            "total_observation_chars": sum(row["observation_chars"] for row in step_summaries),
            "max_observation_chars": max([row["observation_chars"] for row in step_summaries] or [0]),
            "last_5": step_summaries[-5:],
            "top_5_by_observation_chars": sorted(
                step_summaries,
                key=lambda row: int(row["observation_chars"]),
                reverse=True,
            )[:5],
        },
        "memory_record": record_summary,
        "state_head": _clip(example.state_text[: args.snippet_chars], args.snippet_chars),
        "state_tail": _clip(example.state_text[-args.snippet_chars :], args.snippet_chars),
        "target_text": _clip(example.target_text, args.snippet_chars),
    }
    if not args.no_tokenizer:
        cfg = load_config(args.config)
        tokenizer = AutoTokenizer.from_pretrained(cfg.model.name, trust_remote_code=True)
        prompt = _render_training_prompt(tokenizer, example, cfg.benchmark.prompt_profile)
        target = _target_suffix(example)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = _append_eos_token_id(
            tokenizer,
            list(tokenizer(target, add_special_tokens=False)["input_ids"]),
        )
        report["tokens"] = {
            "prompt": len(prompt_ids),
            "target": len(target_ids),
            "total": len(prompt_ids) + len(target_ids),
            "tokenizer_model_max_length": getattr(tokenizer, "model_max_length", None),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
