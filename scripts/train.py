from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend, build_trainer
from rcmf.training.datasets import (
    _append_eos_token_id,
    _render_training_prompt,
    _target_suffix,
    build_rcmf_training_batch,
    load_decision_examples,
    load_memory_records,
)
from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.utils.serialization import append_jsonl, atomic_write_json, maybe_git_commit, sha256_file


def _move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device) for key, value in batch.items()}


def _params_are_finite(params: list[torch.nn.Parameter]) -> bool:
    return all(torch.isfinite(param.detach()).all().item() for param in params)


def _grads_are_finite(params: list[torch.nn.Parameter]) -> bool:
    for param in params:
        if param.grad is not None and not torch.isfinite(param.grad.detach()).all().item():
            return False
    return True


def _example_task_id(example: DecisionExample) -> str:
    task_id = example.metadata.get("task_id")
    if task_id:
        return str(task_id)
    return example.episode_id.rsplit(":", 1)[-1]


def _support_indices_for_examples(
    records: list[MemoryRecord],
    examples: list[DecisionExample],
    mode: str,
    support_size: int,
    rng: random.Random,
) -> list[int]:
    if mode == "sample":
        if len(records) >= support_size:
            chosen = rng.sample(range(len(records)), support_size)
        else:
            chosen = [rng.randrange(len(records)) for _ in range(support_size)]
        return chosen
    if mode == "all_except_current_task":
        current_task_ids = {_example_task_id(example) for example in examples}
        chosen = [index for index, record in enumerate(records) if record.task_id not in current_task_ids]
        if not chosen:
            raise ValueError(
                "all_except_current_task produced an empty support set. "
                f"current_task_ids={sorted(current_task_ids)} records={len(records)}"
            )
        return chosen
    raise ValueError(f"Unknown support mode: {mode}")


def _context_limit_for_backend(backend: object) -> int | None:
    model = getattr(backend, "model", None)
    model_config = getattr(model, "config", None)
    model_limit = getattr(model_config, "max_position_embeddings", None)
    if model_limit is not None:
        return int(model_limit)
    tokenizer = getattr(backend, "tokenizer", None)
    tokenizer_limit = getattr(tokenizer, "model_max_length", None)
    if tokenizer_limit is not None and int(tokenizer_limit) < 1_000_000_000:
        return int(tokenizer_limit)
    return None


def _preflight_query_lengths(
    tokenizer: object,
    examples: list[DecisionExample],
    prompt_profile: str,
    context_limit: int | None,
    output_dir: Path,
) -> None:
    if context_limit is None:
        return
    lengths: list[dict[str, object]] = []
    over_limit: list[dict[str, object]] = []
    for index, example in enumerate(examples):
        prompt = _render_training_prompt(tokenizer, example, prompt_profile)
        target = _target_suffix(example)
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        target_ids = _append_eos_token_id(
            tokenizer,
            list(tokenizer(target, add_special_tokens=False)["input_ids"]),
        )
        row = {
            "index": index,
            "episode_id": example.episode_id,
            "step_id": example.step_id,
            "prompt_tokens": len(prompt_ids),
            "target_tokens": len(target_ids),
            "total_tokens": len(prompt_ids) + len(target_ids),
        }
        lengths.append(row)
        if int(row["total_tokens"]) > context_limit:
            over_limit.append(row)
    sorted_lengths = sorted(lengths, key=lambda row: int(row["total_tokens"]), reverse=True)
    atomic_write_json(
        output_dir / "query_length_preflight.json",
        {
            "context_limit": context_limit,
            "examples": len(examples),
            "over_limit": len(over_limit),
            "top": sorted_lengths[:20],
        },
    )
    if over_limit:
        worst = sorted_lengths[0]
        raise ValueError(
            f"{len(over_limit)} training prompt+target sample(s) exceed context_limit={context_limit}. "
            f"Worst sample episode_id={worst['episode_id']} step_id={worst['step_id']} "
            f"total_tokens={worst['total_tokens']}. "
            f"Details: {output_dir / 'query_length_preflight.json'}"
        )


