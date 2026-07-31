from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from rcmf.injection.base import MemoryInjector
from rcmf.model.backends.base import ChatMessage, GenerateOutput, TokenizedBatch, TrainOutput


class ByteTokenizer:
    eos_token_id = 0
    pad_token_id = 0
    eos_token = "<eos>"
    pad_token = "<pad>"

    def __init__(self, vocab_size: int = 259) -> None:
        self.vocab_size = vocab_size

    def encode_text(self, text: str) -> list[int]:
        return [byte + 3 for byte in text.encode("utf-8")]

    def decode(self, ids: list[int] | Tensor, skip_special_tokens: bool = True) -> str:
        values = ids.tolist() if isinstance(ids, Tensor) else ids
        bytes_out = bytes(max(0, int(item) - 3) for item in values if int(item) >= 3)
        return bytes_out.decode("utf-8", errors="ignore")

    def apply_chat_template(
        self,
        messages: list[ChatMessage],
        tokenize: bool = False,
        add_generation_prompt: bool = True,
        **kwargs: Any,
    ) -> str:
        text = "\n".join(f"{msg['role'].upper()}:\n{msg['content']}" for msg in messages)
        if add_generation_prompt:
            text += "\nASSISTANT:\n"
        return text

    def __call__(self, text: str, return_tensors: str = "pt") -> dict[str, Tensor]:
        ids = torch.tensor([[*self.encode_text(text), self.eos_token_id]], dtype=torch.long)
        return {"input_ids": ids, "attention_mask": torch.ones_like(ids)}


class TinyCausalLM(nn.Module):
    def __init__(self, vocab_size: int = 259, hidden_size: int = 32) -> None:
        super().__init__()
        self.config = type("Config", (), {"hidden_size": hidden_size, "vocab_size": vocab_size})()
        self.embed = nn.Embedding(vocab_size, hidden_size)
        self.lm_head = nn.Linear(hidden_size, vocab_size)

    def get_input_embeddings(self) -> nn.Embedding:
        return self.embed

    def forward(
        self,
        input_ids: Tensor | None = None,
        inputs_embeds: Tensor | None = None,
        attention_mask: Tensor | None = None,
        labels: Tensor | None = None,
        position_ids: Tensor | None = None,
        **kwargs: Any,
    ) -> Any:
        x = inputs_embeds if inputs_embeds is not None else self.embed(input_ids)
        logits = self.lm_head(x)
        loss = None
        if labels is not None:
            loss = F.cross_entropy(
                logits[..., :-1, :].contiguous().view(-1, logits.shape[-1]),
                labels[..., 1:].contiguous().view(-1),
                ignore_index=-100,
            )
        return type("Output", (), {"logits": logits, "loss": loss, "hidden_states": (x,)})()

    def generate(self, input_ids: Tensor | None = None, inputs_embeds: Tensor | None = None, **kwargs: Any) -> Tensor:
        if input_ids is None:
            batch = inputs_embeds.shape[0]
            input_ids = torch.zeros(batch, 1, dtype=torch.long, device=inputs_embeds.device)
        max_new_tokens = kwargs.get("max_new_tokens", 16)
        generated = input_ids.clone()
        for _ in range(max_new_tokens):
            outputs = self(input_ids=generated)
            next_token = outputs.logits[:, -1, :].argmax(dim=-1, keepdim=True)
            generated = torch.cat([generated, next_token], dim=1)
        return generated


