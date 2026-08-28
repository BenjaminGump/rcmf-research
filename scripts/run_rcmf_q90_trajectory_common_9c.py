"""Shared frozen full-trajectory infrastructure for EXP-031C."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import statistics
import time
from typing import Any

import torch
from torch import Tensor

from rcmf.training.rcmf_q90_full_trajectory_9c import (
    Q90_CALIBRATION_SHA256,
    Q90_TAU,
    read_original_slots,
    read_q90_slots,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    LiveFieldQueryEncoder,
    _build_backend,
    _build_components,
    _condition_root,
    _run_task,
    _task_output,
)


GLOBAL_SEED = 25101
TASK_RESULT_VERSION = "rcmf_q90_full_trajectory_task_9c_v1"
CONDITION_SUMMARY_VERSION = "rcmf_q90_full_trajectory_condition_summary_9c_v1"
MANIFEST_VERSION = "rcmf_q90_full_trajectory_manifest_9c_v1"

CONDITION_NAMES = {
    "H0": "bare_zero_field",
    "H1": "original_correct_401_field",
    "H2": "original_key_payload_shuffle_401_field",
    "H3": "q90_correct_401_field",
    "H4": "q90_key_payload_shuffle_401_field",
    "Q1": "q90_correct_499_field",
    "Q2": "q90_key_payload_shuffle_499_field",
    "E1": "q90_determinism_repeat_one",
    "E2": "q90_determinism_repeat_two",
}
CONDITION_SPECS = {
    "H1": {"candidate": "G100", "field_control": "correct"},
    "H2": {"candidate": "G100", "field_control": "key_payload_shuffle"},
    "H3": {"candidate": "Q90", "field_control": "correct"},
    "H4": {"candidate": "Q90", "field_control": "key_payload_shuffle"},
    "Q1": {"candidate": "Q90", "field_control": "correct"},
    "Q2": {"candidate": "Q90", "field_control": "key_payload_shuffle"},
    "E1": {"candidate": "Q90", "field_control": "correct"},
    "E2": {"candidate": "Q90", "field_control": "correct"},
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_or_validate_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if load_json(path) != dict(payload):
            raise ValueError(f"Existing immutable JSON differs: {path}")
        return
    atomic_write_json(path, dict(payload))


def trajectory_paths(
    artifact_dir: Path,
    *,
    scope: str,
    field_path: Path,
    field_provenance_path: Path,
) -> dict[str, Path]:
    root = artifact_dir / scope
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "preflight": root / "runtime_preflight.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": field_path,
        "instant_add": field_provenance_path,
        "final": root / "final_summary.json",
    }


class FrozenTrajectoryFieldRuntime:
    """Read a frozen 401/499 whole-bank field with original or locked Q90 algebra."""

    def __init__(
        self,
        *,
        settings_9a: Mapping[str, Any],
        settings_9c: Mapping[str, Any],
        backend: Any,
        memory_count: int,
        condition_specs: Mapping[str, Mapping[str, str]],
    ) -> None:
        self.backend = backend
        self.memory_count = int(memory_count)
        self.condition_specs = {key: dict(value) for key, value in condition_specs.items()}
        immutable = settings_9c["immutable_exp031a"]
        checkpoint_path = Path(str(immutable["checkpoint"]))
        if sha256_file(checkpoint_path) != str(immutable["checkpoint_sha256"]):
            raise ValueError("Immutable EXP-031A checkpoint SHA differs")
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        _, self.reader = _build_components(backend.device)
        self.reader.load_state_dict(checkpoint["reader_state_dict"])
        self.reader.eval()
        for parameter in self.reader.parameters():
            parameter.requires_grad_(False)
        self.query_encoder = LiveFieldQueryEncoder(settings=settings_9a, backend=backend)

        if self.memory_count == 401:
            correct_path = Path(str(immutable["heldout_correct_field"]))
            shuffle_path = Path(str(immutable["heldout_shuffle_field"]))
            expected = {
                "correct": str(immutable["heldout_correct_field_sha256"]),
                "key_payload_shuffle": str(immutable["heldout_shuffle_field_sha256"]),
            }
            paths = {"correct": correct_path, "key_payload_shuffle": shuffle_path}
            payloads = {
                control: torch.load(path, map_location="cpu", weights_only=False)
                for control, path in paths.items()
            }
            tensors = {
                control: (payload["A"], payload["B"]) for control, payload in payloads.items()
            }
        elif self.memory_count == 499:
            deployment = Path(str(immutable["deployment_field"]))
            expected = {
                "correct": str(immutable["deployment_field_sha256"]),
                "key_payload_shuffle": str(immutable["deployment_field_sha256"]),
            }
            paths = {"correct": deployment, "key_payload_shuffle": deployment}
            payload = torch.load(deployment, map_location="cpu", weights_only=False)
            payloads = {"correct": payload, "key_payload_shuffle": payload}
            tensors = {
                "correct": (payload["A"], payload["B"]),
                "key_payload_shuffle": (payload["shuffled_A"], payload["shuffled_B"]),
            }
        else:
            raise ValueError("EXP-031C supports only the locked 401/499 fields")

        self.field_paths = paths
        self.field_sha256 = {}
        self.fields = {}
        for control, path in paths.items():
            actual = sha256_file(path)
            if actual != expected[control]:
                raise ValueError(f"Frozen {control} field SHA differs")
            payload = payloads[control]
            if int(payload["memory_count"]) != self.memory_count:
                raise ValueError("Frozen field memory count differs")
            if str(payload["checkpoint_sha256"]) != str(
                immutable["checkpoint_sha256"]
            ):
                raise ValueError("Frozen field checkpoint identity differs")
            if "reader_state_dict" in payload:
                reader_state = payload["reader_state_dict"]
                checkpoint_reader = checkpoint["reader_state_dict"]
                if set(reader_state) != set(checkpoint_reader) or any(
                    not torch.equal(reader_state[key], checkpoint_reader[key])
                    for key in checkpoint_reader
                ):
                    raise ValueError("Deployment reader differs from checkpoint")
            A, B = tensors[control]
            if tuple(A.shape) != (960, 8, 256) or tuple(B.shape) != (8, 256):
                raise ValueError("Frozen field tensor shape differs")
            self.field_sha256[control] = actual
            self.fields[control] = (
                A.to(backend.device, torch.float32),
                B.to(backend.device, torch.float32),
            )
        self.identity = {
            "format": "rcmf_q90_trajectory_runtime_identity_9c_v1",
            "checkpoint_sha256": str(immutable["checkpoint_sha256"]),
            "reader_sha256": str(checkpoint["reader_sha256"]),
            "query_encoder_sha256": self.query_encoder.identity_sha256,
            "memory_count": self.memory_count,
            "field_sha256": self.field_sha256,
            "condition_specs": self.condition_specs,
            "q90_tau": Q90_TAU,
            "q90_calibration_sha256": Q90_CALIBRATION_SHA256,
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }
        self.identity["identity_sha256"] = canonical_sha256(self.identity)

    def field_path(self, condition: str) -> Path:
        return self.field_paths[str(self.condition_specs[condition]["field_control"])]

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        spec = self.condition_specs.get(condition)
        if spec is None:
            raise ValueError(f"Unknown non-bare EXP-031C condition: {condition}")
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        A, B = self.fields[str(spec["field_control"])]
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        if str(spec["candidate"]) == "Q90":
            slots, read_audit = read_q90_slots(query=query, A=A, B=B)
        elif str(spec["candidate"]) == "G100":
            slots, read_audit = read_original_slots(query=query, A=A, B=B)
        else:
            raise ValueError("EXP-031C condition uses an unauthorized candidate")
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        return slots, {
            "state_views": views,
            "query": query,
            "query_seconds": query_seconds,
            "field_read_seconds": time.perf_counter() - started,
            "field_control": str(spec["field_control"]),
            "field_candidate": str(spec["candidate"]),
            **read_audit,
        }


def build_manifest(
    *,
    scope: str,
    task_ids: Sequence[str],
    conditions: Sequence[str],
    memory_count: int,
    config_sha256: str,
    field_sha256: Mapping[str, str],
    data_manifest_sha256: str,
) -> dict[str, Any]:
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Duplicate task ID")
    if len(set(conditions)) != len(conditions):
        raise ValueError("Duplicate condition")
    rows = [
        {
            "condition": condition,
            "condition_name": CONDITION_NAMES[condition],
            "task_id": task_id,
            "memory_count": 0 if condition == "H0" else int(memory_count),
            "candidate": "zero" if condition == "H0" else CONDITION_SPECS[condition]["candidate"],
            "field_control": "zero"
            if condition == "H0"
            else CONDITION_SPECS[condition]["field_control"],
            "student_prompt_contains_raw_memory": False,
            "runtime_memory_retrieval": False,
            "runtime_per_memory_scoring": False,
        }
        for condition in conditions
        for task_id in task_ids
    ]
    payload = {
        "format": MANIFEST_VERSION,
        "scope": scope,
        "global_seed": GLOBAL_SEED,
        "task_ids": list(task_ids),
        "task_count": len(task_ids),
        "conditions": list(conditions),
        "condition_count": len(conditions),
        "logical_task_condition_count": len(rows),
        "memory_count": int(memory_count),
        "config_sha256": config_sha256,
        "field_sha256": dict(field_sha256),
        "data_manifest_sha256": data_manifest_sha256,
        "rows": rows,
        "frozen_before_generation": True,
        "outcomes_used": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def load_frozen_backend(config: Any) -> Any:
    backend = _build_backend(config)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("EXP-031C loaded trainable Qwen parameters")
    return backend


def run_condition_tasks(
    *,
    task_ids: Sequence[str],
    condition: str,
    settings_9a: Mapping[str, Any],
    backend: Any,
    runtime: FrozenTrajectoryFieldRuntime | None,
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    config_sha256: str,
    attempt_id: str,
    memory_count: int,
    field_provenance_path: Path,
    smoke: bool = False,
    max_steps_override: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    rows, resumed = [], 0
    is_bare = condition == "H0"
    field_path = (
        paths["deployment"] if is_bare else runtime.field_path(condition)  # type: ignore[union-attr]
    )
    for task_id in task_ids:
        row, reused = _run_task(
            task_id=task_id,
            condition=condition,
            settings=settings_9a,
            backend=backend,
            runtime=runtime,
            paths=paths,
            manifest=manifest,
            config_sha256=config_sha256,
            attempt_id=attempt_id,
            smoke=smoke,
            result_version=TASK_RESULT_VERSION,
            extra_result_fields={
                "exp031c_candidate": "zero" if is_bare else CONDITION_SPECS[condition]["candidate"],
                "q90_tau": None
                if is_bare or CONDITION_SPECS[condition]["candidate"] != "Q90"
                else Q90_TAU,
                "q90_calibration_sha256": None
                if is_bare or CONDITION_SPECS[condition]["candidate"] != "Q90"
                else Q90_CALIBRATION_SHA256,
            },
            bare_condition=is_bare,
            condition_name=CONDITION_NAMES[condition],
            memory_count=0 if is_bare else memory_count,
            field_artifact_path=field_path,
            field_provenance_path=field_provenance_path,
            max_steps_override=max_steps_override,
            experiment_prefix="exp031c",
        )
        rows.append(row)
        resumed += int(reused)
    return rows, resumed


def summarize_condition(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    success_ids = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    counts: Counter[str] = Counter()
    query_seconds, read_seconds, confidences, raw_rms, residual_norms = [], [], [], [], []
    for row in rows:
        counts.update(row["counts"])
        for step in row["steps"]:
            field = step["field"]
            query_seconds.append(float(field["query_seconds"]))
            read_seconds.append(float(field["field_read_seconds"]))
            if field.get("q90_confidence") is not None:
                confidences.append(float(field["q90_confidence"]))
            if field.get("raw_field_rms") is not None:
                raw_rms.append(float(field["raw_field_rms"]))
            for values in step["reader_audit"].get("delta_norms", {}).values():
                residual_norms.extend(float(value) for value in values)
    return {
        "format": CONDITION_SUMMARY_VERSION,
        "condition": condition,
        "condition_name": CONDITION_NAMES[condition],
        "task_count": len(rows),
        "success_count": len(success_ids),
        "success_ids": success_ids,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in rows),
        "total_generated_tokens": sum(
            int(row["usage"].get("completion_tokens", 0)) for row in rows
        ),
        "counts": dict(counts),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "mean_query_seconds": statistics.fmean(query_seconds) if query_seconds else 0.0,
        "mean_field_read_seconds": statistics.fmean(read_seconds) if read_seconds else 0.0,
        "mean_q90_confidence": statistics.fmean(confidences) if confidences else None,
        "mean_raw_field_rms": statistics.fmean(raw_rms) if raw_rms else None,
        "mean_reader_residual_norm": (statistics.fmean(residual_norms) if residual_norms else 0.0),
        "passed_infrastructure": all(
            row["status"] == "complete"
            and row["success_source"] == "evaluation.success"
            and row["raw_audit_complete"]
            for row in rows
        ),
        "single_seed_descriptive_not_statistical": True,
    }


def task_output(paths: Mapping[str, Path], condition: str, task_id: str) -> Path:
    return _task_output(paths, condition, task_id, False)


def condition_summary_path(paths: Mapping[str, Path], condition: str) -> Path:
    return _condition_root(paths, condition, False) / "summary.json"


def first_divergence(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any] | None:
    left_steps, right_steps = list(left["steps"]), list(right["steps"])
    for index in range(max(len(left_steps), len(right_steps))):
        if index >= len(left_steps) or index >= len(right_steps):
            return {
                "step_id": index + 1,
                "reason": "trajectory_length",
                "left_steps": len(left_steps),
                "right_steps": len(right_steps),
            }
        lrow, rrow = left_steps[index], right_steps[index]
        differing = [
            key
            for key in (
                "rendered_message_sha256",
                "generated_token_ids",
                "exact_executed_code",
                "complete_environment_observation",
                "task_completed_status",
            )
            if lrow.get(key) != rrow.get(key)
        ]
        if differing:
            return {"step_id": index + 1, "reason": "step_content", "fields": differing}
    if bool(left["success"]) != bool(right["success"]):
        return {"step_id": None, "reason": "evaluation_success"}
    return None


def deterministic_task_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "task_id": left["task_id"] == right["task_id"],
        "success": left["success"] == right["success"],
        "step_count": left["step_count"] == right["step_count"],
        "prompts": [row["rendered_message_sha256"] for row in left["steps"]]
        == [row["rendered_message_sha256"] for row in right["steps"]],
        "token_ids": [row["generated_token_ids"] for row in left["steps"]]
        == [row["generated_token_ids"] for row in right["steps"]],
        "code": [row["exact_executed_code"] for row in left["steps"]]
        == [row["exact_executed_code"] for row in right["steps"]],
        "observations": [row["complete_environment_observation"] for row in left["steps"]]
        == [row["complete_environment_observation"] for row in right["steps"]],
    }
    return {"passed": all(checks.values()), "checks": checks}
