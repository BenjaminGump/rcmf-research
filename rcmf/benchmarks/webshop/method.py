from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from rcmf.training.rcmf_joint_full_bank_9a import (
    KEY_DIM,
    AlignedTransitionWriter,
    FrozenSelectorDecomposition,
    StandardFieldCrossAttentionReader,
    compile_differentiable_field,
    deterministic_payload_permutation,
    read_compiled_field,
)
from rcmf.training.signature_balanced_field_7c import SignatureBalancedFieldSelector

GLOBAL_SEED = 25101
METHOD_FORMAT = "rcmf_webshop_complete_transition_field_v1"


def deterministic_digest(*parts: Any) -> str:
    return hashlib.sha256(":".join(str(value) for value in parts).encode("utf-8")).hexdigest()


def task_partition(
    task_ids: Sequence[str], *, heldout_fraction: float = 0.2, seed: int = GLOBAL_SEED
) -> Mapping[str, str]:
    if not 0.0 < heldout_fraction < 1.0:
        raise ValueError("heldout_fraction must be in (0,1)")
    unique = sorted(set(task_ids), key=lambda value: (deterministic_digest(seed, value), value))
    if len(unique) < 5:
        raise ValueError("WebShop method split requires at least five tasks")
    heldout_count = max(1, round(len(unique) * heldout_fraction))
    heldout = set(unique[:heldout_count])
    return {
        task_id: ("method_validation" if task_id in heldout else "method_train")
        for task_id in unique
    }


def selector_candidate_indices(
    *,
    transition_ids: Sequence[str],
    task_ids: Sequence[str],
    action_types: Sequence[str],
    seed: int = GLOBAL_SEED,
) -> list[list[int]]:
    if not (len(transition_ids) == len(task_ids) == len(action_types)):
        raise ValueError("selector candidate columns differ in length")
    output: list[list[int]] = []
    for index, transition_id in enumerate(transition_ids):
        eligible = [
            other
            for other in range(len(transition_ids))
            if other != index and task_ids[other] != task_ids[index]
        ]
        same = sorted(
            (other for other in eligible if action_types[other] == action_types[index]),
            key=lambda other: (
                deterministic_digest(seed, transition_id, "same", transition_ids[other]),
                transition_ids[other],
            ),
        )
        different = sorted(
            (other for other in eligible if action_types[other] != action_types[index]),
            key=lambda other: (
                deterministic_digest(seed, transition_id, "different", transition_ids[other]),
                transition_ids[other],
            ),
        )
        negatives = same[:2] + different[:2]
        for other in same[2:] + different[2:]:
            if len(negatives) >= 4:
                break
            if other not in negatives:
                negatives.append(other)
        if len(negatives) != 4:
            raise ValueError(f"selector row lacks four cross-task negatives: {transition_id}")
        output.append([index, *negatives])
    return output


def _selector_metrics(
    *,
    model: SignatureBalancedFieldSelector,
    state_views: Tensor,
    transition_views: Tensor,
    candidates: Sequence[Sequence[int]],
    positions: Sequence[int],
    batch_size: int,
) -> Mapping[str, float]:
    losses: list[float] = []
    correct = 0
    reciprocal = 0.0
    model.eval()
    with torch.no_grad():
        for start in range(0, len(positions), batch_size):
            batch_positions = list(positions[start : start + batch_size])
            candidate_tensor = torch.tensor(
                [candidates[index] for index in batch_positions],
                dtype=torch.long,
                device=state_views.device,
            )
            repeated = state_views[batch_positions].repeat_interleave(
                candidate_tensor.shape[1], dim=0
            )
            values = transition_views[candidate_tensor.flatten()]
            scores = model(repeated, values).reshape(len(batch_positions), -1)
            targets = torch.zeros(len(batch_positions), dtype=torch.long, device=scores.device)
            losses.extend(F.cross_entropy(scores, targets, reduction="none").cpu().tolist())
            ranks = (scores > scores[:, :1]).sum(dim=1) + 1
            correct += int((ranks == 1).sum().item())
            reciprocal += float((1.0 / ranks.to(torch.float32)).sum().item())
    count = len(positions)
    return {
        "count": float(count),
        "cross_entropy": sum(losses) / count if count else float("nan"),
        "recall_at_1": correct / count if count else float("nan"),
        "mean_reciprocal_rank": reciprocal / count if count else float("nan"),
    }


