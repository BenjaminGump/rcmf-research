from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.all_task_interaction_6d import classify_learning_curve
from rcmf.training.cross_encoder_6c import (
    CROSS_ENCODER_MODEL_VERSION,
    CrossEncoderResidualHead,
    exact_training_base_scores,
    feature_normalization,
    normalize_features,
    train_cross_encoder_head,
)
from rcmf.training.datasets import load_decision_examples
from rcmf.training.interaction_representation_6c import (
    fit_two_way_decomposition,
    paired_task_bootstrap_contrast,
    per_task_gate_metrics,
    predict_decomposed_rows,
    summarize_revised_predictions,
    task_grouped_bootstrap,
)
from rcmf.training.multiview_models_6c import StructuredPairFeatureBuilder
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    build_grouped_cv_manifest,
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
from scripts.run_cross_encoder_interaction_6c import (
    _base_score_maps as cross_base_score_maps,
    _cv_select_epochs as cross_cv_select_epochs,
    _prediction_rows as cross_prediction_rows,
)
from scripts.run_interaction_representation_6c import (
    _train_or_load_interaction as current_interaction_checkpoint,
    _train_or_load_main_heads as current_main_checkpoint,
)
from scripts.run_multiview_interaction_6c import (
    _interaction_checkpoint as multiview_interaction_checkpoint,
    _main_checkpoint as multiview_main_checkpoint,
    _normalization as feature_normalization_structured,
    _predict as multiview_predict,
    _single_axis_rows as multiview_single_axis_rows,
)


