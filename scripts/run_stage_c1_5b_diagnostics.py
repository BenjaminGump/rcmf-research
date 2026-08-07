from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.injection.prefix import AdditiveTokenMemoryInjector
from rcmf.training.addressing_4b import _pearson, distribution, mean_std
from rcmf.training.datasets import load_decision_examples
from rcmf.training.stage_c1 import (
    STAGE_C1_PROGRAM_FIELD_VERSION,
    StageC1ProgramField,
    paired_ci,
    parameter_count,
    program_geometry,
    resolve_include_mask,
    split_rows,
    summarize_state_nll_rows,
    target_nll_by_state_from_logits,
    train_memory_prior,
    z_geometry,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, maybe_git_commit, read_jsonl, sha256_file
from scripts.build_stage_c1_response_cache import validate_response_cache
from scripts.run_stage_c1_signed_program import (
    _build_tokenized_rows,
    _collate,
    _compute_z,
    _evaluate_cache_baseline,
    _forward_student,
    _load_representation_cache,
    _load_rows,
    _response_rows_by_state,
    _selector_payload_for_seed,
    evaluate_student,
    sparse_teacher_kl_from_logits,
)


RUN_VERSION = "stage_c1_5b_memory_specific_diagnostics_v1"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bootstrap_mean_ci(values: Sequence[float], *, seed: int, samples: int = 2000) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "mean": None, "lo": None, "hi": None}
    rng = random.Random(seed)
    means = []
    n = len(clean)
    for _ in range(samples):
        means.append(sum(clean[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * (samples - 1))]
    hi = means[int(0.975 * (samples - 1))]
    return {"count": n, "mean": sum(clean) / n, "lo": lo, "hi": hi}


def _rankdata(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(float(value) for value in values), key=lambda item: item[1])
    ranks = [0.0] * len(indexed)
    pos = 0
    while pos < len(indexed):
        end = pos + 1
        while end < len(indexed) and indexed[end][1] == indexed[pos][1]:
            end += 1
        avg_rank = (pos + 1 + end) / 2.0
        for index in range(pos, end):
            ranks[indexed[index][0]] = avg_rank
        pos = end
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return None
    rx = _rankdata([x for x, _ in pairs])
    ry = _rankdata([y for _, y in pairs])
    return _pearson(rx, ry)


def corr_report(xs: Sequence[float], ys: Sequence[float]) -> dict[str, Any]:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 2:
        return {"count": len(pairs), "pearson": None, "spearman": None}
    return {
        "count": len(pairs),
        "pearson": _pearson([x for x, _ in pairs], [y for _, y in pairs]),
        "spearman": spearman([x for x, _ in pairs], [y for _, y in pairs]),
    }


def descending_ranks(values: Tensor) -> Tensor:
    order = torch.argsort(values, descending=True)
    ranks = torch.empty_like(order)
    ranks[order] = torch.arange(1, values.numel() + 1, device=values.device)
    return ranks


def stage_index_to_memory_id(row: dict[str, Any], index: int) -> str:
    return str(row["ordered_effective_memory_ids"][int(index)])


def valid_utility_items(row: dict[str, Any], *, exclude: set[int] | None = None) -> list[tuple[int, float]]:
    excluded = exclude or set()
    items = []
    for index, (valid, value) in enumerate(zip(row["valid_mask"], row["raw_utility"])):
        if index in excluded or not valid or value is None:
            continue
        items.append((index, float(value)))
    return items


def choose_removals(row: dict[str, Any], *, selector_scores: Tensor, contribution_norms: Tensor, seed: int) -> dict[str, int]:
    best = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
    removals = {"teacher_best": best}
    valid = valid_utility_items(row, exclude={best})
    if valid:
        neutral = min(valid, key=lambda item: (abs(item[1]), item[0]))
        removals["neutral"] = int(neutral[0])
        negative = min(valid, key=lambda item: (item[1], item[0]))
        removals["most_negative"] = int(negative[0])
        used = {best, int(neutral[0]), int(negative[0])}
        pool = [index for index, _ in valid if index not in used] or [index for index, _ in valid]
        rng = random.Random(seed * 1_000_003 + int(row["state_index"]))
        removals["random_valid"] = int(pool[rng.randrange(len(pool))])
    removals["selector_top"] = int(torch.argmax(selector_scores).item())
    removals["largest_contribution"] = int(torch.argmax(contribution_norms).item())
    return removals


def make_remove_mask(row: dict[str, Any], remove_index: int) -> Tensor:
    mask = resolve_include_mask([row], validation_full_bank=True)
    mask[0, int(remove_index)] = False
    return mask


def load_field_and_injector(
    *,
    checkpoint_path: Path,
    memory_dim: int,
    model_dim: int,
    device: torch.device,
) -> tuple[StageC1ProgramField, AdditiveTokenMemoryInjector, dict[str, Any]]:
    checkpoint = torch.load(checkpoint_path, map_location=device)
    kind = str(checkpoint.get("program_kind"))
    seed = int(checkpoint["seed"])
    if kind == "content":
        field = StageC1ProgramField(memory_dim=memory_dim, rank=128, program_dim=128).to(device)
    elif kind == "free_id":
        state = checkpoint["field_state_dict"]
        latent_dim = int(state["program_head.embedding"].shape[1])
        target_parameter_count = latent_dim * (int(state["program_head.embedding"].shape[0]) + 128 + 1)
        field = StageC1ProgramField(
            memory_dim=memory_dim,
            rank=128,
            program_dim=128,
            program_kind="free_id",
            memory_count=int(state["program_head.embedding"].shape[0]),
            matched_parameter_count=target_parameter_count,
        ).to(device)
    else:
        raise ValueError(f"unsupported Stage-C1 program kind {kind} at {checkpoint_path}")
    field.load_state_dict(checkpoint["field_state_dict"])
    injector = AdditiveTokenMemoryInjector(program_dim=128, model_dim=model_dim, num_tokens=4, position="last_user_k", initial_scale=0.0).to(device)
    injector.load_state_dict(checkpoint["injector_state_dict"])
    field.eval()
    injector.eval()
    return field, injector, checkpoint


def evaluate_with_explicit_z(
    *,
    backend: Any,
    injector: AdditiveTokenMemoryInjector,
    rows: Sequence[dict[str, Any]],
    z_values: Tensor,
    device: torch.device,
    batch_size: int,
    delta_multiplier: float = 1.0,
) -> dict[str, Any]:
    out_rows = []
    delta_norms = []
    delta_ratios = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch_rows = list(rows[start : start + batch_size])
            batch = _collate(batch_rows, device=device)
            z = z_values[start : start + len(batch_rows)].to(device)
            student = _forward_student(
                backend=backend,
                injector=injector,
                batch=batch,
                memory_z=z,
                delta_multiplier=delta_multiplier,
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
            delta_norms.append(float(student["delta_norm"].detach().cpu()))
            delta_ratios.append(float(student["delta_ratio"].detach().cpu()))
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    return {
        "rows": out_rows,
        "summary": summarize_state_nll_rows(out_rows),
        "delta_norm": distribution(delta_norms),
        "delta_ratio": distribution(delta_ratios),
    }


def decompose_rows(
    *,
    selector_payload: dict[str, Tensor],
    rows: Sequence[dict[str, Any]],
    programs: Tensor,
    include_mask_override: Tensor | None = None,
    eps: float = 1.0e-6,
    device: torch.device,
) -> dict[str, Tensor]:
    indices = torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long, device=device)
    q_bar = selector_payload["q_bar"].to(device).index_select(0, indices)
    gate = selector_payload["gate"].to(device).index_select(0, indices)
    k_bar = selector_payload["k_bar"].to(device)
    programs = programs.to(device=device, dtype=torch.float32)
    mask = resolve_include_mask(rows, validation_full_bank=True, include_mask_override=include_mask_override).to(device)
    scores = (q_bar @ k_bar.T) * mask.to(torch.float32)
    numerator_terms = scores[:, :, None] * programs[None, :, :]
    numerator = numerator_terms.sum(dim=1)
    denom_sq = scores.pow(2).sum(dim=1, keepdim=True) + eps
    denom = torch.sqrt(denom_sq)
    c = numerator_terms / denom[:, :, None]
    gate_c = gate[:, None, None] * c
    z = gate[:, None] * numerator / denom
    removed_num = numerator[:, None, :] - numerator_terms
    removed_denom = torch.sqrt((denom_sq - scores.pow(2)).clamp_min(eps))
    z_without = gate[:, None, None] * removed_num / removed_denom[:, :, None]
    delta_z = z_without - z[:, None, :]
    return {
        "q_bar": q_bar,
        "gate": gate,
        "k_bar": k_bar,
        "scores": scores,
        "programs": programs,
        "numerator": numerator,
        "denominator": denom.squeeze(-1),
        "c": c,
        "gate_c": gate_c,
        "z": z,
        "z_without": z_without,
        "delta_z": delta_z,
        "include_mask": mask,
    }


def removal_effect_summary(rows: list[dict[str, Any]], *, seed: int) -> dict[str, Any]:
    by_name: dict[str, list[float]] = {}
    teacher_vs_control: dict[str, list[float]] = {}
    for row in rows:
        removals = row.get("removals", {})
        teacher = removals.get("teacher_best", {}).get("delta_vs_full")
        for name, payload in removals.items():
            by_name.setdefault(name, []).append(float(payload["delta_vs_full"]))
            if name != "teacher_best" and teacher is not None:
                teacher_vs_control.setdefault(f"teacher_minus_{name}", []).append(float(teacher) - float(payload["delta_vs_full"]))
    return {
        "effects": {
            name: {"distribution": distribution(values), "mean_ci": bootstrap_mean_ci(values, seed=seed + sum(ord(ch) for ch in name))}
            for name, values in sorted(by_name.items())
        },
        "teacher_vs_controls": {
            name: {"distribution": distribution(values), "mean_ci": bootstrap_mean_ci(values, seed=seed + sum(ord(ch) for ch in name))}
            for name, values in sorted(teacher_vs_control.items())
        },
    }


def condition_summary(rows: Sequence[dict[str, Any]], *, condition: str | None = None) -> dict[str, Any]:
    selected = [row for row in rows if condition is None or row["teacher_condition"] == condition]
    return summarize_state_nll_rows(selected)


def no_positive_degradation(rows: Sequence[dict[str, Any]]) -> float | None:
    values = [float(row["student_target_nll"]) - float(row["L0"]) for row in rows if row["teacher_condition"] == "baseline_teacher"]
    return sum(values) / len(values) if values else None


def rows_by_state(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["state_example_id"]): row for row in rows}


