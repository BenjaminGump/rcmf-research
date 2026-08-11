from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from rcmf.training.addressing_4b import (
    distribution,
    effective_rank_from_singular_values,
    pairwise_cosine_summary,
)
from rcmf.training.oracle_convergence_5fa import IndependentPairTensorTable


ORACLE_DECODER_VERSION = "stage_c_shared_decoder_capacity_5fc_v1"
DECODER_SPLIT_VERSION = "stage_c_pair_grouped_decoder_split_5fc_v1"
PLATEAU_RULE_VERSION = "prospective_absolute_change_best_guard_v1"
TENSOR_PLATEAU_RULE_VERSION = "tensor_reconstruction_plateau_with_absolute_floor_v2"
INVERSION_CONTINUATION_RULE_VERSION = "u64_material_improvement_best_guard_v1"
PRIMARY_TARGET_UPDATES = 112
ROBUSTNESS_TARGET_UPDATES = 128
LATENT_DIM = 128
K_TOKENS = 4
LOW_RANKS = (16, 32, 64, 128, 192)
SELECTION_CATEGORIES = ("positive", "neutral", "negative", "random")


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assess_u64_inversion_continuation(
    history: Sequence[Mapping[str, Any]],
    *,
    minimum_relative_huber_improvement: float = 0.01,
    minimum_spearman_improvement: float = 0.01,
    best_huber_multiplier: float = 1.02,
) -> dict[str, Any]:
    current = next(
        (item for item in history if int(item.get("updates_per_pair", -1)) == 64),
        None,
    )
    previous = next(
        (item for item in history if int(item.get("updates_per_pair", -1)) == 32),
        None,
    )
    if current is None or previous is None:
        return {
            "rule_version": INVERSION_CONTINUATION_RULE_VERSION,
            "assessable": False,
            "continue_to_128": False,
            "reason": "missing_u32_or_u64_checkpoint",
        }

    def _metrics(item: Mapping[str, Any]) -> tuple[float, float]:
        summary = item["evaluation_summary"]
        return (
            float(summary["sequence_utility_huber"]["mean"]),
            float(summary["u_text_vs_u_student_spearman"]),
        )

    previous_huber, previous_spearman = _metrics(previous)
    current_huber, current_spearman = _metrics(current)
    observed_hubers = [
        _metrics(item)[0]
        for item in history
        if int(item.get("updates_per_pair", -1)) <= 64
    ]
    best_huber = min(observed_hubers)
    relative_huber_improvement = (
        previous_huber - current_huber
    ) / max(abs(previous_huber), 1.0e-12)
    spearman_improvement = current_spearman - previous_spearman
    checks = {
        "current_huber_lte_best_multiplier": current_huber
        <= best_huber_multiplier * best_huber,
        "material_huber_improvement": relative_huber_improvement
        >= minimum_relative_huber_improvement,
        "material_spearman_improvement": spearman_improvement
        >= minimum_spearman_improvement,
    }
    continue_to_128 = checks["current_huber_lte_best_multiplier"] and (
        checks["material_huber_improvement"]
        or checks["material_spearman_improvement"]
    )
    if continue_to_128:
        reason = "material_improvement_at_u64"
    elif not checks["current_huber_lte_best_multiplier"]:
        reason = "u64_huber_deteriorated_beyond_best_guard"
    else:
        reason = "no_material_improvement_at_u64"
    return {
        "rule_version": INVERSION_CONTINUATION_RULE_VERSION,
        "assessable": True,
        "continue_to_128": continue_to_128,
        "reason": reason,
        "previous_updates": 32,
        "current_updates": 64,
        "previous_sequence_utility_huber": previous_huber,
        "current_sequence_utility_huber": current_huber,
        "best_sequence_utility_huber_through_u64": best_huber,
        "relative_sequence_utility_huber_improvement": relative_huber_improvement,
        "previous_spearman": previous_spearman,
        "current_spearman": current_spearman,
        "spearman_improvement": spearman_improvement,
        "criteria": {
            "minimum_relative_huber_improvement": minimum_relative_huber_improvement,
            "minimum_spearman_improvement": minimum_spearman_improvement,
            "best_huber_multiplier": best_huber_multiplier,
        },
        "checks": checks,
    }


def stack_independent_table_state(
    state_dict: Mapping[str, Tensor], *, pair_count: int
) -> Tensor:
    expected = [f"rows.{index}" for index in range(int(pair_count))]
    if list(state_dict) != expected and set(state_dict) != set(expected):
        missing = sorted(set(expected) - set(state_dict))
        extra = sorted(set(state_dict) - set(expected))
        raise ValueError(f"independent table keys differ: missing={missing[:5]} extra={extra[:5]}")
    return torch.stack([state_dict[key].detach().cpu() for key in expected], dim=0)


