from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rcmf.pipeline.portable_v2.schemas import TaskRecord


DATASET_VERSION = "alfworld-json-2.1.1"
TASK_MANIFEST_SHA256 = "ff7a9f5ea60028a608470c6fa088a5e82768f64033fb2f74804849744f3cf6f9"
TRACK_R_TASK_IDS_SHA256 = "2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534"
EXPECTED_SPLIT_COUNTS = {
    "train": 3553,
    "valid_train": 200,
    "valid_seen": 140,
    "valid_unseen": 134,
}


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_sealed_task_manifest(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise TypeError("ALFWorld task manifest must be an array of objects")
    if canonical_sha256(payload) != TASK_MANIFEST_SHA256:
        raise ValueError("ALFWorld task manifest canonical identity differs")
    counts: dict[str, int] = {}
    task_ids: set[str] = set()
    game_paths: set[str] = set()
    for row in payload:
        split = str(row.get("split", ""))
        counts[split] = counts.get(split, 0) + 1
        task_id = str(row.get("task_id", ""))
        game_path = str(row.get("game_path", ""))
        if not task_id or task_id in task_ids:
            raise ValueError(f"duplicate/empty ALFWorld task ID: {task_id!r}")
        if not game_path or game_path in game_paths:
            raise ValueError(f"duplicate/empty ALFWorld game path: {game_path!r}")
        task_ids.add(task_id)
        game_paths.add(game_path)
    if counts != EXPECTED_SPLIT_COUNTS:
        raise ValueError(f"ALFWorld split population differs: {counts}")
    valid_unseen_ids = sorted(
        str(row["task_id"]) for row in payload if row["split"] == "valid_unseen"
    )
    if canonical_sha256(valid_unseen_ids) != TRACK_R_TASK_IDS_SHA256:
        raise ValueError("Track R task-list identity differs")
    return payload


def portable_task_records(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[TaskRecord, ...]]:
    result: dict[str, list[TaskRecord]] = {}
    for raw in rows:
        lineage = raw["lineage_keys"]
        task = TaskRecord(
            benchmark="alfworld",
            dataset_version=DATASET_VERSION,
            split=str(raw["split"]),
            task_id=str(raw["task_id"]),
            instruction=str(raw["instruction"]),
            lineage_keys=(
                str(lineage["trial"]),
                str(lineage["task_lineage_sha256"]),
                str(lineage["scene_sha256"]),
            ),
            source_identity={
                "task_manifest_sha256": TASK_MANIFEST_SHA256,
                "traj_data_sha256": raw["traj_data_sha256"],
                "initial_state_pddl_sha256": raw["initial_state_pddl_sha256"],
                "game_tw_pddl_sha256": raw["game_tw_pddl_sha256"],
                "official_walkthrough_sha256": raw["official_walkthrough_sha256"],
            },
            metadata={
                "official_task_id": raw["official_task_id"],
                "game_path": raw["game_path"],
                "task_type": raw["task_type"],
                "task_family": raw["task_family"],
                "human_instructions": raw["human_instructions"],
                "scene": raw["scene"],
            },
        )
        task.validate()
        result.setdefault(task.split, []).append(task)
    return {
        split: tuple(sorted(tasks, key=lambda task: task.task_id))
        for split, tasks in sorted(result.items())
    }


def assert_no_train_evaluation_lineage_collision(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    train = [row for row in rows if row["split"] == "train"]
    evaluation = [row for row in rows if row["split"] in {"valid_seen", "valid_unseen"}]
    for field in (
        "task_id",
        "traj_data_sha256",
        "initial_state_pddl_sha256",
        "game_tw_pddl_sha256",
    ):
        overlap = {str(row[field]) for row in train} & {str(row[field]) for row in evaluation}
        if overlap:
            raise ValueError(f"ALFWorld TRAIN/evaluation leakage at {field}: {sorted(overlap)[:3]}")
    train_lineage = {str(row["lineage_keys"]["task_lineage_sha256"]) for row in train}
    eval_lineage = {str(row["lineage_keys"]["task_lineage_sha256"]) for row in evaluation}
    if train_lineage & eval_lineage:
        raise ValueError("ALFWorld TRAIN/evaluation task lineage collision")
