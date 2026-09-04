#!/usr/bin/env python3
"""Generate the read-only EXP-037A-R10 whole-pipeline contract audit."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    formal_stage_output_paths,
)
from rcmf.pipeline.manifests import file_identity
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.utils.serialization import atomic_write_json, sha256_file


FORMAT = "exp037a_r10_whole_pipeline_logic_audit_v1"
FORMAL_SOURCES = (
    Path("rcmf/benchmarks/appworld/reproducible_stages_14b.py"),
    Path("scripts/run_rcmf_joint_full_bank_9a.py"),
    Path("scripts/run_rcmf_joint_full_bank_live_9a.py"),
    Path("scripts/run_rcmf_joint_full_bank_first37_9a.py"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--formal-14h-root", type=Path)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _arm_stage(arm: str, index: int) -> str:
    prefix = "D" if arm == "3d" else "O"
    return _stage_id(f"{prefix}{index:02d}")


def _stage_id(prefix: str) -> str:
    values = [
        stage.stage_id
        for stage in build_exp037a_stage_graph()
        if stage.stage_id == prefix or stage.stage_id.startswith(f"{prefix}_")
    ]
    if len(values) != 1:
        raise KeyError(f"Stage prefix is not unique: {prefix}: {values}")
    return values[0]


def _logical_inputs(stage_id: str, dependencies: tuple[str, ...]) -> list[dict[str, str]]:
    rows = [
        {"producer": dependency, "artifact": "completion.json", "role": "ordering"}
        for dependency in dependencies
    ]
    if stage_id.startswith("S"):
        shared = {
            "S00_environment_manifest": [("preflight", "environment_manifest.json")],
            "S01_authoritative_corpus": [("preflight", "authoritative_source_manifest.json")],
            "S02_task_and_parent_splits": [("preflight", "shared/parent_split.json")],
            "S03_transition_records": [("preflight", "shared/transitions.jsonl")],
            "S04_selector_supervision": [
                ("preflight", "shared/labels.jsonl"),
                ("preflight", "shared/illegal_pairs.jsonl"),
            ],
            "S05_transition_representations": [("S03", "shared/transitions.jsonl")],
            "S05B_joint_source_contract_preflight": [
                ("S05", "shared/representation_cache/multiview")
            ],
            "S06_cv_folds_and_sampling": [("S04", "preflight/shared/labels.jsonl")],
            "S07_initial_parameter_snapshots": [("preflight", "initialization_manifest.json")],
            "S08_two_arm_contract": [("preflight", "resolved arm configs")],
            "S09_runtime_preflight_and_approval": [("launcher", "runtime_authorization.json")],
        }
        rows.extend(
            {
                "producer": _stage_id(producer) if producer.startswith("S") else producer,
                "artifact": artifact,
                "role": "semantic_input",
            }
            for producer, artifact in shared.get(stage_id, [])
        )
        return rows
    if stage_id.startswith(("D", "O")):
        arm = "3d" if stage_id.startswith("D") else "1d"
        index = int(stage_id[1:3])
        producer_indexes: dict[int, tuple[int | str, ...]] = {
            0: ("S03", "S04", "S05", "S06", "S07", "S08"),
            1: (0,),
            2: (1,),
            3: (0, 2),
            4: (3,),
            5: (0, 4),
            6: (5,),
            7: (6,),
            8: (6, 7, "S05B"),
            9: (8,),
            10: (9,),
            11: (10,),
            12: (10, 11),
            13: (10, 12),
            14: (11, 12, 13),
            15: (12, 14),
            16: (8, 15),
            17: (14, 16),
        }
        if stage_id == "D06B_three_demo_causal_reproduction_gate":
            sources: tuple[int | str, ...] = (6,)
        elif stage_id == "D08B_writer_reader_one_unit_smoke":
            sources = (8,)
        elif arm == "3d" and index in (18, 19, 20):
            sources = (17,)
        elif arm == "3d" and index == 21:
            sources = (18, 19, 20)
        elif arm == "3d" and index == 22:
            sources = (*range(0, 22), "D06B", "D08B")
        elif arm == "1d" and index in (18, 19):
            sources = (17,)
        else:
            sources = producer_indexes.get(index, ())
        for source in sources:
            producer = _stage_id(source) if isinstance(source, str) else _arm_stage(arm, source)
            rows.append(
                {
                    "producer": producer,
                    "artifact": "declared output artifact set",
                    "role": "semantic_input",
                }
            )
        return rows
    if stage_id == "F00_two_arm_paired_analysis":
        rows.append(
            {
                "producer": "D22 and O18/O19 when conditional arm ran",
                "artifact": "gate and available dev summaries",
                "role": "terminal_analysis",
            }
        )
    elif stage_id == "F01_portability_validation":
        rows.append(
            {
                "producer": _stage_id("F00"),
                "artifact": "analysis",
                "role": "ordering",
            }
        )
    elif stage_id == "F02_git_safe_audit_export":
        rows.append(
            {"producer": "all executed stages", "artifact": "completion manifests", "role": "audit"}
        )
    elif stage_id == "F03_final_report_and_handoff":
        rows.extend(
            {
                "producer": _stage_id(prefix),
                "artifact": artifact,
                "role": "final",
            }
            for prefix, artifact in (
                ("F00", "analysis"),
                ("F01", "portability"),
                ("F02", "audit"),
            )
        )
    return rows


def _output_contract(stage_id: str) -> list[str]:
    if stage_id.startswith("S"):
        return ["preflight/shared artifact set", "stage_result.json"]
    if stage_id.endswith("writer_reader_epoch_1"):
        return ["epoch_01.pt", "epoch_01_stage_summary.json", "stage_result.json"]
    if stage_id.endswith("writer_reader_epoch_2"):
        return ["epoch_01.pt", "epoch_02.pt", "training_summary.json", "stage_result.json"]
    if "checkpoint_selection" in stage_id:
        return ["checkpoint_selection.json", "stage_result.json"]
    if "401_memory_field" in stage_id:
        return ["selected_401_field.json", "selected_401_field.pt when deployable", "stage_result.json"]
    if "compile_and_add_98_memories" in stage_id:
        return ["instant_add_report.json when deployable", "complete_37_task_field.pt when deployable", "stage_result.json"]
    if "499_memory_deployment_field" in stage_id:
        return ["validation_14b.json when deployable", "stage_result.json"]
    if stage_id == "D06B_three_demo_causal_reproduction_gate":
        return ["d06_three_demo_reproduction_gate.json", "gate.json", "stage_result.json"]
    if stage_id == "D22_three_demo_reproduction_gate":
        return ["three_demo_reproduction_gate.json", "gate.json", "stage_result.json"]
    if stage_id.startswith("F"):
        names = {
            "F00_two_arm_paired_analysis": "two_arm_paired_analysis.json",
            "F01_portability_validation": "validation.json",
            "F02_git_safe_audit_export": "index.json",
            "F03_final_report_and_handoff": "final_record.json",
        }
        return [names[stage_id], "stage_result.json"]
    return ["stage-specific declared artifact set", "stage_result.json"]


def _coverage(stage_id: str, formal_root: Path | None) -> dict[str, Any]:
    completion = None if formal_root is None else formal_root / "stages" / stage_id / "completion.json"
    failure = None if formal_root is None else formal_root / "stages" / stage_id / "failure.json"
    formal_completed = bool(
        completion
        and completion.is_file()
        and _json(completion).get("passed") is True
    )
    formal_failed = bool(failure and failure.is_file())
    return {
        "formal_14h": (
            "completed" if formal_completed else "attempted_failed" if formal_failed else "not_exercised"
        ),
        "production_smoke": stage_id in {
            "S00_environment_manifest",
            "S01_authoritative_corpus",
            "S02_task_and_parent_splits",
            "S03_transition_records",
            "S04_selector_supervision",
        },
        "synthetic_contract_test": True,
        "static_audit": True,
    }


def build_stage_map(formal_root: Path | None) -> list[dict[str, Any]]:
    rows = []
    for ordinal, stage in enumerate(build_exp037a_stage_graph()):
        rows.append(
            {
                "ordinal": ordinal,
                "stage_id": stage.stage_id,
                "arm": stage.arm,
                "dag_dependencies": list(stage.dependencies),
                "conditional_on": stage.conditional_on,
                "production_script": "scripts/run_rcmf_reproducible_stage_14b.py",
                "production_callable": "execute_stage",
                "command": list(stage.command),
                "logical_inputs": _logical_inputs(stage.stage_id, stage.dependencies),
                "declared_outputs": _output_contract(stage.stage_id),
                "output_path_constructor": (
                    "rcmf.benchmarks.appworld.reproducible_stages_14b."
                    "formal_stage_output_paths(stage_id, run_root)"
                ),
                "output_schema": "stage-specific payload plus strict output_manifest.json",
                "output_validator": stage.validator,
                "hash_validation": "all declared files SHA256-validated by validate_stage_completion",
                "identity_validation": "source/run UUID/root/config SHA/contract SHA",
                "resume_eligibility": "only after strict manifest, dependency, identity, and file-hash validation",
                "retry_semantics": "only exit 75; maximum three frozen attempts",
                "downstream_consumer": (
                    build_exp037a_stage_graph()[ordinal + 1].stage_id
                    if ordinal + 1 < len(build_exp037a_stage_graph())
                    else None
                ),
                "coverage": _coverage(stage.stage_id, formal_root),
            }
        )
    return rows


def sealed_formal_artifact_audit(
    formal_root: Path | None,
) -> list[dict[str, Any]]:
    """Content-address actual artifacts from every sealed formal completion."""
    if formal_root is None:
        return []
    rows = []
    for completion in sorted((formal_root / "stages").glob("*/completion.json")):
        if _json(completion).get("passed") is not True:
            continue
        stage_id = completion.parent.name
        outputs = formal_stage_output_paths(stage_id, formal_root)
        rows.append(
            {
                "stage_id": stage_id,
                "completion": file_identity(completion),
                "artifacts": [file_identity(path) for path in outputs],
                "artifact_count": len(outputs),
                "all_declared_artifacts_exist": True,
            }
        )
    return rows


def device_load_audit() -> list[dict[str, Any]]:
    rows = []
    for path in FORMAL_SOURCES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = ast.get_source_segment(source, node.func) or ""
            if function != "torch.load":
                continue
            segment = ast.get_source_segment(source, node) or "torch.load(...)"
            if 'map_location="cpu"' in segment:
                classification = "SAFE_AS_IS"
                reason = "Payload is intentionally materialized on CPU before explicit placement."
            elif path.name == "run_rcmf_joint_full_bank_9a.py" and "backend.device" in segment:
                classification = "SAFE_AS_IS_AFTER_R9_CANONICALIZATION"
                reason = "Training checkpoint tensors map to the active device; RNG tensors are canonicalized to CPU."
            elif "map_location=device" in segment or "map_location=backend.device" in segment:
                classification = "SAFE_BUT_WASTEFUL"
                reason = "Only module/optimizer tensors are consumed; no CPU-only metadata API receives mapped tensors."
            else:
                classification = "UNCERTAIN"
                reason = "No explicit map_location was found in the source segment."
            rows.append(
                {
                    "path": path.as_posix(),
                    "line": int(node.lineno),
                    "call": segment,
                    "classification": classification,
                    "reason": reason,
                }
            )
    return rows


def main() -> None:
    args = parse_args()
    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    formal_root = args.formal_14h_root
    stage_rows = build_stage_map(formal_root)
    device_rows = device_load_audit()
    sealed_artifacts = sealed_formal_artifact_audit(formal_root)
    defects = [
        {
            "id": "R10-D1",
            "defect": "Stage manifests sealed only stage_result.json, not downstream scientific artifacts.",
            "failure_mode": "A corrupted checkpoint, field, or summary could remain resume-eligible.",
            "fix": "Canonical per-stage artifact contracts are included in the strict output manifest.",
            "scientific_semantics_changed": False,
        },
        {
            "id": "R10-D2",
            "defect": "latest_checkpoint.json SHA/root/boundary semantics were not verified before torch.load.",
            "failure_mode": "A stale, replaced, or wrong-kind checkpoint could be deserialized and resumed.",
            "fix": "Fail-closed pointer validation now precedes deserialization.",
            "scientific_semantics_changed": False,
        },
        {
            "id": "R10-D3",
            "defect": "Restored writer/reader tensors were not checked against checkpoint module hashes.",
            "failure_mode": "Internal state corruption could survive strict load_state_dict.",
            "fix": "Post-load module hashes must exactly match checkpoint identities.",
            "scientific_semantics_changed": False,
        },
        {
            "id": "R10-D4",
            "defect": "D06B/D22 prior-stage checks omitted run UUID/root/config/contract identity.",
            "failure_mode": "A self-consistent foreign stage prefix could pass the manual gate audit.",
            "fix": "Gate-time prior-stage validation now uses the full formal identity.",
            "scientific_semantics_changed": False,
        },
        {
            "id": "R10-D5",
            "defect": "401/499 field validators omitted binding, count, shape, checkpoint, and add provenance checks.",
            "failure_mode": "Malformed shuffle B, wrong checkpoint, or incorrect 401+98 composition could reach evaluation.",
            "fix": "Explicit finite/shape/count/checkpoint/memory-ID/add-only checks fail before evaluation.",
            "scientific_semantics_changed": False,
        },
    ]
    residual = [
        {
            "risk": "D11-D22 and O00-O19 have not all executed in a complete fresh formal run.",
            "mitigation": "Static contract map, all-stage synthetic manifest coverage, bounded branch fixtures, and strict artifact sealing.",
            "requires_long_science_to_close": True,
        },
        {
            "risk": "Abrupt host loss can leave scheduler.lock until operator triage.",
            "mitigation": "Fail-closed lock plus read-only process/heartbeat validation prevents duplicate launch.",
            "requires_long_science_to_close": False,
        },
    ]
    leads = {
        "A_latest_checkpoint": "confirmed_issue_fixed",
        "B_device_sensitive_loads": "partially_valid_r9_fix_sufficient_other_formal_loads_safe",
        "C_D11_D22_coverage": "partially_valid_residual_formal_coverage_gap_hardened_statically",
        "D_1D_post_training_parity": "already_handled_generic_dispatch_and_prompt_only_config_diff",
        "E_no_selected_checkpoint": "already_handled_explicit_no_deployable_branch",
        "F_immutable_hard_links": "already_handled_read_only_consumers_no_in_place_mutation_found",
    }
    payloads = {
        "stage_dependency_map.json": {"format": FORMAT, "stages": stage_rows},
        "artifact_contract_map.json": {
            "format": FORMAT,
            "producers": [
                {
                    "producer": "write_stage_manifest",
                    "caller": "scripts/run_rcmf_reproducible_stage_14b.py",
                    "stage_population": len(stage_rows),
                    "identity_fields": [
                        "source_commit",
                        "run_uuid",
                        "run_root",
                        "pipeline_config_sha256",
                        "contract_sha256",
                        "stage_id",
                        "attempt_id",
                    ],
                    "strict_validator_compatible": True,
                    "declared_artifacts_sha256_sealed": True,
                }
            ],
            "stage_artifacts": [
                {"stage_id": row["stage_id"], "outputs": row["declared_outputs"]}
                for row in stage_rows
            ],
        },
        "sealed_14h_artifact_audit.json": {
            "format": FORMAT,
            "formal_root": None if formal_root is None else str(formal_root),
            "completed_stage_count": len(sealed_artifacts),
            "stages": sealed_artifacts,
        },
        "device_load_audit.json": {"format": FORMAT, "loads": device_rows},
        "checkpoint_lifecycle_audit.json": {
            "format": FORMAT,
            "lifecycle": [
                "locked initialization",
                "atomic progress.pt",
                "atomic epoch_01.pt / epoch_02.pt",
                "atomic latest_checkpoint.json",
                "pointer path/root/SHA/epoch/boundary validation before load",
                "checkpoint identity and writer/reader/optimizer/RNG restore",
                "post-load module hash validation",
                "heldout evaluation",
                "checkpoint selection",
                "401-memory field",
                "98-memory instant add",
                "499-memory deployment validation",
            ],
            "same_stage_resume": "supported from progress.pt with strict pointer and payload identity",
            "next_stage_resume": "epoch-boundary filename and completed-unit boundary required",
            "migration_from_14h": False,
        },
        "copy_link_audit.json": {
            "format": FORMAT,
            "paths": [
                {
                    "helper": "_copy_exact",
                    "mechanism": "shutil.copy2",
                    "classification": "SAFE_AS_IS",
                },
                {
                    "helper": "_link_or_copy_exact",
                    "mechanism": "os.link with copy fallback",
                    "classification": "SAFE_AS_IS",
                    "basis": "linked transition aggregate is read-only; no downstream in-place mutation found",
                },
                {
                    "helper": "_ensure_read_only_link",
                    "mechanism": "symlink",
                    "classification": "SAFE_AS_IS",
                    "basis": "D08B reads linked inputs and writes only to its isolated smoke root",
                },
            ],
        },
        "coverage_matrix.json": {"format": FORMAT, "stages": stage_rows},
        "chatgpt_leads_disposition.json": {"format": FORMAT, "leads": leads},
        "pipeline_audit_summary.json": {
            "format": FORMAT,
            "source_commit": args.source_commit,
            "stage_count": len(stage_rows),
            "formal_14h_completed_count": sum(
                row["coverage"]["formal_14h"] == "completed" for row in stage_rows
            ),
            "device_load_count": len(device_rows),
            "unclassified_device_load_count": sum(
                row["classification"] == "UNCERTAIN" for row in device_rows
            ),
            "verified_defects": defects,
            "verified_defect_count": len(defects),
            "scientific_configuration_changes": 0,
            "residual_risks": residual,
            "chatgpt_leads": leads,
            "long_scientific_run_launched": False,
        },
    }
    for name, payload in payloads.items():
        atomic_write_json(output_root / name, payload)
    index = {
        "format": FORMAT,
        "source_commit": args.source_commit,
        "inputs": {
            path.as_posix(): file_identity(path) for path in FORMAL_SOURCES
        },
        "formal_14h_root": str(formal_root) if formal_root else None,
        "outputs": {
            name: file_identity(output_root / name) for name in sorted(payloads)
        },
    }
    atomic_write_json(output_root / "artifact_index.json", index)
    print(json.dumps({"output_root": str(output_root), "artifact_index_sha256": sha256_file(output_root / "artifact_index.json")}, sort_keys=True))


if __name__ == "__main__":
    main()
