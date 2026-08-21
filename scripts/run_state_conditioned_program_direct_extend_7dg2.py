from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.oracle_convergence_5fa import atomic_torch_save, update_count_summary
from rcmf.training.oracle_decoder_5fc import LinearDeltaDecoder, module_state_sha256
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_program_direct_extend_7dg2 import (
    EXTENSION_CHECKPOINTS,
    GLOBAL_SEED,
    PROGRAM_GAINS,
    calibration_audit,
    continuation_decision,
    factorized_extension_gate,
    runtime_projection,
    select_checkpoint,
    select_program_gain,
    validate_resume_checkpoint,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.datasets import load_decision_examples
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
)
from scripts.run_stage_c_oracle_capacity_5e import _collate, _precompute_direct_base_norms
from scripts.run_stage_c_oracle_convergence_5fa import _training_loss
from scripts.run_state_conditioned_program_direct_7dg import (
    FACTORIZED_NAME,
    _applied_delta,
    _checkpoint_payload,
    _evaluate_controls,
    _load_manifests,
    _load_representations,
    _model,
    _objective,
    _pair_indices,
    _preference_partners,
    _private_decoder,
    _restore_rng,
    _settings_paths,
)
from scripts.run_state_conditioned_program_fast_7df import (
    K_TOKENS,
    LATENT_DIM,
    _build_backend,
    _student_forward,
    _validate_cached_teacher_row,
)
from scripts.run_transition_behavior_6a import _build_tokenized_rows