def paired_content_free_id(summary: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"format": "stage_c1_5b_content_free_id_paired_v1", "seeds": []}
    aggregate: dict[str, list[float]] = {
        "all_target_nll": [],
        "all_sparse_teacher_kl": [],
        "positive_target_nll": [],
        "positive_sparse_teacher_kl": [],
        "baseline_target_nll": [],
        "baseline_sparse_teacher_kl": [],
    }
    for run in summary["runs"]:
        content_rows = rows_by_state(run["validation"]["correct"]["rows"])
        free_rows = rows_by_state(run["validation"]["controls"]["free_id_program"]["rows"])
        seed_payload: dict[str, Any] = {"seed": run["seed"], "diffs": {}}
        buckets = {
            "all": [state_id for state_id in content_rows if state_id in free_rows],
            "positive": [state_id for state_id, row in content_rows.items() if row["teacher_condition"] == "positive_teacher" and state_id in free_rows],
            "baseline": [state_id for state_id, row in content_rows.items() if row["teacher_condition"] == "baseline_teacher" and state_id in free_rows],
        }
        for bucket, ids in buckets.items():
            for metric in ("student_target_nll", "sparse_teacher_kl"):
                values = [float(content_rows[state_id][metric]) - float(free_rows[state_id][metric]) for state_id in ids]
                key = f"{bucket}_{'target_nll' if metric == 'student_target_nll' else 'sparse_teacher_kl'}"
                seed_payload["diffs"][key] = {
                    "distribution": distribution(values),
                    "mean_ci": bootstrap_mean_ci(values, seed=int(run["seed"]) * 100 + len(key)),
                }
                aggregate.setdefault(key, []).extend(values)
        result["seeds"].append(seed_payload)
    result["aggregate"] = {
        key: {
            "distribution": distribution(values),
            "mean_ci": bootstrap_mean_ci(values, seed=20260807 + index),
        }
        for index, (key, values) in enumerate(sorted(aggregate.items()))
    }
    atomic_write_json(output_path, result)
    return result


