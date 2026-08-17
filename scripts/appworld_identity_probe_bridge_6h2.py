from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


FORMAT = "appworld_identity_probe_6h2_v1"


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


def full_demo_query(instruction: str, supervisor: Mapping[str, Any]) -> str:
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {supervisor.get('first_name', '')} "
        f"{supervisor.get('last_name', '')}. "
        f"My personal email is {supervisor.get('email', '')} and phone number is "
        f"{supervisor.get('phone_number', '')}.\n"
        f"Task: {instruction}"
    )


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def decode_unverified_payload(token: str) -> dict[str, Any]:
    parts = str(token).split(".")
    if len(parts) != 3:
        raise ValueError("Token does not have three JWT segments")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JWT payload is not an object")
    return payload


def verify_token(token: str) -> dict[str, Any]:
    from appworld.common.utils import import_apis_module

    token_sha256 = hashlib.sha256(str(token).encode()).hexdigest()
    try:
        payload = decode_unverified_payload(token)
    except Exception as error:  # noqa: BLE001 - malformed input is an audited failure
        return {
            "token_sha256": token_sha256,
            "subject_sha256": None,
            "app_name_sha256": None,
            "payload_validator_accepted": False,
            "current_user_validator_accepted": False,
            "exception_type": type(error).__name__,
        }
    subject = payload.get("sub")
    app_name = str(subject).split("+", 1)[0] if isinstance(subject, str) and "+" in subject else ""
    report = {
        "token_sha256": token_sha256,
        "subject_sha256": hashlib.sha256(str(subject).encode()).hexdigest(),
        "app_name_sha256": hashlib.sha256(app_name.encode()).hexdigest(),
        "payload_validator_accepted": False,
        "current_user_validator_accepted": False,
        "exception_type": None,
    }
    try:
        manager = import_apis_module(app_name).logging_manager
        verified = manager._get_payload(token)  # exact installed validator
        report["payload_validator_accepted"] = verified == payload
        report["current_user_validator_accepted"] = manager.get_current_user(token) is not None
    except Exception as error:  # noqa: BLE001 - exact validator result is audited
        report["exception_type"] = type(error).__name__
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request = json.loads(args.input.read_text(encoding="utf-8"))
    root = Path(str(request["appworld_root"])).resolve()
    expected_python = Path(str(request["legacy_python"]))
    if Path(os.path.abspath(sys.executable)) != Path(os.path.abspath(expected_python)):
        raise RuntimeError(f"Wrong legacy executable: {sys.executable}")
    if Path(os.environ.get("APPWORLD_ROOT", "")).resolve() != root:
        raise RuntimeError("Identity probe APPWORLD_ROOT differs from request")

    import appworld
    from appworld import AppWorld
    from appworld.common.constants import DB_VERSION

    if appworld.__version__ != "0.1.0" or DB_VERSION != "0.1.0":
        raise RuntimeError("Identity probe did not import AppWorld 0.1.0/DB 0.1.0")
    module_path = Path(appworld.__file__).resolve()
    if expected_python.parent.parent not in module_path.parents:
        raise RuntimeError(f"AppWorld import leaked outside legacy venv: {module_path}")

    jwt_pairs_by_task: dict[str, list[Mapping[str, Any]]] = {}
    for pair in request.get("jwt_pairs", []):
        jwt_pairs_by_task.setdefault(str(pair["task_id"]), []).append(pair)

    rows = []
    jwt_rows = []
    seen: set[str] = set()
    for task_id in request["task_ids"]:
        task_id = str(task_id)
        if task_id in seen:
            raise ValueError(f"Duplicate task ID in identity probe: {task_id}")
        seen.add(task_id)
        experiment_name = f"{request['experiment_prefix']}_{task_id}"
        experiment_path = root / "experiments" / "outputs" / experiment_name
        if experiment_path.exists():
            raise FileExistsError(f"Identity-probe world is not fresh: {experiment_path}")
        task_root = root / "data" / "tasks" / task_id
        task_files = directory_hash(task_root)
        with AppWorld(
            task_id=task_id,
            experiment_name=experiment_name,
            load_ground_truth=False,
            random_seed=int(request["random_seed"]),
            max_interactions=int(request["max_interactions"]),
            max_api_calls_per_interaction=int(request["max_api_calls_per_interaction"]),
        ) as world:
            supervisor = dict(world.task.supervisor)
            field_values = {
                "instruction": str(world.task.instruction),
                "first_name": str(supervisor.get("first_name", "")),
                "last_name": str(supervisor.get("last_name", "")),
                "email": str(supervisor.get("email", "")),
                "phone_number": str(supervisor.get("phone_number", "")),
            }
            model_hashes = world.models.model_hashes()
            query = full_demo_query(world.task.instruction, supervisor)
            rows.append(
                {
                    "task_id": task_id,
                    "task_id_sha256": hashlib.sha256(task_id.encode()).hexdigest(),
                    "full_task_query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                    "field_sha256": {
                        key: hashlib.sha256(value.encode()).hexdigest()
                        for key, value in sorted(field_values.items())
                    },
                    "supervisor_identity_sha256": canonical_hash(
                        {key: field_values[key] for key in ("first_name", "last_name", "email", "phone_number")}
                    ),
                    "task_datetime_sha256": hashlib.sha256(
                        world.task.datetime.isoformat().encode()
                    ).hexdigest(),
                    "db_version": str(world.task.db_version),
                    "allowed_apps_sha256": canonical_hash(sorted(world.task.allowed_apps)),
                    "task_files": task_files,
                    "initial_database_fingerprint": {
                        "method": "public_ModelCollection.model_hashes",
                        "model_hash_count": len(model_hashes),
                        "sha256": canonical_hash(model_hashes),
                    },
                    "experiment_name_sha256": hashlib.sha256(
                        experiment_name.encode()
                    ).hexdigest(),
                }
            )
            for pair in jwt_pairs_by_task.get(task_id, []):
                jwt_rows.append(
                    {
                        "pair_id": str(pair["pair_id"]),
                        "task_id": task_id,
                        "state_example_id": str(pair["state_example_id"]),
                        "step_id": int(pair["step_id"]),
                        "expected": verify_token(str(pair["expected_token"])),
                        "actual": verify_token(str(pair["actual_token"])),
                        "subsequent_authenticated_action_count": int(
                            pair.get("subsequent_authenticated_action_count", 0)
                        ),
                        "subsequent_authenticated_actions_exception_free": bool(
                            pair.get(
                                "subsequent_authenticated_actions_exception_free", False
                            )
                        ),
                    }
                )

    payload = {
        "format": FORMAT,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "appworld_version": appworld.__version__,
        "db_version": DB_VERSION,
        "appworld_module_sha256": sha256_file(module_path),
        "appworld_root_sha256": hashlib.sha256(str(root).encode()).hexdigest(),
        "request_sha256": canonical_hash(request),
        "task_count": len(rows),
        "rows": rows,
        "jwt_pair_count": len(jwt_rows),
        "jwt_rows": jwt_rows,
    }
    payload["result_sha256"] = canonical_hash(payload)
    atomic_write_json(args.output, payload)


if __name__ == "__main__":
    main()
