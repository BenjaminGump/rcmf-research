from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

import torch
from torch import Tensor, nn

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _render_prompt_with_metadata,
    _target_suffix,
    load_decision_examples,
)
from rcmf.training.oracle_convergence_5fa import (
    OBJECTIVES_5FA,
    IndependentPairTensorTable,
    atomic_torch_save,
    update_count_summary,
)
from rcmf.training.oracle_convergence_5fb import (
    add_selection_category_metrics,
    paired_bootstrap_difference,
    tensor_state_sha256,
)
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    apply_latent_inversion_step,
    assess_u64_inversion_continuation,
    flatten_delta,
    module_state_sha256,
    project_latents_to_output_ratio_,
    validate_direct_checkpoint,
)
from rcmf.training.pair_grounding_5d import spearman
from rcmf.training.transition_memory_6a import (
    deterministic_identity_derangement,
    granularity_advantage,
    normalized_huber_reduction,
    pair_oracle_capacity_gate,
    program_geometry,
    transition_pilot_decision,
    transition_static_gate,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import (
    _context_limit_for_backend,
    _target_token_ids,
    _token_ids,
)
from scripts.run_stage_c_oracle_capacity_5e import _collate, _forward_direct_delta
from scripts.run_stage_c_oracle_convergence_5fa import (
    _evaluate_direct_tensor,
    _precompute_direct_base_norms,
    _train_direct_convergence,
    _training_loss,
)
from scripts.run_stage_c_oracle_decoder_5fc import _run_inversion


K_TOKENS = 4
LATENT_DIM = 128
RATIO_BUDGET = 1.0
REPRO_TOLERANCE = 2.0e-4
MAX_H100_SECONDS = 12.0 * 3600.0


def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _stable_index(seed: int, namespace: str, value: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{seed}|{namespace}|{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % int(modulo)


def _adapt_response_rows(
    rows: Sequence[dict[str, Any]], *, identity_ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    if identity_ids is None:
        identity_ids = sorted({str(row["entity_id"]) for row in rows})
    identity_to_index = {
        str(identity_id): index for index, identity_id in enumerate(identity_ids)
    }
    output = []
    for source in rows:
        row = copy.deepcopy(source)
        identity_id = str(row["entity_id"])
        if identity_id not in identity_to_index:
            continue
        row.update(
            {
                "memory_id": identity_id,
                "memory_task_id": str(row["entity_task_id"]),
                "memory_stage_index": identity_to_index[identity_id],
                "memory_index": identity_to_index[identity_id],
                "raw_memory_tokens": int(row["teacher_prompt_tokens"])
                - int(row["prompt_tokens"]),
            }
        )
        output.append(row)
    return output


def _build_tokenized_rows(
    *,
    backend: Any,
    examples: Sequence[Any],
    response_rows: Sequence[dict[str, Any]],
    prompt_profile: str,
    context_limit: int,
) -> list[dict[str, Any]]:
    tokenizer = backend.tokenizer
    pad_id = int(
        getattr(tokenizer, "pad_token_id", None)
        or getattr(tokenizer, "eos_token_id", 0)
        or 0
    )
    output = []
    for response in response_rows:
        example = examples[int(response["state_index"])]
        messages = _appworld_messages_from_example(example, prompt_profile)
        prompt_text, prompt_metadata = _render_prompt_with_metadata(
            tokenizer, messages, prompt_profile
        )
        prompt_ids = _token_ids(tokenizer, prompt_text, add_special_tokens=False)
        target_ids = _target_token_ids(tokenizer, example)
        target_text = _target_suffix(example)
        if target_ids != [int(value) for value in response["target_token_ids"]]:
            raise ValueError(f"Target IDs differ for {response['pair_id']}")
        if sha256_text(target_text) != str(response["target_sha256"]):
            raise ValueError(f"Target hash differs for {response['pair_id']}")
        if sha256_text(prompt_text) != str(response["prompt_sha256"]):
            raise ValueError(f"Bare prompt hash differs for {response['pair_id']}")
        full_ids = prompt_ids + target_ids
        if len(full_ids) > context_limit:
            raise ValueError(f"Student prompt exceeds context for {response['pair_id']}")
        output.append(
            {
                "format": "decision_transition_tokenized_behavior_row_6a_v1",
                "pair_id": str(response["pair_id"]),
                "pair_key": str(response["pair_key"]),
                "state_index": int(response["state_index"]),
                "state_example_id": str(response["state_example_id"]),
                "task_id": str(response["task_id"]),
                "episode_id": str(response["episode_id"]),
                "step_id": int(response["step_id"]),
                "split": str(response["split"]),
                "memory_stage_index": int(response["memory_stage_index"]),
                "memory_index": int(response["memory_index"]),
                "memory_id": str(response["memory_id"]),
                "memory_task_id": str(response["memory_task_id"]),
                "selection_category": str(response["selection_category"]),
                "utility_category": str(response["utility_category"]),
                "u_text": float(response["text_utility"]),
                "L0": float(response["baseline_mean_target_nll"]),
                "Lj_text": float(response["teacher_mean_target_nll"]),
                "response_cache": response,
                "input_ids": full_ids,
                "labels": [-100] * len(prompt_ids) + target_ids,
                "attention_mask": [1] * len(full_ids),
                "target_len": len(target_ids),
                "prompt_len": len(prompt_ids),
                "last_user_token_indices": list(
                    prompt_metadata.get("last_user_token_indices", [])
                ),
                "pad_token_id": pad_id,
            }
        )
    return output


def _load_frozen_decoder(
    *, checkpoint: Path, model_dim: int, device: torch.device, output_dir: Path
) -> tuple[nn.Module, dict[str, Any]]:
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    expected_pair_ids = [str(value) for value in payload.get("pair_ids", [])]
    validation = validate_direct_checkpoint(
        payload,
        expected_pair_ids=expected_pair_ids,
        expected_updates=112,
        model_dim=model_dim,
    )
    if not validation["passed"] or len(expected_pair_ids) != 192:
        raise ValueError(f"Invalid EXP-016C u112 source checkpoint: {validation}")
    sidecar_path = checkpoint.with_suffix(checkpoint.suffix + ".integrity.json")
    if not sidecar_path.exists():
        raise FileNotFoundError(f"Missing source integrity sidecar: {sidecar_path}")
    sidecar = _load_json(sidecar_path)
    checkpoint_hash = sha256_file(checkpoint)
    delta_hash = tensor_state_sha256(payload["table_state_dict"])
    hash_checks = {
        "checkpoint_file_matches_sidecar": checkpoint_hash
        == sidecar.get("checkpoint_file_sha256"),
        "delta_tensor_matches_sidecar": delta_hash
        == sidecar.get("delta_tensor_sha256"),
        "delta_tensor_matches_embedded": delta_hash
        == payload.get("metadata", {}).get("delta_tensor_sha256"),
    }
    if not all(hash_checks.values()):
        raise ValueError(f"u112 checkpoint hashes differ: {hash_checks}")
    source_delta = validation.pop("tensor").to(device=device, dtype=torch.float64)
    _, singular, vh = torch.linalg.svd(
        flatten_delta(source_delta).to(torch.float64), full_matrices=False
    )
    basis = vh[:LATENT_DIM].to(torch.float32)
    decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * model_dim).to(device)
    decoder.initialize_from_basis(basis)
    decoder.eval()
    for parameter in decoder.parameters():
        parameter.requires_grad_(False)
    orthogonality_error = float(
        (basis @ basis.T - torch.eye(LATENT_DIM, device=device)).abs().max().cpu()
    )
    zero = decoder(torch.zeros(3, LATENT_DIM, device=device))
    report = {
        "format": "exp016c_u112_frozen_linear_decoder_validation_6a_v1",
        "source_checkpoint": str(checkpoint),
        "source_checkpoint_sha256": checkpoint_hash,
        "source_delta_sha256": delta_hash,
        "source_pair_count": len(expected_pair_ids),
        "source_pair_ids_sha256": sha256_text("\n".join(expected_pair_ids)),
        "source_validation": validation,
        "hash_checks": hash_checks,
        "basis_construction": "uncentered_svd_vh_first_128_float64_factorization",
        "basis_sha256": tensor_state_sha256({"basis": basis.detach().cpu()}),
        "decoder_sha256": module_state_sha256(decoder),
        "basis_shape": list(basis.shape),
        "orthogonality_max_abs_error": orthogonality_error,
        "source_singular_values": [float(value) for value in singular.cpu().tolist()],
        "zero_latent_max_abs_delta": float(zero.abs().max().cpu()),
        "passed": all(hash_checks.values())
        and orthogonality_error <= 1.0e-5
        and float(zero.abs().max().cpu()) == 0.0,
    }
    atomic_write_json(output_dir / "frozen_decoder_validation.json", report)
    if not report["passed"]:
        raise RuntimeError(f"Frozen decoder validation failed: {report}")
    return decoder, report


def _zero_equivalence(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    model_dim: int,
    huber_delta: float,
    control: str,
) -> dict[str, Any]:
    pair_ids = [str(row["pair_id"]) for row in rows]
    evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=torch.zeros(len(rows), K_TOKENS, model_dim, device=device),
        pair_ids=pair_ids,
        device=device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=huber_delta,
        control=control,
    )
    differences = [
        abs(float(row["student_target_nll"]) - float(row["L0"]))
        for row in evaluation["rows"]
    ]
    report = {
        "maximum_absolute_nll_difference": max(differences, default=0.0),
        "tolerance": REPRO_TOLERANCE,
        "passed": max(differences, default=0.0) <= REPRO_TOLERANCE,
        "evaluation": evaluation,
    }
    if not report["passed"]:
        raise RuntimeError(f"Zero-delta equivalence failed: {report}")
    return report


def _evaluate_identity_latents(
    *,
    backend: Any,
    decoder: nn.Module,
    rows: Sequence[dict[str, Any]],
    identity_ids: Sequence[str],
    identity_latents: Tensor,
    identity_assignment: Mapping[str, str] | None,
    device: torch.device,
    model_dim: int,
    huber_delta: float,
    control: str,
) -> dict[str, Any]:
    identity_to_index = {
        str(identity_id): index for index, identity_id in enumerate(identity_ids)
    }
    selected = []
    for row in rows:
        source = str(row["memory_id"])
        assigned = (
            str(identity_assignment[source]) if identity_assignment is not None else source
        )
        selected.append(identity_latents[identity_to_index[assigned]])
    z = torch.stack(selected, dim=0).to(device)
    with torch.no_grad():
        delta = decoder(z).view(len(rows), K_TOKENS, model_dim)
    pair_ids = [str(row["pair_id"]) for row in rows]
    evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=delta,
        pair_ids=pair_ids,
        device=device,
        k=K_TOKENS,
        batch_size=1,
        huber_delta=huber_delta,
        control=control,
    )
    evaluation["summary"] = add_selection_category_metrics(
        evaluation["summary"], evaluation["rows"]
    )
    return evaluation


def _mean_huber(rows: Sequence[dict[str, Any]]) -> float:
    return statistics.fmean(float(row["sequence_utility_huber"]) for row in rows)


def _bootstrap_controls(
    *, correct: Sequence[dict[str, Any]], controls: Mapping[str, Sequence[dict[str, Any]]], seed: int
) -> dict[str, Any]:
    return {
        f"correct_minus_{name}_sequence_huber": paired_bootstrap_difference(
            correct,
            rows,
            statistic=_mean_huber,
            samples=5000,
            seed=seed + index,
        )
        for index, (name, rows) in enumerate(sorted(controls.items()), start=1)
    }


def _runtime_allows_extension(
    *,
    teacher_runtime_s: float,
    behavior_started: float,
    completed_updates: int,
    identity_count: int,
    additional_updates: int,
) -> dict[str, Any]:
    elapsed = time.perf_counter() - behavior_started
    observed_update_count = max(1, int(completed_updates) * int(identity_count))
    seconds_per_update = elapsed / observed_update_count
    projected_additional = seconds_per_update * int(additional_updates) * int(identity_count)
    projected_total = teacher_runtime_s + elapsed + projected_additional
    return {
        "teacher_runtime_s": teacher_runtime_s,
        "behavior_elapsed_s": elapsed,
        "observed_seconds_per_identity_update": seconds_per_update,
        "projected_additional_s": projected_additional,
        "projected_total_h100_hours": projected_total / 3600.0,
        "hard_limit_h100_hours": MAX_H100_SECONDS / 3600.0,
        "allowed": projected_total <= MAX_H100_SECONDS,
    }


def _identity_base_norms(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    identity_ids: Sequence[str],
    device: torch.device,
) -> Tensor:
    per_row = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=K_TOKENS
    ).detach().cpu()
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, per_row.tolist()):
        grouped[str(row["memory_id"])].append(float(value))
    missing = [identity_id for identity_id in identity_ids if not grouped[str(identity_id)]]
    if missing:
        raise ValueError(f"Identities have no base norm rows: {missing}")
    return torch.tensor(
        [min(grouped[str(identity_id)]) for identity_id in identity_ids],
        dtype=torch.float32,
        device=device,
    )