def _load_or_compute_representations(
    backend: object,
    texts: list[str],
    cache_path: Path,
    source_path: Path,
    model_name: str,
    batch_size: int,
) -> torch.Tensor:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source_path)
    if cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu")
        if (
            payload.get("format") == "pooled_qwen_hidden_v1"
            and payload.get("source_sha256") == source_hash
            and payload.get("model_name") == model_name
            and int(payload.get("count", -1)) == len(texts)
        ):
            return payload["representations"].to(torch.float32)
    if not hasattr(backend, "encode_texts"):
        raise TypeError("Backend must implement encode_texts for qwen_hidden encoder")
    representations = backend.encode_texts(texts, batch_size=batch_size).to(torch.float32)
    torch.save(
        {
            "format": "pooled_qwen_hidden_v1",
            "representations": representations.cpu(),
            "source_sha256": source_hash,
            "source_path": str(source_path),
            "model_name": model_name,
            "count": len(texts),
            "created_at": time.time(),
        },
        cache_path,
    )
    return representations.cpu()


def _load_or_compute_chunk_representations(
    backend: object,
    texts: list[str],
    cache_path: Path,
    source_path: Path,
    model_name: str,
    batch_size: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source_path)
    if cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu")
        if (
            payload.get("format") == "chunked_qwen_hidden_v1"
            and payload.get("source_sha256") == source_hash
            and payload.get("model_name") == model_name
            and int(payload.get("text_count", -1)) == len(texts)
        ):
            return (
                payload["representations"].to(torch.float32),
                payload["owner_indices"].to(torch.long),
            )
    if not hasattr(backend, "encode_text_chunks"):
        raise TypeError("Backend must implement encode_text_chunks for qwen_hidden memory records")
    representations, owner_indices = backend.encode_text_chunks(texts, batch_size=batch_size)
    representations = representations.to(torch.float32).cpu()
    owner_indices = owner_indices.to(torch.long).cpu()
    torch.save(
        {
            "format": "chunked_qwen_hidden_v1",
            "representations": representations,
            "owner_indices": owner_indices,
            "source_sha256": source_hash,
            "source_path": str(source_path),
            "model_name": model_name,
            "text_count": len(texts),
            "chunk_count": int(owner_indices.numel()),
            "created_at": time.time(),
        },
        cache_path,
    )
    return representations, owner_indices


def _segment_indices_for_record_indices(
    owner_indices: torch.Tensor,
    record_indices: list[int],
) -> list[int]:
    selected_records = set(int(index) for index in record_indices)
    segment_indices = [
        segment_index
        for segment_index, owner_index in enumerate(owner_indices.tolist())
        if int(owner_index) in selected_records
    ]
    if not segment_indices:
        raise ValueError("Selected support records produced an empty chunked support set")
    return segment_indices