RUN_UUID = "state_conditioned_program_direct_extend_7dg2_20260821_001"
CONTROLS = (
    "correct",
    "static_only",
    "state_shuffle",
    "transition_shuffle",
    "memory_swap",
    "zero",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _atomic_row_directory_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_dir():
        raise NotADirectoryError(path)
    row_paths = sorted(path.glob("*.json"))
    if not row_paths:
        raise ValueError(f"Atomic row directory is empty: {path}")
    return [_json(row_path) for row_path in row_paths]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_direct_extend_7dg2.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "train"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg2")
    return parser.parse_args()


def _paths(
    base: Mapping[str, Any], extension: Mapping[str, Any], artifact_dir: Path
) -> dict[str, Path]:
    parent = Path(str(extension["parent_run"]))
    parent_paths = _settings_paths(base, parent)
    return {
        **{f"parent_{name}": path for name, path in parent_paths.items()},
        "parent_run": parent,
        "parent_checkpoint": parent / "factorized/checkpoints/model_u16.pt",
        "parent_factor_training": parent / "factorized/training_summary.json",
        "parent_factor_u16_rows": parent
        / "factorized/a_validation_u16/correct_rows.jsonl",
        "parent_final_summary": parent / "final_exp025dg_summary.json",
        "parent_teacher_rows": parent / "teacher_cache/rows",
        "parent_teacher_summary": parent / "teacher_cache/summary.json",
        "run_manifest": artifact_dir / "run_manifest.json",
        "preflight": artifact_dir / "preflight_summary.json",
        "resume_integrity": artifact_dir / "resume_integrity.json",
        "calibration_audit": artifact_dir / "calibration_audit_u16.json",
        "runtime_preflight": artifact_dir / "runtime_preflight.json",
        "latest_checkpoint": artifact_dir / "factorized/latest_checkpoint.json",
        "training_summary": artifact_dir / "factorized/training_summary.json",
        "selection_summary": artifact_dir / "factorized/selection_summary.json",
        "teacher_forced_summary": artifact_dir / "teacher_forced_summary.json",
        "direct_summary": artifact_dir / "direct_behavior_summary.json",
    }


def _manifest_rows(paths: Mapping[str, Path]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    manifests = _load_manifests(
        {f"pairs_{cell}": paths[f"parent_pairs_{cell}"] for cell in "ABCDE"}
    )
    split = _json(paths["parent_a_split"])
    return manifests, split


def _training_pair_ids(
    manifests: Mapping[str, Sequence[Mapping[str, Any]]], split: Mapping[str, Any]
) -> list[str]:
    return [
        str(manifests["A"][int(index)]["pair_id"])
        for index in split["train_indices"]
    ]


def _data_hashes(paths: Mapping[str, Path], config: Path) -> dict[str, str]:
    names = {
        "config": config,
        "parent_checkpoint": paths["parent_checkpoint"],
        "parent_factor_training": paths["parent_factor_training"],
        "parent_final_summary": paths["parent_final_summary"],
        "parent_a_split": paths["parent_a_split"],
        "state_cache": paths["parent_state_cache"],
        "transition_cache": paths["parent_transition_cache"],
        "selector": paths["parent_selector"],
        **{f"pairs_{cell}": paths[f"parent_pairs_{cell}"] for cell in "ABCDE"},
    }
    missing = {name: str(path) for name, path in names.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-025D-G2 input: {missing}")
    return {name: sha256_file(path) for name, path in names.items()}


def _state_hashes(
    *, payload: Mapping[str, Any], base: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    transition_cache = torch.load(
        paths["parent_transition_cache"], map_location="cpu", weights_only=False
    )
    model = _model(
        FACTORIZED_NAME,
        settings=base,
        transition_view_names=transition_cache["view_names"],
        device=torch.device("cpu"),
    )
    model.load_state_dict(payload["model_state_dict"])
    decoder = LinearDeltaDecoder(LATENT_DIM, K_TOKENS * 4096)
    decoder.load_state_dict(payload["decoder_state_dict"])
    optimizer = torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(base["program"]["program_learning_rate"]),
            },
            {
                "params": list(decoder.parameters()),
                "lr": float(base["program"]["decoder_learning_rate"]),
            },
        ],
        weight_decay=float(base["program"]["weight_decay"]),
    )
    optimizer.load_state_dict(payload["optimizer_state_dict"])
    groups = optimizer.param_groups
    return {
        "model_sha256": module_state_sha256(model),
        "decoder_sha256": module_state_sha256(decoder),
        "optimizer_state_count": len(optimizer.state),
        "optimizer_group_count": len(groups),
        "program_learning_rate": float(groups[0]["lr"]),
        "decoder_learning_rate": float(groups[1]["lr"]),
        "weight_decay": [float(group["weight_decay"]) for group in groups],
    }


def _preflight(
    *,
    config: Path,
    base: Mapping[str, Any],
    extension: Mapping[str, Any],
    paths: Mapping[str, Path],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    if str(extension["run_uuid"]) != RUN_UUID:
        raise ValueError("EXP-025D-G2 run UUID differs")
    manifests, split = _manifest_rows(paths)
    pair_ids = _training_pair_ids(manifests, split)
    checkpoint_hash = sha256_file(paths["parent_checkpoint"])
    payload = torch.load(paths["parent_checkpoint"], map_location="cpu", weights_only=False)
    validation = validate_resume_checkpoint(
        payload,
        expected_pair_ids=pair_ids,
        expected_split_sha256=str(split["manifest_sha256"]),
        expected_initial_decoder_sha256=str(base["expected_clean_decoder_sha256"]),
        expected_source_commit=str(extension["expected_parent_checkpoint_source_commit"]),
    )
    parent_training = _json(paths["parent_factor_training"])
    state_hashes = _state_hashes(payload=payload, base=base, paths=paths)
    hash_checks = {
        "checkpoint_file_sha256": checkpoint_hash
        == str(extension["expected_parent_checkpoint_sha256"]),
        "model_state_sha256": state_hashes["model_sha256"]
        == str(parent_training["model_sha256"]),
        "decoder_state_sha256": state_hashes["decoder_sha256"]
        == str(parent_training["trained_decoder_sha256"]),
        "selector_sha256": sha256_file(paths["parent_selector"])
        == str(base["expected_selector_ensemble_sha256"]),
        "parent_final_summary_sha256": sha256_file(paths["parent_final_summary"])
        == str(extension["expected_parent_final_summary_sha256"]),
        "program_learning_rate": state_hashes["program_learning_rate"]
        == float(base["program"]["program_learning_rate"]),
        "decoder_learning_rate": state_hashes["decoder_learning_rate"]
        == float(base["program"]["decoder_learning_rate"]),
        "optimizer_state_count": int(state_hashes["optimizer_state_count"]) > 0,
    }
    integrity = {
        "format": "factorized_program_resume_integrity_7dg2_v1",
        "parent_run_uuid": str(extension["parent_run_uuid"]),
        "parent_checkpoint": str(paths["parent_checkpoint"]),
        "parent_checkpoint_sha256": checkpoint_hash,
        "checkpoint_contract": validation,
        "hash_and_optimizer_checks": hash_checks,
        "state_hashes": state_hashes,
        "pair_count": len(pair_ids),
        "pair_order_sha256": canonical_sha256(pair_ids),
        "completed_updates_per_pair": 16,
        "transient_preference_cache_resume_contract": (
            "The v1 source checkpoint did not serialize its detached preference utility "
            "cache. As in the existing source resume path, this derived cache starts empty; "
            "all required parameters, Adam state, RNG state, pair order, and counts restore exactly."
        ),
        "passed": validation["passed"] and all(hash_checks.values()),
    }
    if not integrity["passed"]:
        raise ValueError(f"Parent factorized checkpoint integrity failed: {integrity}")
    atomic_write_json(paths["resume_integrity"], integrity)

    saved_rows = _rows(paths["parent_factor_u16_rows"])
    audit = calibration_audit(saved_rows)
    audit.update(
        {
            "source_rows": str(paths["parent_factor_u16_rows"]),
            "source_rows_sha256": sha256_file(paths["parent_factor_u16_rows"]),
            "gpu_forward_count": 0,
        }
    )
    atomic_write_json(paths["calibration_audit"], audit)

    history = list(parent_training["history"])
    measured_segment = float(history[-1]["elapsed_seconds"]) - float(
        history[-2]["elapsed_seconds"]
    )
    parent_preflight = _json(paths["parent_preflight"])
    checkpoint_bytes = paths["parent_checkpoint"].stat().st_size
    runtime = runtime_projection(
        measured_u8_to_u16_seconds=measured_segment,
        a_validation_pairs=int(split["validation_pair_count"]),
        final_cell_pairs=sum(
            int(parent_preflight["cell_pair_counts"][cell]) for cell in "BCDE"
        ),
        one_step_conditions=180,
        checkpoint_bytes=checkpoint_bytes,
        rates=extension["runtime"]["rates"],
    )
    expected_hours = float(
        runtime["scenarios"]["expected"]["maximum_total_additional_h100_hours"]
    )
    runtime.update(
        {
            "review_threshold_h100_hours": float(
                extension["review_threshold_h100_hours"]
            ),
            "automatic_launch_allowed": expected_hours
            <= float(extension["review_threshold_h100_hours"]),
            "projected_artifact_bytes": 3 * checkpoint_bytes
            + 450_000_000,
        }
    )
    atomic_write_json(paths["runtime_preflight"], runtime)
    data_hashes = _data_hashes(paths, config)
    run_manifest = {
        "format": "resumable_experiment_run_manifest_v1",
        "run_uuid": RUN_UUID,
        "initial_source_commit": maybe_git_commit(),
        "config_sha256": sha256_file(config),
        "data_manifest_hashes": data_hashes,
        "parent_run_uuid": str(extension["parent_run_uuid"]),
        "parent_checkpoint_sha256": checkpoint_hash,
        "global_seed": GLOBAL_SEED,
        "command_scope": ["preflight", "u32", "u48", "u64", "calibration", "BCDE", "H1_H4", "finalize"],
    }
    if paths["run_manifest"].exists() and _json(paths["run_manifest"]) != run_manifest:
        raise ValueError("Existing EXP-025D-G2 run manifest differs")
    atomic_write_json(paths["run_manifest"], run_manifest)
    teacher_contract = {
        "model_name": str(base["teacher_cache"]["model_name"]),
        "dtype": str(base["teacher_cache"]["dtype"]),
        "prompt_profile": str(base["teacher_cache"]["prompt_profile"]),
        "renderer_version": str(base["teacher_cache"]["renderer_version"]),
        "context_limit": int(base["teacher_cache"]["context_limit"]),
    }
    summary = {
        "format": "factorized_program_extension_preflight_7dg2_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "parent_resume_integrity": integrity,
        "calibration_audit": audit,
        "runtime_projection": runtime,
        "pair_counts": parent_preflight["cell_pair_counts"],
        "a_train_pairs": int(split["train_pair_count"]),
        "a_validation_pairs": int(split["validation_pair_count"]),
        "teacher_contract": teacher_contract,
        "teacher_contract_sha256": canonical_sha256(teacher_contract),
        "automatic_launch_allowed": bool(runtime["automatic_launch_allowed"]),
        "gpu_used": False,
        "passed": integrity["passed"] and bool(runtime["automatic_launch_allowed"]),
    }
    atomic_write_json(paths["preflight"], summary)
    attempt.progress(
        status="completed_ready_for_exact_u16_resume",
        latest_validated_checkpoint=str(paths["preflight"]),
    )
    return summary


def _load_teacher_rows(
    *,
    base: Mapping[str, Any],
    paths: Mapping[str, Path],
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    pairs = {
        str(row["pair_id"]): dict(row)
        for rows in manifests.values()
        for row in rows
    }
    rows = _atomic_row_directory_rows(paths["parent_teacher_rows"])
    row_pair_ids = [str(row["pair_id"]) for row in rows]
    if len(row_pair_ids) != len(set(row_pair_ids)):
        raise ValueError("Parent teacher cache contains duplicate pair IDs")
    by_pair = {str(row["pair_id"]): row for row in rows}
    if set(by_pair) != set(pairs):
        raise ValueError("Parent teacher row keys differ from the G2 pair union")
    for pair_id, pair in pairs.items():
        _validate_cached_teacher_row(by_pair[pair_id], pair, base)
    return [by_pair[pair_id] for pair_id in sorted(by_pair)]


def _optimizer(
    *, model: torch.nn.Module, decoder: LinearDeltaDecoder, base: Mapping[str, Any]
) -> torch.optim.AdamW:
    return torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(base["program"]["program_learning_rate"]),
            },
            {
                "params": list(decoder.parameters()),
                "lr": float(base["program"]["decoder_learning_rate"]),
            },
        ],
        weight_decay=float(base["program"]["weight_decay"]),
    )


