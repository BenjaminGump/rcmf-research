from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import build_appworld_messages
from rcmf.config import load_config
from rcmf.model.backends.base import GenerateOutput
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    load_decision_examples,
)
from rcmf.training.fixed_memory_reader_8a import (
    GLOBAL_SEED,
    LAYER_INDICES,
    TOKEN_COUNT,
    FixedMemoryReader,
    FixedMemoryReaderHooks,
    reader_behavior_classification,
    select_reader_checkpoint,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.collect_fixed_memory_reader_on_policy_8a import (
    _paired_messages,
    _prepare_on_policy,
)
from scripts.run_deep_residual_carrier_7e import (
    _attention_context,
    _bare_target_forward,
    _selected_indices,
)
from scripts.run_deep_residual_compiler_7f import _build_model
from scripts.run_procedural_causal_audit_7b import LiveBridgeClient
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_fast_7df import _build_backend
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss


CHECKPOINT_FORMAT = "fixed_memory_reader_checkpoint_8a_v1"
VALIDATION_FORMAT = "fixed_memory_reader_heldout_live_validation_8a_v1"
CONTROL_NAMES = ("R1_correct", "R2_transition_shuffle", "R3_state_shuffle", "R0_zero")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_fixed_memory_reader_8a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("implementation", "train", "validate", "select"), required=True
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp029a_reader")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_g = Path(str(settings["parent_exp027b"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "reader"
    return {
        "preflight": artifact_dir / "runtime_preflight.json",
        "collection": artifact_dir / "on_policy/frozen_state_manifest.json",
        "state_tensors": artifact_dir / "on_policy/state_representations.pt",
        "paired_summary": artifact_dir / "paired_outcomes/summary.json",
        "paired_rows": artifact_dir / "paired_outcomes/rows.jsonl",
        "condition_results": artifact_dir / "paired_outcomes/condition_results",
        "parent_training": parent_g / "compiler/pairmlp/training_summary.json",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "state_cache": parent_c
        / "representation_cache/multiview/state_multiview.pt",
        "exp028a_outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "exp028a_teacher_cache": parent_a
        / "structured_compiler/policy_teacher_cache.pt",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "decisions": corpus / "decision_examples.jsonl",
        "implementation": root / "implementation_validation.json",
        "augmentation_manifest": root / "expert_positive_augmentation.json",
        "implementation_report": root / "implementation_validation.md",
        "mismatches": root / "mismatch_manifest.json",
        "training_units": root / "training_unit_manifest.json",
        "tokenized_cache": root / "tokenized_rows.pt",
        "checkpoints": root / "checkpoints",
        "latest_checkpoint": root / "latest_checkpoint.json",
        "training_summary": root / "training_summary.json",
        "validation_root": root / "validation",
        "selection": root / "checkpoint_selection.json",
        "selection_report": root / "validation_report.md",
        "semantic_module": Path(str(settings["appworld"]["semantic_module"])),
        "one_step_bridge": Path(str(settings["appworld"]["one_step_bridge_script"])),
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-029A reader input: {missing}")


def _condition_result_path(
    paths: Mapping[str, Path], state_id: str, condition: str
) -> Path:
    return paths["condition_results"] / f"{sha256_text(f'{state_id}::{condition}')}.json"


def _validation_path(
    paths: Mapping[str, Path], updates: int, state_id: str, control: str
) -> Path:
    return (
        paths["validation_root"]
        / f"u{updates:02d}/condition_results"
        / f"{sha256_text(f'{state_id}::{control}')}.json"
    )


def _load_data(
    paths: Mapping[str, Path], settings: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = _json(paths["collection"])
    states = {str(row["state_id"]): dict(row) for row in manifest["states"]}
    if len(states) != int(manifest["state_count"]):
        raise ValueError("Frozen on-policy manifest contains duplicate state IDs")
    tensor_payload = torch.load(
        paths["state_tensors"], map_location="cpu", weights_only=False
    )
    if list(tensor_payload["ordered_state_ids"]) != list(states):
        raise ValueError("On-policy state tensor order differs from frozen manifest")
    state_values = tensor_payload["values"].to(torch.float32)
    paired = {str(row["state_id"]): row for row in _rows(paths["paired_rows"])}
    if set(paired) != set(states):
        raise ValueError("Paired outcome rows differ from frozen on-policy states")

    transition_payload = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    transition_values = transition_payload["representations"]["final_layer"].to(
        torch.float32
    )
    transition_position = {
        str(value): index
        for index, value in enumerate(transition_payload["ordered_ids"])
    }
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    if set(transition_position) != set(transitions):
        raise ValueError("Transition cache differs from clean transition ledger")

    minimum_positive = int(
        settings["collection"]["minimum_model_train_positive_states"]
    )
    positive_count = sum(
        str(state["model_split"]) == "model_train"
        and str(paired[state_id]["label"]) == "POSITIVE"
        for state_id, state in states.items()
    )
    needed = max(0, minimum_positive - positive_count)
    selected_experts: list[dict[str, Any]] = []
    if needed:
        outcomes = _json(paths["exp028a_outcomes"])
        expert_teacher = torch.load(
            paths["exp028a_teacher_cache"], map_location="cpu", weights_only=False
        )
        state_cache = torch.load(
            paths["state_cache"], map_location="cpu", weights_only=False
        )
        cache_position = {
            str(value): index for index, value in enumerate(state_cache["ordered_ids"])
        }
        cache_values = state_cache["representations"]["final_layer"].to(
            torch.float32
        )
        model_train_tasks = {
            str(state["task_id"])
            for state in states.values()
            if str(state["model_split"]) == "model_train"
        }
        candidates = [
            dict(row)
            for row in outcomes["rows"]
            if str(row["model_split"]) == "model_train"
            and str(row["label"]) == "POSITIVE"
            and str(row["state_task_id"]) in model_train_tasks
            and str(row["state_example_id"]) in cache_position
            and str(row["state_example_id"]) in expert_teacher["teacher_rows"]
            and str(row["selected_transition_id"]) in transition_position
        ]
        candidates.sort(
            key=lambda row: stable_key(
                GLOBAL_SEED,
                "8a-expert-positive-augmentation",
                row["state_example_id"],
            )
        )
        if len(candidates) < needed:
            raise RuntimeError(
                "EXP-028A does not contain enough clean model-train positive expert states"
            )
        extra_tensors = []
        for row in candidates[:needed]:
            source_id = str(row["state_example_id"])
            state_id = f"expert::{source_id}"
            if state_id in states:
                raise ValueError(f"Duplicate expert augmentation state: {state_id}")
            state = {
                "format": "fixed_memory_reader_expert_augmentation_state_8a_v1",
                "state_id": state_id,
                "source_state_example_id": source_id,
                "task_id": str(row["state_task_id"]),
                "step_id": int(row["state_step_id"]),
                "model_split": "model_train",
                "selected_transition_id": str(row["selected_transition_id"]),
                "selected_signature_class_id": str(row["selected_class_id"]),
                "augmentation_source": "exp028a_clean_positive_expert_state",
                "outcome_used_only_after_on_policy_panel_was_frozen": True,
                "test_normal_outcome_used": False,
            }
            states[state_id] = state
            paired[state_id] = {
                "format": "fixed_memory_reader_expert_augmentation_outcome_8a_v1",
                "state_id": state_id,
                "task_id": state["task_id"],
                "model_split": "model_train",
                "selected_transition_id": state["selected_transition_id"],
                "selected_signature_class_id": state[
                    "selected_signature_class_id"
                ],
                "label": "POSITIVE",
                "source_state_example_id": source_id,
                "source_paired_outcomes_sha256": sha256_file(
                    paths["exp028a_outcomes"]
                ),
            }
            extra_tensors.append(cache_values[cache_position[source_id]])
            selected_experts.append(state)
        state_values = torch.cat((state_values, torch.stack(extra_tensors)), dim=0)

    state_position = {
        state_id: index for index, state_id in enumerate(states)
    }
    augmentation = {
        "format": "fixed_memory_reader_expert_positive_augmentation_8a_v1",
        "required": bool(needed),
        "on_policy_model_train_positive_count": positive_count,
        "minimum_positive_count": minimum_positive,
        "added_count": len(selected_experts),
        "selected_expert_state_ids": [
            str(row["state_id"]) for row in selected_experts
        ],
        "source_state_example_ids": [
            str(row["source_state_example_id"]) for row in selected_experts
        ],
        "exp028a_outcomes_sha256": sha256_file(paths["exp028a_outcomes"]),
        "exp028a_teacher_cache_sha256": sha256_file(
            paths["exp028a_teacher_cache"]
        ),
        "state_cache_sha256": sha256_file(paths["state_cache"]),
        "validation_expert_count": 0,
        "test_normal_outcome_count": 0,
    }
    augmentation["manifest_sha256"] = canonical_sha256(augmentation)
    atomic_write_json(paths["augmentation_manifest"], augmentation)
    return {
        "manifest": manifest,
        "states": states,
        "paired": paired,
        "state_values": state_values,
        "state_position": state_position,
        "transition_values": transition_values,
        "transition_position": transition_position,
        "transition_view_names": list(transition_payload["view_names"]),
        "transitions": transitions,
        "augmentation": augmentation,
    }

def _build_pairmlp_reader(
    *,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    data: Mapping[str, Any],
    device: torch.device,
    checkpoint: Mapping[str, Any] | None = None,
) -> tuple[nn.Module, FixedMemoryReader, Path]:
    parent_summary = _json(paths["parent_training"])
    parent_checkpoint = Path(str(parent_summary["selected_checkpoint"]))
    if sha256_file(parent_checkpoint) != str(
        settings["expected_pairmlp_checkpoint_sha256"]
    ):
        raise ValueError("EXP-027B PairMLP checkpoint hash differs")
    model = _build_model(
        kind="pairmlp",
        settings=settings,
        view_names=data["transition_view_names"],
        device=device,
    )
    parent = torch.load(parent_checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(parent["model_state_dict"])
    reader = FixedMemoryReader(
        model_dim=int(settings["compiler"]["representation_dim"]),
        latent_dim=int(settings["reader"]["latent_dim"]),
        bottleneck=int(settings["reader"]["bottleneck_dim"]),
        layer_count=len(settings["reader"]["selected_layer_indices"]),
    ).to(device)
    if checkpoint is not None:
        model.load_state_dict(checkpoint["model_state_dict"])
        reader.load_state_dict(checkpoint["reader_state_dict"])
    return model, reader, parent_checkpoint


def _target_ids(tokenizer: Any, target_action: str) -> list[int]:
    target = str(target_action)
    if "```" not in target:
        target = f"```python\n{target.strip()}\n```"
    values = [int(value) for value in tokenizer(target, add_special_tokens=False)["input_ids"]]
    eos = getattr(tokenizer, "eos_token_id", None)
    if eos is not None and (not values or values[-1] != int(eos)):
        values.append(int(eos))
    return values


def _tokenized_row(
    *,
    backend: Any,
    state: Mapping[str, Any],
    result: Mapping[str, Any],
    teacher_ids: Sequence[int],
    role: str,
) -> dict[str, Any]:
    messages = _paired_messages(
        state=state,
        actual_observations=list(result["actual_replay_observations"]),
        prompt_profile="full_demo",
    )
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    prompt_ids = [int(value) for value in tokenized.input_ids[0].cpu()]
    generated = [int(value) for value in teacher_ids]
    full = prompt_ids + generated
    return {
        "pair_id": f"{state['state_id']}::{role}",
        "state_id": str(state["state_id"]),
        "task_id": str(state["task_id"]),
        "input_ids": full,
        "labels": [-100] * len(prompt_ids) + generated,
        "pad_token_id": int(backend.tokenizer.pad_token_id),
        "last_user_token_indices": [
            int(value)
            for value in tokenized.metadata["last_user_token_indices"]
        ],
        "target_len": len(generated),
        "prompt_len": len(prompt_ids),
        "student_prompt_sha256": sha256_text(str(tokenized.metadata["text"])),
        "student_prompt_contains_raw_transition": False,
        "response_cache": {},
    }


def _tokenized_data(
    *, backend: Any, paths: Mapping[str, Path], data: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    if paths["tokenized_cache"].exists():
        payload = torch.load(
            paths["tokenized_cache"], map_location="cpu", weights_only=False
        )
        if list(payload["ordered_state_ids"]) != list(data["states"]):
            raise ValueError("Reader tokenized cache state order differs")
        return dict(payload["rows"])
    expert_teacher = None
    if any(
        str(state.get("augmentation_source", ""))
        == "exp028a_clean_positive_expert_state"
        for state in data["states"].values()
    ):
        expert_teacher = torch.load(
            paths["exp028a_teacher_cache"], map_location="cpu", weights_only=False
        )
    rows = {}
    for state_id, state in data["states"].items():
        if str(state.get("augmentation_source", "")) == (
            "exp028a_clean_positive_expert_state"
        ):
            if expert_teacher is None:
                raise RuntimeError("EXP-028A expert teacher cache was not loaded")
            source_id = str(state["source_state_example_id"])
            teacher = expert_teacher["teacher_rows"][source_id]
            policy = expert_teacher["policy_rows"][source_id]
            ground_truth = dict(expert_teacher["ground_truth_rows"][source_id])
            raw_policy = dict(policy["raw"])
            bare_policy = dict(policy["bare"])
            for role, value in (
                ("raw_policy", raw_policy),
                ("bare_policy", bare_policy),
                ("ground_truth", ground_truth),
            ):
                value["pair_id"] = f"{state_id}::{role}"
                value["state_id"] = state_id
                value["task_id"] = str(state["task_id"])
                value["student_prompt_contains_raw_transition"] = False
            rows[state_id] = {
                "policy": raw_policy,
                "raw_policy": raw_policy,
                "ground_truth": ground_truth,
                "bare_policy": bare_policy,
                "target_teacher": dict(teacher["raw"]),
                "raw_teacher": dict(teacher["raw"]),
                "bare_teacher": dict(teacher["bare"]),
                "target_condition": "EXP028A_T1_selected_raw",
                "augmentation_source": "exp028a_clean_positive_expert_state",
            }
            continue
        paired = data["paired"][state_id]
        bare = _json(Path(str(paired["bare_result_path"])))
        raw = _json(Path(str(paired["raw_result_path"])))
        target = raw if str(paired["label"]) == "POSITIVE" else bare
        policy = _tokenized_row(
            backend=backend,
            state=state,
            result=target,
            teacher_ids=target["policy_teacher"]["generated_token_ids"],
            role="policy",
        )
        raw_policy = _tokenized_row(
            backend=backend,
            state=state,
            result=raw,
            teacher_ids=raw["policy_teacher"]["generated_token_ids"],
            role="raw_policy",
        )
        ground_truth = _tokenized_row(
            backend=backend,
            state=state,
            result=target,
            teacher_ids=_target_ids(backend.tokenizer, str(state["target_action"])),
            role="ground_truth",
        )
        bare_policy = _tokenized_row(
            backend=backend,
            state=state,
            result=bare,
            teacher_ids=bare["policy_teacher"]["generated_token_ids"],
            role="bare_policy",
        )
        if min(
            len(policy["last_user_token_indices"]),
            len(raw_policy["last_user_token_indices"]),
            len(ground_truth["last_user_token_indices"]),
            len(bare_policy["last_user_token_indices"]),
        ) < TOKEN_COUNT:
            raise ValueError(f"State lacks four eligible reader tokens: {state_id}")
        rows[state_id] = {
            "policy": policy,
            "raw_policy": raw_policy,
            "ground_truth": ground_truth,
            "bare_policy": bare_policy,
            "target_teacher": dict(target["policy_teacher"]),
            "raw_teacher": dict(raw["policy_teacher"]),
            "bare_teacher": dict(bare["policy_teacher"]),
            "target_condition": str(target["condition"]),
            "augmentation_source": None,
        }
    atomic_torch_save(
        {
            "format": "fixed_memory_reader_tokenized_rows_8a_v2",
            "ordered_state_ids": list(data["states"]),
            "rows": rows,
            "student_prompt_contains_raw_transition": False,
            "raw_policy_kl_supported": True,
        },
        paths["tokenized_cache"],
    )
    return rows

def _mismatch_manifest(
    data: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    if paths["mismatches"].exists():
        return _json(paths["mismatches"])
    train = [
        state
        for state in data["states"].values()
        if str(state["model_split"]) == "model_train"
    ]
    validation = [
        state
        for state in data["states"].values()
        if str(state["model_split"]) == "heldout_train_validation"
    ]
    output = []
    for split, rows in (("model_train", train), ("heldout_train_validation", validation)):
        for state in rows:
            transition_candidates = [
                other
                for other in rows
                if str(other["selected_transition_id"])
                != str(state["selected_transition_id"])
                and str(other["selected_signature_class_id"])
                != str(state["selected_signature_class_id"])
            ]
            if not transition_candidates:
                transition_candidates = [
                    other
                    for other in rows
                    if str(other["selected_transition_id"])
                    != str(state["selected_transition_id"])
                ]
            state_candidates = [
                other
                for other in rows
                if str(other["task_id"]) != str(state["task_id"])
            ]
            if not transition_candidates or not state_candidates:
                raise ValueError(f"Cannot construct reader mismatches: {state['state_id']}")
            transition = min(
                transition_candidates,
                key=lambda other: stable_key(
                    GLOBAL_SEED,
                    "8a-transition-mismatch",
                    state["state_id"],
                    other["selected_transition_id"],
                ),
            )
            state_mismatch = min(
                state_candidates,
                key=lambda other: stable_key(
                    GLOBAL_SEED,
                    "8a-state-mismatch",
                    state["state_id"],
                    other["state_id"],
                ),
            )
            output.append(
                {
                    "state_id": str(state["state_id"]),
                    "model_split": split,
                    "transition_mismatch_transition_id": str(
                        transition["selected_transition_id"]
                    ),
                    "transition_signature_differs": str(
                        transition["selected_signature_class_id"]
                    )
                    != str(state["selected_signature_class_id"]),
                    "state_mismatch_state_id": str(state_mismatch["state_id"]),
                    "state_task_differs": str(state_mismatch["task_id"])
                    != str(state["task_id"]),
                    "outcomes_used": False,
                }
            )
    manifest = {
        "format": "fixed_memory_reader_mismatch_manifest_8a_v1",
        "global_seed": GLOBAL_SEED,
        "row_count": len(output),
        "rows": output,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(paths["mismatches"], manifest)
    return manifest


def _training_units(
    data: Mapping[str, Any], mismatch: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    if paths["training_units"].exists():
        return _json(paths["training_units"])
    controls = {str(row["state_id"]): row for row in mismatch["rows"]}
    units = []
    for state_id, state in data["states"].items():
        if str(state["model_split"]) != "model_train":
            continue
        label = str(data["paired"][state_id]["label"])
        units.append(
            {
                "unit_id": f"{state_id}::correct",
                "query_state_id": state_id,
                "program_state_id": state_id,
                "program_transition_id": str(state["selected_transition_id"]),
                "role": "correct",
                "label": label,
                "target": "raw" if label == "POSITIVE" else "bare",
                "balance_group": "positive" if label == "POSITIVE" else "bare",
            }
        )
        if label == "POSITIVE":
            units.extend(
                [
                    {
                        "unit_id": f"{state_id}::transition_mismatch",
                        "query_state_id": state_id,
                        "program_state_id": state_id,
                        "program_transition_id": str(
                            controls[state_id]["transition_mismatch_transition_id"]
                        ),
                        "role": "transition_mismatch",
                        "label": label,
                        "target": "bare",
                        "balance_group": "bare",
                    },
                    {
                        "unit_id": f"{state_id}::state_mismatch",
                        "query_state_id": state_id,
                        "program_state_id": str(
                            controls[state_id]["state_mismatch_state_id"]
                        ),
                        "program_transition_id": str(state["selected_transition_id"]),
                        "role": "state_mismatch",
                        "label": label,
                        "target": "bare",
                        "balance_group": "bare",
                    },
                ]
            )
    group_counts = Counter(str(row["balance_group"]) for row in units)
    if not group_counts["positive"] or not group_counts["bare"]:
        raise ValueError("Reader class balancing requires positive and bare groups")
    for row in units:
        row["weight"] = len(units) / (
            2.0 * group_counts[str(row["balance_group"])]
        )
    manifest = {
        "format": "fixed_memory_reader_training_unit_manifest_8a_v1",
        "global_seed": GLOBAL_SEED,
        "unit_count": len(units),
        "state_count": len({str(row["query_state_id"]) for row in units}),
        "role_counts": dict(sorted(Counter(str(row["role"]) for row in units).items())),
        "balance_group_counts": dict(sorted(group_counts.items())),
        "balance_group_total_weights": {
            group: sum(
                float(row["weight"])
                for row in units
                if str(row["balance_group"]) == group
            )
            for group in sorted(group_counts)
        },
        "units": units,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(paths["training_units"], manifest)
    return manifest


def _reader_hooks(
    *,
    backend: Any,
    reader: FixedMemoryReader,
    batch: Mapping[str, Any],
    latent: Tensor,
    maximum_ratio: float,
) -> FixedMemoryReaderHooks:
    return FixedMemoryReaderHooks(
        model=backend.model,
        reader=reader,
        layer_indices=LAYER_INDICES,
        selected_token_indices=_selected_indices(batch),
        latent=latent,
        expected_prefill_length=int(batch["input_ids"].shape[1]),
        maximum_layer_ratio=maximum_ratio,
    )


def _reader_forward(
    *,
    backend: Any,
    reader: FixedMemoryReader,
    batch: Mapping[str, Any],
    latent: Tensor,
    maximum_ratio: float,
) -> tuple[Tensor, Tensor, FixedMemoryReaderHooks]:
    hooks = _reader_hooks(
        backend=backend,
        reader=reader,
        batch=batch,
        latent=latent,
        maximum_ratio=maximum_ratio,
    )
    with hooks:
        loss, logits = _bare_target_forward(backend=backend, batch=batch)
    return loss, logits, hooks


def _reader_backward_unit(
    *,
    backend: Any,
    reader: FixedMemoryReader,
    batch: Mapping[str, Any],
    latent: Tensor,
    maximum_ratio: float,
    policy_length: int,
    teacher: Mapping[str, Any],
    ground_truth_ids: Tensor | None,
    unit_weight: float,
    training: Mapping[str, Any],
) -> dict[str, Any]:
    hooks = _reader_hooks(
        backend=backend,
        reader=reader,
        batch=batch,
        latent=latent,
        maximum_ratio=maximum_ratio,
    )
    # Hooks must remain installed through activation-checkpoint recomputation.
    with hooks:
        _, logits = _bare_target_forward(backend=backend, batch=batch)
        policy_kl, terms = _policy_loss(logits[:policy_length], teacher)
        ground_truth_ce = torch.zeros((), device=backend.device)
        if ground_truth_ids is not None:
            ground_truth_ce = F.cross_entropy(
                logits[policy_length:].to(torch.float32), ground_truth_ids
            )
        residual_penalty = torch.stack(
            [
                value.to(torch.float32).pow(2).mean()
                for value in hooks.applied_deltas.values()
            ]
        ).mean()
        loss = float(unit_weight) * (
            float(training["policy_kl_weight"]) * policy_kl
            + float(training["teacher_token_ce_weight"])
            * terms["teacher_token_ce"]
            + float(training["ground_truth_ce_weight"]) * ground_truth_ce
            + float(training["residual_norm_weight"]) * residual_penalty
            + float(training["latent_norm_weight"])
            * latent.to(torch.float32).pow(2).mean()
        )
        loss.backward()
    return {
        "loss": loss,
        "policy_kl": policy_kl,
        "teacher_token_ce": terms["teacher_token_ce"],
        "ground_truth_ce": ground_truth_ce,
        "hooks": hooks,
    }

def _checkpoint_payload(
    *,
    model: nn.Module,
    reader: FixedMemoryReader,
    optimizer: torch.optim.Optimizer,
    unit_ids: Sequence[str],
    update_counts: Sequence[int],
    completed_rounds: int,
    history: Sequence[Mapping[str, Any]],
    parent_checkpoint_sha256: str,
    unit_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "format": CHECKPOINT_FORMAT,
        "global_seed": GLOBAL_SEED,
        "completed_rounds": int(completed_rounds),
        "unit_ids": list(unit_ids),
        "update_counts": [int(value) for value in update_counts],
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "reader_state_dict": {
            key: value.detach().cpu() for key, value in reader.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "history": list(history),
        "parent_checkpoint_sha256": parent_checkpoint_sha256,
        "training_unit_manifest_sha256": unit_manifest_sha256,
        "model_sha256": module_state_sha256(model),
        "reader_sha256": module_state_sha256(reader),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all()
        if torch.cuda.is_available()
        else [],
    }


def _implementation_validation(
    *, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    backend = _build_backend(cfg)
    data = _load_data(paths, settings)
    model, reader, parent_checkpoint = _build_pairmlp_reader(
        settings=settings, paths=paths, data=data, device=backend.device
    )
    model.eval()
    reader.eval()
    examples = load_decision_examples(paths["decisions"])
    candidates = []
    for index, example in enumerate(examples):
        if int(example.step_id) != 1:
            continue
        messages = _appworld_messages_from_example(example, "full_demo")
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        if len(tokenized.metadata["last_user_token_indices"]) < TOKEN_COUNT:
            continue
        candidates.append((int(tokenized.attention_mask.sum()), index, messages, example))
    candidates.sort(key=lambda row: (row[0], row[1]))
    positions = [0, len(candidates) // 2, max(0, len(candidates) - 2), len(candidates) - 1]
    if len(set(positions)) != 4:
        raise ValueError("Could not select four implementation-smoke states")
    checks = []
    for ordinal, position in enumerate(positions):
        _, _, messages, example = candidates[position]
        state = next(iter(data["state_values"])).unsqueeze(0).to(backend.device)
        transition = data["transition_values"][0].unsqueeze(0).to(backend.device)
        with torch.no_grad():
            latent = model(state, transition)
        bare = backend.generate(messages=messages, max_new_tokens=64, temperature=0.0, top_p=1.0)
        generated, hook = _generate_reader(
            backend=backend,
            messages=messages,
            reader=reader,
            latent=latent,
            max_new_tokens=64,
            maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
        )
        policy_row = _tokenized_row(
            backend=backend,
            state={"state_id": f"smoke-{ordinal}", "task_id": "smoke", "history": [], "task_message": messages[-1]["content"]},
            result={"actual_replay_observations": []},
            teacher_ids=_target_ids(backend.tokenizer, str(example.target_text)),
            role="smoke",
        )
        batch = _collate([policy_row], device=backend.device, k=TOKEN_COUNT)
        with torch.no_grad():
            bare_loss, bare_logits = _bare_target_forward(backend=backend, batch=batch)
            zero_loss, zero_logits, zero_hooks = _reader_forward(
                backend=backend,
                reader=reader,
                batch=batch,
                latent=latent,
                maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
            )
        checks.append(
            {
                "ordinal": ordinal,
                "prompt_tokens": int(candidates[position][0]),
                "generation_exact": bare.token_ids == generated.token_ids and bare.text == generated.text,
                "logits_close": bool(torch.allclose(bare_logits, zero_logits, atol=2e-4, rtol=2e-4)),
                "nll_close": math.isclose(float(bare_loss), float(zero_loss), rel_tol=2e-4, abs_tol=2e-4),
                "hook": hook,
                "teacher_forward_hook": zero_hooks.audit.as_dict(),
            }
        )
    # A zero output head receives gradients at every selected layer on the first update.
    sample = data["manifest"]["states"][0]
    paired = data["paired"][str(sample["state_id"])]
    result = _json(Path(str(paired["bare_result_path"])))
    row = _tokenized_row(
        backend=backend,
        state=sample,
        result=result,
        teacher_ids=result["policy_teacher"]["generated_token_ids"],
        role="gradient",
    )
    batch = _collate([row], device=backend.device, k=TOKEN_COUNT)
    state_tensor = data["state_values"][data["state_position"][str(sample["state_id"])]]
    transition_tensor = data["transition_values"][data["transition_position"][str(sample["selected_transition_id"])]]
    model.train()
    reader.train()
    latent = model(
        state_tensor.unsqueeze(0).to(backend.device),
        transition_tensor.unsqueeze(0).to(backend.device),
    )
    gradient_hooks = _reader_hooks(
        backend=backend,
        reader=reader,
        batch=batch,
        latent=latent,
        maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
    )
    # Keep hooks active through activation-checkpoint recomputation.
    with gradient_hooks:
        _, logits = _bare_target_forward(backend=backend, batch=batch)
        loss, _ = _policy_loss(logits, result["policy_teacher"])
        loss.backward()
    output_gradients = [
        float(layer.output.weight.grad.norm().cpu())
        if layer.output.weight.grad is not None
        else 0.0
        for layer in reader.layers
    ]
    qwen_gradients = sum(
        parameter.grad is not None for parameter in backend.model.parameters()
    )
    report = {
        "format": "fixed_memory_reader_implementation_validation_8a_v1",
        "global_seed": GLOBAL_SEED,
        "selected_layer_indices": list(LAYER_INDICES),
        "token_count": TOKEN_COUNT,
        "reader_bottleneck": reader.bottleneck,
        "reader_parameter_count": reader.parameter_count(),
        "fixed_size_independent_of_memory_count": True,
        "z_zero_exact": all(
            torch.equal(
                reader.layer_delta(
                    slot,
                    torch.randn(1, TOKEN_COUNT, reader.model_dim, device=backend.device),
                    torch.zeros(1, reader.latent_dim, device=backend.device),
                ),
                torch.zeros(1, TOKEN_COUNT, reader.model_dim, device=backend.device),
            )
            for slot in range(reader.layer_count)
        ),
        "four_state_checks": checks,
        "reader_output_gradient_norms": output_gradients,
        "all_reader_layers_receive_gradient": all(value > 0.0 for value in output_gradients),
        "qwen_requires_grad_count": sum(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "qwen_gradient_count": qwen_gradients,
        "student_prompt_contains_raw_transition": False,
        "parent_pairmlp_checkpoint": str(parent_checkpoint),
        "parent_pairmlp_checkpoint_sha256": sha256_file(parent_checkpoint),
        "gradient_hook": gradient_hooks.audit.as_dict(),
    }
    report["passed"] = bool(
        report["z_zero_exact"]
        and report["all_reader_layers_receive_gradient"]
        and report["qwen_requires_grad_count"] == 0
        and report["qwen_gradient_count"] == 0
        and all(
            row["generation_exact"]
            and row["logits_close"]
            and row["nll_close"]
            for row in checks
        )
    )
    atomic_write_json(paths["implementation"], report)
    atomic_write_text(
        paths["implementation_report"],
        "\n".join(
            [
                "# EXP-029A fixed-reader implementation validation",
                "",
                f"- reader parameters: `{report['reader_parameter_count']}`",
                f"- zero equivalence, four states: `{str(all(row['generation_exact'] and row['logits_close'] and row['nll_close'] for row in checks)).lower()}`",
                f"- z=0 exact zero: `{str(report['z_zero_exact']).lower()}`",
                f"- all four reader layers receive gradients: `{str(report['all_reader_layers_receive_gradient']).lower()}`",
                f"- Qwen trainable parameters/gradients: `{report['qwen_requires_grad_count']}/{report['qwen_gradient_count']}`",
                f"- passed: `{str(report['passed']).lower()}`",
                "",
            ]
        ),
    )
    return report


def _train(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    if not bool(_json(paths["implementation"])["passed"]):
        raise RuntimeError("Fixed-reader implementation validation did not pass")
    backend = _build_backend(cfg)
    data = _load_data(paths, settings)
    tokenized = _tokenized_data(backend=backend, paths=paths, data=data)
    mismatch = _mismatch_manifest(data, paths)
    units_manifest = _training_units(data, mismatch, paths)
    units = list(units_manifest["units"])
    unit_ids = [str(row["unit_id"]) for row in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("Training units contain duplicate IDs")
    seed_everything(GLOBAL_SEED)
    latest = None
    if paths["latest_checkpoint"].exists():
        latest_row = _json(paths["latest_checkpoint"])
        latest_path = Path(str(latest_row["checkpoint"]))
        latest = torch.load(latest_path, map_location=backend.device, weights_only=False)
        if latest["format"] != CHECKPOINT_FORMAT or list(latest["unit_ids"]) != unit_ids:
            raise ValueError("Reader resume checkpoint identity differs")
    model, reader, parent_checkpoint = _build_pairmlp_reader(
        settings=settings,
        paths=paths,
        data=data,
        device=backend.device,
        checkpoint=latest,
    )
    model.train()
    reader.train()
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    for parameter in reader.parameters():
        parameter.requires_grad_(True)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen became trainable")
    if hasattr(backend.model, "gradient_checkpointing_enable"):
        backend.model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )
    backend.model.config.use_cache = False
    backend.model.train()
    optimizer = torch.optim.AdamW(
        [
            {
                "params": list(model.parameters()),
                "lr": float(settings["compiler"]["program_learning_rate"]),
            },
            {
                "params": list(reader.parameters()),
                "lr": float(settings["reader"]["reader_learning_rate"]),
            },
        ],
        weight_decay=float(settings["compiler"]["weight_decay"]),
    )
    completed = 0
    counts = [0] * len(units)
    history = []
    if latest is not None:
        optimizer.load_state_dict(latest["optimizer_state_dict"])
        completed = int(latest["completed_rounds"])
        counts = [int(value) for value in latest["update_counts"]]
        history = list(latest["history"])
        random.setstate(latest["python_random_state"])
        torch.set_rng_state(latest["torch_rng_state"])
        if torch.cuda.is_available():
            torch.cuda.set_rng_state_all(latest["cuda_rng_state"])
    maximum_round = int(settings["training"]["maximum_updates_per_pair"])
    started = time.perf_counter()
    for update_round in range(completed + 1, maximum_round + 1):
        order = sorted(
            range(len(units)),
            key=lambda index: stable_key(
                GLOBAL_SEED, "8a-training-order", update_round, unit_ids[index]
            ),
        )
        round_metrics: dict[str, list[float]] = defaultdict(list)
        for ordinal, index in enumerate(order, start=1):
            unit = units[index]
            query_id = str(unit["query_state_id"])
            state_id = str(unit["program_state_id"])
            transition_id = str(unit["program_transition_id"])
            source_state = data["state_values"][data["state_position"][state_id]].unsqueeze(0).to(backend.device)
            transition = data["transition_values"][data["transition_position"][transition_id]].unsqueeze(0).to(backend.device)
            latent = model(source_state, transition)
            target_is_raw = str(unit["target"]) == "raw"
            policy_row = tokenized[query_id]["policy"] if target_is_raw else tokenized[query_id]["bare_policy"]
            teacher = tokenized[query_id]["target_teacher"] if target_is_raw else tokenized[query_id]["bare_teacher"]
            rows = [policy_row]
            correct = str(unit["role"]) == "correct"
            if correct:
                rows.append(tokenized[query_id]["ground_truth"])
            batch = _collate(rows, device=backend.device, k=TOKEN_COUNT)
            optimizer.zero_grad(set_to_none=True)
            ground_truth_ids = None
            if correct:
                ground_truth_ids = torch.tensor(
                    [
                        int(value)
                        for value in tokenized[query_id]["ground_truth"]["labels"]
                        if int(value) != -100
                    ],
                    dtype=torch.long,
                    device=backend.device,
                )
            backward = _reader_backward_unit(
                backend=backend,
                reader=reader,
                batch=batch,
                latent=latent.repeat(len(rows), 1),
                maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
                policy_length=int(policy_row["target_len"]),
                teacher=teacher,
                ground_truth_ids=ground_truth_ids,
                unit_weight=float(unit["weight"]),
                training=settings["training"],
            )
            loss = backward["loss"]
            policy_kl = backward["policy_kl"]
            ground_truth_ce = backward["ground_truth_ce"]
            hooks = backward["hooks"]
            terms = {"teacher_token_ce": backward["teacher_token_ce"]}
            nn.utils.clip_grad_norm_(
                list(model.parameters()) + list(reader.parameters()),
                float(settings["training"]["max_grad_norm"]),
            )
            optimizer.step()
            counts[index] += 1
            maximum_ratio = float(hooks.maximum_ratio_tensor().detach().cpu())
            if maximum_ratio > 1.0001:
                raise RuntimeError("Reader per-layer residual ratio exceeded one")
            if not math.isfinite(float(loss.detach().cpu())):
                raise RuntimeError("Reader training loss is non-finite")
            round_metrics["loss"].append(float(loss.detach().cpu()))
            round_metrics["policy_kl"].append(float(policy_kl.detach().cpu()))
            round_metrics["teacher_token_ce"].append(
                float(terms["teacher_token_ce"].detach().cpu())
            )
            round_metrics["ground_truth_ce"].append(
                float(ground_truth_ce.detach().cpu())
            )
            round_metrics["maximum_ratio"].append(maximum_ratio)
            if ordinal % 25 == 0:
                attempt.progress(
                    status=f"reader_u{update_round}",
                    completed_units=ordinal,
                    total_units=len(units),
                    updates_per_unit=update_round,
                )
        if min(counts) != update_round or max(counts) != update_round:
            raise RuntimeError("Reader update accounting differs across training units")
        if update_round not in set(settings["training"]["checkpoint_updates"]):
            continue
        entry = {
            "updates_per_pair": update_round,
            "training_metrics": {
                name: statistics.fmean(values)
                for name, values in round_metrics.items()
            },
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(entry)
        checkpoint_path = paths["checkpoints"] / f"model_u{update_round:02d}.pt"
        payload = _checkpoint_payload(
            model=model,
            reader=reader,
            optimizer=optimizer,
            unit_ids=unit_ids,
            update_counts=counts,
            completed_rounds=update_round,
            history=history,
            parent_checkpoint_sha256=sha256_file(parent_checkpoint),
            unit_manifest_sha256=sha256_file(paths["training_units"]),
        )
        atomic_torch_save(payload, checkpoint_path)
        atomic_write_json(
            paths["latest_checkpoint"],
            {
                "checkpoint": str(checkpoint_path),
                "checkpoint_sha256": sha256_file(checkpoint_path),
                "updates_per_pair": update_round,
            },
        )
        attempt.progress(
            status=f"reader_u{update_round}_checkpoint",
            updates_per_pair=update_round,
            latest_validated_checkpoint=str(checkpoint_path),
        )
        print(
            f"fixed reader u{update_round} policy_kl={entry['training_metrics']['policy_kl']:.6f}",
            flush=True,
        )
    summary = {
        "format": "fixed_memory_reader_training_summary_8a_v1",
        "global_seed": GLOBAL_SEED,
        "training_state_count": int(units_manifest["state_count"]),
        "training_unit_count": len(units),
        "training_role_counts": units_manifest["role_counts"],
        "expert_positive_augmentation": data["augmentation"],
        "class_balance_group_total_weights": units_manifest[
            "balance_group_total_weights"
        ],
        "history": history,
        "parent_pairmlp_checkpoint_sha256": sha256_file(parent_checkpoint),
        "reader_parameter_count": reader.parameter_count(),
        "qwen_frozen": not any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(history) == len(settings["training"]["checkpoint_updates"]),
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def _validation_bridge_identity(
    *, state_id: str, updates: int, control: str
) -> tuple[str, str]:
    bridge_condition = f"8a-u{updates:02d}::{control}"
    return bridge_condition, f"{state_id}::{bridge_condition}"


@torch.no_grad()
def _generate_reader(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    reader: FixedMemoryReader,
    latent: Tensor,
    max_new_tokens: int,
    maximum_ratio: float,
) -> tuple[GenerateOutput, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    user_indices = [int(value) for value in tokenized.metadata["last_user_token_indices"]]
    if len(user_indices) < TOKEN_COUNT:
        raise ValueError("Reader prompt has fewer than four eligible last-user tokens")
    selected = torch.tensor(
        [user_indices[-TOKEN_COUNT:]], device=backend.device, dtype=torch.long
    )
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = FixedMemoryReaderHooks(
        model=backend.model,
        reader=reader,
        layer_indices=LAYER_INDICES,
        selected_token_indices=selected,
        latent=latent.to(backend.device),
        expected_prefill_length=prompt_length,
        maximum_layer_ratio=maximum_ratio,
    )
    started = time.perf_counter()
    with hooks:
        with _attention_context(backend.device):
            output_ids = backend.model.generate(
                input_ids=tokenized.input_ids,
                attention_mask=tokenized.attention_mask,
                max_new_tokens=int(max_new_tokens),
                do_sample=False,
                use_cache=True,
                pad_token_id=backend.tokenizer.eos_token_id,
                eos_token_id=backend.tokenizer.eos_token_id,
            )
    generated = [int(value) for value in output_ids[0, prompt_length:].tolist()]
    text = backend.tokenizer.decode(generated, skip_special_tokens=True)
    hook = hooks.audit.as_dict()
    hook["maximum_layer_ratio"] = float(hooks.maximum_ratio_tensor().cpu())
    hook["residual_norm"] = float(hooks.residual_norm_tensor().cpu())
    return (
        GenerateOutput(
            text=text,
            token_ids=generated,
            usage={
                "prompt_tokens": prompt_length,
                "completion_tokens": len(generated),
                "total_tokens": prompt_length + len(generated),
            },
            ttft_ms=(time.perf_counter() - started) * 1000.0,
            extra={"fixed_memory_reader": hook},
        ),
        hook,
    )


def _validation_condition(
    *,
    updates: int,
    control: str,
    state: Mapping[str, Any],
    program_state_id: str,
    program_transition_id: str,
    model: nn.Module,
    reader: FixedMemoryReader,
    data: Mapping[str, Any],
    tokenized: Mapping[str, Mapping[str, Any]],
    backend: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    config_sha256: str,
    checkpoint_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    output = _validation_path(paths, updates, str(state["state_id"]), control)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == VALIDATION_FORMAT,
            "updates": int(row.get("updates_per_pair", -1)) == updates,
            "control": str(row.get("control")) == control,
            "state": str(row.get("state_id")) == str(state["state_id"]),
            "checkpoint": str(row.get("checkpoint_sha256")) == checkpoint_sha256,
            "complete": row.get("status") == "complete",
            "raw_policy_kl": "raw_policy_kl" in row,
        }
        if not all(checks.values()):
            raise ValueError(f"Existing reader validation row differs: {checks}")
        return row
    app = settings["appworld"]
    bridge_condition, condition_key = _validation_bridge_identity(
        state_id=str(state["state_id"]), updates=updates, control=control
    )
    client = LiveBridgeClient(
        executable=Path(str(app["legacy_python"])),
        bridge_script=paths["one_step_bridge"],
        appworld_root=Path(str(app["legacy_root"])),
        stderr_path=output.with_suffix(".stderr.log"),
        timeout_seconds=float(app["worker_timeout_seconds"]),
    )
    try:
        ready = client.prepare(
            _prepare_on_policy(
                state=state,
                condition=bridge_condition,
                settings=settings,
                paths=paths,
                attempt_id=attempt_id,
            )
        )
        messages = _paired_messages(
            state=state,
            actual_observations=list(ready["actual_observations"]),
            prompt_profile=str(app["prompt_profile"]),
        )
        state_tensor = data["state_values"][data["state_position"][program_state_id]].unsqueeze(0).to(backend.device)
        transition_tensor = data["transition_values"][data["transition_position"][program_transition_id]].unsqueeze(0).to(backend.device)
        with torch.no_grad():
            latent = model(state_tensor, transition_tensor)
            if control == "R0_zero":
                latent = torch.zeros_like(latent)
            raw_policy_row = tokenized[str(state["state_id"])]["raw_policy"]
            raw_policy_batch = _collate(
                [raw_policy_row], device=backend.device, k=TOKEN_COUNT
            )
            _, raw_policy_logits, _ = _reader_forward(
                backend=backend,
                reader=reader,
                batch=raw_policy_batch,
                latent=latent,
                maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
            )
            raw_policy_kl, raw_policy_terms = _policy_loss(
                raw_policy_logits,
                tokenized[str(state["state_id"])]["raw_teacher"],
            )
        prompt = backend.tokenize_messages(messages, add_generation_prompt=True)
        remaining = int(app["context_limit"]) - int(prompt.attention_mask.sum())
        generated, hook = _generate_reader(
            backend=backend,
            messages=messages,
            reader=reader,
            latent=latent,
            max_new_tokens=min(int(app["max_new_tokens"]), remaining),
            maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
        )
        code, fixed = extract_code_and_fix_content(generated.text)
        executed = client.execute(
            condition_key=condition_key,
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(state["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise
    metrics = evaluate_generated_action(
        generated.text,
        code,
        str(state["target_action"]),
        str(executed["raw_observation"]),
        str(state["target_observation"]),
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    row = {
        "format": VALIDATION_FORMAT,
        "status": "complete",
        "updates_per_pair": updates,
        "control": control,
        "state_id": str(state["state_id"]),
        "task_id": str(state["task_id"]),
        "step_id": int(state["step_id"]),
        "program_state_id": program_state_id,
        "program_transition_id": program_transition_id,
        "selected_transition_id": str(state["selected_transition_id"]),
        "raw_model_response": generated.text,
        "fixed_model_response": fixed,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "metrics": metrics,
        "reader_hook": hook,
        "latent_norm": float(latent.norm().cpu()),
        "raw_policy_kl": float(raw_policy_kl.cpu()),
        "raw_policy_teacher_token_ce": float(
            raw_policy_terms["teacher_token_ce"].cpu()
        ),
        "checkpoint_sha256": checkpoint_sha256,
        "config_sha256": config_sha256,
        "same_world_execution": bool(executed["same_world_execution"]),
        "same_python_namespace": bool(executed["same_python_namespace"]),
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
    }
    atomic_write_json(output, row)
    return row


def _summarize_validation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["control"])].append(row)
    output = {}
    for control in CONTROL_NAMES:
        values = grouped[control]
        output[control] = {
            "count": len(values),
            "action_signature": statistics.fmean(
                float(row["metrics"]["canonical_procedural_signature_match"])
                for row in values
            ),
            "semantic_successor": statistics.fmean(
                float(row["metrics"]["semantic_successor_match"])
                for row in values
            ),
            "execution": statistics.fmean(
                float(row["metrics"]["execution_success"]) for row in values
            ),
            "observation_similarity": statistics.fmean(
                float(row["metrics"]["normalized_observation_similarity"])
                for row in values
            ),
            "residual_norm": statistics.fmean(
                float(row["reader_hook"]["residual_norm"]) for row in values
            ),
            "latent_norm": statistics.fmean(float(row["latent_norm"]) for row in values),
            "raw_policy_kl": statistics.fmean(
                float(row["raw_policy_kl"]) for row in values
            ),
            "maximum_layer_ratio": max(
                float(row["reader_hook"]["maximum_layer_ratio"]) for row in values
            ),
        }
    by_state = defaultdict(dict)
    task_by_state = {}
    for row in rows:
        by_state[str(row["state_id"])][str(row["control"])] = row
        task_by_state[str(row["state_id"])] = str(row["task_id"])
    task_deltas = defaultdict(list)
    for state_id, values in by_state.items():
        r1 = values["R1_correct"]["metrics"]
        r0 = values["R0_zero"]["metrics"]
        task_deltas[task_by_state[state_id]].append(
            float(r1["canonical_procedural_signature_match"])
            + float(r1["semantic_successor_match"])
            - float(r0["canonical_procedural_signature_match"])
            - float(r0["semantic_successor_match"])
        )
    positive_tasks = sum(statistics.fmean(values) > 0.0 for values in task_deltas.values())
    summary = {
        **output,
        "positive_task_count": positive_tasks,
        "task_count": len(task_deltas),
        "maximum_layer_ratio": max(
            output[name]["maximum_layer_ratio"] for name in CONTROL_NAMES
        ),
    }
    return summary


def _validate(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
    config_sha256: str,
) -> dict[str, Any]:
    backend = _build_backend(cfg)
    data = _load_data(paths, settings)
    tokenized = _tokenized_data(backend=backend, paths=paths, data=data)
    mismatch = _mismatch_manifest(data, paths)
    controls = {str(row["state_id"]): row for row in mismatch["rows"]}
    validation_states = [
        row
        for row in data["states"].values()
        if str(row["model_split"]) == "heldout_train_validation"
    ]
    reports = []
    for updates in settings["training"]["checkpoint_updates"]:
        updates = int(updates)
        checkpoint_path = paths["checkpoints"] / f"model_u{updates:02d}.pt"
        payload = torch.load(
            checkpoint_path, map_location=backend.device, weights_only=False
        )
        model, reader, _ = _build_pairmlp_reader(
            settings=settings,
            paths=paths,
            data=data,
            device=backend.device,
            checkpoint=payload,
        )
        model.eval()
        reader.eval()
        checkpoint_sha256 = sha256_file(checkpoint_path)
        rows = []
        for state_index, state in enumerate(validation_states, start=1):
            state_id = str(state["state_id"])
            definitions = {
                "R1_correct": (
                    state_id,
                    str(state["selected_transition_id"]),
                ),
                "R2_transition_shuffle": (
                    state_id,
                    str(controls[state_id]["transition_mismatch_transition_id"]),
                ),
                "R3_state_shuffle": (
                    str(controls[state_id]["state_mismatch_state_id"]),
                    str(state["selected_transition_id"]),
                ),
                "R0_zero": (state_id, str(state["selected_transition_id"])),
            }
            for control in CONTROL_NAMES:
                program_state, program_transition = definitions[control]
                rows.append(
                    _validation_condition(
                        updates=updates,
                        control=control,
                        state=state,
                        program_state_id=program_state,
                        program_transition_id=program_transition,
                        model=model,
                        reader=reader,
                        data=data,
                        tokenized=tokenized,
                        backend=backend,
                        settings=settings,
                        paths=paths,
                        config_sha256=config_sha256,
                        checkpoint_sha256=checkpoint_sha256,
                        attempt_id=f"validation-u{updates:02d}",
                    )
                )
            attempt.progress(
                status=f"reader_validation_u{updates}",
                completed_states=state_index,
                total_states=len(validation_states),
                latest_validated_checkpoint=str(
                    _validation_path(paths, updates, state_id, "R0_zero")
                ),
            )
        summary = _summarize_validation(rows)
        report = {
            "format": "fixed_memory_reader_checkpoint_validation_8a_v1",
            "updates_per_pair": updates,
            "state_count": len(validation_states),
            "condition_count": len(rows),
            "validation": summary,
            "classification": reader_behavior_classification(summary),
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": checkpoint_sha256,
        }
        report["selection_score"] = float(
            select_reader_checkpoint([report])["selection_score"]
            if select_reader_checkpoint([report]) is not None
            else -math.inf
        )
        atomic_write_json(
            paths["validation_root"] / f"u{updates:02d}/validation_report.json",
            report,
        )
        reports.append(report)
        print(
            f"reader validation u{updates} {report['classification']} score={report['selection_score']:.6f}",
            flush=True,
        )
    return {"checkpoints": reports}


def _select(paths: Mapping[str, Path], settings: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        _json(paths["validation_root"] / f"u{int(value):02d}/validation_report.json")
        for value in settings["training"]["checkpoint_updates"]
    ]
    selected = select_reader_checkpoint(rows)
    classification = "CLEAR_FAILURE" if selected is None else str(selected["classification"])
    report = {
        "format": "fixed_memory_reader_checkpoint_selection_8a_v1",
        "global_seed": GLOBAL_SEED,
        "candidates": rows,
        "selected": selected,
        "classification": classification,
        "heldout_train_only_selection": True,
        "test_normal_outcomes_used": False,
        "decision_branch": {
            "STRONG": "fixed_memory_reader_heldout_strong",
            "PARTIAL": "fixed_memory_reader_partial",
            "CLEAR_FAILURE": "fixed_memory_reader_failed",
        }[classification],
        "run_first37": classification == "STRONG",
    }
    atomic_write_json(paths["selection"], report)
    atomic_write_text(
        paths["selection_report"],
        "\n".join(
            [
                "# EXP-029A heldout train-task reader validation",
                "",
                *[
                    f"- u{int(row['updates_per_pair'])}: `{row['classification']}`, score `{row['selection_score']:.6f}`"
                    for row in rows
                ],
                f"- selected classification: `{classification}`",
                f"- conditional first37 authorized: `{str(report['run_first37']).lower()}`",
                "- checkpoint selection used heldout train tasks only",
                "- test_normal outcomes used: `false`",
                "",
            ]
        ),
    )
    return report


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8a"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-029A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "collection",
        "state_tensors",
        "paired_summary",
        "paired_rows",
        "parent_training",
        "transition_cache",
        "state_cache",
        "exp028a_outcomes",
        "exp028a_teacher_cache",
        "transitions",
        "decisions",
        "semantic_module",
        "one_step_bridge",
    )
    if args.phase == "train":
        required = (*required, "implementation")
    if args.phase in {"validate", "select"}:
        required = (*required, "training_summary")
    _require(paths, required)
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"reader_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "implementation":
            result = _implementation_validation(
                cfg=cfg, settings=settings, paths=paths
            )
        elif args.phase == "train":
            result = _train(
                cfg=cfg, settings=settings, paths=paths, attempt=attempt
            )
        elif args.phase == "validate":
            result = _validate(
                cfg=cfg,
                settings=settings,
                paths=paths,
                attempt=attempt,
                config_sha256=sha256_file(args.config),
            )
        else:
            result = _select(paths, settings)
        attempt.progress(
            status=f"reader_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths[
                    {
                        "implementation": "implementation",
                        "train": "training_summary",
                        "validate": "validation_root",
                        "select": "selection",
                    }[args.phase]
                ]
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