def main() -> None:
    parser = argparse.ArgumentParser(description="Train RCMF memory modules.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--data", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--smoke", action="store_true", help="Run one synthetic batch.")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accumulation-steps", type=int, default=1)
    parser.add_argument("--support-size", type=int, default=None)
    parser.add_argument("--support-mode", choices=["sample", "all_except_current_task"], default="all_except_current_task")
    parser.add_argument("--max-query-tokens", type=int, default=None)
    parser.add_argument("--skip-query-length-preflight", action="store_true")
    parser.add_argument("--representation-cache-dir", default=None)
    parser.add_argument("--representation-batch-size", type=int, default=1)
    parser.add_argument("--save-every", type=int, default=50)
    parser.add_argument("--log-every", type=int, default=1)
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_dir = Path(args.output_dir or cfg.experiment.output_dir) / "train"
    output_dir.mkdir(parents=True, exist_ok=True)
    backend = build_backend(cfg, load_model=not args.smoke)
    if args.smoke:
        cfg.model.backend = "mock"
        cfg.memory.rank = min(cfg.memory.rank, 16)
        cfg.memory.program_dim = min(cfg.memory.program_dim, 16)
        cfg.encoder.hidden_size = min(cfg.encoder.hidden_size, 32)
        cfg.encoder.num_layers = 1
        cfg.encoder.num_heads = 4
        cfg.encoder.intermediate_size = min(cfg.encoder.intermediate_size, 64)
        cfg.injector.num_prefix_tokens = min(cfg.injector.num_prefix_tokens, 2)
        backend = build_backend(cfg, load_model=True)
    trainer = build_trainer(cfg, backend)
    optimizer = trainer.build_optimizer()

    if not args.smoke:
        data_dir = Path(args.data or cfg.raw.get("data", {}).get("output_dir", "runs/appworld/prepared"))
        records = load_memory_records(data_dir / "memory_records.jsonl")
        examples = load_decision_examples(data_dir / "decision_examples.jsonl")
        if not records:
            raise ValueError(f"No memory records found in {data_dir}")
        if not examples:
            raise ValueError(f"No decision examples found in {data_dir}")
        device = backend.device
        if not args.skip_query_length_preflight:
            context_limit = _context_limit_for_backend(backend)
            if args.max_query_tokens is not None:
                context_limit = (
                    min(context_limit, args.max_query_tokens)
                    if context_limit is not None
                    else args.max_query_tokens
                )
            _preflight_query_lengths(
                backend.tokenizer,
                examples,
                cfg.benchmark.prompt_profile,
                context_limit,
                output_dir,
            )
        trainer.to(device)
        trainer.train()
        optimizer = trainer.build_optimizer()
        trainable_params = list(trainer.parameters())
        support_size = args.support_size or cfg.training.support_size
        steps_per_epoch = math.ceil(len(examples) / args.batch_size)
        max_steps = args.max_steps or max(1, steps_per_epoch * args.epochs)
        rng = random.Random(cfg.experiment.seed)
        example_order = list(range(len(examples)))
        record_representations = None
        record_owner_indices = None
        state_representations = None
        if cfg.encoder.type == "qwen_hidden":
            cache_dir = Path(args.representation_cache_dir or output_dir / "representation_cache")
            record_representations, record_owner_indices = _load_or_compute_chunk_representations(
                backend=backend,
                texts=[record.experience_text for record in records],
                cache_path=cache_dir / "memory_record_representations.pt",
                source_path=data_dir / "memory_records.jsonl",
                model_name=cfg.model.name,
                batch_size=args.representation_batch_size,
            )
            state_representations = _load_or_compute_representations(
                backend=backend,
                texts=[example.state_text for example in examples],
                cache_path=cache_dir / "decision_state_representations.pt",
                source_path=data_dir / "decision_examples.jsonl",
                model_name=cfg.model.name,
                batch_size=args.representation_batch_size,
            )
        optimizer.zero_grad(set_to_none=True)
        metrics_path = output_dir / "metrics.jsonl"
        start_time = time.perf_counter()
        use_autocast = device.type == "cuda" and cfg.training.precision.lower() in {"bf16", "bfloat16"}
        optimizer_steps = 0
        for step in range(1, max_steps + 1):
            if (step - 1) % steps_per_epoch == 0:
                rng.shuffle(example_order)
            offset = ((step - 1) * args.batch_size) % len(example_order)
            batch_indices = example_order[offset : offset + args.batch_size]
            if len(batch_indices) < args.batch_size:
                batch_indices += example_order[: args.batch_size - len(batch_indices)]
            batch_examples = [examples[index] for index in batch_indices]
            support_indices = _support_indices_for_examples(
                records,
                batch_examples,
                mode=args.support_mode,
                support_size=support_size,
                rng=rng,
            )
            if record_representations is not None and record_owner_indices is not None:
                segment_indices = _segment_indices_for_record_indices(record_owner_indices, support_indices)
                support_records = [records[int(record_owner_indices[index])] for index in segment_indices]
                support_repr_batch = record_representations[segment_indices]
            else:
                segment_indices = []
                support_records = [records[index] for index in support_indices]
                support_repr_batch = None
            state_repr_batch = (
                state_representations[batch_indices] if state_representations is not None else None
            )
            batch = build_rcmf_training_batch(
                backend.tokenizer,
                cfg,
                support_records=support_records,
                examples=batch_examples,
                max_query_tokens=args.max_query_tokens,
                support_representations=support_repr_batch,
                state_representations=state_repr_batch,
            )
            batch = _move_batch(batch, device)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=use_autocast):
                output = trainer.training_step(batch)
                loss = output.loss / args.grad_accumulation_steps
            if not torch.isfinite(loss.detach()).all().item():
                append_jsonl(metrics_path, {"step": step, "skipped": True, "reason": "nonfinite_loss"})
                optimizer.zero_grad(set_to_none=True)
                print(f"step={step}/{max_steps} skipped nonfinite loss", flush=True)
                continue
            loss.backward()
            if step % args.grad_accumulation_steps == 0 or step == max_steps:
                if not _grads_are_finite(trainable_params):
                    append_jsonl(
                        metrics_path,
                        {"step": step, "skipped": True, "reason": "nonfinite_grad"},
                    )
                    optimizer.zero_grad(set_to_none=True)
                    print(f"step={step}/{max_steps} skipped nonfinite gradients", flush=True)
                    continue
                torch.nn.utils.clip_grad_norm_(trainable_params, cfg.training.grad_clip)
                optimizer.step()
                optimizer_steps += 1
                if not _params_are_finite(trainable_params):
                    raise FloatingPointError(f"Non-finite trainable parameter after optimizer step {step}")
                optimizer.zero_grad(set_to_none=True)
            if step % args.log_every == 0 or step == 1:
                elapsed = time.perf_counter() - start_time
                row = {
                    "step": step,
                    "max_steps": max_steps,
                    "elapsed_s": elapsed,
                    "examples": len(examples),
                    "records": len(records),
                    "support_mode": args.support_mode,
                    "support_records": len(support_indices),
                    "support_segments": len(support_records),
                    **output.metrics,
                }
                append_jsonl(metrics_path, row)
                print(
                    f"step={step}/{max_steps} loss={output.metrics['loss']:.4f} "
                    f"elapsed_s={elapsed:.1f}",
                    flush=True,
                )
            if args.save_every > 0 and step % args.save_every == 0:
                trainer.save_checkpoint(
                    output_dir / f"checkpoint_step{step}.pt",
                    optimizer=optimizer,
                    step=step,
                    extra={"data": str(data_dir), "git_commit": maybe_git_commit()},
                )
        checkpoint_path = output_dir / "checkpoint.pt"
        trainer.save_checkpoint(
            checkpoint_path,
            optimizer=optimizer,
            step=max_steps,
            extra={"data": str(data_dir), "git_commit": maybe_git_commit()},
        )
        save_resolved_config(cfg, output_dir / "resolved_config.yaml")
        atomic_write_json(
            output_dir / "train_summary.json",
            {
                "checkpoint": str(checkpoint_path),
                "steps": max_steps,
                "examples": len(examples),
                "records": len(records),
                "batch_size": args.batch_size,
                "grad_accumulation_steps": args.grad_accumulation_steps,
                "optimizer_steps": optimizer_steps,
                "support_size": support_size,
                "support_mode": args.support_mode,
                "memory_representation_segments": int(record_owner_indices.numel())
                if record_owner_indices is not None
                else None,
                "representation_cache_dir": str(args.representation_cache_dir or output_dir / "representation_cache")
                if cfg.encoder.type == "qwen_hidden"
                else None,
                "git_commit": maybe_git_commit(),
            },
        )
        print(f"Training complete. Checkpoint: {checkpoint_path}")
        return

    batch_size = 2
    support_size = 4
    seq = 12
    vocab = getattr(backend.tokenizer, "vocab_size", 259)
    batch = {
        "query_input_ids": torch.randint(1, vocab, (batch_size, seq)),
        "query_attention_mask": torch.ones(batch_size, seq, dtype=torch.long),
        "labels": torch.randint(1, vocab, (batch_size, seq)),
    }
    if cfg.encoder.type == "qwen_hidden":
        repr_dim = int(getattr(backend.model.config, "hidden_size", cfg.encoder.hidden_size))
        batch.update(
            {
                "support_representations": torch.randn(support_size, repr_dim),
                "state_representations": torch.randn(batch_size, repr_dim),
            }
        )
    else:
        batch.update(
            {
                "support_input_ids": torch.randint(1, vocab, (support_size, seq)),
                "support_attention_mask": torch.ones(support_size, seq, dtype=torch.long),
                "state_input_ids": torch.randint(1, vocab, (batch_size, seq)),
                "state_attention_mask": torch.ones(batch_size, seq, dtype=torch.long),
            }
        )
    output = trainer.training_step(batch)
    output.loss.backward()
    torch.nn.utils.clip_grad_norm_(list(trainer.parameters()), cfg.training.grad_clip)
    optimizer.step()
    trainer.save_checkpoint(output_dir / "checkpoint.pt", optimizer=optimizer, step=1)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")
    atomic_write_json(
        output_dir / "smoke_metrics.json",
        {"metrics": output.metrics, "git_commit": maybe_git_commit(), "data": args.data},
    )
    print(f"Smoke training step complete. Loss: {output.metrics['loss']:.4f}")


if __name__ == "__main__":
    main()