def _load_training_state(
    *,
    base: Mapping[str, Any],
    extension: Mapping[str, Any],
    paths: Mapping[str, Path],
    representations: Mapping[str, Any],
    pair_ids: Sequence[str],
    split_sha256: str,
    device: torch.device,
) -> tuple[torch.nn.Module, LinearDeltaDecoder, torch.optim.AdamW, dict[str, Any], dict[int, float], Path]:
    model = _model(
        FACTORIZED_NAME,
        settings=base,
        transition_view_names=representations["transition_view_names"],
        device=device,
    )
    decoder, initial_decoder_hash = _private_decoder(
        path=paths["parent_clean_decoder"],
        expected_state_sha256=str(base["expected_clean_decoder_sha256"]),
        model_dim=4096,
        device=device,
    )
    optimizer = _optimizer(model=model, decoder=decoder, base=base)
    if paths["latest_checkpoint"].exists():
        source = Path(str(_json(paths["latest_checkpoint"])["checkpoint"]))
    else:
        source = paths["parent_checkpoint"]
    payload = torch.load(source, map_location=device, weights_only=False)
    completed = int(payload["completed_rounds"])
    checks = {
        "name": str(payload["model_name"]) == FACTORIZED_NAME,
        "seed": int(payload["global_seed"]) == GLOBAL_SEED,
        "pair_ids": list(payload["pair_ids"]) == list(pair_ids),
        "split": str(payload["split_sha256"]) == str(split_sha256),
        "decoder_init": str(payload["initial_decoder_sha256"])
        == initial_decoder_hash,
        "equal_update_counts": len(payload["update_counts"]) == len(pair_ids)
        and min(payload["update_counts"]) == max(payload["update_counts"]) == completed,
        "allowed_checkpoint": completed in EXTENSION_CHECKPOINTS,
    }
    if source != paths["parent_checkpoint"]:
        run_manifest = _json(paths["run_manifest"])
        checks.update(
            {
                "extension_format": payload.get("extension_format")
                == "factorized_program_extension_checkpoint_7dg2_v1",
                "parent_checkpoint": payload.get("parent_checkpoint_sha256")
                == str(extension["expected_parent_checkpoint_sha256"]),
                "config": payload.get("extension_config_sha256")
                == sha256_file(
                    Path(
                        "configs/benchmark/stage_c_state_conditioned_program_direct_extend_7dg2.yaml"
                    )
                ),
                "source_commit": str(payload.get("source_commit"))
                == str(run_manifest["initial_source_commit"]),
                "optimizer_state": bool(
                    payload.get("optimizer_state_dict", {}).get("state")
                ),
                "python_rng": payload.get("python_random_state") is not None,
                "torch_rng": torch.is_tensor(payload.get("torch_rng_state")),
                "cuda_rng": len(payload.get("cuda_rng_state", [])) == 1,
            }
        )
    if not all(checks.values()):
        raise ValueError(f"G2 exact resume identity differs: {checks}")
    model.load_state_dict(payload["model_state_dict"])
    decoder.load_state_dict(payload["decoder_state_dict"])
    optimizer.load_state_dict(payload["optimizer_state_dict"])
    _restore_rng(payload)
    utility_cache = {
        int(key): float(value)
        for key, value in payload.get("preference_utility_cache", {}).items()
    }
    payload = dict(payload)
    payload["initial_decoder_sha256"] = initial_decoder_hash
    return model, decoder, optimizer, payload, utility_cache, source


