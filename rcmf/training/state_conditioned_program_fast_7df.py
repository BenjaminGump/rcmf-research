from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass
from typing import Any

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.training.pair_grounding_5d import spearman
from rcmf.training.state_conditioned_program_7d import (
    WeightedFactorizedTransitionField,
    canonical_sha256,
    frozen_selector_choice,
    parent_normalized_weight,
    stable_key,
    stratified_state_subset,
)
from rcmf.training.state_conditioned_transition_6b import DenseTower


FAST_PROGRAM_VERSION = "observation_excluded_state_conditioned_program_7df_v1"
FAST_PAIR_MANIFEST_VERSION = "bounded_program_pair_manifest_7df_v1"
PRIMARY_TRANSITION_VIEWS = (
    "source_task_goal",
    "pre_action_state",
    "complete_action",
)
OUTCOME_TRANSITION_VIEWS = (
    "post_action_observation",
    "full_transition_global",
)
POOLING_RULES = ("token_mean", "final_token")
COMPILED_ONE_STEP_CONDITIONS = (
    "H1_compiled_full_factorized",
    "H2_compiled_static_only",
    "H3_compiled_shuffled_transition",
    "H4_zero_program",
)


def transition_view_layout(
    view_names: Sequence[str], pooling_rules: Sequence[str] = POOLING_RULES
) -> list[tuple[str, str]]:
    return [
        (str(view_name), str(pooling_rule))
        for view_name in view_names
        for pooling_rule in pooling_rules
    ]


def select_transition_program_inputs(
    values: Tensor,
    *,
    view_names: Sequence[str],
    pooling_rules: Sequence[str] = POOLING_RULES,
    include_outcome: bool,
) -> tuple[Tensor, dict[str, Any]]:
    if values.ndim != 3:
        raise ValueError("Transition representations must have shape [batch, views, dim]")
    layout = transition_view_layout(view_names, pooling_rules)
    if values.shape[1] != len(layout):
        raise ValueError(
            f"Transition representation view count {values.shape[1]} != {len(layout)}"
        )
    allowed = set(PRIMARY_TRANSITION_VIEWS)
    if include_outcome:
        allowed.update(OUTCOME_TRANSITION_VIEWS)
    selected_indices = [
        index for index, (view_name, _) in enumerate(layout) if view_name in allowed
    ]
    expected = 10 if include_outcome else 6
    if len(selected_indices) != expected:
        raise ValueError(
            f"Expected {expected} transition vectors, selected {len(selected_indices)}"
        )
    selected = values.index_select(
        1, torch.tensor(selected_indices, device=values.device, dtype=torch.long)
    )
    selected_layout = [layout[index] for index in selected_indices]
    provenance = {
        "format": "transition_program_input_provenance_7df_v1",
        "include_outcome": bool(include_outcome),
        "selected_vector_count": len(selected_layout),
        "selected_views": [
            {"view": view_name, "pooling": pooling}
            for view_name, pooling in selected_layout
        ],
        "post_action_observation_accessed": any(
            view == "post_action_observation" for view, _ in selected_layout
        ),
        "full_transition_global_accessed": any(
            view == "full_transition_global" for view, _ in selected_layout
        ),
    }
    provenance["provenance_sha256"] = canonical_sha256(provenance)
    return selected, provenance


def _flatten_views(values: Tensor) -> Tensor:
    if values.ndim != 3:
        raise ValueError("Multi-view representations must have shape [batch, views, dim]")
    return values.flatten(start_dim=1)


