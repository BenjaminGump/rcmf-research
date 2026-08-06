from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rcmf.schemas import DecisionExample, MemoryRecord
from rcmf.utils.serialization import read_jsonl, sha256_file, write_jsonl


STUDENT_LABEL_DATASET_VERSION = "stage_b_addressing_student_labels_v1"
FULL_TEACHER_CACHE_VERSION = "raw_text_memory_teacher_full_cache_v1"
FULL_TEACHER_SCORING_DEFINITION = "frozen_qwen_full_demo_raw_memory_mean_target_nll_v1"
DEFAULT_NEUTRAL_EPS = 0.01
DEFAULT_STRONG_POSITIVE = 0.05
DEFAULT_STRONG_NEGATIVE = -0.05
SPECIAL_MEMORY_ID = "076f5673-6565-5f20-aada-6f16a0f8d4b0"


def example_task_id(example: DecisionExample) -> str:
    task_id = example.metadata.get("task_id")
    if task_id:
        return str(task_id)
    return example.episode_id.rsplit(":", 1)[-1]


def example_id(index: int, example: DecisionExample) -> str:
    return f"{example.episode_id}:step:{example.step_id}:line:{index + 1}"


def pair_key(example_index: int, memory_index: int) -> str:
    return f"e{example_index}:m{memory_index}"


@dataclass(frozen=True)
class StudentLabelThresholds:
    neutral_eps: float = DEFAULT_NEUTRAL_EPS
    strong_positive: float = DEFAULT_STRONG_POSITIVE
    strong_negative: float = DEFAULT_STRONG_NEGATIVE

    def validate(self) -> None:
        if self.neutral_eps < 0:
            raise ValueError("neutral_eps must be non-negative")
        if self.strong_positive <= self.neutral_eps:
            raise ValueError("strong_positive must be greater than neutral_eps")
        if self.strong_negative >= -self.neutral_eps:
            raise ValueError("strong_negative must be less than -neutral_eps")


@dataclass
class CompiledStudentLabels:
    rows: list[dict[str, Any]]
    memory_bank: list[dict[str, Any]]
    summary: dict[str, Any]
    validation: dict[str, Any]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return bool(value)


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _load_split_manifest(path: Path) -> dict[str, Any]:
    manifest = dict(__import__("json").loads(path.read_text(encoding="utf-8")))
    required = {"seed", "train_task_ids", "validation_task_ids"}
    missing = required.difference(manifest)
    if missing:
        raise ValueError(f"Split manifest missing keys: {sorted(missing)}")
    return manifest


def _load_teacher_rows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    row_by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    counts = Counter()
    for row in read_jsonl(path):
        if row.get("format") != FULL_TEACHER_CACHE_VERSION:
            raise ValueError(f"Unexpected teacher cache row format: {row.get('format')}")
        key = str(row.get("pair_key") or pair_key(int(row["example_index"]), int(row["candidate_memory_index"])))
        if key in row_by_key:
            duplicates.append(key)
        row_by_key[key] = row
        counts[str(row.get("score_status"))] += 1
    if duplicates:
        raise ValueError(f"Teacher cache contains duplicate pair_key values: {duplicates[:10]}")
    return row_by_key, {"row_count": len(row_by_key), "score_status_counts": dict(counts)}


