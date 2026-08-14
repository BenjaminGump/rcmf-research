from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_encoder_6c import (
    CrossEncoderResidualHead,
    exact_training_base_scores,
    feature_normalization,
    normalize_features,
    train_cross_encoder_head,
)
from rcmf.training.data_sufficiency_6c import (
    build_nested_task_learning_curve_manifest,
    expanded_query_cache_projection,
    summarize_learning_curves,
)
from rcmf.training.datasets import load_decision_examples
from rcmf.training.interaction_representation_6c import (
    fit_two_way_decomposition,
    predict_decomposed_rows,
    summarize_revised_predictions,
)
from rcmf.training.multiview_models_6c import StructuredPairFeatureBuilder
from rcmf.training.multiview_representations_6c import (
    POOLING_RULES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
)
from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_D,
    AttemptLedger,
    utc_now,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.run_cross_encoder_interaction_6c import (
    _base_score_maps as cross_base_score_maps,
    _prediction_rows as cross_prediction_rows,
    multiview_artifact_paths,
)
from scripts.run_interaction_representation_6c import (
    _train_or_load_interaction as current_interaction_checkpoint,
    _train_or_load_main_heads as current_main_checkpoint,
)
from scripts.run_multiview_interaction_6c import (
    _interaction_checkpoint as multiview_interaction_checkpoint,
    _main_checkpoint as multiview_main_checkpoint,
    _normalization as feature_stats,
    _predict as multiview_predict,
)


CURRENT_MODELS = (
    "decomposed_signed_bilinear",
    "decomposed_concat_interaction",
)
MULTIVIEW_MODELS = (
    "multiview_signed_bilinear",
    "multiview_lowrank_tensor",
    "multiview_pair_mlp",
    "structured_feature_interaction",
)
CROSS_MODEL = "prompt_only_cross_encoder"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["metrics"]["ranking_ks"],
        "neutral_epsilon": float(settings["metrics"]["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["metrics"]["best_tie_tolerance"]),
        "huber_delta": float(settings["current_representation"]["utility_huber_delta"]),
    }


def _seed(base: int, *parts: Any) -> int:
    import hashlib

    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def _summary_row(
    *,
    model_kind: str,
    fold: int,
    task_count: int,
    pair_count: int,
    metrics: Mapping[str, Any],
    checkpoint: Path,
    training: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "model_kind": model_kind,
        "fold": int(fold),
        "task_count": int(task_count),
        "train_pair_count": int(pair_count),
        "ndcg@4": float(metrics["per_state"]["ndcg@4"]["mean"] or 0.0),
        "interaction_residual_spearman": float(
            metrics["interaction_residual_spearman"] or 0.0
        ),
        "pooled_raw_spearman": float(metrics["pooled_raw_spearman"] or 0.0),
        "mean_per_state_spearman": float(
            metrics["per_state"]["spearman"]["mean"] or 0.0
        ),
        "raw_huber": float(metrics["raw_huber"]),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha256_file(checkpoint),
        "training_final": training["history"][-1] if training.get("history") else None,
    }


def _load_representations(exp018: Path, artifact_dir: Path) -> dict[str, Any]:
    multiview_paths = multiview_artifact_paths(artifact_dir)
    current_state = torch.load(
        exp018 / "representation_cache/query_state_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    current_transition = torch.load(
        exp018 / "representation_cache/transition_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    state_multiview = torch.load(
        multiview_paths["state_cache"],
        map_location="cpu",
        weights_only=False,
    )
    transition_multiview = torch.load(
        multiview_paths["transition_cache"],
        map_location="cpu",
        weights_only=False,
    )
    cross = torch.load(
        artifact_dir / "part_e/cross_encoder_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    return {
        "current_state": current_state["representations"].to(torch.float32),
        "current_transition": current_transition["representations"].to(torch.float32),
        "state_ids": [
            str(value) for value in current_state["ordered_state_example_ids"]
        ],
        "transition_ids": [
            str(value) for value in current_transition["ordered_transition_ids"]
        ],
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
            "current_state": sha256_file(
                exp018 / "representation_cache/query_state_representations.pt"
            ),
            "current_transition": sha256_file(
                exp018 / "representation_cache/transition_representations.pt"
            ),
            "state_multiview": sha256_file(multiview_paths["state_cache"]),
            "transition_multiview": sha256_file(multiview_paths["transition_cache"]),
            "cross_encoder": sha256_file(
                artifact_dir / "part_e/cross_encoder_representations.pt"
            ),
        },
    }


