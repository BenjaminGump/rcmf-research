"""Run the frozen five-condition EXP-036A AppWorld test_normal evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import copy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_appworld_testnormal_final_13a import (
    CONDITIONS,
    CONDITION_NAMES,
    GLOBAL_SEED,
    condition_parts,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    assert_frozen_without_gradients,
    read_compiled_field,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    load_writer_reader_package,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.analyze_rcmf_one_demo_dev_11a import trajectory_metrics
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    LiveFieldQueryEncoder,
    _build_backend,
    _run_task,
)
from scripts.run_rcmf_q90_trajectory_common_9c import deterministic_task_match


TASK_RESULT_FORMAT = "rcmf_appworld_testnormal_task_13a_v1"


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
    parser.add_argument(
        "--phase", choices=("smoke", "run", "finalize"), required=True
    )
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--manifest-source-head")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_unique_attempt(path: Path, attempt_id: str) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and str(json.loads(line).get("attempt_id")) == attempt_id:
            raise ValueError(f"Duplicate attempt ID: {attempt_id}")


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def run_paths(artifact_dir: Path, settings: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "root": artifact_dir / "formal",
        "manifest": artifact_dir / "manifests/condition_manifest.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": Path(
            str(settings["packages"]["BEST"]["deployment_field"])
        ),
        "instant_add": artifact_dir / "manifests/package_manifest.json",
        "final": artifact_dir / "results/formal_summary.json",
    }


class FinalTestRuntime:
    """Serve BEST or FULL1D frozen fields without accessing memory records."""

    def __init__(
        self,
        *,
        settings_9a: Mapping[str, Any],
        settings: Mapping[str, Any],
        backend: Any,
        package_manifest: Mapping[str, Any],
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.package_manifest = package_manifest
        self.query_encoders: dict[str, LiveFieldQueryEncoder] = {}
        self.readers: dict[str, Any] = {}
        self.reader_identities: dict[str, Any] = {}
        self.fields: dict[str, dict[str, tuple[Tensor, Tensor]]] = {}
        self.field_paths: dict[str, Path] = {}
        self.field_sha256: dict[str, str] = {}
        for package in ("BEST", "FULL1D"):
            source = settings["packages"][package]
            selector_settings = copy.deepcopy(dict(settings_9a))
            selector_settings["parent_exp025c"] = str(source["selector_root"])
            selector_settings["expected"] = dict(selector_settings["expected"])
            selector_settings["expected"]["selector_ensemble_sha256"] = str(
                source["selector_ensemble_sha256"]
            )
            selector_settings["appworld"] = dict(selector_settings["appworld"])
            selector_settings["appworld"]["prompt_profile"] = str(
                settings["prompt_profile"]
            )
            self.query_encoders[package] = LiveFieldQueryEncoder(
                settings=selector_settings, backend=backend
            )
            _, reader, identity = load_writer_reader_package(
                name=package.lower(),
                checkpoint_path=Path(str(source["writer_reader_checkpoint"])),
                expected_checkpoint_sha256=str(
                    source["writer_reader_checkpoint_sha256"]
                ),
                device=backend.device,
            )
            self.readers[package] = reader
            self.reader_identities[package] = identity
            path = Path(str(source["deployment_field"])); actual = sha256_file(path)
            if actual != str(source["deployment_field_sha256"]):
                raise ValueError(f"{package} deployment field SHA differs")
            payload = torch.load(path, map_location="cpu", weights_only=False)
            if int(payload["memory_count"]) != 499:
                raise ValueError(f"{package} deployment field is not the 499-memory bank")
            self.fields[package] = {
                "correct": (
                    payload["A"].to(backend.device, torch.float32),
                    payload["B"].to(backend.device, torch.float32),
                ),
                "key_payload_shuffle": (
                    payload["shuffled_A"].to(backend.device, torch.float32),
                    payload["shuffled_B"].to(backend.device, torch.float32),
                ),
            }
            self.field_paths[package] = path
            self.field_sha256[package] = actual
        self.reader = self.readers["BEST"]
        self.query_encoder = self.query_encoders["BEST"]
        self.identity = {
            "format": "rcmf_appworld_testnormal_runtime_13a_v1",
            "packages": {
                name: {
                    "selector": self.query_encoders[name].identity,
                    "writer_reader_checkpoint_sha256": self.reader_identities[
                        name
                    ].checkpoint_sha256,
                    "reader_sha256": self.reader_identities[name].reader_sha256,
                    "deployment_field_sha256": self.field_sha256[name],
                }
                for name in ("BEST", "FULL1D")
            },
            "memory_count": 499,
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }
        self.identity["identity_sha256"] = canonical_sha256(self.identity)

    @staticmethod
    def base_condition(condition: str) -> str:
        return condition.removesuffix("-REPEAT")

    def field_path(self, condition: str) -> Path:
        package, _ = condition_parts(self.base_condition(condition))
        return self.field_paths["BEST" if package is None else package]

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        package, binding = condition_parts(self.base_condition(condition))
        if package is None:
            raise ValueError("Bare condition has no field read")
        self.reader = self.readers[package]
        self.query_encoder = self.query_encoders[package]
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize(self.backend.device)
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize(self.backend.device)
        query_seconds = time.perf_counter() - started
        A, B = self.fields[package][binding]
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize(self.backend.device)
        started = time.perf_counter()
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize(self.backend.device)
        return slots, {
            "state_views": views,
            "query": query,
            "query_seconds": query_seconds,
            "field_read_seconds": time.perf_counter() - started,
            "field_control": binding,
            "package": package,
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
        raise RuntimeError("EXP-036A loaded trainable Qwen parameters")
    return backend


def task_output(paths: Mapping[str, Path], condition: str, task_id: str) -> Path:
    return paths["root"] / "conditions" / condition / "task_results" / f"{task_id}.json"


def run_one(
    *,
    task_id: str,
    condition: str,
    smoke: bool,
    settings_9a: Mapping[str, Any],
    backend: Any,
    runtime: FinalTestRuntime,
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    config_sha256: str,
    attempt_id: str,
    execution_source_head: str,
) -> tuple[dict[str, Any], bool]:
    base = runtime.base_condition(condition)
    package, binding = condition_parts(base)
    row, reused = _run_task(
        task_id=task_id,
        condition=condition,
        settings=settings_9a,
        backend=backend,
        runtime=None if base == "B0" else runtime,
        paths=paths,
        manifest=manifest,
        config_sha256=config_sha256,
        attempt_id=attempt_id,
        smoke=smoke,
        result_version=TASK_RESULT_FORMAT,
        extra_result_fields={
            "exp036a_package": package,
            "exp036a_binding": binding,
            "evaluation_only": True,
            "optimizer_steps": 0,
            "parameter_updates": 0,
            "primary_method": package == "BEST",
            "secondary_ablation": package == "FULL1D",
            "execution_source_head": execution_source_head,
            "task_list_sha256": str(manifest["task_list_sha256"]),
            "package_manifest_sha256": str(
                manifest["package_manifest_sha256"]
            ),
            "prompt_profile": "full_demo_first_only",
        },
        bare_condition=base == "B0",
        condition_name=CONDITION_NAMES[base],
        memory_count=0 if base == "B0" else 499,
        field_artifact_path=runtime.field_path(base),
        field_provenance_path=paths["instant_add"],
        max_steps_override=int(settings_9a["appworld"]["max_steps"]),
        experiment_prefix="exp036a",
        field_control_condition=base,
        collect_resource_metrics=True,
    )
    output = (
        paths["root"]
        / ("smoke_v2" if smoke else "conditions")
        / condition
        / "task_results"
        / f"{task_id}.json"
    )
    required = {
        "execution_source_head": execution_source_head,
        "task_list_sha256": str(manifest["task_list_sha256"]),
        "package_manifest_sha256": str(manifest["package_manifest_sha256"]),
        "prompt_profile": "full_demo_first_only",
    }
    if reused:
        checks = {key: row.get(key) == value for key, value in required.items()}
        content = {key: value for key, value in row.items() if key != "result_sha256"}
        checks["result_sha256"] = str(row.get("result_sha256")) == canonical_sha256(
            content
        )
        if not all(checks.values()):
            raise ValueError(f"EXP-036A completed-row resume identity differs: {checks}")
    else:
        row.update(required)
        row["world_identity"] = {
            "task_id": task_id,
            "experiment_name": str(row["experiment_name"]),
            "fresh_isolated_world": True,
            "appworld_root": str(settings_9a["appworld"]["legacy_root"]),
            "evaluator_success_source": "evaluation.success",
        }
        row["generation_settings"] = {
            "seed": GLOBAL_SEED,
            "max_steps": int(settings_9a["appworld"]["max_steps"]),
            "max_api_calls_per_interaction": int(
                settings_9a["appworld"]["max_api_calls_per_interaction"]
            ),
            "max_context_turns": int(
                settings_9a["appworld"]["max_context_turns"]
            ),
            "max_new_tokens": int(settings_9a["appworld"]["max_new_tokens"]),
            "temperature": 0,
            "top_p": 1,
            "do_sample": False,
            "enable_thinking": False,
        }
        row["result_sha256"] = canonical_sha256(row)
        atomic_write_json(output, row)
    return row, reused


def _all_complete(rows: Sequence[Mapping[str, Any]]) -> bool:
    return all(
        row["status"] == "complete"
        and row["success_source"] == "evaluation.success"
        and row["raw_audit_complete"]
        for row in rows
    )


def smoke(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path],
    manifest: Mapping[str, Any], attempt: AttemptLedger
) -> dict[str, Any]:
    smoke_manifest = read_json(args.artifact_dir / "manifests/smoke_manifest.json")
    backend = load_backend(cfg)
    runtime = FinalTestRuntime(
        settings_9a=cfg.raw["stage_c_9a"], settings=settings, backend=backend,
        package_manifest=read_json(args.artifact_dir / "manifests/package_manifest.json"),
    )
    rows: dict[str, dict[str, Any]] = {}
    completed = 0
    for task_id in smoke_manifest["task_ids"]:
        for condition in CONDITIONS:
            row, _ = run_one(
                task_id=str(task_id), condition=condition, smoke=True,
                settings_9a=cfg.raw["stage_c_9a"], backend=backend, runtime=runtime,
                paths=paths, manifest=manifest, config_sha256=sha256_file(args.config),
                attempt_id=args.attempt_id,
                execution_source_head=args.source_head,
            )
            rows[f"{task_id}:{condition}"] = row; completed += 1
            attempt.progress(status="exp036a_smoke", completed_units=completed, total_units=15)
    deterministic = {}
    repeat_task = str(smoke_manifest["repeat_task_id"])
    for condition in CONDITIONS:
        repeat, _ = run_one(
            task_id=repeat_task, condition=f"{condition}-REPEAT", smoke=True,
            settings_9a=cfg.raw["stage_c_9a"], backend=backend, runtime=runtime,
            paths=paths, manifest=manifest, config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            execution_source_head=args.source_head,
        )
        deterministic[condition] = deterministic_task_match(
            rows[f"{repeat_task}:{condition}"], repeat
        )
        completed += 1
        attempt.progress(status="exp036a_determinism", completed_units=completed, total_units=15)
    primary_rows = list(rows.values())
    if not _all_complete(primary_rows):
        raise RuntimeError("EXP-036A smoke infrastructure failed")
    if not all(value["passed"] for value in deterministic.values()):
        raise RuntimeError("EXP-036A deterministic reproduction failed")
    wall = [float(row["wall_seconds"]) for row in primary_rows]
    expected_formal = statistics.fmean(wall) * 840
    conservative_formal = max(wall) * 840 * 1.25
    auxiliary = settings["runtime"]["auxiliary_estimate"]
    if not bool(auxiliary["phase_b_runs_after_formal"]):
        raise RuntimeError("EXP-036A requires Phase B to run after formal evaluation")
    efficiency_expected = float(auxiliary["efficiency_expected_hours"]) * 3600.0
    efficiency_conservative = (
        float(auxiliary["efficiency_conservative_hours"]) * 3600.0
    )
    reversibility_expected = (
        float(auxiliary["reversibility_expected_hours"]) * 3600.0
    )
    reversibility_conservative = (
        float(auxiliary["reversibility_conservative_hours"]) * 3600.0
    )
    auxiliary_expected = efficiency_expected + reversibility_expected
    auxiliary_conservative = efficiency_conservative + reversibility_conservative
    expected_total = expected_formal + auxiliary_expected
    conservative_total = conservative_formal + auxiliary_conservative
    smoke_bytes = sum(
        path.stat().st_size
        for path in (args.artifact_dir / "formal/smoke_v2").rglob("*")
        if path.is_file()
    )
    report = {
        "format": "rcmf_appworld_testnormal_runtime_preflight_13a_v1",
        "smoke_task_ids": list(smoke_manifest["task_ids"]),
        "smoke_trajectory_count": 15,
        "formal_task_count": 168,
        "formal_condition_count": 840,
        "determinism": deterministic,
        "deterministic": True,
        "evaluation_seeds": [GLOBAL_SEED],
        "mean_task_condition_wall_seconds": statistics.fmean(wall),
        "maximum_task_condition_wall_seconds": max(wall),
        "expected_formal_wall_hours": expected_formal / 3600.0,
        "conservative_formal_wall_hours": conservative_formal / 3600.0,
        "expected_auxiliary_wall_hours": auxiliary_expected / 3600.0,
        "conservative_auxiliary_wall_hours": auxiliary_conservative / 3600.0,
        "efficiency_microbenchmark_estimate": {
            "expected_wall_hours": efficiency_expected / 3600.0,
            "conservative_wall_hours": efficiency_conservative / 3600.0,
            "basis": str(auxiliary["basis"]),
            "scheduled_after_formal": True,
        },
        "numerical_reversibility_estimate": {
            "expected_wall_hours": reversibility_expected / 3600.0,
            "conservative_wall_hours": reversibility_conservative / 3600.0,
            "scheduled_after_formal": True,
        },
        "expected_total_wall_hours": expected_total / 3600.0,
        "conservative_total_wall_hours": conservative_total / 3600.0,
        "expected_h100_active_hours": expected_total / 3600.0,
        "conservative_h100_active_hours": conservative_total / 3600.0,
        "smoke_raw_artifact_bytes": smoke_bytes,
        "projected_lambda_raw_artifact_bytes": int(smoke_bytes / 15 * 840),
        "projected_git_safe_artifact_bytes": int(smoke_bytes / 15 * 840 * 0.2),
        "approved_wall_hours": float(settings["runtime"]["approved_wall_hours"]),
        "automatic_launch_allowed": conservative_total
        <= float(settings["runtime"]["approved_wall_hours"]) * 3600.0,
        "restart_plan": "one atomic task-condition JSON plus raw per-step rows/tensors; resume validates format/config/manifest/field/completion",
        "passed": True,
    }
    report["report_sha256"] = canonical_sha256(report)
    atomic_write_json(args.artifact_dir / "preflight/runtime_preflight.json", report)
    if not report["automatic_launch_allowed"]:
        raise RuntimeError("EXP-036A conservative complete-task estimate exceeds 42 hours")
    assert_frozen_without_gradients(backend.model)
    for reader in runtime.readers.values():
        assert_frozen_without_gradients(reader)
    return report


def condition_summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    successes = [str(row["task_id"]) for row in rows if bool(row["success"])]
    gpu = [row["resource_metrics"] for row in rows]
    return {
        "format": "rcmf_appworld_testnormal_condition_summary_13a_v1",
        "condition": condition,
        "task_count": len(rows),
        "success_count": len(successes),
        "success_ids": successes,
        "trajectory_metrics": trajectory_metrics(rows),
        "total_task_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "peak_allocated_bytes": max(int(row["peak_allocated_bytes"]) for row in gpu),
        "peak_reserved_bytes": max(int(row["peak_reserved_bytes"]) for row in gpu),
        "infrastructure_valid": _all_complete(rows),
        "optimizer_steps": 0,
    }


def formal_run(
    *, args: argparse.Namespace, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path],
    manifest: Mapping[str, Any], attempt: AttemptLedger
) -> dict[str, Any]:
    if args.condition is None:
        raise ValueError("--condition is required for formal run")
    preflight = read_json(args.artifact_dir / "preflight/runtime_preflight.json")
    if not bool(preflight["passed"]) or not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("EXP-036A smoke/runtime gate did not pass")
    condition = args.condition
    for prior in CONDITIONS[: CONDITIONS.index(condition)]:
        summary = paths["root"] / "conditions" / prior / "summary.json"
        if not summary.exists():
            raise RuntimeError(f"Sequential prior condition is missing: {prior}")
    backend = load_backend(cfg)
    runtime = FinalTestRuntime(
        settings_9a=cfg.raw["stage_c_9a"], settings=settings, backend=backend,
        package_manifest=read_json(args.artifact_dir / "manifests/package_manifest.json"),
    )
    rows, resumed = [], 0
    selected = [row for row in manifest["rows"] if row["condition"] == condition]
    for index, manifest_row in enumerate(selected, start=1):
        row, reused = run_one(
            task_id=str(manifest_row["task_id"]), condition=condition, smoke=False,
            settings_9a=cfg.raw["stage_c_9a"], backend=backend, runtime=runtime,
            paths=paths, manifest=manifest, config_sha256=sha256_file(args.config),
            attempt_id=args.attempt_id,
            execution_source_head=args.source_head,
        )
        rows.append(row); resumed += int(reused)
        attempt.progress(
            status=f"exp036a_formal_{condition.lower()}", completed_units=index,
            total_units=len(selected), resumed_units=resumed,
            latest_validated_checkpoint=str(task_output(paths, condition, str(manifest_row["task_id"]))),
        )
        print(f"condition={condition} completed={index}/{len(selected)} reused={reused}", flush=True)
    result = condition_summary(rows, condition)
    if len(rows) != 168 or not result["infrastructure_valid"]:
        raise RuntimeError(f"EXP-036A {condition} is incomplete or infrastructure-invalid")
    result["resumed_count"] = resumed
    result["new_count"] = len(rows) - resumed
    result["summary_sha256"] = canonical_sha256(result)
    atomic_write_json(paths["root"] / "conditions" / condition / "summary.json", result)
    assert_frozen_without_gradients(backend.model)
    for reader in runtime.readers.values():
        assert_frozen_without_gradients(reader)
    return result


def finalize(
    *, settings: Mapping[str, Any], paths: Mapping[str, Path], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    summaries = {
        condition: read_json(paths["root"] / "conditions" / condition / "summary.json")
        for condition in CONDITIONS
    }
    if any(int(row["task_count"]) != 168 or not row["infrastructure_valid"] for row in summaries.values()):
        raise RuntimeError("EXP-036A formal condition set is incomplete")
    result = {
        "format": "rcmf_appworld_testnormal_formal_summary_13a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "task_ids": list(manifest["task_ids"]),
        "task_count": 168,
        "condition_count": 5,
        "trajectory_count": 840,
        "conditions": summaries,
        "evaluation_complete": True,
        "optimizer_steps": 0,
        "parameter_updates": 0,
    }
    result["summary_sha256"] = canonical_sha256(result)
    atomic_write_json(paths["final"], result)
    return result


def main() -> None:
    args = parse_args(); cfg = load_config(args.config); settings = cfg.raw["stage_c_13a"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    if git_head() != args.source_head:
        raise ValueError("EXP-036A execution source HEAD differs from checkout")
    run_manifest = read_json(args.artifact_dir / "run_manifest.json")
    manifest_source = args.manifest_source_head or args.source_head
    if str(run_manifest["source_head"]) != manifest_source:
        raise ValueError("EXP-036A manifest source HEAD differs")
    manifest = read_json(args.artifact_dir / "manifests/condition_manifest.json")
    package = read_json(args.artifact_dir / "manifests/package_manifest.json")
    if str(manifest["package_manifest_sha256"]) != str(package["manifest_sha256"]):
        raise ValueError("EXP-036A package/condition manifest identity differs")
    require_unique_attempt(args.artifact_dir / "attempts.jsonl", args.attempt_id)
    paths = run_paths(args.artifact_dir, settings)
    with AttemptLedger(
        args.artifact_dir, run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase=f"exp036a_{args.phase}" + (f"_{args.condition.lower()}" if args.condition else ""),
        command=list(sys.argv), local_head=args.source_head, github_head=args.source_head,
        lambda_head=args.source_head, tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "condition_manifest": sha256_file(args.artifact_dir / "manifests/condition_manifest.json"),
            "package_manifest": sha256_file(args.artifact_dir / "manifests/package_manifest.json"),
        }, parent_attempt_id="none", resume_checkpoint="atomic_task_condition_rows",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "smoke":
            result = smoke(args=args, cfg=cfg, settings=settings, paths=paths, manifest=manifest, attempt=attempt)
        elif args.phase == "run":
            result = formal_run(args=args, cfg=cfg, settings=settings, paths=paths, manifest=manifest, attempt=attempt)
        else:
            result = finalize(settings=settings, paths=paths, manifest=manifest)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
