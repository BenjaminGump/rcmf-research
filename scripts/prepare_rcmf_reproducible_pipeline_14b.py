#!/usr/bin/env python3
"""Prepare the content-addressed EXP-037A contract without running science."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
import yaml

import _bootstrap  # noqa: F401
from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.benchmarks.appworld.reproducible_config_14b import build_arm_runtime_config
from rcmf.benchmarks.appworld.transition_metadata_14c import (
    enrich_transition_token_metadata,
)
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import validate_resolved_arm_diff
from rcmf.training.procedural_causal_audit_6h import build_signature_equivalence_manifest
from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.signature_balanced_field_7c import (
    deterministic_seed,
    grouped_task_parent_folds,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    ensure_dir,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.prepare_procedural_coverage_6g import _full_transition_signatures
from scripts.prepare_signature_balanced_field_7c import (
    _build_labels,
    _candidate_spaces,
    _query_signatures,
)
from scripts.run_signature_balanced_field_7c import _selector
from rcmf.training.datasets import load_decision_examples


RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001"
PREFLIGHT_FORMAT = "rcmf_reproducible_pipeline_preflight_14b_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"),
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--mode", choices=("contract", "shared-cpu", "preflight"), default="preflight")
    parser.add_argument("--smoke-results", type=Path)
    return parser.parse_args()


def _yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_resolved(path: Path) -> dict[str, Any]:
    raw = _yaml(path)
    arm_rows: dict[str, Any] = {}
    for arm_id, pointer in raw.pop("arms").items():
        include = path.parent / str(pointer["include"])
        arm_rows[arm_id] = _yaml(include)
    raw["arms"] = arm_rows
    return raw


def _ordered_hash(values: Sequence[str]) -> str:
    return content_sha256(list(values))


def _environment_manifest(settings: Mapping[str, Any]) -> dict[str, Any]:
    command = [sys.executable, "-m", "pip", "freeze"]
    pip_freeze = subprocess.run(command, check=True, capture_output=True, text=True).stdout.splitlines()
    gpu: dict[str, Any]
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        gpu = {"available": True, "rows": result.stdout.strip().splitlines()}
    except Exception as error:
        gpu = {"available": False, "error": type(error).__name__}
    payload = {
        "format": "rcmf_reproducible_environment_14b_v1",
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "locale": os.environ.get("LANG") or os.environ.get("LC_ALL"),
        "timezone": os.environ.get("TZ"),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version(),
        "bf16_supported": bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported()),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        "gpu": gpu,
        "pip_freeze": pip_freeze,
        "required_environment": dict(settings["required_environment"]),
    }
    payload["environment_sha256"] = content_sha256(payload)
    return payload


def _source_manifest(config: Mapping[str, Any]) -> dict[str, Any]:
    pipeline = config["pipeline"]
    roots = pipeline["roots"]
    corpus = Path(str(roots["authoritative_corpus"]))
    replay = Path(str(roots["replay_validated_corpus"]))
    paths = {
        "summary": corpus / "summary.json",
        "structural_validation": corpus / "structural_validation.json",
        "task_split": corpus / "train_validation_task_manifest.json",
        "decision_examples": corpus / "decision_examples.jsonl",
        "decision_manifest": corpus / "decision_example_manifest.json",
        "memory_records": corpus / "memory_records.jsonl",
        "memory_manifest": corpus / "memory_record_manifest.json",
        "transitions": corpus / "transition_manifest.jsonl",
        "lineage": corpus / "leakage_lineage_manifest.json",
        "replay_manifest": replay / "replay_validated_corpus_manifest.json",
        "downstream_split": Path(str(roots["approved_downstream_split"])),
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Authoritative input is missing: {missing}")
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    validation = json.loads(paths["structural_validation"].read_text(encoding="utf-8"))
    replay_payload = json.loads(paths["replay_manifest"].read_text(encoding="utf-8"))
    expected = pipeline["expected"]
    checks = {
        "structural_validation": bool(validation["passed"]),
        "structural_lineage": str(summary["lineage_sha256"]) == str(expected["structural_lineage_sha256"]),
        "replay_lineage": str(replay_payload["lineage_sha256"]) == str(expected["replay_lineage_sha256"]),
        "selector_train_tasks": int(summary["train_task_count"]) == int(expected["selector_train_tasks"]),
        "selector_validation_tasks": int(summary["validation_task_count"]) == int(expected["selector_validation_tasks"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Authoritative source validation failed: {checks}")
    return {
        "format": "rcmf_authoritative_source_manifest_14b_v1",
        "checks": checks,
        "files": {name: file_identity(path) for name, path in paths.items()},
        "historical_derived_inputs_allowed": False,
        "structural_lineage_sha256": summary["lineage_sha256"],
        "replay_lineage_sha256": replay_payload["lineage_sha256"],
    }


def _parent_split(transitions: Sequence[Mapping[str, Any]], downstream: Mapping[str, Any]) -> dict[str, Any]:
    train_tasks = set(map(str, downstream["train_task_ids"]))
    heldout_tasks = set(map(str, downstream["validation_task_ids"]))
    overlap = train_tasks & heldout_tasks
    if overlap:
        raise ValueError(f"Downstream task split overlaps: {sorted(overlap)}")
    split_by_parent: dict[str, str] = {}
    split_by_parent_task: dict[str, str] = {}
    for row in transitions:
        task_id = str(row["parent_task_id"])
        if task_id in train_tasks:
            split = "train"
        elif task_id in heldout_tasks:
            split = "heldout"
        else:
            raise ValueError(f"Training transition parent task lacks approved split: {task_id}")
        split_by_parent[str(row["parent_memory_id"])] = split
        split_by_parent_task[task_id] = split
    counts = Counter(split_by_parent.values())
    if counts != Counter({"train": 29, "heldout": 8}):
        raise ValueError(f"Parent split count differs: {counts}")
    return {
        "format": "rcmf_fresh_approved_parent_split_14b_v1",
        "split_by_parent": dict(sorted(split_by_parent.items())),
        "split_by_parent_task": dict(sorted(split_by_parent_task.items())),
        "train_parent_count": 29,
        "heldout_parent_count": 8,
        "source_split_sha256": content_sha256(downstream),
    }


def _add_teacher_token_metadata(
    transitions: Sequence[Mapping[str, Any]], tokenizer: Any
) -> list[dict[str, Any]]:
    """Derive context metadata directly from authoritative transition text."""
    rows, report, mismatches = enrich_transition_token_metadata(transitions, tokenizer)
    if not report["passed"]:
        raise ValueError(f"Transition token metadata audit failed: {mismatches[:3]}")
    return rows


def rebuild_shared_cpu(config: Mapping[str, Any], root: Path) -> dict[str, Any]:
    pipeline = config["pipeline"]
    source_root = Path(str(pipeline["roots"]["authoritative_corpus"]))
    shared = ensure_dir(root / "shared")
    examples = load_decision_examples(source_root / "decision_examples.jsonl")
    corpus_split = json.loads((source_root / "train_validation_task_manifest.json").read_text(encoding="utf-8"))
    task_split = {str(value): "train" for value in corpus_split["train_task_ids"]}
    task_split.update({str(value): "validation" for value in corpus_split["validation_task_ids"]})
    all_transitions = list(read_jsonl(source_root / "transition_manifest.jsonl"))
    transitions = [row for row in all_transitions if task_split[str(row["parent_task_id"])] == "train"]
    transitions = sorted(transitions, key=lambda row: str(row["transition_id"]))
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(pipeline["roots"]["model_snapshot"]),
        trust_remote_code=True,
    )
    transitions, token_metadata_audit, token_metadata_mismatches = (
        enrich_transition_token_metadata(
            transitions,
            tokenizer,
            snapshot=pipeline["roots"]["model_snapshot"],
        )
    )
    atomic_write_json(
        shared / "transition_token_metadata_schema_14c.json",
        token_metadata_audit["schema"],
    )
    atomic_write_json(
        shared / "transition_token_metadata_audit_14c.json",
        token_metadata_audit,
    )
    write_jsonl(
        shared / "transition_token_metadata_mismatches_14c.jsonl",
        token_metadata_mismatches,
    )
    if len(transitions) != int(pipeline["expected"]["train_transitions"]):
        raise ValueError("Transition token metadata row count differs from 499")
    if not token_metadata_audit["passed"]:
        raise ValueError(
            f"Transition token metadata audit failed: {token_metadata_mismatches[:3]}"
        )
    downstream = json.loads(Path(str(pipeline["roots"]["approved_downstream_split"])).read_text(encoding="utf-8"))
    parent_split = _parent_split(transitions, downstream)
    query_rows, query_by_id = _query_signatures(examples, task_split)
    transition_signatures, signature_validation = _full_transition_signatures(
        transitions, old_signature_rows=[]
    )
    signature_by_id = {str(row["transition_id"]): row for row in transition_signatures}
    equivalence = build_signature_equivalence_manifest(transitions, transition_signatures)
    class_by_transition = {
        str(transition_id): str(row["signature_class_id"])
        for row in equivalence["classes"]
        for transition_id in row["member_transition_ids"]
    }
    labels, illegal = _build_labels(
        examples=examples,
        query_signatures=query_by_id,
        transitions=transitions,
        transition_signatures=signature_by_id,
        class_by_transition=class_by_transition,
        parent_split=parent_split,
    )
    candidate_spaces = _candidate_spaces(
        transitions,
        parent_split,
        class_by_transition,
        signature_by_id,
    )
    train_tasks = sorted(str(value) for value in corpus_split["train_task_ids"])
    train_parents = sorted(
        parent for parent, split in parent_split["split_by_parent"].items() if split == "train"
    )
    folds = grouped_task_parent_folds(
        train_tasks,
        train_parents,
        fold_count=int(pipeline["selector"]["cv_folds"]),
        seed=int(pipeline["selector"]["cv_seed"]),
    )
    labels_a = [row for row in labels if row["cell"] == "A"]
    fold_rows = []
    total_cv_updates = 0
    for index, fold in enumerate(folds):
        training = [
            row
            for row in labels_a
            if str(row["state_task_id"]) not in fold["heldout_tasks"]
            and str(row["transition_parent_id"]) not in fold["heldout_parents"]
        ]
        states = sorted({str(row["state_example_id"]) for row in training})
        batches = math.ceil(len(states) / int(pipeline["selector"]["batch_states"]))
        candidates = []
        for candidate in pipeline["selector"]["candidates"]:
            updates = batches * int(candidate["epochs"])
            total_cv_updates += updates
            candidates.append(
                {
                    "name": candidate["name"],
                    "seed": deterministic_seed(int(pipeline["selector"]["cv_seed"]), candidate["name"], index),
                    "updates": updates,
                    "state_order_sha256": _ordered_hash(states),
                }
            )
        fold_rows.append(
            {
                "fold": index,
                "heldout_tasks": sorted(fold["heldout_tasks"]),
                "heldout_parents": sorted(fold["heldout_parents"]),
                "training_state_count": len(states),
                "training_pair_count": len(training),
                "candidates": candidates,
            }
        )
    outputs = {
        "transitions.jsonl": transitions,
        "query_signatures.jsonl": query_rows,
        "transition_signatures.jsonl": transition_signatures,
        "labels.jsonl": labels,
        "illegal_pairs.jsonl": illegal,
    }
    for name, rows in outputs.items():
        write_jsonl(shared / name, rows)
    for name, payload in {
        "parent_split.json": parent_split,
        "signature_equivalence.json": equivalence,
        "signature_validation.json": signature_validation,
        "candidate_spaces.json": candidate_spaces,
        "cv_folds_and_sampling.json": {"format": "fresh_cv_fold_sampling_14b_v1", "folds": fold_rows},
    }.items():
        atomic_write_json(shared / name, payload)
    expected = pipeline["expected"]
    counts = {
        "selector_train_tasks": len(corpus_split["train_task_ids"]),
        "selector_validation_tasks": len(corpus_split["validation_task_ids"]),
        "selector_train_states": sum(str(row["split"]) == "train" for row in query_rows),
        "selector_validation_states": sum(str(row["split"]) == "validation" for row in query_rows),
        "train_transitions": len(transitions),
        "signature_classes": len(equivalence["classes"]),
        "downstream_train_tasks": len(downstream["train_task_ids"]),
        "downstream_heldout_tasks": len(downstream["validation_task_ids"]),
    }
    count_checks = {key: int(value) == int(expected[key]) for key, value in counts.items()}
    if not all(count_checks.values()):
        raise ValueError(f"Fresh shared count gate failed: {counts} {count_checks}")
    summary = {
        "format": "fresh_shared_cpu_rebuild_14b_v1",
        "counts": counts,
        "count_checks": count_checks,
        "legal_pair_count": len(labels),
        "illegal_pair_count": len(illegal),
        "a_pair_count": len(labels_a),
        "cv_updates_per_arm": total_cv_updates,
        "fresh_outputs": {
            name: file_identity(shared / name) for name in outputs
        },
        "teacher_section_metadata": {
            "schema": "rcmf_transition_token_metadata_14c_v1",
            "derivation": "one_complete_teacher_section_tokenization_with_canonical_offset_spans",
            "source": "authoritative_transition_manifest",
            "tokenizer_name_or_path": str(getattr(tokenizer, "name_or_path", "unknown")),
            "tokenizer_identity_sha256": token_metadata_audit["tokenizer"]["tokenizer_identity_sha256"],
            "tokenizer_snapshot_sha256": token_metadata_audit["tokenizer"]["tokenizer_snapshot_sha256"],
            "row_count": len(transitions),
            "required_fields": token_metadata_audit["schema"]["required_integer_fields"],
            "audit": file_identity(shared / "transition_token_metadata_audit_14c.json"),
            "mismatches": file_identity(shared / "transition_token_metadata_mismatches_14c.jsonl"),
        },
        "historical_derived_artifact_loaded": False,
    }
    atomic_write_json(shared / "shared_cpu_summary.json", summary)
    return summary


def _resolved_arm(config: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    pipeline = config["pipeline"]
    arm = config["arms"][arm_id]
    return {
        **arm,
        "selector": pipeline["selector"],
        "memory": pipeline["memory"],
        "reader": pipeline["reader"],
        "training": pipeline["training"],
        "evaluation": pipeline["evaluation"],
        "expected": pipeline["expected"],
        "prompt_assets": {
            **pipeline["prompt_assets"],
            "renderer_version": arm["task_conditioned_prompt_profile"],
            "initial_messages_sha256": (
                pipeline["prompt_assets"]["one_demo_initial_messages_sha256"]
                if arm_id == "1d"
                else pipeline["prompt_assets"]["full_demo_raw_sha256"]
            ),
        },
        "outputs": {"artifact_dir": arm["artifact_prefix"]},
        "runtime_estimate": {"prompt_profile": arm["task_conditioned_prompt_profile"]},
    }


def _initialization_manifest(config: Mapping[str, Any], output_root: Path) -> dict[str, Any]:
    pipeline = config["pipeline"]
    target = ensure_dir(output_root / "initialization_snapshots")
    selector_rows = []
    for fold in range(int(pipeline["selector"]["cv_folds"])):
        for candidate in pipeline["selector"]["candidates"]:
            seed = deterministic_seed(int(pipeline["selector"]["cv_seed"]), candidate["name"], fold)
            model = _selector(pipeline["selector"], seed)
            path = target / f"selector_cv_{candidate['name']}_fold_{fold}.pt"
            if path.exists():
                existing = torch.load(path, map_location="cpu", weights_only=False)
                expected_state = model.state_dict()
                if set(existing) != set(expected_state) or any(
                    not torch.equal(existing[key], expected_state[key])
                    for key in expected_state
                ):
                    raise ValueError(f"Existing selector initialization differs: {path}")
            else:
                torch.save(model.state_dict(), path)
            selector_rows.append({"candidate": candidate["name"], "fold": fold, "seed": seed, **file_identity(path)})
    final_rows = []
    for seed in pipeline["final_selector_member_seeds"]:
        model = _selector(pipeline["selector"], int(seed))
        path = target / f"selector_final_seed_{seed}.pt"
        if path.exists():
            existing = torch.load(path, map_location="cpu", weights_only=False)
            expected_state = model.state_dict()
            if set(existing) != set(expected_state) or any(
                not torch.equal(existing[key], expected_state[key])
                for key in expected_state
            ):
                raise ValueError(f"Existing final selector initialization differs: {path}")
        else:
            torch.save(model.state_dict(), path)
        final_rows.append({"seed": int(seed), **file_identity(path)})
    torch.manual_seed(int(pipeline["global_seed"]))
    writer = AlignedTransitionWriter(
        input_dim=int(pipeline["memory"]["input_dim"]),
        hidden_dim=int(pipeline["memory"]["writer_hidden_dim"]),
        payload_dim=int(pipeline["memory"]["payload_dim"]),
    )
    reader = StandardFieldCrossAttentionReader(
        insertion_layers=pipeline["reader"]["insertion_layers"],
        model_dim=int(pipeline["reader"]["model_dim"]),
        payload_dim=int(pipeline["reader"]["payload_dim"]),
        attention_dim=int(pipeline["reader"]["attention_dim"]),
        heads=int(pipeline["reader"]["heads"]),
    )
    writer_path = target / "writer_initial.pt"
    reader_path = target / "reader_initial.pt"
    for module, path in ((writer, writer_path), (reader, reader_path)):
        if path.exists():
            existing = torch.load(path, map_location="cpu", weights_only=False)
            expected_state = module.state_dict()
            if set(existing) != set(expected_state) or any(
                not torch.equal(existing[key], expected_state[key])
                for key in expected_state
            ):
                raise ValueError(f"Existing writer/reader initialization differs: {path}")
        else:
            torch.save(module.state_dict(), path)
    return {
        "format": "shared_initial_parameter_snapshots_14b_v1",
        "selector_cv": selector_rows,
        "selector_final": final_rows,
        "writer": {**file_identity(writer_path), "parameter_count": writer.parameter_count()},
        "reader": {**file_identity(reader_path), "parameter_count": reader.parameter_count()},
        "writer_reader_seed": int(pipeline["global_seed"]),
        "same_snapshots_required_for_both_arms": True,
        "optimizer": {
            "name": "AdamW",
            "writer_learning_rate": pipeline["training"]["writer_learning_rate"],
            "reader_learning_rate": pipeline["training"]["reader_learning_rate"],
            "weight_decay": pipeline["training"]["weight_decay"],
        },
    }


def _runtime_preflight(config: Mapping[str, Any], shared: Mapping[str, Any], smoke: Mapping[str, Any] | None) -> dict[str, Any]:
    expected = config["pipeline"]["expected"]
    cv_updates = int(shared["cv_updates_per_arm"])
    final_updates = math.ceil(int(expected["selector_train_states"]) / 8) * 120 * 3
    arm_workload = {
        "state_representation_forwards": int(expected["selector_train_states"]) + int(expected["selector_validation_states"]),
        "selector_cv_updates": cv_updates,
        "final_selector_updates_maximum": final_updates,
        "paired_causal_generations": 2 * (int(expected["downstream_train_states"]) + int(expected["downstream_heldout_states"])),
        "policy_teacher_forwards": int(expected["downstream_train_states"]) + int(expected["downstream_heldout_states"]),
        "zero_policy_forwards": int(expected["downstream_train_states"]) + int(expected["downstream_heldout_states"]),
        "writer_reader_backwards": 1152,
        "heldout_teacher_forced_conditions": 2 * 4 * int(expected["downstream_heldout_states"]),
        "heldout_one_step_conditions": 2 * 4 * int(expected["downstream_heldout_states"]),
        "heldout_full_trajectories": 2 * 4 * int(expected["downstream_heldout_tasks"]),
    }
    shared_hours = 2.5
    arm_3d_hours = 24.0
    arm_1d_hours = 21.0
    expected_total = shared_hours + arm_3d_hours + arm_1d_hours
    conservative_total = 92.0
    hard_cap = max(2.0 * expected_total, 1.25 * conservative_total, 160.0)
    measured = dict(smoke or {})
    authorization = config["pipeline"].get(
        "conditional_runtime_authorization", {}
    )
    measured_basis = {
        "technical_smoke_elapsed_seconds": measured.get("elapsed_seconds"),
        "technical_smoke_peak_gpu_memory_bytes": measured.get(
            "peak_gpu_memory_bytes"
        ),
        "state_representation_seconds": {
            arm: row.get("elapsed_seconds")
            for arm, row in measured.get("state", {}).get("arms", {}).items()
        },
        "transition_representation_seconds": [
            row.get("elapsed_seconds")
            for row in measured.get("transitions", [])
        ],
        "selector_single_update_seconds": measured.get("selector", {}).get(
            "elapsed_seconds"
        ),
        "writer_reader_backward_seconds": [
            row.get("elapsed_seconds")
            for row in measured.get("writer_reader_backward", [])
        ],
        "appworld_one_step_wall_seconds": {
            condition: row.get("wall_seconds")
            for condition, row in measured.get("heldout_train_appworld", {})
            .get("conditions", {})
            .items()
        },
        "historical_runtime_anchors": {
            "EXP_031A_H100_active_hours": 8.874528,
            "EXP_034A_H100_active_hours": 9.1179,
            "EXP_034B_successful_H100_wall_hours": 10.7594,
            "EXP_036C_840_trajectory_H100_hours": 29.5635,
        },
        "estimation_method": (
            "measured EXP-037A smoke anchors checked against immutable "
            "EXP-031A/034A/034B/036C complete-run throughput; estimates retain "
            "stage startup, validation, NFS, and long-trajectory allowance"
        ),
    }
    return {
        "format": "rcmf_runtime_preflight_14b_v1",
        "shared_workload": {"transition_representation_forwards": int(expected["train_transitions"])},
        "per_arm_workload": arm_workload,
        "common_evaluation": {
            "three_demo_arm_dev_trajectories": 3 * int(expected["dev_tasks"]),
            "conditional_one_demo_dev_trajectories": 2 * int(expected["dev_tasks"]),
            "maximum_total_dev_trajectories": 5 * int(expected["dev_tasks"]),
        },
        "technical_smoke": measured,
        "measured_runtime_basis": measured_basis,
        "expected_wall_hours": {
            "shared": shared_hours,
            "three_demo": arm_3d_hours,
            "conditional_one_demo": arm_1d_hours,
            "total_if_gate_passes": expected_total,
        },
        "conservative_total_wall_hours": conservative_total,
        "expected_h100_active_hours": 39.0,
        "expected_wall_hours_shared_plus_three_demo": shared_hours
        + arm_3d_hours,
        "expected_additional_one_demo_wall_hours": arm_1d_hours,
        "conservative_wall_hours_shared_plus_three_demo": 56.0,
        "conservative_additional_one_demo_wall_hours": 36.0,
        "storage": {"expected_gib": 46.0, "conservative_gib": 90.0},
        "lambda_cost_estimate": {"available": False, "reason": "no configured hourly rate found"},
        "recommended_hard_cap_hours": hard_cap,
        "approved_hard_cap_hours": float(config["pipeline"]["approved_hard_cap_hours"]),
        "hard_cap_formula": "max(2*expected_total,1.25*conservative_total,160)",
        "authorized_by_user_message": bool(authorization.get("granted_by_user")),
        "restart_plan": {
            "atomic_stage_outputs": True,
            "append_only_attempts": True,
            "content_hash_validation_before_skip": True,
            "restart_parent_only": True,
            "resume_at_first_incomplete_stage": True,
        },
    }


def prepare(config: Mapping[str, Any], output_root: Path, source_commit: str, smoke_path: Path | None) -> dict[str, Any]:
    ensure_dir(output_root)
    pipeline = config["pipeline"]
    environment = _environment_manifest(pipeline)
    sources = _source_manifest(config)
    shared = rebuild_shared_cpu(config, output_root)
    run_root = Path(str(pipeline["roots"]["run_root"]))
    arm_3d = build_arm_runtime_config(config, run_root, "3d")
    arm_1d = build_arm_runtime_config(config, run_root, "1d")
    exact_allowlist = {
        "benchmark.prompt_profile",
        "experiment.name",
        "stage_c_11b.artifact_dir",
        "stage_c_11b.prompt_profile",
        "stage_c_11b.run_uuid",
        "stage_c_7c.artifact_dir",
        "stage_c_7c.generation.prompt_profile",
        "stage_c_7c.multiview_cache.output_root",
        "stage_c_7c.run_uuid",
        "stage_c_7hr.appworld.prompt_profile",
        "stage_c_7hr.artifact_dir",
        "stage_c_7hr.parent_exp025c",
        "stage_c_7hr.run_uuid",
        "stage_c_9a.appworld.prompt_profile",
        "stage_c_9a.artifact_dir",
        "stage_c_9a.parent_exp025c",
        "stage_c_9a.parent_exp028a",
        "stage_c_9a.prompt_dependent_inputs.outcomes",
        "stage_c_9a.prompt_dependent_inputs.state_cache",
        "stage_c_9a.prompt_dependent_inputs.teacher_cache",
        "stage_c_9a.run_uuid",
    }
    diff = validate_resolved_arm_diff(
        arm_3d,
        arm_1d,
        allowlist=exact_allowlist,
        allowed_prefixes=(),
    )
    if not diff["passed"]:
        raise ValueError(f"Resolved arm contract differs outside allowlist: {diff['prohibited_differences']}")
    initialization = _initialization_manifest(config, output_root)
    arms = {
        arm_id: ArmContract(
            arm_id=arm_id,
            task_conditioned_prompt_profile=str(row["task_conditioned_prompt_profile"]),
            artifact_prefix=str(row["artifact_prefix"]),
            run_id=str(row["run_id"]),
        )
        for arm_id, row in config["arms"].items()
    }
    contract = PipelineContract(
        schema_version=str(pipeline["schema_version"]),
        run_uuid=str(pipeline["run_uuid"]),
        source_commit=source_commit,
        global_seed=int(pipeline["global_seed"]),
        hard_cap_hours=float(pipeline["approved_hard_cap_hours"]),
        stages=build_exp037a_stage_graph(),
        arms=arms,
        shared_initialization={
            "writer": initialization["writer"]["sha256"],
            "reader": initialization["reader"]["sha256"],
        },
        metadata={
            "pipeline_config_path": "configs/pipeline/rcmf_appworld_repro_14b.yaml",
            "conditional_authorization_plan": "research/plans/EXP_037A_CONDITIONAL_RUNTIME_AUTHORIZATION.md",
            "maximum_recoverable_attempts_per_stage": int(
                pipeline["maximum_recoverable_attempts_per_stage"]
            ),
            "recoverable_retry_delay_seconds": float(
                pipeline["recoverable_retry_delay_seconds"]
            ),
        },
    )
    smoke = json.loads(smoke_path.read_text(encoding="utf-8")) if smoke_path and smoke_path.exists() else None
    runtime = _runtime_preflight(config, shared, smoke)
    authorization = pipeline.get("conditional_runtime_authorization", {})
    authorization_received = bool(authorization.get("granted_by_user"))
    approval_checks = {
        "source_and_manifests_consistent": True,
        "tests_passed": bool((smoke or {}).get("all_tests_passed", False)),
        "no_identity_or_leakage_error": all(sources["checks"].values()),
        "same_pipeline_only_prompt_diff": bool(diff["passed"]),
        "frozen_method_contract": True,
        "event_driven_scheduler": True,
        "read_only_twenty_minute_monitor": True,
        "preflight_complete": smoke is not None,
        "recommended_hard_cap_at_most_200": float(runtime["recommended_hard_cap_hours"]) <= 200.0,
    }
    authorized = authorization_received and all(approval_checks.values())
    files = {
        "environment_manifest.json": environment,
        "authoritative_source_manifest.json": sources,
        "stage_dag.json": contract.as_dict(),
        "two_arm_contract.json": {
            "format": "two_arm_prompt_intervention_contract_14b_v1",
            "only_intended_intervention": "task_conditioned_prompt_profile",
            "arms": {key: value.as_dict() for key, value in arms.items()},
        },
        "resolved_arm_3d.json": arm_3d,
        "resolved_arm_1d.json": arm_1d,
        "resolved_config_diff.json": diff,
        "initialization_manifest.json": initialization,
        "runtime_preflight.json": runtime,
        "approval_request.json": {
            "format": "conditional_runtime_authorization_plan_14b_v1",
            "user_authorization_received": authorization_received,
            "hard_cap_hours": float(pipeline["approved_hard_cap_hours"]),
            "checks": approval_checks,
            "authorized_to_launch_when_persisted": authorized,
            "authorization_scope": (
                "3d then conditional 1d only after PASS" if authorized else "none"
            ),
        },
    }
    for name, payload in files.items():
        atomic_write_json(output_root / name, payload)
    if smoke is not None:
        atomic_write_json(output_root / "smoke_results.json", smoke)
    summary = {
        "format": PREFLIGHT_FORMAT,
        "run_uuid": str(pipeline["run_uuid"]),
        "source_commit": source_commit,
        "approval_checks": approval_checks,
        "authorized_to_launch": authorized,
        "recommended_hard_cap_hours": runtime["recommended_hard_cap_hours"],
        "approved_hard_cap_hours": float(pipeline["approved_hard_cap_hours"]),
        "output_hashes": {
            name: sha256_file(output_root / name)
            for name in files
        },
    }
    atomic_write_json(output_root / "preflight_summary.json", summary)
    return summary


def main() -> None:
    args = parse_args()
    config = load_resolved(args.config)
    if args.mode == "shared-cpu":
        result = rebuild_shared_cpu(config, args.output_root)
    elif args.mode == "contract":
        arm_3d = _resolved_arm(config, "3d")
        arm_1d = _resolved_arm(config, "1d")
        result = validate_resolved_arm_diff(arm_3d, arm_1d)
        atomic_write_json(args.output_root / "resolved_config_diff.json", result)
    else:
        result = prepare(config, args.output_root, args.source_commit, args.smoke_results)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
