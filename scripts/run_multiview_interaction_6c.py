from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config
from rcmf.factory import build_backend
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
    MODEL_KINDS,
    MultiViewInteractionPredictor,
    StructuredInteractionPredictor,
    StructuredPairFeatureBuilder,
    ViewSetMainEffectHeads,
    train_multiview_interaction,
    train_view_main_effects,
)
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    MULTIVIEW_CACHE_VERSION,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    multiview_geometry,
    query_state_text_and_char_spans,
    readout_payload_hash,
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    canonical_json_sha256,
    deterministic_derangement,
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


def _verify_exp018_snapshot(exp018: Path, snapshot: Mapping[str, Any]) -> None:
    for name, expected in snapshot["hashes"].items():
        actual = sha256_file(exp018 / str(name))
        if actual != str(expected):
            raise RuntimeError(f"Immutable EXP-018 input changed: {name}")


def _step_bucket(step: int, count: int) -> str:
    ratio = (int(step) - 1) / max(int(count) - 1, 1)
    return "early" if ratio < 1 / 3 else "middle" if ratio < 2 / 3 else "late"


def _cache_row_compatible(
    payload: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    return all(payload.get(key) == value for key, value in expected.items())


def _span_preflight(
    *,
    tokenizer: Any,
    examples: Sequence[Any],
    query_manifest: Mapping[str, Any],
    pair_rows: Sequence[Mapping[str, Any]],
    panel_rows: Sequence[Mapping[str, Any]],
    prompt_profile: str,
) -> dict[str, Any]:
    base_hashes: dict[str, set[str]] = {}
    for row in pair_rows:
        base_hashes.setdefault(str(row["state_example_id"]), set()).add(
            str(row["base_prompt_sha256"])
        )
    state_tokens = []
    transition_tokens = []
    span_count = 0
    decoded_exact = 0
    decoded_aligned = 0
    token_boundary_expanded = 0
    for query in query_manifest["query_rows"]:
        state_id = str(query["state_example_id"])
        rendered, char_spans, _ = query_state_text_and_char_spans(
            tokenizer, examples[int(query["example_index"])], prompt_profile
        )
        if base_hashes.get(state_id) != {sha256_text(rendered)}:
            raise ValueError(f"State preflight prompt hash differs for {state_id}")
        input_ids, _, span_rows = tokenize_and_validate_char_spans(
            tokenizer, rendered, char_spans
        )
        tokens = int(input_ids.shape[1])
        if tokens != int(query["prompt_tokens"]):
            raise ValueError(f"State preflight token count differs for {state_id}")
        state_tokens.append(tokens)
        span_count += len(span_rows)
        decoded_exact += sum(
            bool(row["decoded_text_exact_match"]) for row in span_rows.values()
        )
        decoded_aligned += sum(
            bool(row["decoded_matches_aligned_source"])
            for row in span_rows.values()
        )
        token_boundary_expanded += sum(
            int(row["token_aligned_char_start"]) != int(row["char_start"])
            or int(row["token_aligned_char_end"]) != int(row["char_end"])
            for row in span_rows.values()
        )
    for transition in panel_rows:
        transition_id = str(transition["transition_id"])
        text, char_spans, _ = transition_text_and_char_spans(transition)
        if sha256_text(text) != str(transition["teacher_section_sha256"]):
            raise ValueError(
                f"Transition preflight text hash differs for {transition_id}"
            )
        input_ids, _, span_rows = tokenize_and_validate_char_spans(
            tokenizer, text, char_spans
        )
        tokens = int(input_ids.shape[1])
        if tokens != int(transition["teacher_section_tokens"]):
            raise ValueError(
                f"Transition preflight token count differs for {transition_id}"
            )
        transition_tokens.append(tokens)
        span_count += len(span_rows)
        decoded_exact += sum(
            bool(row["decoded_text_exact_match"]) for row in span_rows.values()
        )
        decoded_aligned += sum(
            bool(row["decoded_matches_aligned_source"])
            for row in span_rows.values()
        )
        token_boundary_expanded += sum(
            int(row["token_aligned_char_start"]) != int(row["char_start"])
            or int(row["token_aligned_char_end"]) != int(row["char_end"])
            for row in span_rows.values()
        )
    if decoded_aligned != span_count:
        raise ValueError(
            f"Only {decoded_aligned}/{span_count} spans decode to aligned source text"
        )
    return {
        "format": "span_boundary_preflight_6c_v1",
        "state_count": len(state_tokens),
        "transition_count": len(transition_tokens),
        "span_count": span_count,
        "decoded_exact_count": decoded_exact,
        "decoded_aligned_source_count": decoded_aligned,
        "token_boundary_expanded_span_count": token_boundary_expanded,
        "all_decoded_exact": decoded_exact == span_count,
        "all_decoded_aligned_source_valid": True,
        "all_decoded_aligned_source_valid": True,
        "no_truncation": True,
        "state_token_count": {
            "min": min(state_tokens),
            "mean": statistics.fmean(state_tokens),
            "max": max(state_tokens),
        },
        "transition_token_count": {
            "min": min(transition_tokens),
            "mean": statistics.fmean(transition_tokens),
            "max": max(transition_tokens),
        },
    }


def _state_cache(
    *,
    backend: Any,
    examples: Sequence[Any],
    query_manifest: Mapping[str, Any],
    pair_rows: Sequence[Mapping[str, Any]],
    prompt_profile: str,
    output_dir: Path,
    renderer_version: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, TensorLike], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_hashes: dict[str, set[str]] = {}
    for row in pair_rows:
        base_hashes.setdefault(str(row["state_example_id"]), set()).add(
            str(row["base_prompt_sha256"])
        )
    row_payloads = []
    reused = 0
    computed = 0
    ordered_query = list(query_manifest["query_rows"])
    for position, query in enumerate(ordered_query, start=1):
        state_id = str(query["state_example_id"])
        example = examples[int(query["example_index"])]
        rendered, char_spans, source_metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, prompt_profile
        )
        prompt_hash = sha256_text(rendered)
        if base_hashes.get(state_id) != {prompt_hash}:
            raise ValueError(f"State canonical prompt hash differs for {state_id}")
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        if int(input_ids.shape[1]) != int(query["prompt_tokens"]):
            raise ValueError(
                f"State token count differs for {state_id}: "
                f"{input_ids.shape[1]} != {query['prompt_tokens']}"
            )
        expected = {
            "format": f"{MULTIVIEW_CACHE_VERSION}_state_row",
            "state_example_id": state_id,
            "prompt_sha256": prompt_hash,
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
        }
        row_path = output_dir / f"{state_id.replace(':', '__')}.pt"
        payload = None
        if row_path.exists():
            candidate = torch.load(row_path, map_location="cpu", weights_only=False)
            if not _cache_row_compatible(candidate, expected):
                raise ValueError(f"Incompatible existing state multi-view row: {row_path}")
            payload = candidate
            reused += 1
        if payload is None:
            readouts = frozen_qwen_span_readouts(
                model=backend.model,
                input_ids=input_ids,
                attention_mask=attention_mask,
                span_rows=span_rows,
                device=backend.device,
            )
            payload = {
                **expected,
                "task_id": str(query["task_id"]),
                "example_index": int(query["example_index"]),
                "split": str(query["split"]),
                "apps": [str(value) for value in query.get("apps", [])],
                "step_id": int(query["step_id"]),
                "step_count": int(query["step_count"]),
                "step_bucket": _step_bucket(
                    int(query["step_id"]), int(query["step_count"])
                ),
                "token_count": int(input_ids.shape[1]),
                "text_sha256": prompt_hash,
                "span_rows": span_rows,
                "source_metadata": source_metadata,
                "readouts": readouts,
                "readout_sha256": readout_payload_hash(readouts),
                "model_config_commit_hash": getattr(
                    backend.model.config, "_commit_hash", None
                ),
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "truncated": False,
                "created_at_utc": utc_now(),
            }
            atomic_torch_save(payload, row_path)
            computed += 1
        row_payloads.append(payload)
        attempt.progress(
            status="encoding_multiview_states",
            completed=position,
            total=len(ordered_query),
            reused=reused,
            newly_computed=computed,
            latest_validated_checkpoint=str(row_path),
        )
    ordered_ids = [str(row["state_example_id"]) for row in ordered_query]
    tensors = {
        layer: flatten_multiview_readouts(
            row_payloads, layer=layer, view_names=STATE_VIEW_NAMES
        )
        for layer in LAYER_CANDIDATES
    }
    aggregate = {
        "format": f"{MULTIVIEW_CACHE_VERSION}_state_aggregate",
        "model_name": str(backend.model_name),
        "renderer_version": renderer_version,
        "ordered_ids": ordered_ids,
        "view_names": list(STATE_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "representations": tensors,
        "rows": [
            {key: value for key, value in row.items() if key != "readouts"}
            for row in row_payloads
        ],
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": tensor})
            for layer, tensor in tensors.items()
        },
        "reused": reused,
        "newly_computed": computed,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(aggregate, output_dir.parent / "state_multiview.pt")
    return tensors, aggregate


def _transition_cache(
    *,
    backend: Any,
    panel_rows: Sequence[Mapping[str, Any]],
    output_dir: Path,
    renderer_version: str,
    attempt: AttemptLedger,
) -> tuple[dict[str, TensorLike], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(panel_rows, key=lambda row: str(row["transition_id"]))
    payloads = []
    reused = 0
    computed = 0
    for position, transition in enumerate(ordered, start=1):
        transition_id = str(transition["transition_id"])
        text, char_spans, source_metadata = transition_text_and_char_spans(transition)
        text_hash = sha256_text(text)
        if text_hash != str(transition["teacher_section_sha256"]):
            raise ValueError(f"Transition canonical text hash differs for {transition_id}")
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, text, char_spans
        )
        if int(input_ids.shape[1]) != int(transition["teacher_section_tokens"]):
            raise ValueError(
                f"Transition token count differs for {transition_id}: "
                f"{input_ids.shape[1]} != {transition['teacher_section_tokens']}"
            )
        expected = {
            "format": f"{MULTIVIEW_CACHE_VERSION}_transition_row",
            "transition_id": transition_id,
            "transition_content_sha256": str(
                transition["transition_content_sha256"]
            ),
            "teacher_section_sha256": text_hash,
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
        }
        row_path = output_dir / f"{transition_id}.pt"
        payload = None
        if row_path.exists():
            candidate = torch.load(row_path, map_location="cpu", weights_only=False)
            if not _cache_row_compatible(candidate, expected):
                raise ValueError(
                    f"Incompatible existing transition multi-view row: {row_path}"
                )
            payload = candidate
            reused += 1
        if payload is None:
            readouts = frozen_qwen_span_readouts(
                model=backend.model,
                input_ids=input_ids,
                attention_mask=attention_mask,
                span_rows=span_rows,
                device=backend.device,
            )
            payload = {
                **expected,
                "parent_memory_id": str(transition["parent_memory_id"]),
                "parent_task_id": str(transition["parent_task_id"]),
                "apps": [str(value) for value in transition.get("apps", [])],
                "step_index": int(transition["step_index"]),
                "step_count": int(transition["step_count"]),
                "step_bucket": _step_bucket(
                    int(transition["step_index"]), int(transition["step_count"])
                ),
                "token_count": int(input_ids.shape[1]),
                "span_rows": span_rows,
                "source_metadata": source_metadata,
                "source_hashes": {
                    key: str(transition[key])
                    for key in (
                        "source_task_goal_sha256",
                        "canonical_pre_action_state_sha256",
                        "complete_action_sha256",
                        "complete_post_action_observation_sha256",
                    )
                },
                "readouts": readouts,
                "readout_sha256": readout_payload_hash(readouts),
                "model_config_commit_hash": getattr(
                    backend.model.config, "_commit_hash", None
                ),
                "truncated": False,
                "created_at_utc": utc_now(),
            }
            atomic_torch_save(payload, row_path)
            computed += 1
        payloads.append(payload)
        attempt.progress(
            status="encoding_multiview_transitions",
            completed=position,
            total=len(ordered),
            reused=reused,
            newly_computed=computed,
            latest_validated_checkpoint=str(row_path),
        )
    ordered_ids = [str(row["transition_id"]) for row in ordered]
    tensors = {
        layer: flatten_multiview_readouts(
            payloads, layer=layer, view_names=TRANSITION_VIEW_NAMES
        )
        for layer in LAYER_CANDIDATES
    }
    aggregate = {
        "format": f"{MULTIVIEW_CACHE_VERSION}_transition_aggregate",
        "model_name": str(backend.model_name),
        "renderer_version": renderer_version,
        "ordered_ids": ordered_ids,
        "view_names": list(TRANSITION_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "representations": tensors,
        "rows": [
            {key: value for key, value in row.items() if key != "readouts"}
            for row in payloads
        ],
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": tensor})
            for layer, tensor in tensors.items()
        },
        "reused": reused,
        "newly_computed": computed,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(aggregate, output_dir.parent / "transition_multiview.pt")
    return tensors, aggregate


TensorLike = torch.Tensor


def _new_main(settings: Mapping[str, Any], input_dim: int) -> ViewSetMainEffectHeads:
    return ViewSetMainEffectHeads(
        state_views=len(STATE_VIEW_NAMES) * len(POOLING_RULES),
        transition_views=len(TRANSITION_VIEW_NAMES) * len(POOLING_RULES),
        input_dim=input_dim,
        projection_dim=int(settings["projection_dim"]),
        hidden_dim=int(settings["hidden_dim"]),
        dropout=float(settings["dropout"]),
    )


def _new_model(
    kind: str,
    *,
    main: ViewSetMainEffectHeads,
    decomposition: Mapping[str, Any],
    settings: Mapping[str, Any],
    input_dim: int,
    feature_dim: int | None = None,
) -> MultiViewInteractionPredictor | StructuredInteractionPredictor:
    if kind == "structured_feature_interaction":
        if feature_dim is None:
            raise ValueError("Structured model requires feature_dim")
        return StructuredInteractionPredictor(
            main_effects=main,
            mu=float(decomposition["mu"]),
            feature_dim=int(feature_dim),
            hidden_dim=int(settings["hidden_dim"]),
            dropout=float(settings["dropout"]),
        )
    return MultiViewInteractionPredictor(
        kind,
        main_effects=main,
        mu=float(decomposition["mu"]),
        state_views=len(STATE_VIEW_NAMES) * len(POOLING_RULES),
        transition_views=len(TRANSITION_VIEW_NAMES) * len(POOLING_RULES),
        input_dim=input_dim,
        projection_dim=int(settings["projection_dim"]),
        interaction_rank=int(settings["interaction_rank"]),
        hidden_dim=int(settings["hidden_dim"]),
        dropout=float(settings["dropout"]),
    )


def _main_checkpoint(
    *,
    path: Path,
    decomposition: Mapping[str, Any],
    state_representations: TensorLike,
    transition_representations: TensorLike,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    decomposition_settings: Mapping[str, Any],
    seed: int,
    device: torch.device,
    metadata: Mapping[str, Any],
) -> tuple[ViewSetMainEffectHeads, dict[str, Any], bool]:
    model = _new_main(settings, int(state_representations.shape[-1]))
    if path.exists():
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if payload.get("metadata") != dict(metadata):
            raise ValueError(f"Existing multi-view main checkpoint differs: {path}")
        model.load_state_dict(payload["model_state_dict"])
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        return model, payload["training"], True
    training = train_view_main_effects(
        model=model,
        decomposition=decomposition,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        epochs=int(decomposition_settings["main_epochs"]),
        learning_rate=float(decomposition_settings["main_learning_rate"]),
        weight_decay=float(decomposition_settings["main_weight_decay"]),
        huber_delta=float(decomposition_settings["huber_delta"]),
        seed=int(seed),
        device=device,
    )
    optimizer_state = training.pop("optimizer_state_dict")
    payload = {
        "format": "multiview_main_effect_checkpoint_6c_v1",
        "metadata": dict(metadata),
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer_state,
        "training": training,
    }
    atomic_torch_save(payload, path)
    return model, training, False


def _interaction_checkpoint(
    *,
    path: Path,
    kind: str,
    main: ViewSetMainEffectHeads,
    decomposition: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    state_representations: TensorLike,
    transition_representations: TensorLike,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    settings: Mapping[str, Any],
    epochs: int,
    seed: int,
    device: torch.device,
    metadata: Mapping[str, Any],
    pair_features: TensorLike | None = None,
    feature_dim: int | None = None,
) -> tuple[Any, dict[str, Any], bool]:
    model = _new_model(
        kind,
        main=main,
        decomposition=decomposition,
        settings=settings,
        input_dim=int(state_representations.shape[-1]),
        feature_dim=feature_dim,
    )
    if path.exists():
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if payload.get("metadata") != dict(metadata):
            raise ValueError(f"Existing multi-view interaction checkpoint differs: {path}")
        model.load_state_dict(payload["model_state_dict"])
        return model.to(device).eval(), payload["training"], True
    training = train_multiview_interaction(
        model=model,
        rows=rows,
        decomposition=decomposition,
        state_representations=state_representations,
        transition_representations=transition_representations,
        state_position=state_position,
        transition_position=transition_position,
        settings=settings,
        epochs=epochs,
        seed=seed,
        device=device,
        pair_features=pair_features,
    )
    optimizer_state = training.pop("optimizer_state_dict")
    payload = {
        "format": "multiview_interaction_checkpoint_6c_v1",
        "metadata": dict(metadata),
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "optimizer_state_dict": optimizer_state,
        "training": training,
    }
    atomic_torch_save(payload, path)
    return model.eval(), training, False


def _normalization(values: TensorLike) -> dict[str, TensorLike]:
    mean = values.mean(dim=0)
    std = values.std(dim=0, unbiased=False).clamp_min(1.0e-6)
    return {"mean": mean, "std": std}


def _normalize(values: TensorLike, stats: Mapping[str, TensorLike]) -> TensorLike:
    return (values - stats["mean"]) / stats["std"]


@torch.no_grad()
def _predict(
    *,
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: TensorLike,
    transition_representations: TensorLike,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
    control: str,
    seed: int,
    feature_builder: StructuredPairFeatureBuilder | None = None,
    feature_normalization: Mapping[str, TensorLike] | None = None,
) -> list[dict[str, Any]]:
    if control not in CONTROLS:
        raise ValueError(f"Unknown multi-view control: {control}")
    state_map = {value: value for value in state_position}
    transition_map = {value: value for value in transition_position}
    if control in {"shuffled_state", "both_shuffled"}:
        state_map.update(
            deterministic_derangement(
                [str(row["state_example_id"]) for row in rows],
                seed=seed,
                namespace=f"6c-multiview-{control}-state",
            )
        )
    if control in {"shuffled_transition", "both_shuffled"}:
        transition_map.update(
            deterministic_derangement(
                [str(row["transition_id"]) for row in rows],
                seed=seed,
                namespace=f"6c-multiview-{control}-transition",
            )
        )
    state_all = state_representations.to(device=device, dtype=torch.float32)
    transition_all = transition_representations.to(device=device, dtype=torch.float32)
    mean_state = state_all.mean(dim=0)
    mean_transition = transition_all.mean(dim=0)
    model.to(device).eval()
    output = []
    for start in range(0, len(rows), 256):
        block = rows[start : start + 256]
        state = torch.stack(
            [
                mean_state
                if control == "mean_state"
                else state_all[
                    state_position[state_map[str(row["state_example_id"])]]
                ]
                for row in block
            ]
        )
        transition = torch.stack(
            [
                mean_transition
                if control == "mean_transition"
                else transition_all[
                    transition_position[
                        transition_map[str(row["transition_id"])]
                    ]
                ]
                for row in block
            ]
        )
        if isinstance(model, StructuredInteractionPredictor):
            if feature_builder is None or feature_normalization is None:
                raise ValueError("Structured prediction requires features and normalization")
            features = feature_builder.rows(
                block, state_map=state_map, transition_map=transition_map
            )
            features = _normalize(features, feature_normalization).to(device)
            values = model.components(state, transition, features)
        else:
            values = model.components(state, transition)
        if control == "zero_interaction":
            values["interaction"] = torch.zeros_like(values["interaction"])
            values["score"] = values["mu"] + values["state_main"] + values["transition_main"]
        correct_state = torch.stack(
            [state_all[state_position[str(row["state_example_id"])]] for row in block]
        )
        correct_transition = torch.stack(
            [
                transition_all[transition_position[str(row["transition_id"])]]
                for row in block
            ]
        )
        target_state_main = model.main_effects.state(correct_state)
        target_transition_main = model.main_effects.transition(correct_transition)
        for index, row in enumerate(block):
            state_id = str(row["state_example_id"])
            transition_id = str(row["transition_id"])
            state_effect = float(
                decomposition["state_effects"].get(
                    state_id, float(target_state_main[index].cpu())
                )
            )
            transition_effect = float(
                decomposition["transition_effects"].get(
                    transition_id, float(target_transition_main[index].cpu())
                )
            )
            output.append(
                {
                    "pair_id": str(row["pair_id"]),
                    "state_example_id": state_id,
                    "state_task_id": str(row["state_task_id"]),
                    "transition_id": transition_id,
                    "transition_parent_id": str(row["transition_parent_id"]),
                    "cell": str(row["cell"]),
                    "utility_category": str(row["utility_category"]),
                    "u_text": float(row["text_utility"]),
                    "u_predicted": float(values["score"][index].cpu()),
                    "residual_target": float(row["text_utility"])
                    - float(decomposition["mu"])
                    - state_effect
                    - transition_effect,
                    "residual_predicted": float(values["interaction"][index].cpu()),
                    "state_main_predicted": float(values["state_main"][index].cpu()),
                    "transition_main_predicted": float(
                        values["transition_main"][index].cpu()
                    ),
                    "control": control,
                }
            )
    return output


@torch.no_grad()
def _single_axis_rows(
    *,
    main: ViewSetMainEffectHeads,
    mu: float,
    rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    state_representations: TensorLike,
    transition_representations: TensorLike,
    state_position: Mapping[str, int],
    transition_position: Mapping[str, int],
    device: torch.device,
    axis: str,
) -> list[dict[str, Any]]:
    state_all = state_representations.to(device=device, dtype=torch.float32)
    transition_all = transition_representations.to(device=device, dtype=torch.float32)
    main.to(device).eval()
    output = []
    for start in range(0, len(rows), 256):
        block = rows[start : start + 256]
        state = torch.stack(
            [state_all[state_position[str(row["state_example_id"])]] for row in block]
        )
        transition = torch.stack(
            [
                transition_all[transition_position[str(row["transition_id"])]]
                for row in block
            ]
        )
        state_main = main.state(state)
        transition_main = main.transition(transition)
        for index, row in enumerate(block):
            state_effect = float(
                decomposition["state_effects"].get(
                    str(row["state_example_id"]), float(state_main[index].cpu())
                )
            )
            transition_effect = float(
                decomposition["transition_effects"].get(
                    str(row["transition_id"]), float(transition_main[index].cpu())
                )
            )
            predicted = float(mu) + float(
                state_main[index].cpu() if axis == "state" else transition_main[index].cpu()
            )
            output.append(
                {
                    "pair_id": str(row["pair_id"]),
                    "state_example_id": str(row["state_example_id"]),
                    "state_task_id": str(row["state_task_id"]),
                    "transition_id": str(row["transition_id"]),
                    "transition_parent_id": str(row["transition_parent_id"]),
                    "cell": str(row["cell"]),
                    "utility_category": str(row["utility_category"]),
                    "u_text": float(row["text_utility"]),
                    "u_predicted": predicted,
                    "residual_target": float(row["text_utility"])
                    - float(mu)
                    - state_effect
                    - transition_effect,
                    "residual_predicted": 0.0,
                    "control": f"{axis}_only",
                }
            )
    return output


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    metrics = settings["metrics"]
    return {
        "ranking_ks": metrics["ranking_ks"],
        "neutral_epsilon": float(metrics["neutral_epsilon"]),
        "best_tie_tolerance": float(metrics["best_tie_tolerance"]),
        "huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
    }


def _bootstrap_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["metrics"]["ranking_ks"],
        "neutral_epsilon": float(settings["metrics"]["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
        "huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
    }


def _strip_per_state(value: Mapping[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(dict(value))
    output.pop("per_state_rows", None)
    return output


def _cv_score(summary: Mapping[str, Any]) -> tuple[float, float, float]:
    return (
        float(summary["per_state"]["ndcg@4"]["mean"] or -1.0),
        float(summary["interaction_residual_spearman"] or -1.0),
        float(summary["pooled_raw_spearman"] or -1.0),
    )


def _run_parts_c_d(
    *,
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    started = time.perf_counter()
    exp018 = Path(settings["exp018_artifact"])
    exp017 = Path(settings["exp017_artifact"])
    data_dir = Path(settings["source_data"])
    parts_a_b = _load_json(args.artifact_dir / "parts_a_b_summary.json")
    if not bool(parts_a_b["next_phase_required"]):
        raise RuntimeError("Part A/B did not request multi-view Parts C/D")
    snapshot = parts_a_b["exp018_immutable_snapshot"]
    _verify_exp018_snapshot(exp018, snapshot)
    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    if len(pair_rows) != int(settings["expected"]["scoreable_rows"]):
        raise ValueError("EXP-018 scoreable pair count differs")
    cells = {cell: [row for row in pair_rows if row["cell"] == cell] for cell in CELLS}
    for cell, expected in settings["expected"]["cells"].items():
        if len(cells[cell]) != int(expected):
            raise ValueError(f"Cell count differs for {cell}")
    query_manifest = _load_json(exp017 / "query_manifest.json")
    panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
    transition_by_id = {str(row["transition_id"]): row for row in panel_rows}
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    state_example_by_id = {
        str(row["state_example_id"]): examples[int(row["example_index"])]
        for row in query_manifest["query_rows"]
    }
    state_metadata = {
        str(row["state_example_id"]): row for row in query_manifest["query_rows"]
    }

    attempt.progress(status="loading_frozen_qwen_for_multiview")
    backend = build_backend(cfg, load_model=True)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen parameters are not frozen")
    cache_dir = args.artifact_dir / "parts_c_d" / "multiview_cache"
    span_preflight = _span_preflight(
        tokenizer=backend.tokenizer,
        examples=examples,
        query_manifest=query_manifest,
        pair_rows=pair_rows,
        panel_rows=panel_rows,
        prompt_profile=cfg.benchmark.prompt_profile,
    )
    atomic_write_json(
        args.artifact_dir / "parts_c_d/span_preflight.json", span_preflight
    )
    attempt.progress(
        status="span_preflight_passed",
        state_count=span_preflight["state_count"],
        transition_count=span_preflight["transition_count"],
        span_count=span_preflight["span_count"],
        latest_validated_checkpoint=str(
            args.artifact_dir / "parts_c_d/span_preflight.json"
        ),
    )
    state_tensors, state_cache = _state_cache(
        backend=backend,
        examples=examples,
        query_manifest=query_manifest,
        pair_rows=pair_rows,
        prompt_profile=cfg.benchmark.prompt_profile,
        output_dir=cache_dir / "state_rows",
        renderer_version=str(settings["multiview"]["renderer_version"]),
        attempt=attempt,
    )
    transition_tensors, transition_cache = _transition_cache(
        backend=backend,
        panel_rows=panel_rows,
        output_dir=cache_dir / "transition_rows",
        renderer_version=str(settings["multiview"]["renderer_version"]),
        attempt=attempt,
    )
    model_identity = {
        "model_name": str(backend.model_name),
        "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
        "tokenizer_name_or_path": str(
            getattr(backend.tokenizer, "name_or_path", backend.model_name)
        ),
        "qwen_frozen": True,
    }
    del backend
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    state_ids = list(state_cache["ordered_ids"])
    transition_ids = list(transition_cache["ordered_ids"])
    state_position = {value: index for index, value in enumerate(state_ids)}
    transition_position = {value: index for index, value in enumerate(transition_ids)}
    state_geometry_metadata = {
        state_id: {
            "task_label": str(state_metadata[state_id]["task_id"]),
            "app_label": "+".join(sorted(state_metadata[state_id].get("apps", []))) or "none",
            "step_bucket": _step_bucket(
                int(state_metadata[state_id]["step_id"]),
                int(state_metadata[state_id]["step_count"]),
            ),
        }
        for state_id in state_ids
    }
    transition_geometry_metadata = {
        transition_id: {
            "task_label": str(transition_by_id[transition_id]["parent_task_id"]),
            "app_label": "+".join(sorted(transition_by_id[transition_id].get("apps", []))) or "none",
            "step_bucket": _step_bucket(
                int(transition_by_id[transition_id]["step_index"]),
                int(transition_by_id[transition_id]["step_count"]),
            ),
        }
        for transition_id in transition_ids
    }
    geometry = {"state": {}, "transition": {}}
    for layer in LAYER_CANDIDATES:
        expanded_state_names = [
            f"{view}/{pool}" for view in STATE_VIEW_NAMES for pool in POOLING_RULES
        ]
        expanded_transition_names = [
            f"{view}/{pool}"
            for view in TRANSITION_VIEW_NAMES
            for pool in POOLING_RULES
        ]
        geometry["state"][layer] = multiview_geometry(
            state_tensors[layer],
            ordered_ids=state_ids,
            view_names=expanded_state_names,
            metadata_by_id=state_geometry_metadata,
        )
        geometry["transition"][layer] = multiview_geometry(
            transition_tensors[layer],
            ordered_ids=transition_ids,
            view_names=expanded_transition_names,
            metadata_by_id=transition_geometry_metadata,
        )
    representation_report = {
        "format": "span_aware_multiview_representation_report_6c_v1",
        "timestamp_utc": utc_now(),
        "model_identity": model_identity,
        "span_preflight": span_preflight,
        "state": {
            key: value for key, value in state_cache.items() if key != "representations"
        },
        "transition": {
            key: value
            for key, value in transition_cache.items()
            if key != "representations"
        },
        "geometry": geometry,
        "no_truncation": all(
            not bool(row["truncated"])
            for row in [*state_cache["rows"], *transition_cache["rows"]]
        ),
        "all_span_decoded_aligned_source_valid": all(
            bool(span["decoded_matches_aligned_source"])
            for row in [*state_cache["rows"], *transition_cache["rows"]]
            for span in row["span_rows"].values()
        ),
        "query_target_action_excluded": all(
            not bool(row["target_action_accessed"]) for row in state_cache["rows"]
        ),
    }
    atomic_write_json(args.artifact_dir / "parts_c_d/multiview_cache_report.json", representation_report)

    feature_builder = StructuredPairFeatureBuilder(
        state_examples=state_example_by_id,
        state_metadata=state_metadata,
        transitions=transition_by_id,
    )
    structured_report = {
        "format": "structured_state_transition_features_6c_v1",
        "feature_names": feature_builder.feature_names,
        "feature_count": len(feature_builder.feature_names),
        "target_action_used": False,
        "definitions": {
            "api_names": "regex over current state history and transition metadata",
            "python_calls": "Python AST calls from prior state code and transition action",
            "code_tokens": "case-normalized identifier token overlap",
            "entity_strings": "AST and quoted string literals",
            "lengths": "log1p token/count features",
        },
    }
    atomic_write_json(args.artifact_dir / "parts_c_d/structured_feature_report.json", structured_report)

    device = torch.device(args.device)
    training_settings = {
        **dict(settings["current_representation"]),
        **dict(settings["multiview"]),
        "losses": dict(settings["current_representation"]["losses"]),
        "residual_huber_delta": float(settings["current_representation"]["residual_huber_delta"]),
        "utility_huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
        "teacher_temperature": float(settings["current_representation"]["teacher_temperature"]),
        "student_temperature": float(settings["current_representation"]["student_temperature"]),
        "pair_gap_threshold": float(settings["current_representation"]["pair_gap_threshold"]),
        "pair_gap_clip": float(settings["current_representation"]["pair_gap_clip"]),
    }
    decomposition_settings = settings["decomposition"]
    metric_kwargs = _metric_kwargs(settings)
    bootstrap_settings = _bootstrap_settings(settings)
    cv_manifest = parts_a_b["cv_manifest"]
    row_by_pair = {str(row["pair_id"]): row for row in cells[CELL_A]}
    cv_root = args.artifact_dir / "parts_c_d/cv"
    cv_results: dict[str, Any] = {kind: [] for kind in MODEL_KINDS}
    cv_results["structured_feature_interaction"] = []
    total_jobs = (
        len(LAYER_CANDIDATES)
        * len(MODEL_KINDS)
        * len(training_settings["epoch_candidates"])
        * len(cv_manifest["folds"])
        + len(training_settings["epoch_candidates"]) * len(cv_manifest["folds"])
    )
    completed_jobs = 0
    main_by_layer_fold: dict[tuple[str, int], tuple[Any, Any, Any]] = {}
    for layer in LAYER_CANDIDATES:
        for fold in cv_manifest["folds"]:
            fold_index = int(fold["fold"])
            fold_train = [row_by_pair[value] for value in fold["train_pair_ids"]]
            fold_validation = [row_by_pair[value] for value in fold["validation_pair_ids"]]
            decomposition = fit_two_way_decomposition(
                fold_train,
                max_iterations=int(decomposition_settings["alternating_least_squares_iterations"]),
                tolerance=float(decomposition_settings["tolerance"]),
            )
            metadata = {
                "implementation_commit": args.lambda_head,
                "layer": layer,
                "fold": fold_index,
                "train_pair_ids_sha256": sha256_text("\n".join(sorted(fold["train_pair_ids"]))),
                "state_tensor_sha256": state_cache["tensor_sha256"][layer],
                "transition_tensor_sha256": transition_cache["tensor_sha256"][layer],
                "decomposition_sha256": canonical_json_sha256(decomposition),
            }
            main_path = cv_root / "main" / layer / f"fold_{fold_index}.pt"
            main, main_training, main_reused = _main_checkpoint(
                path=main_path,
                decomposition=decomposition,
                state_representations=state_tensors[layer],
                transition_representations=transition_tensors[layer],
                state_position=state_position,
                transition_position=transition_position,
                settings=training_settings,
                decomposition_settings=decomposition_settings,
                seed=_seed(int(settings["seed"]), "main", layer, fold_index),
                device=device,
                metadata=metadata,
            )
            main_by_layer_fold[(layer, fold_index)] = (
                main,
                decomposition,
                {"training": main_training, "reused": main_reused, "path": str(main_path)},
            )
            for kind in MODEL_KINDS:
                for epochs in [int(value) for value in training_settings["epoch_candidates"]]:
                    checkpoint = cv_root / "models" / kind / layer / f"fold_{fold_index}_epochs_{epochs}.pt"
                    model_metadata = {
                        **metadata,
                        "kind": kind,
                        "epochs": epochs,
                    }
                    model, training, reused = _interaction_checkpoint(
                        path=checkpoint,
                        kind=kind,
                        main=main,
                        decomposition=decomposition,
                        rows=fold_train,
                        state_representations=state_tensors[layer],
                        transition_representations=transition_tensors[layer],
                        state_position=state_position,
                        transition_position=transition_position,
                        settings=training_settings,
                        epochs=epochs,
                        seed=_seed(int(settings["seed"]), kind, layer, fold_index, epochs),
                        device=device,
                        metadata=model_metadata,
                    )
                    predicted = _predict(
                        model=model,
                        rows=fold_validation,
                        decomposition=decomposition,
                        state_representations=state_tensors[layer],
                        transition_representations=transition_tensors[layer],
                        state_position=state_position,
                        transition_position=transition_position,
                        device=device,
                        control="correct",
                        seed=int(settings["seed"]),
                    )
                    summary = _strip_per_state(
                        summarize_revised_predictions(predicted, **metric_kwargs)
                    )
                    cv_results[kind].append(
                        {
                            "layer": layer,
                            "epochs": epochs,
                            "fold": fold_index,
                            "train_count": len(fold_train),
                            "validation_count": len(fold_validation),
                            "metrics": summary,
                            "training": training,
                            "checkpoint": str(checkpoint),
                            "checkpoint_sha256": sha256_file(checkpoint),
                            "reused": reused,
                        }
                    )
                    completed_jobs += 1
                    attempt.progress(
                        status="multiview_grouped_cv",
                        completed_jobs=completed_jobs,
                        total_jobs=total_jobs,
                        layer=layer,
                        model_kind=kind,
                        fold=fold_index,
                        epochs=epochs,
                        latest_validated_checkpoint=str(checkpoint),
                    )

    # Structured features use final-layer main effects only; labels remain A-fold local.
    layer = "final_layer"
    for fold in cv_manifest["folds"]:
        fold_index = int(fold["fold"])
        fold_train = [row_by_pair[value] for value in fold["train_pair_ids"]]
        fold_validation = [row_by_pair[value] for value in fold["validation_pair_ids"]]
        main, decomposition, _main_meta = main_by_layer_fold[(layer, fold_index)]
        train_features_raw = feature_builder.rows(fold_train)
        normalization = _normalization(train_features_raw)
        train_features = _normalize(train_features_raw, normalization)
        for epochs in [int(value) for value in training_settings["epoch_candidates"]]:
            checkpoint = cv_root / "models/structured_feature_interaction" / f"fold_{fold_index}_epochs_{epochs}.pt"
            metadata = {
                "implementation_commit": args.lambda_head,
                "kind": "structured_feature_interaction",
                "layer_for_main_effects": layer,
                "fold": fold_index,
                "epochs": epochs,
                "feature_names_sha256": sha256_text("\n".join(feature_builder.feature_names)),
                "feature_values_sha256": tensor_state_sha256(
                    {"features": train_features}
                ),
                "train_pair_ids_sha256": sha256_text("\n".join(sorted(fold["train_pair_ids"]))),
            }
            model, training, reused = _interaction_checkpoint(
                path=checkpoint,
                kind="structured_feature_interaction",
                main=main,
                decomposition=decomposition,
                rows=fold_train,
                state_representations=state_tensors[layer],
                transition_representations=transition_tensors[layer],
                state_position=state_position,
                transition_position=transition_position,
                settings=training_settings,
                epochs=epochs,
                seed=_seed(int(settings["seed"]), "structured", fold_index, epochs),
                device=device,
                metadata=metadata,
                pair_features=train_features,
                feature_dim=len(feature_builder.feature_names),
            )
            predicted = _predict(
                model=model,
                rows=fold_validation,
                decomposition=decomposition,
                state_representations=state_tensors[layer],
                transition_representations=transition_tensors[layer],
                state_position=state_position,
                transition_position=transition_position,
                device=device,
                control="correct",
                seed=int(settings["seed"]),
                feature_builder=feature_builder,
                feature_normalization=normalization,
            )
            summary = _strip_per_state(
                summarize_revised_predictions(predicted, **metric_kwargs)
            )
            cv_results["structured_feature_interaction"].append(
                {
                    "layer": layer,
                    "epochs": epochs,
                    "fold": fold_index,
                    "train_count": len(fold_train),
                    "validation_count": len(fold_validation),
                    "metrics": summary,
                    "training": training,
                    "checkpoint": str(checkpoint),
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "normalization": {
                        "mean": normalization["mean"].tolist(),
                        "std": normalization["std"].tolist(),
                    },
                    "reused": reused,
                }
            )
            completed_jobs += 1
            attempt.progress(
                status="structured_feature_grouped_cv",
                completed_jobs=completed_jobs,
                total_jobs=total_jobs,
                model_kind="structured_feature_interaction",
                fold=fold_index,
                epochs=epochs,
                latest_validated_checkpoint=str(checkpoint),
            )

    selected: dict[str, Any] = {}
    for kind, results in cv_results.items():
        grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
        for row in results:
            grouped.setdefault((str(row["layer"]), int(row["epochs"])), []).append(row)
        candidates = []
        for (candidate_layer, epochs), fold_rows in sorted(grouped.items()):
            scores = [_cv_score(row["metrics"]) for row in fold_rows]
            candidates.append(
                {
                    "layer": candidate_layer,
                    "epochs": epochs,
                    "fold_count": len(fold_rows),
                    "mean_ndcg@4": statistics.fmean(value[0] for value in scores),
                    "mean_interaction_residual_spearman": statistics.fmean(value[1] for value in scores),
                    "mean_pooled_raw_spearman": statistics.fmean(value[2] for value in scores),
                }
            )
        winner = max(
            candidates,
            key=lambda value: (
                float(value["mean_ndcg@4"]),
                float(value["mean_interaction_residual_spearman"]),
                float(value["mean_pooled_raw_spearman"]),
                -int(value["epochs"]),
                str(value["layer"]) == "final_layer",
            ),
        )
        selected[kind] = {
            **winner,
            "selection_rule": "A-only grouped-CV max NDCG@4 then residual Spearman then raw Spearman then fewer epochs",
            "candidates": candidates,
        }
    atomic_write_json(args.artifact_dir / "parts_c_d/cv_results.json", {"rows": cv_results, "selected": selected})

    full_decomposition = parts_a_b["decomposition"]
    model_results: dict[str, Any] = {}
    gate_results: dict[str, Any] = {}
    train_rows = cells[CELL_A]
    prediction_root = args.artifact_dir / "parts_c_d/predictions"
    for kind in (*MODEL_KINDS, "structured_feature_interaction"):
        winner = selected[kind]
        layer = str(winner["layer"])
        epochs = int(winner["epochs"])
        common = {
            "implementation_commit": args.lambda_head,
            "layer": layer,
            "scope": "all_cell_a",
            "train_pair_ids_sha256": sha256_text("\n".join(sorted(str(row["pair_id"]) for row in train_rows))),
            "state_tensor_sha256": state_cache["tensor_sha256"][layer],
            "transition_tensor_sha256": transition_cache["tensor_sha256"][layer],
            "decomposition_sha256": canonical_json_sha256(full_decomposition),
        }
        main_path = args.artifact_dir / "parts_c_d/checkpoints/main" / f"{layer}.pt"
        main, main_training, main_reused = _main_checkpoint(
            path=main_path,
            decomposition=full_decomposition,
            state_representations=state_tensors[layer],
            transition_representations=transition_tensors[layer],
            state_position=state_position,
            transition_position=transition_position,
            settings=training_settings,
            decomposition_settings=decomposition_settings,
            seed=_seed(int(settings["seed"]), "main", layer, "all"),
            device=device,
            metadata=common,
        )
        feature_normalization = None
        train_features = None
        feature_dim = None
        if kind == "structured_feature_interaction":
            raw_features = feature_builder.rows(train_rows)
            feature_normalization = _normalization(raw_features)
            train_features = _normalize(raw_features, feature_normalization)
            feature_dim = len(feature_builder.feature_names)
        checkpoint = args.artifact_dir / "parts_c_d/checkpoints/models" / f"{kind}.pt"
        metadata = {**common, "kind": kind, "epochs": epochs}
        if train_features is not None:
            metadata["feature_values_sha256"] = tensor_state_sha256(
                {"features": train_features}
            )
        model, training, reused = _interaction_checkpoint(
            path=checkpoint,
            kind=kind,
            main=main,
            decomposition=full_decomposition,
            rows=train_rows,
            state_representations=state_tensors[layer],
            transition_representations=transition_tensors[layer],
            state_position=state_position,
            transition_position=transition_position,
            settings=training_settings,
            epochs=epochs,
            seed=_seed(int(settings["seed"]), kind, layer, "all", epochs),
            device=device,
            metadata=metadata,
            pair_features=train_features,
            feature_dim=feature_dim,
        )
        result = {
            "selected": winner,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": sha256_file(checkpoint),
            "training": training,
            "reused": reused,
            "main_checkpoint": str(main_path),
            "main_training": main_training,
            "main_reused": main_reused,
            "cells": {},
        }
        for cell in CELLS:
            controls = {}
            for control_index, control in enumerate(CONTROLS):
                predicted = _predict(
                    model=model,
                    rows=cells[cell],
                    decomposition=full_decomposition,
                    state_representations=state_tensors[layer],
                    transition_representations=transition_tensors[layer],
                    state_position=state_position,
                    transition_position=transition_position,
                    device=device,
                    control=control,
                    seed=int(settings["seed"]) + control_index,
                    feature_builder=feature_builder if kind == "structured_feature_interaction" else None,
                    feature_normalization=feature_normalization,
                )
                path = prediction_root / kind / cell / f"{control}.jsonl"
                write_jsonl(path, predicted)
                metrics = _strip_per_state(
                    summarize_revised_predictions(predicted, **metric_kwargs)
                )
                controls[control] = {
                    "metrics": metrics,
                    "rows_path": str(path),
                    "rows_sha256": sha256_file(path),
                }
                if control == "correct" and cell in {CELL_B, CELL_C, CELL_D}:
                    controls[control]["task_grouped_bootstrap_ci95"] = task_grouped_bootstrap(
                        predicted,
                        samples=int(settings["metrics"]["bootstrap_samples"]),
                        seed=int(settings["metrics"]["bootstrap_seed"]),
                        metric_settings=bootstrap_settings,
                    )
            result["cells"][cell] = {"controls": controls}
            attempt.progress(
                status="evaluating_multiview_models",
                model_kind=kind,
                cell=cell,
                latest_validated_checkpoint=str(checkpoint),
            )
        d_correct = _load_rows(Path(result["cells"][CELL_D]["controls"]["correct"]["rows_path"]))
        state_only = _single_axis_rows(
            main=main,
            mu=float(full_decomposition["mu"]),
            rows=cells[CELL_D],
            decomposition=full_decomposition,
            state_representations=state_tensors[layer],
            transition_representations=transition_tensors[layer],
            state_position=state_position,
            transition_position=transition_position,
            device=device,
            axis="state",
        )
        transition_only = _single_axis_rows(
            main=main,
            mu=float(full_decomposition["mu"]),
            rows=cells[CELL_D],
            decomposition=full_decomposition,
            state_representations=state_tensors[layer],
            transition_representations=transition_tensors[layer],
            state_position=state_position,
            transition_position=transition_position,
            device=device,
            axis="transition",
        )
        d_shuffled_state = _load_rows(Path(result["cells"][CELL_D]["controls"]["shuffled_state"]["rows_path"]))
        d_shuffled_transition = _load_rows(Path(result["cells"][CELL_D]["controls"]["shuffled_transition"]["rows_path"]))
        baselines = {
            "state_only": _strip_per_state(summarize_revised_predictions(state_only, **metric_kwargs)),
            "transition_only": _strip_per_state(summarize_revised_predictions(transition_only, **metric_kwargs)),
        }
        contrasts = {
            control: paired_task_bootstrap_contrast(
                d_correct,
                _load_rows(Path(result["cells"][CELL_D]["controls"][control]["rows_path"])),
                samples=int(settings["metrics"]["bootstrap_samples"]),
                seed=int(settings["metrics"]["bootstrap_seed"]) + index,
                metric_settings=bootstrap_settings,
            )
            for index, control in enumerate(
                ("shuffled_state", "shuffled_transition", "both_shuffled"), start=1
            )
        }
        per_task = per_task_gate_metrics(
            correct_rows=d_correct,
            state_only_rows=state_only,
            transition_only_rows=transition_only,
            shuffled_state_rows=d_shuffled_state,
            shuffled_transition_rows=d_shuffled_transition,
            metric_settings=bootstrap_settings,
        )
        gate = interaction_gate(
            candidate=result["cells"][CELL_D]["controls"]["correct"]["metrics"],
            state_only=baselines["state_only"],
            transition_only=baselines["transition_only"],
            shuffled_state=result["cells"][CELL_D]["controls"]["shuffled_state"]["metrics"],
            shuffled_transition=result["cells"][CELL_D]["controls"]["shuffled_transition"]["metrics"],
            per_task=per_task,
            transition_shuffle_contrast=contrasts["shuffled_transition"],
            thresholds=settings["interaction_gate"],
        )
        result["baselines"] = baselines
        result["paired_bootstrap_contrasts"] = contrasts
        result["per_heldout_task"] = per_task
        result["gate"] = gate
        if feature_normalization is not None:
            result["feature_normalization"] = {
                "mean": feature_normalization["mean"].tolist(),
                "std": feature_normalization["std"].tolist(),
                "estimated_from": CELL_A,
            }
        model_results[kind] = result
        gate_results[kind] = gate
        atomic_write_json(args.artifact_dir / "parts_c_d/model_results.json", model_results)

    field_candidates = (
        "multiview_signed_bilinear",
        "multiview_lowrank_tensor",
    )
    field_passed = [kind for kind in field_candidates if gate_results[kind]["passed"]]
    qwen_any_passed = [kind for kind in MODEL_KINDS if gate_results[kind]["passed"]]
    structured_passed = bool(gate_results["structured_feature_interaction"]["passed"])
    if field_passed:
        decision = "multiview_state_transition_representation_validated"
        status = "stopped_after_multiview_field_gate_pass"
        next_phase = False
    elif structured_passed and not qwen_any_passed:
        decision = "frozen_qwen_pooling_representation_failure"
        status = "stopped_after_structured_feature_diagnosis"
        next_phase = False
    else:
        decision = "continue_to_prompt_only_cross_encoder"
        status = "parts_c_d_completed_cross_encoder_required"
        next_phase = True
    _verify_exp018_snapshot(exp018, snapshot)
    summary = {
        "format": "interaction_representation_parts_c_d_summary_6c_v1",
        "run_uuid": str(settings["run_uuid"]),
        "source_commit": args.lambda_head,
        "timestamp_utc": utc_now(),
        "status": status,
        "decision_after_part_d": decision,
        "next_phase_required": next_phase,
        "exact_cell_counts": {cell: len(cells[cell]) for cell in CELLS},
        "representation_report": representation_report,
        "structured_feature_report": structured_report,
        "cv_manifest": cv_manifest,
        "cv_results": cv_results,
        "selected_configurations": selected,
        "models": model_results,
        "gates": gate_results,
        "runtime_seconds": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "qwen_behavioral_backpropagation_run": False,
            "behavioral_program_trained": False,
            "injector_trained": False,
            "selector_modified": False,
            "production_transition_field_constructed": False,
            "appworld_generation_or_evaluation_run": False,
            "stage_c2_started": False,
            "end_to_end_rcmf_started": False,
            "full_demo_examples_changed": False,
            "v4_tag_created_or_moved": False,
        },
    }
    atomic_write_json(args.artifact_dir / "parts_c_d_summary.json", summary)
    atomic_write_text(args.artifact_dir / "parts_c_d_report.md", _markdown_report(summary))
    attempt.progress(
        status=status,
        decision_after_part_d=decision,
        latest_validated_checkpoint=str(args.artifact_dir / "parts_c_d_summary.json"),
    )
    return summary


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-019 Parts C/D: Span-Aware Multi-View Interaction",
        "",
        f"- status: `{summary['status']}`",
        f"- decision: `{summary['decision_after_part_d']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- no truncation: `{summary['representation_report']['no_truncation']}`",
        f"- span decoded/aligned-source checks valid: `{summary['representation_report']['all_span_decoded_aligned_source_valid']}`",
        "",
        "## Double-Held-Out Results",
        "",
        "| Model | Layer | NDCG@4 | Raw Spearman | Residual Spearman | State shuffle NDCG@4 | Transition shuffle NDCG@4 | Gate |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for kind, result in summary["models"].items():
        controls = result["cells"][CELL_D]["controls"]
        correct = controls["correct"]["metrics"]
        lines.append(
            f"| {kind} | {result['selected']['layer']} | "
            f"{float(correct['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{float(correct['pooled_raw_spearman'] or 0):.6f} | "
            f"{float(correct['interaction_residual_spearman'] or 0):.6f} | "
            f"{float(controls['shuffled_state']['metrics']['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{float(controls['shuffled_transition']['metrics']['per_state']['ndcg@4']['mean'] or 0):.6f} | "
            f"{result['gate']['passed']} |"
        )
    lines.extend(
        [
            "",
            "No behavioral program, injector, selector, production field, Qwen "
            "behavioral backpropagation, AppWorld generation/evaluation, Stage C2, "
            "end-to-end RCMF training, full-demo modification, or V4 tagging was performed.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EXP-019 conditional multi-view Parts C/D")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp019")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6c"]
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.artifact_dir / "parts_c_d_summary.json"
    if summary_path.exists():
        summary = _load_json(summary_path)
        print(json.dumps({"reused": True, "summary": str(summary_path), "decision": summary["decision_after_part_d"]}, sort_keys=True))
        return
    attempts = _load_rows(args.artifact_dir / "attempts.jsonl")
    if any(str(row.get("attempt_id")) == args.attempt_id for row in attempts):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="parts_c_d",
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
        summary = _run_parts_c_d(
            args=args, cfg=cfg, settings=settings, attempt=attempt
        )
    print(json.dumps({"reused": False, "summary": str(summary_path), "decision": summary["decision_after_part_d"]}, sort_keys=True))


if __name__ == "__main__":
    main()
