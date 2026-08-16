from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import load_decision_examples
from rcmf.training.procedural_coverage_6g import (
    FULL_TRANSITION_SIGNATURE_VERSION,
    candidate_space_summary,
    context_preflight_summary,
    future_runtime_projection,
    missing_state_diagnostics,
    select_decision_branch,
    signature_redundancy_summary,
    two_axis_cell,
)
from rcmf.training.procedural_supervision_6f import (
    PROCEDURAL_LABEL_VERSION,
    canonical_procedure_signature,
    observation_signature,
    procedural_compatibility,
    state_stage_signature,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
    utc_now,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.prepare_all_task_interaction_6d import _build_preflight
from scripts.prepare_procedural_supervision_6f import (
    _signature_credential_leakage_paths,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _assert_count(name: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"{name} differs: {actual} != {expected}")


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _query_rows_by_id(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {
        str(row["state_example_id"]): dict(row) for row in manifest["query_rows"]
    }
    if len(rows) != len(manifest["query_rows"]):
        raise ValueError("Expanded query manifest has duplicate state IDs")
    return rows


def _example_index_by_state(examples: Sequence[Any]) -> dict[str, int]:
    output: dict[str, int] = {}
    for index, example in enumerate(examples):
        identity = state_example_id(index, example)
        if identity in output:
            raise ValueError(f"Duplicate decision-example identity: {identity}")
        output[identity] = index
    return output


def _transition_split(
    transition: Mapping[str, Any], parent_split: Mapping[str, Any]
) -> str:
    parent_id = str(transition["parent_memory_id"])
    try:
        return str(parent_split["split_by_parent"][parent_id])
    except KeyError as exc:
        raise ValueError(f"Transition parent absent from immutable split: {parent_id}") from exc


def _checkpoint_paths(directory: Path, state_id: str) -> tuple[Path, Path, Path]:
    stem = sha256_text(state_id)[:24]
    return (
        directory / f"{stem}.legal.jsonl",
        directory / f"{stem}.illegal.jsonl",
        directory / f"{stem}.meta.json",
    )


def _validate_preflight_checkpoint(
    *,
    legal_path: Path,
    illegal_path: Path,
    meta_path: Path,
    state_id: str,
    transition_ids: set[str],
    transition_manifest_sha256: str,
    context_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not (legal_path.exists() and illegal_path.exists() and meta_path.exists()):
        raise FileNotFoundError("Incomplete per-state preflight checkpoint")
    meta = _load_json(meta_path)
    legal = list(read_jsonl(legal_path))
    illegal = list(read_jsonl(illegal_path))
    checks = {
        "state_id": meta.get("state_example_id") == state_id,
        "transition_hash": meta.get("transition_manifest_sha256")
        == transition_manifest_sha256,
        "context_limit": int(meta.get("context_limit", -1)) == int(context_limit),
        "legal_hash": meta.get("legal_rows_sha256") == sha256_file(legal_path),
        "illegal_hash": meta.get("illegal_rows_sha256") == sha256_file(illegal_path),
        "legal_count": int(meta.get("legal_count", -1)) == len(legal),
        "illegal_count": int(meta.get("illegal_count", -1)) == len(illegal),
    }
    row_state_ids = {
        str(row["state_example_id"]) for row in [*legal, *illegal]
    }
    row_transition_ids = {
        str(row["transition_id"]) for row in [*legal, *illegal]
    }
    checks["row_state"] = row_state_ids == {state_id}
    checks["transition_partition"] = row_transition_ids == transition_ids
    checks["exact_partition_count"] = len(legal) + len(illegal) == len(transition_ids)
    checks["unique_pairs"] = len(
        {str(row["pair_id"]) for row in [*legal, *illegal]}
    ) == len(transition_ids)
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise ValueError(f"Invalid per-state preflight checkpoint {state_id}: {failed}")
    return legal, illegal


def _resumable_preflight(
    *,
    tokenizer: Any,
    examples: list[Any],
    query_rows: Sequence[Mapping[str, Any]],
    transitions: list[dict[str, Any]],
    prompt_profile: str,
    context_limit: int,
    checkpoint_dir: Path,
    transition_manifest_sha256: str,
    attempt: AttemptLedger,
    scope: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    transition_ids = {str(row["transition_id"]) for row in transitions}
    if len(transition_ids) != len(transitions):
        raise ValueError("Full transition manifest has duplicate IDs")
    legal_rows: list[dict[str, Any]] = []
    illegal_rows: list[dict[str, Any]] = []
    resumed = 0
    computed = 0
    for position, raw_query in enumerate(query_rows, start=1):
        query = dict(raw_query)
        state_id = str(query["state_example_id"])
        legal_path, illegal_path, meta_path = _checkpoint_paths(
            checkpoint_dir, state_id
        )
        try:
            legal, illegal = _validate_preflight_checkpoint(
                legal_path=legal_path,
                illegal_path=illegal_path,
                meta_path=meta_path,
                state_id=state_id,
                transition_ids=transition_ids,
                transition_manifest_sha256=transition_manifest_sha256,
                context_limit=context_limit,
            )
            resumed += 1
        except FileNotFoundError:
            legal, illegal, _ = _build_preflight(
                tokenizer=tokenizer,
                examples=examples,
                query_manifest={"query_rows": [query]},
                panel_rows=transitions,
                prompt_profile=prompt_profile,
                context_limit=context_limit,
            )
            _atomic_write_jsonl(legal_path, legal)
            _atomic_write_jsonl(illegal_path, illegal)
            atomic_write_json(
                meta_path,
                {
                    "format": "per_state_context_preflight_checkpoint_6g_v1",
                    "scope": scope,
                    "state_example_id": state_id,
                    "context_limit": context_limit,
                    "transition_manifest_sha256": transition_manifest_sha256,
                    "legal_count": len(legal),
                    "illegal_count": len(illegal),
                    "legal_rows_sha256": sha256_file(legal_path),
                    "illegal_rows_sha256": sha256_file(illegal_path),
                    "completed_at_utc": utc_now(),
                },
            )
            legal, illegal = _validate_preflight_checkpoint(
                legal_path=legal_path,
                illegal_path=illegal_path,
                meta_path=meta_path,
                state_id=state_id,
                transition_ids=transition_ids,
                transition_manifest_sha256=transition_manifest_sha256,
                context_limit=context_limit,
            )
            computed += 1
        legal_rows.extend(legal)
        illegal_rows.extend(illegal)
        attempt.progress(
            status=f"{scope}_preflight",
            completed_states=position,
            total_states=len(query_rows),
            legal_pairs=len(legal_rows),
            illegal_pairs=len(illegal_rows),
            resumed_states=resumed,
            newly_computed_states=computed,
            latest_validated_checkpoint=str(meta_path),
        )
    return legal_rows, illegal_rows, {
        "scope": scope,
        "state_count": len(query_rows),
        "resumed_state_count": resumed,
        "newly_computed_state_count": computed,
        "checkpoint_dir": str(checkpoint_dir),
    }


def _full_transition_signatures(
    transitions: Sequence[Mapping[str, Any]],
    *,
    old_signature_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    old_by_id = {
        str(row["transition_id"]): row
        for row in old_signature_rows
        if row.get("kind") == "transition"
    }
    rows: list[dict[str, Any]] = []
    overlap_mismatches: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    leakage_rows: list[dict[str, Any]] = []
    for transition in transitions:
        transition_id = str(transition["transition_id"])
        action = canonical_procedure_signature(
            str(transition["complete_action"]),
            context_text=str(transition["canonical_pre_action_state"]),
        )
        stage = state_stage_signature(str(transition["canonical_pre_action_state"]))
        observation = observation_signature(
            str(transition["complete_post_action_observation"])
        )
        row = {
            "format": FULL_TRANSITION_SIGNATURE_VERSION,
            "kind": "transition",
            "transition_id": transition_id,
            "parent_id": str(transition["parent_memory_id"]),
            "parent_task_id": str(transition["parent_task_id"]),
            "step_index": int(transition["step_index"]),
            "step_count": int(transition["step_count"]),
            "action_sha256": str(transition["complete_action_sha256"]),
            "pre_state_sha256": str(
                transition["canonical_pre_action_state_sha256"]
            ),
            "observation_sha256": str(
                transition["complete_post_action_observation_sha256"]
            ),
            "transition_content_sha256": str(
                transition["transition_content_sha256"]
            ),
            "action_signature": action,
            "pre_action_stage_signature": stage,
            "post_action_observation_signature": observation,
        }
        rows.append(row)
        if action["parse_status"] != "ast":
            fallback_rows.append(
                {
                    "transition_id": transition_id,
                    "parse_status": action["parse_status"],
                    "syntax_error_category": action.get("syntax_error_category"),
                }
            )
        paths = _signature_credential_leakage_paths(row)
        if paths:
            leakage_rows.append({"transition_id": transition_id, "paths": paths})
        old = old_by_id.get(transition_id)
        if old is not None:
            for field in (
                "action_signature",
                "pre_action_stage_signature",
                "post_action_observation_signature",
                "action_sha256",
                "pre_state_sha256",
                "observation_sha256",
                "transition_content_sha256",
            ):
                if old[field] != row[field]:
                    overlap_mismatches.append(
                        {"transition_id": transition_id, "field": field}
                    )
    if len({str(row["transition_id"]) for row in rows}) != len(rows):
        raise ValueError("Full transition signature rows are duplicated")
    validation = {
        "format": "full_transition_signature_validation_6g_v1",
        "transition_count": len(rows),
        "parse_status": dict(
            Counter(str(row["action_signature"]["parse_status"]) for row in rows)
        ),
        "fallback_count": len(fallback_rows),
        "fallback_rows": fallback_rows,
        "credential_leakage_count": len(leakage_rows),
        "credential_leakage_rows": leakage_rows,
        "old_panel_overlap_count": len(old_by_id),
        "old_panel_overlap_mismatch_count": len(overlap_mismatches),
        "old_panel_overlap_mismatches": overlap_mismatches,
    }
    return rows, validation


def _build_label_rows(
    *,
    preflight_rows: Sequence[Mapping[str, Any]],
    query_signatures: Mapping[str, Mapping[str, Any]],
    transition_signatures: Mapping[str, Mapping[str, Any]],
    parent_split: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for preflight in preflight_rows:
        state_id = str(preflight["state_example_id"])
        transition_id = str(preflight["transition_id"])
        query = query_signatures[state_id]
        transition = transition_signatures[transition_id]
        transition_split = str(
            parent_split["split_by_parent"][str(preflight["parent_memory_id"])]
        )
        compatibility = procedural_compatibility(
            query["target_signature"],
            query["state_stage_signature"],
            transition["action_signature"],
            transition["pre_action_stage_signature"],
            transition["post_action_observation_signature"],
        )
        rows.append(
            {
                "format": "full_transition_procedural_label_6g_v1",
                "source_label_version": PROCEDURAL_LABEL_VERSION,
                "pair_id": str(preflight["pair_id"]),
                "cell": two_axis_cell(str(preflight["split"]), transition_split),
                "state_example_id": state_id,
                "state_task_id": str(preflight["task_id"]),
                "state_split": str(preflight["split"]),
                "transition_id": transition_id,
                "transition_parent_id": str(preflight["parent_memory_id"]),
                "transition_parent_task_id": str(preflight["parent_task_id"]),
                "transition_split": transition_split,
                "procedural_tier": int(compatibility["tier"]),
                "query_primary_app": query["target_signature"]["primary_app"],
                "query_primary_api": query["target_signature"]["primary_api"],
                "query_coarse_action_type": query["target_signature"][
                    "coarse_action_type"
                ],
                "transition_primary_app": transition["action_signature"][
                    "primary_app"
                ],
                "transition_primary_api": transition["action_signature"][
                    "primary_api"
                ],
                "transition_coarse_action_type": transition["action_signature"][
                    "coarse_action_type"
                ],
                "transition_api_documentation_action": bool(
                    transition["action_signature"].get("api_documentation_action")
                ),
                "query_signature_sha256": query["target_signature"][
                    "signature_sha256"
                ],
                "query_stage_sha256": query["state_stage_signature"][
                    "signature_sha256"
                ],
                "transition_signature_sha256": transition["action_signature"][
                    "signature_sha256"
                ],
                "transition_stage_sha256": transition[
                    "pre_action_stage_signature"
                ]["signature_sha256"],
                "transition_observation_sha256": transition[
                    "post_action_observation_signature"
                ]["signature_sha256"],
                "scoreable_under_context": not bool(preflight["over_context"]),
                "over_context": bool(preflight["over_context"]),
                "combined_prompt_tokens": int(preflight["combined_prompt_tokens"]),
                "target_tokens": int(preflight["target_tokens"]),
                "total_tokens_with_target": int(
                    preflight["total_tokens_with_target"]
                ),
                **compatibility,
            }
        )
    if len({str(row["pair_id"]) for row in rows}) != len(rows):
        raise ValueError("Full-bank procedural labels have duplicate pair IDs")
    return rows


def _space_rows(
    rows: Sequence[Mapping[str, Any]],
    cells: Sequence[str],
    *,
    scoreable_only: bool,
) -> list[Mapping[str, Any]]:
    allowed = set(cells)
    return [
        row
        for row in rows
        if str(row["cell"]) in allowed
        and (not scoreable_only or bool(row.get("scoreable_under_context", True)))
    ]


def _coverage_spaces(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_state_ids: Sequence[str],
    heldout_state_ids: Sequence[str],
    state_task_by_id: Mapping[str, str],
) -> dict[str, Any]:
    definitions = {
        "A": (["A"], train_state_ids),
        "B": (["B"], heldout_state_ids),
        "C": (["C"], train_state_ids),
        "D": (["D"], heldout_state_ids),
        "E": (["B", "D"], heldout_state_ids),
    }
    output: dict[str, Any] = {}
    for name, (cells, state_ids) in definitions.items():
        output[name] = {
            "definition_cells": cells,
            "legal": candidate_space_summary(
                _space_rows(rows, cells, scoreable_only=False),
                state_ids=state_ids,
                state_task_by_id=state_task_by_id,
            ),
            "scoreable": candidate_space_summary(
                _space_rows(rows, cells, scoreable_only=True),
                state_ids=state_ids,
                state_task_by_id=state_task_by_id,
            ),
        }
    return output


def _old_panel_audit(
    old_labels: Sequence[Mapping[str, Any]],
    *,
    heldout_state_ids: Sequence[str],
    state_task_by_id: Mapping[str, str],
    threshold: float,
) -> dict[str, Any]:
    spaces = {}
    for name, cells in {"B": ["B"], "D": ["D"], "E": ["B", "D"]}.items():
        spaces[name] = candidate_space_summary(
            [row for row in old_labels if str(row["cell"]) in cells],
            state_ids=heldout_state_ids,
            state_task_by_id=state_task_by_id,
        )
    b_states = {
        row["state_example_id"]: row for row in spaces["B"]["state_rows"]
    }
    d_states = {
        row["state_example_id"]: row for row in spaces["D"]["state_rows"]
    }
    e_states = {
        row["state_example_id"]: row for row in spaces["E"]["state_rows"]
    }
    gaps = []
    for state_id in heldout_state_ids:
        if int(b_states[state_id]["maximum_tier"]) >= 3:
            continue
        gaps.append(
            {
                "state_example_id": state_id,
                "state_task_id": state_task_by_id[state_id],
                "B_maximum_tier": b_states[state_id]["maximum_tier"],
                "D_maximum_tier": d_states[state_id]["maximum_tier"],
                "E_maximum_tier": e_states[state_id]["maximum_tier"],
                "tier3_or_4_exists_in_D": int(d_states[state_id]["maximum_tier"])
                >= 3,
                "exact_api_exists_in_D": int(
                    d_states[state_id]["exact_api_candidate_count"]
                )
                > 0,
                "procedure_absent_from_all_148": int(
                    e_states[state_id]["maximum_tier"]
                )
                < 3,
                "missing_only_from_29_parent_train_side": int(
                    d_states[state_id]["maximum_tier"]
                )
                >= 3,
                "D_best_candidate_ids": d_states[state_id]["best_candidate_ids"],
                "D_best_parent_ids": d_states[state_id]["best_parent_ids"],
            }
        )
    partly_split = any(row["missing_only_from_29_parent_train_side"] for row in gaps)
    return {
        "format": "existing_panel_split_semantics_audit_6g_v1",
        "spaces": spaces,
        "original_b_gap_states": gaps,
        "original_b_gap_state_count": len(gaps),
        "audit_conclusion": (
            "existing_panel_coverage_failure_is_partly_parent_split_induced"
            if partly_split
            else "existing_panel_globally_insufficient"
        ),
        "strict_parent_holdout_branch_condition": bool(
            spaces["B"]["tier3_or_4_state_coverage"] < threshold
            and spaces["E"]["tier3_or_4_state_coverage"] >= threshold
        ),
    }


def _one_step_condition_preflight(
    label_rows: Sequence[Mapping[str, Any]],
    *,
    one_step_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in label_rows:
        if bool(row["scoreable_under_context"]):
            grouped[str(row["state_example_id"])].append(row)
    state_rows = []
    base_condition_count = 0
    optional_count = 0
    for query in one_step_rows:
        state_id = str(query["state_example_id"])
        rows = grouped.get(state_id, [])
        train = [row for row in rows if row["transition_split"] == "train"]
        heldout = [row for row in rows if row["transition_split"] == "heldout"]
        train_high = [row for row in train if int(row["procedural_tier"]) >= 3]
        full_high = [row for row in rows if int(row["procedural_tier"]) >= 3]
        heldout_high = [row for row in heldout if int(row["procedural_tier"]) >= 3]
        hard_negative = [
            row
            for row in train
            if str(row["transition_coarse_action_type"])
            == str(row["query_coarse_action_type"])
            and int(row["procedural_tier"]) < 3
        ]
        availability = {
            "baseline": True,
            "oracle_procedural_train_parent": bool(train_high),
            "field_top_candidate_pool": bool(train),
            "same_intent_hard_negative": bool(hard_negative),
            "transition_popularity_candidate_pool": bool(train),
            "deterministic_random_candidate_pool": bool(train),
            "optional_unseen_parent_oracle": bool(heldout_high),
        }
        base_condition_count += sum(
            availability[key]
            for key in (
                "baseline",
                "oracle_procedural_train_parent",
                "field_top_candidate_pool",
                "same_intent_hard_negative",
                "transition_popularity_candidate_pool",
                "deterministic_random_candidate_pool",
            )
        )
        optional_count += availability["optional_unseen_parent_oracle"]
        state_rows.append(
            {
                "state_example_id": state_id,
                "task_id": str(query["task_id"]),
                "scoreable_train_candidates": len(train),
                "scoreable_heldout_parent_candidates": len(heldout),
                "train_parent_tier3_or_4_candidates": len(train_high),
                "full_bank_tier3_or_4_candidates": len(full_high),
                "heldout_parent_tier3_or_4_candidates": len(heldout_high),
                "same_intent_hard_negative_candidates": len(hard_negative),
                "availability": availability,
            }
        )
    return {
        "format": "one_step_full_bank_condition_preflight_6g_v1",
        "query_state_count": len(one_step_rows),
        "target_base_condition_count": len(one_step_rows) * 6,
        "available_base_condition_count": base_condition_count,
        "missing_base_condition_count": len(one_step_rows) * 6
        - base_condition_count,
        "optional_unseen_parent_condition_count": optional_count,
        "total_condition_count_with_available_optional": base_condition_count
        + optional_count,
        "qwen_generation_count": base_condition_count + optional_count,
        "appworld_reconstruction_count": base_condition_count + optional_count,
        "appworld_execution_count": base_condition_count + optional_count,
        "state_rows": state_rows,
    }


def _timestamp_span_seconds(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = []
    for row in rows:
        value = row.get("created_at_utc")
        if value:
            values.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    if len(values) < 2:
        return None
    return (max(values) - min(values)).total_seconds()


def _runtime_and_storage(
    *,
    settings: Mapping[str, Any],
    transitions: Sequence[Mapping[str, Any]],
    panel_transition_ids: set[str],
    context_summary: Mapping[str, Any],
    old_scoreable_pairs: int,
    one_step_conditions: Mapping[str, Any],
    exp019_multiview_report: Mapping[str, Any],
    exp020_model_summary: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = settings["runtime"]
    new_transitions = [
        row for row in transitions if str(row["transition_id"]) not in panel_transition_ids
    ]
    panel = [
        row for row in transitions if str(row["transition_id"]) in panel_transition_ids
    ]
    panel_tokens = [float(row["teacher_section_tokens"]) for row in panel]
    new_tokens = [float(row["teacher_section_tokens"]) for row in new_transitions]
    token_ratio = sum(new_tokens) / max(1.0, sum(panel_tokens))
    quadratic_ratio = sum(value * value for value in new_tokens) / max(
        1.0, sum(value * value for value in panel_tokens)
    )
    observed_rows = exp019_multiview_report["transition"]["rows"]
    observed_seconds = _timestamp_span_seconds(observed_rows) or float(
        runtime["transition_representation_fallback_seconds"]
    )
    scoreable_pairs = int(context_summary["scoreable_pair_count"])
    new_cross_pairs = scoreable_pairs - int(old_scoreable_pairs)
    if new_cross_pairs < 0:
        raise ValueError("Full-bank scoreable pairs are fewer than the immutable panel")
    condition_count = int(one_step_conditions["total_condition_count_with_available_optional"])
    storage = {
        "new_transition_multiview_rows": len(new_transitions)
        * int(runtime["transition_representation_row_bytes"]),
        "field_model_and_checkpoints": int(runtime["field_model_artifact_bytes"]),
        "optional_new_cross_encoder_rows": new_cross_pairs
        * int(runtime["cross_encoder_row_bytes"]),
        "one_step_logs_snapshots_and_outputs": condition_count
        * int(runtime["one_step_artifact_bytes_per_condition"]),
    }
    return future_runtime_projection(
        newly_added_transitions=len(new_transitions),
        representation_observed_seconds=observed_seconds,
        representation_observed_transitions=len(panel),
        representation_token_ratio=token_ratio,
        representation_quadratic_token_ratio=quadratic_ratio,
        model_reference_seconds=float(exp020_model_summary["runtime_seconds"]),
        pair_scale=scoreable_pairs / int(old_scoreable_pairs),
        new_cross_encoder_pairs=new_cross_pairs,
        cross_encoder_seconds_per_pair=float(
            runtime["observed_cross_encoder_seconds_per_pair"]
        ),
        one_step_condition_count=condition_count,
        generation_seconds=runtime["generation_seconds_per_condition"],
        replay_execution_seconds=runtime["replay_execution_seconds_per_condition"],
        storage_bytes=storage,
        review_threshold_h100_hours=float(runtime["review_threshold_h100_hours"]),
    )


def _coverage_table(spaces: Mapping[str, Any], mode: str) -> list[str]:
    lines = [
        f"| Space | Pairs | Tier 0 | Tier 1 | Tier 2 | Tier 3 | Tier 4 | High-tier states | Exact-API states | Diverse high-tier states |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for space in "ABCDE":
        row = spaces[space][mode]
        tiers = row["tier_counts"]
        lines.append(
            f"| {space} | {row['pair_count']} | {tiers['0']} | {tiers['1']} | "
            f"{tiers['2']} | {tiers['3']} | {tiers['4']} | "
            f"{row['states_with_tier3_or_4']}/{row['state_count']} "
            f"({row['tier3_or_4_state_coverage']:.4f}) | "
            f"{row['states_with_exact_api']}/{row['state_count']} "
            f"({row['exact_api_state_coverage']:.4f}) | "
            f"{row['states_with_diverse_tier3_or_4']}/{row['state_count']} "
            f"({row['diverse_tier3_or_4_state_coverage']:.4f}) |"
        )
    return lines


def _reports(summary: Mapping[str, Any]) -> dict[str, str]:
    old = summary["existing_panel_audit"]
    full = summary["full_coverage"]
    context = summary["context_preflight"]
    redundancy = summary["signature_redundancy"]
    runtime = summary["future_exp024_projection"]
    old_lines = [
        "# EXP-023 Existing 148-Panel Split Semantics",
        "",
        "## VERIFIED",
        "",
    ]
    for space in "BDE":
        row = old["spaces"][space]
        old_lines.append(
            f"- {space}: Tier-3/4 `{row['states_with_tier3_or_4']}/{row['state_count']}` "
            f"(`{row['tier3_or_4_state_coverage']:.6f}`), exact API "
            f"`{row['states_with_exact_api']}/{row['state_count']}`."
        )
    old_lines.extend(
        [
            f"- audit conclusion: `{old['audit_conclusion']}`",
            f"- original B gap states: `{old['original_b_gap_state_count']}`",
            "",
            "## Gap States",
            "",
        ]
    )
    old_lines.extend(
        f"- `{row['state_example_id']}`: D max tier `{row['D_maximum_tier']}`, "
        f"D exact API `{row['exact_api_exists_in_D']}`, absent from all 148 "
        f"`{row['procedure_absent_from_all_148']}`."
        for row in old["original_b_gap_states"]
    )

    coverage_lines = [
        "# EXP-023 Full 499-Transition Procedural Coverage",
        "",
        "## VERIFIED Legal-Pair Coverage",
        "",
        *_coverage_table(full["spaces"], "legal"),
        "",
        "## VERIFIED Scoreable-Under-Context Coverage",
        "",
        *_coverage_table(full["spaces"], "scoreable"),
        "",
        f"- decision branch: `{summary['decision']['branch']}`",
        f"- original B continuity threshold: `{summary['decision']['threshold']:.2f}`",
        f"- full-bank procedural coverage passed: `{summary['decision']['full_bank_coverage_passed']}`",
        "",
    ]

    signature_lines = [
        "# EXP-023 Full Transition Signature Manifest",
        "",
        "## VERIFIED",
        "",
        f"- transitions: `{summary['signature_validation']['transition_count']}`",
        f"- parse status: `{summary['signature_validation']['parse_status']}`",
        f"- parser fallbacks: `{summary['signature_validation']['fallback_count']}`",
        f"- credential leakage rows: `{summary['signature_validation']['credential_leakage_count']}`",
        f"- immutable 148-row overlap mismatches: `{summary['signature_validation']['old_panel_overlap_mismatch_count']}`",
        f"- unique canonical action signatures: `{redundancy['unique_signature_count']}`",
        f"- API-documentation transitions: `{redundancy['api_documentation_transition_count']}` "
        f"(`{redundancy['api_documentation_transition_fraction']:.6f}`)",
        "",
    ]

    redundancy_lines = [
        "# EXP-023 Signature Redundancy And Candidate Diversity",
        "",
        "## VERIFIED",
        "",
        f"- transitions / unique signatures: `{redundancy['transition_count']}` / "
        f"`{redundancy['unique_signature_count']}`",
        f"- duplicate groups: `{redundancy['duplicate_group_count']}`",
        f"- duplicate transitions beyond one representative: `{redundancy['duplicate_transition_count']}`",
        f"- duplicate group-size summary: `{redundancy['group_size']}`",
        "",
        "No transition was deduplicated; these are audit statistics only.",
        "",
    ]

    missing_lines = [
        "# EXP-023 Missing-State Diagnostics",
        "",
        f"- B scoreable states missing Tier-3/4: `{len(summary['missing_states']['B_scoreable'])}`",
        f"- E scoreable states missing Tier-3/4: `{len(summary['missing_states']['E_scoreable'])}`",
        "",
        "## B Scoreable Gaps",
        "",
    ]
    missing_lines.extend(
        f"- `{row['state_example_id']}`: target "
        f"`{row['target_primary_app']}.{row['target_primary_api']}`, max tier "
        f"`{row['maximum_available_tier']}`, exact API "
        f"`{row['exact_api_available']}`, absent from corpus "
        f"`{row['procedure_absent_from_complete_corpus']}`."
        for row in summary["missing_states"]["B_scoreable"]
    )

    context_lines = [
        "# EXP-023 Tokenizer-Only Context Preflight",
        "",
        "## VERIFIED",
        "",
        f"- legal pairs: `{context['legal_pair_count']}`",
        f"- scoreable pairs: `{context['scoreable_pair_count']}`",
        f"- over-context pairs: `{context['over_context_pair_count']}` "
        f"(`{context['over_context_rate']:.6f}`)",
        f"- truncated pairs: `{context['truncated_pair_count']}`",
        f"- total-token summary: `{context['token_counts']['total_tokens_with_target']}`",
        f"- states whose only Tier-3/4 choices are over-context: "
        f"`{context['states_whose_only_tier3_or_4_candidates_are_over_context_count']}`",
        "",
        "This phase loaded the canonical tokenizer only. Qwen forward/generation and "
        "AppWorld execution counts are exactly zero.",
        "",
    ]

    runtime_lines = [
        "# EXP-023 Future EXP-024 Runtime And Storage Estimate",
        "",
        "## INFERENCE FROM MEASURED PRIOR RUNS",
        "",
        f"- required best / expected / conservative: "
        f"`{runtime['required_best_h100_hours']:.3f}` / "
        f"`{runtime['required_expected_h100_hours']:.3f}` / "
        f"`{runtime['required_conservative_h100_hours']:.3f}` H100-hours",
        f"- optional expanded cross-encoder expected: "
        f"`{runtime['optional_cross_encoder_expected_h100_hours']:.3f}` H100-hours",
        f"- expected including optional cross-encoder: "
        f"`{runtime['total_with_optional_expected_h100_hours']:.3f}` H100-hours",
        f"- projected artifacts: `{runtime['artifact_size_total_bytes']}` bytes",
        f"- expected required run exceeds 12-hour review threshold: "
        f"`{runtime['required_expected_exceeds_review_threshold']}`",
        f"- expected run with optional cross-encoder exceeds threshold: "
        f"`{runtime['with_optional_expected_exceeds_review_threshold']}`",
        "",
        "## Resume Plan",
        "",
        "- one immutable run UUID and append-only attempt ledger;",
        "- per-transition representation rows are atomic and hash validated;",
        "- model checkpoints carry source/config/data hashes and optimizer state;",
        "- optional cross-encoder rows use unique pair keys and atomic row files;",
        "- one-step conditions checkpoint each state/condition independently with a fresh AppWorld restore;",
        "- reconnects inspect heartbeat, tmux, process, and latest atomic checkpoint before resume.",
        "",
    ]

    final_lines = [
        "# EXP-023 Final Coverage And Cost Decision",
        "",
        "## VERIFIED",
        "",
        f"- source commit: `{summary['source_commit']}`",
        f"- run UUID: `{summary['run_uuid']}`",
        f"- 499-transition Cartesian / illegal / legal: "
        f"`{summary['counts']['cartesian_pairs']}` / "
        f"`{summary['counts']['illegal_pairs']}` / "
        f"`{summary['counts']['legal_pairs']}`",
        f"- scoreable / over-context: `{summary['counts']['scoreable_pairs']}` / "
        f"`{summary['counts']['over_context_pairs']}`",
        f"- decision branch: `{summary['decision']['branch']}`",
        f"- procedural model and one-step audit remain blocked: "
        f"`{summary['decision']['future_experiment_remains_blocked']}`",
        "",
        "No model training, Qwen forward/generation, AppWorld instance, action execution, "
        "or V4 tag operation occurred.",
        "",
    ]
    return {
        "existing_panel_split_semantics_report.md": "\n".join(old_lines) + "\n",
        "full_transition_signature_report.md": "\n".join(signature_lines) + "\n",
        "full_transition_coverage_report.md": "\n".join(coverage_lines) + "\n",
        "missing_state_report.md": "\n".join(missing_lines) + "\n",
        "signature_redundancy_report.md": "\n".join(redundancy_lines) + "\n",
        "context_preflight_report.md": "\n".join(context_lines) + "\n",
        "future_exp024_runtime_storage_report.md": "\n".join(runtime_lines) + "\n",
        "final_exp023_report.md": "\n".join(final_lines) + "\n",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare EXP-023 coverage preflight")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_procedural_coverage_6g.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp023")
    parser.add_argument("--parent-attempt-id", default=None)
    parser.add_argument("--resume-checkpoint", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6g"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    exp019 = Path(settings["exp019_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp021 = Path(settings["exp021_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "transition_manifest": exp017 / "transition_manifest.jsonl",
        "transition_panel": exp017 / "transition_panel.jsonl",
        "exp017_validation": exp017 / "postrun_validation.json",
        "parent_split": exp018 / "transition_parent_split_manifest.json",
        "exp019_multiview_report": exp019
        / "parts_c_d/multiview_cache_report.json",
        "expanded_query_manifest": exp020 / "expanded_query_manifest.json",
        "old_pair_preflight": exp020 / "pair_preflight.jsonl",
        "exp020_final": exp020 / "final_summary.json",
        "exp020_model_summary": exp020 / "model_summary.json",
        "exp020_validation": exp020 / "postrun_validation.json",
        "exp021_validation": exp021 / "postrun_validation.json",
        "exp022_signatures": exp022 / "procedural_signatures.jsonl",
        "exp022_labels": exp022 / "procedural_label_rows.jsonl",
        "exp022_one_step": exp022 / "one_step_query_manifest.json",
        "exp022_summary": exp022 / "final_exp022_summary.json",
        "exp022_validation": exp022 / "postrun_validation.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "immutable_148_panel_B_D_E_audit",
            "all_499_transition_signatures",
            "all_499_legal_procedural_tiers",
            "tokenizer_only_context_preflight",
            "one_step_condition_preflight",
            "future_exp024_runtime_storage_estimate",
            "no_model_qwen_appworld_execution",
        ],
    )
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    atomic_write_json(args.artifact_dir / "stage_c_6g_settings.json", settings)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="full_transition_procedural_coverage_and_cost_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        expected = settings["expected"]
        validations = {
            "exp017": _load_json(paths["exp017_validation"]),
            "exp020": _load_json(paths["exp020_validation"]),
            "exp021": _load_json(paths["exp021_validation"]),
            "exp022": _load_json(paths["exp022_validation"]),
        }
        if not all(bool(value.get("passed")) for value in validations.values()):
            raise ValueError("One or more immutable prior-artifact validations failed")
        examples = load_decision_examples(paths["decision_examples"])
        transitions = _load_rows(paths["transition_manifest"])
        panel = _load_rows(paths["transition_panel"])
        query_manifest = _load_json(paths["expanded_query_manifest"])
        parent_split = _load_json(paths["parent_split"])
        old_preflight = _load_rows(paths["old_pair_preflight"])
        old_signatures = _load_rows(paths["exp022_signatures"])
        old_labels = _load_rows(paths["exp022_labels"])
        one_step_manifest = _load_json(paths["exp022_one_step"])
        old_exp022 = _load_json(paths["exp022_summary"])
        _assert_count("decision examples", len(examples), expected["decision_examples"])
        _assert_count("full transitions", len(transitions), expected["transitions"])
        _assert_count("old panel", len(panel), expected["old_panel_transitions"])
        _assert_count(
            "transition parents",
            len({str(row["parent_memory_id"]) for row in transitions}),
            expected["transition_parents"],
        )
        _assert_count("query states", query_manifest["query_count"], expected["query_states"])
        _assert_count(
            "train queries", query_manifest["train_query_count"], expected["train_query_states"]
        )
        _assert_count(
            "heldout queries",
            query_manifest["validation_query_count"],
            expected["heldout_query_states"],
        )
        _assert_count(
            "train transition parents",
            parent_split["train_parent_count"],
            expected["train_transition_parents"],
        )
        _assert_count(
            "heldout transition parents",
            parent_split["heldout_parent_count"],
            expected["heldout_transition_parents"],
        )
        _assert_count("old legal rows", len(old_preflight), expected["old_legal_rows"])
        _assert_count("old scoreable rows", len(old_labels), expected["old_scoreable_rows"])
        _assert_count(
            "old over-context rows",
            sum(bool(row["over_context"]) for row in old_preflight),
            expected["old_over_context_rows"],
        )
        _assert_count(
            "one-step query states",
            one_step_manifest["query_count"],
            expected["one_step_query_states"],
        )
        panel_ids = {str(row["transition_id"]) for row in panel}
        transition_ids = {str(row["transition_id"]) for row in transitions}
        if len(transition_ids) != len(transitions) or not panel_ids.issubset(
            transition_ids
        ):
            raise ValueError("Transition manifest IDs are duplicated or omit panel IDs")
        all_parent_ids = {str(row["parent_memory_id"]) for row in transitions}
        if all_parent_ids != set(parent_split["split_by_parent"]):
            raise ValueError("Full transition parents differ from immutable 29/8 split")
        query_by_id = _query_rows_by_id(query_manifest)
        state_task_by_id = {
            state_id: str(row["task_id"]) for state_id, row in query_by_id.items()
        }
        train_state_ids = [
            state_id for state_id, row in query_by_id.items() if row["split"] == "train"
        ]
        heldout_state_ids = [
            state_id
            for state_id, row in query_by_id.items()
            if row["split"] == "validation"
        ]
        _assert_count(
            "train query tasks",
            len({state_task_by_id[value] for value in train_state_ids}),
            expected["train_query_tasks"],
        )
        _assert_count(
            "heldout query tasks",
            len({state_task_by_id[value] for value in heldout_state_ids}),
            expected["heldout_query_tasks"],
        )
        old_b_high = sum(
            int(row["maximum_tier"]) >= 3
            for row in candidate_space_summary(
                [row for row in old_labels if row["cell"] == "B"],
                state_ids=heldout_state_ids,
                state_task_by_id=state_task_by_id,
            )["state_rows"]
        )
        _assert_count(
            "old B high-tier states", old_b_high, expected["old_b_high_tier_states"]
        )
        if old_exp022.get("decision", {}).get("branch") not in (
            None,
            "transition_panel_procedural_coverage_insufficient",
        ):
            raise ValueError("Immutable EXP-022 decision branch differs")
        attempt.progress(
            status="immutable_inputs_validated",
            transition_count=len(transitions),
            query_count=len(query_by_id),
            latest_validated_checkpoint=str(paths["exp022_validation"]),
        )

        query_signature_rows = [
            row for row in old_signatures if row.get("kind") == "query"
        ]
        query_signatures = {
            str(row["state_example_id"]): row for row in query_signature_rows
        }
        _assert_count(
            "query signatures", len(query_signatures), expected["decision_examples"]
        )
        transition_signature_rows, signature_validation = _full_transition_signatures(
            transitions, old_signature_rows=old_signatures
        )
        _atomic_write_jsonl(
            args.artifact_dir / "full_transition_signature_manifest.jsonl",
            transition_signature_rows,
        )
        atomic_write_json(
            args.artifact_dir / "full_transition_signature_validation.json",
            signature_validation,
        )
        if signature_validation["fallback_count"]:
            raise ValueError(
                "At least one full-bank action requires parser fallback and cannot be "
                "audited safely"
            )
        if signature_validation["credential_leakage_count"]:
            raise ValueError("Full-bank procedural signatures leaked credentials")
        if signature_validation["old_panel_overlap_mismatch_count"]:
            raise ValueError("Full-bank parser does not reproduce immutable panel signatures")
        transition_signatures = {
            str(row["transition_id"]): row for row in transition_signature_rows
        }
        redundancy = signature_redundancy_summary(transition_signature_rows)
        atomic_write_json(
            args.artifact_dir / "signature_equivalence_groups.json", redundancy
        )
        attempt.progress(
            status="full_signatures_validated",
            transition_count=len(transition_signature_rows),
            unique_signature_count=redundancy["unique_signature_count"],
            latest_validated_checkpoint=str(
                args.artifact_dir / "full_transition_signature_validation.json"
            ),
        )

        tokenizer = AutoTokenizer.from_pretrained(
            cfg.model.name, trust_remote_code=True
        )
        if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        full_preflight, full_illegal, full_resume = _resumable_preflight(
            tokenizer=tokenizer,
            examples=examples,
            query_rows=query_manifest["query_rows"],
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["context_limit"]),
            checkpoint_dir=args.artifact_dir / "checkpoints/full_query_preflight",
            transition_manifest_sha256=data_hashes["transition_manifest"],
            attempt=attempt,
            scope="full_92_query",
        )
        cartesian = len(query_by_id) * len(transitions)
        if len(full_preflight) + len(full_illegal) != cartesian:
            raise ValueError("Full legal plus illegal rows do not equal Cartesian count")
        _atomic_write_jsonl(args.artifact_dir / "full_pair_preflight.jsonl", full_preflight)
        _atomic_write_jsonl(args.artifact_dir / "full_illegal_pairs.jsonl", full_illegal)
        label_rows = _build_label_rows(
            preflight_rows=full_preflight,
            query_signatures=query_signatures,
            transition_signatures=transition_signatures,
            parent_split=parent_split,
        )
        _atomic_write_jsonl(
            args.artifact_dir / "full_procedural_label_rows.jsonl", label_rows
        )
        label_by_pair = {str(row["pair_id"]): row for row in label_rows}
        old_label_mismatches = []
        for old in old_labels:
            current = label_by_pair.get(str(old["pair_id"]))
            if current is None:
                old_label_mismatches.append(
                    {"pair_id": old["pair_id"], "field": "missing"}
                )
                continue
            for field in (
                "procedural_tier",
                "query_signature_sha256",
                "query_stage_sha256",
                "transition_signature_sha256",
                "transition_stage_sha256",
                "transition_observation_sha256",
            ):
                if old[field] != current[field]:
                    old_label_mismatches.append(
                        {"pair_id": old["pair_id"], "field": field}
                    )
        if old_label_mismatches:
            raise ValueError(
                f"Full labels do not preserve immutable EXP-022 labels: {old_label_mismatches[:20]}"
            )

        old_audit = _old_panel_audit(
            old_labels,
            heldout_state_ids=heldout_state_ids,
            state_task_by_id=state_task_by_id,
            threshold=float(settings["heldout_high_tier_coverage_gate"]),
        )
        coverage_spaces = _coverage_spaces(
            label_rows,
            train_state_ids=train_state_ids,
            heldout_state_ids=heldout_state_ids,
            state_task_by_id=state_task_by_id,
        )
        context_summary = context_preflight_summary(
            full_preflight, label_rows=label_rows
        )
        missing_b = missing_state_diagnostics(
            _space_rows(label_rows, ["B"], scoreable_only=False),
            state_ids=heldout_state_ids,
            query_signatures=query_signatures,
            transition_signatures=transition_signature_rows,
            scoreable_only=True,
        )
        missing_e = missing_state_diagnostics(
            _space_rows(label_rows, ["B", "D"], scoreable_only=False),
            state_ids=heldout_state_ids,
            query_signatures=query_signatures,
            transition_signatures=transition_signature_rows,
            scoreable_only=True,
        )

        example_indices = _example_index_by_state(examples)
        one_step_query_rows = []
        for row in one_step_manifest["rows"]:
            state_id = str(row["state_example_id"])
            if state_id not in example_indices or state_id not in query_signatures:
                raise ValueError(f"One-step state is absent from source examples: {state_id}")
            one_step_query_rows.append(
                {
                    **dict(row),
                    "example_index": example_indices[state_id],
                    "split": "validation",
                }
            )
        one_preflight, one_illegal, one_resume = _resumable_preflight(
            tokenizer=tokenizer,
            examples=examples,
            query_rows=one_step_query_rows,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["context_limit"]),
            checkpoint_dir=args.artifact_dir / "checkpoints/one_step_query_preflight",
            transition_manifest_sha256=data_hashes["transition_manifest"],
            attempt=attempt,
            scope="one_step_45_query",
        )
        if len(one_preflight) + len(one_illegal) != len(one_step_query_rows) * len(
            transitions
        ):
            raise ValueError("One-step legal plus illegal rows do not equal Cartesian count")
        _atomic_write_jsonl(
            args.artifact_dir / "one_step_pair_preflight.jsonl", one_preflight
        )
        _atomic_write_jsonl(
            args.artifact_dir / "one_step_illegal_pairs.jsonl", one_illegal
        )
        one_labels = _build_label_rows(
            preflight_rows=one_preflight,
            query_signatures=query_signatures,
            transition_signatures=transition_signatures,
            parent_split=parent_split,
        )
        one_context_summary = context_preflight_summary(
            one_preflight, label_rows=one_labels
        )
        one_conditions = _one_step_condition_preflight(
            one_labels, one_step_rows=one_step_query_rows
        )
        atomic_write_json(
            args.artifact_dir / "one_step_condition_preflight.json",
            {
                "context": one_context_summary,
                "conditions": one_conditions,
                "resume": one_resume,
            },
        )

        b_scoreable = coverage_spaces["B"]["scoreable"]
        e_scoreable = coverage_spaces["E"]["scoreable"]
        threshold = float(settings["heldout_high_tier_coverage_gate"])
        branch = select_decision_branch(
            b_coverage=float(b_scoreable["tier3_or_4_state_coverage"]),
            b_diverse_coverage=float(
                b_scoreable["diverse_tier3_or_4_state_coverage"]
            ),
            e_coverage=float(e_scoreable["tier3_or_4_state_coverage"]),
            threshold=threshold,
        )
        runtime_projection = _runtime_and_storage(
            settings=settings,
            transitions=transitions,
            panel_transition_ids=panel_ids,
            context_summary=context_summary,
            old_scoreable_pairs=int(expected["old_scoreable_rows"]),
            one_step_conditions=one_conditions,
            exp019_multiview_report=_load_json(paths["exp019_multiview_report"]),
            exp020_model_summary=_load_json(paths["exp020_model_summary"]),
        )
        cell_counts = {
            cell: {
                "legal": sum(str(row["cell"]) == cell for row in label_rows),
                "scoreable": sum(
                    str(row["cell"]) == cell and bool(row["scoreable_under_context"])
                    for row in label_rows
                ),
                "over_context": sum(
                    str(row["cell"]) == cell and bool(row["over_context"])
                    for row in label_rows
                ),
            }
            for cell in "ABCD"
        }
        summary = {
            "format": "full_transition_procedural_coverage_summary_6g_v1",
            "status": "completed_coverage_cost_decision",
            "run_uuid": str(settings["run_uuid"]),
            "timestamp_utc": utc_now(),
            "source_commit": args.lambda_head,
            "counts": {
                "queries": len(query_by_id),
                "train_queries": len(train_state_ids),
                "heldout_queries": len(heldout_state_ids),
                "transitions": len(transitions),
                "new_transitions_beyond_panel": len(transitions) - len(panel),
                "transition_parents": len(all_parent_ids),
                "cartesian_pairs": cartesian,
                "illegal_pairs": len(full_illegal),
                "legal_pairs": len(full_preflight),
                "scoreable_pairs": context_summary["scoreable_pair_count"],
                "over_context_pairs": context_summary["over_context_pair_count"],
                "cell_counts": cell_counts,
            },
            "immutable_validation": {
                "prior_validations_passed": True,
                "old_label_reproduction_mismatches": len(old_label_mismatches),
                "transition_panel_is_exact_subset": True,
                "parent_split_preserved": True,
                "no_duplicate_pair_keys": len(label_by_pair) == len(label_rows),
            },
            "signature_validation": signature_validation,
            "signature_redundancy": redundancy,
            "existing_panel_audit": old_audit,
            "full_coverage": {
                "spaces": coverage_spaces,
                "historical_continuity_uses_scoreable_rows": True,
                "deployment_space": "E = B union D",
                "diversity_definition": {
                    "minimum_unique_high_tier_signatures": int(
                        settings["diversity_minimum_unique_signatures"]
                    ),
                    "minimum_distinct_high_tier_parents": int(
                        settings["diversity_minimum_parents"]
                    ),
                    "note": "A state is diversity-qualified only when both minima hold.",
                },
            },
            "missing_states": {
                "B_scoreable": missing_b,
                "E_scoreable": missing_e,
            },
            "context_preflight": context_summary,
            "one_step_preflight": {
                "context": one_context_summary,
                "conditions": one_conditions,
            },
            "resume": {
                "full_query_preflight": full_resume,
                "one_step_query_preflight": one_resume,
            },
            "future_exp024_projection": runtime_projection,
            "decision": {
                "branch": branch,
                "threshold": threshold,
                "B_scoreable_coverage": b_scoreable["tier3_or_4_state_coverage"],
                "B_scoreable_diverse_coverage": b_scoreable[
                    "diverse_tier3_or_4_state_coverage"
                ],
                "E_scoreable_coverage": e_scoreable["tier3_or_4_state_coverage"],
                "full_bank_coverage_passed": branch
                == "full_transition_bank_procedural_coverage_passed",
                "future_experiment_remains_blocked": True,
                "next_step_requires_user_chatgpt_review": True,
            },
            "hard_scope": {
                "model_training_count": 0,
                "qwen_forward_count": 0,
                "qwen_generation_count": 0,
                "appworld_instance_count": 0,
                "action_execution_count": 0,
                "truncated_pair_count": context_summary["truncated_pair_count"],
                "procedural_tier_changed": False,
                "v4_tag_created_or_moved": False,
            },
            "runtime_seconds": time.perf_counter() - started,
            "artifact_paths": {
                "root": str(args.artifact_dir),
                "signature_manifest": str(
                    args.artifact_dir / "full_transition_signature_manifest.jsonl"
                ),
                "procedural_labels": str(
                    args.artifact_dir / "full_procedural_label_rows.jsonl"
                ),
                "context_preflight": str(
                    args.artifact_dir / "full_pair_preflight.jsonl"
                ),
                "one_step_preflight": str(
                    args.artifact_dir / "one_step_condition_preflight.json"
                ),
            },
        }
        atomic_write_json(args.artifact_dir / "final_exp023_summary.json", summary)
        for name, text in _reports(summary).items():
            atomic_write_text(args.artifact_dir / name, text)
        attempt.progress(
            status="completed_coverage_cost_decision",
            decision_branch=branch,
            legal_pairs=len(full_preflight),
            scoreable_pairs=context_summary["scoreable_pair_count"],
            over_context_pairs=context_summary["over_context_pair_count"],
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_exp023_summary.json"
            ),
        )


if __name__ == "__main__":
    main()
