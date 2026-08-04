from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from common import git_info, repo_root, run_cmd


REQUIRED_FILES = [
    "AGENTS.md",
    "REPO_MAP.md",
    "research/CHATGPT_ENTRYPOINT.md",
    "research/ARCHITECTURE.md",
    "research/CURRENT_STATE.md",
    "research/EVALUATION_CONTRACT.md",
    "research/DECISIONS.md",
    "research/FAILURE_ANALYSIS.md",
    "research/NEXT_EXPERIMENTS.md",
    "research/EXPERIMENT_SCHEMA.md",
    "research/experiments.jsonl",
]

FORBIDDEN_TRACKED_SUFFIXES = {
    ".safetensors",
    ".bin",
    ".pt",
    ".pth",
    ".ckpt",
    ".pem",
    ".key",
}

SECRET_PATTERN = re.compile(
    r"(api[_-]?key|access[_-]?token|secret[_-]?key|BEGIN [A-Z ]*PRIVATE KEY)",
    re.IGNORECASE,
)


def _tracked_files(root: Path) -> list[Path]:
    output = run_cmd(["git", "ls-files"], cwd=root)
    return [root / line for line in output.splitlines() if line.strip()]


def _validate_jsonl(path: Path, errors: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_no}: invalid JSONL: {exc}")
                continue
            if not isinstance(row, dict):
                errors.append(f"{path}:{line_no}: row is not an object")
                continue
            run_id = row.get("run_id")
            if not run_id:
                errors.append(f"{path}:{line_no}: missing run_id")
            elif str(run_id) in seen:
                errors.append(f"{path}:{line_no}: duplicate run_id {run_id}")
            else:
                seen.add(str(run_id))
            metric = row.get("primary_metric")
            if isinstance(metric, dict):
                denominator = metric.get("denominator")
                numerator = metric.get("numerator")
                if denominator is None or numerator is None:
                    errors.append(f"{path}:{line_no}: primary_metric missing numerator/denominator")
            rows.append(row)
    return rows


def main() -> int:
    root = repo_root()
    errors: list[str] = []
    warnings: list[str] = []

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"missing required file: {rel}")

    experiments = root / "research" / "experiments.jsonl"
    rows: list[dict[str, object]] = []
    if experiments.exists():
        rows = _validate_jsonl(experiments, errors)

    for row in rows:
        summary = row.get("result_summary")
        if summary and not (root / str(summary)).exists():
            warnings.append(f"result summary missing for {row.get('run_id')}: {summary}")

    for path in _tracked_files(root):
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if suffix in FORBIDDEN_TRACKED_SUFFIXES:
            errors.append(f"forbidden tracked artifact: {rel}")
        if rel in {".env"} or rel.startswith(".env."):
            errors.append(f"tracked environment/secret file: {rel}")
        if path.is_file() and path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"tracked file over 10MB: {rel}")
        if path.is_file() and path.stat().st_size < 1024 * 1024:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if SECRET_PATTERN.search(text):
                warnings.append(f"possible secret-like text in tracked file: {rel}")

    info = git_info(root)
    print(f"root={root}")
    print(f"branch={info['branch']}")
    print(f"commit={info['commit']}")
    print(f"dirty={info['dirty']}")
    print(f"experiments={len(rows)}")

    if warnings:
        print("\nWARNINGS:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("\nERRORS:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("validation=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
