from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.interaction_representation_6c import summarize_revised_predictions
from rcmf.training.memory_use_target_6e import summarize_target_predictions
from rcmf.training.procedural_coverage_6g import candidate_space_summary
from rcmf.training.procedural_supervision_6f import summarize_label_coverage
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file


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


def _target_metric_kwargs(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_metric_kwargs(settings),
        "pair_gap_threshold": float(settings["pair_gap_threshold"]),
        "pair_gap_weight_clip": float(settings["pair_gap_weight_clip"]),
    }


def _without_task(
    rows: Sequence[Mapping[str, Any]], task_id: str
) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if str(row["state_task_id"]) != str(task_id)]


def _raw_summary(
    rows: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]
) -> dict[str, Any]:
    result = summarize_revised_predictions(rows, **_metric_kwargs(settings))
    result.pop("per_state_rows", None)
    return result


def _ndcg4(summary: Mapping[str, Any]) -> float:
    return float(summary["per_state"]["ndcg@4"]["mean"] or 0.0)


def _raw_control_pack(
    paths: Mapping[str, Path],
    *,
    task_id: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    rows_by_control = {name: _load_jsonl(path) for name, path in paths.items()}
    pair_sets = {
        name: {str(row["pair_id"]) for row in rows}
        for name, rows in rows_by_control.items()
    }
    if len({frozenset(values) for values in pair_sets.values()}) != 1:
        raise ValueError("Sensitivity controls do not contain identical pair IDs")
    original = {
        name: _raw_summary(rows, settings) for name, rows in rows_by_control.items()
    }
    filtered_rows = {
        name: _without_task(rows, task_id) for name, rows in rows_by_control.items()
    }
    filtered = {
        name: _raw_summary(rows, settings) for name, rows in filtered_rows.items()
    }
    correct_original = _ndcg4(original["correct"])
    correct_filtered = _ndcg4(filtered["correct"])
    deltas = {}
    for control in ("shuffled_state", "shuffled_transition", "both_shuffled"):
        if control not in original:
            continue
        deltas[control] = {
            "original_correct_minus_control_ndcg@4": correct_original
            - _ndcg4(original[control]),
            "quarantine_correct_minus_control_ndcg@4": correct_filtered
            - _ndcg4(filtered[control]),
        }
    return {
        "source_paths": {name: str(path) for name, path in paths.items()},
        "source_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "original_row_count": len(rows_by_control["correct"]),
        "quarantine_row_count": len(filtered_rows["correct"]),
        "removed_row_count": len(rows_by_control["correct"])
        - len(filtered_rows["correct"]),
        "original_state_count": len(
            {str(row["state_example_id"]) for row in rows_by_control["correct"]}
        ),
        "quarantine_state_count": len(
            {str(row["state_example_id"]) for row in filtered_rows["correct"]}
        ),
        "original_task_count": len(
            {str(row["state_task_id"]) for row in rows_by_control["correct"]}
        ),
        "quarantine_task_count": len(
            {str(row["state_task_id"]) for row in filtered_rows["correct"]}
        ),
        "original": original,
        "quarantine": filtered,
        "shuffle_gaps": deltas,
        "original_positive_vs_shuffle_task_gate": _positive_task_count(
            rows_by_control["correct"],
            {
                name: rows_by_control[name]
                for name in ("shuffled_state", "shuffled_transition", "both_shuffled")
                if name in rows_by_control
            },
            settings=settings,
        ),
        "quarantine_positive_vs_shuffle_task_gate": _positive_task_count(
            filtered_rows["correct"],
            {
                name: filtered_rows[name]
                for name in ("shuffled_state", "shuffled_transition", "both_shuffled")
                if name in filtered_rows
            },
            settings=settings,
        ),
    }


def _positive_task_count(
    correct_rows: Sequence[Mapping[str, Any]],
    control_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    grouped_correct: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    grouped_controls: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in correct_rows:
        grouped_correct[str(row["state_task_id"])].append(row)
    for name, rows in control_rows.items():
        for row in rows:
            grouped_controls[str(row["state_task_id"])][name].append(row)
    per_task = {}
    for task_id in sorted(grouped_correct):
        correct = _raw_summary(grouped_correct[task_id], settings)
        controls = {
            name: _raw_summary(grouped_controls[task_id][name], settings)
            for name in control_rows
        }
        correct_value = _ndcg4(correct)
        best_control = max(_ndcg4(value) for value in controls.values())
        per_task[task_id] = {
            "correct_ndcg@4": correct_value,
            "best_control_ndcg@4": best_control,
            "positive_relative_behavior": correct_value > best_control,
        }
    return {
        "positive_task_count": sum(
            bool(row["positive_relative_behavior"]) for row in per_task.values()
        ),
        "task_count": len(per_task),
        "per_task": per_task,
    }


def _target_rows_as_raw_predictions(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            **dict(row),
            "u_text": float(row["text_utility"]),
            "u_predicted": float(row["score"]),
            "residual_target": float(row.get("raw_residual_target", 0.0)),
            "residual_predicted": float(
                row.get("interaction_score", row["score"])
            ),
        }
        for row in rows
    ]


def _target_control_pack(
    paths: Mapping[str, Path],
    *,
    task_id: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    rows_by_control = {name: _load_jsonl(path) for name, path in paths.items()}
    if len(
        {
            frozenset(str(row["pair_id"]) for row in rows)
            for rows in rows_by_control.values()
        }
    ) != 1:
        raise ValueError("Target sensitivity controls have different pair IDs")

    def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return summarize_target_predictions(
            rows,
            target_key="T3",
            **_target_metric_kwargs(settings),
        )

    filtered = {
        name: _without_task(rows, task_id) for name, rows in rows_by_control.items()
    }
    original_summary = {name: summarize(rows) for name, rows in rows_by_control.items()}
    filtered_summary = {name: summarize(rows) for name, rows in filtered.items()}
    original_raw = {
        name: value["raw_utility"] for name, value in original_summary.items()
    }
    filtered_raw = {
        name: value["raw_utility"] for name, value in filtered_summary.items()
    }
    return {
        "source_paths": {name: str(path) for name, path in paths.items()},
        "source_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "original_row_count": len(rows_by_control["correct"]),
        "quarantine_row_count": len(filtered["correct"]),
        "removed_row_count": len(rows_by_control["correct"])
        - len(filtered["correct"]),
        "original": original_summary,
        "quarantine": filtered_summary,
        "shuffle_gaps": {
            control: {
                "original_correct_minus_control_ndcg@4": _ndcg4(
                    original_raw["correct"]
                )
                - _ndcg4(original_raw[control]),
                "quarantine_correct_minus_control_ndcg@4": _ndcg4(
                    filtered_raw["correct"]
                )
                - _ndcg4(filtered_raw[control]),
            }
            for control in ("shuffled_state", "shuffled_transition", "both_shuffled")
        },
        "quarantine_positive_task_gate": _positive_task_count(
            _target_rows_as_raw_predictions(filtered["correct"]),
            {
                name: _target_rows_as_raw_predictions(filtered[name])
                for name in ("shuffled_state", "shuffled_transition", "both_shuffled")
            },
            settings=settings,
        ),
    }


def _decision_branches(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "decision_branch" and isinstance(item, str):
                output.append(item)
            else:
                output.extend(_decision_branches(item))
    elif isinstance(value, list):
        for item in value:
            output.extend(_decision_branches(item))
    return sorted(set(output))


def _load_first_json(paths: Sequence[Path]) -> tuple[Path | None, dict[str, Any] | None]:
    for path in paths:
        if path.exists():
            return path, _load_json(path)
    return None, None


def _qualitative_change(
    original_branches: Sequence[str], quarantine_metrics: Mapping[str, Any]
) -> dict[str, Any]:
    del quarantine_metrics
    return {
        "original_decision_branches": list(original_branches),
        "qualitative_branch_change": None,
        "reason": "Final interpretation is assigned after all filtered metrics are assembled.",
    }


def _central_interaction_checks(
    model: Mapping[str, Any],
    transition_only: Mapping[str, Any],
    *,
    minimum_positive_tasks: int,
) -> dict[str, Any]:
    correct = model["quarantine"]["correct"]
    transition = transition_only["quarantine"]
    correct_ndcg = _ndcg4(correct)
    checks = {
        "ndcg4_gain_at_least_0.05": correct_ndcg - _ndcg4(transition) >= 0.05,
        "mean_per_state_spearman_at_least_0.20": float(
            correct["per_state"]["spearman"]["mean"] or 0.0
        )
        >= 0.20,
        "interaction_residual_spearman_at_least_0.20": float(
            correct["interaction_residual_spearman"] or 0.0
        )
        >= 0.20,
        "state_shuffle_drop_at_least_0.08": correct_ndcg
        - _ndcg4(model["quarantine"]["shuffled_state"])
        >= 0.08,
        "transition_shuffle_drop_at_least_0.08": correct_ndcg
        - _ndcg4(model["quarantine"]["shuffled_transition"])
        >= 0.08,
        "positive_task_count": int(
            model["quarantine_positive_vs_shuffle_task_gate"]["positive_task_count"]
        )
        >= int(minimum_positive_tasks),
    }
    return {
        "checks": checks,
        "all_nonbootstrap_central_checks_pass": all(checks.values()),
        "bootstrap_gate_not_recomputed": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r3")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_6h3"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _load_json(args.artifact_dir / "preflight_decision.json")
    task_id = str(settings["expected"]["quarantined_task_id"])
    metric_settings = settings["sensitivity"]
    exp018 = Path(settings["exp018_artifact"])
    exp019 = Path(settings["exp019_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp021 = Path(settings["exp021_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    exp023 = Path(settings["exp023_artifact"])

    summary_candidates = {
        "EXP-018": [exp018 / "parts_a_d_summary.json"],
        "EXP-019": [exp019 / "part_f_summary.json", exp019 / "part_e_summary.json"],
        "EXP-020": [exp020 / "final_summary.json"],
        "EXP-021": [exp021 / "model_audit_summary.json"],
        "EXP-022": [exp022 / "final_exp022_summary.json"],
        "EXP-023": [exp023 / "final_exp023_summary.json"],
    }
    original_branches = {}
    summary_hashes = {}
    for experiment, candidates in summary_candidates.items():
        path, payload = _load_first_json(candidates)
        original_branches[experiment] = _decision_branches(payload or {})
        summary_hashes[experiment] = sha256_file(path) if path else None

    config_hash = sha256_file(args.config)
    data_hashes = {
        "preflight_decision": sha256_file(args.artifact_dir / "preflight_decision.json"),
        **{f"{key}_summary": value for key, value in summary_hashes.items() if value},
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="provenance_quarantine_prior_result_sensitivity",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        dcell = "heldout_state__heldout_transition"
        controls = ("correct", "shuffled_state", "shuffled_transition", "both_shuffled")

        def paths(root: Path, model: str) -> dict[str, Path]:
            return {control: root / model / dcell / f"{control}.jsonl" for control in controls}

        exp018_models = {
            model: _raw_control_pack(
                paths(exp018 / "cheap_gate" / "predictions", model),
                task_id=task_id,
                settings=metric_settings,
            )
            for model in ("transition_only", "state_only", "concat_mlp", "signed_bilinear")
        }
        exp019_models = {
            "cross_encoder": _raw_control_pack(
                {control: exp019 / "part_e" / "predictions" / dcell / f"{control}.jsonl" for control in controls},
                task_id=task_id,
                settings=metric_settings,
            ),
            "multiview_lowrank_tensor": _raw_control_pack(
                paths(exp019 / "parts_c_d" / "predictions", "multiview_lowrank_tensor"),
                task_id=task_id,
                settings=metric_settings,
            ),
        }
        exp020_models = {
            model: _raw_control_pack(
                paths(exp020 / "models" / "lc37" / "predictions", model),
                task_id=task_id,
                settings=metric_settings,
            )
            for model in ("prompt_only_cross_encoder", "multiview_lowrank_tensor")
        }
        transition_path = exp020 / "models" / "lc37" / "predictions" / "transition_only" / f"{dcell}.jsonl"
        transition_rows = _load_jsonl(transition_path)
        exp020_transition = {
            "source_path": str(transition_path),
            "source_sha256": sha256_file(transition_path),
            "original": _raw_summary(transition_rows, metric_settings),
            "quarantine": _raw_summary(_without_task(transition_rows, task_id), metric_settings),
        }

        model_audit = _load_json(exp021 / "model_audit_summary.json")
        exp021_targets = {}
        for target in ("T4", "T6", "T7"):
            exp021_targets[target] = {}
            for family in ("field", "cross"):
                controls_index = model_audit["final_results"]["models"][target][family]["cells"]["D"]["controls"]
                target_paths = {
                    control: Path(str(controls_index[control]["rows_path"]))
                    for control in controls
                }
                for control, path in target_paths.items():
                    if sha256_file(path) != str(controls_index[control]["rows_sha256"]):
                        raise ValueError(f"EXP-021 prediction hash changed: {target}/{family}/{control}")
                exp021_targets[target][family] = _target_control_pack(
                    target_paths,
                    task_id=task_id,
                    settings=metric_settings,
                )

        exp022_rows_path = exp022 / "procedural_label_rows.jsonl"
        exp022_rows = _load_jsonl(exp022_rows_path)
        exp022_coverage = {
            "source_path": str(exp022_rows_path),
            "source_sha256": sha256_file(exp022_rows_path),
            "original": summarize_label_coverage(exp022_rows),
            "quarantine": summarize_label_coverage(_without_task(exp022_rows, task_id)),
        }

        exp023_rows_path = exp023 / "full_procedural_label_rows.jsonl"
        exp023_rows = _load_jsonl(exp023_rows_path)

        def coverage(rows: Sequence[Mapping[str, Any]], cells: set[str]) -> dict[str, Any]:
            selected = [dict(row) for row in rows if str(row["cell"]) in cells]
            state_task = {
                str(row["state_example_id"]): str(row["state_task_id"])
                for row in selected
            }
            return candidate_space_summary(
                selected,
                state_ids=sorted(state_task),
                state_task_by_id=state_task,
            )

        exp023_coverage = {
            "source_path": str(exp023_rows_path),
            "source_sha256": sha256_file(exp023_rows_path),
            "original": {
                "B": coverage(exp023_rows, {"B"}),
                "D": coverage(exp023_rows, {"D"}),
                "E": coverage(exp023_rows, {"B", "D"}),
            },
            "quarantine": {
                "B": coverage(_without_task(exp023_rows, task_id), {"B"}),
                "D": coverage(_without_task(exp023_rows, task_id), {"D"}),
                "E": coverage(_without_task(exp023_rows, task_id), {"B", "D"}),
            },
        }

        experiments = {
            "EXP-018": {"models": exp018_models},
            "EXP-019": {"models": exp019_models},
            "EXP-020": {"models": exp020_models, "transition_only": exp020_transition},
            "EXP-021": {"targets": exp021_targets},
            "EXP-022": {"procedural_coverage": exp022_coverage},
            "EXP-023": {"procedural_coverage": exp023_coverage},
        }
        for experiment, payload in experiments.items():
            payload["decision_sensitivity"] = _qualitative_change(
                original_branches[experiment], payload
            )

        exp018_gate = _central_interaction_checks(
            exp018_models["concat_mlp"],
            exp018_models["transition_only"],
            minimum_positive_tasks=3,
        )
        exp019_cross_gate = _central_interaction_checks(
            exp019_models["cross_encoder"],
            exp018_models["transition_only"],
            minimum_positive_tasks=3,
        )
        exp019_field_gate = _central_interaction_checks(
            exp019_models["multiview_lowrank_tensor"],
            exp018_models["transition_only"],
            minimum_positive_tasks=3,
        )
        exp020_cross_gate = _central_interaction_checks(
            exp020_models["prompt_only_cross_encoder"],
            exp020_transition,
            minimum_positive_tasks=6,
        )
        exp020_field_gate = _central_interaction_checks(
            exp020_models["multiview_lowrank_tensor"],
            exp020_transition,
            minimum_positive_tasks=6,
        )
        selected_target = str(model_audit["scientific_gate"]["selected_target"])
        selected_field = exp021_targets[selected_target]["field"]
        selected_field_adapter = {
            "quarantine": {
                name: value["raw_utility"]
                for name, value in selected_field["quarantine"].items()
            },
            "quarantine_positive_vs_shuffle_task_gate": selected_field[
                "quarantine_positive_task_gate"
            ],
        }
        exp021_transition_adapter = {
            "quarantine": exp020_transition["quarantine"]
        }
        exp021_field_gate = _central_interaction_checks(
            selected_field_adapter,
            exp021_transition_adapter,
            minimum_positive_tasks=6,
        )
        experiments["EXP-018"]["central_gate_sensitivity"] = exp018_gate
        experiments["EXP-019"]["central_gate_sensitivity"] = {
            "cross_encoder": exp019_cross_gate,
            "field": exp019_field_gate,
        }
        experiments["EXP-020"]["central_gate_sensitivity"] = {
            "cross_encoder": exp020_cross_gate,
            "field": exp020_field_gate,
        }
        experiments["EXP-021"]["central_gate_sensitivity"] = {
            "selected_target": selected_target,
            "field": exp021_field_gate,
        }

        exp022_original_b = float(
            exp022_coverage["original"]["cells"]["B"]["tier3_or_4_state_coverage"]
        )
        exp022_quarantine_b = float(
            exp022_coverage["quarantine"]["cells"]["B"]["tier3_or_4_state_coverage"]
        )
        exp022_flip = exp022_original_b < 0.70 <= exp022_quarantine_b
        experiments["EXP-022"]["decision_sensitivity"].update(
            {
                "qualitative_branch_change": exp022_flip,
                "coverage_gate_point_status_changed": exp022_flip,
                "reason": (
                    "The held-out-only task removal changes the fixed-panel B coverage "
                    "point estimate across the historical 70% threshold. EXP-022 remains "
                    "immutable and failed; this does not retroactively pass it."
                    if exp022_flip
                    else "The fixed-panel B coverage threshold status is unchanged."
                ),
            }
        )
        exp023_original_b = float(
            exp023_coverage["original"]["B"]["tier3_or_4_state_coverage"]
        )
        exp023_quarantine_b = float(
            exp023_coverage["quarantine"]["B"]["tier3_or_4_state_coverage"]
        )
        exp023_status_changed = (exp023_original_b >= 0.70) != (
            exp023_quarantine_b >= 0.70
        )
        experiments["EXP-023"]["decision_sensitivity"].update(
            {
                "qualitative_branch_change": exp023_status_changed,
                "coverage_gate_point_status_changed": exp023_status_changed,
                "reason": "The complete-bank B coverage threshold status is unchanged."
                if not exp023_status_changed
                else "The complete-bank B coverage threshold status changed under quarantine.",
            }
        )
        for experiment in ("EXP-018", "EXP-019", "EXP-020", "EXP-021"):
            gate_payload = experiments[experiment]["central_gate_sensitivity"]
            gate_values = []

            def collect(value: Any) -> None:
                if isinstance(value, Mapping):
                    if "all_nonbootstrap_central_checks_pass" in value:
                        gate_values.append(bool(value["all_nonbootstrap_central_checks_pass"]))
                    for item in value.values():
                        collect(item)

            collect(gate_payload)
            all_candidates_fail = bool(gate_values) and not any(gate_values)
            experiments[experiment]["decision_sensitivity"].update(
                {
                    "qualitative_branch_change": False if all_candidates_fail else None,
                    "reason": (
                        "At least one mandatory non-bootstrap central gate remains failed "
                        "after removing the task; the prior blocked conclusion is unchanged."
                        if all_candidates_fail
                        else "Central checks alone are insufficient to reclassify the prior branch."
                    ),
                }
            )

        any_point_status_change = any(
            bool(payload["decision_sensitivity"].get("qualitative_branch_change"))
            for payload in experiments.values()
        )

        result = {
            "format": "provenance_quarantine_sensitivity_6h3_v1",
            "quarantined_task_id": task_id,
            "analysis_type": "existing_predictions_only_no_retraining",
            "preflight_branch": preflight["decision_branch"],
            "original_summary_hashes": summary_hashes,
            "experiments": experiments,
            "qualitative_conclusion": {
                "any_prior_gate_point_status_changed": any_point_status_change,
                "exp022_fixed_panel_coverage_flip_only": exp022_flip,
                "overall_research_conclusion_changed": False,
                "original_metrics_replaced": False,
                "failed_gate_retroactively_passed": False,
                "interpretation": (
                    "EXP-022 fixed-panel coverage may cross its historical point threshold, "
                    "but EXP-023 already superseded panel coverage with the full bank and the "
                    "representation/target conclusions remain blocked. Original records and "
                    "branches remain authoritative."
                ),
            },
            "qwen_import_forward_generation_count": 0,
            "model_training_count": 0,
        }
        output = args.artifact_dir / "prior_result_quarantine_sensitivity.json"
        atomic_write_json(output, result)
        attempt.progress(latest_validated_checkpoint=str(output))
        print(json.dumps(result["qualitative_conclusion"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
