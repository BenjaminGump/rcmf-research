from __future__ import annotations

import torch

from rcmf.config import load_config
from rcmf.factory import build_backend, build_trainer
from rcmf.model.backends.mock import MockBackend
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.training.datasets import _render_training_prompt, build_rcmf_training_batch


class TinyTokenizer:
    pad_token_id = 0
    pad_token = "<pad>"
    eos_token = "</s>"
    eos_token_id = 1

    @property
    def vocab_size(self) -> int:
        return 128

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        text = "\n".join(f"{message['role']}:{message['content']}" for message in messages)
        return text + ("\nassistant:" if add_generation_prompt else "")

    def __call__(
        self,
        texts,
        padding=False,
        truncation=False,
        max_length=None,
        return_tensors=None,
        add_special_tokens=True,
    ):
        def encode_one(text: str) -> list[int]:
            ids = [2 + (ord(char) % 100) for char in text]
            if add_special_tokens:
                ids.append(self.eos_token_id)
            if truncation and max_length is not None:
                ids = ids[:max_length]
            return ids or [self.eos_token_id]

        if isinstance(texts, str):
            return {"input_ids": encode_one(texts)}
        rows = [encode_one(text) for text in texts]
        max_len = max(len(row) for row in rows)
        if padding:
            rows = [row + [self.pad_token_id] * (max_len - len(row)) for row in rows]
        masks = [[1 if token != self.pad_token_id else 0 for token in row] for row in rows]
        if return_tensors == "pt":
            return {
                "input_ids": torch.tensor(rows, dtype=torch.long),
                "attention_mask": torch.tensor(masks, dtype=torch.long),
            }
        return {"input_ids": rows, "attention_mask": masks}


def test_training_step_with_mock_backend() -> None:
    cfg = load_config(
        "configs/base.yaml",
        overrides={
            "model": {"backend": "mock"},
            "memory": {"rank": 8, "program_dim": 6},
            "encoder": {
                "type": "qwen_hidden",
                "hidden_size": 16,
                "num_heads": 4,
                "intermediate_size": 32,
                "num_layers": 1,
            },
            "injector": {"type": "prefix", "num_prefix_tokens": 2},
        },
    )
    backend = build_backend(cfg)
    trainer = build_trainer(cfg, backend)
    vocab = backend.tokenizer.vocab_size
    repr_dim = backend.model.config.hidden_size
    batch = {
        "support_representations": torch.randn(3, repr_dim),
        "state_representations": torch.randn(2, repr_dim),
        "query_input_ids": torch.randint(1, vocab, (2, 5)),
        "query_attention_mask": torch.ones(2, 5, dtype=torch.long),
        "labels": torch.randint(1, vocab, (2, 5)),
    }
    output = trainer.training_step(batch)
    assert torch.isfinite(output.loss)
    output.loss.backward()
    grads = [p.grad for p in trainer.parameters() if p.requires_grad and p.grad is not None]
    assert grads


def test_mock_backend_chunks_long_texts_without_dropping_tokens() -> None:
    backend = MockBackend(hidden_size=8)
    chunk_representations, owner_indices = backend.encode_text_chunks(
        ["abcdef", "xy"],
        batch_size=2,
        max_chunk_tokens=3,
    )
    assert owner_indices.tolist() == [0, 0, 0, 1]
    assert chunk_representations.shape == (4, 8)

    pooled = backend.encode_texts(["abcdef", "xy"], batch_size=2)
    assert pooled.shape == (2, 8)


def test_build_rcmf_training_batch_masks_prompt_tokens() -> None:
    cfg = load_config("configs/base.yaml")
    cfg.encoder.max_experience_tokens = 16
    cfg.encoder.max_state_tokens = None
    tokenizer = TinyTokenizer()
    record = MemoryRecord(
        memory_id="m1",
        benchmark="appworld",
        episode_id="e1",
        task_id="t1",
        raw_trajectory={},
        experience_text="useful memory",
        outcome=1.0,
        success=True,
    )
    example = DecisionExample(
        benchmark="appworld",
        episode_id="e1",
        step_id=0,
        state_text="question",
        target_text="answer",
        target_type="answer",
        candidate_memory_ids=None,
    )

    batch = build_rcmf_training_batch(
        tokenizer,
        cfg,
        support_records=[record],
        examples=[example],
        max_query_tokens=None,
    )

    assert batch["support_input_ids"].shape[0] == 1
    assert batch["state_input_ids"].shape[0] == 1
    assert batch["query_input_ids"].shape == batch["labels"].shape
    assert (batch["labels"] == -100).any()
    assert (batch["labels"] != -100).any()


def test_build_rcmf_training_batch_raises_instead_of_truncating() -> None:
    cfg = load_config("configs/base.yaml")
    cfg.encoder.max_experience_tokens = 16
    cfg.encoder.max_state_tokens = None
    tokenizer = TinyTokenizer()
    record = MemoryRecord(
        memory_id="m1",
        benchmark="appworld",
        episode_id="e1",
        task_id="t1",
        raw_trajectory={},
        experience_text="memory",
        outcome=1.0,
        success=True,
    )
    target = "XYZ"
    example = DecisionExample(
        benchmark="appworld",
        episode_id="e1",
        step_id=1,
        state_text="[SYSTEM PROMPT]\nS\n[QUERY]\n" + ("q" * 200),
        target_text=target,
        target_type="answer",
        candidate_memory_ids=None,
        metadata={"system_prompt": "S", "system_prompt_in_state": True},
    )

    try:
        build_rcmf_training_batch(
            tokenizer,
            cfg,
            support_records=[record],
            examples=[example],
            max_query_tokens=12,
        )
    except ValueError as exc:
        assert "No prompt or target truncation is applied" in str(exc)
    else:
        raise AssertionError("Expected overlong prompt to raise instead of truncating")

    target_ids = tokenizer(target, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
    batch = build_rcmf_training_batch(
        tokenizer,
        cfg,
        support_records=[record],
        examples=[example],
        max_query_tokens=None,
    )
    label_ids = [int(value) for value in batch["labels"][0].tolist() if int(value) != -100]
    assert label_ids == target_ids


def test_full_demo_training_prompt_matches_chat_history_shape() -> None:
    tokenizer = TinyTokenizer()
    example = DecisionExample(
        benchmark="appworld",
        episode_id="appworld:trace:t1",
        step_id=2,
        state_text=(
            "[SYSTEM PROMPT]\nignored old minimal prompt\n"
            "[QUERY]\nNow here is the task:\nTask: Count playlists.\n"
            "[TRACE SO FAR]\n"
            "Step 1 - Response:\n```python\nprint('x')\n```\n"
            "Step 1 - Observation:\n{'ok': true}\n"
        ),
        target_text="```python\napis.supervisor.complete_task(answer=1)\n```",
        target_type="code",
        candidate_memory_ids=None,
    )

    rendered = _render_training_prompt(tokenizer, example, "full_demo")

    assert "user:I am your supervisor" in rendered
    assert "user:Now here is the task:\nTask: Count playlists." in rendered
    assert "assistant:```python\nprint('x')\n```" in rendered
    assert "user:Output:\n```\n{'ok': true}\n```" in rendered
    assert rendered.endswith("\nassistant:")