def _extension_checkpoint_payload(
    *,
    base_payload: dict[str, Any],
    extension_config: Path,
    utility_cache: Mapping[int, float],
    extension: Mapping[str, Any],
) -> dict[str, Any]:
    base_payload.update(
        {
            "extension_format": "factorized_program_extension_checkpoint_7dg2_v1",
            "parent_run_uuid": str(extension["parent_run_uuid"]),
            "parent_checkpoint_sha256": str(
                extension["expected_parent_checkpoint_sha256"]
            ),
            "extension_config_sha256": sha256_file(extension_config),
            "preference_utility_cache": {
                int(key): float(value) for key, value in utility_cache.items()
            },
        }
    )
    return base_payload


def _train_round(
    *,
    update_round: int,
    model: torch.nn.Module,
    decoder: LinearDeltaDecoder,
    optimizer: torch.optim.Optimizer,
    backend: Any,
    base: Mapping[str, Any],
    representations: Mapping[str, Any],
    train_rows: Sequence[dict[str, Any]],
    state_indices: Sequence[int],
    transition_indices: Sequence[int],
    base_norms: Tensor,
    preference: Mapping[int, tuple[int, float, float]],
    utility_cache: dict[int, float],
    update_counts: list[int],
) -> dict[str, Any]:
    pair_ids = [str(row["pair_id"]) for row in train_rows]
    order = sorted(
        range(len(train_rows)),
        key=lambda index: stable_key(
            GLOBAL_SEED,
            f"{FACTORIZED_NAME}-training-round-{update_round}",
            pair_ids[index],
        ),
    )
    objective = _objective(base, FACTORIZED_NAME)
    losses = []
    preference_terms = 0
    maximum_ratio = 0.0
    model.train()
    decoder.train()
    for index in order:
        batch = _collate([train_rows[index]], device=backend.device, k=K_TOKENS)
        state = (
            representations["state_values"][state_indices[index]]
            .unsqueeze(0)
            .to(backend.device)
        )
        transition = (
            representations["transition_values"][transition_indices[index]]
            .unsqueeze(0)
            .to(backend.device)
        )
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
            preservation_weight = float(base["program"]["neutral_preservation_weight"])
        elif utility < 0.0:
            preservation_weight = float(base["program"]["harmful_preservation_weight"])
        loss = loss + preservation_weight * terms["student_utility"].pow(2).mean()
        if index in preference and preference[index][0] in utility_cache:
            partner, direction, margin = preference[index]
            partner_value = torch.tensor(
                utility_cache[partner],
                device=backend.device,
                dtype=terms["student_utility"].dtype,
            )
            preference_loss = F.relu(
                float(margin)
                - float(direction)
                * (terms["student_utility"].mean() - partner_value)
            )
            loss = loss + float(base["program"]["preference_weight"]) * preference_loss
            preference_terms += 1
        raw_ratio = (
            raw_delta.to(torch.float32).flatten(start_dim=1).norm(dim=1)
            / base_norms[index : index + 1].clamp_min(1.0e-12)
        )
        loss = loss + float(base["program"]["ratio_restraint_weight"]) * (
            F.relu(raw_ratio - 1.0).pow(2).mean() + 0.01 * z.pow(2).mean()
        )
        if not bool(torch.isfinite(loss)):
            raise RuntimeError(f"Nonfinite loss at u{update_round} pair {index}")
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(model.parameters()) + list(decoder.parameters()),
            float(base["program"]["max_grad_norm"]),
        )
        optimizer.step()
        update_counts[index] += 1
        utility_cache[index] = float(terms["student_utility"].detach().mean().cpu())
        losses.append(float(loss.detach().cpu()))
        maximum_ratio = max(
            maximum_ratio, float(ratio["maximum_ratio"].detach().cpu())
        )
        if maximum_ratio > 1.0001:
            raise RuntimeError("Training perturbation ratio exceeds 1.0")
    accounting = update_count_summary(pair_ids, update_counts)
    if not accounting["all_pairs_equal"] or int(
        accounting["minimum_updates_per_pair"]
    ) != update_round:
        raise RuntimeError(f"Unequal factorized extension updates after u{update_round}")
    return {
        "mean_training_loss": statistics.fmean(losses),
        "preference_term_count": preference_terms,
        "maximum_applied_ratio": maximum_ratio,
        "update_accounting": accounting,
    }


