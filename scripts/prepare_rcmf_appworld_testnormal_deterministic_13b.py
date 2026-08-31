"""Prepare immutable EXP-036B manifests around the frozen EXP-036A science."""

from __future__ import annotations

import os
from pathlib import Path
import sys

if os.environ.get("PYTHONHASHSEED") != "25101":
    raise RuntimeError("Launch EXP-036B preparation through the 13b hash-seed launcher")

import _bootstrap  # noqa: E402,F401

from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (  # noqa: E402
    REQUIRED_PYTHON_HASH_SEED,
    assert_hash_seed_process,
    write_process_identity,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256  # noqa: E402
from rcmf.utils.serialization import atomic_write_json, sha256_file  # noqa: E402
import scripts.prepare_rcmf_appworld_testnormal_final_13a as base  # noqa: E402


def _write_once(path: Path, value: dict[str, object]) -> None:
    if path.exists():
        import json

        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError(f"Existing immutable EXP-036B manifest differs: {path}")
        return
    atomic_write_json(path, value)


def main() -> None:
    assert_hash_seed_process()
    args = base.parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    deterministic = settings["determinism"]
    root_cause = Path(str(deterministic["root_cause_path"]))
    if sha256_file(root_cause) != str(deterministic["root_cause_sha256"]):
        raise ValueError("EXP-036B root-cause evidence SHA differs")
    process = write_process_identity(
        artifact_dir=args.artifact_dir,
        attempt_id=args.attempt_id,
        launcher_path=Path(str(deterministic["launcher_path"])),
        entrypoint_path=Path(__file__),
        legacy_python=Path(str(cfg.raw["stage_c_9a"]["appworld"]["legacy_python"])),
        source_head=args.source_head,
    )
    policy = {
        "format": "rcmf_exp036b_determinism_policy_v1",
        "run_uuid": str(settings["run_uuid"]),
        "required_python_hash_seed": REQUIRED_PYTHON_HASH_SEED,
        "stage_order": list(deterministic["stage_order"]),
        "stage1_candidate": "hash_seed_only",
        "canonicalizer_enabled_before_stage1": False,
        "root_cause_path": str(root_cause),
        "root_cause_sha256": sha256_file(root_cause),
        "launcher_path": str(deterministic["launcher_path"]),
        "launcher_sha256": sha256_file(Path(str(deterministic["launcher_path"]))),
        "raw_observation_preserved": True,
        "evaluator_state_unchanged": True,
        "selection_uses_task_success": False,
    }
    policy["manifest_sha256"] = canonical_sha256(policy)
    _write_once(args.artifact_dir / "manifests/determinism_policy.json", policy)

    base.main()

    run_manifest = args.artifact_dir / "run_manifest.json"
    supplement = {
        "format": "rcmf_exp036b_run_identity_v1",
        "run_uuid": str(settings["run_uuid"]),
        "source_head": args.source_head,
        "starting_head": str(settings["starting_head"]),
        "working_branch": str(settings["working_branch"]),
        "base_scientific_manifest_path": str(run_manifest),
        "base_scientific_manifest_sha256": sha256_file(run_manifest),
        "determinism_policy_path": str(
            args.artifact_dir / "manifests/determinism_policy.json"
        ),
        "determinism_policy_sha256": sha256_file(
            args.artifact_dir / "manifests/determinism_policy.json"
        ),
        "preparation_process_identity_sha256": process["identity_sha256"],
        "optimizer_steps": 0,
        "parameter_updates": 0,
        "canonicalizer_enabled": False,
    }
    supplement["manifest_sha256"] = canonical_sha256(supplement)
    _write_once(args.artifact_dir / "manifests/exp036b_run_identity.json", supplement)


if __name__ == "__main__":
    main()

