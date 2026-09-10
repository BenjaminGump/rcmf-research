from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.benchmarks.webshop.adapter import load_task_catalog
from rcmf.pipeline.manifests import content_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("validation", "standard200"), required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--method-config", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--runtime-identity", type=Path, required=True)
    parser.add_argument("--validation-decision", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = _object(args.source_manifest)
    source_identity = source.get("source_identity")
    if not isinstance(source_identity, Mapping) or source_identity.get("source_commit") != _head():
        raise RuntimeError("evaluation lock source commit differs")
    config = _object(args.method_config)
    freeze = _object(args.freeze_manifest)
    if freeze.get("status") != "FROZEN_BEFORE_STANDARD200":
        raise RuntimeError("WebShop method package is not frozen")
    package_path = Path(str(freeze["method_package"])).resolve(strict=True)
    if freeze.get("method_package_sha256") != sha256_file(package_path):
        raise RuntimeError("WebShop frozen method package hash differs")
    if args.scope == "validation":
        spec = config["validation"]
        split = "validation"
        decision = None
    else:
        spec = config["formal_evaluation"]
        split = "standard200"
        if args.validation_decision is None:
            raise ValueError("standard200 lock requires a validation decision")
        decision = _object(args.validation_decision)
        if (
            decision.get("decision") != "PROCEED"
            or decision.get("method_package_sha256") != freeze["method_package_sha256"]
            or decision.get("standard200_outcome_inspected") is not False
        ):
            raise RuntimeError("validation decision does not authorize the frozen method")
    start = int(spec["index_start"])
    end = int(spec["index_end"])
    tasks = load_task_catalog(args.task_catalog)[split]
    selected = [task for task in tasks if start <= int(task.metadata["index"]) < end]
    if len(selected) != end - start or len(selected) != int(spec["count"]):
        raise RuntimeError("evaluation lock task range differs from the frozen config")
    lock = {
        "format": "rcmf_agentbench_fc_webshop_evaluation_lock_v1",
        "scope": args.scope,
        "source_commit": _head(),
        "source_manifest_sha256": sha256_file(args.source_manifest),
        "method_config_sha256": sha256_file(args.method_config),
        "freeze_manifest_sha256": sha256_file(args.freeze_manifest),
        "method_package": str(package_path),
        "method_package_sha256": sha256_file(package_path),
        "task_catalog_sha256": sha256_file(args.task_catalog),
        "runtime_identity_sha256": sha256_file(args.runtime_identity),
        "split": split,
        "index_start": start,
        "index_end": end,
        "task_count": len(selected),
        "ordered_task_ids": [task.task_id for task in selected],
        "ordered_task_manifest_sha256": content_sha256(
            [
                {
                    "task_id": task.task_id,
                    "index": task.metadata["index"],
                    "instruction_sha256": task.source_identity["instruction_sha256"],
                }
                for task in selected
            ]
        ),
        "conditions": list(spec["conditions"]),
        "condition_order": ["B0", "RCMF-C", "RCMF-S"],
        "max_rounds": int(config["formal_evaluation"]["max_rounds"]),
        "max_new_tokens": int(config["formal_evaluation"]["max_new_tokens"]),
        "do_sample": bool(config["formal_evaluation"]["do_sample"]),
        "evaluation_seed": int(config["formal_evaluation"]["evaluation_seed"]),
        "validation_decision_sha256": (
            sha256_file(args.validation_decision) if args.validation_decision else None
        ),
        "controls_frozen_together": True,
        "standard200_outcome_inspected": False,
        "excluded_200_499_execution_authorized": False,
        "status": "FROZEN_BEFORE_OUTCOMES",
    }
    lock["lock_sha256"] = content_sha256(lock)
    atomic_write_json(args.output, lock)
    print(json.dumps(lock, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