def train_selector_member(
    *,
    state_views: Tensor,
    transition_views: Tensor,
    candidates: Sequence[Sequence[int]],
    train_positions: Sequence[int],
    validation_positions: Sequence[int],
    seed: int,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    projection_dim: int = 64,
    interaction_rank: int = 32,
) -> Mapping[str, Any]:
    if state_views.ndim != 3 or transition_views.ndim != 3:
        raise ValueError("selector views must be [row,view,hidden]")
    if int(state_views.shape[0]) != int(transition_views.shape[0]):
        raise ValueError("selector state and transition row counts differ")
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    model = SignatureBalancedFieldSelector(
        state_views=int(state_views.shape[1]),
        transition_views=int(transition_views.shape[1]),
        input_dim=int(state_views.shape[2]),
        projection_dim=projection_dim,
        interaction_rank=interaction_rank,
    ).to(state_views.device, dtype=torch.float32)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    history: list[Mapping[str, Any]] = []
    for epoch in range(1, epochs + 1):
        order = sorted(
            train_positions,
            key=lambda index: (deterministic_digest(seed, epoch, index), index),
        )
        model.train()
        epoch_losses: list[float] = []
        for start in range(0, len(order), batch_size):
            positions = order[start : start + batch_size]
            candidate_tensor = torch.tensor(
                [candidates[index] for index in positions],
                dtype=torch.long,
                device=state_views.device,
            )
            repeated = state_views[positions].repeat_interleave(candidate_tensor.shape[1], dim=0)
            values = transition_views[candidate_tensor.flatten()]
            scores = model(repeated, values).reshape(len(positions), -1)
            targets = torch.zeros(len(positions), dtype=torch.long, device=scores.device)
            loss = F.cross_entropy(scores, targets)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            if not math.isfinite(float(loss.detach().cpu())):
                raise RuntimeError("WebShop selector produced a non-finite loss")
            epoch_losses.append(float(loss.detach().cpu()))
        if epoch in {1, epochs} or epoch % 10 == 0:
            history.append(
                {
                    "epoch": epoch,
                    "train_batch_mean_loss": sum(epoch_losses) / len(epoch_losses),
                    "validation": _selector_metrics(
                        model=model,
                        state_views=state_views,
                        transition_views=transition_views,
                        candidates=candidates,
                        positions=validation_positions,
                        batch_size=batch_size,
                    ),
                }
            )
    model.eval()
    train_scores = []
    with torch.no_grad():
        for start in range(0, len(train_positions), batch_size):
            positions = list(train_positions[start : start + batch_size])
            candidate_tensor = torch.tensor(
                [candidates[index] for index in positions],
                dtype=torch.long,
                device=state_views.device,
            )
            repeated = state_views[positions].repeat_interleave(candidate_tensor.shape[1], dim=0)
            values = transition_views[candidate_tensor.flatten()]
            train_scores.append(model(repeated, values).detach().to(torch.float32).cpu())
    flat = torch.cat(train_scores)
    std = float(flat.std(unbiased=False).item())
    if not math.isfinite(std) or std <= 1.0e-8:
        raise RuntimeError("WebShop selector calibration standard deviation is degenerate")
    return {
        "model_state_dict": {
            key: value.detach().cpu() for key, value in model.state_dict().items()
        },
        "seed": seed,
        "train_mean": float(flat.mean().item()),
        "train_std": std,
        "history": history,
        "train_metrics": _selector_metrics(
            model=model,
            state_views=state_views,
            transition_views=transition_views,
            candidates=candidates,
            positions=train_positions,
            batch_size=batch_size,
        ),
        "validation_metrics": _selector_metrics(
            model=model,
            state_views=state_views,
            transition_views=transition_views,
            candidates=candidates,
            positions=validation_positions,
            batch_size=batch_size,
        ),
    }


