from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.cross_encoder_6c import (
    CROSS_ENCODER_CACHE_VERSION,
    CROSS_ENCODER_MODEL_VERSION,
    CROSS_ENCODER_VIEW_NAMES,
    CrossEncoderResidualHead,
    controlled_feature_matrix,
    cross_encoder_control_sources,
    cross_encoder_prompt_and_char_spans,
    cross_encoder_tensor_hash,
    exact_training_base_scores,
    feature_normalization,
    frozen_qwen_cross_encoder_readouts,
    normalize_features,
    train_cross_encoder_head,
)
from rcmf.training.datasets import load_decision_examples
from rcmf.training.interaction_representation_6c import (
    fit_two_way_decomposition,
    interaction_gate,
    paired_task_bootstrap_contrast,
    per_task_gate_metrics,
    summarize_revised_predictions,
    task_grouped_bootstrap,
)
from rcmf.training.multiview_models_6c import (
    ViewSetMainEffectHeads,
    train_view_main_effects,
)
from rcmf.training.multiview_representations_6c import (
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    tokenize_and_validate_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    utc_now,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)


CELLS = (CELL_A, CELL_B, CELL_C, CELL_D)
CONTROLS = (
    "correct",
    "shuffled_state",
    "shuffled_transition",
    "both_shuffled",
    "mean_state",
    "mean_transition",
    "zero_interaction",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _seed(base: int, *parts: Any) -> int:
    value = ":".join(str(item) for item in (base, *parts))
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:4], "big")


