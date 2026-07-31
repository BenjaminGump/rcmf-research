from __future__ import annotations

import torch

from rcmf.injection.none import NoneMemoryInjector
from rcmf.model.backends.mock import TinyCausalLM


def test_none_injector_matches_raw_model_logits() -> None:
    torch.manual_seed(1)
    model = TinyCausalLM(vocab_size=32, hidden_size=12)
    input_ids = torch.randint(1, 32, (2, 7))
    attention_mask = torch.ones_like(input_ids)
    labels = input_ids.clone()
    raw = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
    prepared = NoneMemoryInjector().prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=attention_mask,
        labels=labels,
        memory_z=torch.randn(2, 4),
    )
    injected = model(**prepared.inputs)
    assert torch.allclose(raw.logits, injected.logits)
    assert torch.allclose(raw.loss, injected.loss)

