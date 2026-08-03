from __future__ import annotations

import torch

from rcmf.injection.prefix import AdditivePrefixMemoryInjector, PrefixMemoryInjector
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
