from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import (
    FIELD_VERSION,
    FUSION_ALPHA,
    FUSION_DROPOUT,
    FUSION_RANK,
    GLOBAL_SEED,
    MEMORY_SLOT_COUNT,
    MEMORY_TOKEN_CAP,
    READER_VERSION,
)
from rcmf.training.cross_attention_memory_8b import (
    render_observation_excluded_transition,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256, stable_key
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)


PREFLIGHT_VERSION = "cross_attention_field_runtime_preflight_8b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp030a_prepare")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    parent_reader = Path(str(settings["parent_exp029a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signature_classes": parent_b
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "exp028a_outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "exp028a_teacher_cache": parent_a
        / "structured_compiler/policy_teacher_cache.pt",
        "exp029a_selection": parent_reader / "reader/checkpoint_selection.json",
        "task_split": Path(str(settings["task_split_manifest"])),
        "corpus_summary": corpus / "summary.json",
        "corpus_validation": corpus / "structural_validation.json",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "semantic_module": Path(str(settings["appworld"]["semantic_module"])),
        "full_bridge": Path(str(settings["appworld"]["full_bridge_script"])),
        "one_step_bridge": Path(str(settings["appworld"]["one_step_bridge_script"])),
        "preflight": artifact_dir / "runtime_preflight.json",
        "preflight_report": artifact_dir / "runtime_preflight.md",
        "run_manifest": artifact_dir / "run_manifest.json",
        "memory_manifest": artifact_dir / "memory/observation_excluded_manifest.json",
        "mismatch_manifest": artifact_dir / "curriculum/mismatch_manifest.json",
        "curriculum_manifest": artifact_dir / "curriculum/curriculum_manifest.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-030A immutable input: {missing}")


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
    raise KeyError("Could not identify the frozen 29/8 task split")


def _build_memory_manifest(
    transitions: Sequence[Mapping[str, Any]], path: Path
) -> dict[str, Any]:
    rows = []
    for transition in transitions:
        rendered = render_observation_excluded_transition(transition)
        rows.append(
            {
                "transition_id": str(transition["transition_id"]),
                "parent_id": str(transition["parent_memory_id"]),
                "parent_task_id": str(transition["parent_task_id"]),
                "step_index": int(transition["step_index"]),
                "source_task_goal_sha256": str(transition["source_task_goal_sha256"]),
                "pre_action_state_sha256": str(
                    transition["canonical_pre_action_state_sha256"]
                ),
                "complete_action_sha256": str(transition["complete_action_sha256"]),
                "post_action_observation_sha256": str(
                    transition["complete_post_action_observation_sha256"]
                ),
                "rendered_memory_sha256": sha256_text(rendered),
                "rendered_memory_characters": len(rendered),
                "included_views": [
                    "source_task_goal",
                    "canonical_pre_action_state",
                    "complete_action",
                ],
                "post_action_observation_excluded": True,
                "raw_ledger_transition_content_sha256": str(
                    transition["transition_content_sha256"]
                ),
            }
        )
    output = {
        "format": "observation_excluded_transition_memory_manifest_8b_v1",
        "global_seed": GLOBAL_SEED,
        "memory_count": len(rows),
        "slot_count": MEMORY_SLOT_COUNT,
        "token_cap": MEMORY_TOKEN_CAP,
        "raw_ledger_authoritative": True,
        "student_prompt_contains_raw_memory": False,
        "rows": rows,
    }
    output["manifest_sha256"] = canonical_sha256(output)
    atomic_write_json(path, output)
    return output


def _build_mismatches(
    outcomes: Sequence[Mapping[str, Any]], path: Path
) -> dict[str, Any]:
    rows = []
    for split in ("model_train", "heldout_train_validation"):
        split_rows = [row for row in outcomes if str(row["model_split"]) == split]
        for row in split_rows:
            transition_candidates = [
                other
                for other in split_rows
                if str(other["selected_transition_id"])
                != str(row["selected_transition_id"])
                and str(other["selected_class_id"]) != str(row["selected_class_id"])
            ]
            if not transition_candidates:
                transition_candidates = [
                    other
                    for other in split_rows
                    if str(other["selected_transition_id"])
                    != str(row["selected_transition_id"])
                ]
            state_candidates = [
                other
                for other in split_rows
                if str(other["state_task_id"]) != str(row["state_task_id"])
            ]
            if not transition_candidates or not state_candidates:
                raise ValueError(f"Cannot construct mismatch for {row['state_example_id']}")
            transition = min(
                transition_candidates,
                key=lambda other: stable_key(
                    GLOBAL_SEED,
                    "8b-transition-mismatch",
                    row["state_example_id"],
                    other["selected_transition_id"],
                ),
            )
            state = min(
                state_candidates,
                key=lambda other: stable_key(
                    GLOBAL_SEED,
                    "8b-state-mismatch",
                    row["state_example_id"],
                    other["state_example_id"],
                ),
            )
            rows.append(
                {
                    "state_example_id": str(row["state_example_id"]),
                    "model_split": split,
                    "transition_mismatch_transition_id": str(
                        transition["selected_transition_id"]
                    ),
                    "transition_signature_differs": str(transition["selected_class_id"])
                    != str(row["selected_class_id"]),
                    "state_mismatch_state_example_id": str(state["state_example_id"]),
                    "state_mismatch_task_id": str(state["state_task_id"]),
                    "state_task_differs": True,
                    "outcomes_used": False,
                }
            )
    output = {
        "format": "cross_attention_reader_mismatch_manifest_8b_v1",
        "global_seed": GLOBAL_SEED,
        "row_count": len(rows),
        "rows": rows,
    }
    output["manifest_sha256"] = canonical_sha256(output)
    atomic_write_json(path, output)
    return output


def _runtime_scenario(
    runtime: Mapping[str, Any], *, suffix: str, counts: Mapping[str, int]
) -> dict[str, float]:
    seconds = (
        counts["memory_encodes"] * float(runtime[f"memory_encode_seconds_{suffix}"])
        + counts["backwards"] * float(runtime[f"reader_backward_seconds_{suffix}"])
        + counts["policy_forwards"] * float(runtime[f"policy_forward_seconds_{suffix}"])
        + counts["live_conditions"] * float(runtime[f"live_condition_seconds_{suffix}"])
    )
    conditional_seconds = (
        counts["field_calibration_backwards"]
        * float(runtime[f"reader_backward_seconds_{suffix}"])
        + counts["field_live_conditions"]
        * float(runtime[f"live_condition_seconds_{suffix}"])
        + 2.0 * float(runtime[f"first37_condition_hours_{suffix}"]) * 3600.0
    )
    return {
        "phase_a_c_h100_hours": seconds / 3600.0,
        "conditional_phase_d_f_h100_hours": conditional_seconds / 3600.0,
        "maximum_conditional_total_h100_hours": (seconds + conditional_seconds)
        / 3600.0,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = tuple(
        name
        for name in paths
        if name
        not in {
            "preflight",
            "preflight_report",
            "run_manifest",
            "memory_manifest",
            "mismatch_manifest",
            "curriculum_manifest",
        }
    )
    _require(paths, required)

    split = _json(paths["task_split"])
    model_train_tasks, heldout_tasks = _split_task_ids(split)
    if len(model_train_tasks) != 29 or len(heldout_tasks) != 8:
        raise ValueError("Frozen task split is not 29/8")
    if set(model_train_tasks) & set(heldout_tasks):
        raise ValueError("Frozen train-task split overlaps")

    transitions = _rows(paths["transitions"])
    decisions = _rows(paths["decisions"])
    train_task_set = set(model_train_tasks) | set(heldout_tasks)
    train_transitions = [
        row for row in transitions if str(row["parent_task_id"]) in train_task_set
    ]
    model_train_transitions = [
        row for row in transitions if str(row["parent_task_id"]) in set(model_train_tasks)
    ]
    heldout_transitions = [
        row for row in transitions if str(row["parent_task_id"]) in set(heldout_tasks)
    ]
    train_decisions = [row for row in decisions if str(row["task_id"]) in train_task_set]
    if (len(train_transitions), len(model_train_transitions), len(heldout_transitions)) != (
        499,
        401,
        98,
    ):
        raise ValueError("Clean transition counts differ from the frozen 499/401/98 contract")
    if len(train_decisions) != 499:
        raise ValueError("Clean decision count differs from 499")
    transition_keys = {
        (str(row["parent_task_id"]), int(row["step_index"]))
        for row in train_transitions
    }
    decision_keys = {
        (str(row["task_id"]), int(row["step_id"])) for row in train_decisions
    }
    if transition_keys != decision_keys:
        raise ValueError("Phase-1 source transitions do not align with decision states")

    outcomes_payload = _json(paths["exp028a_outcomes"])
    outcomes = [dict(row) for row in outcomes_payload["rows"]]
    outcome_counts = Counter((str(row["model_split"]), str(row["label"])) for row in outcomes)
    expected_outcomes = {
        ("model_train", "POSITIVE"): 105,
        ("model_train", "NEUTRAL"): 235,
        ("model_train", "HARMFUL"): 26,
        ("heldout_train_validation", "POSITIVE"): 24,
        ("heldout_train_validation", "NEUTRAL"): 65,
        ("heldout_train_validation", "HARMFUL"): 9,
    }
    if dict(outcome_counts) != expected_outcomes:
        raise ValueError(f"EXP-028A paired outcome counts differ: {outcome_counts}")

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    memory_manifest = _build_memory_manifest(train_transitions, paths["memory_manifest"])
    mismatch_manifest = _build_mismatches(outcomes, paths["mismatch_manifest"])
    phase2_train_states = 366
    phase2_positive = 105
    phase2_units = phase2_train_states + 2 * phase2_positive
    curriculum = {
        "format": "cross_attention_reader_curriculum_manifest_8b_v1",
        "global_seed": GLOBAL_SEED,
        "phase1": {
            "model_train_samples": len(model_train_transitions),
            "heldout_samples": len(heldout_transitions),
            "maximum_epochs": int(settings["curriculum"]["phase1_max_epochs"]),
            "source_transition_is_own_memory": True,
            "observation_excluded_memory": True,
        },
        "phase2": {
            "model_train_state_count": phase2_train_states,
            "heldout_state_count": 98,
            "positive_train_state_count": phase2_positive,
            "training_units_per_epoch": phase2_units,
            "maximum_epochs": int(settings["curriculum"]["phase2_max_epochs"]),
            "positive_correct_target": "raw_memory_teacher_policy",
            "neutral_harmful_target": "bare_policy",
            "positive_controls": ["transition_mismatch", "state_mismatch"],
            "class_balance_positive_and_bare": True,
        },
        "test_normal_outcome_used": False,
        "mismatch_manifest_sha256": sha256_file(paths["mismatch_manifest"]),
    }
    curriculum["manifest_sha256"] = canonical_sha256(curriculum)
    atomic_write_json(paths["curriculum_manifest"], curriculum)

    counts = {
        "memory_encodes": 499,
        "backwards": 401 * 3 + phase2_units * 4,
        "policy_forwards": 98 * 3 + 98 * 4 * 4,
        "live_conditions": 98 * 3 * 4,
        "field_calibration_backwards": phase2_train_states * 2,
        "field_live_conditions": 45 * 3,
    }
    expected = _runtime_scenario(settings["runtime"], suffix="expected", counts=counts)
    conservative = _runtime_scenario(
        settings["runtime"], suffix="conservative", counts=counts
    )
    field = settings["field"]
    field_a_bytes = (
        int(field["layer_count"])
        * int(field["key_dim"])
        * int(field["slot_count"])
        * int(field["model_dim"])
        * 4
    )
    field_b_bytes = (
        int(field["layer_count"])
        * int(field["slot_count"])
        * int(field["model_dim"])
        * 4
    )
    memory_cache_bytes = 499 * 36 * 16 * 4096 * 2
    trainable_parameters = 36 * 2 * 4096 * FUSION_RANK
    projected_artifact_bytes = (
        memory_cache_bytes
        + field_a_bytes
        + field_b_bytes
        + 7 * int(settings["runtime"]["checkpoint_bytes"])
        + (counts["live_conditions"] + counts["field_live_conditions"])
        * int(settings["runtime"]["condition_result_bytes"])
    )
    threshold = float(settings["runtime"]["review_threshold_h100_hours"])
    automatic = expected["maximum_conditional_total_h100_hours"] <= threshold
    replay_lineage = _json(paths["replay_lineage"])
    immutable_checks = {
        "replay_lineage": str(replay_lineage["lineage_sha256"])
        == str(settings["expected_replay_lineage_sha256"]),
        "structural_validation": bool(_json(paths["corpus_validation"])["passed"]),
        "selector_sha256": sha256_file(paths["selector"])
        == str(settings["expected_selector_sha256"]),
        "reader_version": READER_VERSION == str(settings["reader"]["version"]),
        "field_version": FIELD_VERSION == str(settings["field"]["version"]),
        "slot_count": MEMORY_SLOT_COUNT == int(settings["memory"]["slot_count"]),
        "token_cap": MEMORY_TOKEN_CAP
        == int(settings["memory"]["token_cap_before_sampling"]),
        "fusion": (
            FUSION_RANK == int(settings["reader"]["fusion_rank"])
            and FUSION_ALPHA == float(settings["reader"]["fusion_alpha"])
            and FUSION_DROPOUT == float(settings["reader"]["fusion_dropout"])
        ),
        "exp029a_failure_preserved": str(
            _json(paths["exp029a_selection"])["decision_branch"]
        )
        == "fixed_memory_reader_failed",
    }
    if not all(immutable_checks.values()):
        raise ValueError(f"EXP-030A immutable contract failed: {immutable_checks}")

    source_hashes = {name: sha256_file(paths[name]) for name in required}
    report = {
        "format": PREFLIGHT_VERSION,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "immutable_checks": immutable_checks,
        "task_counts": {"model_train": 29, "heldout_train": 8},
        "pair_counts": {
            "phase1_model_train": 401,
            "phase1_heldout": 98,
            "phase2_model_train_states": 366,
            "phase2_heldout_states": 98,
            "phase2_training_units_per_epoch": phase2_units,
        },
        "label_counts": {
            f"{split_name}:{label}": count
            for (split_name, label), count in sorted(outcome_counts.items())
        },
        "execution_counts": counts,
        "reader": {
            "qwen_layers": 36,
            "memory_slots_per_layer": 16,
            "trainable_parameter_count": trainable_parameters,
            "qwen_parameter_count_trainable": 0,
        },
        "field": {
            "key_dim": 960,
            "A_shape": [36, 960, 16, 4096],
            "B_shape": [36, 16, 4096],
            "A_bytes_float32": field_a_bytes,
            "B_bytes_float32": field_b_bytes,
            "read_shape_independent_of_memory_count": [36, 16, 4096],
            "exact_selector_decomposition": "three calibrated rank32 seed fields concatenated",
        },
        "memory_slot_cache_bytes_bfloat16": memory_cache_bytes,
        "expected": expected,
        "conservative": conservative,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": automatic,
        "staged_runtime_recheck_before_conditional_field": True,
        "projected_artifact_bytes_with_conditional_field": projected_artifact_bytes,
        "source_hashes": source_hashes,
        "memory_manifest_sha256": sha256_file(paths["memory_manifest"]),
        "mismatch_manifest_sha256": sha256_file(paths["mismatch_manifest"]),
        "curriculum_manifest_sha256": sha256_file(paths["curriculum_manifest"]),
        "passed": automatic,
    }
    report["contract_sha256"] = canonical_sha256(report)
    atomic_write_json(paths["preflight"], report)
    run_manifest = {
        "format": "reversible_cross_attention_field_run_manifest_8b_v1",
        "run_uuid": str(settings["run_uuid"]),
        "starting_head": str(settings["starting_head"]),
        "archive_branch": str(settings["archive_branch"]),
        "working_branch": str(settings["working_branch"]),
        "config_sha256": sha256_file(args.config),
        "source_hashes": source_hashes,
        "parent_exp029a_decision": "fixed_memory_reader_failed",
        "cross_attention_reader_is_borrowed_prior_art": True,
        "third_party_source_copied": False,
        "observation_excluded": True,
        "student_prompt_contains_raw_memory": False,
        "raw_ledger_authoritative": True,
    }
    run_manifest["manifest_sha256"] = canonical_sha256(run_manifest)
    atomic_write_json(paths["run_manifest"], run_manifest)
    atomic_write_text(
        paths["preflight_report"],
        "\n".join(
            (
                "# EXP-030A runtime preflight",
                "",
                f"- run UUID: `{settings['run_uuid']}`",
                "- global seed: `25101`",
                "- Phase-1 train/heldout samples: `401/98`",
                f"- Phase-2 train states/units per epoch: `366/{phase2_units}`",
                "- Phase-2 heldout states: `98`",
                f"- maximum reader backwards: `{counts['backwards']}`",
                f"- heldout live generated conditions: `{counts['live_conditions']}`",
                f"- reader trainable parameters: `{trainable_parameters}`",
                f"- memory cache bytes (BF16): `{memory_cache_bytes}`",
                f"- field A/B shapes: `36x960x16x4096` / `36x16x4096`",
                f"- expected Phase A-C H100 hours: `{expected['phase_a_c_h100_hours']:.3f}`",
                f"- expected conditional total H100 hours: `{expected['maximum_conditional_total_h100_hours']:.3f}`",
                f"- conservative conditional total H100 hours: `{conservative['maximum_conditional_total_h100_hours']:.3f}`",
                f"- automatic 18-hour authorization: `{str(automatic).lower()}`",
                "- conditional field work requires a measured runtime recheck after Phase C",
                "- post-action observations are excluded from memory encoding",
                "- raw memory text is absent from the student query prompt",
                "",
            )
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
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        attempt.progress(
            status="runtime_preflight_complete",
            latest_validated_checkpoint=str(paths["preflight"]),
            automatic_launch_allowed=automatic,
        )


if __name__ == "__main__":
    main()
