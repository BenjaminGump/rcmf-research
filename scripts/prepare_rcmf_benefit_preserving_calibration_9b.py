from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_benefit_preserving_calibration_9b import candidate_manifest
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file


PREPARATION_VERSION = "rcmf_benefit_preserving_preparation_9b_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/"
            "stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031b")
    return parser.parse_args()


def immutable_paths(settings: Mapping[str, Any]) -> dict[str, Path]:
    root = Path(str(settings["persistent_root"])) / "project"
    frozen = settings["immutable_exp031a"]
    return {
        "checkpoint": root / str(frozen["checkpoint"]),
        "deployment_field": root / str(frozen["deployment_field"]),
        "attempt_ledger": root / str(frozen["attempt_ledger"]),
        "audit_index": root / str(frozen["audit_index"]),
    }


def validate_immutable_inputs(settings: Mapping[str, Any]) -> dict[str, Any]:
    frozen = settings["immutable_exp031a"]
    paths = immutable_paths(settings)
    hashes = {}
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable EXP-031A input missing: {path}")
        hashes[name] = sha256_file(path)
        expected = str(frozen[f"{name}_sha256"])
        if hashes[name] != expected:
            raise RuntimeError(f"Immutable EXP-031A {name} hash differs")
    deployment = torch.load(
        paths["deployment_field"], map_location="cpu", weights_only=False
    )
    shapes = {
        "A": list(deployment["A"].shape),
        "B": list(deployment["B"].shape),
        "shuffled_A": list(deployment["shuffled_A"].shape),
        "shuffled_B": list(deployment["shuffled_B"].shape),
    }
    if int(deployment["memory_count"]) != int(frozen["memory_count"]):
        raise RuntimeError("Immutable EXP-031A deployment memory count differs")
    if shapes["A"] != list(frozen["field_A_shape"]) or shapes["B"] != list(
        frozen["field_B_shape"]
    ):
        raise RuntimeError("Immutable EXP-031A field shape differs")
    return {
        "hashes": hashes,
        "shapes": shapes,
        "memory_count": int(deployment["memory_count"]),
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9b"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="immutable_freeze_and_candidate_preregistration",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={},
        parent_attempt_id="none",
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(
            settings["runtime"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        immutable = validate_immutable_inputs(settings)
        candidates = candidate_manifest()
        atomic_write_json(
            args.artifact_dir / "candidate_preregistration.json", candidates
        )
        payload = {
            "format": PREPARATION_VERSION,
            "run_uuid": str(settings["run_uuid"]),
            "source_head": str(settings["source_head"]),
            "working_branch": str(settings["working_branch"]),
            "global_seed": int(settings["global_seed"]),
            "immutable": immutable,
            "candidate_library_sha256": candidates["library_sha256"],
            "no_training": True,
            "no_runtime_retrieval": True,
            "first37_outcomes_inspected": False,
        }
        atomic_write_json(args.artifact_dir / "run_manifest.json", payload)
        attempt.progress(
            phase="complete", immutable_hashes=immutable["hashes"]
        )
        print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
