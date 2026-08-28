from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    read_compiled_field,
    tensor_sha256,
)
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    GLOBAL_SEED,
    classify_task,
    strict_no_progress_loops,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import _build_components
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    LiveFieldQueryEncoder,
    _run_task,
)
from scripts.run_rcmf_q90_trajectory_common_9c import (
    deterministic_task_match,
    load_frozen_backend,
)


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
RESULT_FORMAT = "rcmf_onpolicy_trajectory_task_10a_v1"
CONDITIONS = {
    "T0": "bare_zero_field",
    "T1": "original_correct_task_legal_field",
    "T2": "original_key_payload_shuffle_task_legal_field",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("determinism", "run", "finalize"), required=True)
    parser.add_argument("--condition", choices=tuple(CONDITIONS))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_rollouts")
    return parser.parse_args()


def paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "rollouts"
    return {
        "root": root,
        "manifest": root / "rollout_manifest.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": artifact_dir / "preflight/task_legal_fields.pt",
        "instant_add": artifact_dir / "preflight/task_legal_field_report.json",
        "field_provenance": artifact_dir / "preflight/task_legal_field_report.json",
        "determinism": root / "determinism/determinism_report.json",
        "final": root / "rollout_summary.json",
    }


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in read_jsonl(path)
        if row.get("attempt_id") is not None
    }


class TaskLegalFieldRuntime:
    """Frozen G100 read over precompiled task-legal fields, with no memory scan."""

    def __init__(
        self,
        *,
        settings_9a: Mapping[str, Any],
        settings: Mapping[str, Any],
        backend: Any,
        field_path: Path,
    ) -> None:
        self.backend = backend
        self._field_path = field_path
        payload = torch.load(field_path, map_location="cpu", weights_only=False)
        self.task_ids = tuple(str(value) for value in payload["train_task_ids"])
        self.train_memory_count = int(payload["train_memory_count"])
        checkpoint_path = Path(str(settings["immutable_exp031a"]["checkpoint"]))
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        _, self.reader = _build_components(backend.device)
        self.reader.load_state_dict(checkpoint["reader_state_dict"])
        self.reader.eval()
        for parameter in self.reader.parameters():
            parameter.requires_grad_(False)
        self.query_encoder = LiveFieldQueryEncoder(settings=settings_9a, backend=backend)
        self.controls: dict[str, Any] = {}
        for control in ("correct", "key_payload_shuffle"):
            row = payload["fields"][control]
            self.controls[control] = {
                "A_total": row["A_total"].to(backend.device, torch.float32),
                "B_total": row["B_total"].to(backend.device, torch.float32),
                "task_contributions": {
                    task_id: {
                        "A": values["A"].to(backend.device, torch.float32),
                        "B": values["B"].to(backend.device, torch.float32),
                        "memory_count": int(values["memory_count"]),
                    }
                    for task_id, values in row["task_contributions"].items()
                },
            }
        self.current_task_id: str | None = None
        self.current_fields: dict[str, tuple[Tensor, Tensor]] = {}
        self.current_memory_count = 0
        self.identity = {
            "format": "rcmf_task_legal_runtime_identity_10a_v1",
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "task_field_sha256": sha256_file(field_path),
            "query_encoder_sha256": self.query_encoder.identity_sha256,
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }
        self.identity["identity_sha256"] = canonical_sha256(self.identity)

    def set_task(self, task_id: str) -> None:
        if task_id not in self.task_ids:
            raise ValueError(f"Unknown model-training task: {task_id}")
        self.current_task_id = task_id
        counts = set()
        self.current_fields = {}
        for control, row in self.controls.items():
            contribution = row["task_contributions"][task_id]
            self.current_fields[control] = (
                row["A_total"] - contribution["A"],
                row["B_total"] - contribution["B"],
            )
            counts.add(self.train_memory_count - int(contribution["memory_count"]))
        if len(counts) != 1:
            raise RuntimeError("Correct and shuffle task-legal memory counts differ")
        self.current_memory_count = counts.pop()

    def field_path(self, condition: str) -> Path:
        del condition
        return self._field_path

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        if self.current_task_id is None:
            raise RuntimeError("TaskLegalFieldRuntime.set_task must precede read")
        control = "correct" if condition == "T1" else "key_payload_shuffle"
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        A, B = self.current_fields[control]
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        return slots, {
            "state_views": views,
            "query": query,
            "query_seconds": query_seconds,
            "field_read_seconds": time.perf_counter() - started,
            "field_control": control,
            "task_legal_query_task_id": self.current_task_id,
            "task_legal_memory_count": self.current_memory_count,
            "field_A_sha256": tensor_sha256(A),
            "field_B_sha256": tensor_sha256(B),
            "raw_field_rms": float(
                (B + torch.einsum("k,ksp->sp", query.to(A.dtype), A))
                .float()
                .square()
                .mean()
                .sqrt()
                .cpu()
            ),
        }


def _condition_summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    success_ids = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    loops = {str(row["task_id"]): strict_no_progress_loops(row) for row in rows}
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row["counts"])
    return {
        "format": "rcmf_onpolicy_rollout_condition_summary_10a_v1",
        "condition": condition,
        "condition_name": CONDITIONS[condition],
        "task_count": len(rows),
        "success_count": len(success_ids),
        "success_ids": success_ids,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in rows),
        "total_generated_tokens": sum(
            int(row["usage"].get("completion_tokens", 0)) for row in rows
        ),
        "counts": dict(counts),
        "strict_no_progress_loop_count": sum(len(value) for value in loops.values()),
        "strict_no_progress_loops": loops,
        "passed_infrastructure": len(rows) == 29
        and all(row["status"] == "complete" and row["raw_audit_complete"] for row in rows),
    }


