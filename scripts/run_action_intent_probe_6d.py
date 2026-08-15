from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
from torch.nn import functional as F

from rcmf.benchmarks.appworld.transitions import API_CALL_RE, _action_type
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    MULTIVIEW_CACHE_VERSION,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    query_state_text_and_char_spans,
    readout_payload_hash,
    tokenize_and_validate_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.training.transition_memory_6a import example_task_id, state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)


PROBE_VERSION = "all_successful_decision_action_intent_probe_6d_v1"
LABEL_NAMES = ("target_app", "target_api", "action_type", "completion_action")
EPOCH_CANDIDATES = (30, 60, 120)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed(base: int, *parts: Any) -> int:
    payload = ":".join(str(value) for value in (base, *parts))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:4], "big")


def _intent_labels(target_text: str) -> dict[str, str]:
    calls = [f"{app}.{api}" for app, api in API_CALL_RE.findall(target_text)]
    first = calls[0] if calls else "__no_api__"
    app = first.split(".", 1)[0] if "." in first else first
    return {
        "target_app": app,
        "target_api": first,
        "action_type": _action_type(target_text),
        "completion_action": str("supervisor.complete_task" in calls).lower(),
    }


def _task_folds(task_ids: Sequence[str], *, folds: int, seed: int) -> list[set[str]]:
    ordered = sorted(
        set(task_ids),
        key=lambda value: (sha256_text(f"{seed}:intent-fold:{value}"), value),
    )
    return [{value for index, value in enumerate(ordered) if index % folds == fold} for fold in range(folds)]


class ActionIntentProbe(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, class_counts: Mapping[str, int]) -> None:
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Dropout(0.05),
        )
        self.heads = nn.ModuleDict(
            {name: nn.Linear(hidden_dim, int(class_counts[name])) for name in LABEL_NAMES}
        )

    def forward(self, values: Tensor) -> dict[str, Tensor]:
        hidden = self.shared(values)
        return {name: head(hidden) for name, head in self.heads.items()}


