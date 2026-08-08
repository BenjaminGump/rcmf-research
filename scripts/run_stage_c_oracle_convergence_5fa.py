from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import random
import time
from typing import Any, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.addressing_4b import mean_std
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_capacity_5e import (
    select_balanced_validation_subset,
    validate_target_token_utility_identity,
)
from rcmf.training.oracle_convergence_5fa import (
    OBJECTIVES_5FA,
    ORACLE_CONVERGENCE_VERSION,
    ConvergenceObjective,
    IndependentPairTensorTable,
    apply_independent_optimizer_step,
    assess_plateau,
    choose_pilot_objective,
    enrich_sequence_utility_rows,
    load_training_checkpoint,
    objective_loss,
    save_training_checkpoint,
    select_convergence_subset,
    sequence_utility_loss,
    summarize_convergence_rows,
    update_count_summary,
    utility_capacity_gate,
)
from rcmf.training.pair_grounding_5d import PAIR_RESPONSE_CACHE_VERSION
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend
from scripts.run_stage_c_oracle_capacity_5e import (
    _capacity_injector,
    _collate,
    _evaluate_direct_delta,
    _forward_direct_delta,
    _forward_injector,
    _loss_terms_from_logits,
    _precompute_direct_base_norms,
    _rows_from_logits,
)
from scripts.run_stage_c_pair_grounding_5d import _build_tokenized_pair_rows


def utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _select_by_pair_ids(rows: Sequence[dict[str, Any]], pair_ids: Sequence[str]) -> list[dict[str, Any]]:
    by_id = {str(row["pair_id"]): row for row in rows}
    missing = sorted(set(str(pair_id) for pair_id in pair_ids) - set(by_id))
    if missing:
        raise ValueError(f"Missing tokenized pair IDs: {missing[:10]}")
    return [by_id[str(pair_id)] for pair_id in pair_ids]


def _sequence_terms_from_logits(
    *,
    logits: Tensor,
    labels: Tensor,
    response_rows: Sequence[dict[str, Any]],
    target_lengths: Sequence[int],
    huber_delta: float,
) -> dict[str, Tensor]:
    target_mask = labels[..., 1:].ne(-100)
    target_labels = labels[..., 1:][target_mask].to(logits.device)
    if target_labels.numel() != logits.shape[0]:
        raise ValueError(f"logit/label target count mismatch {logits.shape[0]} != {target_labels.numel()}")
    cursor = 0
    row_student_nll = []
    baseline_nll = []
    teacher_utility = []
    for response, target_len in zip(response_rows, target_lengths):
        length = int(target_len)
        row_logits = logits[cursor : cursor + length].to(torch.float32)
        row_labels = target_labels[cursor : cursor + length]
        row_log_probs = F.log_softmax(row_logits, dim=-1)
        row_student_nll.append(-row_log_probs[torch.arange(length, device=logits.device), row_labels].mean())
        baseline_nll.append(float(response["baseline_mean_target_nll"]))
        teacher_utility.append(float(response["text_utility"]))
        cursor += length
    if cursor != logits.shape[0]:
        raise ValueError(f"target logits row count mismatch: cursor={cursor} logits={logits.shape[0]}")
    return sequence_utility_loss(
        baseline_nll=torch.tensor(baseline_nll, device=logits.device, dtype=torch.float32),
        student_nll=torch.stack(row_student_nll),
        teacher_utility=torch.tensor(teacher_utility, device=logits.device, dtype=torch.float32),
        huber_delta=huber_delta,
    )


def _training_loss(
    *,
    logits: Tensor,
    batch: dict[str, Any],
    objective: ConvergenceObjective,
) -> tuple[Tensor, dict[str, Tensor]]:
    token_terms = _loss_terms_from_logits(
        logits,
        batch["labels"],
        batch["response_rows"],
        target_lengths=batch["target_lengths"],
        huber_delta=objective.huber_delta,
    )
    sequence_terms = _sequence_terms_from_logits(
        logits=logits,
        labels=batch["labels"],
        response_rows=batch["response_rows"],
        target_lengths=batch["target_lengths"],
        huber_delta=objective.huber_delta,
    )
    loss = objective_loss(
        target_delta_huber=token_terms["target_delta_huber"],
        sequence_utility_huber=sequence_terms["sequence_utility_huber"],
        sparse_teacher_kl=token_terms["sparse_teacher_kl"],
        objective=objective,
    )
    return loss, {**token_terms, **sequence_terms}


def _evaluate_direct_tensor(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    delta_tensor: Tensor,
    pair_ids: Sequence[str],
    device: torch.device,
    k: int,
    batch_size: int,
    huber_delta: float,
    control: str,
) -> dict[str, Any]:
    pair_to_index = {str(pair_id): index for index, pair_id in enumerate(pair_ids)}
    evaluation = _evaluate_direct_delta(
        backend=backend,
        rows=rows,
        delta_table=delta_tensor,
        pair_to_index=pair_to_index,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=huber_delta,
        control=control,
    )
    enriched = enrich_sequence_utility_rows(evaluation["rows"], huber_delta=huber_delta)
    return {
        "rows": enriched,
        "summary": summarize_convergence_rows(enriched),
        "selected_token_report": evaluation["selected_token_report"],
    }


def _boundary_fraction(table: IndependentPairTensorTable, base_norms: Tensor, ratio_budget: float) -> float:
    with torch.no_grad():
        norms = table.stacked().to(torch.float32).flatten(start_dim=1).norm(dim=1)
        ratios = norms / base_norms.to(device=norms.device, dtype=torch.float32).clamp_min(1.0e-8)
        return float((ratios >= float(ratio_budget) * (1.0 - 1.0e-5)).to(torch.float32).mean().cpu())


def _movement_summary(current: Tensor, previous: Tensor | None) -> dict[str, Any]:
    if previous is None:
        return {"available": False}
    movement = (current.to(torch.float32) - previous.to(torch.float32)).flatten(start_dim=1).norm(dim=1)
    return {
        "available": True,
        "l2": {
            **mean_std(float(value) for value in movement.tolist()),
            "max": float(movement.max()) if movement.numel() else None,
        },
    }


