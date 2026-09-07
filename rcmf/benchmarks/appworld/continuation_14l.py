from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
import yaml

from rcmf.benchmarks.appworld.scoreable_count_contract_14l import (
    SEALED_UPSTREAM_OUTCOMES,
    scoreable_count_contract,
    validate_scoreable_population,
)
from rcmf.pipeline.authorization import (
    validate_explicit_authorization,
    validate_runtime_authorization,
)
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.orchestrator import load_pipeline_contract
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


PARENT_RUN_UUID = "rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001"
PARENT_SOURCE_COMMIT = "004f866647cfabb38a141b88e6d83821df88c403"
PARENT_CONFIG_SHA256 = (
    "f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69"
)
PARENT_CONTRACT_SHA256 = (
    "eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6"
)
PARENT_REQUIRED_STAGES = (
    "D22_three_demo_reproduction_gate",
    "O00_state_representations",
    "O01_selector_candidate_cv",
    "O02_selector_candidate_selection",
    "O03_final_selector_ensemble",
    "O04_selector_factorization",
    "O05_selected_memory_manifest",
    "O06_paired_causal_outcomes",
    "O07_policy_teacher",
)
PARENT_O08_PARTIAL_RELATIVE_PATHS = (
    "arms/1d/data/memory_provenance.jsonl",
    "arms/1d/data/rcmf_source_cache.pt",
    "arms/1d/data/key_payload_shuffle_manifest.json",
)


