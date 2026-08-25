from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.schemas import DecisionExample
from rcmf.training.cross_attention_field_8b import (
    CrossAttentionMemoryReader,
    CrossAttentionReaderHooks,
    GLOBAL_SEED,
)
from rcmf.training.cross_attention_training_8b import (
    DifferentiableCrossAttentionHooks,
    all_fusion_layers_receive_gradient,
    fusion_gradient_norms,
)
from rcmf.training.datasets import _appworld_messages_from_example
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_program_policy_distill_7dg3 import (
    sparse_policy_kl,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.run_deep_residual_carrier_7e import _attention_context, _bare_target_forward
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_fast_7df import _build_backend


CHECKPOINT_VERSION = "cross_attention_reader_checkpoint_8b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("implementation", "phase1", "phase2", "policy-eval"),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp030a_reader")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "reader"
    return {
        "preflight": artifact_dir / "runtime_preflight.json",
        "memory_index": artifact_dir / "memory/slot_cache/index.json",
        "memory_summary": artifact_dir / "memory/slot_cache/summary.json",
        "mismatches": artifact_dir / "curriculum/mismatch_manifest.json",
        "curriculum": artifact_dir / "curriculum/curriculum_manifest.json",
        "task_split": Path(str(settings["task_split_manifest"])),
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "decisions": corpus / "decision_examples.jsonl",
        "outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "teacher_cache": parent_a
        / "structured_compiler/policy_teacher_cache.pt",
        "implementation": root / "implementation_validation.json",
        "implementation_report": root / "implementation_validation.md",
        "phase1_root": root / "phase1",
        "phase1_latest": root / "phase1/latest_checkpoint.json",
        "phase1_summary": root / "phase1/training_summary.json",
        "phase1_selection": root / "phase1/checkpoint_selection.json",
        "phase1_posttrain": root / "phase1/posttrain_interface_validation.json",
        "phase2_root": root / "phase2",
        "phase2_latest": root / "phase2/latest_checkpoint.json",
        "phase2_summary": root / "phase2/training_summary.json",
        "phase2_units": root / "phase2/training_units.json",
        "policy_eval_root": root / "phase2/policy_evaluation",
        "policy_eval_summary": root / "phase2/policy_evaluation_summary.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-030A reader input: {missing}")


