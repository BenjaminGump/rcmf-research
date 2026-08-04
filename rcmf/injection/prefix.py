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


class AdditiveTokenMemoryInjector(PrefixMemoryInjector):
    """Inject memory by adding learned deltas to selected existing prompt tokens."""

    def __init__(
        self,
        program_dim: int,
        model_dim: int,
        num_tokens: int = 8,
        position: str = "first_k",
        initial_scale: float = 0.1,
    ) -> None:
        if position not in {"first_k", "last_prompt_k", "last_user_k"}:
            raise ValueError(f"Unknown additive-token position: {position}")
        super().__init__(
            program_dim=program_dim,
            model_dim=model_dim,
            num_prefix_tokens=num_tokens,
            initial_scale=initial_scale,
        )
        self.num_tokens = int(num_tokens)
        self.position = position

    def _prompt_mask(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        labels: Tensor | None,
    ) -> Tensor:
        if attention_mask is None:
            mask = torch.ones(input_ids.shape, dtype=torch.bool, device=input_ids.device)
        else:
            mask = attention_mask.to(device=input_ids.device, dtype=torch.bool)
        if labels is not None:
            if labels.shape != input_ids.shape:
                raise ValueError("labels shape must match input_ids")
            mask = mask & labels.to(device=input_ids.device).eq(-100)
        return mask

    def _take_first_or_last(self, mask: Tensor, take_last: bool) -> Tensor:
        rows: list[Tensor] = []
        for row in mask:
            indices = row.nonzero(as_tuple=False).flatten()
            if take_last:
                indices = indices[-self.num_tokens :]
            else:
                indices = indices[: self.num_tokens]
            padded = torch.full(
                (self.num_tokens,),
                -1,
                dtype=torch.long,
                device=mask.device,
            )
            if indices.numel() > 0:
                padded[: indices.numel()] = indices
            rows.append(padded)
        return torch.stack(rows, dim=0)

    def _select_indices(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        labels: Tensor | None,
        injection_token_indices: Tensor | None = None,
    ) -> tuple[Tensor, dict[str, Any]]:
        prompt_mask = self._prompt_mask(input_ids, attention_mask, labels)
        used_fallback = False
        if self.position == "first_k":
            selected = self._take_first_or_last(prompt_mask, take_last=False)
        elif self.position == "last_prompt_k":
            selected = self._take_first_or_last(prompt_mask, take_last=True)
        else:
            selected_rows: list[Tensor] = []
            if injection_token_indices is not None:
                injection_token_indices = injection_token_indices.to(device=input_ids.device, dtype=torch.long)
            for row_index, row_mask in enumerate(prompt_mask):
                if injection_token_indices is None or injection_token_indices.shape[-1] == 0:
                    candidates = row_mask.nonzero(as_tuple=False).flatten()
                    used_fallback = True
                else:
                    row_candidates = injection_token_indices[row_index]
                    row_candidates = row_candidates[
                        (row_candidates >= 0) & (row_candidates < input_ids.shape[1])
                    ]
                    candidates = row_candidates[row_mask[row_candidates]] if row_candidates.numel() else row_candidates
                    if candidates.numel() == 0:
                        candidates = row_mask.nonzero(as_tuple=False).flatten()
                        used_fallback = True
                candidates = candidates[-self.num_tokens :]
                padded = torch.full(
                    (self.num_tokens,),
                    -1,
                    dtype=torch.long,
                    device=input_ids.device,
                )
                if candidates.numel() > 0:
                    padded[: candidates.numel()] = candidates
                selected_rows.append(padded)
            selected = torch.stack(selected_rows, dim=0)
        return selected, {
            "position": self.position,
            "num_tokens": self.num_tokens,
            "last_user_fallback_to_last_prompt": used_fallback,
            "selected_token_indices": selected.detach().cpu().tolist(),
        }

    def _embedding_delta(
        self,
        input_ids: Tensor,
        memory_z: Tensor,
        selected_indices: Tensor,
        dtype: torch.dtype,
    ) -> Tensor:
        token_delta = self(memory_z).to(device=input_ids.device, dtype=dtype)
        embedding_delta = torch.zeros(
            input_ids.shape[0],
            input_ids.shape[1],
            self.model_dim,
            dtype=dtype,
            device=input_ids.device,
        )
        for row_index in range(input_ids.shape[0]):
            for token_slot, token_index in enumerate(selected_indices[row_index].tolist()):
                if token_index < 0:
                    continue
                embedding_delta[row_index, int(token_index), :] += token_delta[row_index, token_slot, :]
        return embedding_delta

    def _prepare_common(
        self,
        model: nn.Module,
        input_ids: Tensor,
        attention_mask: Tensor | None,
        memory_z: Tensor,
        labels: Tensor | None = None,
        injection_token_indices: Tensor | None = None,
    ) -> PreparedInputs:
        if input_ids.dim() != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        if memory_z.shape[0] != input_ids.shape[0]:
            raise ValueError("memory_z batch size must match input_ids")
        token_embeds = model.get_input_embeddings()(input_ids)
        if attention_mask is None:
            attention_mask = torch.ones(
                input_ids.shape[0],
                input_ids.shape[1],
                dtype=torch.long,
                device=input_ids.device,
            )
        selected_indices, selection_metadata = self._select_indices(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            injection_token_indices=injection_token_indices,
        )
        embedding_delta = self._embedding_delta(
            input_ids=input_ids,
            memory_z=memory_z,
            selected_indices=selected_indices,
            dtype=token_embeds.dtype,
        )
        inputs_embeds = token_embeds + embedding_delta
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
                "injector": "additive_token",
                "deprecated_alias": "additive_prefix",
                "additive_token": True,
                **selection_metadata,
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
            raise ValueError("AdditiveTokenMemoryInjector requires memory_z")
        return self._prepare_common(
            model,
            input_ids,
            attention_mask,
            memory_z,
            labels=labels,
            injection_token_indices=kwargs.get("injection_token_indices"),
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
            raise ValueError("AdditiveTokenMemoryInjector requires memory_z")
        injection_token_indices = kwargs.get("injection_token_indices")
        if input_ids.dim() != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        if attention_mask is None:
            attention_mask = torch.ones(
                input_ids.shape[0],
                input_ids.shape[1],
                dtype=torch.long,
                device=input_ids.device,
            )
        token_metadata = kwargs.get("token_metadata") or {}
        if injection_token_indices is None and self.position == "last_user_k":
            values = token_metadata.get("last_user_token_indices") or []
            injection_token_indices = torch.tensor(
                [values],
                dtype=torch.long,
                device=input_ids.device,
            ).expand(input_ids.shape[0], -1)
        selected_indices, selection_metadata = self._select_indices(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=None,
            injection_token_indices=injection_token_indices,
        )
        embedding_delta = self._embedding_delta(
            input_ids=input_ids,
            memory_z=memory_z,
            selected_indices=selected_indices,
            dtype=torch.float32,
        )
        return PreparedInputs(
            inputs={
                "input_ids": input_ids,
                "attention_mask": attention_mask.to(torch.long),
                "memory_embedding_delta": embedding_delta,
            },
            memory_metadata={
                "injector": "additive_token",
                "deprecated_alias": "additive_prefix",
                "additive_token": True,
                "generation_embedding_hook": True,
                **selection_metadata,
            },
        )


class AdditivePrefixMemoryInjector(AdditiveTokenMemoryInjector):
    """Deprecated compatibility alias for older additive_prefix configs/checkpoints."""

    def __init__(
        self,
        program_dim: int,
        model_dim: int,
        num_prefix_tokens: int = 8,
        initial_scale: float = 0.1,
    ) -> None:
        super().__init__(
            program_dim=program_dim,
            model_dim=model_dim,
            num_tokens=num_prefix_tokens,
            position="first_k",
            initial_scale=initial_scale,
        )
