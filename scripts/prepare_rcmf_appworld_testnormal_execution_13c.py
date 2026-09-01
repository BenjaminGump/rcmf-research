"""Prepare EXP-036C by reusing the exact EXP-036B runtime and smoke evidence."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

if os.environ.get("PYTHONHASHSEED") != "25101":
    raise RuntimeError("Launch EXP-036C preparation through the 13b hash-seed launcher")

import _bootstrap  # noqa: E402,F401

from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (  # noqa: E402
    assert_hash_seed_process,
    read_mode_manifest,
    validate_formal_manifest,
    write_process_identity,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256  # noqa: E402
from rcmf.utils.serialization import atomic_write_json, sha256_file  # noqa: E402
import scripts.prepare_rcmf_appworld_testnormal_final_13a as base  # noqa: E402


RUN_UUID = "rcmf_appworld_testnormal_final_13c_20260901_001"
SOURCE_RUN_UUID = "rcmf_appworld_testnormal_final_13b_20260831_001"
CONDITIONS = ["B0", "BEST-C", "BEST-S", "FULL1D-C", "FULL1D-S"]
SCIENTIFIC_CONFIG_KEYS = (
    "prompt",
    "test_normal",
    "packages",
    "shared",
    "efficiency",
    "reversibility",
)


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, value: dict[str, object]) -> None:
    if path.exists():
        if read_json(path) != value:
            raise ValueError(f"Existing immutable EXP-036C artifact differs: {path}")
        return
    atomic_write_json(path, value)


def verify_file(path: Path, expected_sha256: str) -> None:
    if not path.is_file() or sha256_file(path) != expected_sha256:
        raise ValueError(f"Immutable EXP-036B source artifact differs: {path}")


def condition_manifest_for_run(
    source: dict[str, object], run_uuid: str
) -> dict[str, object]:
    result = copy.deepcopy(source)
    result["run_uuid"] = run_uuid
    result.pop("manifest_sha256", None)
    result["manifest_sha256"] = canonical_sha256(result)
    return result


def authorized_runtime_preflight(
    source: dict[str, object], *, authorization_sha256: str
) -> dict[str, object]:
    result = copy.deepcopy(source)
    result.pop("report_sha256", None)
    result["format"] = "rcmf_appworld_testnormal_runtime_preflight_13c_v1"
    result["run_uuid"] = RUN_UUID
    result["approved_wall_hours"] = 200.0
    result["automatic_launch_allowed"] = (
        float(result["conservative_total_wall_hours"]) <= 200.0
    )
    result["source_smoke_run_uuid"] = SOURCE_RUN_UUID
    result["source_smoke_reused"] = True
    result["authorization_record_sha256"] = authorization_sha256
    result["old_42_hour_cap_superseded"] = True
    result["report_sha256"] = canonical_sha256(result)
    return result


def atomic_resume_fixture(
    *, source_head: str, config_sha256: str, condition_manifest_sha256: str
) -> dict[str, object]:
    result = {
        "format": "rcmf_exp036c_atomic_resume_fixture_v1",
        "non_scientific": True,
        "run_uuid": RUN_UUID,
        "source_head": source_head,
        "config_sha256": config_sha256,
        "condition_manifest_sha256": condition_manifest_sha256,
        "task_id": "fixture-task",
        "condition": "B0",
        "audit_complete": True,
    }
    result["result_sha256"] = canonical_sha256(result)
    return result


def source_smoke_passed(smoke: dict[str, object]) -> bool:
    comparisons = smoke.get("determinism")
    return (
        bool(smoke.get("passed"))
        and bool(smoke.get("deterministic"))
        and int(smoke.get("trajectory_count", 0)) == 15
        and isinstance(comparisons, dict)
        and set(comparisons) == set(CONDITIONS)
        and all(bool(row.get("passed")) for row in comparisons.values())
    )


def main() -> None:
    assert_hash_seed_process()
    args = base.parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    continuation = settings["continuation"]
    if str(settings["run_uuid"]) != RUN_UUID:
        raise ValueError("EXP-036C run UUID differs")
    if float(settings["runtime"]["approved_wall_hours"]) != 200.0:
        raise ValueError("EXP-036C requires the explicit 200-hour authorization")

    source_root = Path(str(continuation["source_artifact_dir"])).resolve()
    source_files = {
        "mode": source_root / "manifests/determinism_mode.json",
        "smoke": source_root / "final_smoke/summary.json",
        "preflight": source_root / "preflight/runtime_preflight.json",
    }
    verify_file(
        source_files["mode"],
        str(continuation["source_determinism_mode_file_sha256"]),
    )
    verify_file(
        source_files["smoke"],
        str(continuation["source_smoke_summary_file_sha256"]),
    )
    verify_file(
        source_files["preflight"],
        str(continuation["source_runtime_preflight_file_sha256"]),
    )

    runtime_files = {
        "launcher": Path(str(settings["determinism"]["launcher_path"])),
        "formal_runner": Path(str(settings["determinism"]["runner_entrypoint"])),
        "runtime_module": Path(
            "rcmf/training/rcmf_appworld_testnormal_deterministic_13b.py"
        ),
    }
    for name, path in runtime_files.items():
        verify_file(path, str(continuation[f"{name}_sha256"]))

    process = write_process_identity(
        artifact_dir=args.artifact_dir,
        attempt_id=args.attempt_id,
        launcher_path=runtime_files["launcher"],
        entrypoint_path=Path(__file__),
        legacy_python=Path(str(cfg.raw["stage_c_9a"]["appworld"]["legacy_python"])),
        source_head=args.source_head,
    )
    base.main()

    old_cfg = load_config(str(continuation["source_config"])).raw
    scientific_checks = {
        key: settings[key] == old_cfg["stage_c_13a"][key]
        for key in SCIENTIFIC_CONFIG_KEYS
    }
    scientific_checks["appworld_runtime"] = (
        cfg.raw["stage_c_9a"]["appworld"] == old_cfg["stage_c_9a"]["appworld"]
    )
    if not all(scientific_checks.values()):
        raise ValueError(f"EXP-036C scientific config differs: {scientific_checks}")

    for name in ("package_manifest", "prompt_manifest", "test_normal_manifest", "leakage_audit"):
        old_path = source_root / "manifests" / f"{name}.json"
        new_path = args.artifact_dir / "manifests" / f"{name}.json"
        if sha256_file(old_path) != sha256_file(new_path):
            raise ValueError(f"EXP-036C frozen manifest differs: {name}")

    old_conditions = read_json(source_root / "manifests/condition_manifest.json")
    new_conditions = read_json(args.artifact_dir / "manifests/condition_manifest.json")
    if condition_manifest_for_run(old_conditions, RUN_UUID) != new_conditions:
        raise ValueError("EXP-036C condition manifest differs beyond run UUID")

    source_mode = read_json(source_files["mode"])
    if str(source_mode["manifest_sha256"]) != str(
        continuation["source_determinism_mode_logical_sha256"]
    ):
        raise ValueError("EXP-036B determinism logical SHA differs")
    write_once(args.artifact_dir / "manifests/determinism_mode.json", source_mode)
    mode = read_mode_manifest(args.artifact_dir)

    authorization_path = Path(str(continuation["authorization_record"]))
    authorization = read_json(authorization_path)
    if (
        str(authorization["run_uuid"]) != RUN_UUID
        or float(authorization["hard_cap_wall_hours"]) != 200.0
        or not bool(authorization["old_42_hour_cap_superseded"])
        or not bool(authorization["no_scientific_setting_changed"])
    ):
        raise ValueError("EXP-036C runtime authorization record differs")
    authorization_sha256 = sha256_file(authorization_path)
    write_once(
        args.artifact_dir / "manifests/runtime_authorization_200h.json",
        authorization,
    )

    smoke = read_json(source_files["smoke"])
    preflight = authorized_runtime_preflight(
        read_json(source_files["preflight"]),
        authorization_sha256=authorization_sha256,
    )
    if not source_smoke_passed(smoke):
        raise ValueError("EXP-036B complete-path smoke did not pass")
    if float(preflight["conservative_total_wall_hours"]) > 200.0:
        raise RuntimeError("EXP-036C conservative estimate exceeds 200 hours")
    write_once(args.artifact_dir / "preflight/runtime_preflight.json", preflight)

    reuse = {
        "format": "rcmf_exp036c_smoke_reuse_evidence_v1",
        "run_uuid": RUN_UUID,
        "source_run_uuid": SOURCE_RUN_UUID,
        "source_artifact_dir": str(source_root),
        "source_smoke_summary_sha256": sha256_file(source_files["smoke"]),
        "source_runtime_preflight_sha256": sha256_file(source_files["preflight"]),
        "source_determinism_mode_sha256": sha256_file(source_files["mode"]),
        "determinism_mode_logical_sha256": str(mode["manifest_sha256"]),
        "runtime_code_sha256": {
            name: sha256_file(path) for name, path in runtime_files.items()
        },
        "scientific_config_checks": scientific_checks,
        "complete_path_smoke_trajectory_count": int(smoke["trajectory_count"]),
        "all_repeat_comparisons_exact": source_smoke_passed(smoke),
        "new_complete_trajectory_smoke_required": False,
        "authorization_metadata_only_change": True,
        "passed": True,
    }
    reuse["manifest_sha256"] = canonical_sha256(reuse)
    write_once(args.artifact_dir / "preflight/exp036b_smoke_reuse.json", reuse)

    formal = {
        "format": "rcmf_appworld_testnormal_formal_manifest_13b_v1",
        "run_uuid": RUN_UUID,
        "source_head": args.source_head,
        "config_sha256": sha256_file(args.config),
        "condition_manifest_sha256": sha256_file(
            args.artifact_dir / "manifests/condition_manifest.json"
        ),
        "condition_manifest_logical_sha256": str(new_conditions["manifest_sha256"]),
        "task_ids": list(new_conditions["task_ids"]),
        "task_count": 168,
        "task_list_sha256": str(new_conditions["task_list_sha256"]),
        "conditions": CONDITIONS,
        "trajectory_count": 840,
        "determinism_mode": str(mode["mode"]),
        "determinism_mode_sha256": str(mode["manifest_sha256"]),
        "launcher_sha256": str(mode["launcher"]["sha256"]),
        "canonicalizer": dict(mode["canonicalizer"]),
        "observation_rendering_contract": str(mode["model_visible_observation_contract"]),
        "raw_observation_preserved": bool(mode["raw_observation_preserved"]),
        "evaluator_state_modified": bool(mode["evaluator_state_modified"]),
        "smoke_rows_reusable_as_formal": False,
        "formal_rows_generated": 0,
        "frozen_before_formal_generation": True,
    }
    formal["manifest_sha256"] = canonical_sha256(formal)
    write_once(args.artifact_dir / "manifests/formal_manifest.json", formal)
    validate_formal_manifest(
        artifact_dir=args.artifact_dir,
        condition_manifest=new_conditions,
        mode=mode,
    )

    fixture = atomic_resume_fixture(
        source_head=args.source_head,
        config_sha256=sha256_file(args.config),
        condition_manifest_sha256=str(new_conditions["manifest_sha256"]),
    )
    write_once(args.artifact_dir / "preflight/atomic_resume_fixture.json", fixture)
    if read_json(args.artifact_dir / "preflight/atomic_resume_fixture.json") != fixture:
        raise ValueError("EXP-036C atomic resume fixture differs")

    identity = {
        "format": "rcmf_exp036c_run_identity_v1",
        "run_uuid": RUN_UUID,
        "source_head": args.source_head,
        "starting_head": str(settings["starting_head"]),
        "working_branch": str(settings["working_branch"]),
        "runtime_authorization_sha256": authorization_sha256,
        "determinism_mode_sha256": str(mode["manifest_sha256"]),
        "formal_manifest_sha256": str(formal["manifest_sha256"]),
        "smoke_reuse_manifest_sha256": str(reuse["manifest_sha256"]),
        "preparation_process_identity_sha256": str(process["identity_sha256"]),
        "optimizer_steps": 0,
        "parameter_updates": 0,
        "scientific_configuration_changed": False,
    }
    identity["manifest_sha256"] = canonical_sha256(identity)
    write_once(args.artifact_dir / "manifests/exp036c_run_identity.json", identity)

    print(json.dumps({"authorization": authorization, "identity": identity, "runtime": preflight}, sort_keys=True))


if __name__ == "__main__":
    main()
