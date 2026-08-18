from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor, nn

from rcmf.training.oracle_decoder_5fc import LinearDeltaDecoder
from rcmf.training.signature_balanced_field_7c import (
    select_scoreable_class_exemplar,
)


PROGRAM_VERSION = "clean_state_conditioned_transition_program_7d_v1"
PAIR_MANIFEST_VERSION = "clean_program_distillation_pair_manifest_7d_v1"
DECODER_VERSION = "clean_random_orthonormal_behavioral_decoder_7d_v1"
WEIGHTED_FIELD_VERSION = "inverse_signature_weighted_program_field_7d_v1"
SELECTOR_CANDIDATE_FIELDS = (
    "state_example_id",
    "state_task_id",
    "transition_id",
    "transition_parent_id",
    "transition_parent_task_id",
    "signature_class_id",
)


def selector_candidate_projection(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Strip every supervision field before frozen-selector pair selection."""

    output = []
    for row in rows:
        missing = [field for field in SELECTOR_CANDIDATE_FIELDS if field not in row]
        if missing:
            raise ValueError(f"Selector candidate is missing fields: {missing}")
        output.append({field: row[field] for field in SELECTOR_CANDIDATE_FIELDS})
    return output


def frozen_pair_context_status(row: Mapping[str, Any]) -> dict[str, Any]:
    """Mark over-context teacher rows missing without changing the frozen pair."""

    output = dict(row)
    over_context = bool(output["over_context"])
    output.update(
        {
            "valid_for_teacher_cache": not over_context,
            "score_status": (
                "over_context_missing" if over_context else "scoreable"
            ),
            "context_substitution": False,
            "truncated": False,
            "cross_class_substitution": False,
        }
    )
    return output


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_key(seed: int, namespace: str, *values: Any) -> str:
    payload = ":".join(str(value) for value in (seed, namespace, *values))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _class_scores_for_state(
    rows: Sequence[Mapping[str, Any]],
    score_row: Tensor,
    transition_positions: Mapping[str, int],
) -> list[tuple[str, float, list[Mapping[str, Any]]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["signature_class_id"])].append(row)
    output = []
    for class_id, members in grouped.items():
        indices = [transition_positions[str(row["transition_id"])] for row in members]
        score = float(score_row[indices].to(torch.float64).mean())
        output.append((class_id, score, members))
    return sorted(output, key=lambda item: (-item[1], item[0]))


def frozen_selector_choice(
    *,
    rows: Sequence[Mapping[str, Any]],
    score_row: Tensor,
    transition_positions: Mapping[str, int],
    classes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Choose a class from frozen scores, then its immutable canonical member."""

    ranked = _class_scores_for_state(rows, score_row, transition_positions)
    if not ranked:
        raise ValueError("No legal transition classes are available for the state")
    class_id, class_score, members = ranked[0]
    member_by_id = {str(row["transition_id"]): row for row in members}
    class_row = classes[class_id]
    token_counts = {
        str(transition_id): int(token_count)
        for transition_id, token_count in zip(
            class_row["member_transition_ids"],
            class_row["serialized_token_counts"],
            strict=True,
        )
    }
    exemplar = select_scoreable_class_exemplar(
        class_row=class_row,
        legal_rows=members,
        transitions_by_id={
            transition_id: {"teacher_section_tokens": token_count}
            for transition_id, token_count in token_counts.items()
        },
    )
    selected_id = str(exemplar["transition_id"])
    rule = f"frozen_top_class__{exemplar['selection_rule']}"
    selected = member_by_id[selected_id]
    return {
        "state_example_id": str(selected["state_example_id"]),
        "state_task_id": str(selected["state_task_id"]),
        "transition_id": selected_id,
        "transition_parent_id": str(selected["transition_parent_id"]),
        "transition_parent_task_id": str(selected["transition_parent_task_id"]),
        "signature_class_id": class_id,
        "selector_class_score": class_score,
        "selection_rule": rule,
        "selection_uses_heldout_labels": False,
    }


def _role_candidates_for_state(
    *,
    rows_a: Sequence[Mapping[str, Any]],
    rows_all_train_transitions: Sequence[Mapping[str, Any]],
    score_row: Tensor,
    transition_positions: Mapping[str, int],
    classes: Mapping[str, Mapping[str, Any]],
    seed: int,
) -> dict[str, dict[str, Any]]:
    by_id_a = {str(row["transition_id"]): row for row in rows_a}
    output: dict[str, dict[str, Any]] = {}

    p1 = frozen_selector_choice(
        rows=rows_a,
        score_row=score_row,
        transition_positions=transition_positions,
        classes=classes,
    )
    output["P1_frozen_strict_b_top"] = p1

    p2 = frozen_selector_choice(
        rows=rows_all_train_transitions,
        score_row=score_row,
        transition_positions=transition_positions,
        classes=classes,
    )
    if p2["transition_id"] in by_id_a:
        output["P2_frozen_deployment_top_train_parent"] = p2

    high_tier = sorted(
        rows_a,
        key=lambda row: (
            -int(row["procedural_tier"]),
            -int(bool(row["exact_api_sequence"])),
            -int(bool(row["state_stage_compatible"])),
            stable_key(seed, "p3", row["state_example_id"], row["transition_id"]),
        ),
    )
    if high_tier:
        row = high_tier[0]
        output["P3_procedural_oracle_high_tier"] = _choice_from_label(row)

    hard = [
        row
        for row in rows_a
        if bool(row["same_coarse_action_type"])
        and not bool(row["canonical_action_schema_match"])
    ]
    if hard:
        row = min(
            hard,
            key=lambda value: (
                int(value["procedural_tier"]),
                -int(value["state_stage_conflict_count"]),
                stable_key(seed, "p4", value["state_example_id"], value["transition_id"]),
            ),
        )
        output["P4_same_intent_hard_negative"] = _choice_from_label(row)

    unrelated = [
        row
        for row in rows_a
        if int(row["procedural_tier"]) == 0
        and not bool(row["same_primary_app"])
    ]
    if unrelated:
        row = min(
            unrelated,
            key=lambda value: stable_key(
                seed, "p5", value["state_example_id"], value["transition_id"]
            ),
        )
        output["P5_unrelated_tier0"] = _choice_from_label(row)

    ranked_classes = _class_scores_for_state(rows_a, score_row, transition_positions)
    for class_id, _, members in ranked_classes:
        parents = {str(row["transition_parent_id"]) for row in members}
        class_row = classes[class_id]
        canonical_parent = str(class_row.get("canonical_parent_id") or "")
        alternatives = [
            row
            for row in members
            if len(parents) > 1
            and str(row["transition_parent_id"]) != canonical_parent
        ]
        if not alternatives:
            continue
        row = min(
            alternatives,
            key=lambda value: stable_key(
                seed, "p6", value["state_example_id"], value["transition_id"]
            ),
        )
        output["P6_alternate_same_signature_parent"] = _choice_from_label(row)
        break
    return output


def _choice_from_label(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state_example_id": str(row["state_example_id"]),
        "state_task_id": str(row["state_task_id"]),
        "transition_id": str(row["transition_id"]),
        "transition_parent_id": str(row["transition_parent_id"]),
        "transition_parent_task_id": str(row["transition_parent_task_id"]),
        "signature_class_id": str(row["signature_class_id"]),
        "procedural_tier": int(row["procedural_tier"]),
        "exact_api_sequence": bool(row["exact_api_sequence"]),
        "selection_uses_heldout_labels": False,
    }


def build_program_training_pairs(
    *,
    labels_a: Sequence[Mapping[str, Any]],
    deployment_candidate_rows: Sequence[Mapping[str, Any]],
    scores: Tensor,
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
    classes: Mapping[str, Mapping[str, Any]],
    target_size: int,
    maximum_size: int,
    seed: int,
) -> dict[str, Any]:
    """Build A-only selected anchors plus paired contrast and coverage rows."""

    state_position = {str(value): index for index, value in enumerate(ordered_state_ids)}
    transition_position = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    a_by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    all_by_state: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in labels_a:
        a_by_state[str(row["state_example_id"])].append(row)
        all_by_state[str(row["state_example_id"])].append(row)
    for row in deployment_candidate_rows:
        all_by_state[str(row["state_example_id"])].append(row)
    candidates: dict[str, dict[str, dict[str, Any]]] = {}
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    for state_id in sorted(a_by_state):
        state_candidates = _role_candidates_for_state(
            rows_a=a_by_state[state_id],
            rows_all_train_transitions=all_by_state[state_id],
            score_row=scores[state_position[state_id]],
            transition_positions=transition_position,
            classes=classes,
            seed=seed,
        )
        if not state_candidates:
            raise ValueError(f"No A-only role candidate for {state_id}")
        candidates[state_id] = state_candidates
        role = "P1_frozen_strict_b_top"
        row = {
            **state_candidates[role],
            "cell": "A",
            "pair_role": role,
            "pair_id": f"{state_id}::transition::{state_candidates[role]['transition_id']}",
        }
        selected.append(row)
        selected_keys.add((state_id, str(row["transition_id"])))

    extras: list[dict[str, Any]] = []
    for state_id, state_candidates in candidates.items():
        for role, candidate in state_candidates.items():
            key = (state_id, str(candidate["transition_id"]))
            if key in selected_keys:
                continue
            extras.append(
                {
                    **candidate,
                    "cell": "A",
                    "pair_role": role,
                    "pair_id": f"{state_id}::transition::{candidate['transition_id']}",
                }
            )
    extras.sort(
        key=lambda row: (
            stable_key(
                seed,
                "training-extra",
                row["pair_role"],
                row["state_example_id"],
                row["transition_id"],
            ),
            str(row["pair_id"]),
        )
    )

    def add_candidate(candidate: Mapping[str, Any] | None) -> bool:
        if candidate is None or len(selected) >= int(target_size):
            return False
        key = (str(candidate["state_example_id"]), str(candidate["transition_id"]))
        if key in selected_keys:
            return False
        selected.append(dict(candidate))
        selected_keys.add(key)
        return True

    missing_parents = {
        str(row["transition_parent_id"]) for row in labels_a
    } - {str(row["transition_parent_id"]) for row in selected}
    for parent_id in sorted(missing_parents):
        candidate = next(
            (
                row
                for row in extras
                if str(row["transition_parent_id"]) == parent_id
                and (str(row["state_example_id"]), str(row["transition_id"]))
                not in selected_keys
            ),
            None,
        )
        add_candidate(candidate)

    for role in (
        "P2_frozen_deployment_top_train_parent",
        "P3_procedural_oracle_high_tier",
        "P4_same_intent_hard_negative",
        "P5_unrelated_tier0",
        "P6_alternate_same_signature_parent",
    ):
        add_candidate(next((row for row in extras if row["pair_role"] == role), None))

    state_order = stratified_state_subset(
        [rows[0] for rows in a_by_state.values()],
        count=len(a_by_state),
        seed=seed,
        namespace="paired-contrast-state",
    )
    for state_id in state_order:
        negative_roles = [
            role
            for role in (
                "P4_same_intent_hard_negative",
                "P5_unrelated_tier0",
            )
            if role in candidates[state_id]
        ]
        if not negative_roles:
            continue
        role = min(
            negative_roles,
            key=lambda value: stable_key(seed, "paired-negative-role", state_id, value),
        )
        candidate = candidates[state_id][role]
        add_candidate(
            {
                **candidate,
                "cell": "A",
                "pair_role": role,
                "pair_id": f"{state_id}::transition::{candidate['transition_id']}",
            }
        )

    for candidate in extras:
        if len(selected) >= int(target_size):
            break
        add_candidate(candidate)
    if len(selected) > int(maximum_size):
        raise ValueError("Coverage constraints exceed maximum A pilot size")
    if len(selected) < int(target_size):
        raise ValueError("Insufficient distinct A-only role pairs")
    selected.sort(key=lambda row: (str(row["state_example_id"]), str(row["pair_id"])))
    return _manifest_payload(selected, seed=seed, selection="A_role_stratified")


def stratified_state_subset(
    rows: Sequence[Mapping[str, Any]], *, count: int, seed: int, namespace: str
) -> list[str]:
    by_task: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        state_id = str(row["state_example_id"])
        if state_id not in by_task[str(row["state_task_id"])]:
            by_task[str(row["state_task_id"])].append(state_id)
    for task_id in by_task:
        by_task[task_id].sort(
            key=lambda value: stable_key(seed, namespace, task_id, value)
        )
    output: list[str] = []
    depth = 0
    task_order = sorted(
        by_task, key=lambda value: stable_key(seed, namespace, "task", value)
    )
    while len(output) < count:
        added = False
        for task_id in task_order:
            if depth < len(by_task[task_id]):
                output.append(by_task[task_id][depth])
                added = True
                if len(output) == count:
                    break
        if not added:
            break
        depth += 1
    if len(output) != count:
        raise ValueError(f"Requested {count} states but selected {len(output)}")
    return output


def build_frozen_cell_pairs(
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    scores: Tensor,
    ordered_state_ids: Sequence[str],
    ordered_transition_ids: Sequence[str],
    classes: Mapping[str, Mapping[str, Any]],
    state_count: int | None,
    cell: str,
    seed: int,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[str(row["state_example_id"])].append(row)
    all_state_rows = [values[0] for values in grouped.values()]
    if state_count is None:
        selected_states = sorted(grouped)
    else:
        selected_states = stratified_state_subset(
            all_state_rows,
            count=int(state_count),
            seed=seed,
            namespace=f"cell-{cell}",
        )
    state_position = {str(value): index for index, value in enumerate(ordered_state_ids)}
    transition_position = {
        str(value): index for index, value in enumerate(ordered_transition_ids)
    }
    output = []
    for state_id in selected_states:
        choice = frozen_selector_choice(
            rows=grouped[state_id],
            score_row=scores[state_position[state_id]],
            transition_positions=transition_position,
            classes=classes,
        )
        output.append(
            {
                **choice,
                "cell": str(cell),
                "pair_role": "frozen_selector_top_class",
                "pair_id": f"{state_id}::transition::{choice['transition_id']}",
            }
        )
    output.sort(key=lambda row: str(row["state_example_id"]))
    return _manifest_payload(output, seed=seed, selection="frozen_selector_no_labels")


def grouped_decoder_pair_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    calibration_count: int,
    heldout_count: int,
    seed: int,
) -> dict[str, Any]:
    one_by_state: dict[str, Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda value: str(value["pair_id"])):
        one_by_state.setdefault(str(row["state_example_id"]), row)
    representatives = list(one_by_state.values())
    heldout_states = set(
        stratified_state_subset(
            representatives,
            count=int(heldout_count),
            seed=seed,
            namespace="decoder-heldout",
        )
    )
    remaining = [
        row for row in representatives if str(row["state_example_id"]) not in heldout_states
    ]
    calibration_states = set(
        stratified_state_subset(
            remaining,
            count=int(calibration_count),
            seed=seed,
            namespace="decoder-calibration",
        )
    )
    calibration = [
        dict(row)
        for row in representatives
        if str(row["state_example_id"]) in calibration_states
    ]
    heldout = [
        dict(row)
        for row in representatives
        if str(row["state_example_id"]) in heldout_states
    ]
    calibration.sort(key=lambda row: str(row["pair_id"]))
    heldout.sort(key=lambda row: str(row["pair_id"]))
    payload = {
        "format": "clean_decoder_grouped_pair_split_7d_v1",
        "seed": int(seed),
        "calibration_pairs": calibration,
        "heldout_pairs": heldout,
        "calibration_pair_count": len(calibration),
        "heldout_pair_count": len(heldout),
        "calibration_state_count": len(calibration_states),
        "heldout_state_count": len(heldout_states),
        "state_overlap_count": len(calibration_states & heldout_states),
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _manifest_payload(
    rows: Sequence[Mapping[str, Any]], *, seed: int, selection: str
) -> dict[str, Any]:
    copied = [dict(row) for row in rows]
    pair_ids = [str(row["pair_id"]) for row in copied]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("Pair manifest contains duplicate pair IDs")
    payload = {
        "format": PAIR_MANIFEST_VERSION,
        "seed": int(seed),
        "selection": str(selection),
        "pair_count": len(copied),
        "state_count": len({str(row["state_example_id"]) for row in copied}),
        "task_count": len({str(row["state_task_id"]) for row in copied}),
        "transition_count": len({str(row["transition_id"]) for row in copied}),
        "parent_count": len({str(row["transition_parent_id"]) for row in copied}),
        "role_counts": dict(sorted(Counter(str(row["pair_role"]) for row in copied).items())),
        "states_with_multiple_pairs": sum(
            count > 1
            for count in Counter(str(row["state_example_id"]) for row in copied).values()
        ),
        "within_state_pair_comparison_count": sum(
            count * (count - 1) // 2
            for count in Counter(str(row["state_example_id"]) for row in copied).values()
        ),
        "pairs": copied,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def deterministic_random_orthonormal_decoder(
    *, latent_dim: int, output_dim: int, seed: int, dtype: torch.dtype = torch.float32
) -> LinearDeltaDecoder:
    if int(output_dim) < int(latent_dim):
        raise ValueError("Output dimension must be at least the latent dimension")
    generator = torch.Generator(device="cpu").manual_seed(int(seed))
    source = torch.randn(output_dim, latent_dim, generator=generator, dtype=torch.float64)
    q, r = torch.linalg.qr(source, mode="reduced")
    signs = torch.where(torch.diag(r) < 0, -torch.ones(latent_dim), torch.ones(latent_dim))
    q = q * signs.view(1, -1)
    decoder = LinearDeltaDecoder(latent_dim, output_dim)
    with torch.no_grad():
        decoder.linear.weight.copy_(q.to(dtype=dtype))
    return decoder


def orthonormalize_decoder_preserving_outputs_(
    decoder: LinearDeltaDecoder, latents: Tensor
) -> dict[str, Any]:
    """QR decoder columns and transform z so z @ W.T is preserved."""

    before = decoder(latents).detach().to(torch.float64)
    weight = decoder.linear.weight.detach().to(torch.float64)
    q, r = torch.linalg.qr(weight, mode="reduced")
    signs = torch.where(torch.diag(r) < 0, -torch.ones(r.shape[0]), torch.ones(r.shape[0]))
    q = q * signs.view(1, -1)
    r = signs.view(-1, 1) * r
    transformed = latents.detach().to(torch.float64) @ r.T
    with torch.no_grad():
        decoder.linear.weight.copy_(q.to(decoder.linear.weight))
        latents.copy_(transformed.to(latents))
    after = decoder(latents).detach().to(torch.float64)
    decoder_weight = decoder.linear.weight.detach().to(torch.float64)
    gram = decoder_weight.T @ decoder_weight
    identity = torch.eye(gram.shape[0], dtype=gram.dtype)
    return {
        "format": "decoder_qr_output_preservation_7d_v1",
        "maximum_absolute_output_change": float((after - before).abs().max()),
        "relative_output_change": float(
            (after - before).norm() / before.norm().clamp_min(1.0e-12)
        ),
        "maximum_orthonormality_error": float((gram - identity).abs().max()),
    }


def update_count_summary(pair_ids: Sequence[str], counts: Sequence[int]) -> dict[str, Any]:
    if len(pair_ids) != len(counts):
        raise ValueError("Pair IDs and update counters differ")
    values = [int(value) for value in counts]
    return {
        "pair_count": len(pair_ids),
        "minimum_updates_per_pair": min(values) if values else 0,
        "maximum_updates_per_pair": max(values) if values else 0,
        "mean_updates_per_pair": sum(values) / len(values) if values else 0.0,
        "all_pairs_equal": len(set(values)) <= 1,
        "per_pair_update_counts": {
            str(pair_id): int(value) for pair_id, value in zip(pair_ids, values, strict=True)
        },
    }


def assert_program_student_contract(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forbidden = {
        "raw_transition_text",
        "raw_memory_text",
        "signature_card",
        "selector_score",
        "selector_scores",
        "selector_gate",
        "selector_query",
        "selector_key",
        "selector_class_score",
        "full_bank",
        "full_field",
    }
    violations = [
        {
            "pair_id": row.get("pair_id"),
            "forbidden_keys": sorted(set(row).intersection(forbidden)),
        }
        for row in rows
        if set(row).intersection(forbidden)
    ]
    return {
        "passed": not violations,
        "violations": violations,
        "student_prompt_contains_raw_transition": False,
        "student_prompt_contains_signature_card": False,
        "selector_payload_in_program_gradient": False,
        "full_bank_used": False,
    }


@dataclass
class _WeightedFieldRecord:
    parent_id: str
    weight: Tensor
    key: Tensor
    static_program: Tensor
    conditional_basis: Tensor
    delta_v0: Tensor
    delta_t: Tensor


class WeightedFactorizedTransitionField:
    def __init__(
        self,
        key_rank: int,
        controller_rank: int,
        program_dim: int,
        *,
        dtype: torch.dtype = torch.float64,
    ) -> None:
        self.key_rank = int(key_rank)
        self.controller_rank = int(controller_rank)
        self.program_dim = int(program_dim)
        self.dtype = dtype
        self.V0 = torch.zeros(self.key_rank, self.program_dim, dtype=dtype)
        self.T = torch.zeros(
            self.key_rank, self.controller_rank, self.program_dim, dtype=dtype
        )
        self.records: dict[str, _WeightedFieldRecord] = {}

    def _rebuild(self) -> None:
        """Rebuild in transition-ID order for bitwise reversible bookkeeping."""

        ordered = [self.records[key] for key in sorted(self.records)]
        if not ordered:
            self.V0.zero_()
            self.T.zero_()
            return
        self.V0.copy_(torch.stack([record.delta_v0 for record in ordered]).sum(0))
        self.T.copy_(torch.stack([record.delta_t for record in ordered]).sum(0))

    def _record(
        self,
        parent_id: str,
        weight: float,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> _WeightedFieldRecord:
        scalar = torch.as_tensor(float(weight), dtype=self.dtype)
        key = key.detach().to(self.dtype).reshape(self.key_rank)
        static = static_program.detach().to(self.dtype).reshape(self.program_dim)
        basis = conditional_basis.detach().to(self.dtype).reshape(
            self.controller_rank, self.program_dim
        )
        return _WeightedFieldRecord(
            parent_id=str(parent_id),
            weight=scalar,
            key=key,
            static_program=static,
            conditional_basis=basis,
            delta_v0=scalar * torch.outer(key, static),
            delta_t=scalar * torch.einsum("k,gp->kgp", key, basis),
        )

    def add(
        self,
        transition_id: str,
        parent_id: str,
        weight: float,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> None:
        transition_id = str(transition_id)
        if transition_id in self.records:
            raise KeyError(f"Transition already exists: {transition_id}")
        record = self._record(
            parent_id, weight, key, static_program, conditional_basis
        )
        self.records[transition_id] = record
        self._rebuild()

    def remove(self, transition_id: str) -> None:
        self.records.pop(str(transition_id))
        self._rebuild()

    def replace(
        self,
        transition_id: str,
        parent_id: str,
        weight: float,
        key: Tensor,
        static_program: Tensor,
        conditional_basis: Tensor,
    ) -> None:
        self.remove(transition_id)
        self.add(
            transition_id,
            parent_id,
            weight,
            key,
            static_program,
            conditional_basis,
        )

    def remove_parent(self, parent_id: str) -> list[str]:
        selected = sorted(
            transition_id
            for transition_id, record in self.records.items()
            if record.parent_id == str(parent_id)
        )
        for transition_id in selected:
            self.remove(transition_id)
        return selected

    def read(self, query: Tensor, controller: Tensor) -> Tensor:
        q = query.to(self.dtype).reshape(self.key_rank)
        g = controller.to(self.dtype).reshape(self.controller_rank)
        return q @ self.V0 + torch.einsum("k,kgp,g->p", q, self.T, g)

    def explicit_read(self, query: Tensor, controller: Tensor) -> Tensor:
        q = query.to(self.dtype).reshape(self.key_rank)
        g = controller.to(self.dtype).reshape(self.controller_rank)
        output = torch.zeros(self.program_dim, dtype=self.dtype)
        for transition_id in sorted(self.records):
            record = self.records[transition_id]
            pair = record.static_program + g @ record.conditional_basis
            output.add_(record.weight * torch.dot(q, record.key) * pair)
        return output

    @property
    def runtime_shapes(self) -> dict[str, list[int]]:
        return {"V0": list(self.V0.shape), "T": list(self.T.shape)}


def weighted_field_algebra_validation(seed: int = 25075) -> dict[str, Any]:
    generator = torch.Generator().manual_seed(int(seed))
    dimensions = (7, 5, 11)
    field = WeightedFactorizedTransitionField(*dimensions)
    parents = ("p0", "p0", "p1", "p2")
    source = [
        (
            f"t{index}",
            parent,
            1.0 / (index + 1),
            torch.randn(dimensions[0], generator=generator, dtype=torch.float64),
            torch.randn(dimensions[2], generator=generator, dtype=torch.float64),
            torch.randn(
                dimensions[1], dimensions[2], generator=generator, dtype=torch.float64
            ),
        )
        for index, parent in enumerate(parents)
    ]
    query = torch.randn(dimensions[0], generator=generator, dtype=torch.float64)
    controller = torch.randn(dimensions[1], generator=generator, dtype=torch.float64)
    for item in source:
        field.add(*item)
    original_v0, original_t = field.V0.clone(), field.T.clone()
    explicit_equal = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1e-10
    )

    field.remove("t2")
    field.add(*source[2])
    remove_restore = torch.equal(field.V0, original_v0) and torch.equal(
        field.T, original_t
    )
    replacement = (
        "t2",
        "px",
        0.125,
        torch.randn(dimensions[0], generator=generator, dtype=torch.float64),
        torch.randn(dimensions[2], generator=generator, dtype=torch.float64),
        torch.randn(dimensions[1], dimensions[2], generator=generator, dtype=torch.float64),
    )
    field.replace(*replacement)
    replace_equal = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1e-10
    )
    field.replace(*source[2])
    replace_restore = torch.equal(field.V0, original_v0) and torch.equal(
        field.T, original_t
    )

    parent_rows = [item for item in source if item[1] == "p0"]
    field.remove_parent("p0")
    parent_equal = torch.allclose(
        field.read(query, controller), field.explicit_read(query, controller), atol=1e-10
    )
    for item in parent_rows:
        field.add(*item)
    parent_restore = torch.equal(field.V0, original_v0) and torch.equal(
        field.T, original_t
    )

    reverse = WeightedFactorizedTransitionField(*dimensions)
    for item in reversed(source):
        reverse.add(*item)
    order_equal = torch.equal(reverse.V0, original_v0) and torch.equal(
        reverse.T, original_t
    )
    shapes = field.runtime_shapes
    field.add(
        "extra",
        "p3",
        0.5,
        torch.randn(dimensions[0], generator=generator, dtype=torch.float64),
        torch.zeros(dimensions[2], dtype=torch.float64),
        torch.zeros(dimensions[1], dimensions[2], dtype=torch.float64),
    )
    zero_program = torch.allclose(
        field.records["extra"].delta_v0,
        torch.zeros_like(field.records["extra"].delta_v0),
    ) and torch.allclose(
        field.records["extra"].delta_t,
        torch.zeros_like(field.records["extra"].delta_t),
    )
    checks = {
        "explicit_sum_equals_compiled_contraction": bool(explicit_equal),
        "transition_add_remove_exact_restoration": bool(remove_restore),
        "transition_replace_matches_explicit": bool(replace_equal),
        "transition_replace_exact_restoration": bool(replace_restore),
        "parent_removal_matches_explicit": bool(parent_equal),
        "parent_exact_restoration": bool(parent_restore),
        "arbitrary_insertion_order": bool(order_equal),
        "runtime_shape_independent_of_transition_count": shapes == field.runtime_shapes,
        "zero_program_yields_zero_field_delta": bool(zero_program),
    }
    return {
        "format": WEIGHTED_FIELD_VERSION,
        "seed": int(seed),
        "checks": checks,
        "passed": all(checks.values()),
        "runtime_shapes": shapes,
    }


def program_parameter_counts(
    models: Mapping[str, nn.Module], decoder: nn.Module
) -> dict[str, Any]:
    def count(module: nn.Module) -> int:
        return sum(parameter.numel() for parameter in module.parameters())

    return {
        "programs": {name: count(model) for name, model in models.items()},
        "decoder": count(decoder),
        "decoder_trainable": sum(
            parameter.numel() for parameter in decoder.parameters() if parameter.requires_grad
        ),
    }


def projected_program_parameter_counts(
    *,
    representation_dim: int,
    hidden_dim: int,
    program_dim: int,
    model_dim: int,
    controller_ranks: Sequence[int],
    train_parent_transition_count: int,
) -> dict[str, Any]:
    """Compute architecture sizes without allocating the large representation towers."""

    def tower(input_dim: int, output_dim: int) -> int:
        return (
            int(input_dim) * int(hidden_dim)
            + 3 * int(hidden_dim)
            + int(hidden_dim) * int(output_dim)
            + int(output_dim)
        )

    state_only = tower(representation_dim, program_dim)
    static_only = tower(representation_dim, program_dim)
    output: dict[str, int] = {
        "state_only": state_only,
        "static_only": static_only,
        "pair_mlp": tower(3 * representation_dim, program_dim),
        "free_id": int(train_parent_transition_count) * int(program_dim),
    }
    for index, rank in enumerate(controller_ranks):
        controller = tower(representation_dim, int(rank))
        basis = tower(representation_dim, int(rank) * int(program_dim))
        full = controller + static_only + basis
        output[f"full_factorized_r{int(rank)}"] = full
        output[f"conditional_only_r{int(rank)}"] = controller + basis
        if index == 0:
            output["conditional_only"] = controller + basis
    return {
        "format": "program_parameter_projection_7d_v1",
        "representation_dim": int(representation_dim),
        "hidden_dim": int(hidden_dim),
        "program_dim": int(program_dim),
        "model_dim": int(model_dim),
        "architectures": output,
        "decoder": int(program_dim) * (4 * int(model_dim)),
    }


def estimate_qwen_runtime(
    *,
    new_teacher_rows: int,
    unique_teacher_states: int,
    decoder_minimum_updates: int,
    decoder_maximum_updates: int,
    program_minimum_updates: int,
    program_expected_updates: int,
    program_maximum_updates: int,
    seconds_per_teacher_forward: Mapping[str, float],
    seconds_per_backward_update: Mapping[str, float],
    evaluation_forward_count: Mapping[str, int],
) -> dict[str, Any]:
    scenarios = {}
    values = {
        "best": (decoder_minimum_updates, program_minimum_updates),
        "expected": (decoder_minimum_updates, program_expected_updates),
        "conservative": (decoder_maximum_updates, program_maximum_updates),
    }
    for name, (decoder_updates, program_updates) in values.items():
        teacher_seconds = (
            int(new_teacher_rows) + int(unique_teacher_states)
        ) * float(seconds_per_teacher_forward[name])
        backward_seconds = (
            int(decoder_updates) + int(program_updates)
        ) * float(seconds_per_backward_update[name])
        evaluation_seconds = int(evaluation_forward_count[name]) * float(
            seconds_per_teacher_forward[name]
        )
        total = teacher_seconds + backward_seconds + evaluation_seconds
        scenarios[name] = {
            "teacher_cache_seconds": teacher_seconds,
            "optimization_seconds": backward_seconds,
            "evaluation_seconds": evaluation_seconds,
            "total_seconds": total,
            "h100_hours": total / 3600.0,
            "decoder_updates": int(decoder_updates),
            "program_updates": int(program_updates),
        }
    return {"scenarios": scenarios}
