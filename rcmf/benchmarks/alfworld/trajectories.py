from __future__ import annotations

import hashlib
import json
from pathlib import Path
import traceback
from typing import Any, Iterable, Mapping, Sequence

from rcmf.benchmarks.alfworld.environment import ALFWorldTextRuntime, ENVIRONMENT_VERSION
from rcmf.benchmarks.alfworld.task_manifest import DATASET_VERSION, canonical_sha256
from rcmf.pipeline.portable_v2.schemas import (
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
)


PROVIDER_ID = "alfworld_official_expert_trajectory_provider_v1"
CORPUS_FORMAT = "alfworld_official_expert_corpus_v1"
FAILURE_STATUSES = frozenset(
    {
        "ENVIRONMENT_RESET_FAILURE",
        "EXPERT_PLAN_MISSING",
        "EXPERT_COMMAND_INVALID",
        "ENVIRONMENT_STEP_FAILURE",
        "EXPERT_TIMEOUT",
        "TERMINAL_WITHOUT_OFFICIAL_SUCCESS",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ALFWorldOfficialExpertTrajectoryProvider:
    """Obtain and execute official planner commands through TextWorld."""

    def __init__(
        self,
        *,
        data_root: str | Path,
        identity_bindings: Mapping[str, Any],
        max_steps: int = 250,
        runtime_factory: Any = ALFWorldTextRuntime,
    ) -> None:
        self.data_root = Path(data_root).resolve(strict=True)
        self.identity_bindings = dict(identity_bindings)
        self.max_steps = int(max_steps)
        self.runtime_factory = runtime_factory

    def replay(self, task: TaskRecord) -> dict[str, Any]:
        if task.split != "train":
            raise ValueError("official expert provider rejects non-TRAIN tasks")
        base = {
            "schema_version": CORPUS_FORMAT,
            "provider": PROVIDER_ID,
            "provenance": ProvenanceClass.OFFICIAL_EXPERT.value,
            "task_id": task.task_id,
            "split": task.split,
            "task_family": task.metadata["task_family"],
            "instruction": task.instruction,
            "status": "ENVIRONMENT_RESET_FAILURE",
            "success": False,
            "terminal": False,
            "initial_observation": "",
            "steps": [],
            "bindings": {
                **self.identity_bindings,
                **dict(task.source_identity),
                "game_path": task.metadata["game_path"],
                "dataset_version": DATASET_VERSION,
                "environment_version": ENVIRONMENT_VERSION,
            },
            "error": None,
        }
        runtime = None
        try:
            runtime = self.runtime_factory(
                task,
                data_root=self.data_root,
                with_expert=True,
            )
            base["initial_observation"] = runtime.initial_observation
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                base["status"] = "EXPERT_TIMEOUT"
            base["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
            return self._seal(base)

        try:
            pre_action = runtime.observation
            for step_index in range(self.max_steps):
                try:
                    command = runtime.expert_command()
                except Exception as exc:
                    message = str(exc)
                    if isinstance(exc, TimeoutError):
                        base["status"] = "EXPERT_TIMEOUT"
                    else:
                        base["status"] = (
                            "EXPERT_COMMAND_INVALID"
                            if "invalid" in message or "admissible" in message
                            else "EXPERT_PLAN_MISSING"
                        )
                    base["error"] = {
                        "step": step_index,
                        "type": type(exc).__name__,
                        "message": message,
                    }
                    break
                try:
                    outcome = runtime.step(command)
                except Exception as exc:
                    base["status"] = (
                        "EXPERT_TIMEOUT"
                        if isinstance(exc, TimeoutError)
                        else "ENVIRONMENT_STEP_FAILURE"
                    )
                    base["error"] = {
                        "step": step_index,
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=5),
                    }
                    break
                base["steps"].append(
                    {
                        "step_index": step_index,
                        "pre_action_state": pre_action,
                        "action": command,
                        "post_action_observation": outcome["observation"],
                        "raw_reward": outcome["raw_reward"],
                        "terminal": outcome["done"],
                        "official_won": outcome["official_won"],
                    }
                )
                pre_action = str(outcome["observation"])
                if outcome["done"]:
                    base["terminal"] = True
                    base["success"] = bool(outcome["official_won"])
                    base["status"] = (
                        "SUCCESS"
                        if base["success"]
                        else "TERMINAL_WITHOUT_OFFICIAL_SUCCESS"
                    )
                    break
            else:
                base["status"] = "EXPERT_TIMEOUT"
                base["error"] = {"max_steps": self.max_steps}
        finally:
            runtime.close()
        return self._seal(base)

    @staticmethod
    def _seal(record: dict[str, Any]) -> dict[str, Any]:
        record["sequence_sha256"] = canonical_sha256(
            {
                "initial_observation": record["initial_observation"],
                "steps": record["steps"],
            }
        )
        validate_corpus_row(record)
        return record


def validate_corpus_row(row: Mapping[str, Any]) -> None:
    if row.get("schema_version") != CORPUS_FORMAT:
        raise ValueError("ALFWorld corpus record schema differs")
    if row.get("provider") != PROVIDER_ID:
        raise ValueError("ALFWorld corpus provider differs")
    if row.get("provenance") != ProvenanceClass.OFFICIAL_EXPERT.value:
        raise ValueError("ALFWorld corpus provenance must be OFFICIAL_EXPERT")
    if row.get("split") != "train":
        raise ValueError("ALFWorld corpus record must be TRAIN-derived")
    status = str(row.get("status", ""))
    if status != "SUCCESS" and status not in FAILURE_STATUSES:
        raise ValueError(f"unknown ALFWorld replay status: {status!r}")
    steps = row.get("steps")
    if not isinstance(steps, list):
        raise TypeError("ALFWorld replay steps must be an array")
    if status == "SUCCESS":
        if not row.get("success") or not row.get("terminal") or not steps:
            raise ValueError("successful replay must be non-empty, terminal, and officially won")
        if not steps[-1].get("official_won") or not steps[-1].get("terminal"):
            raise ValueError("successful replay final step must carry official terminal success")
    expected = canonical_sha256(
        {"initial_observation": row.get("initial_observation", ""), "steps": steps}
    )
    if row.get("sequence_sha256") != expected:
        raise ValueError("ALFWorld replay sequence hash differs")


def portable_trajectory(
    row: Mapping[str, Any],
    *,
    source_identity: Mapping[str, Any] | None = None,
) -> TrajectoryRecord:
    validate_corpus_row(row)
    if row["status"] != "SUCCESS":
        raise ValueError("only replay-validated successes enter the portable trajectory corpus")
    steps = tuple(
        TrajectoryStep(
            step_index=int(item["step_index"]),
            pre_action_state=str(item["pre_action_state"]),
            action=str(item["action"]),
            post_action_observation=str(item["post_action_observation"]),
            raw_reward=float(item["raw_reward"]),
            terminal_status=(
                TerminalStatus.SUCCESS if item["terminal"] else TerminalStatus.NOT_TERMINAL
            ),
            metadata={"official_won": bool(item["official_won"])},
        )
        for item in row["steps"]
    )
    result = TrajectoryRecord(
        trajectory_id=f"{row['task_id']}:official-expert",
        task_id=str(row["task_id"]),
        provenance=ProvenanceClass.OFFICIAL_EXPERT,
        source_identity=dict(source_identity or {"provider": PROVIDER_ID}),
        steps=steps,
        raw_reward=float(steps[-1].raw_reward),
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity={
            "environment_version": ENVIRONMENT_VERSION,
            "game_path": row["bindings"]["game_path"],
        },
        metadata={
            "goal": row["instruction"],
            "task_family": row["task_family"],
            "initial_observation": row["initial_observation"],
            "sequence_sha256": row["sequence_sha256"],
            "bindings": dict(row["bindings"]),
        },
    )
    result.validate()
    return result


def read_corpus_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        try:
            validate_corpus_row(row)
        except Exception as exc:
            raise ValueError(f"invalid ALFWorld corpus row {line_number}: {exc}") from exc
        rows.append(row)
    task_ids = [str(row["task_id"]) for row in rows]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("ALFWorld corpus contains duplicate task rows")
    return rows


def build_corpus_manifest(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    source = Path(path).resolve(strict=True)
    status_counts: dict[str, int] = {}
    transitions = 0
    for row in rows:
        validate_corpus_row(row)
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "SUCCESS":
            transitions += len(row["steps"])
    return {
        "schema_version": "alfworld_official_expert_corpus_manifest_v1",
        "provider": PROVIDER_ID,
        "provenance": ProvenanceClass.OFFICIAL_EXPERT.value,
        "split": "train",
        "row_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "admitted_trajectory_count": status_counts.get("SUCCESS", 0),
        "admitted_transition_count": transitions,
        "corpus_jsonl": {"path": str(source), "bytes": source.stat().st_size, "sha256": _sha256_file(source)},
        "task_ids_sha256": canonical_sha256(sorted(str(row["task_id"]) for row in rows)),
    }


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