def _memory_bank_rows(
    records: list[MemoryRecord],
    train_task_ids: set[str],
    validation_task_ids: set[str],
    valid_teacher_label_counts: Counter[str],
    special_memory_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bank: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        in_train = record.task_id in train_task_ids
        in_validation = record.task_id in validation_task_ids
        reason = None
        eligible = in_train and not in_validation
        if in_validation:
            reason = "validation_task_memory"
            eligible = False
        elif not in_train:
            reason = "not_in_train_task_split"
            eligible = False
        if record.memory_id == special_memory_id and in_train:
            if int(valid_teacher_label_counts.get(record.memory_id, 0)) == 0:
                reason = "special_memory_zero_valid_stage_b_train_labels"
                eligible = False
        row = {
            "memory_index": index,
            "memory_id": record.memory_id,
            "task_id": record.task_id,
            "episode_id": record.episode_id,
            "eligible_for_stage_b": bool(eligible),
            "valid_stage_b_train_label_count": int(valid_teacher_label_counts.get(record.memory_id, 0)),
            "exclusion_reason": reason,
        }
        if eligible:
            bank.append(row)
        else:
            excluded.append(row)
    return bank, excluded


def _utility_category_masks(
    utilities: list[float | None],
    valid_mask: list[bool],
    thresholds: StudentLabelThresholds,
) -> dict[str, list[bool] | list[float] | bool]:
    positive = []
    neutral = []
    negative = []
    strong_positive = []
    strong_negative = []
    positive_gain = []
    for utility, valid in zip(utilities, valid_mask):
        if not valid or utility is None:
            positive.append(False)
            neutral.append(False)
            negative.append(False)
            strong_positive.append(False)
            strong_negative.append(False)
            positive_gain.append(0.0)
            continue
        positive.append(utility > thresholds.neutral_eps)
        neutral.append(abs(utility) <= thresholds.neutral_eps)
        negative.append(utility < -thresholds.neutral_eps)
        strong_positive.append(utility >= thresholds.strong_positive)
        strong_negative.append(utility <= thresholds.strong_negative)
        positive_gain.append(max(float(utility) - thresholds.neutral_eps, 0.0))
    has_valid = any(valid_mask)
    has_positive_gain = any(value > 0 for value in positive_gain)
    return {
        "positive_mask": positive,
        "neutral_mask": neutral,
        "negative_mask": negative,
        "strong_positive_mask": strong_positive,
        "strong_negative_mask": strong_negative,
        "positive_gain": positive_gain,
        "all_missing_state": not has_valid,
        "no_positive_state": has_valid and not has_positive_gain,
    }


def _split_for_example(
    state_id: str,
    task_id: str,
    train_state_ids: set[str],
    validation_state_ids: set[str],
    train_task_ids: set[str],
    validation_task_ids: set[str],
) -> str:
    if state_id in train_state_ids:
        return "train"
    if state_id in validation_state_ids:
        return "validation"
    if task_id in train_task_ids:
        return "train"
    if task_id in validation_task_ids:
        return "validation"
    raise ValueError(f"Cannot assign split for state={state_id} task={task_id}")


def _threshold_coverage(rows: list[dict[str, Any]], thresholds: Iterable[float]) -> dict[str, Any]:
    by_split: dict[str, dict[str, Any]] = {}
    for split in sorted({str(row["split"]) for row in rows}):
        split_rows = [row for row in rows if row["split"] == split]
        by_split[split] = {"states": len(split_rows)}
        for threshold in thresholds:
            key = f"{threshold:.2f}"
            state_count = 0
            row_count = 0
            for row in split_rows:
                count = sum(
                    1
                    for utility, valid in zip(row["raw_utility"], row["valid_mask"])
                    if valid and utility is not None and float(utility) >= threshold
                )
                row_count += count
                if count > 0:
                    state_count += 1
            by_split[split][key] = {
                "states_with_at_least_one": state_count,
                "valid_rows_at_or_above": row_count,
            }
    return by_split


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for split in sorted({str(row["split"]) for row in rows}):
        split_rows = [row for row in rows if row["split"] == split]
        counts = Counter()
        valid_rows = 0
        all_missing = 0
        no_positive = 0
        for row in split_rows:
            valid_rows += sum(bool(v) for v in row["valid_mask"])
            all_missing += int(bool(row["all_missing_state"]))
            no_positive += int(bool(row["no_positive_state"]))
            counts["positive"] += sum(bool(v) for v in row["positive_mask"])
            counts["neutral"] += sum(bool(v) for v in row["neutral_mask"])
            counts["negative"] += sum(bool(v) for v in row["negative_mask"])
            counts["strong_positive"] += sum(bool(v) for v in row["strong_positive_mask"])
            counts["strong_negative"] += sum(bool(v) for v in row["strong_negative_mask"])
        output[split] = {
            "states": len(split_rows),
            "valid_rows": valid_rows,
            "all_missing_states": all_missing,
            "no_positive_states": no_positive,
            **dict(counts),
        }
    return output


def compile_stage_b_student_labels(
    *,
    examples: list[DecisionExample],
    records: list[MemoryRecord],
    teacher_cache_jsonl: Path,
    teacher_summary_json: Path,
    split_manifest_json: Path,
    data_dir: Path,
    thresholds: StudentLabelThresholds | None = None,
    special_memory_id: str = SPECIAL_MEMORY_ID,
) -> CompiledStudentLabels:
    thresholds = thresholds or StudentLabelThresholds()
    thresholds.validate()
    import json

    teacher_summary = json.loads(teacher_summary_json.read_text(encoding="utf-8"))
    if teacher_summary.get("cache_version") != FULL_TEACHER_CACHE_VERSION:
        raise ValueError(f"Unexpected teacher cache version: {teacher_summary.get('cache_version')}")
    if teacher_summary.get("scoring_definition") != FULL_TEACHER_SCORING_DEFINITION:
        raise ValueError(f"Unexpected teacher scoring definition: {teacher_summary.get('scoring_definition')}")
    manifest = _load_split_manifest(split_manifest_json)
    train_task_ids = {str(item) for item in manifest["train_task_ids"]}
    validation_task_ids = {str(item) for item in manifest["validation_task_ids"]}
    train_state_ids = {str(item) for item in manifest.get("train_example_ids", [])}
    validation_state_ids = {str(item) for item in manifest.get("validation_example_ids", [])}
    if train_task_ids.intersection(validation_task_ids):
        raise ValueError("Train and validation task splits overlap")

    teacher_rows, teacher_load_summary = _load_teacher_rows(teacher_cache_jsonl)
    teacher_cache_identity = {
        "cache_version": FULL_TEACHER_CACHE_VERSION,
        "scoring_definition": FULL_TEACHER_SCORING_DEFINITION,
        "source_commit": teacher_summary.get("source_commit"),
        "summary_sha256": sha256_file(teacher_summary_json),
        "cache_jsonl_sha256": sha256_file(teacher_cache_jsonl),
        "split_manifest_sha256": sha256_file(split_manifest_json),
        "decision_examples_sha256": sha256_file(data_dir / "decision_examples.jsonl"),
        "memory_records_sha256": sha256_file(data_dir / "memory_records.jsonl"),
    }

    # Count valid labels for train states over train-task memories before special-memory masking.
    valid_stage_b_train_label_counts: Counter[str] = Counter()
    for row in teacher_rows.values():
        if not _as_bool(row.get("valid_for_loss")):
            continue
        example_index = int(row["example_index"])
        memory_index = int(row["candidate_memory_index"])
        example = examples[example_index]
        record = records[memory_index]
        if example_task_id(example) in train_task_ids and record.task_id in train_task_ids:
            if record.task_id != example_task_id(example):
                valid_stage_b_train_label_counts[record.memory_id] += 1

    memory_bank, excluded_memories = _memory_bank_rows(
        records,
        train_task_ids=train_task_ids,
        validation_task_ids=validation_task_ids,
        valid_teacher_label_counts=valid_stage_b_train_label_counts,
        special_memory_id=special_memory_id,
    )
    ordered_memory_indices = [int(row["memory_index"]) for row in memory_bank]
    ordered_memory_ids = [str(row["memory_id"]) for row in memory_bank]
    memory_id_to_stage_index = {memory_id: index for index, memory_id in enumerate(ordered_memory_ids)}

    rows: list[dict[str, Any]] = []
    missing_teacher_pair_count = 0
    masked_own_task_pair_count = 0
    masked_over_context_pair_count = 0
    for example_index, example in enumerate(examples):
        task_id = example_task_id(example)
        state_id = example_id(example_index, example)
        split = _split_for_example(
            state_id,
            task_id,
            train_state_ids,
            validation_state_ids,
            train_task_ids,
            validation_task_ids,
        )
        utilities: list[float | None] = []
        valid_mask: list[bool] = []
        legal_effective_mask: list[bool] = []
        score_statuses: list[str | None] = []
        source_row_pair_keys: list[str | None] = []
        source_target_hashes: list[str | None] = []
        source_memory_hashes: list[str | None] = []
        l0_values: list[float] = []
        for memory_index in ordered_memory_indices:
            record = records[memory_index]
            legal = True
            if split == "train" and record.task_id == task_id:
                legal = False
                masked_own_task_pair_count += 1
            key = pair_key(example_index, memory_index)
            teacher_row = teacher_rows.get(key)
            if teacher_row is None:
                if legal:
                    missing_teacher_pair_count += 1
                utilities.append(None)
                valid_mask.append(False)
                legal_effective_mask.append(False)
                score_statuses.append(None)
                source_row_pair_keys.append(None)
                source_target_hashes.append(None)
                source_memory_hashes.append(None)
                continue
            status = str(teacher_row.get("score_status"))
            valid = bool(legal and _as_bool(teacher_row.get("valid_for_loss")))
            utility = _finite_float(teacher_row.get("text_utility"))
            if status == "over_context":
                masked_over_context_pair_count += 1
            if valid and utility is None:
                raise ValueError(f"Valid teacher row has non-finite utility: {key}")
            if valid:
                l0_value = _finite_float(teacher_row.get("L0"))
                if l0_value is None:
                    raise ValueError(f"Valid teacher row has non-finite L0: {key}")
                l0_values.append(l0_value)
            utilities.append(utility if valid else None)
            valid_mask.append(valid)
            legal_effective_mask.append(bool(legal))
            score_statuses.append(status)
            source_row_pair_keys.append(key)
            source_target_hashes.append(teacher_row.get("target_sha256"))
            source_memory_hashes.append(teacher_row.get("memory_text_sha256"))
        masks = _utility_category_masks(utilities, valid_mask, thresholds)
        l0 = l0_values[0] if l0_values else None
        if len({round(value, 8) for value in l0_values}) > 1:
            raise ValueError(f"State {state_id} has inconsistent L0 values across memories")
        rows.append(
            {
                "format": STUDENT_LABEL_DATASET_VERSION,
                "state_index": example_index,
                "state_example_id": state_id,
                "task_id": task_id,
                "episode_id": example.episode_id,
                "step_id": example.step_id,
                "split": split,
                "ordered_effective_memory_ids": ordered_memory_ids,
                "ordered_effective_memory_indices": ordered_memory_indices,
                "memory_id_to_stage_index": memory_id_to_stage_index,
                "valid_mask": valid_mask,
                "legal_effective_mask": legal_effective_mask,
                "raw_utility": utilities,
                "L0": l0,
                "score_statuses": score_statuses,
                "source_pair_keys": source_row_pair_keys,
                "target_sha256_by_memory": source_target_hashes,
                "memory_text_sha256_by_memory": source_memory_hashes,
                **masks,
                "thresholds": {
                    "neutral_eps": thresholds.neutral_eps,
                    "strong_positive": thresholds.strong_positive,
                    "strong_negative": thresholds.strong_negative,
                },
                "teacher_cache_identity": teacher_cache_identity,
            }
        )

    validation = validate_stage_b_labels(rows, memory_bank, excluded_memories, train_task_ids, validation_task_ids)
    summary = {
        "format": STUDENT_LABEL_DATASET_VERSION,
        "teacher_cache_version": FULL_TEACHER_CACHE_VERSION,
        "teacher_scoring_definition": FULL_TEACHER_SCORING_DEFINITION,
        "split_seed": manifest.get("seed"),
        "train_task_count": len(train_task_ids),
        "validation_task_count": len(validation_task_ids),
        "train_state_count": sum(1 for row in rows if row["split"] == "train"),
        "validation_state_count": sum(1 for row in rows if row["split"] == "validation"),
        "source_state_count": len(examples),
        "source_memory_count": len(records),
        "stage_b_effective_memory_count": len(memory_bank),
        "stage_b_memory_ids": ordered_memory_ids,
        "excluded_memories": excluded_memories,
        "special_memory": next(
            (row for row in memory_bank + excluded_memories if row["memory_id"] == special_memory_id),
            None,
        ),
        "thresholds": {
            "neutral_eps": thresholds.neutral_eps,
            "strong_positive": thresholds.strong_positive,
            "strong_negative": thresholds.strong_negative,
            "coverage_thresholds": [0.01, 0.05, 0.10],
        },
        "label_counts": _label_counts(rows),
        "threshold_coverage": _threshold_coverage(rows, [0.01, 0.05, 0.10]),
        "teacher_load_summary": teacher_load_summary,
        "missing_teacher_pair_count": missing_teacher_pair_count,
        "masked_own_task_pair_count": masked_own_task_pair_count,
        "masked_over_context_pair_count": masked_over_context_pair_count,
        "validation": validation,
    }
    return CompiledStudentLabels(
        rows=rows,
        memory_bank=memory_bank,
        summary=summary,
        validation=validation,
    )


def validate_stage_b_labels(
    rows: list[dict[str, Any]],
    memory_bank: list[dict[str, Any]],
    excluded_memories: list[dict[str, Any]],
    train_task_ids: set[str],
    validation_task_ids: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    bank_ids = [str(row["memory_id"]) for row in memory_bank]
    if len(bank_ids) != len(set(bank_ids)):
        errors.append("duplicate_effective_memory_ids")
    bank_validation_memories = [
        row["memory_id"] for row in memory_bank if str(row["task_id"]) in validation_task_ids
    ]
    if bank_validation_memories:
        errors.append(f"validation_task_memory_in_effective_bank:{bank_validation_memories[:10]}")
    for row in memory_bank:
        if str(row["task_id"]) not in train_task_ids:
            errors.append(f"non_train_task_memory_in_effective_bank:{row['memory_id']}")
    for row in rows:
        length = len(row["ordered_effective_memory_ids"])
        for key in (
            "valid_mask",
            "legal_effective_mask",
            "raw_utility",
            "positive_mask",
            "neutral_mask",
            "negative_mask",
            "strong_positive_mask",
            "strong_negative_mask",
            "positive_gain",
        ):
            if len(row[key]) != length:
                errors.append(f"{row['state_example_id']}:{key}_length_mismatch")
        if row["split"] == "validation":
            validation_task_memories = [
                memory_id
                for memory_id, memory_row in zip(row["ordered_effective_memory_ids"], memory_bank)
                if str(memory_row["task_id"]) in validation_task_ids
            ]
            if validation_task_memories:
                errors.append(
                    f"{row['state_example_id']}:validation_task_memory_visible:{validation_task_memories[:5]}"
                )
        if row["split"] == "train":
            for memory_id, memory_row, legal in zip(
                row["ordered_effective_memory_ids"],
                memory_bank,
                row["legal_effective_mask"],
            ):
                if str(memory_row["task_id"]) == str(row["task_id"]) and legal:
                    errors.append(f"{row['state_example_id']}:own_task_memory_not_masked:{memory_id}")
        if row["all_missing_state"] and row["no_positive_state"]:
            errors.append(f"{row['state_example_id']}:all_missing_and_no_positive")
        for utility, valid in zip(row["raw_utility"], row["valid_mask"]):
            if valid and utility is None:
                errors.append(f"{row['state_example_id']}:valid_null_utility")
            if not valid and utility is not None:
                errors.append(f"{row['state_example_id']}:invalid_nonnull_utility")
    excluded_validation = [
        row["memory_id"] for row in excluded_memories if row.get("exclusion_reason") == "validation_task_memory"
    ]
    return {
        "format": "stage_b_addressing_student_labels_validation_v1",
        "passed": not errors,
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "effective_memory_count": len(memory_bank),
        "excluded_memory_count": len(excluded_memories),
        "excluded_validation_task_memory_count": len(excluded_validation),
    }


def write_compiled_student_labels(output_dir: Path, compiled: CompiledStudentLabels) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "student_labels.jsonl", compiled.rows)
    write_jsonl(output_dir / "effective_memory_bank.jsonl", compiled.memory_bank)
    write_jsonl(output_dir / "excluded_memories.jsonl", compiled.summary.get("excluded_memories", []))
    from rcmf.utils.serialization import atomic_write_json

    atomic_write_json(output_dir / "summary.json", compiled.summary)
    atomic_write_json(output_dir / "validation.json", compiled.validation)