def _vocabularies(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    return {
        name: sorted({str(row["labels"][name]) for row in rows})
        for name in LABEL_NAMES
    }


def _targets(
    rows: Sequence[Mapping[str, Any]], vocabularies: Mapping[str, Sequence[str]]
) -> tuple[dict[str, Tensor], dict[str, int]]:
    output: dict[str, Tensor] = {}
    unknown = {}
    for name in LABEL_NAMES:
        positions = {value: index for index, value in enumerate(vocabularies[name])}
        values = []
        missing = 0
        for row in rows:
            label = str(row["labels"][name])
            if label not in positions:
                values.append(-1)
                missing += 1
            else:
                values.append(positions[label])
        output[name] = torch.tensor(values, dtype=torch.long)
        unknown[name] = missing
    return output, unknown


def _normalize(train: Tensor, values: Tensor) -> tuple[Tensor, dict[str, Tensor]]:
    mean = train.mean(dim=0)
    std = train.std(dim=0, unbiased=False).clamp_min(1.0e-6)
    return (values - mean) / std, {"mean": mean, "std": std}


def _train_probe(
    *,
    train_x: Tensor,
    train_rows: Sequence[Mapping[str, Any]],
    vocabularies: Mapping[str, Sequence[str]],
    epochs: int,
    seed: int,
    device: torch.device,
) -> tuple[ActionIntentProbe, dict[str, Any]]:
    torch.manual_seed(seed)
    model = ActionIntentProbe(
        int(train_x.shape[-1]),
        256,
        {name: len(vocabularies[name]) for name in LABEL_NAMES},
    ).to(device)
    targets, unknown = _targets(train_rows, vocabularies)
    if any(unknown.values()):
        raise ValueError(f"Training intent rows contain unknown labels: {unknown}")
    optimizer = torch.optim.AdamW(model.parameters(), lr=3.0e-4, weight_decay=1.0e-4)
    generator = torch.Generator().manual_seed(seed)
    history = []
    batch_size = 64
    for epoch in range(1, int(epochs) + 1):
        order = torch.randperm(len(train_rows), generator=generator)
        total = 0.0
        updates = 0
        model.train()
        for start in range(0, len(order), batch_size):
            selected = order[start : start + batch_size]
            logits = model(train_x[selected].to(device))
            loss = sum(
                F.cross_entropy(logits[name], targets[name][selected].to(device))
                for name in LABEL_NAMES
            ) / len(LABEL_NAMES)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total += float(loss.detach().cpu())
            updates += 1
        if epoch in {1, int(epochs)}:
            history.append({"epoch": epoch, "mean_loss": total / max(updates, 1)})
    model.eval()
    return model, {
        "epochs": int(epochs),
        "optimizer_updates": int(epochs) * ((len(train_rows) + batch_size - 1) // batch_size),
        "history": history,
        "optimizer_state_dict": optimizer.state_dict(),
    }


def _balanced_accuracy(target: list[int], prediction: list[int]) -> float | None:
    classes = sorted(set(target))
    if not classes:
        return None
    return statistics.fmean(
        sum(p == t for p, t in zip(prediction, target) if t == value)
        / sum(t == value for t in target)
        for value in classes
    )


@torch.no_grad()
def _evaluate(
    *,
    model: ActionIntentProbe,
    values: Tensor,
    rows: Sequence[Mapping[str, Any]],
    vocabularies: Mapping[str, Sequence[str]],
    device: torch.device,
) -> dict[str, Any]:
    targets, unknown = _targets(rows, vocabularies)
    predictions = {
        name: values.argmax(dim=-1).cpu()
        for name, values in model(values.to(device)).items()
    }
    output = {}
    for name in LABEL_NAMES:
        valid = targets[name] >= 0
        target = targets[name][valid].tolist()
        prediction = predictions[name][valid].tolist()
        correct = sum(left == right for left, right in zip(target, prediction))
        output[name] = {
            "accuracy_on_train_vocabulary": correct / len(target) if target else None,
            "balanced_accuracy_on_train_vocabulary": _balanced_accuracy(target, prediction),
            "known_count": len(target),
            "unknown_label_count": int(unknown[name]),
            "known_label_coverage": len(target) / len(rows),
            "strict_accuracy_unknown_incorrect": correct / len(rows),
        }
    output["mean_strict_accuracy"] = statistics.fmean(
        float(output[name]["strict_accuracy_unknown_incorrect"]) for name in LABEL_NAMES
    )
    return output


def _majority_baseline(
    train_rows: Sequence[Mapping[str, Any]], validation_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    output = {}
    for name in LABEL_NAMES:
        majority = Counter(str(row["labels"][name]) for row in train_rows).most_common(1)[0][0]
        output[name] = {
            "train_majority_label": majority,
            "strict_accuracy": sum(
                str(row["labels"][name]) == majority for row in validation_rows
            )
            / len(validation_rows),
        }
    output["mean_strict_accuracy"] = statistics.fmean(
        float(output[name]["strict_accuracy"]) for name in LABEL_NAMES
    )
    return output


def _state_cache(
    *,
    backend: Any,
    examples: Sequence[Any],
    split_by_id: Mapping[str, str],
    prompt_profile: str,
    renderer_version: str,
    query_cache_path: Path,
    output_root: Path,
    attempt: AttemptLedger,
) -> tuple[dict[str, Tensor], list[dict[str, Any]], dict[str, Any]]:
    query_cache = torch.load(query_cache_path, map_location="cpu", weights_only=False)
    query_position = {
        str(value): index for index, value in enumerate(query_cache["ordered_ids"])
    }
    query_rows = {
        str(row["state_example_id"]): row for row in query_cache["rows"]
    }
    if set(query_rows) != set(query_position):
        raise ValueError("EXP-020 query multi-view metadata IDs differ from tensor IDs")
    for layer in LAYER_CANDIDATES:
        actual_hash = tensor_state_sha256(
            {"representations": query_cache["representations"][layer]}
        )
        if actual_hash != query_cache["tensor_sha256"][layer]:
            raise ValueError(f"EXP-020 query multi-view aggregate hash differs: {layer}")
    row_dir = output_root / "rows"
    row_dir.mkdir(parents=True, exist_ok=True)
    values = {layer: [] for layer in LAYER_CANDIDATES}
    rows = []
    reused_query = 0
    resumed = 0
    computed = 0
    for index, example in enumerate(examples):
        identity = state_example_id(index, example)
        if identity not in split_by_id:
            raise ValueError(f"Decision example is absent from locked split: {identity}")
        rendered, char_spans, source_metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, prompt_profile
        )
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        expected = {
            "format": f"{MULTIVIEW_CACHE_VERSION}_intent_state_row_6d",
            "state_example_id": identity,
            "task_id": example_task_id(example),
            "split": split_by_id[identity],
            "prompt_sha256": sha256_text(rendered),
            "renderer_version": renderer_version,
            "model_name": str(backend.model_name),
            "token_count": int(input_ids.shape[1]),
        }
        payload = None
        if identity in query_position:
            position = query_position[identity]
            source_row = query_rows[identity]
            for key in ("prompt_sha256", "renderer_version", "model_name", "token_count"):
                if source_row.get(key) != expected[key]:
                    raise ValueError(
                        f"EXP-020 query row identity differs for {identity}: {key}"
                    )
            payload = {
                **expected,
                "readouts": {
                    layer: query_cache["representations"][layer][position]
                    for layer in LAYER_CANDIDATES
                },
                "reused_from_exp020_query_cache": True,
            }
            reused_query += 1
        else:
            path = row_dir / f"{sha256_text(identity)}.pt"
            if path.exists():
                candidate = torch.load(path, map_location="cpu", weights_only=False)
                if any(candidate.get(key) != value for key, value in expected.items()):
                    raise ValueError(f"Existing action-intent state row differs: {path}")
                if readout_payload_hash(candidate["readouts"]) != candidate["readout_sha256"]:
                    raise ValueError(f"Action-intent row tensor hash differs: {path}")
                payload = candidate
                resumed += 1
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
                    "readouts": readouts,
                    "readout_sha256": readout_payload_hash(readouts),
                    "span_rows": span_rows,
                    "source_metadata": source_metadata,
                    "target_action_accessed": False,
                    "future_observation_accessed": False,
                    "truncated": False,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, path)
                computed += 1
        for layer in LAYER_CANDIDATES:
            if payload.get("reused_from_exp020_query_cache"):
                values[layer].append(payload["readouts"][layer].to(torch.float32))
            else:
                values[layer].append(
                    flatten_multiview_readouts(
                        [payload], layer=layer, view_names=STATE_VIEW_NAMES
                    )[0].to(torch.float32)
                )
        rows.append(
            {
                **expected,
                "example_index": index,
                "step_id": int(example.step_id),
                "labels": _intent_labels(example.target_text),
                "target_sha256": sha256_text(example.target_text),
                "target_not_encoded": True,
            }
        )
        attempt.progress(
            status="encoding_all_successful_action_intent_states",
            completed=index + 1,
            total=len(examples),
            reused_query=reused_query,
            resumed=resumed,
            newly_computed=computed,
            latest_validated_checkpoint=str(query_cache_path if identity in query_position else path),
        )
        del input_ids, attention_mask
        if torch.cuda.is_available() and (index + 1) % 10 == 0:
            torch.cuda.empty_cache()
    matrices = {layer: torch.stack(items) for layer, items in values.items()}
    aggregate = {
        "format": f"{MULTIVIEW_CACHE_VERSION}_all_successful_intent_aggregate_6d",
        "ordered_ids": [str(row["state_example_id"]) for row in rows],
        "representations": matrices,
        "rows": rows,
        "model_name": str(backend.model_name),
        "renderer_version": renderer_version,
        "view_names": list(STATE_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": tensor})
            for layer, tensor in matrices.items()
        },
        "created_at_utc": utc_now(),
    }
    path = output_root / "all_successful_state_multiview.pt"
    atomic_torch_save(aggregate, path)
    return matrices, rows, {
        "path": str(path),
        "sha256": sha256_file(path),
        "state_count": len(rows),
        "reused_query_rows": reused_query,
        "resumed_rows": resumed,
        "newly_computed_rows": computed,
        "target_action_accessed": False,
        "no_truncation": True,
    }


def _run_probe(
    *,
    values: Tensor,
    rows: list[dict[str, Any]],
    seed: int,
    output_root: Path,
    device: torch.device,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    train_rows = [row for row in rows if row["split"] == "train"]
    validation_rows = [row for row in rows if row["split"] == "validation"]
    train_indices = [int(row["example_index"]) for row in train_rows]
    validation_indices = [int(row["example_index"]) for row in validation_rows]
    train_values = values[train_indices].flatten(1)
    validation_values = values[validation_indices].flatten(1)
    vocabularies = _vocabularies(train_rows)
    folds = _task_folds(
        [str(row["task_id"]) for row in train_rows], folds=5, seed=seed
    )
    candidates = []
    for epochs in EPOCH_CANDIDATES:
        fold_rows = []
        for fold_index, heldout_tasks in enumerate(folds):
            fold_train_positions = [
                index for index, row in enumerate(train_rows) if row["task_id"] not in heldout_tasks
            ]
            fold_validation_positions = [
                index for index, row in enumerate(train_rows) if row["task_id"] in heldout_tasks
            ]
            fold_train_rows = [train_rows[index] for index in fold_train_positions]
            fold_validation_rows = [train_rows[index] for index in fold_validation_positions]
            fold_vocabularies = _vocabularies(fold_train_rows)
            normalized_all, stats = _normalize(
                train_values[fold_train_positions], train_values
            )
            model, training = _train_probe(
                train_x=normalized_all[fold_train_positions],
                train_rows=fold_train_rows,
                vocabularies=fold_vocabularies,
                epochs=epochs,
                seed=_seed(seed, epochs, fold_index),
                device=device,
            )
            metrics = _evaluate(
                model=model,
                values=normalized_all[fold_validation_positions],
                rows=fold_validation_rows,
                vocabularies=fold_vocabularies,
                device=device,
            )
            fold_rows.append(
                {
                    "fold": fold_index,
                    "heldout_task_ids": sorted(heldout_tasks),
                    "train_count": len(fold_train_rows),
                    "validation_count": len(fold_validation_rows),
                    "metrics": metrics,
                    "training": {key: value for key, value in training.items() if key != "optimizer_state_dict"},
                    "normalization_hashes": {
                        key: tensor_state_sha256({key: value}) for key, value in stats.items()
                    },
                }
            )
            attempt.progress(
                status="action_intent_grouped_cv",
                epochs=epochs,
                fold=fold_index,
                total_folds=len(folds),
                latest_validated_checkpoint=str(output_root / "cv_progress.json"),
            )
            atomic_write_json(output_root / "cv_progress.json", {"candidates": candidates, "active": fold_rows})
        candidates.append(
            {
                "epochs": epochs,
                "folds": fold_rows,
                "mean_strict_accuracy": statistics.fmean(
                    float(row["metrics"]["mean_strict_accuracy"]) for row in fold_rows
                ),
            }
        )
    selected = max(candidates, key=lambda row: (row["mean_strict_accuracy"], -row["epochs"]))
    normalized_validation, stats = _normalize(train_values, validation_values)
    normalized_train = (train_values - stats["mean"]) / stats["std"]
    model, training = _train_probe(
        train_x=normalized_train,
        train_rows=train_rows,
        vocabularies=vocabularies,
        epochs=int(selected["epochs"]),
        seed=_seed(seed, "final"),
        device=device,
    )
    correct = _evaluate(
        model=model,
        values=normalized_validation,
        rows=validation_rows,
        vocabularies=vocabularies,
        device=device,
    )
    permutation = list(range(len(validation_rows)))
    random.Random(_seed(seed, "shuffle")).shuffle(permutation)
    shuffled = _evaluate(
        model=model,
        values=normalized_validation[permutation],
        rows=validation_rows,
        vocabularies=vocabularies,
        device=device,
    )
    majority = _majority_baseline(train_rows, validation_rows)
    head_checks = {
        name: float(correct[name]["strict_accuracy_unknown_incorrect"])
        >= float(majority[name]["strict_accuracy"]) + 0.05
        for name in LABEL_NAMES
    }
    mean_shuffle_gap = float(correct["mean_strict_accuracy"]) - float(
        shuffled["mean_strict_accuracy"]
    )
    succeeded = sum(head_checks.values()) >= 3 and mean_shuffle_gap >= 0.10
    checkpoint = output_root / "action_intent_probe.pt"
    optimizer_state = training.pop("optimizer_state_dict")
    atomic_torch_save(
        {
            "format": PROBE_VERSION,
            "model_state_dict": {
                key: value.detach().cpu() for key, value in model.state_dict().items()
            },
            "optimizer_state_dict": optimizer_state,
            "normalization": stats,
            "vocabularies": vocabularies,
            "selected_epochs": int(selected["epochs"]),
            "train_state_ids": [str(row["state_example_id"]) for row in train_rows],
            "validation_state_ids": [str(row["state_example_id"]) for row in validation_rows],
        },
        checkpoint,
    )
    return {
        "format": PROBE_VERSION,
        "train_count": len(train_rows),
        "validation_count": len(validation_rows),
        "train_task_count": len({str(row["task_id"]) for row in train_rows}),
        "validation_task_count": len({str(row["task_id"]) for row in validation_rows}),
        "selected_epochs": int(selected["epochs"]),
        "selection_rule": "train-task-grouped CV only; highest mean strict accuracy then fewer epochs",
        "cv_candidates": candidates,
        "correct": correct,
        "shuffled_state": shuffled,
        "majority": majority,
        "correct_minus_shuffled_mean_accuracy": mean_shuffle_gap,
        "head_gain_checks": head_checks,
        "success_rule": "at least 3/4 heads exceed train-majority by 0.05 and mean correct-minus-shuffled accuracy >= 0.10",
        "succeeded": succeeded,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha256_file(checkpoint),
        "label_definition": "first parsed API call; deterministic action type; supervisor.complete_task indicator",
    }


def _report(summary: Mapping[str, Any]) -> str:
    probe = summary["probe"]
    lines = [
        "# EXP-020 Optional Action-Intent Probe",
        "",
        f"- all successful decision states: `{summary['cache']['state_count']}`",
        f"- train/validation: `{probe['train_count']}` / `{probe['validation_count']}`",
        f"- selected epochs: `{probe['selected_epochs']}` (train-task CV only)",
        f"- correct-minus-shuffled mean strict accuracy: `{probe['correct_minus_shuffled_mean_accuracy']:.6f}`",
        f"- diagnostic succeeded: `{probe['succeeded']}`",
        "",
        "| Head | Correct strict accuracy | Shuffled | Majority | Known coverage |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in LABEL_NAMES:
        lines.append(
            f"| {name} | {probe['correct'][name]['strict_accuracy_unknown_incorrect']:.6f} | "
            f"{probe['shuffled_state'][name]['strict_accuracy_unknown_incorrect']:.6f} | "
            f"{probe['majority'][name]['strict_accuracy']:.6f} | "
            f"{probe['correct'][name]['known_label_coverage']:.6f} |"
        )
    lines.extend(
        [
            "",
            "Qwen was frozen; target actions were used only as probe labels and never encoded into state representations.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EXP-020 optional action-intent probe")
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    model_summary = _load_json(args.artifact_dir / "model_summary.json")
    if model_summary["status"] != "completed":
        raise ValueError("Exact expanded EXP-019 model reproduction is incomplete")
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    existing_attempt_ids = {
        str(row["attempt_id"]) for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    }
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="optional_all_successful_action_intent_probe",
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
        source_data = Path(settings["source_data"])
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        split_manifest = _load_json(Path(settings["split_manifest"]))
        split_by_id = {
            **{str(value): "train" for value in split_manifest["train_example_ids"]},
            **{
                str(value): "validation"
                for value in split_manifest["validation_example_ids"]
            },
        }
        if len(split_by_id) != len(examples):
            raise ValueError(
                f"Locked split/example count differs: {len(split_by_id)} vs {len(examples)}"
            )
        backend = build_backend(cfg, load_model=True)
        backend.model.eval()
        for parameter in backend.model.parameters():
            parameter.requires_grad_(False)
        output_root = args.artifact_dir / "action_intent"
        output_root.mkdir(parents=True, exist_ok=True)
        matrices, rows, cache_report = _state_cache(
            backend=backend,
            examples=examples,
            split_by_id=split_by_id,
            prompt_profile=cfg.benchmark.prompt_profile,
            renderer_version=str(settings["multiview"]["renderer_version"]),
            query_cache_path=(
                args.artifact_dir / "representation_cache/multiview/state_multiview.pt"
            ),
            output_root=output_root / "representation_cache",
            attempt=attempt,
        )
        probe = _run_probe(
            values=matrices["final_layer"],
            rows=rows,
            seed=_seed(settings["seed"], "action-intent"),
            output_root=output_root,
            device=backend.device,
            attempt=attempt,
        )
        runtime_seconds = time.perf_counter() - started
        summary = {
            "format": PROBE_VERSION,
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "cache": cache_report,
            "probe": probe,
            "runtime_seconds": runtime_seconds,
            "actual_h100_hours": runtime_seconds / 3600.0,
            "hard_scope": {
                "qwen_frozen": True,
                "qwen_behavioral_backpropagation": False,
                "target_actions_only_probe_labels": True,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "appworld_generation_or_evaluation": False,
            },
            "timestamp_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "action_intent_summary.json", summary)
        atomic_write_text(args.artifact_dir / "action_intent_report.md", _report(summary))
        attempt.progress(
            status="exp020_action_intent_completed",
            succeeded=probe["succeeded"],
            latest_validated_checkpoint=str(args.artifact_dir / "action_intent_summary.json"),
        )
        print(json.dumps({"probe": probe, "cache": cache_report}, indent=2), flush=True)


if __name__ == "__main__":
    main()
