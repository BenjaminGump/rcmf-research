from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json, ensure_dir, sha256_file, write_jsonl


def _read_jsonl_with_lines(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if stripped:
                rows.append((line_no, json.loads(stripped)))
    return rows


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _task_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    task_id = metadata.get("task_id") or row.get("task_id")
    if task_id:
        return str(task_id)
    episode_id = str(row.get("episode_id", ""))
    return episode_id.rsplit(":", 1)[-1]


def _episode_id(row: dict[str, Any]) -> str:
    return str(row.get("episode_id", ""))


def _matches_exclusion(
    row: dict[str, Any],
    excluded_episode_ids: set[str],
    excluded_task_ids: set[str],
) -> bool:
    return _episode_id(row) in excluded_episode_ids or _task_id(row) in excluded_task_ids


def _compact_ranges(values: list[int]) -> list[dict[str, int]]:
    if not values:
        return []
    ranges: list[dict[str, int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append({"start": start, "end": previous})
        start = previous = value
    ranges.append({"start": start, "end": previous})
    return ranges


def _removed_decision_row(line_no: int, row_index: int, row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "jsonl_line": line_no,
        "example_index": row_index,
        "episode_id": _episode_id(row),
        "task_id": _task_id(row),
        "step_id": row.get("step_id"),
        "target_type": row.get("target_type"),
        "source_path": metadata.get("source_path"),
    }


def _removed_memory_row(line_no: int, row_index: int, row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw_trajectory = row.get("raw_trajectory") if isinstance(row.get("raw_trajectory"), dict) else {}
    return {
        "jsonl_line": line_no,
        "record_index": row_index,
        "memory_id": row.get("memory_id"),
        "episode_id": _episode_id(row),
        "task_id": _task_id(row),
        "success": row.get("success"),
        "experience_chars": len(str(row.get("experience_text", ""))),
        "raw_step_count": len(raw_trajectory.get("steps", []))
        if isinstance(raw_trajectory.get("steps"), list)
        else None,
        "source_path": metadata.get("source_path") or raw_trajectory.get("source_path"),
    }


def _ensure_output_dir(output_dir: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Output directory already exists and is not empty: {output_dir}. "
            "Choose a new output path to avoid overwriting prepared data."
        )
    ensure_dir(output_dir)


def filter_prepared_dataset(
    source_dir: Path,
    output_dir: Path,
    excluded_episode_ids: set[str],
    excluded_task_ids: set[str],
    reason: str,
) -> dict[str, Any]:
    if not excluded_episode_ids and not excluded_task_ids:
        raise ValueError("At least one --exclude-episode-id or --exclude-task-id is required")

    decision_path = source_dir / "decision_examples.jsonl"
    memory_path = source_dir / "memory_records.jsonl"
    if not decision_path.exists():
        raise FileNotFoundError(decision_path)
    if not memory_path.exists():
        raise FileNotFoundError(memory_path)

    _ensure_output_dir(output_dir)

    decision_rows = _read_jsonl_with_lines(decision_path)
    memory_rows = _read_jsonl_with_lines(memory_path)

    kept_decisions: list[dict[str, Any]] = []
    removed_decisions: list[dict[str, Any]] = []
    for row_index, (line_no, row) in enumerate(decision_rows):
        if _matches_exclusion(row, excluded_episode_ids, excluded_task_ids):
            removed_decisions.append(_removed_decision_row(line_no, row_index, row))
        else:
            kept_decisions.append(row)

    kept_records: list[dict[str, Any]] = []
    removed_records: list[dict[str, Any]] = []
    for row_index, (line_no, row) in enumerate(memory_rows):
        if _matches_exclusion(row, excluded_episode_ids, excluded_task_ids):
            removed_records.append(_removed_memory_row(line_no, row_index, row))
        else:
            kept_records.append(row)

    if not removed_decisions and not removed_records:
        raise ValueError(
            "The exclusion criteria did not match any decision examples or memory records. "
            "Check the task/episode identifiers before creating a filtered dataset."
        )

    write_jsonl(output_dir / "decision_examples.jsonl", kept_decisions)
    write_jsonl(output_dir / "memory_records.jsonl", kept_records)

    for filename in ("resolved_config.yaml",):
        source_file = source_dir / filename
        if source_file.exists():
            shutil.copy2(source_file, output_dir / filename)

    removed_decision_lines = [int(row["jsonl_line"]) for row in removed_decisions]
    removed_record_lines = [int(row["jsonl_line"]) for row in removed_records]
    removed_task_counts = Counter(str(row["task_id"]) for row in removed_decisions)
    removed_episode_counts = Counter(str(row["episode_id"]) for row in removed_decisions)
    source_summary = _read_json(source_dir / "summary.json")
    created_at = datetime.now(UTC).isoformat()
    filter_summary: dict[str, Any] = {
        "format": "rcmf_prepared_dataset_filter_v1",
        "created_at": created_at,
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "reason": reason,
        "excluded_episode_ids": sorted(excluded_episode_ids),
        "excluded_task_ids": sorted(excluded_task_ids),
        "source_files": {
            "decision_examples.jsonl": {
                "sha256": sha256_file(decision_path),
                "rows": len(decision_rows),
            },
            "memory_records.jsonl": {
                "sha256": sha256_file(memory_path),
                "rows": len(memory_rows),
            },
        },
        "counts": {
            "source_decision_examples": len(decision_rows),
            "kept_decision_examples": len(kept_decisions),
            "removed_decision_examples": len(removed_decisions),
            "source_memory_records": len(memory_rows),
            "kept_memory_records": len(kept_records),
            "removed_memory_records": len(removed_records),
        },
        "removed_decision_examples_by_task": dict(removed_task_counts.most_common()),
        "removed_decision_examples_by_episode": dict(removed_episode_counts.most_common()),
        "removed_decision_example_line_ranges": _compact_ranges(removed_decision_lines),
        "removed_memory_record_line_ranges": _compact_ranges(removed_record_lines),
        "removed_decision_examples": removed_decisions,
        "removed_memory_records": removed_records,
        "source_summary": source_summary,
    }
    atomic_write_json(output_dir / "filter_summary.json", filter_summary)

    summary = dict(source_summary or {})
    summary.update(
        {
            "records": len(kept_records),
            "examples": len(kept_decisions),
            "source_filter": filter_summary,
        }
    )
    atomic_write_json(output_dir / "summary.json", summary)
    return filter_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an audited filtered copy of an RCMF prepared dataset."
    )
    parser.add_argument("--source", required=True, help="Prepared dataset directory to filter.")
    parser.add_argument("--output", required=True, help="New output directory for the filtered dataset.")
    parser.add_argument("--exclude-episode-id", action="append", default=[])
    parser.add_argument("--exclude-task-id", action="append", default=[])
    parser.add_argument(
        "--reason",
        default="Manually approved prepared-dataset filter.",
        help="Human-readable reason recorded in filter_summary.json.",
    )
    args = parser.parse_args()

    summary = filter_prepared_dataset(
        source_dir=Path(args.source),
        output_dir=Path(args.output),
        excluded_episode_ids={str(value) for value in args.exclude_episode_id},
        excluded_task_ids={str(value) for value in args.exclude_task_id},
        reason=args.reason,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
