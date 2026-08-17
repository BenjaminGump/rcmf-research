from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_coverage_6g import candidate_space_summary
from rcmf.training.procedural_supervision_6f import summarize_label_coverage
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.analyze_provenance_quarantine_sensitivity_6h3 import (
    _central_interaction_checks,
    _load_first_json,
    _raw_control_pack,
    _raw_summary,
    _target_control_pack,
    _without_task,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ranking_ks": settings["ranking_ks"],
        "neutral_epsilon": float(settings["neutral_epsilon"]),
        "best_tie_tolerance": float(settings["best_tie_tolerance"]),
        "huber_delta": float(settings["huber_delta"]),
    }


def _transition_parent_task(row: Mapping[str, Any]) -> str:
    for key in (
        "transition_parent_task_id", "parent_task_id", "source_task_id",
        "transition_source_task_id", "memory_task_id",
    ):
        if row.get(key) is not None:
            return str(row[key])
    return ""


def _provenance_filter(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if str(row.get("state_task_id") or row.get("task_id", "")) != "b0a8eae_2"
        and _transition_parent_task(row) != "b0a8eae_3"
    ]


def _coverage(rows: Sequence[Mapping[str, Any]], cells: set[str]) -> dict[str, Any]:
    selected = [dict(row) for row in rows if str(row["cell"]) in cells]
    state_task = {
        str(row["state_example_id"]): str(row["state_task_id"]) for row in selected
    }
    return candidate_space_summary(
        selected, state_ids=sorted(state_task), state_task_by_id=state_task
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/benchmark/stage_c_appworld_identity_reconciliation_7a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp025a")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_7a"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    structural = _load_json(args.artifact_dir / "structural_finalization_summary.json")
    if not bool(structural["clean_corpus_ready"]):
        raise RuntimeError("Sensitivity analysis requires a structurally valid corpus")
    metric_settings = settings["sensitivity"]
    config_hash = sha256_file(args.config)
    data_hashes = {
        "structural": sha256_file(args.artifact_dir / "structural_finalization_summary.json"),
        "policy": sha256_file(args.artifact_dir / "remediation_policy_manifest.json"),
    }
    with AttemptLedger(
        args.artifact_dir, run_uuid=str(settings["run_uuid"]), attempt_id=args.attempt_id,
        phase="contaminated_checkpoint_existing_prediction_sensitivity",
        command=[str(value) for value in sys.argv], local_head=args.local_head,
        github_head=args.github_head, lambda_head=args.lambda_head,
        tmux_session=args.tmux_session, config_sha256=config_hash,
        data_manifest_hashes=data_hashes, parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint, scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        exp018 = Path(settings["parent_exp018"])
        exp019 = Path(settings["parent_exp019"])
        exp020 = Path(settings["parent_exp020"])
        exp021 = Path(settings["parent_exp021"])
        exp022 = Path(settings["parent_exp022"])
        exp023 = Path(settings["parent_exp023"])
        dcell = "heldout_state__heldout_transition"
        controls = ("correct", "shuffled_state", "shuffled_transition", "both_shuffled")

        def paths(root: Path, model: str) -> dict[str, Path]:
            return {control: root / model / dcell / f"{control}.jsonl" for control in controls}

        exp018_models = {
            model: _raw_control_pack(
                paths(exp018 / "cheap_gate" / "predictions", model),
                task_id="b0a8eae_2", settings=metric_settings,
            )
            for model in ("transition_only", "state_only", "concat_mlp", "signed_bilinear")
        }
        exp019_models = {
            "cross_encoder": _raw_control_pack(
                {control: exp019 / "part_e" / "predictions" / dcell / f"{control}.jsonl" for control in controls},
                task_id="b0a8eae_2", settings=metric_settings,
            ),
            "multiview_lowrank_tensor": _raw_control_pack(
                paths(exp019 / "parts_c_d" / "predictions", "multiview_lowrank_tensor"),
                task_id="b0a8eae_2", settings=metric_settings,
            ),
        }
        exp020_models = {
            model: _raw_control_pack(
                paths(exp020 / "models" / "lc37" / "predictions", model),
                task_id="b0a8eae_2", settings=metric_settings,
            )
            for model in ("prompt_only_cross_encoder", "multiview_lowrank_tensor")
        }
        transition_path = exp020 / "models" / "lc37" / "predictions" / "transition_only" / f"{dcell}.jsonl"
        transition_rows = _load_jsonl(transition_path)
        exp020_transition = {
            "source_path": str(transition_path),
            "original": _raw_summary(transition_rows, metric_settings),
            "provenance_filtered": _raw_summary(_without_task(transition_rows, "b0a8eae_2"), metric_settings),
        }
        model_audit = _load_json(exp021 / "model_audit_summary.json")
        exp021_targets = {}
        for target in ("T4", "T6", "T7"):
            exp021_targets[target] = {}
            for family in ("field", "cross"):
                index = model_audit["final_results"]["models"][target][family]["cells"]["D"]["controls"]
                target_paths = {control: Path(str(index[control]["rows_path"])) for control in controls}
                exp021_targets[target][family] = _target_control_pack(
                    target_paths, task_id="b0a8eae_2", settings=metric_settings
                )

        exp022_path = exp022 / "procedural_label_rows.jsonl"
        exp022_rows = _load_jsonl(exp022_path)
        exp023_path = exp023 / "full_procedural_label_rows.jsonl"
        exp023_rows = _load_jsonl(exp023_path)
        exp022_filtered = _provenance_filter(exp022_rows)
        exp023_filtered = _provenance_filter(exp023_rows)
        procedural = {
            "EXP-022": {
                "original": summarize_label_coverage(exp022_rows),
                "provenance_filtered": summarize_label_coverage(exp022_filtered),
                "removed_rows": len(exp022_rows) - len(exp022_filtered),
            },
            "EXP-023": {
                "original": {cell: _coverage(exp023_rows, {cell}) for cell in ("A", "B", "C", "D")},
                "provenance_filtered": {cell: _coverage(exp023_filtered, {cell}) for cell in ("A", "B", "C", "D")},
                "original_E": _coverage(exp023_rows, {"B", "D"}),
                "provenance_filtered_E": _coverage(exp023_filtered, {"B", "D"}),
                "removed_rows": len(exp023_rows) - len(exp023_filtered),
            },
        }

        stage_b_labels = _load_jsonl(Path(settings["stage_b_labels"]) / "student_labels.jsonl")
        b2_state_rows = sum(str(row["task_id"]) == "b0a8eae_2" for row in stage_b_labels)
        b3_state_rows = sum(str(row["task_id"]) == "b0a8eae_3" for row in stage_b_labels)
        b3_memory_presence = sum(
            "806cbabc-95ef-5414-a437-45c9596d3935" in row["ordered_effective_memory_ids"]
            for row in stage_b_labels
        )
        stage_b = {
            "validation_state_rows_removed": b2_state_rows,
            "train_state_rows_with_reconciled_query": b3_state_rows,
            "rows_carrying_b0a8eae_3_memory_slot": b3_memory_presence,
            "saved_per_pair_selector_predictions_available": False,
            "stage_b_validation_ranking_recompute_status": "not_recomputable_from_aggregate_only_artifact_without_checkpoint_forward",
            "stage_4c_selector_metric_recompute_status": "not_recomputable_from_aggregate_only_artifact_without_checkpoint_forward",
            "checkpoint_status": "contaminated_checkpoint_sensitivity_only_model_retraining_required",
        }

        gate_sensitivity = {
            "EXP-018_concat": _central_interaction_checks(
                exp018_models["concat_mlp"], exp018_models["transition_only"], minimum_positive_tasks=3
            ),
            "EXP-019_cross": _central_interaction_checks(
                exp019_models["cross_encoder"], exp018_models["transition_only"], minimum_positive_tasks=3
            ),
            "EXP-019_field": _central_interaction_checks(
                exp019_models["multiview_lowrank_tensor"], exp018_models["transition_only"], minimum_positive_tasks=3
            ),
            "EXP-020_cross": _central_interaction_checks(
                exp020_models["prompt_only_cross_encoder"],
                {"quarantine": exp020_transition["provenance_filtered"]}, minimum_positive_tasks=6,
            ),
            "EXP-020_field": _central_interaction_checks(
                exp020_models["multiview_lowrank_tensor"],
                {"quarantine": exp020_transition["provenance_filtered"]}, minimum_positive_tasks=6,
            ),
        }
        any_gate_pass = any(
            bool(value["all_nonbootstrap_central_checks_pass"])
            for value in gate_sensitivity.values()
        )
        result = {
            "format": "contaminated_checkpoint_sensitivity_analysis_7a_v1",
            "analysis_type": "existing_immutable_predictions_only_no_retraining",
            "filters": {
                "remove_b0a8eae_2_query_evaluation_rows": True,
                "remove_b0a8eae_3_query_descriptive_rows": True,
                "mask_b0a8eae_3_transition_candidates_where_saved_rows_expose_parent": True,
                "d_cell_b0a8eae_3_transition_mask_count": 0,
                "reason": "b0a8eae_3 is a train-parent transition and D uses held-out parents",
            },
            "stage_b_and_stage_4c": stage_b,
            "EXP-018": exp018_models,
            "EXP-019": exp019_models,
            "EXP-020": {"models": exp020_models, "transition_only": exp020_transition},
            "EXP-021": exp021_targets,
            "procedural_coverage": procedural,
            "central_gate_sensitivity": gate_sensitivity,
            "qualitative_conclusion": {
                "any_previously_blocked_interaction_gate_now_passes_nonbootstrap_checks": any_gate_pass,
                "historical_branch_retroactively_changed": False,
                "checkpoint_declared_clean": False,
                "fragile_points": [
                    "EXP-022 fixed-panel coverage point estimate changes when b0a8eae_2 is removed",
                    "all Stage-B/selector checkpoints retain b0a8eae_3 training influence",
                ],
                "overall_v4_blocked_conclusion_changed": False,
            },
            "qwen_forward_count": 0,
            "model_training_count": 0,
        }
        output = args.artifact_dir / "contaminated_checkpoint_sensitivity.json"
        atomic_write_json(output, result)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(result["qualitative_conclusion"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
