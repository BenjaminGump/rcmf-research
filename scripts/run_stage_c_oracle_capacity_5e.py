from __future__ import annotations

import argparse
import copy
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.injection.base import build_position_ids
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.training.addressing_4b import _pearson, mean_std
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_capacity_5e import (
    OBJECTIVES,
    ORACLE_CAPACITY_VERSION,
    FreeMemoryLatentTable,
    FreePairLatentTable,
    ObjectiveSpec,
    perturbation_ratios,
    project_delta_slots_to_ratio_,
    scatter_token_delta,
    select_balanced_validation_subset,
    select_last_user_k_indices,
    stage_5e_decision,
    summarize_oracle_rows,
    validate_target_token_utility_identity,
)
from rcmf.training.pair_grounding_5d import (
    PAIR_RESPONSE_CACHE_VERSION,
    POSITIVE_UTILITY_EPS,
    paired_bootstrap_ci,
    spearman,
)
from rcmf.training.stage_c1 import sparse_bucket_kl
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, maybe_git_commit, read_jsonl, sha256_file, write_jsonl
from scripts.run_stage_c1_signed_program import _initialize_injector
from scripts.run_stage_c_pair_grounding_5d import _build_tokenized_pair_rows
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend


PROB_EPS = 1.0e-12


def utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _select_by_pair_ids(rows: Sequence[dict[str, Any]], pair_ids: set[str]) -> list[dict[str, Any]]:
    by_id = {str(row["pair_id"]): row for row in rows}
    return [by_id[pair_id] for pair_id in sorted(pair_ids) if pair_id in by_id]


def _collate(rows: Sequence[dict[str, Any]], *, device: torch.device, k: int) -> dict[str, Any]:
    if not rows:
        raise ValueError("empty batch")
    max_len = max(len(row["input_ids"]) for row in rows)
    pad_id = int(rows[0]["pad_token_id"])
    input_ids = torch.full((len(rows), max_len), pad_id, dtype=torch.long, device=device)
    labels = torch.full((len(rows), max_len), -100, dtype=torch.long, device=device)
    attention_mask = torch.zeros((len(rows), max_len), dtype=torch.long, device=device)
    selected_indices = torch.full((len(rows), k), -1, dtype=torch.long, device=device)
    max_user = max(1, max(len(row["last_user_token_indices"]) for row in rows))
    injection_indices = torch.full((len(rows), max_user), -1, dtype=torch.long, device=device)
    for row_index, row in enumerate(rows):
        length = len(row["input_ids"])
        input_ids[row_index, :length] = torch.tensor(row["input_ids"], dtype=torch.long, device=device)
        labels[row_index, :length] = torch.tensor(row["labels"], dtype=torch.long, device=device)
        attention_mask[row_index, :length] = 1
        selected = select_last_user_k_indices(
            input_len=length,
            last_user_token_indices=row.get("last_user_token_indices", []),
            labels=row.get("labels"),
            k=k,
        )
        selected_indices[row_index] = torch.tensor(selected, dtype=torch.long, device=device)
        if row.get("last_user_token_indices"):
            values = row["last_user_token_indices"]
            injection_indices[row_index, : len(values)] = torch.tensor(values, dtype=torch.long, device=device)
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": attention_mask,
        "selected_indices": selected_indices,
        "injection_token_indices": injection_indices,
        "target_lengths": [int(row["target_len"]) for row in rows],
        "response_rows": [row["response_cache"] for row in rows],
        "pair_rows": list(rows),
    }


def _selected_base_embeddings(token_embeds: Tensor, selected_indices: Tensor) -> Tensor:
    rows = []
    for row_index in range(token_embeds.shape[0]):
        slots = []
        for token_index in selected_indices[row_index].tolist():
            if int(token_index) < 0:
                slots.append(torch.zeros(token_embeds.shape[-1], device=token_embeds.device, dtype=token_embeds.dtype))
            else:
                slots.append(token_embeds[row_index, int(token_index)])
        rows.append(torch.stack(slots, dim=0))
    return torch.stack(rows, dim=0)


def _forward_direct_delta(
    *,
    backend: Any,
    batch: dict[str, Any],
    delta_slots: Tensor,
) -> dict[str, Any]:
    model = backend.model
    input_ids = batch["input_ids"]
    labels = batch["labels"]
    attention_mask = batch["attention_mask"].to(torch.long)
    token_embeds = model.get_input_embeddings()(input_ids)
    selected_indices = batch["selected_indices"]
    embedding_delta = scatter_token_delta(
        base_embeddings=token_embeds,
        selected_indices=selected_indices,
        delta_slots=delta_slots.to(device=token_embeds.device, dtype=token_embeds.dtype),
    )
    inputs_embeds = token_embeds + embedding_delta
    loss, target_logits = backend._target_only_loss_from_hidden(  # noqa: SLF001
        model_inputs={
            "input_ids": input_ids,
            "inputs_embeds": inputs_embeds,
            "attention_mask": attention_mask,
            "position_ids": build_position_ids(attention_mask),
        },
        labels=labels,
        logit_bias=None,
    )
    base_selected = _selected_base_embeddings(token_embeds, selected_indices)
    ratios = perturbation_ratios(
        delta_slots=delta_slots.to(torch.float32),
        selected_base_embeddings=base_selected.to(torch.float32),
    )
    valid_delta_norms = delta_slots.to(torch.float32).flatten(start_dim=1).norm(dim=1)
    valid_base_norms = base_selected.to(torch.float32).flatten(start_dim=1).norm(dim=1)
    return {
        "loss": loss,
        "target_logits": target_logits,
        "delta": embedding_delta,
        "delta_ratio": ratios.mean(),
        "delta_ratios": ratios.detach(),
        "delta_norm": valid_delta_norms.mean(),
        "base_norm": valid_base_norms.mean(),
        "selected_token_indices": selected_indices.detach().cpu().tolist(),
    }


def _forward_injector(
    *,
    backend: Any,
    injector: AdditiveTokenMemoryInjector,
    batch: dict[str, Any],
    z: Tensor,
) -> dict[str, Any]:
    model = backend.model
    input_ids = batch["input_ids"]
    attention_mask = batch["attention_mask"]
    labels = batch["labels"]
    prepared = injector.prepare_train_inputs(
        model,
        input_ids=input_ids,
        attention_mask=attention_mask,
        labels=labels,
        memory_z=z,
        injection_token_indices=batch["injection_token_indices"],
    )
    token_embeds = model.get_input_embeddings()(input_ids)
    delta = prepared.inputs["inputs_embeds"] - token_embeds
    model_inputs = dict(prepared.inputs)
    labels_for_loss = model_inputs.pop("labels")
    loss, target_logits = backend._target_only_loss_from_hidden(  # noqa: SLF001
        model_inputs=model_inputs,
        labels=labels_for_loss,
        logit_bias=None,
    )
    selected_norms = []
    selected_base_norms = []
    for row_index, indices in enumerate(prepared.memory_metadata["selected_token_indices"]):
        valid = [int(index) for index in indices if int(index) >= 0]
        if not valid:
            continue
        valid_tensor = torch.tensor(valid, dtype=torch.long, device=input_ids.device)
        selected_norms.append(delta[row_index, valid_tensor].norm())
        selected_base_norms.append(token_embeds[row_index, valid_tensor].norm())
    delta_norm = torch.stack(selected_norms).mean() if selected_norms else delta.norm() * 0.0
    base_norm = torch.stack(selected_base_norms).mean() if selected_base_norms else token_embeds.norm().detach()
    return {
        "loss": loss,
        "target_logits": target_logits,
        "delta": delta,
        "delta_norm": delta_norm,
        "delta_ratio": delta_norm / base_norm.detach().clamp_min(1.0e-8),
        "memory_metadata": prepared.memory_metadata,
    }


