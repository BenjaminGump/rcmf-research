from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.training.multiview_representations_6c import (
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)
from rcmf.utils.serialization import sha256_text


SCHEMA_VERSION = "rcmf_transition_token_metadata_14c_v1"
SECTION_TOKEN_FIELDS = {
    "source_task_goal": "source_task_goal_tokens",
    "pre_action_state": "canonical_pre_action_state_tokens",
    "complete_action": "complete_action_tokens",
    "post_action_observation": "complete_post_action_observation_tokens",
}
REQUIRED_TOKEN_FIELDS = (
    "teacher_section_tokens",
    *SECTION_TOKEN_FIELDS.values(),
)


def schema_definition() -> dict[str, Any]:
    body = {
        "format": SCHEMA_VERSION,
        "derivation": {
            "renderer": "transition_text_and_char_spans",
            "tokenizer": "tokenize_and_validate_char_spans",
            "complete_teacher_section_tokenized_once": True,
            "add_special_tokens": False,
            "truncation": False,
            "return_offsets_mapping": True,
            "section_counts_derived_from_complete_render_offsets": True,
        },
        "required_integer_fields": list(REQUIRED_TOKEN_FIELDS),
        "span_to_field": dict(SECTION_TOKEN_FIELDS),
        "consumer": "scripts.prepare_rcmf_joint_full_bank_9a._section_contract",
        "historical_consumer_semantics_changed": False,
    }
    return {**body, "schema_sha256": content_sha256(body)}


def tokenizer_identity(tokenizer: Any, snapshot: str | Path | None = None) -> dict[str, Any]:
    init_kwargs = getattr(tokenizer, "init_kwargs", {})
    stable_init = {
        key: init_kwargs.get(key)
        for key in (
            "_commit_hash",
            "revision",
            "tokenizer_file",
            "model_max_length",
            "padding_side",
            "truncation_side",
        )
        if isinstance(init_kwargs, Mapping) and key in init_kwargs
    }
    identity = {
        "name_or_path": str(getattr(tokenizer, "name_or_path", "unknown")),
        "class": type(tokenizer).__name__,
        "vocab_size": int(getattr(tokenizer, "vocab_size", -1)),
        "model_max_length": int(getattr(tokenizer, "model_max_length", -1)),
        "chat_template_sha256": sha256_text(str(getattr(tokenizer, "chat_template", ""))),
        "special_tokens_map_sha256": content_sha256(
            getattr(tokenizer, "special_tokens_map", {})
        ),
        "stable_init": stable_init,
        "configured_snapshot": None if snapshot is None else str(snapshot),
    }
    snapshot_path = Path(str(snapshot)) if snapshot is not None else None
    snapshot_files = []
    if snapshot_path is not None and snapshot_path.is_dir():
        for name in (
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "added_tokens.json",
            "vocab.json",
            "merges.txt",
        ):
            path = snapshot_path / name
            if path.is_file():
                snapshot_files.append(file_identity(path, snapshot_path))
    identity["snapshot_files"] = snapshot_files
    identity["tokenizer_identity_sha256"] = content_sha256(identity)
    identity["tokenizer_snapshot_sha256"] = content_sha256(
        snapshot_files if snapshot_files else identity
    )
    return identity


def derive_transition_token_metadata(
    transition: Mapping[str, Any], tokenizer: Any, *, tokenizer_info: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    rendered, char_spans, source_metadata = transition_text_and_char_spans(transition)
    input_ids, _, span_rows = tokenize_and_validate_char_spans(
        tokenizer, rendered, char_spans
    )
    metadata = {
        "transition_token_metadata_schema": SCHEMA_VERSION,
        "teacher_section_tokens": int(input_ids.shape[1]),
        "teacher_section_sha256": sha256_text(rendered),
        "tokenizer_name_or_path": str(getattr(tokenizer, "name_or_path", "unknown")),
        "tokenizer_identity_sha256": str(tokenizer_info["tokenizer_identity_sha256"]),
        "tokenizer_snapshot_sha256": str(tokenizer_info["tokenizer_snapshot_sha256"]),
    }
    for span_name, field_name in SECTION_TOKEN_FIELDS.items():
        metadata[field_name] = int(span_rows[span_name]["token_count"])
    audit = {
        "transition_id": str(transition["transition_id"]),
        "teacher_section_sha256": metadata["teacher_section_sha256"],
        "teacher_section_tokens": metadata["teacher_section_tokens"],
        "span_rows": span_rows,
        "source_metadata": source_metadata,
        "truncated": False,
        "token_subsampling": False,
    }
    return metadata, audit


def enrich_transition_token_metadata(
    transitions: Sequence[Mapping[str, Any]],
    tokenizer: Any,
    *,
    snapshot: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    tokenizer_info = tokenizer_identity(tokenizer, snapshot)
    enriched: list[dict[str, Any]] = []
    row_audits = []
    mismatches: list[dict[str, Any]] = []
    ordered_ids = [str(row["transition_id"]) for row in transitions]
    for position, transition in enumerate(transitions):
        transition_id = str(transition["transition_id"])
        try:
            metadata, row_audit = derive_transition_token_metadata(
                transition, tokenizer, tokenizer_info=tokenizer_info
            )
        except Exception as error:
            mismatches.append(
                {
                    "position": position,
                    "transition_id": transition_id,
                    "kind": "metadata_derivation_error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            continue
        row = dict(transition)
        row.update(metadata)
        changed_source_fields = [
            key
            for key, value in transition.items()
            if key not in metadata and row.get(key) != value
        ]
        if changed_source_fields:
            mismatches.append(
                {
                    "position": position,
                    "transition_id": transition_id,
                    "kind": "authoritative_field_changed",
                    "fields": changed_source_fields,
                }
            )
        for text_field, hash_field in (
            ("source_task_goal", "source_task_goal_sha256"),
            ("canonical_pre_action_state", "canonical_pre_action_state_sha256"),
            ("complete_action", "complete_action_sha256"),
            (
                "complete_post_action_observation",
                "complete_post_action_observation_sha256",
            ),
        ):
            if hash_field in transition and sha256_text(str(transition[text_field])) != str(
                transition[hash_field]
            ):
                mismatches.append(
                    {
                        "position": position,
                        "transition_id": transition_id,
                        "kind": "source_text_hash_mismatch",
                        "field": text_field,
                        "hash_field": hash_field,
                    }
                )
        enriched.append(row)
        row_audits.append(row_audit)
    actual_ids = [str(row["transition_id"]) for row in enriched]
    if actual_ids != ordered_ids:
        mismatches.append(
            {
                "kind": "transition_order_or_coverage_mismatch",
                "expected_count": len(ordered_ids),
                "actual_count": len(actual_ids),
                "expected_order_sha256": content_sha256(ordered_ids),
                "actual_order_sha256": content_sha256(actual_ids),
            }
        )
    required_field_missing = sum(
        any(field not in row for field in REQUIRED_TOKEN_FIELDS) for row in enriched
    )
    report = {
        "format": "rcmf_transition_token_metadata_audit_14c_v1",
        "schema": schema_definition(),
        "row_count": len(enriched),
        "expected_row_count": len(transitions),
        "ordered_transition_ids_sha256": content_sha256(ordered_ids),
        "required_field_missing_rows": required_field_missing,
        "mismatch_count": len(mismatches),
        "truncation_count": 0,
        "token_subsampling_count": 0,
        "tokenizer": tokenizer_info,
        "row_audits_sha256": content_sha256(row_audits),
        "passed": (
            len(enriched) == len(transitions)
            and required_field_missing == 0
            and not mismatches
        ),
    }
    return enriched, report, mismatches