def _checkpoint_updates(*, maximum: int) -> list[int]:
    requested = [2, 8, 16, 32, 48, 64, 80, 96, 112, 128]
    return [value for value in requested if value <= int(maximum)]


def _portable_evaluation(evaluation: dict[str, Any], rows_path: Path) -> dict[str, Any]:
    write_jsonl(rows_path, evaluation["rows"])
    return {
        "summary": evaluation["summary"],
        "selected_token_report": evaluation.get("selected_token_report"),
        "rows_path": str(rows_path),
    }


def _train_direct_convergence(
    *,
    backend: Any,
    rows: list[dict[str, Any]],
    objective: ConvergenceObjective,
    ratio_budget: float,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    batch_size: int,
    lr: float,
    minimum_updates: int,
    maximum_updates: int,
    zero_evaluation: dict[str, Any],
    progress_interval_s: float,
    resume: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "run_summary.json"
    if summary_path.exists():
        completed = json.loads(summary_path.read_text(encoding="utf-8"))
        if completed.get("status") == "completed":
            print(f"Reusing completed direct run {output_dir}", flush=True)
            return completed

    torch.manual_seed(seed)
    random.seed(seed)
    pair_ids = [str(row["pair_id"]) for row in rows]
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    table = IndependentPairTensorTable(pair_ids, (k, model_dim), init_std=0.0).to(device)
    base_norms = _precompute_direct_base_norms(backend=backend, rows=rows, device=device, k=k).to(device)
    optimizer = torch.optim.AdamW(table.parameters(), lr=lr, weight_decay=0.0)
    update_counts = [0] * len(rows)
    completed_rounds = 0
    history: list[dict[str, Any]] = []
    latest_pointer = output_dir / "latest_checkpoint.json"
    checkpoint_path: Path | None = None
    if resume and latest_pointer.exists():
        pointer = json.loads(latest_pointer.read_text(encoding="utf-8"))
        resume_path = Path(pointer["checkpoint"])
        restored = load_training_checkpoint(resume_path, table=table, optimizer=optimizer)
        expected = {
            "objective": objective.name,
            "ratio_budget": float(ratio_budget),
            "k": int(k),
        }
        for key, value in expected.items():
            if restored["metadata"].get(key) != value:
                raise ValueError(f"resume metadata mismatch for {key}")
        update_counts = restored["update_counts"]
        completed_rounds = restored["completed_rounds"]
        history_path = output_dir / "history.json"
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        checkpoint_path = resume_path
        print(f"Resumed {objective.name} ratio={ratio_budget} at {completed_rounds} updates/pair", flush=True)

    evaluation_updates = set(_checkpoint_updates(maximum=maximum_updates))
    previous_snapshot = table.stacked().detach().cpu() if completed_rounds else None
    interval_reports: list[dict[str, float]] = []
    last_progress = time.perf_counter()
    final_evaluation: dict[str, Any] | None = None
    convergence = {"assessable": False, "plateau": False}
    if history:
        latest_history = max(history, key=lambda item: int(item["updates_per_pair"]))
        if int(latest_history["updates_per_pair"]) == completed_rounds:
            convergence = dict(latest_history.get("convergence", convergence))
    terminal_resume = bool(
        completed_rounds >= maximum_updates
        or (completed_rounds >= minimum_updates and convergence.get("plateau"))
    )
    if terminal_resume:
        final_evaluation = _evaluate_direct_tensor(
            backend=backend,
            rows=rows,
            delta_tensor=table.stacked().detach(),
            pair_ids=pair_ids,
            device=device,
            k=k,
            batch_size=batch_size,
            huber_delta=objective.huber_delta,
            control=f"direct_delta_u{completed_rounds}",
        )

    loop_maximum = completed_rounds if terminal_resume else maximum_updates
    for update_round in range(completed_rounds + 1, loop_maximum + 1):
        order = list(range(len(rows)))
        random.Random(seed * 1_000_000 + update_round).shuffle(order)
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            indices = order[start : start + batch_size]
            batch_rows = [rows[index] for index in indices]
            batch = _collate(batch_rows, device=device, k=k)
            delta_slots = table.forward_indices(indices)
            student = _forward_direct_delta(backend=backend, batch=batch, delta_slots=delta_slots)
            loss, terms = _training_loss(logits=student["target_logits"], batch=batch, objective=objective)
            grad_norm = apply_independent_optimizer_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                selected_indices=indices,
                update_counts=update_counts,
                base_norms=base_norms,
                ratio_budget=ratio_budget,
            )
            interval_reports.append(
                {
                    "objective": float(loss.detach().cpu()),
                    "gradient_norm": grad_norm,
                    "sequence_utility_huber": float(terms["sequence_utility_huber"].detach().cpu()),
                    "target_delta_huber": float(terms["target_delta_huber"].detach().cpu()),
                    "sparse_teacher_kl": float(terms["sparse_teacher_kl"].detach().cpu()),
                }
            )
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                accounting = update_count_summary(pair_ids, update_counts)
                print(
                    f"direct5fa objective={objective.name} ratio={ratio_budget} "
                    f"round={update_round}/{maximum_updates} batch={batch_number} "
                    f"updates={accounting['minimum_updates_per_pair']}-{accounting['maximum_updates_per_pair']} "
                    f"elapsed={(now-started)/3600.0:.2f}h",
                    flush=True,
                )

        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or int(accounting["minimum_updates_per_pair"]) != update_round:
            raise RuntimeError(f"unequal per-pair updates after round {update_round}: {accounting}")
        if update_round not in evaluation_updates:
            continue

        current_snapshot = table.stacked().detach().cpu()
        evaluation = _evaluate_direct_tensor(
            backend=backend,
            rows=rows,
            delta_tensor=current_snapshot.to(device),
            pair_ids=pair_ids,
            device=device,
            k=k,
            batch_size=batch_size,
            huber_delta=objective.huber_delta,
            control=f"direct_delta_u{update_round}",
        )
        checkpoint_entry = {
            "updates_per_pair": update_round,
            "pair_ids": pair_ids,
            "update_accounting": accounting,
            "train_interval": {
                key: mean_std(report[key] for report in interval_reports)
                for key in (
                    "objective",
                    "gradient_norm",
                    "sequence_utility_huber",
                    "target_delta_huber",
                    "sparse_teacher_kl",
                )
            },
            "fraction_at_ratio_boundary": _boundary_fraction(table, base_norms, ratio_budget),
            "movement_from_previous_checkpoint": _movement_summary(current_snapshot, previous_snapshot),
            "evaluation_summary": evaluation["summary"],
            "timestamp_utc": utc_now(),
        }
        history = [item for item in history if int(item["updates_per_pair"]) != update_round]
        history.append(checkpoint_entry)
        history.sort(key=lambda item: int(item["updates_per_pair"]))
        convergence = assess_plateau(history, current_updates=update_round)
        checkpoint_entry["convergence"] = convergence
        checkpoint_path = output_dir / "checkpoints" / f"direct_{objective.name}_ratio{ratio_budget}_u{update_round:03d}.pt"
        save_training_checkpoint(
            checkpoint_path,
            table=table,
            optimizer=optimizer,
            update_counts=update_counts,
            completed_rounds=update_round,
            metadata={
                "component": "direct_delta",
                "objective": objective.name,
                "ratio_budget": float(ratio_budget),
                "k": int(k),
                "pair_ids": pair_ids,
                "source_commit": maybe_git_commit(),
            },
        )
        atomic_write_json(latest_pointer, {"checkpoint": str(checkpoint_path), "updates_per_pair": update_round})
        atomic_write_json(output_dir / "history.json", history)
        write_jsonl(output_dir / f"evaluation_u{update_round:03d}.jsonl", evaluation["rows"])
        print(
            f"checkpoint objective={objective.name} ratio={ratio_budget} updates={update_round} "
            f"spearman={evaluation['summary']['u_text_vs_u_student_spearman']:.6f} "
            f"sequence_huber={evaluation['summary']['sequence_utility_huber']['mean']:.6f} "
            f"plateau={convergence.get('plateau')}",
            flush=True,
        )
        interval_reports = []
        previous_snapshot = current_snapshot
        final_evaluation = evaluation

        if update_round >= minimum_updates and convergence.get("plateau"):
            break

    if final_evaluation is None or checkpoint_path is None:
        raise RuntimeError("direct convergence run produced no evaluation checkpoint")

    final_tensor = table.stacked().detach().cpu()
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 30000 + int(ratio_budget * 1000) + k)
    random_tensor = torch.randn(final_tensor.shape, generator=generator, dtype=torch.float32)
    trained_norms = final_tensor.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_norms = random_tensor.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_tensor.mul_((trained_norms / random_norms).view(-1, 1, 1))
    random_evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=random_tensor.to(device),
        pair_ids=pair_ids,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="matched_norm_random_delta",
    )
    gate = utility_capacity_gate(
        summary=final_evaluation["summary"],
        zero_summary=zero_evaluation["summary"],
        plateau=bool(convergence.get("plateau")),
    )
    portable_final = _portable_evaluation(final_evaluation, output_dir / "final_rows.jsonl")
    portable_random = _portable_evaluation(random_evaluation, output_dir / "random_control_rows.jsonl")
    result = {
        "format": ORACLE_CONVERGENCE_VERSION,
        "status": "completed",
        "component": "direct_delta",
        "objective": objective.name,
        "objective_weights": {
            "target_delta_weight": objective.target_delta_weight,
            "sequence_utility_weight": objective.sequence_utility_weight,
            "sparse_teacher_kl_weight": objective.sparse_teacher_kl_weight,
            "huber_delta": objective.huber_delta,
        },
        "ratio_budget": ratio_budget,
        "k": k,
        "position": "last_user_k",
        "pair_ids": pair_ids,
        "pair_count": len(pair_ids),
        "updates_per_pair": int(min(update_counts)),
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "history": history,
        "convergence": convergence,
        "checkpoint": str(checkpoint_path),
        "final_evaluation": portable_final,
        "zero_control": {
            "summary": zero_evaluation["summary"],
            "rows_path": zero_evaluation.get("rows_path"),
        },
        "matched_norm_random_control": portable_random,
        "utility_capacity_gate": gate,
        "runtime_s": time.perf_counter() - started,
        "source_commit": maybe_git_commit(),
    }
    atomic_write_json(summary_path, result)
    return result