def _run_current_models(
    *,
    train_rows: Sequence[Mapping[str, Any]],
    evaluation_rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    selected_epochs: Mapping[str, int],
    output_dir: Path,
    fold: int,
    task_count: int,
    device: torch.device,
) -> list[dict[str, Any]]:
    state_position = {
        value: index for index, value in enumerate(representations["state_ids"])
    }
    transition_position = {
        value: index for index, value in enumerate(representations["transition_ids"])
    }
    main_path = output_dir / "current/main.pt"
    main, _, _ = current_main_checkpoint(
        checkpoint=main_path,
        rows=train_rows,
        decomposition=decomposition,
        state_representations=representations["current_state"],
        transition_representations=representations["current_transition"],
        state_position=state_position,
        transition_position=transition_position,
        settings=settings["decomposition"],
        seed=_seed(
            int(settings["learning_curves"]["seed"]), fold, task_count, "current-main"
        ),
        device=device,
        metadata={
            "format": "learning_curve_current_main_6c_v1",
            "fold": fold,
            "task_count": task_count,
        },
    )
    output = []
    for kind in CURRENT_MODELS:
        checkpoint = output_dir / "current" / f"{kind}.pt"
        model, training, _ = current_interaction_checkpoint(
            checkpoint=checkpoint,
            kind=kind,
            rows=train_rows,
            decomposition=decomposition,
            main_effects=main,
            state_representations=representations["current_state"],
            transition_representations=representations["current_transition"],
            state_position=state_position,
            transition_position=transition_position,
            settings=settings["current_representation"],
            epochs=int(selected_epochs[kind]),
            seed=_seed(
                int(settings["learning_curves"]["seed"]), fold, task_count, kind
            ),
            device=device,
            metadata={
                "format": "learning_curve_current_interaction_6c_v1",
                "fold": fold,
                "task_count": task_count,
            },
        )
        predictions = predict_decomposed_rows(
            model=model,
            rows=evaluation_rows,
            decomposition=decomposition,
            state_representations=representations["current_state"],
            transition_representations=representations["current_transition"],
            state_position=state_position,
            transition_position=transition_position,
            device=device,
            control="correct",
        )
        prediction_path = output_dir / "predictions" / f"{kind}.jsonl"
        write_jsonl(prediction_path, predictions)
        metrics = summarize_revised_predictions(predictions, **_metric_kwargs(settings))
        output.append(
            _summary_row(
                model_kind=kind,
                fold=fold,
                task_count=task_count,
                pair_count=len(train_rows),
                metrics=metrics,
                checkpoint=checkpoint,
                training=training,
            )
        )
    return output


def _multiview_training_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
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


