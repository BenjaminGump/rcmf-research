"""Prepare immutable EXP-035A component packages and 401-memory fields."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.prompt import (
    appworld_renderer_metadata,
    full_demo_sections,
    get_system_prompt,
)
from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import tensor_sha256
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    CELL_NAMES,
    CONDITIONS,
    GLOBAL_SEED,
    compile_field_pair,
    condition_order_for_task,
    condition_parts,
    field_rebuild_errors,
    load_selector_package,
    load_writer_reader_package,
    permutation_from_rows,
    remove_restore_error,
    select_leakage_safe_memory_ids,
    tensor_audit,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text


FORMAT = "rcmf_one_demo_component_swap_preparation_12a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_component_swap_12a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_or_validate(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if read_json(path) != dict(value):
            raise ValueError(f"Existing immutable manifest differs: {path}")
        return
    atomic_write_json(path, dict(value))


def atomic_torch_save(value: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pt.tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        torch.save(dict(value), temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def project_path(value: Any) -> Path:
    return Path(str(value))


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def require_unique_attempt(path: Path, attempt_id: str) -> None:
    if not path.exists():
        return
    for row in read_jsonl(path):
        if str(row.get("attempt_id")) == attempt_id:
            raise ValueError(f"Duplicate attempt ID: {attempt_id}")


def selector_manifest(identity: Any, source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": identity.name,
        "source_run": str(source["source_run"]),
        "source_commit": str(source["source_commit"]),
        "archive_identity": str(source["archive_identity"]),
        "lambda_root": str(identity.root),
        "ensemble_path": str(identity.ensemble_path),
        "ensemble_sha256": identity.ensemble_sha256,
        "member_paths": [str(value) for value in identity.member_paths],
        "member_sha256": list(identity.member_sha256),
        "parameter_count": identity.parameter_count,
        "key_dimension": identity.key_dim,
        "calibrated_intercept": identity.intercept,
    }


def writer_reader_manifest(identity: Any, source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": identity.name,
        "source_run": str(source["source_run"]),
        "source_commit": str(source["source_commit"]),
        "archive_identity": str(source["archive_identity"]),
        "lambda_checkpoint_path": str(identity.checkpoint_path),
        "checkpoint_sha256": identity.checkpoint_sha256,
        "writer_sha256": identity.writer_sha256,
        "reader_sha256": identity.reader_sha256,
        "writer_parameter_count": identity.writer_parameter_count,
        "reader_parameter_count": identity.reader_parameter_count,
        "selected_epoch": int(source["selected_epoch"]),
        "architecture": {
            "memory_views": [8, 4096],
            "payload": [8, 256],
            "reader_layers": [7, 14, 21, 28],
            "field_A": [960, 8, 256],
            "field_B": [8, 256],
        },
    }


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_12a"]
    if str(settings["run_uuid"]) != "rcmf_one_demo_component_swap_12a_20260831_001":
        raise ValueError("EXP-035A run UUID differs")
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-035A seed differs")
    if git_head() != args.source_head:
        raise ValueError("Working tree HEAD differs from the declared source head")

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    require_unique_attempt(args.artifact_dir / "attempts.jsonl", args.attempt_id)
    started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="component_identity_field_preparation",
        command=list(os.sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "old_data_manifest": sha256_file(project_path(settings["shared"]["old_data_manifest"])),
            "fresh_data_manifest": sha256_file(project_path(settings["shared"]["fresh_data_manifest"])),
            "shuffle_manifest": sha256_file(project_path(settings["shared"]["shuffle_manifest"])),
        },
        parent_attempt_id="none",
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=240,
    ) as attempt:
        shared = settings["shared"]
        old_data_path = project_path(shared["old_data_manifest"])
        fresh_data_path = project_path(shared["fresh_data_manifest"])
        old_source_path = project_path(shared["old_source_cache"])
        fresh_source_path = project_path(shared["fresh_source_cache"])
        provenance_path = project_path(shared["memory_provenance"])
        shuffle_path = project_path(shared["shuffle_manifest"])
        fresh_shuffle_path = project_path(shared["fresh_shuffle_manifest"])
        transition_cache_path = project_path(shared["transition_cache"])
        for path in (
            old_data_path,
            fresh_data_path,
            old_source_path,
            fresh_source_path,
            provenance_path,
            shuffle_path,
            fresh_shuffle_path,
            transition_cache_path,
        ):
            if not path.is_file():
                raise FileNotFoundError(path)

        if sha256_file(transition_cache_path) != str(shared["transition_cache_sha256"]):
            raise ValueError("Transition multiview cache SHA differs")
        if sha256_file(shuffle_path) != str(shared["shuffle_manifest_sha256"]):
            raise ValueError("EXP-031A common shuffle SHA differs")
        old_data, fresh_data = read_json(old_data_path), read_json(fresh_data_path)
        old_shuffle, fresh_shuffle = read_json(shuffle_path), read_json(fresh_shuffle_path)
        if old_shuffle != fresh_shuffle:
            raise ValueError("EXP-031A and EXP-034B shuffle mappings differ")
        if old_data["train_task_ids"] != fresh_data["train_task_ids"] or old_data[
            "heldout_task_ids"
        ] != fresh_data["heldout_task_ids"]:
            raise ValueError("Old/fresh task splits differ")
        if old_data["memory_provenance_sha256"] != fresh_data["memory_provenance_sha256"]:
            raise ValueError("Old/fresh memory ledgers differ")

        old_source = torch.load(old_source_path, map_location="cpu", weights_only=False)
        fresh_source = torch.load(fresh_source_path, map_location="cpu", weights_only=False)
        if old_source["ordered_transition_ids"] != fresh_source["ordered_transition_ids"]:
            raise ValueError("Old/fresh transition ordering differs")
        if not torch.equal(old_source["memory_views"], fresh_source["memory_views"]):
            raise ValueError("Old/fresh writer inputs differ")
        if str(old_source["memory_view_sha256"]) != str(shared["memory_view_sha256"]):
            raise ValueError("Memory view tensor SHA differs")

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        selectors, selector_ids = {}, {}
        for name in ("old", "fresh"):
            source = settings["selectors"][name]
            selector, identity = load_selector_package(
                name=name,
                root=project_path(source["root"]),
                expected_ensemble_sha256=str(source["ensemble_sha256"]),
                expected_member_sha256=list(source["member_sha256"]),
                device=device,
            )
            if identity.key_dim != 960:
                raise ValueError("Selector q/k shape is not field compatible")
            selectors[name], selector_ids[name] = selector, identity

        writer_readers, writer_reader_ids = {}, {}
        for name in ("old", "fresh"):
            source = settings["writer_readers"][name]
            writer, reader, identity = load_writer_reader_package(
                name=name,
                checkpoint_path=project_path(source["checkpoint"]),
                expected_checkpoint_sha256=str(source["checkpoint_sha256"]),
                device=device,
            )
            writer_readers[name] = (writer, reader)
            writer_reader_ids[name] = identity

        transition_cache = torch.load(
            transition_cache_path, map_location="cpu", weights_only=False
        )
        transition_values = transition_cache["representations"]["final_layer"].to(
            device=device, dtype=torch.float32
        )
        stored_keys = {
            "old": old_source["memory_keys"].to(device=device, dtype=torch.float32),
            "fresh": fresh_source["memory_keys"].to(device=device, dtype=torch.float32),
        }
        key_errors = {}
        for name in ("old", "fresh"):
            expected_key_sha = str(settings["selectors"][name]["memory_key_sha256"])
            if tensor_sha256(stored_keys[name].cpu()) != expected_key_sha:
                raise ValueError(f"{name} stored memory-key SHA differs")
            recomputed = selectors[name].key(transition_values)
            error = float((recomputed - stored_keys[name]).abs().max())
            if error > 1.0e-5:
                raise RuntimeError(f"{name} selector key reconstruction failed: {error}")
            key_errors[name] = error

        old_selector_audit = read_json(
            project_path(settings["writer_readers"]["old"]["native_deployment_field"]).parents[1]
            / "data/selector_decomposition_audit.json"
        )
        fresh_factors_path = project_path(settings["selectors"]["fresh"]["factor_path"])
        if sha256_file(fresh_factors_path) != str(settings["selectors"]["fresh"]["factor_sha256"]):
            raise ValueError("Fresh selector factor artifact SHA differs")
        fresh_factors = torch.load(fresh_factors_path, map_location="cpu", weights_only=False)
        direct_errors = {
            "old": dict(old_selector_audit["errors"]),
            "fresh": dict(fresh_factors["decomposition_errors"]),
        }
        if max(float(value) for errors in direct_errors.values() for value in errors.values()) > 1.0e-5:
            raise RuntimeError("Selector direct/factorized score equivalence failed")

        ordered_ids = [str(value) for value in old_source["ordered_transition_ids"]]
        provenance = read_jsonl(provenance_path)
        provenance_by_id = {str(row["transition_id"]): row for row in provenance}
        if set(provenance_by_id) != set(ordered_ids):
            raise ValueError("Memory provenance does not cover the ledger")
        train_tasks = set(str(value) for value in old_data["train_task_ids"])
        heldout_tasks = [str(value) for value in old_data["heldout_task_ids"]]
        if len(heldout_tasks) != int(shared["heldout_task_count"]):
            raise ValueError("Heldout task count differs")
        train_ids = select_leakage_safe_memory_ids(
            ordered_transition_ids=ordered_ids,
            parent_task_by_transition={
                transition_id: str(row["parent_task_id"])
                for transition_id, row in provenance_by_id.items()
            },
            train_task_ids=sorted(train_tasks),
            heldout_task_ids=heldout_tasks,
        )
        if len(train_ids) != int(shared["field_memory_count"]):
            raise ValueError("Leakage-safe field memory count differs")
        heldout_set = set(heldout_tasks)
        leaked = [
            transition_id
            for transition_id in train_ids
            if str(provenance_by_id[transition_id]["parent_task_id"]) in heldout_set
        ]
        if leaked:
            raise RuntimeError("Heldout-parent memories entered the 401-memory field")
        position = {value: index for index, value in enumerate(ordered_ids)}
        train_indices = torch.tensor([position[value] for value in train_ids], dtype=torch.long)
        rho_map = old_data["rho_by_transition_id"]
        rho_full = torch.tensor(
            [float(rho_map[value]) for value in ordered_ids], device=device, dtype=torch.float32
        )
        rho_train = torch.tensor(
            [float(rho_map[value]) for value in train_ids], device=device, dtype=torch.float32
        )
        train_permutation = permutation_from_rows(
            train_ids, old_shuffle["model_training_bank"]["rows"]
        ).to(device)
        full_permutation = permutation_from_rows(
            ordered_ids, old_shuffle["complete_deployment_bank"]["rows"]
        ).to(device)
        common_shuffle_sha = canonical_sha256(old_shuffle["model_training_bank"])
        if common_shuffle_sha != str(shared["model_training_shuffle_sha256"]):
            raise ValueError("Common 401-memory shuffle identity differs")

        memory_views = old_source["memory_views"].to(device=device, dtype=torch.float32)
        payloads = {}
        with torch.no_grad():
            for name in ("old", "fresh"):
                payloads[name] = writer_readers[name][0](memory_views)
                if tuple(payloads[name].shape) != (499, 8, 256):
                    raise ValueError("Writer payload shape differs")
                if not bool(torch.isfinite(payloads[name]).all()):
                    raise ValueError("Writer payload contains NaN/Inf")

        native = {}
        for name in ("old", "fresh"):
            source = settings["writer_readers"][name]
            native_path = project_path(source["native_deployment_field"])
            if sha256_file(native_path) != str(source["native_deployment_field_sha256"]):
                raise ValueError(f"{name} native deployment field SHA differs")
            historical = torch.load(native_path, map_location="cpu", weights_only=False)
            rebuilt = compile_field_pair(
                keys=stored_keys[name],
                payloads=payloads[name],
                rho=rho_full,
                permutation=full_permutation,
            )
            errors = field_rebuild_errors(fields=rebuilt, historical=historical)
            if max(errors.values()) > 1.0e-5:
                raise RuntimeError(f"{name} 499-memory native reconstruction failed: {errors}")
            native[name] = {
                "historical_path": str(native_path),
                "historical_sha256": sha256_file(native_path),
                "rebuild_max_abs": errors,
                "selector_key_max_abs": key_errors[name],
                "selector_score_errors": direct_errors[name],
            }

        field_root = args.artifact_dir / "fields"
        field_rows = {}
        for cell in CELL_NAMES:
            _, _, selector_name, writer_reader_name = condition_parts(f"{cell}-C")
            train_keys = stored_keys[selector_name][train_indices.to(device)]
            train_payloads = payloads[writer_reader_name][train_indices.to(device)]
            fields = compile_field_pair(
                keys=train_keys,
                payloads=train_payloads,
                rho=rho_train,
                permutation=train_permutation,
            )
            remove_errors = remove_restore_error(
                fields=fields,
                key=train_keys[0],
                payload=train_payloads[0],
                shuffled_payload=train_payloads[train_permutation[0]],
                rho=float(rho_train[0]),
            )
            if max(remove_errors.values()) > 1.0e-5:
                raise RuntimeError(f"{cell} remove/restore failed")
            field_path = field_root / f"{cell}.pt"
            atomic_torch_save(
                {
                    "format": "rcmf_one_demo_component_swap_field_12a_v1",
                    "run_uuid": str(settings["run_uuid"]),
                    "source_head": args.source_head,
                    "cell": cell,
                    "selector_package": selector_name,
                    "writer_reader_package": writer_reader_name,
                    "memory_count": len(train_ids),
                    "memory_ids": train_ids,
                    "common_shuffle_sha256": common_shuffle_sha,
                    **{key: value.detach().cpu() for key, value in fields.items()},
                },
                field_path,
            )
            field_rows[cell] = {
                "path": str(field_path),
                "sha256": sha256_file(field_path),
                "selector_package": selector_name,
                "writer_reader_package": writer_reader_name,
                "memory_count": len(train_ids),
                "tensors": {key: tensor_audit(value) for key, value in fields.items()},
                "remove_restore_max_abs": remove_errors,
            }

        native_401 = {
            "OO": ("old", "old"),
            "FF": ("fresh", "fresh"),
        }
        for cell, (selector_name, writer_reader_name) in native_401.items():
            source = settings["writer_readers"][writer_reader_name]
            correct = torch.load(
                project_path(source["heldout_correct_field"]), map_location="cpu", weights_only=False
            )
            shuffle = torch.load(
                project_path(source["heldout_shuffle_field"]), map_location="cpu", weights_only=False
            )
            rebuilt = torch.load(Path(field_rows[cell]["path"]), map_location="cpu", weights_only=False)
            errors = {
                "correct_A": float((rebuilt["A"] - correct["A"]).abs().max()),
                "correct_B": float((rebuilt["B"] - correct["B"]).abs().max()),
                "shuffle_A": float((rebuilt["shuffled_A"] - shuffle["A"]).abs().max()),
                "shuffle_B": float((rebuilt["shuffled_B"] - shuffle["B"]).abs().max()),
            }
            if max(errors.values()) > 1.0e-5:
                raise RuntimeError(f"{cell} historical 401-memory reconstruction failed: {errors}")
            field_rows[cell]["historical_401_rebuild_max_abs"] = errors

        prompt_metadata = appworld_renderer_metadata(str(settings["prompt_profile"]))
        retained_demo_sha256 = sha256_text(
            full_demo_sections(get_system_prompt("full_demo"))[
                "demo_1_with_instruction_prefix"
            ]
        )
        if str(prompt_metadata["initial_messages_sha256"]) != str(
            settings["prompt"]["initial_messages_sha256"]
        ):
            raise ValueError("One-demo prompt identity differs")
        if retained_demo_sha256 != str(
            settings["prompt"]["retained_demo_sha256"]
        ):
            raise ValueError("Retained demo identity differs")
        prompt_metadata["retained_demo_sha256"] = retained_demo_sha256

        task_list_sha = canonical_sha256(heldout_tasks)
        task_manifest = {
            "format": "rcmf_one_demo_component_swap_tasks_12a_v1",
            "run_uuid": str(settings["run_uuid"]),
            "global_seed": GLOBAL_SEED,
            "task_ids": heldout_tasks,
            "task_count": len(heldout_tasks),
            "task_list_sha256": task_list_sha,
            "source_data_manifest": str(old_data_path),
            "source_data_manifest_sha256": sha256_file(old_data_path),
            "model_training_memory_count": len(train_ids),
            "heldout_parent_memory_count": 0,
            "dev_used": False,
            "first37_used": False,
            "test_normal_used": False,
            "test_challenge_used": False,
            "frozen_before_generation": True,
        }
        write_or_validate(args.artifact_dir / "manifests/heldout_tasks.json", task_manifest)

        dependency_graph = {
            "format": "rcmf_one_demo_component_dependency_graph_12a_v1",
            "selector_package": [
                "state 10-view representation -> frozen selector state factors -> q(s)",
                "transition 10-view representation -> frozen selector transition factors -> k(i)",
                "three-member calibration/ensemble -> exact 960D q/k decomposition",
            ],
            "writer_reader_package": [
                "shared raw transition 8-view tensor -> frozen writer -> 8x256 payload",
                "fixed field read -> frozen reader layers [7,14,21,28] -> frozen Qwen",
            ],
            "shared_infrastructure": [
                "raw 499-memory ledger and 401-memory train subset",
                "one-demo prompt renderer",
                "parent-normalized rho",
                "reversible whole-bank field algebra",
                "AppWorld 0.1.0 runner/evaluator and frozen generation configuration",
            ],
            "cross_cell_path": [
                "selector S computes all memory keys",
                "writer W compiles payloads from the same raw transitions",
                "401-memory field is rebuilt from scratch",
                "runtime query uses selector S",
                "field read injection uses reader W",
            ],
            "compatibility_adapter": False,
            "manual_renormalization": False,
            "scale_change": False,
        }
        dependency_graph["graph_sha256"] = canonical_sha256(dependency_graph)
        write_or_validate(args.artifact_dir / "manifests/dependency_graph.json", dependency_graph)

        package_manifest = {
            "format": "rcmf_one_demo_component_package_manifest_12a_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_head": args.source_head,
            "global_seed": GLOBAL_SEED,
            "selectors": {
                name: selector_manifest(selector_ids[name], settings["selectors"][name])
                for name in ("old", "fresh")
            },
            "writer_readers": {
                name: writer_reader_manifest(
                    writer_reader_ids[name], settings["writer_readers"][name]
                )
                for name in ("old", "fresh")
            },
            "shared": {
                "prompt": prompt_metadata,
                "transition_cache_path": str(transition_cache_path),
                "transition_cache_sha256": sha256_file(transition_cache_path),
                "memory_view_sha256": str(old_source["memory_view_sha256"]),
                "memory_ledger_path": str(provenance_path),
                "memory_ledger_sha256": sha256_file(provenance_path),
                "train_task_ids": list(old_data["train_task_ids"]),
                "heldout_task_ids": heldout_tasks,
                "task_list_sha256": task_list_sha,
                "field_memory_ids_sha256": canonical_sha256(train_ids),
                "field_memory_count": len(train_ids),
                "shuffle_manifest_path": str(shuffle_path),
                "shuffle_manifest_sha256": sha256_file(shuffle_path),
                "model_training_shuffle_sha256": common_shuffle_sha,
                "old_fresh_shuffle_exact_match": True,
                "qwen": "Qwen/Qwen3-8B",
                "appworld": "0.1.0",
                "field_shapes": {"A": [960, 8, 256], "B": [8, 256]},
                "runtime_retrieval": False,
                "runtime_top_k": False,
                "runtime_per_memory_scoring": False,
                "raw_memory_prompt_text": False,
            },
            "native_reconstruction": native,
            "fields": field_rows,
            "optimizer_steps": 0,
            "parameter_updates": 0,
        }
        package_manifest["manifest_sha256"] = canonical_sha256(package_manifest)
        write_or_validate(
            args.artifact_dir / "manifests/component_package_manifest.json",
            package_manifest,
        )

        rows = []
        for task_index, task_id in enumerate(heldout_tasks):
            for order_index, condition in enumerate(condition_order_for_task(task_index)):
                cell, binding, selector_name, writer_reader_name = condition_parts(condition)
                rows.append(
                    {
                        "task_id": task_id,
                        "task_index": task_index,
                        "order_index": order_index,
                        "condition": condition,
                        "cell": cell,
                        "binding": binding,
                        "selector_package": selector_name,
                        "writer_reader_package": writer_reader_name,
                        "field_path": field_rows[cell]["path"],
                        "field_sha256": field_rows[cell]["sha256"],
                        "memory_count": 401,
                        "runtime_retrieval": False,
                        "runtime_per_memory_scoring": False,
                        "student_prompt_contains_raw_memory": False,
                    }
                )
        condition_manifest = {
            "format": "rcmf_one_demo_component_swap_conditions_12a_v1",
            "run_uuid": str(settings["run_uuid"]),
            "source_head": args.source_head,
            "global_seed": GLOBAL_SEED,
            "prompt_profile": str(settings["prompt_profile"]),
            "prompt_initial_messages_sha256": str(
                settings["prompt"]["initial_messages_sha256"]
            ),
            "task_ids": heldout_tasks,
            "task_list_sha256": task_list_sha,
            "conditions": list(CONDITIONS),
            "logical_trajectory_count": len(rows),
            "rows": rows,
            "counterbalanced_condition_order": True,
            "frozen_before_generation": True,
            "outcomes_used": False,
        }
        condition_manifest["manifest_sha256"] = canonical_sha256(condition_manifest)
        write_or_validate(
            args.artifact_dir / "manifests/condition_manifest.json", condition_manifest
        )

        preflight = {
            "format": FORMAT,
            "run_uuid": str(settings["run_uuid"]),
            "source_head": args.source_head,
            "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
            "task_count": len(heldout_tasks),
            "condition_count": len(CONDITIONS),
            "trajectory_count": len(rows),
            "memory_count": len(train_ids),
            "prompt_identity_passed": True,
            "task_memory_leak_count": len(leaked),
            "common_shuffle_exact_match": True,
            "native_reconstruction_passed": True,
            "cross_shapes_compatible": True,
            "selector_key_rebuild_max_abs": key_errors,
            "selector_score_errors": direct_errors,
            "field_rows": field_rows,
            "elapsed_seconds": time.perf_counter() - started,
            "optimizer_steps": 0,
            "parameter_updates": 0,
            "passed": True,
        }
        preflight["preflight_sha256"] = canonical_sha256(preflight)
        write_or_validate(args.artifact_dir / "preflight/identity_and_fields.json", preflight)
        attempt.progress(
            status="component_fields_frozen",
            completed_units=len(rows),
            total_units=len(rows),
            latest_validated_checkpoint=str(
                args.artifact_dir / "manifests/component_package_manifest.json"
            ),
        )
        print(json.dumps(preflight, indent=2))


if __name__ == "__main__":
    main()