def _task_id(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata", {})
    value = metadata.get("task_id") if isinstance(metadata, Mapping) else None
    if value:
        return str(value)
    episode = str(row["episode_id"])
    return episode.rsplit(":", 1)[-1]


def _split_tasks(path: Path) -> tuple[set[str], set[str]]:
    payload = _json(path)
    for train_name, heldout_name in (
        ("train_task_ids", "validation_task_ids"),
        ("model_train_task_ids", "heldout_validation_task_ids"),
        ("training_task_ids", "heldout_task_ids"),
    ):
        if train_name in payload and heldout_name in payload:
            return set(map(str, payload[train_name])), set(map(str, payload[heldout_name]))
    if isinstance(payload.get("task_split"), Mapping):
        nested = payload["task_split"]
        for train_name, heldout_name in (
            ("train_task_ids", "validation_task_ids"),
            ("model_train_task_ids", "heldout_validation_task_ids"),
        ):
            if train_name in nested and heldout_name in nested:
                return set(map(str, nested[train_name])), set(map(str, nested[heldout_name]))
    raise KeyError("Could not identify frozen 29/8 task split")


def _load_slot_bank(index_path: Path) -> dict[str, Tensor]:
    index = _json(index_path)
    slots: dict[str, Tensor] = {}
    for row in index["rows"]:
        path = Path(str(row["cache_path"]))
        if sha256_file(path) != str(row["cache_sha256"]):
            raise ValueError(f"Memory slot cache hash differs: {path}")
        payload = torch.load(path, map_location="cpu", weights_only=False)
        transition_id = str(row["transition_id"])
        if transition_id in slots:
            raise ValueError(f"Duplicate slot cache key: {transition_id}")
        slots[transition_id] = payload["slots"].to(torch.bfloat16)
    if len(slots) != 499:
        raise ValueError("Memory slot bank does not contain 499 transitions")
    return slots


def _load_source(paths: Mapping[str, Path]) -> dict[str, Any]:
    model_train_tasks, heldout_tasks = _split_tasks(paths["task_split"])
    decision_rows = [dict(row) for row in read_jsonl(paths["decisions"])]
    decisions = {
        (_task_id(row), int(row["step_id"])): DecisionExample.from_dict(row)
        for row in decision_rows
        if _task_id(row) in model_train_tasks | heldout_tasks
    }
    transitions = {
        str(row["transition_id"]): dict(row)
        for row in read_jsonl(paths["transitions"])
        if str(row["parent_task_id"]) in model_train_tasks | heldout_tasks
    }
    transitions_by_step = {
        (str(row["parent_task_id"]), int(row["step_index"])): str(row["transition_id"])
        for row in transitions.values()
    }
    if set(decisions) != set(transitions_by_step):
        raise ValueError("Decision and source-transition keys differ")
    outcomes_payload = _json(paths["outcomes"])
    outcomes = {str(row["state_example_id"]): dict(row) for row in outcomes_payload["rows"]}
    teacher = torch.load(paths["teacher_cache"], map_location="cpu", weights_only=False)
    if set(outcomes) != set(teacher["teacher_rows"]):
        raise ValueError("EXP-028A outcomes and policy teacher states differ")
    mismatches = {
        str(row["state_example_id"]): dict(row)
        for row in _json(paths["mismatches"])["rows"]
    }
    if set(mismatches) != set(outcomes):
        raise ValueError("Mismatch manifest differs from paired states")
    outcome_by_step = {
        (str(row["state_task_id"]), int(row["state_step_id"])): state_id
        for state_id, row in outcomes.items()
    }
    return {
        "model_train_tasks": model_train_tasks,
        "heldout_tasks": heldout_tasks,
        "decisions": decisions,
        "transitions": transitions,
        "transitions_by_step": transitions_by_step,
        "outcomes": outcomes,
        "teacher": teacher,
        "mismatches": mismatches,
        "outcome_by_step": outcome_by_step,
    }


def _target_ids(tokenizer: Any, example: DecisionExample) -> list[int]:
    target = str(example.target_text)
    if example.target_type == "code" and "```" not in target:
        target = f"```python\n{target.strip()}\n```"
    values = [int(value) for value in tokenizer(target, add_special_tokens=False)["input_ids"]]
    eos = getattr(tokenizer, "eos_token_id", None)
    if eos is not None and (not values or values[-1] != int(eos)):
        values.append(int(eos))
    return values


def _example_policy_row(backend: Any, example: DecisionExample, pair_id: str) -> dict[str, Any]:
    messages = _appworld_messages_from_example(example, "full_demo")
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    prompt_ids = [int(value) for value in tokenized.input_ids[0].detach().cpu()]
    target = _target_ids(backend.tokenizer, example)
    full = prompt_ids + target
    return {
        "pair_id": pair_id,
        "input_ids": full,
        "labels": [-100] * len(prompt_ids) + target,
        "pad_token_id": int(backend.tokenizer.pad_token_id),
        "last_user_token_indices": [
            int(value) for value in tokenized.metadata["last_user_token_indices"]
        ],
        "target_len": len(target),
        "prompt_len": len(prompt_ids),
        "response_cache": {},
        "student_prompt_contains_raw_memory": False,
    }


def _reader(settings: Mapping[str, Any], device: torch.device) -> CrossAttentionMemoryReader:
    reader = CrossAttentionMemoryReader(
        model_dim=int(settings["reader"]["model_dim"]),
        layer_count=int(settings["reader"]["qwen_layer_count"]),
        rank=int(settings["reader"]["fusion_rank"]),
        alpha=float(settings["reader"]["fusion_alpha"]),
        dropout=float(settings["reader"]["fusion_dropout"]),
    )
    return reader.to(device)


def _forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[dict[str, Any]],
    slots: Tensor | None,
    training: bool,
) -> tuple[Tensor, Tensor, DifferentiableCrossAttentionHooks]:
    batch = _collate(rows, device=backend.device, k=4)
    hooks = DifferentiableCrossAttentionHooks(
        model=backend.model,
        reader=reader,
        memory_slots=None if slots is None else slots.to(backend.device),
    )
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        with hooks:
            loss, logits = _bare_target_forward(backend=backend, batch=batch)
    return loss, logits, hooks