def _run_multiview_models(
    *,
    train_rows: Sequence[Mapping[str, Any]],
    evaluation_rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    selected: Mapping[str, Mapping[str, Any]],
    feature_builder: StructuredPairFeatureBuilder,
    output_dir: Path,
    fold: int,
    task_count: int,
    device: torch.device,
) -> list[dict[str, Any]]:
    state_position = {
        value: index for index, value in enumerate(representations["state_ids"])
    }
    transition_position = {
        value: index for index, value in enumerate(representations["transition_ids"])
    }
    training_settings = _multiview_training_settings(settings)
    main_by_layer = {}
    output = []
    for kind in MULTIVIEW_MODELS:
        layer = str(selected[kind]["layer"])
        if kind == "structured_feature_interaction":
            layer = "final_layer"
        if layer not in main_by_layer:
            main_path = output_dir / "multiview" / f"main_{layer}.pt"
            main_by_layer[layer] = multiview_main_checkpoint(
                path=main_path,
                decomposition=decomposition,
                state_representations=representations["state_multiview"][layer],
                transition_representations=representations["transition_multiview"][
                    layer
                ],
                state_position=state_position,
                transition_position=transition_position,
                settings=training_settings,
                decomposition_settings=settings["decomposition"],
                seed=_seed(
                    int(settings["learning_curves"]["seed"]),
                    fold,
                    task_count,
                    "multiview-main",
                    layer,
                ),
                device=device,
                metadata={
                    "format": "learning_curve_multiview_main_6c_v1",
                    "fold": fold,
                    "task_count": task_count,
                    "layer": layer,
                },
            )[0]
        main = main_by_layer[layer]
        pair_features = None
        normalization = None
        feature_dim = None
        if kind == "structured_feature_interaction":
            pair_features = feature_builder.rows(train_rows)
            normalization = feature_stats(pair_features)
            pair_features = (pair_features - normalization["mean"]) / normalization[
                "std"
            ]
            feature_dim = int(pair_features.shape[-1])
        checkpoint = output_dir / "multiview" / f"{kind}.pt"
        model, training, _ = multiview_interaction_checkpoint(
            path=checkpoint,
            kind=kind,
            main=main,
            decomposition=decomposition,
            rows=train_rows,
            state_representations=representations["state_multiview"][layer],
            transition_representations=representations["transition_multiview"][layer],
            state_position=state_position,
            transition_position=transition_position,
            settings=training_settings,
            epochs=int(selected[kind]["epochs"]),
            seed=_seed(
                int(settings["learning_curves"]["seed"]), fold, task_count, kind
            ),
            device=device,
            metadata={
                "format": "learning_curve_multiview_interaction_6c_v1",
                "fold": fold,
                "task_count": task_count,
                "layer": layer,
            },
            pair_features=pair_features,
            feature_dim=feature_dim,
        )
        predictions = multiview_predict(
            model=model,
            rows=evaluation_rows,
            decomposition=decomposition,
            state_representations=representations["state_multiview"][layer],
            transition_representations=representations["transition_multiview"][layer],
            state_position=state_position,
            transition_position=transition_position,
            device=device,
            control="correct",
            seed=_seed(
                int(settings["learning_curves"]["seed"]),
                fold,
                task_count,
                kind,
                "eval",
            ),
            feature_builder=(
                feature_builder if kind == "structured_feature_interaction" else None
            ),
            feature_normalization=normalization,
        )
        prediction_path = output_dir / "predictions" / f"{kind}.jsonl"
        write_jsonl(prediction_path, predictions)
        metrics = summarize_revised_predictions(predictions, **_metric_kwargs(settings))
        output.append(
            _summary_row(
                model_kind=kind,
                fold=fold,
                task_count=task_count,
                pair_count=len(train_rows),
                metrics=metrics,
                checkpoint=checkpoint,
                training=training,
            )
        )
    return output


