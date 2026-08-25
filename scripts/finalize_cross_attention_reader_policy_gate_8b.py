from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.cross_attention_validation_8b import policy_gate_passes
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file


DECISION_FORMAT = "cross_attention_reader_policy_gate_decision_8b_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b_verified.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", type=Path, required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="none")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(json.loads(line)["attempt_id"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def finalize_policy_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    reports = list(summary.get("reports", []))
    if int(summary.get("checkpoint_count", -1)) != len(reports) or not reports:
        raise ValueError("Policy summary checkpoint accounting is invalid")
    audited: list[dict[str, Any]] = []
    for source in reports:
        values = {
            str(key): float(value)
            for key, value in source["positive_raw_teacher_policy_kl"].items()
        }
        required = {
            "X0_no_memory",
            "X1_correct_memory",
            "X2_transition_shuffle",
            "X3_state_shuffle",
        }
        if set(values) != required:
            raise ValueError("Policy summary control identities differ")
        correct = values["X1_correct_memory"]
        checks = {
            "correct_below_zero": correct < values["X0_no_memory"],
            "correct_below_transition_shuffle": (
                correct < values["X2_transition_shuffle"]
            ),
            "correct_below_state_shuffle": correct < values["X3_state_shuffle"],
        }
        gate_passed = policy_gate_passes(
            {"positive_raw_teacher_policy_kl": values}
        )
        if gate_passed != all(checks.values()):
            raise AssertionError("Finalizer differs from preregistered policy gate")
        audited.append(
            {
                "epoch": int(source["epoch"]),
                "checkpoint_sha256": str(source["checkpoint_sha256"]),
                "positive_raw_teacher_policy_kl": values,
                "checks": checks,
                "policy_gate_passed": gate_passed,
            }
        )
    eligible_epochs = [row["epoch"] for row in audited if row["policy_gate_passed"]]
    best = min(
        audited,
        key=lambda row: (
            row["positive_raw_teacher_policy_kl"]["X1_correct_memory"],
            row["epoch"],
        ),
    )
    failed = not eligible_epochs
    return {
        "format": DECISION_FORMAT,
        "global_seed": GLOBAL_SEED,
        "checkpoint_count": len(audited),
        "audited_checkpoints": audited,
        "eligible_policy_checkpoint_epochs": eligible_epochs,
        "best_diagnostic_epoch": int(best["epoch"]),
        "best_diagnostic_checkpoint_sha256": best["checkpoint_sha256"],
        "best_diagnostic_positive_raw_teacher_policy_kl": best[
            "positive_raw_teacher_policy_kl"
        ],
        "policy_gate_passed": not failed,
        "heldout_live_status": (
            "not_run_blocked_by_policy_gate"
            if failed
            else "required_before_reader_selection"
        ),
        "heldout_live_condition_count": 0,
        "reader_classification": "CLEAR_FAILURE" if failed else "UNRESOLVED",
        "decision_branch": (
            "published_cross_attention_reader_failed_on_appworld"
            if failed
            else "reader_policy_gate_passed_live_validation_required"
        ),
        "reversible_field_authorized": False,
        "field_condition_count": 0,
        "first37_condition_count": 0,
        "test_normal_outcomes_used": False,
        "scientific_parameters_changed": False,
        "stop_reason": (
            "no checkpoint satisfies the mandatory heldout-train policy gate; "
            "live behavior cannot make an ineligible checkpoint selectable"
            if failed
            else "policy gate passed; heldout live validation remains required"
        ),
    }


def _report(decision: Mapping[str, Any]) -> str:
    rows = []
    for row in decision["audited_checkpoints"]:
        values = row["positive_raw_teacher_policy_kl"]
        rows.append(
            "| {epoch} | {x0:.6f} | {x1:.6f} | {x2:.6f} | {x3:.6f} | {passed} |".format(
                epoch=row["epoch"],
                x0=values["X0_no_memory"],
                x1=values["X1_correct_memory"],
                x2=values["X2_transition_shuffle"],
                x3=values["X3_state_shuffle"],
                passed=str(row["policy_gate_passed"]).lower(),
            )
        )
    return "\n".join(
        [
            "# EXP-030A Reader Policy Gate",
            "",
            "| Epoch | X0 zero | X1 correct | X2 transition shuffle | X3 state shuffle | Gate |",
            "|---:|---:|---:|---:|---:|:---:|",
            *rows,
            "",
            f"- Best diagnostic epoch: `{decision['best_diagnostic_epoch']}`",
            f"- Heldout live status: `{decision['heldout_live_status']}`",
            f"- Decision branch: `{decision['decision_branch']}`",
            f"- Reversible field authorized: `{str(decision['reversible_field_authorized']).lower()}`",
            "- Test-normal outcomes used: `false`",
            "- Cross-attention reader is borrowed prior art; this result is not an RCMF field validation.",
            "",
        ]
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _existing_attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    summary_path = args.artifact_dir / "reader/phase2/policy_evaluation_summary.json"
    if args.resume_checkpoint.resolve() != summary_path.resolve():
        raise ValueError("Finalizer must resume from the immutable policy summary")
    required = {
        "policy_evaluation": summary_path,
        "phase2_training": args.artifact_dir / "reader/phase2/training_summary.json",
        "phase1_selection": args.artifact_dir / "reader/phase1/checkpoint_selection.json",
        "implementation": args.artifact_dir / "reader/implementation_validation.json",
        "preflight": args.artifact_dir / "runtime_preflight.json",
    }
    missing = {name: path for name, path in required.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-030A finalizer inputs: {missing}")
    hashes = {name: sha256_file(path) for name, path in required.items()}
    decision = finalize_policy_summary(_json(summary_path))
    decision.update(
        {
            "run_uuid": str(settings["run_uuid"]),
            "source_heads": {
                "local": args.local_head,
                "github": args.github_head,
                "lambda": args.lambda_head,
            },
            "input_sha256": hashes,
        }
    )
    output = args.artifact_dir / "reader/phase2/policy_gate_decision.json"
    report = args.artifact_dir / "reader/phase2/policy_gate_report.md"
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="reader_policy_gate_finalization",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(args.resume_checkpoint),
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        atomic_write_json(output, decision)
        atomic_write_text(report, _report(decision))
        atomic_write_json(
            args.artifact_dir / "policy_gate_orchestration_state.json",
            {
                "format": "cross_attention_field_policy_gate_orchestration_8b_v1",
                "status": (
                    "reader_failed_stop_before_field"
                    if not decision["policy_gate_passed"]
                    else "heldout_live_required"
                ),
                "decision_branch": decision["decision_branch"],
                "selection_path": str(output),
                "reversible_field_authorized": False,
            },
        )
        attempt.progress(
            status="reader_policy_gate_finalized",
            latest_validated_checkpoint=str(output),
            decision_branch=decision["decision_branch"],
            reversible_field_authorized=False,
        )
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
