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

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.training.addressing_4b import _pearson, distribution, mean_std
from rcmf.training.addressing_only import task_balanced_batches
from rcmf.training.datasets import (
    _append_eos_token_id,
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
)
from rcmf.training.stage_c1 import (
    POSITIVE_TEACHER_EPS,
    STAGE_C1_PROGRAM_FIELD_VERSION,
    STAGE_C1_RESPONSE_CACHE_VERSION,
    FixedProgramField,
    StageC1LossWeights,
    StageC1ProgramField,
    build_include_mask,
    explicit_field_read,
    load_signed_selector_checkpoint,
    paired_ci,
    parameter_count,
    prepare_selector_payload,
    program_geometry,
    selector_parameter_change,
    selector_state_hash,
    select_teacher_conditions,
    sparse_teacher_kl_from_logits,
    split_rows,
    resolve_include_mask,
    stage_c1_decision,
    summarize_runs,
    summarize_state_nll_rows,
    target_nll_by_state_from_logits,
    train_memory_prior,
    validate_program_field_algebra,
    validate_selector_preserved,
    z_geometry,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, maybe_git_commit, read_jsonl, sha256_file
from scripts.build_stage_c1_response_cache import validate_response_cache
from scripts.run_raw_text_teacher_pilot import _target_token_ids, _token_ids


RUN_VERSION = STAGE_C1_PROGRAM_FIELD_VERSION


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _load_representation_cache(
    path: Path,
    *,
    expected_count: int,
    expected_source_path: Path | None,
    model_name: str,
    accepted_formats: set[str],
) -> tuple[Tensor, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu")
    fmt = str(payload.get("format"))
    if fmt not in accepted_formats:
        raise ValueError(f"Unsupported representation cache format {fmt} at {path}")
    representations = payload.get("representations")
    if not isinstance(representations, torch.Tensor) or representations.dim() != 2:
        raise ValueError(f"Representation cache missing 2D representations tensor: {path}")
    if representations.shape[0] != expected_count:
        raise ValueError(f"Representation count {representations.shape[0]} != {expected_count}: {path}")
    if payload.get("model_name") != model_name:
        raise ValueError(f"Representation model mismatch: {payload.get('model_name')} != {model_name}")
    if expected_source_path is not None and expected_source_path.exists():
        expected_hash = sha256_file(expected_source_path)
        if payload.get("source_sha256") != expected_hash:
            raise ValueError(f"Representation source hash mismatch for {path}")
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"representations", "chunk_representations", "owner_indices", "chunk_token_counts"}
    }
    metadata["path"] = str(path)
    metadata["shape"] = list(representations.shape)
    return representations.to(torch.float32), metadata


