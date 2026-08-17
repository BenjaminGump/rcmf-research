from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.training.datasets import _parse_appworld_state_text

LEGACY_REPLAY_CONTRACT_VERSION = "appworld_legacy_replay_contract_6h1_v1"
LEGACY_REPLAY_RESULT_VERSION = "appworld_legacy_replay_result_6h1_v1"
LEGACY_SENTINEL_MANIFEST_VERSION = "appworld_legacy_sentinel_manifest_6h1_v1"
LOCKED_NORMALIZATION_VERSION = "appworld_observation_normalization_6h_v1"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_observation_locked(text: str) -> str:
    value = str(text).replace("\r\n", "\n").strip()
    if value.startswith("Output:\n```") and value.endswith("```"):
        value = value[len("Output:\n```") : -3].strip()
    value = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    parsed: Any = None
    parsed_ok = False
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(value)
            parsed_ok = True
            break
        except Exception:  # noqa: BLE001,S112 - preserve EXP-024A normalization
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
        except Exception:  # noqa: BLE001,S110 - preserve EXP-024A normalization
            pass
    return value


def observation_hash(text: str) -> str:
    return hashlib.sha256(normalize_observation_locked(text).encode("utf-8")).hexdigest()


def directory_manifest(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise FileNotFoundError(f"Manifest root is not a directory: {root}")
    rows = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    payload = {
        "root": str(root),
        "file_count": len(rows),
        "total_bytes": sum(row["size"] for row in rows),
        "files": rows,
    }
    payload["manifest_sha256"] = canonical_hash(rows)
    return payload


def validate_legacy_runtime(
    *,
    executable: Path,
    root: Path,
    current_executable: Path | None = None,
) -> None:
    if not executable.is_file():
        raise FileNotFoundError(f"Legacy executable missing: {executable}")
    if not root.is_dir():
        raise FileNotFoundError(f"Legacy APPWORLD_ROOT missing: {root}")
    if current_executable is not None and executable.resolve() == current_executable.resolve():
        raise ValueError("Legacy replay executable aliases the current RCMF Python")
    if "appworld-0.1.0-replay" not in executable.as_posix():
        raise ValueError(f"Unexpected legacy executable path: {executable}")
    if "appworld_legacy/0.1.0/root" not in root.as_posix():
        raise ValueError(f"Unexpected legacy APPWORLD_ROOT: {root}")


def build_replay_contract(
    *,
    query: Mapping[str, Any],
    example: Any,
    record: Any,
    legacy_python: Path,
    appworld_root: Path,
    experiment_name: str,
    random_seed: int,
    max_interactions: int,
    max_api_calls_per_interaction: int,
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Build the immutable JSON boundary consumed by the legacy subprocess."""
    state_id = str(query["state_example_id"])
    task_id = str(query["task_id"])
    step_id = int(query["step_id"])
    if str(record.task_id) != task_id:
        raise ValueError(f"Replay record/task mismatch for {state_id}")
    raw_steps = list(record.raw_trajectory["steps"])
    if step_id < 1 or step_id > len(raw_steps):
        raise ValueError(f"Replay step outside trajectory for {state_id}: {step_id}")

    _, task_instruction, parsed_history = _parse_appworld_state_text(str(example.state_text))
    if len(parsed_history) != step_id - 1:
        raise ValueError(
            f"State history length differs for {state_id}: {len(parsed_history)} != {step_id - 1}"
        )
    actions: list[dict[str, Any]] = []
    for position in range(step_id):
        source_step = raw_steps[position]
        code, _ = extract_code_and_fix_content(str(source_step["response"]))
        if not code.strip():
            raise ValueError(f"Empty recorded action at {state_id} step {position + 1}")
        if position < step_id - 1:
            parsed_index, parsed_response, parsed_observation = parsed_history[position]
            parsed_code, _ = extract_code_and_fix_content(parsed_response)
            if int(parsed_index) != position + 1 or parsed_code.strip() != code.strip():
                raise ValueError(f"History action mismatch at {state_id} step {position + 1}")
            if normalize_observation_locked(parsed_observation) != normalize_observation_locked(
                str(source_step["observation"])
            ):
                raise ValueError(f"History observation mismatch at {state_id} step {position + 1}")
        actions.append(
            {
                "step_id": position + 1,
                "is_target": position == step_id - 1,
                "code": code,
                "response_sha256": hashlib.sha256(
                    str(source_step["response"]).encode("utf-8")
                ).hexdigest(),
                "expected_observation": str(source_step["observation"]),
                "expected_observation_sha256": observation_hash(str(source_step["observation"])),
            }
        )
    target_code, _ = extract_code_and_fix_content(str(example.target_text))
    if target_code.strip() != str(actions[-1]["code"]).strip():
        raise ValueError(f"Decision target differs from trajectory for {state_id}")

    payload = {
        "format": LEGACY_REPLAY_CONTRACT_VERSION,
        "state_example_id": state_id,
        "task_id": task_id,
        "target_step": step_id,
        "history_step_count": step_id - 1,
        "expected_task_instruction": task_instruction,
        "normalization_version": LOCKED_NORMALIZATION_VERSION,
        "legacy_python": str(legacy_python),
        "appworld_root": str(appworld_root),
        "experiment_name": str(experiment_name),
        "random_seed": int(random_seed),
        "max_interactions": int(max_interactions),
        "max_api_calls_per_interaction": int(max_api_calls_per_interaction),
        "source_hashes": dict(sorted(source_hashes.items())),
        "actions": actions,
    }
    payload["actions_sha256"] = canonical_hash(actions)
    validate_replay_contract(payload)
    return payload


def _first_history_divergence(row: Mapping[str, Any]) -> int | None:
    return next(
        (
            int(check["step_id"])
            for check in row.get("history_checks", [])
            if not bool(check["observation_match"])
        ),
        None,
    )


def build_sentinel_manifest(
    old_replay_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not old_replay_rows:
        raise ValueError("Sentinel selection requires old replay rows")
    if len({str(row["state_example_id"]) for row in old_replay_rows}) != len(old_replay_rows):
        raise ValueError("Old replay rows contain duplicate states")

    selected: dict[str, set[str]] = defaultdict(set)
    for row in old_replay_rows:
        if int(row["history_step_count"]) == 0:
            selected[str(row["state_example_id"])].add("no_history")

    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in old_replay_rows:
        by_task[str(row["task_id"])].append(row)
    for task_id, rows in sorted(by_task.items()):
        candidates = [row for row in rows if _first_history_divergence(row) == 1]
        if not candidates:
            candidates = list(rows)
        chosen = min(
            candidates,
            key=lambda row: hashlib.sha256(
                str(row["state_example_id"]).encode("utf-8")
            ).hexdigest(),
        )
        selected[str(chosen["state_example_id"])].add(f"task_family:{task_id}")

    step_two = sorted(
        [row for row in old_replay_rows if _first_history_divergence(row) == 2],
        key=lambda row: hashlib.sha256(str(row["state_example_id"]).encode("utf-8")).hexdigest(),
    )[:2]
    for row in step_two:
        selected[str(row["state_example_id"])].add("step_2_divergence")

    earliest = min(old_replay_rows, key=lambda row: int(row["step_id"]))
    latest = max(old_replay_rows, key=lambda row: int(row["step_id"]))
    selected[str(earliest["state_example_id"])].add("early")
    selected[str(latest["state_example_id"])].add("late")

    source = {str(row["state_example_id"]): row for row in old_replay_rows}
    rows = [
        {
            "state_example_id": state_id,
            "task_id": str(source[state_id]["task_id"]),
            "step_id": int(source[state_id]["step_id"]),
            "history_step_count": int(source[state_id]["history_step_count"]),
            "old_first_divergence_step": _first_history_divergence(source[state_id]),
            "selection_reasons": sorted(reasons),
        }
        for state_id, reasons in sorted(selected.items())
    ]
    payload = {
        "format": LEGACY_SENTINEL_MANIFEST_VERSION,
        "state_count": len(rows),
        "task_count": len({row["task_id"] for row in rows}),
        "no_history_state_count": sum(row["history_step_count"] == 0 for row in rows),
        "rows": rows,
    }
    payload["manifest_sha256"] = canonical_hash(payload)
    return payload


def validate_replay_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("format") != LEGACY_REPLAY_CONTRACT_VERSION:
        raise ValueError("Unexpected legacy replay contract version")
    if contract.get("normalization_version") != LOCKED_NORMALIZATION_VERSION:
        raise ValueError("Legacy replay contract changed observation normalization")
    actions = list(contract.get("actions", []))
    if not actions:
        raise ValueError("Legacy replay contract has no target action")
    step_ids = [int(row["step_id"]) for row in actions]
    if step_ids != list(range(1, len(actions) + 1)):
        raise ValueError(f"Replay action order is not contiguous: {step_ids}")
    target_step = int(contract["target_step"])
    if target_step != len(actions):
        raise ValueError("Target action must be the final replay action")
    if sum(bool(row["is_target"]) for row in actions) != 1:
        raise ValueError("Replay contract must contain exactly one target action")
    if not bool(actions[-1]["is_target"]):
        raise ValueError("Only the final replay action may be the target")
    if canonical_hash(actions) != contract["actions_sha256"]:
        raise ValueError("Replay action hash mismatch")


def validate_bridge_result(
    result: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    executable: Path,
    root: Path,
) -> None:
    if result.get("format") != LEGACY_REPLAY_RESULT_VERSION:
        raise ValueError("Unexpected legacy bridge result version")
    if result.get("contract_sha256") != canonical_hash(contract):
        raise ValueError("Legacy bridge result contract hash mismatch")
    if Path(str(result["python_executable"])).resolve() != executable.resolve():
        raise ValueError("Legacy bridge used the wrong Python executable")
    if Path(str(result["appworld_root"])).resolve() != root.resolve():
        raise ValueError("Legacy bridge used the wrong APPWORLD_ROOT")
    if result.get("appworld_version") != "0.1.0":
        raise ValueError("Legacy bridge did not import AppWorld 0.1.0")
    if result.get("db_version") != "0.1.0":
        raise ValueError("Legacy bridge did not expose DB version 0.1.0")
    if result.get("state_example_id") != contract.get("state_example_id"):
        raise ValueError("Legacy bridge state identity mismatch")
    steps = list(result.get("steps", []))
    if [int(row["step_id"]) for row in steps] != [
        int(row["step_id"]) for row in contract["actions"]
    ]:
        raise ValueError("Legacy bridge changed replay action order")


def summarize_replay_results(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Replay summary requires state rows")
    if len({str(row["state_example_id"]) for row in rows}) != len(rows):
        raise ValueError("Replay summary contains duplicate states")
    history_steps = [step for row in rows for step in row["steps"] if not bool(step["is_target"])]
    first_divergence = Counter(
        "none" if row.get("first_divergence_step") is None else str(row["first_divergence_step"])
        for row in rows
    )
    by_task: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task_id"])].append(row)
    return {
        "state_count": len(rows),
        "task_count": len(by_task),
        "initial_identity_match_count": sum(
            bool(row["initial_task_identity_match"]) for row in rows
        ),
        "complete_history_match_count": sum(bool(row["complete_history_match"]) for row in rows),
        "history_observation_count": len(history_steps),
        "history_observation_match_count": sum(
            bool(step["normalized_match"]) for step in history_steps
        ),
        "history_raw_observation_match_count": sum(
            bool(step["raw_match"]) for step in history_steps
        ),
        "target_observation_match_count": sum(
            bool(row["target_observation_match"]) for row in rows
        ),
        "target_raw_observation_match_count": sum(
            bool(row["target_raw_observation_match"]) for row in rows
        ),
        "complete_replay_pass_count": sum(bool(row["passed"]) for row in rows),
        "first_divergence_step_counts": dict(sorted(first_divergence.items())),
        "by_task": {
            task_id: {
                "state_count": len(values),
                "initial_identity_match_count": sum(
                    bool(value["initial_task_identity_match"]) for value in values
                ),
                "history_match_count": sum(
                    bool(value["complete_history_match"]) for value in values
                ),
                "target_match_count": sum(
                    bool(value["target_observation_match"]) for value in values
                ),
                "pass_count": sum(bool(value["passed"]) for value in values),
            }
            for task_id, values in sorted(by_task.items())
        },
    }


def paired_environment_comparison(
    old_rows: Sequence[Mapping[str, Any]],
    legacy_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    old_by_state = {str(row["state_example_id"]): row for row in old_rows}
    new_by_state = {str(row["state_example_id"]): row for row in legacy_rows}
    if set(old_by_state) != set(new_by_state):
        raise ValueError("Paired replay state identities differ")
    old_history = sum(
        bool(check["observation_match"])
        for row in old_rows
        for check in row.get("history_checks", [])
    )
    new_history = sum(
        bool(step["normalized_match"])
        for row in legacy_rows
        for step in row["steps"]
        if not bool(step["is_target"])
    )
    return {
        "state_count": len(old_by_state),
        "appworld_0_2_dev0": {
            "complete_replay_pass_count": sum(bool(row["passed"]) for row in old_rows),
            "complete_history_match_count": sum(bool(row["history_match"]) for row in old_rows),
            "history_observation_match_count": old_history,
            "target_observation_match_count": sum(
                bool(row["target_observation_match"]) for row in old_rows
            ),
        },
        "appworld_0_1_0": {
            "complete_replay_pass_count": sum(bool(row["passed"]) for row in legacy_rows),
            "complete_history_match_count": sum(
                bool(row["complete_history_match"]) for row in legacy_rows
            ),
            "history_observation_match_count": new_history,
            "target_observation_match_count": sum(
                bool(row["target_observation_match"]) for row in legacy_rows
            ),
        },
    }


def observation_similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None,
        normalize_observation_locked(left),
        normalize_observation_locked(right),
    ).ratio()
