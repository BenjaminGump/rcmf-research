from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import gc
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

import torch

from rcmf.benchmarks.appworld.transitions import (
    extract_decision_transitions,
    transition_teacher_section,
    validate_transition_extraction,
)
from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.datasets import (
    load_decision_examples,
    load_memory_records,
    render_state_representation_text,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    UtilityPredictor,
    build_grouped_cv_manifest,
    build_two_axis_rows,
    canonical_json_sha256,
    deterministic_parent_split,
    factorized_field_algebra_validation,
    initialize_or_validate_run_manifest,
    predict_utility_rows,
    representation_interaction_gate,
    summarize_two_axis_rows,
    summarize_utility_predictions,
    train_utility_predictor,
    utc_now,
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


MODEL_KINDS = (
    "state_only",
    "transition_only",
    "additive",
    "signed_bilinear",
    "concat_mlp",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _require_count(label: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"{label} count differs: {actual} != {expected}")


def _exp017_validation(
    *,
    exp017_dir: Path,
    data_dir: Path,
    settings: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[Any],
]:
    expected = settings["expected"]
    transition_rows = _load_rows(exp017_dir / "transition_manifest.jsonl")
    panel_rows = _load_rows(exp017_dir / "transition_panel.jsonl")
    teacher_rows = _load_rows(exp017_dir / "teacher_cache.jsonl")
    preflight_rows = _load_rows(exp017_dir / "pair_preflight.jsonl")
    query_manifest = _load_json(exp017_dir / "query_manifest.json")
    postrun = _load_json(exp017_dir / "postrun_validation.json")
    teacher_reproducibility = _load_json(
        exp017_dir / "teacher_reproducibility.json"
    )
    _require_count(
        "extracted transition", len(transition_rows), expected["extracted_transitions"]
    )
    _require_count(
        "panel transition", len(panel_rows), expected["panel_transitions"]
    )
    _require_count(
        "train query", query_manifest["train_query_count"], expected["train_query_states"]
    )
    _require_count(
        "heldout query",
        query_manifest["validation_query_count"],
        expected["heldout_query_states"],
    )
    _require_count(
        "legal teacher row", len(teacher_rows), expected["legal_teacher_rows"]
    )
    scoreable = [row for row in teacher_rows if bool(row.get("valid_for_loss"))]
    over_context = [row for row in teacher_rows if bool(row.get("over_context"))]
    _require_count(
        "scoreable teacher row", len(scoreable), expected["scoreable_teacher_rows"]
    )
    _require_count(
        "over-context teacher row", len(over_context), expected["over_context_rows"]
    )
    if not bool(postrun.get("passed")):
        raise ValueError("EXP-017 post-run validation is not passed")
    if not bool(teacher_reproducibility.get("passed")):
        raise ValueError("EXP-017 deterministic teacher reproducibility is not passed")

    transition_ids = [str(row["transition_id"]) for row in transition_rows]
    panel_ids = [str(row["transition_id"]) for row in panel_rows]
    query_ids = [str(row["state_example_id"]) for row in query_manifest["query_rows"]]
    pair_ids = [str(row["pair_id"]) for row in teacher_rows]
    preflight_ids = [str(row["pair_id"]) for row in preflight_rows]
    duplicate_checks = {
        "transition_ids": len(transition_ids) == len(set(transition_ids)),
        "panel_ids": len(panel_ids) == len(set(panel_ids)),
        "query_ids": len(query_ids) == len(set(query_ids)),
        "teacher_pair_ids": len(pair_ids) == len(set(pair_ids)),
        "preflight_pair_ids": len(preflight_ids) == len(set(preflight_ids)),
    }
    if not all(duplicate_checks.values()):
        raise ValueError(f"EXP-017 duplicate identity check failed: {duplicate_checks}")
    if not set(panel_ids).issubset(transition_ids):
        raise ValueError("EXP-017 panel IDs are not a subset of the full manifest")
    if set(pair_ids) != set(preflight_ids):
        raise ValueError("EXP-017 teacher and preflight pair IDs differ")

    preflight_by_id = {str(row["pair_id"]): row for row in preflight_rows}
    maximum_utility_identity_error = 0.0
    maximum_l0_state_spread = 0.0
    l0_by_state: dict[str, list[float]] = defaultdict(list)
    validation_errors = []
    for row in teacher_rows:
        pair_id = str(row["pair_id"])
        preflight = preflight_by_id[pair_id]
        if bool(row.get("truncated")) or bool(preflight.get("truncated")):
            validation_errors.append(f"truncated:{pair_id}")
        if row.get("leakage_overlap") or preflight.get("leakage_overlap"):
            validation_errors.append(f"leakage:{pair_id}")
        if bool(row.get("over_context")):
            if bool(row.get("valid_for_loss")) or row.get("text_utility") is not None:
                validation_errors.append(f"over_context_not_masked:{pair_id}")
            continue
        if not bool(row.get("valid_for_loss")):
            validation_errors.append(f"scoreable_not_valid:{pair_id}")
            continue
        l0 = float(row["L0"])
        lj = float(row["Lj_transition"])
        utility = float(row["text_utility"])
        if not all(math.isfinite(value) for value in (l0, lj, utility)):
            validation_errors.append(f"nonfinite:{pair_id}")
        maximum_utility_identity_error = max(
            maximum_utility_identity_error, abs((l0 - lj) - utility)
        )
        l0_by_state[str(row["state_example_id"])].append(l0)
    for values in l0_by_state.values():
        maximum_l0_state_spread = max(maximum_l0_state_spread, max(values) - min(values))
    if maximum_utility_identity_error > 1.0e-6:
        validation_errors.append(
            f"utility_identity_max_error:{maximum_utility_identity_error}"
        )
    if maximum_l0_state_spread > 1.0e-6:
        validation_errors.append(f"L0_state_spread:{maximum_l0_state_spread}")

    records = load_memory_records(data_dir / "memory_records.jsonl")
    parent_ids = {str(row["parent_memory_id"]) for row in transition_rows}
    parent_records = [record for record in records if record.memory_id in parent_ids]
    _require_count("transition parent", len(parent_records), 37)
    reconstructed = [
        transition
        for record in parent_records
        for transition in extract_decision_transitions(record)
    ]
    reconstruction = validate_transition_extraction(parent_records, reconstructed)
    if not reconstruction["passed"]:
        validation_errors.append("transition_reconstruction_failed")
    expected_by_id = {str(row["transition_id"]): row for row in transition_rows}
    reconstructed_by_id = {
        transition.transition_id: transition.to_manifest_row()
        for transition in reconstructed
    }
    if set(expected_by_id) != set(reconstructed_by_id):
        validation_errors.append("reconstructed_transition_ids_differ")
    else:
        for transition_id, source in expected_by_id.items():
            rebuilt = reconstructed_by_id[transition_id]
            for key in (
                "parent_memory_id",
                "parent_task_id",
                "parent_episode_id",
                "source_task_goal_sha256",
                "canonical_pre_action_state_sha256",
                "complete_action_sha256",
                "complete_post_action_observation_sha256",
                "transition_content_sha256",
            ):
                if source[key] != rebuilt[key]:
                    validation_errors.append(f"transition_hash:{transition_id}:{key}")
                    break

    report = {
        "format": "exp017_reuse_validation_6b_v1",
        "timestamp_utc": utc_now(),
        "passed": not validation_errors,
        "errors_first_100": validation_errors[:100],
        "counts": {
            "transitions": len(transition_rows),
            "panel_transitions": len(panel_rows),
            "queries": len(query_ids),
            "legal_teacher_rows": len(teacher_rows),
            "scoreable_teacher_rows": len(scoreable),
            "over_context_rows": len(over_context),
        },
        "duplicate_checks": duplicate_checks,
        "leakage_exclusion_passed": not any(
            row.get("leakage_overlap") for row in teacher_rows
        ),
        "no_truncation": not any(bool(row.get("truncated")) for row in teacher_rows),
        "maximum_target_token_utility_identity_error": maximum_utility_identity_error,
        "maximum_L0_spread_within_state": maximum_l0_state_spread,
        "teacher_reproducibility": teacher_reproducibility,
        "transition_reconstruction": reconstruction,
        "hashes": {
            "transition_manifest_sha256": sha256_file(
                exp017_dir / "transition_manifest.jsonl"
            ),
            "transition_panel_sha256": sha256_file(
                exp017_dir / "transition_panel.jsonl"
            ),
            "query_manifest_sha256": sha256_file(
                exp017_dir / "query_manifest.json"
            ),
            "pair_preflight_sha256": sha256_file(
                exp017_dir / "pair_preflight.jsonl"
            ),
            "teacher_cache_sha256": sha256_file(exp017_dir / "teacher_cache.jsonl"),
        },
    }
    if validation_errors:
        raise RuntimeError(f"EXP-017 reuse validation failed: {validation_errors[:20]}")
    return report, panel_rows, teacher_rows, query_manifest, parent_records


def _state_representation_cache(
    *,
    backend: Any,
    examples: Sequence[Any],
    query_manifest: Mapping[str, Any],
    teacher_rows: Sequence[Mapping[str, Any]],
    source_cache_path: Path,
    decision_examples_path: Path,
    prompt_profile: str,
    output_path: Path,
) -> tuple[torch.Tensor, list[str], dict[str, Any]]:
    if backend.tokenizer is None:
        raise RuntimeError(
            "Canonical tokenizer is required for state prompt hash validation"
        )
    source = torch.load(source_cache_path, map_location="cpu", weights_only=False)
    if source.get("format") != "pooled_qwen_hidden_v1":
        raise ValueError(f"Unexpected state representation format: {source.get('format')}")
    if source.get("model_name") != str(backend.model_name):
        raise ValueError("State representation model identity differs")
    if source.get("source_sha256") != sha256_file(decision_examples_path):
        raise ValueError("State representation source hash differs")
    representations = source["representations"].to(torch.float32)
    if tuple(representations.shape) != (len(examples), 4096):
        raise ValueError(f"Unexpected state representation shape: {tuple(representations.shape)}")
    base_hash_by_state: dict[str, set[str]] = defaultdict(set)
    for row in teacher_rows:
        base_hash_by_state[str(row["state_example_id"])].add(
            str(row["base_prompt_sha256"])
        )
    ordered_ids = [
        str(row["state_example_id"]) for row in query_manifest["query_rows"]
    ]
    selected = []
    metadata_rows = []
    for query in query_manifest["query_rows"]:
        state_id = str(query["state_example_id"])
        example_index = int(query["example_index"])
        rendered = render_state_representation_text(
            backend.tokenizer, examples[example_index], prompt_profile
        )
        rendered_hash = sha256_text(rendered)
        if base_hash_by_state[state_id] != {rendered_hash}:
            raise ValueError(f"Canonical state prompt hash differs for {state_id}")
        representation = representations[example_index]
        selected.append(representation)
        metadata_rows.append(
            {
                "state_example_id": state_id,
                "example_index": example_index,
                "task_id": str(query["task_id"]),
                "split": str(query["split"]),
                "prompt_tokens": int(query["prompt_tokens"]),
                "prompt_sha256": rendered_hash,
                "representation_sha256": tensor_state_sha256(
                    {"representation": representation}
                ),
                "renderer": "render_state_representation_text/full_demo",
                "future_target_action_used": False,
            }
        )
    tensor = torch.stack(selected, dim=0)
    payload = {
        "format": "frozen_qwen_query_state_representation_cache_6b_v1",
        "model_name": str(backend.model_name),
        "source_cache": str(source_cache_path),
        "source_cache_sha256": sha256_file(source_cache_path),
        "source_decision_examples_sha256": sha256_file(decision_examples_path),
        "ordered_state_example_ids": ordered_ids,
        "representations": tensor,
        "rows": metadata_rows,
        "representation_tensor_sha256": tensor_state_sha256(
            {"representations": tensor}
        ),
        "target_action_exclusion": (
            "renderer consumes state_text/history only; target_text is not an input"
        ),
    }
    atomic_torch_save(payload, output_path)
    report = {key: value for key, value in payload.items() if key != "representations"}
    report["shape"] = list(tensor.shape)
    return tensor, ordered_ids, report


def _transition_representation_cache(
    *,
    backend: Any,
    panel_rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
    attempt: AttemptLedger,
    batch_size: int,
) -> tuple[torch.Tensor, list[str], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    row_dir = output_dir / "rows"
    row_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(panel_rows, key=lambda row: str(row["transition_id"]))
    representations = []
    metadata_rows = []
    reused = 0
    computed = 0
    for position, row in enumerate(ordered, start=1):
        transition_id = str(row["transition_id"])
        text = transition_teacher_section(dict(row))
        text_hash = sha256_text(text)
        if text_hash != str(row["teacher_section_sha256"]):
            raise ValueError(f"Transition renderer hash differs for {transition_id}")
        row_path = row_dir / f"{transition_id}.pt"
        payload = None
        if row_path.exists():
            candidate = torch.load(row_path, map_location="cpu", weights_only=False)
            checks = (
                candidate.get("format")
                == "frozen_qwen_transition_representation_row_6b_v1"
                and candidate.get("transition_id") == transition_id
                and candidate.get("transition_content_sha256")
                == str(row["transition_content_sha256"])
                and candidate.get("teacher_section_sha256") == text_hash
                and candidate.get("model_name") == str(backend.model_name)
            )
            if checks:
                payload = candidate
                reused += 1
            else:
                raise ValueError(
                    f"Existing transition representation row is incompatible: {row_path}"
                )
        if payload is None:
            chunk_representations, owners, token_counts = (
                backend.encode_text_chunks_with_metadata(
                    [text], batch_size=int(batch_size), add_special_tokens=True
                )
            )
            if set(owners.tolist()) != {0}:
                raise RuntimeError("Single-transition representation owner index differs")
            weights = token_counts.to(torch.float32).unsqueeze(-1)
            representation = (
                chunk_representations.to(torch.float32) * weights
            ).sum(dim=0) / weights.sum().clamp_min(1.0)
            payload = {
                "format": "frozen_qwen_transition_representation_row_6b_v1",
                "transition_id": transition_id,
                "parent_memory_id": str(row["parent_memory_id"]),
                "model_name": str(backend.model_name),
                "model_config_commit_hash": getattr(
                    backend.model.config, "_commit_hash", None
                ),
                "renderer_version": "decision_transition_teacher_section_v1",
                "aggregation": "token_weighted_mean_over_complete_chunks",
                "source_task_goal_sha256": str(row["source_task_goal_sha256"]),
                "canonical_pre_action_state_sha256": str(
                    row["canonical_pre_action_state_sha256"]
                ),
                "complete_action_sha256": str(row["complete_action_sha256"]),
                "complete_post_action_observation_sha256": str(
                    row["complete_post_action_observation_sha256"]
                ),
                "transition_content_sha256": str(
                    row["transition_content_sha256"]
                ),
                "teacher_section_sha256": text_hash,
                "token_count": int(token_counts.sum().item()),
                "chunk_count": int(token_counts.numel()),
                "chunk_token_counts": [int(value) for value in token_counts.tolist()],
                "representation": representation.detach().cpu(),
                "representation_sha256": tensor_state_sha256(
                    {"representation": representation.detach().cpu()}
                ),
                "truncated": False,
                "created_at_utc": utc_now(),
            }
            atomic_torch_save(payload, row_path)
            computed += 1
        representations.append(payload["representation"].to(torch.float32))
        metadata_rows.append(
            {key: value for key, value in payload.items() if key != "representation"}
        )
        attempt.progress(
            status="encoding_transition_representations",
            completed=position,
            total=len(ordered),
            reused=reused,
            newly_computed=computed,
            latest_validated_checkpoint=str(row_path),
        )
    tensor = torch.stack(representations, dim=0)
    ordered_ids = [str(row["transition_id"]) for row in ordered]
    aggregate = {
        "format": "frozen_qwen_transition_representation_cache_6b_v1",
        "model_name": str(backend.model_name),
        "renderer_version": "decision_transition_teacher_section_v1",
        "aggregation": "token_weighted_mean_over_complete_chunks",
        "ordered_transition_ids": ordered_ids,
        "representations": tensor,
        "rows": metadata_rows,
        "representation_tensor_sha256": tensor_state_sha256(
            {"representations": tensor}
        ),
        "reused_row_count": reused,
        "newly_computed_row_count": computed,
        "created_at_utc": utc_now(),
    }
    aggregate_path = output_dir / "transition_representations.pt"
    atomic_torch_save(aggregate, aggregate_path)
    report = {key: value for key, value in aggregate.items() if key != "representations"}
    report["shape"] = list(tensor.shape)
    report["token_count_distribution"] = {
        "min": min(int(row["token_count"]) for row in metadata_rows),
        "mean": statistics.fmean(int(row["token_count"]) for row in metadata_rows),
        "max": max(int(row["token_count"]) for row in metadata_rows),
    }
    report["multi_chunk_count"] = sum(
        int(row["chunk_count"]) > 1 for row in metadata_rows
    )
    return tensor, ordered_ids, report


def _model_seed(base: int, kind: str, fold: int, epochs: int) -> int:
    digest = hashlib.sha256(
        f"{base}:{kind}:{fold}:{epochs}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big")


def _new_predictor(kind: str, settings: Mapping[str, Any], representation_dim: int) -> UtilityPredictor:
    return UtilityPredictor(
        kind,
        state_dim=representation_dim,
        transition_dim=representation_dim,
        hidden_dim=int(settings["hidden_dim"]),
        interaction_dim=int(settings["interaction_dim"]),
        dropout=float(settings["dropout"]),
    )


def _train_or_load_predictor(
    *,
    checkpoint: Path,
    kind: str,
    rows: Sequence[Mapping[str, Any]],
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    epochs: int,
    seed: int,
    device: torch.device,
    metadata: Mapping[str, Any],
) -> tuple[UtilityPredictor, dict[str, Any], bool]:
    model = _new_predictor(kind, settings, int(state_representations.shape[1]))
    if checkpoint.exists():
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        expected = {
            "kind": kind,
            "epochs": int(epochs),
            "pair_ids_sha256": sha256_text(
                "\n".join(sorted(str(row["pair_id"]) for row in rows))
            ),
            **dict(metadata),
        }
        if all(payload.get("metadata", {}).get(key) == value for key, value in expected.items()):
            model.load_state_dict(payload["model_state_dict"])
            return model.to(device).eval(), payload["training"], True
        raise ValueError(f"Existing predictor checkpoint is incompatible: {checkpoint}")
    torch.manual_seed(int(seed))
    training = train_utility_predictor(
        model=model,
        rows=rows,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        epochs=int(epochs),
        batch_size=int(settings["batch_size"]),
        learning_rate=float(settings["learning_rate"]),
        weight_decay=float(settings["weight_decay"]),
        huber_delta=float(settings["huber_delta"]),
        seed=int(seed),
        device=device,
    )
    optimizer_state = training.pop("optimizer_state_dict")
    payload = {
        "format": "state_transition_utility_predictor_checkpoint_6b_v1",
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer_state,
        "training": training,
        "metadata": {
            "kind": kind,
            "epochs": int(epochs),
            "pair_ids_sha256": sha256_text(
                "\n".join(sorted(str(row["pair_id"]) for row in rows))
            ),
            **dict(metadata),
        },
    }
    atomic_torch_save(payload, checkpoint)
    return model.eval(), training, False


def _run_cheap_gate(
    *,
    rows: Sequence[Mapping[str, Any]],
    state_representations: torch.Tensor,
    transition_representations: torch.Tensor,
    state_ids: Sequence[str],
    transition_ids: Sequence[str],
    settings: Mapping[str, Any],
    output_dir: Path,
    attempt: AttemptLedger,
    representation_hashes: Mapping[str, str],
    device: torch.device,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_position = {str(value): index for index, value in enumerate(state_ids)}
    transition_position = {
        str(value): index for index, value in enumerate(transition_ids)
    }
    cell_rows = {
        cell: [row for row in rows if str(row["cell"]) == cell]
        for cell in (CELL_A, CELL_B, CELL_C, CELL_D)
    }
    train_rows = cell_rows[CELL_A]
    cv_manifest = build_grouped_cv_manifest(
        train_rows, folds=int(settings["folds"]), seed=int(settings["seed"])
    )
    atomic_write_json(output_dir / "grouped_cv_manifest.json", cv_manifest)
    row_by_pair = {str(row["pair_id"]): row for row in train_rows}
    cv_results: dict[str, Any] = {}
    checkpoint_root = output_dir / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    common_metadata = {
        "state_representation_sha256": str(representation_hashes["state"]),
        "transition_representation_sha256": str(
            representation_hashes["transition"]
        ),
        "cv_manifest_sha256": str(cv_manifest["manifest_sha256"]),
    }
    total_jobs = len(MODEL_KINDS) * len(settings["epoch_candidates"]) * int(
        settings["folds"]
    )
    completed_jobs = 0
    for kind in MODEL_KINDS:
        candidates = []
        for epochs in [int(value) for value in settings["epoch_candidates"]]:
            folds = []
            for fold in cv_manifest["folds"]:
                fold_index = int(fold["fold"])
                fold_train = [row_by_pair[value] for value in fold["train_pair_ids"]]
                fold_validation = [
                    row_by_pair[value] for value in fold["validation_pair_ids"]
                ]
                checkpoint = (
                    checkpoint_root
                    / "cv"
                    / kind
                    / f"fold_{fold_index}_epochs_{epochs}.pt"
                )
                model, training, reused = _train_or_load_predictor(
                    checkpoint=checkpoint,
                    kind=kind,
                    rows=fold_train,
                    state_representations=state_representations,
                    transition_representations=transition_representations,
                    state_position=state_position,
                    transition_position=transition_position,
                    settings=settings,
                    epochs=epochs,
                    seed=_model_seed(int(settings["seed"]), kind, fold_index, epochs),
                    device=device,
                    metadata={**common_metadata, "fold": fold_index},
                )
                predicted = predict_utility_rows(
                    model=model,
                    rows=fold_validation,
                    state_representations=state_representations,
                    transition_representations=transition_representations,
                    state_position=state_position,
                    transition_position=transition_position,
                    device=device,
                )
                summary = summarize_utility_predictions(
                    predicted, huber_delta=float(settings["huber_delta"])
                )
                folds.append(
                    {
                        "fold": fold_index,
                        "train_count": len(fold_train),
                        "validation_count": len(fold_validation),
                        "training": training,
                        "validation": summary,
                        "checkpoint": str(checkpoint),
                        "checkpoint_sha256": sha256_file(checkpoint),
                        "reused": reused,
                    }
                )
                completed_jobs += 1
                attempt.progress(
                    status="cheap_gate_cross_validation",
                    completed_jobs=completed_jobs,
                    total_jobs=total_jobs,
                    model_kind=kind,
                    fold=fold_index,
                    epochs=epochs,
                    latest_validated_checkpoint=str(checkpoint),
                )
            spearman_values = [
                float(item["validation"]["u_text_vs_prediction_spearman"] or -1.0)
                for item in folds
            ]
            sign_values = [
                float(item["validation"]["positive_negative_sign_agreement"] or 0.0)
                for item in folds
            ]
            huber_values = [float(item["validation"]["huber"]) for item in folds]
            candidates.append(
                {
                    "epochs": epochs,
                    "folds": folds,
                    "mean_spearman": statistics.fmean(spearman_values),
                    "mean_sign_agreement": statistics.fmean(sign_values),
                    "mean_huber": statistics.fmean(huber_values),
                }
            )
        selected = max(
            candidates,
            key=lambda item: (
                float(item["mean_spearman"]),
                float(item["mean_sign_agreement"]),
                -float(item["mean_huber"]),
                -int(item["epochs"]),
            ),
        )
        cv_results[kind] = {
            "candidates": candidates,
            "selected_epochs": int(selected["epochs"]),
            "selection_rule": "max_mean_spearman_then_sign_then_negative_huber_then_fewer_epochs",
        }
        atomic_write_json(output_dir / "cv_results.json", cv_results)

    model_results: dict[str, Any] = {}
    prediction_root = output_dir / "predictions"
    for kind in MODEL_KINDS:
        epochs = int(cv_results[kind]["selected_epochs"])
        checkpoint = checkpoint_root / "final" / f"{kind}.pt"
        model, training, reused = _train_or_load_predictor(
            checkpoint=checkpoint,
            kind=kind,
            rows=train_rows,
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_position=state_position,
            transition_position=transition_position,
            settings=settings,
            epochs=epochs,
            seed=_model_seed(int(settings["seed"]), kind, -1, epochs),
            device=device,
            metadata={**common_metadata, "fold": "all_cell_a"},
        )
        kind_results = {
            "selected_epochs": epochs,
            "training": training,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": sha256_file(checkpoint),
            "reused": reused,
            "cells": {},
        }
        for cell in (CELL_A, CELL_B, CELL_C, CELL_D):
            controls = {}
            for control_index, control in enumerate(
                (
                    "correct",
                    "shuffled_state",
                    "shuffled_transition",
                    "both_shuffled",
                    "mean_state",
                    "mean_transition",
                )
            ):
                predicted = predict_utility_rows(
                    model=model,
                    rows=cell_rows[cell],
                    state_representations=state_representations,
                    transition_representations=transition_representations,
                    state_position=state_position,
                    transition_position=transition_position,
                    device=device,
                    control=control,
                    seed=int(settings["seed"]) + control_index,
                )
                path = prediction_root / kind / cell / f"{control}.jsonl"
                write_jsonl(path, predicted)
                controls[control] = {
                    **summarize_utility_predictions(
                        predicted, huber_delta=float(settings["huber_delta"])
                    ),
                    "rows_path": str(path),
                    "rows_sha256": sha256_file(path),
                }
            kind_results["cells"][cell] = {"controls": controls}
        model_results[kind] = kind_results
        attempt.progress(
            status="cheap_gate_final_models",
            model_kind=kind,
            latest_validated_checkpoint=str(checkpoint),
        )
        atomic_write_json(output_dir / "model_results.json", model_results)

    global_mean = statistics.fmean(float(row["text_utility"]) for row in train_rows)
    global_results = {}
    for cell in (CELL_A, CELL_B, CELL_C, CELL_D):
        predicted = predict_utility_rows(
            model=None,
            rows=cell_rows[cell],
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_position=state_position,
            transition_position=transition_position,
            device=device,
            global_mean=global_mean,
        )
        global_results[cell] = summarize_utility_predictions(
            predicted, huber_delta=float(settings["huber_delta"])
        )

    gate_inputs = {
        kind: {
            control: model_results[kind]["cells"][CELL_D]["controls"][control]
            for control in model_results[kind]["cells"][CELL_D]["controls"]
        }
        for kind in MODEL_KINDS
    }
    thresholds = settings["gate"]
    gate = representation_interaction_gate(
        model_results=gate_inputs,
        minimum_spearman=float(thresholds["minimum_spearman"]),
        minimum_sign_agreement=float(thresholds["minimum_sign_agreement"]),
        minimum_baseline_spearman_gain=float(
            thresholds["minimum_baseline_spearman_gain"]
        ),
        minimum_shuffle_spearman_drop=float(
            thresholds["minimum_shuffle_spearman_drop"]
        ),
    )
    report = {
        "format": "state_transition_cheap_interaction_report_6b_v1",
        "timestamp_utc": utc_now(),
        "cv_manifest": cv_manifest,
        "cv_results": cv_results,
        "global_train_utility_mean": global_mean,
        "global_results": global_results,
        "models": model_results,
        "gate": gate,
        "selection_labels_used": CELL_A,
        "heldout_labels_used_for_selection": False,
    }
    atomic_write_json(output_dir / "cheap_interaction_report.json", report)
    return report


def _markdown_report(summary: Mapping[str, Any]) -> str:
    cells = summary["two_axis_summary"]
    cheap = summary["cheap_gate"]
    lines = [
        "# EXP-018 Part A-D: State-Transition Representation Gate",
        "",
        f"- status: `{summary['status']}`",
        f"- branch: `{summary['decision_branch']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- EXP-017 reuse validation: `{summary['exp017_validation']['passed']}`",
        f"- field algebra: `{summary['field_algebra']['passed']}`",
        "",
        "## Two-Axis Cells",
        "",
        "| Cell | Pairs | States | Transitions | Parents | Utility mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cell in (CELL_A, CELL_B, CELL_C, CELL_D):
        item = cells[cell]
        lines.append(
            f"| {cell} | {item['pair_count']} | {item['state_count']} | "
            f"{item['transition_count']} | {item['transition_parent_count']} | "
            f"{item['utility']['mean']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Double-Held-Out Representation Metrics",
            "",
            "| Model | Spearman | Sign | Huber | State shuffle Spearman | Transition shuffle Spearman |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for kind in MODEL_KINDS:
        controls = cheap["models"][kind]["cells"][CELL_D]["controls"]
        correct = controls["correct"]
        lines.append(
            f"| {kind} | {float(correct['u_text_vs_prediction_spearman'] or 0):.6f} | "
            f"{float(correct['positive_negative_sign_agreement'] or 0):.6f} | "
            f"{float(correct['huber']):.6f} | "
            f"{float(controls['shuffled_state']['u_text_vs_prediction_spearman'] or 0):.6f} | "
            f"{float(controls['shuffled_transition']['u_text_vs_prediction_spearman'] or 0):.6f} |"
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "```json",
            json.dumps(cheap["gate"], indent=2, sort_keys=True),
            "```",
            "",
            "No Qwen behavioral backpropagation, selector training, full-bank field, "
            "AppWorld generation/evaluation, Stage C2, end-to-end training, or V4 tag "
            "was performed in Parts A-D.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare EXP-018 representations and run the cheap interaction gate."
    )
    parser.add_argument(
        "--config", default="configs/benchmark/stage_c_state_conditioned_transition_6b.yaml"
    )
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", required=True)
    return parser.parse_args()


def _resume_provenance(artifact_dir: Path, attempt_id: str) -> tuple[str | None, str | None]:
    attempts_path = artifact_dir / "attempts.jsonl"
    if not attempts_path.exists():
        return None, None
    attempts = list(read_jsonl(attempts_path))
    if any(str(row.get("attempt_id")) == str(attempt_id) for row in attempts):
        raise ValueError(f"Attempt ID already exists in append-only ledger: {attempt_id}")
    prior = [row for row in attempts if row.get("event") in {"start", "end"}]
    if not prior:
        return None, None
    last = prior[-1]
    checkpoint = last.get("latest_validated_checkpoint")
    if checkpoint is None:
        heartbeat_path = artifact_dir / "heartbeat.json"
        if heartbeat_path.exists():
            checkpoint = _load_json(heartbeat_path).get("latest_validated_checkpoint")
    return str(last.get("attempt_id")), None if checkpoint is None else str(checkpoint)


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6b"]
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.config)
    exp017_dir = Path(settings["exp017_artifact"])
    data_dir = Path(settings["source_data"])
    state_cache_path = Path(settings["source_state_representation_cache"])
    decoder_checkpoint = Path(settings["source_decoder_checkpoint"])
    source_paths = {
        "config": config_path,
        "decision_examples": data_dir / "decision_examples.jsonl",
        "memory_records": data_dir / "memory_records.jsonl",
        "transition_manifest": exp017_dir / "transition_manifest.jsonl",
        "transition_panel": exp017_dir / "transition_panel.jsonl",
        "query_manifest": exp017_dir / "query_manifest.json",
        "pair_preflight": exp017_dir / "pair_preflight.jsonl",
        "teacher_cache": exp017_dir / "teacher_cache.jsonl",
        "teacher_summary": exp017_dir / "teacher_summary.json",
        "behavior_summary": exp017_dir / "behavior_summary.json",
        "postrun_validation": exp017_dir / "postrun_validation.json",
        "state_representation_cache": state_cache_path,
        "decoder_checkpoint": decoder_checkpoint,
    }
    missing = [str(path) for path in source_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing EXP-018 source files: {missing}")
    data_hashes = {
        key: sha256_file(path) for key, path in source_paths.items() if key != "config"
    }
    config_sha256 = sha256_file(config_path)
    run_manifest = initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_sha256,
        data_manifest_hashes=data_hashes,
        source_commit=str(args.lambda_head),
        command_scope=["exp017_validation", "representations", "cheap_gate"],
    )
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    completed_summary = args.artifact_dir / "parts_a_d_summary.json"
    if completed_summary.exists():
        existing = _load_json(completed_summary)
        if existing.get("status") in {
            "passed_ready_for_behavioral_preflight",
            "stopped_at_representation_gate",
        }:
            print(
                json.dumps(
                    {
                        "status": "reused_completed_parts_a_d",
                        "branch": existing.get("decision_branch"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return
    parent_attempt_id, resume_checkpoint = _resume_provenance(
        args.artifact_dir, args.attempt_id
    )
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="parts_a_to_d",
        command=sys.argv,
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=parent_attempt_id,
        resume_checkpoint=resume_checkpoint,
        heartbeat_interval_s=float(
            settings["representations"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        attempt.progress(status="validating_exp017")
        exp017_validation, panel_rows, teacher_rows, query_manifest, _parents = (
            _exp017_validation(
                exp017_dir=exp017_dir, data_dir=data_dir, settings=settings
            )
        )
        atomic_write_json(
            args.artifact_dir / "exp017_reuse_validation.json", exp017_validation
        )
        parent_split = deterministic_parent_split(
            panel_rows,
            seed=int(settings["transition_split"]["seed"]),
            train_parent_count=int(settings["transition_split"]["train_parents"]),
            heldout_parent_count=int(
                settings["transition_split"]["heldout_parents"]
            ),
        )
        atomic_write_json(
            args.artifact_dir / "transition_parent_split_manifest.json", parent_split
        )
        pair_rows = build_two_axis_rows(
            teacher_rows=teacher_rows,
            panel_rows=panel_rows,
            query_manifest=query_manifest,
            parent_split=parent_split,
        )
        two_axis_summary = summarize_two_axis_rows(pair_rows)
        two_axis_manifest = {
            "format": "state_transition_two_axis_manifest_6b_v1",
            "timestamp_utc": utc_now(),
            "transition_parent_split": parent_split,
            "query_manifest_sha256": data_hashes["query_manifest"],
            "teacher_cache_sha256": data_hashes["teacher_cache"],
            "pair_count": len(pair_rows),
            "cells": two_axis_summary,
            "selection_labels": CELL_A,
            "heldout_cells_used_for_selection": False,
        }
        two_axis_manifest["manifest_sha256"] = canonical_json_sha256(
            two_axis_manifest
        )
        write_jsonl(args.artifact_dir / "two_axis_pair_rows.jsonl", pair_rows)
        atomic_write_json(
            args.artifact_dir / "two_axis_split_manifest.json", two_axis_manifest
        )
        field_algebra = factorized_field_algebra_validation(
            seed=int(settings["seed"])
        )
        atomic_write_json(args.artifact_dir / "field_algebra_validation.json", field_algebra)
        if not field_algebra["passed"]:
            raise RuntimeError("Factorized transition field algebra validation failed")

        attempt.progress(status="loading_frozen_qwen")
        backend = build_backend(cfg, load_model=True)
        if any(parameter.requires_grad for parameter in backend.model.parameters()):
            raise RuntimeError("Qwen parameters are not fully frozen")
        examples = load_decision_examples(data_dir / "decision_examples.jsonl")
        representation_dir = args.artifact_dir / "representation_cache"
        representation_dir.mkdir(parents=True, exist_ok=True)
        state_representations, state_ids, state_report = _state_representation_cache(
            backend=backend,
            examples=examples,
            query_manifest=query_manifest,
            teacher_rows=teacher_rows,
            source_cache_path=state_cache_path,
            decision_examples_path=data_dir / "decision_examples.jsonl",
            prompt_profile=cfg.benchmark.prompt_profile,
            output_path=representation_dir / "query_state_representations.pt",
        )
        transition_representations, transition_ids, transition_report = (
            _transition_representation_cache(
                backend=backend,
                panel_rows=panel_rows,
                output_dir=representation_dir,
                attempt=attempt,
                batch_size=int(settings["representations"]["batch_size"]),
            )
        )
        representation_report = {
            "format": "frozen_state_transition_representation_report_6b_v1",
            "timestamp_utc": utc_now(),
            "qwen_frozen": True,
            "query_state": state_report,
            "transition": transition_report,
            "validation_parent_trajectories_excluded": True,
            "query_future_target_action_excluded": True,
            "no_truncation": all(
                not bool(row.get("truncated")) for row in transition_report["rows"]
            ),
        }
        atomic_write_json(
            args.artifact_dir / "representation_cache_report.json",
            representation_report,
        )
        representation_hashes = {
            "state": str(state_report["representation_tensor_sha256"]),
            "transition": str(transition_report["representation_tensor_sha256"]),
        }
        del backend
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        attempt.progress(status="running_cheap_gate", device=str(device))
        cheap_gate = _run_cheap_gate(
            rows=pair_rows,
            state_representations=state_representations,
            transition_representations=transition_representations,
            state_ids=state_ids,
            transition_ids=transition_ids,
            settings=settings["cheap_gate"],
            output_dir=args.artifact_dir / "cheap_gate",
            attempt=attempt,
            representation_hashes=representation_hashes,
            device=device,
        )
        branch = str(cheap_gate["gate"]["branch"])
        status = (
            "passed_ready_for_behavioral_preflight"
            if cheap_gate["gate"]["proceed_to_behavioral_training"]
            else "stopped_at_representation_gate"
        )
        summary = {
            "format": "state_conditioned_transition_parts_a_d_summary_6b_v1",
            "status": status,
            "decision_branch": branch,
            "timestamp_utc": utc_now(),
            "source_commit": maybe_git_commit(),
            "run_uuid": str(settings["run_uuid"]),
            "run_manifest": run_manifest,
            "exp017_validation": exp017_validation,
            "transition_parent_split": parent_split,
            "two_axis_summary": two_axis_summary,
            "representation_report": representation_report,
            "cheap_gate": cheap_gate,
            "field_algebra": field_algebra,
            "runtime_seconds": time.perf_counter() - started,
            "hard_scope": {
                "qwen_frozen": True,
                "qwen_behavioral_backpropagation_run": False,
                "selector_trained": False,
                "production_full_bank_constructed": False,
                "appworld_generation_or_evaluation_run": False,
                "stage_c2_started": False,
                "end_to_end_rcmf_started": False,
                "v4_tag_created": False,
            },
        }
        atomic_write_json(args.artifact_dir / "parts_a_d_summary.json", summary)
        atomic_write_text(args.artifact_dir / "parts_a_d_report.md", _markdown_report(summary))
        attempt.progress(
            status=status,
            decision_branch=branch,
            latest_validated_checkpoint=str(
                args.artifact_dir / "parts_a_d_summary.json"
            ),
        )
        print(json.dumps({"status": status, "branch": branch}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
