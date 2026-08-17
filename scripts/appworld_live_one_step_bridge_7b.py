from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Mapping

from appworld_semantic_replay_bridge_6h2 import (
    canonical_hash,
    collect_token_pairs,
    identity_checks,
    parse_v1_value,
    sha256_file,
    state_fingerprint,
    verify_token,
)


PROTOCOL_VERSION = "appworld_live_one_step_bridge_7b_v1"


def _emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _load_semantic(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("rcmf_semantic_7b_live_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load semantic module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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


def _token_validation(pair: Mapping[str, Any], semantic: Any) -> dict[str, Any]:
    return {
        "path": str(pair["path"]),
        "expected": verify_token(str(pair["expected"]), semantic),
        "actual": verify_token(str(pair["actual"]), semantic),
    }


def _exception(error: BaseException) -> dict[str, str]:
    return {
        "type": type(error).__name__,
        "message_sha256": hashlib.sha256(str(error).encode()).hexdigest(),
        "traceback_sha256": hashlib.sha256(traceback.format_exc().encode()).hexdigest(),
    }


def _read_message() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise EOFError("Live bridge input closed")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("Live bridge message must be a JSON object")
    return message


def _validate_prepare(message: Mapping[str, Any]) -> None:
    if message.get("format") != PROTOCOL_VERSION:
        raise ValueError("Unexpected live bridge protocol version")
    if message.get("op") != "prepare":
        raise ValueError("First live bridge operation must be prepare")
    expected_hash = canonical_hash(message["history_steps"])
    if expected_hash != str(message["history_steps_sha256"]):
        raise ValueError("Live bridge history hash mismatch")


def _complete_history_comparisons(steps: list[dict[str, Any]], semantic: Any) -> None:
    for step in steps:
        root = step.get("root_token_validation")
        later_authenticated = []
        if root is not None:
            for later in steps:
                if int(later["step_id"]) <= int(step["step_id"]):
                    continue
                calls = semantic.authenticated_calls_using_login_result(
                    str(later["action_code"]),
                    app_name=str(root["app_name"]),
                    assigned_names=list(root["assigned_names"]),
                )
                if calls:
                    later_authenticated.append(
                        {
                            "step_id": int(later["step_id"]),
                            "calls": calls,
                            "exception_free": later["exception"] is None,
                            "semantic_v2_match": bool(
                                later["locked_v2_comparison"]["semantic_v2_match"]
                            ),
                        }
                    )
        accepted = all(
            row["exception_free"] and row["semantic_v2_match"] for row in later_authenticated
        )
        comparison = semantic.compare_observations_semantic_v3(
            str(step["expected_raw_observation"]),
            str(step["actual_raw_observation"]),
            action_code=str(step["action_code"]),
            expected_validator_accepted=bool(
                root
                and root["expected"]["payload_validator_accepted"]
                and root["expected"]["current_user_validator_accepted"]
            ),
            actual_validator_accepted=bool(
                root
                and root["actual"]["payload_validator_accepted"]
                and root["actual"]["current_user_validator_accepted"]
            ),
            subsequent_authenticated_action_count=sum(
                len(row["calls"]) for row in later_authenticated
            ),
            subsequent_authenticated_actions_accepted=accepted,
        )
        step["subsequent_authenticated_actions"] = later_authenticated
        step["semantic_comparison"] = comparison
        step["semantic_v3_match"] = bool(comparison["semantic_v3_match"])


def main() -> None:
    world = None
    try:
        prepare = _read_message()
        _validate_prepare(prepare)
        expected_python = Path(str(prepare["legacy_python"]))
        if Path(os.path.abspath(sys.executable)) != Path(os.path.abspath(expected_python)):
            raise RuntimeError(f"Live bridge used wrong Python: {sys.executable}")
        root = Path(str(prepare["appworld_root"])).resolve()
        if Path(os.environ.get("APPWORLD_ROOT", "")).resolve() != root:
            raise RuntimeError("Live bridge APPWORLD_ROOT differs from contract")
        semantic_path = Path(str(prepare["semantic_module_path"])).resolve()
        if sha256_file(semantic_path) != str(prepare["semantic_module_sha256"]):
            raise RuntimeError("Live bridge semantic module hash changed")
        semantic = _load_semantic(semantic_path)
        if semantic.SEMANTIC_NORMALIZATION_VERSION != str(prepare["normalization_version"]):
            raise RuntimeError("Live bridge semantic normalization version changed")

        import appworld
        from appworld import AppWorld
        from appworld.common.constants import DB_VERSION

        module_path = Path(appworld.__file__).resolve()
        if appworld.__version__ != "0.1.0" or str(DB_VERSION) != "0.1.0":
            raise RuntimeError("Live bridge did not load AppWorld 0.1.0")
        if expected_python.parent.parent not in module_path.parents:
            raise RuntimeError(f"AppWorld import leaked outside legacy venv: {module_path}")
        experiment_name = str(prepare["experiment_name"])
        experiment_path = root / "experiments/outputs" / experiment_name
        if experiment_path.exists():
            raise FileExistsError(f"Live bridge world is not fresh: {experiment_path}")

        started = time.perf_counter()
        with redirect_stdout(sys.stderr):
            world = AppWorld(
                task_id=str(prepare["task_id"]),
                experiment_name=experiment_name,
                load_ground_truth=False,
                random_seed=int(prepare["random_seed"]),
                max_interactions=int(prepare["max_interactions"]),
                max_api_calls_per_interaction=int(prepare["max_api_calls_per_interaction"]),
            )
            world.__enter__()
        checks = identity_checks(prepare, world)
        initial_fingerprint = state_fingerprint(world)
        steps: list[dict[str, Any]] = []
        token_validations: list[dict[str, Any]] = []
        for source in prepare["history_steps"]:
            before = state_fingerprint(world)
            code = str(source["code"])
            exception = None
            actual = ""
            try:
                with redirect_stdout(sys.stderr):
                    actual = str(world.execute(code))
            except Exception as error:  # noqa: BLE001 - recorded at protocol boundary
                exception = _exception(error)
            after = state_fingerprint(world)
            expected = str(source["expected_observation"])
            locked_v2 = semantic.compare_observations_semantic_v2(expected, actual)
            validations = []
            for pair in collect_token_pairs(
                parse_v1_value(semantic, expected),
                parse_v1_value(semantic, actual),
                semantic=semantic,
            ):
                validation = _token_validation(pair, semantic)
                validation["kind"] = "named_access_token"
                validations.append(validation)
                token_validations.append(validation)
            root_pair = _root_token_pair(expected, actual, action_code=code, semantic=semantic)
            root_validation = None
            if root_pair is not None:
                root_validation = _token_validation(root_pair, semantic)
                root_validation.update(
                    {
                        "kind": "root_login_jwt",
                        "app_name": root_pair["app_name"],
                        "assigned_names": root_pair["assigned_names"],
                    }
                )
                validations.append(root_validation)
                token_validations.append(root_validation)
            steps.append(
                {
                    "step_id": int(source["step_id"]),
                    "action_code": code,
                    "action_sha256": hashlib.sha256(code.encode()).hexdigest(),
                    "expected_raw_observation": expected,
                    "actual_raw_observation": actual,
                    "locked_v2_comparison": locked_v2,
                    "token_validations": validations,
                    "root_token_validation": root_validation,
                    "state_before": before,
                    "state_after": after,
                    "exception": exception,
                }
            )
            if exception is not None:
                break
        _complete_history_comparisons(steps, semantic)
        history_passed = bool(
            len(steps) == len(prepare["history_steps"])
            and all(step["semantic_v3_match"] for step in steps)
            and all(step["exception"] is None for step in steps)
        )
        tokens_accepted = all(
            row["actual"]["payload_validator_accepted"]
            and row["actual"]["current_user_validator_accepted"]
            for row in token_validations
        )
        ready = bool(all(checks.values()) and history_passed and tokens_accepted)
        ready_nonce = canonical_hash(
            {
                "condition_key": prepare["condition_key"],
                "experiment_name": experiment_name,
                "history": [step["state_after"] for step in steps],
            }
        )
        _emit(
            {
                "format": PROTOCOL_VERSION,
                "op": "ready",
                "ready": ready,
                "condition_key": str(prepare["condition_key"]),
                "state_example_id": str(prepare["state_example_id"]),
                "ready_nonce": ready_nonce,
                "python_executable": sys.executable,
                "appworld_version": appworld.__version__,
                "db_version": str(DB_VERSION),
                "appworld_module_path": str(module_path),
                "task_identity_checks": checks,
                "initial_state_fingerprint": initial_fingerprint,
                "prepared_state_fingerprint": state_fingerprint(world),
                "history_steps": steps,
                "actual_observations": [str(step["actual_raw_observation"]) for step in steps],
                "token_validations": token_validations,
                "history_semantic_v3_match": history_passed,
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
        if not ready:
            raise RuntimeError("Live replay preparation did not pass")

        execute = _read_message()
        if execute.get("format") != PROTOCOL_VERSION or execute.get("op") != "execute":
            raise ValueError("Second live bridge operation must be execute")
        if str(execute.get("condition_key")) != str(prepare["condition_key"]):
            raise ValueError("Execute condition key differs from prepared world")
        if str(execute.get("ready_nonce")) != ready_nonce:
            raise ValueError("Execute ready nonce differs from prepared world")
        code = str(execute["code"])
        if hashlib.sha256(code.encode()).hexdigest() != str(execute["code_sha256"]):
            raise ValueError("Generated code hash mismatch")
        before = state_fingerprint(world)
        actual = ""
        execution_exception = None
        try:
            with redirect_stdout(sys.stderr):
                actual = str(world.execute(code))
        except Exception as error:  # noqa: BLE001 - scientific outcome is recorded
            execution_exception = _exception(error)
        after = state_fingerprint(world)
        completed = bool(world.task_completed())
        target_semantic_comparison = None
        expected_target = execute.get("expected_target_observation")
        if expected_target is not None:
            expected_text = str(expected_target)
            root_pair = _root_token_pair(expected_text, actual, action_code=code, semantic=semantic)
            expected_validator_accepted = False
            actual_validator_accepted = False
            if root_pair is not None:
                validation = _token_validation(root_pair, semantic)
                expected_validator_accepted = bool(
                    validation["expected"]["payload_validator_accepted"]
                    and validation["expected"]["current_user_validator_accepted"]
                )
                actual_validator_accepted = bool(
                    validation["actual"]["payload_validator_accepted"]
                    and validation["actual"]["current_user_validator_accepted"]
                )
            target_semantic_comparison = semantic.compare_observations_semantic_v3(
                expected_text,
                actual,
                action_code=code,
                expected_validator_accepted=expected_validator_accepted,
                actual_validator_accepted=actual_validator_accepted,
            )
        _emit(
            {
                "format": PROTOCOL_VERSION,
                "op": "executed",
                "complete": True,
                "condition_key": str(prepare["condition_key"]),
                "state_example_id": str(prepare["state_example_id"]),
                "ready_nonce": ready_nonce,
                "same_world_execution": True,
                "same_python_namespace": True,
                "generated_code_sha256": str(execute["code_sha256"]),
                "raw_observation": actual,
                "locked_normalized_observation": semantic.normalize_observation_locked(actual),
                "execution_exception": execution_exception,
                "state_before": before,
                "state_after": after,
                "state_changed": before != after,
                "task_completed": completed,
                "target_semantic_comparison": target_semantic_comparison,
                "target_semantic_match": bool(
                    target_semantic_comparison and target_semantic_comparison["semantic_v3_match"]
                ),
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
    except Exception as error:  # noqa: BLE001 - protocol must return structured failure
        _emit(
            {
                "format": PROTOCOL_VERSION,
                "op": "fatal",
                "fatal": _exception(error),
            }
        )
        raise
    finally:
        if world is not None:
            try:
                with redirect_stdout(sys.stderr):
                    world.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - primary result is already emitted
                traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