def _evaluate_pair_latent_table(
    *,
    backend: Any,
    injector: nn.Module,
    rows: Sequence[dict[str, Any]],
    latent_tensor: Tensor,
    pair_ids: Sequence[str],
    device: torch.device,
    k: int,
    batch_size: int,
    huber_delta: float,
    control: str,
) -> dict[str, Any]:
    pair_to_index = {str(pair_id): index for index, pair_id in enumerate(pair_ids)}
    output_rows: list[dict[str, Any]] = []
    selected_token_report = None
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch_rows = list(rows[start : start + batch_size])
            batch = _collate(batch_rows, device=device, k=k)
            indices = torch.tensor(
                [pair_to_index[str(row["pair_id"])] for row in batch_rows],
                dtype=torch.long,
                device=device,
            )
            z = latent_tensor.index_select(0, indices).to(device)
            student = _forward_injector(backend=backend, injector=injector, batch=batch, z=z)
            ratio_values = [float(student["delta_ratio"].detach().cpu())] * len(batch_rows)
            output_rows.extend(
                _rows_from_logits(
                    logits=student["target_logits"],
                    labels=batch["labels"],
                    response_rows=batch["response_rows"],
                    target_lengths=batch["target_lengths"],
                    pair_rows=batch_rows,
                    delta_ratios=ratio_values,
                    control=control,
                    huber_delta=huber_delta,
                )
            )
            if selected_token_report is None:
                selected = student["memory_metadata"]["selected_token_indices"][0]
                token_ids = [
                    int(batch["input_ids"][0, index].detach().cpu())
                    for index in selected
                    if int(index) >= 0
                ]
                selected_token_report = {
                    "selected_token_indices": selected,
                    "selected_token_ids": token_ids,
                    "selected_token_text": [backend.tokenizer.decode([token_id]) for token_id in token_ids],
                }
    enriched = enrich_sequence_utility_rows(output_rows, huber_delta=huber_delta)
    return {
        "rows": enriched,
        "summary": summarize_convergence_rows(enriched),
        "selected_token_report": selected_token_report,
    }