def _response_rows_by_state(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("format") != STAGE_C1_RESPONSE_CACHE_VERSION:
            raise ValueError(f"Unexpected response cache format: {row.get('format')}")
        key = str(row["state_example_id"])
        if key in output:
            raise ValueError(f"duplicate response cache state: {key}")
        output[key] = row
    return output


def _build_tokenized_rows(
    *,
    backend: Any,
    examples: list[Any],
    label_rows: list[dict[str, Any]],
    response_by_state: dict[str, dict[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> list[dict[str, Any]]:
    tokenizer = backend.tokenizer
    rows = []
    pad_id = int(getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", 0) or 0)
    for label in label_rows:
        response = response_by_state[str(label["state_example_id"])]
        if not response.get("valid_for_stage_c", False):
            continue
        example = examples[int(label["state_index"])]
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt_text, prompt_metadata = _render_prompt_with_metadata(tokenizer, messages, prompt_profile)
        prompt_ids = _token_ids(tokenizer, prompt_text, add_special_tokens=False)
        target_text = _target_suffix(example)
        target_ids = _target_token_ids(tokenizer, example)
        if target_ids != [int(item) for item in response["target_token_ids"]]:
            raise ValueError(f"target ids differ from response cache: {label['state_example_id']}")
        full_ids = prompt_ids + target_ids
        if len(full_ids) > context_limit:
            raise ValueError(f"student prompt+target exceeds context for {label['state_example_id']}: {len(full_ids)}")
        rows.append(
            {
                **label,
                "response_cache": response,
                "input_ids": full_ids,
                "labels": [-100] * len(prompt_ids) + target_ids,
                "attention_mask": [1] * len(full_ids),
                "target_len": len(target_ids),
                "prompt_len": len(prompt_ids),
                "last_user_token_indices": list(prompt_metadata.get("last_user_token_indices", [])),
                "target_text": target_text,
                "pad_token_id": pad_id,
            }
        )
    return rows


def _collate(rows: Sequence[dict[str, Any]], *, device: torch.device) -> dict[str, Any]:
    if not rows:
        raise ValueError("empty batch")
    max_len = max(len(row["input_ids"]) for row in rows)
    pad_id = int(rows[0]["pad_token_id"])
    input_ids = torch.full((len(rows), max_len), pad_id, dtype=torch.long, device=device)
    labels = torch.full((len(rows), max_len), -100, dtype=torch.long, device=device)
    attention_mask = torch.zeros((len(rows), max_len), dtype=torch.long, device=device)
    max_user = max(1, max(len(row["last_user_token_indices"]) for row in rows))
    injection_indices = torch.full((len(rows), max_user), -1, dtype=torch.long, device=device)
    for row_index, row in enumerate(rows):
        length = len(row["input_ids"])
        input_ids[row_index, :length] = torch.tensor(row["input_ids"], dtype=torch.long, device=device)
        labels[row_index, :length] = torch.tensor(row["labels"], dtype=torch.long, device=device)
        attention_mask[row_index, :length] = 1
        indices = row["last_user_token_indices"]
        if indices:
            injection_indices[row_index, : len(indices)] = torch.tensor(indices, dtype=torch.long, device=device)
    return {
        "input_ids": input_ids,
        "labels": labels,
        "attention_mask": attention_mask,
        "injection_token_indices": injection_indices,
        "target_lengths": [int(row["target_len"]) for row in rows],
        "response_rows": [row["response_cache"] for row in rows],
        "label_rows": list(rows),
    }


def _initialize_injector(
    *,
    program_dim: int,
    model_dim: int,
    num_tokens: int,
    position: str,
    seed: int,
) -> AdditiveTokenMemoryInjector:
    torch.manual_seed(seed + 8000)
    injector = AdditiveTokenMemoryInjector(
        program_dim=program_dim,
        model_dim=model_dim,
        num_tokens=num_tokens,
        position=position,
        initial_scale=0.0,
    )
    nn.init.normal_(injector.mlp[-1].weight, mean=0.0, std=0.02)
    with torch.no_grad():
        injector.prefix_scale.fill_(0.0)
    return injector


def _program_matrix(
    field: nn.Module,
    memory_representations: Tensor,
    *,
    control: str,
    seed: int,
    trained_programs: Tensor | None = None,
) -> Tensor:
    if control == "fixed_random_program":
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + 12000)
        random_programs = torch.randn(memory_representations.shape[0], getattr(field, "program_dim", 128), generator=generator)
        return random_programs.to(memory_representations.device, dtype=torch.float32) / torch.sqrt(
            random_programs.pow(2).mean(dim=-1, keepdim=True).to(memory_representations.device) + 1.0e-6
        )
    if trained_programs is None:
        programs = field.programs(memory_representations)
    else:
        programs = trained_programs.to(memory_representations.device)
    if control == "shuffled_program":
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + 13000)
        order = torch.randperm(programs.shape[0], generator=generator).to(programs.device)
        return programs.index_select(0, order)
    if control == "mean_program":
        return programs.mean(dim=0, keepdim=True).expand_as(programs)
    if control == "zero_program":
        return torch.zeros_like(programs)
    return programs


def _compute_z(
    *,
    field: nn.Module,
    selector_payload: dict[str, Tensor],
    rows: Sequence[dict[str, Any]],
    memory_representations: Tensor,
    control: str,
    seed: int,
    device: torch.device,
    trained_programs: Tensor | None = None,
    include_mask_override: Tensor | Sequence[Sequence[bool]] | None = None,
) -> tuple[Tensor, dict[str, Any]]:
    if control == "shuffled_state" and any("_state_index_override" not in row for row in rows):
        raise ValueError("shuffled_state control requires precomputed full-evaluation state-index overrides")
    indices = torch.tensor(
        [int(row.get("_state_index_override", row["state_index"])) for row in rows],
        dtype=torch.long,
        device=device,
    )
    q_bar_all = selector_payload["q_bar"].to(device)
    gate_all = selector_payload["gate"].to(device)
    q_bar = q_bar_all.index_select(0, indices)
    gate = gate_all.index_select(0, indices)
    if control == "mean_state":
        if "control_mean_q_bar" in selector_payload and "control_mean_gate" in selector_payload:
            q_bar = selector_payload["control_mean_q_bar"].to(device).view(1, -1).expand_as(q_bar)
            gate = selector_payload["control_mean_gate"].to(device).view(1).expand_as(gate)
        else:
            q_bar = q_bar_all.mean(dim=0, keepdim=True).expand_as(q_bar)
            gate = gate_all.mean().expand_as(gate)
    elif control == "zero_state":
        q_bar = torch.zeros_like(q_bar)
        q_bar[:, -1] = 1.0
        gate = torch.zeros_like(gate)
    k_bar = selector_payload["k_bar"].to(device)
    memory = memory_representations.to(device=device, dtype=torch.float32)
    programs = _program_matrix(
        field,
        memory,
        control=control,
        seed=seed,
        trained_programs=trained_programs,
    )
    include_mask = resolve_include_mask(
        rows,
        validation_full_bank=True,
        include_mask_override=include_mask_override,
    ).to(device)
    if control == "global_prior_only":
        mu = k_bar[:, -1].view(1, -1).expand(len(rows), -1)
        scores = mu * include_mask.to(torch.float32)
        numerator = scores @ programs
        denom = torch.sqrt(scores.pow(2).sum(dim=1, keepdim=True) + 1.0e-6)
        z = gate.view(-1, 1) * numerator / denom
        return z, {"scores": scores.detach(), "programs": programs.detach(), "denominator": denom.squeeze(-1).detach()}
    z, meta = field.read(q_bar, k_bar, programs, gate, include_mask=include_mask)
    meta["programs"] = programs.detach()
    return z, {key: value.detach() if isinstance(value, Tensor) else value for key, value in meta.items()}


def _prepare_state_control_rows(
    *,
    rows: Sequence[dict[str, Any]],
    selector_payload: dict[str, Tensor],
    control: str,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Tensor]]:
    eval_rows = [dict(row) for row in rows]
    payload = selector_payload
    if control == "shuffled_state":
        source_indices = [int(row["state_index"]) for row in eval_rows]
        if len(source_indices) > 1:
            generator = torch.Generator(device="cpu")
            generator.manual_seed(seed + 11000)
            order = torch.randperm(len(source_indices), generator=generator).tolist()
            shuffled = [source_indices[int(index)] for index in order]
            if shuffled == source_indices:
                shuffled = source_indices[1:] + source_indices[:1]
        else:
            shuffled = source_indices
        for row, override in zip(eval_rows, shuffled):
            row["_state_index_override"] = int(override)
    elif control == "mean_state":
        indices = torch.tensor([int(row["state_index"]) for row in eval_rows], dtype=torch.long)
        q_bar_all = selector_payload["q_bar"].detach().cpu()
        gate_all = selector_payload["gate"].detach().cpu()
        payload = dict(selector_payload)
        payload["control_mean_q_bar"] = q_bar_all.index_select(0, indices).mean(dim=0)
        payload["control_mean_gate"] = gate_all.index_select(0, indices).mean()
    return eval_rows, payload


def _forward_student(
    *,
    backend: Any,
    injector: AdditiveTokenMemoryInjector,
    batch: dict[str, Any],
    memory_z: Tensor,
    delta_multiplier: float = 1.0,
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
        memory_z=memory_z,
        injection_token_indices=batch["injection_token_indices"],
    )
    token_embeds = model.get_input_embeddings()(input_ids)
    delta = prepared.inputs["inputs_embeds"] - token_embeds
    if float(delta_multiplier) != 1.0:
        model_inputs = dict(prepared.inputs)
        model_inputs["inputs_embeds"] = token_embeds + delta.to(dtype=token_embeds.dtype) * float(delta_multiplier)
        delta = model_inputs["inputs_embeds"] - token_embeds
    else:
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


def _loss_for_batch(
    *,
    backend: Any,
    field: nn.Module,
    injector: AdditiveTokenMemoryInjector,
    selector_payload: dict[str, Tensor],
    rows: Sequence[dict[str, Any]],
    memory_representations: Tensor,
    weights: StageC1LossWeights,
    device: torch.device,
    seed: int,
    control: str = "correct",
) -> tuple[Tensor, dict[str, Any]]:
    batch = _collate(rows, device=device)
    z, read_meta = _compute_z(
        field=field,
        selector_payload=selector_payload,
        rows=rows,
        memory_representations=memory_representations,
        control=control,
        seed=seed,
        device=device,
    )
    student = _forward_student(backend=backend, injector=injector, batch=batch, memory_z=z)
    teacher_kl, kl_meta = sparse_teacher_kl_from_logits(
        student["target_logits"],
        batch["response_rows"],
        target_lengths=batch["target_lengths"],
        target="teacher",
    )
    no_positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "baseline_teacher"]
    if no_positive_rows:
        cursor = 0
        selected_logits = []
        selected_lengths = []
        selected_responses = []
        for row, target_len in zip(rows, batch["target_lengths"]):
            if row["response_cache"]["teacher_condition"] == "baseline_teacher":
                selected_logits.append(student["target_logits"][cursor : cursor + target_len])
                selected_lengths.append(target_len)
                selected_responses.append(row["response_cache"])
            cursor += target_len
        base_logits = torch.cat(selected_logits, dim=0) if selected_logits else student["target_logits"][:0]
        preservation_kl, _ = sparse_teacher_kl_from_logits(
            base_logits,
            selected_responses,
            target_lengths=selected_lengths,
            target="baseline",
        )
    else:
        preservation_kl = teacher_kl * 0.0
    perturb = student["delta"].to(torch.float32).pow(2).mean()
    z_l2 = z.to(torch.float32).pow(2).mean()
    total = (
        weights.teacher_kl * teacher_kl
        + weights.action_ce * student["loss"]
        + weights.no_positive_preservation * preservation_kl
        + weights.delta_l2 * perturb
        + weights.z_l2 * z_l2
    )
    metrics = {
        "loss": float(total.detach().cpu()),
        "teacher_kl": float(teacher_kl.detach().cpu()),
        "action_ce": float(student["loss"].detach().cpu()),
        "preservation_kl": float(preservation_kl.detach().cpu()),
        "delta_l2": float(perturb.detach().cpu()),
        "z_l2": float(z_l2.detach().cpu()),
        "delta_norm": float(student["delta_norm"].detach().cpu()),
        "delta_ratio": float(student["delta_ratio"].detach().cpu()),
        "kl_positions": kl_meta["positions"],
        "read_score_distribution": distribution(read_meta["scores"].detach().to(torch.float32).cpu().flatten().tolist()),
    }
    return total, metrics


def _grad_norms(module: nn.Module) -> dict[str, float]:
    output: dict[str, float] = {}
    for name, param in module.named_parameters():
        if param.grad is not None:
            output[name] = float(param.grad.detach().to(torch.float32).norm().cpu())
    return output


def _evaluate_cache_baseline(rows: Sequence[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    out_rows = []
    for row in rows:
        response = row["response_cache"]
        if mode == "teacher_oracle":
            nll = float(response["teacher_mean_target_nll"])
            kl = 0.0
        elif mode == "bare_qwen_zero_field":
            nll = float(response["baseline_mean_target_nll"])
            kl_values = []
            for item in response["target_positions"]:
                teacher_probs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64).exp()
                baseline_logprobs = torch.tensor(item["baseline_union_logprobs"], dtype=torch.float64)
                teacher_logprobs = torch.tensor(item["teacher_union_logprobs"], dtype=torch.float64)
                other_teacher = torch.tensor(float(item["teacher_other_probability"]), dtype=torch.float64).clamp_min(1.0e-12)
                other_base = torch.tensor(float(item["baseline_other_probability"]), dtype=torch.float64).clamp_min(1.0e-12)
                kl_values.append(
                    float((teacher_probs * (teacher_logprobs - baseline_logprobs)).sum().item())
                    + float(other_teacher.item() * (other_teacher.log() - other_base.log()).item())
                )
            kl = sum(kl_values) / len(kl_values) if kl_values else 0.0
        else:
            raise ValueError(f"unknown cache baseline mode {mode}")
        out_rows.append(
            {
                "state_example_id": response["state_example_id"],
                "split": response["split"],
                "teacher_condition": response["teacher_condition"],
                "L0": response["L0"],
                "student_target_nll": nll,
                "sparse_teacher_kl": kl,
            }
        )
    return {"rows": out_rows, "summary": summarize_state_nll_rows(out_rows)}


def evaluate_student(
    *,
    backend: Any,
    field: nn.Module,
    injector: AdditiveTokenMemoryInjector,
    selector_payload: dict[str, Tensor],
    rows: Sequence[dict[str, Any]],
    memory_representations: Tensor,
    device: torch.device,
    seed: int,
    batch_size: int,
    control: str = "correct",
    trained_programs: Tensor | None = None,
    include_mask_override: Tensor | Sequence[Sequence[bool]] | None = None,
    delta_multiplier: float = 1.0,
) -> dict[str, Any]:
    field.eval()
    injector.eval()
    eval_rows, eval_selector_payload = _prepare_state_control_rows(
        rows=rows,
        selector_payload=selector_payload,
        control=control,
        seed=seed,
    )
    out_rows = []
    z_rows = []
    delta_norms = []
    delta_ratios = []
    selected_token_report = None
    override_tensor = None
    if include_mask_override is not None:
        override_tensor = resolve_include_mask(
            eval_rows,
            validation_full_bank=True,
            include_mask_override=include_mask_override,
        )
    with torch.no_grad():
        for start in range(0, len(eval_rows), batch_size):
            batch_rows = list(eval_rows[start : start + batch_size])
            batch_override = override_tensor[start : start + len(batch_rows)] if override_tensor is not None else None
            batch = _collate(batch_rows, device=device)
            z, read_meta = _compute_z(
                field=field,
                selector_payload=eval_selector_payload,
                rows=batch_rows,
                memory_representations=memory_representations,
                control=control,
                seed=seed,
                device=device,
                trained_programs=trained_programs,
                include_mask_override=batch_override,
            )
            student = _forward_student(
                backend=backend,
                injector=injector,
                batch=batch,
                memory_z=z,
                delta_multiplier=delta_multiplier,
            )
            teacher_kl, _ = sparse_teacher_kl_from_logits(
                student["target_logits"],
                batch["response_rows"],
                target_lengths=batch["target_lengths"],
                target="teacher",
            )
            per_state_nll = target_nll_by_state_from_logits(
                student["target_logits"],
                batch["labels"],
                target_lengths=batch["target_lengths"],
            )
            cursor = 0
            per_state_kl = []
            for row, target_len in zip(batch_rows, batch["target_lengths"]):
                kl, _ = sparse_teacher_kl_from_logits(
                    student["target_logits"][cursor : cursor + target_len],
                    [row["response_cache"]],
                    target_lengths=[target_len],
                    target="teacher",
                )
                per_state_kl.append(float(kl.detach().cpu()))
                cursor += target_len
            del teacher_kl
            for row, nll, kl in zip(batch_rows, per_state_nll, per_state_kl):
                response = row["response_cache"]
                out_rows.append(
                    {
                        "state_example_id": response["state_example_id"],
                        "split": response["split"],
                        "teacher_condition": response["teacher_condition"],
                        "L0": response["L0"],
                        "teacher_mean_target_nll": response["teacher_mean_target_nll"],
                        "student_target_nll": nll,
                        "sparse_teacher_kl": kl,
                    }
                )
            z_rows.append(z.detach().cpu())
            delta_norms.append(float(student["delta_norm"].detach().cpu()))
            delta_ratios.append(float(student["delta_ratio"].detach().cpu()))
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
    z_all = torch.cat(z_rows, dim=0) if z_rows else torch.empty(0, getattr(field, "program_dim", 128))
    return {
        "rows": out_rows,
        "summary": summarize_state_nll_rows(out_rows),
        "z_geometry": z_geometry(z_all),
        "delta_norm": distribution(delta_norms),
        "delta_ratio": distribution(delta_ratios),
        "selected_token_report": selected_token_report,
    }


def _train_epoch(
    *,
    backend: Any,
    field: nn.Module,
    injector: AdditiveTokenMemoryInjector,
    selector_payload: dict[str, Tensor],
    rows: list[dict[str, Any]],
    memory_representations: Tensor,
    optimizer: torch.optim.Optimizer,
    weights: StageC1LossWeights,
    device: torch.device,
    seed: int,
    batch_size: int,
    epoch: int,
    max_steps: int | None = None,
) -> dict[str, Any]:
    field.train()
    injector.train()
    metrics = []
    rng = random.Random(seed * 1_000_000 + epoch)
    steps = 0
    for indices in task_balanced_batches(rows, batch_size=batch_size, rng=rng):
        batch_rows = [rows[index] for index in indices]
        loss, report = _loss_for_batch(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=batch_rows,
            memory_representations=memory_representations,
            weights=weights,
            device=device,
            seed=seed,
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        trainable = [param for module in (field, injector) for param in module.parameters() if param.requires_grad]
        torch.nn.utils.clip_grad_norm_(trainable, 1.0)
        optimizer.step()
        metrics.append(report)
        steps += 1
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if max_steps is not None and steps >= max_steps:
            break
    return {
        "steps": steps,
        "metrics": {
            key: mean_std(report[key] for report in metrics if key in report)
            for key in ("loss", "teacher_kl", "action_ce", "preservation_kl", "delta_norm", "delta_ratio", "z_l2")
        },
    }


def _tiny_overfit(
    *,
    backend: Any,
    selector_payload: dict[str, Tensor],
    rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
    steps: int,
    lr: float,
) -> dict[str, Any]:
    positives = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    baseline = [row for row in rows if row["response_cache"]["teacher_condition"] == "baseline_teacher"]
    subset = positives[:8] + baseline[:8]
    if len(subset) < 2:
        return {"passed": False, "reason": "insufficient_tiny_overfit_rows", "subset_size": len(subset)}
    field = StageC1ProgramField(memory_dim=int(memory_representations.shape[1]), rank=128, program_dim=128).to(device)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    weights = StageC1LossWeights()
    before = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
    )
    optimizer = torch.optim.AdamW(list(field.parameters()) + list(injector.parameters()), lr=lr, weight_decay=1.0e-4)
    last_grad = {}
    for step in range(1, steps + 1):
        report = _train_epoch(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=subset,
            memory_representations=memory_representations,
            optimizer=optimizer,
            weights=weights,
            device=device,
            seed=seed,
            batch_size=1,
            epoch=step,
            max_steps=1,
        )
        del report
    batch = subset[:1]
    loss, _ = _loss_for_batch(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=batch,
        memory_representations=memory_representations,
        weights=weights,
        device=device,
        seed=seed,
    )
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    last_grad = {"program_head": _grad_norms(field), "injector": _grad_norms(injector)}
    after = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
    )
    before_kl = before["summary"]["sparse_teacher_kl"]["mean"] or 0.0
    after_kl = after["summary"]["sparse_teacher_kl"]["mean"] or 0.0
    before_nll = before["summary"]["target_nll"]["mean"] or 0.0
    after_nll = after["summary"]["target_nll"]["mean"] or 0.0
    program_grad = max(last_grad["program_head"].values(), default=0.0)
    injector_grad = max(last_grad["injector"].values(), default=0.0)
    return {
        "format": "stage_c1_tiny_overfit_v1",
        "subset_size": len(subset),
        "positive_rows": len(positives[:8]),
        "baseline_rows": len(baseline[:8]),
        "steps": steps,
        "before": before["summary"],
        "after": after["summary"],
        "program_grad_max_norm": program_grad,
        "injector_grad_max_norm": injector_grad,
        "gradient_norms_last_batch": last_grad,
        "passed": bool(after_kl < before_kl and after_nll <= before_nll + 0.05 and program_grad > 0 and injector_grad > 0),
    }


def _zero_delta_equivalence(
    *,
    backend: Any,
    selector_payload: dict[str, Tensor],
    rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
) -> dict[str, Any]:
    subset = rows[: min(4, len(rows))]
    field = StageC1ProgramField(memory_dim=int(memory_representations.shape[1]), rank=128, program_dim=128).to(device)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    evaluated = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=subset,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
    )
    diffs = [
        abs(float(item["student_target_nll"]) - float(item["L0"]))
        for item in evaluated["rows"]
    ]
    return {
        "format": "stage_c1_zero_delta_equivalence_v1",
        "state_count": len(subset),
        "max_abs_nll_delta_vs_bare": max(diffs, default=0.0),
        "delta_norm": evaluated["delta_norm"],
        "delta_ratio": evaluated["delta_ratio"],
        "selected_token_report": evaluated["selected_token_report"],
        "passed": bool(max(diffs, default=0.0) <= 2.0e-4 and (evaluated["delta_norm"].get("max") or 0.0) == 0.0),
    }


