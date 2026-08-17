from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from appworld_semantic_replay_bridge_6h2 import (
    atomic_write_json,
    canonical_hash,
    collect_token_pairs,
    directory_hash,
    identity_checks,
    parse_v1_value,
    sha256_file,
    state_fingerprint,
    verify_token,
)


CONTRACT_VERSION = "appworld_semantic_replay_contract_7b_v1"
RESULT_VERSION = "appworld_semantic_replay_result_7b_v1"


def load_semantic_module(path: Path) -> Any:
    name = "rcmf_semantic_7b_standalone"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load semantic-normalization module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _root_token_pair(
    expected_raw: str,
    actual_raw: str,
    *,
    action_code: str,
    semantic: Any,
) -> dict[str, Any] | None:
    context = semantic.analyze_login_action(action_code)
    if context.login_call_count != 1:
        return None
    expected = parse_v1_value(semantic, expected_raw)
    actual = parse_v1_value(semantic, actual_raw)
    if not isinstance(expected, str) or not isinstance(actual, str):
        return None
    try:
        semantic.decode_jwt_strict(expected)
        semantic.decode_jwt_strict(actual)
    except ValueError:
        return None
    return {
        "path": "$",
        "expected": expected,
        "actual": actual,
        "app_name": context.app_name,
        "assigned_names": list(context.assigned_names),
    }


def _validate_pair(
    pair: dict[str, Any],
    *,
    step_id: int,
    kind: str,
    semantic: Any,
) -> dict[str, Any]:
    return {
        "path": pair["path"],
        "step_id": step_id,
        "kind": kind,
        "expected": verify_token(pair["expected"], semantic),
        "actual": verify_token(pair["actual"], semantic),
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
            code = str(action["code"])
            try:
                actual_raw = str(world.execute(code))
            except Exception as error:  # noqa: BLE001 - exact failure is recorded
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
            locked_v2 = semantic.compare_observations_semantic_v2(
                expected_raw, actual_raw
            )
            step_validations = []
            for pair in collect_token_pairs(
                parse_v1_value(semantic, expected_raw),
                parse_v1_value(semantic, actual_raw),
                semantic=semantic,
            ):
                validation = _validate_pair(
                    pair,
                    step_id=int(action["step_id"]),
                    kind="named_access_token",
                    semantic=semantic,
                )
                step_validations.append(validation)
                token_validations.append(validation)
            root_pair = _root_token_pair(
                expected_raw,
                actual_raw,
                action_code=code,
                semantic=semantic,
            )
            root_validation = None
            if root_pair is not None:
                root_validation = _validate_pair(
                    root_pair,
                    step_id=int(action["step_id"]),
                    kind="root_login_jwt",
                    semantic=semantic,
                )
                root_validation["app_name"] = root_pair["app_name"]
                root_validation["assigned_names"] = root_pair["assigned_names"]
                step_validations.append(root_validation)
                token_validations.append(root_validation)
            steps.append(
                {
                    "step_id": int(action["step_id"]),
                    "is_target": bool(action["is_target"]),
                    "action_code": code,
                    "action_sha256": hashlib.sha256(code.encode()).hexdigest(),
                    "expected_raw_observation": expected_raw,
                    "actual_raw_observation": actual_raw,
                    "locked_v2_comparison": locked_v2,
                    "state_before": before,
                    "state_after": after,
                    "token_validations": step_validations,
                    "root_token_validation": root_validation,
                    "exception": exception,
                    "elapsed_seconds": time.perf_counter() - step_started,
                }
            )
            if exception is not None:
                break
        final_state = state_fingerprint(world)

    for step in steps:
        root_validation = step["root_token_validation"]
        later_authenticated: list[dict[str, Any]] = []
        if root_validation is not None:
            for later in steps:
                if int(later["step_id"]) <= int(step["step_id"]):
                    continue
                calls = semantic.authenticated_calls_using_login_result(
                    later["action_code"],
                    app_name=str(root_validation["app_name"]),
                    assigned_names=list(root_validation["assigned_names"]),
                )
                if calls:
                    later_authenticated.append(
                        {
                            "step_id": int(later["step_id"]),
                            "calls": calls,
                            "exception_free": later["exception"] is None,
                            "observation_semantic_v2_match": bool(
                                later["locked_v2_comparison"]["semantic_v2_match"]
                            ),
                        }
                    )
        subsequent_accepted = all(
            row["exception_free"] and row["observation_semantic_v2_match"]
            for row in later_authenticated
        )
        comparison = semantic.compare_observations_semantic_v3(
            step["expected_raw_observation"],
            step["actual_raw_observation"],
            action_code=step["action_code"],
            expected_validator_accepted=bool(
                root_validation
                and root_validation["expected"]["payload_validator_accepted"]
                and root_validation["expected"]["current_user_validator_accepted"]
            ),
            actual_validator_accepted=bool(
                root_validation
                and root_validation["actual"]["payload_validator_accepted"]
                and root_validation["actual"]["current_user_validator_accepted"]
            ),
            subsequent_authenticated_action_count=sum(
                len(row["calls"]) for row in later_authenticated
            ),
            subsequent_authenticated_actions_accepted=subsequent_accepted,
        )
        step["subsequent_authenticated_actions"] = later_authenticated
        step["semantic_comparison"] = comparison
        step["raw_match"] = bool(comparison["raw_match"])
        step["v1_match"] = bool(comparison["v1_match"])
        step["semantic_v2_match"] = bool(comparison["semantic_v2_match"])
        step["semantic_v3_match"] = bool(comparison["semantic_v3_match"])

    history = [step for step in steps if not bool(step["is_target"])]
    targets = [step for step in steps if bool(step["is_target"])]
    target = targets[0] if len(targets) == 1 else None
    expected_history = int(contract["target_step"]) - 1
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
        "allowed_root_jwt_paths": sorted(semantic.ALLOWED_ROOT_JWT_PATHS),
        "task_identity_checks": checks,
        "initial_task_identity_match": all(checks.values()),
        "initial_task_files": task_files,
        "initial_state_fingerprint": initial_state,
        "final_state_fingerprint": final_state,
        "steps": steps,
        "token_validations": token_validations,
        "complete_history_raw_match": len(history) == expected_history
        and all(bool(step["raw_match"]) for step in history),
        "complete_history_v1_match": len(history) == expected_history
        and all(bool(step["v1_match"]) for step in history),
        "complete_history_v2_match": len(history) == expected_history
        and all(bool(step["semantic_v2_match"]) for step in history),
        "complete_history_v3_match": len(history) == expected_history
        and all(bool(step["semantic_v3_match"]) for step in history),
        "target_raw_match": bool(target and target["raw_match"]),
        "target_v1_match": bool(target and target["v1_match"]),
        "target_v2_match": bool(target and target["semantic_v2_match"]),
        "target_v3_match": bool(target and target["semantic_v3_match"]),
        "first_raw_divergence_step": next(
            (step["step_id"] for step in steps if not bool(step["raw_match"])), None
        ),
        "first_v1_divergence_step": next(
            (step["step_id"] for step in steps if not bool(step["v1_match"])), None
        ),
        "first_semantic_v2_divergence_step": next(
            (
                step["step_id"]
                for step in steps
                if not bool(step["semantic_v2_match"])
            ),
            None,
        ),
        "first_semantic_v3_divergence_step": next(
            (
                step["step_id"]
                for step in steps
                if not bool(step["semantic_v3_match"])
            ),
            None,
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
        and result["complete_history_v3_match"]
        and result["target_v3_match"]
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
