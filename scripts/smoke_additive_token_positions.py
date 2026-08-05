from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.training.datasets import (
    _append_eos_token_id,
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
)
from rcmf.utils.serialization import atomic_write_json, maybe_git_commit


def _token_ids(tokenizer: Any, text: str) -> list[int]:
    encoded = tokenizer(text, truncation=False, add_special_tokens=False)["input_ids"]
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if encoded and isinstance(encoded[0], list):
        encoded = encoded[0]
    return [int(item) for item in encoded]


def _decode_token(tokenizer: Any, token_id: int) -> str:
    try:
        return tokenizer.decode([int(token_id)], skip_special_tokens=False)
    except TypeError:
        return tokenizer.decode([int(token_id)])


def _context_limit(backend: Any) -> int:
    model_limit = getattr(getattr(backend.model, "config", None), "max_position_embeddings", None)
    if model_limit is not None:
        return int(model_limit)
    tokenizer_limit = getattr(backend.tokenizer, "model_max_length", None)
    if tokenizer_limit is not None and int(tokenizer_limit) < 1_000_000_000:
        return int(tokenizer_limit)
    return 32768


def _build_prompt_target_tensors(
    tokenizer: Any,
    prompt_text: str,
    target_text: str,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, int, int]:
    prompt_ids = _token_ids(tokenizer, prompt_text)
    target_ids = _append_eos_token_id(tokenizer, _token_ids(tokenizer, target_text))
    input_ids = torch.tensor([prompt_ids + target_ids], dtype=torch.long, device=device)
    labels = torch.tensor([[-100] * len(prompt_ids) + target_ids], dtype=torch.long, device=device)
    attention_mask = torch.ones_like(input_ids)
    return input_ids, attention_mask, labels, len(prompt_ids), len(target_ids)


def _target_loss(backend: Any, input_ids: torch.Tensor, attention_mask: torch.Tensor, labels: torch.Tensor) -> float:
    with torch.no_grad():
        output = backend.forward_train(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            injector=None,
            memory_z=None,
        )
    if output.loss is None:
        raise RuntimeError("No target loss returned")
    return float(output.loss.detach().cpu())


