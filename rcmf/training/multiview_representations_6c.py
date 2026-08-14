from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import math
from typing import Any

import torch
from torch import Tensor

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.schemas import DecisionExample
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    render_state_representation_text,
)
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.utils.serialization import sha256_text


MULTIVIEW_CACHE_VERSION = "span_aware_frozen_qwen_multiview_6c_v1"
STATE_VIEW_NAMES = (
    "full_prompt_global",
    "current_task_goal",
    "current_task_history",
    "latest_user_output",
    "generation_boundary",
)
TRANSITION_VIEW_NAMES = (
    "source_task_goal",
    "pre_action_state",
    "complete_action",
    "post_action_observation",
    "full_transition_global",
)
LAYER_CANDIDATES = ("final_layer", "mean_final_four_layers")
POOLING_RULES = ("token_mean", "final_token")


def _ordered_message_content_spans(
    rendered: str, messages: Sequence[Mapping[str, str]]
) -> list[dict[str, Any]]:
    cursor = 0
    output = []
    for index, message in enumerate(messages):
        content = str(message.get("content", ""))
        if not content:
            raise ValueError(f"Message {index} has empty content")
        start = rendered.find(content, cursor)
        if start < 0:
            raise ValueError(f"Message {index} content is absent from rendered prompt")
        end = start + len(content)
        output.append(
            {
                "message_index": index,
                "role": str(message.get("role", "")),
                "char_start": start,
                "char_end": end,
                "source_text_sha256": sha256_text(content),
            }
        )
        cursor = end
    return output


def query_state_text_and_char_spans(
    tokenizer: Any,
    example: DecisionExample,
    prompt_profile: str,
) -> tuple[str, dict[str, tuple[int, int]], dict[str, Any]]:
    if example.benchmark != "appworld":
        raise ValueError("EXP-019 multi-view states must be AppWorld examples")
    messages = _appworld_messages_from_example(example, prompt_profile)
    rendered = render_state_representation_text(tokenizer, example, prompt_profile)
    message_spans = _ordered_message_content_spans(rendered, messages)
    initial_count = int(
        appworld_renderer_metadata(prompt_profile)["initial_message_count"]
    )
    current = message_spans[initial_count:]
    if not current or current[0]["role"] != "user":
        raise ValueError("Canonical current-task conversation does not start with user")
    latest_user = next(
        (span for span in reversed(current) if span["role"] == "user"), None
    )
    if latest_user is None:
        raise ValueError("Canonical current-task conversation has no user message")
    boundary_encoding = tokenizer(
        rendered,
        add_special_tokens=False,
        truncation=False,
        return_offsets_mapping=True,
    )
    boundary_offsets = boundary_encoding["offset_mapping"]
    if isinstance(boundary_offsets, Tensor):
        boundary_offsets = boundary_offsets.tolist()
    if boundary_offsets and isinstance(boundary_offsets[0], list) and boundary_offsets[0] and isinstance(boundary_offsets[0][0], list):
        boundary_offsets = boundary_offsets[0]
    nonempty_offsets = [
        (int(start), int(end))
        for start, end in boundary_offsets
        if int(end) > int(start)
    ]
    if not nonempty_offsets:
        raise ValueError("Canonical prompt has no token with a character extent")
    generation_boundary = nonempty_offsets[-1]
    spans = {
        "full_prompt_global": (0, len(rendered)),
        "current_task_goal": (
            int(current[0]["char_start"]),
            int(current[0]["char_end"]),
        ),
        # This cumulative local view is nonempty even for the first decision step.
        "current_task_history": (
            int(current[0]["char_start"]),
            int(current[-1]["char_end"]),
        ),
        "latest_user_output": (
            int(latest_user["char_start"]),
            int(latest_user["char_end"]),
        ),
        "generation_boundary": generation_boundary,
    }
    metadata = {
        "message_count": len(messages),
        "initial_demo_message_count": initial_count,
        "current_task_message_count": len(current),
        "current_task_history_definition": (
            "cumulative current-task conversation from goal start through latest "
            "available user observation; full-demo messages are excluded"
        ),
        "generation_boundary_definition": (
            "complete character extent of the tokenizer's final nonempty prompt token"
        ),
        "target_action_accessed": False,
        "future_observation_accessed": False,
        "message_content_spans": message_spans,
    }
    return rendered, spans, metadata


def _find_after(text: str, value: str, cursor: int, label: str) -> tuple[int, int]:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Transition span {label} is empty")
    start = text.find(normalized, cursor)
    if start < 0:
        raise ValueError(f"Transition span {label} is absent from canonical text")
    return start, start + len(normalized)


