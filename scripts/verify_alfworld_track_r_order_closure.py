from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from analyze_alfworld_paired_results import read_rows
from run_alfworld_agent import _order_tasks_to_frozen_manifest
from rcmf.benchmarks.alfworld.environment import REACT_PUT_MOVE_BRIDGE_ID
from rcmf.benchmarks.alfworld.execution_lock import (
    TRACK_R_EVALUATION_ORDER_SHA256,
    TRACK_R_LOCK_FILE_SHA256,
    TRACK_R_LOCK_IDENTITY_SHA256,
)
from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    GENERATION_IDENTITY_SHA256,
    MODEL_REVISION,
    TRACK_R_ID,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    TRACK_R_TASK_IDS_SHA256,
    canonical_sha256,
    load_sealed_task_manifest,
    portable_task_records,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect_rejection(callback: Callable[[], Any], phrase: str) -> bool:
    try:
        callback()
    except ValueError as exc:
        if phrase not in str(exc):
            raise AssertionError(
                f"wrong fail-closed message: expected {phrase!r}, got {str(exc)!r}"
            ) from exc
        return True
    raise AssertionError(f"expected fail-closed rejection containing {phrase!r}")


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CPU-only closure of real ALFWorld Track R order and embedded v3 identity"
    )
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite closure evidence: {output}")

    manifest_rows = load_sealed_task_manifest(args.task_manifest)
    manifest_evaluation_rows = [
        row for row in manifest_rows if row["split"] == "valid_unseen"
    ]
    manifest_ids = [str(row["task_id"]) for row in manifest_evaluation_rows]
    tasks = list(portable_task_records(manifest_rows)["valid_unseen"])
    adapter_ids = [task.task_id for task in tasks]
    if len(manifest_ids) != 134:
        raise AssertionError("real Track R manifest population is not 134")
    if canonical_sha256(sorted(manifest_ids)) != TRACK_R_TASK_IDS_SHA256:
        raise AssertionError("real Track R task-set identity differs")
    if canonical_sha256(manifest_ids) != TRACK_R_EVALUATION_ORDER_SHA256:
        raise AssertionError("real Track R physical order identity differs")

    correct = _order_tasks_to_frozen_manifest(
        tasks,
        manifest_rows,
        split="valid_unseen",
        expected_order_sha256=TRACK_R_EVALUATION_ORDER_SHA256,
    )
    wrong_rows = [
        *[row for row in manifest_rows if row["split"] != "valid_unseen"],
        *reversed(manifest_evaluation_rows),
    ]
    wrong_order_rejected = _expect_rejection(
        lambda: _order_tasks_to_frozen_manifest(
            tasks,
            wrong_rows,
            split="valid_unseen",
            expected_order_sha256=TRACK_R_EVALUATION_ORDER_SHA256,
        ),
        "evaluation order differs",
    )
    restored = _order_tasks_to_frozen_manifest(
        list(reversed(tasks)),
        manifest_rows,
        split="valid_unseen",
        expected_order_sha256=TRACK_R_EVALUATION_ORDER_SHA256,
    )

    run_identity = {
        "run_uuid": "cpu-order-closure-fixture",
        "source_commit": args.source_commit,
        "track_id": TRACK_R_ID,
        "track_role": "UPSTREAM_PROTOCOL_REFERENCE",
        "task_list_role": "formal_track_r_complete",
        "split": "valid_unseen",
        "task_ids_sha256": TRACK_R_TASK_IDS_SHA256,
        "evaluation_order_sha256": TRACK_R_EVALUATION_ORDER_SHA256,
        "condition": "bare",
        "checkpoint_sha256": None,
        "benchmark_lock_sha256": TRACK_R_LOCK_FILE_SHA256,
        "benchmark_lock_identity_sha256": TRACK_R_LOCK_IDENTITY_SHA256,
        "generation_batch_size": 16,
        "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
        "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
        "model_revision": MODEL_REVISION,
    }
    fixture_rows = [
        {
            "schema_version": "alfworld_agent_episode_v2",
            "task_id": task_id,
            "condition": "bare",
            "run_identity": dict(run_identity),
            "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
            "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
            "steps": [
                {
                    "action_valid": True,
                    "parsed_action": "put egg 1 in/on fridge 1",
                    "executed_action": "move egg 1 to fridge 1",
                    "action_dialect_bridge_applied": True,
                    "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
                }
            ],
        }
        for task_id in manifest_ids
    ]
    embedded_checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="alfworld-order-closure-") as temporary:
        fixture = Path(temporary) / "fixture.jsonl"
        _write_rows(fixture, fixture_rows)
        embedded_checks["correct_v3_identity_accepted"] = (
            len(read_rows(fixture, "bare")) == 134
        )

        mutations = {
            "wrong_embedded_order_rejected": (
                "evaluation_order_sha256",
                "0" * 64,
                "run identity evaluation order differs",
            ),
            "wrong_embedded_lock_file_rejected": (
                "benchmark_lock_sha256",
                "0" * 64,
                "benchmark-lock file differs",
            ),
            "wrong_embedded_lock_identity_rejected": (
                "benchmark_lock_identity_sha256",
                "0" * 64,
                "benchmark-lock identity differs",
            ),
            "wrong_embedded_generation_identity_rejected": (
                "generation_identity_sha256",
                "0" * 64,
                "generation/action identity differs",
            ),
            "wrong_embedded_bridge_identity_rejected": (
                "action_dialect_bridge_id",
                "wrong-bridge",
                "embedded action-dialect identity differs",
            ),
        }
        for name, (field, value, phrase) in mutations.items():
            changed = json.loads(json.dumps(fixture_rows))
            for row in changed:
                row["run_identity"][field] = value
            changed_path = Path(temporary) / f"{name}.jsonl"
            _write_rows(changed_path, changed)
            embedded_checks[name] = _expect_rejection(
                lambda path=changed_path: read_rows(path, "bare"),
                phrase,
            )

        wrong_action = json.loads(json.dumps(fixture_rows))
        wrong_action[0]["steps"][0]["executed_action"] = (
            "put egg 1 in/on fridge 1"
        )
        wrong_action_path = Path(temporary) / "wrong_action.jsonl"
        _write_rows(wrong_action_path, wrong_action)
        embedded_checks["wrong_executed_action_rejected"] = _expect_rejection(
            lambda: read_rows(wrong_action_path, "bare"),
            "executed action differs from bridge",
        )

    checks = {
        "correct_real_set_and_order_accepted": [task.task_id for task in correct]
        == manifest_ids,
        "same_real_set_wrong_order_rejected": wrong_order_rejected,
        "reversed_real_adapter_input_sorted_exactly_to_frozen_order": [
            task.task_id for task in restored
        ]
        == manifest_ids,
        **embedded_checks,
    }
    if not all(checks.values()):
        raise AssertionError(f"Track R order/identity closure failed: {checks}")
    result: dict[str, Any] = {
        "schema_version": "alfworld_track_r_real_order_closure_record_v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_uuid": args.diagnostic_uuid,
        "source_commit": args.source_commit,
        "scope": "CPU-only real 134-task order and embedded v3 identity closure; no tokenizer/model load, forward, or generation",
        "decision": "PASS_REAL_134_TASK_ORDER_AND_EMBEDDED_V3_IDENTITY_CLOSURE",
        "population": {
            "count": len(manifest_ids),
            "set_sha256": canonical_sha256(sorted(manifest_ids)),
            "manifest_order_sha256": canonical_sha256(manifest_ids),
            "adapter_original_order_sha256": canonical_sha256(adapter_ids),
            "reversed_adapter_input_order_sha256": canonical_sha256(
                list(reversed(adapter_ids))
            ),
            "sorted_helper_output_sha256": canonical_sha256(
                [task.task_id for task in restored]
            ),
            "first_task_id": manifest_ids[0],
            "last_task_id": manifest_ids[-1],
        },
        "checks": checks,
        "v3_identity": {
            "lock_file_sha256": TRACK_R_LOCK_FILE_SHA256,
            "lock_identity_sha256": TRACK_R_LOCK_IDENTITY_SHA256,
            "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
            "action_dialect_bridge_id": REACT_PUT_MOVE_BRIDGE_ID,
            "model_revision": MODEL_REVISION,
        },
        "inputs": {
            "manifest_path": str(args.task_manifest.resolve()),
            "manifest_file_sha256": _sha256_file(args.task_manifest),
        },
        "negative_statements": {
            "tokenizer_loaded": False,
            "model_weights_loaded": False,
            "model_forward": False,
            "model_generation": False,
            "evaluation_outcome_used": False,
        },
    }
    result["canonical_identity_sha256"] = canonical_sha256(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_output, output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
