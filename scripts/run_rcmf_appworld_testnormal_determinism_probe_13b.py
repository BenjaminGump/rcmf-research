"""Run and finalize the fresh-process EXP-036B Stage 1 hash-seed probe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if os.environ.get("PYTHONHASHSEED") != "25101":
    raise RuntimeError("Launch the EXP-036B probe through the 13b hash-seed launcher")

import _bootstrap  # noqa: E402,F401

from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (  # noqa: E402
    PROBE_RESULT_FORMAT,
    assert_hash_seed_process,
    augment_task_row,
    freeze_hash_seed_mode,
    write_process_identity,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256  # noqa: E402
from rcmf.training.state_conditioned_transition_6b import AttemptLedger  # noqa: E402
from rcmf.utils.serialization import atomic_write_json, sha256_file  # noqa: E402
import scripts.run_rcmf_appworld_testnormal_final_13a as base  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_appworld_testnormal_deterministic_13b.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("run", "finalize"), required=True)
    parser.add_argument("--condition", choices=("B0", "FULL1D-S"))
    parser.add_argument("--process-label", choices=("A", "B"))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    return parser.parse_args()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _candidate_mode(artifact_dir: Path) -> dict[str, object]:
    policy = json.loads(
        (artifact_dir / "manifests/determinism_policy.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "manifest_sha256": policy["manifest_sha256"],
        "artifact_sha256": sha256_file(
            artifact_dir / "manifests/determinism_policy.json"
        ),
    }


def run_probe(args: argparse.Namespace, cfg: object, process: dict[str, object]) -> dict[str, object]:
    if args.condition is None or args.process_label is None:
        raise ValueError("Probe run requires --condition and --process-label")
    settings = cfg.raw["stage_c_13a"]
    manifest = json.loads(
        (args.artifact_dir / "manifests/condition_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    task_id = str(manifest["task_ids"][0])
    backend = base.load_backend(cfg)
    runtime = base.FinalTestRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings=settings,
        backend=backend,
        package_manifest=json.loads(
            (args.artifact_dir / "manifests/package_manifest.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    paths = base.run_paths(args.artifact_dir, settings)
    paths["root"] = args.artifact_dir / "determinism_probe" / f"process_{args.process_label}"
    base.TASK_RESULT_FORMAT = PROBE_RESULT_FORMAT
    row, reused = base.run_one(
        task_id=task_id,
        condition=args.condition,
        smoke=False,
        settings_9a=cfg.raw["stage_c_9a"],
        backend=backend,
        runtime=runtime,
        paths=paths,
        manifest=manifest,
        config_sha256=sha256_file(args.config),
        attempt_id=args.attempt_id,
        execution_source_head=args.source_head,
    )
    if reused:
        raise RuntimeError("Stage 1 probe rows must be generated in fresh processes")
    row["non_scientific_determinism_probe"] = True
    row["probe_process_label"] = args.process_label
    row = augment_task_row(
        row=row,
        backend=backend,
        process_identity=process,
        mode=_candidate_mode(args.artifact_dir),
        result_format=PROBE_RESULT_FORMAT,
    )
    output = (
        paths["root"]
        / "conditions"
        / args.condition
        / "task_results"
        / f"{task_id}.json"
    )
    atomic_write_json(output, row)
    return {
        "task_id": task_id,
        "condition": args.condition,
        "process_label": args.process_label,
        "row_path": str(output),
        "row_sha256": sha256_file(output),
        "success": bool(row["success"]),
        "step_count": int(row["step_count"]),
    }


def main() -> None:
    assert_hash_seed_process()
    args = parse_args()
    if git_head() != args.source_head:
        raise ValueError("EXP-036B probe source HEAD differs")
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    deterministic = settings["determinism"]
    process = write_process_identity(
        artifact_dir=args.artifact_dir,
        attempt_id=args.attempt_id,
        launcher_path=Path(str(deterministic["launcher_path"])),
        entrypoint_path=Path(__file__),
        legacy_python=Path(str(cfg.raw["stage_c_9a"]["appworld"]["legacy_python"])),
        source_head=args.source_head,
    )
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="exp036b_hash_seed_probe",
        command=list(sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "determinism_policy": sha256_file(
                args.artifact_dir / "manifests/determinism_policy.json"
            ),
            "condition_manifest": sha256_file(
                args.artifact_dir / "manifests/condition_manifest.json"
            ),
        },
        parent_attempt_id="none",
        resume_checkpoint="fresh_process_probe_rows",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "run":
            result = run_probe(args, cfg, process)
        else:
            task_manifest = json.loads(
                (args.artifact_dir / "manifests/test_normal_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            result = freeze_hash_seed_mode(
                artifact_dir=args.artifact_dir,
                task_id=str(task_manifest["task_ids"][0]),
                launcher_path=Path(str(deterministic["launcher_path"])),
                root_cause_path=Path(str(deterministic["root_cause_path"])),
            )
        attempt.progress(
            status="exp036b_hash_seed_probe_complete",
            completed_units=1,
            total_units=1,
            latest_validated_checkpoint=str(
                args.artifact_dir / "manifests/determinism_mode.json"
                if args.phase == "finalize"
                else result["row_path"]
            ),
            result=result,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