def _run_cross_encoder(
    *,
    train_rows: Sequence[Mapping[str, Any]],
    evaluation_rows: Sequence[Mapping[str, Any]],
    decomposition: Mapping[str, Any],
    representations: Mapping[str, Any],
    settings: Mapping[str, Any],
    selected_epochs: int,
    output_dir: Path,
    fold: int,
    task_count: int,
    device: torch.device,
) -> dict[str, Any]:
    state_position = {
        value: index for index, value in enumerate(representations["state_ids"])
    }
    transition_position = {
        value: index for index, value in enumerate(representations["transition_ids"])
    }
    training_settings = _multiview_training_settings(settings)
    main_path = output_dir / "cross_encoder/main_final_layer.pt"
    main, _, _ = multiview_main_checkpoint(
        path=main_path,
        decomposition=decomposition,
        state_representations=representations["state_multiview"]["final_layer"],
        transition_representations=representations["transition_multiview"][
            "final_layer"
        ],
        state_position=state_position,
        transition_position=transition_position,
        settings=training_settings,
        decomposition_settings=settings["decomposition"],
        seed=_seed(
            int(settings["learning_curves"]["seed"]),
            fold,
            task_count,
            "cross-main",
        ),
        device=device,
        metadata={
            "format": "learning_curve_cross_main_6c_v1",
            "fold": fold,
            "task_count": task_count,
            "layer": "final_layer",
        },
    )
    with torch.no_grad():
        state_main = dict(
            zip(
                representations["state_ids"],
                (
                    float(value)
                    for value in main.state(
                        representations["state_multiview"]["final_layer"].to(device)
                    )
                    .cpu()
                    .tolist()
                ),
            )
        )
        transition_main = dict(
            zip(
                representations["transition_ids"],
                (
                    float(value)
                    for value in main.transition(
                        representations["transition_multiview"]["final_layer"].to(
                            device
                        )
                    )
                    .cpu()
                    .tolist()
                ),
            )
        )
    all_rows = [*train_rows, *evaluation_rows]
    base_scores, _, _ = cross_base_score_maps(
        rows=all_rows,
        decomposition=decomposition,
        state_main=state_main,
        transition_main=transition_main,
    )
    feature_by_pair = {
        pair_id: representations["cross_features"][index]
        for index, pair_id in enumerate(representations["cross_pair_ids"])
    }
    train_features = torch.stack(
        [feature_by_pair[str(row["pair_id"])] for row in train_rows]
    )
    normalization = feature_normalization(train_features)
    model = CrossEncoderResidualHead(
        int(train_features.shape[-1]),
        hidden_dim=int(settings["cross_encoder"]["scalar_head_hidden_dim"]),
        dropout=float(settings["multiview"]["dropout"]),
    )
    checkpoint = output_dir / "cross_encoder/model.pt"
    metadata = {
        "format": "learning_curve_cross_encoder_6c_v1",
        "fold": fold,
        "task_count": task_count,
        "epochs": int(selected_epochs),
    }
    if checkpoint.exists():
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if payload["metadata"] != metadata:
            raise ValueError(f"Learning-curve cross checkpoint differs: {checkpoint}")
        model.load_state_dict(payload["model_state_dict"])
        normalization = payload["normalization"]
        training = payload["training"]
    else:
        training = train_cross_encoder_head(
            model=model,
            rows=train_rows,
            features=normalize_features(train_features, normalization),
            base_scores=exact_training_base_scores(train_rows, decomposition),
            decomposition=decomposition,
            settings=settings["current_representation"],
            epochs=int(selected_epochs),
            seed=_seed(
                int(settings["learning_curves"]["seed"]),
                fold,
                task_count,
                CROSS_MODEL,
            ),
            device=device,
        )
        optimizer_state = training.pop("optimizer_state_dict")
        atomic_torch_save(
            {
                "metadata": metadata,
                "model_state_dict": {
                    key: value.detach().cpu()
                    for key, value in model.state_dict().items()
                },
                "optimizer_state_dict": optimizer_state,
                "normalization": {
                    key: value.detach().cpu() for key, value in normalization.items()
                },
                "training": training,
            },
            checkpoint,
        )
    model.to(device).eval()
    predictions = cross_prediction_rows(
        model=model,
        rows=evaluation_rows,
        feature_by_pair=feature_by_pair,
        normalization=normalization,
        base_scores=base_scores,
        control="correct",
        seed=_seed(
            int(settings["learning_curves"]["seed"]),
            fold,
            task_count,
            CROSS_MODEL,
            "eval",
        ),
        device=device,
    )
    prediction_path = output_dir / "predictions" / f"{CROSS_MODEL}.jsonl"
    write_jsonl(prediction_path, predictions)
    metrics = summarize_revised_predictions(predictions, **_metric_kwargs(settings))
    return _summary_row(
        model_kind=CROSS_MODEL,
        fold=fold,
        task_count=task_count,
        pair_count=len(train_rows),
        metrics=metrics,
        checkpoint=checkpoint,
        training=training,
    )