def _project_latent_copy(
    *, decoder: nn.Module, latents: Tensor, base_norms: Tensor
) -> Tensor:
    output = latents.detach().clone().to(base_norms.device)
    project_latents_to_output_ratio_(
        output, decoder, base_norms, max_ratio=RATIO_BUDGET
    )
    return output.detach()


def _save_evaluation(output_dir: Path, name: str, evaluation: Mapping[str, Any]) -> dict[str, Any]:
    path = output_dir / f"{name}_rows.jsonl"
    write_jsonl(path, evaluation["rows"])
    return {
        "summary": evaluation["summary"],
        "rows_path": str(path),
        "rows_sha256": sha256_file(path),
        "selected_token_report": evaluation.get("selected_token_report"),
    }


def _run_static_identity_model(
    *,
    name: str,
    backend: Any,
    decoder: nn.Module,
    train_rows: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, Any]],
    identity_ids: Sequence[str],
    device: torch.device,
    model_dim: int,
    objective: Any,
    learning_rate: float,
    max_gradient_norm: float,
    seed: int,
    output_dir: Path,
    teacher_runtime_s: float,
    behavior_started: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    if summary_path.exists():
        completed = _load_json(summary_path)
        if completed.get("status") == "completed":
            print(f"reusing completed static identity run: {name}", flush=True)
            return completed

    identities = [str(value) for value in identity_ids]
    if len(identities) != len(set(identities)) or len(identities) < 2:
        raise ValueError(f"Invalid {name} identities")
    by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        by_identity[str(row["memory_id"])].append(row)
    absent_train = [value for value in identities if not by_identity[value]]
    if absent_train:
        raise ValueError(f"{name} identities lack train rows: {absent_train}")
    validation_identity_set = {str(row["memory_id"]) for row in validation_rows}
    absent_validation = sorted(set(identities) - validation_identity_set)
    if absent_validation:
        raise ValueError(f"{name} identities lack validation rows: {absent_validation}")
    for rows in by_identity.values():
        rows.sort(key=lambda row: str(row["pair_id"]))

    table = IndependentPairTensorTable(identities, (LATENT_DIM,), init_std=0.0).to(device)
    optimizer = torch.optim.AdamW(table.parameters(), lr=learning_rate, weight_decay=0.0)
    all_rows_by_pair = {
        str(row["pair_id"]): row for row in [*train_rows, *validation_rows]
    }
    all_unique_rows = list(all_rows_by_pair.values())
    base_norms = _identity_base_norms(
        backend=backend,
        rows=all_unique_rows,
        identity_ids=identities,
        device=device,
    )
    update_counts = [0] * len(identities)
    history: list[dict[str, Any]] = []
    completed_rounds = 0
    latest_pointer = output_dir / "latest_checkpoint.json"
    initial_decoder_hash = module_state_sha256(decoder)
    run_identity = {
        "name": name,
        "identity_ids": identities,
        "train_pair_ids": [str(row["pair_id"]) for row in train_rows],
        "validation_pair_ids": [str(row["pair_id"]) for row in validation_rows],
        "objective": vars(objective),
        "learning_rate": float(learning_rate),
        "ratio_budget": RATIO_BUDGET,
        "decoder_sha256": initial_decoder_hash,
        "seed": int(seed),
    }
    run_identity_sha256 = sha256_text(
        json.dumps(run_identity, sort_keys=True, separators=(",", ":"))
    )
    if latest_pointer.exists():
        checkpoint_path = Path(_load_json(latest_pointer)["checkpoint"])
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        checks = {
            "run_identity": payload.get("run_identity_sha256")
            == run_identity_sha256,
            "decoder": payload.get("decoder_sha256") == initial_decoder_hash,
            "identity_ids": payload.get("identity_ids") == identities,
        }
        if not all(checks.values()):
            raise ValueError(f"{name} resume identity differs: {checks}")
        table.load_state_dict(payload["table_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        history = list(payload["history"])
        completed_rounds = int(payload["completed_rounds"])
        print(f"resumed {name} at u{completed_rounds}", flush=True)

    interval: list[dict[str, float]] = []
    final_train_evaluation: dict[str, Any] | None = None
    continuation: dict[str, Any] = {"assessable": False, "continue_to_128": False}
    runtime_gate: dict[str, Any] | None = None
    maximum_round = 128
    checkpoint_rounds = {16, 32, 64, 128}
    identity_to_index = {value: index for index, value in enumerate(identities)}
    loop_maximum = maximum_round
    if completed_rounds >= 64 and history:
        continuation = assess_u64_inversion_continuation(history)
        runtime_gate = _runtime_allows_extension(
            teacher_runtime_s=teacher_runtime_s,
            behavior_started=behavior_started,
            completed_updates=completed_rounds,
            identity_count=len(identities),
            additional_updates=64,
        )
        if not continuation["continue_to_128"] or not runtime_gate["allowed"]:
            loop_maximum = completed_rounds

    for update_round in range(completed_rounds + 1, loop_maximum + 1):
        order = list(identities)
        random.Random(seed * 1_000_000 + update_round).shuffle(order)
        for identity_id in order:
            index = identity_to_index[identity_id]
            options = by_identity[identity_id]
            row_index = _stable_index(
                seed,
                f"{name}:u{update_round}",
                identity_id,
                len(options),
            )
            batch_rows = [options[row_index]]
            batch = _collate(batch_rows, device=device, k=K_TOKENS)
            z = table.forward_indices([index])
            delta = decoder(z).view(1, K_TOKENS, model_dim)
            student = _forward_direct_delta(
                backend=backend, batch=batch, delta_slots=delta
            )
            loss, terms = _training_loss(
                logits=student["target_logits"], batch=batch, objective=objective
            )
            step = apply_latent_inversion_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                decoder=decoder,
                selected_indices=[index],
                update_counts=update_counts,
                base_norms=base_norms,
                ratio_budget=RATIO_BUDGET,
                train_decoder=False,
                max_grad_norm=max_gradient_norm,
            )
            interval.append(
                {
                    "loss": float(loss.detach().cpu()),
                    "sequence_utility_huber": float(
                        terms["sequence_utility_huber"].detach().cpu()
                    ),
                    "sparse_teacher_kl": float(
                        terms["sparse_teacher_kl"].detach().cpu()
                    ),
                    "gradient_norm": float(step["gradient_norm"]),
                }
            )
        accounting = update_count_summary(identities, update_counts)
        if not accounting["all_pairs_equal"] or int(
            accounting["minimum_updates_per_pair"]
        ) != update_round:
            raise RuntimeError(f"Unequal {name} updates at u{update_round}")
        if update_round not in checkpoint_rounds:
            continue
        latents = table.stacked().detach()
        train_evaluation = _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=train_rows,
            identity_ids=identities,
            identity_latents=latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_train_u{update_round}",
        )
        entry = {
            "updates_per_pair": update_round,
            "pair_ids": [str(row["pair_id"]) for row in train_rows],
            "update_accounting": accounting,
            "evaluation_summary": train_evaluation["summary"],
            "train_interval": {
                field: {
                    "mean": statistics.fmean(item[field] for item in interval),
                    "min": min(item[field] for item in interval),
                    "max": max(item[field] for item in interval),
                }
                for field in (
                    "loss",
                    "sequence_utility_huber",
                    "sparse_teacher_kl",
                    "gradient_norm",
                )
            },
            "decoder_sha256": module_state_sha256(decoder),
            "timestamp_utc": utc_now(),
        }
        provisional = [*history, entry]
        if update_round == 64:
            continuation = assess_u64_inversion_continuation(provisional)
            runtime_gate = _runtime_allows_extension(
                teacher_runtime_s=teacher_runtime_s,
                behavior_started=behavior_started,
                completed_updates=update_round,
                identity_count=len(identities),
                additional_updates=64,
            )
            entry["u64_continuation"] = continuation
            entry["runtime_extension_gate"] = runtime_gate
        history.append(entry)
        rows_path = output_dir / f"train_evaluation_u{update_round:03d}.jsonl"
        write_jsonl(rows_path, train_evaluation["rows"])
        checkpoint_path = output_dir / "checkpoints" / f"{name}_u{update_round:03d}.pt"
        atomic_torch_save(
            {
                "format": "decision_transition_static_latent_checkpoint_6a_v1",
                "name": name,
                "identity_ids": identities,
                "completed_rounds": update_round,
                "update_counts": update_counts,
                "update_accounting": accounting,
                "table_state_dict": {
                    key: value.detach().cpu()
                    for key, value in table.state_dict().items()
                },
                "optimizer_state_dict": optimizer.state_dict(),
                "history": history,
                "decoder_sha256": initial_decoder_hash,
                "run_identity": run_identity,
                "run_identity_sha256": run_identity_sha256,
                "source_commit": maybe_git_commit(),
            },
            checkpoint_path,
        )
        atomic_write_json(
            latest_pointer,
            {"checkpoint": str(checkpoint_path), "updates_per_identity": update_round},
        )
        atomic_write_json(output_dir / "history.json", history)
        print(
            f"{name} u{update_round} train_spearman="
            f"{train_evaluation['summary']['u_text_vs_u_student_spearman']} "
            f"train_huber={train_evaluation['summary']['sequence_utility_huber']['mean']:.6f}",
            flush=True,
        )
        final_train_evaluation = train_evaluation
        interval = []
        if update_round == 64 and (
            not continuation["continue_to_128"]
            or runtime_gate is None
            or not runtime_gate["allowed"]
        ):
            break

    if history and final_train_evaluation is None:
        final_train_evaluation = _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=train_rows,
            identity_ids=identities,
            identity_latents=table.stacked().detach(),
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_train_resumed_u{completed_rounds}",
        )
    if not history or final_train_evaluation is None:
        raise RuntimeError(f"{name} produced no checkpoint evaluation")
    final_updates = int(history[-1]["updates_per_pair"])
    learned = table.stacked().detach()
    learned = _project_latent_copy(
        decoder=decoder, latents=learned, base_norms=base_norms
    )
    shuffle_map = deterministic_identity_derangement(
        identities, seed=seed, namespace=f"{name}:shuffle"
    )
    swap_map = deterministic_identity_derangement(
        identities, seed=seed, namespace=f"{name}:swap"
    )
    generator = torch.Generator(device="cpu").manual_seed(seed + 80_000)
    random_latents = torch.randn(
        len(identities), LATENT_DIM, generator=generator, dtype=torch.float32
    ).to(device)
    learned_norms = learned.norm(dim=1).clamp_min(1.0e-8)
    random_norms = random_latents.norm(dim=1).clamp_min(1.0e-8)
    random_latents.mul_((learned_norms / random_norms).unsqueeze(1))
    random_latents = _project_latent_copy(
        decoder=decoder, latents=random_latents, base_norms=base_norms
    )
    mean_latents = learned.mean(dim=0, keepdim=True).repeat(len(identities), 1)
    mean_latents = _project_latent_copy(
        decoder=decoder, latents=mean_latents, base_norms=base_norms
    )
    zero_latents = torch.zeros_like(learned)
    identity_to_index = {value: index for index, value in enumerate(identities)}
    shuffled_latents = torch.stack(
        [learned[identity_to_index[shuffle_map[value]]] for value in identities]
    )
    shuffled_latents = _project_latent_copy(
        decoder=decoder, latents=shuffled_latents, base_norms=base_norms
    )
    swapped_latents = torch.stack(
        [learned[identity_to_index[swap_map[value]]] for value in identities]
    )
    swapped_latents = _project_latent_copy(
        decoder=decoder, latents=swapped_latents, base_norms=base_norms
    )
    evaluations = {
        "correct": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=learned,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_correct",
        ),
        "shuffled": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=shuffled_latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_shuffled",
        ),
        "random": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=random_latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_random",
        ),
        "mean": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=mean_latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_mean",
        ),
        "zero": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=zero_latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_zero",
        ),
        "swap": _evaluate_identity_latents(
            backend=backend,
            decoder=decoder,
            rows=validation_rows,
            identity_ids=identities,
            identity_latents=swapped_latents,
            identity_assignment=None,
            device=device,
            model_dim=model_dim,
            huber_delta=objective.huber_delta,
            control=f"{name}_swap",
        ),
    }
    portable = {
        key: _save_evaluation(output_dir, key, value)
        for key, value in evaluations.items()
    }
    task_results = {}
    for task_id in sorted({str(row["task_id"]) for row in validation_rows}):
        correct_task = [
            row for row in evaluations["correct"]["rows"] if row["source_state_task_id"] == task_id
        ]
        zero_task = [
            row for row in evaluations["zero"]["rows"] if row["source_state_task_id"] == task_id
        ]
        task_results[task_id] = bool(correct_task) and _mean_huber(correct_task) < _mean_huber(zero_task)
    controls_for_gate = {
        key: value["summary"]
        for key, value in evaluations.items()
        if key in {"shuffled", "random", "mean", "swap"}
    }
    geometry = program_geometry(learned)
    gate = transition_static_gate(
        summary=evaluations["correct"]["summary"],
        zero_summary=evaluations["zero"]["summary"],
        controls=controls_for_gate,
        task_results=task_results,
        geometry=geometry,
    )
    bootstrap = _bootstrap_controls(
        correct=evaluations["correct"]["rows"],
        controls={
            key: value["rows"]
            for key, value in evaluations.items()
            if key != "correct"
        },
        seed=seed,
    )
    required_material_controls = ("shuffled", "random", "swap")
    material_control_checks = {
        control: float(
            bootstrap[f"correct_minus_{control}_sequence_huber"]["ci95"][1]
        )
        < 0.0
        for control in required_material_controls
    }
    gate["checks"][
        "paired_huber_ci_below_shuffled_random_swap"
    ] = all(material_control_checks.values())
    gate["material_control_checks"] = material_control_checks
    gate["passed"] = all(gate["checks"].values())
    decoder_final_hash = module_state_sha256(decoder)
    if decoder_final_hash != initial_decoder_hash:
        raise RuntimeError(f"{name} modified the frozen decoder")
    result = {
        "format": "decision_transition_static_latent_run_6a_v1",
        "status": "completed",
        "name": name,
        "identity_count": len(identities),
        "identity_ids": identities,
        "train_pair_count": len(train_rows),
        "validation_pair_count": len(validation_rows),
        "final_updates_per_identity": final_updates,
        "update_accounting": update_count_summary(identities, update_counts),
        "history": history,
        "u64_continuation": continuation,
        "runtime_extension_gate": runtime_gate,
        "evaluations": portable,
        "gate": gate,
        "task_results": task_results,
        "bootstrap_confidence_intervals": bootstrap,
        "geometry": geometry,
        "shuffle_assignment": shuffle_map,
        "swap_assignment": swap_map,
        "latent_sha256": tensor_state_sha256({"latents": learned.cpu()}),
        "decoder_initial_sha256": initial_decoder_hash,
        "decoder_final_sha256": decoder_final_hash,
        "decoder_unchanged": decoder_final_hash == initial_decoder_hash,
        "checkpoint": _load_json(latest_pointer)["checkpoint"],
        "source_commit": maybe_git_commit(),
    }
    atomic_write_json(summary_path, result)
    return result


