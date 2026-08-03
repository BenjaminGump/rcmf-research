from __future__ import annotations

import torch
from torch import nn

from rcmf.benchmarks.appworld.agent import RCMFAppWorldAgent
from rcmf.config import RCMFConfig
from rcmf.memory.state import MemoryState


class FakeBackend:
    def encode_texts(self, texts, batch_size=1):
        return torch.ones(len(texts), 3)


class FakeStateEncoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.anchor = nn.Parameter(torch.zeros(1))

    def forward(self, representations, attention_mask):
        return torch.tensor([[0.5, 0.5]], dtype=torch.float32)


def test_appworld_agent_applies_eval_memory_scale() -> None:
    cfg = RCMFConfig()
    cfg.encoder.type = "qwen_hidden"
    cfg.memory.normalization = "none"
    memory_state = MemoryState(
        rank=2,
        program_dim=3,
        v=torch.tensor([[1.0, 2.0, 3.0], [4.0, 6.0, 8.0]]),
        c=torch.ones(2),
    )
    agent = RCMFAppWorldAgent(
        cfg,
        backend=FakeBackend(),
        memory_state=memory_state,
        state_encoder=FakeStateEncoder(),
        memory_scale=0.25,
    )

    z = agent._memory_z_for_turn("state")

    assert torch.allclose(z, torch.tensor([[0.625, 1.0, 1.375]]))
