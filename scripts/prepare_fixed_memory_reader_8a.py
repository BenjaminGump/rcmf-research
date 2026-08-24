from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.fixed_memory_reader_8a import (
    GLOBAL_SEED,
    LAYER_INDICES,
    READER_BOTTLENECK,
    TOKEN_COUNT,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file


PREFLIGHT_FORMAT = "fixed_memory_reader_runtime_preflight_8a_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_fixed_memory_reader_8a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp029a_prepare")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_g = Path(str(settings["parent_exp027b"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "state_cache": parent_c
        / "representation_cache/multiview/state_multiview.pt",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signature_classes": parent_b
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "pairmlp_training": parent_g / "compiler/pairmlp/training_summary.json",
        "exp028a_outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "exp028a_teacher_cache": parent_a
        / "structured_compiler/policy_teacher_cache.pt",
        "task_split": Path(str(settings["task_split_manifest"])),
        "corpus_summary": corpus / "summary.json",
        "corpus_validation": corpus / "structural_validation.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path(str(settings["appworld"]["semantic_module"])),
        "full_bridge": Path(str(settings["appworld"]["full_bridge_script"])),
        "one_step_bridge": Path(str(settings["appworld"]["one_step_bridge_script"])),
        "preflight": artifact_dir / "runtime_preflight.json",
        "preflight_markdown": artifact_dir / "runtime_preflight.md",
        "run_manifest": artifact_dir / "run_manifest.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-029A immutable inputs: {missing}")


def _split_task_ids(split: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    candidates = (
        ("train_task_ids", "validation_task_ids"),
        ("model_train_task_ids", "heldout_validation_task_ids"),
        ("training_task_ids", "heldout_task_ids"),
    )
    for train_name, validation_name in candidates:
        if train_name in split and validation_name in split:
            return (
                [str(value) for value in split[train_name]],
                [str(value) for value in split[validation_name]],
            )
    nested = split.get("task_split")
    if isinstance(nested, Mapping):
        return _split_task_ids(nested)
    raise KeyError("Could not identify 29/8 task IDs in A split manifest")


def _scenario(
    *,
    name: str,
    runtime: Mapping[str, Any],
    task_count: int,
    maximum_states: int,
    assumed_positive_train: int,
    train_states: int,
    validation_states: int,
    checkpoint_count: int,
) -> dict[str, float]:
    paired_conditions = 2 * maximum_states
    teacher_forwards = paired_conditions
    backwards = (train_states + 2 * assumed_positive_train) * 4
    validation_conditions = validation_states * 4 * checkpoint_count
    validation_policy_forwards = validation_conditions
    seconds = (
        task_count * float(runtime[f"collection_task_seconds_{name}"])
        + paired_conditions * float(runtime[f"paired_condition_seconds_{name}"])
        + teacher_forwards * float(runtime[f"policy_forward_seconds_{name}"])
        + backwards * float(runtime[f"reader_backward_seconds_{name}"])
        + validation_conditions * float(runtime[f"validation_condition_seconds_{name}"])
        + validation_policy_forwards
        * float(runtime[f"policy_forward_seconds_{name}"])
    )
    return {
        "h100_hours_required_before_conditional_first37": seconds / 3600.0,
        "conditional_first37_h100_hours": 2
        * float(runtime[f"first37_condition_hours_{name}"]),
        "h100_hours_with_conditional_first37": seconds / 3600.0
        + 2 * float(runtime[f"first37_condition_hours_{name}"]),
        "collection_hours": task_count
        * float(runtime[f"collection_task_seconds_{name}"])
        / 3600.0,
        "paired_and_teacher_hours": paired_conditions
        * (
            float(runtime[f"paired_condition_seconds_{name}"])
            + float(runtime[f"policy_forward_seconds_{name}"])
        )
        / 3600.0,
        "training_hours": backwards
        * float(runtime[f"reader_backward_seconds_{name}"])
        / 3600.0,
        "validation_hours": validation_conditions
        * (
            float(runtime[f"validation_condition_seconds_{name}"])
            + float(runtime[f"policy_forward_seconds_{name}"])
        )
        / 3600.0,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8a"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-029A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = tuple(
        name
        for name in paths
        if name not in {"preflight", "preflight_markdown", "run_manifest"}
    )
    _require(paths, required)
    parent_training = _json(paths["pairmlp_training"])
    pairmlp_checkpoint = Path(str(parent_training["selected_checkpoint"]))
    if not pairmlp_checkpoint.exists():
        raise FileNotFoundError(f"Missing selected EXP-027B PairMLP: {pairmlp_checkpoint}")
    immutable_checks = {
        "selector": sha256_file(paths["selector"])
        == str(settings["expected_selector_sha256"]),
        "pairmlp_checkpoint": sha256_file(pairmlp_checkpoint)
        == str(settings["expected_pairmlp_checkpoint_sha256"]),
        "replay_lineage": str(_json(paths["replay_lineage"])["lineage_sha256"])
        == str(settings["expected_replay_lineage_sha256"]),
        "structural_validation": bool(_json(paths["corpus_validation"])["passed"]),
        "reader_layers": list(LAYER_INDICES)
        == [int(value) for value in settings["reader"]["selected_layer_indices"]],
        "reader_tokens": TOKEN_COUNT == int(settings["reader"]["token_count"]),
        "reader_bottleneck": READER_BOTTLENECK
        == int(settings["reader"]["bottleneck_dim"]),
    }
    if not all(immutable_checks.values()):
        raise ValueError(f"EXP-029A immutable contract failed: {immutable_checks}")
    split = _json(paths["task_split"])
    train_tasks, validation_tasks = _split_task_ids(split)
    collection = settings["collection"]
    if len(train_tasks) != int(collection["model_train_task_count"]):
        raise ValueError("Model-training task count differs from frozen 29-task split")
    if len(validation_tasks) != int(
        collection["heldout_train_validation_task_count"]
    ):
        raise ValueError("Heldout train-validation task count differs from frozen 8-task split")
    if set(train_tasks) & set(validation_tasks):
        raise ValueError("Train and heldout train-validation tasks overlap")

    maximum_per_task = int(collection["maximum_live_states_per_task"])
    maximum_train_states = len(train_tasks) * maximum_per_task
    maximum_validation_states = len(validation_tasks) * maximum_per_task
    maximum_states = maximum_train_states + maximum_validation_states
    assumed_positive_train = max(
        int(collection["minimum_model_train_positive_states"]),
        round(maximum_train_states / 3),
    )
    checkpoints = len(settings["training"]["checkpoint_updates"])
    runtime = settings["runtime"]
    expected = _scenario(
        name="expected",
        runtime=runtime,
        task_count=len(train_tasks) + len(validation_tasks),
        maximum_states=maximum_states,
        assumed_positive_train=assumed_positive_train,
        train_states=maximum_train_states,
        validation_states=maximum_validation_states,
        checkpoint_count=checkpoints,
    )
    conservative = _scenario(
        name="conservative",
        runtime=runtime,
        task_count=len(train_tasks) + len(validation_tasks),
        maximum_states=maximum_states,
        assumed_positive_train=assumed_positive_train,
        train_states=maximum_train_states,
        validation_states=maximum_validation_states,
        checkpoint_count=checkpoints,
    )
    paired_conditions = 2 * maximum_states
    backwards = (maximum_train_states + 2 * assumed_positive_train) * 4
    validation_conditions = maximum_validation_states * 4 * checkpoints
    projected_bytes = (
        maximum_states * int(runtime["projected_bytes_per_state"])
        + paired_conditions * int(runtime["projected_bytes_per_policy_teacher"])
        + validation_conditions * int(runtime["projected_bytes_per_condition"])
        + checkpoints * int(runtime["projected_bytes_per_checkpoint"])
    )
    threshold = float(runtime["review_threshold_h100_hours"])
    launch = expected["h100_hours_with_conditional_first37"] <= threshold
    report = {
        "format": PREFLIGHT_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_checks": immutable_checks,
        "task_split": {
            "model_train_task_count": len(train_tasks),
            "heldout_train_validation_task_count": len(validation_tasks),
            "model_train_task_ids": train_tasks,
            "heldout_train_validation_task_ids": validation_tasks,
            "split_sha256": sha256_file(paths["task_split"]),
        },
        "maximum_on_policy_state_count": maximum_states,
        "maximum_model_train_state_count": maximum_train_states,
        "maximum_validation_state_count": maximum_validation_states,
        "maximum_paired_condition_count": paired_conditions,
        "assumed_positive_training_state_count": assumed_positive_train,
        "maximum_backward_count": backwards,
        "maximum_validation_condition_count": validation_conditions,
        "conditional_first37_task_condition_count": 74,
        "target_alignment": str(collection["target_alignment"]),
        "over_context_treatment": "explicit_missing_no_truncation_no_replacement",
        "expected": expected,
        "conservative": conservative,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": launch,
        "projected_artifact_bytes": projected_bytes,
        "source_hashes": {name: sha256_file(paths[name]) for name in required}
        | {"pairmlp_checkpoint": sha256_file(pairmlp_checkpoint)},
        "passed": launch,
    }
    report["contract_sha256"] = canonical_sha256(report)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["preflight"], report)
    manifest = {
        "format": "fixed_memory_reader_run_manifest_8a_v1",
        "run_uuid": str(settings["run_uuid"]),
        "starting_head": str(settings["starting_head"]),
        "working_branch": str(settings["working_branch"]),
        "archive_branch": str(settings["archive_branch"]),
        "config_sha256": sha256_file(args.config),
        "preflight_sha256": sha256_file(paths["preflight"]),
        "parent_pairmlp_checkpoint": str(pairmlp_checkpoint),
        "parent_pairmlp_checkpoint_sha256": sha256_file(pairmlp_checkpoint),
        "student_prompt_contains_raw_memory": False,
        "reader_fixed_size_independent_of_memory_count": True,
        "original_qwen_trainable": False,
    }
    atomic_write_json(paths["run_manifest"], manifest)
    atomic_write_text(
        paths["preflight_markdown"],
        "\n".join(
            [
                "# EXP-029A runtime preflight",
                "",
                f"- run UUID: `{settings['run_uuid']}`",
                f"- train/validation tasks: `{len(train_tasks)}/{len(validation_tasks)}`",
                f"- maximum on-policy states: `{maximum_states}`",
                f"- maximum paired T0/T1 conditions: `{paired_conditions}`",
                f"- maximum reader backwards: `{backwards}`",
                f"- maximum checkpoint-validation conditions: `{validation_conditions}`",
                f"- expected H100 hours with conditional first37: `{expected['h100_hours_with_conditional_first37']:.4f}`",
                f"- conservative H100 hours with conditional first37: `{conservative['h100_hours_with_conditional_first37']:.4f}`",
                f"- automatic 18-hour authorization: `{str(launch).lower()}`",
                f"- projected artifact bytes: `{projected_bytes}`",
                "- target alignment: clean successful-trajectory target at the same step ordinal",
                "- over-context rows: explicit missing; no truncation or replacement",
                "- original Qwen parameters: frozen",
                "- student raw-memory text: absent",
                "",
            ]
        ),
    )
    print(json.dumps(report, sort_keys=True))

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="runtime_preflight",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=report["source_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        attempt.progress(
            status="runtime_preflight_complete",
            latest_validated_checkpoint=str(paths["preflight"]),
            automatic_launch_allowed=launch,
        )


if __name__ == "__main__":
    main()
