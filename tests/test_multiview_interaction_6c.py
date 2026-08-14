from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from rcmf.schemas import DecisionExample
from rcmf.training.multiview_models_6c import (
    MultiViewInteractionPredictor,
    StructuredPairFeatureBuilder,
    ViewSetMainEffectHeads,
)
from rcmf.training.multiview_representations_6c import (
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    frozen_qwen_span_readouts,
    query_state_text_and_char_spans,
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)


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
        ids = [ord(value) for value in text]
        output = {"input_ids": ids, "attention_mask": [1] * len(ids)}
        if return_offsets_mapping:
            output["offset_mapping"] = [(index, index + 1) for index in range(len(ids))]
        if return_tensors == "pt":
            output = {
                key: torch.tensor([value], dtype=torch.long)
                for key, value in output.items()
            }
        return output

    def decode(self, ids, **kwargs) -> str:
        del kwargs
        return "".join(chr(int(value)) for value in ids)


class FrozenToyModel(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.weight = torch.nn.Parameter(torch.ones(()), requires_grad=False)
        self.config = SimpleNamespace(max_position_embeddings=10000)

    def forward(self, *, input_ids, attention_mask, **kwargs):
        del attention_mask, kwargs
        base = input_ids.to(torch.float32).unsqueeze(-1).repeat(1, 1, 4)
        return SimpleNamespace(hidden_states=tuple(base + index for index in range(5)))


class MergedTailTokenizer(CharacterTokenizer):
    def __init__(self) -> None:
        self.values: dict[int, str] = {}
        self.identifiers: dict[str, int] = {}

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
        pieces = [(value, index, index + 1) for index, value in enumerate(text)]
        if len(pieces) >= 2:
            pieces = [*pieces[:-2], (text[-2:], len(text) - 2, len(text))]
        ids = []
        for value, _, _ in pieces:
            if value not in self.identifiers:
                identifier = 100000 + len(self.values)
                self.identifiers[value] = identifier
                self.values[identifier] = value
            identifier = self.identifiers[value]
            ids.append(identifier)
        output = {"input_ids": ids, "attention_mask": [1] * len(ids)}
        if return_offsets_mapping:
            output["offset_mapping"] = [
                (start, end) for _, start, end in pieces
            ]
        if return_tensors == "pt":
            output = {
                key: torch.tensor([value], dtype=torch.long)
                for key, value in output.items()
            }
        return output

    def decode(self, ids, **kwargs) -> str:
        del kwargs
        return "".join(self.values[int(value)] for value in ids)


def _example(target: str = "NEVER_INCLUDE_THIS_TARGET") -> DecisionExample:
    return DecisionExample(
        benchmark="appworld",
        episode_id="appworld:trace:task-a",
        step_id=2,
        state_text=(
            "[QUERY]\nDo the current task.\n[TRACE SO FAR]\n"
            "Step 1 - Response:\n```python\nprint('x')\n```\n"
            "Step 1 - Observation:\nOutput:\n```\nx\n```"
        ),
        target_text=target,
        target_type="code",
        candidate_memory_ids=None,
    )


def _transition() -> dict[str, object]:
    return {
        "source_task_goal": "Find a note.",
        "canonical_pre_action_state": "State before action.",
        "complete_action": "```python\napis.simple_note.search_notes(query='x')\n```",
        "complete_post_action_observation": "[{\"title\": \"x\"}]",
        "transition_content_sha256": "content-hash",
        "apps": ["simple_note"],
        "api_names": ["simple_note.search_notes"],
        "action_type": "api_read_or_login",
        "step_index": 2,
        "step_count": 4,
        "canonical_pre_action_state_tokens": 20,
        "complete_action_tokens": 8,
        "complete_post_action_observation_tokens": 6,
        "source_task_goal_tokens": 4,
    }


def test_query_and_transition_spans_cover_exact_canonical_text() -> None:
    tokenizer = CharacterTokenizer()
    rendered, state_spans, metadata = query_state_text_and_char_spans(
        tokenizer, _example(), "full_demo"
    )
    assert set(state_spans) == set(STATE_VIEW_NAMES)
    assert "NEVER_INCLUDE_THIS_TARGET" not in rendered
    assert not metadata["target_action_accessed"]
    _, _, state_rows = tokenize_and_validate_char_spans(
        tokenizer, rendered, state_spans
    )
    assert all(row["decoded_matches_aligned_source"] for row in state_rows.values())

    text, transition_spans, _ = transition_text_and_char_spans(_transition())
    assert set(transition_spans) == set(TRANSITION_VIEW_NAMES)
    _, _, transition_rows = tokenize_and_validate_char_spans(
        tokenizer, text, transition_spans
    )
    assert all(
        row["decoded_matches_aligned_source"] for row in transition_rows.values()
    )


def test_generation_boundary_uses_complete_final_token_extent() -> None:
    tokenizer = MergedTailTokenizer()
    rendered, state_spans, _ = query_state_text_and_char_spans(
        tokenizer, _example(), "full_demo"
    )
    assert state_spans["generation_boundary"] == (len(rendered) - 2, len(rendered))
    _, _, rows = tokenize_and_validate_char_spans(
        tokenizer,
        rendered,
        {"generation_boundary": state_spans["generation_boundary"]},
    )
    assert rows["generation_boundary"]["decoded_text_exact_match"]


def test_semantic_span_aligns_outward_only_across_whitespace() -> None:
    tokenizer = MergedTailTokenizer()
    _, _, rows = tokenize_and_validate_char_spans(
        tokenizer, "abcd\n", {"content": (0, 4)}
    )
    row = rows["content"]
    assert row["token_aligned_char_end"] == 5
    assert row["token_boundary_expansion"]["trailing_characters"] == 1
    assert row["decoded_matches_aligned_source"]

    with pytest.raises(ValueError, match="crosses non-whitespace"):
        tokenize_and_validate_char_spans(
            tokenizer, "abcdX", {"content": (0, 4)}
        )


def test_frozen_span_readouts_return_both_layers_and_poolings() -> None:
    tokenizer = CharacterTokenizer()
    text = "abcdef"
    input_ids, attention_mask, spans = tokenize_and_validate_char_spans(
        tokenizer, text, {"left": (0, 3), "right": (3, 6)}
    )
    readouts = frozen_qwen_span_readouts(
        model=FrozenToyModel(),
        input_ids=input_ids,
        attention_mask=attention_mask,
        span_rows=spans,
        device=torch.device("cpu"),
    )
    assert set(readouts) == {"final_layer", "mean_final_four_layers"}
    assert readouts["final_layer"]["left"].shape == (2, 4)
    assert torch.equal(
        readouts["final_layer"]["left"][1],
        torch.full((4,), float(ord("c") + 4)),
    )


@pytest.mark.parametrize(
    "kind",
    (
        "multiview_signed_bilinear",
        "multiview_lowrank_tensor",
        "multiview_pair_mlp",
    ),
)
def test_multiview_models_have_signed_interaction_gradients(kind: str) -> None:
    main = ViewSetMainEffectHeads(
        state_views=4,
        transition_views=4,
        input_dim=8,
        projection_dim=3,
        hidden_dim=6,
        dropout=0.0,
    )
    model = MultiViewInteractionPredictor(
        kind,
        main_effects=main,
        mu=0.0,
        state_views=4,
        transition_views=4,
        input_dim=8,
        projection_dim=3,
        interaction_rank=2,
        hidden_dim=7,
        dropout=0.0,
    )
    state = torch.randn(5, 4, 8)
    transition = torch.randn(5, 4, 8)
    interaction = model.interaction(state, transition)
    assert interaction.shape == (5,)
    interaction.sum().backward()
    assert any(
        parameter.grad is not None
        for name, parameter in model.named_parameters()
        if not name.startswith("main_effects")
    )
    assert all(
        parameter.grad is None for parameter in model.main_effects.parameters()
    )


def test_structured_features_ignore_target_action() -> None:
    state_metadata = {
        "state": {
            "apps": ["simple_note"],
            "step_id": 2,
            "step_count": 4,
            "prompt_tokens": 100,
        }
    }
    transitions = {"transition": _transition()}
    first = StructuredPairFeatureBuilder(
        state_examples={"state": _example("target-one")},
        state_metadata=state_metadata,
        transitions=transitions,
    )
    second = StructuredPairFeatureBuilder(
        state_examples={"state": _example("target-two")},
        state_metadata=state_metadata,
        transitions=transitions,
    )
    assert torch.equal(
        first.vector("state", "transition"),
        second.vector("state", "transition"),
    )


def test_multiview_runner_stays_inside_representation_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_multiview_interaction_6c.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert "forward_train" not in called
    assert "generate" not in called
    assert "run_appworld" not in called
