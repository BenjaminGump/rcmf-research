from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import random
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.schemas import DecisionExample
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
)
from rcmf.training.interaction_representation_6c import interaction_objective
from rcmf.training.multiview_representations_6c import (
    _ordered_message_content_spans,
    tokenize_and_validate_char_spans,
)
from rcmf.training.state_conditioned_transition_6b import DenseTower
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import sha256_text


CROSS_ENCODER_CACHE_VERSION = "prompt_transition_cross_encoder_cache_6c_v1"
CROSS_ENCODER_MODEL_VERSION = "prompt_transition_residual_head_6c_v1"
CROSS_ENCODER_VIEW_NAMES = (
    "generation_boundary",
    "transition_section_mean",
    "current_task_span_mean",
)


def _last_nonempty_token_extent(tokenizer: Any, rendered: str) -> tuple[int, int]:
    encoded = tokenizer(
        rendered,
        add_special_tokens=False,
        truncation=False,
        return_offsets_mapping=True,
    )
    offsets = encoded["offset_mapping"]
    if isinstance(offsets, Tensor):
        offsets = offsets.tolist()
    if offsets and isinstance(offsets[0], list) and offsets[0] and isinstance(offsets[0][0], list):
        offsets = offsets[0]
    nonempty = [(int(start), int(end)) for start, end in offsets if int(end) > int(start)]
    if not nonempty:
        raise ValueError("Cross-encoder prompt has no token with a character extent")
    return nonempty[-1]


def cross_encoder_prompt_and_char_spans(
    tokenizer: Any,
    example: DecisionExample,
    transition: Mapping[str, Any],
    prompt_profile: str,
) -> tuple[str, dict[str, tuple[int, int]], dict[str, Any]]:
    """Render the exact EXP-017 teacher prompt without accessing the target action."""
    if example.benchmark != "appworld":
        raise ValueError("EXP-019 cross-encoder inputs must be AppWorld examples")
    base_messages = _appworld_messages_from_example(example, prompt_profile)
    teacher_messages = messages_with_transition_memory(
        base_messages, transition, prompt_profile
    )
    rendered, prompt_metadata = _render_prompt_with_metadata(
        tokenizer, teacher_messages, prompt_profile
    )
    section = transition_teacher_section(dict(transition))
    if rendered.count(section) != 1:
        raise ValueError("Cross-encoder prompt must contain exactly one transition section")
    section_start = rendered.index(section)
    section_end = section_start + len(section)

    message_spans = _ordered_message_content_spans(rendered, teacher_messages)
    initial_count = int(
        appworld_renderer_metadata(prompt_profile)["initial_message_count"]
    )
    current_messages = message_spans[initial_count:]
    if not current_messages or current_messages[0]["role"] != "user":
        raise ValueError("Cross-encoder current task does not start with a user message")
    original_goal = str(base_messages[initial_count]["content"])
    marker = "[CURRENT APPWORLD STATE START]\n"
    marker_start = rendered.find(marker, section_end)
    if marker_start < 0:
        raise ValueError("Cross-encoder prompt is missing the current-state marker")
    current_start = rendered.find(original_goal, marker_start + len(marker))
    if current_start < 0:
        raise ValueError("Original current-task goal is absent from teacher prompt")
    current_end = int(current_messages[-1]["char_end"])
    if not (section_end <= current_start < current_end):
        raise ValueError("Transition and current-task spans overlap or are out of order")

    spans = {
        "generation_boundary": _last_nonempty_token_extent(tokenizer, rendered),
        "transition_section_mean": (section_start, section_end),
        "current_task_span_mean": (current_start, current_end),
    }
    return rendered, spans, {
        "prompt_metadata": prompt_metadata,
        "message_content_spans": message_spans,
        "transition_section_sha256": sha256_text(section),
        "current_task_goal_sha256": sha256_text(original_goal),
        "target_action_accessed": False,
        "future_observation_accessed": False,
        "raw_transition_is_teacher_side_input_only": True,
    }


