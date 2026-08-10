from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import random
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import yaml

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.addressing_4b import mean_std
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_capacity_5e import validate_target_token_utility_identity
from rcmf.training.oracle_convergence_5fa import (
    OBJECTIVES_5FA,
    IndependentPairTensorTable,
    assess_plateau,
    atomic_torch_save,
    summarize_convergence_rows,
    update_count_summary,
)
from rcmf.training.oracle_convergence_5fb import (
    add_selection_category_metrics,
    metric_reproduction_report,
    paired_bootstrap_difference,
    tensor_state_sha256,
)
from rcmf.training.oracle_decoder_5fc import (
    K_TOKENS,
    LATENT_DIM,
    LOW_RANKS,
    MLPDeltaDecoder,
    ORACLE_DECODER_VERSION,
    PRIMARY_TARGET_UPDATES,
    ROBUSTNESS_TARGET_UPDATES,
    LinearDeltaDecoder,
    apply_latent_inversion_step,
    assert_pair_only_input_contract,
    decoder_decision,
    decoder_reconstruction_metrics,
    decoder_tensor_loss,
    direct_delta_geometry,
    flatten_delta,
    frozen_decoder_capacity_gate,
    json_sha256,
    low_rank_capacity_gate,
    minimally_project_delta_to_ratio,
    module_state_sha256,
    project_latents_to_output_ratio_,
    reconstruction_summary,
    state_grouped_three_fold_manifest,
    tensor_reconstruction_plateau,
    uncentered_svd_reconstruction,
    validate_direct_checkpoint,
)
from rcmf.training.pair_grounding_5d import PAIR_RESPONSE_CACHE_VERSION, spearman
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend
from scripts.run_stage_c_oracle_capacity_5e import _collate, _forward_direct_delta
from scripts.run_stage_c_oracle_convergence_5fa import (
    _evaluate_direct_tensor,
    _precompute_direct_base_norms,
    _student_prompt_contract,
    _training_loss,
)
from scripts.run_stage_c_pair_grounding_5d import _build_tokenized_pair_rows


del _bootstrap


REPRODUCTION_PATHS = (
    ("u_text_vs_u_student_spearman",),
    ("u_text_vs_u_student_pearson",),
    ("positive_negative_sign_agreement",),
    ("sequence_utility_huber", "mean"),
    ("sequence_utility_mae", "mean"),
    ("sequence_utility_mse", "mean"),
    ("target_token_delta_correlation_global",),
    ("target_token_delta_huber", "mean"),
    ("sparse_teacher_kl", "mean"),
    ("target_nll", "mean"),
    ("delta_ratio", "mean"),
    ("delta_ratio", "max"),
)


def utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"no rows found at {path}")
    return rows


def _select_by_pair_ids(
    rows: Sequence[dict[str, Any]], pair_ids: Sequence[str]
) -> list[dict[str, Any]]:
    by_id = {str(row["pair_id"]): row for row in rows}
    missing = [str(pair_id) for pair_id in pair_ids if str(pair_id) not in by_id]
    if missing:
        raise ValueError(f"missing pair IDs: {missing[:20]}")
    selected = [by_id[str(pair_id)] for pair_id in pair_ids]
    if [str(row["pair_id"]) for row in selected] != [str(value) for value in pair_ids]:
        raise AssertionError("pair order changed")
    return selected


def _evaluate_tensor(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    tensor: Tensor,
    pair_ids: Sequence[str],
    device: torch.device,
    huber_delta: float,
    control: str,
    batch_size: int = 1,
) -> dict[str, Any]:
    evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=tensor.to(device),
        pair_ids=pair_ids,
        device=device,
        k=K_TOKENS,
        batch_size=batch_size,
        huber_delta=huber_delta,
        control=control,
    )
    evaluation["summary"] = add_selection_category_metrics(
        evaluation["summary"], evaluation["rows"]
    )
    return evaluation


def _portable_evaluation(
    evaluation: Mapping[str, Any], *, rows_path: Path
) -> dict[str, Any]:
    write_jsonl(rows_path, evaluation["rows"])
    return {
        "summary": evaluation["summary"],
        "selected_token_report": evaluation.get("selected_token_report"),
        "rows_path": str(rows_path),
    }


def _pooled_evaluation(
    rows: Sequence[dict[str, Any]], *, rows_path: Path
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: str(row["pair_id"]))
    summary = add_selection_category_metrics(summarize_convergence_rows(ordered), ordered)
    write_jsonl(rows_path, ordered)
    return {"summary": summary, "rows": ordered, "rows_path": str(rows_path)}