def _json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(path: str | Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _deduplicated_paths(paths: Sequence[Path]) -> list[Path]:
    by_path = {str(path.resolve(strict=False)): path for path in paths}
    return [by_path[key] for key in sorted(by_path)]


def _manifest_outputs(path: Path) -> list[Path]:
    manifest = _json(path)
    return [Path(str(row["path"])) for row in manifest.get("outputs", [])]


def _resolved_parent_inputs(parent_root: Path) -> list[tuple[str, Path]]:
    parent_contract_path = parent_root / "preflight/stage_dag.json"
    parent_contract = load_pipeline_contract(parent_contract_path)
    parent_pipeline_config = Path(
        str(parent_contract.metadata["pipeline_config_path"])
    )
    explicit_authorization_checks = validate_explicit_authorization(
        _json(parent_root / "preflight/explicit_user_authorization.json"),
        parent_contract,
        run_root=parent_root,
        contract_path=parent_contract_path,
        pipeline_config_path=parent_pipeline_config,
    )
    runtime_authorization_checks = validate_runtime_authorization(
        _json(parent_root / "runtime_authorization.json"),
        parent_contract,
        run_root=parent_root,
        contract_sha256=PARENT_CONTRACT_SHA256,
        pipeline_config_path=parent_pipeline_config,
    )
    resolved = yaml.safe_load(
        (parent_root / "resolved_configs/arm_1d.yaml").read_text(encoding="utf-8")
    )
    settings = resolved["stage_c_9a"]
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    inputs = [
        ("replay_manifest", parent_b / "replay_validated_corpus_manifest.json"),
        (
            "transition_manifest",
            parent_b
            / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        ),
        (
            "signature_manifest",
            parent_b
            / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        ),
        (
            "state_cache",
            parent_c / "representation_cache/multiview/state_multiview.pt",
        ),
        (
            "transition_cache",
            parent_c / "representation_cache/multiview/transition_multiview.pt",
        ),
        (
            "cache_summary",
            parent_c
            / "representation_cache/multiview/clean_multiview_cache_summary.json",
        ),
        ("selector_ensemble", parent_c / "selector/ensemble_scores.pt"),
        ("paired_outcomes", parent_a / "paired_causal/paired_outcomes.json"),
        (
            "teacher_cache",
            parent_a / "structured_compiler/policy_teacher_cache.pt",
        ),
        (
            "teacher_report",
            parent_a / "structured_compiler/policy_teacher_report.json",
        ),
        ("selected_memories", parent_a / "preflight/frozen_train_selections.jsonl"),
        ("task_split", Path(str(settings["task_split_manifest"]))),
        ("corpus_summary", corpus / "summary.json"),
        ("corpus_validation", corpus / "structural_validation.json"),
        ("decision_examples", corpus / "decision_examples.jsonl"),
    ]
    for path in sorted((parent_c / "selector").glob("seed_*/field_selector.pt")):
        inputs.append(("selector_member", path))
    return inputs


def collect_parent_artifact_manifest(
    *, parent_root: str | Path, generated_by_source: str
) -> dict[str, Any]:
    root = Path(parent_root).resolve(strict=True)
    paths: list[tuple[str, Path]] = []
    for stage_id in PARENT_REQUIRED_STAGES:
        stage_dir = root / "stages" / stage_id
        manifest = stage_dir / "output_manifest.json"
        paths.extend(
            (
                (f"{stage_id}:completion", stage_dir / "completion.json"),
                (f"{stage_id}:output_manifest", manifest),
                (f"{stage_id}:validator", stage_dir / "validator.json"),
            )
        )
        paths.extend(
            (f"{stage_id}:declared_output", path)
            for path in _manifest_outputs(manifest)
        )
    paths.extend(_resolved_parent_inputs(root))
    paths.extend(
        (
            ("parent_resolved_arm_1d", root / "resolved_configs/arm_1d.yaml"),
            ("parent_pipeline_contract", root / "preflight/stage_dag.json"),
            ("parent_initialization_manifest", root / "preflight/initialization_manifest.json"),
            ("parent_writer_initial", root / "preflight/initialization_snapshots/writer_initial.pt"),
            ("parent_reader_initial", root / "preflight/initialization_snapshots/reader_initial.pt"),
            ("parent_explicit_authorization", root / "preflight/explicit_user_authorization.json"),
            ("parent_runtime_authorization", root / "runtime_authorization.json"),
            ("parent_o08_completion", root / "stages/O08_zero_cache_and_training_units/completion.json"),
            ("parent_o08_failure", root / "stages/O08_zero_cache_and_training_units/failure.json"),
            ("parent_three_demo_bare", root / "evaluation/common_one_demo_dev/summaries/B0_1D.json"),
            ("parent_three_demo_correct", root / "evaluation/common_one_demo_dev/summaries/FRESH3D_C_1DDEPLOY.json"),
            ("parent_three_demo_shuffle", root / "evaluation/common_one_demo_dev/summaries/FRESH3D_S_1DDEPLOY.json"),
        )
    )
    roles_by_path: dict[str, set[str]] = {}
    concrete: dict[str, Path] = {}
    for role, path in paths:
        resolved = path.resolve(strict=True)
        key = str(resolved)
        concrete[key] = resolved
        roles_by_path.setdefault(key, set()).add(role)
    artifacts = []
    for key in sorted(concrete):
        identity = file_identity(concrete[key])
        artifacts.append(
            {
                **identity,
                "roles": sorted(roles_by_path[key]),
                "read_only": True,
                "scientific_input_boundary": "sealed_parent_o07_to_continuation_o08",
            }
        )
    prohibited = []
    for relative in PARENT_O08_PARTIAL_RELATIVE_PATHS:
        path = root / relative
        prohibited.append(
            {
                "relative_path": relative,
                "exists": path.is_file(),
                "identity": file_identity(path) if path.is_file() else None,
                "allowed_as_continuation_input": False,
            }
        )
    closure = [
        {
            "path": row["path"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "roles": row["roles"],
        }
        for row in artifacts
    ]
    return {
        "format": "exp037a_parent_artifact_manifest_14l_v1",
        "generated_by_source": generated_by_source,
        "parent": {
            "run_uuid": PARENT_RUN_UUID,
            "run_root": str(root),
            "source_commit": PARENT_SOURCE_COMMIT,
            "pipeline_config_sha256": PARENT_CONFIG_SHA256,
            "contract_sha256": PARENT_CONTRACT_SHA256,
        },
        "required_stages": list(PARENT_REQUIRED_STAGES),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "artifact_closure_sha256": content_sha256(closure),
        "prohibited_parent_o08_partials": prohibited,
        "parent_files_opened_for_writing": False,
    }


def validate_parent_artifact_manifest(
    manifest: Mapping[str, Any], *, parent_root: str | Path
) -> dict[str, Any]:
    root = Path(parent_root).resolve(strict=True)
    parent = manifest.get("parent", {})
    checks: dict[str, bool] = {
        "parent_run_uuid": str(parent.get("run_uuid")) == PARENT_RUN_UUID,
        "parent_run_root": Path(str(parent.get("run_root", ""))).resolve(
            strict=False
        )
        == root,
        "parent_source": str(parent.get("source_commit")) == PARENT_SOURCE_COMMIT,
        "parent_config": str(parent.get("pipeline_config_sha256"))
        == PARENT_CONFIG_SHA256,
        "parent_contract": str(parent.get("contract_sha256"))
        == PARENT_CONTRACT_SHA256,
        "required_stages": list(manifest.get("required_stages", []))
        == list(PARENT_REQUIRED_STAGES),
        "artifact_count": int(manifest.get("artifact_count", -1))
        == len(manifest.get("artifacts", [])),
        "artifact_hashes": True,
        "parent_o08_partials_excluded": True,
    }
    closure = []
    prohibited = {
        str((root / relative).resolve(strict=False))
        for relative in PARENT_O08_PARTIAL_RELATIVE_PATHS
    }
    for row in manifest.get("artifacts", []):
        path = Path(str(row.get("path", "")))
        if (
            not path.is_file()
            or path.stat().st_size != int(row.get("size_bytes", -1))
            or sha256_file(path) != str(row.get("sha256"))
        ):
            checks["artifact_hashes"] = False
        if str(path.resolve(strict=False)) in prohibited:
            checks["parent_o08_partials_excluded"] = False
        closure.append(
            {
                "path": str(row.get("path")),
                "size_bytes": int(row.get("size_bytes", -1)),
                "sha256": str(row.get("sha256")),
                "roles": list(row.get("roles", [])),
            }
        )
    checks["artifact_closure_sha256"] = content_sha256(closure) == str(
        manifest.get("artifact_closure_sha256")
    )
    result = {
        "format": "exp037a_parent_artifact_validation_14l_v1",
        "checks": checks,
        "artifact_count": len(closure),
        "artifact_closure_sha256": content_sha256(closure),
        "passed": all(checks.values()),
    }
    if not result["passed"]:
        raise ValueError(f"Parent artifact closure failed: {checks}")
    return result


def validate_parent_boundary(
    *, manifest: Mapping[str, Any], continuation_root: str | Path
) -> dict[str, Any]:
    parent_root = Path(str(manifest["parent"]["run_root"])).resolve(strict=True)
    continuation = Path(continuation_root).resolve(strict=False)
    if continuation == parent_root or parent_root in continuation.parents:
        raise ValueError("Continuation root overlaps the sealed parent root")
    closure = validate_parent_artifact_manifest(manifest, parent_root=parent_root)
    stage_validations = {
        stage_id: validate_stage_completion(
            parent_root / "stages" / stage_id,
            PARENT_SOURCE_COMMIT,
            expected_run_uuid=PARENT_RUN_UUID,
            expected_pipeline_config_sha256=PARENT_CONFIG_SHA256,
            expected_contract_sha256=PARENT_CONTRACT_SHA256,
            expected_run_root=parent_root,
            write_validator=False,
        )
        for stage_id in PARENT_REQUIRED_STAGES
    }
    parent_contract_path = parent_root / "preflight/stage_dag.json"
    parent_contract = load_pipeline_contract(parent_contract_path)
    parent_pipeline_config = Path(
        str(parent_contract.metadata["pipeline_config_path"])
    )
    explicit_authorization_checks = validate_explicit_authorization(
        _json(parent_root / "preflight/explicit_user_authorization.json"),
        parent_contract,
        run_root=parent_root,
        contract_path=parent_contract_path,
        pipeline_config_path=parent_pipeline_config,
    )
    runtime_authorization_checks = validate_runtime_authorization(
        _json(parent_root / "runtime_authorization.json"),
        parent_contract,
        run_root=parent_root,
        contract_sha256=PARENT_CONTRACT_SHA256,
        pipeline_config_path=parent_pipeline_config,
    )
    resolved = yaml.safe_load(
        (parent_root / "resolved_configs/arm_1d.yaml").read_text(encoding="utf-8")
    )
    settings = resolved["stage_c_9a"]
    split = _json(Path(str(settings["task_split_manifest"])))
    outcomes_path = parent_root / "arms/1d/paired_causal/paired_outcomes.json"
    teacher_path = parent_root / "arms/1d/structured_compiler/policy_teacher_cache.pt"
    report_path = parent_root / "arms/1d/structured_compiler/policy_teacher_report.json"
    selections_path = parent_root / "arms/1d/preflight/frozen_train_selections.jsonl"
    outcomes = _json(outcomes_path)
    teacher = torch.load(teacher_path, map_location="cpu", weights_only=True)
    population = validate_scoreable_population(
        contract=scoreable_count_contract(
            arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
        ),
        outcomes=outcomes,
        teacher_cache=teacher,
        teacher_report=_json(report_path),
        selection_rows=_rows(selections_path),
        train_task_ids=split["train_task_ids"],
        heldout_task_ids=split["validation_task_ids"],
        minimum_per_label=40,
        maximum_state_count=499,
        outcomes_sha256=sha256_file(outcomes_path),
        teacher_cache_sha256=sha256_file(teacher_path),
    )
    d22 = _json(parent_root / "stages/D22_three_demo_reproduction_gate/gate.json")
    o08_completion = _json(
        parent_root / "stages/O08_zero_cache_and_training_units/completion.json"
    )
    o08_manifest = parent_root / "stages/O08_zero_cache_and_training_units/output_manifest.json"
    checks = {
        "parent_closure": bool(closure["passed"]),
        "parent_explicit_authorization_valid": all(
            explicit_authorization_checks.values()
        ),
        "parent_runtime_authorization_valid": all(
            runtime_authorization_checks.values()
        ),
        "required_stages_strict_valid": all(
            bool(row.get("passed")) for row in stage_validations.values()
        ),
        "d22_exact_pass": d22.get("decision")
        == "THREE_DEMO_REPRODUCTION_PASS"
        and d22.get("continue_to_one_demo") is True,
        "o06_o07_population_valid": bool(population["passed"]),
        "o08_parent_failed": o08_completion.get("passed") is False,
        "o08_parent_has_no_output_manifest": not o08_manifest.exists(),
        "parent_o08_partials_excluded": all(
            not any(
                str(row["path"]) == str((parent_root / relative).resolve(strict=False))
                for row in manifest["artifacts"]
            )
            for relative in PARENT_O08_PARTIAL_RELATIVE_PATHS
        ),
        "continuation_root_is_separate": continuation != parent_root,
    }
    result = {
        "format": "exp037a_continuation_boundary_validation_14l_v1",
        "checks": checks,
        "parent_stage_validations": stage_validations,
        "parent_authorization_validations": {
            "explicit": explicit_authorization_checks,
            "runtime": runtime_authorization_checks,
        },
        "scoreable_population": population,
        "sealed_inputs": {
            "paired_outcomes": file_identity(outcomes_path),
            "teacher_cache": file_identity(teacher_path),
            "teacher_report": file_identity(report_path),
            "selected_memories": file_identity(selections_path),
        },
        "parent_o08_partial_outputs_used": False,
        "parent_files_opened_for_writing": False,
        "continuation_root": str(continuation),
        "passed": all(checks.values()),
    }
    if not result["passed"]:
        raise ValueError(f"Continuation boundary validation failed: {checks}")
    return result


def build_continuation_arm_config(
    *,
    parent_root: str | Path,
    continuation_root: str | Path,
    continuation_run_uuid: str,
    working_branch: str,
    proposed_hard_cap_hours: float,
) -> dict[str, Any]:
    parent = Path(parent_root).resolve(strict=True)
    target_root = Path(continuation_root).resolve(strict=False)
    resolved = yaml.safe_load(
        (parent / "resolved_configs/arm_1d.yaml").read_text(encoding="utf-8")
    )
    config = copy.deepcopy(resolved)
    settings = config["stage_c_9a"]
    settings.update(
        {
            "run_uuid": f"{continuation_run_uuid}-1d",
            "artifact_dir": str(target_root / "arms/1d"),
            "starting_head": "resolved_at_launch",
            "working_branch": working_branch,
            "parent_exp025b": str(parent / "shared/compat_exp025b"),
            "parent_exp025c": str(parent / "arms/1d"),
            "parent_exp028a": str(parent / "arms/1d"),
        }
    )
    settings["expected"] = {
        **settings["expected"],
        "deployment_dev_task_count": 57,
    }
    settings["expected"].pop("scoreable_train_state_count", None)
    settings["expected"].pop("scoreable_heldout_state_count", None)
    settings["scoreable_count_contract"] = scoreable_count_contract(
        arm_id="1d", policy=SEALED_UPSTREAM_OUTCOMES
    )
    panel = config["stage_c_7hr"]["panel"]
    settings["causal_panel_contract"] = {
        "initial_state_count": int(panel["initial_state_count"]),
        "maximum_state_count": int(panel["maximum_state_count"]),
        "minimum_per_label": int(panel["minimum_per_label"]),
    }
    settings["prompt_dependent_inputs"] = {
        "state_cache": str(
            parent / "arms/1d/representation_cache/multiview/state_multiview.pt"
        ),
        "outcomes": str(parent / "arms/1d/paired_causal/paired_outcomes.json"),
        "teacher_cache": str(
            parent / "arms/1d/structured_compiler/policy_teacher_cache.pt"
        ),
        "teacher_report": str(
            parent / "arms/1d/structured_compiler/policy_teacher_report.json"
        ),
        "selections": str(
            parent / "arms/1d/preflight/frozen_train_selections.jsonl"
        ),
    }
    settings["runtime"]["review_threshold_h100_hours"] = float(
        proposed_hard_cap_hours
    )
    config["stage_c_11b"] = {
        **config["stage_c_11b"],
        "run_uuid": f"{continuation_run_uuid}-1d",
        "artifact_dir": str(target_root / "arms/1d"),
        "prompt_profile": "full_demo_first_only",
    }
    if config["benchmark"]["prompt_profile"] != "full_demo_first_only":
        raise ValueError("Parent 1D prompt profile is not full_demo_first_only")
    return config


def execute_parent_import(config: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    continuation = config["pipeline"]["continuation"]
    manifest_path = Path(str(continuation["parent_artifact_manifest_path"]))
    manifest = _json(manifest_path)
    validation = validate_parent_artifact_manifest(
        manifest, parent_root=continuation["parent_root"]
    )
    contract = _json(run_root / "preflight/stage_dag.json")
    expected_sha = str(
        contract.get("metadata", {}).get("parent_artifact_manifest_sha256", "")
    )
    if not expected_sha or sha256_file(manifest_path) != expected_sha:
        raise ValueError("Parent artifact manifest SHA differs from continuation contract")
    runtime_authorization = _json(run_root / "runtime_authorization.json")
    runtime_manifest = collect_parent_artifact_manifest(
        parent_root=continuation["parent_root"],
        generated_by_source=str(runtime_authorization["source_commit"]),
    )
    if runtime_manifest != manifest:
        raise ValueError("Runtime parent artifact closure differs from sealed preflight")
    runtime_manifest_path = (
        run_root / "continuation/parent_artifact_manifest_runtime.json"
    )
    atomic_write_json(runtime_manifest_path, runtime_manifest)
    result = {
        "format": "exp037a_parent_run_evidence_import_14l_v1",
        "parent_manifest": file_identity(manifest_path),
        "runtime_parent_manifest": file_identity(runtime_manifest_path),
        "runtime_manifest_exact_preflight_match": True,
        "validation": validation,
        "parent_artifacts_copied": False,
        "parent_completion_files_rewritten": False,
        "passed": True,
    }
    atomic_write_json(run_root / "continuation/parent_run_evidence_import.json", result)
    return result


def execute_boundary_validation(
    config: Mapping[str, Any], run_root: Path
) -> dict[str, Any]:
    continuation = config["pipeline"]["continuation"]
    manifest = _json(continuation["parent_artifact_manifest_path"])
    result = validate_parent_boundary(
        manifest=manifest,
        continuation_root=run_root,
    )
    atomic_write_json(run_root / "continuation/boundary_validation.json", result)
    return result