def evaluate_removal(
    *,
    backend: Any,
    field: StageC1ProgramField,
    injector: AdditiveTokenMemoryInjector,
    selector_payload: dict[str, Tensor],
    row: dict[str, Any],
    memory_representations: Tensor,
    programs: Tensor,
    remove_index: int,
    full_nll: float,
    device: torch.device,
    seed: int,
    delta_multiplier: float = 1.0,
) -> dict[str, Any]:
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
        include_mask_override=make_remove_mask(row, remove_index),
        delta_multiplier=delta_multiplier,
    )
    nll = float(payload["rows"][0]["student_target_nll"])
    return {
        "removed_stage_index": int(remove_index),
        "removed_memory_id": stage_index_to_memory_id(row, remove_index),
        "target_nll": nll,
        "delta_vs_full": nll - float(full_nll),
    }


def run_corrected_leave_one_out(
    *,
    backend: Any,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    output_dir: Path,
) -> dict[str, Any]:
    positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    all_per_state = []
    seed_summaries = []
    for seed in seeds:
        print(f"corrected LOO seed={seed} states={len(positive_rows)}", flush=True)
        _, selector_payload, selector_path = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        field, injector, checkpoint = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
        full_eval = evaluate_student(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=positive_rows,
            memory_representations=memory_reps,
            device=device,
            seed=int(seed),
            batch_size=1,
            trained_programs=programs,
        )
        full_by_id = rows_by_state(full_eval["rows"])
        decomp = decompose_rows(
            selector_payload=selector_payload,
            rows=positive_rows,
            programs=programs,
            device=device,
        )
        for row_index, row in enumerate(positive_rows):
            scores = decomp["scores"][row_index].detach().cpu()
            contribution_norms = decomp["gate_c"][row_index].norm(dim=1).detach().cpu()
            removals = choose_removals(row, selector_scores=scores, contribution_norms=contribution_norms, seed=int(seed))
            full_nll = float(full_by_id[row["state_example_id"]]["student_target_nll"])
            record: dict[str, Any] = {
                "seed": int(seed),
                "state_example_id": row["state_example_id"],
                "state_index": int(row["state_index"]),
                "task_id": row["task_id"],
                "teacher_best_memory_id": row["response_cache"]["best_memory_id"],
                "teacher_utility": float(row["response_cache"]["teacher_utility"]),
                "full_nll": full_nll,
                "selector_checkpoint": selector_path,
                "content_checkpoint": str(stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt"),
                "removals": {},
            }
            for name, remove_index in removals.items():
                record["removals"][name] = evaluate_removal(
                    backend=backend,
                    field=field,
                    injector=injector,
                    selector_payload=selector_payload,
                    row=row,
                    memory_representations=memory_reps,
                    programs=programs,
                    remove_index=int(remove_index),
                    full_nll=full_nll,
                    device=device,
                    seed=int(seed),
                )
            all_per_state.append(record)
        seed_rows = [row for row in all_per_state if row["seed"] == int(seed)]
        seed_summaries.append(
            {
                "seed": int(seed),
                "checkpoint_source_commit": checkpoint.get("source_commit"),
                "positive_state_count": len(positive_rows),
                **removal_effect_summary(seed_rows, seed=int(seed) * 1000),
            }
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    summary = {
        "format": "stage_c1_5b_corrected_leave_one_out_v1",
        "positive_state_count_per_seed": len(positive_rows),
        "state_seed_rows": len(all_per_state),
        "seeds": seed_summaries,
        "aggregate": removal_effect_summary(all_per_state, seed=20260807),
        "per_state_path": str(output_dir / "corrected_leave_one_out_rows.jsonl"),
    }
    write_jsonl(output_dir / "corrected_leave_one_out_rows.jsonl", all_per_state)
    atomic_write_json(output_dir / "corrected_leave_one_out.json", summary)
    return summary


def selector_alignment_and_contributions(
    *,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    alignment_rows = []
    contribution_rows = []
    teacher_best_contrib = []
    all_utility: list[float] = []
    all_scores: list[float] = []
    all_delta_norms: list[float] = []
    all_contrib_norms: list[float] = []
    for seed in seeds:
        print(f"selector/contribution analysis seed={seed}", flush=True)
        _, selector_payload, selector_path = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        field, _, _ = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
        decomp = decompose_rows(selector_payload=selector_payload, rows=rows, programs=programs, device=device)
        positive_decomp = decompose_rows(selector_payload=selector_payload, rows=positive_rows, programs=programs, device=device)
        for row_index, row in enumerate(positive_rows):
            scores = positive_decomp["scores"][row_index].detach().cpu()
            score_ranks = descending_ranks(scores)
            best = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
            rank = int(score_ranks[best].item())
            alignment_rows.append(
                {
                    "seed": int(seed),
                    "state_example_id": row["state_example_id"],
                    "state_index": int(row["state_index"]),
                    "task_id": row["task_id"],
                    "teacher_best_memory_id": row["response_cache"]["best_memory_id"],
                    "teacher_best_stage_index": best,
                    "teacher_utility": float(row["response_cache"]["teacher_utility"]),
                    "signed_score": float(scores[best].item()),
                    "signed_score_rank": rank,
                    "signed_score_percentile": 1.0 - ((rank - 1) / max(1, scores.numel() - 1)),
                    "signed_score_is_negative": bool(float(scores[best].item()) < 0.0),
                    "gate": float(positive_decomp["gate"][row_index].detach().cpu()),
                    "selector_checkpoint": selector_path,
                }
            )
        for row_index, row in enumerate(rows):
            scores = decomp["scores"][row_index].detach().cpu()
            score_ranks = descending_ranks(scores)
            c_norms = decomp["c"][row_index].norm(dim=1).detach().cpu()
            gate_c_norms = decomp["gate_c"][row_index].norm(dim=1).detach().cpu()
            delta_norms = decomp["delta_z"][row_index].norm(dim=1).detach().cpu()
            contrib_ranks = descending_ranks(gate_c_norms)
            numerator_norm = float(decomp["numerator"][row_index].detach().cpu().norm().item())
            sum_contrib_norm = float(c_norms.sum().item())
            best_index = None
            if row["response_cache"]["teacher_condition"] == "positive_teacher":
                best_index = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
            for mem_index, memory_id in enumerate(row["ordered_effective_memory_ids"]):
                utility = row["raw_utility"][mem_index] if row["valid_mask"][mem_index] else None
                item = {
                    "seed": int(seed),
                    "state_example_id": row["state_example_id"],
                    "state_index": int(row["state_index"]),
                    "split": row["split"],
                    "teacher_condition": row["response_cache"]["teacher_condition"],
                    "memory_stage_index": mem_index,
                    "memory_id": memory_id,
                    "teacher_utility": float(utility) if utility is not None else None,
                    "signed_score": float(scores[mem_index].item()),
                    "signed_score_rank": int(score_ranks[mem_index].item()),
                    "contribution_norm": float(c_norms[mem_index].item()),
                    "gate_contribution_norm": float(gate_c_norms[mem_index].item()),
                    "contribution_rank": int(contrib_ranks[mem_index].item()),
                    "delta_z_norm": float(delta_norms[mem_index].item()),
                    "is_teacher_best": bool(best_index is not None and mem_index == best_index),
                    "fraction_of_sum_contribution_norm": float(c_norms[mem_index].item()) / max(1.0e-12, sum_contrib_norm),
                    "fraction_of_numerator_norm": float(c_norms[mem_index].item()) / max(1.0e-12, numerator_norm),
                }
                contribution_rows.append(item)
                if utility is not None:
                    all_utility.append(float(utility))
                    all_scores.append(float(scores[mem_index].item()))
                    all_delta_norms.append(float(delta_norms[mem_index].item()))
                    all_contrib_norms.append(float(gate_c_norms[mem_index].item()))
                if item["is_teacher_best"]:
                    teacher_best_contrib.append(item)
    write_jsonl(output_dir / "contribution_rows.jsonl", contribution_rows)
    write_jsonl(output_dir / "selector_alignment_rows.jsonl", alignment_rows)
    ranks = [row["signed_score_rank"] for row in alignment_rows]
    align = {
        "format": "stage_c1_5b_selector_teacher_alignment_v1",
        "rows": len(alignment_rows),
        "positive_state_count_per_seed": len(positive_rows),
        "teacher_best_recall_at_1": sum(rank <= 1 for rank in ranks) / len(ranks),
        "teacher_best_recall_at_4": sum(rank <= 4 for rank in ranks) / len(ranks),
        "teacher_best_recall_at_8": sum(rank <= 8 for rank in ranks) / len(ranks),
        "teacher_best_rank_distribution": distribution(ranks),
        "teacher_best_percentile_distribution": distribution(row["signed_score_percentile"] for row in alignment_rows),
        "teacher_best_negative_signed_score_fraction": sum(row["signed_score_is_negative"] for row in alignment_rows) / len(alignment_rows),
        "teacher_utility_vs_signed_score": corr_report([row["teacher_utility"] for row in alignment_rows], [row["signed_score"] for row in alignment_rows]),
        "gate_distribution": distribution(row["gate"] for row in alignment_rows),
        "rows_path": str(output_dir / "selector_alignment_rows.jsonl"),
    }
    contrib = {
        "format": "stage_c1_5b_contribution_decomposition_v1",
        "rows": len(contribution_rows),
        "rows_path": str(output_dir / "contribution_rows.jsonl"),
        "teacher_best": {
            "count": len(teacher_best_contrib),
            "fraction_of_sum_contribution_norm": distribution(row["fraction_of_sum_contribution_norm"] for row in teacher_best_contrib),
            "fraction_of_numerator_norm": distribution(row["fraction_of_numerator_norm"] for row in teacher_best_contrib),
            "contribution_rank": distribution(row["contribution_rank"] for row in teacher_best_contrib),
            "signed_score_rank": distribution(row["signed_score_rank"] for row in teacher_best_contrib),
        },
        "correlations_valid_teacher_rows": {
            "teacher_utility_vs_delta_z_norm": corr_report(all_utility, all_delta_norms),
            "teacher_utility_vs_gate_contribution_norm": corr_report(all_utility, all_contrib_norms),
            "signed_score_vs_delta_z_norm": corr_report(all_scores, all_delta_norms),
            "gate_contribution_norm_vs_delta_z_norm": corr_report(all_contrib_norms, all_delta_norms),
        },
        "delta_z_norm_distribution": distribution(row["delta_z_norm"] for row in contribution_rows),
    }
    atomic_write_json(output_dir / "selector_alignment.json", align)
    atomic_write_json(output_dir / "contribution_analysis.json", contrib)
    return align, contrib


def run_compiled_all_memory_subset(
    *,
    backend: Any,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    output_dir: Path,
    subset_size: int,
) -> dict[str, Any]:
    subset = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"][:subset_size]
    result_rows = []
    for seed in seeds:
        print(f"all-memory compiled LOO subset seed={seed} states={len(subset)}", flush=True)
        _, selector_payload, _ = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        field, injector, _ = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
        full_eval = evaluate_student(
            backend=backend,
            field=field,
            injector=injector,
            selector_payload=selector_payload,
            rows=subset,
            memory_representations=memory_reps,
            device=device,
            seed=int(seed),
            batch_size=1,
            trained_programs=programs,
        )
        full_by_id = rows_by_state(full_eval["rows"])
        decomp = decompose_rows(selector_payload=selector_payload, rows=subset, programs=programs, device=device)
        for row_index, row in enumerate(subset):
            full_nll = float(full_by_id[row["state_example_id"]]["student_target_nll"])
            for memory_index, memory_id in enumerate(row["ordered_effective_memory_ids"]):
                removal = evaluate_removal(
                    backend=backend,
                    field=field,
                    injector=injector,
                    selector_payload=selector_payload,
                    row=row,
                    memory_representations=memory_reps,
                    programs=programs,
                    remove_index=memory_index,
                    full_nll=full_nll,
                    device=device,
                    seed=int(seed),
                )
                utility = row["raw_utility"][memory_index] if row["valid_mask"][memory_index] else None
                result_rows.append(
                    {
                        "seed": int(seed),
                        "state_example_id": row["state_example_id"],
                        "state_index": int(row["state_index"]),
                        "memory_stage_index": memory_index,
                        "memory_id": memory_id,
                        "full_nll": full_nll,
                        "nll_without": removal["target_nll"],
                        "compiled_effect": removal["delta_vs_full"],
                        "raw_teacher_utility": float(utility) if utility is not None else None,
                        "signed_score": float(decomp["scores"][row_index, memory_index].detach().cpu()),
                        "analytic_delta_z_norm": float(decomp["delta_z"][row_index, memory_index].detach().cpu().norm()),
                    }
                )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    write_jsonl(output_dir / "compiled_all_memory_loo_subset.jsonl", result_rows)
    valid = [row for row in result_rows if row["raw_teacher_utility"] is not None]
    summary = {
        "format": "stage_c1_5b_compiled_all_memory_loo_subset_v1",
        "subset_state_count": len(subset),
        "state_seed_memory_rows": len(result_rows),
        "rows_path": str(output_dir / "compiled_all_memory_loo_subset.jsonl"),
        "compiled_effect_distribution": distribution(row["compiled_effect"] for row in result_rows),
        "correlations_valid_teacher_rows": {
            "compiled_effect_vs_raw_teacher_utility": corr_report(
                [row["compiled_effect"] for row in valid],
                [row["raw_teacher_utility"] for row in valid],
            ),
            "compiled_effect_vs_signed_score": corr_report(
                [row["compiled_effect"] for row in result_rows],
                [row["signed_score"] for row in result_rows],
            ),
            "compiled_effect_vs_analytic_delta_z_norm": corr_report(
                [row["compiled_effect"] for row in result_rows],
                [row["analytic_delta_z_norm"] for row in result_rows],
            ),
        },
        "topk_overlap": topk_overlap_summary(result_rows),
    }
    atomic_write_json(output_dir / "compiled_all_memory_loo_subset_summary.json", summary)
    return summary


def topk_overlap_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["seed"]), str(row["state_example_id"])), []).append(row)
    output: dict[str, Any] = {}
    comparators = {
        "raw_teacher_utility": lambda row: row["raw_teacher_utility"],
        "signed_score": lambda row: row["signed_score"],
        "analytic_delta_z_norm": lambda row: row["analytic_delta_z_norm"],
    }
    for name, getter in comparators.items():
        for k in (1, 4, 8):
            vals = []
            for group in grouped.values():
                effect_top = {row["memory_stage_index"] for row in sorted(group, key=lambda r: r["compiled_effect"], reverse=True)[:k]}
                eligible = [row for row in group if getter(row) is not None]
                if not eligible:
                    continue
                compare_top = {row["memory_stage_index"] for row in sorted(eligible, key=getter, reverse=True)[:k]}
                vals.append(len(effect_top & compare_top) / max(1, k))
            output[f"effect_top{k}_overlap_{name}_top{k}"] = mean_std(vals)
    return output


