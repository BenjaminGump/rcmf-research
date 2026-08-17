from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.clean_cache_execution_7b import (
    rebuild_representations,
    seed_pair_response,
    seed_raw_teacher,
    seed_stage_c1,
    seed_transition_teacher,
    validate_clean_cache_rebuild,
    validate_transition_preflight,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file


PHASES = (
    "seed_raw",
    "transition_preflight",
    "representations",
    "raw_teacher",
    "labels",
    "stage_c1",
    "pair_5d",
    "transition_teacher",
    "validate",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(command: list[str]) -> None:
    print("running:", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one resumable EXP-025B clean-cache phase.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025b")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7b"]
    rebuild = settings["cache_rebuild"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    output_root = Path(str(rebuild["output_root"]))
    output_root.mkdir(parents=True, exist_ok=True)
    clean_data = Path(str(settings["reconciled_corpus_dir"]))
    old_data = Path(str(rebuild["historical_source_data"]))
    old = {key: Path(str(value)) for key, value in rebuild["historical_caches"].items()}
    affected_path = output_root / "preflight" / "affected_cache_keys.json"
    replay_path = args.artifact_dir / "replay_validated_corpus_manifest.json"
    if not affected_path.exists() or not replay_path.exists():
        raise FileNotFoundError("Exact preflight and replay-validated manifest are required")
    affected = _json(affected_path)
    replay = _json(replay_path)
    lineage = str(replay["structural_corpus_lineage_sha256"])
    input_hashes = {
        "replay_validated_manifest": sha256_file(replay_path),
        "affected_cache_keys": sha256_file(affected_path),
        "clean_decisions": sha256_file(clean_data / "decision_examples.jsonl"),
        "clean_memories": sha256_file(clean_data / "memory_records.jsonl"),
        "clean_transitions": sha256_file(clean_data / "transition_manifest.jsonl"),
    }
    phase_dir = output_root / "phase_reports"
    report_path = phase_dir / f"{args.phase}.json"
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"incremental_clean_cache_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=input_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        report: dict[str, Any]
        if args.phase == "seed_raw":
            report = seed_raw_teacher(
                preflight_manifest=affected,
                old_path=old["raw_text_teacher"],
                output_path=output_root / "raw_text_teacher" / "teacher_cache_full_rows.jsonl",
            )
        elif args.phase == "transition_preflight":
            clean_preflight = output_root / "transition_preflight"
            _run(
                [
                    sys.executable,
                    "scripts/prepare_transition_memory_6a.py",
                    "--config",
                    "configs/benchmark/stage_c_transition_memory_6a.yaml",
                    "--data",
                    str(clean_data),
                    "--split-manifest",
                    str(old["student_split_manifest"]),
                    "--decoder-manifest",
                    str(old["decoder_split_manifest"]),
                    "--output-dir",
                    str(clean_preflight),
                ]
            )
            report = validate_transition_preflight(
                old_dir=old["transition_teacher"].parent,
                clean_dir=clean_preflight,
            )
        elif args.phase == "representations":
            backend = build_backend(cfg, load_model=True)
            backend.model.eval()
            for parameter in backend.model.parameters():
                parameter.requires_grad_(False)
            report = rebuild_representations(
                backend=backend,
                data_dir=clean_data,
                old_data_dir=old_data,
                clean_transition_preflight_dir=output_root / "transition_preflight",
                old_transition_dir=old["transition_teacher"].parent,
                old_state_path=old["state_representations"],
                old_memory_path=old["memory_representations"],
                old_transition_path=old["transition_representations"],
                output_dir=output_root / "representations",
                corpus_lineage_sha256=lineage,
                transition_mapping=affected["transition_changes"]["mapping"],
                attempt=attempt,
            )
        elif args.phase == "raw_teacher":
            seed = seed_raw_teacher(
                preflight_manifest=affected,
                old_path=old["raw_text_teacher"],
                output_path=output_root / "raw_text_teacher" / "teacher_cache_full_rows.jsonl",
            )
            _run(
                [
                    sys.executable,
                    "scripts/run_raw_text_teacher_full_cache.py",
                    "--config",
                    str(args.config),
                    "--data",
                    str(clean_data),
                    "--output-dir",
                    str(output_root / "raw_text_teacher"),
                    "--pilot-dir",
                    str(old["raw_text_pilot"]),
                    "--audit3b-dir",
                    str(old["raw_text_audit3b"]),
                    "--disable-external-cache-reuse",
                    "--corpus-lineage-sha256",
                    lineage,
                ]
            )
            report = {
                "format": "identity_reconciled_raw_teacher_phase_7b_v1",
                "seed": seed,
                "summary": _json(output_root / "raw_text_teacher" / "summary.json"),
            }
        elif args.phase == "labels":
            labels_dir = output_root / "student_labels"
            _run(
                [
                    sys.executable,
                    "scripts/compile_student_labels.py",
                    "--data",
                    str(clean_data),
                    "--teacher-cache-dir",
                    str(output_root / "raw_text_teacher"),
                    "--output-dir",
                    str(labels_dir),
                ]
            )
            report = {
                "format": "identity_reconciled_student_label_phase_7b_v1",
                "summary": _json(labels_dir / "summary.json"),
            }
        elif args.phase == "stage_c1":
            output_dir = output_root / "stage_c1_response"
            seed = seed_stage_c1(
                data_dir=clean_data,
                labels_dir=output_root / "student_labels",
                teacher_cache_dir=output_root / "raw_text_teacher",
                old_path=old["stage_c1_response"],
                output_dir=output_dir,
            )
            _run(
                [
                    sys.executable,
                    "scripts/build_stage_c1_response_cache.py",
                    "--config",
                    str(args.config),
                    "--data",
                    str(clean_data),
                    "--teacher-cache-dir",
                    str(output_root / "raw_text_teacher"),
                    "--labels-dir",
                    str(output_root / "student_labels"),
                    "--output-dir",
                    str(output_dir),
                    "--corpus-lineage-sha256",
                    lineage,
                ]
            )
            report = {
                "format": "identity_reconciled_stage_c1_phase_7b_v1",
                "seed": seed,
                "summary": _json(output_dir / "summary.json"),
            }
        elif args.phase == "pair_5d":
            output_dir = output_root / "pair_response_5d"
            seed = seed_pair_response(
                labels_dir=output_root / "student_labels",
                old_path=old["pair_response_5d"],
                output_dir=output_dir,
            )
            _run(
                [
                    sys.executable,
                    "scripts/run_stage_c_pair_grounding_5d.py",
                    "--config",
                    str(args.config),
                    "--data",
                    str(clean_data),
                    "--teacher-cache-dir",
                    str(output_root / "raw_text_teacher"),
                    "--labels-dir",
                    str(output_root / "student_labels"),
                    "--representation-cache-dir",
                    str(output_root / "representations"),
                    "--stage-c1-response-cache-dir",
                    str(output_root / "stage_c1_response"),
                    "--pair-cache-dir",
                    str(output_dir),
                    "--output-dir",
                    str(output_root / "pair_response_5d_run"),
                    "--cache-only",
                    "--corpus-lineage-sha256",
                    lineage,
                ]
            )
            report = {
                "format": "identity_reconciled_pair_response_phase_7b_v1",
                "seed": seed,
                "summary": _json(output_dir / "pair_response_cache_summary.json"),
            }
        elif args.phase == "transition_teacher":
            output_dir = output_root / "transition_teacher"
            seed = seed_transition_teacher(
                old_dir=old["transition_teacher"].parent,
                clean_preflight_dir=output_root / "transition_preflight",
                output_dir=output_dir,
            )
            _run(
                [
                    sys.executable,
                    "scripts/run_transition_teacher_6a.py",
                    "--config",
                    "configs/benchmark/stage_c_transition_memory_6a.yaml",
                    "--data",
                    str(clean_data),
                    "--preflight-dir",
                    str(output_root / "transition_preflight"),
                    "--parent-teacher-cache",
                    str(output_root / "raw_text_teacher" / "teacher_cache_full_rows.jsonl"),
                    "--output-dir",
                    str(output_dir),
                    "--teacher-cache-only",
                    "--corpus-lineage-sha256",
                    lineage,
                ]
            )
            report = {
                "format": "identity_reconciled_transition_teacher_phase_7b_v1",
                "seed": seed,
                "summary": _json(output_dir / "teacher_summary.json"),
            }
        elif args.phase == "validate":
            report = validate_clean_cache_rebuild(
                output_root=output_root,
                old_paths=old,
                affected_manifest=affected,
                corpus_lineage_sha256=lineage,
                expected_counts={
                    str(key): int(value) for key, value in rebuild["expected_cache_rows"].items()
                },
            )
        else:
            raise AssertionError(args.phase)
        phase_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(report_path, report)
        attempt.progress(status="completed", latest_validated_checkpoint=str(report_path))
        print(json.dumps(report, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