def _run_condition(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path]
) -> dict[str, Any]:
    if args.condition is None:
        raise ValueError("--condition is required for phase run")
    order = ("T0", "T1", "T2")
    index = order.index(args.condition)
    if index and not (p["root"] / "conditions" / order[index - 1] / "summary.json").exists():
        raise RuntimeError("Rollout conditions must run sequentially T0/T1/T2")
    manifest = _json(p["manifest"])
    task_ids = [str(value) for value in manifest["task_ids"]]
    backend = load_frozen_backend(cfg)
    runtime = None
    if args.condition != "T0":
        runtime = TaskLegalFieldRuntime(
            settings_9a=cfg.raw["stage_c_9a"],
            settings=settings,
            backend=backend,
            field_path=p["deployment"],
        )
    rows = []
    for task_id in task_ids:
        if runtime is not None:
            runtime.set_task(task_id)
        row, _ = _run_task(
            task_id=task_id,
            condition=args.condition,
            settings=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            paths=p,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            smoke=False,
            result_version=RESULT_FORMAT,
            extra_result_fields={
                "exp032a_rollout": True,
                "same_task_memory_excluded": True,
                "task_legal_memory_count": 0
                if runtime is None
                else runtime.current_memory_count,
            },
            bare_condition=args.condition == "T0",
            condition_name=CONDITIONS[args.condition],
            memory_count=0 if runtime is None else runtime.current_memory_count,
            field_artifact_path=p["deployment"],
            field_provenance_path=p["field_provenance"],
            experiment_prefix="exp032a",
        )
        rows.append(row)
    summary = _condition_summary(rows, args.condition)
    atomic_write_json(p["root"] / "conditions" / args.condition / "summary.json", summary)
    return summary


def _determinism(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], p: Mapping[str, Path]
) -> dict[str, Any]:
    manifest = _json(p["manifest"])
    task_id = str(manifest["task_ids"][0])
    backend = load_frozen_backend(cfg)
    rows = []
    for condition in ("DTA", "DTB"):
        row, _ = _run_task(
            task_id=task_id,
            condition=condition,
            settings=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=None,
            paths=p,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            smoke=False,
            result_version=RESULT_FORMAT,
            extra_result_fields={"exp032a_determinism": True},
            bare_condition=True,
            condition_name="determinism_bare_repeat",
            memory_count=0,
            field_artifact_path=p["deployment"],
            field_provenance_path=p["field_provenance"],
            experiment_prefix="exp032a_determinism",
        )
        rows.append(row)
    comparison = deterministic_task_match(rows[0], rows[1])
    result = {
        "format": "rcmf_onpolicy_complete_task_determinism_10a_v1",
        "task_id": task_id,
        "comparison": comparison,
        "complete_task": True,
        "passed": bool(comparison["passed"]),
    }
    if not result["passed"]:
        raise RuntimeError("EXP-032A complete-task determinism failed")
    atomic_write_json(p["determinism"], result)
    return result


def _finalize(p: Mapping[str, Path]) -> dict[str, Any]:
    summaries = {
        condition: _json(p["root"] / "conditions" / condition / "summary.json")
        for condition in CONDITIONS
    }
    manifest = _json(p["manifest"])
    task_rows = []
    class_counts: Counter[str] = Counter()
    for task_id in manifest["task_ids"]:
        results = {
            condition: _json(
                p["root"] / "conditions" / condition / "task_results" / f"{task_id}.json"
            )
            for condition in CONDITIONS
        }
        task_class = classify_task(
            bare_success=bool(results["T0"]["success"]),
            rcmf_success=bool(results["T1"]["success"]),
        )
        class_counts[task_class] += 1
        task_rows.append(
            {
                "task_id": task_id,
                "task_class": task_class,
                "success": {key: bool(value["success"]) for key, value in results.items()},
                "step_count": {key: int(value["step_count"]) for key, value in results.items()},
                "strict_no_progress_loop_count": {
                    key: len(strict_no_progress_loops(value)) for key, value in results.items()
                },
            }
        )
    payload = {
        "format": "rcmf_onpolicy_rollout_summary_10a_v1",
        "run_uuid": RUN_UUID,
        "condition_summaries": summaries,
        "task_class_counts": dict(class_counts),
        "task_rows": task_rows,
        "trajectory_count": 87,
        "all_detailed_audits_complete": all(
            bool(row["passed_infrastructure"]) for row in summaries.values()
        ),
    }
    payload["summary_sha256"] = canonical_sha256(payload)
    atomic_write_json(p["final"], payload)
    return payload


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    p = paths(args.artifact_dir)
    for required in ("manifest", "deployment", "field_provenance"):
        if not p[required].exists():
            raise FileNotFoundError(p[required])
    hashes = {
        "run_manifest": sha256_file(args.artifact_dir / "run_manifest.json"),
        "rollout_manifest": sha256_file(p["manifest"]),
        "task_fields": sha256_file(p["deployment"]),
        "field_provenance": sha256_file(p["field_provenance"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"exp032a_rollout_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "determinism":
            result = _determinism(args=args, cfg=cfg, settings=settings, p=p)
            latest = p["determinism"]
        elif args.phase == "run":
            if not p["determinism"].exists() or not bool(_json(p["determinism"])["passed"]):
                raise RuntimeError("Complete-task determinism gate has not passed")
            result = _run_condition(args=args, cfg=cfg, settings=settings, p=p)
            latest = p["root"] / "conditions" / str(args.condition) / "summary.json"
        else:
            result = _finalize(p)
            latest = p["final"]
        attempt.progress(
            status=f"rollout_{args.phase}_complete",
            latest_validated_checkpoint=str(latest),
        )
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
