from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    AlignedTransitionWriter,
    FieldReaderHooks,
    StandardFieldCrossAttentionReader,
    assert_frozen_without_gradients,
    compile_differentiable_field,
    freeze_module,
    read_compiled_field,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss


RUNNER_VERSION = "rcmf_joint_full_bank_runtime_9a_v1"
CHECKPOINT_VERSION = "rcmf_joint_full_bank_checkpoint_9a_v1"
UNIT_MANIFEST_VERSION = "rcmf_joint_full_bank_training_units_9a_v1"
CONTROL_NAMES = (
    "V0_zero",
    "V1_correct",
    "V2_key_payload_shuffle",
    "V3_state_query_shuffle",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in _rows(path)
        if row.get("attempt_id") is not None
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=("preflight", "smoke", "zero-cache", "train", "teacher-validate"),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031a")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    root = artifact_dir / "joint_training"
    return {
        "source_cache": artifact_dir / "data/rcmf_source_cache.pt",
        "data_manifest": artifact_dir / "data/full_bank_data_manifest.json",
        "source_audit": artifact_dir / "data/source_representation_audit.json",
        "selector_audit": artifact_dir / "data/selector_decomposition_audit.json",
        "shuffle": artifact_dir / "data/key_payload_shuffle_manifest.json",
        "static_counts": artifact_dir / "runtime/static_counts.json",
        "outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "teacher": parent_a / "structured_compiler/policy_teacher_cache.pt",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "units": root / "training_unit_manifest.json",
        "state_shuffle": root / "state_query_shuffle_manifest.json",
        "smoke": artifact_dir / "runtime/no_science_full_bank_backward_smoke.json",
        "runtime_preflight": artifact_dir / "runtime/formal_gpu_preflight.json",
        "zero_cache_root": root / "zero_policy_nll",
        "zero_summary": root / "zero_policy_nll_summary.json",
        "checkpoints": root / "checkpoints",
        "latest_checkpoint": root / "latest_checkpoint.json",
        "training_summary": root / "training_summary.json",
        "validation_root": artifact_dir / "heldout_validation/teacher_forced",
        "validation_summary": artifact_dir
        / "heldout_validation/teacher_forced_summary.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"EXP-031A runtime input missing: {missing}")


def _state_derangement(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if len(rows) < 2:
        raise ValueError("State-query shuffle requires at least two states")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["state_task_id"]), []).append(row)
    ordered_tasks = sorted(
        grouped,
        key=lambda task_id: (
            stable_key(GLOBAL_SEED, "9a-state-query-task", task_id),
            task_id,
        ),
    )
    ordered = []
    for task_id in ordered_tasks:
        ordered.extend(
            sorted(
                grouped[task_id],
                key=lambda row: (
                    stable_key(
                        GLOBAL_SEED,
                        "9a-state-query-shuffle",
                        row["state_example_id"],
                    ),
                    str(row["state_example_id"]),
                ),
            )
        )
    maximum_group = max(len(grouped[task_id]) for task_id in grouped)
    task_by_state = {
        str(row["state_example_id"]): str(row["state_task_id"]) for row in rows
    }
    best: tuple[int, int, dict[str, str]] | None = None
    for offset in range(maximum_group, len(ordered)):
        mapping = {
            str(row["state_example_id"]): str(
                ordered[(index + offset) % len(ordered)]["state_example_id"]
            )
            for index, row in enumerate(ordered)
        }
        fixed = sum(source == target for source, target in mapping.items())
        same_task = sum(
            task_by_state[source] == task_by_state[target]
            for source, target in mapping.items()
        )
        candidate = (fixed, same_task, mapping)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
        if fixed == 0 and same_task == 0:
            break
    assert best is not None
    if best[0]:
        raise RuntimeError("State-query shuffle contains fixed points")
    if best[1] and maximum_group <= len(rows) - maximum_group:
        raise RuntimeError("State-query shuffle retained avoidable same-task pairs")
    return best[2]
def _build_manifests(
    outcomes: Sequence[Mapping[str, Any]], paths: Mapping[str, Path]
) -> tuple[dict[str, Any], dict[str, Any]]:
    split_rows = {
        split: [row for row in outcomes if str(row["model_split"]) == split]
        for split in ("model_train", "heldout_train_validation")
    }
    shuffle_rows = []
    shuffle_maps: dict[str, dict[str, str]] = {}
    for split, rows in split_rows.items():
        mapping = _state_derangement(rows)
        shuffle_maps[split] = mapping
        task_by_state = {
            str(row["state_example_id"]): str(row["state_task_id"]) for row in rows
        }
        shuffle_rows.extend(
            {
                "model_split": split,
                "query_state_id": source,
                "shuffled_query_state_id": target,
                "fixed_point": source == target,
                "different_task": task_by_state[source] != task_by_state[target],
                "outcomes_used": False,
            }
            for source, target in sorted(mapping.items())
        )
    shuffle_manifest = {
        "format": "rcmf_state_query_shuffle_9a_v1",
        "global_seed": GLOBAL_SEED,
        "selection_uses_outcomes": False,
        "row_count": len(shuffle_rows),
        "fixed_point_count": sum(bool(row["fixed_point"]) for row in shuffle_rows),
        "same_task_count": sum(not bool(row["different_task"]) for row in shuffle_rows),
        "rows": shuffle_rows,
    }
    shuffle_manifest["manifest_sha256"] = canonical_sha256(shuffle_manifest)
    atomic_write_json(paths["state_shuffle"], shuffle_manifest)

    units = []
    training_rows = split_rows["model_train"]
    for row in training_rows:
        state_id = str(row["state_example_id"])
        label = str(row["label"])
        if label == "POSITIVE":
            units.extend(
                [
                    {
                        "unit_id": f"{state_id}::key_payload_shuffle",
                        "state_example_id": state_id,
                        "role": "key_payload_shuffle",
                        "field_control": "key_payload_shuffle",
                        "query_state_id": state_id,
                        "label": label,
                        "target": "bare",
                        "balance_group": "bare",
                    },
                    {
                        "unit_id": f"{state_id}::state_query_shuffle",
                        "state_example_id": state_id,
                        "role": "state_query_shuffle",
                        "field_control": "correct",
                        "query_state_id": shuffle_maps["model_train"][state_id],
                        "label": label,
                        "target": "bare",
                        "balance_group": "bare",
                    },
                ]
            )
        units.append(
            {
                "unit_id": f"{state_id}::correct",
                "state_example_id": state_id,
                "role": "correct",
                "field_control": "correct",
                "query_state_id": state_id,
                "label": label,
                "target": "raw" if label == "POSITIVE" else "bare",
                "balance_group": "positive" if label == "POSITIVE" else "bare",
            }
        )
    group_counts = Counter(str(row["balance_group"]) for row in units)
    if not group_counts["positive"] or not group_counts["bare"]:
        raise ValueError("Full-field training needs positive and bare groups")
    for row in units:
        row["weight"] = len(units) / (
            2.0 * group_counts[str(row["balance_group"])]
        )
    unit_manifest = {
        "format": UNIT_MANIFEST_VERSION,
        "global_seed": GLOBAL_SEED,
        "unit_count_per_epoch": len(units),
        "epoch_count": 2,
        "backward_count": 2 * len(units),
        "correct_state_count": len(training_rows),
        "role_counts": dict(
            sorted(Counter(str(row["role"]) for row in units).items())
        ),
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
    unit_manifest["manifest_sha256"] = canonical_sha256(unit_manifest)
    atomic_write_json(paths["units"], unit_manifest)
    return unit_manifest, shuffle_manifest


def _load_data(paths: Mapping[str, Path]) -> dict[str, Any]:
    source = torch.load(paths["source_cache"], map_location="cpu", weights_only=False)
    data_manifest = _json(paths["data_manifest"])
    outcomes_payload = _json(paths["outcomes"])
    outcomes = list(outcomes_payload["rows"])
    teacher = torch.load(paths["teacher"], map_location="cpu", weights_only=False)
    transitions = _rows(paths["transitions"])
    transition_by_id = {str(row["transition_id"]): row for row in transitions}
    transition_position = {
        str(value): index for index, value in enumerate(source["ordered_transition_ids"])
    }
    state_position = {
        str(value): index for index, value in enumerate(source["ordered_state_ids"])
    }
    if set(teacher["ordered_state_ids"]) != {
        str(row["state_example_id"]) for row in outcomes
    }:
        raise ValueError("Policy teacher rows differ from paired outcome states")
    outcome_by_id = {str(row["state_example_id"]): row for row in outcomes}
    if len(outcome_by_id) != len(outcomes):
        raise ValueError("Paired outcomes contain duplicate state IDs")
    train_tasks = set(data_manifest["train_task_ids"])
    heldout_tasks = set(data_manifest["heldout_task_ids"])
    train_ids = [
        transition_id
        for transition_id in source["ordered_transition_ids"]
        if str(transition_by_id[str(transition_id)]["parent_task_id"]) in train_tasks
    ]
    if len(train_ids) != int(data_manifest["counts"]["model_training_memories"]):
        raise ValueError("Training memory count differs from prepared manifest")
    train_indices = torch.tensor(
        [transition_position[str(value)] for value in train_ids], dtype=torch.long
    )
    memory_tasks = [
        str(transition_by_id[str(value)]["parent_task_id"]) for value in train_ids
    ]
    rho_map = data_manifest["rho_by_transition_id"]
    rho = torch.tensor([float(rho_map[str(value)]) for value in train_ids])
    shuffle_payload = _json(paths["shuffle"])["model_training_bank"]
    payload_by_key = {
        str(row["key_transition_id"]): str(row["payload_transition_id"])
        for row in shuffle_payload["rows"]
    }
    index_in_bank = {transition_id: index for index, transition_id in enumerate(train_ids)}
    permutation = torch.tensor(
        [index_in_bank[payload_by_key[transition_id]] for transition_id in train_ids],
        dtype=torch.long,
    )
    if bool((permutation == torch.arange(len(train_ids))).any()):
        raise ValueError("Prepared key-payload shuffle has a fixed point")
    return {
        "source": source,
        "data_manifest": data_manifest,
        "outcomes": outcomes,
        "outcome_by_id": outcome_by_id,
        "teacher": teacher,
        "transitions": transitions,
        "transition_by_id": transition_by_id,
        "state_position": state_position,
        "train_ids": train_ids,
        "train_indices": train_indices,
        "memory_tasks": memory_tasks,
        "rho": rho,
        "permutation": permutation,
        "train_tasks": train_tasks,
        "heldout_tasks": heldout_tasks,
    }

def _build_backend(cfg: Any) -> Any:
    torch.use_deterministic_algorithms(True, warn_only=False)
    backend = build_backend(cfg, load_model=True)
    freeze_module(backend.model)
    if hasattr(backend.model, "gradient_checkpointing_enable"):
        backend.model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )
    backend.model.config.use_cache = False
    backend.model.train()
    return backend


def _build_components(device: torch.device) -> tuple[nn.Module, nn.Module]:
    seed_everything(GLOBAL_SEED)
    # The shared seeding helper enables warn-only determinism for older runs.
    # EXP-031A requires nondeterministic CUDA kernels to fail before science.
    torch.use_deterministic_algorithms(True, warn_only=False)
    writer = AlignedTransitionWriter().to(device=device, dtype=torch.float32)
    reader = StandardFieldCrossAttentionReader().to(device=device, dtype=torch.float32)
    return writer, reader


def _runtime_tensors(data: Mapping[str, Any], device: torch.device) -> dict[str, Any]:
    source = data["source"]
    train_indices = data["train_indices"]
    task_indices: dict[str, list[int]] = {}
    for index, task_id in enumerate(data["memory_tasks"]):
        task_indices.setdefault(str(task_id), []).append(index)
    return {
        "memory_views": source["memory_views"][train_indices].to(
            device=device, dtype=torch.float32
        ),
        "keys": source["memory_keys"][train_indices].to(
            device=device, dtype=torch.float32
        ),
        "queries": source["state_queries"].to(device=device, dtype=torch.float32),
        "rho": data["rho"].to(device=device, dtype=torch.float32),
        "permutation": data["permutation"].to(device=device),
        "memory_tasks": list(data["memory_tasks"]),
        "task_indices": {
            task_id: torch.tensor(indices, device=device, dtype=torch.long)
            for task_id, indices in task_indices.items()
        },
    }


def _legal_field(
    *,
    writer: nn.Module,
    tensors: Mapping[str, Any],
    query_task_id: str,
    shuffled_payloads: bool,
) -> tuple[Tensor, Tensor, Tensor]:
    payloads = writer(tensors["memory_views"])
    field_payloads = payloads[tensors["permutation"]] if shuffled_payloads else payloads
    A_total, B_total = compile_differentiable_field(
        keys=tensors["keys"], payloads=field_payloads, rho=tensors["rho"]
    )
    selected = tensors["task_indices"].get(query_task_id)
    if selected is not None:
        A_task, B_task = compile_differentiable_field(
            keys=tensors["keys"][selected],
            payloads=field_payloads[selected],
            rho=tensors["rho"][selected],
        )
        A_total = A_total - A_task
        B_total = B_total - B_task
    return A_total, B_total, payloads


def _condition_slots(
    *,
    writer: nn.Module,
    tensors: Mapping[str, Any],
    data: Mapping[str, Any],
    state_id: str,
    query_state_id: str,
    control: str,
) -> tuple[Tensor, dict[str, Any]]:
    outcome = data["outcome_by_id"][state_id]
    A, B, payloads = _legal_field(
        writer=writer,
        tensors=tensors,
        query_task_id=str(outcome["state_task_id"]),
        shuffled_payloads=control == "key_payload_shuffle",
    )
    query = tensors["queries"][data["state_position"][query_state_id]]
    slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
    return slots, {
        "field_A_norm": A.to(torch.float32).norm(),
        "field_B_norm": B.to(torch.float32).norm(),
        "payload_norm": payloads.to(torch.float32).square().mean().sqrt(),
        "slot_norm": slots.to(torch.float32).norm(),
        "slot_wise_norms": slots.to(torch.float32).norm(dim=-1),
    }


def _target_ids(row: Mapping[str, Any], device: torch.device) -> Tensor:
    return torch.tensor(
        row["response_cache"]["target_token_ids"],
        dtype=torch.long,
        device=device,
    )


def _policy_forward(
    *,
    backend: Any,
    reader: StandardFieldCrossAttentionReader,
    slots: Tensor,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Tensor, FieldReaderHooks]:
    batch = _collate(list(rows), device=backend.device, k=4)
    hooks = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
    autocast = torch.autocast(
        device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"
    )
    with hooks, autocast:
        _, logits = _bare_target_forward(backend=backend, batch=batch)
    return logits, hooks


def _unit_rows(
    *,
    data: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    state_id = str(unit["state_example_id"])
    target = str(unit["target"])
    policy = data["teacher"]["policy_rows"][state_id]
    rows: list[Mapping[str, Any]] = [policy[target]]
    lengths = {"policy": int(policy[target]["target_len"])}
    if str(unit["role"]) == "correct":
        ground_truth = data["teacher"]["ground_truth_rows"][state_id]
        rows.append(ground_truth)
        lengths["ground_truth"] = int(ground_truth["target_len"])
    elif str(unit["role"]) == "key_payload_shuffle":
        rows.append(policy["raw"])
        lengths["shuffle_raw"] = int(policy["raw"]["target_len"])
    return rows, lengths


def _loss_terms(
    *,
    logits: Tensor,
    data: Mapping[str, Any],
    unit: Mapping[str, Any],
    lengths: Mapping[str, int],
) -> dict[str, Tensor]:
    state_id = str(unit["state_example_id"])
    target = str(unit["target"])
    teacher = data["teacher"]["teacher_rows"][state_id][target]
    policy_length = int(lengths["policy"])
    policy_kl, policy_terms = _policy_loss(logits[:policy_length], teacher)
    offset = policy_length
    ground_truth_ce = logits.new_zeros((), dtype=torch.float32)
    if "ground_truth" in lengths:
        length = int(lengths["ground_truth"])
        ground_truth = data["teacher"]["ground_truth_rows"][state_id]
        ground_truth_ce = F.cross_entropy(
            logits[offset : offset + length].to(torch.float32),
            _target_ids(ground_truth, logits.device),
        )
        offset += length
    shuffle_raw_nll = logits.new_full((), float("nan"), dtype=torch.float32)
    if "shuffle_raw" in lengths:
        length = int(lengths["shuffle_raw"])
        raw_teacher = data["teacher"]["teacher_rows"][state_id]["raw"]
        raw_ids = torch.tensor(
            raw_teacher["generated_token_ids"], dtype=torch.long, device=logits.device
        )
        shuffle_raw_nll = F.cross_entropy(
            logits[offset : offset + length].to(torch.float32), raw_ids
        )
    return {
        "policy_kl": policy_kl,
        "teacher_token_ce": policy_terms["teacher_token_ce"],
        "teacher_token_top1": policy_terms["top1"],
        "ground_truth_ce": ground_truth_ce,
        "shuffle_raw_nll": shuffle_raw_nll,
    }


def _preflight(
    *, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    data = _load_data(paths)
    units, state_shuffle = _build_manifests(data["outcomes"], paths)
    writer, reader = _build_components(torch.device("cpu"))
    report = {
        "format": "rcmf_joint_full_bank_preflight_pending_smoke_9a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "train_task_ids": sorted(data["train_tasks"]),
        "heldout_task_ids": sorted(data["heldout_tasks"]),
        "model_training_memory_count": len(data["train_ids"]),
        "heldout_memory_count": int(
            data["data_manifest"]["counts"]["heldout_memories"]
        ),
        "scoreable_train_states": sum(
            str(row["model_split"]) == "model_train" for row in data["outcomes"]
        ),
        "scoreable_heldout_states": sum(
            str(row["model_split"]) == "heldout_train_validation"
            for row in data["outcomes"]
        ),
        "train_labels": dict(
            sorted(
                Counter(
                    str(row["label"])
                    for row in data["outcomes"]
                    if str(row["model_split"]) == "model_train"
                ).items()
            )
        ),
        "heldout_labels": dict(
            sorted(
                Counter(
                    str(row["label"])
                    for row in data["outcomes"]
                    if str(row["model_split"]) == "heldout_train_validation"
                ).items()
            )
        ),
        "writer_parameters": sum(value.numel() for value in writer.parameters()),
        "reader_parameters": sum(value.numel() for value in reader.parameters()),
        "total_trainable_parameters": sum(value.numel() for value in writer.parameters())
        + sum(value.numel() for value in reader.parameters()),
        "field_A_shape": [960, 8, 256],
        "field_B_shape": [8, 256],
        "field_float32_bytes": 7_864_320 + 8_192,
        "backward_count": int(units["backward_count"]),
        "teacher_forced_heldout_conditions": 784,
        "heldout_live_conditions": 784,
        "conditional_first37_conditions": 111,
        "unit_manifest_sha256": sha256_file(paths["units"]),
        "state_shuffle_manifest_sha256": sha256_file(paths["state_shuffle"]),
        "state_shuffle_fixed_points": int(state_shuffle["fixed_point_count"]),
        "automatic_launch_allowed": False,
        "pending": "measured_no_science_full_bank_backward_smoke",
    }
    atomic_write_json(paths["runtime_preflight"], report)
    return report


def _smoke(
    *, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    data = _load_data(paths)
    if not paths["units"].exists():
        _build_manifests(data["outcomes"], paths)
    units = _json(paths["units"])["units"]
    candidates = [row for row in units if str(row["balance_group"]) == "positive"]
    smoke_unit = min(
        candidates,
        key=lambda unit: len(
            data["teacher"]["policy_rows"][str(unit["state_example_id"])]["raw"][
                "input_ids"
            ]
        ),
    )
    backend = _build_backend(cfg)
    writer, reader = _build_components(backend.device)
    tensors = _runtime_tensors(data, backend.device)
    optimizer = torch.optim.AdamW(
        [
            {"params": writer.parameters(), "lr": float(settings["training"]["writer_learning_rate"])},
            {"params": reader.parameters(), "lr": float(settings["training"]["reader_learning_rate"])},
        ],
        weight_decay=float(settings["training"]["weight_decay"]),
    )
    timings = []
    gradient_rows = []
    forward_seconds = None
    for pass_index in (1, 2):
        optimizer.zero_grad(set_to_none=True)
        started = time.perf_counter()
        slots, field = _condition_slots(
            writer=writer,
            tensors=tensors,
            data=data,
            state_id=str(smoke_unit["state_example_id"]),
            query_state_id=str(smoke_unit["query_state_id"]),
            control="correct",
        )
        rows, lengths = _unit_rows(data=data, unit=smoke_unit)
        # Hooks remain active through activation-checkpoint recomputation.
        active = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
        with active, torch.autocast(
            device_type="cuda",
            dtype=torch.bfloat16,
            enabled=backend.device.type == "cuda",
        ):
            batch = _collate(rows, device=backend.device, k=4)
            _, backward_logits = _bare_target_forward(backend=backend, batch=batch)
            if forward_seconds is None:
                forward_seconds = time.perf_counter() - started
            backward_terms = _loss_terms(
                logits=backward_logits,
                data=data,
                unit=smoke_unit,
                lengths=lengths,
            )
            backward_loss = (
                backward_terms["policy_kl"]
                + 0.25 * backward_terms["teacher_token_ce"]
                + 0.05 * backward_terms["ground_truth_ce"]
                + 0.001 * active.residual_penalty()
                + 0.0001 * field["payload_norm"].square()
            )
            backward_loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(writer.parameters()) + list(reader.parameters()), 1.0
        )
        optimizer.step()
        timings.append(time.perf_counter() - started)
        writer_gradients = {
            name: any(
                parameter.grad is not None
                and bool((parameter.grad.detach().abs() > 0).any())
                for parameter in module.parameters()
            )
            for name, module in writer.writers.items()
        }
        reader_gradients = {
            name: all(
                any(
                    parameter.grad is not None
                    and bool((parameter.grad.detach().abs() > 0).any())
                    for parameter in projection.parameters()
                )
                for projection in (adapter.query, adapter.key, adapter.value, adapter.output)
            )
            for name, adapter in reader.adapters.items()
        }
        gradient_rows.append(
            {
                "pass": pass_index,
                "writer_gradients": writer_gradients,
                "reader_projection_gradients": reader_gradients,
            }
        )
    assert_frozen_without_gradients(backend.model)
    second = gradient_rows[-1]
    passed = all(second["writer_gradients"].values()) and all(
        second["reader_projection_gradients"].values()
    )
    if not passed:
        raise RuntimeError(f"Full-bank smoke gradient contract failed: {second}")
    backward_seconds = float(timings[-1])
    no_grad_seconds = float(forward_seconds or backward_seconds / 2.0)
    training_seconds = 1152 * backward_seconds
    zero_cache_seconds = 464 * no_grad_seconds
    teacher_validation_seconds = 784 * no_grad_seconds
    prior_one_step_seconds = 1504.6966795157641 / 180.0
    heldout_live_seconds = 784 * prior_one_step_seconds
    prior_first37_correct = 8992.449618292972
    prior_first37_shuffle = 5990.844518950209
    expected_first37_seconds = prior_first37_correct + 2.0 * prior_first37_shuffle
    expected_seconds = (
        training_seconds
        + zero_cache_seconds
        + teacher_validation_seconds
        + heldout_live_seconds
        + expected_first37_seconds
    )
    conservative_seconds = (
        1.25 * (training_seconds + zero_cache_seconds + teacher_validation_seconds)
        + 1.20 * heldout_live_seconds
        + 3.0 * prior_first37_correct
    )
    threshold = float(settings["runtime"]["review_threshold_h100_hours"])
    automatic = expected_seconds / 3600.0 <= threshold and conservative_seconds / 3600.0 <= threshold
    report = {
        "format": "rcmf_no_science_full_bank_backward_smoke_9a_v1",
        "scientific_result": False,
        "global_seed": GLOBAL_SEED,
        "state_example_id": str(smoke_unit["state_example_id"]),
        "complete_legal_bank_memory_count": len(data["train_ids"])
        - sum(task == str(data["outcome_by_id"][str(smoke_unit["state_example_id"])]["state_task_id"]) for task in data["memory_tasks"]),
        "full_path": [
            "eight_complete_transition_views",
            "four_section_writers",
            "complete_task_legal_reversible_field",
            "state_conditioned_eight_slot_read",
            "four_layer_standard_cross_attention",
            "frozen_qwen_target_forward_backward",
        ],
        "passes": gradient_rows,
        "seconds": timings,
        "measured_backward_seconds": backward_seconds,
        "measured_forward_seconds": no_grad_seconds,
        "qwen_frozen_and_gradient_free": True,
        "field_shape": [960, 8, 256],
        "slot_shape": [8, 256],
        "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "automatic_launch_allowed": automatic,
        "expected_h100_hours": expected_seconds / 3600.0,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "review_threshold_h100_hours": threshold,
        "passed": passed,
    }
    atomic_write_json(paths["smoke"], report)
    preflight = _json(paths["runtime_preflight"])
    preflight.update(
        {
            "format": "rcmf_joint_full_bank_formal_gpu_preflight_9a_v1",
            "measured_smoke": report,
            "best_h100_hours": expected_seconds / 3600.0 * 0.8,
            "expected_h100_hours": expected_seconds / 3600.0,
            "conservative_h100_hours": conservative_seconds / 3600.0,
            "expected_artifact_bytes": 12_000_000_000,
            "checkpoint_plan": "atomic every 25 units and at both epoch boundaries",
            "restart_plan": "restore writer, reader, AdamW, RNG, epoch cursor, unit cursor, and shuffle NLL cache",
            "automatic_launch_allowed": automatic,
            "pending": None,
            "passed": automatic,
        }
    )
    atomic_write_json(paths["runtime_preflight"], preflight)
    return report

def _zero_row_path(paths: Mapping[str, Path], state_id: str) -> Path:
    return paths["zero_cache_root"] / f"{sha256_text(state_id)}.json"


def _zero_cache(
    *, cfg: Any, paths: Mapping[str, Path], attempt: AttemptLedger
) -> dict[str, Any]:
    preflight = _json(paths["runtime_preflight"])
    if not bool(preflight.get("automatic_launch_allowed")):
        raise RuntimeError("Formal GPU launch is not authorized by measured preflight")
    data = _load_data(paths)
    backend = _build_backend(cfg)
    backend.model.eval()
    paths["zero_cache_root"].mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    created = 0
    reused = 0
    rows = []
    for index, outcome in enumerate(data["outcomes"], start=1):
        state_id = str(outcome["state_example_id"])
        target = "raw" if str(outcome["label"]) == "POSITIVE" else "bare"
        output = _zero_row_path(paths, state_id)
        if output.exists():
            row = _json(output)
            checks = {
                "state": str(row.get("state_example_id")) == state_id,
                "target": str(row.get("target")) == target,
                "teacher": str(row.get("teacher_cache_sha256"))
                == sha256_file(paths["teacher"]),
            }
            if not all(checks.values()):
                raise ValueError(f"Zero-cache resume identity differs: {checks}")
            reused += 1
        else:
            policy = data["teacher"]["policy_rows"][state_id][target]
            teacher = data["teacher"]["teacher_rows"][state_id][target]
            batch = _collate([policy], device=backend.device, k=4)
            with torch.no_grad(), torch.autocast(
                device_type="cuda",
                dtype=torch.bfloat16,
                enabled=backend.device.type == "cuda",
            ):
                _, logits = _bare_target_forward(backend=backend, batch=batch)
                token_ids = torch.tensor(
                    teacher["generated_token_ids"],
                    dtype=torch.long,
                    device=backend.device,
                )
                nll = F.cross_entropy(logits.to(torch.float32), token_ids)
                kl, terms = _policy_loss(logits, teacher)
            row = {
                "format": "rcmf_zero_field_policy_nll_9a_v1",
                "state_example_id": state_id,
                "state_task_id": str(outcome["state_task_id"]),
                "target": target,
                "policy_nll": float(nll.cpu()),
                "policy_kl": float(kl.cpu()),
                "teacher_token_top1": float(terms["top1"].cpu()),
                "teacher_cache_sha256": sha256_file(paths["teacher"]),
                "qwen_frozen": True,
                "field_is_exact_zero": True,
            }
            atomic_write_json(output, row)
            created += 1
        rows.append(row)
        attempt.progress(
            status="zero_field_policy_cache",
            completed_states=index,
            total_states=len(data["outcomes"]),
            latest_validated_checkpoint=str(output),
        )
    summary = {
        "format": "rcmf_zero_field_policy_nll_summary_9a_v1",
        "state_count": len(rows),
        "created": created,
        "reused": reused,
        "teacher_cache_sha256": sha256_file(paths["teacher"]),
        "mean_policy_nll": statistics.fmean(float(row["policy_nll"]) for row in rows),
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(rows) == len(data["outcomes"]),
    }
    atomic_write_json(paths["zero_summary"], summary)
    return summary


def _atomic_torch_save(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    torch.save(dict(payload), temporary)
    os.replace(temporary, path)


def _ordered_epoch_units(unit_manifest: Mapping[str, Any], epoch: int) -> list[dict[str, Any]]:
    by_state: dict[str, list[dict[str, Any]]] = {}
    for row in unit_manifest["units"]:
        by_state.setdefault(str(row["state_example_id"]), []).append(dict(row))
    role_order = {"key_payload_shuffle": 0, "state_query_shuffle": 1, "correct": 2}
    state_ids = sorted(
        by_state,
        key=lambda state_id: (
            stable_key(GLOBAL_SEED, "9a-training-epoch", epoch, state_id),
            state_id,
        ),
    )
    ordered = []
    for state_id in state_ids:
        ordered.extend(
            sorted(
                by_state[state_id],
                key=lambda row: role_order[str(row["role"])],
            )
        )
    return ordered


def _checkpoint_payload(
    *,
    writer: nn.Module,
    reader: nn.Module,
    optimizer: torch.optim.Optimizer,
    completed_units: int,
    unit_ids: Sequence[str],
    history: Sequence[Mapping[str, Any]],
    shuffle_nll: Mapping[str, float],
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "format": CHECKPOINT_VERSION,
        "global_seed": GLOBAL_SEED,
        "completed_units": int(completed_units),
        "unit_ids": list(unit_ids),
        "history": list(history),
        "shuffle_raw_nll": dict(shuffle_nll),
        "writer_state_dict": {
            key: value.detach().cpu() for key, value in writer.state_dict().items()
        },
        "reader_state_dict": {
            key: value.detach().cpu() for key, value in reader.state_dict().items()
        },
        "optimizer_state_dict": optimizer.state_dict(),
        "writer_sha256": module_state_sha256(writer),
        "reader_sha256": module_state_sha256(reader),
        "source_hashes": dict(source_hashes),
        "python_random_state": random.getstate(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all()
        if torch.cuda.is_available()
        else [],
    }


def _restore_checkpoint(
    *,
    payload: Mapping[str, Any],
    writer: nn.Module,
    reader: nn.Module,
    optimizer: torch.optim.Optimizer,
    unit_ids: Sequence[str],
    source_hashes: Mapping[str, str],
) -> tuple[int, list[dict[str, Any]], dict[str, float]]:
    checks = {
        "format": str(payload.get("format")) == CHECKPOINT_VERSION,
        "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
        "units": list(payload.get("unit_ids", [])) == list(unit_ids),
        "sources": dict(payload.get("source_hashes", {})) == dict(source_hashes),
    }
    if not all(checks.values()):
        raise ValueError(f"Full-field resume identity differs: {checks}")
    writer.load_state_dict(payload["writer_state_dict"])
    reader.load_state_dict(payload["reader_state_dict"])
    optimizer.load_state_dict(payload["optimizer_state_dict"])
    random.setstate(payload["python_random_state"])
    torch.set_rng_state(payload["torch_rng_state"])
    if torch.cuda.is_available() and payload["cuda_rng_state"]:
        torch.cuda.set_rng_state_all(payload["cuda_rng_state"])
    return (
        int(payload["completed_units"]),
        [dict(row) for row in payload["history"]],
        {str(key): float(value) for key, value in payload["shuffle_raw_nll"].items()},
    )


def _train(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    preflight = _json(paths["runtime_preflight"])
    if not bool(preflight.get("automatic_launch_allowed")):
        raise RuntimeError("Formal GPU launch is not authorized by measured preflight")
    if not bool(_json(paths["zero_summary"])["passed"]):
        raise RuntimeError("Zero-field NLL cache is incomplete")
    data = _load_data(paths)
    unit_manifest = _json(paths["units"])
    schedule = []
    epoch_boundaries = []
    for epoch in (1, 2):
        schedule.extend(_ordered_epoch_units(unit_manifest, epoch))
        epoch_boundaries.append(len(schedule))
    unit_ids = [f"e{1 if index < epoch_boundaries[0] else 2}:{row['unit_id']}" for index, row in enumerate(schedule)]
    source_hashes = {
        "source_cache": sha256_file(paths["source_cache"]),
        "data_manifest": sha256_file(paths["data_manifest"]),
        "teacher": sha256_file(paths["teacher"]),
        "units": sha256_file(paths["units"]),
        "state_shuffle": sha256_file(paths["state_shuffle"]),
        "zero_summary": sha256_file(paths["zero_summary"]),
    }
    backend = _build_backend(cfg)
    writer, reader = _build_components(backend.device)
    tensors = _runtime_tensors(data, backend.device)
    optimizer = torch.optim.AdamW(
        [
            {
                "params": writer.parameters(),
                "lr": float(settings["training"]["writer_learning_rate"]),
            },
            {
                "params": reader.parameters(),
                "lr": float(settings["training"]["reader_learning_rate"]),
            },
        ],
        weight_decay=float(settings["training"]["weight_decay"]),
    )
    completed = 0
    history: list[dict[str, Any]] = []
    shuffle_nll: dict[str, float] = {}
    if paths["latest_checkpoint"].exists():
        latest = _json(paths["latest_checkpoint"])
        checkpoint = torch.load(
            Path(str(latest["checkpoint"])),
            map_location=backend.device,
            weights_only=False,
        )
        completed, history, shuffle_nll = _restore_checkpoint(
            payload=checkpoint,
            writer=writer,
            reader=reader,
            optimizer=optimizer,
            unit_ids=unit_ids,
            source_hashes=source_hashes,
        )
    zero_nll = {
        str(row["state_example_id"]): float(row["policy_nll"])
        for row in (
            _json(_zero_row_path(paths, str(outcome["state_example_id"])))
            for outcome in data["outcomes"]
        )
    }
    results_root = paths["training_summary"].parent / "condition_rows"
    results_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    checkpoint_every = int(settings["training"]["checkpoint_every_units"])
    epoch_metrics: dict[int, list[dict[str, float]]] = {1: [], 2: []}
    for cursor in range(completed, len(schedule)):
        epoch = 1 if cursor < epoch_boundaries[0] else 2
        if cursor == epoch_boundaries[0]:
            shuffle_nll = {}
        unit = schedule[cursor]
        state_id = str(unit["state_example_id"])
        optimizer.zero_grad(set_to_none=True)
        unit_started = time.perf_counter()
        slots, field = _condition_slots(
            writer=writer,
            tensors=tensors,
            data=data,
            state_id=state_id,
            query_state_id=str(unit["query_state_id"]),
            control=str(unit["field_control"]),
        )
        rows, lengths = _unit_rows(data=data, unit=unit)
        batch = _collate(rows, device=backend.device, k=4)
        hooks = FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
        with hooks, torch.autocast(
            device_type="cuda",
            dtype=torch.bfloat16,
            enabled=backend.device.type == "cuda",
        ):
            _, logits = _bare_target_forward(backend=backend, batch=batch)
            terms = _loss_terms(logits=logits, data=data, unit=unit, lengths=lengths)
            margin_zero = logits.new_zeros((), dtype=torch.float32)
            margin_shuffle = logits.new_zeros((), dtype=torch.float32)
            if str(unit["role"]) == "correct":
                margin_zero = F.relu(
                    terms["teacher_token_ce"] - float(zero_nll[state_id])
                )
                if str(unit["label"]) == "POSITIVE":
                    if state_id not in shuffle_nll:
                        raise RuntimeError("Positive correct unit preceded its shuffle unit")
                    margin_shuffle = F.relu(
                        terms["teacher_token_ce"] - float(shuffle_nll[state_id])
                    )
            loss = float(unit["weight"]) * (
                float(settings["training"]["raw_or_bare_policy_kl_weight"])
                * terms["policy_kl"]
                + float(settings["training"]["teacher_token_ce_weight"])
                * terms["teacher_token_ce"]
                + float(settings["training"]["ground_truth_action_ce_weight"])
                * terms["ground_truth_ce"]
                + float(settings["training"]["full_vs_zero_nll_weight"])
                * margin_zero
                + float(settings["training"]["full_vs_shuffle_nll_weight"])
                * margin_shuffle
                + float(settings["training"]["reader_residual_norm_weight"])
                * hooks.residual_penalty()
                + float(settings["training"]["writer_payload_norm_weight"])
                * field["payload_norm"].square()
            )
            loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(writer.parameters()) + list(reader.parameters()),
            float(settings["training"]["max_grad_norm"]),
        )
        optimizer.step()
        if not math.isfinite(float(loss.detach().cpu())):
            raise RuntimeError("Full-field training produced NaN/Inf")
        assert_frozen_without_gradients(backend.model)
        if str(unit["role"]) == "key_payload_shuffle":
            shuffle_nll[state_id] = float(terms["shuffle_raw_nll"].detach().cpu())
        metric = {
            "loss": float(loss.detach().cpu()),
            "policy_kl": float(terms["policy_kl"].detach().cpu()),
            "teacher_token_ce": float(terms["teacher_token_ce"].detach().cpu()),
            "ground_truth_ce": float(terms["ground_truth_ce"].detach().cpu()),
            "margin_zero": float(margin_zero.detach().cpu()),
            "margin_shuffle": float(margin_shuffle.detach().cpu()),
            "field_A_norm": float(field["field_A_norm"].detach().cpu()),
            "slot_norm": float(field["slot_norm"].detach().cpu()),
            "payload_norm": float(field["payload_norm"].detach().cpu()),
            "seconds": time.perf_counter() - unit_started,
        }
        epoch_metrics[epoch].append(metric)
        result_path = results_root / f"{sha256_text(unit_ids[cursor])}.json"
        atomic_write_json(
            result_path,
            {
                "format": "rcmf_joint_full_bank_training_condition_9a_v1",
                "global_unit_id": unit_ids[cursor],
                "epoch": epoch,
                "unit": unit,
                "metrics": metric,
                "complete_field_memory_count": len(data["train_ids"]),
                "same_task_exclusion": "task_accumulator_subtraction",
                "runtime_retrieval": False,
                "student_prompt_contains_raw_memory": False,
            },
        )
        completed = cursor + 1
        at_epoch = completed in epoch_boundaries
        if completed % checkpoint_every == 0 or at_epoch or completed == len(schedule):
            history_entry = {
                "completed_units": completed,
                "epoch": epoch,
                "epoch_units_completed": len(epoch_metrics[epoch]),
                "recent_mean_loss": statistics.fmean(
                    row["loss"] for row in epoch_metrics[epoch][-checkpoint_every:]
                ),
                "elapsed_seconds_this_attempt": time.perf_counter() - started,
            }
            history.append(history_entry)
            checkpoint_path = paths["checkpoints"] / "progress.pt"
            payload = _checkpoint_payload(
                writer=writer,
                reader=reader,
                optimizer=optimizer,
                completed_units=completed,
                unit_ids=unit_ids,
                history=history,
                shuffle_nll=shuffle_nll,
                source_hashes=source_hashes,
            )
            _atomic_torch_save(payload, checkpoint_path)
            if at_epoch:
                epoch_path = paths["checkpoints"] / f"epoch_{epoch:02d}.pt"
                _atomic_torch_save(payload, epoch_path)
                checkpoint_path = epoch_path
            atomic_write_json(
                paths["latest_checkpoint"],
                {
                    "checkpoint": str(checkpoint_path),
                    "checkpoint_sha256": sha256_file(checkpoint_path),
                    "completed_units": completed,
                    "epoch": epoch,
                },
            )
            attempt.progress(
                status=f"joint_training_epoch_{epoch}",
                completed_units=completed,
                total_units=len(schedule),
                latest_validated_checkpoint=str(checkpoint_path),
            )
    checkpoints = []
    for epoch in (1, 2):
        path = paths["checkpoints"] / f"epoch_{epoch:02d}.pt"
        checkpoints.append(
            {"epoch": epoch, "path": str(path), "sha256": sha256_file(path)}
        )
    summary = {
        "format": "rcmf_joint_full_bank_training_summary_9a_v1",
        "global_seed": GLOBAL_SEED,
        "epoch_count": 2,
        "completed_units": len(schedule),
        "backward_count": len(schedule),
        "checkpoints": checkpoints,
        "writer_parameters": sum(parameter.numel() for parameter in writer.parameters()),
        "reader_parameters": sum(parameter.numel() for parameter in reader.parameters()),
        "qwen_frozen_and_gradient_free": True,
        "every_scientific_forward_used_complete_task_legal_field": True,
        "runtime_memory_retrieval_used": False,
        "elapsed_seconds_this_attempt": time.perf_counter() - started,
        "passed": len(checkpoints) == 2,
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary

def _validation_condition_path(
    paths: Mapping[str, Path], epoch: int, state_id: str, control: str
) -> Path:
    return (
        paths["validation_root"]
        / f"epoch_{epoch:02d}"
        / "condition_rows"
        / f"{sha256_text(f'{state_id}::{control}')}.json"
    )


def _teacher_validate(
    *,
    cfg: Any,
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    data = _load_data(paths)
    state_shuffle = {
        str(row["query_state_id"]): str(row["shuffled_query_state_id"])
        for row in _json(paths["state_shuffle"])["rows"]
        if str(row["model_split"]) == "heldout_train_validation"
    }
    heldout = [
        row
        for row in data["outcomes"]
        if str(row["model_split"]) == "heldout_train_validation"
    ]
    backend = _build_backend(cfg)
    backend.model.eval()
    tensors = _runtime_tensors(data, backend.device)
    reports = []
    completed = 0
    total = 2 * len(heldout) * len(CONTROL_NAMES)
    for epoch in (1, 2):
        checkpoint_path = paths["checkpoints"] / f"epoch_{epoch:02d}.pt"
        checkpoint = torch.load(
            checkpoint_path, map_location=backend.device, weights_only=False
        )
        writer, reader = _build_components(backend.device)
        writer.load_state_dict(checkpoint["writer_state_dict"])
        reader.load_state_dict(checkpoint["reader_state_dict"])
        writer.eval()
        reader.eval()
        epoch_rows = []
        for outcome in heldout:
            state_id = str(outcome["state_example_id"])
            target = "raw" if str(outcome["label"]) == "POSITIVE" else "bare"
            policy = data["teacher"]["policy_rows"][state_id][target]
            ground_truth = data["teacher"]["ground_truth_rows"][state_id]
            teacher = data["teacher"]["teacher_rows"][state_id][target]
            for control in CONTROL_NAMES:
                output_path = _validation_condition_path(
                    paths, epoch, state_id, control
                )
                if output_path.exists():
                    row = _json(output_path)
                    if str(row.get("checkpoint_sha256")) != sha256_file(checkpoint_path):
                        raise ValueError("Heldout validation resume checkpoint differs")
                else:
                    with torch.no_grad():
                        if control == "V0_zero":
                            slots = torch.zeros(
                                8, 256, device=backend.device, dtype=torch.float32
                            )
                            field = {
                                "field_A_norm": torch.zeros(()),
                                "field_B_norm": torch.zeros(()),
                                "payload_norm": torch.zeros(()),
                                "slot_norm": torch.zeros(()),
                                "slot_wise_norms": torch.zeros(8),
                            }
                        else:
                            slots, field = _condition_slots(
                                writer=writer,
                                tensors=tensors,
                                data=data,
                                state_id=state_id,
                                query_state_id=(
                                    state_shuffle[state_id]
                                    if control == "V3_state_query_shuffle"
                                    else state_id
                                ),
                                control=(
                                    "key_payload_shuffle"
                                    if control == "V2_key_payload_shuffle"
                                    else "correct"
                                ),
                            )
                        logits, hooks = _policy_forward(
                            backend=backend,
                            reader=reader,
                            slots=slots,
                            rows=[policy, ground_truth],
                        )
                        policy_length = int(policy["target_len"])
                        ground_truth_length = int(ground_truth["target_len"])
                        policy_kl, policy_terms = _policy_loss(
                            logits[:policy_length], teacher
                        )
                        ground_truth_ce = F.cross_entropy(
                            logits[
                                policy_length : policy_length + ground_truth_length
                            ].to(torch.float32),
                            _target_ids(ground_truth, backend.device),
                        )
                    attention_values = [
                        value
                        for values in hooks.audit.attention_entropy.values()
                        for value in values
                    ]
                    row = {
                        "format": "rcmf_teacher_forced_heldout_condition_9a_v1",
                        "epoch": epoch,
                        "state_example_id": state_id,
                        "state_task_id": str(outcome["state_task_id"]),
                        "label": str(outcome["label"]),
                        "control": control,
                        "policy_target": target,
                        "policy_kl": float(policy_kl.cpu()),
                        "target_nll": float(policy_terms["teacher_token_ce"].cpu()),
                        "ground_truth_ce": float(ground_truth_ce.cpu()),
                        "action_token_top1": float(policy_terms["top1"].cpu()),
                        "field_A_norm": float(field["field_A_norm"].cpu()),
                        "field_B_norm": float(field["field_B_norm"].cpu()),
                        "field_slot_norm": float(field["slot_norm"].cpu()),
                        "slot_wise_norms": [
                            float(value) for value in field["slot_wise_norms"].cpu()
                        ],
                        "attention_entropy": statistics.fmean(attention_values),
                        "attention_audit": hooks.audit.as_dict(),
                        "checkpoint_sha256": sha256_file(checkpoint_path),
                        "complete_bank_memory_count": len(data["train_ids"]),
                        "same_task_memory_count": 0,
                        "runtime_retrieval": False,
                        "student_prompt_contains_raw_memory": False,
                    }
                    atomic_write_json(output_path, row)
                epoch_rows.append(row)
                completed += 1
                attempt.progress(
                    status=f"teacher_validation_epoch_{epoch}",
                    completed_conditions=completed,
                    total_conditions=total,
                    latest_validated_checkpoint=str(output_path),
                )
        metrics = {}
        for control in CONTROL_NAMES:
            control_rows = [row for row in epoch_rows if row["control"] == control]
            metrics[control] = {
                name: statistics.fmean(float(row[name]) for row in control_rows)
                for name in (
                    "policy_kl",
                    "target_nll",
                    "ground_truth_ce",
                    "action_token_top1",
                    "field_slot_norm",
                    "attention_entropy",
                )
            }
        report = {
            "epoch": epoch,
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "state_count": len(heldout),
            "condition_count": len(epoch_rows),
            "metrics": metrics,
            "correct_minus_zero_target_nll": metrics["V0_zero"]["target_nll"]
            - metrics["V1_correct"]["target_nll"],
            "correct_minus_shuffle_target_nll": metrics[
                "V2_key_payload_shuffle"
            ]["target_nll"]
            - metrics["V1_correct"]["target_nll"],
        }
        reports.append(report)
        atomic_write_json(
            paths["validation_root"] / f"epoch_{epoch:02d}/summary.json", report
        )
    summary = {
        "format": "rcmf_teacher_forced_heldout_summary_9a_v1",
        "global_seed": GLOBAL_SEED,
        "reports": reports,
        "checkpoint_selection_permitted": False,
        "reason": "live heldout L0/L1/L2/L3 remains required",
        "passed_infrastructure": len(reports) == 2,
    }
    atomic_write_json(paths["validation_summary"], summary)
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9a"]
    persistent_root = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent_root):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "source_cache",
        "data_manifest",
        "source_audit",
        "selector_audit",
        "shuffle",
        "static_counts",
        "outcomes",
        "teacher",
        "transitions",
    )
    _require(paths, required)
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"joint_full_bank_{args.phase}",
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
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if not bool(_json(paths["source_audit"])["all_complete_sections"]):
            raise RuntimeError("RCMF source representation gate is not valid")
        if not bool(_json(paths["selector_audit"])["passed"]):
            raise RuntimeError("RCMF selector decomposition gate is not valid")
        if args.phase == "preflight":
            result = _preflight(settings=settings, paths=paths)
            latest = paths["runtime_preflight"]
        elif args.phase == "smoke":
            _require(paths, ("units", "state_shuffle", "runtime_preflight"))
            result = _smoke(cfg=cfg, settings=settings, paths=paths)
            latest = paths["smoke"]
        elif args.phase == "zero-cache":
            _require(paths, ("runtime_preflight",))
            result = _zero_cache(cfg=cfg, paths=paths, attempt=attempt)
            latest = paths["zero_summary"]
        elif args.phase == "train":
            _require(
                paths,
                ("runtime_preflight", "units", "state_shuffle", "zero_summary"),
            )
            result = _train(
                cfg=cfg, settings=settings, paths=paths, attempt=attempt
            )
            latest = paths["training_summary"]
        else:
            _require(
                paths,
                ("training_summary", "state_shuffle", "zero_summary"),
            )
            result = _teacher_validate(cfg=cfg, paths=paths, attempt=attempt)
            latest = paths["validation_summary"]
        attempt.progress(
            status=f"{args.phase}_completed",
            latest_validated_checkpoint=str(latest),
        )
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()