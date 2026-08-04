from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import RCMFConfig
from rcmf.injection.base import MemoryInjector
from rcmf.memory.compiler import ExperienceCompiler, StateEncoder
from rcmf.memory.normalization import address_entropy
from rcmf.memory.state import read_memory_tensors
from rcmf.model.backends.base import ModelBackend
from rcmf.training.losses import hard_negative_ranking_loss, orthogonal_address_loss


@dataclass
class TrainingStepOutput:
    loss: Tensor
    metrics: dict[str, float]


class RCMFTrainer:
    """Shared trainer for all benchmark adapters."""

    def __init__(
        self,
        config: RCMFConfig,
        backend: ModelBackend,
        compiler: ExperienceCompiler,
        state_encoder: StateEncoder,
        injector: MemoryInjector,
    ) -> None:
        self.config = config
        self.backend = backend
        self.compiler = compiler
        self.state_encoder = state_encoder
        self.injector = injector
        self.modules = nn.ModuleDict(
            {
                "compiler": compiler,
                "state_encoder": state_encoder,
                "injector": injector,
            }
        )

    def parameters(self) -> Any:
        return (param for param in self.modules.parameters() if param.requires_grad)

    def build_optimizer(self) -> torch.optim.Optimizer:
        parameter_groups: list[dict[str, Any]] = []
        seen: set[int] = set()

        def add_group(name: str, module: nn.Module, lr: float) -> None:
            params = []
            for param in module.parameters():
                if not param.requires_grad:
                    continue
                param_id = id(param)
                if param_id in seen:
                    continue
                seen.add(param_id)
                params.append(param)
            if params:
                parameter_groups.append({"params": params, "lr": lr, "name": name})

        add_group("compiler", self.compiler, self.config.training.lr_compiler)
        add_group("state_encoder", self.state_encoder, self.config.training.lr_encoder)
        add_group("injector", self.injector, self.config.training.lr_injector)
        return torch.optim.AdamW(
            parameter_groups,
            weight_decay=self.config.training.weight_decay,
        )

    def train(self, mode: bool = True) -> "RCMFTrainer":
        self.modules.train(mode)
        return self

    def eval(self) -> "RCMFTrainer":
        return self.train(False)

    def to(self, device: torch.device | str) -> "RCMFTrainer":
        self.modules.to(device)
        return self

    def training_step(self, batch: dict[str, Tensor]) -> TrainingStepOutput:
        required = {
            "query_input_ids",
            "query_attention_mask",
            "labels",
        }
        missing = required.difference(batch)
        if missing:
            raise ValueError(f"Training batch missing keys: {sorted(missing)}")
        has_support_representations = "support_representations" in batch
        has_state_representations = "state_representations" in batch
        if self.config.encoder.type == "qwen_hidden":
            if not has_support_representations or not has_state_representations:
                raise ValueError("qwen_hidden training requires support_representations and state_representations")
        elif not {
            "support_input_ids",
            "support_attention_mask",
            "state_input_ids",
            "state_attention_mask",
        }.issubset(batch):
            raise ValueError("Token-id training requires support/state input_ids and attention masks")

        if has_support_representations:
            support = self.compiler(batch["support_representations"], None)
        else:
            support = self.compiler(batch["support_input_ids"], batch["support_attention_mask"])
        v = support.delta_v.sum(dim=0)
        c = support.delta_c.sum(dim=0)
        assert v.shape == (self.config.memory.rank, self.config.memory.program_dim)
        assert c.shape == (self.config.memory.rank,)

        if has_state_representations:
            state_address = self.state_encoder(batch["state_representations"], None)
            state_batch_size = batch["state_representations"].shape[0]
        else:
            state_address = self.state_encoder(batch["state_input_ids"], batch["state_attention_mask"])
            state_batch_size = batch["state_input_ids"].shape[0]
        memory_z = read_memory_tensors(
            v=v,
            c=c,
            address=state_address,
            normalization=self.config.memory.normalization,
            eps=self.config.memory.eps,
        )
        assert memory_z.shape == (
            state_batch_size,
            self.config.memory.program_dim,
        )

        model_output = self.backend.forward_train(
            input_ids=batch["query_input_ids"],
            attention_mask=batch["query_attention_mask"],
            labels=batch["labels"],
            injector=self.injector,
            memory_z=memory_z,
            injection_token_indices=batch.get("last_user_token_indices"),
        )
        if model_output.loss is None:
            raise RuntimeError("Model backend did not produce action loss")
        loss = model_output.loss
        metrics = {"loss_action": float(model_output.loss.detach().cpu())}

        if self.config.loss.rank and "utility_scores" in batch and "utility_labels" in batch:
            rank_loss = hard_negative_ranking_loss(
                batch["utility_scores"].to(loss.device),
                batch["utility_labels"].to(loss.device),
            )
            loss = loss + self.config.loss.lambda_rank * rank_loss
            metrics["loss_rank"] = float(rank_loss.detach().cpu())

        if self.config.loss.semantic_retrieval and has_support_representations and has_state_representations:
            state_teacher = F.normalize(batch["state_representations"].to(torch.float32), dim=-1)
            support_teacher = F.normalize(batch["support_representations"].to(torch.float32), dim=-1)
            teacher_logits = state_teacher @ support_teacher.T
            teacher_temperature = max(float(self.config.loss.semantic_teacher_temperature), 1.0e-6)
            student_temperature = max(float(self.config.loss.semantic_student_temperature), 1.0e-6)
            teacher_probs = F.softmax(teacher_logits / teacher_temperature, dim=-1)
            student_logits = state_address.to(torch.float32) @ support.alpha.to(torch.float32).T
            student_log_probs = F.log_softmax(student_logits / student_temperature, dim=-1)
            retrieval_loss = F.kl_div(student_log_probs, teacher_probs, reduction="batchmean")
            loss = loss + self.config.loss.lambda_semantic_retrieval * retrieval_loss
            metrics["loss_semantic_retrieval"] = float(retrieval_loss.detach().cpu())

        if self.config.loss.sparse:
            sparse = address_entropy(state_address).mean()
            loss = loss + self.config.loss.lambda_sparse * sparse
            metrics["loss_sparse"] = float(sparse.detach().cpu())

        if self.config.loss.orthogonal:
            orth = orthogonal_address_loss(state_address)
            loss = loss + self.config.loss.lambda_orthogonal * orth
            metrics["loss_orthogonal"] = float(orth.detach().cpu())

        metrics["loss"] = float(loss.detach().cpu())
        return TrainingStepOutput(loss=loss, metrics=metrics)

    def _checkpoint_state_dict(self) -> tuple[dict[str, Tensor], list[str]]:
        state = self.modules.state_dict()
        omitted: list[str] = []
        if not self.config.encoder.train_token_embedding:
            omitted = [key for key in state if key.endswith("token_embedding.weight")]
        return {key: value for key, value in state.items() if key not in omitted}, omitted

    def save_checkpoint(
        self,
        path: str | Path,
        optimizer: torch.optim.Optimizer | None = None,
        step: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        module_state, omitted_keys = self._checkpoint_state_dict()
        payload = {
            "step": step,
            "modules": module_state,
            "config": self.config.to_dict(),
            "extra": extra or {},
            "omitted_state_keys": omitted_keys,
        }
        if optimizer is not None:
            payload["optimizer"] = optimizer.state_dict()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        torch.save(payload, tmp_path)
        tmp_path.replace(path)

    def load_checkpoint(
        self,
        path: str | Path,
        optimizer: torch.optim.Optimizer | None = None,
        map_location: str | torch.device = "cpu",
    ) -> dict[str, Any]:
        payload = torch.load(path, map_location=map_location)
        result = self.modules.load_state_dict(payload["modules"], strict=False)
        allowed_missing = set(payload.get("omitted_state_keys", []))
        missing = set(result.missing_keys)
        unexpected = set(result.unexpected_keys)
        if unexpected:
            raise ValueError(f"Unexpected checkpoint keys: {sorted(unexpected)}")
        if missing.difference(allowed_missing):
            raise ValueError(f"Missing checkpoint keys: {sorted(missing.difference(allowed_missing))}")
        if optimizer is not None and "optimizer" in payload:
            optimizer.load_state_dict(payload["optimizer"])
        return payload
