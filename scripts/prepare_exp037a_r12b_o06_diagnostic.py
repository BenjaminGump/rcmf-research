from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
import yaml

from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.audit_exp037a_14j_first_divergence import _inventory_hash


DIAGNOSTIC_ID = "rcmf_exp037a_r12b_o06_integration_20260905_001"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    formal_root = args.formal_root.resolve()
    source_root = args.source_root.resolve()
    diagnostic_root = args.diagnostic_root.resolve()
    if diagnostic_root == formal_root or formal_root in diagnostic_root.parents:
        raise ValueError("Diagnostic root cannot be inside the sealed formal root")
    if diagnostic_root.exists():
        raise FileExistsError(f"Diagnostic root already exists: {diagnostic_root}")
    diagnostic_root.mkdir(parents=True)
    before = _inventory_hash(formal_root)
    source_config = formal_root / "resolved_configs/arm_1d.yaml"
    payload = copy.deepcopy(load_config(source_config).raw)
    arm_root = diagnostic_root / "arm_1d"
    original_run_uuid = str(payload["stage_c_7hr"]["run_uuid"])
    original_artifact_dir = str(payload["stage_c_7hr"]["artifact_dir"])
    payload["stage_c_7hr"]["run_uuid"] = DIAGNOSTIC_ID
    payload["stage_c_7hr"]["artifact_dir"] = str(arm_root)
    config_path = diagnostic_root / "config/arm_1d.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    after = _inventory_hash(formal_root)
    if before != after:
        raise RuntimeError("Sealed 14j root changed during diagnostic preparation")
    atomic_write_json(
        diagnostic_root / "runtime_preflight.json",
        {
            "format": "exp037a_r12b_o06_integration_preflight_v1",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "diagnostic_id": DIAGNOSTIC_ID,
            "diagnostic_only": True,
            "scientific_result_eligible": False,
            "source_commit": args.source_commit,
            "source_root": str(source_root),
            "formal_fixture_root": str(formal_root),
            "formal_fixture_config": {
                "path": str(source_config),
                "sha256": sha256_file(source_config),
            },
            "diagnostic_config": {
                "path": str(config_path),
                "sha256": sha256_file(config_path),
                "changed_fields": {
                    "stage_c_7hr.run_uuid": {
                        "from": original_run_uuid,
                        "to": DIAGNOSTIC_ID,
                    },
                    "stage_c_7hr.artifact_dir": {
                        "from": original_artifact_dir,
                        "to": str(arm_root),
                    },
                },
            },
            "fixture_policy": {
                "O00_O04": "sealed_14j_read_only_engineering_fixtures",
                "O05": "fresh_rebuild_with_repaired_token_counter",
                "O06": "fresh_from_beginning_no_condition_reuse",
            },
            "hardware": "NVIDIA H100 80GB HBM3",
            "global_seed": 25101,
            "expected_wall_hours": 2.25,
            "conservative_wall_hours": 4.5,
            "plausibly_exceeds_18_hours": False,
            "basis": {
                "sealed_14j_partial_condition_count": 357,
                "sealed_14j_partial_elapsed_minutes": 44.5,
                "maximum_condition_count": 998,
            },
            "optimizer_steps": 0,
            "backward_count": 0,
            "formal_root_before": before,
            "formal_root_after": after,
            "formal_root_unchanged": True,
        },
    )
    print(config_path)


if __name__ == "__main__":
    main()
