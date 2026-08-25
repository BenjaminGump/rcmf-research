from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterator

import _bootstrap  # noqa: F401

import scripts.prepare_cross_attention_field_8b as base


_READ_JSONL = base.read_jsonl


def decision_task_id(row: Mapping[str, Any]) -> str:
    value = row.get("task_id")
    if value:
        return str(value)
    metadata = row.get("metadata", {})
    if isinstance(metadata, Mapping) and metadata.get("task_id"):
        return str(metadata["task_id"])
    episode = str(row.get("episode_id", ""))
    if episode:
        return episode.rsplit(":", 1)[-1]
    raise KeyError("Decision row has no task identity")


def _schema_compatible_read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    for source in _READ_JSONL(path):
        row = dict(source)
        if path.name == "decision_examples.jsonl" and "task_id" not in row:
            row["task_id"] = decision_task_id(row)
        yield row


def main() -> None:
    base.read_jsonl = _schema_compatible_read_jsonl
    base.main()


if __name__ == "__main__":
    main()
