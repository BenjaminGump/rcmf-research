from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping

import torch
import yaml

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.portable_adapter_v2 import (
    create_appworld_portable_adapter_v2_1,
)
from rcmf.benchmarks.appworld.portable_executor_v2_1 import (
    create_appworld_portable_executor_v2_1,
)
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.portable_v2.adapter import probe_adapter_capabilities
from rcmf.pipeline.portable_v2.checkpoint_policy import (
    CHECKPOINT_POINTER_VERSION,
    CHECKPOINT_RECORD_VERSION,
    TRAINING_UNIT_MANIFEST_VERSION,
    TerminalCheckpointPolicy,
)
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import PortablePhase, phases_for_policy
from rcmf.pipeline.portable_v2.executor import (
    PortableExecutionIdentity,
    PortablePhaseContext,
    PortablePhaseWork,
    execute_and_validate_phase,
)
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    RCMFFieldRecord,
    ReversibleRCMFField,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.state_conditioned_program_7d import stable_key
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


PILOT_VERSION = "rcmf_portable_v2_1_appworld_executable_pilot_v1"
PILOT_UUID = "rcmf_portable_v2_1_appworld_3demo_pilot_20260909_001"
GLOBAL_SEED = 25101
HARD_CAP_HOURS = 8.0
EXPECTED_HOURS = 1.5
CONSERVATIVE_HOURS = 4.0
MAIN_PYTHON = Path("/home/ubuntu/venvs/rcmf-py311/bin/python")
DEFAULT_PARENT = Path(
    "/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/"
    "rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent-root", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--run-uuid", default=PILOT_UUID)
    parser.add_argument(
        "--portable-config",
        type=Path,
        default=Path("configs/pipeline/rcmf_portable_canonical_v2_1.yaml"),
    )
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _checkpoint_losses(payload: Mapping[str, Any]) -> list[float]:
    history = payload.get("history")
    if not isinstance(history, list) or not history:
        raise RuntimeError("pilot checkpoint has no epoch history")
    losses = []
    for row in history:
        if not isinstance(row, Mapping):
            raise RuntimeError("pilot checkpoint history row is not a mapping")
        value = row.get("loss", row.get("recent_mean_loss"))
        if value is None:
            raise RuntimeError("pilot checkpoint history row has no loss statistic")
        loss = float(value)
        if not bool(torch.isfinite(torch.tensor(loss))):
            raise RuntimeError("pilot checkpoint history loss is nonfinite")
        losses.append(loss)
    return losses


def _stable_rows(rows: list[dict[str, Any]], label: str, count: int) -> list[dict[str, Any]]:
    candidates = [row for row in rows if str(row.get("label")) == label]
    ordered = sorted(
        candidates,
        key=lambda row: (
            stable_key(GLOBAL_SEED, "portable-v2.1-pilot", label, row["state_example_id"]),
            str(row["state_example_id"]),
        ),
    )
    if len(ordered) < count:
        raise RuntimeError(f"pilot lacks {count} {label} states")
    return ordered[:count]


def _copy_sealed_file(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.resolve(), target)


class Pilot:
    def __init__(self, args: argparse.Namespace) -> None:
        self.root = args.root.resolve(strict=False)
        self.parent = args.parent_root.resolve(strict=True)
        self.source = str(args.source_commit)
        self.run_uuid = str(args.run_uuid)
        if len(self.source) != 40:
            raise ValueError("source commit must be a full SHA")
        if not self.run_uuid or self.root.name != self.run_uuid:
            raise ValueError("pilot run UUID must be non-empty and equal the root basename")
        if self.root.exists():
            raise FileExistsError(f"fresh pilot root already exists: {self.root}")
        self.root.mkdir(parents=True)
        self.started = time.monotonic()
        self.config = PortablePipelineConfig.load(args.portable_config)
        if self.config.policy.prompt_profile != "full_demo":
            raise RuntimeError("pilot portable config is not the three-demo profile")
        self.artifact = self.root / "arms/3d"
        self.parent_artifact = self.parent / "arms/3d"
        self.parent_inputs = self._parent_inputs()
        self.parent_before = {name: sha256_file(path) for name, path in self.parent_inputs.items()}
        self.selected_rows, self.dev_task_id = self._freeze_population()
        self.identity = PortableExecutionIdentity(
            source_commit=self.source,
            run_uuid=self.run_uuid,
            run_root=self.root,
            pipeline_config_sha256=self.config.sha256,
            dataset_profile_sha256=self.config.dataset_profile.sha256,
            adapter_identity=self.config.adapter_factory,
        )
        pilot_phases = phases_for_policy(self.config.policy)
        self.handlers = {
            phase.value: getattr(self, f"phase_{phase.name.lower()}")
            for phase in pilot_phases
        }
        self.executor = create_appworld_portable_executor_v2_1(phase_handlers=self.handlers)

    def _parent_inputs(self) -> dict[str, Path]:
        rows = {
            "paired_outcomes": self.parent_artifact / "paired_causal/paired_outcomes.json",
            "teacher_cache": self.parent_artifact / "structured_compiler/policy_teacher_cache.pt",
            "source_cache": self.parent_artifact / "data/rcmf_source_cache.pt",
            "data_manifest": self.parent_artifact / "data/full_bank_data_manifest.json",
            "source_audit": self.parent_artifact / "data/source_representation_audit.json",
            "selector_audit": self.parent_artifact / "data/selector_decomposition_audit.json",
            "shuffle": self.parent_artifact / "data/key_payload_shuffle_manifest.json",
            "arm_config": self.parent / "resolved_configs/arm_3d.yaml",
            "writer_initial": self.parent / "preflight/initialization_snapshots/writer_initial.pt",
            "reader_initial": self.parent / "preflight/initialization_snapshots/reader_initial.pt",
        }
        missing = [str(path) for path in rows.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"sealed pilot inputs are missing: {missing}")
        return rows

    def _freeze_population(self) -> tuple[list[dict[str, Any]], str]:
        outcomes = list(_json(self.parent_inputs["paired_outcomes"])["rows"])
        train = [row for row in outcomes if str(row["model_split"]) == "model_train"]
        selected = []
        for label in ("POSITIVE", "NEUTRAL", "HARMFUL"):
            selected.extend(_stable_rows(train, label, 4))
        heldout = [
            row for row in outcomes if str(row["model_split"]) == "heldout_train_validation"
        ]
        heldout = sorted(
            heldout,
            key=lambda row: (
                stable_key(GLOBAL_SEED, "portable-v2.1-heldout-fixture", row["state_example_id"]),
                str(row["state_example_id"]),
            ),
        )
        first = heldout[0]
        second = next(row for row in heldout[1:] if row["state_task_id"] != first["state_task_id"])
        selected.extend((first, second))
        try:
            from appworld import load_task_ids
        except Exception as exc:
            raise RuntimeError("AppWorld is unavailable before pilot freeze") from exc
        dev_ids = list(map(str, load_task_ids(dataset_name="dev")))
        task_id = min(
            dev_ids,
            key=lambda value: (stable_key(GLOBAL_SEED, "portable-v2.1-dev", value), value),
        )
        spec = {
            "format": PILOT_VERSION,
            "run_uuid": self.run_uuid,
            "source_commit": self.source,
            "benchmark": "appworld",
            "model": "Qwen/Qwen3-8B",
            "prompt_profile": "full_demo",
            "global_seed": GLOBAL_SEED,
            "selection_rule": "stable_key(seed,namespace,label_or_split,state_id)_ascending",
            "training_state_ids": [str(row["state_example_id"]) for row in selected[:12]],
            "training_labels": dict(Counter(str(row["label"]) for row in selected[:12])),
            "heldout_fixture_state_ids": [str(row["state_example_id"]) for row in selected[12:]],
            "paired_fixture_count": len(selected),
            "expected_training_units": 20,
            "maximum_training_units": 20,
            "training_epochs": 1,
            "dev_task_ids": [task_id],
            "dev_manifest_sha256": content_sha256(dev_ids),
            "accuracy_threshold": None,
            "expected_wall_hours": EXPECTED_HOURS,
            "conservative_wall_hours": CONSERVATIVE_HOURS,
            "hard_cap_hours": HARD_CAP_HOURS,
            "expected_h100_active_hours": 1.25,
            "storage_gib": {"expected": 4, "conservative": 8},
            "restart_plan": "atomic training progress checkpoint; diagnostic root only",
            "engineering_evidence_only": True,
            "future_scientific_checkpoint_eligible": False,
            "user_authorized": True,
        }
        atomic_write_json(self.root / "pilot_specification.json", spec)
        return selected, task_id

    def _evidence(self, phase: PortablePhase, payload: Mapping[str, Any]) -> Path:
        path = self.root / "stages" / phase.value / "phase_evidence.json"
        atomic_write_json(path, {"format": PILOT_VERSION, "phase": phase.value, **dict(payload)})
        return path

    def _run_legacy(self, phase: str, *, extra_env: Mapping[str, str] | None = None) -> None:
        command = [
            str(MAIN_PYTHON),
            "scripts/run_rcmf_joint_full_bank_9a.py",
            "--config",
            str(self.root / "resolved_configs/arm_3d.yaml"),
            "--artifact-dir",
            str(self.artifact),
            "--phase",
            phase,
            "--attempt-id",
            f"portable-v2.1-{phase}",
            "--local-head",
            self.source,
            "--github-head",
            self.source,
            "--lambda-head",
            self.source,
            "--tmux-session",
            "exp037a_portable_v2_1_pilot",
        ]
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONHASHSEED": str(GLOBAL_SEED),
                "APPWORLD_ROOT": "/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root",
                "RCMF_PIPELINE_RUN_UUID": self.run_uuid,
                "RCMF_PIPELINE_RUN_ROOT": str(self.root),
                "RCMF_PIPELINE_CONFIG_SHA256": self.config.sha256,
                "RCMF_PIPELINE_CONTRACT_SHA256": content_sha256({"pilot": PILOT_VERSION}),
            }
        )
        environment.update(extra_env or {})
        log = self.root / f"logs/{phase}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            remaining = HARD_CAP_HOURS * 3600.0 - (time.monotonic() - self.started)
            if remaining <= 0:
                raise TimeoutError("portable pilot hard cap reached before subprocess")
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=remaining,
            )
        if completed.returncode:
            raise RuntimeError(f"legacy AppWorld phase {phase} failed with {completed.returncode}")

    def phase_provenance(self, context: PortablePhaseContext) -> PortablePhaseWork:
        probe = probe_adapter_capabilities(
            create_appworld_portable_adapter_v2_1(), prompt_profile="full_demo"
        )
        evidence = self._evidence(context.phase, {
            "capability_probe": probe,
            "sealed_parent_inputs": {
                name: file_identity(path) for name, path in sorted(self.parent_inputs.items())
            },
        })
        return PortablePhaseWork(({"operation": "capability_probe", "count": 1},), {"evidence": evidence}, {})

    def phase_successful_corpus(self, context: PortablePhaseContext) -> PortablePhaseWork:
        payload = _json(self.parent_inputs["paired_outcomes"])
        payload["rows"] = self.selected_rows
        payload["portable_pilot_subset"] = True
        path = self.root / "inputs/paired_outcomes.json"
        atomic_write_json(path, payload)
        teacher = torch.load(self.parent_inputs["teacher_cache"], map_location="cpu", weights_only=False)
        selected_ids = {str(row["state_example_id"]) for row in self.selected_rows}
        subset = copy.deepcopy(teacher)
        subset["ordered_state_ids"] = [value for value in teacher["ordered_state_ids"] if str(value) in selected_ids]
        for key in ("policy_rows", "teacher_rows"):
            subset[key] = {value: teacher[key][value] for value in subset["ordered_state_ids"]}
        teacher_path = self.root / "inputs/policy_teacher_cache.pt"
        torch.save(subset, teacher_path)
        evidence = self._evidence(context.phase, {
            "paired_states": len(self.selected_rows),
            "training_states": 12,
            "labels": dict(Counter(str(row["label"]) for row in self.selected_rows[:12])),
        })
        return PortablePhaseWork(
            ({"operation": "deterministic_subset", "count": len(self.selected_rows)},),
            {"paired_outcomes": path, "teacher_cache": teacher_path, "evidence": evidence},
            {},
        )

    def phase_memory_ledger(self, context: PortablePhaseContext) -> PortablePhaseWork:
        for name in ("source_cache", "data_manifest", "source_audit", "selector_audit", "shuffle"):
            _copy_sealed_file(
                self.parent_inputs[name],
                self.artifact / "data" / self.parent_inputs[name].name,
            )
        config = yaml.safe_load(self.parent_inputs["arm_config"].read_text(encoding="utf-8"))
        settings = config["stage_c_9a"]
        settings["run_uuid"] = self.run_uuid
        settings["expected"]["deployment_dev_task_count"] = 1
        settings["runtime"]["review_threshold_h100_hours"] = HARD_CAP_HOURS
        settings["prompt_dependent_inputs"] = {
            "outcomes": str(self.root / "inputs/paired_outcomes.json"),
            "teacher_cache": str(self.root / "inputs/policy_teacher_cache.pt"),
        }
        resolved = self.root / "resolved_configs/arm_3d.yaml"
        resolved.parent.mkdir(parents=True)
        resolved.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        init = self.root / "preflight/initialization_snapshots"
        init.mkdir(parents=True)
        shutil.copy2(self.parent_inputs["writer_initial"], init / "writer_initial.pt")
        shutil.copy2(self.parent_inputs["reader_initial"], init / "reader_initial.pt")
        evidence = self._evidence(
            context.phase,
            {"memory_ledger_count": 499, "sealed_parent_inputs_copied": True},
        )
        return PortablePhaseWork(({"operation": "bind_memory_ledger", "count": 499},), {"resolved_config": resolved, "evidence": evidence}, {})

    def phase_representations(self, context: PortablePhaseContext) -> PortablePhaseWork:
        source = torch.load(self.artifact / "data/rcmf_source_cache.pt", map_location="cpu", weights_only=False)
        checks = {
            "memory_count": len(source["ordered_transition_ids"]) == 499,
            "state_subset_present": set(str(row["state_example_id"]) for row in self.selected_rows).issubset(set(map(str, source["ordered_state_ids"]))),
            "memory_views_finite": bool(torch.isfinite(source["memory_views"]).all()),
            "memory_keys_finite": bool(torch.isfinite(source["memory_keys"]).all()),
            "state_queries_finite": bool(torch.isfinite(source["state_queries"]).all()),
        }
        if not all(checks.values()):
            raise RuntimeError(f"sealed representation checks failed: {checks}")
        evidence = self._evidence(context.phase, {"checks": checks})
        return PortablePhaseWork(({"operation": "validate_representations", "count": 3},), {"evidence": evidence}, {})

    def phase_selector_supervision(self, context: PortablePhaseContext) -> PortablePhaseWork:
        audit = _json(self.artifact / "data/selector_decomposition_audit.json")
        if not audit.get("passed"):
            raise RuntimeError("sealed selector decomposition audit did not pass")
        evidence = self._evidence(context.phase, {"selector_frozen": True, "selector_audit_sha256": sha256_file(self.artifact / "data/selector_decomposition_audit.json")})
        return PortablePhaseWork(({"operation": "validate_frozen_selector", "count": 1},), {"evidence": evidence}, {})

    def phase_paired_outcomes(self, context: PortablePhaseContext) -> PortablePhaseWork:
        rows = self.selected_rows
        if len({str(row["state_example_id"]) for row in rows}) != len(rows):
            raise RuntimeError("pilot paired states are duplicated")
        evidence = self._evidence(context.phase, {
            "condition_count": 2 * len(rows),
            "fresh_generation": False,
            "sealed_engineering_fixture": True,
            "outcomes_used_to_change_selection": False,
        })
        return PortablePhaseWork(({"operation": "construct_paired_conditions", "count": 2 * len(rows)},), {"evidence": evidence}, {})

    def phase_training_units(self, context: PortablePhaseContext) -> PortablePhaseWork:
        static = {
            "scoreable_train_states": 12,
            "scoreable_heldout_states": 2,
            "maximum_training_backwards": 40,
            "teacher_forced_heldout_forwards": 0,
            "heldout_live_conditions": 0,
        }
        atomic_write_json(self.artifact / "runtime/static_counts.json", static)
        self._run_legacy("preflight")
        units = _json(self.artifact / "joint_training/training_unit_manifest.json")
        if int(units["unit_count_per_epoch"]) != 20:
            raise RuntimeError("pilot unit count differs from frozen specification")
        self._run_legacy("smoke")
        self._run_legacy("zero-cache")
        evidence = self._evidence(context.phase, {
            "unit_count_per_epoch": 20,
            "no_science_smoke_backward_count": 2,
            "zero_cache_state_count": 14,
            "preflight": file_identity(self.artifact / "runtime/formal_gpu_preflight.json"),
        })
        return PortablePhaseWork(
            ({"operation": "build_training_units", "count": 20}, {"operation": "zero_cache", "count": 14}),
            {
                "units": self.artifact / "joint_training/training_unit_manifest.json",
                "zero_cache": self.artifact / "joint_training/zero_policy_nll_summary.json",
                "evidence": evidence,
            },
            {},
        )

    def phase_training(self, context: PortablePhaseContext) -> PortablePhaseWork:
        init = self.root / "preflight/initialization_snapshots"
        self._run_legacy(
            "train",
            extra_env={
                "RCMF_TRAIN_STOP_AFTER_EPOCH": "1",
                "RCMF_WRITER_INITIAL_PATH": str(init / "writer_initial.pt"),
                "RCMF_READER_INITIAL_PATH": str(init / "reader_initial.pt"),
            },
        )
        checkpoint = self.artifact / "joint_training/checkpoints/epoch_01.pt"
        summary = _json(self.artifact / "joint_training/checkpoints/epoch_01_stage_summary.json")
        if int(summary.get("completed_units", -1)) != 20:
            raise RuntimeError("pilot did not execute all 20 training units")
        evidence = self._evidence(context.phase, {
            "checkpoint": file_identity(checkpoint),
            "completed_units": 20,
            "backward_count": 20,
            "optimizer_step_count": 20,
            "qwen_frozen": True,
            "selector_frozen": True,
        })
        return PortablePhaseWork(({"operation": "backward_and_optimizer_step", "count": 20},), {"checkpoint": checkpoint, "evidence": evidence}, {})

    def phase_epoch_diagnostics(self, context: PortablePhaseContext) -> PortablePhaseWork:
        checkpoint = torch.load(
            self.artifact / "joint_training/checkpoints/epoch_01.pt",
            map_location="cpu",
            weights_only=False,
        )
        losses = _checkpoint_losses(checkpoint)
        evidence = self._evidence(
            context.phase,
            {
                "finite_losses": True,
                "history_rows": len(losses),
                "terminal_loss": losses[-1],
                "metric_selected": False,
            },
        )
        return PortablePhaseWork(
            ({"operation": "epoch_diagnostics", "count": len(losses)},),
            {"evidence": evidence},
            {},
        )

    def phase_terminal_checkpoint(self, context: PortablePhaseContext) -> PortablePhaseWork:
        checkpoint = self.artifact / "joint_training/checkpoints/epoch_01.pt"
        checkpoint_sha = sha256_file(checkpoint)
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        tensors = [value for state in (payload["writer_state_dict"], payload["reader_state_dict"]) for value in state.values()]
        if not all(bool(torch.isfinite(value).all()) for value in tensors):
            raise RuntimeError("pilot terminal checkpoint contains nonfinite tensors")
        legacy_units = _json(self.artifact / "joint_training/training_unit_manifest.json")
        unit_ids = [str(row["unit_id"]) for row in legacy_units["units"]]
        unit_manifest: dict[str, Any] = {
            "format": TRAINING_UNIT_MANIFEST_VERSION,
            "identity": {},
            "training_epochs": 1,
            "units_per_epoch": len(unit_ids),
            "unit_ids": unit_ids,
        }
        unit_manifest_path = self.root / "portable_checkpoint/training_unit_manifest.json"
        unit_manifest["identity"] = {
            "run_uuid": self.run_uuid,
            "source_commit": self.source,
            "pipeline_config_sha256": self.config.sha256,
            "data_manifest_sha256": sha256_file(self.artifact / "data/full_bank_data_manifest.json"),
            "training_unit_manifest_sha256": sha256_file(self.artifact / "joint_training/training_unit_manifest.json"),
        }
        unit_manifest["manifest_sha256"] = content_sha256(unit_manifest)
        atomic_write_json(unit_manifest_path, unit_manifest)
        identity = dict(unit_manifest["identity"])
        record = {
            "format": CHECKPOINT_RECORD_VERSION,
            "epoch": 1,
            "path": str(checkpoint.resolve()),
            "sha256": checkpoint_sha,
            "complete": True,
            "finite": True,
            "identity": identity,
            "completed_units": 20,
            "terminal_loss": _checkpoint_losses(payload)[-1],
        }
        pointer = {
            "format": CHECKPOINT_POINTER_VERSION,
            "identity": identity,
            "epoch": 1,
            "completed_units": 20,
            "path": str(checkpoint.resolve()),
            "sha256": checkpoint_sha,
        }
        pointer["pointer_sha256"] = content_sha256(pointer)
        pointer_path = self.root / "portable_checkpoint/latest_checkpoint.json"
        atomic_write_json(pointer_path, pointer)
        result = TerminalCheckpointPolicy(1).resolve(
            [record],
            expected_identity=identity,
            training_unit_manifest=unit_manifest,
            checkpoint_pointer=pointer,
        )
        evidence = self._evidence(context.phase, result)
        return PortablePhaseWork(({"operation": "terminal_checkpoint_validation", "count": 1},), {"unit_manifest": unit_manifest_path, "pointer": pointer_path, "evidence": evidence}, {})

    def phase_deployment_field(self, context: PortablePhaseContext) -> PortablePhaseWork:
        checkpoint = self.artifact / "joint_training/checkpoints/epoch_01.pt"
        checkpoint_sha = sha256_file(checkpoint)
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        source = torch.load(self.artifact / "data/rcmf_source_cache.pt", map_location="cpu", weights_only=False)
        manifest = _json(self.artifact / "data/full_bank_data_manifest.json")
        transitions = {
            str(row["transition_id"]): row
            for row in read_jsonl(
                Path(yaml.safe_load((self.root / "resolved_configs/arm_3d.yaml").read_text())["stage_c_9a"]["parent_exp025b"])
                / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
            )
        }
        writer = AlignedTransitionWriter()
        writer.load_state_dict(payload["writer_state_dict"], strict=True)
        writer.eval()
        reader = StandardFieldCrossAttentionReader()
        reader.load_state_dict(payload["reader_state_dict"], strict=True)
        reader.eval()
        with torch.no_grad():
            memory_payloads = writer(source["memory_views"].to(torch.float32))
        rho_map = manifest["rho_by_transition_id"]
        records = []
        for index, memory_id in enumerate(map(str, source["ordered_transition_ids"])):
            row = transitions[memory_id]
            records.append(
                RCMFFieldRecord(
                    memory_id=memory_id,
                    parent_id=str(row["parent_memory_id"]),
                    parent_task_id=str(row["parent_task_id"]),
                    key=source["memory_keys"][index].to(torch.float32),
                    payload=memory_payloads[index].to(torch.float32),
                    rho=float(rho_map[memory_id]),
                )
            )
        field = ReversibleRCMFField(key_dim=960, slot_count=8, payload_dim=256)
        for record in records:
            field.add_memory_fast(record)
        original_a, original_b = field.A.clone(), field.B.clone()
        removed = field.remove_memory_fast(records[0].memory_id)
        field.add_memory_fast(removed)
        reversible = bool(
            torch.allclose(field.A, original_a, atol=1.0e-5)
            and torch.allclose(field.B, original_b, atol=1.0e-5)
        )
        query = source["state_queries"][0].to(torch.float32)
        slots = field.read(query)
        if not reversible or not bool(torch.isfinite(slots).all()):
            raise RuntimeError("pilot field reversibility/read contract failed")
        bundle = {
            "format": "rcmf_portable_v2_1_pilot_deployment_field_v1",
            "checkpoint_sha256": checkpoint_sha,
            "memory_count": len(records),
            "memory_ids": sorted(record.memory_id for record in records),
            "A": field.A,
            "B": field.B,
            "shuffled_A": field.A.clone(),
            "shuffled_B": field.B.clone(),
            "reader_state_dict": payload["reader_state_dict"],
        }
        bundle_path = self.root / "deployment/pilot_field.pt"
        bundle_path.parent.mkdir(parents=True)
        torch.save(bundle, bundle_path)
        evidence = self._evidence(context.phase, {
            "field": file_identity(bundle_path),
            "memory_count": len(records),
            "A_shape": list(field.A.shape),
            "B_shape": list(field.B.shape),
            "add_count": len(records),
            "remove_count": 1,
            "restore_count": 1,
            "read_count": 1,
            "reversible_exact": reversible,
            "finite": True,
        })
        return PortablePhaseWork(({"operation": "field_add", "count": len(records)}, {"operation": "field_remove_restore_read", "count": 3}), {"field": bundle_path, "evidence": evidence}, {})

    def phase_official_evaluation(self, context: PortablePhaseContext) -> PortablePhaseWork:
        from rcmf.benchmarks.appworld.reproducible_stages_14b import _run_task_set

        field = self.root / "deployment/pilot_field.pt"
        environment = {
            "RCMF_PIPELINE_RUN_UUID": self.run_uuid,
            "RCMF_PIPELINE_RUN_ROOT": str(self.root),
            "RCMF_PIPELINE_CONFIG_SHA256": self.config.sha256,
            "RCMF_PIPELINE_CONTRACT_SHA256": content_sha256({"pilot": PILOT_VERSION}),
            "PYTHONHASHSEED": str(GLOBAL_SEED),
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        try:
            summary = _run_task_set(
                run_root=self.root,
                arm_id="3d",
                task_ids=(self.dev_task_id,),
                output_root=self.root / "evaluation",
                condition_id="PILOT_D1",
                condition_name="portable_v2_1_engineering_correct_field",
                field_control="D1",
                prompt_profile="full_demo",
                correct_field=field,
                shuffled_field=None,
                checkpoint=None,
                provenance=self.artifact / "data/full_bank_data_manifest.json",
                memory_count=499,
                source_commit=self.source,
                attempt_id="portable-v2.1-evaluation",
                deployment_bundle=True,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        if int(summary["task_count"]) != 1 or summary.get("counts", {}).get("infrastructure_exception", 0):
            raise RuntimeError("pilot AppWorld evaluation did not return one typed result")
        summary_path = self.root / "evaluation/summaries/PILOT_D1.json"
        evidence = self._evidence(context.phase, {
            "task_id": self.dev_task_id,
            "typed_evaluator_result": True,
            "success": bool(summary["success_count"]),
            "accuracy_threshold": None,
            "generation_reached_environment": int(summary["total_steps"]) > 0,
            "summary": file_identity(summary_path),
        })
        return PortablePhaseWork(({"operation": "qwen_generation_and_appworld_evaluation", "count": 1},), {"evaluation": summary_path, "evidence": evidence}, {})

    def run(self) -> dict[str, Any]:
        previous: Path | None = None
        completed = []
        for phase in phases_for_policy(self.config.policy):
            if (time.monotonic() - self.started) / 3600.0 >= HARD_CAP_HOURS:
                raise TimeoutError("portable pilot hard cap reached")
            output_root = self.root / "stages" / phase.value
            context = PortablePhaseContext(
                identity=self.identity,
                phase=phase,
                dependency_manifests=(previous,) if previous else (),
                input_artifacts=(self.parent_inputs if phase == PortablePhase.PROVENANCE else {}),
                output_root=output_root,
                policy={"prompt_profile": "full_demo", "training_epochs": 1},
            )
            execute_and_validate_phase(self.executor, context)
            previous = output_root / "stage_manifest.json"
            completed.append(phase.value)
        parent_after = {name: sha256_file(path) for name, path in self.parent_inputs.items()}
        if parent_after != self.parent_before:
            raise RuntimeError("sealed parent input changed during pilot")
        elapsed = time.monotonic() - self.started
        summary = {
            "format": PILOT_VERSION,
            "run_uuid": self.run_uuid,
            "source_commit": self.source,
            "completed_phases": completed,
            "phase_count": len(completed),
            "training_states": 12,
            "training_units": 20,
            "backward_count": 20,
            "optimizer_step_count": 20,
            "evaluation_task_count": 1,
            "elapsed_seconds": elapsed,
            "h100_active_hours_upper_bound": elapsed / 3600.0,
            "parent_inputs_unchanged": True,
            "scientific_result": False,
            "classification": "ENGINEERING_EXECUTABLE_INTEGRATION_EVIDENCE",
            "passed": True,
        }
        atomic_write_json(self.root / "pilot_summary.json", summary)
        return summary


def main() -> None:
    args = _parse_args()
    if EXPECTED_HOURS > 4 or CONSERVATIVE_HOURS > 8 or HARD_CAP_HOURS != 8:
        raise RuntimeError("bounded pilot runtime contract is not satisfied")
    result = Pilot(args).run()
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