def _run_part_f(
    *,
    args: argparse.Namespace,
    settings: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    started = time.perf_counter()
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    parts_a_b = _load_json(args.artifact_dir / "parts_a_b_summary.json")
    parts_c_d = _load_json(args.artifact_dir / "parts_c_d_summary.json")
    part_e = _load_json(args.artifact_dir / "part_e_summary.json")
    pair_rows = _load_rows(exp018 / "two_axis_pair_rows.jsonl")
    rows_a = [row for row in pair_rows if str(row["cell"]) == CELL_A]
    rows_d = [row for row in pair_rows if str(row["cell"]) == CELL_D]
    if len(rows_a) != int(settings["expected"]["cells"][CELL_A]) or len(rows_d) != int(
        settings["expected"]["cells"][CELL_D]
    ):
        raise ValueError("Learning-curve A/D counts differ")
    manifest = build_nested_task_learning_curve_manifest(
        rows_a,
        task_counts=settings["learning_curves"]["available_query_task_counts"],
        folds=int(settings["learning_curves"]["folds"]),
        seed=int(settings["learning_curves"]["seed"]),
    )
    if not all(
        bool(level["all_parent_coverage"])
        for fold in manifest["folds"]
        for level in fold["levels"]
    ):
        raise ValueError("Learning-curve subset lost train-parent transition coverage")
    atomic_write_json(
        args.artifact_dir / "part_f/learning_curve_manifest.json", manifest
    )
    representations = _load_representations(exp018, args.artifact_dir)
    examples = load_decision_examples(
        Path(settings["source_data"]) / "decision_examples.jsonl"
    )
    query_manifest = _load_json(exp017 / "query_manifest.json")
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
    current_epochs = {
        kind: int(parts_a_b["models"][kind]["epochs"]) for kind in CURRENT_MODELS
    }
    multiview_selected = {
        kind: parts_c_d["selected_configurations"][kind] for kind in MULTIVIEW_MODELS
    }
    cross_epochs = int(part_e["selected_configuration"]["epochs"])
    device = torch.device(args.device)
    progress_path = args.artifact_dir / "part_f/learning_curve_progress.json"
    progress = _load_json(progress_path) if progress_path.exists() else {"rows": []}
    by_key = {
        (str(row["model_kind"]), int(row["fold"]), int(row["task_count"])): row
        for row in progress.get("rows", [])
    }
    total_levels = len(manifest["folds"]) * len(manifest["task_counts"])
    completed_levels = 0
    for fold_payload in manifest["folds"]:
        fold = int(fold_payload["fold"])
        for level in fold_payload["levels"]:
            task_count = int(level["task_count"])
            selected_tasks = set(str(value) for value in level["task_ids"])
            train_rows = [
                row for row in rows_a if str(row["state_task_id"]) in selected_tasks
            ]
            if len(train_rows) != int(level["pair_count"]):
                raise ValueError("Learning-curve pair count differs from manifest")
            decomposition = fit_two_way_decomposition(
                train_rows,
                max_iterations=int(
                    settings["decomposition"]["alternating_least_squares_iterations"]
                ),
                tolerance=float(settings["decomposition"]["tolerance"]),
            )
            output_dir = (
                args.artifact_dir
                / "part_f"
                / "folds"
                / f"fold_{fold}"
                / f"tasks_{task_count}"
            )
            level_rows = []
            level_rows.extend(
                _run_current_models(
                    train_rows=train_rows,
                    evaluation_rows=rows_d,
                    decomposition=decomposition,
                    representations=representations,
                    settings=settings,
                    selected_epochs=current_epochs,
                    output_dir=output_dir,
                    fold=fold,
                    task_count=task_count,
                    device=device,
                )
            )
            level_rows.extend(
                _run_multiview_models(
                    train_rows=train_rows,
                    evaluation_rows=rows_d,
                    decomposition=decomposition,
                    representations=representations,
                    settings=settings,
                    selected=multiview_selected,
                    feature_builder=feature_builder,
                    output_dir=output_dir,
                    fold=fold,
                    task_count=task_count,
                    device=device,
                )
            )
            level_rows.append(
                _run_cross_encoder(
                    train_rows=train_rows,
                    evaluation_rows=rows_d,
                    decomposition=decomposition,
                    representations=representations,
                    settings=settings,
                    selected_epochs=cross_epochs,
                    output_dir=output_dir,
                    fold=fold,
                    task_count=task_count,
                    device=device,
                )
            )
            for row in level_rows:
                by_key[
                    (str(row["model_kind"]), int(row["fold"]), int(row["task_count"]))
                ] = row
            completed_levels += 1
            atomic_write_json(
                progress_path,
                {
                    "format": "learning_curve_atomic_progress_6c_v1",
                    "manifest_sha256": manifest["manifest_sha256"],
                    "completed_levels": completed_levels,
                    "total_levels": total_levels,
                    "rows": [by_key[key] for key in sorted(by_key)],
                    "updated_at_utc": utc_now(),
                },
            )
            attempt.progress(
                status="running_query_task_learning_curves",
                fold=fold,
                task_count=task_count,
                completed_levels=completed_levels,
                total_levels=total_levels,
                latest_validated_checkpoint=str(progress_path),
            )
    rows = [by_key[key] for key in sorted(by_key)]
    expected_rows = (len(CURRENT_MODELS) + len(MULTIVIEW_MODELS) + 1) * total_levels
    if len(rows) != expected_rows:
        raise ValueError(
            f"Learning-curve result count differs: {len(rows)} != {expected_rows}"
        )
    curves = summarize_learning_curves(rows)
    cross_curve = curves["models"][CROSS_MODEL]
    if bool(part_e["gate"]["passed"]):
        decision = "independent_encoding_or_field_factorization_bottleneck"
    elif bool(cross_curve["still_materially_rising_at_maximum"]) or bool(
        cross_curve["unstable_across_folds"]
    ):
        decision = "query_task_coverage_insufficient"
    else:
        decision = "teacher_utility_not_predictable_from_available_prompt_only_features"
    projection = expanded_query_cache_projection(
        current_queries=32,
        legal_pairs=4640,
        scoreable_pairs=4579,
        over_context_pairs=61,
        query_counts=(64, 96),
        seconds_per_scoreable_pair=float(
            settings["cross_encoder"]["observed_exp017_seconds_per_pair"]
        ),
    )
    summary = {
        "format": "interaction_representation_data_sufficiency_summary_6c_v1",
        "run_uuid": str(settings["run_uuid"]),
        "source_commit": args.lambda_head,
        "status": "exp019_completed_after_part_f",
        "manifest": manifest,
        "representation_hashes": representations["hashes"],
        "row_count": len(rows),
        "raw_rows": rows,
        "learning_curves": curves,
        "cross_encoder_gate_passed": bool(part_e["gate"]["passed"]),
        "decision": decision,
        "representation_gate_repaired": False,
        "behavioral_program_remains_blocked": True,
        "expanded_query_cache_projection": projection,
        "runtime_seconds": time.perf_counter() - started,
        "hard_scope": {
            "qwen_forward_calls": 0,
            "qwen_behavioral_backpropagation": False,
            "behavioral_program_training": False,
            "injector_training": False,
            "selector_training": False,
            "appworld_generation": False,
            "stage_c2": False,
            "v4_tag_created_or_moved": False,
        },
        "timestamp_utc": utc_now(),
    }
    atomic_write_json(args.artifact_dir / "part_f_summary.json", summary)
    atomic_write_text(args.artifact_dir / "part_f_report.md", _markdown_report(summary))
    attempt.progress(
        status="exp019_part_f_completed",
        decision=decision,
        latest_validated_checkpoint=str(args.artifact_dir / "part_f_summary.json"),
    )
    return summary


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-019 Part F: Query-Task Coverage Learning Curves",
        "",
        f"- status: `{summary['status']}`",
        f"- decision: `{summary['decision']}`",
        f"- source commit: `{summary['source_commit']}`",
        f"- immutable manifest: `{summary['manifest']['manifest_sha256']}`",
        f"- result rows: `{summary['row_count']}`",
        "",
        "| Model | 4-task NDCG@4 | 8-task NDCG@4 | 12-task NDCG@4 | 8->12 gain | Rising | Unstable |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for kind, values in summary["learning_curves"]["models"].items():
        levels = values["levels"]
        lines.append(
            f"| {kind} | {float(levels['4']['ndcg@4_mean']):.6f} | "
            f"{float(levels['8']['ndcg@4_mean']):.6f} | "
            f"{float(levels['12']['ndcg@4_mean']):.6f} | "
            f"{float(values['final_ndcg@4_gain']):.6f} | "
            f"{values['still_materially_rising_at_maximum']} | "
            f"{values['unstable_across_folds']} |"
        )
    lines.extend(
        [
            "",
            "No Qwen forward/backpropagation, behavioral program, injector, selector, "
            "production field, AppWorld generation/evaluation, Stage C2, end-to-end "
            "RCMF training, demo change, or V4 tag occurred in Part F.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EXP-019 Part F data sufficiency audit"
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
    summary_path = args.artifact_dir / "part_f_summary.json"
    if summary_path.exists():
        summary = _load_json(summary_path)
        print(
            json.dumps(
                {
                    "reused": True,
                    "summary": str(summary_path),
                    "decision": summary["decision"],
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
        phase="part_f_query_task_learning_curves",
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
        summary = _run_part_f(args=args, settings=settings, attempt=attempt)
    print(
        json.dumps(
            {
                "reused": False,
                "summary": str(summary_path),
                "decision": summary["decision"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
