from __future__ import annotations

import argparse
import ast
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.appworld_structured_rescue_7hr import (
    GLOBAL_SEED,
    LABELS,
    MemoryUseGate,
    class_balanced_weights,
    classify_paired_outcome,
    gate_validation,
    paired_policy_metrics,
    select_gate_threshold,
    standardize_fit,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_audit_7b import condition_checkpoint_name
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _records_by_task,
    _run_condition,
)


MANIFEST_FORMAT = "train_side_paired_causal_condition_manifest_7hr_v1"
PAIRED_FORMAT = "train_side_paired_causal_outcomes_7hr_v1"
GATE_FORMAT = "train_side_causal_memory_gate_7hr_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_structured_rescue_7hr.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("paired", "gate"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_gate")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "panel": artifact_dir / "preflight/initial_panel.json",
        "selections": artifact_dir / "preflight/frozen_train_selections.jsonl",
        "features": artifact_dir / "preflight/structured_feature_rows.jsonl",
        "feature_schema": artifact_dir / "preflight/structured_feature_schema.json",
        "leakage": artifact_dir / "preflight/feature_leakage_audit.json",
        "runtime": artifact_dir / "runtime_preflight.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signatures": parent_b
        / "clean_procedural_audit/clean_transition_signature_manifest.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "manifest": artifact_dir / "paired_causal/condition_manifest.json",
        "outcomes": artifact_dir / "paired_causal/paired_outcomes.json",
        "outcome_report": artifact_dir / "paired_causal/report.md",
        "replay_missing_dir": artifact_dir / "paired_causal/replay_missing",
        "gate_checkpoint": artifact_dir / "gate/memory_use_gate.pt",
        "gate_report": artifact_dir / "gate/gate_report.json",
        "gate_markdown": artifact_dir / "gate/report.md",
    }


def _condition(
    state: Mapping[str, Any], selection: Mapping[str, Any], *, raw: bool
) -> dict[str, Any]:
    name = "T1_selected_raw" if raw else "T0_bare"
    key = hashlib.sha256(
        f"{state['state_example_id']}::{name}::{selection.get('selected_transition_id')}".encode()
    ).hexdigest()
    return {
        "condition_key": key,
        "condition_name": name,
        "prompt_kind": "raw_transition" if raw else "bare",
        "state_example_id": str(state["state_example_id"]),
        "state_task_id": str(state["state_task_id"]),
        "state_step_id": int(state["state_step_id"]),
        "audit_stratum": "train_side_causal_memory_gate",
        "transition_id": str(selection["selected_transition_id"]) if raw else None,
        "transition_parent_id": None,
        "signature_class_id": selection.get("selected_class_id") if raw else None,
        "signature_sha256": None,
        "signature_class_size": None,
        "procedural_tier": None,
        "api_documentation_action": None,
        "selection_frozen_before_outcome": True,
    }


