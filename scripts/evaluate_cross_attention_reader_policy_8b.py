from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_cross_attention_reader_8b import (
    _evaluate_policy_checkpoint,
    _json,
    _load_slot_bank,
    _load_source,
    _paths,
    _policy_control,
    _reader,
    _require,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp030a_reader")
    return parser.parse_args()


def _cross_prompt_policy_row(
    prompt_row: Mapping[str, Any], teacher: Mapping[str, Any], *, pair_id: str
) -> dict[str, Any]:
    prompt_length = int(prompt_row["prompt_len"])
    prefix = [int(value) for value in prompt_row["input_ids"][:prompt_length]]
    target = [int(value) for value in teacher["generated_token_ids"]]
    return {
        "pair_id": pair_id,
        "input_ids": prefix + target,
        "labels": [-100] * len(prefix) + target,
        "pad_token_id": int(prompt_row["pad_token_id"]),
        "last_user_token_indices": [
            int(value) for value in prompt_row["last_user_token_indices"]
        ],
        "target_len": len(target),
        "prompt_len": len(prefix),
        "response_cache": {},
        "student_prompt_contains_raw_memory": False,
    }


def _positive_raw_policy(
    *, backend: Any, reader: Any, source: Mapping[str, Any], slots: Mapping[str, torch.Tensor]
) -> dict[str, float]:
    rows: dict[str, list[float]] = {
        name: []
        for name in (
            "X0_no_memory",
            "X1_correct_memory",
            "X2_transition_shuffle",
            "X3_state_shuffle",
        )
    }
    for state_id, outcome in sorted(source["outcomes"].items()):
        if str(outcome["model_split"]) != "heldout_train_validation":
            continue
        if str(outcome["label"]) != "POSITIVE":
            continue
        teacher = source["teacher"]["teacher_rows"][state_id]["raw"]
        policy = source["teacher"]["policy_rows"][state_id]["raw"]
        mismatch = source["mismatches"][state_id]
        state_mismatch = str(mismatch["state_mismatch_state_example_id"])
        cross_policy = _cross_prompt_policy_row(
            source["teacher"]["policy_rows"][state_mismatch]["bare"],
            teacher,
            pair_id=f"{state_id}::raw_teacher_on::{state_mismatch}",
        )
        definitions = (
            ("X0_no_memory", policy, None),
            (
                "X1_correct_memory",
                policy,
                str(outcome["selected_transition_id"]),
            ),
            (
                "X2_transition_shuffle",
                policy,
                str(mismatch["transition_mismatch_transition_id"]),
            ),
            (
                "X3_state_shuffle",
                cross_policy,
                str(outcome["selected_transition_id"]),
            ),
        )
        for control, policy_row, transition_id in definitions:
            metric = _policy_control(
                backend=backend,
                reader=reader,
                policy_row=policy_row,
                teacher=teacher,
                slots=None if transition_id is None else slots[transition_id],
            )
            rows[control].append(float(metric["policy_kl"]))
    if not rows["X1_correct_memory"]:
        raise ValueError("Heldout policy evaluation contains no positive states")
    return {name: statistics.fmean(values) for name, values in rows.items()}


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    seed_everything(GLOBAL_SEED)
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "memory_index",
        "mismatches",
        "task_split",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
        "implementation",
        "phase1_selection",
        "phase2_summary",
    )
    _require(paths, required)
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen must remain frozen")
    source = _load_source(paths)
    slot_bank = _load_slot_bank(paths["memory_index"])
    maximum = int(settings["curriculum"]["phase2_max_epochs"])
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    reports = []
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="reader_policy_evaluation_v2",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        for epoch in range(1, maximum + 1):
            checkpoint = paths["phase2_root"] / f"checkpoints/model_epoch_{epoch:02d}.pt"
            payload = torch.load(checkpoint, map_location=backend.device, weights_only=False)
            reader = _reader(settings, backend.device)
            reader.load_state_dict(payload["reader_state_dict"])
            reader.eval()
            output = paths["policy_eval_root"] / f"epoch_{epoch:02d}.json"
            started = time.perf_counter()
            evaluation = _evaluate_policy_checkpoint(
                backend=backend, reader=reader, source=source, slots=slot_bank
            )
            raw = _positive_raw_policy(
                backend=backend, reader=reader, source=source, slots=slot_bank
            )
            evaluation["positive_raw_teacher_policy_kl"] = raw
            evaluation["positive_correct_below_zero"] = (
                raw["X1_correct_memory"] < raw["X0_no_memory"]
            )
            evaluation["positive_correct_below_transition_shuffle"] = (
                raw["X1_correct_memory"] < raw["X2_transition_shuffle"]
            )
            evaluation["positive_correct_below_state_shuffle"] = (
                raw["X1_correct_memory"] < raw["X3_state_shuffle"]
            )
            report = {
                "format": "cross_attention_reader_policy_evaluation_8b_v2",
                "epoch": epoch,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
                "reader_sha256": module_state_sha256(reader),
                "heldout_state_count": 98,
                "condition_count": 392,
                "evaluation": evaluation,
                "elapsed_seconds": time.perf_counter() - started,
                "test_normal_outcomes_used": False,
            }
            atomic_write_json(output, report)
            reports.append(report)
            attempt.progress(
                status=f"reader_policy_eval_epoch_{epoch}",
                latest_validated_checkpoint=str(output),
                completed_checkpoints=epoch,
                total_checkpoints=maximum,
                policy_gate=(
                    evaluation["positive_correct_below_zero"]
                    and evaluation["positive_correct_below_transition_shuffle"]
                    and evaluation["positive_correct_below_state_shuffle"]
                ),
            )
        summary = {
            "format": "cross_attention_reader_policy_evaluation_summary_8b_v2",
            "global_seed": GLOBAL_SEED,
            "checkpoint_count": len(reports),
            "reports": [
                {
                    "epoch": row["epoch"],
                    "checkpoint_sha256": row["checkpoint_sha256"],
                    "summary": row["evaluation"]["summary"],
                    "positive_raw_teacher_policy_kl": row["evaluation"][
                        "positive_raw_teacher_policy_kl"
                    ],
                    "positive_correct_below_zero": row["evaluation"][
                        "positive_correct_below_zero"
                    ],
                    "positive_correct_below_transition_shuffle": row["evaluation"][
                        "positive_correct_below_transition_shuffle"
                    ],
                    "positive_correct_below_state_shuffle": row["evaluation"][
                        "positive_correct_below_state_shuffle"
                    ],
                }
                for row in reports
            ],
            "test_normal_outcomes_used": False,
            "passed": len(reports) == maximum,
        }
        atomic_write_json(paths["policy_eval_summary"], summary)
        attempt.progress(
            status="reader_policy_evaluation_v2_complete",
            latest_validated_checkpoint=str(paths["policy_eval_summary"]),
            result=summary,
        )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
