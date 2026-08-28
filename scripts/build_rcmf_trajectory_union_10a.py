from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import re
import statistics
import sys
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    GLOBAL_SEED,
    UNION_FORMAT,
    balance_union_rows,
    deterministic_bank_augmentation,
    first_common_history_preference,
    strict_no_progress_loops,
    successful_trajectory_weights,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import _load_data, _paths as parent_paths


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
API_PATTERN = re.compile(r"apis\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_union")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    atomic_write_text(
        path,
        "".join(json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
    )


def _result_path(root: Path, condition: str, task_id: str) -> Path:
    return root / "rollouts/conditions" / condition / "task_results" / f"{task_id}.json"


def _action_type(code: str) -> str:
    match = API_PATTERN.search(code)
    if match is None:
        return "python_reasoning"
    api = match.group(2)
    if api == "complete_task":
        return "completion"
    if api == "login":
        return "authentication"
    if api == "show_api_doc":
        return "documentation"
    if api.startswith(("create", "add", "update", "delete", "send", "post", "import")):
        return "write_mutation"
    return "read_query"


def _onpolicy_row(
    *,
    artifact_dir: Path,
    task_id: str,
    task_class: str,
    selection: Mapping[str, Any],
    step: Mapping[str, Any],
    parent_tasks: Sequence[str],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    condition = str(selection["condition"])
    unit_id = f"onpolicy::{task_id}::{condition}::{int(step['step_id']):02d}"
    role = str(selection["role"])
    if role == "preservation":
        group = "preservation"
    elif role == "memory_benefit":
        group = "memory_benefit"
    else:
        group = "both_success"
    augmentation = deterministic_bank_augmentation(
        unit_id=unit_id,
        query_task_id=task_id,
        parent_task_ids=parent_tasks,
        fraction=float(settings["union"]["bank_augmentation_fraction"]),
        removal_fraction=float(settings["union"]["unrelated_parent_removal_fraction"]),
    )
    code = str(step["exact_executed_code"])
    match = API_PATTERN.search(code)
    return {
        "format": "rcmf_trajectory_union_training_row_10a_v1",
        "unit_id": unit_id,
        "source_kind": "onpolicy_successful_trajectory",
        "source_task_id": task_id,
        "source_task_class": task_class,
        "source_condition": condition,
        "source_step_id": int(step["step_id"]),
        "source_task_result": str(_result_path(artifact_dir, condition, task_id)),
        "teacher_condition": "bare" if condition == "T0" else "original_correct_field",
        "student_field_control": "correct",
        "balance_group": group,
        "sample_weight": float(selection["weight"]),
        "prompt_sha256": str(step["rendered_message_sha256"]),
        "prompt_tokens": int(step["prompt_tokens"]),
        "response_sha256": canonical_sha256(step["generated_token_ids"]),
        "target_token_count": len(step["generated_token_ids"]),
        "primary_app": None if match is None else match.group(1),
        "primary_api": None if match is None else match.group(2),
        "action_type": _action_type(code),
        "bank_augmentation": augmentation,
        "first37_outcome_used": False,
        "test_normal_used": False,
    }


def _auxiliary_rows(
    *,
    neither_tasks: set[str],
    data: Mapping[str, Any],
    parent_tasks: Sequence[str],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for outcome in sorted(data["outcomes"], key=lambda row: str(row["state_example_id"])):
        task_id = str(outcome["state_task_id"])
        if task_id not in neither_tasks or str(outcome["model_split"]) != "model_train":
            continue
        state_id = str(outcome["state_example_id"])
        unit_id = f"clean-replay-aux::{state_id}"
        target = data["teacher"]["ground_truth_rows"][state_id]
        rows.append(
            {
                "format": "rcmf_trajectory_union_training_row_10a_v1",
                "unit_id": unit_id,
                "source_kind": "clean_replay_success_auxiliary",
                "source_task_id": task_id,
                "source_task_class": "neither_success",
                "source_condition": "clean_replay_success",
                "source_step_id": int(outcome["state_step_id"]),
                "state_example_id": state_id,
                "teacher_condition": "clean_replay_action_under_original_correct_field",
                "student_field_control": "correct",
                "balance_group": "neither_auxiliary",
                "sample_weight": float(settings["union"]["neither_auxiliary_weight"]),
                "prompt_tokens": len(target["input_ids"]) - int(target["target_len"]),
                "response_sha256": canonical_sha256(
                    target["response_cache"]["target_token_ids"]
                ),
                "target_token_count": int(target["target_len"]),
                "primary_app": None,
                "primary_api": None,
                "action_type": "clean_replay_auxiliary",
                "bank_augmentation": deterministic_bank_augmentation(
                    unit_id=unit_id,
                    query_task_id=task_id,
                    parent_task_ids=parent_tasks,
                    fraction=float(settings["union"]["bank_augmentation_fraction"]),
                    removal_fraction=float(
                        settings["union"]["unrelated_parent_removal_fraction"]
                    ),
                ),
                "first37_outcome_used": False,
                "test_normal_used": False,
            }
        )
    return rows


def build_union(
    *, cfg: Any, settings: Mapping[str, Any], artifact_dir: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rollout = _json(artifact_dir / "rollouts/rollout_summary.json")
    data_manifest = _json(Path(str(settings["immutable_exp031a"]["data_manifest"])))
    parent_tasks = [str(value) for value in data_manifest["train_task_ids"]]
    if rollout["trajectory_count"] != 87 or len(parent_tasks) != 29:
        raise ValueError("Frozen rollout or train-task count differs")
    parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
    data = _load_data(parent_paths(cfg.raw["stage_c_9a"], parent_root))
    raw_rows: list[dict[str, Any]] = []
    preference_rows: list[dict[str, Any]] = []
    loop_rows: list[dict[str, Any]] = []
    task_rows = []
    neither_tasks: set[str] = set()
    for task_id in parent_tasks:
        results = {
            condition: _json(_result_path(artifact_dir, condition, task_id))
            for condition in ("T0", "T1", "T2")
        }
        for row in results.values():
            row["strict_no_progress_loop_count"] = len(strict_no_progress_loops(row))
        task_class = next(
            row["task_class"]
            for row in rollout["task_rows"]
            if str(row["task_id"]) == task_id
        )
        selections = successful_trajectory_weights(
            bare=results["T0"], rcmf=results["T1"]
        )
        if task_class == "neither_success":
            neither_tasks.add(task_id)
        for selection in selections:
            source = results[str(selection["condition"])]
            raw_rows.extend(
                _onpolicy_row(
                    artifact_dir=artifact_dir,
                    task_id=task_id,
                    task_class=task_class,
                    selection=selection,
                    step=step,
                    parent_tasks=parent_tasks,
                    settings=settings,
                )
                for step in source["steps"]
            )
        preference = first_common_history_preference(
            bare=results["T0"], rcmf=results["T1"]
        )
        if preference is not None:
            unit_id = f"preference::{task_id}"
            preference_rows.append(
                {
                    "format": "rcmf_first_divergence_preference_10a_v1",
                    "unit_id": unit_id,
                    "task_id": task_id,
                    "task_class": task_class,
                    "weight": float(settings["training"]["preference_margin_weight"]),
                    "bank_augmentation": deterministic_bank_augmentation(
                        unit_id=unit_id,
                        query_task_id=task_id,
                        parent_task_ids=parent_tasks,
                        fraction=float(settings["union"]["bank_augmentation_fraction"]),
                        removal_fraction=float(
                            settings["union"]["unrelated_parent_removal_fraction"]
                        ),
                    ),
                    **preference,
                }
            )
        for condition, result in results.items():
            for loop in strict_no_progress_loops(result):
                unit_id = (
                    f"loop::{task_id}::{condition}::{int(loop['start_step'])}::"
                    f"{len(loop_rows)}"
                )
                loop_rows.append(
                    {
                        "format": "rcmf_no_progress_loop_negative_10a_v1",
                        "unit_id": unit_id,
                        "task_id": task_id,
                        "condition": condition,
                        "task_result": str(_result_path(artifact_dir, condition, task_id)),
                        "weight": float(settings["union"]["no_progress_negative_weight"]),
                        "bank_augmentation": deterministic_bank_augmentation(
                            unit_id=unit_id,
                            query_task_id=task_id,
                            parent_task_ids=parent_tasks,
                            fraction=float(
                                settings["union"]["bank_augmentation_fraction"]
                            ),
                            removal_fraction=float(
                                settings["union"]["unrelated_parent_removal_fraction"]
                            ),
                        ),
                        **loop,
                    }
                )
        task_rows.append(
            {
                "task_id": task_id,
                "task_class": task_class,
                "selected_trajectories": selections,
                "preference_created": preference is not None,
                "loop_negative_count": sum(
                    len(strict_no_progress_loops(row)) for row in results.values()
                ),
            }
        )
    raw_rows.extend(
        _auxiliary_rows(
            neither_tasks=neither_tasks,
            data=data,
            parent_tasks=parent_tasks,
            settings=settings,
        )
    )
    rows = balance_union_rows(raw_rows)
    unit_ids = [str(row["unit_id"]) for row in rows]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("Trajectory union contains duplicate unit IDs")
    prompt_lengths = [int(row["prompt_tokens"]) for row in rows]
    group_total_weights = {
        group: sum(
            float(row["balanced_weight"])
            for row in rows
            if str(row["balance_group"]) == group
        )
        for group in sorted({str(row["balance_group"]) for row in rows})
    }
    manifest = {
        "format": UNION_FORMAT,
        "global_seed": GLOBAL_SEED,
        "source_rollout_sha256": str(rollout["summary_sha256"]),
        "task_class_counts": dict(Counter(row["task_class"] for row in task_rows)),
        "task_rows": task_rows,
        "training_row_count": len(rows),
        "onpolicy_row_count": sum(
            row["source_kind"] == "onpolicy_successful_trajectory" for row in rows
        ),
        "auxiliary_expert_row_count": sum(
            row["source_kind"] == "clean_replay_success_auxiliary" for row in rows
        ),
        "preservation_state_count": sum(
            row["balance_group"] == "preservation" for row in rows
        ),
        "memory_benefit_state_count": sum(
            row["balance_group"] == "memory_benefit" for row in rows
        ),
        "preference_pair_count": len(preference_rows),
        "no_progress_negative_count": len(loop_rows),
        "total_training_unit_count": len(rows) + len(preference_rows) + len(loop_rows),
        "augmentation_unit_count": sum(
            row["bank_augmentation"]["active"]
            for row in [*rows, *preference_rows, *loop_rows]
        ),
        "group_total_weights": group_total_weights,
        "prompt_length": {
            "average": statistics.fmean(prompt_lengths),
            "median": statistics.median(prompt_lengths),
            "maximum": max(prompt_lengths),
        },
        "app_distribution": dict(Counter(str(row["primary_app"]) for row in rows)),
        "api_distribution": dict(Counter(str(row["primary_api"]) for row in rows)),
        "action_type_distribution": dict(Counter(row["action_type"] for row in rows)),
        "first37_outcomes_used": False,
        "test_normal_used": False,
        "frozen_before_training": True,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest, rows, preference_rows, loop_rows


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")
    output = args.artifact_dir / "trajectory_union"
    if (output / "trajectory_union_manifest.json").exists():
        raise FileExistsError("Frozen trajectory union already exists")
    source_hashes = {
        "rollout_summary": sha256_file(args.artifact_dir / "rollouts/rollout_summary.json"),
        "data_manifest": sha256_file(Path(str(settings["immutable_exp031a"]["data_manifest"]))),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase="exp032a_freeze_trajectory_union",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        manifest, rows, preferences, loops = build_union(
            cfg=cfg, settings=settings, artifact_dir=args.artifact_dir
        )
        atomic_write_json(output / "trajectory_union_manifest.json", manifest)
        _write_jsonl(output / "training_rows.jsonl", rows)
        _write_jsonl(output / "preference_rows.jsonl", preferences)
        _write_jsonl(output / "loop_negative_rows.jsonl", loops)
        summary = {
            **manifest,
            "training_rows_sha256": sha256_file(output / "training_rows.jsonl"),
            "preference_rows_sha256": sha256_file(output / "preference_rows.jsonl"),
            "loop_negative_rows_sha256": sha256_file(output / "loop_negative_rows.jsonl"),
            "passed": True,
        }
        atomic_write_json(output / "summary.json", summary)
        attempt.progress(
            status="trajectory_union_frozen",
            latest_validated_checkpoint=str(output / "summary.json"),
        )
        print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