@torch.no_grad()
def frozen_qwen_cross_encoder_readouts(
    *,
    model: Any,
    input_ids: Tensor,
    attention_mask: Tensor,
    span_rows: Mapping[str, Mapping[str, Any]],
    device: torch.device,
) -> Tensor:
    """Return the three prescribed final-layer views without materializing all layers."""
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise RuntimeError("Cross-encoder readout received trainable Qwen parameters")
    maximum = getattr(getattr(model, "config", None), "max_position_embeddings", None)
    if maximum is not None and input_ids.shape[1] > int(maximum):
        raise ValueError(
            f"Cross-encoder prompt has {input_ids.shape[1]} tokens, exceeding "
            f"context {maximum}; no truncation is allowed"
        )
    base_model = getattr(model, "model", None)
    if base_model is None:
        raise RuntimeError("Qwen causal LM does not expose its frozen base model")
    outputs = base_model(
        input_ids=input_ids.to(device),
        attention_mask=attention_mask.to(device),
        use_cache=False,
        return_dict=True,
    )
    hidden = getattr(outputs, "last_hidden_state", None)
    if hidden is None:
        hidden = outputs[0]
    hidden = hidden[0].to(torch.float32)
    values = []
    for name in CROSS_ENCODER_VIEW_NAMES:
        row = span_rows[name]
        span = hidden[int(row["token_start"]) : int(row["token_end"])]
        if span.numel() == 0:
            raise RuntimeError(f"Cross-encoder hidden span is empty: {name}")
        values.append(span[-1] if name == "generation_boundary" else span.mean(dim=0))
    return torch.stack(values).detach().cpu()


def cross_encoder_tensor_hash(values: Tensor) -> str:
    contiguous = values.detach().to(torch.float32).cpu().contiguous()
    return hashlib.sha256(contiguous.numpy().tobytes()).hexdigest()