def _pair_latent_gate(summary: dict[str, Any], zero_summary: dict[str, Any], *, plateau: bool) -> dict[str, Any]:
    trained_huber = float(summary["sequence_utility_huber"]["mean"])
    zero_huber = float(zero_summary["sequence_utility_huber"]["mean"])
    reduction = 1.0 - trained_huber / max(zero_huber, 1.0e-12)
    checks = {
        "spearman_gte_0_60": float(summary.get("u_text_vs_u_student_spearman") or -1.0) >= 0.60,
        "sign_agreement_gte_0_75": float(summary.get("positive_negative_sign_agreement") or 0.0) >= 0.75,
        "sequence_huber_better_than_zero": reduction > 0.0,
        "documented_plateau": bool(plateau),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "sequence_utility_huber_reduction_vs_zero": reduction,
    }


def _train_pair_z_convergence(
    *,
    backend: Any,
    base_injector: nn.Module,
    rows: list[dict[str, Any]],
    objective: ConvergenceObjective,
    device: torch.device,
    output_dir: Path,
    seed: int,
    k: int,
    batch_size: int,
    lr: float,
    ratio_target: float,
    ratio_penalty: float,
    train_injector: bool,
    progress_interval_s: float,
    resume: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "run_summary.json"
    if summary_path.exists():
        completed = json.loads(summary_path.read_text(encoding="utf-8"))
        if completed.get("status") == "completed":
            print(f"Reusing completed pair-z run {output_dir}", flush=True)
            return completed

    torch.manual_seed(seed)
    pair_ids = [str(row["pair_id"]) for row in rows]
    injector = copy.deepcopy(base_injector).to(device)
    for parameter in injector.parameters():
        parameter.requires_grad_(train_injector)
    table = IndependentPairTensorTable(pair_ids, (128,), init_std=0.0).to(device)
    shared_parameters = [parameter for parameter in injector.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(list(table.parameters()) + shared_parameters, lr=lr, weight_decay=0.0)
    update_counts = [0] * len(rows)
    completed_rounds = 0
    history: list[dict[str, Any]] = []
    latest_pointer = output_dir / "latest_checkpoint.json"
    checkpoint_path: Path | None = None
    if resume and latest_pointer.exists():
        pointer = json.loads(latest_pointer.read_text(encoding="utf-8"))
        checkpoint_path = Path(pointer["checkpoint"])
        restored = load_training_checkpoint(
            checkpoint_path,
            table=table,
            optimizer=optimizer,
            shared_module=injector,
        )
        if bool(restored["metadata"].get("train_injector")) != bool(train_injector):
            raise ValueError("pair-z resume train_injector mismatch")
        if str(restored["metadata"].get("objective")) != objective.name:
            raise ValueError("pair-z resume objective mismatch")
        update_counts = restored["update_counts"]
        completed_rounds = restored["completed_rounds"]
        history_path = output_dir / "history.json"
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []

    evaluation_updates = {32, 48, 64}
    interval_reports: list[dict[str, float]] = []
    previous_snapshot = table.stacked().detach().cpu() if completed_rounds else None
    last_progress = time.perf_counter()
    final_evaluation = None
    convergence = {"assessable": False, "plateau": False}
    if history:
        latest_history = max(history, key=lambda item: int(item["updates_per_pair"]))
        if int(latest_history["updates_per_pair"]) == completed_rounds:
            convergence = dict(latest_history.get("convergence", convergence))
    if completed_rounds >= 64:
        final_evaluation = _evaluate_pair_latent_table(
            backend=backend,
            injector=injector,
            rows=rows,
            latent_tensor=table.stacked().detach(),
            pair_ids=pair_ids,
            device=device,
            k=k,
            batch_size=batch_size,
            huber_delta=objective.huber_delta,
            control="pair_z_u64",
        )
    loop_maximum = completed_rounds if completed_rounds >= 64 else 64
    for update_round in range(completed_rounds + 1, loop_maximum + 1):
        order = list(range(len(rows)))
        random.Random(seed * 1_000_000 + update_round).shuffle(order)
        for batch_number, start in enumerate(range(0, len(order), batch_size), start=1):
            indices = order[start : start + batch_size]
            batch_rows = [rows[index] for index in indices]
            batch = _collate(batch_rows, device=device, k=k)
            z = table.forward_indices(indices)
            student = _forward_injector(backend=backend, injector=injector, batch=batch, z=z)
            primary, terms = _training_loss(logits=student["target_logits"], batch=batch, objective=objective)
            ratio_loss = F.relu(student["delta_ratio"].to(torch.float32) - float(ratio_target)).pow(2)
            loss = primary + float(ratio_penalty) * ratio_loss
            grad_norm = apply_independent_optimizer_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                selected_indices=indices,
                update_counts=update_counts,
                shared_parameters=shared_parameters,
                max_grad_norm=1.0,
            )
            interval_reports.append(
                {
                    "objective": float(loss.detach().cpu()),
                    "gradient_norm": grad_norm,
                    "sequence_utility_huber": float(terms["sequence_utility_huber"].detach().cpu()),
                    "target_delta_huber": float(terms["target_delta_huber"].detach().cpu()),
                    "sparse_teacher_kl": float(terms["sparse_teacher_kl"].detach().cpu()),
                    "delta_ratio": float(student["delta_ratio"].detach().cpu()),
                }
            )
            now = time.perf_counter()
            if now - last_progress >= progress_interval_s:
                last_progress = now
                accounting = update_count_summary(pair_ids, update_counts)
                print(
                    f"pair-z train_injector={train_injector} round={update_round}/64 batch={batch_number} "
                    f"updates={accounting['minimum_updates_per_pair']}-{accounting['maximum_updates_per_pair']} "
                    f"elapsed={(now-started)/3600.0:.2f}h",
                    flush=True,
                )

        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or int(accounting["minimum_updates_per_pair"]) != update_round:
            raise RuntimeError("pair-z updates are not equal after a complete round")
        if update_round not in evaluation_updates:
            continue
        snapshot = table.stacked().detach().cpu()
        evaluation = _evaluate_pair_latent_table(
            backend=backend,
            injector=injector,
            rows=rows,
            latent_tensor=snapshot.to(device),
            pair_ids=pair_ids,
            device=device,
            k=k,
            batch_size=batch_size,
            huber_delta=objective.huber_delta,
            control=f"pair_z_u{update_round}",
        )
        entry = {
            "updates_per_pair": update_round,
            "pair_ids": pair_ids,
            "update_accounting": accounting,
            "train_interval": {
                key: mean_std(report[key] for report in interval_reports)
                for key in (
                    "objective",
                    "gradient_norm",
                    "sequence_utility_huber",
                    "target_delta_huber",
                    "sparse_teacher_kl",
                    "delta_ratio",
                )
            },
            "movement_from_previous_checkpoint": _movement_summary(snapshot, previous_snapshot),
            "evaluation_summary": evaluation["summary"],
            "timestamp_utc": utc_now(),
        }
        history = [item for item in history if int(item["updates_per_pair"]) != update_round]
        history.append(entry)
        history.sort(key=lambda item: int(item["updates_per_pair"]))
        convergence = assess_plateau(history, current_updates=update_round)
        entry["convergence"] = convergence
        checkpoint_path = output_dir / "checkpoints" / f"pair_z_u{update_round:03d}.pt"
        save_training_checkpoint(
            checkpoint_path,
            table=table,
            optimizer=optimizer,
            update_counts=update_counts,
            completed_rounds=update_round,
            metadata={
                "component": "pair_z",
                "objective": objective.name,
                "train_injector": train_injector,
                "k": k,
                "source_commit": maybe_git_commit(),
            },
            shared_module=injector,
        )
        atomic_write_json(latest_pointer, {"checkpoint": str(checkpoint_path), "updates_per_pair": update_round})
        atomic_write_json(output_dir / "history.json", history)
        write_jsonl(output_dir / f"evaluation_u{update_round:03d}.jsonl", evaluation["rows"])
        interval_reports = []
        previous_snapshot = snapshot
        final_evaluation = evaluation

    if final_evaluation is None or checkpoint_path is None:
        raise RuntimeError("pair-z convergence run produced no final evaluation")
    final_tensor = table.stacked().detach().cpu()
    zero_tensor = torch.zeros_like(final_tensor)
    zero_evaluation = _evaluate_pair_latent_table(
        backend=backend,
        injector=injector,
        rows=rows,
        latent_tensor=zero_tensor.to(device),
        pair_ids=pair_ids,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="zero_pair_z",
    )
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed + 77000)
    random_tensor = torch.randn(final_tensor.shape, generator=generator)
    final_norms = final_tensor.norm(dim=1).clamp_min(1.0e-8)
    random_tensor.mul_((final_norms / random_tensor.norm(dim=1).clamp_min(1.0e-8)).view(-1, 1))
    random_evaluation = _evaluate_pair_latent_table(
        backend=backend,
        injector=injector,
        rows=rows,
        latent_tensor=random_tensor.to(device),
        pair_ids=pair_ids,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=objective.huber_delta,
        control="matched_norm_random_pair_z",
    )
    gate = _pair_latent_gate(
        final_evaluation["summary"],
        zero_evaluation["summary"],
        plateau=bool(convergence.get("plateau")),
    )
    result = {
        "format": ORACLE_CONVERGENCE_VERSION,
        "status": "completed",
        "component": "pair_z",
        "train_injector": train_injector,
        "objective": objective.name,
        "pair_ids": pair_ids,
        "pair_count": len(pair_ids),
        "updates_per_pair": int(min(update_counts)),
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "history": history,
        "convergence": convergence,
        "checkpoint": str(checkpoint_path),
        "final_evaluation": _portable_evaluation(final_evaluation, output_dir / "final_rows.jsonl"),
        "zero_control": _portable_evaluation(zero_evaluation, output_dir / "zero_rows.jsonl"),
        "matched_norm_random_control": _portable_evaluation(
            random_evaluation,
            output_dir / "random_rows.jsonl",
        ),
        "pair_latent_gate": gate,
        "runtime_s": time.perf_counter() - started,
        "source_commit": maybe_git_commit(),
    }
    atomic_write_json(summary_path, result)
    return result