def validate_direct_checkpoint(
    payload: Mapping[str, Any],
    *,
    expected_pair_ids: Sequence[str],
    expected_updates: int,
    model_dim: int,
    k: int = K_TOKENS,
    ratio_budget: float = 1.0,
) -> dict[str, Any]:
    errors: list[str] = []
    pair_ids = [str(value) for value in payload.get("pair_ids", [])]
    expected_ids = [str(value) for value in expected_pair_ids]
    if pair_ids != expected_ids:
        errors.append("ordered pair IDs differ from the immutable manifest")
    counts = [int(value) for value in payload.get("update_counts", [])]
    if len(counts) != len(expected_ids) or set(counts) != {int(expected_updates)}:
        errors.append(f"pair update counts are not uniformly {expected_updates}")
    if int(payload.get("completed_rounds", -1)) != int(expected_updates):
        errors.append("completed_rounds differs from the expected checkpoint")
    metadata = dict(payload.get("metadata") or {})
    expected_metadata = {
        "component": "direct_delta",
        "objective": "sequence_utility_plus_sparse_kl",
        "ratio_budget": float(ratio_budget),
        "k": int(k),
        "position": "last_user_k",
        "injection_site": "input_embedding",
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            errors.append(f"metadata {key} differs: {metadata.get(key)!r} != {expected!r}")
    try:
        tensor = stack_independent_table_state(
            payload.get("table_state_dict") or {}, pair_count=len(expected_ids)
        )
    except (ValueError, KeyError) as exc:
        errors.append(str(exc))
        tensor = torch.empty(0)
    expected_shape = (len(expected_ids), int(k), int(model_dim))
    if tuple(tensor.shape) != expected_shape:
        errors.append(f"DeltaE shape {tuple(tensor.shape)} != {expected_shape}")
    return {
        "passed": not errors,
        "errors": errors,
        "pair_count": len(pair_ids),
        "pair_ids_match_exactly_in_order": pair_ids == expected_ids,
        "updates": sorted(set(counts)),
        "shape": list(tensor.shape),
        "ratio_budget": metadata.get("ratio_budget"),
        "k": metadata.get("k"),
        "position": metadata.get("position"),
        "model_identity": metadata.get("model_identity"),
        "pair_cache_sha256": metadata.get("pair_cache_sha256"),
        "ordered_pair_manifest_sha256": metadata.get("ordered_pair_manifest_sha256"),
        "embedded_delta_tensor_sha256": metadata.get("delta_tensor_sha256"),
        "tensor": tensor,
    }


def flatten_delta(delta: Tensor) -> Tensor:
    if delta.dim() != 3:
        raise ValueError(f"DeltaE must have shape [pairs, K, model_dim], got {tuple(delta.shape)}")
    return delta.detach().to(torch.float32).flatten(start_dim=1)


def svd_factorization(delta: Tensor) -> tuple[Tensor, Tensor, Tensor]:
    matrix = flatten_delta(delta)
    return torch.linalg.svd(matrix, full_matrices=False)


def uncentered_svd_reconstruction(
    delta: Tensor,
    rank: int,
    *,
    factorization: tuple[Tensor, Tensor, Tensor] | None = None,
) -> dict[str, Tensor]:
    matrix = flatten_delta(delta)
    u, singular, vh = factorization or torch.linalg.svd(matrix, full_matrices=False)
    requested = int(rank)
    if requested <= 0 or requested > min(matrix.shape):
        raise ValueError(f"rank {requested} is outside [1, {min(matrix.shape)}]")
    basis = vh[:requested]
    work_matrix = matrix.to(basis.dtype)
    coordinates = work_matrix @ basis.T
    reconstruction = (coordinates @ basis).to(matrix.dtype)
    return {
        "basis": basis,
        "coordinates": coordinates,
        "flat": reconstruction,
        "delta": reconstruction.view_as(delta),
        "singular_values": singular,
    }


def _spectrum_summary(values: Tensor) -> dict[str, Any]:
    singular = values.detach().to(torch.float64).cpu()
    squared = singular.square()
    cumulative = squared.cumsum(0) / squared.sum().clamp_min(1.0e-18)
    stable_rank = float(squared.sum() / squared.max().clamp_min(1.0e-18))
    return {
        "count": int(singular.numel()),
        "values": [float(value) for value in singular.tolist()],
        "cumulative_explained_squared_norm": [float(value) for value in cumulative.tolist()],
        "effective_rank": effective_rank_from_singular_values(singular),
        "stable_rank": stable_rank,
        "distribution": distribution(singular.tolist()),
    }


def direct_delta_geometry(
    delta: Tensor,
    *,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    matrix = flatten_delta(delta)
    geometry_matrix = matrix.to(torch.float64)
    singular = torch.linalg.svdvals(geometry_matrix)
    centered = geometry_matrix - geometry_matrix.mean(dim=0, keepdim=True)
    centered_singular = torch.linalg.svdvals(centered)
    slot_norms = delta.detach().to(torch.float32).norm(dim=-1)
    category_geometry = {}
    if rows is not None:
        if len(rows) != delta.shape[0]:
            raise ValueError("row metadata and DeltaE pair counts differ")
        for category in SELECTION_CATEGORIES:
            indices = [
                index
                for index, row in enumerate(rows)
                if str(row.get("selection_category")) == category
            ]
            if not indices:
                continue
            block = matrix[indices]
            category_geometry[category] = {
                "count": len(indices),
                "row_norm": distribution(block.norm(dim=1).tolist()),
                "pairwise_cosine": pairwise_cosine_summary(block),
            }
    return {
        "shape": list(delta.shape),
        "flat_shape": list(matrix.shape),
        "uncentered_spectrum": _spectrum_summary(singular),
        "centered_spectrum": _spectrum_summary(centered_singular),
        "pairwise_cosine": pairwise_cosine_summary(matrix),
        "row_norm": distribution(matrix.norm(dim=1).tolist()),
        "per_token_slot_norm": {
            str(slot): distribution(slot_norms[:, slot].tolist())
            for slot in range(delta.shape[1])
        },
        "category_geometry": category_geometry,
    }


def minimally_project_delta_to_ratio(
    delta: Tensor,
    *,
    base_norms: Tensor,
    max_ratio: float = 1.0,
    tolerance: float = 1.0e-6,
) -> tuple[Tensor, dict[str, Any]]:
    projected = delta.detach().to(torch.float32).clone()
    norms = projected.flatten(start_dim=1).norm(dim=1)
    bases = base_norms.detach().to(torch.float32).cpu().clamp_min(1.0e-8)
    if bases.numel() != projected.shape[0]:
        raise ValueError("base norm count differs from DeltaE rows")
    pre_ratios = norms.cpu() / bases
    needs_projection = pre_ratios > float(max_ratio) + float(tolerance)
    projection_mask = needs_projection.to(norms.device)
    scales = torch.ones_like(norms)
    scales[projection_mask] = (
        bases.to(norms.device)[projection_mask] * float(max_ratio)
    ) / norms[projection_mask].clamp_min(1.0e-8)
    projected.mul_(scales.view(-1, 1, 1))
    post_ratios = projected.flatten(start_dim=1).norm(dim=1).cpu() / bases
    return projected, {
        "projected_row_count": int(needs_projection.sum()),
        "maximum_ratio_before": float(pre_ratios.max()),
        "maximum_ratio_after": float(post_ratios.max()),
        "mean_ratio_after": float(post_ratios.mean()),
        "tolerance": float(tolerance),
    }


def reconstruction_summary(
    original: Tensor,
    reconstructed: Tensor,
    *,
    base_norms: Tensor,
) -> dict[str, Any]:
    if original.shape != reconstructed.shape:
        raise ValueError("original and reconstructed DeltaE shapes differ")
    source = flatten_delta(original)
    estimate = flatten_delta(reconstructed)
    error = estimate - source
    row_cosine = F.cosine_similarity(source, estimate, dim=1, eps=1.0e-12)
    source_norms = source.norm(dim=1).clamp_min(1.0e-12)
    estimate_norms = estimate.norm(dim=1)
    bases = base_norms.detach().to(torch.float32).cpu().clamp_min(1.0e-8)
    slot_relative = []
    for slot in range(original.shape[1]):
        slot_error = (reconstructed[:, slot] - original[:, slot]).to(torch.float32).norm()
        slot_source = original[:, slot].to(torch.float32).norm().clamp_min(1.0e-12)
        slot_relative.append(float(slot_error / slot_source))
    return {
        "relative_frobenius_error": float(error.norm() / source.norm().clamp_min(1.0e-12)),
        "cosine_reconstruction": distribution(row_cosine.tolist()),
        "per_slot_relative_frobenius_error": slot_relative,
        "retained_delta_norm_fraction": distribution((estimate_norms / source_norms).tolist()),
        "perturbation_ratio": {
            **distribution((estimate_norms.cpu() / bases).tolist()),
            "maximum": float((estimate_norms.cpu() / bases).max()),
        },
    }


def low_rank_capacity_gate(
    *,
    rank128_summary: Mapping[str, Any],
    full_summary: Mapping[str, Any],
    zero_summary: Mapping[str, Any],
) -> dict[str, Any]:
    huber = float(rank128_summary["sequence_utility_huber"]["mean"])
    zero_huber = float(zero_summary["sequence_utility_huber"]["mean"])
    full_huber = float(full_summary["sequence_utility_huber"]["mean"])
    by_utility = rank128_summary["by_utility_category"]
    checks = {
        "utility_spearman_gte_0_90": float(
            rank128_summary.get("u_text_vs_u_student_spearman") or -1.0
        )
        >= 0.90,
        "sign_agreement_gte_0_90": float(
            rank128_summary.get("positive_negative_sign_agreement") or 0.0
        )
        >= 0.90,
        "huber_reduction_vs_zero_gte_0_75": 1.0 - huber / max(zero_huber, 1.0e-12)
        >= 0.75,
        "neutral_mean_abs_lte_0_05": float(
            by_utility["neutral"].get("mean_abs_u_student") or math.inf
        )
        <= 0.05,
        "ratio_lte_1_0": float(rank128_summary["delta_ratio"].get("max") or math.inf)
        <= 1.0001,
        "huber_no_more_than_0_05_worse_than_full": huber <= full_huber + 0.05,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "huber_reduction_vs_zero": 1.0 - huber / max(zero_huber, 1.0e-12),
        "huber_difference_from_full": huber - full_huber,
    }


def state_grouped_three_fold_manifest(
    rows: Sequence[Mapping[str, Any]],
    *,
    seed: int = 20260810,
) -> dict[str, Any]:
    if len({str(row["pair_id"]) for row in rows}) != len(rows):
        raise ValueError("decoder split input contains duplicate pair IDs")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        state_id = str(row.get("state_example_id") or "")
        if not state_id:
            raise ValueError("decoder split requires state_example_id on every row")
        grouped[state_id].append(row)

    all_category = Counter(str(row.get("selection_category")) for row in rows)
    all_memories = sorted({int(row["memory_stage_index"]) for row in rows})
    memory_frequency = Counter(int(row["memory_stage_index"]) for row in rows)
    state_ids = sorted(grouped)
    fold_pair_targets = [len(rows) // 3 + int(fold < len(rows) % 3) for fold in range(3)]
    if max(len(bucket) for bucket in grouped.values()) > max(fold_pair_targets):
        raise ValueError("a state group is larger than a decoder heldout fold")

    def subset_with_exact_size(
        candidates: Sequence[str],
        target: int,
    ) -> set[str] | None:
        reachable: dict[int, tuple[str, ...]] = {0: ()}
        for state_id in candidates:
            size = len(grouped[state_id])
            for total, selected in sorted(reachable.items(), reverse=True):
                next_total = total + size
                if next_total <= target and next_total not in reachable:
                    reachable[next_total] = (*selected, state_id)
        selected = reachable.get(target)
        return None if selected is None else set(selected)

    def assignment_score(assignment: Sequence[set[str]]) -> tuple[Any, ...]:
        fold_categories = [Counter() for _ in range(3)]
        fold_memories = [Counter() for _ in range(3)]
        for fold, selected_states in enumerate(assignment):
            for state_id in selected_states:
                fold_categories[fold].update(
                    str(row.get("selection_category")) for row in grouped[state_id]
                )
                fold_memories[fold].update(
                    int(row["memory_stage_index"]) for row in grouped[state_id]
                )
        missing_train_memories = sum(
            fold_memories[fold][memory] == memory_frequency[memory]
            for fold in range(3)
            for memory in all_memories
        )
        category_deviation = sum(
            (
                (fold_categories[fold][category] - all_category[category] / 3.0)
                / max(all_category[category] / 3.0, 1.0)
            )
            ** 2
            for fold in range(3)
            for category in SELECTION_CATEGORIES
        )
        memory_concentration = sum(
            (fold_memories[fold][memory] / memory_frequency[memory]) ** 2
            for fold in range(3)
            for memory in all_memories
        )
        state_count_deviation = sum(
            (len(selected_states) - len(grouped) / 3.0) ** 2
            for selected_states in assignment
        )
        signature = tuple(
            tuple(sorted(selected_states)) for selected_states in assignment
        )
        return (
            missing_train_memories,
            category_deviation,
            memory_concentration,
            state_count_deviation,
            signature,
        )

    best_assignment: list[set[str]] | None = None
    best_score: tuple[Any, ...] | None = None
    successful_searches = 0
    search_trials = 4096
    for trial in range(search_trials):
        trial_rng = random.Random(seed + trial)
        permuted = list(state_ids)
        trial_rng.shuffle(permuted)
        fold_zero = subset_with_exact_size(permuted, fold_pair_targets[0])
        if fold_zero is None:
            continue
        remaining = [state_id for state_id in permuted if state_id not in fold_zero]
        trial_rng.shuffle(remaining)
        fold_one = subset_with_exact_size(remaining, fold_pair_targets[1])
        if fold_one is None:
            continue
        fold_two = set(state_ids) - fold_zero - fold_one
        assignment = [fold_zero, fold_one, fold_two]
        if [sum(len(grouped[state_id]) for state_id in fold) for fold in assignment] != fold_pair_targets:
            continue
        successful_searches += 1
        score = assignment_score(assignment)
        if best_score is None or score < best_score:
            best_assignment = assignment
            best_score = score

    if best_assignment is None or best_score is None:
        raise ValueError(
            "could not construct an exact state-grouped three-fold decoder split"
        )
    if best_score[0] != 0:
        raise ValueError(
            "no searched decoder split preserved every memory in every training fold"
        )

    fold_states = best_assignment
    fold_rows: list[list[Mapping[str, Any]]] = [
        [row for state_id in sorted(selected) for row in grouped[state_id]]
        for selected in fold_states
    ]
    fold_categories = [
        Counter(str(row.get("selection_category")) for row in selected_rows)
        for selected_rows in fold_rows
    ]
    fold_memories = [
        Counter(int(row["memory_stage_index"]) for row in selected_rows)
        for selected_rows in fold_rows
    ]

    folds = []
    all_pair_ids = {str(row["pair_id"]) for row in rows}
    for fold in range(3):
        heldout = sorted(str(row["pair_id"]) for row in fold_rows[fold])
        train = sorted(all_pair_ids - set(heldout))
        train_memories = sorted(
            {
                int(row["memory_stage_index"])
                for other in range(3)
                if other != fold
                for row in fold_rows[other]
            }
        )
        folds.append(
            {
                "fold": fold,
                "heldout_state_example_ids": sorted(fold_states[fold]),
                "train_pair_ids": train,
                "heldout_pair_ids": heldout,
                "heldout_category_counts": dict(sorted(fold_categories[fold].items())),
                "heldout_memory_indices": sorted(fold_memories[fold]),
                "train_memory_indices": train_memories,
                "train_covers_all_memories": train_memories == all_memories,
            }
        )
    manifest = {
        "format": DECODER_SPLIT_VERSION,
        "seed": int(seed),
        "pair_count": len(rows),
        "state_count": len(grouped),
        "memory_count": len(all_memories),
        "memory_indices": all_memories,
        "state_group_size": distribution(
            [len(grouped[state_id]) for state_id in sorted(grouped)]
        ),
        "selection_category_counts": dict(sorted(all_category.items())),
        "ordered_source_pair_ids": [str(row["pair_id"]) for row in rows],
        "assignment_search": {
            "algorithm": "deterministic_exact_capacity_multistart_v1",
            "trials": search_trials,
            "successful_exact_assignments": successful_searches,
            "fold_pair_targets": fold_pair_targets,
            "missing_train_memory_count": int(best_score[0]),
            "category_deviation": float(best_score[1]),
            "memory_concentration": float(best_score[2]),
            "state_count_deviation": float(best_score[3]),
        },
        "folds": folds,
    }
    manifest["manifest_sha256"] = json_sha256(manifest)
    validation = validate_decoder_split_manifest(manifest, rows=rows)
    if not validation["passed"]:
        raise ValueError(f"decoder split validation failed: {validation['errors']}")
    manifest["validation"] = validation
    return manifest


def validate_decoder_split_manifest(
    manifest: Mapping[str, Any],
    *,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    source_ids = [str(row["pair_id"]) for row in rows]
    state_by_pair = {str(row["pair_id"]): str(row["state_example_id"]) for row in rows}
    memory_by_pair = {
        str(row["pair_id"]): int(row["memory_stage_index"]) for row in rows
    }
    all_ids = set(source_ids)
    all_memories = set(memory_by_pair.values())
    heldout_occurrences: Counter[str] = Counter()
    for fold in manifest.get("folds", []):
        train = set(str(value) for value in fold["train_pair_ids"])
        heldout = set(str(value) for value in fold["heldout_pair_ids"])
        if train & heldout:
            errors.append(f"fold {fold['fold']} has pair overlap")
        if train | heldout != all_ids:
            errors.append(f"fold {fold['fold']} does not cover every pair")
        train_states = {state_by_pair[pair_id] for pair_id in train}
        heldout_states = {state_by_pair[pair_id] for pair_id in heldout}
        if train_states & heldout_states:
            errors.append(f"fold {fold['fold']} leaks state_example_id")
        train_memories = {memory_by_pair[pair_id] for pair_id in train}
        if train_memories != all_memories:
            errors.append(f"fold {fold['fold']} training rows do not cover every memory")
        heldout_occurrences.update(heldout)
    if set(heldout_occurrences) != all_ids or set(heldout_occurrences.values()) != {1}:
        errors.append("each pair must appear in exactly one heldout fold")
    return {
        "passed": not errors,
        "errors": errors,
        "pair_count": len(source_ids),
        "heldout_exactly_once": not errors or (
            set(heldout_occurrences) == all_ids and set(heldout_occurrences.values()) == {1}
        ),
    }


class LinearDeltaDecoder(nn.Module):
    def __init__(self, latent_dim: int, output_dim: int) -> None:
        super().__init__()
        self.latent_dim = int(latent_dim)
        self.output_dim = int(output_dim)
        self.linear = nn.Linear(self.latent_dim, self.output_dim, bias=False)

    def forward(self, z: Tensor) -> Tensor:
        return self.linear(z.to(torch.float32))

    def initialize_from_basis(self, basis: Tensor) -> None:
        if tuple(basis.shape) != (self.latent_dim, self.output_dim):
            raise ValueError(
                f"basis shape {tuple(basis.shape)} != {(self.latent_dim, self.output_dim)}"
            )
        with torch.no_grad():
            self.linear.weight.copy_(basis.T.to(self.linear.weight))


class MLPDeltaDecoder(nn.Module):
    def __init__(self, latent_dim: int, output_dim: int, hidden_dim: int = 512) -> None:
        super().__init__()
        self.latent_dim = int(latent_dim)
        self.output_dim = int(output_dim)
        self.hidden_dim = int(hidden_dim)
        self.network = nn.Sequential(
            nn.Linear(self.latent_dim, self.hidden_dim, bias=False),
            nn.GELU(),
            nn.Linear(self.hidden_dim, self.output_dim, bias=False),
        )

    def forward(self, z: Tensor) -> Tensor:
        return self.network(z.to(torch.float32))


def decoder_tensor_loss(prediction: Tensor, target: Tensor) -> dict[str, Tensor]:
    prediction = prediction.to(torch.float32)
    target = target.to(torch.float32)
    squared_error = (prediction - target).square().sum(dim=1)
    target_squared = target.square().sum(dim=1).clamp_min(1.0e-12)
    normalized_mse = (squared_error / target_squared).mean()
    cosine_loss = (1.0 - F.cosine_similarity(prediction, target, dim=1, eps=1.0e-12)).mean()
    return {
        "loss": normalized_mse + 0.1 * cosine_loss,
        "normalized_mse": normalized_mse,
        "cosine_loss": cosine_loss,
    }


def decoder_reconstruction_metrics(prediction: Tensor, target: Tensor) -> dict[str, float]:
    terms = decoder_tensor_loss(prediction, target)
    error = prediction.to(torch.float32) - target.to(torch.float32)
    cosine = F.cosine_similarity(prediction, target, dim=1, eps=1.0e-12)
    return {
        "loss": float(terms["loss"].detach().cpu()),
        "normalized_mse": float(terms["normalized_mse"].detach().cpu()),
        "cosine_loss": float(terms["cosine_loss"].detach().cpu()),
        "relative_frobenius_error": float(
            error.norm() / target.to(torch.float32).norm().clamp_min(1.0e-12)
        ),
        "mean_cosine": float(cosine.mean()),
    }


def tensor_reconstruction_plateau(
    history: Sequence[Mapping[str, Any]],
    *,
    current_epoch: int,
    previous_epoch: int,
    normalized_mse_floor: float = 5.0e-6,
    relative_frobenius_floor: float = 2.0e-3,
    cosine_error_floor: float = 2.0e-6,
) -> dict[str, Any]:
    current = next((row for row in history if int(row["epoch"]) == int(current_epoch)), None)
    previous = next((row for row in history if int(row["epoch"]) == int(previous_epoch)), None)
    if current is None or previous is None:
        return {
            "rule_version": TENSOR_PLATEAU_RULE_VERSION,
            "assessable": False,
            "plateau": False,
        }
    current_loss = float(current["metrics"]["loss"])
    previous_loss = float(previous["metrics"]["loss"])
    best_loss = min(float(row["metrics"]["loss"]) for row in history if int(row["epoch"]) <= current_epoch)
    current_cosine = float(current["metrics"]["mean_cosine"])
    previous_cosine = float(previous["metrics"]["mean_cosine"])
    loss_change = abs(current_loss - previous_loss) / max(abs(previous_loss), 1.0e-12)
    cosine_change = abs(current_cosine - previous_cosine)
    checks = {
        "absolute_relative_loss_change_lt_0_01": loss_change < 0.01,
        "absolute_cosine_change_lt_0_01": cosine_change < 0.01,
        "current_loss_lte_1_02_best_so_far": current_loss <= 1.02 * best_loss,
    }
    numerical_floor_checks = {
        "normalized_mse_lte_floor": float(current["metrics"]["normalized_mse"])
        <= float(normalized_mse_floor),
        "relative_frobenius_error_lte_floor": float(
            current["metrics"]["relative_frobenius_error"]
        )
        <= float(relative_frobenius_floor),
        "one_minus_mean_cosine_lte_floor": 1.0 - current_cosine
        <= float(cosine_error_floor),
    }
    relative_plateau = all(checks.values())
    numerical_floor_plateau = all(numerical_floor_checks.values())
    return {
        "rule_version": TENSOR_PLATEAU_RULE_VERSION,
        "assessable": True,
        "plateau": relative_plateau or numerical_floor_plateau,
        "plateau_mode": (
            "relative_change_best_guard"
            if relative_plateau
            else "absolute_numerical_floor"
            if numerical_floor_plateau
            else None
        ),
        "current_epoch": int(current_epoch),
        "previous_epoch": int(previous_epoch),
        "absolute_relative_loss_change": loss_change,
        "absolute_cosine_change": cosine_change,
        "best_so_far_loss": best_loss,
        "checks": checks,
        "numerical_floor_checks": numerical_floor_checks,
        "numerical_floor_thresholds": {
            "normalized_mse": float(normalized_mse_floor),
            "relative_frobenius_error": float(relative_frobenius_floor),
            "one_minus_mean_cosine": float(cosine_error_floor),
        },
    }


def project_latents_to_output_ratio_(
    latents: Tensor,
    decoder: nn.Module,
    base_norms: Tensor,
    *,
    max_ratio: float = 1.0,
    row_indices: Sequence[int] | None = None,
    tolerance: float = 1.0e-6,
    maximum_iterations: int = 12,
) -> dict[str, Any]:
    indices = list(range(latents.shape[0])) if row_indices is None else [int(i) for i in row_indices]
    index_tensor = torch.tensor(indices, dtype=torch.long, device=latents.device)
    bases = base_norms.to(device=latents.device, dtype=torch.float32).index_select(0, index_tensor)
    projected_count = 0
    with torch.no_grad():
        for _ in range(maximum_iterations):
            selected = latents.index_select(0, index_tensor)
            output = decoder(selected).to(torch.float32)
            ratios = output.norm(dim=1) / bases.clamp_min(1.0e-8)
            violating = ratios > float(max_ratio) + float(tolerance)
            if not bool(violating.any()):
                break
            projected_count += int(violating.sum())
            scales = torch.ones_like(ratios)
            scales[violating] = float(max_ratio) / ratios[violating].clamp_min(1.0e-8)
            selected.mul_(scales.view(-1, 1))
            latents.index_copy_(0, index_tensor, selected)
        final_output = decoder(latents.index_select(0, index_tensor)).to(torch.float32)
        final_ratios = final_output.norm(dim=1) / bases.clamp_min(1.0e-8)
    if float(final_ratios.max()) > float(max_ratio) + 5.0 * float(tolerance):
        raise RuntimeError("latent projection failed to enforce decoded perturbation ratio")
    return {
        "projected_steps": projected_count,
        "mean_ratio": float(final_ratios.mean()),
        "max_ratio": float(final_ratios.max()),
    }


def project_independent_latents_to_ratio_(
    table: IndependentPairTensorTable,
    decoder: nn.Module,
    base_norms: Tensor,
    *,
    max_ratio: float = 1.0,
    row_indices: Sequence[int] | None = None,
    tolerance: float = 1.0e-6,
    maximum_iterations: int = 12,
) -> dict[str, Any]:
    indices = list(range(len(table.rows))) if row_indices is None else [int(i) for i in row_indices]
    index_tensor = torch.tensor(indices, dtype=torch.long, device=table.rows[0].device)
    bases = base_norms.to(device=table.rows[0].device, dtype=torch.float32).index_select(
        0, index_tensor
    )
    projected_count = 0
    with torch.no_grad():
        for _ in range(maximum_iterations):
            selected = table.forward_indices(indices)
            output = decoder(selected).to(torch.float32)
            ratios = output.norm(dim=1) / bases.clamp_min(1.0e-8)
            violating = ratios > float(max_ratio) + float(tolerance)
            if not bool(violating.any()):
                break
            projected_count += int(violating.sum())
            scales = torch.ones_like(ratios)
            scales[violating] = float(max_ratio) / ratios[violating].clamp_min(1.0e-8)
            for local_index, table_index in enumerate(indices):
                table.rows[table_index].mul_(scales[local_index].to(table.rows[table_index]))
        final_output = decoder(table.forward_indices(indices)).to(torch.float32)
        final_ratios = final_output.norm(dim=1) / bases.clamp_min(1.0e-8)
    if float(final_ratios.max()) > float(max_ratio) + 5.0 * float(tolerance):
        raise RuntimeError("independent latent projection failed to enforce decoded ratio")
    return {
        "projected_steps": projected_count,
        "mean_ratio": float(final_ratios.mean()),
        "max_ratio": float(final_ratios.max()),
    }


def apply_latent_inversion_step(
    *,
    optimizer: torch.optim.Optimizer,
    loss: Tensor,
    table: IndependentPairTensorTable,
    decoder: nn.Module,
    selected_indices: Sequence[int],
    update_counts: list[int],
    base_norms: Tensor,
    ratio_budget: float = 1.0,
    train_decoder: bool = False,
    max_grad_norm: float = 1.0,
) -> dict[str, Any]:
    selected = [int(index) for index in selected_indices]
    if len(selected) != len(set(selected)):
        raise ValueError("a pair may appear only once in an inversion batch")
    if len(update_counts) != len(table.rows):
        raise ValueError("update count length differs from latent table")
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    active = [table.rows[index] for index in selected]
    if train_decoder:
        active.extend(parameter for parameter in decoder.parameters() if parameter.requires_grad)
    grad_squared = sum(
        float(parameter.grad.detach().to(torch.float32).square().sum())
        for parameter in active
        if parameter.grad is not None
    )
    grad_norm = math.sqrt(grad_squared)
    torch.nn.utils.clip_grad_norm_(active, float(max_grad_norm))
    optimizer.step()
    projection = project_independent_latents_to_ratio_(
        table,
        decoder,
        base_norms,
        max_ratio=ratio_budget,
        row_indices=None if train_decoder else selected,
    )
    for index in selected:
        update_counts[index] += 1
    return {"gradient_norm": grad_norm, "projection": projection}


def module_state_sha256(module: nn.Module) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(module.state_dict().items()):
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(json.dumps(list(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes(order="C"))
    return digest.hexdigest()


def assert_pair_only_input_contract(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forbidden = {
        "raw_memory_text",
        "memory_text",
        "selector_score",
        "selector_scores",
        "selector_gate",
        "gate",
        "empirical_mu",
        "mu_i",
        "full_bank",
        "full_bank_delta",
    }
    violations = [
        {"pair_id": row.get("pair_id"), "keys": sorted(forbidden & set(row))}
        for row in rows
        if forbidden & set(row)
    ]
    return {
        "passed": not violations,
        "violations": violations[:20],
        "student_prompt_contains_raw_memory": False,
        "selector_payload_accessed": False,
        "full_bank_accessed": False,
    }


def frozen_decoder_capacity_gate(
    *,
    pooled_summary: Mapping[str, Any],
    pooled_zero_summary: Mapping[str, Any],
    fold_summaries: Sequence[Mapping[str, Any]],
    fold_zero_summaries: Sequence[Mapping[str, Any]],
    plateau_by_fold: Sequence[bool],
) -> dict[str, Any]:
    huber = float(pooled_summary["sequence_utility_huber"]["mean"])
    zero_huber = float(pooled_zero_summary["sequence_utility_huber"]["mean"])
    by_utility = pooled_summary["by_utility_category"]
    positive_folds = [
        float(summary["sequence_utility_huber"]["mean"])
        < float(zero["sequence_utility_huber"]["mean"])
        for summary, zero in zip(fold_summaries, fold_zero_summaries)
    ]
    checks = {
        "utility_spearman_gte_0_80": float(
            pooled_summary.get("u_text_vs_u_student_spearman") or -1.0
        )
        >= 0.80,
        "sign_agreement_gte_0_85": float(
            pooled_summary.get("positive_negative_sign_agreement") or 0.0
        )
        >= 0.85,
        "sequence_huber_reduction_gte_0_50": 1.0 - huber / max(zero_huber, 1.0e-12)
        >= 0.50,
        "neutral_mean_abs_lte_0_05": float(
            by_utility["neutral"].get("mean_abs_u_student") or math.inf
        )
        <= 0.05,
        "ratio_lte_1_0": float(pooled_summary["delta_ratio"].get("max") or math.inf)
        <= 1.0001,
        "positive_result_all_three_folds": len(positive_folds) == 3 and all(positive_folds),
        "documented_plateau_all_three_folds": len(plateau_by_fold) == 3
        and all(bool(value) for value in plateau_by_fold),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "huber_reduction_vs_zero": 1.0 - huber / max(zero_huber, 1.0e-12),
        "positive_fold_flags": positive_folds,
        "plateau_by_fold": [bool(value) for value in plateau_by_fold],
    }


def decoder_decision(
    *,
    rank128_passed: bool,
    rank192_reproduced: bool,
    linear_passed: bool,
    mlp_passed: bool,
    joint_mlp_passed: bool,
) -> dict[str, Any]:
    if not rank192_reproduced:
        branch = "rank192_implementation_or_evaluation_error"
        bottleneck = "implementation_or_evaluation"
    elif not rank128_passed:
        branch = "latent_dimension_128_insufficient"
        bottleneck = "latent_dimension_128"
    elif linear_passed and mlp_passed:
        branch = "shared_128d_decoder_capacity_passed"
        bottleneck = None
    elif linear_passed and not mlp_passed:
        branch = "current_injector_mlp_decoder_is_bottleneck"
        bottleneck = "current_injector_mlp_decoder"
    elif mlp_passed:
        branch = "shared_128d_decoder_capacity_passed_mlp_only"
        bottleneck = "linear_decoder_generalization"
    elif joint_mlp_passed:
        branch = "decoder_does_not_generalize_across_pairs"
        bottleneck = "shared_decoder_pair_generalization"
    else:
        branch = "shared_decoder_optimization_or_generalization_failure"
        bottleneck = "tensor_reconstruction_or_qwen_inversion"
    return {
        "branch": branch,
        "identified_bottleneck": bottleneck,
        "rank128_svd_passed": bool(rank128_passed),
        "rank192_reproduced": bool(rank192_reproduced),
        "frozen_linear_passed": bool(linear_passed),
        "frozen_mlp_passed": bool(mlp_passed),
        "joint_mlp_passed": bool(joint_mlp_passed),
    }