def _pearson_values(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return None if denominator == 0 else numerator / denominator


def _teacher_validity_gate(
    summary: Mapping[str, Any], rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    validation = summary["validation"]
    utility = summary["teacher_analysis"]["utility"]
    categories = utility["category_counts"]
    correlations = summary["teacher_analysis"]["correlations"]
    scored_rows = [row for row in rows if row.get("valid_for_loss")]
    scored = int(validation["scoreable_pair_count"])
    if len(scored_rows) != scored:
        raise ValueError("Teacher rows differ from the validated scoreable count")
    exact_count = int(summary["teacher_analysis"]["target_exact_substring_count"])
    finite_correlations = [
        abs(float(value)) for value in correlations.values() if value is not None
    ]
    exact_indicators = [
        float(bool(row["normalized_target_exact_substring_in_transition"]))
        for row in scored_rows
    ]
    if int(sum(exact_indicators)) != exact_count:
        raise ValueError("Teacher exact-target incidence differs from summary")
    utilities = [float(row["text_utility"]) for row in scored_rows]
    exact_utility_correlation = _pearson_values(exact_indicators, utilities)
    noncopy_positive_count = sum(
        float(row["text_utility"]) > 0.01
        and not bool(row["normalized_target_exact_substring_in_transition"])
        for row in scored_rows
    )
    positive_rows = [row for row in scored_rows if float(row["text_utility"]) > 0.01]
    positive_exact_fraction = sum(
        bool(row["normalized_target_exact_substring_in_transition"])
        for row in positive_rows
    ) / max(len(positive_rows), 1)
    top_count = max(10, int(math.ceil(0.10 * len(scored_rows))))
    top_rows = sorted(
        scored_rows, key=lambda row: -float(row["text_utility"])
    )[:top_count]
    top_utility_exact_fraction = sum(
        bool(row["normalized_target_exact_substring_in_transition"])
        for row in top_rows
    ) / len(top_rows)
    checks = {
        "teacher_cache_validation": bool(validation["passed"]),
        "reproducible_scoring": bool(summary["reproducibility"]["passed"]),
        "representative_serialization_inspection": bool(
            summary["representative_inspection"]["passed"]
        ),
        "positive_population_gte_16": int(categories.get("positive", 0)) >= 16,
        "negative_population_gte_16": int(categories.get("negative", 0)) >= 16,
        "length_or_overlap_correlation_abs_lt_0_80": max(
            finite_correlations, default=0.0
        )
        < 0.80,
        "noncopy_positive_population_gte_16": noncopy_positive_count >= 16,
        "exact_match_utility_correlation_abs_lt_0_80": abs(
            float(exact_utility_correlation or 0.0)
        )
        < 0.80,
        "positive_population_not_copy_dominated": positive_exact_fraction < 0.80,
        "top_utility_decile_not_copy_dominated": top_utility_exact_fraction
        < 0.80,
        "no_leakage_or_truncation": int(validation["error_count"]) == 0,
    }
    return {
        "checks": checks,
        "maximum_absolute_length_or_overlap_correlation": max(
            finite_correlations, default=0.0
        ),
        "exact_target_substring_fraction": exact_count / max(scored, 1),
        "exact_match_utility_correlation": exact_utility_correlation,
        "noncopy_positive_count": noncopy_positive_count,
        "positive_exact_match_fraction": positive_exact_fraction,
        "top_utility_decile_exact_match_fraction": top_utility_exact_fraction,
        "top_utility_decile_count": top_count,
        "passed": all(checks.values()),
    }


def _teacher_length_stratification(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    scored = [row for row in rows if row.get("valid_for_loss")]
    output: dict[str, Any] = {
        "format": "decision_transition_teacher_length_stratification_6a_v1"
    }
    for field in (
        "source_state_tokens",
        "action_tokens",
        "observation_tokens",
        "transition_section_tokens",
    ):
        ordered = sorted(int(row[field]) for row in scored)
        q1 = ordered[int(round((len(ordered) - 1) / 3.0))]
        q2 = ordered[int(round(2.0 * (len(ordered) - 1) / 3.0))]
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in scored:
            value = int(row[field])
            bucket = "short" if value <= q1 else "medium" if value <= q2 else "long"
            buckets[bucket].append(float(row["text_utility"]))
        output[field] = {
            "quantile_boundaries": {"q1": q1, "q2": q2},
            "buckets": {
                bucket: {
                    "count": len(values),
                    "mean_utility": statistics.fmean(values),
                    "median_utility": statistics.median(values),
                    "positive": sum(value > 0.01 for value in values),
                    "neutral": sum(abs(value) <= 0.01 for value in values),
                    "negative": sum(value < -0.01 for value in values),
                }
                for bucket, values in sorted(buckets.items())
            },
        }
    return output


def _pair_oracle_run(
    *,
    backend: Any,
    decoder: nn.Module,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    model_dim: int,
    objective: Any,
    learning_rate: float,
    max_gradient_norm: float,
    seed: int,
    output_dir: Path,
) -> dict[str, Any]:
    pair_ids = [str(row["pair_id"]) for row in rows]
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=K_TOKENS
    ).detach().cpu()
    zero = _zero_equivalence(
        backend=backend,
        rows=rows,
        device=device,
        model_dim=model_dim,
        huber_delta=objective.huber_delta,
        control="transition_pair_oracle_zero",
    )
    initial_z = torch.zeros(len(rows), LATENT_DIM, dtype=torch.float32)
    settings = {
        "heldout_inversion": {
            "latent_learning_rate": float(learning_rate),
            "joint_decoder_learning_rate": 0.0,
            "max_gradient_norm": float(max_gradient_norm),
            "optional_final_updates_per_pair": 128,
        },
        "prospective_plateau": {
            "absolute_relative_sequence_huber_change_lt": 0.01,
            "absolute_spearman_change_lt": 0.01,
            "current_huber_lte_best_multiplier": 1.02,
        },
    }
    run = _run_inversion(
        name="transition_pair_oracle_frozen_linear",
        decoder_source=decoder,
        initial_z=initial_z,
        rows=rows,
        pair_ids=pair_ids,
        base_norms=base_norms,
        backend=backend,
        device=device,
        model_dim=model_dim,
        objective=objective,
        settings=settings,
        seed=seed,
        output_dir=output_dir,
        train_decoder=False,
    )
    final_summary = run["final_evaluation"]["summary"]
    zero_summary = zero["evaluation"]["summary"]
    gate = pair_oracle_capacity_gate(
        summary=final_summary, zero_summary=zero_summary
    )
    zero_portable = _save_evaluation(
        output_dir, "zero", zero["evaluation"]
    )
    final_rows = run["final_evaluation_runtime"]["rows"]
    bootstrap = {
        "correct_minus_zero_sequence_huber": paired_bootstrap_difference(
            final_rows,
            zero["evaluation"]["rows"],
            statistic=_mean_huber,
            samples=5000,
            seed=seed + 1,
        )
    }
    summary = {
        "format": "decision_transition_pair_oracle_6a_v1",
        "status": "completed",
        "pair_count": len(rows),
        "selection_category_counts": {
            category: sum(row["selection_category"] == category for row in rows)
            for category in ("positive", "neutral", "negative", "random")
        },
        "unique_transition_count": len({row["memory_id"] for row in rows}),
        "unique_query_count": len({row["state_example_id"] for row in rows}),
        "final_updates_per_pair": run["final_updates_per_pair"],
        "history": run["history"],
        "u64_continuation": run["u64_continuation"],
        "final_evaluation": run["final_evaluation"],
        "zero_evaluation": zero_portable,
        "zero_equivalence": {
            key: value for key, value in zero.items() if key != "evaluation"
        },
        "gate": gate,
        "bootstrap_confidence_intervals": bootstrap,
        "checkpoint": run["latest_checkpoint"],
        "decoder_initial_sha256": run["initial_decoder_state_sha256"],
        "decoder_final_sha256": run["final_decoder_state_sha256"],
        "decoder_unchanged": run["frozen_decoder_unchanged"],
    }
    atomic_write_json(output_dir / "pair_oracle_summary.json", summary)
    return summary


def _conditional_direct_oracle(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    model_dim: int,
    objective: Any,
    seed: int,
    output_dir: Path,
) -> dict[str, Any]:
    selected = []
    for category in ("positive", "neutral", "negative", "random"):
        bucket = sorted(
            [row for row in rows if row["selection_category"] == category],
            key=lambda row: str(row["pair_id"]),
        )
        selected.extend(bucket[:8])
    if len(selected) != 32:
        raise ValueError("Conditional direct oracle did not select 8 rows per category")
    zero = _zero_equivalence(
        backend=backend,
        rows=selected,
        device=device,
        model_dim=model_dim,
        huber_delta=objective.huber_delta,
        control="transition_conditional_direct_zero",
    )
    run = _train_direct_convergence(
        backend=backend,
        rows=list(selected),
        objective=objective,
        ratio_budget=RATIO_BUDGET,
        device=device,
        output_dir=output_dir,
        seed=seed,
        k=K_TOKENS,
        batch_size=1,
        lr=0.05,
        minimum_updates=64,
        maximum_updates=64,
        zero_evaluation=zero["evaluation"],
        progress_interval_s=300.0,
        resume=True,
    )
    summary = run["final_evaluation"]["summary"]
    gate = pair_oracle_capacity_gate(
        summary=summary, zero_summary=zero["evaluation"]["summary"]
    )
    result = {
        "format": "decision_transition_conditional_direct_oracle_6a_v1",
        "status": "completed",
        "pair_count": len(selected),
        "pair_ids": [str(row["pair_id"]) for row in selected],
        "final_evaluation": run["final_evaluation"],
        "zero_summary": zero["evaluation"]["summary"],
        "gate": gate,
        "checkpoint": run["checkpoint"],
    }
    atomic_write_json(output_dir / "conditional_direct_summary.json", result)
    return result


def _static_comparison_payload(run: Mapping[str, Any]) -> dict[str, Any]:
    correct = run["evaluations"]["correct"]["summary"]
    zero = run["evaluations"]["zero"]["summary"]
    swap = run["evaluations"]["swap"]["summary"]
    correct_huber = float(correct["sequence_utility_huber"]["mean"])
    zero_huber = float(zero["sequence_utility_huber"]["mean"])
    swap_huber = float(swap["sequence_utility_huber"]["mean"])
    return {
        "utility_spearman": float(correct.get("u_text_vs_u_student_spearman") or -1.0),
        "sign_agreement": float(
            correct.get("positive_negative_sign_agreement") or 0.0
        ),
        "normalized_huber_reduction": normalized_huber_reduction(
            correct_huber, zero_huber
        ),
        "swap_sensitivity": swap_huber - correct_huber,
        "positive_task_count": int(run["gate"]["positive_task_count"]),
    }


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-017 Decision-Transition Behavioral Pilot",
        "",
        "## VERIFIED",
        "",
        f"- source commit: `{summary['source_commit']}`",
        f"- teacher gate: `{summary['teacher_validity_gate']['passed']}`",
        f"- decoder validation: `{summary['decoder_validation']['passed']}`",
        f"- pair-oracle gate: `{summary['pair_oracle']['gate']['passed']}`",
        f"- decision branch: `{summary['decision']['branch']}`",
        f"- transition granularity validated: `{summary['decision']['transition_granularity_validated']}`",
        "",
        "## Pair Oracle",
        "",
        "```json",
        json.dumps(summary["pair_oracle"]["gate"], indent=2, sort_keys=True),
        "```",
    ]
    if summary.get("static_transition"):
        lines.extend(
            [
                "",
                "## Static Transition Programs",
                "",
                "```json",
                json.dumps(
                    summary["static_transition"]["gate"], indent=2, sort_keys=True
                ),
                "```",
                "",
                "## Whole-Trajectory Baseline",
                "",
                "```json",
                json.dumps(
                    summary["trajectory_baseline"]["gate"], indent=2, sort_keys=True
                ),
                "```",
                "",
                "## Granularity",
                "",
                "```json",
                json.dumps(summary["granularity_advantage"], indent=2, sort_keys=True),
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## Hard Scope",
            "",
            "- Qwen and the u112 rank-128 linear decoder remained frozen.",
            "- No selector, compiler, full-bank field, Stage C2, generation, or AppWorld evaluation ran.",
            "- EXP-016D was not launched.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EXP-017 frozen-decoder transition behavior diagnostics."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_transition_memory_6a.yaml"),
    )
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--u112-checkpoint", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    behavior_started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6a"]
    teacher_summary_path = args.artifact_dir / "teacher_summary.json"
    if not teacher_summary_path.exists():
        raise FileNotFoundError("Teacher phase has not completed")
    teacher_summary = _load_json(teacher_summary_path)
    if teacher_summary.get("status") != "completed":
        raise ValueError("Teacher summary is not complete")
    teacher_rows = _load_rows(args.artifact_dir / "teacher_cache.jsonl")
    teacher_gate = _teacher_validity_gate(teacher_summary, teacher_rows)
    atomic_write_json(args.artifact_dir / "teacher_validity_gate.json", teacher_gate)
    if not teacher_gate["passed"]:
        raise RuntimeError(f"Transition teacher gate failed: {teacher_gate}")
    teacher_length_stratification = _teacher_length_stratification(
        teacher_rows
    )
    atomic_write_json(
        args.artifact_dir / "teacher_length_stratification.json",
        teacher_length_stratification,
    )

    pair_response_rows = _adapt_response_rows(
        _load_rows(
            args.artifact_dir / "pair_oracle_response_cache" / "response_cache.jsonl"
        )
    )
    static_manifest = _load_json(args.artifact_dir / "static_transition_manifest.json")
    static_identity_ids = [str(value) for value in static_manifest["transition_ids"]]
    static_response_rows = _adapt_response_rows(
        _load_rows(
            args.artifact_dir
            / "static_transition_response_cache"
            / "response_cache.jsonl"
        ),
        identity_ids=static_identity_ids,
    )
    trajectory_response_source = _load_rows(
        args.artifact_dir
        / "trajectory_baseline_response_cache"
        / "response_cache.jsonl"
    )
    selected_parent_ids = [str(value) for value in static_manifest["parent_memory_ids"]]
    trajectory_response_rows = _adapt_response_rows(
        trajectory_response_source, identity_ids=selected_parent_ids
    )
    trajectory_identity_ids = sorted(
        {str(row["memory_id"]) for row in trajectory_response_rows}
    )
    excluded_trajectory_parents = sorted(
        set(selected_parent_ids) - set(trajectory_identity_ids)
    )
    if len(pair_response_rows) != 64:
        raise ValueError(f"Pair response cache has {len(pair_response_rows)} rows, expected 64")
    if len(static_identity_ids) != 24:
        raise ValueError("Static transition manifest does not contain 24 transitions")

    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    device = next(backend.model.parameters()).device
    model_dim = int(getattr(backend.model.config, "hidden_size"))
    context_limit = _context_limit_for_backend(backend)
    if context_limit != int(settings["context_limit"]):
        raise ValueError("Runtime context limit differs from teacher preflight")
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    prompt_profile = cfg.benchmark.prompt_profile
    pair_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        response_rows=pair_response_rows,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
    )
    static_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        response_rows=static_response_rows,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
    )
    trajectory_rows = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        response_rows=trajectory_response_rows,
        prompt_profile=prompt_profile,
        context_limit=context_limit,
    )
    decoder, decoder_validation = _load_frozen_decoder(
        checkpoint=args.u112_checkpoint,
        model_dim=model_dim,
        device=device,
        output_dir=args.artifact_dir,
    )
    decoder_hash = module_state_sha256(decoder)
    objective = OBJECTIVES_5FA[str(settings["objective"]["name"])]
    if abs(
        float(objective.sparse_teacher_kl_weight)
        - float(settings["objective"]["sparse_teacher_kl_weight"])
    ) > 1.0e-12:
        raise ValueError("Configured sparse KL weight differs from fixed objective")
    learning_rate = float(settings["optimization"]["latent_learning_rate"])
    max_gradient_norm = float(settings["optimization"]["max_gradient_norm"])
    seed = int(settings["seed"])

    pair_oracle = _pair_oracle_run(
        backend=backend,
        decoder=decoder,
        rows=pair_rows,
        device=device,
        model_dim=model_dim,
        objective=objective,
        learning_rate=learning_rate,
        max_gradient_norm=max_gradient_norm,
        seed=seed + 1,
        output_dir=args.artifact_dir / "behavior" / "pair_oracle",
    )
    conditional_direct = None
    static_transition = None
    trajectory_baseline = None
    granularity = None
    if not pair_oracle["gate"]["passed"]:
        conditional_direct = _conditional_direct_oracle(
            backend=backend,
            rows=pair_rows,
            device=device,
            model_dim=model_dim,
            objective=objective,
            seed=seed + 2,
            output_dir=args.artifact_dir / "behavior" / "conditional_direct_oracle",
        )
    else:
        static_train = [row for row in static_rows if row["split"] == "train"]
        static_validation = [
            row for row in static_rows if row["split"] == "validation"
        ]
        trajectory_train = [
            row for row in trajectory_rows if row["split"] == "train"
        ]
        trajectory_validation = [
            row for row in trajectory_rows if row["split"] == "validation"
        ]
        teacher_runtime_s = float(teacher_summary["runtime_s"])
        static_transition = _run_static_identity_model(
            name="static_transition_latent",
            backend=backend,
            decoder=decoder,
            train_rows=static_train,
            validation_rows=static_validation,
            identity_ids=static_identity_ids,
            device=device,
            model_dim=model_dim,
            objective=objective,
            learning_rate=learning_rate,
            max_gradient_norm=max_gradient_norm,
            seed=seed + 3,
            output_dir=args.artifact_dir / "behavior" / "static_transition",
            teacher_runtime_s=teacher_runtime_s,
            behavior_started=behavior_started,
        )
        if len(trajectory_identity_ids) < 2:
            raise RuntimeError("Fewer than two parent trajectories have valid baseline rows")
        trajectory_baseline = _run_static_identity_model(
            name="static_whole_trajectory_latent",
            backend=backend,
            decoder=decoder,
            train_rows=trajectory_train,
            validation_rows=trajectory_validation,
            identity_ids=trajectory_identity_ids,
            device=device,
            model_dim=model_dim,
            objective=objective,
            learning_rate=learning_rate,
            max_gradient_norm=max_gradient_norm,
            seed=seed + 4,
            output_dir=args.artifact_dir / "behavior" / "trajectory_baseline",
            teacher_runtime_s=teacher_runtime_s,
            behavior_started=behavior_started,
        )
        granularity = granularity_advantage(
            _static_comparison_payload(static_transition),
            _static_comparison_payload(trajectory_baseline),
        )
        atomic_write_json(
            args.artifact_dir / "behavior" / "granularity_advantage.json",
            granularity,
        )

    decision = transition_pilot_decision(
        teacher_valid=teacher_gate["passed"],
        pair_oracle_passed=pair_oracle["gate"]["passed"],
        direct_oracle_passed=(
            None if conditional_direct is None else conditional_direct["gate"]["passed"]
        ),
        static_transition_passed=(
            None if static_transition is None else static_transition["gate"]["passed"]
        ),
        granularity_passed=None if granularity is None else granularity["passed"],
    )
    final_decoder_hash = module_state_sha256(decoder)
    if final_decoder_hash != decoder_hash:
        raise RuntimeError("Frozen decoder changed during EXP-017")
    summary = {
        "format": "decision_transition_behavior_pilot_6a_v1",
        "status": "completed",
        "timestamp_utc": utc_now(),
        "source_commit": maybe_git_commit(),
        "artifact_dir": str(args.artifact_dir),
        "teacher_summary": str(teacher_summary_path),
        "teacher_validity_gate": teacher_gate,
        "teacher_length_stratification": teacher_length_stratification,
        "decoder_validation": decoder_validation,
        "pair_oracle": pair_oracle,
        "conditional_direct_oracle": conditional_direct,
        "static_transition": static_transition,
        "trajectory_baseline": trajectory_baseline,
        "granularity_advantage": granularity,
        "decision": decision,
        "excluded_trajectory_parent_ids_without_valid_whole_trajectory_labels": excluded_trajectory_parents,
        "runtime_s": time.perf_counter() - behavior_started,
        "decoder_initial_sha256": decoder_hash,
        "decoder_final_sha256": final_decoder_hash,
        "hard_scope": {
            "qwen_frozen": True,
            "signed_selector_used_or_trained": False,
            "content_compiler_used_or_trained": False,
            "full_bank_field_trained": False,
            "appworld_generation_or_evaluation": False,
            "stage_c2_started": False,
            "end_to_end_rcmf_started": False,
            "exp016d_launched": False,
            "injection_position": "last_user_k",
            "k": K_TOKENS,
            "decoder": "frozen_exp016c_u112_uncentered_svd_rank128_linear_no_bias",
        },
    }
    atomic_write_json(args.artifact_dir / "behavior_summary.json", summary)
    atomic_write_text(args.artifact_dir / "behavior_report.md", _report(summary))
    print(json.dumps(decision, indent=2, sort_keys=True), flush=True)
    print(
        f"completed EXP-017 behavior in {summary['runtime_s'] / 3600.0:.3f}h",
        flush=True,
    )


if __name__ == "__main__":
    main()
