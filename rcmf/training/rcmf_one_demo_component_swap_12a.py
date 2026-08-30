"""Frozen component assembly and paired analysis for EXP-035A."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn

from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    FrozenSelectorDecomposition,
    StandardFieldCrossAttentionReader,
    compile_differentiable_field,
    freeze_module,
    tensor_sha256,
)
from rcmf.utils.serialization import sha256_file


GLOBAL_SEED = 25101
CELL_NAMES = ("OO", "OF", "FO", "FF")
BINDINGS = ("C", "S")
CONDITIONS = tuple(f"{cell}-{binding}" for cell in CELL_NAMES for binding in BINDINGS)


@dataclass(frozen=True)
class SelectorIdentity:
    name: str
    root: Path
    ensemble_path: Path
    ensemble_sha256: str
    member_paths: tuple[Path, ...]
    member_sha256: tuple[str, ...]
    parameter_count: int
    key_dim: int
    intercept: float


@dataclass(frozen=True)
class WriterReaderIdentity:
    name: str
    checkpoint_path: Path
    checkpoint_sha256: str
    writer_sha256: str
    reader_sha256: str
    writer_parameter_count: int
    reader_parameter_count: int


def condition_parts(condition: str) -> tuple[str, str, str, str]:
    cell, binding = condition.split("-", maxsplit=1)
    if cell not in CELL_NAMES or binding not in BINDINGS:
        raise ValueError(f"Unknown EXP-035A condition: {condition}")
    selector_name = "old" if cell[0] == "O" else "fresh"
    writer_reader_name = "old" if cell[1] == "O" else "fresh"
    return cell, binding, selector_name, writer_reader_name


def condition_order_for_task(task_index: int) -> list[str]:
    """Counterbalance all eight conditions without using outcomes."""
    base = sorted(
        CONDITIONS,
        key=lambda value: hashlib.sha256(
            f"{GLOBAL_SEED}:exp035a-condition:{value}".encode("utf-8")
        ).hexdigest(),
    )
    offset = int(task_index) % len(base)
    return base[offset:] + base[:offset]


def _state_dict_parameter_count(state: Mapping[str, Tensor]) -> int:
    return sum(int(value.numel()) for value in state.values())


def load_selector_package(
    *,
    name: str,
    root: Path,
    expected_ensemble_sha256: str,
    expected_member_sha256: Sequence[str],
    device: torch.device | str = "cpu",
) -> tuple[FrozenSelectorDecomposition, SelectorIdentity]:
    ensemble_path = root / "selector/ensemble_scores.pt"
    if sha256_file(ensemble_path) != expected_ensemble_sha256:
        raise ValueError(f"{name} selector ensemble SHA differs")
    ensemble = torch.load(ensemble_path, map_location="cpu", weights_only=False)
    rows = list(ensemble["seed_checkpoints"])
    if len(rows) != len(expected_member_sha256):
        raise ValueError(f"{name} selector member count differs")
    checkpoints: list[Mapping[str, Any]] = []
    member_paths: list[Path] = []
    member_hashes: list[str] = []
    for row, expected in zip(rows, expected_member_sha256, strict=True):
        path = Path(str(row["checkpoint"]))
        actual = sha256_file(path)
        if actual != str(row["checkpoint_sha256"]) or actual != str(expected):
            raise ValueError(f"{name} selector member SHA differs: {path}")
        member_paths.append(path)
        member_hashes.append(actual)
        checkpoints.append(torch.load(path, map_location="cpu", weights_only=False))
    decomposition = FrozenSelectorDecomposition.from_checkpoints(
        checkpoints, ensemble["train_calibration"]
    ).to(device)
    decomposition.eval()
    freeze_module(decomposition)
    identity = SelectorIdentity(
        name=name,
        root=root,
        ensemble_path=ensemble_path,
        ensemble_sha256=expected_ensemble_sha256,
        member_paths=tuple(member_paths),
        member_sha256=tuple(member_hashes),
        parameter_count=sum(int(parameter.numel()) for parameter in decomposition.parameters()),
        key_dim=int(decomposition.key_dim),
        intercept=float(decomposition.intercept),
    )
    return decomposition, identity


def load_writer_reader_package(
    *,
    name: str,
    checkpoint_path: Path,
    expected_checkpoint_sha256: str,
    device: torch.device | str = "cpu",
) -> tuple[nn.Module, nn.Module, WriterReaderIdentity]:
    if sha256_file(checkpoint_path) != expected_checkpoint_sha256:
        raise ValueError(f"{name} writer/reader checkpoint SHA differs")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    writer = AlignedTransitionWriter().to(device=device, dtype=torch.float32)
    reader = StandardFieldCrossAttentionReader().to(device=device, dtype=torch.float32)
    writer.load_state_dict(checkpoint["writer_state_dict"], strict=True)
    reader.load_state_dict(checkpoint["reader_state_dict"], strict=True)
    writer.eval()
    reader.eval()
    freeze_module(writer)
    freeze_module(reader)
    identity = WriterReaderIdentity(
        name=name,
        checkpoint_path=checkpoint_path,
        checkpoint_sha256=expected_checkpoint_sha256,
        writer_sha256=str(checkpoint["writer_sha256"]),
        reader_sha256=str(checkpoint["reader_sha256"]),
        writer_parameter_count=_state_dict_parameter_count(checkpoint["writer_state_dict"]),
        reader_parameter_count=_state_dict_parameter_count(checkpoint["reader_state_dict"]),
    )
    return writer, reader, identity


def permutation_from_rows(
    ordered_transition_ids: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> Tensor:
    payload_by_key = {
        str(row["key_transition_id"]): str(row["payload_transition_id"])
        for row in rows
    }
    ordered = [str(value) for value in ordered_transition_ids]
    if set(payload_by_key) != set(ordered):
        raise ValueError("Shuffle key IDs differ from the memory IDs")
    if set(payload_by_key.values()) != set(ordered):
        raise ValueError("Shuffle payload IDs are not a bijection")
    position = {value: index for index, value in enumerate(ordered)}
    permutation = torch.tensor(
        [position[payload_by_key[value]] for value in ordered], dtype=torch.long
    )
    if bool((permutation == torch.arange(len(ordered))).any()):
        raise ValueError("Shuffle permutation contains a fixed point")
    return permutation


def select_leakage_safe_memory_ids(
    *,
    ordered_transition_ids: Sequence[str],
    parent_task_by_transition: Mapping[str, str],
    train_task_ids: Sequence[str],
    heldout_task_ids: Sequence[str],
) -> list[str]:
    train = set(str(value) for value in train_task_ids)
    heldout = set(str(value) for value in heldout_task_ids)
    if train & heldout:
        raise ValueError("Train and heldout task sets overlap")
    selected = [
        str(transition_id)
        for transition_id in ordered_transition_ids
        if str(parent_task_by_transition[str(transition_id)]) in train
    ]
    if any(str(parent_task_by_transition[value]) in heldout for value in selected):
        raise RuntimeError("Heldout-parent memory entered the train field")
    return selected


@torch.no_grad()
def compile_field_pair(
    *,
    keys: Tensor,
    payloads: Tensor,
    rho: Tensor,
    permutation: Tensor,
) -> dict[str, Tensor]:
    if int(keys.shape[0]) != int(payloads.shape[0]):
        raise ValueError("Key and payload counts differ")
    if tuple(permutation.shape) != (int(keys.shape[0]),):
        raise ValueError("Permutation shape differs")
    A, B = compile_differentiable_field(keys=keys, payloads=payloads, rho=rho)
    shuffled_A, shuffled_B = compile_differentiable_field(
        keys=keys, payloads=payloads[permutation], rho=rho
    )
    values = {
        "A": A,
        "B": B,
        "shuffled_A": shuffled_A,
        "shuffled_B": shuffled_B,
    }
    if not all(bool(torch.isfinite(value).all()) for value in values.values()):
        raise ValueError("Compiled field contains NaN/Inf")
    return values


def tensor_audit(value: Tensor) -> dict[str, Any]:
    work = value.detach().to(device="cpu", dtype=torch.float32).contiguous()
    flat = work.flatten()
    return {
        "shape": list(work.shape),
        "dtype": str(work.dtype),
        "sha256": tensor_sha256(work),
        "norm": float(flat.norm()),
        "rms": float(flat.square().mean().sqrt()),
        "minimum": float(flat.min()),
        "maximum": float(flat.max()),
        "finite": bool(torch.isfinite(work).all()),
        "exact_zero_rate": float((work == 0).to(torch.float32).mean()),
    }


def field_rebuild_errors(
    *, fields: Mapping[str, Tensor], historical: Mapping[str, Tensor]
) -> dict[str, float]:
    return {
        key: float((fields[key].detach().cpu() - historical[key].detach().cpu()).abs().max())
        for key in ("A", "B", "shuffled_A", "shuffled_B")
    }


def remove_restore_error(
    *,
    fields: Mapping[str, Tensor],
    key: Tensor,
    payload: Tensor,
    shuffled_payload: Tensor,
    rho: float,
) -> dict[str, float]:
    correct_delta_A = torch.einsum("k,sp->ksp", key, payload) * float(rho)
    shuffle_delta_A = torch.einsum("k,sp->ksp", key, shuffled_payload) * float(rho)
    restored_A = fields["A"] - correct_delta_A + correct_delta_A
    restored_shuffle_A = (
        fields["shuffled_A"] - shuffle_delta_A + shuffle_delta_A
    )
    return {
        "correct_A": float((restored_A - fields["A"]).abs().max()),
        "shuffle_A": float(
            (restored_shuffle_A - fields["shuffled_A"]).abs().max()
        ),
        "B": 0.0,
        "shuffle_B": 0.0,
    }


def cosine(left: Tensor, right: Tensor) -> float:
    left_flat = left.detach().to(torch.float64).flatten()
    right_flat = right.detach().to(torch.float64).flatten()
    denominator = float(left_flat.norm() * right_flat.norm())
    if denominator == 0.0:
        return math.nan
    return float(torch.dot(left_flat, right_flat) / denominator)


def matched_specificity(success_correct: Sequence[bool], success_shuffle: Sequence[bool]) -> list[int]:
    if len(success_correct) != len(success_shuffle):
        raise ValueError("Matched success vectors differ")
    return [int(correct) - int(shuffle) for correct, shuffle in zip(success_correct, success_shuffle, strict=True)]
