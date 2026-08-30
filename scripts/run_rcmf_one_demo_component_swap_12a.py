"""Run the frozen EXP-035A 4x2 heldout complete-trajectory diagnostic."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import copy
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    assert_frozen_without_gradients,
    read_compiled_field,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    CONDITIONS,
    GLOBAL_SEED,
    condition_parts,
    load_writer_reader_package,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    LiveFieldQueryEncoder,
    _build_backend,
    _run_task,
)
from scripts.run_rcmf_q90_trajectory_common_9c import deterministic_task_match


TASK_RESULT_VERSION = "rcmf_one_demo_component_swap_task_12a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_component_swap_12a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("smoke", "run", "finalize"), required=True
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_or_validate(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if read_json(path) != dict(value):
            raise ValueError(f"Existing immutable JSON differs: {path}")
        return
    atomic_write_json(path, dict(value))


def require_unique_attempt(path: Path, attempt_id: str) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and str(json.loads(line).get("attempt_id")) == attempt_id:
            raise ValueError(f"Duplicate attempt ID: {attempt_id}")


def paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "trajectories"
    return {
        "root": root,
        "manifest": artifact_dir / "manifests/condition_manifest.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": artifact_dir / "fields/OO.pt",
        "instant_add": artifact_dir / "manifests/component_package_manifest.json",
        "final": artifact_dir / "results/final_summary.json",
    }


class ComponentSwapRuntime:
    """Serve one of four frozen selector x writer/reader field cells."""

    def __init__(
        self,
        *,
        settings_9a: Mapping[str, Any],
        settings_12a: Mapping[str, Any],
        backend: Any,
        package_manifest: Mapping[str, Any],
    ) -> None:
        self.backend = backend
        self.settings = settings_12a
        self.package_manifest = package_manifest
        self.query_encoders = {}
        for name in ("old", "fresh"):
            selector_settings = copy.deepcopy(dict(settings_9a))
            selector_settings["parent_exp025c"] = str(
                settings_12a["selectors"][name]["root"]
            )
            selector_settings["expected"] = dict(selector_settings["expected"])
            selector_settings["expected"]["selector_ensemble_sha256"] = str(
                settings_12a["selectors"][name]["ensemble_sha256"]
            )
            selector_settings["appworld"] = dict(selector_settings["appworld"])
            selector_settings["appworld"]["prompt_profile"] = str(
                settings_12a["prompt_profile"]
            )
            self.query_encoders[name] = LiveFieldQueryEncoder(
                settings=selector_settings, backend=backend
            )

        self.readers = {}
        self.writer_reader_identity = {}
        for name in ("old", "fresh"):
            source = settings_12a["writer_readers"][name]
            _, reader, identity = load_writer_reader_package(
                name=name,
                checkpoint_path=Path(str(source["checkpoint"])),
                expected_checkpoint_sha256=str(source["checkpoint_sha256"]),
                device=backend.device,
            )
            self.readers[name] = reader
            self.writer_reader_identity[name] = identity

        self.fields, self.field_paths, self.field_sha256 = {}, {}, {}
        for cell in ("OO", "OF", "FO", "FF"):
            row = package_manifest["fields"][cell]
            path = Path(str(row["path"]))
            actual = sha256_file(path)
            if actual != str(row["sha256"]):
                raise ValueError(f"{cell} field artifact SHA differs")
            payload = torch.load(path, map_location="cpu", weights_only=False)
            if str(payload["cell"]) != cell or int(payload["memory_count"]) != 401:
                raise ValueError(f"{cell} field metadata differs")
            self.fields[cell] = {
                "C": (
                    payload["A"].to(backend.device, torch.float32),
                    payload["B"].to(backend.device, torch.float32),
                ),
                "S": (
                    payload["shuffled_A"].to(backend.device, torch.float32),
                    payload["shuffled_B"].to(backend.device, torch.float32),
                ),
            }
            self.field_paths[cell] = path
            self.field_sha256[cell] = actual
        self.reader = self.readers["old"]
        self.query_encoder = self.query_encoders["old"]
        self.identity = {
            "format": "rcmf_one_demo_component_swap_runtime_12a_v1",
            "selectors": {
                key: value.identity for key, value in self.query_encoders.items()
            },
            "writer_readers": {
                key: {
                    "checkpoint_sha256": value.checkpoint_sha256,
                    "reader_sha256": value.reader_sha256,
                }
                for key, value in self.writer_reader_identity.items()
            },
            "fields": dict(self.field_sha256),
            "memory_count": 401,
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }
        self.identity["identity_sha256"] = canonical_sha256(self.identity)

    @staticmethod
    def base_condition(condition: str) -> str:
        return condition.removesuffix("-R")

    def field_path(self, condition: str) -> Path:
        cell, _, _, _ = condition_parts(self.base_condition(condition))
        return self.field_paths[cell]

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        base = self.base_condition(condition)
        cell, binding, selector_name, writer_reader_name = condition_parts(base)
        self.reader = self.readers[writer_reader_name]
        self.query_encoder = self.query_encoders[selector_name]
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        A, B = self.fields[cell][binding]
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
            "field_control": "correct" if binding == "C" else "key_payload_shuffle",
            "component_cell": cell,
            "binding": binding,
            "selector_package": selector_name,
            "writer_reader_package": writer_reader_name,
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }


def load_backend(cfg: Any) -> Any:
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("EXP-035A loaded trainable Qwen parameters")
    return backend


def task_output(run_paths: Mapping[str, Path], condition: str, task_id: str) -> Path:
    return run_paths["root"] / "conditions" / condition / "task_results" / f"{task_id}.json"


def run_one(
    *,
    task_id: str,
    condition: str,
    smoke: bool,
    settings_9a: Mapping[str, Any],
    backend: Any,
    runtime: ComponentSwapRuntime,
    run_paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    config_sha256: str,
    attempt_id: str,
) -> tuple[dict[str, Any], bool]:
    base = runtime.base_condition(condition)
    cell, binding, selector_name, writer_reader_name = condition_parts(base)
    return _run_task(
        task_id=task_id,
        condition=condition,
        settings=settings_9a,
        backend=backend,
        runtime=runtime,
        paths=run_paths,
        manifest=manifest,
        config_sha256=config_sha256,
        attempt_id=attempt_id,
        smoke=smoke,
        result_version=TASK_RESULT_VERSION,
        extra_result_fields={
            "exp035a_component_cell": cell,
            "exp035a_binding": binding,
            "selector_package": selector_name,
            "writer_reader_package": writer_reader_name,
            "evaluation_only": True,
            "optimizer_steps": 0,
        },
        bare_condition=False,
        condition_name=f"{cell}_{'correct' if binding == 'C' else 'shuffle'}",
        memory_count=401,
        field_artifact_path=runtime.field_path(base),
        field_provenance_path=run_paths["instant_add"],
        max_steps_override=int(settings_9a["appworld"]["max_steps"]),
        experiment_prefix="exp035a",
        field_control_condition=base,
    )


def smoke(
    *,
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    run_paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    task_id = str(manifest["task_ids"][0])
    backend = load_backend(cfg)
    package = read_json(args.artifact_dir / "manifests/component_package_manifest.json")
    runtime = ComponentSwapRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings_12a=settings,
        backend=backend,
        package_manifest=package,
    )
    rows = {}
    for condition in CONDITIONS:
        row, _ = run_one(
            task_id=task_id,
            condition=condition,
            smoke=True,
            settings_9a=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            run_paths=run_paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
        )
        rows[condition] = row
        attempt.progress(
            status="complete_trajectory_smoke",
            completed_units=len(rows),
            total_units=12,
            latest_validated_checkpoint=str(
                run_paths["root"] / "smoke_v2" / condition / "task_results" / f"{task_id}.json"
            ),
        )

    deterministic = {}
    for condition in ("OO-C", "OO-S", "FF-C", "FF-S"):
        repeat, _ = run_one(
            task_id=task_id,
            condition=f"{condition}-R",
            smoke=True,
            settings_9a=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            run_paths=run_paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
        )
        deterministic[condition] = deterministic_task_match(rows[condition], repeat)
        attempt.progress(
            status="complete_trajectory_determinism",
            completed_units=8 + len(deterministic),
            total_units=12,
            latest_validated_checkpoint=str(
                run_paths["root"]
                / "smoke_v2"
                / f"{condition}-R"
                / "task_results"
                / f"{task_id}.json"
            ),
        )

    infrastructure = all(
        row["status"] == "complete"
        and row["success_source"] == "evaluation.success"
        and row["raw_audit_complete"]
        and row["terminal_error"] is None
        for row in rows.values()
    )
    deterministic_passed = all(value["passed"] for value in deterministic.values())
    if not infrastructure:
        raise RuntimeError("EXP-035A complete-trajectory smoke infrastructure failed")
    if not deterministic_passed:
        raise RuntimeError(
            "EXP-035A deterministic reproduction failed; three-seed expansion requires a new frozen manifest"
        )
    wall_values = [float(row["wall_seconds"]) for row in rows.values()]
    measured_mean = statistics.fmean(wall_values)
    expected_seconds = measured_mean * 64
    conservative_seconds = max(wall_values) * 64 * 1.25
    report = {
        "format": "rcmf_one_demo_component_swap_smoke_12a_v1",
        "task_id": task_id,
        "condition_count": len(rows),
        "complete_trajectory_count": len(rows),
        "infrastructure_exception_count": 0,
        "determinism": deterministic,
        "deterministic": True,
        "evaluation_seeds": [GLOBAL_SEED],
        "condition_wall_seconds": {key: float(value["wall_seconds"]) for key, value in rows.items()},
        "mean_condition_wall_seconds": measured_mean,
        "expected_full_wall_hours": expected_seconds / 3600.0,
        "conservative_full_wall_hours": conservative_seconds / 3600.0,
        "expected_h100_hours": expected_seconds / 3600.0,
        "conservative_h100_hours": conservative_seconds / 3600.0,
        "automatic_launch_allowed": expected_seconds <= 18 * 3600 and conservative_seconds <= 18 * 3600,
        "restart_plan": "atomic task-condition JSON; resume validates config, manifest, field, status, and smoke flag",
        "passed": infrastructure and deterministic_passed,
    }
    report["report_sha256"] = canonical_sha256(report)
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("EXP-035A conservative 64-trajectory estimate exceeds 18 hours")
    atomic_write_json(args.artifact_dir / "preflight/trajectory_smoke.json", report)
    assert_frozen_without_gradients(backend.model)
    for reader in runtime.readers.values():
        assert_frozen_without_gradients(reader)
    return report


def formal_run(
    *,
    args: argparse.Namespace,
    cfg: Any,
    settings: Mapping[str, Any],
    run_paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    smoke_report = read_json(args.artifact_dir / "preflight/trajectory_smoke.json")
    if not bool(smoke_report["passed"]) or not bool(smoke_report["automatic_launch_allowed"]):
        raise RuntimeError("EXP-035A smoke/runtime gate did not pass")
    backend = load_backend(cfg)
    runtime = ComponentSwapRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings_12a=settings,
        backend=backend,
        package_manifest=read_json(
            args.artifact_dir / "manifests/component_package_manifest.json"
        ),
    )
    completed, resumed = 0, 0
    for manifest_row in manifest["rows"]:
        task_id = str(manifest_row["task_id"])
        condition = str(manifest_row["condition"])
        _, reused = run_one(
            task_id=task_id,
            condition=condition,
            smoke=False,
            settings_9a=cfg.raw["stage_c_9a"],
            backend=backend,
            runtime=runtime,
            run_paths=run_paths,
            manifest=manifest,
            config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
        )
        completed += 1
        resumed += int(reused)
        attempt.progress(
            status="formal_component_swap_trajectories",
            completed_units=completed,
            total_units=len(manifest["rows"]),
            resumed_units=resumed,
            latest_validated_checkpoint=str(task_output(run_paths, condition, task_id)),
        )
        print(
            f"task={task_id} condition={condition} completed={completed}/64 reused={reused}",
            flush=True,
        )
    assert_frozen_without_gradients(backend.model)
    for reader in runtime.readers.values():
        assert_frozen_without_gradients(reader)
    result = {
        "format": "rcmf_one_demo_component_swap_execution_12a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "task_condition_count": completed,
        "resumed_count": resumed,
        "new_count": completed - resumed,
        "optimizer_steps": 0,
        "parameter_updates": 0,
        "complete": completed == 64,
    }
    result["execution_sha256"] = canonical_sha256(result)
    atomic_write_json(args.artifact_dir / "results/execution_summary.json", result)
    return result


def summarize_condition(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    query_seconds, read_seconds, residuals, entropies, slot_norms, q_norms = [], [], [], [], [], []
    for row in rows:
        counts.update(row["counts"])
        for step in row["steps"]:
            field = step["field"]
            query_seconds.append(float(field["query_seconds"]))
            read_seconds.append(float(field["field_read_seconds"]))
            if field.get("query"):
                q_norms.append(float(field["query"]["norm"]))
            slot_norms.append(float(field["slots"]["norm"]))
            reader = step.get("reader_audit", {})
            for values in reader.get("delta_norms", {}).values():
                residuals.extend(float(value) for value in values)
            for values in reader.get("attention_entropy", {}).values():
                entropies.extend(float(value) for value in values)
    successes = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    return {
        "condition": condition,
        "task_count": len(rows),
        "success_count": len(successes),
        "success_ids": successes,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "mean_steps": statistics.fmean(int(row["step_count"]) for row in rows),
        "median_steps": statistics.median(int(row["step_count"]) for row in rows),
        "counts": dict(counts),
        "total_prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in rows),
        "total_generated_tokens": sum(int(row["usage"].get("completion_tokens", 0)) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "mean_query_seconds": statistics.fmean(query_seconds),
        "mean_field_read_seconds": statistics.fmean(read_seconds),
        "mean_q_norm": statistics.fmean(q_norms),
        "mean_slot_norm": statistics.fmean(slot_norms),
        "mean_reader_residual_norm": statistics.fmean(residuals) if residuals else 0.0,
        "mean_attention_entropy": statistics.fmean(entropies) if entropies else None,
        "infrastructure_valid": all(
            row["status"] == "complete"
            and row["success_source"] == "evaluation.success"
            and row["raw_audit_complete"]
            for row in rows
        ),
    }


def finalize(
    *, args: argparse.Namespace, settings: Mapping[str, Any], run_paths: Mapping[str, Path], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    task_ids = [str(value) for value in manifest["task_ids"]]
    all_rows = {
        condition: [read_json(task_output(run_paths, condition, task_id)) for task_id in task_ids]
        for condition in CONDITIONS
    }
    summaries = {
        condition: summarize_condition(rows, condition) for condition, rows in all_rows.items()
    }
    if not all(value["infrastructure_valid"] for value in summaries.values()):
        raise RuntimeError("EXP-035A formal trajectory infrastructure is invalid")
    result = {
        "format": "rcmf_one_demo_component_swap_final_raw_12a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "task_ids": task_ids,
        "conditions": summaries,
        "task_condition_count": sum(len(value) for value in all_rows.values()),
        "all_rows_complete": True,
        "optimizer_steps": 0,
        "parameter_updates": 0,
    }
    result["summary_sha256"] = canonical_sha256(result)
    atomic_write_json(run_paths["final"], result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_12a"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    manifest_path = args.artifact_dir / "manifests/condition_manifest.json"
    package_path = args.artifact_dir / "manifests/component_package_manifest.json"
    if not manifest_path.exists() or not package_path.exists():
        raise RuntimeError("EXP-035A frozen preparation artifacts are missing")
    manifest = read_json(manifest_path)
    if str(manifest["source_head"]) != args.source_head:
        raise ValueError("EXP-035A manifest source HEAD differs")
    run_paths = paths(args.artifact_dir)
    require_unique_attempt(args.artifact_dir / "attempts.jsonl", args.attempt_id)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"component_swap_{args.phase}",
        command=list(os.sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "condition_manifest": sha256_file(manifest_path),
            "component_package_manifest": sha256_file(package_path),
        },
        parent_attempt_id="none",
        resume_checkpoint="atomic_task_condition_rows",
        scientific_parameter_changed=False,
        heartbeat_interval_s=240,
    ) as attempt:
        if args.phase == "smoke":
            result = smoke(
                args=args,
                cfg=cfg,
                settings=settings,
                run_paths=run_paths,
                manifest=manifest,
                attempt=attempt,
            )
        elif args.phase == "run":
            result = formal_run(
                args=args,
                cfg=cfg,
                settings=settings,
                run_paths=run_paths,
                manifest=manifest,
                attempt=attempt,
            )
        else:
            result = finalize(
                args=args, settings=settings, run_paths=run_paths, manifest=manifest
            )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