class FactorizedProgramFast(nn.Module):
    def __init__(
        self,
        *,
        state_vector_count: int,
        transition_view_names: Sequence[str],
        representation_dim: int,
        controller_rank: int = 16,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
        include_outcome: bool = False,
    ) -> None:
        super().__init__()
        self.transition_view_names = tuple(str(value) for value in transition_view_names)
        self.include_outcome = bool(include_outcome)
        self.controller_rank = int(controller_rank)
        self.program_dim = int(program_dim)
        state_dim = int(state_vector_count) * int(representation_dim)
        transition_vectors = 10 if include_outcome else 6
        transition_dim = transition_vectors * int(representation_dim)
        self.state_controller = DenseTower(
            state_dim,
            self.controller_rank,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.static_program_head = DenseTower(
            transition_dim,
            self.program_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.conditional_basis_head = DenseTower(
            transition_dim,
            self.controller_rank * self.program_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )

    def transition_inputs(self, transition_views: Tensor) -> tuple[Tensor, dict[str, Any]]:
        selected, provenance = select_transition_program_inputs(
            transition_views,
            view_names=self.transition_view_names,
            include_outcome=self.include_outcome,
        )
        return _flatten_views(selected), provenance

    def components(self, state_views: Tensor, transition_views: Tensor) -> dict[str, Tensor]:
        transition, _ = self.transition_inputs(transition_views)
        state = _flatten_views(state_views)
        controller = self.state_controller(state)
        static = self.static_program_head(transition)
        basis = self.conditional_basis_head(transition).view(
            -1, self.controller_rank, self.program_dim
        )
        conditional = torch.einsum("bg,bgp->bp", controller, basis)
        return {
            "controller": controller,
            "static": static,
            "basis": basis,
            "conditional": conditional,
            "z": static + conditional,
        }

    def input_provenance(self) -> dict[str, Any]:
        dummy = torch.zeros(1, len(self.transition_view_names) * 2, 1)
        _, provenance = self.transition_inputs(dummy)
        return provenance

    def forward(self, state_views: Tensor, transition_views: Tensor) -> Tensor:
        return self.components(state_views, transition_views)["z"]


class StaticProgramFast(nn.Module):
    def __init__(
        self,
        *,
        transition_view_names: Sequence[str],
        representation_dim: int,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.transition_view_names = tuple(str(value) for value in transition_view_names)
        self.head = DenseTower(
            6 * int(representation_dim),
            int(program_dim),
            hidden_dim=hidden_dim,
            dropout=dropout,
        )

    def forward(self, state_views: Tensor, transition_views: Tensor) -> Tensor:
        del state_views
        selected, _ = select_transition_program_inputs(
            transition_views,
            view_names=self.transition_view_names,
            include_outcome=False,
        )
        return self.head(_flatten_views(selected))


class PairMLPProgramFast(nn.Module):
    def __init__(
        self,
        *,
        state_vector_count: int,
        transition_view_names: Sequence[str],
        representation_dim: int,
        program_dim: int = 128,
        hidden_dim: int = 256,
        dropout: float = 0.05,
    ) -> None:
        super().__init__()
        self.transition_view_names = tuple(str(value) for value in transition_view_names)
        self.state_projection = DenseTower(
            int(state_vector_count) * int(representation_dim),
            hidden_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.transition_projection = DenseTower(
            6 * int(representation_dim),
            hidden_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
        )
        self.pair_head = DenseTower(
            3 * hidden_dim,
            int(program_dim),
            hidden_dim=hidden_dim,
            dropout=dropout,
        )

    def forward(self, state_views: Tensor, transition_views: Tensor) -> Tensor:
        selected, _ = select_transition_program_inputs(
            transition_views,
            view_names=self.transition_view_names,
            include_outcome=False,
        )
        state = self.state_projection(_flatten_views(state_views))
        transition = self.transition_projection(_flatten_views(selected))
        return self.pair_head(torch.cat((state, transition, state * transition), dim=-1))


class FreeIDProgramFast(nn.Module):
    def __init__(self, transition_ids: Sequence[str], program_dim: int = 128) -> None:
        super().__init__()
        ordered = sorted(set(str(value) for value in transition_ids))
        self.positions = {value: index for index, value in enumerate(ordered)}
        self.rows = nn.Embedding(len(ordered), int(program_dim))
        nn.init.zeros_(self.rows.weight)

    def forward_ids(self, transition_ids: Sequence[str], *, device: torch.device) -> Tensor:
        output = torch.zeros(
            len(transition_ids),
            self.rows.embedding_dim,
            device=device,
            dtype=self.rows.weight.dtype,
        )
        known_positions = [
            (row, self.positions[str(transition_id)])
            for row, transition_id in enumerate(transition_ids)
            if str(transition_id) in self.positions
        ]
        if known_positions:
            rows, positions = zip(*known_positions, strict=True)
            output[list(rows)] = self.rows(
                torch.tensor(positions, device=device, dtype=torch.long)
            )
        return output


def transition_boundary_invariance(
    model: FactorizedProgramFast,
    *,
    state_views: Tensor,
    transition_views: Tensor,
    observation_permutation: Tensor,
) -> dict[str, Any]:
    if model.include_outcome:
        raise ValueError("Primary invariance applies only to observation-excluded models")
    layout = transition_view_layout(model.transition_view_names)
    observation_indices = [
        index
        for index, (view_name, _) in enumerate(layout)
        if view_name in OUTCOME_TRANSITION_VIEWS
    ]
    shuffled = transition_views.clone()
    shuffled[:, observation_indices] = transition_views.index_select(
        0, observation_permutation.to(transition_views.device)
    )[:, observation_indices]
    model.eval()
    with torch.no_grad():
        original = model.components(state_views, transition_views)
        changed = model.components(state_views, shuffled)
    return {
        "static_program_unchanged": bool(torch.equal(original["static"], changed["static"])),
        "conditional_basis_unchanged": bool(torch.equal(original["basis"], changed["basis"])),
        "pair_latent_unchanged": bool(torch.equal(original["z"], changed["z"])),
        "observation_indices": observation_indices,
    }


def _choice(row: Mapping[str, Any], role: str) -> dict[str, Any]:
    state_id = str(row["state_example_id"])
    transition_id = str(row["transition_id"])
    return {
        "state_example_id": state_id,
        "state_task_id": str(row["state_task_id"]),
        "transition_id": transition_id,
        "transition_parent_id": str(row["transition_parent_id"]),
        "transition_parent_task_id": str(row["transition_parent_task_id"]),
        "signature_class_id": str(row["signature_class_id"]),
        "pair_id": f"{state_id}::transition::{transition_id}",
        "pair_role": str(role),
        "cell": "A",
        "procedural_tier": int(row.get("procedural_tier", -1)),
        "exact_api_sequence": bool(row.get("exact_api_sequence", False)),
        "selection_uses_behavioral_outcomes": False,
    }


def _manifest(
    rows: Sequence[Mapping[str, Any]], *, cell: str, seed: int
) -> dict[str, Any]:
    copied = [dict(row) for row in rows]
    keys = [(str(row["pair_id"]), str(row["cell"])) for row in copied]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Fast {cell} manifest contains duplicate rows")
    payload = {
        "format": FAST_PAIR_MANIFEST_VERSION,
        "cell": str(cell),
        "seed": int(seed),
        "pair_count": len(copied),
        "state_count": len({str(row["state_example_id"]) for row in copied}),
        "task_count": len({str(row["state_task_id"]) for row in copied}),
        "transition_count": len({str(row["transition_id"]) for row in copied}),
        "parent_count": len({str(row["transition_parent_id"]) for row in copied}),
        "role_counts": dict(
            sorted(Counter(str(row["pair_role"]) for row in copied).items())
        ),
        "pairs": copied,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def build_bounded_a_pairs(
    *,
    labels_a: Sequence[Mapping[str, Any]],
    scalar_utilities: Mapping[tuple[str, str], float],
    scores: Tensor,
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
    transition_token_counts: Mapping[str, int],
    classes: Mapping[str, Mapping[str, Any]],
    target_size: int,
    seed: int,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in labels_a:
        grouped[str(row["state_example_id"])].append(row)
    state_position = {
        str(value): index for index, value in enumerate(ordered_state_ids)
    }
    transition_position = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    state_top: dict[str, dict[str, Any]] = {}
    candidate_pool: list[dict[str, Any]] = []
    for state_id, rows in grouped.items():
        top = frozen_selector_choice(
            rows=rows,
            score_row=scores[state_position[state_id]],
            transition_positions=transition_position,
            transition_token_counts=transition_token_counts,
            classes=classes,
        )
        top_row = next(
            row
            for row in rows
            if str(row["transition_id"]) == top["transition_id"]
        )
        state_top[state_id] = _choice(top_row, "selector_useful")
        candidate_pool.append(state_top[state_id])

        high = min(
            rows,
            key=lambda row: (
                -int(row.get("procedural_tier", 0)),
                -int(bool(row.get("exact_api_sequence", False))),
                stable_key(seed, "high", state_id, row["transition_id"]),
            ),
        )
        candidate_pool.append(_choice(high, "procedural_useful"))
        scalar_rows = [
            row
            for row in rows
            if (state_id, str(row["transition_id"])) in scalar_utilities
        ]
        if scalar_rows:
            neutral = min(
                scalar_rows,
                key=lambda row: (
                    abs(scalar_utilities[(state_id, str(row["transition_id"]))]),
                    stable_key(seed, "neutral", state_id, row["transition_id"]),
                ),
            )
            harmful = min(
                scalar_rows,
                key=lambda row: (
                    scalar_utilities[(state_id, str(row["transition_id"]))],
                    stable_key(seed, "harmful", state_id, row["transition_id"]),
                ),
            )
            candidate_pool.append(_choice(neutral, "neutral_cached_utility"))
            candidate_pool.append(_choice(harmful, "harmful_cached_utility"))
        else:
            harmful_rows = [
                row
                for row in rows
                if int(row.get("procedural_tier", 0)) == 0
                or bool(row.get("state_stage_conflict_count", 0))
            ]
            if harmful_rows:
                harmful = min(
                    harmful_rows,
                    key=lambda row: stable_key(
                        seed, "harmful-proxy", state_id, row["transition_id"]
                    ),
                )
                candidate_pool.append(_choice(harmful, "harmful_procedural_proxy"))

    ordered_states = sorted(
        grouped,
        key=lambda state_id: stable_key(seed, "swap-state", state_id),
    )
    for index, state_id in enumerate(ordered_states):
        other = ordered_states[(index + 1) % len(ordered_states)]
        other_transition = state_top[other]["transition_id"]
        swap_row = next(
            (
                row
                for row in grouped[state_id]
                if str(row["transition_id"]) == other_transition
            ),
            None,
        )
        if swap_row is not None:
            candidate_pool.append(_choice(swap_row, "memory_swap"))

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()

    def add(row: Mapping[str, Any] | None) -> bool:
        if row is None or len(selected) >= int(target_size):
            return False
        key = (str(row["state_example_id"]), str(row["transition_id"]))
        if key in selected_keys:
            return False
        selected.append(dict(row))
        selected_keys.add(key)
        return True

    by_task: dict[str, list[str]] = defaultdict(list)
    for state_id, rows in grouped.items():
        by_task[str(rows[0]["state_task_id"])].append(state_id)
    for task_id in sorted(
        by_task, key=lambda value: stable_key(seed, "task", value)
    ):
        state_id = min(
            by_task[task_id],
            key=lambda value: stable_key(seed, "task-state", task_id, value),
        )
        add(state_top[state_id])

    all_parents = {str(row["transition_parent_id"]) for row in labels_a}
    for parent_id in sorted(all_parents):
        if parent_id in {str(row["transition_parent_id"]) for row in selected}:
            continue
        add(
            min(
                (
                    row
                    for row in candidate_pool
                    if str(row["transition_parent_id"]) == parent_id
                ),
                key=lambda row: stable_key(seed, "parent", parent_id, row["pair_id"]),
                default=None,
            )
        )

    role_order = (
        "neutral_cached_utility",
        "harmful_cached_utility",
        "memory_swap",
        "procedural_useful",
        "selector_useful",
        "harmful_procedural_proxy",
    )
    rows_by_role = {
        role: sorted(
            (row for row in candidate_pool if row["pair_role"] == role),
            key=lambda row: stable_key(seed, role, row["pair_id"]),
        )
        for role in role_order
    }
    role_offsets = {role: 0 for role in role_order}
    while len(selected) < int(target_size):
        added_this_round = False
        for role in role_order:
            rows = rows_by_role[role]
            while role_offsets[role] < len(rows):
                row = rows[role_offsets[role]]
                role_offsets[role] += 1
                if add(row):
                    added_this_round = True
                    break
            if len(selected) >= int(target_size):
                break
        if not added_this_round:
            break
    if len(selected) < int(target_size):
        for row in sorted(
            candidate_pool,
            key=lambda value: stable_key(seed, "fill", value["pair_id"]),
        ):
            add(row)
            if len(selected) >= int(target_size):
                break
    if len(selected) != int(target_size):
        raise ValueError(f"Requested {target_size} A pairs, selected {len(selected)}")
    selected.sort(
        key=lambda row: (
            str(row["state_example_id"]),
            str(row["transition_id"]),
        )
    )
    result = _manifest(selected, cell="A", seed=seed)
    required = {
        "all_train_tasks": result["task_count"] == 37,
        "neutral": result["role_counts"].get("neutral_cached_utility", 0) > 0,
        "harmful_or_swap": sum(
            result["role_counts"].get(name, 0)
            for name in (
                "harmful_cached_utility",
                "harmful_procedural_proxy",
                "memory_swap",
            )
        )
        > 0,
        "selected_useful": sum(
            result["role_counts"].get(name, 0)
            for name in ("selector_useful", "procedural_useful")
        )
        > 0,
    }
    if not all(required.values()):
        raise ValueError(f"Bounded A coverage failed: {required}")
    result["coverage_checks"] = required
    result["manifest_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "manifest_sha256"}
    )
    return result


def fast_field_validation(seed: int = 25081) -> dict[str, Any]:
    generator = torch.Generator().manual_seed(int(seed))
    dimensions = (5, 3, 7)
    field = WeightedFactorizedTransitionField(*dimensions)
    parent_sizes = {"p0": 2, "p1": 1}
    rows = [
        (
            f"t{index}",
            parent,
            parent_normalized_weight(parent_sizes[parent]),
            torch.randn(dimensions[0], generator=generator, dtype=torch.float64),
            torch.randn(dimensions[2], generator=generator, dtype=torch.float64),
            torch.randn(dimensions[1], dimensions[2], generator=generator, dtype=torch.float64),
        )
        for index, parent in enumerate(("p0", "p0", "p1"))
    ]
    for row in rows:
        field.add_fast(*row)
    query = torch.randn(dimensions[0], generator=generator, dtype=torch.float64)
    controller = torch.randn(dimensions[1], generator=generator, dtype=torch.float64)
    fast_v0, fast_t = field.V0.clone(), field.T.clone()
    explicit = field.explicit_read(query, controller)
    fast_read = field.read(query, controller)
    field.audit_rebuild()
    audit_v0, audit_t = field.V0.clone(), field.T.clone()
    field.remove_fast("t2")
    field.add_fast(*rows[2])
    remove_restore = torch.allclose(field.V0, audit_v0, atol=1.0e-12) and torch.allclose(
        field.T, audit_t, atol=1.0e-12
    )
    replacement = list(rows[1])
    replacement[3] = torch.randn(dimensions[0], generator=generator, dtype=torch.float64)
    field.replace_fast(*replacement)
    replace_explicit = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1.0e-11
    )
    field.replace_fast(*rows[1])
    removed = field.remove_parent_fast("p0")
    parent_explicit = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1.0e-11
    )
    for row in rows[:2]:
        field.add_fast(*row)
    checks = {
        "fast_equals_explicit_sum": bool(torch.allclose(fast_read, explicit, atol=1.0e-11)),
        "fast_equals_audit_rebuild": bool(
            torch.allclose(fast_v0, audit_v0, atol=1.0e-12)
            and torch.allclose(fast_t, audit_t, atol=1.0e-12)
        ),
        "add_remove_restore": bool(remove_restore),
        "replace_matches_explicit": bool(replace_explicit),
        "parent_remove_matches_explicit": bool(parent_explicit and removed == ["t0", "t1"]),
        "parent_restore": bool(
            torch.allclose(field.V0, audit_v0, atol=1.0e-12)
            and torch.allclose(field.T, audit_t, atol=1.0e-12)
        ),
        "fixed_read_tensor_shape": field.runtime_shapes
        == {"V0": [dimensions[0], dimensions[2]], "T": list(dimensions)},
        "parent_normalized_default": parent_normalized_weight(2) == 0.5,
        "standalone_default": parent_normalized_weight(None) == 1.0,
    }
    return {
        "format": "fast_incremental_field_validation_7df_v1",
        "checks": checks,
        "passed": all(checks.values()),
        "runtime_shapes": field.runtime_shapes,
    }


def build_compiled_one_step_manifest(
    field_selected_rows: Sequence[Mapping[str, Any]], *, seed: int = 25096
) -> dict[str, Any]:
    """Freeze H1-H4 inputs without consulting Qwen or AppWorld outcomes."""
    rows_by_state: dict[str, Mapping[str, Any]] = {}
    for row in field_selected_rows:
        if str(row.get("condition_name")) != "F3_deployment_e_field_raw":
            continue
        state_id = str(row["state_example_id"])
        if state_id in rows_by_state:
            raise ValueError(f"Duplicate F3 selection for {state_id}")
        rows_by_state[state_id] = row
    if not rows_by_state:
        raise ValueError("No frozen F3 selections were supplied")
    transition_ids = sorted(
        {str(row["transition_id"]) for row in rows_by_state.values()}
    )
    if len(transition_ids) < 2:
        raise ValueError("Shuffled-transition control requires two transition IDs")

    conditions: list[dict[str, Any]] = []
    for state_id, source in sorted(rows_by_state.items()):
        own_transition = str(source["transition_id"])
        shuffled_transition = min(
            (value for value in transition_ids if value != own_transition),
            key=lambda value: stable_key(seed, "one-step-shuffle", state_id, value),
        )
        for name in COMPILED_ONE_STEP_CONDITIONS:
            program_transition = (
                shuffled_transition
                if name == "H3_compiled_shuffled_transition"
                else own_transition
            )
            payload = {
                "format": "compiled_program_one_step_condition_7df_v1",
                "condition_name": name,
                "state_example_id": state_id,
                "state_task_id": str(source["state_task_id"]),
                "state_step_id": int(source["state_step_id"]),
                "audit_stratum": str(source["audit_stratum"]),
                "api_documentation_action": bool(
                    source.get("api_documentation_action", False)
                ),
                "procedural_tier": source.get("procedural_tier"),
                "signature_class_id": source.get("signature_class_id"),
                "selector_transition_id": own_transition,
                "program_transition_id": program_transition,
                "prompt_kind": "bare_compiled_program",
                "student_prompt_contains_raw_transition": False,
                "selection_source": "frozen_exp025cr_deployment_e",
                "selection_uses_qwen_or_appworld_outcomes": False,
                "valid_for_generation": True,
            }
            payload["condition_key"] = canonical_sha256(payload)
            conditions.append(payload)
    manifest = {
        "format": "compiled_program_one_step_manifest_7df_v1",
        "state_count": len(rows_by_state),
        "condition_count": len(conditions),
        "condition_name_counts": dict(
            sorted(Counter(row["condition_name"] for row in conditions).items())
        ),
        "raw_transition_prompt_count": sum(
            bool(row["student_prompt_contains_raw_transition"])
            for row in conditions
        ),
        "conditions": conditions,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def decoded_effect_stability(
    first_z: Tensor,
    second_z: Tensor,
    decoder_weight: Tensor,
    first_utilities: Sequence[float],
    second_utilities: Sequence[float],
) -> dict[str, Any]:
    first_delta = first_z.to(torch.float64) @ decoder_weight.to(torch.float64).T
    second_delta = second_z.to(torch.float64) @ decoder_weight.to(torch.float64).T
    cosine = F.cosine_similarity(first_delta, second_delta, dim=-1)
    signs = [
        (left == 0.0 and right == 0.0) or (left > 0.0) == (right > 0.0)
        for left, right in zip(first_utilities, second_utilities, strict=True)
    ]
    result = {
        "decoded_delta_cosine_mean": float(cosine.mean()),
        "decoded_delta_cosine_minimum": float(cosine.min()),
        "repeat_utility_spearman": spearman(first_utilities, second_utilities),
        "repeat_sign_agreement": sum(signs) / len(signs),
    }
    result["passed"] = bool(
        result["decoded_delta_cosine_mean"] >= 0.85
        and (result["repeat_utility_spearman"] or -1.0) >= 0.90
        and result["repeat_sign_agreement"] >= 0.90
    )
    return result


@dataclass(frozen=True)
class RuntimeScenario:
    teacher_seconds: float
    optimization_seconds: float
    evaluation_seconds: float
    one_step_seconds: float

    @property
    def total_seconds(self) -> float:
        return (
            self.teacher_seconds
            + self.optimization_seconds
            + self.evaluation_seconds
            + self.one_step_seconds
        )


def fast_runtime_projection(
    *,
    unique_pairs: int,
    unique_states: int,
    pair_updates: int,
    repair_rows: int,
    repair_updates: int,
    stability_pairs: int,
    stability_updates: int,
    evaluation_forwards: int,
    one_step_conditions: int,
    rates: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name in ("best", "expected", "conservative"):
        values = rates[name]
        teacher = (int(unique_pairs) + int(unique_states)) * float(values["forward"])
        updates = (
            int(unique_pairs) * int(pair_updates)
            + int(repair_rows) * int(repair_updates)
            + int(stability_pairs) * int(stability_updates)
        )
        optimization = updates * float(values["backward"])
        evaluation = int(evaluation_forwards) * float(values["forward"])
        one_step = int(one_step_conditions) * float(values["generation"])
        scenario = RuntimeScenario(teacher, optimization, evaluation, one_step)
        output[name] = {
            "teacher_cache_seconds": teacher,
            "optimization_seconds": optimization,
            "evaluation_seconds": evaluation,
            "conditional_one_step_seconds": one_step,
            "total_seconds": scenario.total_seconds,
            "h100_hours": scenario.total_seconds / 3600.0,
            "pair_latent_updates": int(unique_pairs) * int(pair_updates),
            "decoder_repair_updates": int(repair_rows) * int(repair_updates),
            "stability_updates": int(stability_pairs) * int(stability_updates),
        }
    return {"scenarios": output}