def _load_stage5e_injector(*, backend: Any, checkpoint_path: Path, device: torch.device, k: int) -> nn.Module:
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    injector = _capacity_injector(
        program_dim=128,
        model_dim=model_dim,
        k=k,
        seed=1,
        initial_scale=0.1,
    ).to(device)
    injector.load_state_dict(payload["injector_state_dict"])
    return injector


def _evaluate_underoptimized_stage5e(
    *,
    backend: Any,
    rows: list[dict[str, Any]],
    stage5e_dir: Path,
    output_dir: Path,
    device: torch.device,
    k: int,
    batch_size: int,
    huber_delta: float,
) -> dict[str, Any]:
    summary = json.loads((stage5e_dir / "summary.json").read_text(encoding="utf-8"))
    checkpoint_path = Path(summary["direct_delta_best"]["checkpoint"])
    if not checkpoint_path.is_absolute():
        checkpoint_path = Path.cwd() / checkpoint_path
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    pair_ids = [str(pair_id) for pair_id in payload["pair_ids"]]
    current_ids = [str(row["pair_id"]) for row in rows]
    if pair_ids != current_ids:
        raise ValueError("Stage-5E direct checkpoint pair IDs do not match the reproduced 192-pair subset")
    evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=payload["delta_table"].to(device),
        pair_ids=pair_ids,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=huber_delta,
        control="underoptimized_two_update_result",
    )
    result = {
        "interpretation": "underoptimized_two_update_result",
        "supersedes_capacity_interpretation_only": True,
        "original_artifact_preserved": True,
        "original_stage5e_dir": str(stage5e_dir),
        "original_checkpoint": str(checkpoint_path),
        "original_objective": payload["objective"],
        "original_ratio_budget": float(payload["ratio_budget"]),
        "update_accounting": {
            "updates_per_pair": 2,
            "minimum_updates_per_pair": 2,
            "maximum_updates_per_pair": 2,
            "mean_updates_per_pair": 2.0,
            "pair_count": len(pair_ids),
            "basis": "Stage-5E direct_epochs=2, batch_size=1, one visit per pair per epoch",
        },
        "evaluation": _portable_evaluation(evaluation, output_dir / "underoptimized_stage5e_rows.jsonl"),
    }
    atomic_write_json(output_dir / "underoptimized_stage5e.json", result)
    return result


