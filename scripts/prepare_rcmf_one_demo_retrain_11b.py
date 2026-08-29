"""Prepare prompt-dependent EXP-034A inputs without changing EXP-031A semantics."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.benchmarks.appworld.prompt import (
    FULL_DEMO_FIRST_ONLY_PROFILE,
    appworld_renderer_metadata,
)
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
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
from rcmf.training.rcmf_joint_full_bank_9a import (
    FrozenSelectorDecomposition,
    tensor_sha256,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.training.transition_memory_6a import example_task_id, state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.prepare_appworld_structured_rescue_7hr import _class_selection


GLOBAL_SEED = 25101
CACHE_FORMAT = "one_demo_state_multiview_11b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(
        "configs/benchmark/stage_c_rcmf_one_demo_retrain_11b.yaml"
    ))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("dependency", "timing-smoke", "state-cache", "selections"),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp034a_prepare")
    return parser.parse_args()


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path) if row.get("attempt_id")}


def _paths(cfg: Any, artifact_dir: Path) -> dict[str, Path]:
    s9a = cfg.raw["stage_c_9a"]
    s11b = cfg.raw["stage_c_11b"]
    parent_b = Path(str(s9a["parent_exp025b"]))
    corpus = Path(str(s9a["reconciled_corpus_dir"]))
    immutable = s11b["immutable"]
    old_root = Path(str(immutable["exp031a_root"]))
    exp033a = Path(str(immutable["exp033a_root"]))
    return {
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "transitions": parent_b / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signatures": parent_b / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "transition_cache": Path(str(immutable["transition_cache"])),
        "selector_ensemble": Path(str(immutable["selector_ensemble"])),
        "selector_root": Path(str(immutable["selector_root"])),
        "task_split": Path(str(s9a["task_split_manifest"])),
        "replay": parent_b / "replay_validated_corpus_manifest.json",
        "old_outcomes": Path(str(immutable["old_outcomes"])),
        "old_state_cache": Path(str(immutable["old_state_cache"])),
        "old_checkpoint": old_root / "joint_training/checkpoints/epoch_02.pt",
        "old_deployment": old_root / "deployment_field/complete_37_task_field.pt",
        "old_data_manifest": old_root / "data/full_bank_data_manifest.json",
        "old_provenance": old_root / "data/memory_provenance.jsonl",
        "old_shuffle": old_root / "data/key_payload_shuffle_manifest.json",
        "exp033a_prompt": exp033a / "prompt_manifest.json",
        "exp033a_dev": exp033a / "dev_manifest.json",
        "exp033a_audit": Path(str(immutable["exp033a_audit_index"])),
        "dependency": artifact_dir / "dependency_manifest.json",
        "fixed_states": artifact_dir / "prompt_dependent/fixed_state_manifest.json",
        "state_root": artifact_dir / "prompt_dependent/state_rows",
        "state_cache": artifact_dir / "prompt_dependent/state_multiview.pt",
        "state_summary": artifact_dir / "prompt_dependent/one_demo_state_cache_summary.json",
        "timing_smoke": artifact_dir / "runtime/early_timing_smoke.json",
        "early_preflight": artifact_dir / "runtime/early_runtime_preflight.json",
        "runtime_preflight": artifact_dir / "runtime_preflight.json",
        "selections": artifact_dir / "preflight/frozen_train_selections.jsonl",
        "selection_manifest": artifact_dir / "preflight/selection_manifest.json",
        "panel": artifact_dir / "preflight/initial_panel.json",
        "features": artifact_dir / "preflight/structured_feature_rows.jsonl",
        "feature_schema": artifact_dir / "preflight/structured_feature_schema.json",
        "leakage": artifact_dir / "preflight/feature_leakage_audit.json",
    }


def _required(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"EXP-034A input missing: {missing}")


def _prompt_checks(settings: Mapping[str, Any]) -> dict[str, bool]:
    metadata = appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)
    expected = settings["expected"]
    return {
        "profile": str(metadata["prompt_profile"]) == FULL_DEMO_FIRST_ONLY_PROFILE,
        "initial_asset": str(metadata["initial_messages_sha256"]) == str(
            expected["initial_prompt_asset_sha256"]
        ),
        "initial_message_count": int(metadata["initial_message_count"]) == 20,
    }


def _old_rows(paths: Mapping[str, Path], settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = list(_json(paths["old_outcomes"])["rows"])
    counts = Counter(str(row["model_split"]) for row in rows)
    expected = settings["expected"]
    wanted = {
        "model_train": int(expected["train_state_count"]),
        "heldout_train_validation": int(expected["heldout_state_count"]),
    }
    if counts != wanted:
        raise ValueError(f"Frozen EXP-031A state counts differ: {dict(counts)}")
    ids = [str(row["state_example_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Frozen accepted state IDs contain duplicates")
    return rows


def _dependency(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    names = (
        "decisions", "memories", "transitions", "signatures", "transition_cache",
        "selector_ensemble", "task_split", "replay", "old_outcomes",
        "old_state_cache", "old_checkpoint", "old_deployment", "old_data_manifest",
        "old_provenance", "old_shuffle", "exp033a_prompt", "exp033a_dev",
        "exp033a_audit",
    )
    _required(paths, names)
    expected = settings["expected"]
    checks = {
        **_prompt_checks(settings),
        "old_checkpoint": sha256_file(paths["old_checkpoint"]) == str(expected["old_checkpoint_sha256"]),
        "old_deployment": sha256_file(paths["old_deployment"]) == str(expected["old_deployment_field_sha256"]),
        "selector": sha256_file(paths["selector_ensemble"]) == str(expected["selector_ensemble_sha256"]),
        "exp033a_audit": sha256_file(paths["exp033a_audit"]) == str(expected["old_dev_audit_sha256"]),
        "replay_lineage": str(_json(paths["replay"])["lineage_sha256"]) == str(expected["replay_lineage_sha256"]),
        "dev_task_list": str(_json(paths["exp033a_dev"])["ordered_task_ids_sha256"]) == str(expected["dev_task_list_sha256"]),
    }
    if not all(checks.values()):
        raise RuntimeError(f"EXP-034A immutable identity failed: {checks}")
    old = _old_rows(paths, settings)
    split = _json(paths["task_split"])
    if len(split["train_task_ids"]) != 29 or len(split["validation_task_ids"]) != 8:
        raise ValueError("Immutable 29/8 task split differs")
    independent = (
        "replay", "transitions", "memories", "transition_cache",
        "selector_ensemble", "task_split", "old_provenance", "old_shuffle",
    )
    dependent = ("old_state_cache", "old_outcomes")
    fixed_ids = [str(row["state_example_id"]) for row in old]
    payload = {
        "format": "rcmf_one_demo_dependency_manifest_11b_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "only_scientific_change": "training prompt full_demo to full_demo_first_only",
        "prompt_independent": {
            name: {"path": str(paths[name]), "sha256": sha256_file(paths[name])}
            for name in independent
        },
        "prompt_dependent_rejected": {
            name: {
                "path": str(paths[name]),
                "sha256": sha256_file(paths[name]),
                "reused_for_scientific_value": False,
                "identity_only": name == "old_outcomes",
            }
            for name in dependent
        },
        "must_rebuild": [
            "state_renderings", "state_multiview", "state_queries",
            "selected_transition_identities", "paired_bare_raw_outcomes",
            "causal_labels", "policy_teacher_rows", "ground_truth_prompt_rows",
            "zero_policy_nll", "training_units", "heldout_teacher_forced",
            "heldout_live",
        ],
        "fixed_state_ids": fixed_ids,
        "fixed_state_id_sha256": canonical_sha256(fixed_ids),
        "state_counts": dict(Counter(str(row["model_split"]) for row in old)),
        "task_split": {
            "train_task_ids": list(split["train_task_ids"]),
            "validation_task_ids": list(split["validation_task_ids"]),
            "sha256": sha256_file(paths["task_split"]),
        },
        "identity_checks": checks,
        "dev_accessed_for_training": False,
        "test_normal_accessed": False,
        "first37_accessed": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["dependency"], payload)
    atomic_write_json(paths["fixed_states"], {
        "format": "rcmf_one_demo_fixed_state_manifest_11b_v1",
        "ordered_state_ids": fixed_ids,
        "state_counts": payload["state_counts"],
        "task_split_sha256": payload["task_split"]["sha256"],
        "source_old_outcomes_sha256": sha256_file(paths["old_outcomes"]),
        "dev_accessed": False,
        "manifest_sha256": payload["fixed_state_id_sha256"],
    })
    return payload



def _timing_smoke(
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
    *,
    source_head: str,
) -> dict[str, Any]:
    _required(paths, ("dependency", "decisions", "old_outcomes"))
    accepted = _old_rows(paths, settings)
    selected_ids = [str(row["state_example_id"]) for row in accepted[:2]]
    examples = load_decision_examples(paths["decisions"])
    by_id = {
        state_example_id(index, example): example
        for index, example in enumerate(examples)
    }
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    elapsed_rows = []
    for ordinal, state_id in enumerate(selected_ids, start=1):
        example = by_id[state_id]
        rendered, char_spans, _ = query_state_text_and_char_spans(
            backend.tokenizer, example, FULL_DEMO_FIRST_ONLY_PROFILE
        )
        input_ids, attention_mask, spans = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        if backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        readouts = frozen_qwen_span_readouts(
            model=backend.model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            span_rows=spans,
            device=backend.device,
        )
        if backend.device.type == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        if not readouts:
            raise RuntimeError("EXP-034A timing smoke produced no state readouts")
        elapsed_rows.append(
            {
                "state_example_id": state_id,
                "prompt_sha256": sha256_text(rendered),
                "prompt_tokens": int(input_ids.shape[1]),
                "forward_seconds": elapsed,
            }
        )
        attempt.progress(
            status="one_demo_timing_smoke",
            completed=ordinal,
            total=len(selected_ids),
        )
    per_state = sum(row["forward_seconds"] for row in elapsed_rows) / len(elapsed_rows)
    old_root = paths["old_checkpoint"].parents[2]
    old_teacher_report = (
        paths["old_outcomes"].parents[1]
        / "structured_compiler/policy_teacher_report.json"
    )
    old_training = old_root / "joint_training/training_summary.json"
    old_dev_root = Path(str(settings["immutable"]["exp033a_root"]))
    old_d1 = old_dev_root / "dev/conditions/D1/summary.json"
    old_d2 = old_dev_root / "dev/conditions/D2/summary.json"
    required = {
        "teacher": old_teacher_report,
        "training": old_training,
        "d1": old_d1,
        "d2": old_d2,
    }
    missing = {name: str(path) for name, path in required.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"EXP-034A timing references missing: {missing}")
    historical = {
        "paired_supervision_seconds": float(_json(paths["old_outcomes"])["elapsed_seconds"]),
        "policy_teacher_seconds": float(_json(old_teacher_report)["elapsed_seconds"]),
        "training_seconds": float(
            _json(old_training)["elapsed_seconds_this_attempt"]
        ),
        "dev_n1_n2_seconds": float(_json(old_d1)["total_wall_seconds"])
        + float(_json(old_d2)["total_wall_seconds"]),
    }
    state_seconds = per_state * (
        int(settings["expected"]["train_state_count"])
        + int(settings["expected"]["heldout_state_count"])
    )
    scientific_seconds = state_seconds + sum(historical.values())
    expected_seconds = scientific_seconds + 1.5 * 3600.0
    conservative_seconds = scientific_seconds * 1.35 + 2.0 * 3600.0
    threshold_seconds = (
        float(settings["runtime"]["review_threshold_hours"]) * 3600.0
    )
    smoke = {
        "format": "rcmf_one_demo_retrain_early_timing_smoke_11b_v1",
        "non_scientific": True,
        "source_commit": source_head,
        "state_rows": elapsed_rows,
        "mean_state_forward_seconds": per_state,
        "hardware": (
            torch.cuda.get_device_name(0)
            if torch.cuda.is_available()
            else "unavailable"
        ),
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "qwen_frozen": not any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "passed": len(elapsed_rows) == 2,
    }
    atomic_write_json(paths["timing_smoke"], smoke)
    report = {
        "format": "rcmf_one_demo_retrain_early_runtime_preflight_11b_v1",
        "purpose": (
            "fixed 464-state reconstruction, one-demo supervision, exact "
            "two-epoch training, heldout selection, and conditional 57x2 dev"
        ),
        "source_commit": source_head,
        "config_sha256": sha256_file(
            Path("configs/benchmark/stage_c_rcmf_one_demo_retrain_11b.yaml")
        ),
        "global_seed": GLOBAL_SEED,
        "state_count": 464,
        "paired_condition_count": 928,
        "dev_new_condition_count": 114,
        "measured_state_smoke": smoke,
        "historical_exact_recipe_seconds": historical,
        "projected_state_rebuild_seconds": state_seconds,
        "expected_wall_hours": expected_seconds / 3600.0,
        "conservative_wall_hours": conservative_seconds / 3600.0,
        "expected_h100_active_hours": scientific_seconds / 3600.0,
        "hardware_required": str(settings["runtime"]["hardware_required"]),
        "expected_git_safe_audit_bytes": int(
            settings["runtime"]["expected_git_safe_audit_bytes"]
        ),
        "expected_lambda_raw_audit_bytes": int(
            settings["runtime"]["expected_lambda_raw_audit_bytes"]
        ),
        "estimated_cost": {
            "unit": "H100 active hours",
            "expected": scientific_seconds / 3600.0,
            "provider_currency_rate": "not recorded; no fabricated currency estimate",
        },
        "restart_plan": {
            "atomic_state_rows": True,
            "atomic_paired_conditions": True,
            "atomic_teacher_cache": True,
            "atomic_training_checkpoints_every_units": 25,
            "epoch_boundaries": [1, 2],
            "atomic_heldout_conditions": True,
            "atomic_dev_tasks": True,
        },
        "review_threshold_hours": float(
            settings["runtime"]["review_threshold_hours"]
        ),
        "automatic_launch_allowed": conservative_seconds <= threshold_seconds,
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError(
            "EXP-034A conservative end-to-end estimate may exceed 18 hours"
        )
    atomic_write_json(paths["early_preflight"], report)
    atomic_write_json(paths["runtime_preflight"], report)
    return report

def _state_cache(cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger) -> dict[str, Any]:
    _required(paths, ("dependency", "fixed_states", "decisions"))
    accepted = _old_rows(paths, settings)
    ordered_ids = [str(row["state_example_id"]) for row in accepted]
    split_by_id = {str(row["state_example_id"]): str(row["model_split"]) for row in accepted}
    examples = load_decision_examples(paths["decisions"])
    by_id = {
        state_example_id(index, example): (index, example)
        for index, example in enumerate(examples)
    }
    if not set(ordered_ids) <= set(by_id):
        raise ValueError("Fixed states are absent from the clean corpus")
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("State-cache Qwen is not frozen")
    renderer = str(appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)["renderer_version"])
    lineage = str(settings["expected"]["structural_lineage_sha256"])
    paths["state_root"].mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    computed = resumed = 0
    rows: list[dict[str, Any]] = []
    matrices = {layer: [] for layer in LAYER_CANDIDATES}
    for ordinal, state_id in enumerate(ordered_ids, start=1):
        global_index, example = by_id[state_id]
        rendered, char_spans, metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, FULL_DEMO_FIRST_ONLY_PROFILE
        )
        input_ids, attention_mask, spans = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        identity = {
            "state_example_id": state_id,
            "prompt_sha256": sha256_text(rendered),
            "renderer_version": renderer,
            "model_name": str(backend.model_name),
            "corpus_lineage_sha256": lineage,
        }
        row_path = paths["state_root"] / f"{sha256_text(state_id)}.pt"
        was_resumed = row_path.exists()
        if was_resumed:
            payload = torch.load(row_path, map_location="cpu", weights_only=False)
            if any(str(payload.get(k)) != str(v) for k, v in identity.items()):
                raise ValueError(f"Resumed state identity differs: {state_id}")
            if readout_payload_hash(payload["readouts"]) != payload["readout_sha256"]:
                raise ValueError(f"Resumed state tensor differs: {state_id}")
            resumed += 1
        else:
            readouts = frozen_qwen_span_readouts(
                model=backend.model, input_ids=input_ids, attention_mask=attention_mask,
                span_rows=spans, device=backend.device,
            )
            payload = {
                "format": "one_demo_state_multiview_row_11b_v1",
                **identity,
                "readouts": readouts,
                "readout_sha256": readout_payload_hash(readouts),
                "span_rows": spans,
                "source_metadata": metadata,
                "token_count": int(input_ids.shape[1]),
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "truncated": False,
                "created_at_utc": utc_now(),
            }
            atomic_torch_save(payload, row_path)
            computed += 1
        for layer in LAYER_CANDIDATES:
            matrices[layer].append(flatten_multiview_readouts(
                [payload], layer=layer, view_names=STATE_VIEW_NAMES
            )[0].to(torch.float32))
        rows.append({
            "format": "one_demo_state_metadata_11b_v1",
            **identity,
            "example_index": global_index,
            "task_id": example_task_id(example),
            "model_split": split_by_id[state_id],
            "step_id": int(example.step_id),
            "token_count": int(input_ids.shape[1]),
            "provenance": "resumed_one_demo_row" if was_resumed else "computed_one_demo_row",
            "target_action_accessed": False,
            "future_observation_accessed": False,
            "truncated": False,
            "dev_task": False,
        })
        attempt.progress(
            status="one_demo_state_cache", completed=ordinal, total=len(ordered_ids),
            computed=computed, resumed=resumed, latest_validated_checkpoint=str(row_path),
        )
        if ordinal % 10 == 0 or ordinal == len(ordered_ids):
            print(f"one-demo states {ordinal}/{len(ordered_ids)}", flush=True)
    stacked = {layer: torch.stack(values) for layer, values in matrices.items()}
    aggregate = {
        "format": CACHE_FORMAT,
        "ordered_ids": ordered_ids,
        "representations": stacked,
        "rows": rows,
        "model_name": str(backend.model_name),
        "renderer_version": renderer,
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "initial_prompt_asset_sha256": str(settings["expected"]["initial_prompt_asset_sha256"]),
        "view_names": list(STATE_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": value})
            for layer, value in stacked.items()
        },
        "corpus_lineage_sha256": lineage,
        "target_action_accessed": False,
        "future_observation_accessed": False,
        "dev_accessed": False,
        "truncated": False,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(aggregate, paths["state_cache"])
    elapsed = time.perf_counter() - started
    summary = {
        "format": "one_demo_state_cache_summary_11b_v1",
        "state_count": len(rows),
        "model_train_count": sum(row["model_split"] == "model_train" for row in rows),
        "heldout_count": sum(row["model_split"] == "heldout_train_validation" for row in rows),
        "new_qwen_forward_count": computed,
        "resumed_count": resumed,
        "state_cache": str(paths["state_cache"]),
        "state_cache_sha256": sha256_file(paths["state_cache"]),
        "tensor_sha256": aggregate["tensor_sha256"],
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "prompt_identity": _prompt_checks(settings),
        "no_target_action": all(not row["target_action_accessed"] for row in rows),
        "no_future_observation": all(not row["future_observation_accessed"] for row in rows),
        "no_truncation": all(not row["truncated"] for row in rows),
        "no_dev": all(not row["dev_task"] for row in rows),
        "elapsed_seconds": elapsed,
        "h100_hours": elapsed / 3600.0,
        "passed": len(rows) == int(settings["expected"]["train_state_count"]) + int(settings["expected"]["heldout_state_count"]),
    }
    if not summary["passed"]:
        raise RuntimeError("One-demo state-cache accounting differs")
    atomic_write_json(paths["state_summary"], summary)
    return summary


def _selections(cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger) -> dict[str, Any]:
    _required(paths, (
        "dependency", "state_cache", "transition_cache", "selector_ensemble",
        "transitions", "signatures", "decisions", "old_outcomes",
    ))
    state_cache = torch.load(paths["state_cache"], map_location="cpu", weights_only=False)
    transition_cache = torch.load(paths["transition_cache"], map_location="cpu", weights_only=False)
    ensemble = torch.load(paths["selector_ensemble"], map_location="cpu", weights_only=False)
    checkpoint_paths = sorted(paths["selector_root"].glob("seed_*/field_selector.pt"))
    checkpoints = [torch.load(path, map_location="cpu", weights_only=False) for path in checkpoint_paths]
    decomposition = FrozenSelectorDecomposition.from_checkpoints(
        checkpoints, ensemble["train_calibration"]
    )
    state_values = state_cache["representations"]["final_layer"].to(torch.float32)
    transition_values = transition_cache["representations"]["final_layer"].to(torch.float32)
    queries = decomposition.query(state_values)
    keys = decomposition.key(transition_values)
    direct = decomposition.direct_scores(state_values, transition_values)
    decomposed = queries @ keys.T + decomposition.intercept
    error = float((direct - decomposed).abs().max())
    if error > float(cfg.raw["stage_c_9a"]["selector"]["equality_atol"]):
        raise RuntimeError("Frozen selector decomposition differs")
    transition_rows = _rows(paths["transitions"])
    transitions = {str(row["transition_id"]): row for row in transition_rows}
    ordered_transition_ids = [str(value) for value in transition_cache["ordered_ids"]]
    if set(ordered_transition_ids) != set(transitions):
        raise ValueError("Transition candidate universe differs")
    class_payload = _json(paths["signatures"])
    classes = {str(row["signature_class_id"]): row for row in class_payload["classes"]}
    class_by_transition = {
        str(tid): str(row["signature_class_id"])
        for row in class_payload["classes"] for tid in row["member_transition_ids"]
    }
    transition_class_ids = [class_by_transition[value] for value in ordered_transition_ids]
    examples = load_decision_examples(paths["decisions"])
    by_id = {
        state_example_id(index, example): example
        for index, example in enumerate(examples)
    }
    old_rows = _old_rows(paths, settings)
    old_by_id = {str(row["state_example_id"]): row for row in old_rows}
    positions = {str(value): index for index, value in enumerate(state_cache["ordered_ids"])}
    tokenizer = AutoTokenizer.from_pretrained(
        str(cfg.raw["stage_c_9a"]["expected"]["model_name"]), trust_remote_code=True
    )
    selections = []
    for ordinal, old in enumerate(old_rows, start=1):
        state_id = str(old["state_example_id"])
        selected = _class_selection(
            example=by_id[state_id],
            state_scores=decomposed[positions[state_id]].tolist(),
            ordered_transition_ids=ordered_transition_ids,
            transition_class_ids=transition_class_ids,
            transitions=transitions,
            classes=classes,
            tokenizer=tokenizer,
            prompt_profile=FULL_DEMO_FIRST_ONLY_PROFILE,
            context_limit=int(cfg.raw["stage_c_9a"]["appworld"]["context_limit"]),
        )
        if not bool(selected["scoreable"]):
            raise RuntimeError(f"Fixed state became unscoreable: {state_id}")
        selections.append({
            "format": "one_demo_frozen_state_selection_11b_v1",
            "state_example_id": state_id,
            "state_task_id": str(old["state_task_id"]),
            "state_step_id": int(old["state_step_id"]),
            "model_split": str(old["model_split"]),
            **selected,
            "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
            "selection_uses_target_action": False,
            "selection_uses_behavioral_outcome": False,
            "selection_uses_dev": False,
        })
        attempt.progress(
            status="one_demo_frozen_selector", completed=ordinal, total=len(old_rows),
            latest_validated_checkpoint=str(paths["selections"]),
        )
    write_jsonl(paths["selections"], selections)
    ordered_ids = [str(row["state_example_id"]) for row in selections]
    panel = {
        "format": "one_demo_fixed_causal_panel_11b_v1",
        "global_seed": GLOBAL_SEED,
        "state_ids": ordered_ids,
        "expansion_order": [],
        "initial_state_count": len(ordered_ids),
        "maximum_state_count": len(ordered_ids),
        "minimum_per_label": 0,
        "selection_frozen_before_outcomes": True,
        "outcomes_used": False,
        "dev_used": False,
    }
    panel["manifest_sha256"] = canonical_sha256(panel)
    atomic_write_json(paths["panel"], panel)
    schema = {
        "format": "one_demo_unused_gate_feature_schema_11b_v1",
        "names": [], "feature_count": 0, "scientific_training_input": False,
    }
    atomic_write_json(paths["feature_schema"], schema)
    write_jsonl(paths["features"], [{
        "format": "one_demo_unused_gate_feature_row_11b_v1",
        "state_example_id": row["state_example_id"],
        "state_task_id": row["state_task_id"],
        "transition_id": row["selected_transition_id"],
        "selected_class_id": row["selected_class_id"],
        "scoreable": True,
        "feature_values": [],
        "deployment_available": True,
        "scientific_training_input": False,
    } for row in selections])
    atomic_write_json(paths["leakage"], {
        "format": "one_demo_unused_gate_feature_leakage_11b_v1",
        "feature_count": 0, "deployment_available": True,
        "target_action_used": False, "outcome_used": False, "dev_used": False,
    })
    changed = [
        row["state_example_id"] for row in selections
        if str(row["selected_transition_id"]) != str(
            old_by_id[str(row["state_example_id"])]["selected_transition_id"]
        )
    ]
    manifest = {
        "format": "one_demo_selection_manifest_11b_v1",
        "state_count": len(selections),
        "model_train_count": sum(row["model_split"] == "model_train" for row in selections),
        "heldout_count": sum(row["model_split"] == "heldout_train_validation" for row in selections),
        "candidate_transition_count": len(ordered_transition_ids),
        "selector_checkpoint_sha256": [sha256_file(path) for path in checkpoint_paths],
        "selector_ensemble_sha256": sha256_file(paths["selector_ensemble"]),
        "selector_frozen": True,
        "state_query_sha256": tensor_sha256(queries),
        "memory_key_sha256": tensor_sha256(keys),
        "direct_vs_decomposed_max_abs": error,
        "changed_selected_transition_count": len(changed),
        "changed_selected_state_ids": changed,
        "over_context_count": 0,
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "target_action_used": False,
        "behavioral_outcomes_used": False,
        "dev_used": False,
        "selections_sha256": sha256_file(paths["selections"]),
        "panel_sha256": sha256_file(paths["panel"]),
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(paths["selection_manifest"], manifest)
    return manifest


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_11b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-034A requires seed 25101")
    if str(cfg.benchmark.prompt_profile) != FULL_DEMO_FIRST_ONLY_PROFILE:
        raise ValueError("EXP-034A mixed prompt profiles")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    paths = _paths(cfg, args.artifact_dir)
    hashes = {"config": sha256_file(args.config), "old_outcomes": sha256_file(paths["old_outcomes"])}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"one_demo_retrain_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=True,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        started = time.perf_counter()
        if args.phase == "dependency":
            result = _dependency(settings, paths)
            latest = paths["dependency"]
        elif args.phase == "timing-smoke":
            result = _timing_smoke(
                cfg, settings, paths, attempt, source_head=args.local_head
            )
            latest = paths["early_preflight"]
        elif args.phase == "state-cache":
            result = _state_cache(cfg, settings, paths, attempt)
            latest = paths["state_summary"]
        else:
            result = _selections(cfg, settings, paths, attempt)
            latest = paths["selection_manifest"]
        result["phase_elapsed_seconds"] = time.perf_counter() - started
        attempt.progress(
            status=f"one_demo_retrain_{args.phase}_complete",
            latest_validated_checkpoint=str(latest),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
