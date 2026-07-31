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
    build_rcmf_training_batch,
    load_decision_examples,
    load_memory_records,
    sample_support_records,
)
from rcmf.utils.serialization import append_jsonl, atomic_write_json, maybe_git_commit


def _move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device) for key, value in batch.items()}


def _params_are_finite(params: list[torch.nn.Parameter]) -> bool:
    return all(torch.isfinite(param.detach()).all().item() for param in params)


def _grads_are_finite(params: list[torch.nn.Parameter]) -> bool:
    for param in params:
        if param.grad is not None and not torch.isfinite(param.grad.detach()).all().item():
            return False
    return True


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
    parser.add_argument("--max-query-tokens", type=int, default=768)
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
        trainer.to(device)
        trainer.train()
        optimizer = trainer.build_optimizer()
        trainable_params = list(trainer.parameters())
        support_size = args.support_size or cfg.training.support_size
        steps_per_epoch = math.ceil(len(examples) / args.batch_size)
        max_steps = args.max_steps or max(1, steps_per_epoch * args.epochs)
        rng = random.Random(cfg.experiment.seed)
        example_order = list(examples)
        optimizer.zero_grad(set_to_none=True)
        metrics_path = output_dir / "metrics.jsonl"
        start_time = time.perf_counter()
        use_autocast = device.type == "cuda" and cfg.training.precision.lower() in {"bf16", "bfloat16"}
        optimizer_steps = 0
        for step in range(1, max_steps + 1):
            if (step - 1) % steps_per_epoch == 0:
                rng.shuffle(example_order)
            offset = ((step - 1) * args.batch_size) % len(example_order)
            batch_examples = example_order[offset : offset + args.batch_size]
            if len(batch_examples) < args.batch_size:
                batch_examples += example_order[: args.batch_size - len(batch_examples)]
            support_records = sample_support_records(records, support_size, rng)
            batch = build_rcmf_training_batch(
                backend.tokenizer,
                cfg,
                support_records=support_records,
                examples=batch_examples,
                max_query_tokens=args.max_query_tokens,
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
        "support_input_ids": torch.randint(1, vocab, (support_size, seq)),
        "support_attention_mask": torch.ones(support_size, seq, dtype=torch.long),
        "state_input_ids": torch.randint(1, vocab, (batch_size, seq)),
        "state_attention_mask": torch.ones(batch_size, seq, dtype=torch.long),
        "query_input_ids": torch.randint(1, vocab, (batch_size, seq)),
        "query_attention_mask": torch.ones(batch_size, seq, dtype=torch.long),
        "labels": torch.randint(1, vocab, (batch_size, seq)),
    }
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