CELLS = (CELL_B, CELL_C, CELL_D)
CONTROLS = (
    "correct",
    "shuffled_state",
    "shuffled_transition",
    "both_shuffled",
    "mean_state",
    "mean_transition",
    "zero_interaction",
)
MULTIVIEW_KINDS = (
    "multiview_lowrank_tensor",
    "multiview_pair_mlp",
    "structured_feature_interaction",
)
CROSS_KIND = "prompt_only_cross_encoder"
CURRENT_KIND = "decomposed_signed_bilinear"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _seed(base: int, *parts: Any) -> int:
    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def _strip_per_state(value: Mapping[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(dict(value))
    output.pop("per_state_rows", None)
    return output


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["metrics"]["ranking_ks"],
        "neutral_epsilon": float(settings["metrics"]["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
        "huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
    }


def _bootstrap_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return _metric_kwargs(settings)


def _training_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(settings["current_representation"]),
        **dict(settings["multiview"]),
        "losses": dict(settings["current_representation"]["losses"]),
        "residual_huber_delta": float(
            settings["current_representation"]["residual_huber_delta"]
        ),
        "utility_huber_delta": float(
            settings["current_representation"]["utility_huber_delta"]
        ),
        "teacher_temperature": float(
            settings["current_representation"]["teacher_temperature"]
        ),
        "student_temperature": float(
            settings["current_representation"]["student_temperature"]
        ),
        "pair_gap_threshold": float(
            settings["current_representation"]["pair_gap_threshold"]
        ),
        "pair_gap_clip": float(settings["current_representation"]["pair_gap_clip"]),
    }


def _candidate_summary(folds: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {
        "mean_ndcg@4": statistics.fmean(
            float(row["metrics"]["per_state"]["ndcg@4"]["mean"] or 0.0)
            for row in folds
        ),
        "mean_per_state_spearman": statistics.fmean(
            float(row["metrics"]["per_state"]["spearman"]["mean"] or 0.0)
            for row in folds
        ),
        "mean_interaction_residual_spearman": statistics.fmean(
            float(row["metrics"]["interaction_residual_spearman"] or 0.0)
            for row in folds
        ),
        "mean_raw_huber": statistics.fmean(
            float(row["metrics"]["raw_huber"]) for row in folds
        ),
    }


def _selection_key(row: Mapping[str, Any]) -> tuple[float, float, float, float, int]:
    return (
        float(row["mean_ndcg@4"]),
        float(row["mean_interaction_residual_spearman"]),
        float(row["mean_per_state_spearman"]),
        -float(row["mean_raw_huber"]),
        -int(row["epochs"]),
    )


def _load_representations(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Any]:
    exp018 = Path(settings["exp018_artifact"])
    state = torch.load(
        artifact_dir / "representation_cache/query_state_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition = torch.load(
        exp018 / "representation_cache/transition_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    state_multiview = torch.load(
        artifact_dir / "representation_cache/multiview/state_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition_multiview = torch.load(
        artifact_dir / "representation_cache/multiview/transition_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    cross = torch.load(
        artifact_dir / "representation_cache/cross_encoder/cross_encoder_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    state_ids = [str(value) for value in state["ordered_state_example_ids"]]
    transition_ids = [str(value) for value in transition["ordered_transition_ids"]]
    if state_ids != [str(value) for value in state_multiview["ordered_ids"]]:
        raise ValueError("Base and multi-view state orders differ")
    if transition_ids != [str(value) for value in transition_multiview["ordered_ids"]]:
        raise ValueError("Base and multi-view transition orders differ")
    return {
        "state_ids": state_ids,
        "transition_ids": transition_ids,
        "state_position": {value: index for index, value in enumerate(state_ids)},
        "transition_position": {
            value: index for index, value in enumerate(transition_ids)
        },
        "current_state": state["representations"].to(torch.float32),
        "current_transition": transition["representations"].to(torch.float32),
        "state_multiview": {
            key: value.to(torch.float32)
            for key, value in state_multiview["representations"].items()
        },
        "transition_multiview": {
            key: value.to(torch.float32)
            for key, value in transition_multiview["representations"].items()
        },
        "cross_pair_ids": [str(value) for value in cross["ordered_pair_ids"]],
        "cross_features": cross["representations"].to(torch.float32),
        "hashes": {
            "state": sha256_file(
                artifact_dir / "representation_cache/query_state_representations.pt"
            ),
            "transition": sha256_file(
                exp018 / "representation_cache/transition_representations.pt"
            ),
            "state_multiview": sha256_file(
                artifact_dir / "representation_cache/multiview/state_multiview.pt"
            ),
            "transition_multiview": sha256_file(
                artifact_dir / "representation_cache/multiview/transition_multiview.pt"
            ),
            "cross": sha256_file(
                artifact_dir
                / "representation_cache/cross_encoder/cross_encoder_representations.pt"
            ),
        },
    }


def _current_cv(
    *,
    rows: list[dict[str, Any]],
    cv_manifest: Mapping[str, Any],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    output_dir: Path,
    level: str,
    device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    row_by_id = {str(row["pair_id"]): row for row in rows}
    candidates = []
    for epochs in settings["current_representation"]["epoch_candidates"]:
        fold_results = []
        for fold in cv_manifest["folds"]:
            fold_id = int(fold["fold"])
            train_rows = [row_by_id[value] for value in fold["train_pair_ids"]]
            validation_rows = [row_by_id[value] for value in fold["validation_pair_ids"]]
            decomposition = fit_two_way_decomposition(
                train_rows,
                max_iterations=int(
                    settings["decomposition"]["alternating_least_squares_iterations"]
                ),
                tolerance=float(settings["decomposition"]["tolerance"]),
            )
            fold_root = output_dir / f"fold_{fold_id}"
            main_path = fold_root / "current_main.pt"
            metadata = {
                "format": "all_task_current_cv_6d_v1",
                "level": level,
                "fold": fold_id,
                "train_pair_ids_sha256": sha256_text(
                    "\n".join(str(row["pair_id"]) for row in train_rows)
                ),
            }
            main, _, _ = current_main_checkpoint(
                checkpoint=main_path,
                rows=train_rows,
                decomposition=decomposition,
                state_representations=representations["current_state"],
                transition_representations=representations["current_transition"],
                state_position=representations["state_position"],
                transition_position=representations["transition_position"],
                settings=settings["decomposition"],
                seed=_seed(settings["seed"], level, "current-main", fold_id),
                device=device,
                metadata=metadata,
            )
            checkpoint = fold_root / CURRENT_KIND / f"epochs_{epochs}.pt"
            model, training, reused = current_interaction_checkpoint(
                checkpoint=checkpoint,
                kind=CURRENT_KIND,
                rows=train_rows,
                decomposition=decomposition,
                main_effects=main,
                state_representations=representations["current_state"],
                transition_representations=representations["current_transition"],
                state_position=representations["state_position"],
                transition_position=representations["transition_position"],
                settings=settings["current_representation"],
                epochs=int(epochs),
                seed=_seed(settings["seed"], level, CURRENT_KIND, fold_id, epochs),
                device=device,
                metadata=metadata,
            )
            predictions = predict_decomposed_rows(
                model=model,
                rows=validation_rows,
                decomposition=decomposition,
                state_representations=representations["current_state"],
                transition_representations=representations["current_transition"],
                state_position=representations["state_position"],
                transition_position=representations["transition_position"],
                device=device,
                control="correct",
            )
            metrics = _strip_per_state(
                summarize_revised_predictions(predictions, **_metric_kwargs(settings))
            )
            fold_results.append(
                {
                    "fold": fold_id,
                    "metrics": metrics,
                    "checkpoint": str(checkpoint),
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "training": training,
                    "reused": reused,
                }
            )
            attempt.progress(
                status="exp020_current_grouped_cv",
                level=level,
                fold=fold_id,
                epochs=int(epochs),
                latest_validated_checkpoint=str(checkpoint),
            )
        candidates.append(
            {"epochs": int(epochs), "folds": fold_results, **_candidate_summary(fold_results)}
        )
    selected = max(candidates, key=_selection_key)
    return {
        "candidates": candidates,
        "selected_epochs": int(selected["epochs"]),
        "selection_rule": (
            "train-only grouped-CV max NDCG@4, residual Spearman, per-state Spearman, "
            "negative Huber, then fewer epochs"
        ),
    }


def _multiview_cv(
    *,
    kind: str,
    rows: list[dict[str, Any]],
    cv_manifest: Mapping[str, Any],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    feature_builder: StructuredPairFeatureBuilder,
    output_dir: Path,
    level: str,
    device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    row_by_id = {str(row["pair_id"]): row for row in rows}
    training_settings = _training_settings(settings)
    layers = (
        ["final_layer"]
        if kind == "structured_feature_interaction"
        else list(settings["multiview"]["layer_candidates"])
    )
    candidates = []
    for layer in layers:
        for epochs in settings["multiview"]["epoch_candidates"]:
            fold_results = []
            for fold in cv_manifest["folds"]:
                fold_id = int(fold["fold"])
                train_rows = [row_by_id[value] for value in fold["train_pair_ids"]]
                validation_rows = [row_by_id[value] for value in fold["validation_pair_ids"]]
                decomposition = fit_two_way_decomposition(
                    train_rows,
                    max_iterations=int(
                        settings["decomposition"]["alternating_least_squares_iterations"]
                    ),
                    tolerance=float(settings["decomposition"]["tolerance"]),
                )
                fold_root = output_dir / f"fold_{fold_id}" / layer
                metadata = {
                    "format": "all_task_multiview_cv_6d_v1",
                    "level": level,
                    "fold": fold_id,
                    "layer": layer,
                    "train_pair_ids_sha256": sha256_text(
                        "\n".join(str(row["pair_id"]) for row in train_rows)
                    ),
                }
                main, _, _ = multiview_main_checkpoint(
                    path=fold_root / "main.pt",
                    decomposition=decomposition,
                    state_representations=representations["state_multiview"][layer],
                    transition_representations=representations["transition_multiview"][layer],
                    state_position=representations["state_position"],
                    transition_position=representations["transition_position"],
                    settings=training_settings,
                    decomposition_settings=settings["decomposition"],
                    seed=_seed(settings["seed"], level, kind, "main", fold_id, layer),
                    device=device,
                    metadata=metadata,
                )
                pair_features = None
                normalization = None
                feature_dim = None
                if kind == "structured_feature_interaction":
                    pair_features = feature_builder.rows(train_rows)
                    normalization = feature_normalization_structured(pair_features)
                    pair_features = (
                        pair_features - normalization["mean"]
                    ) / normalization["std"]
                    feature_dim = int(pair_features.shape[-1])
                checkpoint = fold_root / kind / f"epochs_{epochs}.pt"
                model, training, reused = multiview_interaction_checkpoint(
                    path=checkpoint,
                    kind=kind,
                    main=main,
                    decomposition=decomposition,
                    rows=train_rows,
                    state_representations=representations["state_multiview"][layer],
                    transition_representations=representations["transition_multiview"][layer],
                    state_position=representations["state_position"],
                    transition_position=representations["transition_position"],
                    settings=training_settings,
                    epochs=int(epochs),
                    seed=_seed(settings["seed"], level, kind, fold_id, layer, epochs),
                    device=device,
                    metadata=metadata,
                    pair_features=pair_features,
                    feature_dim=feature_dim,
                )
                predictions = multiview_predict(
                    model=model,
                    rows=validation_rows,
                    decomposition=decomposition,
                    state_representations=representations["state_multiview"][layer],
                    transition_representations=representations["transition_multiview"][layer],
                    state_position=representations["state_position"],
                    transition_position=representations["transition_position"],
                    device=device,
                    control="correct",
                    seed=_seed(settings["seed"], level, kind, fold_id, "eval"),
                    feature_builder=(
                        feature_builder if kind == "structured_feature_interaction" else None
                    ),
                    feature_normalization=normalization,
                )
                metrics = _strip_per_state(
                    summarize_revised_predictions(predictions, **_metric_kwargs(settings))
                )
                fold_results.append(
                    {
                        "fold": fold_id,
                        "metrics": metrics,
                        "checkpoint": str(checkpoint),
                        "checkpoint_sha256": sha256_file(checkpoint),
                        "training": training,
                        "reused": reused,
                    }
                )
                attempt.progress(
                    status="exp020_multiview_grouped_cv",
                    level=level,
                    model_kind=kind,
                    fold=fold_id,
                    layer=layer,
                    epochs=int(epochs),
                    latest_validated_checkpoint=str(checkpoint),
                )
            candidates.append(
                {
                    "layer": layer,
                    "epochs": int(epochs),
                    "folds": fold_results,
                    **_candidate_summary(fold_results),
                }
            )
    selected = max(candidates, key=_selection_key)
    return {
        "candidates": candidates,
        "selected_layer": str(selected["layer"]),
        "selected_epochs": int(selected["epochs"]),
        "selection_rule": (
            "train-only grouped-CV max NDCG@4, residual Spearman, per-state Spearman, "
            "negative Huber, then fewer epochs"
        ),
    }


def _evaluate_predictions(
    *,
    predictions: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    bootstrap_seed: int,
) -> dict[str, Any]:
    return {
        "metrics": _strip_per_state(
            summarize_revised_predictions(predictions, **_metric_kwargs(settings))
        ),
        "task_grouped_bootstrap_ci95": task_grouped_bootstrap(
            predictions,
            samples=int(settings["metrics"]["bootstrap_samples"]),
            seed=int(bootstrap_seed),
            metric_settings=_bootstrap_settings(settings),
        ),
    }


def _save_prediction(
    path: Path, predictions: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    write_jsonl(path, predictions)
    return {"rows_path": str(path), "rows_sha256": sha256_file(path)}


def _run_level(
    *,
    level: Mapping[str, Any],
    all_rows: list[dict[str, Any]],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    feature_builder: StructuredPairFeatureBuilder,
    output_root: Path,
    device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    name = str(level["name"])
    task_ids = {str(value) for value in level["task_ids"]}
    rows_a = [
        row
        for row in all_rows
        if row["cell"] == CELL_A and str(row["state_task_id"]) in task_ids
    ]
    cells = {
        CELL_B: [row for row in all_rows if row["cell"] == CELL_B],
        CELL_C: [
            row
            for row in all_rows
            if row["cell"] == CELL_C and str(row["state_task_id"]) in task_ids
        ],
        CELL_D: [row for row in all_rows if row["cell"] == CELL_D],
    }
    cv_manifest = build_grouped_cv_manifest(
        rows_a,
        folds=int(settings["learning_curves"]["grouped_cv_folds"]),
        seed=_seed(settings["learning_curve_seed"], name, "grouped-cv"),
    )
    level_root = output_root / name.lower()
    atomic_write_json(level_root / "grouped_cv_manifest.json", cv_manifest)
    selections = {
        CURRENT_KIND: _current_cv(
            rows=rows_a,
            cv_manifest=cv_manifest,
            representations=representations,
            settings=settings,
            output_dir=level_root / "cv/current",
            level=name,
            device=device,
            attempt=attempt,
        )
    }
    for kind in MULTIVIEW_KINDS:
        selections[kind] = _multiview_cv(
            kind=kind,
            rows=rows_a,
            cv_manifest=cv_manifest,
            representations=representations,
            settings=settings,
            feature_builder=feature_builder,
            output_dir=level_root / "cv/multiview" / kind,
            level=name,
            device=device,
            attempt=attempt,
        )
    cross_features = {
        pair_id: representations["cross_features"][index]
        for index, pair_id in enumerate(representations["cross_pair_ids"])
    }
    cross_main_metadata = {
        "state_values": representations["state_multiview"]["final_layer"],
        "transition_values": representations["transition_multiview"]["final_layer"],
        "state_ids": representations["state_ids"],
        "transition_ids": representations["transition_ids"],
        "state_position": representations["state_position"],
        "transition_position": representations["transition_position"],
    }
    selected_cross, cross_cv_rows = cross_cv_select_epochs(
        rows_a=rows_a,
        cv_manifest=cv_manifest,
        feature_by_pair=cross_features,
        main_metadata=cross_main_metadata,
        settings=settings,
        output_dir=level_root / "cv/cross_encoder",
        device=device,
        attempt=attempt,
    )
    selections[CROSS_KIND] = selected_cross
    atomic_write_json(level_root / "cv/cross_encoder_rows.json", cross_cv_rows)
    atomic_write_json(level_root / "selected_configurations.json", selections)
    decomposition = fit_two_way_decomposition(
        rows_a,
        max_iterations=int(settings["decomposition"]["alternating_least_squares_iterations"]),
        tolerance=float(settings["decomposition"]["tolerance"]),
    )
    metadata = {
        "format": "all_task_final_model_6d_v1",
        "level": name,
        "train_pair_ids_sha256": sha256_text(
            "\n".join(str(row["pair_id"]) for row in rows_a)
        ),
    }
    current_main, _, _ = current_main_checkpoint(
        checkpoint=level_root / "models/current_main.pt",
        rows=rows_a,
        decomposition=decomposition,
        state_representations=representations["current_state"],
        transition_representations=representations["current_transition"],
        state_position=representations["state_position"],
        transition_position=representations["transition_position"],
        settings=settings["decomposition"],
        seed=_seed(settings["seed"], name, "current-main-final"),
        device=device,
        metadata=metadata,
    )
    current_model, current_training, _ = current_interaction_checkpoint(
        checkpoint=level_root / f"models/{CURRENT_KIND}.pt",
        kind=CURRENT_KIND,
        rows=rows_a,
        decomposition=decomposition,
        main_effects=current_main,
        state_representations=representations["current_state"],
        transition_representations=representations["current_transition"],
        state_position=representations["state_position"],
        transition_position=representations["transition_position"],
        settings=settings["current_representation"],
        epochs=int(selections[CURRENT_KIND]["selected_epochs"]),
        seed=_seed(settings["seed"], name, CURRENT_KIND, "final"),
        device=device,
        metadata=metadata,
    )
    training_settings = _training_settings(settings)
    main_by_layer: dict[str, Any] = {}
    multiview_models: dict[str, Any] = {}
    multiview_training: dict[str, Any] = {}
    structured_stats = None
    for kind in MULTIVIEW_KINDS:
        layer = str(selections[kind]["selected_layer"])
        if layer not in main_by_layer:
            main_by_layer[layer] = multiview_main_checkpoint(
                path=level_root / f"models/multiview_main_{layer}.pt",
                decomposition=decomposition,
                state_representations=representations["state_multiview"][layer],
                transition_representations=representations["transition_multiview"][layer],
                state_position=representations["state_position"],
                transition_position=representations["transition_position"],
                settings=training_settings,
                decomposition_settings=settings["decomposition"],
                seed=_seed(settings["seed"], name, "multiview-main-final", layer),
                device=device,
                metadata={**metadata, "layer": layer},
            )[0]
        pair_features = None
        feature_dim = None
        if kind == "structured_feature_interaction":
            pair_features = feature_builder.rows(rows_a)
            structured_stats = feature_normalization_structured(pair_features)
            pair_features = (
                pair_features - structured_stats["mean"]
            ) / structured_stats["std"]
            feature_dim = int(pair_features.shape[-1])
        model, training, _ = multiview_interaction_checkpoint(
            path=level_root / f"models/{kind}.pt",
            kind=kind,
            main=main_by_layer[layer],
            decomposition=decomposition,
            rows=rows_a,
            state_representations=representations["state_multiview"][layer],
            transition_representations=representations["transition_multiview"][layer],
            state_position=representations["state_position"],
            transition_position=representations["transition_position"],
            settings=training_settings,
            epochs=int(selections[kind]["selected_epochs"]),
            seed=_seed(settings["seed"], name, kind, "final"),
            device=device,
            metadata={**metadata, "layer": layer},
            pair_features=pair_features,
            feature_dim=feature_dim,
        )
        multiview_models[kind] = model
        multiview_training[kind] = training
    final_layer_main = main_by_layer.get("final_layer")
    if final_layer_main is None:
        final_layer_main = multiview_main_checkpoint(
            path=level_root / "models/multiview_main_final_layer.pt",
            decomposition=decomposition,
            state_representations=representations["state_multiview"]["final_layer"],
            transition_representations=representations["transition_multiview"]["final_layer"],
            state_position=representations["state_position"],
            transition_position=representations["transition_position"],
            settings=training_settings,
            decomposition_settings=settings["decomposition"],
            seed=_seed(settings["seed"], name, "multiview-main-final", "final_layer"),
            device=device,
            metadata={**metadata, "layer": "final_layer"},
        )[0]
    with torch.no_grad():
        state_main_map = dict(
            zip(
                representations["state_ids"],
                (
                    float(value)
                    for value in final_layer_main.state(
                        representations["state_multiview"]["final_layer"].to(device)
                    )
                    .cpu()
                    .tolist()
                ),
            )
        )
        transition_main_map = dict(
            zip(
                representations["transition_ids"],
                (
                    float(value)
                    for value in final_layer_main.transition(
                        representations["transition_multiview"]["final_layer"].to(device)
                    )
                    .cpu()
                    .tolist()
                ),
            )
        )
    base_scores, _, _ = cross_base_score_maps(
        rows=[*rows_a, *cells[CELL_B], *cells[CELL_C], *cells[CELL_D]],
        decomposition=decomposition,
        state_main=state_main_map,
        transition_main=transition_main_map,
    )
    train_cross = torch.stack([cross_features[str(row["pair_id"])] for row in rows_a])
    cross_stats = feature_normalization(train_cross)
    cross_model = CrossEncoderResidualHead(
        int(train_cross.shape[-1]),
        hidden_dim=int(settings["cross_encoder"]["scalar_head_hidden_dim"]),
        dropout=float(settings["multiview"]["dropout"]),
    )
    cross_path = level_root / "models/prompt_only_cross_encoder.pt"
    cross_metadata = {
        **metadata,
        "format": f"{CROSS_ENCODER_MODEL_VERSION}_all_task_final_6d",
        "epochs": int(selections[CROSS_KIND]["epochs"]),
    }
    if cross_path.exists():
        payload = torch.load(cross_path, map_location="cpu", weights_only=False)
        if payload["metadata"] != cross_metadata:
            raise ValueError(f"Existing final cross checkpoint differs: {cross_path}")
        cross_model.load_state_dict(payload["model_state_dict"])
        cross_stats = payload["normalization"]
        cross_training = payload["training"]
    else:
        cross_training = train_cross_encoder_head(
            model=cross_model,
            rows=rows_a,
            features=normalize_features(train_cross, cross_stats),
            base_scores=exact_training_base_scores(rows_a, decomposition),
            decomposition=decomposition,
            settings=settings["current_representation"],
            epochs=int(selections[CROSS_KIND]["epochs"]),
            seed=_seed(settings["seed"], name, CROSS_KIND, "final"),
            device=device,
        )
        optimizer_state = cross_training.pop("optimizer_state_dict")
        atomic_torch_save(
            {
                "metadata": cross_metadata,
                "model_state_dict": {
                    key: value.detach().cpu() for key, value in cross_model.state_dict().items()
                },
                "optimizer_state_dict": optimizer_state,
                "normalization": {
                    key: value.detach().cpu() for key, value in cross_stats.items()
                },
                "training": cross_training,
            },
            cross_path,
        )
    cross_model.to(device).eval()
    prediction_root = level_root / "predictions"
    results: dict[str, Any] = {
        "level": name,
        "train_task_count": len(task_ids),
        "train_state_count": int(level["state_count"]),
        "train_pair_count": len(rows_a),
        "cell_counts": {cell: len(rows) for cell, rows in cells.items()},
        "cv_manifest": cv_manifest,
        "selected_configurations": selections,
        "models": {},
    }
    baseline_predictions: dict[str, dict[str, list[dict[str, Any]]]] = {
        "state_only": {},
        "transition_only": {},
    }
    for cell, evaluation_rows in cells.items():
        for axis in ("state", "transition"):
            rows = multiview_single_axis_rows(
                main=final_layer_main,
                mu=float(decomposition["mu"]),
                rows=evaluation_rows,
                decomposition=decomposition,
                state_representations=representations["state_multiview"]["final_layer"],
                transition_representations=representations["transition_multiview"]["final_layer"],
                state_position=representations["state_position"],
                transition_position=representations["transition_position"],
                device=device,
                axis=axis,
            )
            baseline_predictions[f"{axis}_only"][cell] = rows
            path = prediction_root / f"{axis}_only/{cell}.jsonl"
            details = _save_prediction(path, rows)
            results["models"].setdefault(f"{axis}_only", {"cells": {}})["cells"][cell] = {
                "controls": {
                    "correct": {
                        **details,
                        **_evaluate_predictions(
                            predictions=rows,
                            settings=settings,
                            bootstrap_seed=_seed(
                                settings["metrics"]["bootstrap_seed"],
                                name,
                                axis,
                                cell,
                            ),
                        ),
                    }
                }
            }
    model_specs = {
        CURRENT_KIND: (current_model, current_training),
        **{
            kind: (multiview_models[kind], multiview_training[kind])
            for kind in MULTIVIEW_KINDS
        },
        CROSS_KIND: (cross_model, cross_training),
    }
    control_rows_by_model: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    for kind, (model, training) in model_specs.items():
        model_result = {
            "training": training,
            "cells": {},
        }
        control_rows_by_model[kind] = {}
        for cell, evaluation_rows in cells.items():
            control_rows_by_model[kind][cell] = {}
            control_results = {}
            for control_index, control in enumerate(CONTROLS):
                if kind == CURRENT_KIND:
                    predicted = predict_decomposed_rows(
                        model=model,
                        rows=evaluation_rows,
                        decomposition=decomposition,
                        state_representations=representations["current_state"],
                        transition_representations=representations["current_transition"],
                        state_position=representations["state_position"],
                        transition_position=representations["transition_position"],
                        device=device,
                        control=control,
                        seed=_seed(settings["seed"], name, kind, cell, control),
                    )
                elif kind in MULTIVIEW_KINDS:
                    layer = str(selections[kind]["selected_layer"])
                    predicted = multiview_predict(
                        model=model,
                        rows=evaluation_rows,
                        decomposition=decomposition,
                        state_representations=representations["state_multiview"][layer],
                        transition_representations=representations["transition_multiview"][layer],
                        state_position=representations["state_position"],
                        transition_position=representations["transition_position"],
                        device=device,
                        control=control,
                        seed=_seed(settings["seed"], name, kind, cell, control),
                        feature_builder=(
                            feature_builder
                            if kind == "structured_feature_interaction"
                            else None
                        ),
                        feature_normalization=(
                            structured_stats
                            if kind == "structured_feature_interaction"
                            else None
                        ),
                    )
                else:
                    predicted = cross_prediction_rows(
                        model=model,
                        rows=evaluation_rows,
                        feature_by_pair=cross_features,
                        normalization=cross_stats,
                        base_scores=base_scores,
                        control=control,
                        seed=_seed(settings["seed"], name, kind, cell, control),
                        device=device,
                    )
                control_rows_by_model[kind][cell][control] = predicted
                path = prediction_root / kind / cell / f"{control}.jsonl"
                details = _save_prediction(path, predicted)
                control_results[control] = {
                    **details,
                    **_evaluate_predictions(
                        predictions=predicted,
                        settings=settings,
                        bootstrap_seed=_seed(
                            settings["metrics"]["bootstrap_seed"],
                            name,
                            kind,
                            cell,
                            control_index,
                        ),
                    ),
                }
            contrasts = {
                control: paired_task_bootstrap_contrast(
                    control_rows_by_model[kind][cell]["correct"],
                    control_rows_by_model[kind][cell][control],
                    samples=int(settings["metrics"]["bootstrap_samples"]),
                    seed=_seed(
                        settings["metrics"]["bootstrap_seed"],
                        name,
                        kind,
                        cell,
                        control,
                    ),
                    metric_settings=_bootstrap_settings(settings),
                )
                for control in (
                    "shuffled_state",
                    "shuffled_transition",
                    "both_shuffled",
                )
            }
            model_result["cells"][cell] = {
                "controls": control_results,
                "paired_bootstrap_contrasts": contrasts,
            }
            attempt.progress(
                status="exp020_evaluating_models",
                level=name,
                model_kind=kind,
                cell=cell,
                latest_validated_checkpoint=str(
                    prediction_root / kind / cell / "correct.jsonl"
                ),
            )
        d_rows = control_rows_by_model[kind][CELL_D]
        model_result["per_heldout_task"] = per_task_gate_metrics(
            correct_rows=d_rows["correct"],
            state_only_rows=baseline_predictions["state_only"][CELL_D],
            transition_only_rows=baseline_predictions["transition_only"][CELL_D],
            shuffled_state_rows=d_rows["shuffled_state"],
            shuffled_transition_rows=d_rows["shuffled_transition"],
            metric_settings=_bootstrap_settings(settings),
        )
        results["models"][kind] = model_result
        atomic_write_json(level_root / "model_results_progress.json", results)
    atomic_write_json(level_root / "model_results.json", results)
    return results


def _model_metric(
    level: Mapping[str, Any], model: str, cell: str, control: str, path: Sequence[str]
) -> float:
    value: Any = level["models"][model]["cells"][cell]["controls"][control]["metrics"]
    for key in path:
        value = value[key]
    return float(value or 0.0)


def _gate_summary(
    levels: Mapping[str, Mapping[str, Any]], settings: Mapping[str, Any]
) -> dict[str, Any]:
    full = levels["LC37"]
    thresholds = settings["expanded_gate"]
    transition_ndcg = _model_metric(
        full, "transition_only", CELL_D, "correct", ("per_state", "ndcg@4", "mean")
    )
    cross_ndcg = _model_metric(
        full, CROSS_KIND, CELL_D, "correct", ("per_state", "ndcg@4", "mean")
    )
    field_ndcg = _model_metric(
        full,
        "multiview_lowrank_tensor",
        CELL_D,
        "correct",
        ("per_state", "ndcg@4", "mean"),
    )
    cross_state_shuffle = _model_metric(
        full,
        CROSS_KIND,
        CELL_D,
        "shuffled_state",
        ("per_state", "ndcg@4", "mean"),
    )
    cross_transition_shuffle = _model_metric(
        full,
        CROSS_KIND,
        CELL_D,
        "shuffled_transition",
        ("per_state", "ndcg@4", "mean"),
    )
    field_state_shuffle = _model_metric(
        full,
        "multiview_lowrank_tensor",
        CELL_D,
        "shuffled_state",
        ("per_state", "ndcg@4", "mean"),
    )
    field_transition_shuffle = _model_metric(
        full,
        "multiview_lowrank_tensor",
        CELL_D,
        "shuffled_transition",
        ("per_state", "ndcg@4", "mean"),
    )
    cross_tasks = full["models"][CROSS_KIND]["per_heldout_task"]
    field_tasks = full["models"]["multiview_lowrank_tensor"]["per_heldout_task"]
    cross_positive_tasks = sum(
        row["correct_ndcg@4"] > row["transition_only_ndcg@4"]
        for row in cross_tasks.values()
    )
    field_positive_tasks = sum(
        row["correct_ndcg@4"] > row["transition_only_ndcg@4"]
        for row in field_tasks.values()
    )
    cross_ci = full["models"][CROSS_KIND]["cells"][CELL_D][
        "paired_bootstrap_contrasts"
    ]["shuffled_transition"]["ndcg@4_correct_minus_control"]
    cross_checks = {
        "ndcg4_gain_over_transition": cross_ndcg - transition_ndcg
        >= float(thresholds["cross_encoder_ndcg4_transition_gain"]),
        "mean_per_state_spearman": _model_metric(
            full, CROSS_KIND, CELL_D, "correct", ("per_state", "spearman", "mean")
        )
        >= float(thresholds["mean_per_state_spearman"]),
        "interaction_residual_spearman": _model_metric(
            full, CROSS_KIND, CELL_D, "correct", ("interaction_residual_spearman",)
        )
        >= float(thresholds["interaction_residual_spearman"]),
        "transition_shuffle_drop": cross_ndcg - cross_transition_shuffle
        >= float(thresholds["transition_shuffle_ndcg4_drop"]),
        "state_shuffle_drop": cross_ndcg - cross_state_shuffle
        >= float(thresholds["state_shuffle_ndcg4_drop"]),
        "transition_shuffle_ci_excludes_zero": float(cross_ci["ci95_low"]) > 0.0,
        "positive_heldout_tasks": cross_positive_tasks
        >= int(thresholds["minimum_positive_heldout_tasks"]),
    }
    cross_gain = cross_ndcg - transition_ndcg
    field_gain = field_ndcg - transition_ndcg
    field_checks = {
        "cross_encoder_passed": all(cross_checks.values()),
        "gain_retention": field_gain >= float(thresholds["field_gain_retention"]) * cross_gain,
        "mean_per_state_spearman": _model_metric(
            full,
            "multiview_lowrank_tensor",
            CELL_D,
            "correct",
            ("per_state", "spearman", "mean"),
        )
        >= float(thresholds["mean_per_state_spearman"]),
        "interaction_residual_spearman": _model_metric(
            full,
            "multiview_lowrank_tensor",
            CELL_D,
            "correct",
            ("interaction_residual_spearman",),
        )
        >= float(thresholds["interaction_residual_spearman"]),
        "state_shuffle_drop": field_ndcg - field_state_shuffle
        >= float(thresholds["state_shuffle_ndcg4_drop"]),
        "transition_shuffle_drop": field_ndcg - field_transition_shuffle
        >= float(thresholds["transition_shuffle_ndcg4_drop"]),
        "positive_heldout_tasks": field_positive_tasks
        >= int(thresholds["minimum_positive_heldout_tasks"]),
    }
    cross_curve_values = [
        _model_metric(
            levels[name],
            CROSS_KIND,
            CELL_D,
            "correct",
            ("per_state", "ndcg@4", "mean"),
        )
        for name in ("LC12", "LC24", "LC37")
    ]
    curve_class = classify_learning_curve(
        cross_curve_values,
        material_gain=float(settings["learning_curves"]["material_ndcg_gain"]),
        instability=float(settings["learning_curves"]["instability_threshold"]),
    )
    cross_passed = all(cross_checks.values())
    field_passed = all(field_checks.values())
    if cross_passed and field_passed:
        branch = "all_task_query_coverage_representation_gate_passed"
    elif cross_passed:
        branch = "field_compatible_factorization_bottleneck"
    elif curve_class == "materially_increasing":
        branch = "query_task_coverage_still_data_limited"
    else:
        branch = "prompt_only_transition_utility_not_generalizing"
    return {
        "cross_encoder": {
            "passed": cross_passed,
            "checks": cross_checks,
            "ndcg@4": cross_ndcg,
            "transition_only_ndcg@4": transition_ndcg,
            "gain": cross_gain,
            "state_shuffle_drop": cross_ndcg - cross_state_shuffle,
            "transition_shuffle_drop": cross_ndcg - cross_transition_shuffle,
            "positive_heldout_tasks": cross_positive_tasks,
            "primary_transition_shuffle_ci": cross_ci,
        },
        "field_compatible": {
            "passed": field_passed,
            "checks": field_checks,
            "ndcg@4": field_ndcg,
            "transition_only_ndcg@4": transition_ndcg,
            "gain": field_gain,
            "cross_gain_retained_fraction": (
                field_gain / cross_gain if abs(cross_gain) > 1.0e-12 else None
            ),
            "state_shuffle_drop": field_ndcg - field_state_shuffle,
            "transition_shuffle_drop": field_ndcg - field_transition_shuffle,
            "positive_heldout_tasks": field_positive_tasks,
        },
        "cross_encoder_learning_curve": {
            "levels": dict(zip(("LC12", "LC24", "LC37"), cross_curve_values)),
            "classification": curve_class,
        },
        "decision_branch": branch,
        "representation_gate_passed": branch
        == "all_task_query_coverage_representation_gate_passed",
        "behavioral_program_remains_blocked": True,
    }


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-020 All-Task Interaction Results",
        "",
        f"- decision: `{summary['gate']['decision_branch']}`",
        f"- representation gate passed: `{summary['gate']['representation_gate_passed']}`",
        f"- behavioral p(s,m) blocked: `{summary['gate']['behavioral_program_remains_blocked']}`",
        "",
        "| LC | Model | D NDCG@4 | D per-state Spearman | D residual Spearman | State shuffle drop | Transition shuffle drop |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for level_name in ("LC12", "LC24", "LC37"):
        level = summary["levels"][level_name]
        for model in (
            "transition_only",
            CURRENT_KIND,
            "multiview_lowrank_tensor",
            "multiview_pair_mlp",
            "structured_feature_interaction",
            CROSS_KIND,
        ):
            correct = level["models"][model]["cells"][CELL_D]["controls"]["correct"][
                "metrics"
            ]
            if model in {"transition_only", "state_only"}:
                state_drop = 0.0
                transition_drop = 0.0
            else:
                shuffled_state = level["models"][model]["cells"][CELL_D]["controls"][
                    "shuffled_state"
                ]["metrics"]
                shuffled_transition = level["models"][model]["cells"][CELL_D][
                    "controls"
                ]["shuffled_transition"]["metrics"]
                state_drop = float(correct["per_state"]["ndcg@4"]["mean"] or 0.0) - float(
                    shuffled_state["per_state"]["ndcg@4"]["mean"] or 0.0
                )
                transition_drop = float(
                    correct["per_state"]["ndcg@4"]["mean"] or 0.0
                ) - float(shuffled_transition["per_state"]["ndcg@4"]["mean"] or 0.0)
            lines.append(
                f"| {level_name} | {model} | "
                f"{float(correct['per_state']['ndcg@4']['mean'] or 0):.6f} | "
                f"{float(correct['per_state']['spearman']['mean'] or 0):.6f} | "
                f"{float(correct['interaction_residual_spearman'] or 0):.6f} | "
                f"{state_drop:.6f} | {transition_drop:.6f} |"
            )
    lines.extend(
        [
            "",
            "No behavioral program, injector, selector, Qwen gradient, AppWorld generation, "
            "Stage C2, end-to-end training, demo change, or V4 tag occurred.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EXP-020 interaction models")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp020")
    parser.add_argument(
        "--device", default="cuda" if torch.cuda.is_available() else "cpu"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    representation_summary = _load_json(args.artifact_dir / "representation_summary.json")
    if representation_summary["status"] != "completed":
        raise ValueError("Expanded representations are not complete")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    existing_attempt_ids = {
        str(row["attempt_id"])
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    }
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="all_task_interaction_models_and_learning_curves",
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
        all_rows = _load_rows(args.artifact_dir / "two_axis_pair_rows.jsonl")
        learning_manifest = _load_json(args.artifact_dir / "learning_curve_manifest.json")
        representations = _load_representations(settings, args.artifact_dir)
        source_data = Path(settings["source_data"])
        exp017 = Path(settings["exp017_artifact"])
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        query_manifest = _load_json(args.artifact_dir / "expanded_query_manifest.json")
        query_by_id = {
            str(row["state_example_id"]): row for row in query_manifest["query_rows"]
        }
        state_examples = {
            state_id: examples[int(row["example_index"])]
            for state_id, row in query_by_id.items()
        }
        transitions = {
            str(row["transition_id"]): row
            for row in _load_rows(exp017 / "transition_panel.jsonl")
        }
        feature_builder = StructuredPairFeatureBuilder(
            state_examples=state_examples,
            state_metadata=query_by_id,
            transitions=transitions,
        )
        output_root = args.artifact_dir / "models"
        levels = {}
        device = torch.device(args.device)
        for level in learning_manifest["levels"]:
            result = _run_level(
                level=level,
                all_rows=all_rows,
                representations=representations,
                settings=settings,
                feature_builder=feature_builder,
                output_root=output_root,
                device=device,
                attempt=attempt,
            )
            levels[str(level["name"])] = result
            atomic_write_json(args.artifact_dir / "model_results_progress.json", levels)
        gate = _gate_summary(levels, settings)
        runtime_seconds = time.perf_counter() - started
        summary = {
            "format": "all_task_interaction_model_summary_6d_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "representation_hashes": representations["hashes"],
            "levels": levels,
            "gate": gate,
            "runtime_seconds": runtime_seconds,
            "actual_h100_hours": runtime_seconds / 3600.0,
            "hard_scope": {
                "qwen_forward_calls": 0,
                "qwen_behavioral_backpropagation": False,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "appworld_generation_or_evaluation": False,
                "stage_c2": False,
                "end_to_end_rcmf": False,
                "demo_changed": False,
                "v4_tag_created_or_moved": False,
            },
            "timestamp_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "model_summary.json", summary)
        atomic_write_text(args.artifact_dir / "model_report.md", _report(summary))
        attempt.progress(
            status="exp020_interaction_models_completed",
            decision_branch=gate["decision_branch"],
            latest_validated_checkpoint=str(args.artifact_dir / "model_summary.json"),
        )
        print(json.dumps(gate, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
