from __future__ import annotations

import argparse
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
from torch import Tensor
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    assert_frozen_without_gradients,
    compile_differentiable_field,
    freeze_module,
    read_compiled_field,
)
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    CHECKPOINT_FORMAT,
    GLOBAL_SEED,
    configure_reader_only_trainables,
    configure_writer_last_layer_trainables,
    trainable_parameter_names,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_deep_residual_carrier_7e import _bare_target_forward
from scripts.run_rcmf_benefit_preserving_cached_9b import _sparse_teacher
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _build_backend,
    _build_components,
    _load_data,
    _paths as parent_paths,
    _runtime_tensors,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import LiveFieldQueryEncoder
from scripts.run_stage_c_oracle_capacity_5e import _collate
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("teacher-cache", "train"), required=True)
    parser.add_argument("--stage", choices=("reader", "writer"))
    parser.add_argument("--epoch", type=int)
    parser.add_argument("--initial-checkpoint", type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_training")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def paths(artifact_dir: Path) -> dict[str, Path]:
    return {
        "task_fields": artifact_dir / "preflight/task_legal_fields.pt",
        "union": artifact_dir / "trajectory_union/trajectory_union_manifest.json",
        "training_rows": artifact_dir / "trajectory_union/training_rows.jsonl",
        "preferences": artifact_dir / "trajectory_union/preference_rows.jsonl",
        "loops": artifact_dir / "trajectory_union/loop_negative_rows.jsonl",
        "teacher_root": artifact_dir / "trajectory_union/teacher_cache",
        "teacher_summary": artifact_dir / "trajectory_union/teacher_cache_summary.json",
        "reader_root": artifact_dir / "reader_training",
        "writer_root": artifact_dir / "writer_training",
    }


def _target_row_from_step(backend: Any, step: Mapping[str, Any], unit_id: str) -> dict[str, Any]:
    prompt = backend.tokenizer.apply_chat_template(
        [dict(row) for row in step["exact_model_message_array"]],
        tokenize=True,
        add_generation_prompt=True,
    )
    if isinstance(prompt, Tensor):
        prompt = prompt.flatten().tolist()
    prompt_ids = [int(value) for value in prompt]
    target_ids = [int(value) for value in step["generated_token_ids"]]
    return {
        "pair_id": unit_id,
        "input_ids": prompt_ids + target_ids,
        "labels": [-100] * len(prompt_ids) + target_ids,
        "last_user_token_indices": [],
        "pad_token_id": int(backend.tokenizer.pad_token_id or backend.tokenizer.eos_token_id),
        "target_len": len(target_ids),
        "response_cache": {"target_token_ids": target_ids},
    }


def _step(result: Mapping[str, Any], step_id: int) -> dict[str, Any]:
    matches = [dict(row) for row in result["steps"] if int(row["step_id"]) == step_id]
    if len(matches) != 1:
        raise ValueError(f"Trajectory step is not unique: {step_id}")
    return matches[0]


def _tensor_path(root: Path, kind: str, unit_id: str) -> Path:
    digest = hashlib.sha256(unit_id.encode("utf-8")).hexdigest()
    return root / kind / f"{digest}.pt"


def _load_unit_cache(path: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if payload["row_sha256"] != canonical_sha256(row):
        raise ValueError(f"Teacher cache source row differs: {path}")
    return payload


class StaticTaskFieldBank:
    def __init__(self, payload: Mapping[str, Any], device: torch.device) -> None:
        self.device = device
        self.train_task_ids = tuple(str(value) for value in payload["train_task_ids"])
        self.controls = {}
        for control in ("correct", "key_payload_shuffle"):
            row = payload["fields"][control]
            self.controls[control] = {
                "A_total": row["A_total"].to(device, torch.float32),
                "B_total": row["B_total"].to(device, torch.float32),
                "tasks": {
                    task_id: {
                        "A": values["A"].to(device, torch.float32),
                        "B": values["B"].to(device, torch.float32),
                    }
                    for task_id, values in row["task_contributions"].items()
                },
            }

    def field(
        self, *, task_id: str, control: str, removed_parent_task_ids: Sequence[str] = ()
    ) -> tuple[Tensor, Tensor]:
        row = self.controls[control]
        excluded = [task_id, *sorted(set(str(value) for value in removed_parent_task_ids))]
        if len(excluded) != len(set(excluded)):
            excluded = list(dict.fromkeys(excluded))
        A, B = row["A_total"], row["B_total"]
        for parent in excluded:
            if parent not in row["tasks"]:
                raise ValueError(f"Unknown excluded parent task: {parent}")
            A = A - row["tasks"][parent]["A"]
            B = B - row["tasks"][parent]["B"]
        return A, B


def _forward_logits(
    *, backend: Any, reader: Any | None, slots: Tensor | None, target_row: Mapping[str, Any], grad: bool
) -> tuple[Tensor, FieldReaderHooks | None]:
    batch = _collate([dict(target_row)], device=backend.device, k=4)
    hooks = None if reader is None or slots is None else FieldReaderHooks(
        model=backend.model, reader=reader, slots=slots
    )
    context = nullcontext() if grad else torch.no_grad()
    autocast = torch.autocast(
        device_type="cuda", dtype=torch.bfloat16, enabled=backend.device.type == "cuda"
    )
    with context, autocast:
        if hooks is None:
            _, logits = _bare_target_forward(backend=backend, batch=batch)
        else:
            with hooks:
                _, logits = _bare_target_forward(backend=backend, batch=batch)
    return logits[: int(target_row["target_len"])], hooks


def _load_training_context(cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    backend = _build_backend(cfg)
    backend.model.eval()
    backend.model.config.use_cache = False
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    data = _load_data(parent_paths(cfg.raw["stage_c_9a"], parent_root))
    tensors = _runtime_tensors(data, backend.device)
    checkpoint_path = Path(str(settings["immutable_exp031a"]["checkpoint"]))
    checkpoint = torch.load(checkpoint_path, map_location=backend.device, weights_only=False)
    writer, reader = _build_components(backend.device)
    writer.load_state_dict(checkpoint["writer_state_dict"])
    reader.load_state_dict(checkpoint["reader_state_dict"])
    field_bank = StaticTaskFieldBank(
        torch.load(p["task_fields"], map_location="cpu", weights_only=False), backend.device
    )
    return {
        "backend": backend,
        "data": data,
        "tensors": tensors,
        "checkpoint": checkpoint,
        "writer": writer,
        "reader": reader,
        "field_bank": field_bank,
    }


def _materialize_imitation(
    *, row: Mapping[str, Any], runtime: Mapping[str, Any], query_encoder: Any
) -> tuple[dict[str, Any], Tensor]:
    backend, data = runtime["backend"], runtime["data"]
    if row["source_kind"] == "onpolicy_successful_trajectory":
        result = _json(Path(str(row["source_task_result"])))
        step = _step(result, int(row["source_step_id"]))
        target = _target_row_from_step(backend, step, str(row["unit_id"]))
        with torch.no_grad():
            _, query = query_encoder.query(step["exact_model_message_array"])
    else:
        state_id = str(row["state_example_id"])
        target = dict(data["teacher"]["ground_truth_rows"][state_id])
        query = data["source"]["state_queries"][data["state_position"][state_id]].to(
            backend.device, torch.float32
        )
    return target, query


def _teacher_cache(
    *, cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path], attempt: AttemptLedger
) -> dict[str, Any]:
    runtime = _load_training_context(cfg, settings, p)
    backend, reader = runtime["backend"], runtime["reader"]
    freeze_module(runtime["writer"])
    freeze_module(reader)
    query_encoder = LiveFieldQueryEncoder(settings=cfg.raw["stage_c_9a"], backend=backend)
    rows = _rows(p["training_rows"])
    preferences = _rows(p["preferences"])
    loops = _rows(p["loops"])
    completed, reused = 0, 0
    for row in rows:
        output = _tensor_path(p["teacher_root"], "imitation", str(row["unit_id"]))
        if output.exists():
            payload = torch.load(output, map_location="cpu", weights_only=False)
            if payload["row_sha256"] != canonical_sha256(row):
                raise ValueError("Existing imitation teacher cache row differs")
            reused += 1
        else:
            target, query = _materialize_imitation(
                row=row, runtime=runtime, query_encoder=query_encoder
            )
            if str(row["teacher_condition"]) == "bare":
                logits, _ = _forward_logits(
                    backend=backend, reader=None, slots=None, target_row=target, grad=False
                )
            else:
                A, B = runtime["field_bank"].field(
                    task_id=str(row["source_task_id"]), control="correct"
                )
                slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
                logits, _ = _forward_logits(
                    backend=backend, reader=reader, slots=slots, target_row=target, grad=False
                )
            teacher = _sparse_teacher(
                logits, target["response_cache"]["target_token_ids"], top_k=64
            )
            _atomic_torch_save(
                {
                    "format": "rcmf_trajectory_teacher_cache_10a_v1",
                    "unit_id": str(row["unit_id"]),
                    "row_sha256": canonical_sha256(row),
                    "target_row": target,
                    "query": query.detach().cpu(),
                    "sparse_teacher": teacher,
                    "teacher_condition": str(row["teacher_condition"]),
                },
                output,
            )
        completed += 1
        attempt.progress(status="teacher_cache_imitation", completed=completed, total=len(rows) + len(preferences) + len(loops))
    for row in preferences:
        unit_id = str(row["unit_id"])
        output = _tensor_path(p["teacher_root"], "preferences", unit_id)
        if not output.exists():
            results = {
                condition: _json(
                    args_artifact_root(p) / "rollouts/conditions" / condition / "task_results" / f"{row['task_id']}.json"
                )
                for condition in (str(row["preferred_condition"]), str(row["rejected_condition"]))
            }
            preferred = _step(results[str(row["preferred_condition"])], int(row["step_id"]))
            rejected = _step(results[str(row["rejected_condition"])], int(row["step_id"]))
            with torch.no_grad():
                _, query = query_encoder.query(preferred["exact_model_message_array"])
            _atomic_torch_save(
                {
                    "format": "rcmf_trajectory_preference_cache_10a_v1",
                    "unit_id": unit_id,
                    "row_sha256": canonical_sha256(row),
                    "query": query.detach().cpu(),
                    "preferred": _target_row_from_step(backend, preferred, unit_id + "::preferred"),
                    "rejected": _target_row_from_step(backend, rejected, unit_id + "::rejected"),
                },
                output,
            )
        else:
            _load_unit_cache(output, row)
            reused += 1
        completed += 1
    for row in loops:
        unit_id = str(row["unit_id"])
        output = _tensor_path(p["teacher_root"], "loops", unit_id)
        if not output.exists():
            result = _json(Path(str(row["task_result"])))
            step = _step(result, int(row["start_step"]))
            with torch.no_grad():
                _, query = query_encoder.query(step["exact_model_message_array"])
            _atomic_torch_save(
                {
                    "format": "rcmf_trajectory_loop_cache_10a_v1",
                    "unit_id": unit_id,
                    "row_sha256": canonical_sha256(row),
                    "query": query.detach().cpu(),
                    "target_row": _target_row_from_step(backend, step, unit_id),
                },
                output,
            )
        else:
            _load_unit_cache(output, row)
            reused += 1
        completed += 1
    summary = {
        "format": "rcmf_trajectory_teacher_cache_summary_10a_v1",
        "imitation_rows": len(rows),
        "preference_rows": len(preferences),
        "loop_rows": len(loops),
        "cache_rows": completed,
        "reused_rows": reused,
        "top_k": 64,
        "qwen_frozen": True,
        "passed": completed == len(rows) + len(preferences) + len(loops),
    }
    atomic_write_json(p["teacher_summary"], summary)
    return summary


def args_artifact_root(p: Mapping[str, Path]) -> Path:
    return p["task_fields"].parents[1]


def _static_slots(
    *, cache: Mapping[str, Any], row: Mapping[str, Any], field_bank: StaticTaskFieldBank, control: str
) -> Tensor:
    augmentation = row.get("bank_augmentation", {})
    removed = augmentation.get("removed_parent_task_ids", []) if augmentation.get("active") else []
    A, B = field_bank.field(
        task_id=str(row["source_task_id"]), control=control, removed_parent_task_ids=removed
    )
    return read_compiled_field(
        query=cache["query"].to(field_bank.device, torch.float32), A=A, B=B, nonempty=True
    )


def _differentiable_slots(
    *,
    cache: Mapping[str, Any],
    row: Mapping[str, Any],
    runtime: Mapping[str, Any],
    control: str,
) -> Tensor:
    tensors, writer = runtime["tensors"], runtime["writer"]
    removed = set(
        row.get("bank_augmentation", {}).get("removed_parent_task_ids", [])
        if row.get("bank_augmentation", {}).get("active")
        else []
    )
    removed.add(str(row["source_task_id"]))
    keep = torch.tensor(
        [str(task_id) not in removed for task_id in tensors["memory_tasks"]],
        device=runtime["backend"].device,
        dtype=torch.bool,
    )
    payloads = writer(tensors["memory_views"])
    if control == "key_payload_shuffle":
        payloads = payloads[tensors["permutation"]]
    A, B = compile_differentiable_field(
        keys=tensors["keys"][keep], payloads=payloads[keep], rho=tensors["rho"][keep]
    )
    return read_compiled_field(
        query=cache["query"].to(runtime["backend"].device, torch.float32),
        A=A,
        B=B,
        nonempty=True,
    )


def _anchor_loss(reader: Any, anchor: Mapping[str, Tensor]) -> Tensor:
    values = []
    for name, parameter in reader.named_parameters():
        if name.endswith("output.weight"):
            base = anchor[name].to(parameter.device, torch.float32)
            values.append(
                (parameter.float() - base).square().mean() / base.square().mean().clamp_min(1.0e-12)
            )
    return torch.stack(values).mean()


def _frozen_hash(module: Any, trainable_names: set[str]) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(module.state_dict().items()):
        if name in trainable_names:
            continue
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _checkpoint(
    *, runtime: Mapping[str, Any], optimizer: Any, stage: str, epoch: int, cursor: int,
    schedule_ids: Sequence[str], metrics: Sequence[Mapping[str, float]], source_hashes: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "format": CHECKPOINT_FORMAT,
        "global_seed": GLOBAL_SEED,
        "stage": stage,
        "epoch": epoch,
        "cursor": cursor,
        "schedule_ids": list(schedule_ids),
        "writer_state_dict": runtime["writer"].state_dict(),
        "reader_state_dict": runtime["reader"].state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        "python_rng_state": random.getstate(),
        "metrics": list(metrics),
        "source_hashes": dict(source_hashes),
    }


def _train_epoch(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path], attempt: AttemptLedger
) -> dict[str, Any]:
    if args.stage is None or args.epoch is None:
        raise ValueError("Training requires --stage and --epoch")
    stage, epoch = args.stage, int(args.epoch)
    if stage == "reader" and epoch not in (1, 2):
        raise ValueError("Reader stage supports epochs 1 and 2")
    if stage == "writer" and epoch != 1:
        raise ValueError("Writer stage supports exactly one epoch")
    runtime = _load_training_context(cfg, settings, p)
    backend, writer, reader = runtime["backend"], runtime["writer"], runtime["reader"]
    original = runtime["checkpoint"]
    if args.initial_checkpoint is not None:
        initial = torch.load(args.initial_checkpoint, map_location=backend.device, weights_only=False)
        writer.load_state_dict(initial["writer_state_dict"])
        reader.load_state_dict(initial["reader_state_dict"])
    elif stage == "reader" and epoch == 2:
        initial_path = p["reader_root"] / "checkpoints/epoch_01.pt"
        initial = torch.load(initial_path, map_location=backend.device, weights_only=False)
        writer.load_state_dict(initial["writer_state_dict"])
        reader.load_state_dict(initial["reader_state_dict"])
    if stage == "reader":
        trainable = configure_reader_only_trainables(writer=writer, reader=reader)
        groups = [{"params": trainable, "lr": float(settings["training"]["reader_learning_rate"])}]
    else:
        reader_parameters, writer_parameters = configure_writer_last_layer_trainables(
            writer=writer, reader=reader
        )
        groups = [
            {"params": reader_parameters, "lr": float(settings["training"]["reader_learning_rate"])},
            {"params": writer_parameters, "lr": float(settings["training"]["writer_learning_rate"])},
        ]
    optimizer = torch.optim.AdamW(
        groups,
        weight_decay=float(settings["training"]["weight_decay"]),
    )
    if stage == "reader" and epoch == 2 and args.initial_checkpoint is None:
        optimizer.load_state_dict(initial["optimizer_state_dict"])
    rows = _rows(p["training_rows"])
    preferences = _rows(p["preferences"])
    loops = _rows(p["loops"])
    units = [
        {"kind": "imitation", "unit_id": str(row["unit_id"]), "row": row}
        for row in rows
    ]
    units.extend(
        {"kind": "preference", "unit_id": str(row["unit_id"]), "row": row}
        for row in preferences
    )
    units.extend(
        {
            "kind": "loop",
            "unit_id": str(row["unit_id"]),
            "row": row,
        }
        for row in loops
    )
    units.sort(key=lambda unit: (stable_key(GLOBAL_SEED, f"10a-{stage}-e{epoch}", unit["unit_id"]), unit["unit_id"]))
    schedule_ids = [str(unit["unit_id"]) for unit in units]
    source_hashes = {
        "union": sha256_file(p["union"]),
        "training_rows": sha256_file(p["training_rows"]),
        "preferences": sha256_file(p["preferences"]),
        "loops": sha256_file(p["loops"]),
        "teacher_summary": sha256_file(p["teacher_summary"]),
    }
    cursor, metrics = 0, []
    if args.resume_checkpoint != "none":
        resume = torch.load(Path(args.resume_checkpoint), map_location=backend.device, weights_only=False)
        if resume["schedule_ids"] != schedule_ids or resume["source_hashes"] != source_hashes:
            raise ValueError("Resume identity differs")
        writer.load_state_dict(resume["writer_state_dict"])
        reader.load_state_dict(resume["reader_state_dict"])
        optimizer.load_state_dict(resume["optimizer_state_dict"])
        torch.set_rng_state(resume["torch_rng_state"])
        if torch.cuda.is_available():
            torch.cuda.set_rng_state_all(resume["cuda_rng_state"])
        random.setstate(resume["python_rng_state"])
        cursor, metrics = int(resume["cursor"]), list(resume["metrics"])
    anchor = {
        name: value.detach().cpu().clone()
        for name, value in original["reader_state_dict"].items()
        if name.endswith("output.weight")
    }
    qwen_versions = tuple(parameter._version for parameter in backend.model.parameters())
    writer_trainable = set(trainable_parameter_names(writer))
    reader_trainable = set(trainable_parameter_names(reader))
    frozen_writer_before = _frozen_hash(writer, writer_trainable)
    frozen_reader_before = _frozen_hash(reader, reader_trainable)
    scale = len(rows) / sum(float(row["balanced_weight"]) for row in rows)
    checkpoint_root = p[f"{stage}_root"] / "checkpoints"
    result_root = p[f"{stage}_root"] / f"epoch_{epoch:02d}/unit_results"
    started = time.perf_counter()
    for index in range(cursor, len(units)):
        unit = units[index]
        row = unit["row"]
        optimizer.zero_grad(set_to_none=True)
        if unit["kind"] == "imitation":
            cache = _load_unit_cache(
                _tensor_path(p["teacher_root"], "imitation", unit["unit_id"]),
                row,
            )
            slots_fn = _static_slots if stage == "reader" else _differentiable_slots
            if stage == "reader":
                slots = slots_fn(cache=cache, row=row, field_bank=runtime["field_bank"], control="correct")
            else:
                slots = slots_fn(cache=cache, row=row, runtime=runtime, control="correct")
            logits, hooks = _forward_logits(
                backend=backend, reader=reader, slots=slots,
                target_row=cache["target_row"], grad=True
            )
            target_ids = torch.tensor(
                cache["target_row"]["response_cache"]["target_token_ids"],
                device=backend.device,
                dtype=torch.long,
            )
            sequence_ce = F.cross_entropy(logits.float(), target_ids)
            policy_kl, _ = _policy_loss(logits, cache["sparse_teacher"])
            loss = float(row["balanced_weight"]) * scale * (
                float(settings["training"]["sequence_ce_weight"]) * sequence_ce
                + float(settings["training"]["sparse_teacher_policy_kl_weight"]) * policy_kl
            )
            if row["balance_group"] == "preservation":
                loss = loss + float(row["balanced_weight"]) * scale * float(
                    settings["training"]["bare_preservation_kl_weight"]
                ) * policy_kl
            margin = logits.new_zeros((), dtype=torch.float32)
            if row["balance_group"] == "memory_benefit":
                if stage == "reader":
                    shuffled_slots = _static_slots(
                        cache=cache, row=row, field_bank=runtime["field_bank"],
                        control="key_payload_shuffle"
                    )
                else:
                    shuffled_slots = _differentiable_slots(
                        cache=cache, row=row, runtime=runtime,
                        control="key_payload_shuffle"
                    )
                shuffled_logits, _ = _forward_logits(
                    backend=backend, reader=reader, slots=shuffled_slots,
                    target_row=cache["target_row"], grad=True
                )
                shuffled_ce = F.cross_entropy(shuffled_logits.float(), target_ids)
                margin = F.relu(
                    sequence_ce - shuffled_ce
                    + float(settings["training"]["correct_shuffle_margin_nll"])
                )
                loss = loss + float(row["balanced_weight"]) * scale * float(
                    settings["training"]["correct_shuffle_margin_weight"]
                ) * margin
            anchor_loss = _anchor_loss(reader, anchor)
            loss = loss + float(settings["training"]["reader_anchor_weight"]) * anchor_loss
            metric = {
                "loss": float(loss.detach().cpu()),
                "sequence_ce": float(sequence_ce.detach().cpu()),
                "policy_kl": float(policy_kl.detach().cpu()),
                "shuffle_margin": float(margin.detach().cpu()),
                "anchor": float(anchor_loss.detach().cpu()),
            }
        elif unit["kind"] == "preference":
            cache = _load_unit_cache(
                _tensor_path(p["teacher_root"], "preferences", unit["unit_id"]),
                row,
            )
            synthetic = {
                "source_task_id": str(row["task_id"]),
                "bank_augmentation": row.get("bank_augmentation", {"active": False}),
            }
            if stage == "reader":
                slots = _static_slots(
                    cache=cache, row=synthetic, field_bank=runtime["field_bank"], control="correct"
                )
            else:
                slots = _differentiable_slots(
                    cache=cache, row=synthetic, runtime=runtime, control="correct"
                )
            preferred_logits, _ = _forward_logits(
                backend=backend, reader=reader, slots=slots,
                target_row=cache["preferred"], grad=True
            )
            rejected_logits, _ = _forward_logits(
                backend=backend, reader=reader, slots=slots,
                target_row=cache["rejected"], grad=True
            )
            preferred_ids = torch.tensor(
                cache["preferred"]["response_cache"]["target_token_ids"],
                device=backend.device, dtype=torch.long
            )
            rejected_ids = torch.tensor(
                cache["rejected"]["response_cache"]["target_token_ids"],
                device=backend.device, dtype=torch.long
            )
            preferred_nll = F.cross_entropy(preferred_logits.float(), preferred_ids)
            rejected_nll = F.cross_entropy(rejected_logits.float(), rejected_ids)
            preference = F.softplus(preferred_nll - rejected_nll)
            loss = float(row["weight"]) * preference + float(
                settings["training"]["reader_anchor_weight"]
            ) * _anchor_loss(reader, anchor)
            metric = {
                "loss": float(loss.detach().cpu()),
                "preference_loss": float(preference.detach().cpu()),
                "preferred_nll": float(preferred_nll.detach().cpu()),
                "rejected_nll": float(rejected_nll.detach().cpu()),
            }
        else:
            cache = _load_unit_cache(
                _tensor_path(p["teacher_root"], "loops", unit["unit_id"]),
                row,
            )
            synthetic = {
                "source_task_id": str(row["task_id"]),
                "bank_augmentation": row.get("bank_augmentation", {"active": False}),
            }
            if stage == "reader":
                slots = _static_slots(
                    cache=cache, row=synthetic, field_bank=runtime["field_bank"], control="correct"
                )
            else:
                slots = _differentiable_slots(
                    cache=cache, row=synthetic, runtime=runtime, control="correct"
                )
            logits, _ = _forward_logits(
                backend=backend, reader=reader, slots=slots,
                target_row=cache["target_row"], grad=True
            )
            ids = torch.tensor(
                cache["target_row"]["response_cache"]["target_token_ids"],
                device=backend.device, dtype=torch.long
            )
            probabilities = F.softmax(logits.float(), dim=-1).gather(-1, ids[:, None]).squeeze(-1)
            unlikelihood = -torch.log1p(-probabilities.clamp(max=1.0 - 1.0e-6)).mean()
            loss = float(row["weight"]) * unlikelihood + float(
                settings["training"]["reader_anchor_weight"]
            ) * _anchor_loss(reader, anchor)
            metric = {
                "loss": float(loss.detach().cpu()),
                "unlikelihood": float(unlikelihood.detach().cpu()),
            }
        if not math.isfinite(float(loss.detach().cpu())):
            raise RuntimeError("EXP-032A produced NaN/Inf loss")
        loss.backward()
        parameters = [parameter for group in optimizer.param_groups for parameter in group["params"]]
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            parameters, float(settings["training"]["max_grad_norm"])
        )
        if not math.isfinite(float(gradient_norm.detach().cpu())):
            raise RuntimeError("EXP-032A produced NaN/Inf gradients")
        optimizer.step()
        assert_frozen_without_gradients(backend.model)
        metric.update(
            {
                "kind": unit["kind"],
                "unit_id": unit["unit_id"],
                "gradient_norm": float(gradient_norm.detach().cpu()),
            }
        )
        metrics.append(metric)
        atomic_write_json(
            result_root / f"{hashlib.sha256(unit['unit_id'].encode('utf-8')).hexdigest()}.json",
            {"format": "rcmf_trajectory_training_unit_result_10a_v1", **metric},
        )
        cursor = index + 1
        if cursor % int(settings["training"]["checkpoint_every_units"]) == 0 or cursor == len(units):
            progress = _checkpoint(
                runtime=runtime, optimizer=optimizer, stage=stage, epoch=epoch,
                cursor=cursor, schedule_ids=schedule_ids, metrics=metrics,
                source_hashes=source_hashes
            )
            _atomic_torch_save(progress, checkpoint_root / "progress.pt")
            attempt.progress(
                status=f"{stage}_epoch_{epoch}", completed_units=cursor,
                total_units=len(units),
                latest_validated_checkpoint=str(checkpoint_root / "progress.pt")
            )
    if tuple(parameter._version for parameter in backend.model.parameters()) != qwen_versions:
        raise RuntimeError("Frozen Qwen parameter version changed")
    if _frozen_hash(writer, writer_trainable) != frozen_writer_before:
        raise RuntimeError("Forbidden writer parameters changed")
    if _frozen_hash(reader, reader_trainable) != frozen_reader_before:
        raise RuntimeError("Forbidden reader parameters changed")
    final_payload = _checkpoint(
        runtime=runtime, optimizer=optimizer, stage=stage, epoch=epoch,
        cursor=len(units), schedule_ids=schedule_ids, metrics=metrics,
        source_hashes=source_hashes
    )
    final_path = checkpoint_root / f"epoch_{epoch:02d}.pt"
    _atomic_torch_save(final_payload, final_path)
    summary = {
        "format": "rcmf_trajectory_training_epoch_summary_10a_v1",
        "stage": stage,
        "epoch": epoch,
        "checkpoint": str(final_path),
        "checkpoint_sha256": sha256_file(final_path),
        "unit_count": len(units),
        "backward_count": len(units),
        "mean_loss": statistics.fmean(float(row["loss"]) for row in metrics),
        "trainable_writer_parameters": sum(
            parameter.numel() for parameter in writer.parameters() if parameter.requires_grad
        ),
        "trainable_reader_parameters": sum(
            parameter.numel() for parameter in reader.parameters() if parameter.requires_grad
        ),
        "writer_sha256": module_state_sha256(writer),
        "reader_sha256": module_state_sha256(reader),
        "qwen_frozen_and_unchanged": True,
        "forbidden_parameters_unchanged": True,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": True,
    }
    atomic_write_json(p[f"{stage}_root"] / f"epoch_{epoch:02d}/summary.json", summary)
    return summary


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")
    seed_everything(GLOBAL_SEED)
    torch.use_deterministic_algorithms(True, warn_only=False)
    p = paths(args.artifact_dir)
    required = ("task_fields", "union", "training_rows", "preferences", "loops")
    for name in required:
        if not p[name].exists():
            raise FileNotFoundError(p[name])
    source_hashes = {name: sha256_file(p[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"exp032a_{args.phase}_{args.stage or 'cache'}_{args.epoch or 0}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=args.phase == "train",
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "teacher-cache":
            result = _teacher_cache(cfg=cfg, settings=settings, p=p, attempt=attempt)
            latest = p["teacher_summary"]
        else:
            if not p["teacher_summary"].exists() or not bool(_json(p["teacher_summary"])["passed"]):
                raise RuntimeError("Teacher cache gate has not passed")
            result = _train_epoch(args=args, cfg=cfg, settings=settings, p=p, attempt=attempt)
            latest = Path(str(result["checkpoint"]))
        attempt.progress(status="phase_complete", latest_validated_checkpoint=str(latest))
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