def transition_text_and_char_spans(
    transition: Mapping[str, Any],
) -> tuple[str, dict[str, tuple[int, int]], dict[str, Any]]:
    text = transition_teacher_section(dict(transition))
    cursor = 0
    spans: dict[str, tuple[int, int]] = {}
    fields = (
        ("source_task_goal", "source_task_goal"),
        ("pre_action_state", "canonical_pre_action_state"),
        ("complete_action", "complete_action"),
        ("post_action_observation", "complete_post_action_observation"),
    )
    for view, field in fields:
        start, end = _find_after(text, str(transition[field]), cursor, view)
        spans[view] = (start, end)
        cursor = end
    spans["full_transition_global"] = (0, len(text))
    return (
        text,
        spans,
        {
            "teacher_section_sha256": sha256_text(text),
            "transition_content_sha256": str(transition["transition_content_sha256"]),
        },
    )


def tokenize_and_validate_char_spans(
    tokenizer: Any,
    text: str,
    char_spans: Mapping[str, tuple[int, int]],
) -> tuple[Tensor, Tensor, dict[str, dict[str, Any]]]:
    encoded = tokenizer(
        text,
        add_special_tokens=False,
        truncation=False,
        return_offsets_mapping=True,
        return_tensors="pt",
    )
    input_ids = encoded["input_ids"].to(torch.long)
    attention_mask = encoded.get("attention_mask", torch.ones_like(input_ids)).to(
        torch.long
    )
    offsets_value = encoded["offset_mapping"]
    offsets = (
        offsets_value[0].tolist()
        if isinstance(offsets_value, Tensor)
        else list(offsets_value[0])
    )
    if input_ids.shape[0] != 1 or input_ids.shape[1] != len(offsets):
        raise ValueError("Tokenizer IDs and offset mapping differ")
    rows: dict[str, dict[str, Any]] = {}
    for name, (char_start, char_end) in char_spans.items():
        if not (0 <= int(char_start) < int(char_end) <= len(text)):
            raise ValueError(f"Invalid character span for {name}: {(char_start, char_end)}")
        selected = [
            index
            for index, (start, end) in enumerate(offsets)
            if int(end) > int(char_start) and int(start) < int(char_end)
        ]
        if not selected or selected != list(range(selected[0], selected[-1] + 1)):
            raise ValueError(f"Token span for {name} is empty or non-contiguous")
        token_start = selected[0]
        token_end = selected[-1] + 1
        covered_start = int(offsets[token_start][0])
        covered_end = int(offsets[token_end - 1][1])
        if covered_start != int(char_start) or covered_end != int(char_end):
            raise ValueError(
                f"Token boundary differs for {name}: chars {(char_start, char_end)} "
                f"covered by {(covered_start, covered_end)}"
            )
        source = text[int(char_start) : int(char_end)]
        token_ids = input_ids[0, token_start:token_end].tolist()
        decoded = tokenizer.decode(
            token_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        roundtrip = tokenizer(
            decoded, add_special_tokens=False, truncation=False
        )["input_ids"]
        if list(roundtrip) != list(token_ids):
            raise ValueError(f"Decoded token roundtrip differs for {name}")
        rows[name] = {
            "char_start": int(char_start),
            "char_end": int(char_end),
            "token_start": token_start,
            "token_end": token_end,
            "token_count": token_end - token_start,
            "source_text_sha256": sha256_text(source),
            "decoded_text_sha256": sha256_text(decoded),
            "decoded_roundtrip_token_sha256": hashlib.sha256(
                torch.tensor(token_ids, dtype=torch.long).numpy().tobytes()
            ).hexdigest(),
            "decoded_roundtrip_matches": True,
            "decoded_text_exact_match": decoded == source,
        }
    return input_ids, attention_mask, rows


@torch.no_grad()
def frozen_qwen_span_readouts(
    *,
    model: Any,
    input_ids: Tensor,
    attention_mask: Tensor,
    span_rows: Mapping[str, Mapping[str, Any]],
    device: torch.device,
) -> dict[str, dict[str, Tensor]]:
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise RuntimeError("Frozen span readout received trainable Qwen parameters")
    maximum = getattr(getattr(model, "config", None), "max_position_embeddings", None)
    if maximum is not None and input_ids.shape[1] > int(maximum):
        raise ValueError(
            f"Span source has {input_ids.shape[1]} tokens, exceeding context {maximum}; "
            "no truncation is allowed"
        )
    outputs = model(
        input_ids=input_ids.to(device),
        attention_mask=attention_mask.to(device),
        output_hidden_states=True,
        use_cache=False,
        return_dict=True,
    )
    hidden_states = outputs.hidden_states
    if hidden_states is None or len(hidden_states) < 4:
        raise RuntimeError("Qwen did not return enough hidden layers")
    layers = {
        "final_layer": hidden_states[-1][0].to(torch.float32),
        "mean_final_four_layers": torch.stack(
            [value[0].to(torch.float32) for value in hidden_states[-4:]], dim=0
        ).mean(dim=0),
    }
    output: dict[str, dict[str, Tensor]] = {}
    for layer_name, hidden in layers.items():
        output[layer_name] = {}
        for view_name, row in span_rows.items():
            start = int(row["token_start"])
            end = int(row["token_end"])
            span = hidden[start:end]
            if span.numel() == 0:
                raise RuntimeError(f"Hidden span {view_name} is empty")
            output[layer_name][view_name] = torch.stack(
                (span.mean(dim=0), span[-1]), dim=0
            ).detach().cpu()
    return output


def flatten_multiview_readouts(
    rows: Sequence[Mapping[str, Any]],
    *,
    layer: str,
    view_names: Sequence[str],
) -> Tensor:
    if layer not in LAYER_CANDIDATES:
        raise ValueError(f"Unknown layer candidate: {layer}")
    tensors = []
    for row in rows:
        layer_values = row["readouts"][layer]
        tensors.append(torch.cat([layer_values[name] for name in view_names], dim=0))
    return torch.stack(tensors).to(torch.float32)


def _effective_rank(values: Tensor) -> dict[str, Any]:
    matrix = values.to(torch.float64)
    centered = matrix - matrix.mean(dim=0, keepdim=True)
    singular = torch.linalg.svdvals(centered)
    total = singular.sum()
    probabilities = singular / total.clamp_min(torch.finfo(singular.dtype).eps)
    entropy = -(probabilities * probabilities.clamp_min(1.0e-30).log()).sum()
    return {
        "centered_effective_rank": float(entropy.exp()),
        "centered_rank": int(torch.linalg.matrix_rank(centered)),
        "singular_values": [float(value) for value in singular.cpu().tolist()],
    }


def _leave_one_out_nearest_neighbor_accuracy(
    values: Tensor, labels: Sequence[str]
) -> float | None:
    if len(values) < 2 or len(set(labels)) < 2:
        return None
    normalized = torch.nn.functional.normalize(values.to(torch.float64), dim=-1)
    similarity = normalized @ normalized.T
    similarity.fill_diagonal_(-float("inf"))
    nearest = similarity.argmax(dim=1).tolist()
    return sum(labels[index] == labels[other] for index, other in enumerate(nearest)) / len(labels)


def multiview_geometry(
    representations: Tensor,
    *,
    ordered_ids: Sequence[str],
    view_names: Sequence[str],
    metadata_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if representations.ndim != 3:
        raise ValueError("Multi-view geometry expects [items, views, hidden]")
    output = {}
    for view_index, view_name in enumerate(view_names):
        values = representations[:, view_index]
        norms = values.norm(dim=-1)
        normalized = torch.nn.functional.normalize(values.to(torch.float64), dim=-1)
        cosine = normalized @ normalized.T
        upper = cosine[torch.triu_indices(len(values), len(values), offset=1).unbind()]
        task_labels = [str(metadata_by_id[value].get("task_label", "unknown")) for value in ordered_ids]
        app_labels = [str(metadata_by_id[value].get("app_label", "unknown")) for value in ordered_ids]
        step_labels = [str(metadata_by_id[value].get("step_bucket", "unknown")) for value in ordered_ids]
        output[view_name] = {
            "norm": {
                "min": float(norms.min()),
                "mean": float(norms.mean()),
                "std": float(norms.std(unbiased=False)),
                "max": float(norms.max()),
            },
            "pairwise_cosine": {
                "count": int(upper.numel()),
                "mean": float(upper.mean()) if upper.numel() else None,
                "std": float(upper.std(unbiased=False)) if upper.numel() else None,
                "min": float(upper.min()) if upper.numel() else None,
                "max": float(upper.max()) if upper.numel() else None,
            },
            **_effective_rank(values),
            "predictability_probe": {
                "method": "leave_one_out_cosine_nearest_neighbor",
                "task_accuracy": _leave_one_out_nearest_neighbor_accuracy(values, task_labels),
                "app_accuracy": _leave_one_out_nearest_neighbor_accuracy(values, app_labels),
                "step_bucket_accuracy": _leave_one_out_nearest_neighbor_accuracy(values, step_labels),
                "task_majority": max(Counter(task_labels).values()) / len(task_labels),
                "app_majority": max(Counter(app_labels).values()) / len(app_labels),
                "step_bucket_majority": max(Counter(step_labels).values()) / len(step_labels),
            },
        }
    return output


def readout_payload_hash(readouts: Mapping[str, Mapping[str, Tensor]]) -> str:
    state = {
        f"{layer}.{view}": tensor.detach().cpu()
        for layer, layer_rows in readouts.items()
        for view, tensor in layer_rows.items()
    }
    return tensor_state_sha256(state)