def _train_full_run(
    *,
    backend: Any,
    selector: nn.Module,
    selector_payload: dict[str, Tensor],
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    memory_representations: Tensor,
    model_dim: int,
    device: torch.device,
    seed: int,
    output_dir: Path,
    epochs: int,
    batch_size: int,
    eval_batch_size: int,
    lr: float,
    patience: int,
    program_kind: str = "content",
    matched_parameter_count: int | None = None,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    before_selector = selector_state_hash(selector)
    if program_kind == "content":
        field = StageC1ProgramField(memory_dim=int(memory_representations.shape[1]), rank=128, program_dim=128).to(device)
    elif program_kind == "free_id":
        field = StageC1ProgramField(
            memory_dim=int(memory_representations.shape[1]),
            rank=128,
            program_dim=128,
            program_kind="free_id",
            memory_count=int(memory_representations.shape[0]),
            matched_parameter_count=int(matched_parameter_count or 1),
        ).to(device)
    else:
        raise ValueError(program_kind)
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=seed).to(device)
    trainable = list(field.parameters()) + list(injector.parameters())
    optimizer = torch.optim.AdamW(trainable, lr=lr, weight_decay=1.0e-4)
    weights = StageC1LossWeights()
    best_metric = float("inf")
    best_epoch = 0
    best_field = copy.deepcopy(field.state_dict())
    best_injector = copy.deepcopy(injector.state_dict())
    history = []
    bad = 0
    for epoch in range(1, epochs + 1):
        train_report = _train_epoch(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=train_rows,
            memory_representations=memory_representations,
            optimizer=optimizer,
            weights=weights,
            device=device,
            seed=seed,
            batch_size=batch_size,
            epoch=epoch,
        )
        validation_eval = evaluate_student(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=validation_rows,
            memory_representations=memory_representations,
            device=device,
            seed=seed,
            batch_size=eval_batch_size,
        )
        metric = float(validation_eval["summary"]["sparse_teacher_kl"]["mean"] or float("inf"))
        history.append({"epoch": epoch, "train": train_report, "validation": validation_eval["summary"]})
        atomic_write_json(output_dir / f"{program_kind}_seed_{seed}_history.json", history)
        print(
            f"stage-c1 {program_kind} seed={seed} epoch={epoch} "
            f"val_kl={metric:.6f} val_nll={validation_eval['summary']['target_nll']['mean']}",
            flush=True,
        )
        if metric < best_metric - 1.0e-6:
            best_metric = metric
            best_epoch = epoch
            best_field = copy.deepcopy(field.state_dict())
            best_injector = copy.deepcopy(injector.state_dict())
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    field.load_state_dict(best_field)
    injector.load_state_dict(best_injector)
    train_eval = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=train_rows,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=eval_batch_size,
    )
    validation_eval = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=validation_rows,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=eval_batch_size,
    )
    with torch.no_grad():
        programs = field.programs(memory_representations.to(device=device, dtype=torch.float32)).detach().cpu()
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{program_kind}_seed_{seed}.pt"
    torch.save(
        {
            "format": RUN_VERSION,
            "program_kind": program_kind,
            "seed": seed,
            "best_epoch": best_epoch,
            "field_state_dict": {key: value.detach().cpu() for key, value in field.state_dict().items()},
            "injector_state_dict": {key: value.detach().cpu() for key, value in injector.state_dict().items()},
            "selector_checkpoint_seed": seed,
            "source_commit": maybe_git_commit(),
            "program_parameter_count": parameter_count(field),
            "injector_parameter_count": parameter_count(injector),
        },
        checkpoint_path,
    )
    controls = {}
    trained_programs = programs.to(device)
    for control in (
        "shuffled_state",
        "mean_state",
        "zero_state",
        "shuffled_program",
        "fixed_random_program",
        "mean_program",
        "zero_program",
        "global_prior_only",
    ):
        controls[control] = evaluate_student(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=validation_rows,
            memory_representations=memory_representations,
            device=device,
            seed=seed,
            batch_size=eval_batch_size,
            control=control,
            trained_programs=trained_programs,
        )
    controls["bare_qwen_zero_field"] = _evaluate_cache_baseline(validation_rows, mode="bare_qwen_zero_field")
    controls["best_raw_text_memory_teacher_oracle"] = _evaluate_cache_baseline(validation_rows, mode="teacher_oracle")
    ci_inputs = {"correct": validation_eval["rows"], **{key: value["rows"] for key, value in controls.items() if "rows" in value}}
    bootstrap = paired_ci(ci_inputs, baseline_name="correct", metrics=("student_target_nll", "sparse_teacher_kl"), seed=seed)
    validation_eval["bootstrap_ci"] = bootstrap
    validation_eval["control_deltas"] = _control_deltas(validation_eval, controls)
    selector_after = prepare_selector_payload(
        selector=selector,
        state_representations=selector_payload["q"].new_zeros((selector_payload["q"].shape[0], 0)),
        memory_representations=selector_payload["k"].new_zeros((selector_payload["k"].shape[0], 0)),
        mu=selector_payload["k_bar"][:, -1].detach().cpu(),
        device=device,
    ) if False else selector_payload
    selector_change = selector_parameter_change(before_selector, selector)
    return {
        "format": "stage_c1_seed_run_v1",
        "program_kind": program_kind,
        "seed": seed,
        "best_epoch": best_epoch,
        "epochs_ran": len(history),
        "checkpoint": str(checkpoint_path),
        "history": history,
        "train": {"correct": train_eval},
        "validation": {"correct": validation_eval, "controls": controls, "control_deltas": validation_eval["control_deltas"]},
        "program_geometry": program_geometry(programs),
        "selector_parameter_change": selector_change,
        "program_parameter_count": parameter_count(field),
        "injector_parameter_count": parameter_count(injector),
    }


