"""Audit record-level numerical reversibility of the frozen BEST field."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_appworld_testnormal_final_13a import quantile
from rcmf.training.rcmf_joint_full_bank_9a import (
    RCMFFieldRecord,
    ReversibleRCMFField,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.benchmark_rcmf_appworld_testnormal_efficiency_13a import (
    load_cached_records,
    timed_cuda,
    timing_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_appworld_testnormal_final_13a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--manifest-source-head")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def errors(
    A: Tensor, B: Tensor, reference_A: Tensor, reference_B: Tensor
) -> dict[str, float]:
    delta_A = (A - reference_A).to(torch.float64)
    delta_B = (B - reference_B).to(torch.float64)
    reference = torch.cat((reference_A.flatten(), reference_B.flatten())).to(
        torch.float64
    )
    delta = torch.cat((delta_A.flatten(), delta_B.flatten()))
    return {
        "maximum_absolute": float(delta.abs().max().item()),
        "relative_frobenius": float(
            delta.norm().item() / max(reference.norm().item(), 1.0e-30)
        ),
        "A_maximum_absolute": float(delta_A.abs().max().item()),
        "B_maximum_absolute": float(delta_B.abs().max().item()),
    }


def reset_field(
    field: ReversibleRCMFField,
    records: list[RCMFFieldRecord],
    A: Tensor,
    B: Tensor,
) -> None:
    field.A.copy_(A)
    field.B.copy_(B)
    field.records = {row.memory_id: row for row in records}
    index: defaultdict[str, set[str]] = defaultdict(set)
    for row in records:
        index[row.parent_id].add(row.memory_id)
    field.parent_index = index


def aggregate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row[key]) for row in rows]
    return timing_summary(values)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    if git_head() != args.source_head:
        raise ValueError("EXP-036A reversibility source HEAD differs")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    run_manifest = read_json(args.artifact_dir / "run_manifest.json")
    if str(run_manifest["source_head"]) != str(
        args.manifest_source_head or args.source_head
    ):
        raise ValueError("EXP-036A reversibility manifest source differs")
    formal = read_json(args.artifact_dir / "results/formal_summary.json")
    if not bool(formal.get("evaluation_complete")) or int(
        formal.get("trajectory_count", 0)
    ) != 840:
        raise RuntimeError("Reversibility cannot precede the sealed formal run")
    attempts_path = args.artifact_dir / "attempts.jsonl"
    if attempts_path.exists() and any(
        str(json.loads(line).get("attempt_id")) == args.attempt_id
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line
    ):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    if not torch.cuda.is_available():
        raise RuntimeError("EXP-036A reversibility requires the H100 CUDA device")
    device = torch.device("cuda")
    records = load_cached_records(
        args.artifact_dir / "efficiency/cache/best_compiled_records.pt", device
    )
    if len(records) != 499:
        raise ValueError("Reversibility requires all 499 BEST records")
    deployment_path = Path(
        str(settings["packages"]["BEST"]["deployment_field"])
    )
    if sha256_file(deployment_path) != str(
        settings["packages"]["BEST"]["deployment_field_sha256"]
    ):
        raise ValueError("BEST deployment field SHA differs")
    deployment = torch.load(deployment_path, map_location="cpu", weights_only=False)
    reference_A = deployment["A"].to(device, torch.float32)
    reference_B = deployment["B"].to(device, torch.float32)
    field = ReversibleRCMFField(device=device)
    for record in records:
        field.add_memory_fast(record)
    rebuild_error = errors(field.A, field.B, reference_A, reference_B)
    tolerance = float(settings["reversibility"]["absolute_tolerance"])
    if rebuild_error["maximum_absolute"] > tolerance:
        raise RuntimeError(
            f"BEST 499-memory rebuild exceeds tolerance: {rebuild_error}"
        )

    rows: list[dict[str, Any]] = []
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="exp036a_numerical_reversibility",
        command=list(sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "deployment_field": sha256_file(deployment_path),
            "compiled_record_cache": sha256_file(
                args.artifact_dir / "efficiency/cache/best_compiled_records.pt"
            ),
        },
        parent_attempt_id="none",
        resume_checkpoint="all_499_records_atomic_result",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(
            settings["runtime"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        started = time.perf_counter()
        for index, record in enumerate(records):
            reset_field(field, records, reference_A, reference_B)
            _, remove_ms = timed_cuda(
                device, lambda record=record: field.remove_memory_fast(record.memory_id)
            )
            _, restore_ms = timed_cuda(
                device, lambda record=record: field.add_memory_fast(record)
            )
            restored = errors(field.A, field.B, reference_A, reference_B)

            empty = ReversibleRCMFField(device=device)
            _, add_empty_ms = timed_cuda(
                device, lambda record=record: empty.add_memory_fast(record)
            )
            _, remove_after_add_ms = timed_cuda(
                device,
                lambda record=record: empty.remove_memory_fast(record.memory_id),
            )
            empty_error = errors(
                empty.A,
                empty.B,
                torch.zeros_like(empty.A),
                torch.zeros_like(empty.B),
            )

            reset_field(field, records, reference_A, reference_B)
            _, replace_same_ms = timed_cuda(
                device,
                lambda record=record: field.replace_memory_fast(
                    record.memory_id, record
                ),
            )
            replace_same_error = errors(
                field.A, field.B, reference_A, reference_B
            )

            other = records[(index + 1) % len(records)]
            replacement = RCMFFieldRecord(
                memory_id=record.memory_id,
                parent_id=record.parent_id,
                parent_task_id=record.parent_task_id,
                key=other.key,
                payload=other.payload,
                rho=record.rho,
            )
            reset_field(field, records, reference_A, reference_B)
            _, replace_other_ms = timed_cuda(
                device,
                lambda record=record, replacement=replacement: field.replace_memory_fast(
                    record.memory_id, replacement
                ),
            )
            _, replace_restore_ms = timed_cuda(
                device,
                lambda record=record: field.replace_memory_fast(
                    record.memory_id, record
                ),
            )
            replace_restore_error = errors(
                field.A, field.B, reference_A, reference_B
            )
            rows.append(
                {
                    "record_index": index,
                    "memory_id": record.memory_id,
                    "remove_ms": remove_ms,
                    "restore_ms": restore_ms,
                    "restored_maximum_absolute": restored["maximum_absolute"],
                    "restored_relative_frobenius": restored[
                        "relative_frobenius"
                    ],
                    "add_to_empty_ms": add_empty_ms,
                    "remove_after_add_ms": remove_after_add_ms,
                    "empty_maximum_absolute": empty_error["maximum_absolute"],
                    "replace_same_ms": replace_same_ms,
                    "replace_same_maximum_absolute": replace_same_error[
                        "maximum_absolute"
                    ],
                    "replace_other_ms": replace_other_ms,
                    "replace_restore_ms": replace_restore_ms,
                    "replace_restore_maximum_absolute": replace_restore_error[
                        "maximum_absolute"
                    ],
                    "replace_restore_relative_frobenius": replace_restore_error[
                        "relative_frobenius"
                    ],
                }
            )
            attempt.progress(
                status="exp036a_reversibility",
                completed_units=index + 1,
                total_units=499,
                latest_validated_checkpoint=record.memory_id,
            )
            print(f"reversibility={index + 1}/499", flush=True)

        reset_field(field, records, reference_A, reference_B)
        rebuilt_A, rebuilt_B = field.audit_rebuild()
        audit_error = errors(
            rebuilt_A, rebuilt_B, reference_A, reference_B
        )
        insertion_orders = {
            "forward": list(range(499)),
            "reverse": list(reversed(range(499))),
        }
        shuffled = list(range(499))
        random.Random(25101).shuffle(shuffled)
        insertion_orders["seed_25101_permutation"] = shuffled
        insertion_results = {}
        for name, order in insertion_orders.items():
            candidate = ReversibleRCMFField(device=device)
            for position in order:
                candidate.add_memory_fast(records[position])
            insertion_results[name] = errors(
                candidate.A, candidate.B, reference_A, reference_B
            )

        result = {
            "format": "rcmf_exp036a_numerical_reversibility_13a_v1",
            "memory_count": 499,
            "deployment_field_sha256": sha256_file(deployment_path),
            "initial_rebuild_error": rebuild_error,
            "remove_ms": aggregate(rows, "remove_ms"),
            "restore_ms": aggregate(rows, "restore_ms"),
            "maximum_absolute_error": timing_summary(
                [float(row["restored_maximum_absolute"]) for row in rows]
            ),
            "relative_frobenius_error": timing_summary(
                [float(row["restored_relative_frobenius"]) for row in rows]
            ),
            "add_to_empty_ms": aggregate(rows, "add_to_empty_ms"),
            "remove_after_add_ms": aggregate(rows, "remove_after_add_ms"),
            "replace_same_ms": aggregate(rows, "replace_same_ms"),
            "replace_other_ms": aggregate(rows, "replace_other_ms"),
            "replace_restore_ms": aggregate(rows, "replace_restore_ms"),
            "audit_rebuild_error": audit_error,
            "insertion_order_sensitivity": insertion_results,
            "rows": rows,
            "wall_seconds": time.perf_counter() - started,
            "numerical_only_not_behavioral_deletion": True,
        }
        result["result_sha256"] = canonical_sha256(result)
        output = args.artifact_dir / "reversibility/reversibility_results.json"
        atomic_write_json(output, result)
        attempt.progress(
            status="exp036a_reversibility_complete",
            completed_units=499,
            total_units=499,
            latest_validated_checkpoint=str(output),
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