def _custom_huber(error: Tensor, *, delta: float) -> Tensor:
    abs_error = error.abs()
    d = torch.as_tensor(float(delta), device=error.device, dtype=error.dtype)
    return torch.where(abs_error <= d, 0.5 * error.pow(2) / d.clamp_min(1.0e-12), abs_error - 0.5 * d)


def _loss_terms_from_logits(
    logits: Tensor,
    labels: Tensor,
    response_rows: Sequence[dict[str, Any]],
    *,
    target_lengths: Sequence[int],
    huber_delta: float,
) -> dict[str, Tensor]:
    losses: dict[str, list[Tensor]] = {
        "target_delta_huber": [],
        "target_delta_mse": [],
        "sparse_delta_huber": [],
        "sparse_delta_mse": [],
        "sparse_teacher_kl": [],
        "target_nll": [],
    }
    target_mask = labels[..., 1:].ne(-100)
    target_labels = labels[..., 1:][target_mask].to(logits.device)
    if target_labels.numel() != logits.shape[0]:
        raise ValueError(f"logit/label target count mismatch {logits.shape[0]} != {target_labels.numel()}")
    cursor = 0
    for response, target_len in zip(response_rows, target_lengths):
        target_len = int(target_len)
        row_logits = logits[cursor : cursor + target_len].to(torch.float32)
        row_target_labels = target_labels[cursor : cursor + target_len]
        row_log_probs = F.log_softmax(row_logits, dim=-1)
        nll = -row_log_probs[torch.arange(target_len, device=logits.device), row_target_labels].mean()
        target_errors = []
        sparse_errors = []
        sparse_mse = []
        sparse_kls = []
        for pos, item in enumerate(response["target_positions"]):
            position_logits = row_logits[pos].to(torch.float64)
            logsumexp = torch.logsumexp(position_logits, dim=-1)
            union_ids = torch.tensor(item["union_token_ids"], dtype=torch.long, device=logits.device)
            student_log_probs = position_logits[union_ids] - logsumexp
            union_prob = student_log_probs.exp().sum().clamp(min=0.0, max=1.0 - 1.0e-8)
            student_other_log_prob = torch.log1p(-union_prob)
            baseline_log_probs = torch.tensor(item["baseline_union_logprobs"], dtype=torch.float64, device=logits.device)
            teacher_log_probs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_teacher = torch.tensor(item["delta_teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_student = student_log_probs - baseline_log_probs
            baseline_other = torch.tensor(float(item["baseline_other_logprob"]), dtype=torch.float64, device=logits.device)
            teacher_other = torch.tensor(float(item["teacher_other_logprob"]), dtype=torch.float64, device=logits.device)
            delta_student_other = student_other_log_prob - baseline_other
            delta_teacher_other = teacher_other - baseline_other
            sparse_error = torch.cat([(delta_student - delta_teacher), (delta_student_other - delta_teacher_other).view(1)])
            sparse_errors.append(_custom_huber(sparse_error.to(torch.float32), delta=huber_delta).mean())
            sparse_mse.append(sparse_error.to(torch.float32).pow(2).mean())
            sparse_kls.append(
                sparse_bucket_kl(
                    student_log_probs,
                    student_other_log_prob,
                    teacher_log_probs,
                    torch.tensor(float(item["teacher_other_probability"]), dtype=torch.float64, device=logits.device),
                )
            )
            target_id = int(item["target_token_id"])
            target_delta_student = row_log_probs[pos, target_id].to(torch.float64) - float(item["baseline_target_logprob"])
            target_delta_teacher = float(item["teacher_target_logprob"]) - float(item["baseline_target_logprob"])
            target_errors.append((target_delta_student - target_delta_teacher).to(torch.float32))
        target_error_tensor = torch.stack(target_errors)
        losses["target_delta_huber"].append(_custom_huber(target_error_tensor, delta=huber_delta).mean())
        losses["target_delta_mse"].append(target_error_tensor.pow(2).mean())
        losses["sparse_delta_huber"].append(torch.stack(sparse_errors).mean())
        losses["sparse_delta_mse"].append(torch.stack(sparse_mse).mean())
        losses["sparse_teacher_kl"].append(torch.stack(sparse_kls).mean())
        losses["target_nll"].append(nll)
        cursor += target_len
    if cursor != logits.shape[0]:
        raise ValueError(f"target logits row count mismatch: cursor={cursor} logits={logits.shape[0]}")
    return {key: torch.stack(values).mean() for key, values in losses.items()}


def _objective_loss(terms: dict[str, Tensor], objective: ObjectiveSpec) -> Tensor:
    loss = terms["target_delta_huber"] * float(objective.target_delta_weight)
    loss = loss + terms["sparse_delta_huber"] * float(objective.sparse_delta_weight)
    loss = loss + terms["sparse_teacher_kl"] * float(objective.sparse_teacher_kl_weight)
    return loss


def _rows_from_logits(
    *,
    logits: Tensor,
    labels: Tensor,
    response_rows: Sequence[dict[str, Any]],
    target_lengths: Sequence[int],
    pair_rows: Sequence[dict[str, Any]],
    delta_ratios: Sequence[float] | Tensor | None,
    control: str,
    huber_delta: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    target_mask = labels[..., 1:].ne(-100)
    target_labels = labels[..., 1:][target_mask].to(logits.device)
    cursor = 0
    if isinstance(delta_ratios, Tensor):
        ratio_values = [float(value) for value in delta_ratios.detach().cpu().tolist()]
    elif delta_ratios is None:
        ratio_values = [None] * len(response_rows)
    else:
        ratio_values = [float(value) for value in delta_ratios]
    for row_index, (response, target_len, pair_row) in enumerate(zip(response_rows, target_lengths, pair_rows)):
        target_len = int(target_len)
        row_logits = logits[cursor : cursor + target_len].to(torch.float32)
        row_target_labels = target_labels[cursor : cursor + target_len]
        row_log_probs = F.log_softmax(row_logits, dim=-1)
        row_nll = -row_log_probs[torch.arange(target_len, device=logits.device), row_target_labels].mean()
        sparse_huber = []
        sparse_mse = []
        sparse_kl = []
        target_errors = []
        target_teacher = []
        target_student = []
        for pos, item in enumerate(response["target_positions"]):
            position_logits = row_logits[pos].to(torch.float64)
            logsumexp = torch.logsumexp(position_logits, dim=-1)
            union_ids = torch.tensor(item["union_token_ids"], dtype=torch.long, device=logits.device)
            student_log_probs = position_logits[union_ids] - logsumexp
            union_prob = student_log_probs.exp().sum().clamp(min=0.0, max=1.0 - 1.0e-8)
            student_other_log_prob = torch.log1p(-union_prob)
            baseline_log_probs = torch.tensor(item["baseline_union_logprobs"], dtype=torch.float64, device=logits.device)
            teacher_log_probs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_teacher = torch.tensor(item["delta_teacher_union_logprobs"], dtype=torch.float64, device=logits.device)
            delta_student = student_log_probs - baseline_log_probs
            baseline_other = torch.tensor(float(item["baseline_other_logprob"]), dtype=torch.float64, device=logits.device)
            teacher_other = torch.tensor(float(item["teacher_other_logprob"]), dtype=torch.float64, device=logits.device)
            sparse_error = torch.cat(
                [
                    (delta_student - delta_teacher),
                    (student_other_log_prob - baseline_other - (teacher_other - baseline_other)).view(1),
                ]
            )
            sparse_huber.append(float(_custom_huber(sparse_error.to(torch.float32), delta=huber_delta).mean().detach().cpu()))
            sparse_mse.append(float(sparse_error.to(torch.float32).pow(2).mean().detach().cpu()))
            sparse_kl.append(
                float(
                    sparse_bucket_kl(
                        student_log_probs,
                        student_other_log_prob,
                        teacher_log_probs,
                        torch.tensor(float(item["teacher_other_probability"]), dtype=torch.float64, device=logits.device),
                    )
                    .detach()
                    .cpu()
                )
            )
            target_id = int(item["target_token_id"])
            student_delta = float((row_log_probs[pos, target_id].to(torch.float64) - float(item["baseline_target_logprob"])).detach().cpu())
            teacher_delta = float(item["teacher_target_logprob"]) - float(item["baseline_target_logprob"])
            target_teacher.append(teacher_delta)
            target_student.append(student_delta)
            target_errors.append(student_delta - teacher_delta)
        target_huber = [
            float(_custom_huber(torch.tensor(value, dtype=torch.float32), delta=huber_delta).item())
            for value in target_errors
        ]
        out.append(
            {
                "pair_id": response["pair_id"],
                "pair_key": response["pair_key"],
                "state_example_id": response["state_example_id"],
                "memory_id": response["memory_id"],
                "split": response["split"],
                "selection_category": response["selection_category"],
                "utility_category": response["utility_category"],
                "memory_stage_index": int(response["memory_stage_index"]),
                "u_text": float(response["text_utility"]),
                "L0": float(response["baseline_mean_target_nll"]),
                "teacher_Lj_text": float(response["teacher_mean_target_nll"]),
                "student_target_nll": float(row_nll.detach().cpu()),
                "u_student": float(response["baseline_mean_target_nll"]) - float(row_nll.detach().cpu()),
                "target_delta_teacher": target_teacher,
                "target_delta_student": target_student,
                "target_token_delta_huber": sum(target_huber) / len(target_huber),
                "target_token_delta_mse": sum(value * value for value in target_errors) / len(target_errors),
                "target_token_delta_pearson": _pearson(target_teacher, target_student),
                "target_token_delta_spearman": spearman(target_teacher, target_student),
                "sparse_teacher_kl": sum(sparse_kl) / len(sparse_kl),
                "sparse_delta_huber": sum(sparse_huber) / len(sparse_huber),
                "sparse_delta_mse": sum(sparse_mse) / len(sparse_mse),
                "delta_ratio": ratio_values[row_index],
                "control": control,
                "target_tokens": response["target_tokens"],
                "prompt_tokens": response["prompt_tokens"],
                "raw_memory_tokens": response["raw_memory_tokens"],
                "source_state_task_id": pair_row.get("task_id"),
                "memory_task_id": pair_row.get("memory_task_id"),
            }
        )
        cursor += target_len
    return out


def _evaluate_direct_delta(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    delta_table: Tensor,
    pair_to_index: dict[str, int],
    device: torch.device,
    k: int,
    batch_size: int,
    huber_delta: float,
    control: str,
) -> dict[str, Any]:
    out_rows: list[dict[str, Any]] = []
    selected_token_report = None
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch_rows = list(rows[start : start + batch_size])
            batch = _collate(batch_rows, device=device, k=k)
            indices = torch.tensor([pair_to_index[str(row["pair_id"])] for row in batch_rows], dtype=torch.long, device=device)
            delta_slots = delta_table.index_select(0, indices).to(device)
            student = _forward_direct_delta(backend=backend, batch=batch, delta_slots=delta_slots)
            out_rows.extend(
                _rows_from_logits(
                    logits=student["target_logits"],
                    labels=batch["labels"],
                    response_rows=batch["response_rows"],
                    target_lengths=batch["target_lengths"],
                    pair_rows=batch_rows,
                    delta_ratios=student["delta_ratios"],
                    control=control,
                    huber_delta=huber_delta,
                )
            )
            if selected_token_report is None:
                selected = student["selected_token_indices"][0]
                ids = [int(batch["input_ids"][0, index].detach().cpu()) for index in selected if int(index) >= 0]
                selected_token_report = {
                    "selected_token_indices": selected,
                    "selected_token_ids": ids,
                    "selected_token_text": [backend.tokenizer.decode([token_id]) for token_id in ids],
                }
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    return {
        "rows": out_rows,
        "summary": summarize_oracle_rows(out_rows),
        "selected_token_report": selected_token_report,
    }


def _precompute_direct_base_norms(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    k: int,
) -> Tensor:
    norms = []
    with torch.no_grad():
        for row in rows:
            batch = _collate([row], device=device, k=k)
            token_embeds = backend.model.get_input_embeddings()(batch["input_ids"])
            selected_base = _selected_base_embeddings(token_embeds, batch["selected_indices"])
            norms.append(float(selected_base.to(torch.float32).flatten(start_dim=1).norm(dim=1).detach().cpu()[0]))
    return torch.tensor(norms, dtype=torch.float32)


def _train_direct_delta_oracle(
    *,
    backend: Any,
    rows: list[dict[str, Any]],
    objective: ObjectiveSpec,
    ratio_budget: float,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    epochs: int,
    batch_size: int,
    lr: float,
    progress_interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    torch.manual_seed(seed)
    random.seed(seed)
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    pair_to_index = {str(row["pair_id"]): index for index, row in enumerate(rows)}
    delta_table = nn.Parameter(torch.zeros(len(rows), k, model_dim, dtype=torch.float32, device=device))
    base_norms = _precompute_direct_base_norms(backend=backend, rows=rows, device=device, k=k).to(device)
    optimizer = torch.optim.AdamW([delta_table], lr=lr, weight_decay=0.0)
    history = []
    last_progress = 0.0
    for epoch in range(1, epochs + 1):
        rng = random.Random(seed * 1_000_000 + epoch)
        order = list(range(len(rows)))
        rng.shuffle(order)
        reports = []
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            row_indices = order[start : start + batch_size]
            batch_rows = [rows[index] for index in row_indices]
            batch = _collate(batch_rows, device=device, k=k)
            table_indices = torch.tensor(row_indices, dtype=torch.long, device=device)
            delta_slots = delta_table.index_select(0, table_indices)
            student = _forward_direct_delta(backend=backend, batch=batch, delta_slots=delta_slots)
            terms = _loss_terms_from_logits(
                student["target_logits"],
                batch["labels"],
                batch["response_rows"],
                target_lengths=batch["target_lengths"],
                huber_delta=objective.huber_delta,
            )
            loss = _objective_loss(terms, objective)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            with torch.no_grad():
                project_delta_slots_to_ratio_(
                    delta_table,
                    base_norms,
                    max_ratio=ratio_budget,
                    row_indices=table_indices,
                )
            reports.append(
                {
                    "loss": float(loss.detach().cpu()),
                    **{key: float(value.detach().cpu()) for key, value in terms.items()},
                    "delta_ratio": float(student["delta_ratio"].detach().cpu()),
                }
            )
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                print(
                    f"direct-delta objective={objective.name} ratio={ratio_budget} k={k} "
                    f"epoch={epoch}/{epochs} batch={batch_number} elapsed={(now-started)/3600.0:.2f}h",
                    flush=True,
                )
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        history.append(
            {
                "epoch": epoch,
                "metrics": {
                    key: mean_std(report[key] for report in reports if key in report)
                    for key in ("loss", "target_delta_huber", "sparse_delta_huber", "sparse_teacher_kl", "target_nll", "delta_ratio")
                },
            }
        )
        atomic_write_json(output_dir / f"direct_delta_{objective.name}_ratio{ratio_budget}_k{k}_history.json", history)
    trained_eval = _evaluate_direct_delta(
        backend=backend,
        rows=rows,
        delta_table=delta_table.detach(),
        pair_to_index=pair_to_index,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="direct_delta",
    )
    zero_eval = _evaluate_direct_delta(
        backend=backend,
        rows=rows,
        delta_table=torch.zeros_like(delta_table.detach()),
        pair_to_index=pair_to_index,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="zero_direct_delta",
    )
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 30000 + int(ratio_budget * 1000) + k)
    random_delta = torch.randn(delta_table.shape, generator=generator, dtype=torch.float32)
    trained_norms = delta_table.detach().cpu().flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_norms = random_delta.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_delta = random_delta * (trained_norms / random_norms).view(-1, 1, 1)
    random_eval = _evaluate_direct_delta(
        backend=backend,
        rows=rows,
        delta_table=random_delta.to(device),
        pair_to_index=pair_to_index,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="random_direct_delta_matched_norm",
    )
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"direct_delta_{objective.name}_ratio{ratio_budget}_k{k}.pt"
    torch.save(
        {
            "format": ORACLE_CAPACITY_VERSION,
            "component": "direct_delta",
            "objective": objective.name,
            "ratio_budget": ratio_budget,
            "k": k,
            "pair_ids": [row["pair_id"] for row in rows],
            "delta_table": delta_table.detach().cpu(),
            "base_norms": base_norms.detach().cpu(),
            "source_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    return {
        "format": "stage_c_direct_delta_oracle_5e_v1",
        "objective": objective.name,
        "ratio_budget": ratio_budget,
        "k": k,
        "epochs": epochs,
        "lr": lr,
        "checkpoint": str(checkpoint_path),
        "history": history,
        "evaluation": trained_eval,
        "controls": {"zero_direct_delta": zero_eval, "random_direct_delta_matched_norm": random_eval},
        "runtime_s": time.perf_counter() - started,
    }


def _capacity_injector(*, program_dim: int, model_dim: int, k: int, seed: int, initial_scale: float) -> AdditiveTokenMemoryInjector:
    injector = _initialize_injector(program_dim=program_dim, model_dim=model_dim, num_tokens=k, position="last_user_k", seed=seed)
    with torch.no_grad():
        injector.prefix_scale.fill_(float(initial_scale))
    return injector


def _latent_loss_for_batch(
    *,
    backend: Any,
    injector: AdditiveTokenMemoryInjector,
    rows: Sequence[dict[str, Any]],
    z: Tensor,
    objective: ObjectiveSpec,
    device: torch.device,
    k: int,
    ratio_target: float,
    ratio_penalty: float,
) -> tuple[Tensor, dict[str, Any]]:
    batch = _collate(rows, device=device, k=k)
    student = _forward_injector(backend=backend, injector=injector, batch=batch, z=z)
    terms = _loss_terms_from_logits(
        student["target_logits"],
        batch["labels"],
        batch["response_rows"],
        target_lengths=batch["target_lengths"],
        huber_delta=objective.huber_delta,
    )
    loss = _objective_loss(terms, objective)
    ratio_loss = F.relu(student["delta_ratio"].to(torch.float32) - float(ratio_target)).pow(2)
    loss = loss + float(ratio_penalty) * ratio_loss
    return loss, {
        "loss": float(loss.detach().cpu()),
        **{key: float(value.detach().cpu()) for key, value in terms.items()},
        "delta_ratio": float(student["delta_ratio"].detach().cpu()),
        "ratio_loss": float(ratio_loss.detach().cpu()),
    }


def _evaluate_pair_latents(
    *,
    backend: Any,
    injector: AdditiveTokenMemoryInjector,
    rows: Sequence[dict[str, Any]],
    z_provider: Any,
    objective: ObjectiveSpec,
    device: torch.device,
    k: int,
    batch_size: int,
    control: str,
) -> dict[str, Any]:
    out_rows: list[dict[str, Any]] = []
    selected_token_report = None
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch_rows = list(rows[start : start + batch_size])
            batch = _collate(batch_rows, device=device, k=k)
            z = z_provider(batch_rows).to(device)
            student = _forward_injector(backend=backend, injector=injector, batch=batch, z=z)
            ratio_values = [float(student["delta_ratio"].detach().cpu())] * len(batch_rows)
            out_rows.extend(
                _rows_from_logits(
                    logits=student["target_logits"],
                    labels=batch["labels"],
                    response_rows=batch["response_rows"],
                    target_lengths=batch["target_lengths"],
                    pair_rows=batch_rows,
                    delta_ratios=ratio_values,
                    control=control,
                    huber_delta=objective.huber_delta,
                )
            )
            if selected_token_report is None:
                selected = student["memory_metadata"]["selected_token_indices"][0]
                ids = [int(batch["input_ids"][0, index].detach().cpu()) for index in selected if int(index) >= 0]
                selected_token_report = {
                    "selected_token_indices": selected,
                    "selected_token_ids": ids,
                    "selected_token_text": [backend.tokenizer.decode([token_id]) for token_id in ids],
                }
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    return {
        "rows": out_rows,
        "summary": summarize_oracle_rows(out_rows),
        "selected_token_report": selected_token_report,
    }


def _train_pair_z_injector(
    *,
    backend: Any,
    train_rows: list[dict[str, Any]],
    objective: ObjectiveSpec,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    epochs: int,
    batch_size: int,
    lr: float,
    initial_scale: float,
    ratio_target: float,
    ratio_penalty: float,
    progress_interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    torch.manual_seed(seed + 41000)
    random.seed(seed + 41000)
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    table = FreePairLatentTable([row["pair_id"] for row in train_rows], 128, init_std=0.0).to(device)
    injector = _capacity_injector(program_dim=128, model_dim=model_dim, k=k, seed=seed + 41000, initial_scale=initial_scale).to(device)
    optimizer = torch.optim.AdamW(list(table.parameters()) + list(injector.parameters()), lr=lr, weight_decay=0.0)
    history = []
    last_progress = 0.0
    for epoch in range(1, epochs + 1):
        rng = random.Random(seed * 1_000_000 + 41000 + epoch)
        order = list(range(len(train_rows)))
        rng.shuffle(order)
        reports = []
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            batch_rows = [train_rows[index] for index in order[start : start + batch_size]]
            z = table(batch_rows)
            loss, report = _latent_loss_for_batch(
                backend=backend,
                injector=injector,
                rows=batch_rows,
                z=z,
                objective=objective,
                device=device,
                k=k,
                ratio_target=ratio_target,
                ratio_penalty=ratio_penalty,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(list(table.parameters()) + list(injector.parameters()), 1.0)
            optimizer.step()
            reports.append(report)
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                print(f"pair-z train epoch={epoch}/{epochs} batch={batch_number} elapsed={(now-started)/3600.0:.2f}h", flush=True)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        history.append(
            {
                "epoch": epoch,
                "metrics": {
                    key: mean_std(report[key] for report in reports if key in report)
                    for key in ("loss", "target_delta_huber", "sparse_delta_huber", "sparse_teacher_kl", "target_nll", "delta_ratio")
                },
            }
        )
        atomic_write_json(output_dir / "pair_z_train_history.json", history)
    train_eval = _evaluate_pair_latents(
        backend=backend,
        injector=injector,
        rows=train_rows,
        z_provider=table,
        objective=objective,
        device=device,
        k=k,
        batch_size=batch_size,
        control="free_train_pair_z",
    )
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / "pair_z_trained_injector.pt"
    torch.save(
        {
            "format": ORACLE_CAPACITY_VERSION,
            "component": "pair_z_trained_injector",
            "objective": objective.name,
            "k": k,
            "table_state_dict": {key: value.detach().cpu() for key, value in table.state_dict().items()},
            "injector_state_dict": {key: value.detach().cpu() for key, value in injector.state_dict().items()},
            "train_pair_ids": [row["pair_id"] for row in train_rows],
            "source_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    return {
        "format": "stage_c_pair_z_train_5e_v1",
        "objective": objective.name,
        "epochs": epochs,
        "checkpoint": str(checkpoint_path),
        "history": history,
        "train": train_eval,
        "injector": injector,
        "runtime_s": time.perf_counter() - started,
    }


def _optimize_validation_pair_z(
    *,
    backend: Any,
    base_injector: AdditiveTokenMemoryInjector,
    rows: list[dict[str, Any]],
    objective: ObjectiveSpec,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    epochs: int,
    batch_size: int,
    lr: float,
    ratio_target: float,
    ratio_penalty: float,
    train_injector: bool,
    name: str,
    progress_interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    torch.manual_seed(seed + (51000 if train_injector else 52000))
    injector = copy.deepcopy(base_injector).to(device)
    for param in injector.parameters():
        param.requires_grad_(bool(train_injector))
    table = FreePairLatentTable([row["pair_id"] for row in rows], 128, init_std=0.0).to(device)
    params = list(table.parameters()) + ([param for param in injector.parameters() if param.requires_grad])
    optimizer = torch.optim.AdamW(params, lr=lr, weight_decay=0.0)
    history = []
    last_progress = 0.0
    for epoch in range(1, epochs + 1):
        rng = random.Random(seed * 1_000_000 + epoch + (51000 if train_injector else 52000))
        order = list(range(len(rows)))
        rng.shuffle(order)
        reports = []
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            batch_rows = [rows[index] for index in order[start : start + batch_size]]
            z = table(batch_rows)
            loss, report = _latent_loss_for_batch(
                backend=backend,
                injector=injector,
                rows=batch_rows,
                z=z,
                objective=objective,
                device=device,
                k=k,
                ratio_target=ratio_target,
                ratio_penalty=ratio_penalty,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            reports.append(report)
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                print(f"{name} epoch={epoch}/{epochs} batch={batch_number} elapsed={(now-started)/3600.0:.2f}h", flush=True)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        history.append(
            {
                "epoch": epoch,
                "metrics": {
                    key: mean_std(report[key] for report in reports if key in report)
                    for key in ("loss", "target_delta_huber", "sparse_delta_huber", "sparse_teacher_kl", "target_nll", "delta_ratio")
                },
            }
        )
        atomic_write_json(output_dir / f"{name}_history.json", history)
    evaluation = _evaluate_pair_latents(
        backend=backend,
        injector=injector,
        rows=rows,
        z_provider=table,
        objective=objective,
        device=device,
        k=k,
        batch_size=batch_size,
        control=name,
    )
    zero_provider = lambda batch_rows: torch.zeros(len(batch_rows), 128, device=device)
    zero_eval = _evaluate_pair_latents(
        backend=backend,
        injector=injector,
        rows=rows,
        z_provider=zero_provider,
        objective=objective,
        device=device,
        k=k,
        batch_size=batch_size,
        control=f"{name}_zero_z",
    )
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 53000)
    random_latents = torch.randn(len(rows), 128, generator=generator, dtype=torch.float32)
    row_to_index = {str(row["pair_id"]): index for index, row in enumerate(rows)}
    random_provider = lambda batch_rows: random_latents[[row_to_index[str(row["pair_id"])] for row in batch_rows]].to(device)
    random_eval = _evaluate_pair_latents(
        backend=backend,
        injector=injector,
        rows=rows,
        z_provider=random_provider,
        objective=objective,
        device=device,
        k=k,
        batch_size=batch_size,
        control=f"{name}_random_z",
    )
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{name}.pt"
    torch.save(
        {
            "format": ORACLE_CAPACITY_VERSION,
            "component": name,
            "objective": objective.name,
            "k": k,
            "train_injector": train_injector,
            "table_state_dict": {key: value.detach().cpu() for key, value in table.state_dict().items()},
            "injector_state_dict": {key: value.detach().cpu() for key, value in injector.state_dict().items()},
            "pair_ids": [row["pair_id"] for row in rows],
            "source_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    return {
        "format": "stage_c_validation_pair_z_oracle_5e_v1",
        "name": name,
        "objective": objective.name,
        "train_injector": train_injector,
        "epochs": epochs,
        "checkpoint": str(checkpoint_path),
        "history": history,
        "evaluation": evaluation,
        "controls": {"zero_z": zero_eval, "random_z": random_eval},
        "runtime_s": time.perf_counter() - started,
    }


def _train_memory_z(
    *,
    backend: Any,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    objective: ObjectiveSpec,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    epochs: int,
    batch_size: int,
    eval_batch_size: int,
    lr: float,
    initial_scale: float,
    ratio_target: float,
    ratio_penalty: float,
    progress_interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    torch.manual_seed(seed + 61000)
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    memory_indices = sorted({int(row["memory_stage_index"]) for row in train_rows + validation_rows})
    table = FreeMemoryLatentTable(memory_indices, 128, init_std=0.0).to(device)
    injector = _capacity_injector(program_dim=128, model_dim=model_dim, k=k, seed=seed + 61000, initial_scale=initial_scale).to(device)
    optimizer = torch.optim.AdamW(list(table.parameters()) + list(injector.parameters()), lr=lr, weight_decay=0.0)
    history = []
    last_progress = 0.0
    for epoch in range(1, epochs + 1):
        rng = random.Random(seed * 1_000_000 + 61000 + epoch)
        order = list(range(len(train_rows)))
        rng.shuffle(order)
        reports = []
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            batch_rows = [train_rows[index] for index in order[start : start + batch_size]]
            z = table(batch_rows)
            loss, report = _latent_loss_for_batch(
                backend=backend,
                injector=injector,
                rows=batch_rows,
                z=z,
                objective=objective,
                device=device,
                k=k,
                ratio_target=ratio_target,
                ratio_penalty=ratio_penalty,
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(list(table.parameters()) + list(injector.parameters()), 1.0)
            optimizer.step()
            reports.append(report)
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                print(f"memory-z train epoch={epoch}/{epochs} batch={batch_number} elapsed={(now-started)/3600.0:.2f}h", flush=True)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        history.append(
            {
                "epoch": epoch,
                "metrics": {
                    key: mean_std(report[key] for report in reports if key in report)
                    for key in ("loss", "target_delta_huber", "sparse_delta_huber", "sparse_teacher_kl", "target_nll", "delta_ratio")
                },
            }
        )
        atomic_write_json(output_dir / "memory_z_train_history.json", history)

    correct_eval = _evaluate_pair_latents(
        backend=backend,
        injector=injector,
        rows=validation_rows,
        z_provider=table,
        objective=objective,
        device=device,
        k=k,
        batch_size=eval_batch_size,
        control="free_memory_z",
    )
    with torch.no_grad():
        memory_latents = table.latents.detach().clone()
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 62000)
    order = torch.randperm(memory_latents.shape[0], generator=generator).to(device)
    random_memory = torch.randn(memory_latents.shape, generator=generator, dtype=torch.float32).to(device)
    mean_memory = memory_latents.mean(dim=0, keepdim=True).expand_as(memory_latents)
    zero_memory = torch.zeros_like(memory_latents)
    memory_to_offset = dict(table.memory_to_index)

    def provider_from_matrix(matrix: Tensor) -> Any:
        def _provider(batch_rows: Sequence[dict[str, Any]]) -> Tensor:
            offsets = [memory_to_offset[int(row["memory_stage_index"])] for row in batch_rows]
            return matrix.index_select(0, torch.tensor(offsets, dtype=torch.long, device=device))

        return _provider

    controls = {
        "shuffled_memory_z": _evaluate_pair_latents(
            backend=backend,
            injector=injector,
            rows=validation_rows,
            z_provider=provider_from_matrix(memory_latents.index_select(0, order)),
            objective=objective,
            device=device,
            k=k,
            batch_size=eval_batch_size,
            control="shuffled_memory_z",
        ),
        "random_memory_z": _evaluate_pair_latents(
            backend=backend,
            injector=injector,
            rows=validation_rows,
            z_provider=provider_from_matrix(random_memory),
            objective=objective,
            device=device,
            k=k,
            batch_size=eval_batch_size,
            control="random_memory_z",
        ),
        "mean_memory_z": _evaluate_pair_latents(
            backend=backend,
            injector=injector,
            rows=validation_rows,
            z_provider=provider_from_matrix(mean_memory),
            objective=objective,
            device=device,
            k=k,
            batch_size=eval_batch_size,
            control="mean_memory_z",
        ),
        "zero_memory_z": _evaluate_pair_latents(
            backend=backend,
            injector=injector,
            rows=validation_rows,
            z_provider=provider_from_matrix(zero_memory),
            objective=objective,
            device=device,
            k=k,
            batch_size=eval_batch_size,
            control="zero_memory_z",
        ),
    }
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / "memory_z.pt"
    torch.save(
        {
            "format": ORACLE_CAPACITY_VERSION,
            "component": "memory_z",
            "objective": objective.name,
            "k": k,
            "table_state_dict": {key: value.detach().cpu() for key, value in table.state_dict().items()},
            "injector_state_dict": {key: value.detach().cpu() for key, value in injector.state_dict().items()},
            "memory_stage_indices": memory_indices,
            "source_commit": maybe_git_commit(),
        },
        checkpoint_path,
    )
    return {
        "format": "stage_c_memory_z_5e_v1",
        "objective": objective.name,
        "epochs": epochs,
        "checkpoint": str(checkpoint_path),
        "history": history,
        "validation": {"correct": correct_eval, "controls": controls},
        "runtime_s": time.perf_counter() - started,
    }


def _load_stage5d_reference(stage5d_dir: Path) -> dict[str, Any]:
    summary_path = stage5d_dir / "summary.json"
    if not summary_path.exists():
        return {"available": False, "reason": f"missing {summary_path}"}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    state_heldout = summary.get("state_heldout", {})
    compact: dict[str, Any] = {"available": True, "summary_path": str(summary_path)}
    for name in ("content", "free_id", "fixed_random"):
        payload = state_heldout.get(name, {})
        compact[name] = payload.get("validation", {}).get("correct", {}).get("summary")
        compact[f"{name}_control_deltas"] = payload.get("validation", {}).get("control_deltas")
    compact["decision"] = summary.get("decision")
    return compact


def _hierarchy_table(
    *,
    direct: dict[str, Any],
    pair_z: dict[str, Any],
    pair_z_inversion: dict[str, Any],
    memory_z: dict[str, Any],
    reference: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []

    def add(name: str, summary: dict[str, Any] | None) -> None:
        if not summary:
            return
        rows.append(
            {
                "model": name,
                "target_nll": (summary.get("target_nll") or {}).get("mean"),
                "sparse_teacher_kl": (summary.get("sparse_teacher_kl") or {}).get("mean"),
                "target_token_delta_huber": (summary.get("target_token_delta_huber") or {}).get("mean"),
                "u_text_spearman": summary.get("u_text_vs_u_student_spearman")
                if "u_text_vs_u_student_spearman" in summary
                else (summary.get("u_text_vs_u_program_spearman") or {}).get("mean"),
                "sign_agreement": summary.get("positive_negative_sign_agreement")
                if not isinstance(summary.get("positive_negative_sign_agreement"), dict)
                else summary["positive_negative_sign_agreement"].get("mean"),
                "delta_ratio": (summary.get("delta_ratio") or {}).get("mean") if isinstance(summary.get("delta_ratio"), dict) else None,
            }
        )

    add("direct_delta_oracle", direct.get("evaluation", {}).get("summary"))
    add("free_pair_z_joint_oracle", pair_z.get("evaluation", {}).get("summary"))
    add("free_pair_z_frozen_injector_inversion", pair_z_inversion.get("evaluation", {}).get("summary"))
    add("free_memory_z", memory_z.get("validation", {}).get("correct", {}).get("summary"))
    if reference.get("available"):
        add("stage5d_content_program", reference.get("content"))
        add("stage5d_free_id_program", reference.get("free_id"))
        add("stage5d_fixed_random_program", reference.get("fixed_random"))
    return rows


def _report(summary: dict[str, Any]) -> str:
    decision = summary["decision"]
    lines = [
        "# Milestone 5E / EXP-015 Oracle Capacity Diagnostic",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- pair cache: `{summary['pair_cache_dir']}`",
        f"- pair cache validation reused: `{summary['pair_cache_validation']['passed']}`",
        f"- diagnostic subset pairs: `{summary['subset']['selected_total']}`",
        f"- runtime seconds: `{summary['runtime_s']:.2f}`",
        "",
        "## Decision",
        "",
        f"- branch: `{decision['branch']}`",
        f"- bottleneck: `{decision['identified_bottleneck']}`",
        f"- Stage C2 allowed: `{decision['stage_c2_allowed']}`",
        "",
        "## Hierarchy",
        "",
        "| model | target NLL | sparse KL | target-delta Huber | Spearman | sign agreement | delta ratio |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["hierarchy_table"]:
        lines.append(
            f"| `{row['model']}` | {row['target_nll']} | {row['sparse_teacher_kl']} | "
            f"{row['target_token_delta_huber']} | {row['u_text_spearman']} | "
            f"{row['sign_agreement']} | {row['delta_ratio']} |"
        )
    lines.extend(
        [
            "",
            "## Direct DeltaE",
            "",
            "```json",
            json.dumps(summary["direct_delta_best"]["evaluation"]["summary"], indent=2, sort_keys=True),
            "```",
            "",
            "## Pair-z Frozen-Injector Inversion",
            "",
            "```json",
            json.dumps(summary["pair_z"]["frozen_injector_inversion"]["evaluation"]["summary"], indent=2, sort_keys=True),
            "```",
            "",
            "## Memory-z",
            "",
            "```json",
            json.dumps(summary["memory_z"]["validation"]["correct"]["summary"], indent=2, sort_keys=True),
            "```",
        ]
    )
    if summary.get("k8_direct_delta") is not None:
        lines.extend(["", "## Optional K=8 Direct DeltaE", "", "```json", json.dumps(summary["k8_direct_delta"]["evaluation"]["summary"], indent=2, sort_keys=True), "```"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pair-cache-dir", type=Path, required=True)
    parser.add_argument("--stage5d-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--direct-subset-count", type=int, default=192)
    parser.add_argument("--debug-train-pair-count", type=int, default=None)
    parser.add_argument("--debug-validation-pair-count", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--direct-epochs", type=int, default=2)
    parser.add_argument("--pair-z-epochs", type=int, default=1)
    parser.add_argument("--validation-z-epochs", type=int, default=2)
    parser.add_argument("--memory-z-epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--direct-lr", type=float, default=0.05)
    parser.add_argument("--latent-lr", type=float, default=2.0e-3)
    parser.add_argument("--injector-initial-scale", type=float, default=0.1)
    parser.add_argument("--ratio-target", type=float, default=1.0)
    parser.add_argument("--ratio-penalty", type=float, default=0.2)
    parser.add_argument("--progress-interval-s", type=float, default=120.0)
    parser.add_argument("--skip-k8", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_resolved_config(cfg, args.output_dir / "resolved_config.yaml")
    backend = build_backend(cfg, load_model=not args.smoke)
    if args.smoke:
        print("Stage-5E smoke mode validated config loading; use tests for no-model unit checks.", flush=True)
        return
    backend.model.eval()
    for param in backend.model.parameters():
        param.requires_grad_(False)
    device = backend.device
    context_limit = _context_limit_for_backend(backend)
    source_commit = maybe_git_commit()

    pair_rows = _load_rows(args.pair_cache_dir / "pair_response_cache.jsonl")
    if any(row.get("format") != PAIR_RESPONSE_CACHE_VERSION for row in pair_rows):
        raise ValueError("Unexpected pair cache format")
    pair_cache_summary = json.loads((args.pair_cache_dir / "pair_response_cache_summary.json").read_text(encoding="utf-8"))
    if not pair_cache_summary.get("validation", {}).get("passed"):
        raise ValueError("Stage-5D pair cache validation is not passed")
    identity = validate_target_token_utility_identity(pair_rows)
    if not identity["passed"]:
        raise ValueError(f"target-token utility identity failed: {identity['errors_first_20']}")

    subset_rows_raw, subset_summary = select_balanced_validation_subset(
        pair_rows,
        target_total=args.direct_subset_count,
        seed=20260808 + args.seed,
    )
    train_rows_raw = [row for row in pair_rows if str(row.get("split")) == "train"]
    validation_rows_raw = [row for row in pair_rows if str(row.get("split")) == "validation"]
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    prompt_profile = str(cfg.get("benchmark", {}).get("prompt_profile", "full_demo"))
    needed_ids = {str(row["pair_id"]) for row in train_rows_raw + validation_rows_raw}
    tokenized_all = _build_tokenized_pair_rows(
        backend=backend,
        examples=examples,
        pair_rows=[row for row in pair_rows if str(row["pair_id"]) in needed_ids],
        prompt_profile=prompt_profile,
        context_limit=context_limit,
    )
    by_pair = {str(row["pair_id"]): row for row in tokenized_all}
    train_rows = _select_by_pair_ids(tokenized_all, {str(row["pair_id"]) for row in train_rows_raw})
    validation_rows = _select_by_pair_ids(tokenized_all, {str(row["pair_id"]) for row in validation_rows_raw})
    subset_rows = _select_by_pair_ids(tokenized_all, {str(row["pair_id"]) for row in subset_rows_raw})
    if args.debug_train_pair_count is not None:
        train_rows = train_rows[: int(args.debug_train_pair_count)]
    if args.debug_validation_pair_count is not None:
        validation_rows = validation_rows[: int(args.debug_validation_pair_count)]

    objective_primary = OBJECTIVES["target_delta_plus_sparse_kl"]
    direct_dir = args.output_dir / "direct_delta"
    direct_dir.mkdir(parents=True, exist_ok=True)
    direct_runs: dict[str, Any] = {}
    for ratio in (0.5, 1.0, 2.0):
        run = _train_direct_delta_oracle(
            backend=backend,
            rows=subset_rows,
            objective=objective_primary,
            ratio_budget=ratio,
            device=device,
            output_dir=direct_dir,
            seed=args.seed,
            k=args.k,
            epochs=args.direct_epochs,
            batch_size=args.batch_size,
            lr=args.direct_lr,
            progress_interval_s=args.progress_interval_s,
        )
        direct_runs[f"{objective_primary.name}_ratio_{ratio}"] = run
        atomic_write_json(args.output_dir / "direct_delta_partial.json", direct_runs)

    objective_ablation: dict[str, Any] = {}
    for name in ("sparse_delta_huber", "target_delta_huber", "target_delta_plus_sparse_kl"):
        if name == objective_primary.name:
            run = direct_runs[f"{objective_primary.name}_ratio_1.0"]
        else:
            run = _train_direct_delta_oracle(
                backend=backend,
                rows=subset_rows,
                objective=OBJECTIVES[name],
                ratio_budget=1.0,
                device=device,
                output_dir=direct_dir,
                seed=args.seed + 100,
                k=args.k,
                epochs=args.direct_epochs,
                batch_size=args.batch_size,
                lr=args.direct_lr,
                progress_interval_s=args.progress_interval_s,
            )
            direct_runs[f"{name}_ratio_1.0"] = run
        objective_ablation[name] = run["evaluation"]["summary"]
        atomic_write_json(args.output_dir / "objective_ablation_partial.json", objective_ablation)

    def _direct_sort_key(item: tuple[str, Any]) -> tuple[float, float, float]:
        summary = item[1]["evaluation"]["summary"]
        return (
            float(summary.get("u_text_vs_u_student_spearman") or -999.0),
            float(summary.get("positive_negative_sign_agreement") or -999.0),
            -float((summary.get("target_token_delta_huber") or {}).get("mean") or 999.0),
        )

    direct_best_name, direct_best = max(
        [(name, run) for name, run in direct_runs.items() if name.startswith(objective_primary.name)],
        key=_direct_sort_key,
    )
    k8_run = None
    direct_best_summary = direct_best["evaluation"]["summary"]
    direct_pass = (
        (direct_best_summary.get("u_text_vs_u_student_spearman") or -1.0) >= 0.70
        and (direct_best_summary.get("positive_negative_sign_agreement") or 0.0) >= 0.80
        and (direct_best_summary.get("target_token_delta_correlation_global") or -1.0) >= 0.80
        and ((direct_best_summary.get("delta_ratio") or {}).get("max") or 999.0) <= 2.0001
    )
    if (not direct_pass) and (not args.skip_k8):
        k8_run = _train_direct_delta_oracle(
            backend=backend,
            rows=subset_rows,
            objective=objective_primary,
            ratio_budget=2.0,
            device=device,
            output_dir=direct_dir,
            seed=args.seed + 200,
            k=8,
            epochs=args.direct_epochs,
            batch_size=args.batch_size,
            lr=args.direct_lr,
            progress_interval_s=args.progress_interval_s,
        )

    pair_dir = args.output_dir / "pair_z"
    pair_dir.mkdir(parents=True, exist_ok=True)
    pair_train = _train_pair_z_injector(
        backend=backend,
        train_rows=train_rows,
        objective=objective_primary,
        device=device,
        output_dir=pair_dir,
        seed=args.seed,
        k=args.k,
        epochs=args.pair_z_epochs,
        batch_size=args.batch_size,
        lr=args.latent_lr,
        initial_scale=args.injector_initial_scale,
        ratio_target=args.ratio_target,
        ratio_penalty=args.ratio_penalty,
        progress_interval_s=args.progress_interval_s,
    )
    frozen_inversion = _optimize_validation_pair_z(
        backend=backend,
        base_injector=pair_train["injector"],
        rows=subset_rows,
        objective=objective_primary,
        device=device,
        output_dir=pair_dir,
        seed=args.seed,
        k=args.k,
        epochs=args.validation_z_epochs,
        batch_size=args.batch_size,
        lr=args.latent_lr,
        ratio_target=args.ratio_target,
        ratio_penalty=args.ratio_penalty,
        train_injector=False,
        name="frozen_injector_validation_pair_z",
        progress_interval_s=args.progress_interval_s,
    )
    joint_oracle = _optimize_validation_pair_z(
        backend=backend,
        base_injector=pair_train["injector"],
        rows=subset_rows,
        objective=objective_primary,
        device=device,
        output_dir=pair_dir,
        seed=args.seed,
        k=args.k,
        epochs=args.validation_z_epochs,
        batch_size=args.batch_size,
        lr=args.latent_lr,
        ratio_target=args.ratio_target,
        ratio_penalty=args.ratio_penalty,
        train_injector=True,
        name="joint_validation_pair_z",
        progress_interval_s=args.progress_interval_s,
    )
    pair_train_injector = pair_train.pop("injector")
    del pair_train_injector

    memory_dir = args.output_dir / "memory_z"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_z = _train_memory_z(
        backend=backend,
        train_rows=train_rows,
        validation_rows=validation_rows,
        objective=objective_primary,
        device=device,
        output_dir=memory_dir,
        seed=args.seed,
        k=args.k,
        epochs=args.memory_z_epochs,
        batch_size=args.batch_size,
        eval_batch_size=args.eval_batch_size,
        lr=args.latent_lr,
        initial_scale=args.injector_initial_scale,
        ratio_target=args.ratio_target,
        ratio_penalty=args.ratio_penalty,
        progress_interval_s=args.progress_interval_s,
    )

    reference = _load_stage5d_reference(args.stage5d_dir)
    hierarchy = _hierarchy_table(
        direct=direct_best,
        pair_z=joint_oracle,
        pair_z_inversion=frozen_inversion,
        memory_z=memory_z,
        reference=reference,
    )
    content_reference_summary = reference.get("content") if reference.get("available") else None
    decision = stage_5e_decision(
        direct_summary=direct_best["evaluation"]["summary"],
        pair_z_summary=frozen_inversion["evaluation"]["summary"],
        memory_z_summary=memory_z["validation"]["correct"]["summary"],
        content_reference_summary=content_reference_summary,
        objective_ablation=objective_ablation,
    )
    summary = {
        "format": ORACLE_CAPACITY_VERSION,
        "source_commit": source_commit,
        "timestamp_utc": utc_now(),
        "output_dir": str(args.output_dir),
        "data_dir": str(args.data),
        "pair_cache_dir": str(args.pair_cache_dir),
        "stage5d_dir": str(args.stage5d_dir),
        "pair_cache_validation": pair_cache_summary.get("validation"),
        "pair_cache_sha256": sha256_file(args.pair_cache_dir / "pair_response_cache.jsonl"),
        "target_token_utility_identity": identity,
        "subset": subset_summary,
        "train_pair_count": len(train_rows),
        "validation_pair_count": len(validation_rows),
        "objective_primary": objective_primary.name,
        "objective_ablation": objective_ablation,
        "direct_delta_runs": direct_runs,
        "direct_delta_best_name": direct_best_name,
        "direct_delta_best": direct_best,
        "k8_direct_delta": k8_run,
        "pair_z": {
            "train": pair_train,
            "frozen_injector_inversion": frozen_inversion,
            "joint_oracle": joint_oracle,
        },
        "memory_z": memory_z,
        "stage5d_reference": reference,
        "hierarchy_table": hierarchy,
        "decision": decision,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "selector_used": False,
            "selector_scores_used": False,
            "selector_gate_used": False,
            "empirical_mu_used": False,
            "full_bank_aggregation_used": False,
            "appworld_generation_evaluation": False,
            "stage_c2_started": False,
        },
    }
    atomic_write_json(args.output_dir / "summary.json", summary)
    atomic_write_text(args.output_dir / "report.md", _report(summary))
    write_jsonl(args.output_dir / "direct_delta_best_rows.jsonl", direct_best["evaluation"]["rows"])
    write_jsonl(args.output_dir / "pair_z_frozen_inversion_rows.jsonl", frozen_inversion["evaluation"]["rows"])
    write_jsonl(args.output_dir / "memory_z_validation_rows.jsonl", memory_z["validation"]["correct"]["rows"])
    print(json.dumps({"summary": str(args.output_dir / "summary.json"), "decision": decision}, indent=2), flush=True)


if __name__ == "__main__":
    main()