def frozen_selector(
    payload: Mapping[str, Any], *, device: torch.device
) -> FrozenSelectorDecomposition:
    members = list(payload["members"])
    selector = FrozenSelectorDecomposition.from_checkpoints(
        members,
        [
            {"train_mean": member["train_mean"], "train_std": member["train_std"]}
            for member in members
        ],
    ).to(device=device, dtype=torch.float32)
    selector.eval()
    if selector.key_dim != KEY_DIM:
        raise RuntimeError(
            f"WebShop selector key dimension is {selector.key_dim}, expected {KEY_DIM}"
        )
    return selector


def parent_normalized_rho(task_ids: Sequence[str], *, device: torch.device) -> Tensor:
    counts = Counter(task_ids)
    return torch.tensor(
        [1.0 / counts[task_id] for task_id in task_ids],
        dtype=torch.float32,
        device=device,
    )


def compile_query_slots(
    *,
    writer: AlignedTransitionWriter,
    memory_views: Tensor,
    keys: Tensor,
    rho: Tensor,
    query: Tensor,
    memory_task_ids: Sequence[str],
    excluded_task_id: str | None,
    permutation: Sequence[int] | None = None,
) -> tuple[Tensor, Mapping[str, Tensor]]:
    payloads = writer(memory_views)
    field_payloads = (
        payloads[torch.tensor(permutation, dtype=torch.long, device=payloads.device)]
        if permutation is not None
        else payloads
    )
    A, B = compile_differentiable_field(keys=keys, payloads=field_payloads, rho=rho)
    if excluded_task_id is not None:
        selected = [
            index for index, task_id in enumerate(memory_task_ids) if task_id == excluded_task_id
        ]
        if selected:
            positions = torch.tensor(selected, dtype=torch.long, device=keys.device)
            task_A, task_B = compile_differentiable_field(
                keys=keys[positions],
                payloads=field_payloads[positions],
                rho=rho[positions],
            )
            A = A - task_A
            B = B - task_B
    slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
    return slots, {"A": A, "B": B, "payloads": payloads}


@torch.no_grad()
def compile_deployment_fields(
    *,
    writer: AlignedTransitionWriter,
    memory_views: Tensor,
    keys: Tensor,
    rho: Tensor,
    transition_ids: Sequence[str],
    task_ids: Sequence[str],
) -> Mapping[str, Any]:
    writer.eval()
    payloads = writer(memory_views)
    correct_A, correct_B = compile_differentiable_field(keys=keys, payloads=payloads, rho=rho)
    rows = [
        {"transition_id": transition_id, "parent_task_id": task_id}
        for transition_id, task_id in zip(transition_ids, task_ids, strict=True)
    ]
    permutation = deterministic_payload_permutation(rows, seed=GLOBAL_SEED)
    permutation_tensor = torch.tensor(permutation, dtype=torch.long, device=payloads.device)
    shuffled_A, shuffled_B = compile_differentiable_field(
        keys=keys,
        payloads=payloads[permutation_tensor],
        rho=rho,
    )
    return {
        "correct_A": correct_A.detach().cpu(),
        "correct_B": correct_B.detach().cpu(),
        "shuffled_A": shuffled_A.detach().cpu(),
        "shuffled_B": shuffled_B.detach().cpu(),
        "permutation": permutation,
        "payloads": payloads.detach().cpu(),
    }


def build_trainable_components(
    device: torch.device,
) -> tuple[AlignedTransitionWriter, StandardFieldCrossAttentionReader]:
    torch.manual_seed(GLOBAL_SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(GLOBAL_SEED)
    return (
        AlignedTransitionWriter().to(device=device, dtype=torch.float32),
        StandardFieldCrossAttentionReader().to(device=device, dtype=torch.float32),
    )
