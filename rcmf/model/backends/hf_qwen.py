from __future__ import annotations

from contextlib import nullcontext
import time
from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor

from rcmf.injection.base import MemoryInjector
from rcmf.model.backends.base import ChatMessage, GenerateOutput, TokenizedBatch, TrainOutput


DTYPES = {
    "float32": torch.float32,
    "fp32": torch.float32,
    "float16": torch.float16,
    "fp16": torch.float16,
    "bfloat16": torch.bfloat16,
    "bf16": torch.bfloat16,
}


class HFQwenBackend:
    """Transformers-backed Qwen causal LM backend."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-8B",
        dtype: str = "bfloat16",
        device_map: str | None = None,
        freeze_backbone: bool = True,
        enable_thinking: bool = False,
        load_model: bool = True,
    ) -> None:
        self.model_name = model_name
        self.dtype = DTYPES.get(dtype, torch.bfloat16)
        self.device_map = device_map
        self.freeze_backbone = freeze_backbone
        self.enable_thinking = enable_thinking
        self.tokenizer = None
        self.model = None
        if load_model:
            self.load()

    def load(self) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        if self.tokenizer.pad_token_id is None and self.tokenizer.eos_token is not None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        kwargs: dict[str, Any] = {
            "torch_dtype": self.dtype,
            "trust_remote_code": True,
        }
        if self.device_map is not None:
            kwargs["device_map"] = self.device_map
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, **kwargs)
        if self.device_map is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model.to(device)
        if self.freeze_backbone:
            for param in self.model.parameters():
                param.requires_grad_(False)
        self.model.eval()

    @property
    def device(self) -> torch.device:
        if self.model is None:
            return torch.device("cpu")
        return next(self.model.parameters()).device

    def _apply_chat_template(
        self,
        messages: list[ChatMessage],
        add_generation_prompt: bool,
    ) -> str:
        if self.tokenizer is None:
            raise RuntimeError("HFQwenBackend.load() has not been called")
        try:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
                enable_thinking=self.enable_thinking,
            )
        except TypeError:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
            )

    def tokenize_messages(
        self,
        messages: list[ChatMessage],
        add_generation_prompt: bool = True,
        return_tensors: str = "pt",
    ) -> TokenizedBatch:
        text = self._apply_chat_template(messages, add_generation_prompt=add_generation_prompt)
        tokenized = self.tokenizer(text, return_tensors=return_tensors)
        input_ids = tokenized["input_ids"].to(self.device)
        attention_mask = tokenized.get("attention_mask", torch.ones_like(input_ids)).to(self.device)
        return TokenizedBatch(
            input_ids=input_ids,
            attention_mask=attention_mask,
            metadata={"text": text, "input_tokens": int(attention_mask.sum().item())},
        )

    def _loss_with_logits(self, logits: Tensor, labels: Tensor) -> Tensor:
        shift_logits = logits[..., :-1, :].contiguous()
        shift_labels = labels[..., 1:].contiguous()
        return F.cross_entropy(
            shift_logits.view(-1, shift_logits.shape[-1]),
            shift_labels.view(-1),
            ignore_index=-100,
        )

    def forward_train(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
        labels: Tensor | None = None,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> TrainOutput:
        if self.model is None:
            raise RuntimeError("HFQwenBackend.load() has not been called")
        if injector is None:
            model_inputs: dict[str, Any] = {"input_ids": input_ids.to(self.device)}
            if attention_mask is not None:
                model_inputs["attention_mask"] = attention_mask.to(self.device)
            if labels is not None:
                model_inputs["labels"] = labels.to(self.device)
            memory_metadata = {"injector": None}
        else:
            prepared = injector.prepare_train_inputs(
                self.model,
                input_ids.to(self.device),
                attention_mask.to(self.device) if attention_mask is not None else None,
                labels.to(self.device) if labels is not None else None,
                memory_z.to(self.device) if memory_z is not None else None,
            )
            model_inputs = dict(prepared.inputs)
            memory_metadata = prepared.memory_metadata
        logit_bias = model_inputs.pop("memory_logit_bias", None)
        outputs = self.model(**model_inputs)
        logits = outputs.logits
        if logit_bias is not None:
            logits = logits + logit_bias.to(device=logits.device, dtype=logits.dtype)[:, None, :]
        loss = getattr(outputs, "loss", None)
        if logit_bias is not None and "labels" in model_inputs:
            loss = self._loss_with_logits(logits, model_inputs["labels"])
        return TrainOutput(loss=loss, logits=logits, extra={"memory": memory_metadata})

    @torch.no_grad()
    def generate(
        self,
        messages: list[ChatMessage],
        max_new_tokens: int = 512,
        temperature: float = 0.0,
        top_p: float = 1.0,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> GenerateOutput:
        if self.model is None:
            raise RuntimeError("HFQwenBackend.load() has not been called")
        tokenized = self.tokenize_messages(messages, add_generation_prompt=True)
        generation_inputs: dict[str, Any]
        memory_metadata: dict[str, Any]
        if injector is None:
            generation_inputs = {
                "input_ids": tokenized.input_ids,
                "attention_mask": tokenized.attention_mask,
            }
            memory_metadata = {"injector": None}
        else:
            prepared = injector.prepare_generate_inputs(
                self.model,
                tokenized.input_ids,
                tokenized.attention_mask,
                memory_z.to(self.device) if memory_z is not None else None,
            )
            generation_inputs = dict(prepared.inputs)
            memory_metadata = prepared.memory_metadata
        logit_bias = generation_inputs.pop("memory_logit_bias", None)
        if logit_bias is not None:
            raise NotImplementedError("logit_bias generation requires a custom logits processor")
        start = time.perf_counter()
        generate_kwargs: dict[str, Any] = {
            **generation_inputs,
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0,
            "use_cache": True,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if temperature > 0:
            generate_kwargs["temperature"] = temperature
            generate_kwargs["top_p"] = top_p
        attention_context = nullcontext()
        using_forced_flash = False
        if self.device.type == "cuda":
            try:
                from torch.nn.attention import SDPBackend, sdpa_kernel

                attention_context = sdpa_kernel([SDPBackend.FLASH_ATTENTION])
                using_forced_flash = True
            except Exception:
                attention_context = nullcontext()
        try:
            with attention_context:
                output_ids = self.model.generate(**generate_kwargs)
        except RuntimeError as exc:
            message = str(exc)
            flash_unavailable = (
                "No available kernel" in message
                or "No viable backend" in message
                or "No supported kernel" in message
            )
            if not using_forced_flash or not flash_unavailable:
                raise
            with nullcontext():
                output_ids = self.model.generate(**generate_kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        generated = output_ids[0, tokenized.input_ids.shape[1] :].tolist()
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        return GenerateOutput(
            text=text,
            token_ids=generated,
            usage={
                "prompt_tokens": int(tokenized.attention_mask.sum().item()),
                "completion_tokens": len(generated),
                "total_tokens": int(tokenized.attention_mask.sum().item()) + len(generated),
            },
            ttft_ms=elapsed_ms,
            extra={"memory": memory_metadata},
        )

    @torch.no_grad()
    def score_targets(
        self,
        messages: list[ChatMessage],
        targets: list[str],
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> list[float]:
        if self.model is None:
            raise RuntimeError("HFQwenBackend.load() has not been called")
        scores: list[float] = []
        base_text = self._apply_chat_template(messages, add_generation_prompt=True)
        for target in targets:
            tokenized = self.tokenizer(base_text + target, return_tensors="pt").to(self.device)
            labels = tokenized["input_ids"].clone()
            prefix_len = len(self.tokenizer(base_text, return_tensors="pt")["input_ids"][0])
            labels[:, :prefix_len] = -100
            output = self.forward_train(
                input_ids=tokenized["input_ids"],
                attention_mask=tokenized.get("attention_mask"),
                labels=labels,
                injector=injector,
                memory_z=memory_z,
            )
            if output.loss is None:
                raise RuntimeError("Model did not return a loss")
            scores.append(float(-output.loss.detach().cpu()))
        return scores
