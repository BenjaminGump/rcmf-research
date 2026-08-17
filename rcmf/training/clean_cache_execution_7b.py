from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

import torch

from rcmf.benchmarks.appworld.transitions import transition_teacher_section
from rcmf.schemas import MemoryRecord
from rcmf.training.clean_cache_rebuild_7b import (
    AFFECTED_TASK_IDS,
    canonical_json_sha256,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.pair_grounding_5d import PairSelectionConfig, select_stratified_pair_set
from rcmf.training.stage_c1 import (
    POSITIVE_TEACHER_EPS,
    load_teacher_rows,
    select_teacher_conditions,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)


EXECUTION_VERSION = "identity_reconciled_incremental_cache_execution_7b_v1"


def transition_representation_work_queue(
    *,
    transition_mapping: Sequence[Mapping[str, Any]],
    clean_transitions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    clean_by_id = {str(row["transition_id"]): dict(row) for row in clean_transitions}
    output = []
    for mapping in transition_mapping:
        clean_id = str(mapping["clean_transition_id"])
        row = clean_by_id.get(clean_id)
        if row is None:
            raise ValueError(f"Clean transition is missing from the structural corpus: {clean_id}")
        if str(row["parent_task_id"]) != str(mapping["parent_task_id"]):
            raise ValueError(f"Transition parent differs for {clean_id}")
        if int(row["step_index"]) != int(mapping["step_index"]):
            raise ValueError(f"Transition step differs for {clean_id}")
        output.append({**row, "source_old_transition_id": str(mapping["old_transition_id"])})
    return output


def _rows(path: Path) -> list[dict[str, Any]]:
    values = list(read_jsonl(path))
    if not values:
        raise ValueError(f"No rows found at {path}")
    return values


def _keyed(
    rows: Sequence[Mapping[str, Any]], key_fn: Callable[[Mapping[str, Any]], str]
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        if key in output:
            raise ValueError(f"Duplicate row key: {key}")
        output[key] = dict(row)
    return output


def _canonical_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return canonical_json_sha256(dict(left)) == canonical_json_sha256(dict(right))


def seed_jsonl(
    *,
    output_path: Path,
    reusable_rows: Sequence[Mapping[str, Any]],
    expected_keys: set[str],
    key_fn: Callable[[Mapping[str, Any]], str],
) -> dict[str, Any]:
    reusable = _keyed(reusable_rows, key_fn)
    if not set(reusable).issubset(expected_keys):
        raise ValueError("Reusable rows contain keys outside the clean expected set")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = list(read_jsonl(output_path))
    existing = _keyed(existing_rows, key_fn) if existing_rows else {}
    if not set(existing).issubset(expected_keys):
        unexpected = sorted(set(existing) - expected_keys)
        raise ValueError(f"Existing clean cache has unexpected keys: {unexpected[:5]}")
    appended = 0
    for key, row in reusable.items():
        prior = existing.get(key)
        if prior is not None:
            if not _canonical_equal(prior, row):
                raise ValueError(f"Reusable row changed in existing clean cache: {key}")
            continue
        if not existing_rows:
            existing[key] = row
            continue
        append_jsonl(output_path, row)
        existing[key] = row
        appended += 1
    if not existing_rows:
        ordered = [reusable[key] for key in reusable]
        write_jsonl(output_path, ordered)
        appended = len(ordered)
    return {
        "output_path": str(output_path),
        "expected_key_count": len(expected_keys),
        "reusable_row_count": len(reusable),
        "existing_row_count_after_seed": len(existing),
        "appended_row_count": appended,
        "output_sha256": sha256_file(output_path) if output_path.exists() else None,
    }


def seed_raw_teacher(
    *, preflight_manifest: Mapping[str, Any], old_path: Path, output_path: Path
) -> dict[str, Any]:
    audit = preflight_manifest["caches"]["raw_text_teacher"]
    affected = {str(row["key"]) for row in audit["affected_rows"]}
    old_rows = _rows(old_path)
    expected = {str(row["pair_key"]) for row in old_rows}
    reusable = [row for row in old_rows if str(row["pair_key"]) not in affected]
    result = seed_jsonl(
        output_path=output_path,
        reusable_rows=reusable,
        expected_keys=expected,
        key_fn=lambda row: str(row["pair_key"]),
    )
    result.update(
        {
            "format": EXECUTION_VERSION,
            "cache": "raw_text_teacher",
            "historical_row_count": len(old_rows),
            "affected_row_count": len(affected),
        }
    )
    return result


def _condition_matches_old_row(
    condition: Mapping[str, Any], row: Mapping[str, Any], records: Sequence[MemoryRecord]
) -> bool:
    if str(condition["task_id"]) in AFFECTED_TASK_IDS:
        return False
    mapped = {
        "state_index": "state_index",
        "state_example_id": "state_example_id",
        "task_id": "task_id",
        "episode_id": "episode_id",
        "step_id": "step_id",
        "split": "split",
        "valid_for_stage_c": "valid_for_stage_c",
        "all_missing_state": "all_missing_state",
        "no_positive_state": "no_positive_state",
        "best_memory_id": "best_memory_id",
        "best_memory_index": "best_memory_index",
        "best_pair_key": "best_pair_key",
        "L0": "L0",
        "Lj_text": "teacher_Lj_text",
        "best_utility": "teacher_utility",
    }
    if any(condition.get(source) != row.get(target) for source, target in mapped.items()):
        return False
    if condition.get("condition") != row.get("teacher_condition"):
        return False
    memory_index = condition.get("best_memory_index")
    if memory_index is not None:
        record = records[int(memory_index)]
        if record.task_id in AFFECTED_TASK_IDS:
            return False
        if str(row.get("memory_text_sha256")) != sha256_text(record.experience_text):
            return False
    return True


def seed_stage_c1(
    *,
    data_dir: Path,
    labels_dir: Path,
    teacher_cache_dir: Path,
    old_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    records = load_memory_records(data_dir / "memory_records.jsonl")
    label_rows = _rows(labels_dir / "student_labels.jsonl")
    memory_bank = _rows(labels_dir / "effective_memory_bank.jsonl")
    teacher = load_teacher_rows(read_jsonl(teacher_cache_dir / "teacher_cache_full_rows.jsonl"))
    conditions = select_teacher_conditions(
        label_rows, memory_bank, teacher, positive_eps=POSITIVE_TEACHER_EPS
    )
    old = _keyed(_rows(old_path), lambda row: str(row["state_example_id"]))
    reusable = []
    for condition in conditions:
        candidate = old.get(str(condition["state_example_id"]))
        if candidate is not None and _condition_matches_old_row(condition, candidate, records):
            reusable.append(candidate)
    expected = {str(row["state_example_id"]) for row in conditions}
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output_dir / "clean_teacher_conditions_seed.json",
        {"format": "identity_reconciled_stage_c1_condition_seed_7b_v1", "rows": conditions},
    )
    result = seed_jsonl(
        output_path=output_dir / "response_cache.jsonl",
        reusable_rows=reusable,
        expected_keys=expected,
        key_fn=lambda row: str(row["state_example_id"]),
    )
    result.update(
        {
            "format": EXECUTION_VERSION,
            "cache": "stage_c1_response",
            "selected_state_count": len(conditions),
            "cascade_recompute_count": len(conditions) - len(reusable),
            "historical_static_affected_count": 60,
        }
    )
    return result


def _selected_pair_matches_old(
    selected: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    allow_reconciled_tasks: bool = False,
) -> bool:
    if not allow_reconciled_tasks and str(selected["task_id"]) in AFFECTED_TASK_IDS:
        return False
    if not allow_reconciled_tasks and str(selected["memory_task_id"]) in AFFECTED_TASK_IDS:
        return False
    fields = (
        "state_index",
        "state_example_id",
        "task_id",
        "episode_id",
        "step_id",
        "split",
        "memory_stage_index",
        "memory_index",
        "memory_id",
        "memory_task_id",
        "memory_episode_id",
        "pair_key",
        "pair_id",
        "selection_category",
        "utility_category",
        "L0",
        "memory_text_sha256",
    )
    return all(selected.get(field) == row.get(field) for field in fields) and (
        selected.get("raw_utility") == row.get("text_utility")
    )


def seed_pair_response(*, labels_dir: Path, old_path: Path, output_dir: Path) -> dict[str, Any]:
    label_rows = _rows(labels_dir / "student_labels.jsonl")
    memory_bank = _rows(labels_dir / "effective_memory_bank.jsonl")
    selected, selection_summary = select_stratified_pair_set(
        label_rows, memory_bank, config=PairSelectionConfig()
    )
    old = _keyed(_rows(old_path), lambda row: str(row["pair_id"]))
    output_path = output_dir / "pair_response_cache.jsonl"
    existing = _keyed(_rows(output_path), lambda row: str(row["pair_id"]))
    selected_by_id = _keyed(selected, lambda row: str(row["pair_id"]))
    for pair_id, row in existing.items():
        selected_pair = selected_by_id.get(pair_id)
        if selected_pair is None:
            raise ValueError(f"Existing pair-response row is stale or invalid: {pair_id}")
        old_candidate = old.get(pair_id)
        if old_candidate is not None and _selected_pair_matches_old(
            selected_pair, old_candidate
        ):
            continue
        if not _selected_pair_matches_old(
            selected_pair, row, allow_reconciled_tasks=True
        ):
            raise ValueError(f"Existing pair-response row is stale or invalid: {pair_id}")

    canonical_rows = []
    legacy_reusable_count = 0
    resumed_recomputed_count = 0
    replaced_existing_with_legacy_count = 0
    for pair in selected:
        pair_id = str(pair["pair_id"])
        candidate = old.get(pair_id)
        if candidate is not None and _selected_pair_matches_old(pair, candidate):
            canonical_rows.append(candidate)
            legacy_reusable_count += 1
            if pair_id in existing and existing[pair_id] != candidate:
                replaced_existing_with_legacy_count += 1
            continue
        completed = existing.get(pair_id)
        if completed is not None:
            canonical_rows.append(completed)
            resumed_recomputed_count += 1
    expected = {str(row["pair_id"]) for row in selected}
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "clean_selected_pairs_seed.jsonl", selected)
    atomic_write_json(output_dir / "clean_pair_selection_seed_summary.json", selection_summary)
    temporary_path = output_path.with_name(f".{output_path.name}.seed.tmp")
    write_jsonl(temporary_path, canonical_rows)
    os.replace(temporary_path, output_path)
    return {
        "format": EXECUTION_VERSION,
        "cache": "pair_response_5d",
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
        "selected_pair_count": len(selected),
        "expected_key_count": len(expected),
        "existing_row_count_before_seed": len(existing),
        "existing_row_count_after_seed": len(canonical_rows),
        "resumed_recomputed_row_count": resumed_recomputed_count,
        "legacy_reusable_row_count": legacy_reusable_count,
        "replaced_existing_with_legacy_count": replaced_existing_with_legacy_count,
        "cascade_recompute_count": len(selected) - legacy_reusable_count,
        "remaining_recompute_count": len(selected) - len(canonical_rows),
        "historical_static_affected_count": 121,
    }


def validate_transition_preflight(*, old_dir: Path, clean_dir: Path) -> dict[str, Any]:
    old_queries = json.loads((old_dir / "query_manifest.json").read_text(encoding="utf-8"))
    clean_queries = json.loads((clean_dir / "query_manifest.json").read_text(encoding="utf-8"))
    old_ids = [str(row["state_example_id"]) for row in old_queries["query_rows"]]
    clean_ids = [str(row["state_example_id"]) for row in clean_queries["query_rows"]]
    if old_ids != clean_ids:
        raise ValueError("Clean transition preflight changed the immutable query-state selection")
    old_panel = _rows(old_dir / "transition_panel.jsonl")
    clean_panel = _rows(clean_dir / "transition_panel.jsonl")
    old_semantic = [(str(row["parent_task_id"]), int(row["step_index"])) for row in old_panel]
    clean_semantic = [(str(row["parent_task_id"]), int(row["step_index"])) for row in clean_panel]
    if set(old_semantic) != set(clean_semantic):
        raise ValueError("Clean transition preflight changed the immutable semantic panel")
    old_summary = json.loads((old_dir / "preflight_summary.json").read_text(encoding="utf-8"))
    clean_summary = json.loads((clean_dir / "preflight_summary.json").read_text(encoding="utf-8"))
    keys = ("panel_transition_count", "query_count", "legal_pair_count")
    if any(old_summary["counts"][key] != clean_summary["counts"][key] for key in keys):
        raise ValueError("Clean transition preflight changed panel/query/legal counts")
    return {
        "format": "identity_reconciled_transition_preflight_validation_7b_v1",
        "query_state_count": len(clean_ids),
        "panel_transition_count": len(clean_panel),
        "semantic_query_selection_identical": True,
        "semantic_panel_selection_identical": True,
        "old_counts": old_summary["counts"],
        "clean_counts": clean_summary["counts"],
        "passed": True,
    }


def seed_transition_teacher(
    *, old_dir: Path, clean_preflight_dir: Path, output_dir: Path
) -> dict[str, Any]:
    old_rows = _rows(old_dir / "teacher_cache.jsonl")
    old = _keyed(old_rows, lambda row: str(row["pair_id"]))
    preflight = _rows(clean_preflight_dir / "pair_preflight.jsonl")
    expected = {str(row["pair_id"]) for row in preflight}
    reusable = []
    for clean in preflight:
        candidate = old.get(str(clean["pair_id"]))
        if candidate is None:
            continue
        if str(clean["task_id"]) in AFFECTED_TASK_IDS:
            continue
        if str(clean["parent_task_id"]) in AFFECTED_TASK_IDS:
            continue
        fields = (
            "state_example_id",
            "example_index",
            "task_id",
            "episode_id",
            "step_id",
            "split",
            "transition_id",
            "parent_memory_id",
            "parent_task_id",
            "parent_episode_id",
            "state_prompt_tokens",
            "combined_prompt_tokens",
            "target_tokens",
            "total_tokens_with_target",
            "base_prompt_sha256",
            "teacher_prompt_sha256",
            "target_sha256",
            "target_token_sha256",
            "transition_content_sha256",
            "teacher_section_sha256",
        )
        if all(clean.get(field) == candidate.get(field) for field in fields):
            reusable.append(candidate)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = seed_jsonl(
        output_path=output_dir / "teacher_cache_journal.jsonl",
        reusable_rows=reusable,
        expected_keys=expected,
        key_fn=lambda row: str(row["pair_id"]),
    )
    old_l0 = json.loads((old_dir / "l0_cache.json").read_text(encoding="utf-8"))
    clean_state_tasks = {str(row["state_example_id"]): str(row["task_id"]) for row in preflight}
    reusable_l0 = {
        key: value
        for key, value in old_l0.items()
        if key in clean_state_tasks and clean_state_tasks[key] not in AFFECTED_TASK_IDS
    }
    l0_path = output_dir / "l0_cache.json"
    if l0_path.exists():
        existing = json.loads(l0_path.read_text(encoding="utf-8"))
        for key, value in reusable_l0.items():
            if key in existing and existing[key] != value:
                raise ValueError(f"Existing transition L0 differs for {key}")
            existing.setdefault(key, value)
        reusable_l0 = existing
    atomic_write_json(l0_path, reusable_l0)
    result.update(
        {
            "format": EXECUTION_VERSION,
            "cache": "transition_teacher",
            "historical_row_count": len(old_rows),
            "clean_expected_row_count": len(preflight),
            "recompute_count": len(preflight) - len(reusable),
            "seeded_l0_count": len(reusable_l0),
        }
    )
    return result


def _atomic_torch_save(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    torch.save(dict(payload), temporary)
    os.replace(temporary, path)


def _load_or_compute_vector(
    *,
    row_path: Path,
    metadata: Mapping[str, Any],
    compute: Callable[[], torch.Tensor],
) -> tuple[torch.Tensor, bool]:
    if row_path.exists():
        payload = torch.load(row_path, map_location="cpu", weights_only=False)
        if all(payload.get(key) == value for key, value in metadata.items()):
            return payload["representation"].to(torch.float32), True
        raise ValueError(f"Incompatible existing representation row: {row_path}")
    vector = compute().detach().to(torch.float32).cpu()
    payload = {
        **dict(metadata),
        "representation": vector,
        "representation_sha256": tensor_state_sha256({"representation": vector}),
    }
    _atomic_torch_save(payload, row_path)
    return vector, False


def rebuild_representations(
    *,
    backend: Any,
    data_dir: Path,
    old_data_dir: Path,
    clean_transition_preflight_dir: Path,
    old_transition_dir: Path,
    old_state_path: Path,
    old_memory_path: Path,
    old_transition_path: Path,
    output_dir: Path,
    corpus_lineage_sha256: str,
    transition_mapping: Sequence[Mapping[str, Any]],
    attempt: Any,
) -> dict[str, Any]:
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    old_examples = load_decision_examples(old_data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    old_records = load_memory_records(old_data_dir / "memory_records.jsonl")
    state_payload = torch.load(old_state_path, map_location="cpu", weights_only=False)
    memory_payload = torch.load(old_memory_path, map_location="cpu", weights_only=False)
    transition_payload = torch.load(old_transition_path, map_location="cpu", weights_only=False)
    state_tensor = state_payload["representations"].to(torch.float32).clone()
    memory_tensor = memory_payload["representations"].to(torch.float32).clone()
    changed_state_indices = [
        index
        for index, (old, clean) in enumerate(zip(old_examples, examples, strict=True))
        if canonical_json_sha256(old.to_dict()) != canonical_json_sha256(clean.to_dict())
    ]
    changed_memory_indices = [
        index
        for index, (old, clean) in enumerate(zip(old_records, records, strict=True))
        if canonical_json_sha256(old.to_dict()) != canonical_json_sha256(clean.to_dict())
    ]
    if len(changed_state_indices) != 35 or len(changed_memory_indices) != 2:
        raise ValueError("Representation invalidation count differs from 35 states / 2 memories")
    state_rows = output_dir / "state_rows"
    memory_rows = output_dir / "memory_rows"
    transition_rows_dir = output_dir / "transition_rows"
    reused_checkpoints = 0
    computed = 0
    for position, index in enumerate(changed_state_indices, start=1):
        example = examples[index]
        metadata = {
            "format": "identity_reconciled_state_representation_row_7b_v1",
            "state_index": index,
            "state_example_id": state_example_id(index, example),
            "state_text_sha256": sha256_text(example.state_text),
            "model_name": str(backend.model_name),
            "corpus_lineage_sha256": corpus_lineage_sha256,
        }
        vector, reused = _load_or_compute_vector(
            row_path=state_rows / f"{index:04d}.pt",
            metadata=metadata,
            compute=lambda text=example.state_text: backend.encode_texts([text], batch_size=1)[0],
        )
        state_tensor[index] = vector
        reused_checkpoints += int(reused)
        computed += int(not reused)
        attempt.progress(
            status="state_representations",
            completed=position,
            total=len(changed_state_indices),
            latest_validated_checkpoint=str(state_rows / f"{index:04d}.pt"),
        )
    for position, index in enumerate(changed_memory_indices, start=1):
        record = records[index]
        metadata = {
            "format": "identity_reconciled_memory_representation_row_7b_v1",
            "memory_index": index,
            "memory_id": record.memory_id,
            "memory_text_sha256": sha256_text(record.experience_text),
            "model_name": str(backend.model_name),
            "corpus_lineage_sha256": corpus_lineage_sha256,
        }

        def compute_memory(text: str = record.experience_text) -> torch.Tensor:
            chunks, owners, counts = backend.encode_text_chunks_with_metadata(
                [text], batch_size=1, add_special_tokens=True
            )
            if set(owners.tolist()) != {0}:
                raise ValueError("Single memory representation has unexpected owner indices")
            weights = counts.to(torch.float32).unsqueeze(-1)
            return (chunks.to(torch.float32) * weights).sum(0) / weights.sum().clamp_min(1.0)

        vector, reused = _load_or_compute_vector(
            row_path=memory_rows / f"{index:04d}.pt", metadata=metadata, compute=compute_memory
        )
        memory_tensor[index] = vector
        reused_checkpoints += int(reused)
        computed += int(not reused)
        attempt.progress(
            status="memory_representations",
            completed=position,
            total=len(changed_memory_indices),
            latest_validated_checkpoint=str(memory_rows / f"{index:04d}.pt"),
        )
    old_transition_ids = [str(value) for value in transition_payload["ordered_transition_ids"]]
    old_transition_vectors = {
        transition_id: transition_payload["representations"][index].to(torch.float32)
        for index, transition_id in enumerate(old_transition_ids)
    }
    old_to_clean = {
        str(row["old_transition_id"]): str(row["clean_transition_id"]) for row in transition_mapping
    }
    clean_to_old = {clean: old for old, clean in old_to_clean.items()}
    clean_transition_rows = _rows(data_dir / "transition_manifest.jsonl")
    transition_work_queue = transition_representation_work_queue(
        transition_mapping=transition_mapping,
        clean_transitions=clean_transition_rows,
    )
    if len(transition_work_queue) != 17:
        raise ValueError(
            f"Changed transition representation work queue is {len(transition_work_queue)}, expected 17"
        )
    recomputed_transition_vectors: dict[str, torch.Tensor] = {}
    recomputed_transition_metadata: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(transition_work_queue, start=1):
        transition_id = str(row["transition_id"])
        text = transition_teacher_section(dict(row))
        metadata = {
            "format": "identity_reconciled_transition_representation_row_7b_v1",
            "transition_id": transition_id,
            "source_old_transition_id": str(row["source_old_transition_id"]),
            "transition_content_sha256": str(row["transition_content_sha256"]),
            "teacher_section_sha256": sha256_text(text),
            "model_name": str(backend.model_name),
            "corpus_lineage_sha256": corpus_lineage_sha256,
            "recomputed": True,
        }

        def compute_transition(value: str = text) -> torch.Tensor:
            chunks, owners, counts = backend.encode_text_chunks_with_metadata(
                [value], batch_size=1, add_special_tokens=True
            )
            if set(owners.tolist()) != {0}:
                raise ValueError("Single transition representation has unexpected owners")
            weights = counts.to(torch.float32).unsqueeze(-1)
            return (chunks.to(torch.float32) * weights).sum(0) / weights.sum().clamp_min(1.0)

        vector, reused = _load_or_compute_vector(
            row_path=transition_rows_dir / f"{transition_id}.pt",
            metadata=metadata,
            compute=compute_transition,
        )
        recomputed_transition_vectors[transition_id] = vector
        recomputed_transition_metadata[transition_id] = metadata
        reused_checkpoints += int(reused)
        computed += int(not reused)
        attempt.progress(
            status="transition_representations",
            completed=position,
            total=len(transition_work_queue),
            latest_validated_checkpoint=str(transition_rows_dir / f"{transition_id}.pt"),
        )
    panel = _rows(clean_transition_preflight_dir / "transition_panel.jsonl")
    panel_by_id = {str(row["transition_id"]): row for row in panel}
    ordered_transition_ids = sorted(panel_by_id)
    transition_vectors = []
    transition_metadata = []
    changed_panel_transition_count = 0
    for position, transition_id in enumerate(ordered_transition_ids, start=1):
        row = panel_by_id[transition_id]
        source_old_id = clean_to_old.get(transition_id, transition_id)
        if source_old_id == transition_id:
            vector = old_transition_vectors[source_old_id].clone()
            metadata = {
                "format": "reused_frozen_qwen_transition_representation_row_7b_v1",
                "transition_id": transition_id,
                "source_old_transition_id": source_old_id,
                "transition_content_sha256": str(row["transition_content_sha256"]),
                "teacher_section_sha256": str(row["teacher_section_sha256"]),
                "model_name": str(backend.model_name),
                "corpus_lineage_sha256": corpus_lineage_sha256,
                "recomputed": False,
                "representation_sha256": tensor_state_sha256({"representation": vector}),
            }
        else:
            changed_panel_transition_count += 1
            vector = recomputed_transition_vectors[transition_id]
            metadata = recomputed_transition_metadata[transition_id]
        transition_vectors.append(vector)
        transition_metadata.append(metadata)
        attempt.progress(
            status="transition_representations",
            completed=position,
            total=len(ordered_transition_ids),
            latest_validated_checkpoint=(
                str(transition_rows_dir / f"{transition_id}.pt")
                if source_old_id != transition_id
                else str(old_transition_path)
            ),
        )
    transition_tensor = torch.stack(transition_vectors)
    state_out = {
        **{key: value for key, value in state_payload.items() if key != "representations"},
        "format": "identity_reconciled_pooled_qwen_hidden_7b_v1",
        "representations": state_tensor,
        "source_path": str(data_dir / "decision_examples.jsonl"),
        "source_sha256": sha256_file(data_dir / "decision_examples.jsonl"),
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "recomputed_indices": changed_state_indices,
        "representation_tensor_sha256": tensor_state_sha256({"representations": state_tensor}),
    }
    memory_out = {
        **{key: value for key, value in memory_payload.items() if key != "representations"},
        "format": "identity_reconciled_chunked_qwen_hidden_7b_v1",
        "representations": memory_tensor,
        "source_path": str(data_dir / "memory_records.jsonl"),
        "source_sha256": sha256_file(data_dir / "memory_records.jsonl"),
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "recomputed_indices": changed_memory_indices,
        "representation_tensor_sha256": tensor_state_sha256({"representations": memory_tensor}),
    }
    transition_out = {
        "format": "identity_reconciled_frozen_qwen_transition_representation_cache_7b_v1",
        "model_name": str(backend.model_name),
        "renderer_version": "decision_transition_teacher_section_v1",
        "aggregation": "token_weighted_mean_over_complete_chunks",
        "ordered_transition_ids": ordered_transition_ids,
        "representations": transition_tensor,
        "rows": transition_metadata,
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "source_clean_transition_panel_sha256": sha256_file(
            clean_transition_preflight_dir / "transition_panel.jsonl"
        ),
        "source_old_cache_sha256": sha256_file(old_transition_path),
        "representation_tensor_sha256": tensor_state_sha256({"representations": transition_tensor}),
        "recomputed_source_row_count": len(transition_work_queue),
        "recomputed_panel_row_count": changed_panel_transition_count,
        "reused_panel_row_count": len(ordered_transition_ids) - changed_panel_transition_count,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_torch_save(state_out, output_dir / "decision_state_representations.pt")
    _atomic_torch_save(memory_out, output_dir / "memory_record_representations.pt")
    _atomic_torch_save(transition_out, output_dir / "transition_representations.pt")
    unchanged_state = sorted(set(range(len(examples))) - set(changed_state_indices))
    unchanged_memory = sorted(set(range(len(records))) - set(changed_memory_indices))
    if not torch.equal(
        state_tensor[unchanged_state], state_payload["representations"][unchanged_state]
    ):
        raise ValueError("An unaffected state representation changed")
    if not torch.equal(
        memory_tensor[unchanged_memory], memory_payload["representations"][unchanged_memory]
    ):
        raise ValueError("An unaffected memory representation changed")
    report = {
        "format": "identity_reconciled_representation_rebuild_7b_v1",
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "state": {
            "count": len(examples),
            "recomputed": len(changed_state_indices),
            "reused": len(unchanged_state),
            "shape": list(state_tensor.shape),
            "sha256": sha256_file(output_dir / "decision_state_representations.pt"),
        },
        "memory": {
            "count": len(records),
            "recomputed": len(changed_memory_indices),
            "reused": len(unchanged_memory),
            "shape": list(memory_tensor.shape),
            "sha256": sha256_file(output_dir / "memory_record_representations.pt"),
        },
        "transition": {
            "count": len(ordered_transition_ids),
            "source_rows_recomputed": len(transition_work_queue),
            "panel_rows_recomputed": changed_panel_transition_count,
            "panel_rows_reused": len(ordered_transition_ids) - changed_panel_transition_count,
            "shape": list(transition_tensor.shape),
            "sha256": sha256_file(output_dir / "transition_representations.pt"),
        },
        "row_checkpoints_reused": reused_checkpoints,
        "row_checkpoints_newly_computed": computed,
        "unaffected_tensors_bit_identical": True,
        "passed": True,
    }
    atomic_write_json(output_dir / "representation_rebuild_report.json", report)
    return report


def _validate_merged_rows(
    *,
    name: str,
    clean_path: Path,
    old_path: Path,
    key_field: str,
    expected_count: int,
    corpus_lineage_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    clean_rows = _rows(clean_path)
    old_rows = _keyed(_rows(old_path), lambda row: str(row[key_field]))
    clean = _keyed(clean_rows, lambda row: str(row[key_field]))
    if len(clean) != expected_count:
        raise ValueError(f"{name} row count differs: {len(clean)} != {expected_count}")
    recomputed = []
    reused = []
    for key, row in clean.items():
        lineage = row.get("corpus_lineage_sha256")
        if lineage is not None:
            if str(lineage) != corpus_lineage_sha256:
                raise ValueError(f"{name} row has wrong corpus lineage: {key}")
            recomputed.append(row)
        else:
            prior = old_rows.get(key)
            if prior is None or not _canonical_equal(prior, row):
                raise ValueError(f"{name} reusable row is not byte/canonical identical: {key}")
            reused.append(row)
        if row.get("truncated") is not False:
            raise ValueError(f"{name} row was truncated: {key}")
        if row.get("leakage_overlap"):
            raise ValueError(f"{name} row has leakage overlap: {key}")
    report = {
        "cache": name,
        "path": str(clean_path),
        "row_count": len(clean),
        "unique_key_count": len(clean),
        "recomputed_row_count": len(recomputed),
        "reused_row_count": len(reused),
        "all_recomputed_rows_lineage_stamped": True,
        "all_reused_rows_canonical_identical": True,
        "no_truncation": True,
        "no_leakage_overlap": True,
        "sha256": sha256_file(clean_path),
    }
    return report, recomputed


def _utility_identity_samples(
    rows: Sequence[Mapping[str, Any]], *, key_field: str
) -> dict[str, Any]:
    categories: dict[str, Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda value: str(value[key_field])):
        category = row.get("utility_category")
        if category in {"positive", "neutral", "negative"}:
            categories.setdefault(str(category), row)
    checks = {}
    for category in ("positive", "neutral", "negative"):
        row = categories.get(category)
        if row is None:
            checks[category] = {"available": False, "passed": None}
            continue
        utility = float(row["text_utility"])
        lj_key = "Lj_text" if row.get("Lj_text") is not None else "Lj_transition"
        reproduced = float(row["L0"]) - float(row[lj_key])
        passed = abs(utility - reproduced) <= 1e-8
        if not passed:
            raise ValueError(f"Utility identity failed for {category} sample")
        checks[category] = {
            "available": True,
            "key": str(row[key_field]),
            "utility": utility,
            "reproduced": reproduced,
            "passed": True,
        }
    return {
        "selection": "lexicographically_first_recomputed_row_per_utility_category",
        "checks": checks,
        "all_available_samples_passed": all(
            value["passed"] is not False for value in checks.values()
        ),
    }


def validate_clean_cache_rebuild(
    *,
    output_root: Path,
    old_paths: Mapping[str, Path],
    affected_manifest: Mapping[str, Any],
    corpus_lineage_sha256: str,
    expected_counts: Mapping[str, int],
) -> dict[str, Any]:
    specifications = (
        (
            "raw_text_teacher",
            output_root / "raw_text_teacher/teacher_cache_full_rows.jsonl",
            old_paths["raw_text_teacher"],
            "pair_key",
        ),
        (
            "stage_c1_response",
            output_root / "stage_c1_response/response_cache.jsonl",
            old_paths["stage_c1_response"],
            "state_example_id",
        ),
        (
            "pair_response_5d",
            output_root / "pair_response_5d/pair_response_cache.jsonl",
            old_paths["pair_response_5d"],
            "pair_id",
        ),
        (
            "transition_teacher",
            output_root / "transition_teacher/teacher_cache.jsonl",
            old_paths["transition_teacher"],
            "pair_id",
        ),
    )
    cache_reports = {}
    recomputed_by_name = {}
    for name, clean_path, old_path, key in specifications:
        report, recomputed = _validate_merged_rows(
            name=name,
            clean_path=clean_path,
            old_path=old_path,
            key_field=key,
            expected_count=int(expected_counts[name]),
            corpus_lineage_sha256=corpus_lineage_sha256,
        )
        cache_reports[name] = report
        recomputed_by_name[name] = recomputed

    raw_expected = int(affected_manifest["caches"]["raw_text_teacher"]["affected_row_count"])
    transition_expected = int(
        affected_manifest["caches"]["transition_teacher"]["affected_row_count"]
    )
    if cache_reports["raw_text_teacher"]["recomputed_row_count"] != raw_expected:
        raise ValueError("Raw-teacher recomputation count differs from exact preflight")
    if cache_reports["transition_teacher"]["recomputed_row_count"] != transition_expected:
        raise ValueError("Transition-teacher recomputation count differs from exact preflight")

    representation_report = json.loads(
        (output_root / "representations/representation_rebuild_report.json").read_text(
            encoding="utf-8"
        )
    )
    if not bool(representation_report.get("passed")):
        raise ValueError("Representation rebuild report did not pass")
    if (
        int(representation_report["state"]["recomputed"]) != 35
        or int(representation_report["memory"]["recomputed"]) != 2
        or int(representation_report["transition"]["source_rows_recomputed"]) != 17
    ):
        raise ValueError("Representation recomputation counts differ from 35/2/17")

    transition_mapping = list(affected_manifest["transition_changes"]["mapping"])
    superseded = {
        str(row["old_transition_id"])
        for row in transition_mapping
        if str(row["old_transition_id"]) != str(row["clean_transition_id"])
    }
    clean_transition_rows = _rows(output_root / "transition_preflight/transition_manifest.jsonl")
    clean_transition_ids = {str(row["transition_id"]) for row in clean_transition_rows}
    remaining_superseded = sorted(clean_transition_ids.intersection(superseded))
    if remaining_superseded:
        raise ValueError(f"Superseded transition IDs remain: {remaining_superseded[:5]}")
    transition_cache_ids = {
        str(row["transition_id"])
        for row in _rows(output_root / "transition_teacher/teacher_cache.jsonl")
    }
    if transition_cache_ids.intersection(superseded):
        raise ValueError("Superseded transition IDs remain in clean teacher cache")

    summary_paths = {
        "raw_text_teacher": output_root / "raw_text_teacher/summary.json",
        "stage_c1_response": output_root / "stage_c1_response/summary.json",
        "pair_response_5d": output_root / "pair_response_5d/pair_response_cache_summary.json",
        "transition_teacher": output_root / "transition_teacher/teacher_summary.json",
    }
    summaries = {
        name: json.loads(path.read_text(encoding="utf-8")) for name, path in summary_paths.items()
    }
    for name, summary in summaries.items():
        if str(summary.get("corpus_lineage_sha256")) != corpus_lineage_sha256:
            raise ValueError(f"{name} summary has wrong corpus lineage")
        validation = summary.get("validation")
        if isinstance(validation, Mapping) and not bool(validation.get("passed")):
            raise ValueError(f"{name} internal validation failed")

    raw_samples = _utility_identity_samples(
        recomputed_by_name["raw_text_teacher"], key_field="pair_key"
    )
    transition_samples = _utility_identity_samples(
        recomputed_by_name["transition_teacher"], key_field="pair_id"
    )
    report = {
        "format": "identity_reconciled_clean_cache_validation_7b_v1",
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "caches": cache_reports,
        "representations": representation_report,
        "raw_teacher_preflight_recompute_count": raw_expected,
        "transition_teacher_preflight_recompute_count": transition_expected,
        "stage_c1_cascade_recompute_count": cache_reports["stage_c1_response"][
            "recomputed_row_count"
        ],
        "pair_5d_cascade_recompute_count": cache_reports["pair_response_5d"][
            "recomputed_row_count"
        ],
        "utility_identity_samples": {
            "raw_text_teacher": raw_samples,
            "transition_teacher": transition_samples,
        },
        "superseded_transition_id_count": len(superseded),
        "superseded_transition_ids_absent": True,
        "unaffected_rows_canonical_identical": True,
        "all_recomputed_rows_lineage_stamped": True,
        "duplicate_key_count": 0,
        "truncated_row_count": 0,
        "leakage_overlap_row_count": 0,
        "internal_cache_validations_passed": True,
        "passed": True,
    }
    atomic_write_json(output_root / "postrun_validation.json", report)
    return report