def _checkpoint_path(paths: Mapping[str, Path], artifact_dir: Path, updates: int) -> Path:
    if updates == 16:
        return paths["parent_checkpoint"]
    return artifact_dir / f"factorized/checkpoints/model_u{updates:02d}.pt"


def _load_selected(
    *,
    checkpoint: Path,
    base: Mapping[str, Any],
    paths: Mapping[str, Path],
    representations: Mapping[str, Any],
    device: torch.device,
) -> tuple[torch.nn.Module, LinearDeltaDecoder, Mapping[str, Any]]:
    model = _model(
        FACTORIZED_NAME,
        settings=base,
        transition_view_names=representations["transition_view_names"],
        device=device,
    )
    decoder, _ = _private_decoder(
        path=paths["parent_clean_decoder"],
        expected_state_sha256=str(base["expected_clean_decoder_sha256"]),
        model_dim=4096,
        device=device,
    )
    payload = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(payload["model_state_dict"])
    decoder.load_state_dict(payload["decoder_state_dict"])
    model.eval()
    decoder.eval()
    return model, decoder, payload


def _tokenized_data(
    *,
    cfg: Any,
    base: Mapping[str, Any],
    paths: Mapping[str, Path],
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
    backend: Any,
) -> dict[str, dict[str, Any]]:
    teacher_rows = _load_teacher_rows(base=base, paths=paths, manifests=manifests)
    examples = load_decision_examples(paths["parent_decisions"])
    tokenized = _build_tokenized_rows(
        backend=backend,
        examples=examples,
        response_rows=teacher_rows,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=int(base["teacher_cache"]["context_limit"]),
    )
    output = {str(row["pair_id"]): row for row in tokenized}
    if len(output) != len(teacher_rows):
        raise ValueError("G2 tokenized pair keys are not unique")
    return output


