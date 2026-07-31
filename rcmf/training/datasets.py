from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Iterable

import torch
from torch import Tensor
from torch.utils.data import Dataset

from rcmf.benchmarks.appworld.prompt import get_system_prompt
from rcmf.config import RCMFConfig
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.utils.serialization import read_jsonl, write_jsonl


def load_memory_records(path: str | Path) -> list[MemoryRecord]:
    return [MemoryRecord.from_dict(row) for row in read_jsonl(path)]


def load_decision_examples(path: str | Path) -> list[DecisionExample]:
    return [DecisionExample.from_dict(row) for row in read_jsonl(path)]


def save_memory_records(path: str | Path, records: Iterable[MemoryRecord]) -> None:
    write_jsonl(path, (record.to_dict() for record in records))


def save_decision_examples(path: str | Path, examples: Iterable[DecisionExample]) -> None:
    write_jsonl(path, (example.to_dict() for example in examples))


class MemoryRecordDataset(Dataset):
    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> MemoryRecord:
        return self.records[index]


class DecisionExampleDataset(Dataset):
    def __init__(self, examples: list[DecisionExample]) -> None:
        self.examples = examples

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> DecisionExample:
        return self.examples[index]


def _ensure_padding_token(tokenizer: Any) -> int:
    if getattr(tokenizer, "pad_token_id", None) is None:
        eos_token = getattr(tokenizer, "eos_token", None)
        if eos_token is not None:
            tokenizer.pad_token = eos_token
        else:
            tokenizer.add_special_tokens({"pad_token": "<|pad|>"})
    return int(tokenizer.pad_token_id)


def _tokenize_texts(tokenizer: Any, texts: list[str], max_length: int) -> dict[str, Tensor]:
    _ensure_padding_token(tokenizer)
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
        add_special_tokens=True,
    )
    return {
        "input_ids": encoded["input_ids"].to(torch.long),
        "attention_mask": encoded.get("attention_mask", torch.ones_like(encoded["input_ids"])).to(torch.long),
    }


def _apply_chat_template(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    if callable(apply_template):
        try:
            return apply_template(messages, tokenize=False, add_generation_prompt=True)
        except TypeError:
            return apply_template(messages, tokenize=False)
    lines = []
    for message in messages:
        lines.append(f"{message['role'].upper()}:\n{message['content']}")
    lines.append("ASSISTANT:")
    return "\n\n".join(lines)


def _split_embedded_system_prompt(state_text: str) -> tuple[str | None, str]:
    system_marker = "[SYSTEM PROMPT]"
    query_marker = "[QUERY]"
    stripped = state_text.strip()
    if not stripped.startswith(system_marker):
        return None, state_text
    query_at = stripped.find(query_marker)
    if query_at < 0:
        return None, state_text
    system_prompt = stripped[len(system_marker) : query_at].strip()
    user_text = stripped[query_at:].strip()
    return system_prompt, user_text


def _render_training_prompt(tokenizer: Any, example: DecisionExample, prompt_profile: str) -> str:
    embedded_system_prompt, user_text = _split_embedded_system_prompt(example.state_text)
    system_prompt = str(example.metadata.get("system_prompt") or embedded_system_prompt or "")
    if not system_prompt:
        system_prompt = get_system_prompt(prompt_profile)
    if example.target_type == "answer":
        user_text += "\n\nRespond with the final answer only."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    return _apply_chat_template(tokenizer, messages)


def _target_suffix(tokenizer: Any, example: DecisionExample) -> str:
    target = example.target_text
    eos = getattr(tokenizer, "eos_token", None) or ""
    if example.target_type == "code":
        if "```" not in target:
            target = f"```python\n{target.strip()}\n```"
    return target + eos


def _pad_sequences(
    values: list[list[int]],
    pad_value: int,
    dtype: torch.dtype = torch.long,
) -> Tensor:
    max_len = max(len(value) for value in values)
    output = torch.full((len(values), max_len), pad_value, dtype=dtype)
    for index, value in enumerate(values):
        output[index, : len(value)] = torch.tensor(value, dtype=dtype)
    return output


def _build_query_tensors(
    tokenizer: Any,
    examples: list[DecisionExample],
    prompt_profile: str,
    max_length: int,
) -> dict[str, Tensor]:
    pad_id = _ensure_padding_token(tokenizer)
    input_rows: list[list[int]] = []
    label_rows: list[list[int]] = []
    mask_rows: list[list[int]] = []
    for example in examples:
        prompt = _render_training_prompt(tokenizer, example, prompt_profile)
        target = _target_suffix(tokenizer, example)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
        if len(target_ids) >= max_length:
            full_ids = target_ids[:max_length]
            labels = list(full_ids)
        else:
            max_prompt_len = max_length - len(target_ids)
            if len(prompt_ids) > max_prompt_len:
                prompt_ids = prompt_ids[-max_prompt_len:]
            full_ids = prompt_ids + target_ids
            labels = [-100] * len(prompt_ids) + list(target_ids)
        if not full_ids:
            full_ids = [pad_id]
        if all(label == -100 for label in labels):
            labels[-1] = full_ids[-1]
        input_rows.append(full_ids)
        label_rows.append(labels)
        mask_rows.append([1] * len(full_ids))
    return {
        "query_input_ids": _pad_sequences(input_rows, pad_id),
        "query_attention_mask": _pad_sequences(mask_rows, 0),
        "labels": _pad_sequences(label_rows, -100),
    }


def sample_support_records(
    records: list[MemoryRecord],
    support_size: int,
    rng: random.Random,
) -> list[MemoryRecord]:
    if not records:
        raise ValueError("At least one memory record is required for RCMF training")
    if support_size <= 0:
        raise ValueError("support_size must be positive")
    if len(records) >= support_size:
        return rng.sample(records, support_size)
    return [rng.choice(records) for _ in range(support_size)]


def build_rcmf_training_batch(
    tokenizer: Any,
    config: RCMFConfig,
    support_records: list[MemoryRecord],
    examples: list[DecisionExample],
    max_query_tokens: int = 768,
) -> dict[str, Tensor]:
    support = _tokenize_texts(
        tokenizer,
        [record.experience_text for record in support_records],
        max_length=config.encoder.max_experience_tokens,
    )
    state = _tokenize_texts(
        tokenizer,
        [example.state_text for example in examples],
        max_length=config.encoder.max_state_tokens,
    )
    query = _build_query_tensors(
        tokenizer,
        examples,
        prompt_profile=config.benchmark.prompt_profile,
        max_length=max_query_tokens,
    )
    return {
        "support_input_ids": support["input_ids"],
        "support_attention_mask": support["attention_mask"],
        "state_input_ids": state["input_ids"],
        "state_attention_mask": state["attention_mask"],
        **query,
    }