def _strip_per_state(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("per_state_rows", None)
    return result


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["metrics"]["ranking_ks"],
        "neutral_epsilon": float(settings["metrics"]["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
        "huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
    }


def _bootstrap_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return _metric_kwargs(settings)


def _validate_inputs(
    *,
    settings: Mapping[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    part_c_d = _load_json(artifact_dir / "parts_c_d_summary.json")
    if part_c_d["decision_after_part_d"] != "continue_to_prompt_only_cross_encoder":
        raise RuntimeError("Part C/D did not authorize the prompt-only cross-encoder")
    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    expected = settings["expected"]
    if len(pair_rows) != int(expected["scoreable_rows"]):
        raise ValueError("EXP-019 scoreable pair count differs before Part E")
    counts = {
        cell: sum(str(row["cell"]) == cell for row in pair_rows) for cell in CELLS
    }
    if counts != {str(key): int(value) for key, value in expected["cells"].items()}:
        raise ValueError(f"EXP-019 cells differ before Part E: {counts}")
    pair_ids = [str(row["pair_id"]) for row in pair_rows]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("EXP-019 pair rows contain duplicate pair IDs")
    preflight_rows = _load_rows(exp017 / "pair_preflight.jsonl")
    preflight = {str(row["pair_id"]): row for row in preflight_rows}
    if len(preflight) != len(preflight_rows):
        raise ValueError("EXP-017 preflight contains duplicate pair IDs")
    for row in pair_rows:
        source = preflight.get(str(row["pair_id"]))
        if source is None:
            raise ValueError(f"Pair is absent from EXP-017 preflight: {row['pair_id']}")
        if bool(source["over_context"]) or source["leakage_overlap"]:
            raise ValueError(f"Illegal/over-context scoreable pair: {row['pair_id']}")
        if bool(row["truncated"]) or bool(row["over_context"]):
            raise ValueError(
                f"Scoreable row was truncated/over-context: {row['pair_id']}"
            )
    hashes = {
        "parts_c_d_summary": sha256_file(artifact_dir / "parts_c_d_summary.json"),
        "two_axis_pair_rows": sha256_file(exp018 / "two_axis_pair_rows.jsonl"),
        "pair_preflight": sha256_file(exp017 / "pair_preflight.jsonl"),
        "transition_panel": sha256_file(exp017 / "transition_panel.jsonl"),
        "query_manifest": sha256_file(exp017 / "query_manifest.json"),
    }
    return {
        "format": "prompt_only_cross_encoder_input_validation_6c_v1",
        "passed": True,
        "scoreable_pair_count": len(pair_rows),
        "preflight_legal_pair_count": len(preflight_rows),
        "cell_counts": counts,
        "no_duplicates": True,
        "no_leakage": True,
        "no_over_context": True,
        "no_truncation": True,
        "hashes": hashes,
        "validated_at_utc": utc_now(),
    }


def _prompt_preflight(
    *,
    tokenizer: Any,
    examples: Sequence[Any],
    pair_rows: Sequence[Mapping[str, Any]],
    preflight_by_pair: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    prompt_tokens = []
    exact_span_count = 0
    aligned_span_count = 0
    ordered = sorted(pair_rows, key=lambda row: str(row["pair_id"]))
    for position, row in enumerate(ordered, start=1):
        pair_id = str(row["pair_id"])
        prompt, char_spans, metadata = cross_encoder_prompt_and_char_spans(
            tokenizer,
            examples[int(row["state_index"])],
            transitions[str(row["transition_id"])],
            prompt_profile,
        )
        source = preflight_by_pair[pair_id]
        if sha256_text(prompt) != str(source["teacher_prompt_sha256"]):
            raise ValueError(f"Cross-encoder preflight prompt hash differs: {pair_id}")
        input_ids, _, span_rows = tokenize_and_validate_char_spans(
            tokenizer, prompt, char_spans
        )
        token_count = int(input_ids.shape[1])
        if token_count != int(source["combined_prompt_tokens"]):
            raise ValueError(f"Cross-encoder preflight token count differs: {pair_id}")
        if token_count + int(row["target_tokens"]) > int(source["context_limit"]):
            raise ValueError(f"Cross-encoder preflight exceeds context: {pair_id}")
        if metadata["target_action_accessed"] or bool(row["truncated"]):
            raise ValueError(f"Cross-encoder preflight violates hard scope: {pair_id}")
        prompt_tokens.append(token_count)
        exact_span_count += sum(
            bool(value["decoded_text_exact_match"]) for value in span_rows.values()
        )
        aligned_span_count += sum(
            bool(value["decoded_matches_aligned_source"])
            for value in span_rows.values()
        )
        if position % 25 == 0 or position == len(ordered):
            attempt.progress(
                status="preflighting_prompt_only_cross_encoder_pairs",
                completed=position,
                total=len(ordered),
                latest_validated_checkpoint=str(
                    attempt.artifact_dir / "part_e/input_validation.json"
                ),
            )
    span_count = len(ordered) * len(CROSS_ENCODER_VIEW_NAMES)
    if aligned_span_count != span_count:
        raise ValueError("Not all cross-encoder spans decode to aligned source text")
    return {
        "format": "prompt_only_cross_encoder_token_preflight_6c_v1",
        "pair_count": len(ordered),
        "span_count": span_count,
        "decoded_exact_span_count": exact_span_count,
        "decoded_aligned_source_span_count": aligned_span_count,
        "all_decoded_aligned_source_valid": True,
        "prompt_tokens": {
            "min": min(prompt_tokens),
            "mean": statistics.fmean(prompt_tokens),
            "max": max(prompt_tokens),
        },
        "over_context_count": 0,
        "truncated_count": 0,
        "target_action_accessed": False,
        "passed": True,
    }


def _pair_cache(
    *,
    backend: Any,
    examples: Sequence[Any],
    pair_rows: Sequence[Mapping[str, Any]],
    preflight_by_pair: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
    renderer_version: str,
    output_dir: Path,
    source_commit: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, torch.Tensor], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    features: dict[str, torch.Tensor] = {}
    reused = 0
    computed = 0
    prompt_tokens = []
    span_counts = {name: [] for name in CROSS_ENCODER_VIEW_NAMES}
    model_commit = getattr(backend.model.config, "_commit_hash", None)
    ordered = sorted(pair_rows, key=lambda row: str(row["pair_id"]))
    for position, row in enumerate(ordered, start=1):
        pair_id = str(row["pair_id"])
        transition = transitions[str(row["transition_id"])]
        prompt, char_spans, source_metadata = cross_encoder_prompt_and_char_spans(
            backend.tokenizer,
            examples[int(row["state_index"])],
            transition,
            prompt_profile,
        )
        preflight = preflight_by_pair[pair_id]
        prompt_hash = sha256_text(prompt)
        if prompt_hash != str(preflight["teacher_prompt_sha256"]):
            raise ValueError(f"Cross-encoder teacher prompt hash differs: {pair_id}")
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, prompt, char_spans
        )
        token_count = int(input_ids.shape[1])
        if token_count != int(preflight["combined_prompt_tokens"]):
            raise ValueError(f"Cross-encoder prompt token count differs: {pair_id}")
        if token_count + int(row["target_tokens"]) > int(preflight["context_limit"]):
            raise ValueError(
                f"Cross-encoder pair unexpectedly exceeds context: {pair_id}"
            )
        expected = {
            "format": f"{CROSS_ENCODER_CACHE_VERSION}_row",
            "pair_id": pair_id,
            "state_example_id": str(row["state_example_id"]),
            "transition_id": str(row["transition_id"]),
            "teacher_prompt_sha256": prompt_hash,
            "base_prompt_sha256": str(row["base_prompt_sha256"]),
            "transition_content_sha256": str(row["transition_content_sha256"]),
            "renderer_version": renderer_version,
            "teacher_renderer_version": str(preflight["renderer_version"]),
            "transition_renderer_version": str(
                preflight["transition_renderer_version"]
            ),
            "model_name": str(backend.model_name),
            "model_config_commit_hash": model_commit,
        }
        row_path = output_dir / f"{sha256_text(pair_id)}.pt"
        payload = None
        if row_path.exists():
            candidate = torch.load(row_path, map_location="cpu", weights_only=False)
            if any(candidate.get(key) != value for key, value in expected.items()):
                raise ValueError(
                    f"Existing cross-encoder cache row differs: {row_path}"
                )
            values = candidate.get("representations")
            if not isinstance(values, torch.Tensor) or tuple(values.shape) != (3, 4096):
                raise ValueError(
                    f"Existing cross-encoder tensor shape differs: {row_path}"
                )
            if cross_encoder_tensor_hash(values) != str(candidate["tensor_sha256"]):
                raise ValueError(
                    f"Existing cross-encoder tensor hash differs: {row_path}"
                )
            payload = candidate
            reused += 1
        if payload is None:
            values = frozen_qwen_cross_encoder_readouts(
                model=backend.model,
                input_ids=input_ids,
                attention_mask=attention_mask,
                span_rows=span_rows,
                device=backend.device,
            ).to(torch.float16)
            payload = {
                **expected,
                "representations": values,
                "tensor_sha256": cross_encoder_tensor_hash(values),
                "view_names": list(CROSS_ENCODER_VIEW_NAMES),
                "pooling_rules": {
                    "generation_boundary": "final_token",
                    "transition_section_mean": "token_mean",
                    "current_task_span_mean": "token_mean",
                },
                "combined_prompt_tokens": token_count,
                "target_tokens_not_encoded": int(row["target_tokens"]),
                "context_limit": int(preflight["context_limit"]),
                "span_rows": span_rows,
                "source_metadata": source_metadata,
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "truncated": False,
                "source_commit": source_commit,
                "created_at_utc": utc_now(),
            }
            atomic_torch_save(payload, row_path)
            computed += 1
        features[pair_id] = payload["representations"].to(torch.float32).flatten()
        prompt_tokens.append(token_count)
        for name in CROSS_ENCODER_VIEW_NAMES:
            span_counts[name].append(int(span_rows[name]["token_count"]))
        attempt.progress(
            status="encoding_prompt_only_cross_encoder_pairs",
            completed=position,
            total=len(ordered),
            reused=reused,
            newly_computed=computed,
            latest_validated_checkpoint=str(row_path),
        )
        del input_ids, attention_mask, payload
        if position % 25 == 0:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
    aggregate_path = output_dir.parent / "cross_encoder_representations.pt"
    matrix = torch.stack([features[str(row["pair_id"])] for row in ordered]).to(
        torch.float16
    )
    aggregate = {
        "format": f"{CROSS_ENCODER_CACHE_VERSION}_aggregate",
        "ordered_pair_ids": [str(row["pair_id"]) for row in ordered],
        "representations": matrix,
        "tensor_sha256": cross_encoder_tensor_hash(matrix),
        "view_names": list(CROSS_ENCODER_VIEW_NAMES),
        "model_name": str(backend.model_name),
        "model_config_commit_hash": model_commit,
        "renderer_version": renderer_version,
        "reused": reused,
        "newly_computed": computed,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(aggregate, aggregate_path)
    report = {
        "format": "prompt_only_cross_encoder_cache_report_6c_v1",
        "cache_version": CROSS_ENCODER_CACHE_VERSION,
        "pair_count": len(ordered),
        "reused": reused,
        "newly_computed": computed,
        "aggregate_path": str(aggregate_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "aggregate_tensor_sha256": aggregate["tensor_sha256"],
        "row_directory": str(output_dir),
        "no_truncation": True,
        "target_action_accessed": False,
        "prompt_tokens": {
            "min": min(prompt_tokens),
            "mean": statistics.fmean(prompt_tokens),
            "max": max(prompt_tokens),
        },
        "span_token_counts": {
            name: {
                "min": min(values),
                "mean": statistics.fmean(values),
                "max": max(values),
            }
            for name, values in span_counts.items()
        },
    }
    return features, report


def multiview_artifact_paths(artifact_dir: Path) -> dict[str, Path]:
    root = Path(artifact_dir) / "parts_c_d"
    cache_root = root / "multiview_cache"
    return {
        "state_cache": cache_root / "state_multiview.pt",
        "transition_cache": cache_root / "transition_multiview.pt",
        "main_checkpoint": root / "checkpoints/main/final_layer.pt",
    }


def cross_encoder_aggregate_path(artifact_dir: Path) -> Path:
    return (
        Path(artifact_dir)
        / "part_e/cross_encoder_cache/cross_encoder_representations.pt"
    )


def _load_multiview_main_inputs(
    artifact_dir: Path,
    settings: Mapping[str, Any],
    device: torch.device,
) -> tuple[Any, dict[str, float], dict[str, float], dict[str, Any]]:
    paths = multiview_artifact_paths(artifact_dir)
    state_cache = torch.load(
        paths["state_cache"], map_location="cpu", weights_only=False
    )
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    state_values = state_cache["representations"]["final_layer"].to(torch.float32)
    transition_values = transition_cache["representations"]["final_layer"].to(
        torch.float32
    )
    state_ids = [str(value) for value in state_cache["ordered_ids"]]
    transition_ids = [str(value) for value in transition_cache["ordered_ids"]]
    main = ViewSetMainEffectHeads(
        state_views=len(STATE_VIEW_NAMES) * len(POOLING_RULES),
        transition_views=len(TRANSITION_VIEW_NAMES) * len(POOLING_RULES),
        input_dim=int(state_values.shape[-1]),
        projection_dim=int(settings["multiview"]["projection_dim"]),
        hidden_dim=int(settings["multiview"]["hidden_dim"]),
        dropout=float(settings["multiview"]["dropout"]),
    )
    checkpoint_path = paths["main_checkpoint"]
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    main.load_state_dict(checkpoint["model_state_dict"])
    main.to(device).eval()
    for parameter in main.parameters():
        parameter.requires_grad_(False)
    with torch.no_grad():
        state_predictions = main.state(state_values.to(device)).cpu().tolist()
        transition_predictions = (
            main.transition(transition_values.to(device)).cpu().tolist()
        )
    metadata = {
        "state_values": state_values,
        "transition_values": transition_values,
        "state_ids": state_ids,
        "transition_ids": transition_ids,
        "state_position": {value: index for index, value in enumerate(state_ids)},
        "transition_position": {
            value: index for index, value in enumerate(transition_ids)
        },
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
    }
    return (
        main,
        dict(zip(state_ids, (float(value) for value in state_predictions))),
        dict(zip(transition_ids, (float(value) for value in transition_predictions))),
        metadata,
    )


def _base_score_maps(
    *,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_main: Mapping[str, float],
    transition_main: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    mu = float(decomposition["mu"])
    full = {}
    state_only = {}
    transition_only = {}
    for row in rows:
        pair_id = str(row["pair_id"])
        state = float(state_main[str(row["state_example_id"])])
        transition = float(transition_main[str(row["transition_id"])])
        full[pair_id] = mu + state + transition
        state_only[pair_id] = mu + state
        transition_only[pair_id] = mu + transition
    return full, state_only, transition_only


def _prediction_rows(
    *,
    model: CrossEncoderResidualHead,
    rows: Sequence[Mapping[str, Any]],
    feature_by_pair: Mapping[str, torch.Tensor],
    normalization: Mapping[str, torch.Tensor],
    base_scores: Mapping[str, float],
    control: str,
    seed: int,
    device: torch.device,
) -> list[dict[str, Any]]:
    if control == "correct":
        source_ids: list[str | None] = [str(row["pair_id"]) for row in rows]
        features = torch.stack([feature_by_pair[str(row["pair_id"])] for row in rows])
    elif control == "zero_interaction":
        source_ids = [None] * len(rows)
        features = None
    else:
        sources = cross_encoder_control_sources(rows, seed=seed)
        source_ids = list(sources[control])
        features = controlled_feature_matrix(
            rows=rows,
            feature_by_pair=feature_by_pair,
            control_sources=sources,
            control=control,
        )
    if features is None:
        interactions = torch.zeros(len(rows), dtype=torch.float32)
    else:
        with torch.no_grad():
            interactions = (
                model(normalize_features(features, normalization).to(device))
                .detach()
                .cpu()
            )
    output = []
    for index, row in enumerate(rows):
        pair_id = str(row["pair_id"])
        base = float(base_scores[pair_id])
        interaction = float(interactions[index])
        output.append(
            {
                "pair_id": pair_id,
                "state_example_id": str(row["state_example_id"]),
                "state_task_id": str(row["state_task_id"]),
                "transition_id": str(row["transition_id"]),
                "transition_parent_id": str(row["transition_parent_id"]),
                "cell": str(row["cell"]),
                "utility_category": str(row["utility_category"]),
                "u_text": float(row["text_utility"]),
                "u_predicted": base + interaction,
                "residual_target": float(row["text_utility"]) - base,
                "residual_predicted": interaction,
                "control": control,
                "control_source_pair_id": source_ids[index],
                "main_effect_score_held_fixed": base,
            }
        )
    return output


def _single_axis_rows(
    rows: Sequence[Mapping[str, Any]],
    scores: Mapping[str, float],
    base_scores: Mapping[str, float],
    control: str,
) -> list[dict[str, Any]]:
    return [
        {
            "pair_id": str(row["pair_id"]),
            "state_example_id": str(row["state_example_id"]),
            "state_task_id": str(row["state_task_id"]),
            "transition_id": str(row["transition_id"]),
            "transition_parent_id": str(row["transition_parent_id"]),
            "cell": str(row["cell"]),
            "utility_category": str(row["utility_category"]),
            "u_text": float(row["text_utility"]),
            "u_predicted": float(scores[str(row["pair_id"])]),
            "residual_target": float(row["text_utility"])
            - float(base_scores[str(row["pair_id"])]),
            "residual_predicted": 0.0,
            "control": control,
        }
        for row in rows
    ]


def _cv_select_epochs(
    *,
    rows_a: Sequence[Mapping[str, Any]],
    cv_manifest: Mapping[str, Any],
    feature_by_pair: Mapping[str, torch.Tensor],
    main_metadata: Mapping[str, Any],
    settings: Mapping[str, Any],
    output_dir: Path,
    device: torch.device,
    attempt: AttemptLedger,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row_by_id = {str(row["pair_id"]): row for row in rows_a}
    state_values = main_metadata["state_values"]
    transition_values = main_metadata["transition_values"]
    state_position = main_metadata["state_position"]
    transition_position = main_metadata["transition_position"]
    candidates = []
    cv_rows = []
    epochs_values = [
        int(value) for value in settings["cross_encoder"]["epoch_candidates"]
    ]
    for epochs in epochs_values:
        fold_metrics = []
        for fold in cv_manifest["folds"]:
            fold_index = int(fold["fold"])
            train_rows = [row_by_id[value] for value in fold["train_pair_ids"]]
            validation_rows = [
                row_by_id[value] for value in fold["validation_pair_ids"]
            ]
            decomposition = fit_two_way_decomposition(
                train_rows,
                max_iterations=int(
                    settings["decomposition"]["alternating_least_squares_iterations"]
                ),
                tolerance=float(settings["decomposition"]["tolerance"]),
            )
            fold_dir = output_dir / f"fold_{fold_index}" / f"epochs_{epochs}"
            main_path = fold_dir / "main.pt"
            head_path = fold_dir / "cross_encoder.pt"
            main = ViewSetMainEffectHeads(
                state_views=len(STATE_VIEW_NAMES) * len(POOLING_RULES),
                transition_views=len(TRANSITION_VIEW_NAMES) * len(POOLING_RULES),
                input_dim=int(state_values.shape[-1]),
                projection_dim=int(settings["multiview"]["projection_dim"]),
                hidden_dim=int(settings["multiview"]["hidden_dim"]),
                dropout=float(settings["multiview"]["dropout"]),
            )
            main_metadata_expected = {
                "format": "cross_encoder_fold_main_6c_v1",
                "fold": fold_index,
                "epochs_candidate": epochs,
                "train_pair_ids_sha256": sha256_text(
                    "\n".join(str(row["pair_id"]) for row in train_rows)
                ),
            }
            if main_path.exists():
                payload = torch.load(main_path, map_location="cpu", weights_only=False)
                if payload["metadata"] != main_metadata_expected:
                    raise ValueError(
                        f"Cross-encoder CV main checkpoint differs: {main_path}"
                    )
                main.load_state_dict(payload["model_state_dict"])
                main_training = payload["training"]
            else:
                main_training = train_view_main_effects(
                    model=main,
                    decomposition=decomposition,
                    state_representations=state_values,
                    transition_representations=transition_values,
                    state_position=state_position,
                    transition_position=transition_position,
                    epochs=int(settings["decomposition"]["main_epochs"]),
                    learning_rate=float(
                        settings["decomposition"]["main_learning_rate"]
                    ),
                    weight_decay=float(settings["decomposition"]["main_weight_decay"]),
                    huber_delta=float(settings["decomposition"]["huber_delta"]),
                    seed=_seed(int(settings["seed"]), "cross-cv-main", fold_index),
                    device=device,
                )
                optimizer_state = main_training.pop("optimizer_state_dict")
                atomic_torch_save(
                    {
                        "metadata": main_metadata_expected,
                        "model_state_dict": {
                            key: value.detach().cpu()
                            for key, value in main.state_dict().items()
                        },
                        "optimizer_state_dict": optimizer_state,
                        "training": main_training,
                    },
                    main_path,
                )
            main.to(device).eval()
            for parameter in main.parameters():
                parameter.requires_grad_(False)
            with torch.no_grad():
                state_main = dict(
                    zip(
                        main_metadata["state_ids"],
                        (
                            float(value)
                            for value in main.state(state_values.to(device))
                            .cpu()
                            .tolist()
                        ),
                    )
                )
                transition_main = dict(
                    zip(
                        main_metadata["transition_ids"],
                        (
                            float(value)
                            for value in main.transition(transition_values.to(device))
                            .cpu()
                            .tolist()
                        ),
                    )
                )
            validation_base, _, _ = _base_score_maps(
                rows=validation_rows,
                decomposition=decomposition,
                state_main=state_main,
                transition_main=transition_main,
            )
            train_features = torch.stack(
                [feature_by_pair[str(row["pair_id"])] for row in train_rows]
            )
            normalization = feature_normalization(train_features)
            head = CrossEncoderResidualHead(
                int(train_features.shape[-1]),
                hidden_dim=int(settings["cross_encoder"]["scalar_head_hidden_dim"]),
                dropout=float(settings["multiview"]["dropout"]),
            )
            head_metadata = {
                "format": f"{CROSS_ENCODER_MODEL_VERSION}_cv_checkpoint",
                "fold": fold_index,
                "epochs": epochs,
                "train_pair_ids_sha256": main_metadata_expected[
                    "train_pair_ids_sha256"
                ],
                "normalization_mean_sha256": cross_encoder_tensor_hash(
                    normalization["mean"]
                ),
                "normalization_std_sha256": cross_encoder_tensor_hash(
                    normalization["std"]
                ),
            }
            if head_path.exists():
                payload = torch.load(head_path, map_location="cpu", weights_only=False)
                if payload["metadata"] != head_metadata:
                    raise ValueError(
                        f"Cross-encoder CV head checkpoint differs: {head_path}"
                    )
                head.load_state_dict(payload["model_state_dict"])
                head_training = payload["training"]
                normalization = payload["normalization"]
            else:
                head_training = train_cross_encoder_head(
                    model=head,
                    rows=train_rows,
                    features=normalize_features(train_features, normalization),
                    base_scores=exact_training_base_scores(train_rows, decomposition),
                    decomposition=decomposition,
                    settings={**settings["current_representation"]},
                    epochs=epochs,
                    seed=_seed(
                        int(settings["seed"]), "cross-cv-head", fold_index, epochs
                    ),
                    device=device,
                )
                optimizer_state = head_training.pop("optimizer_state_dict")
                atomic_torch_save(
                    {
                        "metadata": head_metadata,
                        "model_state_dict": {
                            key: value.detach().cpu()
                            for key, value in head.state_dict().items()
                        },
                        "optimizer_state_dict": optimizer_state,
                        "normalization": {
                            key: value.detach().cpu()
                            for key, value in normalization.items()
                        },
                        "training": head_training,
                    },
                    head_path,
                )
            head.to(device).eval()
            predicted = _prediction_rows(
                model=head,
                rows=validation_rows,
                feature_by_pair=feature_by_pair,
                normalization=normalization,
                base_scores=validation_base,
                control="correct",
                seed=_seed(int(settings["seed"]), "cross-cv-eval", fold_index),
                device=device,
            )
            metrics = _strip_per_state(
                summarize_revised_predictions(predicted, **_metric_kwargs(settings))
            )
            fold_row = {
                "epochs": epochs,
                "fold": fold_index,
                "train_pair_count": len(train_rows),
                "validation_pair_count": len(validation_rows),
                "validation_metrics": metrics,
                "main_checkpoint": str(main_path),
                "head_checkpoint": str(head_path),
            }
            fold_metrics.append(metrics)
            cv_rows.append(fold_row)
            attempt.progress(
                status="cross_encoder_grouped_cv",
                epochs=epochs,
                fold=fold_index,
                total_folds=len(cv_manifest["folds"]),
                latest_validated_checkpoint=str(head_path),
            )
        candidate = {
            "epochs": epochs,
            "fold_count": len(fold_metrics),
            "mean_ndcg@4": statistics.fmean(
                float(value["per_state"]["ndcg@4"]["mean"] or 0.0)
                for value in fold_metrics
            ),
            "mean_interaction_residual_spearman": statistics.fmean(
                float(value["interaction_residual_spearman"] or 0.0)
                for value in fold_metrics
            ),
            "mean_pooled_raw_spearman": statistics.fmean(
                float(value["pooled_raw_spearman"] or 0.0) for value in fold_metrics
            ),
        }
        candidates.append(candidate)
    selected = max(
        candidates,
        key=lambda row: (
            float(row["mean_ndcg@4"]),
            float(row["mean_interaction_residual_spearman"]),
            float(row["mean_pooled_raw_spearman"]),
            -int(row["epochs"]),
        ),
    )
    return {
        **selected,
        "candidates": candidates,
        "selection_rule": (
            "cell-A-only grouped-CV max NDCG@4, residual Spearman, raw "
            "Spearman, then fewer epochs"
        ),
    }, cv_rows


def _run_part_e(
    *,
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    started = time.perf_counter()
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    input_validation = _validate_inputs(
        settings=settings, artifact_dir=args.artifact_dir
    )
    atomic_write_json(
        args.artifact_dir / "part_e/input_validation.json", input_validation
    )
    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    cells = {
        cell: [row for row in pair_rows if str(row["cell"]) == cell] for cell in CELLS
    }
    preflight_by_pair = {
        str(row["pair_id"]): row for row in _load_rows(exp017 / "pair_preflight.jsonl")
    }
    transitions = {
        str(row["transition_id"]): row
        for row in _load_rows(exp017 / "transition_panel.jsonl")
    }
    examples = load_decision_examples(
        Path(settings["source_data"]) / "decision_examples.jsonl"
    )
    attempt.progress(status="loading_frozen_qwen_for_prompt_cross_encoder")
    backend = build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("EXP-019 Part E requires fully frozen Qwen")
    token_preflight = _prompt_preflight(
        tokenizer=backend.tokenizer,
        examples=examples,
        pair_rows=pair_rows,
        preflight_by_pair=preflight_by_pair,
        transitions=transitions,
        prompt_profile=cfg.benchmark.prompt_profile,
        attempt=attempt,
    )
    atomic_write_json(
        args.artifact_dir / "part_e/cross_encoder_token_preflight.json",
        token_preflight,
    )
    feature_by_pair, cache_report = _pair_cache(
        backend=backend,
        examples=examples,
        pair_rows=pair_rows,
        preflight_by_pair=preflight_by_pair,
        transitions=transitions,
        prompt_profile=cfg.benchmark.prompt_profile,
        renderer_version=str(settings["cross_encoder"]["renderer_version"]),
        output_dir=args.artifact_dir / "part_e/cross_encoder_cache/rows",
        source_commit=args.lambda_head,
        attempt=attempt,
    )
    atomic_write_json(
        args.artifact_dir / "part_e/cross_encoder_cache_report.json", cache_report
    )
    del backend
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    device = torch.device(args.device)
    parts_c_d = _load_json(args.artifact_dir / "parts_c_d_summary.json")
    decomposition = _load_json(args.artifact_dir / "utility_decomposition.json")
    _, state_main, transition_main, main_metadata = _load_multiview_main_inputs(
        args.artifact_dir, settings, device
    )
    full_base, state_only_scores, transition_only_scores = _base_score_maps(
        rows=pair_rows,
        decomposition=decomposition,
        state_main=state_main,
        transition_main=transition_main,
    )
    selected, cv_rows = _cv_select_epochs(
        rows_a=cells[CELL_A],
        cv_manifest=parts_c_d["cv_manifest"],
        feature_by_pair=feature_by_pair,
        main_metadata=main_metadata,
        settings=settings,
        output_dir=args.artifact_dir / "part_e/cv",
        device=device,
        attempt=attempt,
    )
    atomic_write_json(
        args.artifact_dir / "part_e/cv_results.json",
        {"selected": selected, "fold_rows": cv_rows},
    )
    train_rows = cells[CELL_A]
    train_features = torch.stack(
        [feature_by_pair[str(row["pair_id"])] for row in train_rows]
    )
    normalization = feature_normalization(train_features)
    head = CrossEncoderResidualHead(
        int(train_features.shape[-1]),
        hidden_dim=int(settings["cross_encoder"]["scalar_head_hidden_dim"]),
        dropout=float(settings["multiview"]["dropout"]),
    )
    checkpoint_path = args.artifact_dir / "part_e/checkpoints/cross_encoder.pt"
    checkpoint_metadata = {
        "format": f"{CROSS_ENCODER_MODEL_VERSION}_checkpoint",
        "epochs": int(selected["epochs"]),
        "cell_a_pair_ids_sha256": sha256_text(
            "\n".join(str(row["pair_id"]) for row in train_rows)
        ),
        "cross_encoder_aggregate_sha256": cache_report["aggregate_sha256"],
        "main_checkpoint_sha256": main_metadata["checkpoint_sha256"],
    }
    if checkpoint_path.exists():
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        if checkpoint["metadata"] != checkpoint_metadata:
            raise ValueError("Existing full cross-encoder checkpoint differs")
        head.load_state_dict(checkpoint["model_state_dict"])
        training = checkpoint["training"]
        normalization = checkpoint["normalization"]
        reused_checkpoint = True
    else:
        training = train_cross_encoder_head(
            model=head,
            rows=train_rows,
            features=normalize_features(train_features, normalization),
            base_scores=exact_training_base_scores(train_rows, decomposition),
            decomposition=decomposition,
            settings=settings["current_representation"],
            epochs=int(selected["epochs"]),
            seed=_seed(int(settings["seed"]), "cross-full"),
            device=device,
        )
        optimizer_state = training.pop("optimizer_state_dict")
        atomic_torch_save(
            {
                "metadata": checkpoint_metadata,
                "model_state_dict": {
                    key: value.detach().cpu()
                    for key, value in head.state_dict().items()
                },
                "optimizer_state_dict": optimizer_state,
                "normalization": {
                    key: value.detach().cpu() for key, value in normalization.items()
                },
                "training": training,
            },
            checkpoint_path,
        )
        reused_checkpoint = False
    head.to(device).eval()
    prediction_root = args.artifact_dir / "part_e/predictions"
    cell_results = {}
    for cell in CELLS:
        controls = {}
        for control_index, control in enumerate(CONTROLS, start=1):
            predicted = _prediction_rows(
                model=head,
                rows=cells[cell],
                feature_by_pair=feature_by_pair,
                normalization=normalization,
                base_scores=full_base,
                control=control,
                seed=_seed(int(settings["seed"]), "cross-control", cell, control_index),
                device=device,
            )
            path = prediction_root / cell / f"{control}.jsonl"
            write_jsonl(path, predicted)
            metrics = _strip_per_state(
                summarize_revised_predictions(predicted, **_metric_kwargs(settings))
            )
            controls[control] = {
                "metrics": metrics,
                "rows_path": str(path),
                "rows_sha256": sha256_file(path),
            }
            if control == "correct" and cell in {CELL_B, CELL_C, CELL_D}:
                controls[control]["task_grouped_bootstrap_ci95"] = (
                    task_grouped_bootstrap(
                        predicted,
                        samples=int(settings["metrics"]["bootstrap_samples"]),
                        seed=int(settings["metrics"]["bootstrap_seed"]),
                        metric_settings=_bootstrap_kwargs(settings),
                    )
                )
        cell_results[cell] = {"controls": controls}
        attempt.progress(
            status="evaluating_prompt_only_cross_encoder",
            cell=cell,
            latest_validated_checkpoint=str(checkpoint_path),
        )
    d_rows = cells[CELL_D]
    state_only_rows = _single_axis_rows(
        d_rows, state_only_scores, full_base, "state_only"
    )
    transition_only_rows = _single_axis_rows(
        d_rows, transition_only_scores, full_base, "transition_only"
    )
    baselines = {
        "state_only": _strip_per_state(
            summarize_revised_predictions(state_only_rows, **_metric_kwargs(settings))
        ),
        "transition_only": _strip_per_state(
            summarize_revised_predictions(
                transition_only_rows, **_metric_kwargs(settings)
            )
        ),
    }
    correct_rows = _load_rows(
        Path(cell_results[CELL_D]["controls"]["correct"]["rows_path"])
    )
    shuffled_state_rows = _load_rows(
        Path(cell_results[CELL_D]["controls"]["shuffled_state"]["rows_path"])
    )
    shuffled_transition_rows = _load_rows(
        Path(cell_results[CELL_D]["controls"]["shuffled_transition"]["rows_path"])
    )
    contrasts = {
        control: paired_task_bootstrap_contrast(
            correct_rows,
            _load_rows(Path(cell_results[CELL_D]["controls"][control]["rows_path"])),
            samples=int(settings["metrics"]["bootstrap_samples"]),
            seed=int(settings["metrics"]["bootstrap_seed"]) + index,
            metric_settings=_bootstrap_kwargs(settings),
        )
        for index, control in enumerate(
            ("shuffled_state", "shuffled_transition", "both_shuffled"), start=1
        )
    }
    per_task = per_task_gate_metrics(
        correct_rows=correct_rows,
        state_only_rows=state_only_rows,
        transition_only_rows=transition_only_rows,
        shuffled_state_rows=shuffled_state_rows,
        shuffled_transition_rows=shuffled_transition_rows,
        metric_settings=_bootstrap_kwargs(settings),
    )
    gate = interaction_gate(
        candidate=cell_results[CELL_D]["controls"]["correct"]["metrics"],
        state_only=baselines["state_only"],
        transition_only=baselines["transition_only"],
        shuffled_state=cell_results[CELL_D]["controls"]["shuffled_state"]["metrics"],
        shuffled_transition=cell_results[CELL_D]["controls"]["shuffled_transition"][
            "metrics"
        ],
        per_task=per_task,
        transition_shuffle_contrast=contrasts["shuffled_transition"],
        thresholds=settings["interaction_gate"],
    )
    decision = (
        "independent_encoding_or_field_factorization_bottleneck"
        if gate["passed"]
        else "continue_to_data_sufficiency_part_f"
    )
    summary = {
        "format": "prompt_only_cross_encoder_summary_6c_v1",
        "run_uuid": str(settings["run_uuid"]),
        "source_commit": args.lambda_head,
        "status": "part_e_completed",
        "decision_after_part_e": decision,
        "input_validation": input_validation,
        "token_preflight": token_preflight,
        "cache_report": cache_report,
        "selected_configuration": selected,
        "training": training,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "checkpoint_reused": reused_checkpoint,
        "main_effect_checkpoint": main_metadata["checkpoint_path"],
        "main_effect_checkpoint_sha256": main_metadata["checkpoint_sha256"],
        "normalization_estimated_from": CELL_A,
        "cells": cell_results,
        "baselines": baselines,
        "paired_bootstrap_contrasts": contrasts,
        "per_heldout_task": per_task,
        "gate": gate,
        "runtime_seconds": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "qwen_gradients": False,
            "target_action_encoded": False,
            "no_truncation": True,
            "no_behavioral_program_or_injector": True,
            "no_selector_or_field": True,
            "no_appworld_generation": True,
        },
        "timestamp_utc": utc_now(),
    }
    atomic_write_json(args.artifact_dir / "part_e_summary.json", summary)
    atomic_write_text(args.artifact_dir / "part_e_report.md", _markdown_report(summary))
    attempt.progress(
        status="part_e_completed",
        decision_after_part_e=decision,
        latest_validated_checkpoint=str(args.artifact_dir / "part_e_summary.json"),
    )
    return summary


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-019 Part E: Prompt-Only Frozen-Qwen Cross-Encoder Upper Bound",
        "",
        f"- status: `{summary['status']}`",
        f"- decision: `{summary['decision_after_part_e']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- exact cached pairs: `{summary['cache_report']['pair_count']}`",
        f"- reused/new: `{summary['cache_report']['reused']}` / `{summary['cache_report']['newly_computed']}`",
        f"- selected epochs (A-only CV): `{summary['selected_configuration']['epochs']}`",
        f"- scientific interaction gate: `{summary['gate']['passed']}`",
        "",
        "## Cell Metrics",
        "",
        "| Cell | NDCG@4 | Per-state Spearman | Raw Spearman | Residual Spearman | State-shuffle NDCG@4 | Transition-shuffle NDCG@4 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        controls = summary["cells"][cell]["controls"]
        correct = controls["correct"]["metrics"]
        lines.append(
            f"| {cell} | {float(correct['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{float(correct['per_state']['spearman']['mean'] or 0):.6f} | "
            f"{float(correct['pooled_raw_spearman'] or 0):.6f} | "
            f"{float(correct['interaction_residual_spearman'] or 0):.6f} | "
            f"{float(controls['shuffled_state']['metrics']['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{float(controls['shuffled_transition']['metrics']['per_state']['ndcg@4']['mean'] or 0):.6f} |"
        )
    lines.extend(
        [
            "",
            "This is a non-deployable O(number-of-memories) information upper bound. "
            "No behavioral program, injector, selector, production field, Qwen "
            "behavioral backpropagation, AppWorld generation/evaluation, Stage C2, "
            "end-to-end RCMF training, prompt-demo change, or V4 tag was performed.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EXP-019 conditional prompt-only Part E"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp019")
    parser.add_argument(
        "--device", default="cuda" if torch.cuda.is_available() else "cpu"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6c"]
    summary_path = args.artifact_dir / "part_e_summary.json"
    if summary_path.exists():
        summary = _load_json(summary_path)
        print(
            json.dumps(
                {
                    "reused": True,
                    "summary": str(summary_path),
                    "decision": summary["decision_after_part_e"],
                },
                sort_keys=True,
            )
        )
        return
    attempts = _load_rows(args.artifact_dir / "attempts.jsonl")
    if any(str(row.get("attempt_id")) == args.attempt_id for row in attempts):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="part_e_prompt_only_cross_encoder",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        summary = _run_part_e(args=args, cfg=cfg, settings=settings, attempt=attempt)
    print(
        json.dumps(
            {
                "reused": False,
                "summary": str(summary_path),
                "decision": summary["decision_after_part_e"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
