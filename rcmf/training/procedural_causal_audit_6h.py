from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import ast
from difflib import SequenceMatcher
import hashlib
import json
import math
import random
import re
from statistics import mean, median
from typing import Any

from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata
from rcmf.training.procedural_supervision_6f import (
    canonical_procedure_signature,
    observation_signature,
    stable_hash,
)


SIGNATURE_EQUIVALENCE_VERSION = "procedural_signature_equivalence_6h_v1"
SIGNATURE_CARD_RENDERER_VERSION = "procedural_signature_card_6h_v1"
AUDIT_STRATA_VERSION = "procedural_causal_audit_strata_6h_v1"
CONDITION_MANIFEST_VERSION = "procedural_causal_condition_manifest_6h_v1"
OBSERVATION_NORMALIZATION_VERSION = "appworld_observation_normalization_6h_v1"
GENERATION_RESULT_VERSION = "procedural_causal_generation_result_6h_v1"
REPLAY_FAILURE_DIAGNOSTIC_VERSION = "appworld_replay_failure_diagnostic_6h_v1"

FENCED_CODE_RE = re.compile(
    r"```(?:python)?\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL
)


def _sha_order(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * float(fraction)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def numeric_summary(values: Sequence[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    if not clean:
        return {
            "count": 0,
            "min": None,
            "mean": None,
            "median": None,
            "q90": None,
            "q95": None,
            "max": None,
        }
    return {
        "count": len(clean),
        "min": min(clean),
        "mean": mean(clean),
        "median": median(clean),
        "q90": _percentile(clean, 0.90),
        "q95": _percentile(clean, 0.95),
        "max": max(clean),
    }


def build_signature_equivalence_manifest(
    transition_rows: Sequence[Mapping[str, Any]],
    signature_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze action-signature classes without consulting query-side labels."""
    transition_by_id = {
        str(row["transition_id"]): row for row in transition_rows
    }
    signature_by_id = {
        str(row["transition_id"]): row for row in signature_rows
    }
    if len(transition_by_id) != len(transition_rows):
        raise ValueError("Transition manifest contains duplicate IDs")
    if len(signature_by_id) != len(signature_rows):
        raise ValueError("Transition signature manifest contains duplicate IDs")
    if set(transition_by_id) != set(signature_by_id):
        raise ValueError("Transition and signature manifests contain different IDs")

    grouped: dict[str, list[str]] = defaultdict(list)
    for transition_id, row in signature_by_id.items():
        grouped[str(row["action_signature"]["signature_sha256"])].append(
            transition_id
        )

    classes: list[dict[str, Any]] = []
    for signature_hash, member_ids in sorted(grouped.items()):
        action_signatures = [
            signature_by_id[transition_id]["action_signature"]
            for transition_id in member_ids
        ]
        if len({stable_hash(value) for value in action_signatures}) != 1:
            raise ValueError(
                f"Signature class {signature_hash} contains unequal schemas"
            )
        token_counts = {
            transition_id: int(
                transition_by_id[transition_id]["teacher_section_tokens"]
            )
            for transition_id in member_ids
        }
        class_median = float(median(token_counts.values()))
        ordered = sorted(
            member_ids,
            key=lambda transition_id: (
                abs(token_counts[transition_id] - class_median),
                _sha_order(transition_id),
            ),
        )
        canonical_id = ordered[0]
        canonical_parent = str(
            transition_by_id[canonical_id]["parent_memory_id"]
        )
        alternate_candidates = [
            transition_id
            for transition_id in member_ids
            if str(transition_by_id[transition_id]["parent_memory_id"])
            != canonical_parent
        ]
        alternate_id = (
            min(
                alternate_candidates,
                key=lambda transition_id: (
                    abs(
                        token_counts[transition_id]
                        - token_counts[canonical_id]
                    ),
                    _sha_order(transition_id),
                ),
            )
            if alternate_candidates
            else None
        )
        sample = action_signatures[0]
        classes.append(
            {
                "format": SIGNATURE_EQUIVALENCE_VERSION,
                "signature_class_id": f"procedure:{signature_hash}",
                "signature_sha256": signature_hash,
                "member_transition_ids": sorted(member_ids),
                "member_parent_ids": sorted(
                    {
                        str(transition_by_id[value]["parent_memory_id"])
                        for value in member_ids
                    }
                ),
                "member_source_task_ids": sorted(
                    {
                        str(transition_by_id[value]["parent_task_id"])
                        for value in member_ids
                    }
                ),
                "class_size": len(member_ids),
                "inverse_frequency_weight": 1.0 / len(member_ids),
                "coarse_action_type": str(sample["coarse_action_type"]),
                "primary_app": str(sample["primary_app"]),
                "primary_api": str(sample["primary_api"]),
                "ordered_api_sequence": list(sample["ordered_api_sequence"]),
                "keyword_argument_names": list(
                    sample["keyword_argument_names"]
                ),
                "argument_value_source_roles": list(
                    sample["argument_value_source_roles"]
                ),
                "control_flow_constructs": list(
                    sample["control_flow_constructs"]
                ),
                "pagination_loop_pattern": bool(
                    sample["pagination_loop_pattern"]
                ),
                "assignment_dataflow_pattern": list(
                    sample["assignment_dataflow_pattern"]
                ),
                "api_documentation_action": bool(
                    sample["api_documentation_action"]
                ),
                "serialized_token_counts": sorted(token_counts.values()),
                "serialized_token_count_summary": numeric_summary(
                    list(token_counts.values())
                ),
                "canonical_transition_id": canonical_id,
                "canonical_parent_id": canonical_parent,
                "canonical_token_count": token_counts[canonical_id],
                "alternate_transition_id": alternate_id,
                "alternate_parent_id": (
                    str(transition_by_id[alternate_id]["parent_memory_id"])
                    if alternate_id is not None
                    else None
                ),
                "alternate_token_count": (
                    token_counts[alternate_id]
                    if alternate_id is not None
                    else None
                ),
            }
        )

    sizes = [int(row["class_size"]) for row in classes]
    payload = {
        "format": SIGNATURE_EQUIVALENCE_VERSION,
        "transition_count": len(transition_rows),
        "signature_class_count": len(classes),
        "duplicate_transition_count": len(transition_rows) - len(classes),
        "duplicate_class_count": sum(value > 1 for value in sizes),
        "api_documentation_transition_count": sum(
            bool(row["action_signature"]["api_documentation_action"])
            for row in signature_rows
        ),
        "class_size_distribution": dict(Counter(sizes)),
        "class_size_summary": numeric_summary(sizes),
        "classes": classes,
    }
    payload["manifest_sha256"] = stable_hash(payload)
    return payload


def signature_only_card(signature_row: Mapping[str, Any]) -> str:
    action = signature_row["action_signature"]
    stage = signature_row["pre_action_stage_signature"]
    observation = signature_row["post_action_observation_signature"]
    payload = {
        "format": SIGNATURE_CARD_RENDERER_VERSION,
        "procedure": {
            "ordered_app_api_sequence": list(action["ordered_api_sequence"]),
            "calls": [
                {
                    "app": str(call["app"]),
                    "api": str(call["api"]),
                    "keyword_names": list(call["keyword_names"]),
                    "keyword_roles": dict(call["keyword_roles"]),
                    "positional_roles": list(call["positional_roles"]),
                    "assigned_to": call["assigned_to"],
                }
                for call in action["calls"]
            ],
            "argument_names": list(action["keyword_argument_names"]),
            "argument_source_roles": list(
                action["argument_value_source_roles"]
            ),
            "control_flow": list(action["control_flow_constructs"]),
            "pagination_loop": bool(action["pagination_loop_pattern"]),
            "dataflow": list(action["assignment_dataflow_pattern"]),
        },
        "pre_action_stage": {
            key: stage[key]
            for key in (
                "docs_known",
                "credentials_obtained",
                "authenticated",
                "authentication_token_present",
                "object_ids_available",
                "available_id_keys",
                "collection_loaded",
                "pagination_state",
                "completion_ready",
                "latest_observation_category",
                "latest_observation_schema_keys",
            )
        },
        "observation_schema": {
            key: observation[key]
            for key in (
                "category",
                "schema_keys",
                "id_keys",
                "has_access_token_key",
                "is_error",
                "is_empty",
            )
        },
    }
    rendered = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return (
        "[SIGNATURE-ONLY PROCEDURAL MEMORY]\n"
        f"{rendered}\n"
        "[END SIGNATURE-ONLY PROCEDURAL MEMORY]"
    )


def messages_with_signature_card(
    base_messages: Sequence[Mapping[str, str]],
    card: str,
    prompt_profile: str,
) -> list[dict[str, str]]:
    messages = [dict(message) for message in base_messages]
    initial_count = int(
        appworld_renderer_metadata(prompt_profile)["initial_message_count"]
    )
    for index in range(initial_count, len(messages)):
        if messages[index].get("role") == "user":
            messages[index]["content"] = (
                f"{card}\n\n"
                "[CURRENT APPWORLD STATE START]\n"
                f"{messages[index]['content']}\n"
                "[CURRENT APPWORLD STATE END]"
            )
            return messages
    raise ValueError("Could not locate current task user message for card insertion")


def classify_audit_states(
    audit_rows: Sequence[Mapping[str, Any]],
    scoreable_label_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    audit_by_id = {
        str(row["state_example_id"]): row for row in audit_rows
    }
    if len(audit_by_id) != len(audit_rows):
        raise ValueError("Audit-state manifest contains duplicate IDs")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in scoreable_label_rows:
        state_id = str(row["state_example_id"])
        if state_id in audit_by_id:
            grouped[state_id].append(row)

    output: list[dict[str, Any]] = []
    for state_id, audit in sorted(audit_by_id.items()):
        candidates = grouped.get(state_id, [])
        high = [row for row in candidates if int(row["procedural_tier"]) >= 3]
        non_doc_high = [
            row
            for row in high
            if not bool(row["transition_api_documentation_action"])
        ]
        non_doc_signatures = {
            str(row["transition_signature_sha256"]) for row in non_doc_high
        }
        non_doc_parents = {
            str(row["transition_parent_id"]) for row in non_doc_high
        }
        exact = [row for row in candidates if bool(row["exact_api_sequence"])]
        max_tier = max(
            (int(row["procedural_tier"]) for row in candidates), default=-1
        )
        if len(non_doc_signatures) >= 2 and len(non_doc_parents) >= 2:
            stratum = "A"
            reason = "diverse_non_documentation_high_tier"
        elif non_doc_high:
            stratum = "B"
            reason = "non_documentation_high_tier_not_diverse"
        elif high:
            stratum = "C"
            reason = "api_documentation_only_high_tier"
        elif exact and max_tier <= 2:
            stratum = "D"
            reason = "exact_api_maximum_tier_2"
        else:
            stratum = "E"
            reason = "no_relevant_candidate"
        output.append(
            {
                "format": AUDIT_STRATA_VERSION,
                "state_example_id": state_id,
                "task_id": str(audit["task_id"]),
                "step_id": int(audit["step_id"]),
                "stratum": stratum,
                "stratum_reason": reason,
                "candidate_count": len(candidates),
                "maximum_tier": max_tier,
                "tier3_or_4_count": len(high),
                "non_documentation_tier3_or_4_count": len(non_doc_high),
                "unique_non_documentation_high_tier_signatures": len(
                    non_doc_signatures
                ),
                "non_documentation_high_tier_parent_count": len(
                    non_doc_parents
                ),
                "exact_api_candidate_count": len(exact),
                "query_coarse_action_type": (
                    str(candidates[0]["query_coarse_action_type"])
                    if candidates
                    else str(audit.get("coarse_action_type", ""))
                ),
            }
        )

    primary = [row for row in output if row["stratum"] in {"A", "B"}]
    payload = {
        "format": AUDIT_STRATA_VERSION,
        "state_count": len(output),
        "task_count": len({row["task_id"] for row in output}),
        "stratum_state_counts": dict(
            Counter(str(row["stratum"]) for row in output)
        ),
        "stratum_task_counts": {
            stratum: len(
                {
                    row["task_id"]
                    for row in output
                    if row["stratum"] == stratum
                }
            )
            for stratum in "ABCDE"
        },
        "primary_non_documentation_high_tier_state_count": len(primary),
        "primary_non_documentation_high_tier_task_count": len(
            {row["task_id"] for row in primary}
        ),
        "rows": output,
    }
    payload["manifest_sha256"] = stable_hash(payload)
    return payload


def validate_audit_label_coverage(
    audit_rows: Sequence[Mapping[str, Any]],
    label_rows: Sequence[Mapping[str, Any]],
    *,
    expected_scoreable_count: int,
) -> dict[str, Any]:
    """Reject the EXP-020-only subset bug before audit-state stratification."""
    expected_ids = {
        str(row["state_example_id"]) for row in audit_rows
    }
    if len(expected_ids) != len(audit_rows):
        raise ValueError("Audit-state manifest contains duplicate IDs")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in label_rows:
        state_id = str(row["state_example_id"])
        if state_id not in expected_ids:
            raise ValueError(f"Unexpected state in audit labels: {state_id}")
        grouped[state_id].append(row)
    missing = sorted(expected_ids - set(grouped))
    scoreable = sum(
        bool(row["scoreable_under_context"]) for row in label_rows
    )
    if missing:
        raise ValueError(
            "Audit procedural labels omit states: " + ", ".join(missing)
        )
    if scoreable != int(expected_scoreable_count):
        raise ValueError(
            f"Audit scoreable labels differ: {scoreable} != "
            f"{expected_scoreable_count}"
        )
    return {
        "audit_state_count": len(expected_ids),
        "legal_label_count": len(label_rows),
        "scoreable_label_count": scoreable,
        "over_context_label_count": len(label_rows) - scoreable,
        "minimum_legal_candidates_per_state": min(
            len(values) for values in grouped.values()
        ),
        "maximum_legal_candidates_per_state": max(
            len(values) for values in grouped.values()
        ),
        "all_audit_states_covered": True,
    }


def _representative_candidates(
    rows: Sequence[Mapping[str, Any]],
    class_by_signature: Mapping[str, Mapping[str, Any]],
    *,
    representative: str,
) -> list[Mapping[str, Any]]:
    by_transition = {str(row["transition_id"]): row for row in rows}
    candidates: list[Mapping[str, Any]] = []
    field = (
        "canonical_transition_id"
        if representative == "canonical"
        else "alternate_transition_id"
    )
    for class_row in class_by_signature.values():
        transition_id = class_row.get(field)
        if transition_id is None:
            continue
        candidate = by_transition.get(str(transition_id))
        if candidate is not None and bool(candidate["scoreable_under_context"]):
            candidates.append(candidate)
    return candidates


def _best_oracle(
    rows: Sequence[Mapping[str, Any]],
    class_by_signature: Mapping[str, Mapping[str, Any]],
    *,
    transition_split: str | None = None,
) -> Mapping[str, Any] | None:
    candidates = _representative_candidates(
        rows, class_by_signature, representative="canonical"
    )
    if transition_split is not None:
        candidates = [
            row
            for row in candidates
            if str(row["transition_split"]) == transition_split
        ]
    if not candidates:
        return None
    query_non_doc = (
        str(candidates[0]["query_coarse_action_type"])
        != "api_documentation"
    )
    return min(
        candidates,
        key=lambda row: (
            -int(row["procedural_tier"]),
            -int(bool(row["exact_api_sequence"])),
            -int(bool(row["canonical_action_schema_match"])),
            -int(bool(row["state_stage_compatible"])),
            -int(
                query_non_doc
                and not bool(row["transition_api_documentation_action"])
            ),
            str(row["transition_signature_sha256"]),
        ),
    )


def _hard_negative(
    rows: Sequence[Mapping[str, Any]],
    class_by_signature: Mapping[str, Mapping[str, Any]],
    oracle: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    candidates = _representative_candidates(
        rows, class_by_signature, representative="canonical"
    )
    oracle_signature = (
        str(oracle["transition_signature_sha256"])
        if oracle is not None
        else None
    )
    eligible = [
        row
        for row in candidates
        if str(row["transition_signature_sha256"]) != oracle_signature
        and (
            bool(row["exact_api_sequence"])
            or bool(row["same_primary_app"])
            or bool(row["same_coarse_action_type"])
        )
        and (
            not bool(row["state_stage_compatible"])
            or not bool(row["argument_control_compatible"])
            or not bool(row["canonical_action_schema_match"])
        )
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -int(bool(row["exact_api_sequence"])),
            -int(bool(row["same_primary_app"])),
            -int(bool(row["same_coarse_action_type"])),
            -int(row["state_stage_conflict_count"]),
            -int(row["procedural_tier"]),
            str(row["transition_signature_sha256"]),
        ),
    )


def _popularity_control(
    rows: Sequence[Mapping[str, Any]],
    class_by_signature: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    candidates = _representative_candidates(
        rows, class_by_signature, representative="canonical"
    )
    same_coarse = [
        row for row in candidates if bool(row["same_coarse_action_type"])
    ]
    if not same_coarse:
        return None
    return min(
        same_coarse,
        key=lambda row: (
            -int(
                class_by_signature[str(row["transition_signature_sha256"])][
                    "class_size"
                ]
            ),
            str(row["transition_signature_sha256"]),
        ),
    )


def _unrelated_control(
    rows: Sequence[Mapping[str, Any]],
    class_by_signature: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    candidates = _representative_candidates(
        rows, class_by_signature, representative="canonical"
    )
    tier_zero = [
        row
        for row in candidates
        if int(row["procedural_tier"]) == 0
        and str(row["query_primary_app"])
        != str(row["transition_primary_app"])
    ]
    if not tier_zero:
        tier_zero = [
            row for row in candidates if int(row["procedural_tier"]) == 0
        ]
    if not tier_zero:
        return None
    return min(
        tier_zero,
        key=lambda row: (
            _sha_order(
                f"{row['state_example_id']}:{row['transition_signature_sha256']}"
            ),
            str(row["transition_signature_sha256"]),
        ),
    )


def build_condition_manifest(
    audit_strata: Mapping[str, Any],
    label_rows: Sequence[Mapping[str, Any]],
    equivalence_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    state_meta = {
        str(row["state_example_id"]): row for row in audit_strata["rows"]
    }
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in label_rows:
        state_id = str(row["state_example_id"])
        if state_id in state_meta:
            grouped[state_id].append(row)
    class_by_signature = {
        str(row["signature_sha256"]): row
        for row in equivalence_manifest["classes"]
    }
    conditions: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    def add_condition(
        state_id: str,
        name: str,
        row: Mapping[str, Any] | None,
        *,
        prompt_kind: str,
        required: bool,
        note: str,
    ) -> None:
        transition_id = str(row["transition_id"]) if row is not None else None
        signature_hash = (
            str(row["transition_signature_sha256"])
            if row is not None
            else None
        )
        if row is None and name != "C0_bare":
            missing.append(
                {
                    "state_example_id": state_id,
                    "condition_name": name,
                    "required": required,
                    "reason": note,
                }
            )
            return
        identity = {
            "version": CONDITION_MANIFEST_VERSION,
            "state_example_id": state_id,
            "condition_name": name,
            "transition_id": transition_id,
            "signature_sha256": signature_hash,
            "prompt_kind": prompt_kind,
        }
        class_row = (
            class_by_signature[signature_hash]
            if signature_hash is not None
            else None
        )
        conditions.append(
            {
                "format": CONDITION_MANIFEST_VERSION,
                "condition_key": stable_hash(identity),
                "state_example_id": state_id,
                "state_task_id": str(state_meta[state_id]["task_id"]),
                "state_step_id": int(state_meta[state_id]["step_id"]),
                "audit_stratum": str(state_meta[state_id]["stratum"]),
                "condition_name": name,
                "prompt_kind": prompt_kind,
                "transition_id": transition_id,
                "transition_parent_id": (
                    str(row["transition_parent_id"])
                    if row is not None
                    else None
                ),
                "transition_parent_task_id": (
                    str(row["transition_parent_task_id"])
                    if row is not None
                    else None
                ),
                "transition_split": (
                    str(row["transition_split"])
                    if row is not None
                    else None
                ),
                "signature_class_id": (
                    str(class_row["signature_class_id"])
                    if class_row is not None
                    else None
                ),
                "signature_sha256": signature_hash,
                "signature_class_size": (
                    int(class_row["class_size"])
                    if class_row is not None
                    else None
                ),
                "procedural_tier": (
                    int(row["procedural_tier"])
                    if row is not None
                    else None
                ),
                "exact_api_sequence": (
                    bool(row["exact_api_sequence"])
                    if row is not None
                    else None
                ),
                "state_stage_compatible": (
                    bool(row["state_stage_compatible"])
                    if row is not None
                    else None
                ),
                "api_documentation_action": (
                    bool(row["transition_api_documentation_action"])
                    if row is not None
                    else None
                ),
                "selection_note": note,
            }
        )

    for state_id in sorted(state_meta):
        rows = grouped.get(state_id, [])
        add_condition(
            state_id,
            "C0_bare",
            None,
            prompt_kind="bare",
            required=True,
            note="unchanged canonical full-demo prompt",
        )
        oracle = _best_oracle(rows, class_by_signature)
        add_condition(
            state_id,
            "C1_raw_oracle",
            oracle,
            prompt_kind="raw_transition",
            required=True,
            note="full-bank canonical exemplar selected without utility/outcome",
        )
        add_condition(
            state_id,
            "C2_signature_only",
            oracle,
            prompt_kind="signature_card",
            required=True,
            note="normalized metadata for the C1 signature",
        )
        hard = _hard_negative(rows, class_by_signature, oracle)
        add_condition(
            state_id,
            "C3_hard_negative",
            hard,
            prompt_kind="raw_transition",
            required=True,
            note="same intent with incompatible stage/schema/control",
        )
        popular = _popularity_control(rows, class_by_signature)
        add_condition(
            state_id,
            "C4_signature_popularity",
            popular,
            prompt_kind="raw_transition",
            required=True,
            note="largest same-coarse signature class",
        )
        unrelated = _unrelated_control(rows, class_by_signature)
        add_condition(
            state_id,
            "C5_unrelated",
            unrelated,
            prompt_kind="raw_transition",
            required=True,
            note="deterministic tier-0 different-app control",
        )

        alternate = None
        if oracle is not None:
            class_row = class_by_signature[
                str(oracle["transition_signature_sha256"])
            ]
            alternate_id = class_row.get("alternate_transition_id")
            if alternate_id is not None:
                alternate = next(
                    (
                        row
                        for row in rows
                        if str(row["transition_id"]) == str(alternate_id)
                        and bool(row["scoreable_under_context"])
                    ),
                    None,
                )
        if alternate is not None:
            add_condition(
                state_id,
                "C6_alternate_same_signature",
                alternate,
                prompt_kind="raw_transition",
                required=False,
                note="fixed different-parent alternate for the C1 signature",
            )

        strict = _best_oracle(
            rows, class_by_signature, transition_split="train"
        )
        if (
            strict is not None
            and oracle is not None
            and str(strict["transition_id"]) != str(oracle["transition_id"])
        ):
            add_condition(
                state_id,
                "C7_strict_B_oracle",
                strict,
                prompt_kind="raw_transition",
                required=False,
                note="best canonical exemplar restricted to 29 train parents",
            )

        if str(state_meta[state_id]["stratum"]) == "D":
            tier_two = [
                row
                for row in _representative_candidates(
                    rows, class_by_signature, representative="canonical"
                )
                if bool(row["exact_api_sequence"])
                and int(row["procedural_tier"]) == 2
            ]
            exact_tier_two = (
                min(
                    tier_two,
                    key=lambda row: (
                        -float(row["P2_canonical_schema_compatibility"]),
                        -float(row["P3_state_stage_compatibility"]),
                        str(row["transition_signature_sha256"]),
                    ),
                )
                if tier_two
                else None
            )
            add_condition(
                state_id,
                "C8_exact_API_tier2",
                exact_tier_two,
                prompt_kind="raw_transition",
                required=False,
                note="exact-API Tier-2 diagnostic for uncovered stratum",
            )

    if len({row["condition_key"] for row in conditions}) != len(conditions):
        raise ValueError("Condition manifest contains duplicate condition keys")
    payload = {
        "format": CONDITION_MANIFEST_VERSION,
        "state_count": len(state_meta),
        "condition_count": len(conditions),
        "condition_counts": dict(
            Counter(str(row["condition_name"]) for row in conditions)
        ),
        "prompt_kind_counts": dict(
            Counter(str(row["prompt_kind"]) for row in conditions)
        ),
        "missing_condition_count": len(missing),
        "missing_conditions": missing,
        "conditions": conditions,
    }
    payload["manifest_sha256"] = stable_hash(payload)
    return payload


def normalize_observation(text: str) -> str:
    value = str(text).replace("\r\n", "\n").strip()
    if value.startswith("Output:\n```") and value.endswith("```"):
        value = value[len("Output:\n```") : -3].strip()
    lines = [line.rstrip() for line in value.splitlines()]
    value = "\n".join(lines).strip()
    parsed: Any = None
    parsed_ok = False
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(value)
            parsed_ok = True
            break
        except Exception:
            continue
    if parsed_ok:
        try:
            return json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            )
        except Exception:
            pass
    return value


def normalized_observation_hash(text: str) -> str:
    return hashlib.sha256(normalize_observation(text).encode("utf-8")).hexdigest()


def summarize_replay_failure(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Replay diagnostics require at least one state row")
    state_ids = [str(row["state_example_id"]) for row in rows]
    if len(set(state_ids)) != len(state_ids):
        raise ValueError("Replay diagnostics contain duplicate state IDs")

    history_checks = [
        check for row in rows for check in row.get("history_checks", [])
    ]
    task_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    first_mismatches: Counter[str] = Counter()
    for row in rows:
        task_rows[str(row["task_id"])].append(row)
        first_mismatch = next(
            (
                int(check["step_id"])
                for check in row.get("history_checks", [])
                if not bool(check["observation_match"])
            ),
            None,
        )
        first_mismatches[
            "no_history_mismatch" if first_mismatch is None else str(first_mismatch)
        ] += 1

    by_task = {
        task_id: {
            "state_count": len(values),
            "history_match_state_count": sum(
                bool(value["history_match"]) for value in values
            ),
            "target_match_state_count": sum(
                bool(value["target_observation_match"]) for value in values
            ),
            "passed_state_count": sum(bool(value["passed"]) for value in values),
        }
        for task_id, values in sorted(task_rows.items())
    }
    zero_history = [row for row in rows if int(row["history_step_count"]) == 0]
    return {
        "format": REPLAY_FAILURE_DIAGNOSTIC_VERSION,
        "state_count": len(rows),
        "task_count": len(task_rows),
        "passed_state_count": sum(bool(row["passed"]) for row in rows),
        "failed_state_count": sum(not bool(row["passed"]) for row in rows),
        "history_match_state_count": sum(
            bool(row["history_match"]) for row in rows
        ),
        "target_match_state_count": sum(
            bool(row["target_observation_match"]) for row in rows
        ),
        "history_step_count": len(history_checks),
        "history_step_match_count": sum(
            bool(check["observation_match"]) for check in history_checks
        ),
        "history_step_match_fraction": (
            sum(bool(check["observation_match"]) for check in history_checks)
            / len(history_checks)
            if history_checks
            else None
        ),
        "zero_history_state_count": len(zero_history),
        "zero_history_target_match_count": sum(
            bool(row["target_observation_match"]) for row in zero_history
        ),
        "first_history_mismatch_step_counts": dict(sorted(first_mismatches.items())),
        "by_task": by_task,
    }


def observation_similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None, normalize_observation(left), normalize_observation(right)
    ).ratio()


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if a or b else 1.0


def _exception_category(observation: str) -> str | None:
    lowered = str(observation).lower()
    names = (
        "syntaxerror",
        "nameerror",
        "typeerror",
        "valueerror",
        "keyerror",
        "indexerror",
        "attributeerror",
        "runtimeerror",
        "permissionerror",
        "exception",
    )
    for name in names:
        if name in lowered:
            return name
    if "syntax error" in lowered:
        return "syntax_error"
    if "execution failed" in lowered or "error:" in lowered:
        return "execution_error"
    return None


def evaluate_generated_action(
    response: str,
    code: str,
    target_action: str,
    observation: str,
    target_observation: str,
) -> dict[str, Any]:
    target = canonical_procedure_signature(target_action)
    generated = canonical_procedure_signature(code)
    try:
        ast.parse(code)
        valid_python = bool(code.strip())
    except SyntaxError:
        valid_python = False
    blocks = FENCED_CODE_RE.findall(str(response))
    stripped_response = str(response).strip()
    one_block = len(blocks) == 1
    one_block_compliance = bool(
        one_block
        and re.fullmatch(
            r"```(?:python)?\s*.*?```",
            stripped_response,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    exception = _exception_category(observation)
    generated_observation = observation_signature(observation)
    expected_observation = observation_signature(target_observation)
    exact_api = (
        bool(generated["ordered_api_sequence"])
        and generated["primary_app"] == target["primary_app"]
        and generated["primary_api"] == target["primary_api"]
    )
    return {
        "nonempty_code": bool(code.strip()),
        "valid_python": valid_python,
        "api_call_extracted": bool(generated["ordered_api_sequence"]),
        "one_code_block_compliance": one_block_compliance,
        "exact_primary_app_api_match": exact_api,
        "canonical_procedural_signature_match": generated["signature_sha256"]
        == target["signature_sha256"],
        "coarse_action_type_match": generated["coarse_action_type"]
        == target["coarse_action_type"],
        "keyword_argument_name_match": generated["keyword_argument_names"]
        == target["keyword_argument_names"],
        "keyword_argument_name_similarity": _jaccard(
            generated["keyword_argument_names"],
            target["keyword_argument_names"],
        ),
        "argument_source_role_match": generated[
            "argument_value_source_roles"
        ]
        == target["argument_value_source_roles"],
        "argument_source_role_similarity": _jaccard(
            generated["argument_value_source_roles"],
            target["argument_value_source_roles"],
        ),
        "control_flow_pagination_match": (
            generated["control_flow_constructs"]
            == target["control_flow_constructs"]
            and generated["pagination_loop_pattern"]
            == target["pagination_loop_pattern"]
        ),
        "completion_action_correctness": generated["completion_action"]
        == target["completion_action"],
        "execution_success": exception is None
        and not generated_observation["is_error"],
        "exception_category": exception,
        "observation_schema_match": (
            generated_observation["category"]
            == expected_observation["category"]
            and generated_observation["schema_keys"]
            == expected_observation["schema_keys"]
        ),
        "normalized_observation_similarity": observation_similarity(
            observation, target_observation
        ),
        "exact_successor_match": normalize_observation(observation)
        == normalize_observation(target_observation),
        "generated_signature_sha256": generated["signature_sha256"],
        "target_signature_sha256": target["signature_sha256"],
    }


def paired_task_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    left_condition: str,
    right_condition: str,
    metric: str,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    task_by_state: dict[str, str] = {}
    for row in rows:
        state_id = str(row["state_example_id"])
        by_state[state_id][str(row["condition_name"])] = row
        task_by_state[state_id] = str(row["state_task_id"])
    differences_by_task: dict[str, list[float]] = defaultdict(list)
    for state_id, values in by_state.items():
        if left_condition not in values or right_condition not in values:
            continue
        left = float(values[left_condition]["metrics"][metric])
        right = float(values[right_condition]["metrics"][metric])
        differences_by_task[task_by_state[state_id]].append(left - right)
    task_ids = sorted(differences_by_task)
    observed_values = [
        value for task in task_ids for value in differences_by_task[task]
    ]
    if not task_ids:
        return {
            "left_condition": left_condition,
            "right_condition": right_condition,
            "metric": metric,
            "paired_state_count": 0,
            "task_count": 0,
            "difference": None,
            "ci95_low": None,
            "ci95_high": None,
            "bootstrap_samples": samples,
            "seed": seed,
        }
    observed = mean(observed_values)
    rng = random.Random(seed)
    boot: list[float] = []
    for _ in range(samples):
        sampled_tasks = [rng.choice(task_ids) for _ in task_ids]
        values = [
            value
            for task in sampled_tasks
            for value in differences_by_task[task]
        ]
        boot.append(mean(values))
    return {
        "left_condition": left_condition,
        "right_condition": right_condition,
        "metric": metric,
        "paired_state_count": len(observed_values),
        "task_count": len(task_ids),
        "difference": observed,
        "ci95_low": _percentile(boot, 0.025),
        "ci95_high": _percentile(boot, 0.975),
        "bootstrap_samples": samples,
        "seed": seed,
    }