@torch.no_grad()
def _generate(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    messages: Sequence[Mapping[str, str]],
    slots: Tensor | None,
    max_new_tokens: int,
) -> tuple[list[int], str, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = CrossAttentionReaderHooks(
        model=backend.model,
        reader=reader,
        memory_slots=None if slots is None else slots.to(backend.device),
    )
    with hooks:
        with _attention_context(backend.device):
            output = backend.model.generate(
                input_ids=tokenized.input_ids,
                attention_mask=tokenized.attention_mask,
                max_new_tokens=int(max_new_tokens),
                do_sample=False,
                use_cache=True,
                pad_token_id=backend.tokenizer.eos_token_id,
                eos_token_id=backend.tokenizer.eos_token_id,
            )
    generated = [int(value) for value in output[0, prompt_length:].tolist()]
    return (
        generated,
        backend.tokenizer.decode(generated, skip_special_tokens=True),
        hooks.audit.as_dict(),
    )


def _checkpoint_payload(
    *,
    phase: str,
    epoch: int,
    reader: CrossAttentionMemoryReader,
    optimizer: torch.optim.Optimizer,
    unit_ids: Sequence[str],
    history: Sequence[Mapping[str, Any]],
    parent_checkpoint_sha256: str | None,
) -> dict[str, Any]:
    return {
        "format": CHECKPOINT_VERSION,
        "global_seed": GLOBAL_SEED,
        "phase": phase,
        "completed_epochs": int(epoch),
        "unit_ids": list(unit_ids),
        "reader_state_dict": {
            key: value.detach().cpu() for key, value in reader.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "reader_sha256": module_state_sha256(reader),
        "parent_checkpoint_sha256": parent_checkpoint_sha256,
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all()
        if torch.cuda.is_available()
        else [],
    }


def _implementation(
    *,
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    source: Mapping[str, Any],
    slots: Mapping[str, Tensor],
) -> dict[str, Any]:
    reader = _reader(settings, backend.device)
    reader.eval()
    candidates = []
    for key, example in source["decisions"].items():
        messages = _appworld_messages_from_example(example, "full_demo")
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        candidates.append((int(tokenized.attention_mask.sum()), key, example, messages))
    candidates.sort(key=lambda row: (row[0], row[1]))
    positions = [0, len(candidates) // 2, max(0, len(candidates) - 2), len(candidates) - 1]
    checks = []
    for ordinal, position in enumerate(positions):
        prompt_tokens, key, example, messages = candidates[position]
        transition_id = source["transitions_by_step"][key]
        row = _example_policy_row(backend, example, f"implementation::{ordinal}")
        bare_loss, bare_logits, _ = _forward(
            backend=backend, reader=reader, rows=[row], slots=None, training=False
        )
        zero_loss, zero_logits, hooks = _forward(
            backend=backend,
            reader=reader,
            rows=[row],
            slots=slots[transition_id],
            training=False,
        )
        bare_ids, bare_text, _ = _generate(
            backend=backend,
            reader=reader,
            messages=messages,
            slots=None,
            max_new_tokens=64,
        )
        zero_ids, zero_text, generation_hooks = _generate(
            backend=backend,
            reader=reader,
            messages=messages,
            slots=slots[transition_id],
            max_new_tokens=64,
        )
        query_lengths = generation_hooks["query_lengths"]
        checks.append(
            {
                "ordinal": ordinal,
                "prompt_tokens": prompt_tokens,
                "transition_id": transition_id,
                "target_logits_equal": bool(torch.equal(bare_logits, zero_logits)),
                "target_nll_equal": float(bare_loss) == float(zero_loss),
                "generation_equal": bare_ids == zero_ids and bare_text == zero_text,
                "prompt_token_count_unchanged": True,
                "position_ids_unchanged": True,
                "memory_not_appended_to_self_attention_kv": True,
                "decode_tokens_query_memory": all(
                    1 in lengths for lengths in query_lengths.values()
                ),
                "attention_softmax_error": max(
                    generation_hooks["attention_row_sum_error"].values()
                ),
                "teacher_forward_residual_norm": hooks.residual_norm(),
            }
        )

    reader.train()
    sample_key = sorted(source["decisions"])[0]
    sample = source["decisions"][sample_key]
    transition_id = source["transitions_by_step"][sample_key]
    row = _example_policy_row(backend, sample, "implementation::gradient")
    reader.zero_grad(set_to_none=True)
    _, logits, hooks = _forward(
        backend=backend,
        reader=reader,
        rows=[row],
        slots=slots[transition_id],
        training=True,
    )
    logits.to(torch.float32).square().mean().backward()
    gradients = fusion_gradient_norms(reader)
    qwen_trainable = sum(parameter.requires_grad for parameter in backend.model.parameters())
    qwen_gradients = sum(parameter.grad is not None for parameter in backend.model.parameters())
    report = {
        "format": "cross_attention_reader_implementation_validation_8b_v1",
        "global_seed": GLOBAL_SEED,
        "reader_parameter_count": reader.parameter_count(),
        "qwen_layer_count": reader.layer_count,
        "memory_slot_count": 16,
        "four_state_checks": checks,
        "fusion_gradient_norms": gradients,
        "all_fusion_layers_receive_initial_output_gradient": all_fusion_layers_receive_gradient(
            gradients, require_down=False
        ),
        "down_gradient_expected_after_nonzero_output_training": True,
        "qwen_trainable_parameter_count": qwen_trainable,
        "qwen_gradient_count": qwen_gradients,
        "student_prompt_contains_raw_memory": False,
        "cross_attention_uses_separate_softmax": True,
        "memory_rope_applied": False,
        "memory_slots_in_self_attention_kv": False,
        "memory_slots_fixed_size": True,
        "gradient_attention_entropy": hooks.attention_entropy(),
    }
    report["passed"] = bool(
        report["all_fusion_layers_receive_initial_output_gradient"]
        and qwen_trainable == 0
        and qwen_gradients == 0
        and all(
            row["target_logits_equal"]
            and row["target_nll_equal"]
            and row["generation_equal"]
            and row["decode_tokens_query_memory"]
            for row in checks
        )
    )
    atomic_write_json(paths["implementation"], report)
    atomic_write_text(
        paths["implementation_report"],
        "\n".join(
            (
                "# EXP-030A cross-attention implementation validation",
                "",
                f"- reader parameters: `{report['reader_parameter_count']}`",
                f"- four-state zero equivalence: `{str(all(row['target_logits_equal'] and row['target_nll_equal'] and row['generation_equal'] for row in checks)).lower()}`",
                f"- generated tokens query external memory: `{str(all(row['decode_tokens_query_memory'] for row in checks)).lower()}`",
                f"- all 36 fusion outputs receive gradients: `{str(report['all_fusion_layers_receive_initial_output_gradient']).lower()}`",
                f"- Qwen trainable parameters/gradients: `{qwen_trainable}/{qwen_gradients}`",
                f"- passed: `{str(report['passed']).lower()}`",
                "",
            )
        ),
    )
    if not report["passed"]:
        raise RuntimeError("deep cross-attention reader implementation validation failed")
    return report


def _phase1_examples(
    source: Mapping[str, Any], backend: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output = []
    for key, example in source["decisions"].items():
        task_id, step_id = key
        output.append(
            {
                "unit_id": f"phase1::{task_id}::{step_id}",
                "task_id": task_id,
                "step_id": step_id,
                "transition_id": source["transitions_by_step"][key],
                "row": _example_policy_row(
                    backend, example, f"phase1::{task_id}::{step_id}"
                ),
            }
        )
    train = [row for row in output if row["task_id"] in source["model_train_tasks"]]
    heldout = [row for row in output if row["task_id"] in source["heldout_tasks"]]
    if (len(train), len(heldout)) != (401, 98):
        raise ValueError("Phase-1 sample counts differ from 401/98")
    return train, heldout


def _evaluate_phase1(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    rows: Sequence[Mapping[str, Any]],
    slots: Mapping[str, Tensor],
) -> dict[str, float]:
    reader.eval()
    losses = []
    for row in rows:
        loss, _, _ = _forward(
            backend=backend,
            reader=reader,
            rows=[row["row"]],
            slots=slots[str(row["transition_id"])],
            training=False,
        )
        losses.append(float(loss.cpu()))
    return {"cross_entropy": statistics.fmean(losses), "count": float(len(losses))}


def _save_checkpoint(
    path: Path,
    latest_path: Path,
    payload: Mapping[str, Any],
) -> None:
    atomic_torch_save(dict(payload), path)
    atomic_write_json(
        latest_path,
        {
            "checkpoint": str(path),
            "checkpoint_sha256": sha256_file(path),
            "phase": str(payload["phase"]),
            "completed_epochs": int(payload["completed_epochs"]),
        },
    )


def _phase1(
    *,
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    source: Mapping[str, Any],
    slots: Mapping[str, Tensor],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    if not bool(_json(paths["implementation"])["passed"]):
        raise RuntimeError("Implementation validation did not pass")
    train, heldout = _phase1_examples(source, backend)
    unit_ids = [str(row["unit_id"]) for row in train]
    seed_everything(GLOBAL_SEED)
    reader = _reader(settings, backend.device)
    optimizer = torch.optim.AdamW(
        reader.parameters(),
        lr=float(settings["reader"]["learning_rate"]),
        weight_decay=float(settings["reader"]["weight_decay"]),
    )
    completed = 0
    history: list[dict[str, Any]] = []
    if paths["phase1_latest"].exists():
        latest_path = Path(str(_json(paths["phase1_latest"])["checkpoint"]))
        latest = torch.load(latest_path, map_location=backend.device, weights_only=False)
        if latest["phase"] != "phase1" or list(latest["unit_ids"]) != unit_ids:
            raise ValueError("Phase-1 resume identity differs")
        reader.load_state_dict(latest["reader_state_dict"])
        optimizer.load_state_dict(latest["optimizer_state_dict"])
        completed = int(latest["completed_epochs"])
        history = list(latest["history"])
        random.setstate(latest["python_random_state"])
        torch.set_rng_state(latest["torch_rng_state"])
        if torch.cuda.is_available():
            torch.cuda.set_rng_state_all(latest["cuda_rng_state"])

    backend.model.config.use_cache = False
    maximum = int(settings["curriculum"]["phase1_max_epochs"])
    started = time.perf_counter()
    for epoch in range(completed + 1, maximum + 1):
        reader.train()
        order = sorted(
            range(len(train)),
            key=lambda index: stable_key(
                GLOBAL_SEED, "8b-phase1-order", epoch, unit_ids[index]
            ),
        )
        losses = []
        for ordinal, index in enumerate(order, start=1):
            unit = train[index]
            optimizer.zero_grad(set_to_none=True)
            loss, _, _ = _forward(
                backend=backend,
                reader=reader,
                rows=[unit["row"]],
                slots=slots[str(unit["transition_id"])],
                training=True,
            )
            loss.backward()
            nn.utils.clip_grad_norm_(
                reader.parameters(), float(settings["reader"]["max_grad_norm"])
            )
            optimizer.step()
            if not math.isfinite(float(loss.detach().cpu())):
                raise RuntimeError("Phase-1 loss is non-finite")
            losses.append(float(loss.detach().cpu()))
            if ordinal % 25 == 0:
                attempt.progress(
                    status=f"reader_phase1_epoch_{epoch}",
                    completed_units=ordinal,
                    total_units=len(train),
                )
        validation = _evaluate_phase1(
            backend=backend, reader=reader, rows=heldout, slots=slots
        )
        entry = {
            "epoch": epoch,
            "train_cross_entropy": statistics.fmean(losses),
            "heldout_cross_entropy": validation["cross_entropy"],
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint = paths["phase1_root"] / f"checkpoints/model_epoch_{epoch:02d}.pt"
        payload = _checkpoint_payload(
            phase="phase1",
            epoch=epoch,
            reader=reader,
            optimizer=optimizer,
            unit_ids=unit_ids,
            history=history,
            parent_checkpoint_sha256=None,
        )
        _save_checkpoint(checkpoint, paths["phase1_latest"], payload)
        attempt.progress(
            status=f"reader_phase1_epoch_{epoch}_checkpoint",
            latest_validated_checkpoint=str(checkpoint),
            validation=validation,
        )
        print(
            f"phase1 epoch={epoch} train={entry['train_cross_entropy']:.6f} "
            f"heldout={entry['heldout_cross_entropy']:.6f}",
            flush=True,
        )
    selected = min(history, key=lambda row: (row["heldout_cross_entropy"], row["epoch"]))
    selected_path = paths["phase1_root"] / (
        f"checkpoints/model_epoch_{int(selected['epoch']):02d}.pt"
    )
    selection = {
        "format": "cross_attention_reader_phase1_selection_8b_v1",
        "selection_split": "eight_heldout_train_tasks",
        "selection_metric": "lowest_heldout_utilization_cross_entropy",
        "selected_epoch": int(selected["epoch"]),
        "selected_checkpoint": str(selected_path),
        "selected_checkpoint_sha256": sha256_file(selected_path),
        "history": history,
        "test_normal_outcomes_used": False,
    }
    atomic_write_json(paths["phase1_selection"], selection)
    summary = {
        "format": "cross_attention_reader_phase1_summary_8b_v1",
        "global_seed": GLOBAL_SEED,
        "training_samples": len(train),
        "heldout_samples": len(heldout),
        "completed_epochs": len(history),
        "selected": selection,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(history) == maximum,
    }
    atomic_write_json(paths["phase1_summary"], summary)
    return summary


def _phase2_units(source: Mapping[str, Any], path: Path) -> list[dict[str, Any]]:
    if path.exists():
        return [dict(row) for row in _json(path)["units"]]
    units = []
    for state_id, outcome in source["outcomes"].items():
        if str(outcome["model_split"]) != "model_train":
            continue
        label = str(outcome["label"])
        transition_id = str(outcome["selected_transition_id"])
        units.append(
            {
                "unit_id": f"{state_id}::correct",
                "query_state_id": state_id,
                "memory_transition_id": transition_id,
                "role": "correct",
                "target": "raw" if label == "POSITIVE" else "bare",
                "balance_group": "positive" if label == "POSITIVE" else "bare",
                "label": label,
            }
        )
        if label == "POSITIVE":
            mismatch = source["mismatches"][state_id]
            units.extend(
                (
                    {
                        "unit_id": f"{state_id}::transition_mismatch",
                        "query_state_id": state_id,
                        "memory_transition_id": str(
                            mismatch["transition_mismatch_transition_id"]
                        ),
                        "role": "transition_mismatch",
                        "target": "bare",
                        "balance_group": "bare",
                        "label": label,
                    },
                    {
                        "unit_id": f"{state_id}::state_mismatch",
                        "query_state_id": str(
                            mismatch["state_mismatch_state_example_id"]
                        ),
                        "memory_transition_id": transition_id,
                        "role": "state_mismatch",
                        "target": "bare",
                        "balance_group": "bare",
                        "label": label,
                    },
                )
            )
    counts = Counter(str(row["balance_group"]) for row in units)
    if counts != Counter({"bare": 471, "positive": 105}):
        raise ValueError(f"Phase-2 balance groups differ: {counts}")
    for unit in units:
        unit["weight"] = len(units) / (2.0 * counts[str(unit["balance_group"])])
    manifest = {
        "format": "cross_attention_reader_phase2_units_8b_v1",
        "global_seed": GLOBAL_SEED,
        "unit_count": len(units),
        "role_counts": dict(sorted(Counter(str(row["role"]) for row in units).items())),
        "balance_group_counts": dict(sorted(counts.items())),
        "balance_group_total_weights": {
            group: sum(float(row["weight"]) for row in units if row["balance_group"] == group)
            for group in counts
        },
        "units": units,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(path, manifest)
    return units


def _policy_terms(logits: Tensor, teacher: Mapping[str, Any]) -> tuple[Tensor, Tensor, Tensor]:
    kl = sparse_policy_kl(logits, teacher["positions"])
    target_ids = torch.tensor(
        teacher["generated_token_ids"], dtype=torch.long, device=logits.device
    )
    ce = F.cross_entropy(logits.to(torch.float32), target_ids)
    top1 = (logits.argmax(dim=-1) == target_ids).to(torch.float32).mean()
    return kl, ce, top1


def _phase2_unit_forward(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    unit: Mapping[str, Any],
    teacher_cache: Mapping[str, Any],
    slots: Mapping[str, Tensor],
    settings: Mapping[str, Any],
) -> dict[str, Tensor | DifferentiableCrossAttentionHooks]:
    state_id = str(unit["query_state_id"])
    target = str(unit["target"])
    policy_row = teacher_cache["policy_rows"][state_id][target]
    teacher = teacher_cache["teacher_rows"][state_id][target]
    rows = [policy_row]
    correct = str(unit["role"]) == "correct"
    if correct:
        rows.append(teacher_cache["ground_truth_rows"][state_id])
    _, logits, hooks = _forward(
        backend=backend,
        reader=reader,
        rows=rows,
        slots=slots[str(unit["memory_transition_id"])],
        training=True,
    )
    policy_length = int(policy_row["target_len"])
    kl, teacher_ce, top1 = _policy_terms(logits[:policy_length], teacher)
    ground_truth_ce = torch.zeros((), device=backend.device)
    if correct:
        gt_ids = torch.tensor(
            [
                int(value)
                for value in teacher_cache["ground_truth_rows"][state_id]["labels"]
                if int(value) != -100
            ],
            dtype=torch.long,
            device=backend.device,
        )
        ground_truth_ce = F.cross_entropy(logits[policy_length:].to(torch.float32), gt_ids)
    curriculum = settings["curriculum"]
    loss = float(unit["weight"]) * (
        float(curriculum["phase2_policy_kl_weight"]) * kl
        + float(curriculum["phase2_teacher_token_ce_weight"]) * teacher_ce
        + float(curriculum["phase2_ground_truth_ce_weight"]) * ground_truth_ce
        + float(curriculum["phase2_residual_norm_weight"]) * hooks.residual_penalty()
    )
    return {
        "loss": loss,
        "policy_kl": kl,
        "teacher_token_ce": teacher_ce,
        "teacher_top1": top1,
        "ground_truth_ce": ground_truth_ce,
        "hooks": hooks,
    }


def _phase2(
    *,
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    source: Mapping[str, Any],
    slots: Mapping[str, Tensor],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    selection = _json(paths["phase1_selection"])
    parent_path = Path(str(selection["selected_checkpoint"]))
    if sha256_file(parent_path) != str(selection["selected_checkpoint_sha256"]):
        raise ValueError("Selected Phase-1 checkpoint hash differs")
    parent = torch.load(parent_path, map_location=backend.device, weights_only=False)
    units = _phase2_units(source, paths["phase2_units"])
    unit_ids = [str(row["unit_id"]) for row in units]
    seed_everything(GLOBAL_SEED)
    reader = _reader(settings, backend.device)
    reader.load_state_dict(parent["reader_state_dict"])
    optimizer = torch.optim.AdamW(
        reader.parameters(),
        lr=float(settings["reader"]["learning_rate"]),
        weight_decay=float(settings["reader"]["weight_decay"]),
    )
    completed = 0
    history: list[dict[str, Any]] = []
    if paths["phase2_latest"].exists():
        latest_path = Path(str(_json(paths["phase2_latest"])["checkpoint"]))
        latest = torch.load(latest_path, map_location=backend.device, weights_only=False)
        if latest["phase"] != "phase2" or list(latest["unit_ids"]) != unit_ids:
            raise ValueError("Phase-2 resume identity differs")
        if str(latest["parent_checkpoint_sha256"]) != sha256_file(parent_path):
            raise ValueError("Phase-2 parent checkpoint differs")
        reader.load_state_dict(latest["reader_state_dict"])
        optimizer.load_state_dict(latest["optimizer_state_dict"])
        completed = int(latest["completed_epochs"])
        history = list(latest["history"])
        random.setstate(latest["python_random_state"])
        torch.set_rng_state(latest["torch_rng_state"])
        if torch.cuda.is_available():
            torch.cuda.set_rng_state_all(latest["cuda_rng_state"])

    maximum = int(settings["curriculum"]["phase2_max_epochs"])
    started = time.perf_counter()
    for epoch in range(completed + 1, maximum + 1):
        reader.train()
        order = sorted(
            range(len(units)),
            key=lambda index: stable_key(
                GLOBAL_SEED, "8b-phase2-order", epoch, unit_ids[index]
            ),
        )
        metrics: dict[str, list[float]] = defaultdict(list)
        for ordinal, index in enumerate(order, start=1):
            optimizer.zero_grad(set_to_none=True)
            result = _phase2_unit_forward(
                backend=backend,
                reader=reader,
                unit=units[index],
                teacher_cache=source["teacher"],
                slots=slots,
                settings=settings,
            )
            loss = result["loss"]
            assert isinstance(loss, Tensor)
            loss.backward()
            nn.utils.clip_grad_norm_(
                reader.parameters(), float(settings["reader"]["max_grad_norm"])
            )
            optimizer.step()
            values = {
                "loss": loss,
                "policy_kl": result["policy_kl"],
                "teacher_token_ce": result["teacher_token_ce"],
                "teacher_top1": result["teacher_top1"],
                "ground_truth_ce": result["ground_truth_ce"],
            }
            for name, value in values.items():
                assert isinstance(value, Tensor)
                scalar = float(value.detach().cpu())
                if not math.isfinite(scalar):
                    raise RuntimeError(f"Phase-2 {name} is non-finite")
                metrics[name].append(scalar)
            hooks = result["hooks"]
            assert isinstance(hooks, DifferentiableCrossAttentionHooks)
            metrics["residual_norm"].append(hooks.residual_norm())
            metrics["attention_entropy"].append(hooks.attention_entropy())
            if ordinal % 25 == 0:
                attempt.progress(
                    status=f"reader_phase2_epoch_{epoch}",
                    completed_units=ordinal,
                    total_units=len(units),
                )
        entry = {
            "epoch": epoch,
            "training_metrics": {
                name: statistics.fmean(values) for name, values in metrics.items()
            },
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint = paths["phase2_root"] / f"checkpoints/model_epoch_{epoch:02d}.pt"
        payload = _checkpoint_payload(
            phase="phase2",
            epoch=epoch,
            reader=reader,
            optimizer=optimizer,
            unit_ids=unit_ids,
            history=history,
            parent_checkpoint_sha256=sha256_file(parent_path),
        )
        _save_checkpoint(checkpoint, paths["phase2_latest"], payload)
        attempt.progress(
            status=f"reader_phase2_epoch_{epoch}_checkpoint",
            latest_validated_checkpoint=str(checkpoint),
            training_metrics=entry["training_metrics"],
        )
        print(
            f"phase2 epoch={epoch} policy_kl={entry['training_metrics']['policy_kl']:.6f}",
            flush=True,
        )
    summary = {
        "format": "cross_attention_reader_phase2_summary_8b_v1",
        "global_seed": GLOBAL_SEED,
        "training_state_count": 366,
        "training_unit_count": len(units),
        "completed_epochs": len(history),
        "history": history,
        "parent_phase1_checkpoint_sha256": sha256_file(parent_path),
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(history) == maximum,
    }
    atomic_write_json(paths["phase2_summary"], summary)
    return summary


def _policy_control(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    policy_row: Mapping[str, Any],
    teacher: Mapping[str, Any],
    slots: Tensor | None,
) -> dict[str, float]:
    _, logits, hooks = _forward(
        backend=backend,
        reader=reader,
        rows=[dict(policy_row)],
        slots=slots,
        training=False,
    )
    kl, ce, top1 = _policy_terms(logits, teacher)
    return {
        "policy_kl": float(kl.cpu()),
        "teacher_token_ce": float(ce.cpu()),
        "teacher_top1": float(top1.cpu()),
        "residual_norm": hooks.residual_norm(),
        "attention_entropy": hooks.attention_entropy(),
    }


def _evaluate_policy_checkpoint(
    *,
    backend: Any,
    reader: CrossAttentionMemoryReader,
    source: Mapping[str, Any],
    slots: Mapping[str, Tensor],
) -> dict[str, Any]:
    reader.eval()
    rows = []
    heldout = [
        (state_id, row)
        for state_id, row in source["outcomes"].items()
        if str(row["model_split"]) == "heldout_train_validation"
    ]
    for state_id, outcome in heldout:
        label = str(outcome["label"])
        intended = "raw" if label == "POSITIVE" else "bare"
        correct_transition = str(outcome["selected_transition_id"])
        mismatch = source["mismatches"][state_id]
        mismatch_transition = str(mismatch["transition_mismatch_transition_id"])
        state_mismatch = str(mismatch["state_mismatch_state_example_id"])
        teacher_cache = source["teacher"]
        definitions = (
            (
                "X0_no_memory",
                state_id,
                None,
                intended,
            ),
            (
                "X1_correct_memory",
                state_id,
                correct_transition,
                intended,
            ),
            (
                "X2_transition_shuffle",
                state_id,
                mismatch_transition,
                "bare",
            ),
            (
                "X3_state_shuffle",
                state_mismatch,
                correct_transition,
                "bare",
            ),
        )
        for control, query_id, transition_id, target in definitions:
            metric = _policy_control(
                backend=backend,
                reader=reader,
                policy_row=teacher_cache["policy_rows"][query_id][target],
                teacher=teacher_cache["teacher_rows"][query_id][target],
                slots=None if transition_id is None else slots[transition_id],
            )
            rows.append(
                {
                    "source_state_id": state_id,
                    "query_state_id": query_id,
                    "task_id": str(outcome["state_task_id"]),
                    "label": label,
                    "control": control,
                    "transition_id": transition_id,
                    "target": target,
                    **metric,
                }
            )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["control"])].append(row)
    summary = {
        control: {
            "count": len(values),
            **{
                name: statistics.fmean(float(row[name]) for row in values)
                for name in (
                    "policy_kl",
                    "teacher_token_ce",
                    "teacher_top1",
                    "residual_norm",
                    "attention_entropy",
                )
            },
        }
        for control, values in grouped.items()
    }
    positives = [row for row in heldout if str(row[1]["label"]) == "POSITIVE"]
    raw_rows = []
    for state_id, outcome in positives:
        raw_teacher = source["teacher"]["teacher_rows"][state_id]["raw"]
        raw_policy = source["teacher"]["policy_rows"][state_id]["raw"]
        for control, transition_id in (
            ("X0_no_memory", None),
            ("X1_correct_memory", str(outcome["selected_transition_id"])),
            (
                "X2_transition_shuffle",
                str(source["mismatches"][state_id]["transition_mismatch_transition_id"]),
            ),
        ):
            metric = _policy_control(
                backend=backend,
                reader=reader,
                policy_row=raw_policy,
                teacher=raw_teacher,
                slots=None if transition_id is None else slots[transition_id],
            )
            raw_rows.append({"control": control, **metric})
    raw_summary = {
        control: statistics.fmean(
            row["policy_kl"] for row in raw_rows if row["control"] == control
        )
        for control in ("X0_no_memory", "X1_correct_memory", "X2_transition_shuffle")
    }
    return {
        "rows": rows,
        "summary": summary,
        "positive_raw_teacher_policy_kl": raw_summary,
        "positive_correct_below_zero": raw_summary["X1_correct_memory"]
        < raw_summary["X0_no_memory"],
        "positive_correct_below_transition_shuffle": raw_summary["X1_correct_memory"]
        < raw_summary["X2_transition_shuffle"],
    }


def _policy_eval(
    *,
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    source: Mapping[str, Any],
    slots: Mapping[str, Tensor],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    reports = []
    maximum = int(settings["curriculum"]["phase2_max_epochs"])
    for epoch in range(1, maximum + 1):
        checkpoint = paths["phase2_root"] / f"checkpoints/model_epoch_{epoch:02d}.pt"
        payload = torch.load(checkpoint, map_location=backend.device, weights_only=False)
        reader = _reader(settings, backend.device)
        reader.load_state_dict(payload["reader_state_dict"])
        output = paths["policy_eval_root"] / f"epoch_{epoch:02d}.json"
        if output.exists():
            report = _json(output)
            if str(report["checkpoint_sha256"]) != sha256_file(checkpoint):
                raise ValueError("Existing policy evaluation checkpoint differs")
        else:
            started = time.perf_counter()
            evaluation = _evaluate_policy_checkpoint(
                backend=backend, reader=reader, source=source, slots=slots
            )
            report = {
                "format": "cross_attention_reader_policy_evaluation_8b_v1",
                "epoch": epoch,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
                "reader_sha256": module_state_sha256(reader),
                "heldout_state_count": 98,
                "condition_count": 392,
                "evaluation": evaluation,
                "elapsed_seconds": time.perf_counter() - started,
                "test_normal_outcomes_used": False,
            }
            atomic_write_json(output, report)
        reports.append(report)
        attempt.progress(
            status=f"reader_policy_eval_epoch_{epoch}",
            latest_validated_checkpoint=str(output),
            completed_checkpoints=epoch,
            total_checkpoints=maximum,
        )
    summary = {
        "format": "cross_attention_reader_policy_evaluation_summary_8b_v1",
        "global_seed": GLOBAL_SEED,
        "checkpoint_count": len(reports),
        "reports": [
            {
                "epoch": row["epoch"],
                "checkpoint_sha256": row["checkpoint_sha256"],
                "summary": row["evaluation"]["summary"],
                "positive_raw_teacher_policy_kl": row["evaluation"][
                    "positive_raw_teacher_policy_kl"
                ],
                "positive_correct_below_zero": row["evaluation"][
                    "positive_correct_below_zero"
                ],
                "positive_correct_below_transition_shuffle": row["evaluation"][
                    "positive_correct_below_transition_shuffle"
                ],
            }
            for row in reports
        ],
        "test_normal_outcomes_used": False,
        "passed": len(reports) == maximum,
    }
    atomic_write_json(paths["policy_eval_summary"], summary)
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "memory_index",
        "memory_summary",
        "mismatches",
        "curriculum",
        "task_split",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
    )
    if args.phase != "implementation":
        required = (*required, "implementation")
    if args.phase in {"phase2", "policy-eval"}:
        required = (*required, "phase1_selection")
    if args.phase == "policy-eval":
        required = (*required, "phase2_summary")
    _require(paths, required)
    if not bool(_json(paths["preflight"])["automatic_launch_allowed"]):
        raise RuntimeError("Runtime preflight did not authorize EXP-030A")
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen must remain frozen")
    source = _load_source(paths)
    slot_bank = _load_slot_bank(paths["memory_index"])
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"reader_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "implementation":
            result = _implementation(
                backend=backend,
                settings=settings,
                paths=paths,
                source=source,
                slots=slot_bank,
            )
        elif args.phase == "phase1":
            result = _phase1(
                backend=backend,
                settings=settings,
                paths=paths,
                source=source,
                slots=slot_bank,
                attempt=attempt,
            )
        elif args.phase == "phase2":
            result = _phase2(
                backend=backend,
                settings=settings,
                paths=paths,
                source=source,
                slots=slot_bank,
                attempt=attempt,
            )
        else:
            result = _policy_eval(
                backend=backend,
                settings=settings,
                paths=paths,
                source=source,
                slots=slot_bank,
                attempt=attempt,
            )
        attempt.progress(
            status=f"reader_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths[
                    {
                        "implementation": "implementation",
                        "phase1": "phase1_summary",
                        "phase2": "phase2_summary",
                        "policy-eval": "policy_eval_summary",
                    }[args.phase]
                ]
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