def _best_confirmation(runs: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    def key(item: tuple[str, dict[str, Any]]) -> tuple[float, ...]:
        run = item[1]
        summary = run["final_evaluation"]["summary"]
        return (
            float(bool(run["utility_capacity_gate"]["passed"])),
            float(bool(run["convergence"]["plateau"])),
            float(summary.get("u_text_vs_u_student_spearman") or -999.0),
            float(summary.get("positive_negative_sign_agreement") or -999.0),
            -float(summary["sequence_utility_huber"]["mean"]),
        )

    return max(runs.items(), key=key)


def _decision(confirmation_runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    passing = [name for name, run in confirmation_runs.items() if run["utility_capacity_gate"]["passed"]]
    if passing:
        branch = "input_embedding_channel_capacity_passed_after_convergence"
        previous_superseded = True
        immediate_bottleneck = "underoptimization_and_or_objective_mismatch"
    elif any(not run["convergence"]["plateau"] for run in confirmation_runs.values()):
        branch = "oracle_not_converged_extend_updates"
        previous_superseded = True
        immediate_bottleneck = "convergence_not_reached"
    else:
        branch = "converged_input_embedding_channel_insufficient"
        previous_superseded = False
        immediate_bottleneck = "k4_last_user_input_embedding_channel"
    _, best = _best_confirmation(confirmation_runs)
    best_summary = best["final_evaluation"]["summary"]
    distribution_finding = (
        "utility_reachable_distribution_not_fully_reproduced"
        if branch == "input_embedding_channel_capacity_passed_after_convergence"
        and float(best_summary.get("target_token_delta_correlation_global") or -1.0) < 0.80
        else None
    )
    return {
        "format": "stage_c_direct_delta_convergence_decision_5fa_v1",
        "branch": branch,
        "passing_ratio_runs": passing,
        "previous_direct_failure_interpretation_superseded": previous_superseded,
        "immediate_bottleneck": immediate_bottleneck,
        "secondary_distribution_finding": distribution_finding,
        "new_injection_site_allowed_in_this_milestone": False,
        "stage_c2_allowed": False,
    }


def _student_prompt_contract(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    errors = []
    for row in rows:
        response = row["response_cache"]
        expected_prompt = int(response["prompt_tokens"])
        if int(row["prompt_len"]) != expected_prompt:
            errors.append(
                {
                    "pair_id": row["pair_id"],
                    "prompt_len": row["prompt_len"],
                    "cached_baseline_prompt_tokens": expected_prompt,
                }
            )
        if len(row["input_ids"]) != int(row["prompt_len"]) + int(row["target_len"]):
            errors.append({"pair_id": row["pair_id"], "reason": "input length mismatch"})
    return {
        "passed": not errors,
        "pair_count": len(rows),
        "student_input_definition": "unchanged full-demo baseline prompt plus ground-truth target IDs",
        "raw_memory_text_in_student_prompt": False,
        "selector_payload_accessed": False,
        "errors_first_20": errors[:20],
        "error_count": len(errors),
    }


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# Milestone 5F-A / EXP-016A Convergence-Corrected Direct-Delta Oracle",
        "",
        f"- source commit: `{summary['source_commit']}`",
        f"- runtime seconds: `{summary['runtime_s']:.3f}`",
        f"- selected pilot objective: `{summary['objective_selection']['selected_objective']}`",
        f"- decision branch: `{summary['decision']['branch']}`",
        "",
        "## Stage-5E Superseding Interpretation",
        "",
        "The original direct result is preserved and is now labeled `underoptimized_two_update_result`. "
        "Its objective-mismatch evidence remains valid; its direct-channel capacity conclusion was inconclusive.",
        "",
        "## 64-Pair Pilot",
        "",
    ]
    for name, run in summary["pilot_runs"].items():
        metric = run["final_evaluation"]["summary"]
        lines.extend(
            [
                f"### {name}",
                "",
                f"- updates per pair: `{run['updates_per_pair']}`",
                f"- plateau: `{run['convergence']['plateau']}`",
                f"- Spearman: `{metric['u_text_vs_u_student_spearman']}`",
                f"- sign agreement: `{metric['positive_negative_sign_agreement']}`",
                f"- sequence Huber: `{metric['sequence_utility_huber']['mean']}`",
                f"- target-delta correlation: `{metric['target_token_delta_correlation_global']}`",
                "",
            ]
        )
    lines.extend(["## 192-Pair Confirmation", ""])
    for name, run in summary["confirmation_runs"].items():
        metric = run["final_evaluation"]["summary"]
        lines.extend(
            [
                f"### {name}",
                "",
                f"- updates per pair: `{run['updates_per_pair']}`",
                f"- plateau: `{run['convergence']['plateau']}`",
                f"- gate passed: `{run['utility_capacity_gate']['passed']}`",
                f"- Spearman / Pearson: `{metric['u_text_vs_u_student_spearman']}` / `{metric['u_text_vs_u_student_pearson']}`",
                f"- sign agreement: `{metric['positive_negative_sign_agreement']}`",
                f"- sequence MAE / MSE / Huber: `{metric['sequence_utility_mae']['mean']}` / "
                f"`{metric['sequence_utility_mse']['mean']}` / `{metric['sequence_utility_huber']['mean']}`",
                f"- target NLL: `{metric['target_nll']['mean']}`",
                f"- perturbation ratio mean / max: `{metric['delta_ratio']['mean']}` / `{metric['delta_ratio']['max']}`",
                f"- target-delta correlation: `{metric['target_token_delta_correlation_global']}`",
                f"- sparse teacher KL: `{metric['sparse_teacher_kl']['mean']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Decision",
            "",
            "```json",
            json.dumps(summary["decision"], indent=2, sort_keys=True),
            "```",
        ]
    )
    if summary.get("pair_z_sanity") is not None:
        lines.extend(
            [
                "",
                "## Optional Pair-Z Sanity",
                "",
                "```json",
                json.dumps(summary["pair_z_sanity"], indent=2, sort_keys=True),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pair-cache-dir", type=Path, required=True)
    parser.add_argument("--stage5e-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--pilot-pair-count", type=int, default=64)
    parser.add_argument("--full-pair-count", type=int, default=192)
    parser.add_argument("--pilot-min-updates", type=int, default=64)
    parser.add_argument("--pilot-max-updates", type=int, default=128)
    parser.add_argument("--confirmation-updates", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--direct-lr", type=float, default=0.05)
    parser.add_argument("--pair-z-lr", type=float, default=2.0e-3)
    parser.add_argument("--pair-z-ratio-target", type=float, default=1.0)
    parser.add_argument("--pair-z-ratio-penalty", type=float, default=0.2)
    parser.add_argument("--progress-interval-s", type=float, default=300.0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--skip-pair-z", action="store_true")
    parser.add_argument("--debug-skip-stage5e-reference", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    cfg = load_config(args.config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_resolved_config(cfg, args.output_dir / "resolved_config.yaml")
    backend = build_backend(cfg, load_model=not args.smoke)
    if args.smoke:
        print("Stage-5F-A smoke mode validated config loading; unit tests cover no-model mechanics.", flush=True)
        return
    if args.k != 4:
        raise ValueError("Milestone 5F-A is restricted to K=4")
    if args.batch_size != 1:
        raise ValueError("The formal 5F-A audit uses batch_size=1 for exact per-pair accounting")
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    device = backend.device
    context_limit = _context_limit_for_backend(backend)
    source_commit = maybe_git_commit()

    pair_cache_path = args.pair_cache_dir / "pair_response_cache.jsonl"
    pair_rows = _load_rows(pair_cache_path)
    if any(row.get("format") != PAIR_RESPONSE_CACHE_VERSION for row in pair_rows):
        raise ValueError("Unexpected pair cache format")
    pair_cache_summary = json.loads(
        (args.pair_cache_dir / "pair_response_cache_summary.json").read_text(encoding="utf-8")
    )
    if not pair_cache_summary.get("validation", {}).get("passed"):
        raise ValueError("Stage-5D pair cache validation is not passed")
    identity = validate_target_token_utility_identity(pair_rows)
    if not identity["passed"]:
        raise ValueError(f"target-token utility identity failed: {identity['errors_first_20']}")

    full_raw, full_subset = select_balanced_validation_subset(
        pair_rows,
        target_total=args.full_pair_count,
        seed=20260808 + args.seed,
    )
    full_pair_ids = sorted(str(row["pair_id"]) for row in full_raw)
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    tokenized = _build_tokenized_pair_rows(
        backend=backend,
        examples=examples,
        pair_rows=full_raw,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=context_limit,
    )
    full_rows = _select_by_pair_ids(tokenized, full_pair_ids)
    prompt_contract = _student_prompt_contract(full_rows)
    if not prompt_contract["passed"]:
        raise ValueError(f"student prompt contract failed: {prompt_contract['errors_first_20']}")
    pilot_rows, pilot_subset = select_convergence_subset(
        full_rows,
        target_total=args.pilot_pair_count,
        seed=20260810 + args.seed,
    )

    model_dim = int(getattr(backend.model.config, "hidden_size"))
    pilot_pair_ids = [str(row["pair_id"]) for row in pilot_rows]
    zero_pilot = _evaluate_direct_tensor(
        backend=backend,
        rows=pilot_rows,
        delta_tensor=torch.zeros(len(pilot_rows), args.k, model_dim, device=device),
        pair_ids=pilot_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=0.1,
        control="zero_direct_delta",
    )
    write_jsonl(args.output_dir / "pilot_zero_rows.jsonl", zero_pilot["rows"])
    zero_pilot["rows_path"] = str(args.output_dir / "pilot_zero_rows.jsonl")
    zero_full = _evaluate_direct_tensor(
        backend=backend,
        rows=full_rows,
        delta_tensor=torch.zeros(len(full_rows), args.k, model_dim, device=device),
        pair_ids=full_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=0.1,
        control="zero_direct_delta",
    )
    write_jsonl(args.output_dir / "confirmation_zero_rows.jsonl", zero_full["rows"])
    zero_full["rows_path"] = str(args.output_dir / "confirmation_zero_rows.jsonl")
    zero_equivalence = {
        "max_abs_u_student_pilot": max(abs(float(row["u_student"])) for row in zero_pilot["rows"]),
        "max_abs_u_student_confirmation": max(abs(float(row["u_student"])) for row in zero_full["rows"]),
    }
    zero_equivalence["passed"] = max(zero_equivalence.values()) <= 5.0e-5
    if not zero_equivalence["passed"]:
        raise ValueError(f"zero DeltaE equivalence failed: {zero_equivalence}")

    if args.debug_skip_stage5e_reference:
        underoptimized = {
            "interpretation": "debug_skip_only",
            "original_artifact_preserved": True,
            "formal_result": False,
        }
    else:
        underoptimized = _evaluate_underoptimized_stage5e(
            backend=backend,
            rows=full_rows,
            stage5e_dir=args.stage5e_dir,
            output_dir=args.output_dir,
            device=device,
            k=args.k,
            batch_size=args.batch_size,
            huber_delta=0.1,
        )

    pilot_objectives = (
        "target_delta_huber",
        "sequence_utility_huber",
        "sequence_utility_plus_sparse_kl",
    )
    pilot_runs: dict[str, dict[str, Any]] = {}
    for objective_name in pilot_objectives:
        pilot_runs[objective_name] = _train_direct_convergence(
            backend=backend,
            rows=pilot_rows,
            objective=OBJECTIVES_5FA[objective_name],
            ratio_budget=0.5,
            device=device,
            output_dir=args.output_dir / "pilot" / objective_name,
            seed=args.seed,
            k=args.k,
            batch_size=args.batch_size,
            lr=args.direct_lr,
            minimum_updates=args.pilot_min_updates,
            maximum_updates=args.pilot_max_updates,
            zero_evaluation=zero_pilot,
            progress_interval_s=args.progress_interval_s,
            resume=not args.no_resume,
        )
        atomic_write_json(args.output_dir / "pilot_partial.json", pilot_runs)
    selection = choose_pilot_objective(pilot_runs)
    atomic_write_json(args.output_dir / "objective_selection.json", selection)
    selected_objective = OBJECTIVES_5FA[selection["selected_objective"]]

    confirmation_runs: dict[str, dict[str, Any]] = {}
    for ratio_budget in (0.5, 1.0):
        name = f"ratio_{ratio_budget}"
        confirmation_runs[name] = _train_direct_convergence(
            backend=backend,
            rows=full_rows,
            objective=selected_objective,
            ratio_budget=ratio_budget,
            device=device,
            output_dir=args.output_dir / "confirmation" / name,
            seed=args.seed + 1000,
            k=args.k,
            batch_size=args.batch_size,
            lr=args.direct_lr,
            minimum_updates=args.confirmation_updates,
            maximum_updates=args.confirmation_updates,
            zero_evaluation=zero_full,
            progress_interval_s=args.progress_interval_s,
            resume=not args.no_resume,
        )
        atomic_write_json(args.output_dir / "confirmation_partial.json", confirmation_runs)
    decision = _decision(confirmation_runs)

    pair_z_sanity = None
    if decision["branch"] == "input_embedding_channel_capacity_passed_after_convergence" and not args.skip_pair_z:
        pair_rows_32, pair_subset = select_convergence_subset(
            pilot_rows,
            target_total=32,
            seed=20260820 + args.seed,
        )
        stage5e_summary = json.loads((args.stage5e_dir / "summary.json").read_text(encoding="utf-8"))
        injector_checkpoint = Path(stage5e_summary["pair_z"]["train"]["checkpoint"])
        if not injector_checkpoint.is_absolute():
            injector_checkpoint = Path.cwd() / injector_checkpoint
        base_injector = _load_stage5e_injector(
            backend=backend,
            checkpoint_path=injector_checkpoint,
            device=device,
            k=args.k,
        )
        frozen = _train_pair_z_convergence(
            backend=backend,
            base_injector=base_injector,
            rows=pair_rows_32,
            objective=selected_objective,
            device=device,
            output_dir=args.output_dir / "pair_z" / "frozen_injector",
            seed=args.seed + 2000,
            k=args.k,
            batch_size=args.batch_size,
            lr=args.pair_z_lr,
            ratio_target=args.pair_z_ratio_target,
            ratio_penalty=args.pair_z_ratio_penalty,
            train_injector=False,
            progress_interval_s=args.progress_interval_s,
            resume=not args.no_resume,
        )
        joint = None
        if not frozen["pair_latent_gate"]["passed"]:
            joint = _train_pair_z_convergence(
                backend=backend,
                base_injector=base_injector,
                rows=pair_rows_32,
                objective=selected_objective,
                device=device,
                output_dir=args.output_dir / "pair_z" / "joint_injector",
                seed=args.seed + 3000,
                k=args.k,
                batch_size=args.batch_size,
                lr=args.pair_z_lr,
                ratio_target=args.pair_z_ratio_target,
                ratio_penalty=args.pair_z_ratio_penalty,
                train_injector=True,
                progress_interval_s=args.progress_interval_s,
                resume=not args.no_resume,
            )
        pair_z_sanity = {
            "subset": pair_subset,
            "source_stage5e_injector_checkpoint": str(injector_checkpoint),
            "frozen_injector": frozen,
            "joint_injector": joint,
        }

    best_name, best_confirmation = _best_confirmation(confirmation_runs)
    summary = {
        "format": ORACLE_CONVERGENCE_VERSION,
        "status": "completed",
        "timestamp_utc": utc_now(),
        "source_commit": source_commit,
        "output_dir": str(args.output_dir),
        "data_dir": str(args.data),
        "pair_cache_dir": str(args.pair_cache_dir),
        "pair_cache_sha256": sha256_file(pair_cache_path),
        "pair_cache_validation": pair_cache_summary["validation"],
        "target_token_utility_identity": identity,
        "student_prompt_contract": prompt_contract,
        "zero_delta_equivalence": zero_equivalence,
        "full_subset": full_subset,
        "pilot_subset": pilot_subset,
        "underoptimized_stage5e": underoptimized,
        "pilot_runs": pilot_runs,
        "objective_selection": selection,
        "confirmation_runs": confirmation_runs,
        "best_confirmation_name": best_name,
        "best_confirmation": best_confirmation,
        "decision": decision,
        "pair_z_sanity": pair_z_sanity,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "teacher_forced_target_scoring_only": True,
            "injection_site": "input_embedding",
            "position": "last_user_k",
            "k": 4,
            "new_injection_site_implemented": False,
            "memory_compiler_trained": False,
            "signed_selector_used": False,
            "full_bank_model_trained": False,
            "appworld_generation_evaluation": False,
            "stage_c2_started": False,
        },
    }
    atomic_write_json(args.output_dir / "summary.json", summary)
    atomic_write_text(args.output_dir / "report.md", _report(summary))
    print(
        json.dumps(
            {
                "summary": str(args.output_dir / "summary.json"),
                "selected_objective": selection["selected_objective"],
                "decision": decision,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