def _train(
    *,
    config: Path,
    cfg: Any,
    base: Mapping[str, Any],
    extension: Mapping[str, Any],
    paths: Mapping[str, Path],
    artifact_dir: Path,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    preflight = _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Expected additional H100 time exceeds 14 hours")
    manifests, split = _manifest_rows(paths)
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen parameters are not frozen")
    tokenized_by_pair = _tokenized_data(
        cfg=cfg,
        base=base,
        paths=paths,
        manifests=manifests,
        backend=backend,
    )
    representations = _load_representations(paths={
        "state_cache": paths["parent_state_cache"],
        "transition_cache": paths["parent_transition_cache"],
    }, device=backend.device)
    train_rows = [
        tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
        for index in split["train_indices"]
    ]
    validation_rows = [
        tokenized_by_pair[str(manifests["A"][index]["pair_id"])]
        for index in split["validation_indices"]
    ]
    pair_ids = [str(row["pair_id"]) for row in train_rows]
    model, decoder, optimizer, payload, utility_cache, resume_source = _load_training_state(
        base=base,
        extension=extension,
        paths=paths,
        representations=representations,
        pair_ids=pair_ids,
        split_sha256=str(split["manifest_sha256"]),
        device=backend.device,
    )
    history = list(payload["history"])
    completed = int(payload["completed_rounds"])
    update_counts = [int(value) for value in payload["update_counts"]]
    state_indices, transition_indices = _pair_indices(train_rows, representations)
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=train_rows, device=backend.device, k=K_TOKENS
    ).to(backend.device)
    preference = _preference_partners(train_rows, base)
    extension_started = time.perf_counter()
    visited = {int(row["updates_per_pair"]) for row in history}
    stop_reason = "u64_limit"
    for target in (32, 48, 64):
        if target <= completed:
            continue
        if completed > 16:
            previous_decision_path = artifact_dir / f"factorized/continuation_u{completed:02d}.json"
            if previous_decision_path.exists() and not bool(
                _json(previous_decision_path)["continue"]
            ):
                stop_reason = f"continuation_stopped_at_u{completed}"
                break
        last_round = None
        for update_round in range(completed + 1, target + 1):
            last_round = _train_round(
                update_round=update_round,
                model=model,
                decoder=decoder,
                optimizer=optimizer,
                backend=backend,
                base=base,
                representations=representations,
                train_rows=train_rows,
                state_indices=state_indices,
                transition_indices=transition_indices,
                base_norms=base_norms,
                preference=preference,
                utility_cache=utility_cache,
                update_counts=update_counts,
            )
        assert last_round is not None
        validation = _evaluate_controls(
            name=FACTORIZED_NAME,
            model=model,
            decoder=decoder,
            rows=validation_rows,
            representations=representations,
            backend=backend,
            settings=base,
            output_dir=artifact_dir / f"factorized/a_validation_u{target:02d}",
            controls=("correct",),
        )
        entry = {
            "updates_per_pair": target,
            **last_round,
            "a_validation": {"correct": validation["correct"]["summary"]},
            "extension_elapsed_seconds": time.perf_counter() - extension_started,
        }
        history.append(entry)
        checkpoint = _checkpoint_path(paths, artifact_dir, target)
        checkpoint_payload = _checkpoint_payload(
            name=FACTORIZED_NAME,
            model=model,
            decoder=decoder,
            optimizer=optimizer,
            pair_ids=pair_ids,
            update_counts=update_counts,
            completed_rounds=target,
            history=history,
            initial_decoder_hash=str(payload["initial_decoder_sha256"]),
            split_sha256=str(split["manifest_sha256"]),
        )
        checkpoint_payload = _extension_checkpoint_payload(
            base_payload=checkpoint_payload,
            extension_config=config,
            utility_cache=utility_cache,
            extension=extension,
        )
        atomic_torch_save(checkpoint_payload, checkpoint)
        atomic_write_json(
            paths["latest_checkpoint"],
            {
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
                "updates_per_pair": target,
            },
        )
        previous = next(
            row for row in reversed(history[:-1]) if int(row["updates_per_pair"]) in visited
        )
        decision = continuation_decision(
            previous["a_validation"]["correct"],
            entry["a_validation"]["correct"],
            train_loss_previous=float(previous["mean_training_loss"]),
            train_loss_current=float(entry["mean_training_loss"]),
        )
        decision.update(
            {
                "from_updates_per_pair": int(previous["updates_per_pair"]),
                "to_updates_per_pair": target,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
            }
        )
        if target == 64:
            decision["continue"] = False
            decision["stop_reason"] = "maximum_u64_reached"
        elif not decision["continue"]:
            decision["stop_reason"] = "preregistered_continuation_rule_not_met"
        atomic_write_json(
            artifact_dir / f"factorized/continuation_u{target:02d}.json", decision
        )
        attempt.progress(
            status=f"factorized_extension_u{target}",
            latest_validated_checkpoint=str(checkpoint),
            updates_per_pair=target,
            continuation=bool(decision["continue"]),
        )
        print(
            f"factorized extension u{target} val_rho="
            f"{validation['correct']['summary'].get('u_text_vs_u_student_spearman')} "
            f"val_huber={validation['correct']['summary']['sequence_utility_huber']['mean']:.6f} "
            f"continue={decision['continue']}",
            flush=True,
        )
        completed = target
        visited.add(target)
        if not decision["continue"]:
            stop_reason = str(decision["stop_reason"])
            break

    training_selection = select_checkpoint(history)
    selected_updates = int(training_selection["selected_updates_per_pair"])
    selected_checkpoint = _checkpoint_path(paths, artifact_dir, selected_updates)
    selected_hash = sha256_file(selected_checkpoint)
    model, decoder, selected_payload = _load_selected(
        checkpoint=selected_checkpoint,
        base=base,
        paths=paths,
        representations=representations,
        device=backend.device,
    )
    training_summary = {
        "format": "factorized_program_extension_training_summary_7dg2_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "resume_source": str(resume_source),
        "resume_source_sha256": sha256_file(resume_source),
        "parent_checkpoint_sha256": str(extension["expected_parent_checkpoint_sha256"]),
        "train_pair_count": len(train_rows),
        "validation_pair_count": len(validation_rows),
        "history": history,
        "visited_updates_per_pair": sorted(
            int(row["updates_per_pair"])
            for row in history
            if int(row["updates_per_pair"]) in EXTENSION_CHECKPOINTS
        ),
        "training_stop_reason": stop_reason,
        **training_selection,
        "selected_checkpoint": str(selected_checkpoint),
        "selected_checkpoint_sha256": selected_hash,
        "model_sha256": module_state_sha256(model),
        "trained_decoder_sha256": module_state_sha256(decoder),
        "initial_decoder_sha256": str(selected_payload["initial_decoder_sha256"]),
        "qwen_parameters_trainable": any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "student_prompt_contains_raw_transition": False,
    }
    atomic_write_json(paths["training_summary"], training_summary)

    if paths["selection_summary"].exists():
        frozen = _json(paths["selection_summary"])
        if (
            str(frozen["selected_checkpoint_sha256"]) != selected_hash
            or float(frozen["selected_gamma"]) not in PROGRAM_GAINS
        ):
            raise ValueError("Frozen G2 checkpoint/gamma selection differs")
        selected_gamma = float(frozen["selected_gamma"])
        a_validation = frozen["a_validation"]
    else:
        gamma_candidates = {}
        for gamma in PROGRAM_GAINS:
            result = _evaluate_controls(
                name=FACTORIZED_NAME,
                model=model,
                decoder=decoder,
                rows=validation_rows,
                representations=representations,
                backend=backend,
                settings=base,
                output_dir=artifact_dir
                / f"factorized/calibration/gamma_{gamma:.2f}",
                controls=("correct",),
                latent_scale=gamma,
            )
            gamma_candidates[gamma] = result["correct"]["summary"]
            attempt.progress(
                status="factorized_extension_calibration",
                gamma=gamma,
                latest_validated_checkpoint=str(
                    artifact_dir / f"factorized/calibration/gamma_{gamma:.2f}"
                ),
            )
        zero = _evaluate_controls(
            name=FACTORIZED_NAME,
            model=model,
            decoder=decoder,
            rows=validation_rows,
            representations=representations,
            backend=backend,
            settings=base,
            output_dir=artifact_dir / "factorized/calibration/zero",
            controls=("zero",),
        )["zero"]["summary"]
        gain_selection = select_program_gain(gamma_candidates)
        selected_gamma = float(gain_selection["selected_gamma"])
        a_validation = {
            "correct": gamma_candidates[selected_gamma],
            "zero": zero,
        }
        frozen = {
            "format": "factorized_program_checkpoint_gain_selection_7dg2_v1",
            "selection_data": "A_validation_only",
            "selected_updates_per_pair": selected_updates,
            "selected_checkpoint": str(selected_checkpoint),
            "selected_checkpoint_sha256": selected_hash,
            "checkpoint_selection": training_selection,
            "gamma_candidates": {
                f"{gamma:.2f}": summary
                for gamma, summary in gamma_candidates.items()
            },
            **gain_selection,
            "a_validation": a_validation,
            "selection_frozen_before_BCDE": True,
        }
        atomic_write_json(paths["selection_summary"], frozen)

    training_summary["selected_gamma"] = selected_gamma
    training_summary["selection_summary"] = str(paths["selection_summary"])
    atomic_write_json(paths["training_summary"], training_summary)

    cells = {}
    for cell in "BCDE":
        cell_path = artifact_dir / f"factorized/final_evaluation/{cell}/cell_summary.json"
        if cell_path.exists():
            payload_cell = _json(cell_path)
            if (
                str(payload_cell["selected_checkpoint_sha256"]) != selected_hash
                or float(payload_cell["selected_gamma"]) != selected_gamma
            ):
                raise ValueError(f"Frozen final cell {cell} identity differs")
            cells[cell] = payload_cell["controls"]
            continue
        rows = [
            tokenized_by_pair[str(row["pair_id"])] for row in manifests[cell]
        ]
        values = _evaluate_controls(
            name=FACTORIZED_NAME,
            model=model,
            decoder=decoder,
            rows=rows,
            representations=representations,
            backend=backend,
            settings=base,
            output_dir=cell_path.parent,
            controls=CONTROLS,
            latent_scale=selected_gamma,
        )
        controls = {name: value["summary"] for name, value in values.items()}
        atomic_write_json(
            cell_path,
            {
                "format": "factorized_program_frozen_final_cell_7dg2_v1",
                "cell": cell,
                "pair_count": len(rows),
                "selected_checkpoint_sha256": selected_hash,
                "selected_gamma": selected_gamma,
                "controls": controls,
            },
        )
        cells[cell] = controls
        attempt.progress(
            status="factorized_extension_final_evaluation",
            completed_cell=cell,
            latest_validated_checkpoint=str(cell_path),
        )
    gate = factorized_extension_gate(a_validation=a_validation, cells=cells)
    selector_hash = sha256_file(paths["parent_selector"])
    branch = (
        "factorized_teacher_forced_passed_one_step_pending"
        if gate["passed"]
        else "converged_r16_factorization_failed"
    )
    summary = {
        "format": "factorized_program_extension_teacher_forced_summary_7dg2_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "training": training_summary,
        "selection": frozen,
        "a_validation": a_validation,
        "cells": cells,
        "gate": gate,
        "passed": bool(gate["passed"]),
        "decision_branch": branch,
        "selector_sha256": selector_hash,
        "selector_unchanged": selector_hash
        == str(base["expected_selector_ensemble_sha256"]),
        "observation_excluded": True,
        "controller_rank": 16,
        "K": 4,
        "last_user_k_unchanged": True,
        "elapsed_h100_hours": (time.perf_counter() - extension_started) / 3600.0,
    }
    atomic_write_json(paths["teacher_forced_summary"], summary)
    atomic_write_json(
        paths["direct_summary"],
        {
            "format": "state_conditioned_program_direct_extension_gpu_summary_7dg2_v1",
            "run_uuid": RUN_UUID,
            "global_seed": GLOBAL_SEED,
            "factorized": summary,
            "decision_branch": branch,
            "teacher_forced_factorized_passed": bool(gate["passed"]),
            "elapsed_h100_hours": summary["elapsed_h100_hours"],
        },
    )
    atomic_write_text(
        artifact_dir / "teacher_forced_report.md",
        "\n".join(
            [
                "# EXP-025D-G2 teacher-forced result",
                "",
                f"- selected checkpoint: `u{selected_updates}`",
                f"- selected gamma: `{selected_gamma}`",
                f"- gate passed: `{gate['passed']}`",
                f"- decision: `{branch}`",
                "",
            ]
        ),
    )
    attempt.progress(
        status="factorized_extension_teacher_forced_completed",
        latest_validated_checkpoint=str(paths["teacher_forced_summary"]),
        decision_branch=branch,
    )
    del model, decoder
    gc.collect()
    torch.cuda.empty_cache()
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    base = cfg.raw["stage_c_7dg"]
    extension = cfg.raw["stage_c_7dg2"]
    if int(extension["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-025D-G2 permits only global seed 25101")
    seed_everything(GLOBAL_SEED)
    if os.name != "nt" and not os.path.ismount(
        Path(str(extension["persistent_root"]))
    ):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(base, extension, args.artifact_dir)
    data_hashes = _data_hashes(paths, args.config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"factorized_program_extension_{args.phase}",
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
        heartbeat_interval_s=float(extension["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "preflight":
            result = _preflight(
                config=args.config,
                base=base,
                extension=extension,
                paths=paths,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        else:
            result = _train(
                config=args.config,
                cfg=cfg,
                base=base,
                extension=extension,
                paths=paths,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
            )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