def _stage_settings(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    settings = raw.get("stage_c_5fc") or {}
    expected = {
        "primary_checkpoint_updates": PRIMARY_TARGET_UPDATES,
        "robustness_checkpoint_updates": ROBUSTNESS_TARGET_UPDATES,
        "pair_count": 192,
        "latent_dim": LATENT_DIM,
        "decoder_hidden_dim": 512,
        "low_ranks": list(LOW_RANKS),
        "svd_factorization_dtype": "float64",
        "folds": 3,
    }
    differences = {
        key: {"actual": settings.get(key), "expected": value}
        for key, value in expected.items()
        if settings.get(key) != value
    }
    tensor_expected = {
        "version": "plateau_required_v3",
        "maximum_epochs": 2048,
        "require_documented_plateau": True,
        "linear_learning_rate": 0.000001,
        "mlp_learning_rate": 0.0003,
        "numerical_floor": {
            "normalized_mse": 1.0e-8,
            "relative_frobenius_error": 1.0e-4,
            "one_minus_mean_cosine": 1.0e-6,
        },
    }
    tensor_actual = dict(settings.get("tensor_training") or {})
    differences.update(
        {
            f"tensor_training.{key}": {
                "actual": tensor_actual.get(key),
                "expected": value,
            }
            for key, value in tensor_expected.items()
            if tensor_actual.get(key) != value
        }
    )
    if differences:
        raise ValueError(f"formal EXP-016C config differs: {differences}")
    return settings


def _model_identity(backend: Any, pair_cache_summary: Mapping[str, Any]) -> dict[str, Any]:
    runtime_model = str(
        getattr(backend, "model_name", getattr(backend.model.config, "name_or_path", ""))
    )
    runtime_commit = getattr(backend.model.config, "_commit_hash", None)
    tokenizer_name = str(getattr(backend.tokenizer, "name_or_path", ""))
    chat_template = getattr(backend.tokenizer, "chat_template", None) or ""
    errors = []
    if runtime_model != str(pair_cache_summary.get("model_name")):
        errors.append("runtime model differs from pair cache")
    if runtime_commit != pair_cache_summary.get("model_config_commit_hash"):
        errors.append("runtime model commit differs from pair cache")
    return {
        "passed": not errors,
        "errors": errors,
        "runtime_model_name": runtime_model,
        "runtime_model_config_commit_hash": runtime_commit,
        "runtime_tokenizer_name_or_path": tokenizer_name,
        "runtime_chat_template_sha256": hashlib.sha256(
            chat_template.encode("utf-8")
        ).hexdigest(),
        "cache_checkpoint_identity": pair_cache_summary.get("checkpoint_identity"),
    }


def _checkpoint_expected_metrics(
    source_summary: Mapping[str, Any], updates: int
) -> Mapping[str, Any]:
    item = next(
        row
        for row in source_summary["convergence_history"]
        if int(row["updates_per_pair"]) == int(updates)
    )
    return item["evaluation_summary"]


def _load_and_validate_target(
    *,
    checkpoint: Path,
    updates: int,
    expected_pair_ids: Sequence[str],
    model_dim: int,
    source_summary: Mapping[str, Any],
    backend: Any,
    rows: Sequence[dict[str, Any]],
    device: torch.device,
    huber_delta: float,
    tolerance: float,
    output_dir: Path,
) -> dict[str, Any]:
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    validation = validate_direct_checkpoint(
        payload,
        expected_pair_ids=expected_pair_ids,
        expected_updates=updates,
        model_dim=model_dim,
    )
    sidecar_path = checkpoint.with_suffix(checkpoint.suffix + ".integrity.json")
    if not sidecar_path.exists():
        raise FileNotFoundError(f"missing checkpoint integrity sidecar: {sidecar_path}")
    sidecar = _load_json(sidecar_path)
    file_hash = sha256_file(checkpoint)
    delta_hash = tensor_state_sha256(payload["table_state_dict"])
    hash_checks = {
        "file_matches_sidecar": file_hash == sidecar.get("checkpoint_file_sha256"),
        "delta_matches_sidecar": delta_hash == sidecar.get("delta_tensor_sha256"),
        "delta_matches_embedded": delta_hash
        == payload.get("metadata", {}).get("delta_tensor_sha256"),
    }
    if not validation["passed"] or not all(hash_checks.values()):
        raise ValueError(
            f"u{updates} checkpoint validation failed: {validation['errors']} {hash_checks}"
        )
    tensor = validation.pop("tensor")
    evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=tensor,
        pair_ids=expected_pair_ids,
        device=device,
        huber_delta=huber_delta,
        control=f"source_u{updates}_reproduction",
    )
    reproduction = metric_reproduction_report(
        actual=evaluation["summary"],
        expected=_checkpoint_expected_metrics(source_summary, updates),
        paths=REPRODUCTION_PATHS,
        tolerance=tolerance,
    )
    if not reproduction["passed"]:
        raise ValueError(f"u{updates} metric reproduction failed: {reproduction}")
    rows_path = output_dir / f"source_u{updates}_reproduction_rows.jsonl"
    portable = _portable_evaluation(evaluation, rows_path=rows_path)
    return {
        "updates": updates,
        "checkpoint": str(checkpoint),
        "checkpoint_file_sha256": file_hash,
        "delta_tensor_sha256": delta_hash,
        "normalized_delta_tensor_sha256": delta_hash,
        "sidecar": str(sidecar_path),
        "validation": validation,
        "hash_checks": hash_checks,
        "metric_reproduction": reproduction,
        "tensor": tensor,
        "evaluation": evaluation,
        "portable_evaluation": portable,
    }


