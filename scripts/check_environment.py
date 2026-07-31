from __future__ import annotations

import argparse
import importlib
import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import _bootstrap  # noqa: F401


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _module_version(module_name: str, attr: str = "__version__") -> str:
    module = importlib.import_module(module_name)
    return str(getattr(module, attr, "unknown"))


def check_import(module_name: str, attr: str = "__version__") -> CheckResult:
    try:
        version = _module_version(module_name, attr)
    except Exception as exc:
        return CheckResult(module_name, False, str(exc))
    return CheckResult(module_name, True, version)


def check_torch(require_gpu: bool = False) -> CheckResult:
    try:
        import torch

        cuda_ok = torch.cuda.is_available()
        gpu_count = torch.cuda.device_count()
        detail = f"torch={torch.__version__}, cuda={cuda_ok}, gpu_count={gpu_count}"
        return CheckResult("torch/cuda", cuda_ok or not require_gpu, detail)
    except Exception as exc:
        return CheckResult("torch/cuda", False, str(exc))


def check_python_for_appworld() -> CheckResult:
    version = sys.version.split()[0]
    ok = sys.version_info >= (3, 11)
    detail = f"python={version}; AppWorld requires Python 3.11+"
    return CheckResult("python>=3.11", ok, detail)


def check_pydantic_for_appworld() -> CheckResult:
    try:
        import pydantic

        version = getattr(pydantic, "VERSION", getattr(pydantic, "__version__", "unknown"))
        major = int(str(version).split(".", maxsplit=1)[0])
        ok = major < 2
        detail = f"pydantic={version}; AppWorld expects <2"
        return CheckResult("pydantic<2", ok, detail)
    except Exception as exc:
        return CheckResult("pydantic<2", False, str(exc))


def check_click_for_appworld() -> CheckResult:
    try:
        import click

        version = click.__version__
        ok = version.startswith("8.1.")
        detail = f"click={version}; recommended 8.1.7 for AppWorld"
        return CheckResult("click==8.1.7", ok, detail)
    except Exception as exc:
        return CheckResult("click==8.1.7", False, str(exc))


def check_appworld(require_data: bool = False) -> CheckResult:
    try:
        from appworld import load_task_ids

        task_ids = load_task_ids("train")
        ok = bool(task_ids) or not require_data
        detail = f"train_tasks={len(task_ids)}, sample={task_ids[:3]}"
        return CheckResult("appworld data", ok, detail)
    except Exception as exc:
        return CheckResult("appworld data", False, str(exc))


def check_rcmf_pytest() -> CheckResult:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return CheckResult("pytest", False, str(exc))
    output = (result.stdout + "\n" + result.stderr).strip()
    last_line = output.splitlines()[-1] if output else ""
    return CheckResult("pytest", result.returncode == 0, last_line)


def print_results(results: list[CheckResult]) -> int:
    width = max(len(result.name) for result in results)
    failed = 0
    for result in results:
        status = "OK" if result.ok else "FAIL"
        if not result.ok:
            failed += 1
        print(f"[{status}] {result.name:<{width}}  {result.detail}")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Check RCMF/AppWorld/Lambda environment health.")
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument("--require-appworld-data", action="store_true")
    parser.add_argument("--run-pytest", action="store_true")
    args = parser.parse_args()

    print(f"python={sys.version.split()[0]} platform={platform.platform()}")
    results: list[CheckResult] = [
        check_import("rcmf"),
        check_import("transformers"),
        check_import("safetensors"),
        check_import("yaml"),
        check_torch(require_gpu=args.require_gpu),
        check_python_for_appworld(),
        check_pydantic_for_appworld(),
        check_click_for_appworld(),
        check_appworld(require_data=args.require_appworld_data),
    ]
    if args.run_pytest:
        results.append(check_rcmf_pytest())
    failed = print_results(results)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
