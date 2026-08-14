from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import torch

from rcmf.schemas import DecisionExample
from rcmf.training.cross_encoder_6c import (
    CROSS_ENCODER_VIEW_NAMES,
    CrossEncoderResidualHead,
    controlled_feature_matrix,
    cross_encoder_control_sources,
    cross_encoder_prompt_and_char_spans,
    frozen_qwen_cross_encoder_readouts,
)
from rcmf.training.multiview_representations_6c import (
    tokenize_and_validate_char_spans,
)
from scripts.run_cross_encoder_interaction_6c import _prediction_rows


class CharacterTokenizer:
    def apply_chat_template(
        self, messages, *, tokenize: bool, add_generation_prompt: bool
    ) -> str:
        assert not tokenize
        rendered = "".join(
            f"<{message['role']}>{message['content']}</{message['role']}>"
            for message in messages
        )
        return rendered + ("<assistant>" if add_generation_prompt else "")

    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool = False,
        truncation: bool = False,
        return_offsets_mapping: bool = False,
        return_tensors: str | None = None,
    ):
        del add_special_tokens, truncation
        output = {
            "input_ids": [ord(value) for value in text],
            "attention_mask": [1] * len(text),
        }
        if return_offsets_mapping:
            output["offset_mapping"] = [
                (index, index + 1) for index in range(len(text))
            ]
        if return_tensors == "pt":
            output = {
                key: torch.tensor([value], dtype=torch.long)
                for key, value in output.items()
            }
        return output

    def decode(self, ids, **kwargs) -> str:
        del kwargs
        return "".join(chr(int(value)) for value in ids)


class FrozenBase(torch.nn.Module):
    def forward(self, *, input_ids, attention_mask, **kwargs):
        del attention_mask, kwargs
        hidden = input_ids.to(torch.float32).unsqueeze(-1).repeat(1, 1, 4)
        return SimpleNamespace(last_hidden_state=hidden)


class FrozenCausalLM(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.weight = torch.nn.Parameter(torch.ones(()), requires_grad=False)
        self.model = FrozenBase()
        self.config = SimpleNamespace(max_position_embeddings=100000)


def _example() -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id="appworld:trace:query-task",
        step_id=2,
        state_text=(
            "[QUERY]\nDo the current task.\n[TRACE SO FAR]\n"
            "Step 1 - Response:\n```python\nprint('x')\n```\n"
            "Step 1 - Observation:\nOutput:\n```\nx\n```"
        ),
        target_text="NEVER_INCLUDE_THIS_TARGET_ACTION",
        target_type="code",
        candidate_memory_ids=None,
    )


def _transition() -> dict[str, object]:
    return {
        "source_task_goal": "Find a note.",
        "canonical_pre_action_state": "State before action.",
        "complete_action": "```python\napis.simple_note.search_notes(query='x')\n```",
        "complete_post_action_observation": "found x",
    }


def test_cross_encoder_prompt_has_exact_views_and_no_target_action() -> None:
    tokenizer = CharacterTokenizer()
    rendered, spans, metadata = cross_encoder_prompt_and_char_spans(
        tokenizer, _example(), _transition(), "full_demo"
    )
    assert set(spans) == set(CROSS_ENCODER_VIEW_NAMES)
    assert rendered.count("[DECISION TRANSITION MEMORY]") == 1
    assert "NEVER_INCLUDE_THIS_TARGET_ACTION" not in rendered
    assert not metadata["target_action_accessed"]
    input_ids, attention_mask, rows = tokenize_and_validate_char_spans(
        tokenizer, rendered, spans
    )
    values = frozen_qwen_cross_encoder_readouts(
        model=FrozenCausalLM(),
        input_ids=input_ids,
        attention_mask=attention_mask,
        span_rows=rows,
        device=torch.device("cpu"),
    )
    assert values.shape == (3, 4)


def _rows() -> list[dict[str, str]]:
    return [
        {
            "pair_id": f"{state}:{transition}",
            "state_example_id": state,
            "transition_id": transition,
        }
        for state in ("s1", "s2", "s3")
        for transition in ("m1", "m2", "m3")
    ]


def test_cross_encoder_controls_change_only_the_requested_pair_axis() -> None:
    rows = _rows()
    controls = cross_encoder_control_sources(rows, seed=19)
    by_pair = {row["pair_id"]: row for row in rows}
    for row, pair_id in zip(rows, controls["shuffled_state"]):
        source = by_pair[str(pair_id)]
        assert source["state_example_id"] != row["state_example_id"]
        assert source["transition_id"] == row["transition_id"]
    for row, pair_id in zip(rows, controls["shuffled_transition"]):
        source = by_pair[str(pair_id)]
        assert source["state_example_id"] == row["state_example_id"]
        assert source["transition_id"] != row["transition_id"]
    for row, pair_id in zip(rows, controls["both_shuffled"]):
        source = by_pair[str(pair_id)]
        assert source["state_example_id"] != row["state_example_id"]
        assert source["transition_id"] != row["transition_id"]


def test_cross_encoder_zero_interaction_and_head_gradient() -> None:
    rows = _rows()
    feature_by_pair = {
        row["pair_id"]: torch.full((6,), float(index))
        for index, row in enumerate(rows)
    }
    controls = cross_encoder_control_sources(rows, seed=23)
    assert (
        controlled_feature_matrix(
            rows=rows,
            feature_by_pair=feature_by_pair,
            control_sources=controls,
            control="zero_interaction",
        )
        is None
    )
    model = CrossEncoderResidualHead(6, hidden_dim=8, dropout=0.0)
    loss = model(torch.stack(list(feature_by_pair.values()))).square().mean()
    loss.backward()
    assert all(parameter.grad is not None for parameter in model.parameters())


def test_cross_encoder_runner_stays_inside_representation_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_cross_encoder_interaction_6c.py").read_text(
        encoding="utf-8"
    )
    assert "forward_train(" not in source
    assert "generate(" not in source
    assert "AdditiveTokenMemoryInjector" not in source
    assert "signed_selector" not in source
    assert "target_text" not in source


def test_correct_prediction_does_not_require_unused_shuffle_candidates() -> None:
    row = {
        "pair_id": "only-pair",
        "state_example_id": "only-state",
        "state_task_id": "task",
        "transition_id": "only-transition",
        "transition_parent_id": "parent",
        "cell": "validation",
        "utility_category": "neutral",
        "text_utility": 0.0,
    }
    model = CrossEncoderResidualHead(6, hidden_dim=8, dropout=0.0).eval()
    predictions = _prediction_rows(
        model=model,
        rows=[row],
        feature_by_pair={"only-pair": torch.zeros(6)},
        normalization={"mean": torch.zeros(6), "std": torch.ones(6)},
        base_scores={"only-pair": 0.0},
        control="correct",
        seed=1,
        device=torch.device("cpu"),
    )
    assert predictions[0]["control_source_pair_id"] == "only-pair"
