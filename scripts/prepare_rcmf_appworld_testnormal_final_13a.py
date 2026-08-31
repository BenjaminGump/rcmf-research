"""Freeze EXP-036A packages, AppWorld test_normal tasks, and leakage audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_appworld_testnormal_final_13a import (
    CONDITIONS,
    GLOBAL_SEED,
    build_condition_manifest,
    ordered_sha256,
    validate_field_payload,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.prepare_rcmf_one_demo_dev_11a import (
    _prompt_manifest,
    _ready_subscript_keys,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _attempt_ids


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
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_or_validate(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if read_json(path) != dict(value):
            raise ValueError(f"Existing immutable manifest differs: {path}")
        return
    atomic_write_json(path, dict(value))


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def legacy_inventory(
    settings_9a: Mapping[str, Any], demo_instruction: str
) -> dict[str, Any]:
    app = settings_9a["appworld"]
    code = r'''
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from appworld import load_task_ids

root = Path(os.environ["APPWORLD_ROOT"])
ids = list(load_task_ids(dataset_name="test_normal"))
needle = os.environ["RCMF_DEMO_INSTRUCTION"]
matches = []
spec_rows = []
for path in sorted((root / "data/tasks").glob("*/specs.json")):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    relative = path.relative_to(root).as_posix()
    spec_rows.append([relative, digest, len(raw)])
    payload = json.loads(raw)
    if str(payload.get("instruction", "")) == needle:
        matches.append({"task_id": path.parent.name, "specs_sha256": digest})
manifest_raw = json.dumps(spec_rows, separators=(",", ":")).encode("utf-8")
versions = {}
for package in ("appworld", "torch", "transformers"):
    try:
        versions[package] = importlib.metadata.version(package)
    except Exception as error:
        versions[package] = "unavailable:" + type(error).__name__
print(json.dumps({
    "test_normal_ids": ids,
    "demo_matches": matches,
    "versions": versions,
    "data_root": str(root),
    "task_spec_count": len(spec_rows),
    "task_spec_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
}, sort_keys=True))
'''
    env = dict(os.environ)
    env["APPWORLD_ROOT"] = str(app["legacy_root"])
    env["RCMF_DEMO_INSTRUCTION"] = demo_instruction
    completed = subprocess.run(
        [str(app["legacy_python"]), "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("Legacy AppWorld inventory emitted unexpected stdout")
    return json.loads(lines[0])


def package_manifest(settings: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    packages: dict[str, Any] = {}
    memory_ids: list[str] | None = None
    provenance_rows: list[dict[str, Any]] | None = None
    for name in ("BEST", "FULL1D"):
        source = settings["packages"][name]
        paths = {
            key: Path(str(source[key]))
            for key in (
                "selector_ensemble",
                "writer_reader_checkpoint",
                "deployment_field",
                "data_manifest",
                "source_cache",
                "memory_provenance",
                "shuffle_manifest",
                "instant_add_report",
            )
        }
        missing = {key: str(path) for key, path in paths.items() if not path.exists()}
        if missing:
            raise FileNotFoundError(f"{name} package artifacts are missing: {missing}")
        hashes = {key: sha256_file(path) for key, path in paths.items()}
        expected = {
            "selector_ensemble": str(source["selector_ensemble_sha256"]),
            "writer_reader_checkpoint": str(
                source["writer_reader_checkpoint_sha256"]
            ),
            "deployment_field": str(source["deployment_field_sha256"]),
            "shuffle_manifest": str(source["shuffle_manifest_sha256"]),
        }
        identity_checks = {key: hashes[key] == value for key, value in expected.items()}
        if not all(identity_checks.values()):
            raise ValueError(f"{name} package SHA differs: {identity_checks}")

        selector = torch.load(paths["selector_ensemble"], map_location="cpu", weights_only=False)
        checkpoint = torch.load(
            paths["writer_reader_checkpoint"], map_location="cpu", weights_only=False
        )
        field = torch.load(paths["deployment_field"], map_location="cpu", weights_only=False)
        field_identity = validate_field_payload(
            field,
            expected_checkpoint_sha256=hashes["writer_reader_checkpoint"],
            memory_count=int(settings["shared"]["memory_count"]),
        )
        shuffle = read_json(paths["shuffle_manifest"])
        deployment_shuffle = shuffle["complete_deployment_bank"]
        shuffle_checks = {
            "memory_count": int(deployment_shuffle["memory_count"]) == 499,
            "fixed_point_count": int(deployment_shuffle["fixed_point_count"]) == 0,
            "outcomes_unused": not bool(shuffle["selection_uses_outcomes"]),
        }
        if not all(shuffle_checks.values()):
            raise ValueError(f"{name} shuffle identity differs: {shuffle_checks}")
        package_memory_ids = [str(value) for value in field["memory_ids"]]
        if memory_ids is None:
            memory_ids = package_memory_ids
        elif package_memory_ids != memory_ids:
            raise ValueError("BEST and FULL1D use different ordered memory ledgers")
        package_provenance = read_jsonl(paths["memory_provenance"])
        if provenance_rows is None:
            provenance_rows = package_provenance
        elif package_provenance != provenance_rows:
            raise ValueError("BEST and FULL1D provenance ledgers differ")
        members = [
            str(row.get("checkpoint_sha256", row.get("sha256", "")))
            for row in selector.get("seed_checkpoints", [])
        ]
        member_paths = [
            str(row.get("checkpoint", ""))
            for row in selector.get("seed_checkpoints", [])
        ]
        if any(not value for value in members) or any(
            not Path(value).exists() for value in member_paths
        ):
            raise ValueError(f"{name} selector member identity is incomplete")
        if any(
            sha256_file(Path(path)) != digest
            for path, digest in zip(member_paths, members, strict=True)
        ):
            raise ValueError(f"{name} selector member checkpoint SHA differs")
        packages[name] = {
            "scientific_role": str(source["scientific_role"]),
            "selector_root": str(source["selector_root"]),
            "selected_epoch": int(source["selected_epoch"]),
            "paths": {key: str(path) for key, path in paths.items()},
            "hashes": hashes,
            "expected_hashes": expected,
            "identity_checks": identity_checks,
            "selector_member_count": len(selector.get("seed_checkpoints", [])),
            "selector_member_sha256": members,
            "selector_member_paths": member_paths,
            "checkpoint_reader_sha256": str(checkpoint.get("reader_sha256")),
            "checkpoint_writer_sha256": str(checkpoint.get("writer_sha256")),
            "reader_parameter_count": sum(
                int(value.numel()) for value in checkpoint["reader_state_dict"].values()
            ),
            "writer_parameter_count": sum(
                int(value.numel()) for value in checkpoint["writer_state_dict"].values()
            ),
            "field": field_identity,
            "shuffle_checks": shuffle_checks,
        }
    assert memory_ids is not None and provenance_rows is not None
    payload = {
        "format": "rcmf_appworld_testnormal_package_manifest_13a_v1",
        "global_seed": GLOBAL_SEED,
        "primary_method": "BEST",
        "secondary_ablation": "FULL1D",
        "packages": packages,
        "shared_memory_count": len(memory_ids),
        "shared_memory_ids_sha256": ordered_sha256(memory_ids),
        "same_ordered_memory_ledger": True,
        "prompt_profile": str(settings["prompt_profile"]),
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload, provenance_rows


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    settings_9a = cfg.raw["stage_c_9a"]
    if not (args.source_head == args.github_head == args.lambda_head == git_head()):
        raise ValueError("Local/GitHub/Lambda/execution HEAD identities differ")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-036A requires seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    packages, provenance = package_manifest(settings)
    prompt = _prompt_manifest(cfg.raw["stage_c_11a"])
    prompt.pop("manifest_sha256", None)
    prompt["format"] = "rcmf_appworld_testnormal_prompt_manifest_13a_v1"
    prompt["frozen_before_test_generation"] = True
    prompt["test_outcomes_used"] = False
    prompt["manifest_sha256"] = canonical_sha256(prompt)
    if (
        str(prompt["one_demo_initial_messages_sha256"])
        != str(settings["prompt"]["initial_messages_sha256"])
        or str(prompt["retained_demo_sha256"])
        != str(settings["prompt"]["retained_demo_sha256"])
    ):
        raise ValueError("One-demo prompt identity differs")

    legacy = legacy_inventory(
        settings_9a, str(cfg.raw["stage_c_11a"]["prompt"]["retained_demo_instruction"])
    )
    task_ids = [str(value) for value in legacy["test_normal_ids"]]
    if len(task_ids) != int(settings["test_normal"]["expected_task_count"]):
        raise ValueError("Official AppWorld test_normal task count differs")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Official AppWorld test_normal list has duplicates")
    if ordered_sha256(task_ids) != str(
        settings["test_normal"]["ordered_task_ids_sha256"]
    ):
        raise ValueError("Official AppWorld test_normal ordered identity differs")
    demo_matches = list(legacy["demo_matches"])
    if len(demo_matches) > 1:
        raise RuntimeError("Retained demo task identity is not unique")
    demo_task_id = str(demo_matches[0]["task_id"]) if demo_matches else None
    memory_parent_ids = {str(row["parent_task_id"]) for row in provenance}
    demo_overlap = demo_task_id in set(task_ids) if demo_task_id else False
    memory_overlap = sorted(memory_parent_ids & set(task_ids))

    forbidden = {
        "required_apps", "required_apis", "ground_truth", "solution", "answer",
        "evaluation_code", "compiled_solution", "ground_truth_api_calls",
        "difficulty", "allowed_apps",
    }
    ready_keys = _ready_subscript_keys()
    leaked_ready_keys = sorted(set(ready_keys) & forbidden)
    if demo_overlap or memory_overlap or leaked_ready_keys:
        raise RuntimeError(
            "EXP-036A leakage audit failed: "
            f"demo={demo_overlap}, memory={memory_overlap}, ground_truth={leaked_ready_keys}"
        )
    leakage = {
        "format": "rcmf_appworld_testnormal_leakage_audit_13a_v1",
        "dev_ground_truth_model_input_leak_count": 0,
        "test_ground_truth_model_input_leak_count": len(leaked_ready_keys),
        "model_visible_ready_keys": ready_keys,
        "forbidden_ready_keys_used": leaked_ready_keys,
        "demo_task_id": demo_task_id,
        "demo_exact_instruction_match_count": len(demo_matches),
        "demo_overlaps_test_normal": demo_overlap,
        "memory_parent_overlaps_test_normal": memory_overlap,
        "authoritative_evaluation_after_generation": True,
        "passed": not demo_overlap and not memory_overlap and not leaked_ready_keys,
    }
    test_manifest = {
        "format": "rcmf_official_testnormal_manifest_13a_v1",
        "dataset_name": "test_normal",
        "source": "appworld.load_task_ids(dataset_name=''test_normal'')",
        "legacy_python": str(settings_9a["appworld"]["legacy_python"]),
        "legacy_root": str(settings_9a["appworld"]["legacy_root"]),
        "versions": legacy["versions"],
        "data_root_task_spec_count": int(legacy["task_spec_count"]),
        "data_root_task_spec_manifest_sha256": str(
            legacy["task_spec_manifest_sha256"]
        ),
        "task_ids": task_ids,
        "task_count": len(task_ids),
        "ordered_task_ids_sha256": ordered_sha256(task_ids),
        "no_subset": True,
        "partially_exposed_by_prior_work": True,
        "outcomes_inspected_before_manifest_freeze": False,
    }
    test_manifest["manifest_sha256"] = canonical_sha256(test_manifest)
    conditions = build_condition_manifest(
        run_uuid=str(settings["run_uuid"]),
        task_ids=task_ids,
        package_manifest_sha256=str(packages["manifest_sha256"]),
    )
    smoke_manifest = {
        "format": "rcmf_appworld_testnormal_smoke_manifest_13a_v1",
        "task_ids": task_ids[:2],
        "repeat_task_id": task_ids[0],
        "conditions": list(CONDITIONS),
        "complete_trajectory_count": 15,
        "non_scientific_engineering_gate": True,
        "outcomes_cannot_modify_science": True,
    }
    smoke_manifest["manifest_sha256"] = canonical_sha256(smoke_manifest)
    run_manifest = {
        "format": "rcmf_appworld_testnormal_run_manifest_13a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "source_head": args.source_head,
        "starting_head": str(settings["starting_head"]),
        "working_branch": str(settings["working_branch"]),
        "global_seed": GLOBAL_SEED,
        "config": str(args.config),
        "config_sha256": sha256_file(args.config),
        "package_manifest_sha256": packages["manifest_sha256"],
        "prompt_manifest_sha256": prompt["manifest_sha256"],
        "test_manifest_sha256": test_manifest["manifest_sha256"],
        "condition_manifest_sha256": conditions["manifest_sha256"],
        "leakage_audit": leakage,
        "optimizer_steps": 0,
        "backward_passes": 0,
        "formal_trajectory_count": 840,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "raw_memory_prompt": False,
    }
    run_manifest["manifest_sha256"] = canonical_sha256(run_manifest)
    hashes = {
        "config": sha256_file(args.config),
        "package_manifest": str(packages["manifest_sha256"]),
        "prompt_manifest": str(prompt["manifest_sha256"]),
        "test_manifest": str(test_manifest["manifest_sha256"]),
        "condition_manifest": str(conditions["manifest_sha256"]),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="exp036a_prepare",
        command=list(sys.argv),
        local_head=args.source_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        write_or_validate(args.artifact_dir / "run_manifest.json", run_manifest)
        write_or_validate(args.artifact_dir / "manifests/package_manifest.json", packages)
        write_or_validate(args.artifact_dir / "manifests/prompt_manifest.json", prompt)
        write_or_validate(args.artifact_dir / "manifests/test_normal_manifest.json", test_manifest)
        write_or_validate(args.artifact_dir / "manifests/condition_manifest.json", conditions)
        write_or_validate(args.artifact_dir / "manifests/smoke_manifest.json", smoke_manifest)
        write_or_validate(args.artifact_dir / "manifests/leakage_audit.json", leakage)
        attempt.progress(
            status="exp036a_prepare_complete",
            completed_units=840,
            total_units=840,
            latest_validated_checkpoint=str(
                args.artifact_dir / "manifests/condition_manifest.json"
            ),
            result={
                "task_count": 168,
                "condition_count": 840,
                "task_list_sha256": test_manifest["ordered_task_ids_sha256"],
                "test_ground_truth_model_input_leak_count": 0,
            },
        )
    print(json.dumps(run_manifest, sort_keys=True))


if __name__ == "__main__":
    main()
