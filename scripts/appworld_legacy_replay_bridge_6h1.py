from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "appworld_legacy_replay_contract_6h1_v2"
RESULT_VERSION = "appworld_legacy_replay_result_6h1_v2"
NORMALIZATION_VERSION = "appworld_observation_normalization_6h_v1"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_observation(text: str) -> str:
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _directory_hash(root: Path) -> dict[str, Any]:
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(value for value in root.rglob("*") if value.is_file())
    ]
    return {
        "file_count": len(rows),
        "total_bytes": sum(row["size"] for row in rows),
        "sha256": canonical_hash(rows),
    }


def _state_fingerprint(world: Any) -> dict[str, Any]:
    model_hashes = world.models.model_hashes()
    return {
        "method": "public_ModelCollection.model_hashes",
        "model_hash_count": len(model_hashes),
        "sha256": canonical_hash(model_hashes),
    }


def _full_demo_task_query(instruction: str, supervisor: Mapping[str, Any]) -> str:
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {supervisor.get('first_name', '')} "
        f"{supervisor.get('last_name', '')}. "
        f"My personal email is {supervisor.get('email', '')} and phone number is "
        f"{supervisor.get('phone_number', '')}.\n"
        f"Task: {instruction}"
    )


def _task_identity_checks(
    contract: Mapping[str, Any], task_metadata: Mapping[str, Any]
) -> dict[str, bool]:
    actual_query = _full_demo_task_query(
        str(task_metadata["instruction"]),
        task_metadata["supervisor"],
    )
    return {
        "task_query_match": actual_query == str(contract["expected_task_query"]),
        "task_id_match": str(task_metadata["task_id"]) == str(contract["task_id"]),
        "db_version_match": str(task_metadata["db_version"]) == "0.1.0",
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract = json.loads(args.input.read_text(encoding="utf-8"))
    if contract.get("format") != CONTRACT_VERSION:
        raise ValueError("Unexpected replay contract version")
    if contract.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("Observation normalization version changed")
    if canonical_hash(contract["actions"]) != contract["actions_sha256"]:
        raise ValueError("Replay action hash mismatch")
    root = Path(str(contract["appworld_root"])).resolve()
    expected_python = Path(str(contract["legacy_python"]))
    if _lexical_absolute(Path(sys.executable)) != _lexical_absolute(expected_python):
        raise RuntimeError(f"Wrong replay Python: {sys.executable}")
    if Path(os.environ.get("APPWORLD_ROOT", "")).resolve() != root:
        raise RuntimeError("Bridge APPWORLD_ROOT differs from the contract")

    import appworld
    from appworld.common.constants import DB_VERSION

    if appworld.__version__ != "0.1.0" or DB_VERSION != "0.1.0":
        raise RuntimeError(f"Wrong AppWorld version triple: {appworld.__version__}/{DB_VERSION}")
    module_path = Path(appworld.__file__).resolve()
    expected_venv = _lexical_absolute(expected_python).parent.parent
    if expected_venv not in module_path.parents:
        raise RuntimeError(f"AppWorld import leaked outside legacy venv: {module_path}")

    from appworld import AppWorld

    experiment_name = str(contract["experiment_name"])
    experiment_path = root / "experiments" / "outputs" / experiment_name
    if experiment_path.exists():
        raise FileExistsError(f"Fresh-world experiment output already exists: {experiment_path}")
    task_id = str(contract["task_id"])
    task_input_root = root / "data" / "tasks" / task_id
    initial_task_files = _directory_hash(task_input_root)
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    fatal_exception = None
    with AppWorld(
        task_id=task_id,
        experiment_name=experiment_name,
        load_ground_truth=False,
        random_seed=int(contract["random_seed"]),
        max_interactions=int(contract["max_interactions"]),
        max_api_calls_per_interaction=int(contract["max_api_calls_per_interaction"]),
    ) as world:
        task_metadata = {
            "task_id": world.task.id,
            "instruction": world.task.instruction,
            "instruction_sha256": hashlib.sha256(
                world.task.instruction.encode("utf-8")
            ).hexdigest(),
            "supervisor": dict(world.task.supervisor),
            "supervisor_sha256": canonical_hash(dict(world.task.supervisor)),
            "datetime": world.task.datetime.isoformat(),
            "db_version": world.task.db_version,
            "allowed_apps": sorted(world.task.allowed_apps),
        }
        initial_state = _state_fingerprint(world)
        for action in contract["actions"]:
            step_started = time.perf_counter()
            before = _state_fingerprint(world)
            raw_observation = ""
            exception = None
            try:
                raw_observation = str(world.execute(str(action["code"])))
            except Exception as error:  # noqa: BLE001 - capture exact replay failure
                exception = {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                }
                fatal_exception = exception
            after = _state_fingerprint(world)
            expected_raw = str(action["expected_observation"])
            expected_normalized = normalize_observation(expected_raw)
            actual_normalized = normalize_observation(raw_observation)
            steps.append(
                {
                    "step_id": int(action["step_id"]),
                    "is_target": bool(action["is_target"]),
                    "action": str(action["code"]),
                    "action_sha256": hashlib.sha256(
                        str(action["code"]).encode("utf-8")
                    ).hexdigest(),
                    "expected_raw_observation": expected_raw,
                    "actual_raw_observation": raw_observation,
                    "expected_normalized_observation": expected_normalized,
                    "actual_normalized_observation": actual_normalized,
                    "expected_raw_sha256": hashlib.sha256(expected_raw.encode("utf-8")).hexdigest(),
                    "actual_raw_sha256": hashlib.sha256(
                        raw_observation.encode("utf-8")
                    ).hexdigest(),
                    "expected_normalized_sha256": hashlib.sha256(
                        expected_normalized.encode("utf-8")
                    ).hexdigest(),
                    "actual_normalized_sha256": hashlib.sha256(
                        actual_normalized.encode("utf-8")
                    ).hexdigest(),
                    "raw_match": raw_observation == expected_raw,
                    "normalized_match": actual_normalized == expected_normalized,
                    "state_before": before,
                    "state_after": after,
                    "exception": exception,
                    "elapsed_seconds": time.perf_counter() - step_started,
                }
            )
            if exception is not None:
                break
        final_state = _state_fingerprint(world)

    history_steps = [step for step in steps if not step["is_target"]]
    target_steps = [step for step in steps if step["is_target"]]
    target = target_steps[0] if len(target_steps) == 1 else None
    first_divergence = next(
        (step["step_id"] for step in steps if not step["normalized_match"]),
        None,
    )
    actual_task_query = _full_demo_task_query(
        str(task_metadata["instruction"]), task_metadata["supervisor"]
    )
    identity_checks = _task_identity_checks(contract, task_metadata)
    result = {
        "format": RESULT_VERSION,
        "contract_sha256": canonical_hash(contract),
        "state_example_id": contract["state_example_id"],
        "task_id": task_id,
        "target_step": int(contract["target_step"]),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "appworld_version": appworld.__version__,
        "db_version": DB_VERSION,
        "appworld_module_path": str(module_path),
        "appworld_root": str(root),
        "appworld_cache": os.environ.get("APPWORLD_CACHE"),
        "experiment_name": experiment_name,
        "task_metadata": task_metadata,
        "initial_task_files": initial_task_files,
        "expected_task_query_sha256": hashlib.sha256(
            str(contract["expected_task_query"]).encode("utf-8")
        ).hexdigest(),
        "actual_task_query_sha256": hashlib.sha256(
            actual_task_query.encode("utf-8")
        ).hexdigest(),
        "task_identity_checks": identity_checks,
        "initial_task_identity_match": all(identity_checks.values()),
        "initial_state_fingerprint": initial_state,
        "final_state_fingerprint": final_state,
        "state_snapshot_api": {
            "supported": callable(getattr(world, "save_state", None)),
            "used_for_mutation": False,
            "fingerprint_method": "public_ModelCollection.model_hashes",
        },
        "steps": steps,
        "complete_history_match": len(history_steps) == int(contract["target_step"]) - 1
        and all(step["normalized_match"] for step in history_steps),
        "complete_history_raw_match": len(history_steps) == int(contract["target_step"]) - 1
        and all(step["raw_match"] for step in history_steps),
        "target_observation_match": bool(target and target["normalized_match"]),
        "target_raw_observation_match": bool(target and target["raw_match"]),
        "first_divergence_step": first_divergence,
        "fatal_exception": fatal_exception,
        "elapsed_seconds": time.perf_counter() - started,
    }
    result["passed"] = bool(
        result["initial_task_identity_match"]
        and result["complete_history_match"]
        and result["target_observation_match"]
        and fatal_exception is None
    )
    result["result_sha256"] = canonical_hash(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    _atomic_write_json(args.output, result)


if __name__ == "__main__":
    main()