class MockBackend:
    def __init__(self, vocab_size: int = 259, hidden_size: int = 32) -> None:
        self.tokenizer = ByteTokenizer(vocab_size=vocab_size)
        self.model = TinyCausalLM(vocab_size=vocab_size, hidden_size=hidden_size)

    @property
    def device(self) -> torch.device:
        return torch.device("cpu")

    def tokenize_messages(
        self,
        messages: list[ChatMessage],
        add_generation_prompt: bool = True,
        return_tensors: str = "pt",
    ) -> TokenizedBatch:
        text = self.tokenizer.apply_chat_template(messages, add_generation_prompt=add_generation_prompt)
        tokenized = self.tokenizer(text, return_tensors=return_tensors)
        return TokenizedBatch(
            input_ids=tokenized["input_ids"],
            attention_mask=tokenized["attention_mask"],
            metadata={"text": text},
        )

    def forward_train(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
        labels: Tensor | None = None,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> TrainOutput:
        if injector is None:
            inputs: dict[str, Any] = {"input_ids": input_ids}
            if attention_mask is not None:
                inputs["attention_mask"] = attention_mask
            if labels is not None:
                inputs["labels"] = labels
            meta = {"injector": None}
        else:
            prepared = injector.prepare_train_inputs(
                self.model,
                input_ids,
                attention_mask,
                labels,
                memory_z,
            )
            inputs = dict(prepared.inputs)
            meta = prepared.memory_metadata
        logit_bias = inputs.pop("memory_logit_bias", None)
        output = self.model(**inputs)
        logits = output.logits
        if logit_bias is not None:
            logits = logits + logit_bias[:, None, :]
        return TrainOutput(loss=output.loss, logits=logits, extra={"memory": meta})

    @torch.no_grad()
    def encode_input_ids(
        self,
        input_ids: Tensor,
        attention_mask: Tensor | None = None,
    ) -> Tensor:
        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids)
        outputs = self.model(input_ids=input_ids, attention_mask=attention_mask, output_hidden_states=True)
        hidden = outputs.hidden_states[-1]
        lengths = attention_mask.to(torch.long).sum(dim=1).clamp_min(1) - 1
        rows = torch.arange(hidden.shape[0], device=hidden.device)
        return hidden[rows, lengths].detach().to(torch.float32)

    @torch.no_grad()
    def encode_texts(
        self,
        texts: list[str],
        batch_size: int = 1,
        add_special_tokens: bool = True,
    ) -> Tensor:
        chunk_representations, owner_indices = self.encode_text_chunks(
            texts,
            batch_size=batch_size,
            add_special_tokens=add_special_tokens,
        )
        if not texts:
            return torch.empty(0, self.model.config.hidden_size, dtype=torch.float32)
        pooled = torch.zeros(len(texts), chunk_representations.shape[-1], dtype=torch.float32)
        counts = torch.zeros(len(texts), 1, dtype=torch.float32)
        pooled.index_add_(0, owner_indices, chunk_representations)
        counts.index_add_(0, owner_indices, torch.ones(owner_indices.shape[0], 1, dtype=torch.float32))
        return pooled / counts.clamp_min(1.0)

    @torch.no_grad()
    def encode_text_chunks(
        self,
        texts: list[str],
        batch_size: int = 1,
        add_special_tokens: bool = True,
        max_chunk_tokens: int | None = None,
    ) -> tuple[Tensor, Tensor]:
        del add_special_tokens
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        chunk_limit = int(max_chunk_tokens or 32768)
        if chunk_limit <= 0:
            raise ValueError("max_chunk_tokens must be positive")
        chunks: list[list[int]] = []
        owners: list[int] = []
        for owner_index, text in enumerate(texts):
            token_ids = self.tokenizer(text)["input_ids"][0].tolist()
            for start in range(0, len(token_ids), chunk_limit):
                chunks.append(list(token_ids[start : start + chunk_limit]))
                owners.append(owner_index)
        if not chunks:
            return (
                torch.empty(0, self.model.config.hidden_size, dtype=torch.float32),
                torch.empty(0, dtype=torch.long),
            )
        outputs: list[Tensor] = []
        for start in range(0, len(chunks), batch_size):
            batch_chunks = chunks[start : start + batch_size]
            max_len = max(len(chunk) for chunk in batch_chunks)
            input_ids = torch.full((len(batch_chunks), max_len), self.tokenizer.pad_token_id, dtype=torch.long)
            attention_mask = torch.zeros_like(input_ids)
            for row, chunk in enumerate(batch_chunks):
                input_ids[row, : len(chunk)] = torch.tensor(chunk, dtype=torch.long)
                attention_mask[row, : len(chunk)] = 1
            outputs.append(self.encode_input_ids(input_ids, attention_mask))
        return torch.cat(outputs, dim=0), torch.tensor(owners, dtype=torch.long)

    def generate(
        self,
        messages: list[ChatMessage],
        max_new_tokens: int = 16,
        temperature: float = 0.0,
        top_p: float = 1.0,
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> GenerateOutput:
        tokenized = self.tokenize_messages(messages)
        if injector is None:
            inputs = {"input_ids": tokenized.input_ids}
            meta = {"injector": None}
        else:
            prepared = injector.prepare_generate_inputs(
                self.model,
                tokenized.input_ids,
                tokenized.attention_mask,
                memory_z,
            )
            inputs = dict(prepared.inputs)
            inputs.pop("memory_logit_bias", None)
            meta = prepared.memory_metadata
        output_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens)
        generated = output_ids[0, tokenized.input_ids.shape[1] :].tolist()
        return GenerateOutput(
            text=self.tokenizer.decode(generated),
            token_ids=generated,
            usage={
                "prompt_tokens": int(tokenized.attention_mask.sum().item()),
                "completion_tokens": len(generated),
                "total_tokens": int(tokenized.attention_mask.sum().item()) + len(generated),
            },
            extra={"memory": meta},
        )

    def score_targets(
        self,
        messages: list[ChatMessage],
        targets: list[str],
        injector: MemoryInjector | None = None,
        memory_z: Tensor | None = None,
    ) -> list[float]:
        text = self.tokenizer.apply_chat_template(messages)
        scores = []
        for target in targets:
            digest = hashlib.sha256((text + target).encode("utf-8")).digest()
            scores.append(int.from_bytes(digest[:4], "big") / 2**32)
        return scores