def run_free_id_comparison(
    *,
    backend: Any,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    stage_c1_summary: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    paired = paired_content_free_id(stage_c1_summary, output_path=output_dir / "content_free_id_paired.json")
    positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    geometry = []
    loo_rows = []
    for seed in seeds:
        print(f"free-ID comparison seed={seed}", flush=True)
        _, selector_payload, _ = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        for kind in ("content", "free_id"):
            ckpt_name = "content" if kind == "content" else "free_id"
            field, injector, _ = load_field_and_injector(
                checkpoint_path=stage_c1_dir / "checkpoints" / f"{ckpt_name}_seed_{seed}.pt",
                memory_dim=int(memory_reps.shape[1]),
                model_dim=model_dim,
                device=device,
            )
            with torch.no_grad():
                programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
            decomp = decompose_rows(selector_payload=selector_payload, rows=positive_rows, programs=programs, device=device)
            utility = []
            delta_norm = []
            concentration = []
            for row_index, row in enumerate(positive_rows):
                valid_values = valid_utility_items(row)
                for memory_index, value in valid_values:
                    utility.append(value)
                    delta_norm.append(float(decomp["delta_z"][row_index, memory_index].detach().cpu().norm()))
                norms = decomp["gate_c"][row_index].norm(dim=1).detach().cpu()
                concentration.append(float(norms.max().item()) / max(1.0e-12, float(norms.sum().item())))
            geometry.append(
                {
                    "seed": int(seed),
                    "program_kind": kind,
                    "program_geometry": program_geometry(programs.detach().cpu()),
                    "contribution_concentration": distribution(concentration),
                    "teacher_utility_vs_delta_z_norm": corr_report(utility, delta_norm),
                }
            )
            full_eval = evaluate_student(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                rows=positive_rows,
                memory_representations=memory_reps,
                device=device,
                seed=int(seed),
                batch_size=1,
                trained_programs=programs,
            )
            full_by_id = rows_by_state(full_eval["rows"])
            for row in positive_rows:
                best = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
                removal = evaluate_removal(
                    backend=backend,
                    field=field,
                    injector=injector,
                    selector_payload=selector_payload,
                    row=row,
                    memory_representations=memory_reps,
                    programs=programs,
                    remove_index=best,
                    full_nll=float(full_by_id[row["state_example_id"]]["student_target_nll"]),
                    device=device,
                    seed=int(seed),
                )
                loo_rows.append({"seed": int(seed), "program_kind": kind, "state_example_id": row["state_example_id"], **removal})
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    write_jsonl(output_dir / "content_free_id_teacher_best_loo_rows.jsonl", loo_rows)
    result = {
        "format": "stage_c1_5b_free_id_comparison_v1",
        "paired_statistics": paired,
        "geometry": geometry,
        "teacher_best_leave_one_out": {
            kind: removal_effect_summary(
                [
                    {
                        "removals": {"teacher_best": row},
                    }
                    for row in loo_rows
                    if row["program_kind"] == kind
                ],
                seed=20260807,
            )["effects"]["teacher_best"]
            for kind in ("content", "free_id")
        },
        "loo_rows_path": str(output_dir / "content_free_id_teacher_best_loo_rows.jsonl"),
    }
    atomic_write_json(output_dir / "free_id_comparison.json", result)
    return result


def run_injector_scale_sweep(
    *,
    backend: Any,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    scales: Sequence[float],
    output_dir: Path,
) -> dict[str, Any]:
    positive_rows = [row for row in rows if row["response_cache"]["teacher_condition"] == "positive_teacher"]
    scale_rows = []
    for seed in seeds:
        print(f"injector scale sweep seed={seed}", flush=True)
        _, selector_payload, _ = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        field, injector, _ = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
        for scale in scales:
            print(f"  scale={scale}", flush=True)
            full_eval = evaluate_student(
                backend=backend,
                field=field,
                injector=injector,
                selector_payload=selector_payload,
                rows=rows,
                memory_representations=memory_reps,
                device=device,
                seed=int(seed),
                batch_size=1,
                trained_programs=programs,
                delta_multiplier=float(scale),
            )
            full_by_id = rows_by_state(full_eval["rows"])
            teacher_effects = []
            for row in positive_rows:
                best = int(row["memory_id_to_stage_index"][row["response_cache"]["best_memory_id"]])
                removal = evaluate_removal(
                    backend=backend,
                    field=field,
                    injector=injector,
                    selector_payload=selector_payload,
                    row=row,
                    memory_representations=memory_reps,
                    programs=programs,
                    remove_index=best,
                    full_nll=float(full_by_id[row["state_example_id"]]["student_target_nll"]),
                    device=device,
                    seed=int(seed),
                    delta_multiplier=float(scale),
                )
                teacher_effects.append(float(removal["delta_vs_full"]))
            scale_rows.append(
                {
                    "seed": int(seed),
                    "scale": float(scale),
                    "all_state_summary": full_eval["summary"],
                    "positive_teacher_summary": condition_summary(full_eval["rows"], condition="positive_teacher"),
                    "baseline_teacher_summary": condition_summary(full_eval["rows"], condition="baseline_teacher"),
                    "no_positive_degradation_vs_bare_qwen": no_positive_degradation(full_eval["rows"]),
                    "sparse_teacher_kl": full_eval["summary"]["sparse_teacher_kl"],
                    "improved_fraction": full_eval["summary"]["improved_fraction"],
                    "delta_norm": full_eval["delta_norm"],
                    "delta_ratio": full_eval["delta_ratio"],
                    "teacher_best_loo_effect": {
                        "distribution": distribution(teacher_effects),
                        "mean_ci": bootstrap_mean_ci(teacher_effects, seed=int(seed) * 1000 + int(float(scale) * 1000)),
                    },
                }
            )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    grouped: dict[float, list[dict[str, Any]]] = {}
    for row in scale_rows:
        grouped.setdefault(float(row["scale"]), []).append(row)
    summary = {
        "format": "stage_c1_5b_injector_scale_sweep_v1",
        "scale_seed_rows": scale_rows,
        "by_scale": {
            str(scale): {
                "target_nll": mean_std(item["all_state_summary"]["target_nll"]["mean"] for item in items),
                "positive_target_nll": mean_std(item["positive_teacher_summary"]["target_nll"]["mean"] for item in items),
                "baseline_target_nll": mean_std(item["baseline_teacher_summary"]["target_nll"]["mean"] for item in items),
                "no_positive_degradation_vs_bare_qwen": mean_std(item["no_positive_degradation_vs_bare_qwen"] for item in items),
                "sparse_teacher_kl": mean_std(item["sparse_teacher_kl"]["mean"] for item in items),
                "improved_fraction": mean_std(item["improved_fraction"] for item in items),
                "delta_norm": mean_std(item["delta_norm"]["mean"] for item in items),
                "delta_ratio": mean_std(item["delta_ratio"]["mean"] for item in items),
                "teacher_best_loo_effect": mean_std(item["teacher_best_loo_effect"]["distribution"]["mean"] for item in items),
            }
            for scale, items in sorted(grouped.items())
        },
    }
    atomic_write_json(output_dir / "injector_scale_sweep.json", summary)
    return summary


def z_variants(
    *,
    selector_payload: dict[str, Tensor],
    rows: list[dict[str, Any]],
    programs: Tensor,
    train_rows: list[dict[str, Any]],
    device: torch.device,
) -> tuple[dict[str, Tensor], dict[str, Any]]:
    current = decompose_rows(selector_payload=selector_payload, rows=rows, programs=programs, device=device)
    train = decompose_rows(selector_payload=selector_payload, rows=train_rows, programs=programs, device=device)
    train_constant = float(train["denominator"].detach().cpu().mean().item())
    scores = current["scores"]
    gate = current["gate"]
    terms = scores[:, :, None] * programs.to(device)[None, :, :]
    numerator = terms.sum(dim=1)
    fixed = gate[:, None] * numerator / max(1.0e-6, train_constant)
    unnormalized = gate[:, None] * numerator
    scale = float(current["z"].norm(dim=1).mean().detach().cpu()) / max(1.0e-12, float(unnormalized.norm(dim=1).mean().detach().cpu()))
    unnormalized_scaled = unnormalized * scale
    gate_terms = current["gate_c"]
    top_indices = gate_terms.norm(dim=2).argmax(dim=1)
    teacher_indices = torch.tensor(
        [int(row["memory_id_to_stage_index"].get(row["response_cache"].get("best_memory_id"), 0)) for row in rows],
        dtype=torch.long,
        device=device,
    )
    top_z = []
    teacher_z = []
    for row_index in range(len(rows)):
        for output, index in ((top_z, top_indices[row_index]), (teacher_z, teacher_indices[row_index])):
            mask = torch.zeros(scores.shape[1], dtype=torch.bool, device=device)
            mask[int(index)] = True
            single = decompose_rows(
                selector_payload=selector_payload,
                rows=[rows[row_index]],
                programs=programs,
                include_mask_override=mask.view(1, -1).detach().cpu(),
                device=device,
            )["z"][0]
            output.append(single)
    variants = {
        "current_normalized": current["z"].detach().cpu(),
        "fixed_denominator": fixed.detach().cpu(),
        "unnormalized_matched_scale": unnormalized_scaled.detach().cpu(),
        "top_abs_contribution_only": torch.stack(top_z).detach().cpu(),
        "raw_teacher_best_only": torch.stack(teacher_z).detach().cpu(),
    }
    meta = {"train_denominator_mean": train_constant, "unnormalized_external_scale": scale}
    return variants, meta


def run_aggregate_read_diagnosis(
    *,
    backend: Any,
    stage_c1_dir: Path,
    selector_dir: Path,
    state_reps: Tensor,
    memory_reps: Tensor,
    mu: Tensor,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    model_dim: int,
    device: torch.device,
    seeds: Sequence[int],
    output_dir: Path,
    subset_size: int,
) -> dict[str, Any]:
    positive_subset = [row for row in validation_rows if row["response_cache"]["teacher_condition"] == "positive_teacher"][:subset_size]
    seed_payloads = []
    for seed in seeds:
        print(f"aggregate-read diagnosis seed={seed}", flush=True)
        _, selector_payload, _ = _selector_payload_for_seed(
            selector_dir=selector_dir,
            seed=int(seed),
            state_reps=state_reps,
            memory_reps=memory_reps.detach().cpu(),
            mu=mu,
            device=device,
        )
        field, injector, _ = load_field_and_injector(
            checkpoint_path=stage_c1_dir / "checkpoints" / f"content_seed_{seed}.pt",
            memory_dim=int(memory_reps.shape[1]),
            model_dim=model_dim,
            device=device,
        )
        with torch.no_grad():
            programs = field.programs(memory_reps.to(device=device, dtype=torch.float32)).detach()
        variants, meta = z_variants(
            selector_payload=selector_payload,
            rows=positive_subset,
            programs=programs,
            train_rows=train_rows,
            device=device,
        )
        evals = {}
        for name, z in variants.items():
            evals[name] = evaluate_with_explicit_z(
                backend=backend,
                injector=injector,
                rows=positive_subset,
                z_values=z,
                device=device,
                batch_size=1,
            )
        seed_payloads.append(
            {
                "seed": int(seed),
                "meta": meta,
                "z_geometry": {name: z_geometry(z) for name, z in variants.items()},
                "teacher_forced_subset": {name: payload["summary"] for name, payload in evals.items()},
            }
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    result = {
        "format": "stage_c1_5b_aggregate_read_diagnosis_v1",
        "subset_state_count": len(positive_subset),
        "seeds": seed_payloads,
    }
    atomic_write_json(output_dir / "aggregate_read_diagnosis.json", result)
    return result


def write_report(summary: dict[str, Any]) -> str:
    loo = summary["corrected_leave_one_out"]["aggregate"]["effects"]
    align = summary["selector_alignment"]
    contrib = summary["contribution_analysis"]
    scale = summary["injector_scale_sweep"]["by_scale"]
    free = summary["free_id_comparison"]["paired_statistics"]["aggregate"]
    lines = [
        "# Milestone 5B Stage-C1 Memory-Specific Causality Diagnostics",
        "",
        f"- format: `{summary['format']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- artifact: `{summary['output_dir']}`",
        f"- hard scope: existing Stage-C1 checkpoints only; no retraining, no Stage C2, no AppWorld generation/evaluation.",
        f"- old Stage-C1 leave-one-out metric superseded: `{summary['old_leave_one_out_invalidated']}`",
        "",
        "## Corrected Leave-One-Out",
        "",
    ]
    for name in ("teacher_best", "neutral", "most_negative", "random_valid", "selector_top", "largest_contribution"):
        if name in loo:
            lines.append(f"- {name}: `{loo[name]['distribution']}`; mean CI `{loo[name]['mean_ci']}`")
    lines.extend(
        [
            "",
            "## Selector Alignment",
            "",
            f"- teacher-best recall@1/4/8: `{align['teacher_best_recall_at_1']}`, `{align['teacher_best_recall_at_4']}`, `{align['teacher_best_recall_at_8']}`",
            f"- teacher-best rank distribution: `{align['teacher_best_rank_distribution']}`",
            f"- negative signed-score fraction: `{align['teacher_best_negative_signed_score_fraction']}`",
            f"- utility vs signed score: `{align['teacher_utility_vs_signed_score']}`",
            "",
            "## Contribution Decomposition",
            "",
            f"- teacher-best contribution fraction: `{contrib['teacher_best']['fraction_of_sum_contribution_norm']}`",
            f"- teacher utility vs delta-z norm: `{contrib['correlations_valid_teacher_rows']['teacher_utility_vs_delta_z_norm']}`",
            "",
            "## Free-ID Paired Statistics",
            "",
            f"- all target NLL content-freeID: `{free['all_target_nll']['mean_ci']}`",
            f"- positive target NLL content-freeID: `{free['positive_target_nll']['mean_ci']}`",
            f"- baseline target NLL content-freeID: `{free['baseline_target_nll']['mean_ci']}`",
            "",
            "## Injector Scale Sweep",
            "",
        ]
    )
    for key, value in scale.items():
        lines.append(f"- scale {key}: target_nll `{value['target_nll']}`, no_positive_degradation `{value['no_positive_degradation_vs_bare_qwen']}`, teacher_best_LOO `{value['teacher_best_loo_effect']}`")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"```json\n{json.dumps(summary['decision'], indent=2, sort_keys=True)}\n```",
            "",
            "## Artifacts",
            "",
            f"- summary: `{summary['output_dir']}/summary.json`",
            f"- report: `{summary['output_dir']}/report.md`",
        ]
    )
    return "\n".join(lines) + "\n"


