from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Mapping

from appworld_semantic_replay_bridge_6h2 import state_fingerprint


PROTOCOL_VERSION = "appworld_full_agent_bridge_7f_v1"


def _emit(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise EOFError("Full-agent bridge input closed")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("Full-agent bridge messages must be JSON objects")
    return value


def _exception(error: BaseException) -> dict[str, str]:
    return {
        "type": type(error).__name__,
        "message_sha256": hashlib.sha256(str(error).encode("utf-8")).hexdigest(),
        "traceback_sha256": hashlib.sha256(traceback.format_exc().encode("utf-8")).hexdigest(),
    }


def _supervisor(task: Any) -> dict[str, str]:
    value = task.supervisor
    return {
        "first_name": str(getattr(value, "first_name", "")),
        "last_name": str(getattr(value, "last_name", "")),
        "email": str(getattr(value, "email", "")),
        "phone_number": str(getattr(value, "phone_number", "")),
    }


def _evaluation(world: Any) -> dict[str, Any]:
    try:
        return world.evaluate(suppress_errors=True).to_dict(stats_only=True)
    except Exception as error:  # noqa: BLE001 - evaluation failure is part of the row
        return {"evaluation_exception": _exception(error)}


def main() -> None:
    world = None
    started = time.perf_counter()
    try:
        prepare = _read()
        if prepare.get("format") != PROTOCOL_VERSION or prepare.get("op") != "prepare":
            raise ValueError("First full-agent bridge operation must be prepare")
        declared_python = Path(str(prepare["legacy_python"])).absolute()
        expected_python = declared_python.resolve()
        legacy_environment_root = declared_python.parent.parent.resolve()
        if Path(sys.executable).resolve() != expected_python:
            raise RuntimeError("Full-agent bridge used the wrong Python executable")
        root = Path(str(prepare["appworld_root"])).resolve()
        if Path(os.environ.get("APPWORLD_ROOT", "")).resolve() != root:
            raise RuntimeError("Full-agent bridge APPWORLD_ROOT differs from contract")

        import appworld
        from appworld import AppWorld
        from appworld.common.constants import DB_VERSION

        module_path = Path(appworld.__file__).resolve()
        if appworld.__version__ != "0.1.0" or str(DB_VERSION) != "0.1.0":
            raise RuntimeError("Full-agent bridge did not load AppWorld 0.1.0")
        if legacy_environment_root not in module_path.parents:
            raise RuntimeError("AppWorld import leaked outside the legacy environment")
        experiment_name = str(prepare["experiment_name"])
        output_path = root / "experiments/outputs" / experiment_name
        if output_path.exists():
            raise FileExistsError(f"Experiment output already exists: {output_path}")

        with redirect_stdout(sys.stderr):
            world = AppWorld(
                task_id=str(prepare["task_id"]),
                experiment_name=experiment_name,
                random_seed=int(prepare["random_seed"]),
                max_interactions=int(prepare["max_interactions"]),
                max_api_calls_per_interaction=int(prepare["max_api_calls_per_interaction"]),
            )
            world.__enter__()
        nonce = hashlib.sha256(
            f"{prepare['task_id']}:{experiment_name}:{state_fingerprint(world)}".encode("utf-8")
        ).hexdigest()
        _emit(
            {
                "format": PROTOCOL_VERSION,
                "op": "ready",
                "ready": True,
                "task_id": str(world.task.id),
                "instruction": str(world.task.instruction),
                "supervisor": _supervisor(world.task),
                "allowed_apps": sorted(str(value) for value in world.task.allowed_apps),
                "ready_nonce": nonce,
                "initial_state_fingerprint": state_fingerprint(world),
                "python_executable": sys.executable,
                "appworld_version": appworld.__version__,
                "db_version": str(DB_VERSION),
                "module_path": str(module_path),
            }
        )

        step_count = 0
        while True:
            message = _read()
            if message.get("format") != PROTOCOL_VERSION:
                raise ValueError("Unexpected full-agent bridge protocol version")
            if str(message.get("ready_nonce")) != nonce:
                raise ValueError("Full-agent bridge nonce differs")
            operation = str(message.get("op"))
            if operation == "finish":
                evaluation = _evaluation(world)
                completion = float(
                    evaluation.get("aggregate", {}).get("task_goal_completion", 0.0)
                )
                _emit(
                    {
                        "format": PROTOCOL_VERSION,
                        "op": "finished",
                        "task_id": str(world.task.id),
                        "step_count": step_count,
                        "task_completed": bool(world.task_completed()),
                        "evaluation": evaluation,
                        "success": bool(completion == 100.0),
                        "final_state_fingerprint": state_fingerprint(world),
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                )
                break
            if operation != "execute":
                raise ValueError(f"Unsupported full-agent bridge operation: {operation}")
            step_id = int(message["step_id"])
            if step_id != step_count + 1:
                raise ValueError("Full-agent bridge action order is not contiguous")
            code = str(message["code"])
            if hashlib.sha256(code.encode("utf-8")).hexdigest() != str(
                message["code_sha256"]
            ):
                raise ValueError("Generated code hash differs")
            before = state_fingerprint(world)
            exception = None
            observation = ""
            try:
                with redirect_stdout(sys.stderr):
                    observation = str(world.execute(code))
            except Exception as error:  # noqa: BLE001 - scientific outcome is recorded
                exception = _exception(error)
            step_count = step_id
            _emit(
                {
                    "format": PROTOCOL_VERSION,
                    "op": "executed",
                    "step_id": step_id,
                    "raw_observation": observation,
                    "execution_exception": exception,
                    "state_before": before,
                    "state_after": state_fingerprint(world),
                    "task_completed": bool(world.task_completed()),
                    "same_world_execution": True,
                    "same_python_namespace": True,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
    except Exception as error:  # noqa: BLE001 - protocol failure must be structured
        _emit({"format": PROTOCOL_VERSION, "op": "fatal", "fatal": _exception(error)})
        raise
    finally:
        if world is not None:
            try:
                with redirect_stdout(sys.stderr):
                    world.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
