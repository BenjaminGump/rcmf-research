from __future__ import annotations

import argparse
from collections import defaultdict
import gc
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.oracle_convergence_5fa import (
    ConvergenceObjective,
    atomic_torch_save,
    update_count_summary,
)
from rcmf.training.oracle_decoder_5fc import (
    LinearDeltaDecoder,
    module_state_sha256,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import (
    GLOBAL_SEED,
    continuation_decision,
    differentiable_ratio_projection,
    factorized_behavior_gate,
    pairmlp_behavior_gate,
    require_global_seed,
    seed_everything,
)
from rcmf.training.state_conditioned_program_fast_7df import (
    FactorizedProgramFast,
    PairMLPProgramFast,
    transition_boundary_invariance,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.prepare_state_conditioned_program_7d import _context_builder
from scripts.run_stage_c_oracle_capacity_5e import (
    _collate,
    _precompute_direct_base_norms,
)
from scripts.run_stage_c_oracle_convergence_5fa import (
    _evaluate_direct_tensor,
    _training_loss,
)
from scripts.run_state_conditioned_program_fast_7df import (
    K_TOKENS,
    LATENT_DIM,
    _build_backend,
    _build_teacher_cache,
    _file_rows,
    _row_file,
    _student_forward,
    _validate_cached_teacher_row,
)
from scripts.run_transition_behavior_6a import _build_tokenized_rows
from rcmf.training.datasets import load_decision_examples


PAIRMLP_NAME = "pair_mlp_observation_excluded"
FACTORIZED_NAME = "full_factorized_r16_observation_excluded"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_direct_7dg.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg")
    parser.add_argument(
        "--stop-after",
        choices=("teacher", "pairmlp", "factorized"),
        default="factorized",
    )
    return parser.parse_args()


def _settings_paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_fast = Path(str(settings["parent_exp025df"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "preflight": artifact_dir / "preflight_summary.json",
        "a_split": artifact_dir / "preflight/a_task_split.json",
        "teacher_reuse": artifact_dir / "preflight/teacher_cache_reuse.json",
        "parent_teacher_rows": parent_fast / "teacher_cache/rows",
        "clean_decoder": parent_fast / "decoder/repaired_rank128_decoder.pt",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": Path(str(settings["parent_exp025b"]))
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        **{
            f"pairs_{cell}": artifact_dir / f"preflight/pairs_{cell}.jsonl"
            for cell in "ABCDE"
        },
    }


def _load_manifests(paths: Mapping[str, Path]) -> dict[str, list[dict[str, Any]]]:
    manifests = {cell: _rows(paths[f"pairs_{cell}"]) for cell in "ABCDE"}
    for cell, rows in manifests.items():
        if len(rows) != len({str(row["pair_id"]) for row in rows}):
            raise ValueError(f"Cell {cell} contains duplicate pair IDs")
    return manifests


def _copy_reusable_teacher_rows(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
    paths: Mapping[str, Path],
    pairs: Mapping[str, Mapping[str, Any]],
) -> int:
    reuse = _json(paths["teacher_reuse"])
    destination = artifact_dir / "teacher_cache/rows"
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for pair_id in reuse["reusable_pair_ids"]:
        pair = pairs[str(pair_id)]
        source = _row_file(paths["parent_teacher_rows"], str(pair_id))
        row = _json(source)
        _validate_cached_teacher_row(row, pair, settings)
        target = _row_file(destination, str(pair_id))
        if target.exists():
            _validate_cached_teacher_row(_json(target), pair, settings)
        else:
            atomic_write_json(target, row)
        copied += 1
    return copied


def _load_representations(paths: Mapping[str, Path], device: torch.device) -> dict[str, Any]:
    state = torch.load(paths["state_cache"], map_location="cpu", weights_only=False)
    transition = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    return {
        "state_values": state["representations"]["final_layer"].to(torch.float32),
        "transition_values": transition["representations"]["final_layer"].to(
            torch.float32
        ),
        "state_position": {
            str(value): index for index, value in enumerate(state["ordered_ids"])
        },
        "transition_position": {
            str(value): index for index, value in enumerate(transition["ordered_ids"])
        },
        "transition_view_names": list(transition["view_names"]),
        "device": device,
    }


def _private_decoder(
    *,
    path: Path,
    expected_state_sha256: str,
    model_dim: int,
    device: torch.device,
) -> tuple[LinearDeltaDecoder, str]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * int(model_dim)).to(device)
    decoder.load_state_dict(payload["decoder_state_dict"])
    if decoder.linear.bias is not None:
        raise ValueError("Direct behavioral decoder must remain no-bias")
    initial_hash = module_state_sha256(decoder)
    if initial_hash != str(expected_state_sha256):
        raise ValueError("Clean decoder initialization hash differs")
    decoder.train()
    for parameter in decoder.parameters():
        parameter.requires_grad_(True)
    return decoder, initial_hash


def _model(
    name: str,
    *,
    settings: Mapping[str, Any],
    transition_view_names: Sequence[str],
    device: torch.device,
) -> nn.Module:
    seed_everything(int(settings["global_seed"]))
    values = settings["program"]
    common = {
        "state_vector_count": int(values["state_vector_count"]),
        "transition_view_names": transition_view_names,
        "representation_dim": int(values["representation_dim"]),
        "program_dim": int(values["program_dim"]),
        "hidden_dim": int(values["hidden_dim"]),
        "dropout": float(values["dropout"]),
    }
    if name == PAIRMLP_NAME:
        return PairMLPProgramFast(**common).to(device)
    if name == FACTORIZED_NAME:
        return FactorizedProgramFast(
            **common,
            controller_rank=int(values["controller_rank"]),
            include_outcome=False,
        ).to(device)
    raise ValueError(f"Unknown direct behavioral model: {name}")


def _objective(settings: Mapping[str, Any], name: str) -> ConvergenceObjective:
    values = settings["program"]
    return ConvergenceObjective(
        name=f"direct_{name}_sequence_utility_sparse_kl_target_delta",
        target_delta_weight=float(values["target_delta_huber_weight"]),
        sequence_utility_weight=float(values["sequence_utility_weight"]),
        sparse_teacher_kl_weight=float(values["sparse_teacher_kl_weight"]),
        huber_delta=float(values["sequence_huber_delta"]),
    )


def _pair_indices(
    rows: Sequence[Mapping[str, Any]], representations: Mapping[str, Any]
) -> tuple[list[int], list[int]]:
    return (
        [
            int(representations["state_position"][str(row["state_example_id"])])
            for row in rows
        ],
        [
            int(representations["transition_position"][str(row["transition_id"])])
            for row in rows
        ],
    )


def _preference_partners(
    rows: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]
) -> dict[int, tuple[int, float, float]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row["state_example_id"])].append(index)
    minimum = float(settings["program"]["preference_minimum_gap"])
    cap = float(settings["program"]["preference_margin_cap"])
    output = {}
    for indices in grouped.values():
        for index in indices:
            utility = float(rows[index]["response_cache"]["text_utility"])
            candidates = [
                partner
                for partner in indices
                if partner != index
                and abs(
                    utility
                    - float(rows[partner]["response_cache"]["text_utility"])
                )
                >= minimum
            ]
            if not candidates:
                continue
            partner = max(
                candidates,
                key=lambda value: (
                    abs(
                        utility
                        - float(rows[value]["response_cache"]["text_utility"])
                    ),
                    stable_key(
                        GLOBAL_SEED,
                        "direct-preference-partner",
                        rows[index]["pair_id"],
                        rows[value]["pair_id"],
                    ),
                ),
            )
            gap = utility - float(rows[partner]["response_cache"]["text_utility"])
            output[index] = (partner, 1.0 if gap > 0.0 else -1.0, min(abs(gap), cap))
    return output


def _applied_delta(
    *,
    decoder: LinearDeltaDecoder,
    z: Tensor,
    base_norms: Tensor,
) -> tuple[Tensor, dict[str, Tensor], Tensor]:
    raw = decoder(z).view(len(z), K_TOKENS, -1)
    projected, ratio = differentiable_ratio_projection(
        raw, base_norms, maximum_ratio=1.0
    )
    return projected, ratio, raw


def _checkpoint_payload(
    *,
    name: str,
    model: nn.Module,
    decoder: LinearDeltaDecoder,
    optimizer: torch.optim.Optimizer,
    pair_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    initial_decoder_hash: str,
    split_sha256: str,
) -> dict[str, Any]:
    return {
        "format": "direct_behavior_program_checkpoint_7dg_v1",
        "model_name": name,
        "global_seed": GLOBAL_SEED,
        "pair_ids": list(pair_ids),
        "update_counts": [int(value) for value in update_counts],
        "update_accounting": update_count_summary(pair_ids, update_counts),
        "completed_rounds": int(completed_rounds),
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "decoder_state_dict": {
            key: value.detach().cpu() for key, value in decoder.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "initial_decoder_sha256": initial_decoder_hash,
        "current_decoder_sha256": module_state_sha256(decoder),
        "split_sha256": split_sha256,
        "source_commit": maybe_git_commit(),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _restore_rng(payload: Mapping[str, Any]) -> None:
    random.setstate(payload["python_random_state"])
    torch.set_rng_state(payload["torch_rng_state"].cpu())
    if torch.cuda.is_available() and payload.get("cuda_rng_state"):
        torch.cuda.set_rng_state_all(payload["cuda_rng_state"])


def _derangement(pair_ids: Sequence[str], namespace: str) -> list[int]:
    count = len(pair_ids)
    if count <= 1:
        return list(range(count))
    order = sorted(
        range(count),
        key=lambda index: stable_key(GLOBAL_SEED, namespace, pair_ids[index]),
    )
    permutation = list(range(count))
    for offset, index in enumerate(order):
        permutation[index] = order[(offset + 1) % count]
    return permutation


def _predict_latents(
    *,
    name: str,
    model: nn.Module,
    state_values: Tensor,
    transition_values: Tensor,
    pair_ids: Sequence[str],
    control: str,
    device: torch.device,
    batch_size: int = 16,
) -> Tensor:
    state_permutation = _derangement(pair_ids, f"{name}-{control}-state")
    transition_permutation = _derangement(pair_ids, f"{name}-{control}-transition")
    memory_swap = list(range(1, len(pair_ids))) + [0] if len(pair_ids) > 1 else [0]
    output = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(pair_ids), batch_size):
            stop = min(len(pair_ids), start + batch_size)
            indices = list(range(start, stop))
            states = state_values[indices].to(device)
            transitions = transition_values[indices].to(device)
            if control == "state_shuffle":
                states = state_values[state_permutation[start:stop]].to(device)
            elif control == "transition_shuffle":
                transitions = transition_values[transition_permutation[start:stop]].to(device)
            elif control == "memory_swap":
                transitions = transition_values[memory_swap[start:stop]].to(device)
            if name == FACTORIZED_NAME and control in {"static_only", "conditional_only"}:
                components = model.components(states, transitions)
                z = components["static" if control == "static_only" else "conditional"]
            else:
                z = model(states, transitions)
            output.append(z.cpu())
    correct = torch.cat(output).to(device)
    if control == "zero":
        return torch.zeros_like(correct)
    if control == "matched_random":
        generator = torch.Generator(device="cpu").manual_seed(GLOBAL_SEED)
        random_values = torch.randn(correct.shape, generator=generator).to(device)
        random_values = F.normalize(random_values, dim=1)
        return random_values * correct.norm(dim=1, keepdim=True)
    return correct


def _evaluate_controls(
    *,
    name: str,
    model: nn.Module,
    decoder: LinearDeltaDecoder,
    rows: Sequence[dict[str, Any]],
    representations: Mapping[str, Any],
    backend: Any,
    settings: Mapping[str, Any],
    output_dir: Path,
    controls: Sequence[str],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    device = backend.device
    pair_ids = [str(row["pair_id"]) for row in rows]
    state_indices, transition_indices = _pair_indices(rows, representations)
    states = representations["state_values"][state_indices]
    transitions = representations["transition_values"][transition_indices]
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=K_TOKENS
    ).to(device)
    result = {}
    for control in controls:
        z = _predict_latents(
            name=name,
            model=model,
            state_values=states,
            transition_values=transitions,
            pair_ids=pair_ids,
            control=control,
            device=device,
        )
        with torch.no_grad():
            delta, ratio, _ = _applied_delta(
                decoder=decoder, z=z, base_norms=base_norms
            )
        if float(ratio["maximum_ratio"].cpu()) > 1.0001:
            raise RuntimeError("Evaluation ratio exceeds 1.0")
        evaluation = _evaluate_direct_tensor(
            backend=backend,
            rows=rows,
            delta_tensor=delta,
            pair_ids=pair_ids,
            device=device,
            k=K_TOKENS,
            batch_size=1,
            huber_delta=float(settings["program"]["sequence_huber_delta"]),
            control=f"{name}_{control}",
        )
        path = output_dir / f"{control}_rows.jsonl"
        write_jsonl(path, evaluation["rows"])
        result[control] = {
            "summary": evaluation["summary"],
            "rows_path": str(path),
            "rows_sha256": sha256_file(path),
        }
    return result


def _train_model(
    *,
    name: str,
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    representations: Mapping[str, Any],
    train_rows: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, Any]],
    split_sha256: str,
    output_dir: Path,
    attempt: AttemptLedger,
) -> tuple[nn.Module, LinearDeltaDecoder, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    device = backend.device
    model = _model(
        name,
        settings=settings,
        transition_view_names=representations["transition_view_names"],
        device=device,
    )
    decoder, initial_decoder_hash = _private_decoder(
        path=paths["clean_decoder"],
        expected_state_sha256=str(settings["expected_clean_decoder_sha256"]),
        model_dim=int(backend.model.config.hidden_size),
        device=device,
    )
    optimizer = torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(settings["program"]["program_learning_rate"]),
            },
            {
                "params": list(decoder.parameters()),
                "lr": float(settings["program"]["decoder_learning_rate"]),
            },
        ],
        weight_decay=float(settings["program"]["weight_decay"]),
    )
    pair_ids = [str(row["pair_id"]) for row in train_rows]
    state_indices, transition_indices = _pair_indices(train_rows, representations)
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=train_rows, device=device, k=K_TOKENS
    ).to(device)
    preference = _preference_partners(train_rows, settings)
    utility_cache: dict[int, float] = {}
    update_counts = [0] * len(train_rows)
    history: list[dict[str, Any]] = []
    completed = 0
    latest = output_dir / "latest_checkpoint.json"
    if latest.exists():
        payload = torch.load(
            Path(str(_json(latest)["checkpoint"])),
            map_location=device,
            weights_only=False,
        )
        checks = {
            "name": str(payload["model_name"]) == name,
            "seed": int(payload["global_seed"]) == GLOBAL_SEED,
            "pair_ids": list(payload["pair_ids"]) == pair_ids,
            "split": str(payload["split_sha256"]) == split_sha256,
            "decoder_init": str(payload["initial_decoder_sha256"])
            == initial_decoder_hash,
        }
        if not all(checks.values()):
            raise ValueError(f"Direct model resume identity differs: {checks}")
        model.load_state_dict(payload["model_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        history = list(payload["history"])
        completed = int(payload["completed_rounds"])
        _restore_rng(payload)
    objective = _objective(settings, name)
    checkpoints: dict[int, Path] = {}
    started = time.perf_counter()
    for update_round in range(completed + 1, 17):
        order = sorted(
            range(len(train_rows)),
            key=lambda index: stable_key(
                GLOBAL_SEED,
                f"{name}-training-round-{update_round}",
                pair_ids[index],
            ),
        )
        losses = []
        preference_terms = 0
        maximum_applied_ratio = 0.0
        model.train()
        decoder.train()
        for index in order:
            batch = _collate([train_rows[index]], device=device, k=K_TOKENS)
            state = representations["state_values"][state_indices[index]].unsqueeze(0).to(device)
            transition = representations["transition_values"][transition_indices[index]].unsqueeze(0).to(device)
            z = model(state, transition)
            delta, ratio, raw_delta = _applied_delta(
                decoder=decoder, z=z, base_norms=base_norms[index : index + 1]
            )
            student = _student_forward(
                backend=backend, batch=batch, delta=delta, prefix_enabled=False
            )
            loss, terms = _training_loss(
                logits=student["target_logits"], batch=batch, objective=objective
            )
            utility = float(train_rows[index]["response_cache"]["text_utility"])
            category = str(train_rows[index]["response_cache"]["utility_category"])
            preservation_weight = 0.0
            if category == "neutral":
                preservation_weight = float(
                    settings["program"]["neutral_preservation_weight"]
                )
            elif utility < 0.0:
                preservation_weight = float(
                    settings["program"]["harmful_preservation_weight"]
                )
            loss = loss + preservation_weight * terms["student_utility"].pow(2).mean()
            if index in preference and preference[index][0] in utility_cache:
                partner, direction, margin = preference[index]
                partner_value = torch.tensor(
                    utility_cache[partner], device=device, dtype=terms["student_utility"].dtype
                )
                preference_loss = F.relu(
                    float(margin)
                    - float(direction)
                    * (terms["student_utility"].mean() - partner_value)
                )
                loss = loss + float(settings["program"]["preference_weight"]) * preference_loss
                preference_terms += 1
            raw_ratio = raw_delta.to(torch.float32).flatten(start_dim=1).norm(dim=1) / base_norms[index : index + 1].clamp_min(1.0e-12)
            loss = loss + float(settings["program"]["ratio_restraint_weight"]) * (
                F.relu(raw_ratio - 1.0).pow(2).mean() + 0.01 * z.pow(2).mean()
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                list(model.parameters()) + list(decoder.parameters()),
                float(settings["program"]["max_grad_norm"]),
            )
            optimizer.step()
            update_counts[index] += 1
            utility_cache[index] = float(terms["student_utility"].detach().mean().cpu())
            losses.append(float(loss.detach().cpu()))
            maximum_applied_ratio = max(
                maximum_applied_ratio, float(ratio["maximum_ratio"].detach().cpu())
            )
            if maximum_applied_ratio > 1.0001:
                raise RuntimeError("Training perturbation ratio exceeds 1.0")
        accounting = update_count_summary(pair_ids, update_counts)
        if not accounting["all_pairs_equal"] or int(
            accounting["minimum_updates_per_pair"]
        ) != update_round:
            raise RuntimeError(f"Unequal direct updates after u{update_round}")
        if update_round not in {8, 16}:
            continue
        controls = (
            ("correct", "state_shuffle", "transition_shuffle", "zero")
            if name == PAIRMLP_NAME
            else (
                "correct",
                "static_only",
                "conditional_only",
                "state_shuffle",
                "transition_shuffle",
                "memory_swap",
                "zero",
                "matched_random",
            )
        )
        validation = _evaluate_controls(
            name=name,
            model=model,
            decoder=decoder,
            rows=validation_rows,
            representations=representations,
            backend=backend,
            settings=settings,
            output_dir=output_dir / f"a_validation_u{update_round:02d}",
            controls=controls,
        )
        entry = {
            "updates_per_pair": update_round,
            "mean_training_loss": statistics.fmean(losses),
            "preference_term_count": preference_terms,
            "maximum_applied_ratio": maximum_applied_ratio,
            "update_accounting": accounting,
            "a_validation": {
                control: value["summary"] for control, value in validation.items()
            },
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint = output_dir / "checkpoints" / f"model_u{update_round:02d}.pt"
        atomic_torch_save(
            _checkpoint_payload(
                name=name,
                model=model,
                decoder=decoder,
                optimizer=optimizer,
                pair_ids=pair_ids,
                update_counts=update_counts,
                completed_rounds=update_round,
                history=history,
                initial_decoder_hash=initial_decoder_hash,
                split_sha256=split_sha256,
            ),
            checkpoint,
        )
        checkpoints[update_round] = checkpoint
        atomic_write_json(
            latest, {"checkpoint": str(checkpoint), "updates_per_pair": update_round}
        )
        attempt.progress(
            status=f"direct_{name}_u{update_round}",
            latest_validated_checkpoint=str(checkpoint),
            updates_per_pair=update_round,
        )
        print(
            f"direct {name} u{update_round} val_rho="
            f"{validation['correct']['summary'].get('u_text_vs_u_student_spearman')} "
            f"val_huber={validation['correct']['summary']['sequence_utility_huber']['mean']:.6f}",
            flush=True,
        )
    if len(history) != 2 or [int(row["updates_per_pair"]) for row in history[-2:]] != [8, 16]:
        raise RuntimeError("Direct training did not produce u8 and u16 checkpoints")
    continuation = continuation_decision(
        history[-2]["a_validation"]["correct"],
        history[-1]["a_validation"]["correct"],
    )
    train_improvement = (
        float(history[-2]["mean_training_loss"])
        - float(history[-1]["mean_training_loss"])
    ) / max(abs(float(history[-2]["mean_training_loss"])), 1.0e-12)
    memorization_collapse = bool(
        train_improvement >= 0.05
        and float(continuation["relative_huber_improvement"]) < 0.0
    )
    continuation["train_loss_relative_improvement"] = train_improvement
    continuation["obvious_memorization_collapse"] = memorization_collapse
    if memorization_collapse:
        continuation["select_u16"] = False
        continuation["selected_updates_per_pair"] = 8
    selected_updates = int(continuation["selected_updates_per_pair"])
    selected_checkpoint = checkpoints.get(selected_updates)
    if selected_checkpoint is None:
        selected_checkpoint = output_dir / "checkpoints" / f"model_u{selected_updates:02d}.pt"
    selected = torch.load(selected_checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(selected["model_state_dict"])
    decoder.load_state_dict(selected["decoder_state_dict"])
    model.eval()
    decoder.eval()
    summary = {
        "format": "direct_behavior_program_training_summary_7dg_v1",
        "model_name": name,
        "global_seed": GLOBAL_SEED,
        "train_pair_count": len(train_rows),
        "validation_pair_count": len(validation_rows),
        "preference_pair_count": len(preference),
        "history": history,
        "continuation": continuation,
        "selected_updates_per_pair": selected_updates,
        "selected_checkpoint": str(selected_checkpoint),
        "selected_checkpoint_sha256": sha256_file(selected_checkpoint),
        "initial_decoder_sha256": initial_decoder_hash,
        "trained_decoder_sha256": module_state_sha256(decoder),
        "model_sha256": module_state_sha256(model),
        "qwen_parameters_trainable": any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "student_prompt_contains_raw_transition": False,
        "maximum_applied_ratio": max(
            float(row["maximum_applied_ratio"]) for row in history
        ),
    }
    atomic_write_json(output_dir / "training_summary.json", summary)
    return model, decoder, summary


def _load_selected_model(
    *,
    name: str,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    representations: Mapping[str, Any],
    backend: Any,
    output_dir: Path,
) -> tuple[nn.Module, LinearDeltaDecoder, dict[str, Any]]:
    summary = _json(output_dir / "training_summary.json")
    model = _model(
        name,
        settings=settings,
        transition_view_names=representations["transition_view_names"],
        device=backend.device,
    )
    decoder, _ = _private_decoder(
        path=paths["clean_decoder"],
        expected_state_sha256=str(settings["expected_clean_decoder_sha256"]),
        model_dim=int(backend.model.config.hidden_size),
        device=backend.device,
    )
    payload = torch.load(
        Path(str(summary["selected_checkpoint"])),
        map_location=backend.device,
        weights_only=False,
    )
    model.load_state_dict(payload["model_state_dict"])
    decoder.load_state_dict(payload["decoder_state_dict"])
    model.eval()
    decoder.eval()
    return model, decoder, summary


def _final_cells(
    *,
    name: str,
    model: nn.Module,
    decoder: LinearDeltaDecoder,
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    tokenized_by_pair: Mapping[str, dict[str, Any]],
    split: Mapping[str, Any],
    representations: Mapping[str, Any],
    backend: Any,
    settings: Mapping[str, Any],
    output_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    controls = (
        ("correct", "state_shuffle", "transition_shuffle", "zero")
        if name == PAIRMLP_NAME
        else (
            "correct",
            "static_only",
            "conditional_only",
            "state_shuffle",
            "transition_shuffle",
            "memory_swap",
            "zero",
            "matched_random",
        )
    )
    cell_rows = {
        "A_validation": [
            tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
            for index in split["validation_indices"]
        ],
        **{
            cell: [tokenized_by_pair[str(row["pair_id"])] for row in manifests[cell]]
            for cell in "BCDE"
        },
    }
    evaluations = {}
    for cell, rows in cell_rows.items():
        values = _evaluate_controls(
            name=name,
            model=model,
            decoder=decoder,
            rows=rows,
            representations=representations,
            backend=backend,
            settings=settings,
            output_dir=output_dir / "evaluation" / cell,
            controls=controls,
        )
        evaluations[cell] = {
            control: result["summary"] for control, result in values.items()
        }
        attempt.progress(
            status=f"direct_{name}_final_evaluation",
            completed_cell=cell,
            latest_validated_checkpoint=str(output_dir / "evaluation" / cell),
        )
    gate = (
        pairmlp_behavior_gate(evaluations)
        if name == PAIRMLP_NAME
        else factorized_behavior_gate(evaluations)
    )
    selector_hash = sha256_file(Path(str(settings["parent_exp025c"])) / "selector/ensemble_scores.pt")
    summary = {
        "format": "direct_behavior_program_final_evaluation_7dg_v1",
        "model_name": name,
        "global_seed": GLOBAL_SEED,
        "evaluations": evaluations,
        "gate": gate,
        "selector_sha256": selector_hash,
        "selector_unchanged": selector_hash
        == str(settings["expected_selector_ensemble_sha256"]),
        "passed": gate["passed"],
    }
    if name == FACTORIZED_NAME:
        a_rows = [
            tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
            for index in split["validation_indices"][: min(8, len(split["validation_indices"]))]
        ]
        state_indices, transition_indices = _pair_indices(a_rows, representations)
        summary["observation_invariance"] = transition_boundary_invariance(
            model,
            state_views=representations["state_values"][state_indices].to(backend.device),
            transition_views=representations["transition_values"][transition_indices].to(backend.device),
            observation_permutation=torch.arange(
                len(a_rows) - 1, -1, -1, device=backend.device
            ),
        )
    atomic_write_json(output_dir / "final_evaluation_summary.json", summary)
    return summary


def _write_report(
    *,
    artifact_dir: Path,
    pairmlp: Mapping[str, Any],
    factorized: Mapping[str, Any] | None,
    branch: str,
    started: float,
) -> None:
    payload = {
        "format": "state_conditioned_program_direct_gpu_summary_7dg_v1",
        "run_uuid": "state_conditioned_program_direct_7dg_20260821_001",
        "global_seed": GLOBAL_SEED,
        "pairmlp": pairmlp,
        "factorized": factorized,
        "decision_branch": branch,
        "teacher_forced_factorized_passed": bool(
            factorized and factorized.get("passed")
        ),
        "elapsed_h100_hours": (time.perf_counter() - started) / 3600.0,
    }
    atomic_write_json(artifact_dir / "direct_behavior_summary.json", payload)
    atomic_write_text(
        artifact_dir / "direct_behavior_report.md",
        "\n".join(
            [
                "# EXP-025D-Direct behavioral training",
                "",
                f"- global seed: `{GLOBAL_SEED}`",
                f"- PairMLP gate: `{pairmlp['passed']}`",
                f"- factorized gate: `{None if factorized is None else factorized['passed']}`",
                f"- decision branch: `{branch}`",
                "",
            ]
        ),
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7dg"]
    require_global_seed(int(settings["global_seed"]))
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _settings_paths(settings, args.artifact_dir)
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Expected H100 runtime exceeds the 18-hour review threshold")
    data_hashes = {
        name: sha256_file(path)
        for name, path in paths.items()
        if path.is_file()
    }
    started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="direct_pairmlp_then_conditional_factorized",
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
        manifests = _load_manifests(paths)
        split = _json(paths["a_split"])
        unique_pairs = {
            str(row["pair_id"]): dict(row)
            for rows in manifests.values()
            for row in rows
        }
        copied = _copy_reusable_teacher_rows(
            settings=settings,
            artifact_dir=args.artifact_dir,
            paths=paths,
            pairs=unique_pairs,
        )
        examples = load_decision_examples(paths["decisions"])
        transitions_list = _file_rows(paths["transitions"])
        transition_ids = {str(row["transition_id"]) for row in unique_pairs.values()}
        transitions = {
            str(row["transition_id"]): row
            for row in transitions_list
            if str(row["transition_id"]) in transition_ids
        }
        backend = _build_backend(cfg)
        if any(parameter.requires_grad for parameter in backend.model.parameters()):
            raise RuntimeError("Qwen parameters are not frozen")
        contexts, index_by_state = _context_builder(
            tokenizer=backend.tokenizer,
            examples=examples,
            prompt_profile=cfg.benchmark.prompt_profile,
        )
        ordered_pairs = [unique_pairs[key] for key in sorted(unique_pairs)]
        teacher_rows = _build_teacher_cache(
            backend=backend,
            cfg=cfg,
            settings=settings,
            artifact_dir=args.artifact_dir,
            examples=examples,
            contexts=contexts,
            index_by_state=index_by_state,
            transitions=transitions,
            ordered_transition_ids=[
                str(row["transition_id"]) for row in transitions_list
            ],
            pairs=ordered_pairs,
            attempt=attempt,
        )
        teacher_summary = _json(args.artifact_dir / "teacher_cache/summary.json")
        teacher_summary["reused_parent_rows"] = copied
        teacher_summary["new_rows"] = len(teacher_rows) - copied
        atomic_write_json(args.artifact_dir / "teacher_cache/summary.json", teacher_summary)
        tokenized = _build_tokenized_rows(
            backend=backend,
            examples=examples,
            response_rows=teacher_rows,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["teacher_cache"]["context_limit"]),
        )
        tokenized_by_pair = {str(row["pair_id"]): row for row in tokenized}
        if len(tokenized_by_pair) != len(unique_pairs):
            raise ValueError("Tokenized direct pair count differs")
        if args.stop_after == "teacher":
            return
        representations = _load_representations(paths, backend.device)
        train_rows = [
            tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
            for index in split["train_indices"]
        ]
        validation_rows = [
            tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
            for index in split["validation_indices"]
        ]
        pair_root = args.artifact_dir / "pairmlp"
        if (pair_root / "training_summary.json").exists():
            pair_model, pair_decoder, pair_training = _load_selected_model(
                name=PAIRMLP_NAME,
                settings=settings,
                paths=paths,
                representations=representations,
                backend=backend,
                output_dir=pair_root,
            )
        else:
            pair_model, pair_decoder, pair_training = _train_model(
                name=PAIRMLP_NAME,
                backend=backend,
                settings=settings,
                paths=paths,
                representations=representations,
                train_rows=train_rows,
                validation_rows=validation_rows,
                split_sha256=str(split["manifest_sha256"]),
                output_dir=pair_root,
                attempt=attempt,
            )
        pair_evaluation = _final_cells(
            name=PAIRMLP_NAME,
            model=pair_model,
            decoder=pair_decoder,
            manifests=manifests,
            tokenized_by_pair=tokenized_by_pair,
            split=split,
            representations=representations,
            backend=backend,
            settings=settings,
            output_dir=pair_root,
            attempt=attempt,
        )
        pair_payload = {"training": pair_training, "evaluation": pair_evaluation, "passed": pair_evaluation["passed"]}
        if not pair_evaluation["passed"]:
            _write_report(
                artifact_dir=args.artifact_dir,
                pairmlp=pair_payload,
                factorized=None,
                branch="direct_behavior_pair_upper_bound_failed",
                started=started,
            )
            raise RuntimeError("direct_behavior_pair_upper_bound_failed")
        if args.stop_after == "pairmlp":
            _write_report(
                artifact_dir=args.artifact_dir,
                pairmlp=pair_payload,
                factorized=None,
                branch="pairmlp_passed_factorized_not_run",
                started=started,
            )
            return
        del pair_model, pair_decoder
        gc.collect()
        torch.cuda.empty_cache()
        factor_root = args.artifact_dir / "factorized"
        if (factor_root / "training_summary.json").exists():
            factor_model, factor_decoder, factor_training = _load_selected_model(
                name=FACTORIZED_NAME,
                settings=settings,
                paths=paths,
                representations=representations,
                backend=backend,
                output_dir=factor_root,
            )
        else:
            factor_model, factor_decoder, factor_training = _train_model(
                name=FACTORIZED_NAME,
                backend=backend,
                settings=settings,
                paths=paths,
                representations=representations,
                train_rows=train_rows,
                validation_rows=validation_rows,
                split_sha256=str(split["manifest_sha256"]),
                output_dir=factor_root,
                attempt=attempt,
            )
        factor_evaluation = _final_cells(
            name=FACTORIZED_NAME,
            model=factor_model,
            decoder=factor_decoder,
            manifests=manifests,
            tokenized_by_pair=tokenized_by_pair,
            split=split,
            representations=representations,
            backend=backend,
            settings=settings,
            output_dir=factor_root,
            attempt=attempt,
        )
        factor_payload = {
            "training": factor_training,
            "evaluation": factor_evaluation,
            "passed": factor_evaluation["passed"],
        }
        branch = (
            "factorized_teacher_forced_passed_one_step_pending"
            if factor_evaluation["passed"]
            else "direct_behavior_factorized_program_failed"
        )
        _write_report(
            artifact_dir=args.artifact_dir,
            pairmlp=pair_payload,
            factorized=factor_payload,
            branch=branch,
            started=started,
        )
        if not factor_evaluation["passed"]:
            raise RuntimeError("direct_behavior_factorized_program_failed")


if __name__ == "__main__":
    main()