def make_decision(summary: dict[str, Any]) -> dict[str, Any]:
    loo = summary["corrected_leave_one_out"]["aggregate"]["effects"]
    teacher_effect = loo.get("teacher_best", {}).get("distribution", {}).get("mean") or 0.0
    utility_delta_corr = summary["contribution_analysis"]["correlations_valid_teacher_rows"]["teacher_utility_vs_delta_z_norm"]["spearman"]
    align = summary["selector_alignment"]
    free_ci = summary["free_id_comparison"]["paired_statistics"]["aggregate"]["all_target_nll"]["mean_ci"]
    scale_ok = []
    for scale, payload in summary["injector_scale_sweep"]["by_scale"].items():
        degradation = payload["no_positive_degradation_vs_bare_qwen"]["mean"]
        teacher_loo = payload["teacher_best_loo_effect"]["mean"]
        if degradation is not None and degradation <= 0.02 and teacher_loo is not None and abs(teacher_loo) > abs(teacher_effect):
            scale_ok.append(scale)
    if abs(teacher_effect) > 1.0e-4 and utility_delta_corr is not None and utility_delta_corr > 0.1:
        branch = "corrected_teacher_best_loo_nonzero_reassess_stage_c1"
        recommendation = "Reassess Stage-C1 with corrected leave-one-out evidence before designing a repair."
    elif (align["teacher_best_rank_distribution"]["p50"] or 999) > 8:
        branch = "selector_teacher_alignment_issue"
        recommendation = "Repair selector-teacher alignment before another program-channel run."
    elif align["teacher_best_recall_at_8"] >= 0.5 and abs(teacher_effect) <= 1.0e-4:
        branch = "normalized_aggregate_or_program_redundancy_issue"
        recommendation = "Diagnose aggregate read and program redundancy with single-memory/pair-level behavioral targets."
    elif free_ci["lo"] is not None and free_ci["lo"] <= 0.0 <= free_ci["hi"]:
        branch = "content_free_id_statistically_indistinguishable_state_control_shortcut"
        recommendation = "Treat Stage-C1 as a likely state-control shortcut; add pair-level/single-memory behavioral distillation before full-bank retraining."
    elif scale_ok:
        branch = "lower_injector_scale_promising"
        recommendation = f"Consider a restrained-injector Stage-C1 retraining pilot around scales {scale_ok}, after review."
    else:
        branch = "memory_specific_behavior_absent_recommend_pair_level_distillation"
        recommendation = "Run a pair-level or single-memory behavioral distillation pilot before any full-bank Stage-C retraining."
    return {
        "format": "stage_c1_5b_decision_v1",
        "branch": branch,
        "recommendation": recommendation,
        "stage_c2_allowed": False,
        "values": {
            "teacher_best_loo_mean": teacher_effect,
            "teacher_utility_vs_delta_z_spearman": utility_delta_corr,
            "teacher_best_recall_at_8": align["teacher_best_recall_at_8"],
            "content_minus_free_id_all_target_nll_ci": free_ci,
            "scale_candidates": scale_ok,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 5B corrected Stage-C1 leave-one-out and memory-causality diagnostics.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--response-cache-dir", required=True)
    parser.add_argument("--signed-field-dir", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--stage-c1-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--all-memory-subset-size", type=int, default=32)
    parser.add_argument("--aggregate-read-subset-size", type=int, default=16)
    parser.add_argument("--scales", type=float, nargs="+", default=[0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0])
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    device = torch.device(args.device)
    backend = build_backend(cfg, load_model=True)
    context_limit = getattr(getattr(backend.model, "config", None), "max_position_embeddings", 40960)
    model_dim = int(getattr(getattr(backend.model, "config", None), "hidden_size"))
    data_dir = Path(args.data)
    labels_dir = Path(args.labels_dir)
    response_dir = Path(args.response_cache_dir)
    repr_dir = Path(args.representation_cache_dir)
    selector_dir = Path(args.signed_field_dir)
    stage_c1_dir = Path(args.stage_c1_dir)

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
    train_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        label_rows=train_label_rows,
        response_by_state=response_by_state,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(context_limit),
    )
    validation_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        label_rows=validation_label_rows,
        response_by_state=response_by_state,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(context_limit),
    )
    mu = train_memory_prior(train_label_rows, memory_count=len(memory_bank))
    stage_c1_summary = load_json(stage_c1_dir / "summary.json")
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
        "stage_c1_dir": str(stage_c1_dir),
        "stage_c1_training_source_commit": stage_c1_summary.get("training_source_commit"),
        "stage_c1_corrected_eval_source_commit": stage_c1_summary.get("evaluation_source_commit"),
        "seeds": args.seeds,
        "model_name": cfg.model.name,
        "checkpoint_identity": f"frozen_hf_pretrained:{cfg.model.name}",
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "positive_validation_rows": sum(row["response_cache"]["teacher_condition"] == "positive_teacher" for row in validation_rows),
        "effective_memory_count": len(memory_bank),
        "state_cache": state_meta,
        "memory_cache": memory_meta,
        "teacher_cache_sha256": sha256_file(Path(args.teacher_cache_dir) / "teacher_cache_full_rows.jsonl"),
        "response_cache_sha256": sha256_file(response_dir / "response_cache.jsonl"),
    }
    atomic_write_json(output_dir / "run_metadata.json", metadata)
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")

    corrected_loo = run_corrected_leave_one_out(
        backend=backend,
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        output_dir=output_dir,
    )
    selector_alignment, contribution_analysis = selector_alignment_and_contributions(
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        output_dir=output_dir,
    )
    compiled_subset = run_compiled_all_memory_subset(
        backend=backend,
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        output_dir=output_dir,
        subset_size=args.all_memory_subset_size,
    )
    free_id = run_free_id_comparison(
        backend=backend,
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        stage_c1_summary=stage_c1_summary,
        output_dir=output_dir,
    )
    scale_sweep = run_injector_scale_sweep(
        backend=backend,
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        scales=args.scales,
        output_dir=output_dir,
    )
    aggregate_read = run_aggregate_read_diagnosis(
        backend=backend,
        stage_c1_dir=stage_c1_dir,
        selector_dir=selector_dir,
        state_reps=state_reps,
        memory_reps=memory_reps,
        mu=mu,
        train_rows=train_rows,
        validation_rows=validation_rows,
        model_dim=model_dim,
        device=device,
        seeds=args.seeds,
        output_dir=output_dir,
        subset_size=args.aggregate_read_subset_size,
    )
    summary = {
        **metadata,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": "checkpoint_eval_only_no_retraining_no_stage_c2_no_appworld_generation",
        "response_cache_validation": response_validation,
        "old_leave_one_out_invalidated": True,
        "old_leave_one_out_bug": "validation_full_bank=True ignored legal_effective_mask changes in the original leave-one-out audit",
        "corrected_leave_one_out": corrected_loo,
        "selector_alignment": selector_alignment,
        "contribution_analysis": contribution_analysis,
        "compiled_all_memory_subset": compiled_subset,
        "free_id_comparison": free_id,
        "injector_scale_sweep": scale_sweep,
        "aggregate_read_diagnosis": aggregate_read,
    }
    summary["decision"] = make_decision(summary)
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", write_report(summary))
    print(f"Wrote Stage-C1 5B diagnostics to {output_dir}", flush=True)
    print(json.dumps(summary["decision"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
