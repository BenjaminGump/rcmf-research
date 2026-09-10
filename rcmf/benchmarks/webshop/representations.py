from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import torch
from torch import Tensor

from rcmf.pipeline.portable_v2.schemas import DecisionStateRecord, TransitionRecord

STATE_VIEW_NAMES = (
    "instruction",
    "trajectory_history",
    "current_observation",
    "available_actions",
    "full_state",
)
TRANSITION_VIEW_NAMES = (
    "goal",
    "pre_action_state",
    "complete_action",
    "post_action_observation",
    "full_transition",
)
POOLING_RULES = ("token_mean", "final_token")
REPRESENTATION_FORMAT = "webshop_frozen_qwen_structured_multiview_v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _structured_text(
    sections: Sequence[tuple[str, str]], *, full_name: str
) -> tuple[str, dict[str, tuple[int, int]]]:
    chunks: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for name, value in sections:
        if not value:
            value = "<EMPTY>"
        prefix = f"<{name}>\n"
        suffix = f"\n</{name}>\n"
        chunks.append(prefix)
        cursor += len(prefix)
        start = cursor
        chunks.append(value)
        cursor += len(value)
        spans[name] = (start, cursor)
        chunks.append(suffix)
        cursor += len(suffix)
    text = "".join(chunks)
    spans[full_name] = (0, len(text))
    return text, spans


def state_representation_text(
    state: DecisionStateRecord,
) -> tuple[str, dict[str, tuple[int, int]], Mapping[str, Any]]:
    history = _canonical_json(list(state.trajectory_prefix))
    available = _canonical_json(state.metadata.get("current_available_actions", {}))
    text, spans = _structured_text(
        (
            ("instruction", str(state.metadata["instruction"])),
            ("trajectory_history", history),
            (
                "current_observation",
                str(state.metadata.get("current_observation_raw", state.current_observation)),
            ),
            ("available_actions", available),
        ),
        full_name="full_state",
    )
    return (
        text,
        spans,
        {
            "format": REPRESENTATION_FORMAT,
            "state_id": state.state_id,
            "target_action_accessed": False,
            "future_observation_accessed": False,
        },
    )


def live_state_representation_text(
    *,
    task_id: str,
    instruction: str,
    history: Sequence[Mapping[str, Any]],
    observation: str,
    available_actions: Mapping[str, Any],
) -> tuple[str, dict[str, tuple[int, int]], Mapping[str, Any]]:
    text, spans = _structured_text(
        (
            ("instruction", instruction),
            ("trajectory_history", _canonical_json(list(history))),
            ("current_observation", observation),
            ("available_actions", _canonical_json(available_actions)),
        ),
        full_name="full_state",
    )
    return (
        text,
        spans,
        {
            "format": REPRESENTATION_FORMAT,
            "task_id": task_id,
            "target_action_accessed": False,
            "future_observation_accessed": False,
        },
    )


def transition_representation_text(
    transition: TransitionRecord,
) -> tuple[str, dict[str, tuple[int, int]], Mapping[str, Any]]:
    text, spans = _structured_text(
        (
            ("goal", transition.goal),
            ("pre_action_state", transition.pre_action_state),
            ("complete_action", transition.action),
            ("post_action_observation", transition.post_action_observation),
        ),
        full_name="full_transition",
    )
    return (
        text,
        spans,
        {
            "format": REPRESENTATION_FORMAT,
            "transition_id": transition.transition_id,
            "complete_transition": True,
        },
    )


def tokenize_spans(
    tokenizer: Any,
    text: str,
    spans: Mapping[str, tuple[int, int]],
) -> tuple[Tensor, Tensor, Mapping[str, Mapping[str, Any]]]:
    encoded = tokenizer(
        text,
        add_special_tokens=False,
        truncation=False,
        return_offsets_mapping=True,
        return_tensors="pt",
    )
    input_ids = encoded["input_ids"].to(torch.long)
    attention_mask = encoded.get("attention_mask", torch.ones_like(input_ids)).to(torch.long)
    offsets_value = encoded["offset_mapping"]
    offsets = (
        offsets_value[0].tolist() if isinstance(offsets_value, Tensor) else list(offsets_value[0])
    )
    rows: dict[str, Mapping[str, Any]] = {}
    for name, (char_start, char_end) in spans.items():
        selected = [
            index
            for index, (start, end) in enumerate(offsets)
            if int(end) > int(char_start) and int(start) < int(char_end)
        ]
        if not selected or selected != list(range(selected[0], selected[-1] + 1)):
            raise ValueError(f"WebShop representation span is empty/non-contiguous: {name}")
        start = selected[0]
        end = selected[-1] + 1
        rows[name] = {
            "token_start": start,
            "token_end": end,
            "token_count": end - start,
            "source_sha256": hashlib.sha256(text[char_start:char_end].encode("utf-8")).hexdigest(),
        }
    return input_ids, attention_mask, rows


@torch.no_grad()
def frozen_span_readouts(
    *,
    model: Any,
    input_ids: Tensor,
    attention_mask: Tensor,
    span_rows: Mapping[str, Mapping[str, Any]],
    view_names: Sequence[str],
    device: torch.device,
) -> Tensor:
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise RuntimeError("WebShop representations require a frozen Qwen model")
    maximum = getattr(getattr(model, "config", None), "max_position_embeddings", None)
    if maximum is not None and int(input_ids.shape[1]) > int(maximum):
        raise ValueError("WebShop representation exceeds the frozen model context")
    outputs = model(
        input_ids=input_ids.to(device),
        attention_mask=attention_mask.to(device),
        output_hidden_states=True,
        use_cache=False,
        return_dict=True,
    )
    hidden = outputs.hidden_states[-1][0].to(torch.float32)
    rows = []
    for name in view_names:
        span = hidden[int(span_rows[name]["token_start"]) : int(span_rows[name]["token_end"])]
        if span.numel() == 0:
            raise RuntimeError(f"WebShop hidden-state span is empty: {name}")
        rows.extend((span.mean(dim=0), span[-1]))
    return torch.stack(rows, dim=0).detach().cpu()


def encode_structured_text(
    *,
    model: Any,
    tokenizer: Any,
    text: str,
    spans: Mapping[str, tuple[int, int]],
    view_names: Sequence[str],
    device: torch.device,
) -> tuple[Tensor, Mapping[str, Mapping[str, Any]]]:
    input_ids, attention_mask, span_rows = tokenize_spans(tokenizer, text, spans)
    values = frozen_span_readouts(
        model=model,
        input_ids=input_ids,
        attention_mask=attention_mask,
        span_rows=span_rows,
        view_names=view_names,
        device=device,
    )
    return values, span_rows
