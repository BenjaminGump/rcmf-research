from __future__ import annotations

from types import SimpleNamespace

import torch
from torch import nn
import torch.nn.functional as F

from rcmf.injection.prefix import AdditivePrefixMemoryInjector, PrefixMemoryInjector
from rcmf.model.backends.hf_qwen import HFQwenBackend


class TinyBaseModel(nn.Module):
    def __init__(self, vocab_size: int = 32, hidden_size: int = 12) -> None:
        super().__init__()
        self.embed = nn.Embedding(vocab_size, hidden_size)
        self.proj = nn.Linear(hidden_size, hidden_size)

    def forward(self, input_ids=None, inputs_embeds=None, **kwargs):
        if input_ids is not None and inputs_embeds is not None:
            raise ValueError("You must specify exactly one of input_ids or inputs_embeds")
        hidden = inputs_embeds if inputs_embeds is not None else self.embed(input_ids)
        hidden = torch.tanh(self.proj(hidden))
        return SimpleNamespace(last_hidden_state=hidden)


class TinyCausalLMWrapper(nn.Module):
    def __init__(self, vocab_size: int = 32, hidden_size: int = 12) -> None:
        super().__init__()
        self.config = SimpleNamespace(hidden_size=hidden_size, vocab_size=vocab_size)
        self.model = TinyBaseModel(vocab_size=vocab_size, hidden_size=hidden_size)
        self.lm_head = nn.Linear(hidden_size, vocab_size, bias=False)

    def get_input_embeddings(self):
        return self.model.embed


def _backend_for_tiny_model(model: TinyCausalLMWrapper) -> HFQwenBackend:
    backend = HFQwenBackend(load_model=False)
    backend.model = model
    return backend


def test_hf_qwen_backend_computes_loss_only_for_target_positions() -> None:
    torch.manual_seed(7)
    model = TinyCausalLMWrapper(vocab_size=40, hidden_size=10)
    backend = _backend_for_tiny_model(model)
    input_ids = torch.tensor([[3, 4, 5, 6, 7, 8]])
    labels = torch.tensor([[-100, -100, -100, -100, 7, 8]])

    output = backend.forward_train(
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
    )

    hidden = model.model(input_ids=input_ids).last_hidden_state
    shift_labels = labels[..., 1:]
    mask = shift_labels.ne(-100)
    expected_logits = model.lm_head(hidden[..., :-1, :][mask])
    expected_loss = F.cross_entropy(expected_logits, shift_labels[mask])
    assert output.logits.shape == (2, 40)
    assert torch.allclose(output.loss, expected_loss)


def test_hf_qwen_target_only_loss_keeps_prefix_gradients() -> None:
    torch.manual_seed(8)
    model = TinyCausalLMWrapper(vocab_size=40, hidden_size=10)
    backend = _backend_for_tiny_model(model)
    injector = PrefixMemoryInjector(program_dim=6, model_dim=10, num_prefix_tokens=3)
    input_ids = torch.tensor([[3, 4, 5, 6]])
    labels = torch.tensor([[-100, -100, 5, 6]])
    memory_z = torch.randn(1, 6)

    output = backend.forward_train(
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        injector=injector,
        memory_z=memory_z,
    )
    output.loss.backward()

    grads = [param.grad for param in injector.parameters() if param.requires_grad]
    assert output.logits.shape == (2, 40)
    assert any(grad is not None and torch.isfinite(grad).all() for grad in grads)


def test_hf_qwen_target_only_loss_does_not_pass_input_ids_with_additive_embeds() -> None:
    torch.manual_seed(9)
    model = TinyCausalLMWrapper(vocab_size=40, hidden_size=10)
    backend = _backend_for_tiny_model(model)
    injector = AdditivePrefixMemoryInjector(program_dim=6, model_dim=10, num_prefix_tokens=3)
    input_ids = torch.tensor([[3, 4, 5, 6]])
    labels = torch.tensor([[-100, -100, 5, 6]])
    memory_z = torch.randn(1, 6)

    output = backend.forward_train(
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
        injector=injector,
        memory_z=memory_z,
    )
    output.loss.backward()

    grads = [param.grad for param in injector.parameters() if param.requires_grad]
    assert output.logits.shape == (2, 40)
    assert any(grad is not None and torch.isfinite(grad).all() for grad in grads)


def test_hf_qwen_target_only_loss_restores_model_training_mode() -> None:
    model = TinyCausalLMWrapper(vocab_size=40, hidden_size=10)
    backend = _backend_for_tiny_model(model)
    backend._gradient_checkpointing_enabled = True
    model.eval()
    input_ids = torch.tensor([[3, 4, 5]])
    labels = torch.tensor([[-100, 4, 5]])

    output = backend.forward_train(
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        labels=labels,
    )

    assert torch.isfinite(output.loss)
    assert model.training is False
