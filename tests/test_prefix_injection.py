from __future__ import annotations

import torch

from rcmf.injection.none import build_injector
from rcmf.injection.prefix import (
    AdditivePrefixMemoryInjector,
    AdditiveTokenMemoryInjector,
    PrefixMemoryInjector,
)
from rcmf.model.backends.mock import TinyCausalLM


def test_prefix_train_inputs_shapes_and_ignore_labels() -> None:
    model = TinyCausalLM(vocab_size=50, hidden_size=16)
    injector = PrefixMemoryInjector(program_dim=8, model_dim=16, num_prefix_tokens=3)
    input_ids = torch.randint(1, 50, (2, 5))
    labels = input_ids.clone()
    memory_z = torch.randn(2, 8)
    prepared = injector.prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        memory_z=memory_z,
    )
    assert prepared.inputs["inputs_embeds"].shape == (2, 8, 16)
    assert prepared.inputs["attention_mask"].shape == (2, 8)
    assert prepared.inputs["labels"].shape == (2, 8)
    assert torch.all(prepared.inputs["labels"][:, :3] == -100)


def test_prefix_generate_inputs_use_full_length_dummy_input_ids() -> None:
    model = TinyCausalLM(vocab_size=50, hidden_size=16)
    injector = PrefixMemoryInjector(program_dim=8, model_dim=16, num_prefix_tokens=3)
    input_ids = torch.randint(1, 50, (2, 5))
    memory_z = torch.randn(2, 8)

    prepared = injector.prepare_generate_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        memory_z=memory_z,
    )

    assert prepared.inputs["inputs_embeds"].shape == (2, 8, 16)
    assert prepared.inputs["attention_mask"].shape == (2, 8)
    assert prepared.inputs["position_ids"].shape == (2, 8)
    assert prepared.inputs["input_ids"].shape == (2, 8)
    assert torch.all(prepared.inputs["input_ids"][:, 3:] == input_ids)


def test_additive_prefix_zero_memory_preserves_prompt_embeddings() -> None:
    model = TinyCausalLM(vocab_size=50, hidden_size=16)
    injector = AdditivePrefixMemoryInjector(program_dim=8, model_dim=16, num_prefix_tokens=3)
    input_ids = torch.randint(1, 50, (2, 5))
    memory_z = torch.zeros(2, 8)
    token_embeds = model.get_input_embeddings()(input_ids)

    prepared = injector.prepare_generate_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        memory_z=memory_z,
    )

    assert prepared.inputs["input_ids"].shape == input_ids.shape
    assert prepared.inputs["attention_mask"].shape == input_ids.shape
    assert "inputs_embeds" not in prepared.inputs
    assert prepared.inputs["memory_embedding_delta"].shape == token_embeds.shape
    assert torch.allclose(prepared.inputs["memory_embedding_delta"], torch.zeros_like(token_embeds))


def test_additive_token_last_prompt_k_does_not_inject_target_tokens() -> None:
    model = TinyCausalLM(vocab_size=50, hidden_size=16)
    injector = AdditiveTokenMemoryInjector(
        program_dim=8,
        model_dim=16,
        num_tokens=3,
        position="last_prompt_k",
    )
    input_ids = torch.randint(1, 50, (1, 8))
    labels = torch.tensor([[-100, -100, -100, -100, -100, 7, 8, 9]])
    prepared = injector.prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        memory_z=torch.randn(1, 8),
    )

    assert prepared.memory_metadata["selected_token_indices"] == [[2, 3, 4]]
    assert prepared.inputs["labels"].shape == labels.shape


def test_additive_token_last_user_k_uses_explicit_indices() -> None:
    model = TinyCausalLM(vocab_size=50, hidden_size=16)
    injector = AdditiveTokenMemoryInjector(
        program_dim=8,
        model_dim=16,
        num_tokens=2,
        position="last_user_k",
    )
    input_ids = torch.randint(1, 50, (1, 8))
    labels = torch.tensor([[-100, -100, -100, -100, -100, -100, 7, 8]])
    prepared = injector.prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        memory_z=torch.randn(1, 8),
        injection_token_indices=torch.tensor([[1, 3, 5, 7, 99]]),
    )

    assert prepared.memory_metadata["selected_token_indices"] == [[3, 5]]
    assert prepared.memory_metadata["last_user_fallback_to_last_prompt"] is False


def test_build_injector_additive_token_and_deprecated_alias() -> None:
    injector = build_injector(
        injector_type="additive_token",
        program_dim=8,
        model_dim=16,
        num_prefix_tokens=9,
        num_tokens=4,
        position="last_user_k",
    )
    alias = build_injector(
        injector_type="additive_prefix",
        program_dim=8,
        model_dim=16,
        num_prefix_tokens=3,
    )

    assert isinstance(injector, AdditiveTokenMemoryInjector)
    assert injector.position == "last_user_k"
    assert injector.num_tokens == 4
    assert isinstance(alias, AdditiveTokenMemoryInjector)
    assert alias.position == "first_k"
