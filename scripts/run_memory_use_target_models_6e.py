from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
from torch import Tensor
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.cross_encoder_6c import (
    controlled_feature_matrix,
    cross_encoder_control_sources,
)
from rcmf.training.interaction_representation_6c import (
    paired_task_bootstrap_contrast,
    summarize_revised_predictions,
    task_grouped_bootstrap,
)
from rcmf.training.memory_use_target_6e import (
    CachedArchitectureScorer,
    IntentCompatibilityModel,
    gap_weighted_pairwise_accuracy,
    intent_feature_vector,
    relative_target_objective,
    stable_key,
    summarize_target_predictions,
)
from rcmf.training.multiview_representations_6c import LAYER_CANDIDATES
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    build_grouped_cv_manifest,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_action_intent_probe_6d import ActionIntentProbe, LABEL_NAMES


CELLS = ("B", "C", "D")
CONTROLS = ("correct", "shuffled_state", "shuffled_transition", "both_shuffled")
MODEL_TARGETS = ("T3", "T4", "T6", "T7")
EXP020_CELL_NAMES = {
    "B": "heldout_state__train_transition",
    "C": "train_state__heldout_transition",
    "D": "heldout_state__heldout_transition",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows at {path}")
    return rows


def _seed(base: int, *parts: Any) -> int:
    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def _device(value: str) -> torch.device:
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    return device


def _load_representations(exp020: Path) -> dict[str, Any]:
    state_path = exp020 / "representation_cache/multiview/state_multiview.pt"
    transition_path = exp020 / "representation_cache/multiview/transition_multiview.pt"
    cross_path = exp020 / "representation_cache/cross_encoder/cross_encoder_representations.pt"
    state = torch.load(state_path, map_location="cpu", weights_only=False)
    transition = torch.load(transition_path, map_location="cpu", weights_only=False)
    cross = torch.load(cross_path, map_location="cpu", weights_only=False)
    layer = "final_layer"
    state_values = state["representations"][layer].to(torch.float32)
    transition_values = transition["representations"][layer].to(torch.float32)
    state_ids = [str(value) for value in state["ordered_ids"]]
    transition_ids = [str(value) for value in transition["ordered_ids"]]
    cross_ids = [str(value) for value in cross["ordered_pair_ids"]]
    return {
        "state_values": state_values,
        "transition_values": transition_values,
        "cross_values": cross["representations"].to(torch.float32),
        "state_position": {value: index for index, value in enumerate(state_ids)},
        "transition_position": {value: index for index, value in enumerate(transition_ids)},
        "cross_position": {value: index for index, value in enumerate(cross_ids)},
        "hashes": {
            "state": sha256_file(state_path),
            "transition": sha256_file(transition_path),
            "cross": sha256_file(cross_path),
        },
    }


def _ece(probabilities: Tensor, targets: Tensor, bins: int) -> float:
    confidence, predicted = probabilities.max(dim=-1)
    correct = predicted.eq(targets)
    value = 0.0
    for index in range(int(bins)):
        low = index / bins
        high = (index + 1) / bins
        mask = confidence.ge(low) & (confidence.lt(high) if index + 1 < bins else confidence.le(high))
        if mask.any():
            value += float(mask.float().mean()) * abs(
                float(confidence[mask].mean()) - float(correct[mask].float().mean())
            )
    return value


def _intent_predictions(
    *, exp020: Path, query_ids: set[str], settings: Mapping[str, Any]
) -> tuple[dict[str, dict[str, dict[str, float]]], dict[str, Any]]:
    root = exp020 / "action_intent"
    cache = torch.load(
        root / "representation_cache/all_successful_state_multiview.pt",
        map_location="cpu", weights_only=False,
    )
    checkpoint = torch.load(root / "action_intent_probe.pt", map_location="cpu", weights_only=False)
    values = cache["representations"]["final_layer"].to(torch.float32).flatten(1)
    rows = list(cache["rows"])
    if len(values) != len(rows):
        raise ValueError("Action-intent cache tensor/row count differs")
    normalized = (values - checkpoint["normalization"]["mean"]) / checkpoint["normalization"]["std"]
    vocabularies = checkpoint["vocabularies"]
    model = ActionIntentProbe(
        int(normalized.shape[-1]),
        int(settings["intent"]["probe_hidden_dim"]),
        {name: len(vocabularies[name]) for name in LABEL_NAMES},
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    with torch.no_grad():
        logits = {name: [] for name in LABEL_NAMES}
        for start in range(0, len(normalized), 64):
            batch = model(normalized[start : start + 64])
            for name in LABEL_NAMES:
                logits[name].append(batch[name].cpu())
        logits = {name: torch.cat(parts) for name, parts in logits.items()}
    train_indices = [index for index, row in enumerate(rows) if row["split"] == "train"]
    temperatures = {}
    for name in LABEL_NAMES:
        position = {value: index for index, value in enumerate(vocabularies[name])}
        target = torch.tensor([position[str(rows[index]["labels"][name])] for index in train_indices])
        selected_logits = logits[name][train_indices]
        candidates = []
        for temperature in settings["intent"]["calibration_temperatures"]:
            nll = float(F.cross_entropy(selected_logits / float(temperature), target))
            candidates.append((nll, float(temperature)))
        temperatures[name] = min(candidates)[1]
    probabilities = {
        name: torch.softmax(logits[name] / temperatures[name], dim=-1)
        for name in LABEL_NAMES
    }
    by_id = {}
    for index, row in enumerate(rows):
        identity = str(row["state_example_id"])
        if identity not in query_ids:
            continue
        by_id[identity] = {
            name: {
                label: float(probabilities[name][index, position])
                for position, label in enumerate(vocabularies[name])
            }
            for name in LABEL_NAMES
        }
    if set(by_id) != query_ids:
        raise ValueError(f"Missing query intent predictions: {sorted(query_ids - set(by_id))[:5]}")

    validation_indices = [
        index for index, row in enumerate(rows)
        if row["split"] == "validation" and str(row["state_example_id"]) in query_ids
    ]
    permutation = list(validation_indices)
    random.Random(_seed(int(settings["seed"]), "intent-shuffle")).shuffle(permutation)
    heads = {}
    for name in LABEL_NAMES:
        vocab = vocabularies[name]
        position = {value: index for index, value in enumerate(vocab)}
        known = [index for index in validation_indices if str(rows[index]["labels"][name]) in position]
        target = torch.tensor([position[str(rows[index]["labels"][name])] for index in known])
        probs = probabilities[name][known]
        predicted = probs.argmax(dim=-1)
        shuffled_lookup = {source: replacement for source, replacement in zip(validation_indices, permutation)}
        shuffled_probs = probabilities[name][[shuffled_lookup[index] for index in known]]
        confusion: dict[str, Counter[str]] = defaultdict(Counter)
        for truth, estimate in zip(target.tolist(), predicted.tolist()):
            confusion[vocab[truth]][vocab[estimate]] += 1
        one_hot = F.one_hot(target, num_classes=len(vocab)).to(torch.float32)
        heads[name] = {
            "temperature": temperatures[name],
            "known_count": len(known),
            "class_coverage": len(known) / len(validation_indices),
            "correct_accuracy": float(predicted.eq(target).float().mean()) if known else None,
            "shuffled_state_accuracy": float(shuffled_probs.argmax(dim=-1).eq(target).float().mean()) if known else None,
            "nll": float(F.nll_loss(probs.clamp_min(1.0e-12).log(), target)) if known else None,
            "brier": float((probs - one_hot).square().sum(dim=-1).mean()) if known else None,
            "ece": _ece(probs, target, int(settings["intent"]["calibration_bins"])) if known else None,
            "confusion_matrix": {key: dict(value) for key, value in confusion.items()},
            "vocabulary": vocab,
        }
    summary = {
        "format": "calibrated_action_intent_predictions_6e_v1",
        "query_count": len(by_id),
        "heldout_query_count": len(validation_indices),
        "calibration_source": "train decision examples only",
        "temperature_grid": list(settings["intent"]["calibration_temperatures"]),
        "heads": heads,
        "mean_correct_accuracy": statistics.fmean(float(heads[name]["correct_accuracy"] or 0.0) for name in LABEL_NAMES),
        "mean_shuffled_accuracy": statistics.fmean(float(heads[name]["shuffled_state_accuracy"] or 0.0) for name in LABEL_NAMES),
        "source_checkpoint": str(root / "action_intent_probe.pt"),
        "source_checkpoint_sha256": sha256_file(root / "action_intent_probe.pt"),
    }
    return by_id, summary


def _oracle_probabilities(row: Mapping[str, Any], vocabularies: Mapping[str, Sequence[str]]) -> dict[str, dict[str, float]]:
    signature = row["query_action_signature"]
    labels = {
        "target_app": str(signature["primary_app"]),
        "target_api": str(signature["primary_api"]),
        "action_type": str(signature["probe_action_type"]),
        "completion_action": str(bool(signature["completion_action"])).lower(),
    }
    return {
        name: {label: float(label == labels[name]) for label in vocabularies[name]}
        for name in LABEL_NAMES
    }


def _fit_intent_model(
    rows: Sequence[Mapping[str, Any]], *, settings: Mapping[str, Any], device: torch.device
) -> tuple[IntentCompatibilityModel, dict[str, Any]]:
    features = torch.tensor([row["intent_features_predicted"] for row in rows], dtype=torch.float32, device=device)
    model = IntentCompatibilityModel(features.shape[-1]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(settings["models"]["learning_rate"]), weight_decay=float(settings["models"]["weight_decay"]))
    history = []
    for epoch in range(1, max(int(value) for value in settings["models"]["epoch_candidates"]) + 1):
        scores = model(features)
        loss, parts = relative_target_objective(
            scores, rows, target_name="T3",
            pair_gap_threshold=float(settings["targets"]["pair_gap_threshold"]),
            pair_gap_weight_clip=float(settings["targets"]["pair_gap_weight_clip"]),
            teacher_temperature=float(settings["models"]["teacher_temperature"]),
            student_temperature=float(settings["models"]["student_temperature"]),
            huber_delta=float(settings["models"]["huber_delta"]),
            loss_weights=settings["models"]["losses"],
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        if epoch in set(int(value) for value in settings["models"]["epoch_candidates"]):
            history.append({"epoch": epoch, "loss": float(loss.detach().cpu()), **{key: float(value.detach().cpu()) for key, value in parts.items()}})
    model.eval()
    return model, {"history": history, "epochs": history[-1]["epoch"]}


def _derangement(values: Sequence[str], seed: int, namespace: str) -> dict[str, str]:
    unique = sorted(set(str(value) for value in values))
    if len(unique) <= 1:
        return {value: value for value in unique}
    shift = 1 + int(stable_key(seed, namespace), 16) % (len(unique) - 1)
    return {value: unique[(index + shift) % len(unique)] for index, value in enumerate(unique)}


def _tensor_inputs(
    rows: Sequence[Mapping[str, Any]], reps: Mapping[str, Any], kind: str,
    *, state_map: Mapping[str, str] | None = None,
    transition_map: Mapping[str, str] | None = None,
) -> tuple[Tensor | None, Tensor | None, Tensor | None]:
    if kind == "cross":
        return None, None, torch.stack([
            reps["cross_values"][reps["cross_position"][str(row["pair_id"])]] for row in rows
        ])
    state = torch.stack([
        reps["state_values"][reps["state_position"][
            (state_map or {}).get(str(row["state_example_id"]), str(row["state_example_id"]))
        ]] for row in rows
    ])
    transition = torch.stack([
        reps["transition_values"][reps["transition_position"][
            (transition_map or {}).get(str(row["transition_id"]), str(row["transition_id"]))
        ]] for row in rows
    ])
    return state, transition, None


def _new_model(kind: str, reps: Mapping[str, Any], settings: Mapping[str, Any], seed: int) -> CachedArchitectureScorer:
    torch.manual_seed(int(seed))
    return CachedArchitectureScorer(
        kind,
        state_views=int(reps["state_values"].shape[1]),
        transition_views=int(reps["transition_values"].shape[1]),
        input_dim=int(reps["state_values"].shape[-1]),
        cross_dim=int(reps["cross_values"].shape[-1]),
        projection_dim=int(settings["models"]["projection_dim"]),
        interaction_rank=int(settings["models"]["interaction_rank"]),
        hidden_dim=int(settings["models"]["hidden_dim"]),
        dropout=float(settings["models"]["dropout"]),
    )


def _checkpoint_metadata(
    *, kind: str, target: str, fold: str, rows: Sequence[Mapping[str, Any]], seed: int
) -> dict[str, Any]:
    return {
        "format": "memory_use_target_model_checkpoint_6e_v1",
        "kind": kind,
        "target": target,
        "fold": fold,
        "seed": int(seed),
        "train_pair_ids_sha256": sha256_text("\n".join(sorted(str(row["pair_id"]) for row in rows))),
    }


def _train_checkpoints(
    *, model: CachedArchitectureScorer, rows: Sequence[Mapping[str, Any]],
    reps: Mapping[str, Any], kind: str, target: str,
    intent_model: IntentCompatibilityModel, settings: Mapping[str, Any],
    output_dir: Path, metadata: Mapping[str, Any], device: torch.device,
) -> tuple[dict[int, Path], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted(int(value) for value in settings["models"]["epoch_candidates"])
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(settings["models"]["learning_rate"]), weight_decay=float(settings["models"]["weight_decay"]))
    latest_epoch = 0
    paths = {epoch: output_dir / f"epoch_{epoch:03d}.pt" for epoch in candidates}
    existing = [epoch for epoch in candidates if paths[epoch].exists()]
    if existing:
        latest_epoch = max(existing)
        payload = torch.load(paths[latest_epoch], map_location="cpu", weights_only=False)
        if payload["metadata"] != dict(metadata) or int(payload["epoch"]) != latest_epoch:
            raise ValueError(f"Existing checkpoint metadata differs: {paths[latest_epoch]}")
        model.load_state_dict(payload["model_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
    model.to(device).train()
    state, transition, cross = _tensor_inputs(rows, reps, kind)
    state = state.to(device) if state is not None else None
    transition = transition.to(device) if transition is not None else None
    cross = cross.to(device) if cross is not None else None
    with torch.no_grad():
        intent_base = intent_model(torch.tensor([row["intent_features_predicted"] for row in rows], dtype=torch.float32, device=device)) if target == "T6" else torch.zeros(len(rows), device=device)
    history = []
    for epoch in range(latest_epoch + 1, candidates[-1] + 1):
        score = model(state, transition, cross) + intent_base
        loss, parts = relative_target_objective(
            score, rows, target_name=target,
            pair_gap_threshold=float(settings["targets"]["pair_gap_threshold"]),
            pair_gap_weight_clip=float(settings["targets"]["pair_gap_weight_clip"]),
            teacher_temperature=float(settings["models"]["teacher_temperature"]),
            student_temperature=float(settings["models"]["student_temperature"]),
            huber_delta=float(settings["models"]["huber_delta"]),
            loss_weights=settings["models"]["losses"],
            matched_intent_only=target == "T7",
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        gradient_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).detach().cpu())
        optimizer.step()
        if epoch in candidates:
            history.append({"epoch": epoch, "loss": float(loss.detach().cpu()), "gradient_norm": gradient_norm, **{key: float(value.detach().cpu()) for key, value in parts.items()}})
            atomic_torch_save({
                "metadata": dict(metadata), "epoch": epoch,
                "model_state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
                "optimizer_state_dict": optimizer.state_dict(), "history": history,
            }, paths[epoch])
    return paths, history


def _score_model(
    *, model: CachedArchitectureScorer, rows: Sequence[Mapping[str, Any]],
    reps: Mapping[str, Any], kind: str, target: str,
    intent_model: IntentCompatibilityModel,
    predicted_intent: Mapping[str, Mapping[str, Mapping[str, float]]],
    control: str, seed: int, device: torch.device,
) -> list[dict[str, Any]]:
    state_map = None
    transition_map = None
    if control in {"shuffled_state", "both_shuffled"}:
        state_map = _derangement([str(row["state_example_id"]) for row in rows], seed, f"{control}:state")
    if control in {"shuffled_transition", "both_shuffled"}:
        transition_map = _derangement([str(row["transition_id"]) for row in rows], seed, f"{control}:transition")
    if kind == "cross" and control != "correct":
        sources = cross_encoder_control_sources(rows, seed=seed)
        feature_by_pair = {
            pair_id: reps["cross_values"][position]
            for pair_id, position in reps["cross_position"].items()
        }
        cross = controlled_feature_matrix(
            rows=rows, feature_by_pair=feature_by_pair,
            control_sources=sources, control=control,
        )
        state = transition = None
    else:
        state, transition, cross = _tensor_inputs(
            rows, reps, kind, state_map=state_map, transition_map=transition_map
        )
    with torch.no_grad():
        interaction = model(
            state.to(device) if state is not None else None,
            transition.to(device) if transition is not None else None,
            cross.to(device) if cross is not None else None,
        ).detach().cpu()
    intent_scores = torch.zeros(len(rows))
    if target == "T6":
        features = []
        for row in rows:
            state_id = (state_map or {}).get(str(row["state_example_id"]), str(row["state_example_id"]))
            transition_id = (transition_map or {}).get(str(row["transition_id"]), str(row["transition_id"]))
            transition_signature = row["transition_signature"]
            if transition_id != str(row["transition_id"]):
                source = next(value for value in rows if str(value["transition_id"]) == transition_id)
                transition_signature = source["transition_signature"]
            features.append(intent_feature_vector(predicted_intent[state_id], transition_signature))
        with torch.no_grad():
            intent_scores = intent_model(torch.tensor(features, dtype=torch.float32, device=device)).cpu()
    scores = interaction + intent_scores
    return [{
        **dict(row), "score": float(scores[index]),
        "interaction_score": float(interaction[index]),
        "intent_score": float(intent_scores[index]), "control": control,
    } for index, row in enumerate(rows)]


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["metrics"]["ranking_ks"],
        "neutral_epsilon": float(settings["targets"]["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
        "huber_delta": float(settings["models"]["huber_delta"]),
        "pair_gap_threshold": float(settings["targets"]["pair_gap_threshold"]),
        "pair_gap_weight_clip": float(settings["targets"]["pair_gap_weight_clip"]),
    }


def _bootstrap_rows(rows: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> dict[str, Any]:
    normalized = [{
        "pair_id": row["pair_id"], "state_example_id": row["state_example_id"],
        "state_task_id": row["state_task_id"], "transition_id": row["transition_id"],
        "transition_parent_id": row["transition_parent_id"], "cell": row["cell"],
        "utility_category": row["utility_category"], "u_text": float(row["text_utility"]),
        "u_predicted": float(row["score"]),
        "residual_target": float(row.get("raw_residual_target", 0.0)),
        "residual_predicted": float(row.get("interaction_score", row["score"])),
    } for row in rows]
    return task_grouped_bootstrap(
        normalized, samples=int(settings["metrics"]["bootstrap_samples"]),
        seed=int(settings["metrics"]["bootstrap_seed"]),
        metric_settings={
            "ranking_ks": settings["metrics"]["ranking_ks"],
            "neutral_epsilon": float(settings["targets"]["neutral_epsilon"]),
            "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
            "huber_delta": float(settings["models"]["huber_delta"]),
        },
    )


def _cv_models(
    *, rows_a: list[dict[str, Any]], reps: Mapping[str, Any],
    intent_model: IntentCompatibilityModel, predicted_intent: Mapping[str, Any],
    settings: Mapping[str, Any], output_root: Path, device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    manifest = build_grouped_cv_manifest(
        rows_a, folds=int(settings["models"]["grouped_cv_folds"]),
        seed=int(settings["models"]["grouped_cv_seed"]),
    )
    row_by_id = {str(row["pair_id"]): row for row in rows_a}
    results = {}
    for target in MODEL_TARGETS:
        results[target] = {}
        for kind in ("field", "cross"):
            fold_candidates = []
            for fold in manifest["folds"]:
                fold_index = int(fold["fold"])
                train_rows = [row_by_id[value] for value in fold["train_pair_ids"]]
                validation_rows = [row_by_id[value] for value in fold["validation_pair_ids"]]
                seed = _seed(int(settings["seed"]), "cv", target, kind, fold_index)
                model = _new_model(kind, reps, settings, seed)
                metadata = _checkpoint_metadata(kind=kind, target=target, fold=str(fold_index), rows=train_rows, seed=seed)
                paths, history = _train_checkpoints(
                    model=model, rows=train_rows, reps=reps, kind=kind, target=target,
                    intent_model=intent_model, settings=settings,
                    output_dir=output_root / target / kind / f"fold_{fold_index}",
                    metadata=metadata, device=device,
                )
                for epoch, path in paths.items():
                    payload = torch.load(path, map_location="cpu", weights_only=False)
                    candidate = _new_model(kind, reps, settings, seed)
                    candidate.load_state_dict(payload["model_state_dict"])
                    candidate.to(device).eval()
                    controls = {}
                    for control in CONTROLS:
                        predicted = _score_model(
                            model=candidate, rows=validation_rows, reps=reps,
                            kind=kind, target=target, intent_model=intent_model,
                            predicted_intent=predicted_intent, control=control,
                            seed=_seed(seed, "control", control), device=device,
                        )
                        controls[control] = summarize_target_predictions(
                            predicted, target_key="T3" if target != "T0" else "T0",
                            **_metric_kwargs(settings),
                        )
                    correct = controls["correct"]
                    fold_candidates.append({
                        "fold": fold_index, "epochs": epoch,
                        "train_pair_count": len(train_rows),
                        "validation_pair_count": len(validation_rows),
                        "ndcg@4": correct["raw_utility"]["per_state"]["ndcg@4"]["mean"],
                        "gap_pairwise": correct["gap_weighted_pairwise_accuracy"],
                        "state_shuffle_gap": float(correct["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0) - float(controls["shuffled_state"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0),
                        "transition_shuffle_gap": float(correct["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0) - float(controls["shuffled_transition"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0),
                        "controls": controls,
                    })
                attempt.progress(
                    status="target_grouped_cv", target=target, architecture=kind,
                    fold=fold_index, latest_validated_checkpoint=str(paths[max(paths)]),
                )
            candidates = []
            for epoch in sorted(set(row["epochs"] for row in fold_candidates)):
                selected = [row for row in fold_candidates if row["epochs"] == epoch]
                candidates.append({
                    "epochs": epoch,
                    "mean_ndcg@4": statistics.fmean(float(row["ndcg@4"] or 0.0) for row in selected),
                    "mean_gap_pairwise": statistics.fmean(float(row["gap_pairwise"] or 0.0) for row in selected),
                    "mean_state_shuffle_gap": statistics.fmean(float(row["state_shuffle_gap"]) for row in selected),
                    "mean_transition_shuffle_gap": statistics.fmean(float(row["transition_shuffle_gap"]) for row in selected),
                    "positive_ndcg_folds": sum(float(row["ndcg@4"] or 0.0) > 0.0 for row in selected),
                })
            chosen = max(candidates, key=lambda row: (
                float(row["mean_ndcg@4"]), float(row["mean_gap_pairwise"]),
                float(row["mean_state_shuffle_gap"]), float(row["mean_transition_shuffle_gap"]),
                -int(row["epochs"]),
            ))
            results[target][kind] = {
                "selected": chosen, "candidates": candidates,
                "folds": [row for row in fold_candidates if row["epochs"] == chosen["epochs"]],
            }
    primary = max(
        ({"target": target, **results[target]["field"]["selected"]} for target in MODEL_TARGETS),
        key=lambda row: (
            float(row["mean_ndcg@4"]), float(row["mean_gap_pairwise"]),
            float(row["mean_state_shuffle_gap"]), float(row["mean_transition_shuffle_gap"]),
            -int(row["epochs"]),
        ),
    )
    return {
        "format": "memory_use_target_grouped_cv_6e_v1",
        "manifest": manifest,
        "targets": results,
        "selected_primary_revised_target": primary,
        "selection_rule": "A-only grouped CV: field raw NDCG@4, gap-weighted pair accuracy, state-shuffle gap, transition-shuffle gap, then fewer epochs",
        "selection_frozen_before_bcd": True,
    }


def _intent_only_rows(
    rows: Sequence[Mapping[str, Any]], *, intent_model: IntentCompatibilityModel,
    probabilities: Mapping[str, Any], control: str, seed: int, device: torch.device,
    oracle: bool = False,
) -> list[dict[str, Any]]:
    state_map = _derangement([str(row["state_example_id"]) for row in rows], seed, "intent-state") if control in {"shuffled_state", "both_shuffled"} else {}
    transition_map = _derangement([str(row["transition_id"]) for row in rows], seed, "intent-transition") if control in {"shuffled_transition", "both_shuffled"} else {}
    transition_rows = {str(row["transition_id"]): row for row in rows}
    features = []
    for row in rows:
        if oracle:
            values = row["intent_features_oracle"]
        else:
            state_id = state_map.get(str(row["state_example_id"]), str(row["state_example_id"]))
            transition_id = transition_map.get(str(row["transition_id"]), str(row["transition_id"]))
            transition_signature = transition_rows.get(transition_id, row)["transition_signature"]
            values = intent_feature_vector(probabilities[state_id], transition_signature)
        features.append(values)
    with torch.no_grad():
        scores = intent_model(torch.tensor(features, dtype=torch.float32, device=device)).cpu()
    return [{**dict(row), "score": float(scores[index]), "interaction_score": 0.0, "intent_score": float(scores[index]), "control": control} for index, row in enumerate(rows)]


def _transition_only_rows(rows_a: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows_a:
        grouped[str(row["transition_id"])].append(float(row["text_utility"]))
    global_mean = statistics.fmean(float(row["text_utility"]) for row in rows_a)
    means = {key: statistics.fmean(values) for key, values in grouped.items()}
    return [{**dict(row), "score": float(means.get(str(row["transition_id"]), global_mean)), "interaction_score": 0.0, "intent_score": 0.0, "control": "transition_only"} for row in rows]


def _locked_transition_only_baselines(
    locked_level: Mapping[str, Any], *, verify_rows: bool = True
) -> dict[str, dict[str, Any]]:
    """Load the immutable EXP-020 LC37 transition-only comparator."""
    cells = locked_level["models"]["transition_only"]["cells"]
    output: dict[str, dict[str, Any]] = {}
    for short_name, long_name in EXP020_CELL_NAMES.items():
        correct = cells[long_name]["controls"]["correct"]
        rows_path = Path(str(correct["rows_path"]))
        rows_sha256 = str(correct["rows_sha256"])
        if verify_rows:
            if not rows_path.is_file():
                raise FileNotFoundError(
                    f"Locked EXP-020 transition-only rows are missing: {rows_path}"
                )
            actual_sha256 = sha256_file(rows_path)
            if actual_sha256 != rows_sha256:
                raise ValueError(
                    "Locked EXP-020 transition-only row hash differs for "
                    f"cell {short_name}: {actual_sha256} != {rows_sha256}"
                )
        metrics = copy.deepcopy(correct["metrics"])
        locked_rows = _load_rows(rows_path) if verify_rows else []
        normalized_rows = (
            _normalized_raw_metric_rows(locked_rows) if locked_rows else []
        )
        output[short_name] = {
            "count": int(metrics["count"]),
            "raw_utility": metrics,
            "candidate_target": copy.deepcopy(metrics),
            "raw_utility_huber": float(metrics["raw_huber"]),
            "candidate_target_huber": float(metrics["raw_huber"]),
            "gap_weighted_pairwise_accuracy": (
                gap_weighted_pairwise_accuracy(
                    normalized_rows, threshold=0.05, weight_clip=0.25
                )
                if normalized_rows
                else None
            ),
            "locked_source": {
                "experiment": "EXP-020",
                "level": str(locked_level["level"]),
                "cell": long_name,
                "rows_path": str(rows_path),
                "rows_sha256": rows_sha256,
            },
        }
    return output


def _final_models(
    *, rows: list[dict[str, Any]], rows_a: list[dict[str, Any]], reps: Mapping[str, Any],
    cv: Mapping[str, Any], intent_model: IntentCompatibilityModel,
    predicted_intent: Mapping[str, Any], settings: Mapping[str, Any],
    locked_transition_only: Mapping[str, Mapping[str, Any]],
    output_root: Path, device: torch.device, attempt: AttemptLedger,
) -> dict[str, Any]:
    cells = {cell: [row for row in rows if str(row["cell"]) == cell] for cell in CELLS}
    results: dict[str, Any] = {}
    for target in MODEL_TARGETS:
        results[target] = {}
        for kind in ("field", "cross"):
            epochs = int(cv["targets"][target][kind]["selected"]["epochs"])
            seed = _seed(int(settings["seed"]), "final", target, kind)
            model = _new_model(kind, reps, settings, seed)
            metadata = _checkpoint_metadata(kind=kind, target=target, fold="all_A", rows=rows_a, seed=seed)
            paths, history = _train_checkpoints(
                model=model, rows=rows_a, reps=reps, kind=kind, target=target,
                intent_model=intent_model, settings={
                    **settings,
                    "models": {**settings["models"], "epoch_candidates": [epochs]},
                }, output_dir=output_root / target / kind,
                metadata=metadata, device=device,
            )
            payload = torch.load(paths[epochs], map_location="cpu", weights_only=False)
            model.load_state_dict(payload["model_state_dict"])
            model.to(device).eval()
            cell_results = {}
            for cell, selected in cells.items():
                controls = {}
                prediction_dir = output_root / "predictions" / target / kind / cell
                prediction_dir.mkdir(parents=True, exist_ok=True)
                prediction_rows = {}
                for control in CONTROLS:
                    predicted = _score_model(
                        model=model, rows=selected, reps=reps, kind=kind, target=target,
                        intent_model=intent_model, predicted_intent=predicted_intent,
                        control=control, seed=_seed(seed, cell, control), device=device,
                    )
                    path = prediction_dir / f"{control}.jsonl"
                    write_jsonl(path, predicted)
                    prediction_rows[control] = predicted
                    controls[control] = {
                        "metrics": summarize_target_predictions(
                            predicted, target_key="T3", **_metric_kwargs(settings)
                        ),
                        "rows_path": str(path), "rows_sha256": sha256_file(path),
                    }
                    if control == "correct":
                        controls[control]["task_grouped_bootstrap_ci95"] = _bootstrap_rows(predicted, settings)
                normalized_correct = [{
                    "pair_id": row["pair_id"], "state_example_id": row["state_example_id"],
                    "state_task_id": row["state_task_id"], "transition_id": row["transition_id"],
                    "transition_parent_id": row["transition_parent_id"], "cell": row["cell"],
                    "utility_category": row["utility_category"], "u_text": float(row["text_utility"]),
                    "u_predicted": float(row["score"]), "residual_target": float(row.get("raw_residual_target", 0.0)),
                    "residual_predicted": float(row.get("interaction_score", row["score"])),
                } for row in prediction_rows["correct"]]
                contrasts = {}
                for control in ("shuffled_state", "shuffled_transition", "both_shuffled"):
                    normalized_control = [{**base, "u_predicted": float(row["score"]), "residual_predicted": float(row.get("interaction_score", row["score"]))} for base, row in zip(normalized_correct, prediction_rows[control])]
                    contrasts[control] = paired_task_bootstrap_contrast(
                        normalized_correct, normalized_control,
                        samples=int(settings["metrics"]["bootstrap_samples"]),
                        seed=int(settings["metrics"]["bootstrap_seed"]) + list(CONTROLS).index(control),
                        metric_settings={
                            "ranking_ks": settings["metrics"]["ranking_ks"],
                            "neutral_epsilon": float(settings["targets"]["neutral_epsilon"]),
                            "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
                            "huber_delta": float(settings["models"]["huber_delta"]),
                        },
                    )
                cell_results[cell] = {"controls": controls, "paired_bootstrap_contrasts": contrasts}
            results[target][kind] = {
                "selected_epochs": epochs, "training_history": history,
                "checkpoint": str(paths[epochs]), "checkpoint_sha256": sha256_file(paths[epochs]),
                "cells": cell_results,
            }
            attempt.progress(status="final_target_model", target=target, architecture=kind, latest_validated_checkpoint=str(paths[epochs]))

    baselines = {"transition_only": {}, "intent_only_predicted": {}, "intent_only_oracle": {}}
    for cell, selected in cells.items():
        baselines["transition_only"][cell] = copy.deepcopy(
            locked_transition_only[cell]
        )
        for oracle, name in ((False, "intent_only_predicted"), (True, "intent_only_oracle")):
            controls = {}
            for control in CONTROLS:
                predicted = _intent_only_rows(
                    selected, intent_model=intent_model, probabilities=predicted_intent,
                    control=control, seed=_seed(int(settings["seed"]), name, cell, control),
                    device=device, oracle=oracle,
                )
                controls[control] = summarize_target_predictions(predicted, target_key="T5_oracle", **_metric_kwargs(settings))
            baselines[name][cell] = controls
    return {"models": results, "baselines": baselines}


def _normalized_raw_metric_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        normalized.append(
            {
                **dict(row),
                "u_text": float(row.get("u_text", row.get("text_utility"))),
                "u_predicted": float(row.get("u_predicted", row.get("score"))),
                "residual_target": float(
                    row.get("residual_target", row.get("raw_residual_target", 0.0))
                ),
                "residual_predicted": float(
                    row.get(
                        "residual_predicted",
                        row.get("interaction_score", row.get("score", 0.0)),
                    )
                ),
            }
        )
    return normalized


def _per_task_relative_behavior(
    rows: Sequence[Mapping[str, Any]],
    transition_baseline_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _normalized_raw_metric_rows(rows):
        candidate_by_task[str(row["state_task_id"])].append(row)
    for row in _normalized_raw_metric_rows(transition_baseline_rows):
        baseline_by_task[str(row["state_task_id"])].append(row)
    if set(candidate_by_task) != set(baseline_by_task):
        raise ValueError("Candidate and locked transition baseline task sets differ")
    metric_kwargs = {
        "ranking_ks": (1, 4, 8),
        "neutral_epsilon": 0.01,
        "best_tie_tolerance": 1.0e-8,
        "huber_delta": 0.1,
    }
    tasks = {}
    for task_id in sorted(candidate_by_task):
        candidate = candidate_by_task[task_id]
        baseline = baseline_by_task[task_id]
        if {str(row["pair_id"]) for row in candidate} != {
            str(row["pair_id"]) for row in baseline
        }:
            raise ValueError(
                f"Candidate and locked transition baseline pair IDs differ for {task_id}"
            )
        candidate_summary = summarize_revised_predictions(candidate, **metric_kwargs)
        baseline_summary = summarize_revised_predictions(baseline, **metric_kwargs)
        candidate_ndcg = float(
            candidate_summary["per_state"]["ndcg@4"]["mean"] or 0.0
        )
        baseline_ndcg = float(
            baseline_summary["per_state"]["ndcg@4"]["mean"] or 0.0
        )
        tasks[task_id] = {
            "candidate_ndcg@4": candidate_ndcg,
            "locked_transition_only_ndcg@4": baseline_ndcg,
            "difference": candidate_ndcg - baseline_ndcg,
            "positive": candidate_ndcg > baseline_ndcg,
        }
    return {
        "positive_task_count": sum(bool(value["positive"]) for value in tasks.values()),
        "task_count": len(tasks),
        "tasks": tasks,
    }


def _ndcg4_contrast(cell: Mapping[str, Any], control: str) -> Mapping[str, Any]:
    return cell["paired_bootstrap_contrasts"][control][
        "ndcg@4_correct_minus_control"
    ]


def _gate_and_decision(
    *, selected_target: str, final: Mapping[str, Any], rows_d: list[dict[str, Any]],
    locked_transition_rows_d: list[dict[str, Any]], settings: Mapping[str, Any],
    serialization_passed: bool,
) -> dict[str, Any]:
    if not serialization_passed:
        return {"passed": False, "decision_branch": "raw_nll_teacher_serialization_instability", "checks": {}}
    field = final["models"][selected_target]["field"]["cells"]["D"]
    cross = final["models"][selected_target]["cross"]["cells"]["D"]
    correct = field["controls"]["correct"]["metrics"]
    state_shuffle = field["controls"]["shuffled_state"]["metrics"]
    transition_shuffle = field["controls"]["shuffled_transition"]["metrics"]
    transition_only = final["baselines"]["transition_only"]["D"]
    ndcg = float(correct["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    transition_ndcg = float(transition_only["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    state_gap = ndcg - float(state_shuffle["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    transition_gap = ndcg - float(transition_shuffle["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    ndcg_ci = _ndcg4_contrast(field, "shuffled_transition")
    relative_tasks = _per_task_relative_behavior(
        _load_rows(Path(field["controls"]["correct"]["rows_path"])),
        locked_transition_rows_d,
    )
    positive_tasks = int(relative_tasks["positive_task_count"])
    thresholds = settings["gate"]
    checks = {
        "raw_ndcg4_transition_gain": ndcg - transition_ndcg >= float(thresholds["raw_ndcg4_transition_gain"]),
        "mean_per_state_spearman": float(correct["raw_utility"]["per_state"]["spearman"]["mean"] or 0.0) >= float(thresholds["mean_per_state_spearman"]),
        "interaction_residual_spearman": float(correct["raw_utility"]["interaction_residual_spearman"] or 0.0) >= float(thresholds["interaction_residual_spearman"]),
        "gap_weighted_pairwise_accuracy": float(correct["gap_weighted_pairwise_accuracy"] or 0.0) >= float(thresholds["gap_weighted_pairwise_accuracy"]),
        "transition_shuffle_gap": transition_gap >= float(thresholds["transition_shuffle_ndcg4_drop"]),
        "state_shuffle_gap": state_gap >= float(thresholds["state_shuffle_ndcg4_drop"]),
        "transition_shuffle_bootstrap_ci_excludes_zero": ndcg_ci.get("ci95_low") is not None and float(ndcg_ci["ci95_low"]) > 0.0,
        "positive_heldout_tasks": positive_tasks >= int(thresholds["minimum_positive_heldout_tasks"]),
    }
    field_passed = all(checks.values())
    cross_correct = cross["controls"]["correct"]["metrics"]
    cross_ndcg = float(cross_correct["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    predicted_intent = final["baselines"]["intent_only_predicted"]["D"]["correct"]
    oracle_intent = final["baselines"]["intent_only_oracle"]["D"]["correct"]
    predicted_gain = float(predicted_intent["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0) - transition_ndcg
    oracle_gain = float(oracle_intent["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0) - transition_ndcg
    retention = predicted_gain / oracle_gain if oracle_gain > 0.0 else None
    content_gain = ndcg - float(predicted_intent["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    checks["predicted_intent_oracle_gain_retention"] = retention is not None and retention >= float(thresholds["predicted_intent_oracle_gain_retention"])
    checks["transition_content_adds_value"] = content_gain > 0.0
    cross_state = float(cross["controls"]["shuffled_state"]["metrics"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    cross_transition = float(cross["controls"]["shuffled_transition"]["metrics"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
    cross_relative_tasks = _per_task_relative_behavior(
        _load_rows(Path(cross["controls"]["correct"]["rows_path"])),
        locked_transition_rows_d,
    )
    cross_checks = {
        "raw_ndcg4_transition_gain": cross_ndcg - transition_ndcg >= float(thresholds["raw_ndcg4_transition_gain"]),
        "mean_per_state_spearman": float(cross_correct["raw_utility"]["per_state"]["spearman"]["mean"] or 0.0) >= float(thresholds["mean_per_state_spearman"]),
        "interaction_residual_spearman": float(cross_correct["raw_utility"]["interaction_residual_spearman"] or 0.0) >= float(thresholds["interaction_residual_spearman"]),
        "gap_weighted_pairwise_accuracy": float(cross_correct["gap_weighted_pairwise_accuracy"] or 0.0) >= float(thresholds["gap_weighted_pairwise_accuracy"]),
        "state_shuffle_gap": cross_ndcg - cross_state >= float(thresholds["state_shuffle_ndcg4_drop"]),
        "transition_shuffle_gap": cross_ndcg - cross_transition >= float(thresholds["transition_shuffle_ndcg4_drop"]),
        "transition_shuffle_bootstrap_ci_excludes_zero": float(_ndcg4_contrast(cross, "shuffled_transition")["ci95_low"]) > 0.0,
        "positive_heldout_tasks": int(cross_relative_tasks["positive_task_count"]) >= int(thresholds["minimum_positive_heldout_tasks"]),
    }
    cross_passed = all(cross_checks.values())
    passed = field_passed and checks["predicted_intent_oracle_gain_retention"] and checks["transition_content_adds_value"] and selected_target in {"T6", "T7"}
    if passed:
        branch = "relative_intent_conditioned_memory_use_target_validated"
    elif oracle_gain > 0.05 and (retention is None or retention < float(thresholds["predicted_intent_oracle_gain_retention"])):
        branch = "query_intent_prediction_or_calibration_bottleneck"
    elif predicted_gain > 0.05 and content_gain <= 0.0:
        branch = "coarse_action_intent_explains_memory_use_signal"
    elif cross_passed and not field_passed:
        branch = "revised_target_learnable_but_field_factorization_insufficient"
    else:
        branch = "raw_nll_memory_use_target_not_deployably_predictable"
    return {
        "passed": passed, "field_gate_passed_before_intent_checks": field_passed,
        "checks": checks, "decision_branch": branch,
        "selected_target": selected_target,
        "cross_encoder_gate": {"passed": cross_passed, "checks": cross_checks},
        "per_task_relative_behavior": relative_tasks,
        "cross_per_task_relative_behavior": cross_relative_tasks,
        "metrics": {
            "field_ndcg@4": ndcg, "transition_only_ndcg@4": transition_ndcg,
            "state_shuffle_gap": state_gap, "transition_shuffle_gap": transition_gap,
            "cross_ndcg@4": cross_ndcg, "predicted_intent_gain": predicted_gain,
            "oracle_intent_gain": oracle_gain, "predicted_oracle_gain_retention": retention,
            "content_residual_gain_over_intent": content_gain,
            "positive_heldout_task_count": positive_tasks,
        },
    }


def _report(summary: Mapping[str, Any]) -> str:
    selected = summary["cv_selection"]["selected_primary_revised_target"]
    gate = summary["scientific_gate"]
    lines = [
        "# EXP-021 Relative and Intent-Conditioned Memory-Use Target Audit", "",
        "## VERIFIED", "",
        f"- selected target from A-only grouped CV: `{selected['target']}`",
        f"- selected field epochs: `{selected['epochs']}`",
        f"- serialization gate: `{summary['serialization_gate_passed']}`",
        f"- revised target gate: `{gate['passed']}`",
        f"- decision branch: `{gate['decision_branch']}`", "",
        f"- locked EXP-020 transition-only D NDCG@4: `{float(gate['metrics']['transition_only_ndcg@4']):.6f}`",
        f"- positive heldout tasks versus their own locked baselines: `{int(gate['metrics']['positive_heldout_task_count'])}/9`",
        *(
            [
                "- The prior gate record is preserved and superseded by a record-only locked-comparator repair; no model or checkpoint changed.",
                "",
            ]
            if summary.get("record_repair")
            else [""]
        ),
        "## Cell-D Primary Metrics", "",
        "| Target | Architecture | Raw NDCG@4 | Raw Spearman | Residual Spearman | Gap pair accuracy | State shuffle gap | Transition shuffle gap |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for target, architectures in summary["final_results"]["models"].items():
        for kind, result in architectures.items():
            cell = result["cells"]["D"]
            correct = cell["controls"]["correct"]["metrics"]
            ndcg = float(correct["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
            state = float(cell["controls"]["shuffled_state"]["metrics"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
            transition = float(cell["controls"]["shuffled_transition"]["metrics"]["raw_utility"]["per_state"]["ndcg@4"]["mean"] or 0.0)
            lines.append(
                f"| {target} | {kind} | {ndcg:.6f} | "
                f"{float(correct['raw_utility']['per_state']['spearman']['mean'] or 0.0):.6f} | "
                f"{float(correct['raw_utility']['interaction_residual_spearman'] or 0.0):.6f} | "
                f"{float(correct['gap_weighted_pairwise_accuracy'] or 0.0):.6f} | "
                f"{ndcg - state:.6f} | {ndcg - transition:.6f} |"
            )
    lines.extend([
        "", "The raw EXP-020 target-NLL utility remains the immutable historical comparator. "
        "No behavioral program, injector, selector, Qwen training/backpropagation, AppWorld "
        "evaluation, production field, Stage C2, end-to-end RCMF run, demo change, or V4 tag was performed.", "",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run cached EXP-021 target models")
    parser.add_argument("--config", type=Path, default=Path("configs/benchmark/stage_c_memory_use_target_6e.yaml"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp021")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6e"]
    serialization = _load_json(args.artifact_dir / "serialization_summary.json")
    if not bool(serialization["robustness"]["gate_passed"]):
        raise RuntimeError("Serialization robustness gate failed; model audit must stop")
    exp020 = Path(settings["exp020_artifact"])
    locked_t0_exp020 = _load_json(exp020 / "model_summary.json")["levels"]["LC37"]
    locked_transition_only = _locked_transition_only_baselines(locked_t0_exp020)
    rows = _load_rows(args.artifact_dir / "candidate_target_rows.jsonl")
    locked = _load_json(args.artifact_dir / "locked_raw_utility_decomposition.json")["cell_a_decomposition"]
    for row in rows:
        state_effect = float(locked["state_effects"].get(str(row["state_example_id"]), 0.0))
        transition_effect = float(locked["transition_effects"].get(str(row["transition_id"]), 0.0))
        row["raw_residual_target"] = float(row["text_utility"]) - float(locked["mu"]) - state_effect - transition_effect
    reps = _load_representations(exp020)
    query_ids = {str(row["state_example_id"]) for row in rows}
    predicted_intent, intent_summary = _intent_predictions(
        exp020=exp020, query_ids=query_ids, settings=settings
    )
    action_checkpoint = torch.load(
        exp020 / "action_intent/action_intent_probe.pt", map_location="cpu", weights_only=False
    )
    vocabularies = action_checkpoint["vocabularies"]
    for row in rows:
        row["intent_features_predicted"] = intent_feature_vector(
            predicted_intent[str(row["state_example_id"])], row["transition_signature"]
        )
        row["intent_features_oracle"] = intent_feature_vector(
            _oracle_probabilities(row, vocabularies), row["transition_signature"]
        )
        row["T5_oracle"] = statistics.fmean(row["intent_features_oracle"])
    write_jsonl(args.artifact_dir / "model_input_rows.jsonl", rows)
    atomic_write_json(args.artifact_dir / "intent_probe_calibration.json", intent_summary)
    atomic_write_json(args.artifact_dir / "predicted_query_intent.json", predicted_intent)
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    device = _device(args.device)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="cached_relative_intent_target_models",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head, github_head=args.github_head,
        lambda_head=args.lambda_head, tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        rows_a = [row for row in rows if str(row["cell"]) == "A"]
        intent_model, intent_training = _fit_intent_model(rows_a, settings=settings, device=device)
        intent_path = args.artifact_dir / "models/intent_compatibility.pt"
        atomic_torch_save({
            "format": "intent_compatibility_model_6e_v1",
            "model_state_dict": {key: value.detach().cpu() for key, value in intent_model.state_dict().items()},
            "training": intent_training,
            "train_pair_ids_sha256": sha256_text("\n".join(sorted(str(row["pair_id"]) for row in rows_a))),
        }, intent_path)
        intent_model.to(device).eval()
        cv = _cv_models(
            rows_a=rows_a, reps=reps, intent_model=intent_model,
            predicted_intent=predicted_intent, settings=settings,
            output_root=args.artifact_dir / "models/cv", device=device,
            attempt=attempt,
        )
        atomic_write_json(args.artifact_dir / "a_only_grouped_cv_selection.json", cv)
        final = _final_models(
            rows=rows, rows_a=rows_a, reps=reps, cv=cv,
            intent_model=intent_model, predicted_intent=predicted_intent,
            settings=settings, locked_transition_only=locked_transition_only,
            output_root=args.artifact_dir / "models/final",
            device=device, attempt=attempt,
        )
        selected_target = str(cv["selected_primary_revised_target"]["target"])
        rows_d = [row for row in rows if str(row["cell"]) == "D"]
        gate = _gate_and_decision(
            selected_target=selected_target, final=final, rows_d=rows_d,
            locked_transition_rows_d=_load_rows(
                Path(locked_transition_only["D"]["locked_source"]["rows_path"])
            ),
            settings=settings, serialization_passed=True,
        )
        summary = {
            "format": "memory_use_target_model_audit_6e_v1",
            "status": "completed", "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "serialization_gate_passed": True,
            "intent_probe": intent_summary,
            "intent_model": {"checkpoint": str(intent_path), "checkpoint_sha256": sha256_file(intent_path), "training": intent_training},
            "locked_t0_exp020": locked_t0_exp020,
            "cv_selection": cv,
            "final_results": final,
            "scientific_gate": gate,
            "runtime_seconds": time.perf_counter() - started,
            "actual_h100_hours": (time.perf_counter() - started) / 3600.0,
            "representation_hashes": reps["hashes"],
            "hard_scope": {
                "qwen_forward_calls": 0,
                "qwen_behavioral_backpropagation": False,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "production_field_training": False,
                "appworld_generation_or_evaluation": False,
                "stage_c2": False,
                "end_to_end_rcmf": False,
                "demo_changed": False,
                "query_or_transition_added": False,
                "v4_tag_created_or_moved": False,
            },
        }
        atomic_write_json(args.artifact_dir / "model_audit_summary.json", summary)
        atomic_write_text(args.artifact_dir / "final_target_audit_report.md", _report(summary))
        attempt.progress(
            status="completed", decision_branch=gate["decision_branch"],
            latest_validated_checkpoint=str(args.artifact_dir / "model_audit_summary.json"),
        )
        print(json.dumps({
            "status": "completed", "selected_target": selected_target,
            "decision_branch": gate["decision_branch"], "gate_passed": gate["passed"],
            "summary": str(args.artifact_dir / "model_audit_summary.json"),
        }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
