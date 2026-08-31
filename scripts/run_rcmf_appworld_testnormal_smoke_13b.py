"""Run and finalize the separate-process EXP-036B complete-path smoke."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if os.environ.get("PYTHONHASHSEED") != "25101":
    raise RuntimeError("Launch EXP-036B smoke through the 13b hash-seed launcher")

import _bootstrap  # noqa: E402,F401

from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (  # noqa: E402
    SMOKE_RESULT_FORMAT,
    assert_hash_seed_process,
    augment_task_row,
    build_runtime_preflight,
    compare_complete_smoke_rows,
    freeze_formal_manifest,
    read_mode_manifest,
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
    parser.add_argument("--condition", choices=base.CONDITIONS)
    parser.add_argument("--task-index", type=int, choices=(0, 1))
    parser.add_argument("--process-role", choices=("primary", "repeat"))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--manifest-source-head", required=True)
    return parser.parse_args()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def row_path(
    artifact_dir: Path, process_role: str, condition: str, task_id: str
) -> Path:
    return (
        artifact_dir
        / "final_smoke"
        / process_role
        / "smoke_v2"
        / condition
        / "task_results"
        / f"{task_id}.json"
    )


def run_unit(
    args: argparse.Namespace, cfg: object, process: dict[str, object]
) -> dict[str, object]:
    if args.condition is None or args.task_index is None or args.process_role is None:
        raise ValueError("Smoke run requires condition, task-index, and process-role")
    if args.process_role == "repeat" and args.task_index != 0:
        raise ValueError("EXP-036B repeats only the first ordered smoke task")
    settings = cfg.raw["stage_c_13a"]
    manifest = json.loads(
        (args.artifact_dir / "manifests" / "condition_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    task_id = str(manifest["task_ids"][args.task_index])
    mode = read_mode_manifest(args.artifact_dir)
    output = row_path(args.artifact_dir, args.process_role, args.condition, task_id)
    backend = base.load_backend(cfg)
    runtime = base.FinalTestRuntime(
        settings_9a=cfg.raw["stage_c_9a"],
        settings=settings,
        backend=backend,
        package_manifest=json.loads(
            (args.artifact_dir / "manifests" / "package_manifest.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    paths = base.run_paths(args.artifact_dir, settings)
    paths["root"] = args.artifact_dir / "final_smoke" / args.process_role
    base.TASK_RESULT_FORMAT = SMOKE_RESULT_FORMAT
    row, reused = base.run_one(
        task_id=task_id,
        condition=args.condition,
        smoke=True,
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
        content = {key: value for key, value in row.items() if key != "result_sha256"}
        checks = {
            "format": row.get("format") == SMOKE_RESULT_FORMAT,
            "mode": row.get("determinism", {}).get("mode") == "hash_seed_only",
            "mode_sha": row.get("determinism", {}).get("mode_manifest_sha256")
            == mode["manifest_sha256"],
            "result_sha": row.get("result_sha256") == canonical_sha256(content),
            "process_role": row.get("smoke_process_role") == args.process_role,
            "task_index": row.get("smoke_task_index") == args.task_index,
        }
        if not all(checks.values()):
            raise ValueError(f"EXP-036B smoke resume identity differs: {checks}")
    else:
        row["final_exp036b_smoke"] = True
        row["smoke_process_role"] = args.process_role
        row["smoke_task_index"] = args.task_index
        row = augment_task_row(
            row=row,
            backend=backend,
            process_identity=process,
            mode=mode,
            result_format=SMOKE_RESULT_FORMAT,
        )
        atomic_write_json(output, row)
    return {
        "task_id": task_id,
        "task_index": args.task_index,
        "condition": args.condition,
        "process_role": args.process_role,
        "row_path": str(output),
        "row_sha256": sha256_file(output),
        "success": bool(row["success"]),
        "step_count": int(row["step_count"]),
        "wall_seconds": float(row["wall_seconds"]),
        "reused": reused,
    }


def finalize(args: argparse.Namespace, cfg: object) -> dict[str, object]:
    settings = cfg.raw["stage_c_13a"]
    manifest = json.loads(
        (args.artifact_dir / "manifests" / "condition_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    mode = read_mode_manifest(args.artifact_dir)
    task_ids = [str(value) for value in manifest["task_ids"][:2]]
    primary = {
        (task_id, condition): json.loads(
            row_path(args.artifact_dir, "primary", condition, task_id).read_text(
                encoding="utf-8"
            )
        )
        for task_id in task_ids
        for condition in base.CONDITIONS
    }
    repeat = {
        condition: json.loads(
            row_path(
                args.artifact_dir, "repeat", condition, task_ids[0]
            ).read_text(encoding="utf-8")
        )
        for condition in base.CONDITIONS
    }
    rows = [*primary.values(), *repeat.values()]
    if len(rows) != 15 or not base._all_complete(rows):
        raise RuntimeError("EXP-036B final smoke is incomplete or infrastructure-invalid")
    deterministic = {
        condition: compare_complete_smoke_rows(
            primary[(task_ids[0], condition)], repeat[condition]
        )
        for condition in base.CONDITIONS
    }
    if not all(result["passed"] for result in deterministic.values()):
        raise RuntimeError(f"EXP-036B final smoke determinism failed: {deterministic}")
    preflight = build_runtime_preflight(
        artifact_dir=args.artifact_dir,
        primary_rows=list(primary.values()),
        deterministic=deterministic,
        settings=settings,
        mode=mode,
        smoke_task_ids=task_ids,
    )
    atomic_write_json(
        args.artifact_dir / "preflight" / "runtime_preflight.json", preflight
    )
    formal_manifest = freeze_formal_manifest(
        artifact_dir=args.artifact_dir,
        condition_manifest=manifest,
        mode=mode,
        source_head=args.source_head,
        config_sha256=sha256_file(args.config),
    )
    summary = {
        "format": "rcmf_appworld_testnormal_final_smoke_13b_v1",
        "task_ids": task_ids,
        "trajectory_count": 15,
        "process_count": 15,
        "fresh_process_per_trajectory": True,
        "determinism": deterministic,
        "deterministic": True,
        "determinism_mode": mode["mode"],
        "determinism_mode_sha256": mode["manifest_sha256"],
        "runtime_preflight_sha256": preflight["report_sha256"],
        "formal_manifest_sha256": formal_manifest["manifest_sha256"],
        "passed": True,
    }
    summary["summary_sha256"] = canonical_sha256(summary)
    atomic_write_json(args.artifact_dir / "final_smoke" / "summary.json", summary)
    if not preflight["automatic_launch_allowed"]:
        raise RuntimeError("EXP-036B conservative complete estimate exceeds 42 hours")
    return {
        "summary": summary,
        "runtime_preflight": preflight,
        "formal_manifest": formal_manifest,
    }


def main() -> None:
    assert_hash_seed_process()
    args = parse_args()
    if git_head() != args.source_head:
        raise ValueError("EXP-036B smoke source HEAD differs from checkout")
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    run_manifest = json.loads(
        (args.artifact_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    if str(run_manifest["source_head"]) != args.manifest_source_head:
        raise ValueError("EXP-036B smoke manifest source HEAD differs")
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
        phase="exp036b_final_smoke_" + args.phase,
        command=list(sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "condition_manifest": sha256_file(
                args.artifact_dir / "manifests" / "condition_manifest.json"
            ),
            "determinism_mode": sha256_file(
                args.artifact_dir / "manifests" / "determinism_mode.json"
            ),
        },
        parent_attempt_id="none",
        resume_checkpoint="atomic_separate_process_smoke_rows",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        result = run_unit(args, cfg, process) if args.phase == "run" else finalize(args, cfg)
        attempt.progress(
            status="exp036b_final_smoke_complete",
            completed_units=1,
            total_units=1,
            latest_validated_checkpoint=str(
                args.artifact_dir / "final_smoke" / "summary.json"
                if args.phase == "finalize"
                else result["row_path"]
            ),
            result=result,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
