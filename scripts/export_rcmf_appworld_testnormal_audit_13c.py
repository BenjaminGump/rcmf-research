"""Export EXP-036C with the frozen EXP-036A Git-safe audit implementation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import sha256_file
from scripts import export_rcmf_appworld_testnormal_audit_13a as base


RUN_UUID = "rcmf_appworld_testnormal_final_13c_20260901_002"
FORMAT = "rcmf_appworld_testnormal_git_safe_audit_13c_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def export(artifact_dir: Path, audit_root: Path, result_root: Path) -> dict[str, Any]:
    formal = read_json(artifact_dir / "results/formal_summary.json")
    analysis = read_json(artifact_dir / "analysis/paired_analysis.json")
    if formal.get("run_uuid") != RUN_UUID or analysis.get("run_uuid") != RUN_UUID:
        raise RuntimeError("EXP-036C export identity differs")
    if not bool(formal.get("evaluation_complete")) or int(
        formal.get("trajectory_count", 0)
    ) != 840:
        raise RuntimeError("EXP-036C export requires all 840 formal rows")

    base.RUN_UUID = RUN_UUID
    base.FORMAT = FORMAT
    result = base.export(artifact_dir, audit_root, result_root)

    index_path = audit_root / "index.json"
    index = read_json(index_path)
    if index.get("run_uuid") != RUN_UUID or int(index.get("trajectory_count", 0)) != 840:
        raise RuntimeError("EXP-036C exported audit index identity differs")
    result.update(
        {
            "format": "rcmf_exp036c_git_safe_export_summary_13c_v1",
            "run_uuid": RUN_UUID,
            "audit_index_sha256": sha256_file(index_path),
        }
    )
    return result


def main() -> None:
    args = parse_args()
    result = export(args.artifact_dir, args.audit_root, args.result_root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
