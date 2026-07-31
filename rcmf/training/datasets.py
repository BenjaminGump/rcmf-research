from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any, Iterable

import torch
from torch import Tensor
from torch.utils.data import Dataset

from rcmf.benchmarks.appworld.prompt import get_initial_messages, get_system_prompt, uses_chat_history_prompt
from rcmf.config import RCMFConfig
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.utils.serialization import read_jsonl, write_jsonl


STATE_STEP_RE = re.compile(
    r"^Step (?P<index>\d+) - Response:\n(?P<response>.*?)\n"
    r"Step (?P=index) - Observation:\n(?P<observation>.*?)(?=\nStep \d+ - Response:\n|\Z)",
    flags=re.MULTILINE | re.DOTALL,
)


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


def _tokenize_texts(tokenizer: Any, texts: list[str], max_length: int | None = None) -> dict[str, Tensor]:
    _ensure_padding_token(tokenizer)
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=False,
        return_tensors="pt",
        add_special_tokens=True,
    )
    if max_length is not None and encoded["input_ids"].shape[-1] > max_length:
        raise ValueError(
            f"Tokenized text length {encoded['input_ids'].shape[-1]} exceeds max_length={max_length}. "
            "No truncation is applied in the training pipeline."
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


def _parse_appworld_state_text(state_text: str) -> tuple[str | None, str, list[tuple[int, str, str]]]:
    embedded_system_prompt, remainder = _split_embedded_system_prompt(state_text)
    text = remainder.strip()
    query_marker = "[QUERY]"
    trace_marker = "[TRACE SO FAR]"
    if text.startswith(query_marker):
        text = text[len(query_marker) :].strip()
    trace_at = text.find(trace_marker)
    if trace_at >= 0:
        query = text[:trace_at].strip()
        trace_text = text[trace_at + len(trace_marker) :].strip()
    else:
        query = text.strip()
        trace_text = ""
    steps = [
        (
            int(match.group("index")),
            match.group("response").strip(),
            match.group("observation").strip(),
        )
        for match in STATE_STEP_RE.finditer(trace_text)
    ]
    return embedded_system_prompt, query, steps


def _observation_to_chat_content(observation: str) -> str:
    stripped = observation.strip()
    if stripped.startswith("Output:\n```"):
        return stripped
    return f"Output:\n```\n{stripped}\n```"


def _render_appworld_chat_history_prompt(tokenizer: Any, example: DecisionExample, prompt_profile: str) -> str:
    _, query, steps = _parse_appworld_state_text(example.state_text)
    if example.target_type == "answer":
        query += "\n\nRespond with the final answer only."
    messages = [dict(message) for message in get_initial_messages(prompt_profile)]
    messages.append({"role": "user", "content": query})
    for _, response, observation in steps:
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": _observation_to_chat_content(observation)})
    return _apply_chat_template(tokenizer, messages)


def _render_training_prompt(tokenizer: Any, example: DecisionExample, prompt_profile: str) -> str:
    if example.benchmark == "appworld" and uses_chat_history_prompt(prompt_profile):
        return _render_appworld_chat_history_prompt(tokenizer, example, prompt_profile)
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


def _target_suffix(example: DecisionExample) -> str:
    target = example.target_text
    if example.target_type == "code":
        if "```" not in target:
            target = f"```python\n{target.strip()}\n```"
    return target


def _append_eos_token_id(tokenizer: Any, token_ids: list[int]) -> list[int]:
    eos_id = getattr(tokenizer, "eos_token_id", None)
    if eos_id is None:
        return token_ids
    if not token_ids or int(token_ids[-1]) != int(eos_id):
        return token_ids + [int(eos_id)]
    return token_ids


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
    max_length: int | None,
) -> dict[str, Tensor]:
    pad_id = _ensure_padding_token(tokenizer)
    input_rows: list[list[int]] = []
    label_rows: list[list[int]] = []
    mask_rows: list[list[int]] = []
    for example in examples:
        prompt = _render_training_prompt(tokenizer, example, prompt_profile)
        target = _target_suffix(example)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = _append_eos_token_id(
            tokenizer,
            list(tokenizer(target, add_special_tokens=False)["input_ids"]),
        )
        full_ids = prompt_ids + target_ids
        labels = [-100] * len(prompt_ids) + list(target_ids)
        if max_length is not None and len(full_ids) > max_length:
            raise ValueError(
                f"Training prompt+target length {len(full_ids)} exceeds max_query_tokens={max_length}. "
                "No prompt or target truncation is applied."
            )
        if not full_ids:
            full_ids = [pad_id]
            labels = [-100]
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
    max_query_tokens: int | None = None,
    support_representations: Tensor | None = None,
    state_representations: Tensor | None = None,
) -> dict[str, Tensor]:
    batch: dict[str, Tensor] = {}
    if support_representations is None:
        support = _tokenize_texts(
            tokenizer,
            [record.experience_text for record in support_records],
            max_length=config.encoder.max_experience_tokens,
        )
        batch.update(
            {
                "support_input_ids": support["input_ids"],
                "support_attention_mask": support["attention_mask"],
            }
        )
    else:
        if support_representations.dim() != 2:
            raise ValueError("support_representations must have shape [support, hidden]")
        if support_representations.shape[0] != len(support_records):
            raise ValueError("support_representations row count must match support_records")
        batch["support_representations"] = support_representations.to(torch.float32)
    if state_representations is None:
        state = _tokenize_texts(
            tokenizer,
            [example.state_text for example in examples],
            max_length=config.encoder.max_state_tokens,
        )
        batch.update(
            {
                "state_input_ids": state["input_ids"],
                "state_attention_mask": state["attention_mask"],
            }
        )
    else:
        if state_representations.dim() != 2:
            raise ValueError("state_representations must have shape [batch, hidden]")
        if state_representations.shape[0] != len(examples):
            raise ValueError("state_representations row count must match examples")
        batch["state_representations"] = state_representations.to(torch.float32)
    query = _build_query_tensors(
        tokenizer,
        examples,
        prompt_profile=config.benchmark.prompt_profile,
        max_length=max_query_tokens,
    )
    batch.update(query)
    return batch
