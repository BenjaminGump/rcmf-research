from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
)
from scripts.run_memory_use_target_models_6e import (
    _gate_and_decision,
    _load_json,
    _load_rows,
    _locked_transition_only_baselines,
    _report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair the EXP-021 locked transition comparator record only"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_memory_use_target_6e.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp021-record-repair")
    return parser.parse_args()


def _preserve_once(path: Path, payload: dict[str, Any] | str) -> None:
    if path.exists():
        return
    if isinstance(payload, str):
        atomic_write_text(path, payload)
    else:
        atomic_write_json(path, payload)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6e"]
    root = args.artifact_dir
    summary_path = root / "model_audit_summary.json"
    report_path = root / "final_target_audit_report.md"
    summary = _load_json(summary_path)
    backup_summary = root / "model_audit_summary_pre_locked_transition_repair.json"
    backup_report = root / "final_target_audit_report_pre_locked_transition_repair.md"
    if backup_summary.exists() or backup_report.exists():
        if not backup_summary.exists() or not backup_report.exists():
            raise RuntimeError("The EXP-021 record-repair backup pair is incomplete")
        old_summary = _load_json(backup_summary)
        old_report = backup_report.read_text(encoding="utf-8")
    else:
        old_summary = copy.deepcopy(summary)
        old_report = report_path.read_text(encoding="utf-8")
        _preserve_once(backup_summary, old_summary)
        _preserve_once(backup_report, old_report)
    old_summary_sha256 = sha256_file(backup_summary)
    old_report_sha256 = sha256_file(backup_report)

    exp020 = Path(settings["exp020_artifact"])
    locked_level = _load_json(exp020 / "model_summary.json")["levels"]["LC37"]
    locked_transition = _locked_transition_only_baselines(locked_level)
    selected_target = str(
        summary["cv_selection"]["selected_primary_revised_target"]["target"]
    )
    run_manifest = _load_json(root / "run_manifest.json")
    with AttemptLedger(
        root,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="locked_transition_baseline_record_repair",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        summary["final_results"]["baselines"]["transition_only"] = (
            locked_transition
        )
        rows_d = [
            row
            for row in _load_rows(root / "candidate_target_rows.jsonl")
            if str(row["cell"]) == "D"
        ]
        locked_rows_d = _load_rows(
            Path(locked_transition["D"]["locked_source"]["rows_path"])
        )
        gate = _gate_and_decision(
            selected_target=selected_target,
            final=summary["final_results"],
            rows_d=rows_d,
            locked_transition_rows_d=locked_rows_d,
            settings=settings,
            serialization_passed=bool(summary["serialization_gate_passed"]),
        )
        summary["scientific_gate"] = gate
        summary["record_repair"] = {
            "format": "locked_transition_baseline_record_repair_6e_v1",
            "reason": "EXP-021 must use the immutable EXP-020 LC37 transition-only comparator; the prior record recomputed a different train-mean baseline and read the wrong NDCG bootstrap key.",
            "scientific_parameter_changed": False,
            "model_or_checkpoint_changed": False,
            "training_rerun": False,
            "source_commit": args.lambda_head,
            "superseded_decision_branch": old_summary["scientific_gate"][
                "decision_branch"
            ],
            "corrected_decision_branch": gate["decision_branch"],
            "prior_summary_sha256": old_summary_sha256,
            "prior_report_sha256": old_report_sha256,
            "backup_summary": str(backup_summary),
            "backup_summary_sha256": sha256_file(backup_summary),
            "backup_report": str(backup_report),
            "backup_report_sha256": sha256_file(backup_report),
            "locked_transition_only": {
                cell: value["locked_source"]
                for cell, value in locked_transition.items()
            },
        }
        atomic_write_json(summary_path, summary)
        atomic_write_text(report_path, _report(summary))
        repair = {
            **summary["record_repair"],
            "format": "memory_use_target_scientific_gate_repair_6e_v1",
            "selected_target": selected_target,
            "scientific_gate": gate,
            "corrected_summary_sha256": sha256_file(summary_path),
            "corrected_report_sha256": sha256_file(report_path),
        }
        repair_path = root / "scientific_gate_repair.json"
        atomic_write_json(repair_path, repair)
        attempt.progress(
            status="completed",
            decision_branch=gate["decision_branch"],
            latest_validated_checkpoint=str(repair_path),
        )
        print(json.dumps(repair, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