def main() -> None:
    parser = argparse.ArgumentParser(description="No-training Qwen smoke checks for additive-token positions.")
    parser.add_argument("--config", default="configs/benchmark/appworld_rcmf_full_prompt.yaml")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--num-tokens", type=int, default=4)
    args = parser.parse_args()

    cfg = load_config(args.config)
    backend = build_backend(cfg, load_model=True)
    examples = load_decision_examples(Path(args.data) / "decision_examples.jsonl")
    prompt_profile = cfg.benchmark.prompt_profile
    rendered_examples: list[tuple[int, Any, str, dict[str, Any]]] = []
    for index, example in enumerate(examples):
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt_text, prompt_metadata = _render_prompt_with_metadata(
            backend.tokenizer,
            messages,
            prompt_profile,
        )
        rendered_examples.append((index, example, prompt_text, prompt_metadata))
    index, example, prompt_text, prompt_metadata = min(
        rendered_examples,
        key=lambda row: len(_token_ids(backend.tokenizer, row[2])),
    )
    input_ids, attention_mask, labels, prompt_tokens, target_tokens = _build_prompt_target_tensors(
        backend.tokenizer,
        prompt_text,
        _target_suffix(example),
        backend.device,
    )
    total_tokens = int(input_ids.shape[-1])
    context_limit = _context_limit(backend)
    if total_tokens > context_limit:
        raise ValueError(f"Smoke prompt+target tokens {total_tokens} exceed context_limit={context_limit}")
    base_loss = _target_loss(backend, input_ids, attention_mask, labels)
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    memory_z = torch.zeros(1, cfg.memory.program_dim, dtype=torch.float32, device=backend.device)
    base_embeds = backend.model.get_input_embeddings()(input_ids)
    rows = []
    for position in ("first_k", "last_prompt_k", "last_user_k"):
        injector = AdditiveTokenMemoryInjector(
            program_dim=cfg.memory.program_dim,
            model_dim=model_dim,
            num_tokens=args.num_tokens,
            position=position,
            initial_scale=cfg.injector.initial_scale,
        ).to(backend.device)
        prepared = injector.prepare_train_inputs(
            backend.model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            memory_z=memory_z,
            injection_token_indices=torch.tensor(
                [prompt_metadata.get("last_user_token_indices", [])],
                dtype=torch.long,
                device=backend.device,
            ),
        )
        selected = prepared.memory_metadata["selected_token_indices"][0]
        selected = [int(item) for item in selected if int(item) >= 0]
        selected_text = [
            {
                "index": token_index,
                "token_id": int(input_ids[0, token_index].detach().cpu()),
                "text": _decode_token(backend.tokenizer, int(input_ids[0, token_index].detach().cpu())),
            }
            for token_index in selected
        ]
        delta = prepared.inputs["inputs_embeds"] - base_embeds
        max_abs_delta = float(delta.detach().abs().max().cpu())
        with torch.no_grad():
            output = backend.forward_train(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
                injector=injector,
                memory_z=memory_z,
                injection_token_indices=torch.tensor(
                    [prompt_metadata.get("last_user_token_indices", [])],
                    dtype=torch.long,
                    device=backend.device,
                ),
            )
        if output.loss is None:
            raise RuntimeError(f"No zero-delta loss returned for position={position}")
        loss_with_zero_delta = float(output.loss.detach().cpu())
        rows.append(
            {
                "position": position,
                "num_tokens": args.num_tokens,
                "selected_token_indices": selected,
                "selected_tokens": selected_text,
                "last_user_fallback_to_last_prompt": prepared.memory_metadata.get(
                    "last_user_fallback_to_last_prompt"
                ),
                "max_abs_embedding_delta_with_zero_memory": max_abs_delta,
                "base_loss": base_loss,
                "zero_delta_loss": loss_with_zero_delta,
                "loss_abs_diff": abs(base_loss - loss_with_zero_delta),
                "zero_delta_equivalent": max_abs_delta == 0.0 and abs(base_loss - loss_with_zero_delta) <= 1.0e-4,
            }
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    output = {
        "format": "additive_token_position_smoke_v1",
        "commit_sha": maybe_git_commit() or "unknown",
        "config": args.config,
        "data": args.data,
        "model_name": backend.model_name,
        "state_example_id": f"{example.episode_id}:step:{example.step_id}:line:{index + 1}",
        "task_id": str(example.metadata.get("task_id") or example.episode_id.rsplit(":", 1)[-1]),
        "episode_id": example.episode_id,
        "step_id": example.step_id,
        "prompt_tokens": prompt_tokens,
        "target_tokens": target_tokens,
        "total_tokens": total_tokens,
        "context_limit": context_limit,
        "last_user_token_indices": prompt_metadata.get("last_user_token_indices", []),
        "rows": rows,
        "all_zero_delta_equivalent": all(row["zero_delta_equivalent"] for row in rows),
    }
    atomic_write_json(args.output_json, output)
    md_lines = [
        "# Additive-Token Position Smoke",
        "",
        f"- commit: `{output['commit_sha']}`",
        f"- model: `{output['model_name']}`",
        f"- state: `{output['state_example_id']}`",
        f"- prompt tokens: {prompt_tokens}",
        f"- target tokens: {target_tokens}",
        f"- all zero-delta equivalent: {output['all_zero_delta_equivalent']}",
        "",
        "| position | selected indices | loss diff | max abs delta |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in rows:
        md_lines.append(
            f"| {row['position']} | {row['selected_token_indices']} | "
            f"{row['loss_abs_diff']:.8f} | {row['max_abs_embedding_delta_with_zero_memory']:.8f} |"
        )
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Wrote additive-token smoke to {args.output_json} and {args.output_md}")


if __name__ == "__main__":
    main()