def _load_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _content_field_and_injector_from_checkpoint(
    *,
    checkpoint_path: Path,
    memory_dim: int,
    model_dim: int,
    device: torch.device,
) -> tuple[StageC1ProgramField, AdditiveTokenMemoryInjector, dict[str, Any]]:
    checkpoint = torch.load(checkpoint_path, map_location=device)
    if checkpoint.get("program_kind") != "content":
        raise ValueError(f"Expected content checkpoint at {checkpoint_path}, found {checkpoint.get('program_kind')}")
    field = StageC1ProgramField(memory_dim=memory_dim, rank=128, program_dim=128).to(device)
    field.load_state_dict(checkpoint["field_state_dict"])
    injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=int(checkpoint["seed"])).to(device)
    injector.load_state_dict(checkpoint["injector_state_dict"])
    return field, injector, checkpoint


def _recompute_existing_summary(
    *,
    backend: Any,
    metadata: dict[str, Any],
    response_validation: dict[str, Any],
    output_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    validation_rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    eval_batch_size: int,
    controls_to_recompute: Sequence[str],
    started: float,
) -> dict[str, Any]:
    previous_summary_path = output_dir / "summary.json"
    if not previous_summary_path.exists():
        raise FileNotFoundError(f"Cannot eval-only recompute without existing summary: {previous_summary_path}")
    previous_summary = _load_json_file(previous_summary_path)
    field_algebra = _load_json_file(output_dir / "field_algebra_validation.json")
    zero_delta = _load_json_file(output_dir / "zero_delta_equivalence.json")
    tiny = _load_json_file(output_dir / "tiny_overfit.json")
    smoke = _load_json_file(output_dir / "short_smoke.json")
    leave_one_out = _load_json_file(output_dir / "leave_one_out_audit.json")
    selector_preservation = []
    runs = []
    for seed in seeds:
        print(f"eval-only recomputing Stage-C1 controls seed={seed}: {list(controls_to_recompute)}", flush=True)
        run_path = output_dir / f"seed_{seed}_run.json"
        run = _load_json_file(run_path)
        selector, selector_payload, selector_path = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        del selector
        field, injector, checkpoint = _content_field_and_injector_from_checkpoint(
            checkpoint_path=output_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach().cpu()
        trained_programs = programs.to(device)
        controls = dict(run.get("validation", {}).get("controls", {}))
        for control in controls_to_recompute:
            controls[control] = evaluate_student(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                rows=validation_rows,
                memory_representations=memory_reps,
                device=device,
                seed=int(seed),
                batch_size=eval_batch_size,
                control=str(control),
                trained_programs=trained_programs,
            )
            atomic_write_json(output_dir / f"seed_{seed}_{control}_eval_only.json", controls[control])
        validation_eval = run["validation"]["correct"]
        ci_inputs = {"correct": validation_eval["rows"], **{key: value["rows"] for key, value in controls.items() if "rows" in value}}
        validation_eval["bootstrap_ci"] = paired_ci(
            ci_inputs,
            baseline_name="correct",
            metrics=("student_target_nll", "sparse_teacher_kl"),
            seed=int(seed),
        )
        validation_eval["control_deltas"] = _control_deltas(validation_eval, controls)
        run["validation"]["correct"] = validation_eval
        run["validation"]["controls"] = controls
        run["validation"]["control_deltas"] = validation_eval["control_deltas"]
        run["program_geometry"] = program_geometry(programs)
        run["selector_checkpoint"] = selector_path
        run["eval_only_recomputed_from_checkpoints"] = True
        run["eval_only_recomputed_controls"] = list(controls_to_recompute)
        run["eval_only_source_commit"] = maybe_git_commit()
        run["content_checkpoint_metadata"] = {
            "checkpoint": str(output_dir / "checkpoints" / f"content_seed_{seed}.pt"),
            "training_source_commit": checkpoint.get("source_commit"),
            "best_epoch": checkpoint.get("best_epoch"),
        }
        preservation = run.get("selector_payload_preservation")
        if preservation is None:
            prior = previous_summary.get("selector_preservation", [])
            preservation = prior[len(selector_preservation)] if len(prior) > len(selector_preservation) else {"passed": False, "reason": "missing_selector_preservation"}
        selector_preservation.append(preservation)
        atomic_write_json(run_path, run)
        runs.append(run)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    aggregate = summarize_runs(runs)
    decision = stage_c1_decision(
        runs=runs,
        selector_preservation=selector_preservation,
        cache_validation_passed=bool(response_validation["passed"]),
        tiny_overfit_passed=bool(tiny["passed"]),
        leave_one_out=leave_one_out,
    )
    summary = {
        **metadata,
        "runtime_s": previous_summary.get("runtime_s"),
        "eval_only_runtime_s": time.perf_counter() - started,
        "eval_only_recomputed_from_checkpoints": True,
        "eval_only_recomputed_controls": list(controls_to_recompute),
        "training_source_commit": previous_summary.get("source_commit"),
        "evaluation_source_commit": maybe_git_commit(),
        "response_cache_validation": response_validation,
        "field_algebra": field_algebra,
        "zero_delta_equivalence": zero_delta,
        "tiny_overfit": tiny,
        "short_smoke": smoke,
        "runs": runs,
        "aggregate": aggregate,
        "selector_preservation": selector_preservation,
        "leave_one_out": leave_one_out,
        "decision_gate": decision,
        "checkpoint_dir": str(output_dir / "checkpoints"),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _write_report(summary))
    return summary


def _control_deltas(correct: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    correct_rows = {row["state_example_id"]: row for row in correct["rows"]}
    output = {}
    for name, payload in controls.items():
        if "rows" not in payload:
            continue
        values: dict[str, list[float]] = {"student_target_nll": [], "sparse_teacher_kl": []}
        for row in payload["rows"]:
            state_id = row["state_example_id"]
            if state_id not in correct_rows:
                continue
            values["student_target_nll"].append(float(correct_rows[state_id]["student_target_nll"]) - float(row["student_target_nll"]))
            values["sparse_teacher_kl"].append(float(correct_rows[state_id]["sparse_teacher_kl"]) - float(row["sparse_teacher_kl"]))
        output[name] = {metric: mean_std(items) for metric, items in values.items()}
    return output


def _leave_one_out_audit(
    *,
    backend: Any,
    field: nn.Module,
    injector: AdditiveTokenMemoryInjector,
    selector_payload: dict[str, Tensor],
    rows: list[dict[str, Any]],
    memory_representations: Tensor,
    device: torch.device,
    seed: int,
    max_states: int = 16,
) -> dict[str, Any]:
    positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    positive_rows = positive_rows[:max_states]
    if not positive_rows:
        return {"state_count": 0, "teacher_best_hurts_more_fraction": 0.0}
    full_eval = evaluate_student(
        backend=backend,
        field=field,
        injector=injector,
        selector_payload=selector_payload,
        rows=positive_rows,
        memory_representations=memory_representations,
        device=device,
        seed=seed,
        batch_size=1,
    )
    full_by_state = {row["state_example_id"]: row for row in full_eval["rows"]}
    effects = []
    utility_values = []
    best_hurts_more = 0
    per_state = []
    with torch.no_grad():
        programs = field.programs(memory_representations.to(device=device, dtype=torch.float32)).detach()
    for row in positive_rows:
        best_pos = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
        valid_utils = [
            (index, float(value))
            for index, (valid, value) in enumerate(zip(row["valid_mask"], row["raw_utility"]))
            if valid and value is not None and index != best_pos
        ]
        neutral = min(valid_utils, key=lambda item: abs(item[1]), default=None)
        negative = min(valid_utils, key=lambda item: item[1], default=None)
        removals = {"teacher_best": best_pos}
        if neutral is not None:
            removals["matched_neutral"] = neutral[0]
        if negative is not None:
            removals["matched_negative_or_random"] = negative[0]
        row_effect: dict[str, Any] = {
            "state_example_id": row["state_example_id"],
            "best_memory_id": row["response_cache"]["best_memory_id"],
            "teacher_utility": row["response_cache"]["teacher_utility"],
            "full_nll": full_by_state[row["state_example_id"]]["student_target_nll"],
            "removals": {},
        }
        for name, remove_index in removals.items():
            include_mask = resolve_include_mask([row], validation_full_bank=True)
            include_mask[0, int(remove_index)] = False
            payload = evaluate_student(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                rows=[row],
                memory_representations=memory_representations,
                device=device,
                seed=seed,
                batch_size=1,
                trained_programs=programs,
                include_mask_override=include_mask,
            )
            nll = payload["rows"][0]["student_target_nll"]
            delta = float(nll) - float(row_effect["full_nll"])
            row_effect["removals"][name] = {"removed_stage_index": remove_index, "target_nll": nll, "delta_vs_full": delta}
        teacher_delta = row_effect["removals"].get("teacher_best", {}).get("delta_vs_full")
        control_deltas = [
            value["delta_vs_full"]
            for key, value in row_effect["removals"].items()
            if key != "teacher_best"
        ]
        if teacher_delta is not None and control_deltas and teacher_delta > max(control_deltas):
            best_hurts_more += 1
        if teacher_delta is not None:
            effects.append(float(teacher_delta))
            utility_values.append(float(row["response_cache"]["teacher_utility"]))
        per_state.append(row_effect)
    return {
        "format": "stage_c1_leave_one_out_audit_v1",
        "state_count": len(positive_rows),
        "teacher_best_hurts_more_fraction": best_hurts_more / len(positive_rows),
        "teacher_utility_vs_leave_one_out_effect_correlation": _pearson(utility_values, effects),
        "teacher_best_delta_distribution": distribution(effects),
        "per_state": per_state,
    }


def _selector_payload_for_seed(
    *,
    selector_dir: Path,
    seed: int,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    device: torch.device,
) -> tuple[nn.Module, dict[str, Tensor], str]:
    path = selector_dir / "continuity" / "checkpoints" / f"signed_core_field_r128_empirical_train_mu_seed_{seed}.pt"
    checkpoint = torch.load(path, map_location="cpu")
    selector = load_signed_selector_checkpoint(
        checkpoint=checkpoint,
        state_dim=int(state_reps.shape[1]),
        memory_dim=int(memory_reps.shape[1]),
        rank=128,
    ).to(device)
    payload = prepare_selector_payload(
        selector=selector,
        state_representations=state_reps,
        memory_representations=memory_reps,
        mu=mu,
        device=device,
    )
    return selector, payload, str(path)


def _smoke_subset(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    positives = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    baseline = [row for row in rows if row["response_cache"]["teacher_condition"] == "baseline_teacher"]
    out = []
    for index in range(max(len(positives), len(baseline))):
        if index < len(positives):
            out.append(positives[index])
        if index < len(baseline):
            out.append(baseline[index])
        if len(out) >= limit:
            break
    return out


def _write_report(summary: dict[str, Any]) -> str:
    gate = summary["decision_gate"]
    aggregate = summary["aggregate"]
    validation = aggregate.get("validation", {})
    lines = [
        "# Milestone 5 Stage C1 Signed Program Field",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- hard scope: no AppWorld generation/evaluation, no Qwen fine-tuning, no selector fine-tuning, no Stage C2.",
        f"- response cache: `{summary['response_cache_dir']}`",
        f"- response-cache validation passed: `{summary['response_cache_validation']['passed']}`",
        f"- field algebra passed: `{summary['field_algebra']['passed']}`",
        f"- zero-delta equivalence passed: `{summary['zero_delta_equivalence']['passed']}`",
        f"- tiny overfit passed: `{summary['tiny_overfit']['passed']}`",
        "",
        "## Three-Seed Validation",
        "",
        f"- target NLL: `{validation.get('target_nll', {})}`",
        f"- sparse teacher KL: `{validation.get('sparse_teacher_kl', {})}`",
        f"- L0 - student: `{validation.get('L0_minus_student', {})}`",
        f"- improved fraction: `{validation.get('improved_fraction', {})}`",
        "",
        "## Controls",
        "",
    ]
    for key, value in aggregate.items():
        if key.startswith("correct_minus_"):
            lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"```json\n{json.dumps(gate, indent=2, sort_keys=True)}\n```",
            "",
            "## Artifacts",
            "",
            f"- checkpoints: `{summary['checkpoint_dir']}`",
            f"- summary: `{summary['output_dir']}/summary.json`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Milestone 5 / Stage-C1 signed program field pilot.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--response-cache-dir", required=True)
    parser.add_argument("--signed-field-dir", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--lr", type=float, default=2.0e-4)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--tiny-overfit-steps", type=int, default=32)
    parser.add_argument("--smoke-steps", type=int, default=16)
    parser.add_argument("--smoke-states", type=int, default=32)
    parser.add_argument("--skip-free-id-control", action="store_true")
    parser.add_argument("--eval-only-existing", action="store_true")
    parser.add_argument("--recompute-controls", nargs="+", default=["shuffled_state", "mean_state"])
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    if cfg.model.backend != "hf_qwen":
        raise ValueError("Stage-C1 requires hf_qwen")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)
    backend = build_backend(cfg, load_model=True)
    context_limit = getattr(getattr(backend.model, "config", None), "max_position_embeddings", 40960)
    model_dim = int(getattr(getattr(backend.model, "config", None), "hidden_size"))
    data_dir = Path(args.data)
    labels_dir = Path(args.labels_dir)
    repr_dir = Path(args.representation_cache_dir)
    response_dir = Path(args.response_cache_dir)
    selector_dir = Path(args.signed_field_dir)

    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    label_rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    response_rows = _load_rows(response_dir / "response_cache.jsonl")
    response_by_state = _response_rows_by_state(response_rows)
    teacher_rows = {
        str(row.get("pair_key")): row
        for row in read_jsonl(Path(args.teacher_cache_dir) / "teacher_cache_full_rows.jsonl")
    }
    response_validation = validate_response_cache(
        response_rows,
        label_rows=label_rows,
        memory_bank=memory_bank,
        teacher_rows=teacher_rows,
    )
    if not response_validation["passed"]:
        atomic_write_json(output_dir / "response_cache_validation_failed.json", response_validation)
        raise SystemExit(f"Response cache validation failed: {response_validation['errors_first_50']}")

    memory_indices = [int(row["memory_index"]) for row in memory_bank]
    state_reps, state_meta = _load_representation_cache(
        repr_dir / "decision_state_representations.pt",
        expected_count=len(label_rows),
        expected_source_path=data_dir / "decision_examples.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"pooled_qwen_hidden_v1", "pooled_qwen_hidden_v2"},
    )
    all_memory_reps, memory_meta = _load_representation_cache(
        repr_dir / "memory_record_representations.pt",
        expected_count=46,
        expected_source_path=data_dir / "memory_records.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"chunked_qwen_hidden_v1", "record_qwen_hidden_v2"},
    )
    memory_reps = all_memory_reps[memory_indices].to(device=device, dtype=torch.float32)
    train_label_rows, validation_label_rows = split_rows(label_rows)
    train_token_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        label_rows=train_label_rows,
        response_by_state=response_by_state,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(context_limit),
    )
    validation_token_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        label_rows=validation_label_rows,
        response_by_state=response_by_state,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(context_limit),
    )
    mu = train_memory_prior(train_label_rows, memory_count=len(memory_bank))

    metadata = {
        "format": RUN_VERSION,
        "source_commit": maybe_git_commit(),
        "output_dir": str(output_dir),
        "config": str(args.config),
        "data_dir": str(data_dir),
        "teacher_cache_dir": str(args.teacher_cache_dir),
        "labels_dir": str(labels_dir),
        "response_cache_dir": str(response_dir),
        "signed_field_dir": str(selector_dir),
        "representation_cache_dir": str(repr_dir),
        "seeds": args.seeds,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "eval_batch_size": args.eval_batch_size,
        "lr": args.lr,
        "patience": args.patience,
        "device": str(device),
        "model_name": cfg.model.name,
        "checkpoint_identity": f"frozen_hf_pretrained:{cfg.model.name}",
        "train_rows": len(train_token_rows),
        "validation_rows": len(validation_token_rows),
        "effective_memory_count": len(memory_bank),
        "state_cache": state_meta,
        "memory_cache": memory_meta,
    }
    atomic_write_json(output_dir / "run_metadata.json", metadata)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")

    if args.eval_only_existing:
        summary = _recompute_existing_summary(
            backend=backend,
            metadata=metadata,
            response_validation=response_validation,
            output_dir=output_dir,
            selector_dir=selector_dir,
            state_reps=state_reps,
            memory_reps=memory_reps,
            mu=mu,
            validation_rows=validation_token_rows,
            model_dim=model_dim,
            device=device,
            seeds=args.seeds,
            eval_batch_size=args.eval_batch_size,
            controls_to_recompute=args.recompute_controls,
            started=started,
        )
        print(f"Recomputed Stage-C1 evaluation artifacts to {output_dir}; decision={summary['decision_gate']}", flush=True)
        return

    field_algebra = validate_program_field_algebra(rank=128, program_dim=128, count=len(memory_bank), seed=13)
    atomic_write_json(output_dir / "field_algebra_validation.json", field_algebra)
    selector0, selector_payload0, selector_path0 = _selector_payload_for_seed(
        selector_dir=selector_dir,
        seed=args.seeds[0],
        state_reps=state_reps,
        memory_reps=memory_reps.detach().cpu(),
        mu=mu,
        device=device,
    )
    zero_delta = _zero_delta_equivalence(
        backend=backend,
        selector_payload=selector_payload0,
        rows=validation_token_rows,
        memory_representations=memory_reps,
        model_dim=model_dim,
        device=device,
        seed=args.seeds[0],
    )
    atomic_write_json(output_dir / "zero_delta_equivalence.json", zero_delta)
    if not zero_delta["passed"]:
        raise SystemExit(f"Zero-delta equivalence failed: {zero_delta}")
    tiny = _tiny_overfit(
        backend=backend,
        selector_payload=selector_payload0,
        rows=train_token_rows,
        memory_representations=memory_reps,
        model_dim=model_dim,
        device=device,
        seed=args.seeds[0],
        steps=args.tiny_overfit_steps,
        lr=args.lr,
    )
    atomic_write_json(output_dir / "tiny_overfit.json", tiny)
    if not tiny["passed"]:
        summary = {**metadata, "response_cache_validation": response_validation, "field_algebra": field_algebra, "zero_delta_equivalence": zero_delta, "tiny_overfit": tiny}
        atomic_write_json(output_dir / "summary_stopped_after_tiny_overfit.json", summary)
        raise SystemExit(f"Tiny overfit failed: {tiny}")

    smoke_rows = _smoke_subset(train_token_rows, args.smoke_states)
    smoke_field = StageC1ProgramField(memory_dim=int(memory_reps.shape[1]), rank=128, program_dim=128).to(device)
    smoke_injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=args.seeds[0]).to(device)
    smoke_optimizer = torch.optim.AdamW(list(smoke_field.parameters()) + list(smoke_injector.parameters()), lr=args.lr, weight_decay=1.0e-4)
    smoke_history = []
    for step in range(1, args.smoke_steps + 1):
        smoke_history.append(
            _train_epoch(
                backend=backend,
                field=smoke_field,
                injector=smoke_injector,
                selector_payload=selector_payload0,
                rows=smoke_rows,
                memory_representations=memory_reps,
                optimizer=smoke_optimizer,
                weights=StageC1LossWeights(),
                device=device,
                seed=args.seeds[0],
                batch_size=args.batch_size,
                epoch=step,
                max_steps=1,
            )
        )
    smoke_eval = evaluate_student(
        backend=backend,
        field=smoke_field,
        injector=smoke_injector,
        selector_payload=selector_payload0,
        rows=smoke_rows[: min(8, len(smoke_rows))],
        memory_representations=memory_reps,
        device=device,
        seed=args.seeds[0],
        batch_size=args.eval_batch_size,
    )
    smoke = {"format": "stage_c1_smoke_v1", "history": smoke_history, "eval": smoke_eval["summary"], "passed": bool(math.isfinite(smoke_eval["summary"]["target_nll"]["mean"] or 0.0))}
    atomic_write_json(output_dir / "short_smoke.json", smoke)
    if not smoke["passed"]:
        raise SystemExit("Short smoke failed")

    runs = []
    selector_preservation = []
    content_param_count = parameter_count(StageC1ProgramField(memory_dim=int(memory_reps.shape[1]), rank=128, program_dim=128))
    for seed in args.seeds:
        print(f"running Stage-C1 content-derived seed={seed}", flush=True)
        selector, selector_payload, selector_path = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=seed,
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        before_payload = {key: value.detach().cpu().clone() for key, value in selector_payload.items()}
        run = _train_full_run(
            backend=backend,
            selector=selector,
            selector_payload=selector_payload,
            train_rows=train_token_rows,
            validation_rows=validation_token_rows,
            memory_representations=memory_reps,
            model_dim=model_dim,
            device=device,
            seed=seed,
            output_dir=output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            eval_batch_size=args.eval_batch_size,
            lr=args.lr,
            patience=args.patience,
            program_kind="content",
        )
        run["selector_checkpoint"] = selector_path
        after_payload = prepare_selector_payload(
            selector=selector,
            state_representations=state_reps,
            memory_representations=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        preservation = validate_selector_preserved(before_payload, after_payload, atol=0.0)
        selector_preservation.append(preservation)
        run["selector_payload_preservation"] = preservation
        if not args.skip_free_id_control:
            print(f"running Stage-C1 free-ID control seed={seed}", flush=True)
            free_run = _train_full_run(
                backend=backend,
                selector=selector,
                selector_payload=selector_payload,
                train_rows=train_token_rows,
                validation_rows=validation_token_rows,
                memory_representations=memory_reps,
                model_dim=model_dim,
                device=device,
                seed=seed,
                output_dir=output_dir,
                epochs=args.epochs,
                batch_size=args.batch_size,
                eval_batch_size=args.eval_batch_size,
                lr=args.lr,
                patience=args.patience,
                program_kind="free_id",
                matched_parameter_count=content_param_count,
            )
            run["validation"]["controls"]["free_id_program"] = free_run["validation"]["correct"]
            run["validation"]["control_deltas"] = _control_deltas(
                run["validation"]["correct"],
                run["validation"]["controls"],
            )
            run["free_id_control"] = {
                "checkpoint": free_run["checkpoint"],
                "program_parameter_count": free_run["program_parameter_count"],
                "injector_parameter_count": free_run["injector_parameter_count"],
                "validation": free_run["validation"]["correct"]["summary"],
            }
        atomic_write_json(output_dir / f"seed_{seed}_run.json", run)
        runs.append(run)

    # Use the first completed content run for the leave-one-out audit.
    first_run = runs[0]
    checkpoint = torch.load(first_run["checkpoint"], map_location=device)
    audit_field = StageC1ProgramField(memory_dim=int(memory_reps.shape[1]), rank=128, program_dim=128).to(device)
    audit_field.load_state_dict(checkpoint["field_state_dict"])
    audit_injector = _initialize_injector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", seed=args.seeds[0]).to(device)
    audit_injector.load_state_dict(checkpoint["injector_state_dict"])
    leave_one_out = _leave_one_out_audit(
        backend=backend,
        field=audit_field,
        injector=audit_injector,
        selector_payload=selector_payload0,
        rows=validation_token_rows,
        memory_representations=memory_reps,
        device=device,
        seed=args.seeds[0],
        max_states=16,
    )
    atomic_write_json(output_dir / "leave_one_out_audit.json", leave_one_out)

    aggregate = summarize_runs(runs)
    decision = stage_c1_decision(
        runs=runs,
        selector_preservation=selector_preservation,
        cache_validation_passed=bool(response_validation["passed"]),
        tiny_overfit_passed=bool(tiny["passed"]),
        leave_one_out=leave_one_out,
    )
    summary = {
        **metadata,
        "runtime_s": time.perf_counter() - started,
        "response_cache_validation": response_validation,
        "field_algebra": field_algebra,
        "zero_delta_equivalence": zero_delta,
        "tiny_overfit": tiny,
        "short_smoke": smoke,
        "runs": runs,
        "aggregate": aggregate,
        "selector_preservation": selector_preservation,
        "leave_one_out": leave_one_out,
        "decision_gate": decision,
        "checkpoint_dir": str(output_dir / "checkpoints"),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _write_report(summary))
    print(f"Wrote Stage-C1 signed program artifacts to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