def _build_manifest(
    panel: Mapping[str, Any],
    selections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    ordered_ids = list(panel["state_ids"]) + list(panel["expansion_order"])
    slots = []
    conditions = []
    for ordinal, state_id in enumerate(ordered_ids):
        selection = selections[state_id]
        scoreable = bool(selection["scoreable"])
        slot = {
            "state_example_id": state_id,
            "state_task_id": str(selection["state_task_id"]),
            "state_step_id": int(selection["state_step_id"]),
            "model_split": str(selection["model_split"]),
            "panel_part": "initial" if ordinal < len(panel["state_ids"]) else "expansion",
            "scoreable": scoreable,
            "missing_reason": None if scoreable else "selected_signature_class_over_context",
            "selected_transition_id": selection.get("selected_transition_id"),
            "selected_class_id": selection.get("selected_class_id"),
        }
        slots.append(slot)
        if scoreable:
            conditions.extend(
                [_condition(slot, selection, raw=False), _condition(slot, selection, raw=True)]
            )
    payload = {
        "format": MANIFEST_FORMAT,
        "global_seed": GLOBAL_SEED,
        "selection_frozen_before_outcomes": True,
        "first37_outcomes_used": False,
        "initial_state_count": len(panel["state_ids"]),
        "maximum_state_count": len(ordered_ids),
        "slots": slots,
        "conditions": conditions,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _paired_row(
    slot: Mapping[str, Any],
    result: Mapping[str, Mapping[str, Any]],
    feature: Mapping[str, Any],
    feature_schema_sha256: str,
) -> dict[str, Any]:
    bare = result["T0_bare"]
    raw = result["T1_selected_raw"]
    def gate_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **dict(row),
            "action_signature_match": bool(
                row["canonical_procedural_signature_match"]
            ),
        }

    bare_metrics = gate_metrics(bare["metrics"])
    raw_metrics = gate_metrics(raw["metrics"])
    classified = classify_paired_outcome(bare_metrics, raw_metrics)
    return {
        "state_example_id": str(slot["state_example_id"]),
        "state_task_id": str(slot["state_task_id"]),
        "state_step_id": int(slot["state_step_id"]),
        "model_split": str(slot["model_split"]),
        "panel_part": str(slot["panel_part"]),
        "selected_transition_id": str(slot["selected_transition_id"]),
        "selected_class_id": str(slot["selected_class_id"]),
        "label": classified["label"],
        "classification": classified,
        "bare_metrics": bare_metrics,
        "raw_metrics": raw_metrics,
        "feature_values": list(feature["feature_values"]),
        "feature_schema_sha256": feature_schema_sha256,
        "bare_condition_key": str(bare["condition_key"]),
        "raw_condition_key": str(raw["condition_key"]),
        "bare_prompt_sha256": str(bare["prompt_sha256"]),
        "raw_prompt_sha256": str(raw["prompt_sha256"]),
        "same_world_pairing": bool(
            bare["live_worker"]["same_world_execution"]
            and raw["live_worker"]["same_world_execution"]
        ),
    }


def _traceback_format_only_replay_missing(
    error: BaseException, *, state_id: str
) -> dict[str, Any] | None:
    prefix = "Live bridge did not become ready: "
    message = str(error)
    if not isinstance(error, RuntimeError) or not message.startswith(prefix):
        return None
    try:
        response = ast.literal_eval(message[len(prefix) :])
    except (SyntaxError, ValueError):
        return None
    if not isinstance(response, Mapping) or bool(response.get("ready")):
        return None
    if str(response.get("state_example_id")) != state_id:
        return None
    identity = response.get("task_identity_checks", {})
    if not identity or not all(bool(value) for value in identity.values()):
        return None
    token_validations = list(response.get("token_validations", []))
    if not all(
        bool(row.get("actual", {}).get("payload_validator_accepted"))
        and bool(row.get("actual", {}).get("current_user_validator_accepted"))
        for row in token_validations
    ):
        return None
    failed_steps = [
        row
        for row in response.get("history_steps", [])
        if not bool(row.get("semantic_v3_match")) or row.get("exception") is not None
    ]
    if not failed_steps:
        return None
    redacted_steps = []
    for row in failed_steps:
        if row.get("exception") is not None:
            return None
        before = row.get("state_before", {}).get("sha256")
        after = row.get("state_after", {}).get("sha256")
        if not before or before != after:
            return None
        expected = str(row.get("expected_raw_observation", ""))
        actual = str(row.get("actual_raw_observation", ""))
        expected_lines = expected.splitlines()
        actual_lines = actual.splitlines()
        if (
            not expected_lines
            or not actual_lines
            or expected_lines[0] != "Execution failed. Traceback:"
            or actual_lines[0] != "Execution failed. Traceback:"
            or expected_lines[-1] != actual_lines[-1]
            or ":" not in expected_lines[-1]
        ):
            return None
        exception_type = expected_lines[-1].split(":", 1)[0]
        if not exception_type.endswith("Error"):
            return None
        comparison = row.get("semantic_comparison", {})
        if int(comparison.get("locked_v2", {}).get("non_token_difference_count", 0)) != 1:
            return None
        redacted_steps.append(
            {
                "step_id": int(row["step_id"]),
                "action_sha256": str(row["action_sha256"]),
                "exception_type": exception_type,
                "terminal_exception_sha256": hashlib.sha256(
                    expected_lines[-1].encode()
                ).hexdigest(),
                "expected_observation_sha256": hashlib.sha256(
                    expected.encode()
                ).hexdigest(),
                "actual_observation_sha256": hashlib.sha256(actual.encode()).hexdigest(),
                "state_fingerprint_sha256": str(before),
                "semantic_v3_match": False,
            }
        )
    return {
        "format": "train_side_replay_missing_7hr_v1",
        "state_example_id": state_id,
        "condition_status": "replay_semantic_mismatch_missing",
        "valid_for_generation": False,
        "valid_for_gate_training": False,
        "missing_reason": "python_traceback_format_differs_under_locked_semantic_v3",
        "locked_semantic_v3_preserved": True,
        "task_identity_checks": dict(identity),
        "token_validation_count": len(token_validations),
        "failed_steps": redacted_steps,
    }


def _run_paired(
    *,
    replay_cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    artifact_dir: Path,
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    panel = _json(paths["panel"])
    selections = {str(row["state_example_id"]): row for row in _rows(paths["selections"])}
    features = {str(row["state_example_id"]): row for row in _rows(paths["features"])}
    manifest = _build_manifest(panel, selections)
    if paths["manifest"].exists():
        if _json(paths["manifest"]) != manifest:
            raise ValueError("Frozen paired-causal condition manifest changed")
    else:
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(paths["manifest"], manifest)
    replay = replay_cfg.raw["stage_c_7b"]
    generation = replay["causal_audit"]["generation"]
    backend = HFQwenBackend(
        model_name=str(generation["model_name"]),
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=False,
        load_model=True,
    )
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Paired causal panel loaded trainable Qwen parameters")
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    signatures = {str(row["transition_id"]): row for row in _rows(paths["signatures"])}
    by_state_conditions: dict[str, list[dict[str, Any]]] = {}
    for condition in manifest["conditions"]:
        by_state_conditions.setdefault(str(condition["state_example_id"]), []).append(condition)
    slots = {str(row["state_example_id"]): row for row in manifest["slots"]}
    output_dir = artifact_dir / "paired_causal/condition_outputs"
    initial_ids = list(panel["state_ids"])
    all_ids = initial_ids + list(panel["expansion_order"])
    completed_rows: list[dict[str, Any]] = []
    replay_missing_rows: list[dict[str, Any]] = []
    generated = 0
    reused = 0
    started = time.perf_counter()
    feature_schema_sha256 = sha256_file(paths["feature_schema"])
    for position, state_id in enumerate(all_ids):
        slot = slots[state_id]
        initial = position < len(initial_ids)
        counts = Counter(row["label"] for row in completed_rows)
        if not initial and all(
            counts.get(label, 0) >= int(settings["panel"]["minimum_per_label"])
            for label in LABELS
        ):
            break
        if not bool(slot["scoreable"]):
            continue
        missing_path = paths["replay_missing_dir"] / (
            hashlib.sha256(state_id.encode()).hexdigest() + ".json"
        )
        if missing_path.exists():
            missing = _json(missing_path)
            if (
                str(missing.get("state_example_id")) != state_id
                or str(missing.get("condition_status"))
                != "replay_semantic_mismatch_missing"
            ):
                raise ValueError("Existing replay-missing row differs")
            replay_missing_rows.append(missing)
            continue
        state_results = {}
        for condition in by_state_conditions[state_id]:
            output_path = output_dir / condition_checkpoint_name(str(condition["condition_key"]))
            try:
                row, was_reused = _run_condition(
                    condition=condition,
                    output_path=output_path,
                    stderr_path=artifact_dir
                    / f"paired_causal/worker_logs/{condition_checkpoint_name(str(condition['condition_key']))}.stderr.log",
                    attempt_id=attempt_id,
                    ordinal=generated + reused + 1,
                    settings=replay,
                    config_sha256=sha256_file(paths["manifest"]),
                    corpus_lineage_sha256=str(settings["expected_replay_lineage_sha256"]),
                    condition_manifest=manifest,
                    example=examples[state_id],
                    record=records[str(slot["state_task_id"])],
                    transitions=transitions,
                    signatures=signatures,
                    raw_utility={},
                    backend=backend,
                    semantic_path=paths["semantic_module"],
                    bridge_script=paths["bridge_script"],
                )
            except BaseException as error:
                missing = _traceback_format_only_replay_missing(
                    error, state_id=state_id
                )
                if missing is None:
                    raise
                missing.update(
                    {
                        "state_task_id": str(slot["state_task_id"]),
                        "state_step_id": int(slot["state_step_id"]),
                        "model_split": str(slot["model_split"]),
                        "panel_part": str(slot["panel_part"]),
                        "selected_transition_id": str(
                            slot["selected_transition_id"]
                        ),
                        "selected_class_id": str(slot["selected_class_id"]),
                        "failed_condition_key": str(condition["condition_key"]),
                    }
                )
                atomic_write_json(missing_path, missing)
                replay_missing_rows.append(missing)
                attempt.progress(
                    status="paired_train_causal_replay_missing",
                    completed_states=len(completed_rows),
                    completed_conditions=generated + reused,
                    replay_missing_states=len(replay_missing_rows),
                    latest_validated_checkpoint=str(missing_path),
                )
                print(
                    json.dumps(
                        {
                            "state": state_id,
                            "condition_status": missing["condition_status"],
                            "replay_missing_states": len(replay_missing_rows),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                break
            state_results[str(condition["condition_name"])] = row
            generated += int(not was_reused)
            reused += int(was_reused)
        if len(state_results) != 2:
            continue
        completed_rows.append(
            _paired_row(
                slot,
                state_results,
                features[state_id],
                feature_schema_sha256,
            )
        )
        counts = Counter(row["label"] for row in completed_rows)
        attempt.progress(
            status="paired_train_causal_generation",
            completed_states=len(completed_rows),
            completed_conditions=generated + reused,
            label_counts=dict(counts),
            latest_validated_checkpoint=str(output_path),
        )
        print(
            json.dumps(
                {
                    "state": state_id,
                    "states": len(completed_rows),
                    "conditions": generated + reused,
                    "labels": dict(counts),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    counts = Counter(row["label"] for row in completed_rows)
    passed = all(
        counts.get(label, 0) >= int(settings["panel"]["minimum_per_label"])
        for label in LABELS
    )
    payload = {
        "format": PAIRED_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "state_count": len(completed_rows),
        "condition_count": 2 * len(completed_rows),
        "executed_condition_output_count": generated + reused,
        "initial_state_count": len(initial_ids),
        "initial_completed_state_count": sum(
            row["panel_part"] == "initial" for row in completed_rows
        ),
        "expanded_state_count": sum(
            row["panel_part"] == "expansion" for row in completed_rows
        ),
        "over_context_missing_count": sum(
            not bool(row["scoreable"]) for row in manifest["slots"]
        ),
        "replay_semantic_missing_count": len(replay_missing_rows),
        "replay_semantic_missing_rows": replay_missing_rows,
        "label_counts": dict(counts),
        "generated_conditions": generated,
        "reused_conditions": reused,
        "elapsed_seconds": time.perf_counter() - started,
        "rows": completed_rows,
        "minimum_label_gate_passed": passed,
    }
    payload["maximum_state_space_exhausted"] = (
        payload["state_count"]
        + payload["over_context_missing_count"]
        + payload["replay_semantic_missing_count"]
        == int(manifest["maximum_state_count"])
    )
    atomic_write_json(paths["outcomes"], payload)
    atomic_write_text(
        paths["outcome_report"],
        "\n".join(
            [
                "# EXP-028A train-side paired causal outcomes",
                "",
                f"- states: `{payload['state_count']}`",
                f"- paired conditions: `{payload['condition_count']}`",
                f"- labels: `{json.dumps(payload['label_counts'], sort_keys=True)}`",
                f"- deterministic expansion: `{payload['expanded_state_count']}`",
                f"- over-context logical rows: `{payload['over_context_missing_count']}`",
                f"- locked-v3 replay-missing states: `{payload['replay_semantic_missing_count']}`",
                f"- minimum 40/label gate: `{str(passed).lower()}`",
                f"- maximum state space exhausted: `{str(payload['maximum_state_space_exhausted']).lower()}`",
                "- first37 outcomes used: `false`",
                "",
            ]
        ),
    )
    return payload


def _temperature_scale(
    logits: torch.Tensor, labels: torch.Tensor, candidates: Sequence[float]
) -> tuple[float, list[dict[str, float]]]:
    rows = []
    for value in candidates:
        nll = float(F.cross_entropy(logits / float(value), labels).item())
        rows.append({"temperature": float(value), "validation_nll": nll})
    selected = min(rows, key=lambda row: (row["validation_nll"], row["temperature"]))
    return float(selected["temperature"]), rows


def _run_gate(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    seed_everything(GLOBAL_SEED)
    outcomes = _json(paths["outcomes"])
    label_counts = Counter(str(row["label"]) for row in outcomes["rows"])
    maximum_state_space_exhausted = bool(
        outcomes.get(
            "maximum_state_space_exhausted",
            int(outcomes["state_count"])
            + int(outcomes["over_context_missing_count"])
            + int(outcomes["replay_semantic_missing_count"])
            == len(_json(paths["manifest"])["slots"]),
        )
    )
    complete_or_quota_met = bool(outcomes["minimum_label_gate_passed"]) or bool(
        maximum_state_space_exhausted
    )
    if not complete_or_quota_met or any(label_counts.get(label, 0) == 0 for label in LABELS):
        raise RuntimeError(
            "Paired causal panel neither met the label quota nor exhausted the fixed state space"
        )
    rows = list(outcomes["rows"])
    train_rows = [row for row in rows if row["model_split"] == "model_train"]
    validation_rows = [
        row for row in rows if row["model_split"] == "heldout_train_validation"
    ]
    if not train_rows or not validation_rows:
        raise ValueError("The locked 29/8 model-training split is empty or malformed")
    feature_schema = _json(paths["feature_schema"])
    feature_names = list(feature_schema["names"])
    feature_schema_sha256 = sha256_file(paths["feature_schema"])
    if any(str(row["feature_schema_sha256"]) != feature_schema_sha256 for row in rows):
        raise ValueError("Gate row feature schema differs")
    index = {label: position for position, label in enumerate(LABELS)}
    train_x = torch.tensor([row["feature_values"] for row in train_rows], dtype=torch.float32)
    train_y = torch.tensor([index[row["label"]] for row in train_rows], dtype=torch.long)
    val_x = torch.tensor([row["feature_values"] for row in validation_rows], dtype=torch.float32)
    val_y = torch.tensor([index[row["label"]] for row in validation_rows], dtype=torch.long)
    mean, std = standardize_fit(train_x)
    model = MemoryUseGate(train_x.shape[1], int(settings["gate"]["hidden_dim"]))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(settings["gate"]["learning_rate"]),
        weight_decay=float(settings["gate"]["weight_decay"]),
    )
    weights = class_balanced_weights(train_y)
    checkpoints = []
    previous = 0
    for epoch in settings["gate"]["epoch_checkpoints"]:
        model.train()
        for _ in range(previous, int(epoch)):
            logits = model((train_x - mean) / std)
            loss = F.cross_entropy(logits, train_y, weight=weights)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
        previous = int(epoch)
        model.eval()
        with torch.no_grad():
            train_logits = model((train_x - mean) / std)
            val_logits = model((val_x - mean) / std)
        temperature, calibration = _temperature_scale(
            val_logits, val_y, settings["gate"]["calibration_temperatures"]
        )
        probabilities = torch.softmax(val_logits / temperature, dim=1)
        threshold_rows = [
            paired_policy_metrics(
                validation_rows,
                probabilities[:, index["POSITIVE"]].tolist(),
                probabilities[:, index["HARMFUL"]].tolist(),
                float(threshold),
                float(settings["gate"]["maximum_harmful_probability"]),
            )
            for threshold in settings["gate"]["activation_thresholds"]
        ]
        selected = select_gate_threshold(threshold_rows)
        validation = gate_validation(
            selected,
            minimum_activation_rate=float(settings["gate"]["minimum_activation_rate"]),
            maximum_activation_rate=float(settings["gate"]["maximum_activation_rate"]),
            maximum_harmful_activation_rate=float(settings["gate"]["maximum_harmful_activation_rate"]),
            minimum_positive_prevalence_lift=float(settings["gate"]["minimum_positive_prevalence_lift"]),
            maximum_execution_drop=float(settings["gate"]["maximum_execution_drop"]),
        )
        checkpoints.append(
            {
                "epoch": int(epoch),
                "train_loss": float(F.cross_entropy(train_logits, train_y, weight=weights)),
                "validation_nll": float(F.cross_entropy(val_logits / temperature, val_y)),
                "temperature": temperature,
                "calibration_candidates": calibration,
                "threshold_candidates": threshold_rows,
                "selected_threshold_metrics": selected,
                "validation_gate": validation,
                "state_dict": {
                    key: value.detach().cpu() for key, value in model.state_dict().items()
                },
            }
        )
    eligible = [row for row in checkpoints if row["validation_gate"]["passed"]]
    selected_checkpoint = max(
        eligible if eligible else checkpoints,
        key=lambda row: (
            bool(row["validation_gate"]["passed"]),
            float(row["selected_threshold_metrics"]["gated_successor"]),
            float(row["selected_threshold_metrics"]["gated_signature"]),
            float(row["selected_threshold_metrics"]["gated_execution"]),
            -int(row["selected_threshold_metrics"]["harmful_activation_count"]),
            -float(row["selected_threshold_metrics"]["activation_rate"]),
            float(row["selected_threshold_metrics"]["threshold"]),
            -float(row["validation_nll"]),
            -int(row["epoch"]),
        ),
    )
    paths["gate_checkpoint"].parent.mkdir(parents=True, exist_ok=True)
    atomic_torch_save(
        {
            "format": GATE_FORMAT,
            "global_seed": GLOBAL_SEED,
            "model_state_dict": selected_checkpoint["state_dict"],
            "feature_names": feature_names,
            "feature_schema_sha256": feature_schema_sha256,
            "standardizer_mean": mean,
            "standardizer_std": std,
            "labels": list(LABELS),
            "selected_epoch": int(selected_checkpoint["epoch"]),
            "temperature": float(selected_checkpoint["temperature"]),
            "activation_threshold": float(
                selected_checkpoint["selected_threshold_metrics"]["threshold"]
            ),
            "maximum_harmful_probability": float(
                settings["gate"]["maximum_harmful_probability"]
            ),
            "paired_outcomes_sha256": sha256_file(paths["outcomes"]),
        },
        paths["gate_checkpoint"],
    )
    report_checkpoints = [
        {key: value for key, value in row.items() if key != "state_dict"}
        for row in checkpoints
    ]
    report = {
        "format": GATE_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "train_state_count": len(train_rows),
        "validation_state_count": len(validation_rows),
        "paired_label_counts": dict(label_counts),
        "minimum_40_per_label_achieved": bool(
            outcomes["minimum_label_gate_passed"]
        ),
        "maximum_state_space_exhausted": maximum_state_space_exhausted,
        "train_task_count": len({row["state_task_id"] for row in train_rows}),
        "validation_task_count": len({row["state_task_id"] for row in validation_rows}),
        "train_label_counts": dict(Counter(row["label"] for row in train_rows)),
        "validation_label_counts": dict(Counter(row["label"] for row in validation_rows)),
        "checkpoints": report_checkpoints,
        "selected_epoch": int(selected_checkpoint["epoch"]),
        "selected_temperature": float(selected_checkpoint["temperature"]),
        "selected_threshold": float(
            selected_checkpoint["selected_threshold_metrics"]["threshold"]
        ),
        "selected_metrics": selected_checkpoint["selected_threshold_metrics"],
        "validation_gate": selected_checkpoint["validation_gate"],
        "gate_checkpoint": str(paths["gate_checkpoint"]),
        "gate_checkpoint_sha256": sha256_file(paths["gate_checkpoint"]),
        "passed": bool(selected_checkpoint["validation_gate"]["passed"]),
        "first37_outcomes_used": False,
    }
    atomic_write_json(paths["gate_report"], report)
    atomic_write_text(
        paths["gate_markdown"],
        "\n".join(
            [
                "# EXP-028A train-side causal memory gate",
                "",
                f"- paired states: `{len(rows)}`",
                f"- train/validation: `{len(train_rows)}/{len(validation_rows)}`",
                f"- selected epoch: `{report['selected_epoch']}`",
                f"- selected temperature: `{report['selected_temperature']:.4f}`",
                f"- selected threshold: `{report['selected_threshold']:.2f}`",
                f"- validation gate: `{str(report['passed']).lower()}`",
                f"- validation metrics: `{json.dumps(report['selected_metrics'], sort_keys=True)}`",
                "- first37 outcomes used: `false`",
                "",
            ]
        ),
    )
    return report


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7hr"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "panel",
        "selections",
        "features",
        "feature_schema",
        "leakage",
        "runtime",
        "decisions",
        "memories",
        "transitions",
        "signatures",
        "semantic_module",
        "bridge_script",
    )
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing gate inputs: {missing}")
    if _json(paths["leakage"])["violations"]:
        raise RuntimeError("Structured feature leakage audit failed")
    if not _json(paths["runtime"])["automatic_launch_allowed"]:
        raise RuntimeError("Runtime preflight did not authorize GPU work")
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"train_side_causal_{args.phase}",
        command=[str(value) for value in sys.argv],
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
        if args.phase == "paired":
            result = _run_paired(
                replay_cfg=replay_cfg,
                settings=settings,
                paths=paths,
                artifact_dir=args.artifact_dir,
                attempt=attempt,
                attempt_id=args.attempt_id,
            )
        else:
            if not paths["outcomes"].exists():
                raise FileNotFoundError("Paired causal outcomes are unavailable")
            result = _run_gate(settings, paths)
        attempt.progress(status=f"train_side_causal_{args.phase}_complete")
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "rows"},
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
