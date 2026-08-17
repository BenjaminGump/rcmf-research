from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "appworld_semantic_replay_contract_6h2_v1"
RESULT_VERSION = "appworld_semantic_replay_result_6h2_v1"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_hash(root: Path) -> dict[str, Any]:
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(value for value in root.rglob("*") if value.is_file())
    ]
    return {
        "file_count": len(rows),
        "total_bytes": sum(row["size"] for row in rows),
        "sha256": canonical_hash(rows),
    }


def state_fingerprint(world: Any) -> dict[str, Any]:
    hashes = world.models.model_hashes()
    return {
        "method": "public_ModelCollection.model_hashes",
        "model_hash_count": len(hashes),
        "sha256": canonical_hash(hashes),
    }


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
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


def load_semantic_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("rcmf_semantic_6h2_standalone", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load semantic-normalization module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_v1_value(module: Any, text: str) -> Any:
    normalized = module.normalize_observation_locked(text)
    try:
        return json.loads(normalized)
    except Exception:  # noqa: BLE001 - locked v1 may remain plain text
        return normalized


def collect_token_pairs(
    expected: Any,
    actual: Any,
    *,
    path: str = "$",
) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key in sorted(set(expected) & set(actual), key=str):
            child = f"{path}.{key}"
            if str(key) == "access_token" and isinstance(expected[key], str) and isinstance(actual[key], str):
                pairs.append(
                    {"path": child, "expected": expected[key], "actual": actual[key]}
                )
            else:
                pairs.extend(collect_token_pairs(expected[key], actual[key], path=child))
    elif isinstance(expected, list) and isinstance(actual, list):
        for index, (left, right) in enumerate(zip(expected, actual)):
            pairs.extend(collect_token_pairs(left, right, path=f"{path}[{index}]"))
    return pairs


def verify_token(token: str, semantic: Any) -> dict[str, Any]:
    from appworld.common.utils import import_apis_module

    decoded = semantic.decode_jwt_strict(token)
    subject = decoded.payload.get("sub")
    app_name = str(subject).split("+", 1)[0] if isinstance(subject, str) and "+" in subject else ""
    report = {
        "token_sha256": decoded.token_sha256,
        "subject_sha256": hashlib.sha256(str(subject).encode()).hexdigest(),
        "app_name_sha256": hashlib.sha256(app_name.encode()).hexdigest(),
        "payload_validator_accepted": False,
        "current_user_validator_accepted": False,
        "exception_type": None,
    }
    try:
        manager = import_apis_module(app_name).logging_manager
        payload = manager._get_payload(token)  # exact installed fastapi-login validator
        report["payload_validator_accepted"] = payload == decoded.payload
        user = manager.get_current_user(token)
        report["current_user_validator_accepted"] = user is not None
    except Exception as error:  # noqa: BLE001 - exact validator outcome is audited
        report["exception_type"] = type(error).__name__
    return report


def identity_checks(contract: Mapping[str, Any], world: Any) -> dict[str, bool]:
    supervisor = dict(world.task.supervisor)
    actual = {
        "instruction": str(world.task.instruction),
        "first_name": str(supervisor.get("first_name", "")),
        "last_name": str(supervisor.get("last_name", "")),
        "email": str(supervisor.get("email", "")),
        "phone_number": str(supervisor.get("phone_number", "")),
    }
    actual_hashes = {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in actual.items()
    }
    expected_hashes = dict(contract["expected_identity_field_sha256"])
    return {
        "task_id_match": str(world.task.id) == str(contract["task_id"]),
        "db_version_match": str(world.task.db_version) == "0.1.0",
        **{
            f"{key}_match": actual_hashes[key] == str(expected_hashes[key])
            for key in sorted(expected_hashes)
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract = json.loads(args.input.read_text(encoding="utf-8"))
    if contract.get("format") != CONTRACT_VERSION:
        raise ValueError("Unexpected semantic replay contract version")
    if canonical_hash(contract["actions"]) != contract["actions_sha256"]:
        raise ValueError("Semantic replay action hash mismatch")
    expected_python = Path(str(contract["legacy_python"]))
    if Path(os.path.abspath(sys.executable)) != Path(os.path.abspath(expected_python)):
        raise RuntimeError(f"Wrong replay executable: {sys.executable}")
    root = Path(str(contract["appworld_root"])).resolve()
    if Path(os.environ.get("APPWORLD_ROOT", "")).resolve() != root:
        raise RuntimeError("Semantic replay APPWORLD_ROOT differs from contract")
    semantic_path = Path(str(contract["semantic_module_path"])).resolve()
    if sha256_file(semantic_path) != str(contract["semantic_module_sha256"]):
        raise RuntimeError("Semantic-normalization source hash changed")
    semantic = load_semantic_module(semantic_path)
    if semantic.SEMANTIC_NORMALIZATION_VERSION != str(contract["normalization_version"]):
        raise RuntimeError("Semantic-normalization version changed")

    import appworld
    from appworld import AppWorld
    from appworld.common.constants import DB_VERSION

    if appworld.__version__ != "0.1.0" or DB_VERSION != "0.1.0":
        raise RuntimeError("Semantic bridge did not import AppWorld 0.1.0/DB 0.1.0")
    module_path = Path(appworld.__file__).resolve()
    if expected_python.parent.parent not in module_path.parents:
        raise RuntimeError(f"AppWorld import leaked outside legacy venv: {module_path}")
    experiment_name = str(contract["experiment_name"])
    experiment_path = root / "experiments" / "outputs" / experiment_name
    if experiment_path.exists():
        raise FileExistsError(f"Semantic replay world is not fresh: {experiment_path}")

    task_id = str(contract["task_id"])
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    token_validations: list[dict[str, Any]] = []
    fatal_exception = None
    task_files = directory_hash(root / "data" / "tasks" / task_id)
    with AppWorld(
        task_id=task_id,
        experiment_name=experiment_name,
        load_ground_truth=False,
        random_seed=int(contract["random_seed"]),
        max_interactions=int(contract["max_interactions"]),
        max_api_calls_per_interaction=int(contract["max_api_calls_per_interaction"]),
    ) as world:
        checks = identity_checks(contract, world)
        initial_state = state_fingerprint(world)
        for action in contract["actions"]:
            step_started = time.perf_counter()
            before = state_fingerprint(world)
            actual_raw = ""
            exception = None
            try:
                actual_raw = str(world.execute(str(action["code"])))
            except Exception as error:  # noqa: BLE001 - preserve exact replay failure
                exception = {
                    "type": type(error).__name__,
                    "message_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
                    "traceback_sha256": hashlib.sha256(
                        traceback.format_exc().encode()
                    ).hexdigest(),
                }
                fatal_exception = exception
            after = state_fingerprint(world)
            expected_raw = str(action["expected_observation"])
            comparison = semantic.compare_observations_semantic(expected_raw, actual_raw)
            step_token_validations = []
            for pair in collect_token_pairs(
                parse_v1_value(semantic, expected_raw),
                parse_v1_value(semantic, actual_raw),
            ):
                expected_validation = verify_token(pair["expected"], semantic)
                actual_validation = verify_token(pair["actual"], semantic)
                validation = {
                    "path": pair["path"],
                    "step_id": int(action["step_id"]),
                    "expected": expected_validation,
                    "actual": actual_validation,
                }
                step_token_validations.append(validation)
                token_validations.append(validation)
            steps.append(
                {
                    "step_id": int(action["step_id"]),
                    "is_target": bool(action["is_target"]),
                    "action_sha256": hashlib.sha256(str(action["code"]).encode()).hexdigest(),
                    "action_uses_access_token": "access_token" in str(action["code"]),
                    "expected_raw_observation": expected_raw,
                    "actual_raw_observation": actual_raw,
                    "semantic_comparison": comparison,
                    "raw_match": bool(comparison["raw_match"]),
                    "v1_match": bool(comparison["v1_match"]),
                    "semantic_v2_match": bool(comparison["semantic_v2_match"]),
                    "state_before": before,
                    "state_after": after,
                    "token_validations": step_token_validations,
                    "exception": exception,
                    "elapsed_seconds": time.perf_counter() - step_started,
                }
            )
            if exception is not None:
                break
        final_state = state_fingerprint(world)

    for validation in token_validations:
        later = [
            step
            for step in steps
            if int(step["step_id"]) > int(validation["step_id"])
            and bool(step["action_uses_access_token"])
        ]
        validation["subsequent_authenticated_action_count"] = len(later)
        validation["subsequent_authenticated_actions_exception_free"] = all(
            step["exception"] is None for step in later
        )

    history = [step for step in steps if not bool(step["is_target"])]
    targets = [step for step in steps if bool(step["is_target"])]
    target = targets[0] if len(targets) == 1 else None
    result = {
        "format": RESULT_VERSION,
        "contract_sha256": canonical_hash(contract),
        "state_example_id": str(contract["state_example_id"]),
        "task_id": task_id,
        "target_step": int(contract["target_step"]),
        "repeat_index": int(contract["repeat_index"]),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "appworld_version": appworld.__version__,
        "db_version": DB_VERSION,
        "appworld_module_path": str(module_path),
        "appworld_module_sha256": sha256_file(module_path),
        "appworld_root": str(root),
        "experiment_name_sha256": hashlib.sha256(experiment_name.encode()).hexdigest(),
        "normalization_version": semantic.SEMANTIC_NORMALIZATION_VERSION,
        "allowed_token_fields": sorted(semantic.ALLOWED_TOKEN_FIELDS),
        "allowed_temporal_claims": sorted(semantic.ALLOWED_TEMPORAL_CLAIMS),
        "task_identity_checks": checks,
        "initial_task_identity_match": all(checks.values()),
        "initial_task_files": task_files,
        "initial_state_fingerprint": initial_state,
        "final_state_fingerprint": final_state,
        "steps": steps,
        "token_validations": token_validations,
        "complete_history_raw_match": len(history) == int(contract["target_step"]) - 1
        and all(bool(step["raw_match"]) for step in history),
        "complete_history_v1_match": len(history) == int(contract["target_step"]) - 1
        and all(bool(step["v1_match"]) for step in history),
        "complete_history_semantic_match": len(history) == int(contract["target_step"]) - 1
        and all(bool(step["semantic_v2_match"]) for step in history),
        "target_raw_match": bool(target and target["raw_match"]),
        "target_v1_match": bool(target and target["v1_match"]),
        "target_semantic_match": bool(target and target["semantic_v2_match"]),
        "first_raw_divergence_step": next(
            (step["step_id"] for step in steps if not bool(step["raw_match"])), None
        ),
        "first_v1_divergence_step": next(
            (step["step_id"] for step in steps if not bool(step["v1_match"])), None
        ),
        "first_semantic_divergence_step": next(
            (step["step_id"] for step in steps if not bool(step["semantic_v2_match"])), None
        ),
        "fatal_exception": fatal_exception,
        "elapsed_seconds": time.perf_counter() - started,
        "future_generation_bridge": {
            "actual_replay_observations_preserved": True,
            "same_world_must_execute_generated_action": True,
            "historical_jwt_must_not_be_inserted": True,
        },
    }
    result["passed"] = bool(
        result["initial_task_identity_match"]
        and result["complete_history_semantic_match"]
        and result["target_semantic_match"]
        and fatal_exception is None
        and all(
            validation["actual"]["payload_validator_accepted"]
            and validation["actual"]["current_user_validator_accepted"]
            for validation in token_validations
        )
    )
    result["result_sha256"] = canonical_hash(result)
    atomic_write_json(args.output, result)


if __name__ == "__main__":
    main()
