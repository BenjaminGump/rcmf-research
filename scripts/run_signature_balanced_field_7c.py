from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import statistics
import tempfile
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor
from torch.nn import functional as F

from rcmf.config import load_config
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.signature_balanced_field_7c import (
    FIELD_FORMAT,
    SignatureBalancedFieldSelector,
    calibrated_ensemble,
    class_selection_diversity,
    deterministic_seed,
    evaluate_score_matrix,
    grouped_task_parent_folds,
    score_field_selector,
    select_scoreable_class_exemplar,
    state_class_balanced_weights,
    task_grouped_bootstrap_difference,
    train_field_selector,
    validate_class_balance,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_action_intent_probe_6d import (
    ActionIntentProbe,
    LABEL_NAMES,
    _run_probe as run_intent_probe,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _validate_multiview(path: Path, expected_lineage: str) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if str(payload["corpus_lineage_sha256"]) != expected_lineage:
        raise ValueError(f"Clean multiview lineage differs: {path}")
    for layer, tensor in payload["representations"].items():
        if tensor_state_sha256({"representations": tensor}) != payload[
            "tensor_sha256"
        ][layer]:
            raise ValueError(f"Clean multiview tensor hash differs: {path} {layer}")
    if len(set(str(value) for value in payload["ordered_ids"])) != len(
        payload["ordered_ids"]
    ):
        raise ValueError(f"Clean multiview IDs are duplicated: {path}")
    return payload


def _selector(
    settings: Mapping[str, Any], seed: int
) -> SignatureBalancedFieldSelector:
    torch.manual_seed(int(seed))
    model = SignatureBalancedFieldSelector(
        state_views=int(settings["state_views"]),
        transition_views=int(settings["transition_views"]),
        input_dim=int(settings["input_dim"]),
        projection_dim=int(settings["projection_dim"]),
        interaction_rank=int(settings["interaction_rank"]),
    )
    return model


def _subset_representations(
    payload: Mapping[str, Any], ids: Sequence[str], layer: str
) -> Tensor:
    position = {str(value): index for index, value in enumerate(payload["ordered_ids"])}
    missing = [value for value in ids if value not in position]
    if missing:
        raise ValueError(f"Multiview IDs are absent: {missing[:3]}")
    return torch.stack(
        [payload["representations"][layer][position[value]] for value in ids]
    ).to(torch.float32)


def _fit_temperature(logits: Tensor, targets: Tensor) -> float:
    if logits.numel() == 0:
        return 1.0
    log_temperature = torch.zeros((), requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=50)

    def closure() -> Tensor:
        optimizer.zero_grad(set_to_none=True)
        temperature = log_temperature.exp().clamp(0.05, 20.0)
        loss = F.cross_entropy(logits / temperature, targets)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_temperature.detach().exp().clamp(0.05, 20.0))


def _calibration_metrics(
    probabilities: Tensor, targets: Tensor, *, bins: int = 10
) -> dict[str, float | None]:
    if probabilities.numel() == 0:
        return {"ece": None, "brier": None, "nll": None}
    confidence, prediction = probabilities.max(dim=-1)
    correct = prediction.eq(targets).to(torch.float32)
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        selected = (confidence > lower) & (confidence <= upper)
        if selected.any():
            ece += float(selected.to(torch.float32).mean()) * abs(
                float(confidence[selected].mean()) - float(correct[selected].mean())
            )
    one_hot = F.one_hot(targets, num_classes=probabilities.shape[-1]).to(
        torch.float32
    )
    brier = float(((probabilities - one_hot) ** 2).sum(dim=-1).mean())
    nll = float(
        -probabilities[
            torch.arange(len(targets)), targets
        ].clamp_min(1.0e-12).log().mean()
    )
    return {"ece": ece, "brier": brier, "nll": nll}


def _intent_predictions(
    *,
    cache: Mapping[str, Any],
    probe_summary: Mapping[str, Any],
    checkpoint_path: Path,
    layer: str,
    device: torch.device,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    vocabularies = checkpoint["vocabularies"]
    values = cache["representations"][layer].to(torch.float32).flatten(1)
    mean = checkpoint["normalization"]["mean"]
    std = checkpoint["normalization"]["std"]
    normalized = (values - mean) / std
    model = ActionIntentProbe(
        int(normalized.shape[-1]),
        256,
        {name: len(vocabularies[name]) for name in LABEL_NAMES},
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(device).eval()
    with torch.no_grad():
        logits = {name: value.cpu() for name, value in model(normalized.to(device)).items()}
    rows = list(cache["rows"])
    train_indices = [index for index, row in enumerate(rows) if row["split"] == "train"]
    validation_indices = [
        index for index, row in enumerate(rows) if row["split"] == "validation"
    ]
    temperatures = {}
    calibration = {}
    for name in LABEL_NAMES:
        positions = {value: index for index, value in enumerate(vocabularies[name])}
        train_known = [
            index for index in train_indices if rows[index]["labels"][name] in positions
        ]
        train_target = torch.tensor(
            [positions[rows[index]["labels"][name]] for index in train_known],
            dtype=torch.long,
        )
        temperature = _fit_temperature(logits[name][train_known], train_target)
        temperatures[name] = temperature
        validation_known = [
            index
            for index in validation_indices
            if rows[index]["labels"][name] in positions
        ]
        validation_target = torch.tensor(
            [positions[rows[index]["labels"][name]] for index in validation_known],
            dtype=torch.long,
        )
        probabilities = F.softmax(
            logits[name][validation_known] / temperature, dim=-1
        )
        calibration[name] = {
            **_calibration_metrics(probabilities, validation_target),
            "temperature": temperature,
            "known_validation_count": len(validation_known),
            "class_count": len(vocabularies[name]),
        }
    predictions = []
    for index, row in enumerate(rows):
        distributions = {}
        for name in LABEL_NAMES:
            probability = F.softmax(logits[name][index] / temperatures[name], dim=-1)
            distributions[name] = {
                value: float(probability[position])
                for position, value in enumerate(vocabularies[name])
            }
        predictions.append(
            {
                "format": "clean_action_intent_prediction_7c_v1",
                "state_example_id": str(row["state_example_id"]),
                "task_id": str(row["task_id"]),
                "split": str(row["split"]),
                "distributions": distributions,
                "target_labels": dict(row["labels"]),
                "ground_truth_not_used_as_input": True,
            }
        )
    return predictions, {
        "format": "clean_action_intent_calibration_7c_v1",
        "probe": dict(probe_summary),
        "temperatures": temperatures,
        "validation_calibration": calibration,
        "calibration_selection": "train rows only",
    }


def _intent_diagnostics(
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    train = [row for row in predictions if row["split"] == "train"]
    validation = [row for row in predictions if row["split"] == "validation"]

    def predicted(row: Mapping[str, Any], name: str) -> str:
        distribution = row["distributions"][name]
        return min(
            distribution,
            key=lambda value: (-float(distribution[value]), str(value)),
        )

    per_task = {}
    for task in sorted({str(row["task_id"]) for row in validation}):
        selected = [row for row in validation if str(row["task_id"]) == task]
        per_task[task] = {
            "state_count": len(selected),
            "accuracy": {
                name: statistics.fmean(
                    float(predicted(row, name) == row["target_labels"][name])
                    for row in selected
                )
                for name in LABEL_NAMES
            },
        }
    return {
        "train_state_count": len(train),
        "validation_state_count": len(validation),
        "class_coverage": {
            name: {
                "train_classes": len(
                    {str(row["target_labels"][name]) for row in train}
                ),
                "validation_classes": len(
                    {str(row["target_labels"][name]) for row in validation}
                ),
                "validation_classes_seen_in_train": len(
                    {str(row["target_labels"][name]) for row in validation}
                    & {str(row["target_labels"][name]) for row in train}
                ),
            }
            for name in LABEL_NAMES
        },
        "per_validation_task": per_task,
    }


def _mean(values: Sequence[float | None]) -> float | None:
    selected = [float(value) for value in values if value is not None]
    return statistics.fmean(selected) if selected else None


def _class_balanced_calibration_values(
    *,
    rows: Sequence[Mapping[str, Any]],
    scores: Tensor,
    state_positions: Mapping[str, int],
    transition_positions: Mapping[str, int],
) -> Tensor:
    """Return one score per legal state/signature class for seed calibration."""

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        state_id = str(row["state_example_id"])
        transition_id = str(row["transition_id"])
        grouped[(state_id, str(row["signature_class_id"]))].append(
            float(scores[state_positions[state_id], transition_positions[transition_id]])
        )
    return torch.tensor(
        [statistics.fmean(grouped[key]) for key in sorted(grouped)],
        dtype=torch.float32,
    )


def _compact_state_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in row.items() if key != "ranked_classes"}
        for row in rows
    ]


def _class_prior_matrix(
    *,
    labels: Sequence[Mapping[str, Any]],
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
) -> Tensor:
    train_rows = [row for row in labels if row["cell"] == "A"]
    state_class: dict[tuple[str, str], list[float]] = defaultdict(list)
    class_by_transition = {}
    for row in labels:
        class_by_transition[str(row["transition_id"])] = str(
            row["signature_class_id"]
        )
    for row in train_rows:
        state_class[
            (str(row["state_example_id"]), str(row["signature_class_id"]))
        ].append(float(row["procedural_tier"]))
    class_values: dict[str, list[float]] = defaultdict(list)
    for (_, class_id), values in state_class.items():
        class_values[class_id].append(statistics.fmean(values))
    global_value = statistics.fmean(
        value for values in class_values.values() for value in values
    )
    transition_scores = torch.tensor(
        [
            statistics.fmean(class_values[class_by_transition[transition_id]])
            if class_by_transition[transition_id] in class_values
            else global_value
            for transition_id in ordered_transition_ids
        ],
        dtype=torch.float32,
    )
    return transition_scores.unsqueeze(0).expand(len(ordered_state_ids), -1).clone()


def _intent_matrix(
    *,
    predictions: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
) -> Tensor:
    prediction_by_state = {
        str(row["state_example_id"]): row for row in predictions
    }
    transition_metadata = {}
    for row in labels:
        transition_metadata[str(row["transition_id"])] = {
            "target_app": str(row["transition_primary_app"]),
            "target_api": str(row["transition_primary_api"]),
            "action_type": str(row["transition_coarse_action_type"]),
            "completion_action": str(
                str(row["transition_primary_api"]) == "supervisor.complete_task"
            ).lower(),
        }
    matrix = torch.empty(
        (len(ordered_state_ids), len(ordered_transition_ids)), dtype=torch.float32
    )
    for state_position, state_id in enumerate(ordered_state_ids):
        distributions = prediction_by_state[state_id]["distributions"]
        for transition_position, transition_id in enumerate(ordered_transition_ids):
            metadata = transition_metadata[transition_id]
            matrix[state_position, transition_position] = statistics.fmean(
                float(distributions[name].get(metadata[name], 0.0))
                for name in LABEL_NAMES
            )
    return matrix


def _split_permutation(
    ids: Sequence[str], group_by_id: Mapping[str, str], *, seed: int
) -> list[int]:
    output = list(range(len(ids)))
    groups: dict[str, list[int]] = defaultdict(list)
    for index, value in enumerate(ids):
        groups[str(group_by_id[value])].append(index)
    for group, positions in groups.items():
        shuffled = list(positions)
        random.Random(deterministic_seed(seed, group)).shuffle(shuffled)
        for source, target in zip(positions, shuffled, strict=True):
            output[source] = target
    return output


def _ensemble_scores(
    *,
    models: Sequence[SignatureBalancedFieldSelector],
    state_values: Tensor,
    transition_values: Tensor,
    calibration: Sequence[Mapping[str, float]],
    batch_states: int,
    device: torch.device,
) -> Tensor:
    values = []
    for model, row in zip(models, calibration, strict=True):
        score = score_field_selector(
            model=model,
            state_representations=state_values,
            transition_representations=transition_values,
            batch_states=batch_states,
            device=device,
        )
        values.append((score - float(row["train_mean"])) / float(row["train_std"]))
    return torch.stack(values, dim=0).mean(dim=0)


def _space_labels(labels: Sequence[Mapping[str, Any]], space: str) -> list[dict[str, Any]]:
    cells = {"B": {"B"}, "C": {"C"}, "D": {"D"}, "E": {"B", "D"}}[space]
    return [dict(row) for row in labels if str(row["cell"]) in cells]


def _evaluate_spaces(
    *,
    labels: Sequence[Mapping[str, Any]],
    scores_by_control: Mapping[str, Tensor],
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
    output_root: Path,
    bootstrap_samples: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    output = {}
    for space in ("B", "C", "D", "E"):
        rows = _space_labels(labels, space)
        controls = {}
        full_rows = {}
        for name, matrix in scores_by_control.items():
            state_rows, metrics = evaluate_score_matrix(
                rows=rows,
                scores=matrix,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
            )
            controls[name] = metrics
            full_rows[name] = state_rows
            _atomic_jsonl(
                output_root / f"{space}_{name}_state_metrics.jsonl",
                state_rows if name == "correct" else _compact_state_rows(state_rows),
            )
        comparisons = {}
        for control in (
            "transition_only",
            "predicted_intent",
            "shuffled_state",
            "shuffled_transition",
            "both_shuffled",
            "mean_state",
            "mean_transition",
            "zero_interaction",
        ):
            comparisons[control] = {
                "ndcg4": task_grouped_bootstrap_difference(
                    full_rows["correct"],
                    full_rows[control],
                    metric="ndcg@4",
                    samples=bootstrap_samples,
                    seed=deterministic_seed(bootstrap_seed, space, control),
                ),
                "tier34_recall4_difference": float(
                    controls["correct"]["tier34_recall@4"]
                    - controls[control]["tier34_recall@4"]
                ),
                "exact_api_recall4_difference": float(
                    controls["correct"]["exact_api_recall@4"]
                    - controls[control]["exact_api_recall@4"]
                ),
            }
        output[space] = {
            "controls": controls,
            "comparisons": comparisons,
        }
    return output


def _evaluate_action_strata(
    *,
    labels: Sequence[Mapping[str, Any]],
    scores_by_control: Mapping[str, Tensor],
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
) -> dict[str, Any]:
    predicates = {
        "non_documentation": lambda row: not bool(
            row["query_api_documentation_action"]
        ),
        "api_documentation": lambda row: bool(
            row["query_api_documentation_action"]
        ),
        "authentication": lambda row: row["query_coarse_action_type"]
        == "authentication",
        "read_query": lambda row: row["query_coarse_action_type"]
        == "read_query",
        "write_mutation": lambda row: row["query_coarse_action_type"]
        == "write_mutation",
        "completion": lambda row: row["query_coarse_action_type"]
        == "completion",
        "python_reasoning": lambda row: row["query_coarse_action_type"]
        == "python_reasoning",
    }
    output: dict[str, Any] = {}
    for space in ("B", "C", "D", "E"):
        space_rows = _space_labels(labels, space)
        output[space] = {}
        for name, predicate in predicates.items():
            selected = [row for row in space_rows if predicate(row)]
            if not selected:
                continue
            output[space][name] = {
                control: evaluate_score_matrix(
                    rows=selected,
                    scores=matrix,
                    ordered_state_ids=ordered_state_ids,
                    ordered_transition_ids=ordered_transition_ids,
                )[1]
                for control, matrix in scores_by_control.items()
            }
    return output


def _selection_diagnostics(
    *,
    state_rows: Sequence[Mapping[str, Any]],
    label_rows: Sequence[Mapping[str, Any]],
    classes: Mapping[str, Mapping[str, Any]],
    transitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    labels_by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in label_rows:
        labels_by_state[str(row["state_example_id"])].append(row)
    output = []
    for state in state_rows:
        state_id = str(state["state_example_id"])
        class_id = str(state["top1_class_id"])
        class_row = classes[class_id]
        legal_class_rows = [
            row
            for row in labels_by_state[state_id]
            if str(row["signature_class_id"]) == class_id
        ]
        exemplar = select_scoreable_class_exemplar(
            class_row=class_row,
            legal_rows=legal_class_rows,
            transitions_by_id=transitions,
        )
        selected = next(
            row
            for row in legal_class_rows
            if str(row["transition_id"]) == exemplar["transition_id"]
        )
        output.append(
            {
                "state_example_id": state_id,
                "task_id": str(state["task_id"]),
                "top1_class_id": class_id,
                "selected_transition_id": str(exemplar["transition_id"]),
                "selected_parent_id": str(selected["transition_parent_id"]),
                "selected_source_task_id": str(
                    selected["transition_parent_task_id"]
                ),
                "selected_tier": int(selected["procedural_tier"]),
                "selected_exact_api": bool(selected["exact_api_sequence"]),
                "selected_signature_frequency": int(class_row["class_size"]),
                "selected_api_documentation": bool(
                    selected["transition_api_documentation_action"]
                ),
                **exemplar,
            }
        )
    return output


def _cv_candidate(
    *,
    candidate: Mapping[str, Any],
    candidate_index: int,
    folds: Sequence[Mapping[str, set[str]]],
    labels_a: Sequence[Mapping[str, Any]],
    state_cache: Mapping[str, Any],
    transition_cache: Mapping[str, Any],
    settings: Mapping[str, Any],
    output_root: Path,
    source_hashes: Mapping[str, str],
    device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    layer = str(settings["layer"])
    fold_rows = []
    for fold_index, fold in enumerate(folds):
        training_rows = [
            row
            for row in labels_a
            if str(row["state_task_id"]) not in fold["heldout_tasks"]
            and str(row["transition_parent_id"]) not in fold["heldout_parents"]
        ]
        validation_rows = [
            row
            for row in labels_a
            if str(row["state_task_id"]) in fold["heldout_tasks"]
            and str(row["transition_parent_id"]) in fold["heldout_parents"]
        ]
        train_state_ids = sorted(
            {str(row["state_example_id"]) for row in training_rows}
        )
        train_transition_ids = sorted(
            {str(row["transition_id"]) for row in training_rows}
        )
        validation_state_ids = sorted(
            {str(row["state_example_id"]) for row in validation_rows}
        )
        validation_transition_ids = sorted(
            {str(row["transition_id"]) for row in validation_rows}
        )
        if not all(
            (
                train_state_ids,
                train_transition_ids,
                validation_state_ids,
                validation_transition_ids,
            )
        ):
            raise RuntimeError(f"Grouped CV fold {fold_index} is empty")
        model_seed = deterministic_seed(
            int(settings["cv_seed"]), candidate["name"], fold_index
        )
        model = _selector(settings, model_seed)
        checkpoint = output_root / f"candidate_{candidate_index}/fold_{fold_index}.pt"
        resume = None
        if checkpoint.exists():
            candidate_resume = torch.load(
                checkpoint, map_location="cpu", weights_only=False
            )
            if candidate_resume.get("source_hashes") != dict(source_hashes):
                raise ValueError(f"CV checkpoint source hashes differ: {checkpoint}")
            resume = candidate_resume

        def save(payload: Mapping[str, Any]) -> None:
            atomic_torch_save(
                {**dict(payload), "source_hashes": dict(source_hashes)}, checkpoint
            )

        training = train_field_selector(
            model=model,
            rows=training_rows,
            state_representations=_subset_representations(
                state_cache, train_state_ids, layer
            ),
            transition_representations=_subset_representations(
                transition_cache, train_transition_ids, layer
            ),
            ordered_state_ids=train_state_ids,
            ordered_transition_ids=train_transition_ids,
            candidate=candidate,
            batch_states=int(settings["batch_states"]),
            maximum_pair_samples_per_state=int(
                settings["maximum_pair_samples_per_state"]
            ),
            maximum_hard_samples_per_state=int(
                settings["maximum_hard_samples_per_state"]
            ),
            weight_decay=float(settings["weight_decay"]),
            seed=model_seed,
            device=device,
            resume=resume,
            checkpoint_callback=save,
            checkpoint_interval_epochs=int(settings["checkpoint_interval_epochs"]),
        )
        validation_state = _subset_representations(
            state_cache, validation_state_ids, layer
        )
        validation_transition = _subset_representations(
            transition_cache, validation_transition_ids, layer
        )
        correct = score_field_selector(
            model=model,
            state_representations=validation_state,
            transition_representations=validation_transition,
            batch_states=int(settings["batch_states"]),
            device=device,
        )
        state_permutation = list(range(len(validation_state_ids)))
        random.Random(deterministic_seed(model_seed, "state-shuffle")).shuffle(
            state_permutation
        )
        transition_permutation = list(range(len(validation_transition_ids)))
        random.Random(
            deterministic_seed(model_seed, "transition-shuffle")
        ).shuffle(transition_permutation)
        state_shuffled = score_field_selector(
            model=model,
            state_representations=validation_state[state_permutation],
            transition_representations=validation_transition,
            batch_states=int(settings["batch_states"]),
            device=device,
        )
        transition_shuffled = score_field_selector(
            model=model,
            state_representations=validation_state,
            transition_representations=validation_transition[transition_permutation],
            batch_states=int(settings["batch_states"]),
            device=device,
        )
        _, correct_metrics = evaluate_score_matrix(
            rows=validation_rows,
            scores=correct,
            ordered_state_ids=validation_state_ids,
            ordered_transition_ids=validation_transition_ids,
        )
        _, state_metrics = evaluate_score_matrix(
            rows=validation_rows,
            scores=state_shuffled,
            ordered_state_ids=validation_state_ids,
            ordered_transition_ids=validation_transition_ids,
        )
        _, transition_metrics = evaluate_score_matrix(
            rows=validation_rows,
            scores=transition_shuffled,
            ordered_state_ids=validation_state_ids,
            ordered_transition_ids=validation_transition_ids,
        )
        fold_rows.append(
            {
                "fold": fold_index,
                "heldout_tasks": sorted(fold["heldout_tasks"]),
                "heldout_parents": sorted(fold["heldout_parents"]),
                "training_pair_count": len(training_rows),
                "validation_pair_count": len(validation_rows),
                "training_state_count": len(train_state_ids),
                "validation_state_count": len(validation_state_ids),
                "metrics": correct_metrics,
                "state_shuffle_ndcg4_drop": float(
                    correct_metrics["ndcg@4"] - state_metrics["ndcg@4"]
                ),
                "transition_shuffle_ndcg4_drop": float(
                    correct_metrics["ndcg@4"] - transition_metrics["ndcg@4"]
                ),
                "training": {
                    key: value
                    for key, value in training.items()
                    if not key.endswith("state_dict")
                },
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
            }
        )
        attempt.progress(
            status="a_only_grouped_cv",
            candidate=str(candidate["name"]),
            fold=fold_index,
            total_folds=len(folds),
            latest_validated_checkpoint=str(checkpoint),
        )
    return {
        "candidate": dict(candidate),
        "folds": fold_rows,
        "mean_ndcg4": _mean([row["metrics"]["ndcg@4"] for row in fold_rows]),
        "mean_pairwise_accuracy": _mean(
            [row["metrics"]["same_intent_pairwise_accuracy"] for row in fold_rows]
        ),
        "mean_state_shuffle_drop": _mean(
            [row["state_shuffle_ndcg4_drop"] for row in fold_rows]
        ),
        "mean_transition_shuffle_drop": _mean(
            [row["transition_shuffle_ndcg4_drop"] for row in fold_rows]
        ),
        "fold_ndcg4_std": statistics.pstdev(
            float(row["metrics"]["ndcg@4"]) for row in fold_rows
        ),
    }


def _positive_task_count(
    correct: Mapping[str, Any], control: Mapping[str, Any], metric: str
) -> int:
    return sum(
        correct["per_task"][task][metric]
        > control["per_task"][task][metric]
        for task in correct["per_task"]
        if task in control["per_task"]
        and correct["per_task"][task][metric] is not None
        and control["per_task"][task][metric] is not None
    )


def _gate_summary(
    evaluation: Mapping[str, Any], settings: Mapping[str, Any]
) -> dict[str, Any]:
    strict = evaluation["B"]
    deployment = evaluation["E"]
    heldout_parent = evaluation["D"]
    strict_correct = strict["controls"]["correct"]
    strict_popularity = strict["controls"]["transition_only"]
    deployment_correct = deployment["controls"]["correct"]
    deployment_popularity = deployment["controls"]["transition_only"]
    strict_ci = strict["comparisons"]["shuffled_transition"]["ndcg4"]["ci95"]
    strict_gate = settings["strict_b"]
    strict_checks = {
        "ndcg4_gain": strict_correct["ndcg@4"]
        >= strict_popularity["ndcg@4"] + float(strict_gate["ndcg4_gain"]),
        "tier34_recall4": strict_correct["tier34_recall@4"]
        >= float(strict_gate["tier34_recall4"]),
        "exact_api_recall4": strict_correct["exact_api_recall@4"]
        >= float(strict_gate["exact_api_recall4"]),
        "same_intent_pairwise": strict_correct["same_intent_pairwise_accuracy"]
        is not None
        and strict_correct["same_intent_pairwise_accuracy"]
        >= float(strict_gate["same_intent_pairwise_accuracy"]),
        "state_shuffle": strict["comparisons"]["shuffled_state"]["ndcg4"][
            "observed_difference"
        ]
        >= float(strict_gate["state_shuffle_drop"]),
        "transition_shuffle": strict["comparisons"]["shuffled_transition"][
            "ndcg4"
        ]["observed_difference"]
        >= float(strict_gate["transition_shuffle_drop"]),
        "transition_shuffle_ci": strict_ci is not None and strict_ci[0] > 0.0,
        "positive_tasks": _positive_task_count(
            strict_correct, strict_popularity, "ndcg@4"
        )
        >= int(strict_gate["positive_tasks"]),
    }
    deployment_gate = settings["deployment_e"]
    deployment_checks = {
        "ndcg4_gain": deployment_correct["ndcg@4"]
        >= deployment_popularity["ndcg@4"]
        + float(deployment_gate["ndcg4_gain"]),
        "tier34_recall4": deployment_correct["tier34_recall@4"]
        >= float(deployment_gate["tier34_recall4"]),
        "exact_api_recall4": deployment_correct["exact_api_recall@4"]
        >= float(deployment_gate["exact_api_recall4"]),
        "top1_tier34": deployment_correct["top1_tier34_coverage"]
        >= float(deployment_gate["top1_tier34_coverage"]),
        "state_shuffle": deployment["comparisons"]["shuffled_state"]["ndcg4"][
            "observed_difference"
        ]
        >= float(deployment_gate["state_shuffle_drop"]),
        "transition_shuffle": deployment["comparisons"][
            "shuffled_transition"
        ]["ndcg4"]["observed_difference"]
        >= float(deployment_gate["transition_shuffle_drop"]),
        "positive_tasks": _positive_task_count(
            deployment_correct, deployment_popularity, "ndcg@4"
        )
        >= int(deployment_gate["positive_tasks"]),
    }
    heldout_checks = {
        "positive_ndcg4_gain": heldout_parent["controls"]["correct"]["ndcg@4"]
        > heldout_parent["controls"]["transition_only"]["ndcg@4"],
        "positive_transition_shuffle": heldout_parent["comparisons"][
            "shuffled_transition"
        ]["ndcg4"]["observed_difference"]
        > 0.0,
        "majority_positive_tasks": _positive_task_count(
            heldout_parent["controls"]["correct"],
            heldout_parent["controls"]["transition_only"],
            "ndcg@4",
        )
        > len(heldout_parent["controls"]["correct"]["per_task"]) / 2,
    }
    strict_passed = all(strict_checks.values())
    deployment_passed = all(deployment_checks.values())
    heldout_parent_passed = all(heldout_checks.values())
    if not strict_passed and not deployment_passed:
        branch = "procedural_oracle_valid_field_selector_failed"
    elif deployment_passed and not strict_passed:
        branch = "deployment_selector_passed_strict_parent_generalization_weak"
    else:
        branch = "selector_gates_passed_pending_behavioral_audit"
    return {
        "strict_b": {
            "checks": strict_checks,
            "passed": strict_passed,
            "positive_task_count": _positive_task_count(
                strict_correct, strict_popularity, "ndcg@4"
            ),
        },
        "deployment_e": {
            "checks": deployment_checks,
            "passed": deployment_passed,
            "positive_task_count": _positive_task_count(
                deployment_correct, deployment_popularity, "ndcg@4"
            ),
        },
        "heldout_parent_d": {
            "checks": heldout_checks,
            "passed": heldout_parent_passed,
        },
        "branch": branch,
        "behavioral_audit_allowed": deployment_passed,
    }


def _report(summary: Mapping[str, Any]) -> str:
    intent = summary["intent_probe"]["probe"]
    gates = summary["gates"]
    lines = [
        "# EXP-025C Signature-Balanced Procedural Field Selector",
        "",
        "## VERIFIED",
        "",
        f"- A-only selected candidate: `{summary['selected_candidate']['name']}`",
        f"- final seeds: `{summary['final_seeds']}`",
        f"- strict-B passed: `{gates['strict_b']['passed']}`",
        f"- deployment-E passed: `{gates['deployment_e']['passed']}`",
        f"- held-out-parent D passed: `{gates['heldout_parent_d']['passed']}`",
        f"- selector branch: `{gates['branch']}`",
        f"- behavioral audit allowed: `{gates['behavioral_audit_allowed']}`",
        "",
        "## Clean Intent Probe",
        "",
        f"- validation mean strict accuracy: "
        f"`{intent['correct']['mean_strict_accuracy']:.6f}`",
        f"- shuffled-state mean strict accuracy: "
        f"`{intent['shuffled_state']['mean_strict_accuracy']:.6f}`",
        "",
        "| Space | Field NDCG@4 | Popularity | Tier-3/4 R@4 | Exact API R@4 | "
        "State-shuffle drop | Transition-shuffle drop |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for space in ("B", "C", "D", "E"):
        row = summary["evaluation"][space]
        correct = row["controls"]["correct"]
        popularity = row["controls"]["transition_only"]
        lines.append(
            f"| {space} | {correct['ndcg@4']:.6f} | {popularity['ndcg@4']:.6f} | "
            f"{correct['tier34_recall@4']:.6f} | "
            f"{correct['exact_api_recall@4']:.6f} | "
            f"{row['comparisons']['shuffled_state']['ndcg4']['observed_difference']:.6f} | "
            f"{row['comparisons']['shuffled_transition']['ndcg4']['observed_difference']:.6f} |"
        )
    lines.extend(
        [
            "",
            "Raw-NLL utility and EXP-025B behavioral outcomes were absent from "
            "training, CV, calibration, and selector gate selection.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_signature_balanced_field_7c.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025c")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7c"]
    selector_settings = settings["selector"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preparation = _json(args.artifact_dir / "data_preparation_summary.json")
    cache_root = Path(settings["multiview_cache"]["output_root"])
    cache_summary = _json(cache_root / "clean_multiview_cache_summary.json")
    if preparation["status"] != "completed" or cache_summary["status"] != "completed":
        raise RuntimeError("Clean preparation or multiview cache is incomplete")
    paths = {
        "labels": args.artifact_dir / "clean_full_procedural_labels.jsonl",
        "candidate_spaces": args.artifact_dir / "candidate_space_manifest.json",
        "state_cache": cache_root / "state_multiview.pt",
        "transition_cache": cache_root / "transition_multiview.pt",
        "cache_summary": cache_root / "clean_multiview_cache_summary.json",
        "preparation": args.artifact_dir / "data_preparation_summary.json",
        "signature_classes": Path(settings["parent_exp025b"])
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "parent_split": Path(settings["parent_exp025b"])
        / "clean_procedural_audit/clean_parent_split_manifest.json",
        "transitions": Path(settings["parent_exp025b"])
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Selector input is missing: {name}={path}")
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    labels = _rows(paths["labels"])
    labels_a = [row for row in labels if row["cell"] == "A"]
    weights = state_class_balanced_weights(labels_a)
    balance = validate_class_balance(labels_a, weights)
    if not balance["passed"]:
        raise RuntimeError("Signature-class balancing validation failed")
    lineage = str(settings["expected_structural_lineage_sha256"])
    state_cache = _validate_multiview(paths["state_cache"], lineage)
    transition_cache = _validate_multiview(paths["transition_cache"], lineage)
    layer = str(selector_settings["layer"])
    ordered_state_ids = [str(value) for value in state_cache["ordered_ids"]]
    ordered_transition_ids = [
        str(value) for value in transition_cache["ordered_ids"]
    ]
    if {str(row["state_example_id"]) for row in labels} != set(ordered_state_ids):
        raise ValueError("Label and state multiview IDs differ")
    if {str(row["transition_id"]) for row in labels} != set(
        ordered_transition_ids
    ):
        raise ValueError("Label and transition multiview IDs differ")
    state_values = state_cache["representations"][layer].to(torch.float32)
    transition_values = transition_cache["representations"][layer].to(torch.float32)
    expected_state_shape = (
        len(ordered_state_ids),
        int(selector_settings["state_views"]),
        int(selector_settings["input_dim"]),
    )
    expected_transition_shape = (
        len(ordered_transition_ids),
        int(selector_settings["transition_views"]),
        int(selector_settings["input_dim"]),
    )
    if tuple(state_values.shape) != expected_state_shape:
        raise ValueError(
            f"Clean state multiview shape differs: {tuple(state_values.shape)}"
        )
    if tuple(transition_values.shape) != expected_transition_shape:
        raise ValueError(
            "Clean transition multiview shape differs: "
            f"{tuple(transition_values.shape)}"
        )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    output_root = args.artifact_dir / "selector"
    output_root.mkdir(parents=True, exist_ok=True)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="clean_intent_and_signature_balanced_field_training",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        intent_root = args.artifact_dir / "clean_intent_probe"
        intent_root.mkdir(parents=True, exist_ok=True)
        intent_summary_path = intent_root / "clean_intent_summary.json"
        if intent_summary_path.exists():
            intent_summary = _json(intent_summary_path)
            predictions = _rows(intent_root / "calibrated_predictions.jsonl")
        else:
            intent_probe = run_intent_probe(
                values=state_values,
                rows=list(state_cache["rows"]),
                seed=int(selector_settings["cv_seed"]),
                output_root=intent_root,
                device=device,
                attempt=attempt,
            )
            predictions, calibration = _intent_predictions(
                cache=state_cache,
                probe_summary=intent_probe,
                checkpoint_path=intent_root / "action_intent_probe.pt",
                layer=layer,
                device=device,
            )
            _atomic_jsonl(intent_root / "calibrated_predictions.jsonl", predictions)
            intent_summary = {
                "format": "clean_action_intent_probe_7c_v1",
                "probe": intent_probe,
                "calibration": calibration,
                "diagnostics": _intent_diagnostics(predictions),
                "checkpoint": str(intent_root / "action_intent_probe.pt"),
                "checkpoint_sha256": sha256_file(
                    intent_root / "action_intent_probe.pt"
                ),
            }
            atomic_write_json(intent_summary_path, intent_summary)
        train_parents = sorted(
            {str(row["transition_parent_id"]) for row in labels_a}
        )
        train_tasks = sorted({str(row["state_task_id"]) for row in labels_a})
        folds = grouped_task_parent_folds(
            train_tasks,
            train_parents,
            fold_count=int(selector_settings["cv_folds"]),
            seed=int(selector_settings["cv_seed"]),
        )
        cv_root = output_root / "a_only_cv"
        cv_root.mkdir(parents=True, exist_ok=True)
        candidates = []
        for candidate_index, candidate in enumerate(
            selector_settings["candidates"]
        ):
            candidates.append(
                _cv_candidate(
                    candidate=candidate,
                    candidate_index=candidate_index,
                    folds=folds,
                    labels_a=labels_a,
                    state_cache=state_cache,
                    transition_cache=transition_cache,
                    settings=selector_settings,
                    output_root=cv_root,
                    source_hashes=source_hashes,
                    device=device,
                    attempt=attempt,
                )
            )
            atomic_write_json(
                cv_root / "cv_progress.json", {"candidates": candidates}
            )
        selected = max(
            candidates,
            key=lambda row: (
                float(row["mean_ndcg4"]),
                float(row["mean_pairwise_accuracy"] or -1.0),
                float(row["mean_state_shuffle_drop"] or -1.0),
                float(row["mean_transition_shuffle_drop"] or -1.0),
                -float(row["fold_ndcg4_std"]),
                -int(row["candidate"]["epochs"]),
            ),
        )
        selected_candidate = dict(selected["candidate"])
        cv_report = {
            "format": "a_only_grouped_cv_7c_v1",
            "fold_definition": "simultaneously held-out query tasks and transition parents within cell A",
            "folds": [
                {
                    "fold": index,
                    "heldout_tasks": sorted(row["heldout_tasks"]),
                    "heldout_parents": sorted(row["heldout_parents"]),
                }
                for index, row in enumerate(folds)
            ],
            "candidates": candidates,
            "selected_candidate": selected_candidate,
            "selection_rule": (
                "descending mean NDCG@4, same-intent pairwise accuracy, state and "
                "transition shuffle drops; lower fold std; fewer epochs"
            ),
            "b_c_d_e_inspected_for_selection": False,
        }
        atomic_write_json(cv_root / "a_only_cv_report.json", cv_report)
        final_state_ids = sorted(
            {str(row["state_example_id"]) for row in labels_a}
        )
        final_transition_ids = sorted(
            {str(row["transition_id"]) for row in labels_a}
        )
        final_state_values = _subset_representations(
            state_cache, final_state_ids, layer
        )
        final_transition_values = _subset_representations(
            transition_cache, final_transition_ids, layer
        )
        models = []
        seed_reports = []
        correct_seed_scores = []
        train_score_values = []
        global_state_position = {
            value: index for index, value in enumerate(ordered_state_ids)
        }
        global_transition_position = {
            value: index for index, value in enumerate(ordered_transition_ids)
        }
        for seed in selector_settings["final_seeds"]:
            seed = int(seed)
            model = _selector(selector_settings, seed)
            checkpoint = output_root / f"seed_{seed}/field_selector.pt"
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            resume = None
            if checkpoint.exists():
                candidate_resume = torch.load(
                    checkpoint, map_location="cpu", weights_only=False
                )
                if candidate_resume.get("source_hashes") != source_hashes:
                    raise ValueError(f"Final checkpoint source hashes differ: {checkpoint}")
                resume = candidate_resume

            def save(payload: Mapping[str, Any], path: Path = checkpoint) -> None:
                atomic_torch_save(
                    {**dict(payload), "source_hashes": source_hashes}, path
                )

            training = train_field_selector(
                model=model,
                rows=labels_a,
                state_representations=final_state_values,
                transition_representations=final_transition_values,
                ordered_state_ids=final_state_ids,
                ordered_transition_ids=final_transition_ids,
                candidate=selected_candidate,
                batch_states=int(selector_settings["batch_states"]),
                maximum_pair_samples_per_state=int(
                    selector_settings["maximum_pair_samples_per_state"]
                ),
                maximum_hard_samples_per_state=int(
                    selector_settings["maximum_hard_samples_per_state"]
                ),
                weight_decay=float(selector_settings["weight_decay"]),
                seed=seed,
                device=device,
                resume=resume,
                checkpoint_callback=save,
                checkpoint_interval_epochs=int(
                    selector_settings["checkpoint_interval_epochs"]
                ),
            )
            all_scores = score_field_selector(
                model=model,
                state_representations=state_values,
                transition_representations=transition_values,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            )
            score_path = output_root / f"seed_{seed}/all_transition_scores.pt"
            atomic_torch_save(
                {
                    "format": FIELD_FORMAT,
                    "ordered_state_ids": ordered_state_ids,
                    "ordered_transition_ids": ordered_transition_ids,
                    "scores": all_scores,
                    "scores_sha256": tensor_state_sha256({"scores": all_scores}),
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "source_hashes": source_hashes,
                },
                score_path,
            )
            legal_train_values = _class_balanced_calibration_values(
                rows=labels_a,
                scores=all_scores,
                state_positions=global_state_position,
                transition_positions=global_transition_position,
            )
            models.append(model)
            correct_seed_scores.append(all_scores)
            train_score_values.append(legal_train_values)
            seed_reports.append(
                {
                    "seed": seed,
                    "checkpoint": str(checkpoint),
                    "checkpoint_sha256": sha256_file(checkpoint),
                    "score_path": str(score_path),
                    "score_sha256": sha256_file(score_path),
                    "training": {
                        key: value
                        for key, value in training.items()
                        if not key.endswith("state_dict")
                    },
                }
            )
            attempt.progress(
                status="final_three_seed_training",
                completed_seed_count=len(seed_reports),
                total_seed_count=len(selector_settings["final_seeds"]),
                latest_validated_checkpoint=str(checkpoint),
            )
        for report, seed_scores in zip(
            seed_reports, correct_seed_scores, strict=True
        ):
            report["evaluation"] = {
                space: evaluate_score_matrix(
                    rows=_space_labels(labels, space),
                    scores=seed_scores,
                    ordered_state_ids=ordered_state_ids,
                    ordered_transition_ids=ordered_transition_ids,
                )[1]
                for space in ("B", "C", "D", "E")
            }
        correct, calibration = calibrated_ensemble(
            train_score_values, correct_seed_scores
        )
        state_split = {
            str(row["state_example_id"]): str(row["split"])
            for row in state_cache["rows"]
        }
        parent_split_manifest = _json(paths["parent_split"])
        transition_split = {
            str(row["transition_id"]): str(
                parent_split_manifest["split_by_parent"][
                    str(row["parent_memory_id"])
                ]
            )
            for row in transition_cache["rows"]
        }
        state_permutation = _split_permutation(
            ordered_state_ids,
            state_split,
            seed=deterministic_seed(selector_settings["cv_seed"], "state-control"),
        )
        transition_permutation = _split_permutation(
            ordered_transition_ids,
            transition_split,
            seed=deterministic_seed(
                selector_settings["cv_seed"], "transition-control"
            ),
        )
        train_state_indices = [
            index
            for index, value in enumerate(ordered_state_ids)
            if state_split[value] == "train"
        ]
        train_transition_indices = [
            index
            for index, value in enumerate(ordered_transition_ids)
            if transition_split[value] == "train"
        ]
        mean_state_values = state_values[train_state_indices].mean(
            dim=0, keepdim=True
        ).expand_as(state_values)
        mean_transition_values = transition_values[train_transition_indices].mean(
            dim=0, keepdim=True
        ).expand_as(transition_values)
        scores_by_control = {
            "correct": correct,
            "transition_only": _class_prior_matrix(
                labels=labels,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
            ),
            "predicted_intent": _intent_matrix(
                predictions=predictions,
                labels=labels,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
            ),
            "shuffled_state": _ensemble_scores(
                models=models,
                state_values=state_values[state_permutation],
                transition_values=transition_values,
                calibration=calibration,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            ),
            "shuffled_transition": _ensemble_scores(
                models=models,
                state_values=state_values,
                transition_values=transition_values[transition_permutation],
                calibration=calibration,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            ),
            "both_shuffled": _ensemble_scores(
                models=models,
                state_values=state_values[state_permutation],
                transition_values=transition_values[transition_permutation],
                calibration=calibration,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            ),
            "mean_state": _ensemble_scores(
                models=models,
                state_values=mean_state_values,
                transition_values=transition_values,
                calibration=calibration,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            ),
            "mean_transition": _ensemble_scores(
                models=models,
                state_values=state_values,
                transition_values=mean_transition_values,
                calibration=calibration,
                batch_states=int(selector_settings["batch_states"]),
                device=device,
            ),
            "zero_interaction": torch.zeros_like(correct),
        }
        ensemble_path = output_root / "ensemble_scores.pt"
        atomic_torch_save(
            {
                "format": "calibrated_three_seed_field_ensemble_7c_v1",
                "ordered_state_ids": ordered_state_ids,
                "ordered_transition_ids": ordered_transition_ids,
                "scores": correct,
                "scores_sha256": tensor_state_sha256({"scores": correct}),
                "train_calibration": calibration,
                "seed_checkpoints": seed_reports,
                "source_hashes": source_hashes,
            },
            ensemble_path,
        )
        evaluation_root = output_root / "evaluation"
        evaluation_root.mkdir(parents=True, exist_ok=True)
        evaluation = _evaluate_spaces(
            labels=labels,
            scores_by_control=scores_by_control,
            ordered_state_ids=ordered_state_ids,
            ordered_transition_ids=ordered_transition_ids,
            output_root=evaluation_root,
            bootstrap_samples=int(selector_settings["bootstrap_samples"]),
            bootstrap_seed=int(selector_settings["bootstrap_seed"]),
        )
        action_strata = _evaluate_action_strata(
            labels=labels,
            scores_by_control=scores_by_control,
            ordered_state_ids=ordered_state_ids,
            ordered_transition_ids=ordered_transition_ids,
        )
        atomic_write_json(
            evaluation_root / "action_stratified_metrics.json", action_strata
        )
        class_manifest = _json(paths["signature_classes"])
        class_metadata = {
            str(row["signature_class_id"]): row for row in class_manifest["classes"]
        }
        transition_metadata = {
            str(row["transition_id"]): row for row in _rows(paths["transitions"])
        }
        selection_diagnostics = {}
        diversity = {}
        for space in ("B", "D", "E"):
            diagnostics = _selection_diagnostics(
                state_rows=_rows(
                    evaluation_root / f"{space}_correct_state_metrics.jsonl"
                ),
                label_rows=_space_labels(labels, space),
                classes=class_metadata,
                transitions=transition_metadata,
            )
            selection_diagnostics[space] = diagnostics
            diversity[space] = class_selection_diversity(diagnostics)
            _atomic_jsonl(
                evaluation_root / f"{space}_selected_transition_diagnostics.jsonl",
                diagnostics,
            )
        gates = _gate_summary(evaluation, settings["gates"])
        summary = {
            "format": "signature_balanced_field_selector_summary_7c_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "class_balance": balance,
            "intent_probe": intent_summary,
            "a_only_cv": cv_report,
            "selected_candidate": selected_candidate,
            "final_seeds": [int(value) for value in selector_settings["final_seeds"]],
            "seed_reports": seed_reports,
            "ensemble": {
                "path": str(ensemble_path),
                "sha256": sha256_file(ensemble_path),
                "train_calibration": calibration,
            },
            "evaluation": evaluation,
            "action_stratified_evaluation": action_strata,
            "selected_transition_diagnostics": selection_diagnostics,
            "selected_class_diversity": diversity,
            "gates": gates,
            "hard_scope": {
                "raw_nll_primary_target": False,
                "behavioral_outcomes_used_for_training_or_selection": False,
                "qwen_forward_during_selector_training": False,
                "interaction_only_field": True,
                "historical_checkpoint_retrained": False,
            },
            "completed_at_utc": utc_now(),
        }
        summary_path = output_root / "selector_summary.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(output_root / "selector_report.md", _report(summary))
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