def _low_rank_analysis(
    *,
    target_name: str,
    target: Mapping[str, Any],
    backend: Any,
    rows: Sequence[dict[str, Any]],
    pair_ids: Sequence[str],
    base_norms: Tensor,
    device: torch.device,
    huber_delta: float,
    output_dir: Path,
    tolerance: float,
) -> dict[str, Any]:
    target_dir = output_dir / target_name
    target_dir.mkdir(parents=True, exist_ok=True)
    completed_path = target_dir / "low_rank_capacity.json"
    target_delta_sha256 = str(target["normalized_delta_tensor_sha256"])
    ordered_pair_ids_sha256 = json_sha256(list(pair_ids))
    expected_row_paths = [target_dir / "rank_000_zero_rows.jsonl"] + [
        target_dir / f"rank_{rank:03d}_rows.jsonl" for rank in LOW_RANKS
    ]
    if completed_path.exists() and all(path.exists() for path in expected_row_paths):
        completed = _load_json(completed_path)
        resume_checks = {
            "target_updates": completed.get("target_updates") == target["updates"],
            "target_delta": completed.get("target_delta_tensor_sha256")
            == target_delta_sha256,
            "ordered_pair_ids": completed.get("ordered_pair_ids_sha256")
            == ordered_pair_ids_sha256,
            "svd_factorization_dtype": completed.get("svd_factorization_dtype")
            == "torch.float64",
        }
        if not all(resume_checks.values()):
            raise ValueError(f"cached low-rank target identity differs for {target_name}")
        print(f"reused completed low-rank analysis for {target_name}", flush=True)
        return {**completed, "zero_evaluation_runtime": None}
    delta = target["tensor"].to(torch.float32)
    device_delta = delta.to(device)
    geometry = direct_delta_geometry(device_delta, rows=rows)
    atomic_write_json(target_dir / "direct_delta_geometry.json", geometry)
    factorization = torch.linalg.svd(
        flatten_delta(device_delta).to(torch.float64), full_matrices=False
    )

    zero_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=torch.zeros_like(delta),
        pair_ids=pair_ids,
        device=device,
        huber_delta=huber_delta,
        control=f"{target_name}_zero_delta",
    )
    zero_portable = _portable_evaluation(
        zero_evaluation, rows_path=target_dir / "rank_000_zero_rows.jsonl"
    )
    rank_results = {}
    for rank in LOW_RANKS:
        reconstruction = uncentered_svd_reconstruction(
            device_delta, rank, factorization=factorization
        )["delta"].detach().cpu()
        reconstruction, projection = minimally_project_delta_to_ratio(
            reconstruction, base_norms=base_norms, max_ratio=1.0
        )
        tensor_metrics = reconstruction_summary(
            delta, reconstruction, base_norms=base_norms
        )
        evaluation = _evaluate_tensor(
            backend=backend,
            rows=rows,
            tensor=reconstruction,
            pair_ids=pair_ids,
            device=device,
            huber_delta=huber_delta,
            control=f"{target_name}_uncentered_svd_rank_{rank}",
        )
        portable = _portable_evaluation(
            evaluation, rows_path=target_dir / f"rank_{rank:03d}_rows.jsonl"
        )
        rank_results[str(rank)] = {
            "rank": rank,
            "tensor_reconstruction": tensor_metrics,
            "ratio_projection": projection,
            "evaluation": portable,
        }
        print(
            f"{target_name} rank={rank} rel_frob={tensor_metrics['relative_frobenius_error']:.6f} "
            f"spearman={evaluation['summary']['u_text_vs_u_student_spearman']:.6f} "
            f"huber={evaluation['summary']['sequence_utility_huber']['mean']:.6f}",
            flush=True,
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    rank192 = rank_results["192"]
    rank192_reproduction = metric_reproduction_report(
        actual=rank192["evaluation"]["summary"],
        expected=target["evaluation"]["summary"],
        paths=REPRODUCTION_PATHS,
        tolerance=tolerance,
    )
    implementation_check = {
        "passed": bool(
            rank192["tensor_reconstruction"]["relative_frobenius_error"] <= 1.0e-4
            and rank192_reproduction["passed"]
        ),
        "relative_frobenius_error": rank192["tensor_reconstruction"][
            "relative_frobenius_error"
        ],
        "metric_reproduction": rank192_reproduction,
    }
    gate = low_rank_capacity_gate(
        rank128_summary=rank_results["128"]["evaluation"]["summary"],
        full_summary=target["evaluation"]["summary"],
        zero_summary=zero_evaluation["summary"],
    )
    result = {
        "target": target_name,
        "target_updates": target["updates"],
        "target_delta_tensor_sha256": target_delta_sha256,
        "ordered_pair_ids_sha256": ordered_pair_ids_sha256,
        "svd_factorization_dtype": "torch.float64",
        "geometry_path": str(target_dir / "direct_delta_geometry.json"),
        "rank_results": rank_results,
        "zero_evaluation": zero_portable,
        "rank192_implementation_check": implementation_check,
        "rank128_capacity_gate": gate,
    }
    atomic_write_json(completed_path, result)
    return {**result, "zero_evaluation_runtime": zero_evaluation}


def _train_tensor_decoder(
    *,
    architecture: str,
    train_pair_ids: Sequence[str],
    train_target: Tensor,
    basis: Tensor,
    settings: Mapping[str, Any],
    device: torch.device,
    seed: int,
    output_dir: Path,
) -> tuple[nn.Module, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dim = int(train_target.shape[1])
    tensor_cfg = settings["tensor_training"]
    if architecture == "linear":
        decoder: nn.Module = LinearDeltaDecoder(LATENT_DIM, output_dim)
        decoder.initialize_from_basis(basis)
        initial_z = train_target.to(basis.device) @ basis.T
        lr = float(tensor_cfg["linear_learning_rate"])
    elif architecture == "mlp":
        torch.manual_seed(seed)
        decoder = MLPDeltaDecoder(
            LATENT_DIM, output_dim, hidden_dim=int(settings["decoder_hidden_dim"])
        )
        generator = torch.Generator(device="cpu").manual_seed(seed + 1)
        initial_z = torch.randn(
            len(train_pair_ids), LATENT_DIM, generator=generator, dtype=torch.float32
        ) * float(tensor_cfg["latent_initial_std"])
        lr = float(tensor_cfg["mlp_learning_rate"])
    else:
        raise ValueError(f"unknown decoder architecture: {architecture}")

    decoder = decoder.to(device)
    z = nn.Parameter(initial_z.to(device))
    optimizer = torch.optim.AdamW([z, *decoder.parameters()], lr=lr, weight_decay=0.0)
    batch_size = int(tensor_cfg["batch_size"])
    interval = int(tensor_cfg["checkpoint_interval_epochs"])
    minimum = int(tensor_cfg["minimum_epochs"])
    maximum = int(tensor_cfg["maximum_epochs"])
    target = train_target.to(device)
    history: list[dict[str, Any]] = []
    checkpoint_path = output_dir / "tensor_decoder.pt"
    latest_path = output_dir / "latest.json"
    target_sha256 = tensor_state_sha256({"train_target": train_target})
    basis_sha256 = tensor_state_sha256({"basis": basis})
    training_identity = {
        "architecture": architecture,
        "seed": int(seed),
        "train_pair_ids_sha256": json_sha256(list(train_pair_ids)),
        "tensor_training": dict(tensor_cfg),
        "latent_dim": LATENT_DIM,
        "output_dim": output_dim,
    }
    training_identity_sha256 = json_sha256(training_identity)
    start_epoch = 0
    plateau = {"assessable": False, "plateau": False}
    numerical_floor = dict(tensor_cfg["numerical_floor"])
    if latest_path.exists() and checkpoint_path.exists():
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        resume_checks = {
            "pair_ids": payload.get("train_pair_ids") == list(train_pair_ids),
            "architecture": payload.get("architecture") == architecture,
            "target": payload.get("train_target_sha256") == target_sha256,
            "basis": payload.get("basis_sha256") == basis_sha256,
            "training_identity": payload.get("training_identity_sha256")
            == training_identity_sha256,
        }
        if not all(resume_checks.values()):
            raise ValueError(f"tensor decoder resume identity differs: {resume_checks}")
        decoder.load_state_dict(payload["decoder_state_dict"])
        z.data.copy_(payload["z"].to(z))
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        history = list(payload["history"])
        start_epoch = int(payload["epoch"])
        plateau = dict(payload.get("plateau") or plateau)
        if history:
            current_epoch = int(history[-1]["epoch"])
            plateau = tensor_reconstruction_plateau(
                history,
                current_epoch=current_epoch,
                previous_epoch=current_epoch - interval,
                normalized_mse_floor=float(numerical_floor["normalized_mse"]),
                relative_frobenius_floor=float(
                    numerical_floor["relative_frobenius_error"]
                ),
                cosine_error_floor=float(
                    numerical_floor["one_minus_mean_cosine"]
                ),
            )

    loop_maximum = (
        start_epoch if start_epoch >= minimum and plateau.get("plateau") else maximum
    )
    for epoch in range(start_epoch + 1, loop_maximum + 1):
        order = list(range(len(train_pair_ids)))
        random.Random(seed * 1_000_000 + epoch).shuffle(order)
        for start in range(0, len(order), batch_size):
            indices = torch.tensor(order[start : start + batch_size], device=device)
            prediction = decoder(z.index_select(0, indices))
            terms = decoder_tensor_loss(prediction, target.index_select(0, indices))
            optimizer.zero_grad(set_to_none=True)
            terms["loss"].backward()
            torch.nn.utils.clip_grad_norm_([z, *decoder.parameters()], 1.0)
            optimizer.step()
        if epoch % interval:
            continue
        with torch.no_grad():
            metrics = decoder_reconstruction_metrics(decoder(z), target)
        history.append({"epoch": epoch, "metrics": metrics})
        plateau = tensor_reconstruction_plateau(
            history,
            current_epoch=epoch,
            previous_epoch=epoch - interval,
            normalized_mse_floor=float(numerical_floor["normalized_mse"]),
            relative_frobenius_floor=float(
                numerical_floor["relative_frobenius_error"]
            ),
            cosine_error_floor=float(numerical_floor["one_minus_mean_cosine"]),
        )
        payload = {
            "format": ORACLE_DECODER_VERSION,
            "component": "tensor_decoder",
            "architecture": architecture,
            "epoch": epoch,
            "train_pair_ids": list(train_pair_ids),
            "train_target_sha256": target_sha256,
            "basis_sha256": basis_sha256,
            "training_identity": training_identity,
            "training_identity_sha256": training_identity_sha256,
            "decoder_state_dict": {
                key: value.detach().cpu() for key, value in decoder.state_dict().items()
            },
            "z": z.detach().cpu(),
            "optimizer_state_dict": optimizer.state_dict(),
            "history": history,
            "plateau": plateau,
            "source_commit": maybe_git_commit(),
        }
        atomic_torch_save(payload, checkpoint_path)
        atomic_write_json(latest_path, {"checkpoint": str(checkpoint_path), "epoch": epoch})
        atomic_write_json(output_dir / "history.json", history)
        print(
            f"tensor {architecture} epoch={epoch} loss={metrics['loss']:.6f} "
            f"cosine={metrics['mean_cosine']:.6f} plateau={plateau.get('plateau')}",
            flush=True,
        )
        if epoch >= minimum and plateau.get("plateau"):
            break

    with torch.no_grad():
        final_metrics = decoder_reconstruction_metrics(decoder(z), target)
    summary = {
        "status": "completed" if plateau.get("plateau") else "stopped_without_plateau",
        "architecture": architecture,
        "train_pair_count": len(train_pair_ids),
        "train_pair_ids_sha256": json_sha256(list(train_pair_ids)),
        "epochs": int(history[-1]["epoch"]),
        "history": history,
        "plateau": plateau,
        "final_metrics": final_metrics,
        "decoder_state_sha256": module_state_sha256(decoder),
        "training_identity_sha256": training_identity_sha256,
        "checkpoint": str(checkpoint_path),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    if bool(tensor_cfg.get("require_documented_plateau", True)) and not plateau.get(
        "plateau"
    ):
        raise RuntimeError(
            f"tensor {architecture} did not reach the documented plateau by epoch {maximum}"
        )
    return decoder, summary


def _decoded_tensor(
    table: IndependentPairTensorTable, decoder: nn.Module, *, k: int, model_dim: int
) -> Tensor:
    with torch.no_grad():
        return decoder(table.stacked()).view(len(table.rows), k, model_dim).detach().cpu()


def _inversion_checkpoint_schedule(maximum: int) -> list[int]:
    return [value for value in (2, 8, 16, 32, 64, 128) if value <= int(maximum)]


def _run_inversion(
    *,
    name: str,
    decoder_source: nn.Module,
    initial_z: Tensor,
    rows: Sequence[dict[str, Any]],
    pair_ids: Sequence[str],
    base_norms: Tensor,
    backend: Any,
    device: torch.device,
    model_dim: int,
    objective: Any,
    settings: Mapping[str, Any],
    seed: int,
    output_dir: Path,
    train_decoder: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    inversion_cfg = settings["heldout_inversion"]
    decoder = copy.deepcopy(decoder_source).to(device)
    for parameter in decoder.parameters():
        parameter.requires_grad_(train_decoder)
    initial_decoder_hash = module_state_sha256(decoder)
    initial_z_sha256 = tensor_state_sha256({"initial_z": initial_z})
    base_norms_sha256 = tensor_state_sha256({"base_norms": base_norms})
    inversion_identity = {
        "name": name,
        "train_decoder": bool(train_decoder),
        "pair_ids_sha256": json_sha256(list(pair_ids)),
        "initial_decoder_state_sha256": initial_decoder_hash,
        "initial_z_sha256": initial_z_sha256,
        "base_norms_sha256": base_norms_sha256,
        "objective": vars(objective),
        "heldout_inversion": dict(inversion_cfg),
        "seed": int(seed),
        "model_dim": int(model_dim),
        "k": K_TOKENS,
        "ratio_budget": 1.0,
    }
    inversion_identity_sha256 = json_sha256(inversion_identity)
    table = IndependentPairTensorTable(pair_ids, (LATENT_DIM,), init_std=0.0).to(device)
    with torch.no_grad():
        for index, value in enumerate(initial_z):
            table.rows[index].copy_(value.to(table.rows[index]))
    parameter_groups: list[dict[str, Any]] = [
        {"params": list(table.parameters()), "lr": float(inversion_cfg["latent_learning_rate"])}
    ]
    if train_decoder:
        parameter_groups.append(
            {
                "params": list(decoder.parameters()),
                "lr": float(inversion_cfg["joint_decoder_learning_rate"]),
            }
        )
    optimizer = torch.optim.AdamW(parameter_groups, weight_decay=0.0)
    counts = [0] * len(pair_ids)
    history: list[dict[str, Any]] = []
    latest_pointer = output_dir / "latest_checkpoint.json"
    completed = 0
    if latest_pointer.exists():
        path = Path(_load_json(latest_pointer)["checkpoint"])
        payload = torch.load(path, map_location="cpu", weights_only=False)
        resume_checks = {
            "pair_ids": payload.get("pair_ids") == list(pair_ids),
            "name": payload.get("name") == name,
            "train_decoder": payload.get("train_decoder") == train_decoder,
            "initial_decoder": payload.get("initial_decoder_state_sha256")
            == initial_decoder_hash,
            "inversion_identity": payload.get("inversion_identity_sha256")
            == inversion_identity_sha256,
        }
        if not all(resume_checks.values()):
            raise ValueError(f"inversion resume identity differs: {resume_checks}")
        table.load_state_dict(payload["table_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        counts = [int(value) for value in payload["update_counts"]]
        history = list(payload["history"])
        completed = int(payload["completed_rounds"])
        print(f"resumed {name} at u{completed}", flush=True)

    maximum = int(inversion_cfg["optional_final_updates_per_pair"])
    schedule = _inversion_checkpoint_schedule(maximum)
    base_norms_device = base_norms.to(device)
    interval_reports: list[dict[str, float]] = []
    final_plateau = history[-1].get("plateau", {}) if history else {}
    should_continue_to_128 = True
    if completed >= 64 and history:
        at64 = next((item for item in history if int(item["updates_per_pair"]) == 64), None)
        if at64 is not None and at64.get("plateau", {}).get("plateau"):
            should_continue_to_128 = False
    loop_max = maximum if should_continue_to_128 else completed
    for update_round in range(completed + 1, loop_max + 1):
        order = list(range(len(rows)))
        random.Random(seed * 1_000_000 + update_round).shuffle(order)
        for index in order:
            batch_rows = [rows[index]]
            batch = _collate(batch_rows, device=device, k=K_TOKENS)
            z = table.forward_indices([index])
            delta_slots = decoder(z).view(1, K_TOKENS, model_dim)
            student = _forward_direct_delta(
                backend=backend, batch=batch, delta_slots=delta_slots
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
                update_counts=counts,
                base_norms=base_norms_device,
                ratio_budget=1.0,
                train_decoder=train_decoder,
                max_grad_norm=float(inversion_cfg["max_gradient_norm"]),
            )
            interval_reports.append(
                {
                    "loss": float(loss.detach().cpu()),
                    "sequence_utility_huber": float(
                        terms["sequence_utility_huber"].detach().cpu()
                    ),
                    "sparse_teacher_kl": float(terms["sparse_teacher_kl"].detach().cpu()),
                    "gradient_norm": float(step["gradient_norm"]),
                }
            )
        accounting = update_count_summary(pair_ids, counts)
        if not accounting["all_pairs_equal"] or accounting["minimum_updates_per_pair"] != update_round:
            raise RuntimeError(f"inversion update accounting failed: {accounting}")
        if update_round not in schedule:
            continue
        decoded = _decoded_tensor(table, decoder, k=K_TOKENS, model_dim=model_dim)
        evaluation = _evaluate_tensor(
            backend=backend,
            rows=rows,
            tensor=decoded,
            pair_ids=pair_ids,
            device=device,
            huber_delta=objective.huber_delta,
            control=f"{name}_u{update_round}",
        )
        if update_round == 64:
            lag = 32
        elif update_round == 128:
            lag = 64
        else:
            lag = max(1, update_round - (history[-1]["updates_per_pair"] if history else 0))
        entry = {
            "updates_per_pair": update_round,
            "pair_ids": list(pair_ids),
            "update_accounting": accounting,
            "evaluation_summary": evaluation["summary"],
            "train_interval": {
                field: mean_std(item[field] for item in interval_reports)
                for field in (
                    "loss",
                    "sequence_utility_huber",
                    "sparse_teacher_kl",
                    "gradient_norm",
                )
            },
            "decoder_state_sha256": module_state_sha256(decoder),
            "timestamp_utc": utc_now(),
        }
        provisional = [*history, entry]
        entry["plateau"] = assess_plateau(
            provisional, current_updates=update_round, lag=lag
        )
        final_plateau = entry["plateau"]
        history.append(entry)
        rows_path = output_dir / f"evaluation_u{update_round:03d}.jsonl"
        write_jsonl(rows_path, evaluation["rows"])
        checkpoint = output_dir / "checkpoints" / f"{name}_u{update_round:03d}.pt"
        payload = {
            "format": ORACLE_DECODER_VERSION,
            "component": "heldout_inversion",
            "name": name,
            "train_decoder": train_decoder,
            "pair_ids": list(pair_ids),
            "completed_rounds": update_round,
            "update_counts": counts,
            "update_accounting": accounting,
            "table_state_dict": {
                key: value.detach().cpu() for key, value in table.state_dict().items()
            },
            "decoder_state_dict": {
                key: value.detach().cpu() for key, value in decoder.state_dict().items()
            },
            "optimizer_state_dict": optimizer.state_dict(),
            "history": history,
            "initial_decoder_state_sha256": initial_decoder_hash,
            "inversion_identity": inversion_identity,
            "inversion_identity_sha256": inversion_identity_sha256,
            "source_commit": maybe_git_commit(),
        }
        atomic_torch_save(payload, checkpoint)
        atomic_write_json(
            latest_pointer,
            {"checkpoint": str(checkpoint), "updates_per_pair": update_round},
        )
        atomic_write_json(output_dir / "history.json", history)
        print(
            f"{name} u{update_round} spearman="
            f"{evaluation['summary']['u_text_vs_u_student_spearman']:.6f} "
            f"huber={evaluation['summary']['sequence_utility_huber']['mean']:.6f} "
            f"plateau={entry['plateau'].get('plateau')}",
            flush=True,
        )
        interval_reports = []
        if update_round == 64 and entry["plateau"].get("plateau"):
            break

    final_updates = int(history[-1]["updates_per_pair"])
    final_tensor = _decoded_tensor(table, decoder, k=K_TOKENS, model_dim=model_dim)
    final_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=final_tensor,
        pair_ids=pair_ids,
        device=device,
        huber_delta=objective.huber_delta,
        control=f"{name}_final_u{final_updates}",
    )
    final_rows_path = output_dir / "final_rows.jsonl"
    portable = _portable_evaluation(final_evaluation, rows_path=final_rows_path)
    final_decoder_hash = module_state_sha256(decoder)
    frozen_unchanged = train_decoder or final_decoder_hash == initial_decoder_hash
    if not frozen_unchanged:
        raise RuntimeError("frozen decoder final hash differs from initialization")
    result = {
        "name": name,
        "train_decoder": train_decoder,
        "pair_count": len(pair_ids),
        "final_updates_per_pair": final_updates,
        "final_update_accounting": update_count_summary(pair_ids, counts),
        "history": history,
        "final_plateau": final_plateau,
        "initial_decoder_state_sha256": initial_decoder_hash,
        "final_decoder_state_sha256": final_decoder_hash,
        "inversion_identity_sha256": inversion_identity_sha256,
        "frozen_decoder_unchanged": frozen_unchanged,
        "final_evaluation": portable,
        "latest_checkpoint": _load_json(latest_pointer)["checkpoint"],
    }
    atomic_write_json(output_dir / "summary.json", result)
    return {**result, "final_evaluation_runtime": final_evaluation, "decoder": decoder, "table": table}


def _random_delta_like(delta: Tensor, *, seed: int) -> Tensor:
    generator = torch.Generator(device="cpu").manual_seed(seed)
    random_delta = torch.randn(delta.shape, generator=generator, dtype=torch.float32)
    target_norms = delta.to(torch.float32).flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_norms = random_delta.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_delta.mul_((target_norms / random_norms).view(-1, 1, 1))
    return random_delta


def _random_z_delta(
    *,
    decoder: nn.Module,
    pair_count: int,
    base_norms: Tensor,
    seed: int,
    model_dim: int,
    device: torch.device,
) -> Tensor:
    generator = torch.Generator(device="cpu").manual_seed(seed)
    z = torch.randn(pair_count, LATENT_DIM, generator=generator, dtype=torch.float32).to(device)
    project_latents_to_output_ratio_(z, decoder, base_norms, max_ratio=1.0)
    with torch.no_grad():
        return decoder(z).view(pair_count, K_TOKENS, model_dim).detach().cpu()


def _paired_ci_suite(
    *,
    model_rows: Sequence[dict[str, Any]],
    zero_rows: Sequence[dict[str, Any]],
    random_rows: Sequence[dict[str, Any]],
    full_rows: Sequence[dict[str, Any]],
    samples: int,
    seed: int,
) -> dict[str, Any]:
    mean_huber = lambda rows: sum(float(row["sequence_utility_huber"]) for row in rows) / len(rows)

    def sign(rows: Sequence[dict[str, Any]]) -> float:
        values = [row for row in rows if abs(float(row["u_text"])) > 0.01]
        return sum(
            (float(row["u_text"]) > 0) == (float(row["u_student"]) > 0)
            for row in values
        ) / len(values)

    def utility_spearman(rows: Sequence[dict[str, Any]]) -> float:
        return float(
            spearman(
                [float(row["u_text"]) for row in rows],
                [float(row["u_student"]) for row in rows],
            )
            or 0.0
        )

    return {
        "model_minus_zero_huber": paired_bootstrap_difference(
            model_rows, zero_rows, statistic=mean_huber, samples=samples, seed=seed + 1
        ),
        "model_minus_random_huber": paired_bootstrap_difference(
            model_rows, random_rows, statistic=mean_huber, samples=samples, seed=seed + 2
        ),
        "model_minus_full_huber": paired_bootstrap_difference(
            model_rows, full_rows, statistic=mean_huber, samples=samples, seed=seed + 3
        ),
        "model_minus_zero_sign": paired_bootstrap_difference(
            model_rows, zero_rows, statistic=sign, samples=samples, seed=seed + 4
        ),
        "model_minus_random_spearman": paired_bootstrap_difference(
            model_rows,
            random_rows,
            statistic=utility_spearman,
            samples=samples,
            seed=seed + 5,
        ),
    }


def _run_decoder_target(
    *,
    target_name: str,
    target: Mapping[str, Any],
    manifest: Mapping[str, Any],
    all_rows: Sequence[dict[str, Any]],
    all_pair_ids: Sequence[str],
    all_base_norms: Tensor,
    backend: Any,
    device: torch.device,
    model_dim: int,
    objective: Any,
    settings: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    target_dir = output_dir / target_name
    target_dir.mkdir(parents=True, exist_ok=True)
    pair_to_index = {str(pair_id): index for index, pair_id in enumerate(all_pair_ids)}
    row_by_id = {str(row["pair_id"]): row for row in all_rows}
    target_flat = flatten_delta(target["tensor"])
    tensor_version = str(settings["tensor_training"]["version"])
    fold_results = []
    pooled_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for fold_spec in manifest["folds"]:
        fold = int(fold_spec["fold"])
        fold_dir = target_dir / f"fold_{fold}"
        fold_dir.mkdir(parents=True, exist_ok=True)
        train_ids = [pair_id for pair_id in all_pair_ids if pair_id in set(fold_spec["train_pair_ids"])]
        heldout_ids = [
            pair_id for pair_id in all_pair_ids if pair_id in set(fold_spec["heldout_pair_ids"])
        ]
        if len(train_ids) < LATENT_DIM:
            raise ValueError(f"fold {fold} has only {len(train_ids)} train pairs for rank 128")
        train_indices = [pair_to_index[pair_id] for pair_id in train_ids]
        heldout_indices = [pair_to_index[pair_id] for pair_id in heldout_ids]
        train_target = target_flat[train_indices]
        heldout_target = target_flat[heldout_indices]
        heldout_delta = target["tensor"][heldout_indices]
        heldout_rows = [row_by_id[pair_id] for pair_id in heldout_ids]
        heldout_base_norms = all_base_norms[heldout_indices]

        _, _, train_vh = torch.linalg.svd(train_target.to(device), full_matrices=False)
        basis = train_vh[:LATENT_DIM].detach()
        heldout_svd_z = heldout_target.to(device) @ basis.T
        heldout_svd_delta = (heldout_svd_z @ basis).view_as(heldout_delta).detach().cpu()
        heldout_svd_delta, heldout_svd_projection = minimally_project_delta_to_ratio(
            heldout_svd_delta, base_norms=heldout_base_norms
        )

        linear_decoder, linear_tensor = _train_tensor_decoder(
            architecture="linear",
            train_pair_ids=train_ids,
            train_target=train_target,
            basis=basis,
            settings=settings,
            device=device,
            seed=int(settings["split_seed"]) + fold * 101,
            output_dir=fold_dir / f"tensor_linear_{tensor_version}",
        )
        mlp_decoder, mlp_tensor = _train_tensor_decoder(
            architecture="mlp",
            train_pair_ids=train_ids,
            train_target=train_target,
            basis=basis,
            settings=settings,
            device=device,
            seed=int(settings["split_seed"]) + fold * 101 + 17,
            output_dir=fold_dir / f"tensor_mlp_{tensor_version}",
        )

        control_tensors = {
            "full_direct": heldout_delta,
            "rank128_svd": heldout_svd_delta,
            "zero": torch.zeros_like(heldout_delta),
            "matched_random": _random_delta_like(
                heldout_delta, seed=int(settings["split_seed"]) + fold * 1000 + 1
            ),
            "random_z_linear": _random_z_delta(
                decoder=linear_decoder,
                pair_count=len(heldout_ids),
                base_norms=heldout_base_norms,
                seed=int(settings["split_seed"]) + fold * 1000 + 2,
                model_dim=model_dim,
                device=device,
            ),
            "random_z_mlp": _random_z_delta(
                decoder=mlp_decoder,
                pair_count=len(heldout_ids),
                base_norms=heldout_base_norms,
                seed=int(settings["split_seed"]) + fold * 1000 + 3,
                model_dim=model_dim,
                device=device,
            ),
        }
        controls = {}
        for control, tensor in control_tensors.items():
            evaluation = _evaluate_tensor(
                backend=backend,
                rows=heldout_rows,
                tensor=tensor,
                pair_ids=heldout_ids,
                device=device,
                huber_delta=objective.huber_delta,
                control=f"{target_name}_fold{fold}_{control}",
            )
            controls[control] = _portable_evaluation(
                evaluation, rows_path=fold_dir / f"control_{control}_rows.jsonl"
            )
            pooled_rows[control].extend(evaluation["rows"])

        linear_inversion = _run_inversion(
            name="frozen_linear",
            decoder_source=linear_decoder,
            initial_z=heldout_svd_z.detach().cpu(),
            rows=heldout_rows,
            pair_ids=heldout_ids,
            base_norms=heldout_base_norms,
            backend=backend,
            device=device,
            model_dim=model_dim,
            objective=objective,
            settings=settings,
            seed=int(settings["split_seed"]) + fold * 1000 + 10,
            output_dir=fold_dir / "inversion_frozen_linear",
            train_decoder=False,
        )
        mlp_inversion = _run_inversion(
            name="frozen_mlp",
            decoder_source=mlp_decoder,
            initial_z=torch.zeros(len(heldout_ids), LATENT_DIM),
            rows=heldout_rows,
            pair_ids=heldout_ids,
            base_norms=heldout_base_norms,
            backend=backend,
            device=device,
            model_dim=model_dim,
            objective=objective,
            settings=settings,
            seed=int(settings["split_seed"]) + fold * 1000 + 20,
            output_dir=fold_dir / "inversion_frozen_mlp",
            train_decoder=False,
        )
        joint_inversion = _run_inversion(
            name="joint_mlp_oracle",
            decoder_source=mlp_decoder,
            initial_z=torch.zeros(len(heldout_ids), LATENT_DIM),
            rows=heldout_rows,
            pair_ids=heldout_ids,
            base_norms=heldout_base_norms,
            backend=backend,
            device=device,
            model_dim=model_dim,
            objective=objective,
            settings=settings,
            seed=int(settings["split_seed"]) + fold * 1000 + 30,
            output_dir=fold_dir / "inversion_joint_mlp",
            train_decoder=True,
        )
        pooled_rows["frozen_linear"].extend(
            linear_inversion["final_evaluation_runtime"]["rows"]
        )
        pooled_rows["frozen_mlp"].extend(
            mlp_inversion["final_evaluation_runtime"]["rows"]
        )
        pooled_rows["joint_mlp"].extend(
            joint_inversion["final_evaluation_runtime"]["rows"]
        )
        fold_result = {
            "fold": fold,
            "train_pair_count": len(train_ids),
            "heldout_pair_count": len(heldout_ids),
            "train_pair_ids_sha256": json_sha256(train_ids),
            "heldout_pair_ids_sha256": json_sha256(heldout_ids),
            "train_memory_coverage": fold_spec["train_memory_indices"],
            "heldout_svd_projection": heldout_svd_projection,
            "linear_tensor_reconstruction": linear_tensor,
            "mlp_tensor_reconstruction": mlp_tensor,
            "controls": controls,
            "frozen_linear": {key: value for key, value in linear_inversion.items() if key not in {"final_evaluation_runtime", "decoder", "table"}},
            "frozen_mlp": {key: value for key, value in mlp_inversion.items() if key not in {"final_evaluation_runtime", "decoder", "table"}},
            "joint_mlp": {key: value for key, value in joint_inversion.items() if key not in {"final_evaluation_runtime", "decoder", "table"}},
        }
        fold_results.append(fold_result)
        atomic_write_json(fold_dir / "fold_summary.json", fold_result)
        del linear_inversion, mlp_inversion, joint_inversion
        del linear_decoder, mlp_decoder
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    pooled = {}
    for name, rows in pooled_rows.items():
        pooled[name] = _pooled_evaluation(
            rows, rows_path=target_dir / "pooled" / f"{name}_rows.jsonl"
        )
    fold_zero = [fold["controls"]["zero"]["summary"] for fold in fold_results]
    linear_gate = frozen_decoder_capacity_gate(
        pooled_summary=pooled["frozen_linear"]["summary"],
        pooled_zero_summary=pooled["zero"]["summary"],
        fold_summaries=[fold["frozen_linear"]["final_evaluation"]["summary"] for fold in fold_results],
        fold_zero_summaries=fold_zero,
        plateau_by_fold=[fold["frozen_linear"]["final_plateau"].get("plateau", False) for fold in fold_results],
    )
    mlp_gate = frozen_decoder_capacity_gate(
        pooled_summary=pooled["frozen_mlp"]["summary"],
        pooled_zero_summary=pooled["zero"]["summary"],
        fold_summaries=[fold["frozen_mlp"]["final_evaluation"]["summary"] for fold in fold_results],
        fold_zero_summaries=fold_zero,
        plateau_by_fold=[fold["frozen_mlp"]["final_plateau"].get("plateau", False) for fold in fold_results],
    )
    joint_gate = frozen_decoder_capacity_gate(
        pooled_summary=pooled["joint_mlp"]["summary"],
        pooled_zero_summary=pooled["zero"]["summary"],
        fold_summaries=[fold["joint_mlp"]["final_evaluation"]["summary"] for fold in fold_results],
        fold_zero_summaries=fold_zero,
        plateau_by_fold=[fold["joint_mlp"]["final_plateau"].get("plateau", False) for fold in fold_results],
    )
    bootstrap = {
        architecture: _paired_ci_suite(
            model_rows=pooled[architecture]["rows"],
            zero_rows=pooled["zero"]["rows"],
            random_rows=pooled["matched_random"]["rows"],
            full_rows=pooled["full_direct"]["rows"],
            samples=int(settings["bootstrap_samples"]),
            seed=int(settings["split_seed"]) + offset,
        )
        for architecture, offset in (
            ("rank128_svd", 50),
            ("frozen_linear", 100),
            ("frozen_mlp", 200),
            ("joint_mlp", 300),
        )
    }
    result = {
        "target": target_name,
        "folds": fold_results,
        "pooled": {
            key: {inner: value for inner, value in item.items() if inner != "rows"}
            for key, item in pooled.items()
        },
        "gates": {
            "frozen_linear": linear_gate,
            "frozen_mlp": mlp_gate,
            "joint_mlp": joint_gate,
        },
        "paired_bootstrap": bootstrap,
    }
    atomic_write_json(target_dir / "decoder_results.json", result)
    return result


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-016C Shared-Decoder Capacity",
        "",
        f"Status: `{summary['status']}`",
        f"Source commit: `{summary['source_commit']}`",
        f"Artifact: `{summary['output_dir']}`",
        "",
        "## Direct Targets",
        "",
    ]
    for target, validation in summary["source_validation"].items():
        reproduction = validation["metric_reproduction"]
        lines.append(
            f"- {target}: `{validation['checkpoint']}`, shape "
            f"`{validation['validation']['shape']}`, reproduction max delta "
            f"`{reproduction['maximum_absolute_delta']:.8g}`."
        )
    lines.extend(["", "## Low Rank", ""])
    for target, analysis in summary["low_rank"].items():
        lines.append(
            f"- {target}: rank128 gate `{analysis['rank128_capacity_gate']['passed']}`, "
            f"rank192 reproduction `{analysis['rank192_implementation_check']['passed']}`."
        )
    lines.extend(["", "## Decoder Gates", ""])
    for target, result in summary["decoder_results"].items():
        gates = result["gates"]
        lines.append(
            f"- {target}: linear `{gates['frozen_linear']['passed']}`, MLP "
            f"`{gates['frozen_mlp']['passed']}`, joint MLP "
            f"`{gates['joint_mlp']['passed']}`."
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "```json",
            json.dumps(summary["decision"], indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _smoke() -> dict[str, Any]:
    generator = torch.Generator().manual_seed(13)
    delta = torch.randn(12, 4, 16, generator=generator)
    reconstruction = uncentered_svd_reconstruction(delta, 12)["delta"]
    rows = [
        {
            "pair_id": f"p{index}",
            "state_example_id": f"s{index // 2}",
            "selection_category": ("positive", "neutral", "negative", "random")[index % 4],
            "memory_stage_index": index % 6,
        }
        for index in range(12)
    ]
    manifest = state_grouped_three_fold_manifest(rows, seed=13)
    linear = LinearDeltaDecoder(128, 64)
    mlp = MLPDeltaDecoder(128, 64)
    return {
        "rank12_exact": torch.allclose(delta, reconstruction, atol=1.0e-5, rtol=1.0e-5),
        "manifest_validation": manifest["validation"],
        "linear_zero": bool(torch.equal(linear(torch.zeros(1, 128)), torch.zeros(1, 64))),
        "mlp_zero": bool(torch.equal(mlp(torch.zeros(1, 128)), torch.zeros(1, 64))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pair-cache-dir", type=Path, required=True)
    parser.add_argument("--stage5fb-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    settings = _stage_settings(args.config)
    if args.smoke:
        print(json.dumps(_smoke(), indent=2, sort_keys=True))
        return

    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    save_resolved_config(cfg, args.output_dir / "resolved_config.yaml")
    atomic_write_json(args.output_dir / "stage_c_5fc_settings.json", settings)

    source_summary_path = args.stage5fb_dir / "summary.json"
    pair_cache_path = args.pair_cache_dir / "pair_response_cache.jsonl"
    pair_cache_summary_path = args.pair_cache_dir / "pair_response_cache_summary.json"
    required = [source_summary_path, pair_cache_path, pair_cache_summary_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing EXP-016C inputs: {missing}")
    source_summary = _load_json(source_summary_path)
    pair_ids = [str(value) for value in source_summary["pair_ids"]]
    if len(pair_ids) != 192 or len(set(pair_ids)) != 192:
        raise ValueError("EXP-016C requires exactly 192 unique ordered pair IDs")
    pair_rows = _load_rows(pair_cache_path)
    if any(row.get("format") != PAIR_RESPONSE_CACHE_VERSION for row in pair_rows):
        raise ValueError("unexpected pair-cache format")
    target_identity = validate_target_token_utility_identity(pair_rows)
    if not target_identity["passed"]:
        raise ValueError(f"target-token utility identity failed: {target_identity}")
    pair_cache_summary = _load_json(pair_cache_summary_path)
    if not pair_cache_summary.get("validation", {}).get("passed"):
        raise ValueError("pair response cache validation is not passed")

    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    device = backend.device
    model_dim = int(backend.model.config.hidden_size)
    model_identity = _model_identity(backend, pair_cache_summary)
    if not model_identity["passed"]:
        raise ValueError(f"model identity failed: {model_identity}")
    context_limit = _context_limit_for_backend(backend)
    selected_raw = _select_by_pair_ids(pair_rows, pair_ids)
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    tokenized = _build_tokenized_pair_rows(
        backend=backend,
        examples=examples,
        pair_rows=selected_raw,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=context_limit,
    )
    rows = _select_by_pair_ids(tokenized, pair_ids)
    prompt_contract = _student_prompt_contract(rows)
    pair_only_contract = assert_pair_only_input_contract(rows)
    if not prompt_contract["passed"] or not pair_only_contract["passed"]:
        raise ValueError(
            f"student input contract failed: {prompt_contract} {pair_only_contract}"
        )
    objective = OBJECTIVES_5FA["sequence_utility_plus_sparse_kl"]
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=K_TOKENS
    ).cpu()

    checkpoint_dir = args.stage5fb_dir / "checkpoints"
    targets = {}
    source_validation = {}
    for name, updates in (
        ("u112", PRIMARY_TARGET_UPDATES),
        ("u128", ROBUSTNESS_TARGET_UPDATES),
    ):
        checkpoint = (
            checkpoint_dir
            / f"direct_sequence_utility_plus_sparse_kl_ratio1.0_u{updates:03d}.pt"
        )
        loaded = _load_and_validate_target(
            checkpoint=checkpoint,
            updates=updates,
            expected_pair_ids=pair_ids,
            model_dim=model_dim,
            source_summary=source_summary,
            backend=backend,
            rows=rows,
            device=device,
            huber_delta=objective.huber_delta,
            tolerance=float(settings["reproduction_tolerance"]),
            output_dir=args.output_dir / "source_validation",
        )
        targets[name] = loaded
        source_validation[name] = {
            key: value
            for key, value in loaded.items()
            if key not in {"tensor", "evaluation"}
        }
    if source_validation["u112"]["validation"]["model_identity"] != source_validation[
        "u128"
    ]["validation"]["model_identity"]:
        raise ValueError("u112/u128 model identities differ")
    atomic_write_json(
        args.output_dir / "source_checkpoint_validation.json", source_validation
    )

    low_rank = {}
    for name in ("u112", "u128"):
        low_rank[name] = _low_rank_analysis(
            target_name=name,
            target=targets[name],
            backend=backend,
            rows=rows,
            pair_ids=pair_ids,
            base_norms=base_norms,
            device=device,
            huber_delta=objective.huber_delta,
            output_dir=args.output_dir / "low_rank",
            tolerance=float(settings["reproduction_tolerance"]),
        )
        if not low_rank[name]["rank192_implementation_check"]["passed"]:
            failure = {
                "format": ORACLE_DECODER_VERSION,
                "status": "stopped_rank192_implementation_error",
                "target": name,
                "low_rank": low_rank[name],
                "source_commit": maybe_git_commit(),
            }
            atomic_write_json(args.output_dir / "summary.json", failure)
            raise RuntimeError(f"rank192 failed to reproduce direct behavior for {name}")

    manifest = state_grouped_three_fold_manifest(
        rows, seed=int(settings["split_seed"])
    )
    if not all(
        len(fold["train_pair_ids"]) >= LATENT_DIM and fold["train_covers_all_memories"]
        for fold in manifest["folds"]
    ):
        raise ValueError("decoder folds do not provide rank128 train capacity/all-memory coverage")
    atomic_write_json(args.output_dir / "decoder_split_manifest.json", manifest)

    decoder_results = {}
    for name in ("u112", "u128"):
        decoder_results[name] = _run_decoder_target(
            target_name=name,
            target=targets[name],
            manifest=manifest,
            all_rows=rows,
            all_pair_ids=pair_ids,
            all_base_norms=base_norms,
            backend=backend,
            device=device,
            model_dim=model_dim,
            objective=objective,
            settings=settings,
            output_dir=args.output_dir / "decoders",
        )

    primary_gates = decoder_results["u112"]["gates"]
    primary_decision = decoder_decision(
        rank128_passed=bool(low_rank["u112"]["rank128_capacity_gate"]["passed"]),
        rank192_reproduced=bool(
            low_rank["u112"]["rank192_implementation_check"]["passed"]
        ),
        linear_passed=bool(primary_gates["frozen_linear"]["passed"]),
        mlp_passed=bool(primary_gates["frozen_mlp"]["passed"]),
        joint_mlp_passed=bool(primary_gates["joint_mlp"]["passed"]),
    )
    robustness_gates = decoder_results["u128"]["gates"]
    robustness_decision = decoder_decision(
        rank128_passed=bool(low_rank["u128"]["rank128_capacity_gate"]["passed"]),
        rank192_reproduced=bool(
            low_rank["u128"]["rank192_implementation_check"]["passed"]
        ),
        linear_passed=bool(robustness_gates["frozen_linear"]["passed"]),
        mlp_passed=bool(robustness_gates["frozen_mlp"]["passed"]),
        joint_mlp_passed=bool(robustness_gates["joint_mlp"]["passed"]),
    )
    decision = {
        "primary_u112": primary_decision,
        "robustness_u128": robustness_decision,
        "main_conclusion_reproduced_on_u128": primary_decision["branch"]
        == robustness_decision["branch"],
        "stage5fb_artifacts_rewritten": False,
        "stage_c2_started": False,
    }
    summary = {
        "format": ORACLE_DECODER_VERSION,
        "status": "completed",
        "source_commit": maybe_git_commit(),
        "output_dir": str(args.output_dir),
        "source_stage5fb": str(args.stage5fb_dir),
        "pair_cache": str(args.pair_cache_dir),
        "pair_count": len(pair_ids),
        "pair_ids_sha256": json_sha256(pair_ids),
        "model_identity": model_identity,
        "prompt_contract": prompt_contract,
        "pair_only_input_contract": pair_only_contract,
        "target_token_utility_identity": target_identity,
        "source_validation": source_validation,
        "prospective_plateau_rule": {
            "version": "prospective_absolute_change_best_guard_v1",
            "u112_best_observed": True,
            "u128_formal_stage5fb_stop": True,
            "stage5fb_artifacts_preserved": True,
        },
        "low_rank": {
            name: {
                key: value
                for key, value in result.items()
                if key != "zero_evaluation_runtime"
            }
            for name, result in low_rank.items()
        },
        "decoder_split_manifest": str(args.output_dir / "decoder_split_manifest.json"),
        "decoder_split_manifest_sha256": manifest["manifest_sha256"],
        "decoder_results": decoder_results,
        "decision": decision,
        "runtime_s": time.perf_counter() - started,
        "timestamp_utc": utc_now(),
        "hard_scope": {
            "qwen_frozen": True,
            "teacher_forced_only": True,
            "selector_used": False,
            "memory_compiler_trained": False,
            "full_bank_used": False,
            "appworld_generation_run": False,
            "stage_c2_started": False,
        },
    }
    atomic_write_json(args.output_dir / "summary.json", summary)
    atomic_write_text(args.output_dir / "report.md", _report(summary))
    print(
        f"EXP-016C complete branch={primary_decision['branch']} "
        f"runtime_h={summary['runtime_s'] / 3600.0:.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
