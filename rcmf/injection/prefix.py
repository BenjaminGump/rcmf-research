from __future__ import annotations

from typing import Any

import torch
from torch import Tensor, nn

from rcmf.injection.base import MemoryInjector, PreparedInputs, build_position_ids


class PrefixMemoryInjector(MemoryInjector):
    """Map fixed-size memory vector z to fixed-count latent prefix embeddings."""

    def __init__(
        self,
        program_dim: int,
        model_dim: int,
        num_prefix_tokens: int = 8,
        initial_scale: float = 0.1,
    ) -> None:
        super().__init__()
        if program_dim <= 0 or model_dim <= 0 or num_prefix_tokens <= 0:
            raise ValueError("program_dim, model_dim and num_prefix_tokens must be positive")
        self.program_dim = program_dim
        self.model_dim = model_dim
        self.num_prefix_tokens = num_prefix_tokens
        self.mlp = nn.Sequential(
            nn.Linear(program_dim, 4 * program_dim, bias=False),
            nn.GELU(),
            nn.Linear(4 * program_dim, num_prefix_tokens * model_dim, bias=False),
        )
        nn.init.normal_(self.mlp[-1].weight, mean=0.0, std=1.0e-5)
        self.prefix_scale = nn.Parameter(torch.tensor(float(initial_scale)))

    def forward(self, memory_z: Tensor) -> Tensor:
        if memory_z.dim() != 2 or memory_z.shape[-1] != self.program_dim:
            raise ValueError(
                f"memory_z must have shape [batch, {self.program_dim}], got {tuple(memory_z.shape)}"
            )
        memory_z = torch.nan_to_num(memory_z.to(torch.float32), nan=0.0, posinf=10.0, neginf=-10.0)
        memory_z = memory_z.clamp(min=-10.0, max=10.0)
        prefix = self.mlp(memory_z)
        prefix = torch.nan_to_num(prefix, nan=0.0, posinf=10.0, neginf=-10.0)
        prefix = prefix.clamp(min=-10.0, max=10.0)
        prefix = prefix.view(memory_z.shape[0], self.num_prefix_tokens, self.model_dim)
        scale = torch.nan_to_num(self.prefix_scale.to(prefix.dtype), nan=0.0, posinf=1.0, neginf=-1.0)
        return prefix * scale.clamp(min=-1.0, max=1.0)

    def _prepare_common(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor,
        labels: Tensor | None = None,
    ) -> PreparedInputs:
        if input_ids.dim() != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        if memory_z.shape[0] != input_ids.shape[0]:
            raise ValueError("memory_z batch size must match input_ids")
        token_embeds = model.get_input_embeddings()(input_ids)
        prefix = self(memory_z).to(device=token_embeds.device, dtype=token_embeds.dtype)
        inputs_embeds = torch.cat([prefix, token_embeds], dim=1)
        batch_size, token_len = input_ids.shape
        prefix_mask = torch.ones(
            batch_size,
            self.num_prefix_tokens,
            dtype=torch.long,
            device=input_ids.device,
        )
        if attention_mask is None:
            attention_mask = torch.ones(batch_size, token_len, dtype=torch.long, device=input_ids.device)
        full_attention_mask = torch.cat([prefix_mask, attention_mask.to(torch.long)], dim=1)
        prepared: dict[str, Any] = {
            "inputs_embeds": inputs_embeds,
            "attention_mask": full_attention_mask,
            "position_ids": build_position_ids(full_attention_mask),
        }
        if labels is not None:
            if labels.shape != input_ids.shape:
                raise ValueError("labels shape must match input_ids")
            prefix_labels = torch.full(
                (batch_size, self.num_prefix_tokens),
                -100,
                dtype=labels.dtype,
                device=labels.device,
            )
            prepared["labels"] = torch.cat([prefix_labels, labels], dim=1)
        return PreparedInputs(
            inputs=prepared,
            memory_metadata={
                "injector": "prefix",
                "num_prefix_tokens": self.num_prefix_tokens,
                "prefix_once_per_turn": True,
            },
        )

    def prepare_train_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        labels: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        if memory_z is None:
            raise ValueError("PrefixMemoryInjector requires memory_z")
        return self._prepare_common(model, input_ids, attention_mask, memory_z, labels)

    def prepare_generate_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        if memory_z is None:
            raise ValueError("PrefixMemoryInjector requires memory_z")
        prepared = self._prepare_common(model, input_ids, attention_mask, memory_z, labels=None)
        batch_size = input_ids.shape[0]
        pad_token_id = int(getattr(getattr(model, "config", None), "pad_token_id", 0) or 0)
        prefix_ids = torch.full(
            (batch_size, self.num_prefix_tokens),
            pad_token_id,
            dtype=input_ids.dtype,
            device=input_ids.device,
        )
        prepared.inputs["input_ids"] = torch.cat([prefix_ids, input_ids], dim=1)
        return prepared


class AdditivePrefixMemoryInjector(PrefixMemoryInjector):
    """Inject memory by adding learned deltas to existing prompt token embeddings."""

    def _prepare_common(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor,
        labels: Tensor | None = None,
    ) -> PreparedInputs:
        if input_ids.dim() != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        if memory_z.shape[0] != input_ids.shape[0]:
            raise ValueError("memory_z batch size must match input_ids")
        token_embeds = model.get_input_embeddings()(input_ids)
        prefix = self(memory_z).to(device=token_embeds.device, dtype=token_embeds.dtype)
        inject_len = min(self.num_prefix_tokens, token_embeds.shape[1])
        inputs_embeds = token_embeds.clone()
        if inject_len > 0:
            inputs_embeds[:, :inject_len, :] = inputs_embeds[:, :inject_len, :] + prefix[:, :inject_len, :]
        if attention_mask is None:
            attention_mask = torch.ones(
                input_ids.shape[0],
                input_ids.shape[1],
                dtype=torch.long,
                device=input_ids.device,
            )
        prepared: dict[str, Any] = {
            "input_ids": input_ids,
            "inputs_embeds": inputs_embeds,
            "attention_mask": attention_mask.to(torch.long),
            "position_ids": build_position_ids(attention_mask.to(torch.long)),
        }
        if labels is not None:
            if labels.shape != input_ids.shape:
                raise ValueError("labels shape must match input_ids")
            prepared["labels"] = labels
        return PreparedInputs(
            inputs=prepared,
            memory_metadata={
                "injector": "additive_prefix",
                "num_prefix_tokens": self.num_prefix_tokens,
                "prefix_additive": True,
            },
        )

    def prepare_generate_inputs(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor | None,
        **kwargs: Any,
    ) -> PreparedInputs:
        if memory_z is None:
            raise ValueError("AdditivePrefixMemoryInjector requires memory_z")
        return self._prepare_common(model, input_ids, attention_mask, memory_z, labels=None)
