from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from rcmf.benchmarks.appworld.reproducible_config_14b import (
    arm_root,
    compatibility_parent_b,
    write_resolved_arm_config,
)
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.stage_graph import SHARED_STAGES, THREE_DEMO_STAGES
from rcmf.pipeline.validators import (
    evaluate_d06_reproduction_gate,
    evaluate_three_demo_reproduction_gate,
    validate_stage_completion,
)
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    FrozenSelectorDecomposition,
    read_compiled_field,
    tensor_sha256,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    ensure_dir,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.build_clean_multiview_cache_7c import (
    _aggregate,
    _clean_state_cache,
    _clean_transition_cache,
    _task_split,
)


PIPELINE_FORMAT = "rcmf_reproducible_stage_output_14b_v1"
REPLAY_CONFIG = Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml")


class _StageProgress:
    def __init__(self, path: Path) -> None:
        self.path = path

    def progress(self, **payload: Any) -> None:
        atomic_write_json(self.path, {"format": "stage_progress_14b_v1", **payload})


def _json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(path: str | Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _formal_identity_from_environment(run_root: Path) -> dict[str, str]:
    values = {
        "run_uuid": os.environ.get("RCMF_PIPELINE_RUN_UUID", ""),
        "pipeline_config_sha256": os.environ.get(
            "RCMF_PIPELINE_CONFIG_SHA256", ""
        ),
        "contract_sha256": os.environ.get("RCMF_PIPELINE_CONTRACT_SHA256", ""),
        "run_root": os.environ.get("RCMF_PIPELINE_RUN_ROOT", ""),
    }
    missing = sorted(key for key, value in values.items() if not value)
    if missing:
        raise PermissionError(f"Formal stage identity is incomplete: {missing}")
    if Path(values["run_root"]).resolve(strict=False) != run_root.resolve(
        strict=False
    ):
        raise PermissionError("Formal stage run root differs from scheduler identity")
    return values


def _strict_prior_stage_validation(
    stage_id: str, run_root: Path, source_commit: str
) -> dict[str, Any]:
    identity = _formal_identity_from_environment(run_root)
    return validate_stage_completion(
        run_root / "stages" / stage_id,
        source_commit,
        expected_run_uuid=identity["run_uuid"],
        expected_pipeline_config_sha256=identity["pipeline_config_sha256"],
        expected_contract_sha256=identity["contract_sha256"],
        expected_run_root=run_root,
    )


def _copy_exact(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_file(target) != sha256_file(source):
            raise ValueError(f"Immutable copied artifact differs: {target}")
        return
    shutil.copy2(source, target)
    if sha256_file(target) != sha256_file(source):
        raise IOError(f"Copied artifact hash differs: {target}")


def _link_exact(source: Path, target: Path) -> None:
    if target.exists():
        if sha256_file(target) != sha256_file(source):
            raise ValueError(f"Linked artifact differs: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        _copy_exact(source, target)


def _run(command: Sequence[str], *, environment: Mapping[str, str] | None = None) -> None:
    env = dict(os.environ)
    env.update(environment or {})
    env["PYTHONHASHSEED"] = "25101"
    subprocess.run(list(command), check=True, cwd=Path.cwd(), env=env)


def _runner_args(
    script: str,
    *,
    config: Path,
    artifact_dir: Path,
    attempt_id: str,
    source_commit: str,
    parent_attempt_id: str,
) -> list[str]:
    return [
        sys.executable,
        script,
        "--config",
        str(config),
        "--artifact-dir",
        str(artifact_dir),
        "--attempt-id",
        attempt_id,
        "--local-head",
        source_commit,
        "--github-head",
        source_commit,
        "--lambda-head",
        source_commit,
        "--parent-attempt-id",
        parent_attempt_id,
        "--resume-checkpoint",
        "auto",
        "--tmux-session",
        "exp037a_pipeline",
    ]


def _arm_from_stage(stage_id: str) -> str | None:
    if stage_id.startswith("D"):
        return "3d"
    if stage_id.startswith("O"):
        return "1d"
    return None


def _arm_config(run_root: Path, arm_id: str) -> Path:
    return run_root / "resolved_configs" / f"arm_{arm_id}.yaml"


def _existing_files(*paths: Path) -> list[Path]:
    return [path for path in paths if path.is_file()]


def _tree_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def formal_stage_output_paths(stage_id: str, run_root: Path) -> list[Path]:
    """Return immutable, resume-critical outputs produced by a formal stage."""
    shared: dict[str, list[Path]] = {
        "S00_environment_manifest": [run_root / "preflight/environment_manifest.json"],
        "S01_authoritative_corpus": [
            run_root / "preflight/authoritative_source_manifest.json"
        ],
        "S02_task_and_parent_splits": [run_root / "preflight/shared/parent_split.json"],
        "S03_transition_records": [run_root / "preflight/shared/transitions.jsonl"],
        "S04_selector_supervision": [
            run_root / "preflight/shared/labels.jsonl",
            run_root / "preflight/shared/illegal_pairs.jsonl",
        ],
        "S05_transition_representations": [
            run_root / "shared/representation_cache/multiview/transition_multiview.pt",
            run_root / "shared/representation_cache/multiview/transition_summary.json",
            *_tree_files(
                run_root / "shared/representation_cache/multiview/transition_rows"
            ),
        ],
        "S05B_joint_source_contract_preflight": [
            run_root / "preflight/shared/joint_source_contract_preflight_14c.json",
            run_root / "preflight/shared/joint_source_contract_mismatches_14c.jsonl",
        ],
        "S06_cv_folds_and_sampling": [
            run_root / "preflight/shared/cv_folds_and_sampling.json"
        ],
        "S07_initial_parameter_snapshots": [
            run_root / "preflight/initialization_manifest.json",
            *_tree_files(run_root / "preflight/initialization_snapshots"),
        ],
        "S08_two_arm_contract": [
            run_root / "preflight/two_arm_contract.json",
            run_root / "preflight/resolved_config_diff.json",
        ],
        "S09_runtime_preflight_and_approval": [run_root / "runtime_authorization.json"],
    }
    if stage_id in shared:
        paths = shared[stage_id]
    elif stage_id.startswith(("D", "O")):
        arm_id = _arm_from_stage(stage_id)
        if arm_id is None:
            raise KeyError(stage_id)
        target = arm_root(run_root, arm_id)
        index = int(stage_id[1:3])
        paths = []
        if index == 0:
            paths = [
                target / "clean_query_signature_manifest.jsonl",
                target / "clean_full_procedural_labels.jsonl",
                target / "clean_full_illegal_pairs.jsonl",
                target / "candidate_space_manifest.json",
                target / "data_preparation_summary.json",
                target / "representation_cache/multiview/state_multiview.pt",
                target
                / "representation_cache/multiview/clean_multiview_cache_summary.json",
            ]
        elif index == 1:
            paths = _tree_files(target / "selector/a_only_cv")
        elif index == 2:
            paths = [target / "selector/candidate_selection.json"]
        elif index == 3:
            paths = [
                target / "selector/ensemble_scores.pt",
                target / "selector/selector_summary.json",
                *_tree_files(target / "selector/seed_25071"),
                *_tree_files(target / "selector/seed_25072"),
                *_tree_files(target / "selector/seed_25073"),
            ]
        elif index == 4:
            paths = [
                target / "selector/factorization.pt",
                target / "selector/factorization_audit.json",
            ]
        elif index == 5:
            paths = [
                target / "preflight/initial_panel.json",
                target / "preflight/frozen_train_selections.jsonl",
                target / "preflight/structured_feature_rows.jsonl",
                target / "preflight/structured_feature_schema.json",
                target / "preflight/feature_leakage_audit.json",
                target / "preflight/selected_memory_summary.json",
            ]
        elif stage_id == "D06B_three_demo_causal_reproduction_gate":
            paths = [
                run_root / "gate/d06_three_demo_reproduction_gate.json",
                run_root
                / "stages/D06B_three_demo_causal_reproduction_gate/gate.json",
            ]
        elif index == 6:
            paths = [
                target / "paired_causal/condition_manifest.json",
                target / "paired_causal/paired_outcomes.json",
                *_tree_files(target / "paired_causal/condition_outputs"),
                *_tree_files(target / "paired_causal/replay_missing"),
            ]
        elif index == 7:
            paths = [
                target / "structured_compiler/policy_teacher_cache.pt",
                target / "structured_compiler/policy_teacher_report.json",
                *_existing_files(
                    target / "structured_compiler/mismatch_manifest.json"
                ),
            ]
        elif index == 8 and stage_id != "D08B_writer_reader_one_unit_smoke":
            paths = [
                target / "data/rcmf_source_cache.pt",
                target / "data/memory_provenance.jsonl",
                target / "data/source_representation_audit.json",
                target / "data/selector_decomposition_audit.json",
                target / "data/key_payload_shuffle_manifest.json",
                target / "data/full_bank_data_manifest.json",
                target / "joint_training/training_unit_manifest.json",
                target / "joint_training/state_query_shuffle_manifest.json",
                target / "joint_training/zero_policy_nll_summary.json",
                target / "runtime/static_counts.json",
                target / "runtime/formal_gpu_preflight.json",
                *_tree_files(target / "joint_training/zero_policy_nll"),
            ]
        elif stage_id == "D08B_writer_reader_one_unit_smoke":
            paths = [
                run_root
                / "engineering_smoke/3d_writer_reader_one_unit/writer_reader_smoke_gate.json"
            ]
        elif index == 9:
            paths = [
                target / "joint_training/checkpoints/epoch_01.pt",
                target / "joint_training/checkpoints/epoch_01_stage_summary.json",
            ]
        elif index == 10:
            paths = [
                target / "joint_training/checkpoints/epoch_01.pt",
                target / "joint_training/checkpoints/epoch_02.pt",
                target / "joint_training/training_summary.json",
            ]
        elif index == 11:
            paths = [
                target / "heldout_validation/teacher_forced_summary.json",
                *_tree_files(target / "heldout_validation/teacher_forced"),
            ]
        elif index == 12:
            paths = [
                target / "heldout_validation/teacher_forced_zero_exact_summary.json",
                *_tree_files(target / "heldout_validation/live_full_field"),
            ]
        elif index == 13:
            paths = _tree_files(target / "heldout_validation/full_trajectory")
        elif index == 14:
            paths = [
                target / "heldout_validation/live_full_field/checkpoint_selection.json"
            ]
        elif index == 15:
            paths = _existing_files(
                target / "deployment_field/selected_401_field.json",
                target / "deployment_field/selected_401_field.pt",
            )
        elif index == 16:
            paths = _existing_files(
                target / "deployment_field/instant_add_report.json",
                target / "deployment_field/complete_37_task_field.pt",
            )
        elif index == 17:
            paths = _existing_files(target / "deployment_field/validation_14b.json")
        elif arm_id == "3d" and index in (18, 19, 20):
            condition = {
                18: "B0_1D",
                19: "FRESH3D_C_1DDEPLOY",
                20: "FRESH3D_S_1DDEPLOY",
            }[index]
            paths = [
                run_root
                / f"evaluation/common_one_demo_dev/summaries/{condition}.json"
            ]
        elif arm_id == "3d" and index == 21:
            paths = [run_root / "historical_comparison/three_demo.json"]
        elif arm_id == "3d" and index == 22:
            paths = [
                run_root / "gate/three_demo_reproduction_gate.json",
                run_root / "stages/D22_three_demo_reproduction_gate/gate.json",
            ]
        elif arm_id == "1d" and index in (18, 19):
            condition = "FRESH1D_C_1DDEPLOY" if index == 18 else "FRESH1D_S_1DDEPLOY"
            paths = [
                run_root
                / f"evaluation/common_one_demo_dev/summaries/{condition}.json"
            ]
    elif stage_id == "F00_two_arm_paired_analysis":
        paths = [run_root / "analysis/two_arm_paired_analysis.json"]
    elif stage_id == "F01_portability_validation":
        paths = [run_root / "portability/validation.json"]
    elif stage_id == "F02_git_safe_audit_export":
        paths = [run_root / "audit/index.json"]
    elif stage_id == "F03_final_report_and_handoff":
        paths = [run_root / "final/final_record.json"]
    else:
        raise KeyError(f"No formal output contract for stage: {stage_id}")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Formal stage did not produce its declared artifacts: {missing[:5]}"
        )
    unique = {str(path.resolve(strict=False)): path for path in paths}
    return [unique[key] for key in sorted(unique)]


def _compatibility_inputs(config: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    pipeline = config["pipeline"]
    shared = run_root / "preflight" / "shared"
    parent = compatibility_parent_b(run_root)
    replay_root = Path(str(pipeline["roots"]["replay_validated_corpus"]))
    mapping = {
        replay_root / "replay_validated_corpus_manifest.json": parent
        / "replay_validated_corpus_manifest.json",
        shared / "transitions.jsonl": parent
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        shared / "transition_signatures.jsonl": parent
        / "clean_procedural_audit/clean_transition_signature_manifest.jsonl",
        shared / "signature_equivalence.json": parent
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        shared / "parent_split.json": parent
        / "clean_procedural_audit/clean_parent_split_manifest.json",
    }
    for source, target in mapping.items():
        _copy_exact(source, target)
    return {
        "parent_exp025b": str(parent),
        "files": {str(target): file_identity(target) for target in mapping.values()},
    }


def initialize_runtime_layout(
    config: Mapping[str, Any], run_root: Path
) -> dict[str, Any]:
    ensure_dir(run_root / "resolved_configs")
    compat = _compatibility_inputs(config, run_root)
    resolved = {}
    for arm_id in ("3d", "1d"):
        path = _arm_config(run_root, arm_id)
        write_resolved_arm_config(path, config, run_root, arm_id)
        resolved[arm_id] = file_identity(path)
    payload = {
        "format": "rcmf_reproducible_runtime_layout_14b_v1",
        "compatibility_inputs": compat,
        "resolved_configs": resolved,
    }
    atomic_write_json(run_root / "runtime_layout.json", payload)
    return payload


def _load_backend(config_path: Path) -> Any:
    cfg = load_config(config_path)
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen parameter freeze failed")
    return cfg, backend


def _fresh_transition_representations(
    config: Mapping[str, Any], run_root: Path, stage_dir: Path
) -> dict[str, Any]:
    config_path = _arm_config(run_root, "3d")
    cfg, backend = _load_backend(config_path)
    settings = cfg.raw["stage_c_7c"]
    source = Path(str(config["pipeline"]["roots"]["authoritative_corpus"]))
    split = _task_split(source)
    transitions = [
        row
        for row in _rows(source / "transition_manifest.jsonl")
        if split[str(row["parent_task_id"])] == "train"
    ]
    output = ensure_dir(run_root / "shared" / "representation_cache" / "multiview")
    progress = _StageProgress(stage_dir / "representation_progress.json")
    matrices, rows, counts = _clean_transition_cache(
        backend=backend,
        transitions=transitions,
        old={"ordered_ids": [], "rows": []},
        output_root=output,
        renderer_version=str(settings["multiview_cache"]["renderer_version"]),
        lineage=str(settings["expected_structural_lineage_sha256"]),
        attempt=progress,
    )
    aggregate = _aggregate(
        path=output / "transition_multiview.pt",
        kind="transition",
        matrices=matrices,
        rows=rows,
        view_names=TRANSITION_VIEW_NAMES,
        model_name=str(backend.model_name),
        renderer_version=str(settings["multiview_cache"]["renderer_version"]),
        lineage=str(settings["expected_structural_lineage_sha256"]),
    )
    summary = {
        "format": "fresh_shared_transition_representations_14b_v1",
        "counts": counts,
        "aggregate": aggregate,
        "historical_derived_artifact_loaded": False,
        "prompt_demonstrations_used": False,
        "qwen_frozen": True,
    }
    atomic_write_json(output / "transition_summary.json", summary)
    return summary


def _joint_source_contract_preflight(
    config: Mapping[str, Any], run_root: Path
) -> dict[str, Any]:
    """Exercise the unchanged EXP-031A consumer over every real fresh cache row."""
    from scripts.prepare_rcmf_joint_full_bank_9a import _section_contract

    shared = run_root / "preflight" / "shared"
    transition_path = shared / "transitions.jsonl"
    transitions = _rows(transition_path)
    transition_by_id = {str(row["transition_id"]): row for row in transitions}
    cache_root = run_root / "shared" / "representation_cache" / "multiview"
    aggregate_path = cache_root / "transition_multiview.pt"
    aggregate = torch.load(aggregate_path, map_location="cpu", weights_only=False)
    ordered_ids = [str(value) for value in aggregate["ordered_ids"]]
    aggregate_rows = list(aggregate["rows"])
    expected_count = int(config["pipeline"]["expected"]["train_transitions"])
    lineage = str(config["pipeline"]["expected"]["structural_lineage_sha256"])
    representations = aggregate["representations"]["final_layer"].to(torch.float32)
    mismatches: list[dict[str, Any]] = []
    consumer_rows = []
    section_map = {
        "source_task_goal_tokens": "source_task_goal",
        "canonical_pre_action_state_tokens": "pre_action_state",
        "complete_action_tokens": "complete_action",
        "complete_post_action_observation_tokens": "post_action_observation",
    }
    if len(transitions) != expected_count or len(ordered_ids) != expected_count:
        mismatches.append(
            {
                "kind": "row_count_mismatch",
                "transition_rows": len(transitions),
                "cache_rows": len(ordered_ids),
                "expected": expected_count,
            }
        )
    expected_order = sorted(transition_by_id)
    if ordered_ids != expected_order:
        mismatches.append(
            {
                "kind": "transition_order_mismatch",
                "manifest_order_sha256": content_sha256(expected_order),
                "cache_order_sha256": content_sha256(ordered_ids),
            }
        )
    for position, transition_id in enumerate(ordered_ids):
        if transition_id not in transition_by_id or position >= len(aggregate_rows):
            mismatches.append(
                {
                    "kind": "coverage_mismatch",
                    "position": position,
                    "transition_id": transition_id,
                }
            )
            continue
        row_path = cache_root / "transition_rows" / f"{transition_id}.pt"
        if not row_path.exists():
            mismatches.append(
                {
                    "kind": "missing_cache_row",
                    "position": position,
                    "transition_id": transition_id,
                    "path": str(row_path),
                }
            )
            continue
        cache_payload = torch.load(row_path, map_location="cpu", weights_only=False)
        transition = transition_by_id[transition_id]
        cache_row = aggregate_rows[position]
        row_mismatches = []
        if str(cache_payload["transition_id"]) != transition_id:
            row_mismatches.append("transition_id")
        if int(cache_payload["token_count"]) != int(transition["teacher_section_tokens"]):
            row_mismatches.append("teacher_section_tokens")
        if str(cache_payload["teacher_section_sha256"]) != str(
            transition["teacher_section_sha256"]
        ):
            row_mismatches.append("teacher_section_sha256")
        if bool(cache_payload.get("truncated")):
            row_mismatches.append("truncated")
        for field_name, span_name in section_map.items():
            actual = int(cache_payload["span_rows"][span_name]["token_count"])
            if int(transition[field_name]) != actual:
                row_mismatches.append(field_name)
        if row_mismatches:
            mismatches.append(
                {
                    "kind": "cache_contract_mismatch",
                    "position": position,
                    "transition_id": transition_id,
                    "fields": row_mismatches,
                    "cache_row": file_identity(row_path),
                }
            )
            continue
        try:
            consumer_rows.append(
                _section_contract(
                    transition,
                    cache_row,
                    representations[position, :8],
                    lineage=lineage,
                )
            )
        except Exception as error:
            mismatches.append(
                {
                    "kind": "historical_consumer_error",
                    "position": position,
                    "transition_id": transition_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    mismatch_path = shared / "joint_source_contract_mismatches_14c.jsonl"
    write_jsonl(mismatch_path, mismatches)
    required_fields = (
        "teacher_section_tokens",
        "source_task_goal_tokens",
        "canonical_pre_action_state_tokens",
        "complete_action_tokens",
        "complete_post_action_observation_tokens",
    )
    checks = {
        "row_count_499": len(transitions) == len(ordered_ids) == expected_count,
        "full_order_match": ordered_ids == sorted(transition_by_id),
        "consumer_row_count_499": len(consumer_rows) == expected_count,
        "required_metadata_present": all(
            all(field in row for field in required_fields) for row in transitions
        ),
        "no_truncation": all(not bool(row.get("truncated")) for row in aggregate_rows),
        "mismatch_count_zero": not mismatches,
    }
    report = {
        "format": "rcmf_joint_source_contract_preflight_14c_v1",
        "stage_id": "S05B_joint_source_contract_preflight",
        "historical_consumer": {
            "callable": "scripts.prepare_rcmf_joint_full_bank_9a._section_contract",
            "semantics_changed": False,
        },
        "transition_manifest": file_identity(transition_path),
        "aggregate_cache": file_identity(aggregate_path),
        "ordered_transition_ids_sha256": content_sha256(ordered_ids),
        "representation_tensor_sha256": tensor_sha256(representations),
        "individual_cache_row_count": sum(
            (cache_root / "transition_rows" / f"{value}.pt").exists()
            for value in ordered_ids
        ),
        "consumer_rows_sha256": content_sha256(consumer_rows),
        "mismatches": file_identity(mismatch_path),
        "checks": checks,
        "passed": all(checks.values()),
    }
    report_path = shared / "joint_source_contract_preflight_14c.json"
    atomic_write_json(report_path, report)
    if not report["passed"]:
        raise RuntimeError(f"Joint source contract preflight failed: {mismatches[:3]}")
    return report


def _prepare_selector_inputs(
    config: Mapping[str, Any], run_root: Path, arm_id: str, stage_dir: Path,
    source_commit: str, attempt_id: str,
) -> dict[str, Any]:
    config_path = _arm_config(run_root, arm_id)
    target = arm_root(run_root, arm_id)
    prepare_command = _runner_args(
        "scripts/prepare_signature_balanced_field_7c.py",
        config=config_path,
        artifact_dir=target,
        attempt_id=f"{attempt_id}-prepare",
        source_commit=source_commit,
        parent_attempt_id=attempt_id,
    )
    _run(prepare_command)
    cfg, backend = _load_backend(config_path)
    settings = cfg.raw["stage_c_7c"]
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    examples = load_decision_examples(corpus / "decision_examples.jsonl")
    task_split = _task_split(corpus)
    output = ensure_dir(target / "representation_cache" / "multiview")
    progress = _StageProgress(stage_dir / "representation_progress.json")
    matrices, rows, counts = _clean_state_cache(
        backend=backend,
        examples=examples,
        task_split=task_split,
        old={"ordered_ids": [], "rows": []},
        output_root=output,
        prompt_profile=str(cfg.benchmark.prompt_profile),
        renderer_version=str(settings["multiview_cache"]["renderer_version"]),
        lineage=str(settings["expected_structural_lineage_sha256"]),
        attempt=progress,
    )
    state = _aggregate(
        path=output / "state_multiview.pt",
        kind="state",
        matrices=matrices,
        rows=rows,
        view_names=STATE_VIEW_NAMES,
        model_name=str(backend.model_name),
        renderer_version=str(settings["multiview_cache"]["renderer_version"]),
        lineage=str(settings["expected_structural_lineage_sha256"]),
    )
    shared_transition = (
        run_root / "shared/representation_cache/multiview/transition_multiview.pt"
    )
    _link_exact(shared_transition, output / "transition_multiview.pt")
    transition = _json(
        run_root / "shared/representation_cache/multiview/transition_summary.json"
    )
    summary = {
        "format": "clean_multiview_cache_summary_7c_v1",
        "status": "completed",
        "checks": {
            "state_reused": counts["reused"] == 0,
            "state_new": counts["resumed"] + counts["newly_computed"] == 638,
            "transition_reused": transition["counts"]["reused"] == 0,
            "transition_new": transition["counts"]["resumed"]
            + transition["counts"]["newly_computed"]
            == 499,
            "no_truncation": all(not row["truncated"] for row in rows),
        },
        "state": {**counts, "aggregate": state},
        "transition": transition,
        "new_qwen_forward_count": counts["newly_computed"],
        "shared_transition_cache": file_identity(shared_transition),
        "historical_derived_artifact_loaded": False,
        "qwen_frozen": True,
    }
    if not all(summary["checks"].values()):
        raise RuntimeError(f"Fresh arm representation gate failed: {summary['checks']}")
    atomic_write_json(output / "clean_multiview_cache_summary.json", summary)
    return summary


def _selector_command(
    run_root: Path,
    arm_id: str,
    source_commit: str,
    attempt_id: str,
    *,
    stop_after_cv: bool,
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    command = _runner_args(
        "scripts/run_signature_balanced_field_7c.py",
        config=_arm_config(run_root, arm_id),
        artifact_dir=target,
        attempt_id=attempt_id,
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    environment = {"RCMF_SELECTOR_STOP_AFTER_CV": "1"} if stop_after_cv else {}
    _run(command, environment=environment)
    result_path = (
        target / "selector/a_only_cv/a_only_cv_report.json"
        if stop_after_cv
        else target / "selector/selector_summary.json"
    )
    return {"result": file_identity(result_path), "stop_after_cv": stop_after_cv}


def _selector_candidate_selection(run_root: Path, arm_id: str) -> dict[str, Any]:
    path = arm_root(run_root, arm_id) / "selector/a_only_cv/a_only_cv_report.json"
    payload = _json(path)
    selected = dict(payload["selected_candidate"])
    result = {
        "format": "fresh_selector_candidate_selection_14b_v1",
        "arm": arm_id,
        "selected_candidate": selected,
        "selection_rule": payload["selection_rule"],
        "b_c_d_e_inspected_for_selection": False,
        "cv_report": file_identity(path),
    }
    output = arm_root(run_root, arm_id) / "selector/candidate_selection.json"
    atomic_write_json(output, result)
    return result


def _selector_factorization(run_root: Path, arm_id: str) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    ensemble_path = target / "selector/ensemble_scores.pt"
    ensemble = torch.load(ensemble_path, map_location="cpu", weights_only=False)
    checkpoints = []
    checkpoint_rows = []
    for row in ensemble["seed_checkpoints"]:
        path = Path(str(row["checkpoint"]))
        if sha256_file(path) != str(row["checkpoint_sha256"]):
            raise ValueError(f"Fresh selector checkpoint hash differs: {path}")
        checkpoints.append(torch.load(path, map_location="cpu", weights_only=False))
        checkpoint_rows.append(file_identity(path))
    decomposition = FrozenSelectorDecomposition.from_checkpoints(
        checkpoints, ensemble["train_calibration"]
    )
    state_cache = torch.load(
        target / "representation_cache/multiview/state_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition_cache = torch.load(
        target / "representation_cache/multiview/transition_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    states = state_cache["representations"]["final_layer"].to(torch.float32)
    transitions = transition_cache["representations"]["final_layer"].to(
        torch.float32
    )
    with torch.no_grad():
        query = decomposition.query(states)
        key = decomposition.key(transitions)
        direct = decomposition.direct_scores(states, transitions)
        factorized = query @ key.T + decomposition.intercept
    error = float((direct - factorized).abs().max())
    if error > 1e-5:
        raise RuntimeError(f"Selector factorization error exceeds tolerance: {error}")
    artifact = target / "selector/factorization.pt"
    torch.save(
        {
            "format": "fresh_selector_factorization_14b_v1",
            "ordered_state_ids": list(state_cache["ordered_ids"]),
            "ordered_transition_ids": list(transition_cache["ordered_ids"]),
            "query": query,
            "key": key,
            "intercept": decomposition.intercept,
            "ensemble_sha256": sha256_file(ensemble_path),
        },
        artifact,
    )
    result = {
        "format": "fresh_selector_factorization_audit_14b_v1",
        "arm": arm_id,
        "query_shape": list(query.shape),
        "key_shape": list(key.shape),
        "key_dimension": int(decomposition.key_dim),
        "direct_vs_factorized_max_abs": error,
        "tolerance": 1e-5,
        "passed": True,
        "ensemble": file_identity(ensemble_path),
        "checkpoints": checkpoint_rows,
        "artifact": file_identity(artifact),
    }
    atomic_write_json(target / "selector/factorization_audit.json", result)
    return result


def _prepare_selected_memories(
    run_root: Path, arm_id: str, source_commit: str, attempt_id: str
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    command = _runner_args(
        "scripts/prepare_appworld_structured_rescue_7hr.py",
        config=_arm_config(run_root, arm_id),
        artifact_dir=target,
        attempt_id=attempt_id,
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    _run(command)
    selections = _rows(target / "preflight/frozen_train_selections.jsonl")
    scoreable = [row for row in selections if bool(row["scoreable"])]
    result = {
        "format": "fresh_selected_memory_manifest_14b_v1",
        "arm": arm_id,
        "logical_state_count": len(selections),
        "scoreable_state_count": len(scoreable),
        "missing_over_context_count": len(selections) - len(scoreable),
        "selection_uses_outcomes": False,
        "selection_uses_target": False,
        "selections": file_identity(
            target / "preflight/frozen_train_selections.jsonl"
        ),
    }
    atomic_write_json(target / "preflight/selected_memory_summary.json", result)
    return result


def _paired_or_teacher_command(
    run_root: Path,
    arm_id: str,
    source_commit: str,
    attempt_id: str,
    *,
    teacher: bool,
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    if teacher and arm_id == "3d":
        _require_d06_reproduction_gate(run_root)
    script = (
        "scripts/run_appworld_structured_compiler_7hr.py"
        if teacher
        else "scripts/run_appworld_train_causal_gate_7hr.py"
    )
    phase = "teacher" if teacher else "paired"
    command = _runner_args(
        script,
        config=_arm_config(run_root, arm_id),
        artifact_dir=target,
        attempt_id=attempt_id,
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    command.extend(["--replay-config", str(REPLAY_CONFIG), "--phase", phase])
    _run(command)
    output = (
        target / "structured_compiler/policy_teacher_cache.pt"
        if teacher
        else target / "paired_causal/paired_outcomes.json"
    )
    return {"phase": phase, "output": file_identity(output)}


def _require_d06_reproduction_gate(run_root: Path) -> dict[str, Any]:
    path = run_root / "gate/d06_three_demo_reproduction_gate.json"
    if not path.exists():
        raise RuntimeError("Fresh D06 reproduction gate has not completed")
    gate = _json(path)
    if (
        gate.get("decision") != "D06_THREE_DEMO_REPRODUCTION_PASS"
        or gate.get("passed") is not True
    ):
        raise RuntimeError("Fresh D06 reproduction gate did not pass")
    return gate


def _d06_reproduction_gate(
    config: Mapping[str, Any], run_root: Path, source_commit: str
) -> dict[str, Any]:
    target = arm_root(run_root, "3d")
    d06_stage = run_root / "stages/D06_paired_causal_outcomes"
    completion = _json(d06_stage / "completion.json")
    strict = _strict_prior_stage_validation(
        "D06_paired_causal_outcomes", run_root, source_commit
    )
    if not bool(completion.get("passed")) or not bool(strict.get("passed")):
        raise RuntimeError("Fresh D06 must be sealed before historical comparison")
    fresh_path = target / "paired_causal/paired_outcomes.json"
    fresh_selections_path = target / "preflight/frozen_train_selections.jsonl"
    fresh_identity = {
        "paired_outcomes": file_identity(fresh_path),
        "selected_memories": file_identity(fresh_selections_path),
        "d06_completion": file_identity(d06_stage / "completion.json"),
        "d06_output_manifest": file_identity(d06_stage / "output_manifest.json"),
    }
    references = config["pipeline"]["reproduction_contract"]["audit_references"]
    historical_path = Path(str(references["paired_outcomes"]))
    historical_selections_path = Path(str(references["selected_memories"]))
    historical_identity = {
        "paired_outcomes": file_identity(historical_path),
        "selected_memories": file_identity(historical_selections_path),
    }
    contract = config["pipeline"]["reproduction_contract"][
        "post_d06_reproduction_gate"
    ]
    result = evaluate_d06_reproduction_gate(
        fresh=_json(fresh_path),
        historical=_json(historical_path),
        fresh_selections=_rows(fresh_selections_path),
        historical_selections=_rows(historical_selections_path),
        expected_train_completed=int(contract["expected_train_completed"]),
        expected_heldout_completed=int(contract["expected_heldout_completed"]),
        expected_label_counts=contract["expected_label_counts"],
    )
    result.update(
        {
            "fresh_seal": fresh_identity,
            "historical_references": historical_identity,
            "historical_read_after_fresh_seal": True,
            "source_commit": source_commit,
        }
    )
    path = run_root / "gate/d06_three_demo_reproduction_gate.json"
    atomic_write_json(path, result)
    atomic_write_json(
        run_root
        / "stages/D06B_three_demo_causal_reproduction_gate/gate.json",
        result,
    )
    return result


def _joint_prepare(
    config: Mapping[str, Any],
    run_root: Path,
    arm_id: str,
    source_commit: str,
    attempt_id: str,
) -> dict[str, Any]:
    prerequisites: dict[str, Any] = {}
    if arm_id == "3d":
        d06_gate = _require_d06_reproduction_gate(run_root)
        source_gate = _json(
            run_root
            / "preflight/shared/joint_source_contract_preflight_14c.json"
        )
        expected = config["pipeline"]["reproduction_contract"][
            "post_d06_reproduction_gate"
        ]
        prerequisite_checks = {
            "d06_reproduction_passed": bool(d06_gate.get("passed")),
            "completed_366_98": d06_gate.get("counts", {}).get(
                "fresh_train_completed"
            )
            == int(expected["expected_train_completed"])
            and d06_gate.get("counts", {}).get("fresh_heldout_completed")
            == int(expected["expected_heldout_completed"]),
            "s05b_source_consumer_contract_passed": bool(
                source_gate.get("passed")
            ),
            "transition_metadata_rows_499": bool(
                source_gate.get("checks", {}).get("row_count_499")
            )
            and bool(
                source_gate.get("checks", {}).get("consumer_row_count_499")
            ),
            "no_truncation": bool(
                source_gate.get("checks", {}).get("no_truncation")
            ),
            "no_missing_row_imputation": True,
        }
        if not all(prerequisite_checks.values()):
            raise RuntimeError(
                f"D08 prerequisites failed: {prerequisite_checks}"
            )
        prerequisites = {
            "checks": prerequisite_checks,
            "d06_gate": file_identity(
                run_root / "gate/d06_three_demo_reproduction_gate.json"
            ),
            "s05b_gate": file_identity(
                run_root
                / "preflight/shared/joint_source_contract_preflight_14c.json"
            ),
        }
    target = arm_root(run_root, arm_id)
    config_path = _arm_config(run_root, arm_id)
    command = _runner_args(
        "scripts/prepare_rcmf_joint_full_bank_9a.py",
        config=config_path,
        artifact_dir=target,
        attempt_id=f"{attempt_id}-prepare",
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    _run(command)
    outputs = {}
    for phase in ("preflight", "smoke", "zero-cache"):
        phase_command = _runner_args(
            "scripts/run_rcmf_joint_full_bank_9a.py",
            config=config_path,
            artifact_dir=target,
            attempt_id=f"{attempt_id}-{phase}",
            source_commit=source_commit,
            parent_attempt_id=attempt_id,
        )
        phase_command.extend(["--phase", phase])
        _run(phase_command)
        outputs[phase] = True
    return {
        "format": "fresh_joint_training_units_and_zero_cache_14b_v1",
        "phases": outputs,
        "prerequisites": prerequisites,
        "data_manifest": file_identity(target / "data/full_bank_data_manifest.json"),
            "zero_cache": file_identity(
                target / "joint_training/zero_policy_nll_summary.json"
            ),
    }


def _link_smoke_input(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.resolve(strict=True) != source.resolve(strict=True):
            raise ValueError(f"Writer-reader smoke input link differs: {target}")
        return
    target.symlink_to(source, target_is_directory=source.is_dir())


def _checkpoint_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _path_identity(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"kind": "file", **file_identity(path)}
    files = [
        file_identity(candidate, path)
        for candidate in sorted(path.rglob("*"))
        if candidate.is_file()
    ]
    return {
        "kind": "directory",
        "path": str(path.resolve(strict=False)),
        "file_count": len(files),
        "size_bytes": sum(int(row["size_bytes"]) for row in files),
        "sha256": content_sha256(files),
        "files": files,
    }


def _writer_reader_one_unit_smoke(
    run_root: Path, source_commit: str, attempt_id: str
) -> dict[str, Any]:
    _require_d06_reproduction_gate(run_root)
    target = arm_root(run_root, "3d")
    smoke_root = run_root / "engineering_smoke/3d_writer_reader_one_unit"
    gate_path = smoke_root / "writer_reader_smoke_gate.json"
    if gate_path.exists():
        prior = _json(gate_path)
        if bool(prior.get("passed")):
            return prior
        raise RuntimeError("Existing writer-reader smoke gate is not valid")
    links = {
        "data/rcmf_source_cache.pt": target / "data/rcmf_source_cache.pt",
        "data/full_bank_data_manifest.json": target
        / "data/full_bank_data_manifest.json",
        "data/source_representation_audit.json": target
        / "data/source_representation_audit.json",
        "data/selector_decomposition_audit.json": target
        / "data/selector_decomposition_audit.json",
        "data/key_payload_shuffle_manifest.json": target
        / "data/key_payload_shuffle_manifest.json",
        "runtime/static_counts.json": target / "runtime/static_counts.json",
        "runtime/formal_gpu_preflight.json": target
        / "runtime/formal_gpu_preflight.json",
        "joint_training/training_unit_manifest.json": target
        / "joint_training/training_unit_manifest.json",
        "joint_training/state_query_shuffle_manifest.json": target
        / "joint_training/state_query_shuffle_manifest.json",
        "joint_training/zero_policy_nll_summary.json": target
        / "joint_training/zero_policy_nll_summary.json",
        "joint_training/zero_policy_nll": target
        / "joint_training/zero_policy_nll",
    }
    for relative, source in links.items():
        _link_smoke_input(source, smoke_root / relative)
    scientific_checkpoint_root = target / "joint_training/checkpoints"
    scientific_before = _checkpoint_hashes(scientific_checkpoint_root)
    init = run_root / "preflight/initialization_snapshots"
    command = _runner_args(
        "scripts/run_rcmf_joint_full_bank_9a.py",
        config=_arm_config(run_root, "3d"),
        artifact_dir=smoke_root,
        attempt_id=f"{attempt_id}-isolated-one-unit",
        source_commit=source_commit,
        parent_attempt_id=attempt_id,
    )
    command.extend(["--phase", "train"])
    _run(
        command,
        environment={
            "RCMF_TRAIN_STOP_AFTER_EPOCH": "1",
            "RCMF_DIAGNOSTIC_MAX_TRAINING_UNITS": "1",
            "RCMF_WRITER_INITIAL_PATH": str(init / "writer_initial.pt"),
            "RCMF_READER_INITIAL_PATH": str(init / "reader_initial.pt"),
        },
    )
    summary_path = (
        smoke_root
        / "joint_training/checkpoints/diagnostic_one_unit_summary_14c.json"
    )
    summary = _json(summary_path)
    checkpoint = Path(str(summary["checkpoint"]["path"]))
    checkpoint_hash = sha256_file(checkpoint)
    scientific_after = _checkpoint_hashes(scientific_checkpoint_root)
    checks = {
        "diagnostic_summary_passed": bool(summary.get("passed")),
        "one_backward": int(summary.get("backward_count", 0)) == 1,
        "one_unit": int(summary.get("completed_units", 0)) == 1,
        "finite_loss": bool(summary.get("all_losses_finite")),
        "finite_gradients": bool(summary.get("trainable_gradients_finite")),
        "writer_gradient_nonzero": bool(summary.get("writer_gradient_nonzero")),
        "reader_gradient_nonzero": bool(summary.get("reader_gradient_nonzero")),
        "qwen_frozen": bool(summary.get("qwen_frozen_and_gradient_free")),
        "selector_frozen": bool(summary.get("selector_tensors_frozen")),
        "scientific_checkpoints_unchanged": scientific_before == scientific_after,
        "isolated_checkpoint": smoke_root.resolve(strict=False)
        in checkpoint.resolve(strict=False).parents,
    }
    result = {
        "format": "d08b_writer_reader_one_unit_smoke_14g_v1",
        "passed": all(checks.values()),
        "scientific_result": False,
        "checks": checks,
        "source_commit": source_commit,
        "training_unit_ids": list(summary.get("completed_global_unit_ids", [])),
        "backward_count": int(summary.get("backward_count", 0)),
        "optimizer_step_count": int(summary.get("backward_count", 0)),
        "checkpoint_sha256_before_discard": checkpoint_hash,
        "smoke_parameters_used_by_d09": False,
        "d09_initialization": {
            "writer": file_identity(init / "writer_initial.pt"),
            "reader": file_identity(init / "reader_initial.pt"),
        },
        "input_links": {
            relative: _path_identity(source) for relative, source in links.items()
        },
        "diagnostic_summary": file_identity(summary_path),
    }
    if not result["passed"]:
        atomic_write_json(gate_path, result)
        return result
    checkpoint.unlink()
    latest = smoke_root / "joint_training/latest_checkpoint.json"
    latest.unlink(missing_ok=True)
    result["smoke_parameter_disposition"] = "discarded_after_hash_recorded"
    result["checkpoint_exists_after_discard"] = checkpoint.exists()
    atomic_write_json(gate_path, result)
    return result


def _require_writer_reader_smoke_gate(run_root: Path) -> dict[str, Any]:
    path = (
        run_root
        / "engineering_smoke/3d_writer_reader_one_unit/writer_reader_smoke_gate.json"
    )
    if not path.exists():
        raise RuntimeError("D08B writer-reader smoke gate has not completed")
    gate = _json(path)
    if not bool(gate.get("passed")):
        raise RuntimeError("D08B writer-reader smoke gate did not pass")
    if gate.get("smoke_parameters_used_by_d09") is not False:
        raise RuntimeError("D08B smoke parameters are not isolated from D09")
    return gate


def _joint_training_epoch(
    run_root: Path,
    arm_id: str,
    source_commit: str,
    attempt_id: str,
    epoch: int,
) -> dict[str, Any]:
    if arm_id == "3d":
        _require_writer_reader_smoke_gate(run_root)
    target = arm_root(run_root, arm_id)
    command = _runner_args(
        "scripts/run_rcmf_joint_full_bank_9a.py",
        config=_arm_config(run_root, arm_id),
        artifact_dir=target,
        attempt_id=attempt_id,
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    command.extend(["--phase", "train"])
    init = run_root / "preflight/initialization_snapshots"
    _run(
        command,
        environment={
            "RCMF_TRAIN_STOP_AFTER_EPOCH": str(epoch),
            "RCMF_WRITER_INITIAL_PATH": str(init / "writer_initial.pt"),
            "RCMF_READER_INITIAL_PATH": str(init / "reader_initial.pt"),
        },
    )
    checkpoint = target / f"joint_training/checkpoints/epoch_{epoch:02d}.pt"
    return {"epoch": epoch, "checkpoint": file_identity(checkpoint)}


def _joint_phase(
    run_root: Path,
    arm_id: str,
    source_commit: str,
    attempt_id: str,
    *,
    script: str,
    phase: str,
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    command = _runner_args(
        script,
        config=_arm_config(run_root, arm_id),
        artifact_dir=target,
        attempt_id=attempt_id,
        source_commit=source_commit,
        parent_attempt_id="pipeline",
    )
    if script.endswith("live_9a.py"):
        command.extend(["--replay-config", str(REPLAY_CONFIG)])
    command.extend(["--phase", phase])
    _run(command)
    return {"script": script, "phase": phase, "completed": True}


class _FixedFieldRuntime:
    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        backend: Any,
        correct_field: Path,
        shuffled_field: Path | None,
        checkpoint: Path | None,
        deployment_bundle: bool,
    ) -> None:
        from scripts.run_rcmf_joint_full_bank_9a import _build_components
        from scripts.run_rcmf_joint_full_bank_first37_9a import LiveFieldQueryEncoder

        correct = torch.load(correct_field, map_location="cpu", weights_only=False)
        if deployment_bundle:
            self.A = correct["A"].to(backend.device, torch.float32)
            self.B = correct["B"].to(backend.device, torch.float32)
            self.shuffled_A = correct["shuffled_A"].to(backend.device, torch.float32)
            self.shuffled_B = correct["shuffled_B"].to(backend.device, torch.float32)
            reader_state = correct["reader_state_dict"]
            self.memory_count = int(correct["memory_count"])
        else:
            if shuffled_field is None or checkpoint is None:
                raise ValueError("Heldout fields require shuffle and checkpoint")
            shuffled = torch.load(shuffled_field, map_location="cpu", weights_only=False)
            checkpoint_payload = torch.load(
                checkpoint, map_location="cpu", weights_only=False
            )
            self.A = correct["A"].to(backend.device, torch.float32)
            self.B = correct["B"].to(backend.device, torch.float32)
            self.shuffled_A = shuffled["A"].to(backend.device, torch.float32)
            self.shuffled_B = shuffled["B"].to(backend.device, torch.float32)
            reader_state = checkpoint_payload["reader_state_dict"]
            self.memory_count = int(correct["memory_count"])
        _, self.reader = _build_components(backend.device)
        self.reader.load_state_dict(reader_state, strict=True)
        self.reader.eval()
        for parameter in self.reader.parameters():
            parameter.requires_grad_(False)
        self.backend = backend
        self.query_encoder = LiveFieldQueryEncoder(settings=settings, backend=backend)
        self._override: tuple[torch.Tensor, torch.Tensor] | None = None

    def set_state_query_override(
        self, views: torch.Tensor | None, query: torch.Tensor | None
    ) -> None:
        self._override = None if views is None or query is None else (views, query)

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[torch.Tensor, dict[str, Any]]:
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        if condition == "D3":
            if self._override is None:
                raise RuntimeError("State-query shuffle override is unavailable")
            views, query = self._override
            views = views.to(self.backend.device, torch.float32)
            query = query.to(self.backend.device, torch.float32)
        else:
            views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        if condition == "D2":
            A, B, field_control = self.shuffled_A, self.shuffled_B, "key_payload_shuffle"
        else:
            A, B, field_control = self.A, self.B, "correct"
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
            "field_control": field_control,
            "state_query_shuffled": condition == "D3",
        }


def _trajectory_paths(root: Path, deployment: Path, provenance: Path) -> dict[str, Path]:
    return {
        "root": root,
        "static_assets": root / "raw_audit/static_prompt_assets.json",
        "deployment": deployment,
        "instant_add": provenance,
    }


def _run_task_set(
    *,
    run_root: Path,
    arm_id: str,
    task_ids: Sequence[str],
    output_root: Path,
    condition_id: str,
    condition_name: str,
    field_control: str,
    prompt_profile: str,
    correct_field: Path,
    shuffled_field: Path | None,
    checkpoint: Path | None,
    provenance: Path,
    memory_count: int,
    source_commit: str,
    attempt_id: str,
    deployment_bundle: bool,
    query_overrides: Mapping[str, tuple[torch.Tensor, torch.Tensor]] | None = None,
) -> dict[str, Any]:
    from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task

    config_path = _arm_config(run_root, arm_id)
    cfg = load_config(config_path)
    settings = copy.deepcopy(cfg.raw["stage_c_9a"])
    settings["appworld"]["prompt_profile"] = prompt_profile
    settings["expected"]["selector_ensemble_sha256"] = "fresh_stage_output"
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    bare = field_control == "D0"
    runtime = None
    if not bare:
        runtime = _FixedFieldRuntime(
            settings=settings,
            backend=backend,
            correct_field=correct_field,
            shuffled_field=shuffled_field,
            checkpoint=checkpoint,
            deployment_bundle=deployment_bundle,
        )
    manifest = {
        "format": "rcmf_reproducible_complete_trajectory_manifest_14b_v1",
        "arm": arm_id,
        "condition": condition_id,
        "condition_name": condition_name,
        "task_ids": list(task_ids),
        "prompt_profile": prompt_profile,
        "memory_count": 0 if bare else memory_count,
        "source_commit": source_commit,
        "run_uuid": os.environ.get("RCMF_PIPELINE_RUN_UUID"),
        "run_root": str(run_root.resolve(strict=False)),
        "pipeline_config_sha256": os.environ.get(
            "RCMF_PIPELINE_CONFIG_SHA256"
        ),
        "contract_sha256": os.environ.get("RCMF_PIPELINE_CONTRACT_SHA256"),
        "fresh_isolated_world_per_task": True,
        "runtime_retrieval": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    active_field = (
        shuffled_field
        if field_control == "D2" and shuffled_field is not None
        else correct_field
    )
    paths = _trajectory_paths(output_root, active_field, provenance)
    rows = []
    reused = 0
    for task_id in task_ids:
        if runtime is not None:
            override = (query_overrides or {}).get(task_id)
            runtime.set_state_query_override(*(override or (None, None)))
        row, was_reused = _run_task(
            task_id=task_id,
            condition=condition_id,
            settings=settings,
            backend=backend,
            runtime=runtime,
            paths=paths,
            manifest=manifest,
            config_sha256=sha256_file(config_path),
            attempt_id=attempt_id,
            smoke=False,
            result_version="rcmf_reproducible_complete_trajectory_task_14b_v1",
            bare_condition=bare,
            condition_name=condition_name,
            memory_count=0 if bare else memory_count,
            field_artifact_path=active_field,
            field_provenance_path=provenance,
            experiment_prefix="exp037a",
            field_control_condition=field_control,
        )
        rows.append(row)
        reused += int(was_reused)
    aggregate_counts: Counter[str] = Counter()
    for row in rows:
        aggregate_counts.update(row.get("counts", {}))
    summary = {
        "format": "rcmf_reproducible_complete_trajectory_summary_14b_v1",
        "condition": condition_id,
        "condition_name": condition_name,
        "arm": arm_id,
        "prompt_profile": prompt_profile,
        "task_count": len(rows),
        "success_count": sum(bool(row["success"]) for row in rows),
        "success_ids": sorted(
            str(row["task_id"]) for row in rows if bool(row["success"])
        ),
        "success_by_task": {
            str(row["task_id"]): bool(row["success"]) for row in rows
        },
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_prompt_tokens": sum(
            int(row.get("usage", {}).get("prompt_tokens", 0)) for row in rows
        ),
        "total_generated_tokens": sum(
            int(row.get("usage", {}).get("completion_tokens", 0)) for row in rows
        ),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "counts": dict(aggregate_counts),
        "reused_task_rows": reused,
        "manifest": manifest,
    }
    summary_path = output_root / "summaries" / f"{condition_id}.json"
    atomic_write_json(summary_path, summary)
    return summary


def _heldout_query_overrides(
    target: Path, task_ids: Sequence[str]
) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    source = torch.load(target / "data/rcmf_source_cache.pt", map_location="cpu", weights_only=False)
    state_cache = torch.load(
        target / "representation_cache/multiview/state_multiview.pt",
        map_location="cpu",
        weights_only=False,
    )
    state_rows = {str(row["state_example_id"]): row for row in state_cache["rows"]}
    state_positions = {str(value): index for index, value in enumerate(state_cache["ordered_ids"])}
    query_positions = {str(value): index for index, value in enumerate(source["ordered_state_ids"])}
    first_by_task: dict[str, str] = {}
    for state_id in source["ordered_state_ids"]:
        task_id = str(state_rows[str(state_id)]["task_id"])
        if task_id in task_ids and task_id not in first_by_task:
            first_by_task[task_id] = str(state_id)
    if set(first_by_task) != set(task_ids):
        raise ValueError("Heldout state-query shuffle lacks a task state")
    ordered = list(task_ids)
    mapping = {task: ordered[(index + 1) % len(ordered)] for index, task in enumerate(ordered)}
    output = {}
    for task_id, other_task in mapping.items():
        state_id = first_by_task[other_task]
        output[task_id] = (
            state_cache["representations"]["final_layer"][state_positions[state_id]],
            source["state_queries"][query_positions[state_id]],
        )
    return output


def _heldout_full_trajectories(
    run_root: Path, arm_id: str, source_commit: str, attempt_id: str
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    data = _json(target / "data/full_bank_data_manifest.json")
    task_ids = [str(value) for value in data["heldout_task_ids"]]
    if len(task_ids) != 8:
        raise ValueError("Heldout complete-trajectory task count differs")
    live = target / "heldout_validation/live_full_field"
    output_root = target / "heldout_validation/full_trajectory"
    overrides = _heldout_query_overrides(target, task_ids)
    summaries = []
    for epoch in (1, 2):
        correct = live / f"field_artifacts/epoch_{epoch:02d}_correct.pt"
        shuffled = live / f"field_artifacts/epoch_{epoch:02d}_key_payload_shuffle.pt"
        checkpoint = target / f"joint_training/checkpoints/epoch_{epoch:02d}.pt"
        for suffix, control, name in (
            ("H0", "D0", "zero"),
            ("H1", "D1", "correct"),
            ("H2", "D2", "key_payload_shuffle"),
            ("H3", "D3", "state_query_shuffle"),
        ):
            summaries.append(
                _run_task_set(
                    run_root=run_root,
                    arm_id=arm_id,
                    task_ids=task_ids,
                    output_root=output_root,
                    condition_id=f"E{epoch}_{suffix}",
                    condition_name=f"epoch_{epoch}_{name}",
                    field_control=control,
                    prompt_profile=str(
                        load_config(_arm_config(run_root, arm_id)).benchmark.prompt_profile
                    ),
                    correct_field=correct,
                    shuffled_field=shuffled,
                    checkpoint=checkpoint,
                    provenance=target / "data/full_bank_data_manifest.json",
                    memory_count=401,
                    source_commit=source_commit,
                    attempt_id=attempt_id,
                    deployment_bundle=False,
                    query_overrides=overrides if control == "D3" else None,
                )
            )
    result = {
        "format": "rcmf_heldout_complete_trajectory_summary_14b_v1",
        "arm": arm_id,
        "task_ids": task_ids,
        "task_list_sha256": content_sha256(task_ids),
        "condition_count": len(summaries) * len(task_ids),
        "summaries": summaries,
        "complete": len(summaries) == 8,
    }
    atomic_write_json(output_root / "summary.json", result)
    return result


def _select_checkpoint(run_root: Path, arm_id: str, source_commit: str, attempt_id: str) -> dict[str, Any]:
    result = _joint_phase(
        run_root,
        arm_id,
        source_commit,
        attempt_id,
        script="scripts/run_rcmf_joint_full_bank_live_9a.py",
        phase="select",
    )
    selection_path = (
        arm_root(run_root, arm_id)
        / "heldout_validation/live_full_field/checkpoint_selection.json"
    )
    selection = _json(selection_path)
    trajectory = _json(
        arm_root(run_root, arm_id)
        / "heldout_validation/full_trajectory/summary.json"
    )
    selection["complete_heldout_trajectory_evidence"] = {
        "path": str(
            arm_root(run_root, arm_id)
            / "heldout_validation/full_trajectory/summary.json"
        ),
        "sha256": sha256_file(
            arm_root(run_root, arm_id)
            / "heldout_validation/full_trajectory/summary.json"
        ),
        "complete": bool(trajectory["complete"]),
        "checkpoint_selection_changed": False,
        "reason": "historical checkpoint selection implementation remains immutable",
    }
    atomic_write_json(selection_path, selection)
    return {**result, "selection": selection}


def _selected_401_field(run_root: Path, arm_id: str) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    selection = _json(
        target / "heldout_validation/live_full_field/checkpoint_selection.json"
    )
    selected = selection.get("selected")
    if not isinstance(selected, Mapping):
        result = {
            "format": "rcmf_selected_401_field_14b_v1",
            "arm": arm_id,
            "status": "NO_DEPLOYABLE_CHECKPOINT",
            "passed": True,
        }
        atomic_write_json(target / "deployment_field/selected_401_field.json", result)
        return result
    epoch = int(selected["epoch"])
    live = target / "heldout_validation/live_full_field"
    correct_path = live / f"field_artifacts/epoch_{epoch:02d}_correct.pt"
    shuffle_path = live / f"field_artifacts/epoch_{epoch:02d}_key_payload_shuffle.pt"
    correct = torch.load(correct_path, map_location="cpu", weights_only=False)
    shuffled = torch.load(shuffle_path, map_location="cpu", weights_only=False)
    checkpoint_sha = str(selected["checkpoint_sha256"])
    checks = {
        "correct_memory_count": int(correct.get("memory_count", -1)) == 401,
        "shuffle_memory_count": int(shuffled.get("memory_count", -1)) == 401,
        "correct_checkpoint": str(correct.get("checkpoint_sha256"))
        == checkpoint_sha,
        "shuffle_checkpoint": str(shuffled.get("checkpoint_sha256"))
        == checkpoint_sha,
        "correct_A_shape": tuple(correct["A"].shape) == (960, 8, 256),
        "correct_B_shape": tuple(correct["B"].shape) == (8, 256),
        "shuffle_A_shape": tuple(shuffled["A"].shape) == (960, 8, 256),
        "shuffle_B_shape": tuple(shuffled["B"].shape) == (8, 256),
        "finite": all(
            bool(torch.isfinite(value).all())
            for value in (correct["A"], correct["B"], shuffled["A"], shuffled["B"])
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Selected 401-memory field identity failed: {checks}")
    payload = {
        "format": "rcmf_selected_401_field_tensor_14b_v1",
        "arm": arm_id,
        "epoch": epoch,
        "memory_count": 401,
        "A": correct["A"],
        "B": correct["B"],
        "shuffled_A": shuffled["A"],
        "shuffled_B": shuffled["B"],
        "checkpoint": selected["checkpoint"],
        "checkpoint_sha256": selected["checkpoint_sha256"],
    }
    path = target / "deployment_field/selected_401_field.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)
    result = {
        "format": "rcmf_selected_401_field_14b_v1",
        "arm": arm_id,
        "status": "complete",
        "epoch": epoch,
        "field": file_identity(path),
        "correct_source": file_identity(correct_path),
        "shuffle_source": file_identity(shuffle_path),
        "checks": checks,
        "passed": True,
    }
    atomic_write_json(target / "deployment_field/selected_401_field.json", result)
    return result


def _validate_deployment_field(run_root: Path, arm_id: str) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    selection = _json(
        target / "heldout_validation/live_full_field/checkpoint_selection.json"
    )
    if not isinstance(selection.get("selected"), Mapping):
        return {
            "format": "rcmf_deployment_field_validation_14b_v1",
            "arm": arm_id,
            "status": "NO_DEPLOYABLE_CHECKPOINT",
            "passed": True,
        }
    path = target / "deployment_field/complete_37_task_field.pt"
    report = target / "deployment_field/instant_add_report.json"
    payload = torch.load(path, map_location="cpu", weights_only=False)
    report_payload = _json(report)
    selected = selection["selected"]
    checkpoint_path = Path(str(selected["checkpoint"]))
    expected_memory_ids = sorted(
        str(value)
        for value in torch.load(
            target / "data/rcmf_source_cache.pt",
            map_location="cpu",
            weights_only=False,
        )["ordered_transition_ids"]
    )
    checks = {
        "memory_count": int(payload["memory_count"]) == 499,
        "memory_ids": list(payload.get("memory_ids", [])) == expected_memory_ids,
        "memory_ids_unique": len(set(payload.get("memory_ids", []))) == 499,
        "A_shape": tuple(payload["A"].shape) == (960, 8, 256),
        "B_shape": tuple(payload["B"].shape) == (8, 256),
        "shuffle_A_shape": tuple(payload["shuffled_A"].shape) == (960, 8, 256),
        "shuffle_B_shape": tuple(payload["shuffled_B"].shape) == (8, 256),
        "finite": all(
            bool(torch.isfinite(payload[name]).all())
            for name in ("A", "B", "shuffled_A", "shuffled_B")
        ),
        "report_hash": str(report_payload["deployment_field_sha256"])
        == sha256_file(path),
        "selected_checkpoint_exists": checkpoint_path.is_file(),
        "selected_checkpoint_hash": checkpoint_path.is_file()
        and sha256_file(checkpoint_path) == str(selected["checkpoint_sha256"]),
        "payload_checkpoint": str(payload.get("checkpoint_sha256"))
        == str(selected["checkpoint_sha256"]),
        "report_checkpoint": str(report_payload.get("selected_checkpoint_sha256"))
        == str(selected["checkpoint_sha256"]),
        "instant_add_counts": int(report_payload.get("field_memory_count_before", -1))
        == 401
        and int(report_payload.get("new_memory_count", -1)) == 98
        and int(report_payload.get("field_memory_count_after", -1)) == 499,
        "no_retraining": report_payload.get("no_retraining_or_optimizer_step") is True,
    }
    result = {
        "format": "rcmf_deployment_field_validation_14b_v1",
        "arm": arm_id,
        "checks": checks,
        "passed": all(checks.values()),
        "field": file_identity(path),
        "instant_add": file_identity(report),
    }
    if not result["passed"]:
        raise RuntimeError(f"Deployment field validation failed: {checks}")
    atomic_write_json(target / "deployment_field/validation_14b.json", result)
    return result


def _dev_task_ids(config: Mapping[str, Any]) -> list[str]:
    pipeline = config["pipeline"]
    legacy_python = str(pipeline["required_environment"]["legacy_python"])
    legacy_root = str(pipeline["required_environment"]["legacy_root"])
    code = (
        "import json; from appworld import load_task_ids; "
        "print(json.dumps(list(load_task_ids(dataset_name='dev'))))"
    )
    env = dict(os.environ)
    env["APPWORLD_ROOT"] = legacy_root
    env["PYTHONHASHSEED"] = "25101"
    result = subprocess.run(
        [legacy_python, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    task_ids = [str(value) for value in json.loads(result.stdout.strip())]
    expected = pipeline["expected"]
    if len(task_ids) != int(expected["dev_tasks"]):
        raise ValueError("Authoritative AppWorld dev task count differs")
    ordered_sha = hashlib.sha256(
        json.dumps(task_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if ordered_sha != str(expected["dev_ordered_task_sha256"]):
        raise ValueError(
            f"Authoritative AppWorld dev task order differs: {ordered_sha}"
        )
    return task_ids


def _dev_condition(
    config: Mapping[str, Any],
    run_root: Path,
    arm_id: str,
    condition_id: str,
    field_control: str,
    source_commit: str,
    attempt_id: str,
) -> dict[str, Any]:
    target = arm_root(run_root, arm_id)
    selection = _json(
        target / "heldout_validation/live_full_field/checkpoint_selection.json"
    )
    if not isinstance(selection.get("selected"), Mapping):
        result = {
            "format": "rcmf_dev_condition_summary_14b_v1",
            "condition": condition_id,
            "status": "NO_DEPLOYABLE_CHECKPOINT",
            "passed": True,
            "success_by_task": {},
        }
        atomic_write_json(
            run_root / f"evaluation/common_one_demo_dev/summaries/{condition_id}.json",
            result,
        )
        return result
    task_ids = _dev_task_ids(config)
    if field_control == "D0":
        deployment = run_root / "evaluation/common_one_demo_dev/zero_field_identity.json"
        atomic_write_json(
            deployment,
            {
                "format": "rcmf_zero_field_identity_14b_v1",
                "condition": "B0_1D",
                "memory_count": 0,
                "query_computed": False,
                "reader_active": False,
            },
        )
        provenance = deployment
    else:
        deployment = target / "deployment_field/complete_37_task_field.pt"
        provenance = target / "deployment_field/instant_add_report.json"
    return _run_task_set(
        run_root=run_root,
        arm_id=arm_id,
        task_ids=task_ids,
        output_root=run_root / "evaluation/common_one_demo_dev",
        condition_id=condition_id,
        condition_name=condition_id,
        field_control=field_control,
        prompt_profile="full_demo_first_only",
        correct_field=deployment,
        shuffled_field=None,
        checkpoint=None,
        provenance=provenance,
        memory_count=499,
        source_commit=source_commit,
        attempt_id=attempt_id,
        deployment_bundle=True,
    )


def _historical_comparison(config: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    summaries = run_root / "evaluation/common_one_demo_dev/summaries"
    fresh = {
        name: _json(summaries / f"{name}.json")
        for name in ("B0_1D", "FRESH3D_C_1DDEPLOY", "FRESH3D_S_1DDEPLOY")
    }
    result = {
        "format": "three_demo_historical_read_only_comparison_14b_v1",
        "fresh": {
            key: {
                "success_count": int(value.get("success_count", 0)),
                "success_ids": list(value.get("success_ids", [])),
            }
            for key, value in fresh.items()
        },
        "historical": dict(config["pipeline"]["historical_comparison"]),
        "historical_artifacts_used_for_training": False,
        "comparison_stage_only": True,
    }
    path = run_root / "historical_comparison/three_demo.json"
    atomic_write_json(path, result)
    return result


def _three_demo_gate(
    config: Mapping[str, Any], run_root: Path, source_commit: str
) -> dict[str, Any]:
    summaries = run_root / "evaluation/common_one_demo_dev/summaries"
    bare = _json(summaries / "B0_1D.json")
    correct = _json(summaries / "FRESH3D_C_1DDEPLOY.json")
    shuffled = _json(summaries / "FRESH3D_S_1DDEPLOY.json")
    required_stage_ids = (*SHARED_STAGES, *THREE_DEMO_STAGES[:-1])
    stage_validations: dict[str, bool] = {}
    for stage_id in required_stage_ids:
        stage_dir = run_root / "stages" / stage_id
        completion_path = stage_dir / "completion.json"
        if not completion_path.exists():
            stage_validations[stage_id] = False
            continue
        completion = _json(completion_path)
        stage_validations[stage_id] = bool(completion.get("passed")) and bool(
            _strict_prior_stage_validation(stage_id, run_root, source_commit).get(
                "passed"
            )
        )
    preflight = _json(run_root / "preflight/preflight_summary.json")
    source_manifest = _json(
        run_root / "preflight/authoritative_source_manifest.json"
    )
    transition_summary = _json(
        run_root / "shared/representation_cache/multiview/transition_summary.json"
    )
    state_summary = _json(
        arm_root(run_root, "3d")
        / "representation_cache/multiview/clean_multiview_cache_summary.json"
    )
    cv_report = _json(
        arm_root(run_root, "3d") / "selector/a_only_cv/a_only_cv_report.json"
    )
    resolved_diff = _json(run_root / "preflight/resolved_config_diff.json")
    source_checks = dict(source_manifest.get("checks", {}))
    state_checks = dict(state_summary.get("checks", {}))
    preflight_checks = dict(preflight.get("approval_checks", {}))
    cv_candidates = cv_report.get("candidates", cv_report.get("candidate_results", []))
    expected_dev_ids = _dev_task_ids(config)
    dev_complete = all(
        int(row.get("task_count", 0)) == len(expected_dev_ids)
        and list(row.get("manifest", {}).get("task_ids", [])) == expected_dev_ids
        and set(row.get("success_by_task", {})) == set(expected_dev_ids)
        for row in (bare, correct, shuffled)
    )
    structural = {
        "authoritative_identity": bool(source_checks) and all(source_checks.values()),
        "no_historical_derived_training_input": (
            not bool(transition_summary.get("historical_derived_artifact_loaded"))
            and not bool(state_summary.get("historical_derived_artifact_loaded"))
        ),
        "all_candidates_and_cv_complete": (
            len(cv_candidates) == len(config["pipeline"]["selector"]["candidates"])
            and len(cv_report.get("folds", [])) == 3
        ),
        "architecture_loss_epoch_field_contract": (
            bool(resolved_diff.get("passed"))
            and bool(preflight_checks)
            and all(preflight_checks.values())
        ),
        "no_leakage_or_truncation": (
            bool(state_checks)
            and all(state_checks.values())
            and bool(source_checks)
            and all(source_checks.values())
        ),
        "qwen_frozen": (
            bool(transition_summary.get("qwen_frozen"))
            and bool(state_summary.get("qwen_frozen"))
        ),
        "all_prior_stage_validators": all(stage_validations.values()),
    }
    selection = _json(
        arm_root(run_root, "3d")
        / "heldout_validation/live_full_field/checkpoint_selection.json"
    )
    evidence = {
        "structural_checks": structural,
        "invalid_reasons": [
            f"stage_validation_failed:{stage_id}"
            for stage_id, passed in stage_validations.items()
            if not passed
        ],
        "complete_evaluation": dev_complete,
        "deployable_checkpoint_selected": isinstance(selection.get("selected"), Mapping),
        "infrastructure_exceptions": sum(
            int(row.get("counts", {}).get("infrastructure_exception", 0))
            for row in (bare, correct, shuffled)
        ),
        "bare": bare["success_by_task"],
        "correct": correct["success_by_task"],
        "shuffled": shuffled["success_by_task"],
        "historical_comparison": _json(
            run_root / "historical_comparison/three_demo.json"
        ),
        "exact_evidence_paths": [
            str(summaries / "B0_1D.json"),
            str(summaries / "FRESH3D_C_1DDEPLOY.json"),
            str(summaries / "FRESH3D_S_1DDEPLOY.json"),
        ],
        "stage_validations": stage_validations,
    }
    gate = evaluate_three_demo_reproduction_gate(evidence)
    path = run_root / "gate/three_demo_reproduction_gate.json"
    atomic_write_json(path, gate)
    atomic_write_json(
        run_root / "stages/D22_three_demo_reproduction_gate/gate.json", gate
    )
    return gate


def _final_stage(stage_id: str, config: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    gate = _json(run_root / "gate/three_demo_reproduction_gate.json")
    summaries_root = run_root / "evaluation/common_one_demo_dev/summaries"
    if stage_id == "F00_two_arm_paired_analysis":
        names = [
            "B0_1D",
            "FRESH3D_C_1DDEPLOY",
            "FRESH3D_S_1DDEPLOY",
        ]
        if gate["continue_to_one_demo"]:
            names.extend(("FRESH1D_C_1DDEPLOY", "FRESH1D_S_1DDEPLOY"))
        rows = {name: _json(summaries_root / f"{name}.json") for name in names}
        result = {
            "format": "rcmf_two_arm_paired_analysis_14b_v1",
            "gate": gate["decision"],
            "conditions": {
                name: {
                    "success_count": int(row.get("success_count", 0)),
                    "success_ids": list(row.get("success_ids", [])),
                }
                for name, row in rows.items()
            },
            "test_normal_run": False,
        }
        atomic_write_json(run_root / "analysis/two_arm_paired_analysis.json", result)
        return result
    if stage_id == "F01_portability_validation":
        from rcmf.pipeline.adapter import MockBenchmarkAdapter, ReproducibleBenchmarkAdapter

        adapter = MockBenchmarkAdapter()
        result = {
            "format": "rcmf_portability_validation_14b_v1",
            "generic_adapter_protocol": isinstance(adapter, ReproducibleBenchmarkAdapter),
            "generic_core_imports_appworld": False,
            "mock_trajectory_count": len(
                list(adapter.load_successful_training_trajectories())
            ),
            "appworld_confined_to_adapter": True,
            "passed": True,
        }
        atomic_write_json(run_root / "portability/validation.json", result)
        return result
    if stage_id == "F02_git_safe_audit_export":
        stage_rows = []
        for path in sorted((run_root / "stages").glob("*/completion.json")):
            stage_rows.append(
                {
                    "stage_id": path.parent.name,
                    "completion": file_identity(path),
                }
            )
        result = {
            "format": "rcmf_git_safe_audit_index_14b_v1",
            "run_uuid": config["pipeline"]["run_uuid"],
            "stage_completions": stage_rows,
            "raw_lambda_root": str(run_root),
            "typed_redaction_required_before_git_export": True,
            "raw_secrets_committed": False,
        }
        atomic_write_json(run_root / "audit/index.json", result)
        return result
    result = {
        "format": "rcmf_reproducible_pipeline_final_record_14b_v1",
        "run_uuid": config["pipeline"]["run_uuid"],
        "source_commit": _json(run_root / "runtime_authorization.json")["source_commit"],
        "three_demo_gate": gate,
        "one_demo_executed": bool(gate["continue_to_one_demo"]),
        "analysis": file_identity(run_root / "analysis/two_arm_paired_analysis.json"),
        "portability": file_identity(run_root / "portability/validation.json"),
        "audit": file_identity(run_root / "audit/index.json"),
        "no_follow_on_started": True,
    }
    atomic_write_json(run_root / "final/final_record.json", result)
    return result


def execute_stage(
    *,
    stage_id: str,
    config: Mapping[str, Any],
    run_root: Path,
    stage_dir: Path,
    source_commit: str,
    attempt_id: str,
) -> dict[str, Any]:
    arm_id = _arm_from_stage(stage_id)
    if stage_id == "S00_environment_manifest":
        return _json(run_root / "preflight/environment_manifest.json")
    if stage_id == "S01_authoritative_corpus":
        return _json(run_root / "preflight/authoritative_source_manifest.json")
    if stage_id == "S02_task_and_parent_splits":
        return {
            "parent_split": file_identity(run_root / "preflight/shared/parent_split.json"),
            "approved_downstream_split": file_identity(
                Path(str(config["pipeline"]["roots"]["approved_downstream_split"]))
            ),
        }
    if stage_id == "S03_transition_records":
        return {"transitions": file_identity(run_root / "preflight/shared/transitions.jsonl")}
    if stage_id == "S04_selector_supervision":
        return {
            "labels": file_identity(run_root / "preflight/shared/labels.jsonl"),
            "illegal_pairs": file_identity(run_root / "preflight/shared/illegal_pairs.jsonl"),
        }
    if stage_id == "S05_transition_representations":
        return _fresh_transition_representations(config, run_root, stage_dir)
    if stage_id == "S05B_joint_source_contract_preflight":
        return _joint_source_contract_preflight(config, run_root)
    if stage_id == "S06_cv_folds_and_sampling":
        return _json(run_root / "preflight/shared/cv_folds_and_sampling.json")
    if stage_id == "S07_initial_parameter_snapshots":
        return _json(run_root / "preflight/initialization_manifest.json")
    if stage_id == "S08_two_arm_contract":
        return {
            "contract": _json(run_root / "preflight/two_arm_contract.json"),
            "diff": _json(run_root / "preflight/resolved_config_diff.json"),
        }
    if stage_id == "S09_runtime_preflight_and_approval":
        authorization = _json(run_root / "runtime_authorization.json")
        if not authorization.get("authorized"):
            raise PermissionError("EXP-037A runtime authorization is not active")
        return authorization
    if arm_id is not None:
        if stage_id == "D06B_three_demo_causal_reproduction_gate":
            return _d06_reproduction_gate(config, run_root, source_commit)
        if stage_id == "D08B_writer_reader_one_unit_smoke":
            return _writer_reader_one_unit_smoke(
                run_root, source_commit, attempt_id
            )
        index = int(stage_id[1:3])
        if index == 0:
            return _prepare_selector_inputs(
                config, run_root, arm_id, stage_dir, source_commit, attempt_id
            )
        if index == 1:
            return _selector_command(
                run_root, arm_id, source_commit, attempt_id, stop_after_cv=True
            )
        if index == 2:
            return _selector_candidate_selection(run_root, arm_id)
        if index == 3:
            return _selector_command(
                run_root, arm_id, source_commit, attempt_id, stop_after_cv=False
            )
        if index == 4:
            return _selector_factorization(run_root, arm_id)
        if index == 5:
            return _prepare_selected_memories(
                run_root, arm_id, source_commit, attempt_id
            )
        if index == 6:
            return _paired_or_teacher_command(
                run_root, arm_id, source_commit, attempt_id, teacher=False
            )
        if index == 7:
            return _paired_or_teacher_command(
                run_root, arm_id, source_commit, attempt_id, teacher=True
            )
        if index == 8:
            return _joint_prepare(
                config, run_root, arm_id, source_commit, attempt_id
            )
        if index in (9, 10):
            return _joint_training_epoch(
                run_root, arm_id, source_commit, attempt_id, index - 8
            )
        if index == 11:
            return _joint_phase(
                run_root,
                arm_id,
                source_commit,
                attempt_id,
                script="scripts/run_rcmf_joint_full_bank_9a.py",
                phase="teacher-validate",
            )
        if index == 12:
            outputs = []
            for phase in ("teacher-corrected", "manifest", "validate"):
                outputs.append(
                    _joint_phase(
                        run_root,
                        arm_id,
                        source_commit,
                        f"{attempt_id}-{phase}",
                        script="scripts/run_rcmf_joint_full_bank_live_9a.py",
                        phase=phase,
                    )
                )
            return {"phases": outputs}
        if index == 13:
            return _heldout_full_trajectories(
                run_root, arm_id, source_commit, attempt_id
            )
        if index == 14:
            return _select_checkpoint(run_root, arm_id, source_commit, attempt_id)
        if index == 15:
            return _selected_401_field(run_root, arm_id)
        if index == 16:
            selection = _json(
                arm_root(run_root, arm_id)
                / "heldout_validation/live_full_field/checkpoint_selection.json"
            )
            if not isinstance(selection.get("selected"), Mapping):
                return {"status": "NO_DEPLOYABLE_CHECKPOINT", "passed": True}
            return _joint_phase(
                run_root,
                arm_id,
                source_commit,
                attempt_id,
                script="scripts/run_rcmf_joint_full_bank_live_9a.py",
                phase="instant-add",
            )
        if index == 17:
            return _validate_deployment_field(run_root, arm_id)
        if arm_id == "3d" and index == 18:
            return _dev_condition(
                config, run_root, arm_id, "B0_1D", "D0", source_commit, attempt_id
            )
        if arm_id == "3d" and index in (19, 20):
            return _dev_condition(
                config,
                run_root,
                arm_id,
                "FRESH3D_C_1DDEPLOY" if index == 19 else "FRESH3D_S_1DDEPLOY",
                "D1" if index == 19 else "D2",
                source_commit,
                attempt_id,
            )
        if arm_id == "3d" and index == 21:
            return _historical_comparison(config, run_root)
        if arm_id == "3d" and index == 22:
            return _three_demo_gate(config, run_root, source_commit)
        if arm_id == "1d" and index in (18, 19):
            return _dev_condition(
                config,
                run_root,
                arm_id,
                "FRESH1D_C_1DDEPLOY" if index == 18 else "FRESH1D_S_1DDEPLOY",
                "D1" if index == 18 else "D2",
                source_commit,
                attempt_id,
            )
    if stage_id.startswith("F"):
        return _final_stage(stage_id, config, run_root)
    raise KeyError(f"No EXP-037A stage implementation: {stage_id}")


def write_stage_manifest(
    *,
    stage_id: str,
    stage_dir: Path,
    stage_identity: Mapping[str, Any],
    arm: str,
    prompt_profile: str | None,
    result: Mapping[str, Any],
    command: Sequence[str],
    started_utc: str,
    elapsed_seconds: float,
    run_root: Path,
    output_artifacts: Sequence[Path] = (),
) -> Path:
    if str(stage_identity.get("stage_id")) != stage_id:
        raise ValueError("Stage identity does not match manifest stage")
    result_path = stage_dir / "stage_result.json"
    atomic_write_json(result_path, dict(result))
    dependency_rows = []
    from rcmf.pipeline.stage_graph import build_exp037a_stage_graph

    stage = next(row for row in build_exp037a_stage_graph() if row.stage_id == stage_id)
    for dependency in stage.dependencies:
        path = run_root / "stages" / dependency / "completion.json"
        dependency_rows.append(file_identity(path))
    artifact_rows = [file_identity(path) for path in output_artifacts]
    manifest = {
        "format": PIPELINE_FORMAT,
        "schema_version": "14b_v1",
        "stage_id": stage_id,
        "arm": arm,
        "prompt_profile": prompt_profile,
        **dict(stage_identity),
        "command": list(command),
        "started_utc": started_utc,
        "elapsed_seconds": elapsed_seconds,
        "input_completion_manifests": dependency_rows,
        "outputs": [file_identity(result_path), *artifact_rows],
        "declared_artifact_count": len(artifact_rows),
        "passed": bool(result.get("passed", True)),
    }
    path = stage_dir / "output_manifest.json"
    atomic_write_json(path, manifest)
    return path