class CrossEncoderResidualHead(nn.Module):
    def __init__(
        self,
        input_dim: int,
        *,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.head = DenseTower(
            int(input_dim), 1, hidden_dim=int(hidden_dim), dropout=float(dropout)
        )

    def forward(self, values: Tensor) -> Tensor:
        return self.head(values).squeeze(-1)


def feature_normalization(values: Tensor) -> dict[str, Tensor]:
    values = values.to(torch.float32)
    return {
        "mean": values.mean(dim=0),
        "std": values.std(dim=0, unbiased=False).clamp_min(1.0e-6),
    }


def normalize_features(values: Tensor, normalization: Mapping[str, Tensor]) -> Tensor:
    return (values.to(torch.float32) - normalization["mean"]) / normalization["std"]


def exact_training_base_scores(
    rows: Sequence[Mapping[str, Any]], decomposition: Mapping[str, Any]
) -> Tensor:
    return torch.tensor(
        [
            float(decomposition["mu"])
            + float(decomposition["state_effects"][str(row["state_example_id"])])
            + float(decomposition["transition_effects"][str(row["transition_id"])])
            for row in rows
        ],
        dtype=torch.float32,
    )


def train_cross_encoder_head(
    *,
    model: CrossEncoderResidualHead,
    rows: Sequence[Mapping[str, Any]],
    features: Tensor,
    base_scores: Tensor,
    decomposition: Mapping[str, Any],
    settings: Mapping[str, Any],
    epochs: int,
    seed: int,
    device: torch.device,
) -> dict[str, Any]:
    torch.manual_seed(int(seed))
    random.seed(int(seed))
    model.to(device).train()
    normalized = features.to(device=device, dtype=torch.float32)
    base = base_scores.to(device=device, dtype=torch.float32)
    utility = torch.tensor(
        [float(row["text_utility"]) for row in rows],
        device=device,
        dtype=torch.float32,
    )
    residual = utility - base
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[str(row["state_example_id"])].append(index)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(settings["learning_rate"]),
        weight_decay=float(settings["weight_decay"]),
    )
    history = []
    for epoch in range(1, int(epochs) + 1):
        interaction = model(normalized)
        score = base + interaction
        loss, components = interaction_objective(
            score=score,
            interaction=interaction,
            utility=utility,
            residual_target=residual,
            state_groups=[groups[key] for key in sorted(groups)],
            residual_huber_delta=float(settings["residual_huber_delta"]),
            utility_huber_delta=float(settings["utility_huber_delta"]),
            teacher_temperature=float(settings["teacher_temperature"]),
            student_temperature=float(settings["student_temperature"]),
            pair_gap_threshold=float(settings["pair_gap_threshold"]),
            pair_gap_clip=float(settings["pair_gap_clip"]),
            loss_weights=settings["losses"],
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu()
        )
        optimizer.step()
        if epoch == 1 or epoch % 10 == 0 or epoch == int(epochs):
            history.append(
                {
                    "epoch": epoch,
                    "total_loss": float(loss.detach().cpu()),
                    **{
                        key: float(value.detach().cpu())
                        for key, value in components.items()
                    },
                    "gradient_norm": gradient_norm,
                }
            )
    model.eval()
    return {
        "format": f"{CROSS_ENCODER_MODEL_VERSION}_training",
        "epochs": int(epochs),
        "optimizer_updates": int(epochs),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


def cross_encoder_control_sources(
    rows: Sequence[Mapping[str, Any]], *, seed: int
) -> dict[str, list[str | None]]:
    """Map each target row to an existing pair representation for causal controls."""
    by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_transition: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_state[str(row["state_example_id"])].append(row)
        by_transition[str(row["transition_id"])].append(row)

    def ordered(candidates: Sequence[Mapping[str, Any]], namespace: str) -> list[Mapping[str, Any]]:
        return sorted(
            candidates,
            key=lambda row: (
                hashlib.sha256(
                    f"{seed}:{namespace}:{row['pair_id']}".encode("utf-8")
                ).hexdigest(),
                str(row["pair_id"]),
            ),
        )

    controls: dict[str, list[str | None]] = {
        name: []
        for name in (
            "correct",
            "shuffled_state",
            "shuffled_transition",
            "both_shuffled",
            "mean_state",
            "mean_transition",
            "zero_interaction",
        )
    }
    all_rows = list(rows)
    for row in rows:
        state = str(row["state_example_id"])
        transition = str(row["transition_id"])
        state_candidates = [
            value
            for value in by_transition[transition]
            if str(value["state_example_id"]) != state
        ]
        transition_candidates = [
            value
            for value in by_state[state]
            if str(value["transition_id"]) != transition
        ]
        both_candidates = [
            value
            for value in all_rows
            if str(value["state_example_id"]) != state
            and str(value["transition_id"]) != transition
        ]
        if not state_candidates or not transition_candidates or not both_candidates:
            raise ValueError("Cross-encoder control cannot construct a strict shuffle")
        controls["correct"].append(str(row["pair_id"]))
        controls["shuffled_state"].append(
            str(ordered(state_candidates, f"state:{row['pair_id']}")[0]["pair_id"])
        )
        controls["shuffled_transition"].append(
            str(
                ordered(transition_candidates, f"transition:{row['pair_id']}")[0][
                    "pair_id"
                ]
            )
        )
        controls["both_shuffled"].append(
            str(ordered(both_candidates, f"both:{row['pair_id']}")[0]["pair_id"])
        )
        controls["mean_state"].append(None)
        controls["mean_transition"].append(None)
        controls["zero_interaction"].append(None)
    return controls


def controlled_feature_matrix(
    *,
    rows: Sequence[Mapping[str, Any]],
    feature_by_pair: Mapping[str, Tensor],
    control_sources: Mapping[str, Sequence[str | None]],
    control: str,
) -> Tensor | None:
    if control == "zero_interaction":
        return None
    if control in {"correct", "shuffled_state", "shuffled_transition", "both_shuffled"}:
        return torch.stack(
            [feature_by_pair[str(pair_id)] for pair_id in control_sources[control]]
        )
    values = []
    for row in rows:
        state = str(row["state_example_id"])
        transition = str(row["transition_id"])
        if control == "mean_state":
            candidates = [
                feature_by_pair[str(candidate["pair_id"])]
                for candidate in rows
                if str(candidate["transition_id"]) == transition
            ]
        elif control == "mean_transition":
            candidates = [
                feature_by_pair[str(candidate["pair_id"])]
                for candidate in rows
                if str(candidate["state_example_id"]) == state
            ]
        else:
            raise ValueError(f"Unknown cross-encoder control: {control}")
        values.append(torch.stack(candidates).mean(dim=0))
    return torch.stack(values)

