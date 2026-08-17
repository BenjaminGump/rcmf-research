from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
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


def _primitive(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)) and all(
        isinstance(item, (str, int, float, bool)) or item is None for item in value
    ):
        return list(value)
    return repr(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-python", type=Path, required=True)
    parser.add_argument("--expected-root", type=Path, required=True)
    args = parser.parse_args()

    if Path(sys.executable).resolve() != args.expected_python.resolve():
        raise RuntimeError(f"Probe used wrong Python: {sys.executable}")
    root = Path(os.environ.get("APPWORLD_ROOT", "")).resolve()
    if root != args.expected_root.resolve():
        raise RuntimeError(f"Probe used wrong APPWORLD_ROOT: {root}")

    import appworld
    from appworld.common import constants

    module_path = Path(appworld.__file__).resolve()
    if args.expected_python.resolve().parents[1] not in module_path.parents:
        raise RuntimeError(f"AppWorld import leaked outside legacy venv: {module_path}")
    exposed_versions = {
        name: _primitive(getattr(constants, name))
        for name in sorted(dir(constants))
        if name.isupper() and "VERSION" in name
    }
    distribution = metadata.distribution("appworld")
    payload = {
        "format": "appworld_legacy_environment_probe_6h1_v1",
        "python_executable": sys.executable,
        "python_version": sys.version,
        "appworld_version": appworld.__version__,
        "distribution_version": distribution.version,
        "appworld_module_path": str(module_path),
        "appworld_root": str(root),
        "appworld_cache": os.environ.get("APPWORLD_CACHE"),
        "exposed_version_constants": exposed_versions,
        "db_version": exposed_versions.get("DB_VERSION"),
    }
    _atomic_write(args.output, payload)


if __name__ == "__main__":
    main()
