from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    CalibrationCandidate,
    CalibratedFieldReaderHooks,
    preregistered_candidates,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    assert_frozen_without_gradients,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.prepare_rcmf_benefit_preserving_calibration_9b import (
    validate_immutable_inputs,
)
from scripts.run_rcmf_joint_full_bank_9a import _attempt_ids, _build_backend
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    CompleteFieldRuntime,
    RESULT_FORMAT as PARENT_RESULT_FORMAT,
    _condition_root,
    _run_task,
    _task_ids,
    _task_output,
    classify_first37,
    summarize_condition,
)

RESULT_FORMAT = "rcmf_benefit_preserving_first37_task_9b_v1"
SUMMARY_FORMAT = "rcmf_benefit_preserving_first37_condition_summary_9b_v1"
MANIFEST_FORMAT = "rcmf_benefit_preserving_first37_manifest_9b_v1"
PREFLIGHT_FORMAT = "rcmf_benefit_preserving_first37_preflight_9b_v1"
FINAL_FORMAT = "rcmf_benefit_preserving_first37_final_9b_v1"
PAIR_CONDITIONS = {
    "D1": "L1_correct_complete_field",
    "D2": "L1_key_payload_shuffled_complete_field",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/"
            "stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preflight", "smoke", "run", "finalize"), required=True
    )
    parser.add_argument("--condition", choices=sorted(PAIR_CONDITIONS))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031b_stage8d_l1")
    return parser.parse_args()


def _paths(artifact_dir: Path, settings_9b: Mapping[str, Any]) -> dict[str, Path]:
    parent = Path(str(settings_9b["immutable_exp031a"]["artifact_root"]))
    root = artifact_dir / "stage_8d_first37/L1"
    return {
        "root": root,
        "preflight": root / "runtime_preflight.json",
        "manifest": root / "condition_manifest.json",
        "final": root / "final_summary.json",
        "static_assets": root / "raw_audit/static_prompt_assets.json",
        "deployment": parent / "deployment_field/complete_37_task_field.pt",
        "instant_add": parent / "deployment_field/instant_add_report.json",
        "parent_root": parent,
        "parent_manifest": parent / "first37/condition_manifest.json",
        "parent_d0_summary": parent / "first37/conditions/D0/summary.json",
        "parent_d1_summary": parent / "first37/conditions/D1/summary.json",
        "parent_d2_summary": parent / "first37/conditions/D2/summary.json",
        "stage8b_summary": artifact_dir
        / "stage_8b_exact_prompt_v2/critical_live_summary.json",
        "stage8c_summary": artifact_dir
        / "stage_8c_heldout_live/heldout_live_summary.json",
        "audit_index": Path(str(settings_9b["immutable_exp031a"]["audit_index"])),
        "parent_config": Path(
            "configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"
        ),
    }


def _candidate(candidate_id: str = "L1") -> CalibrationCandidate:
    matches = [
        row for row in preregistered_candidates() if row.candidate_id == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one candidate {candidate_id}, found {len(matches)}")
    return matches[0]


def _write_or_validate_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if _json(path) != dict(payload):
            raise ValueError(f"Existing immutable JSON differs: {path}")
    else:
        atomic_write_json(path, dict(payload))


def _tree_bytes(path: Path) -> int:
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def _stage8_equivalence(paths: Mapping[str, Path]) -> dict[str, Any]:
    stage8b = _json(paths["stage8b_summary"])
    by_id = {
        str(row["candidate_id"]): row for row in stage8b["candidate_matrix"]
    }
    required = {"R0-original", "R0-bare", "G100", "L1"}
    if not required.issubset(by_id):
        raise RuntimeError("Stage8B equivalence controls are incomplete")
    original, bare, g100 = (
        by_id["R0-original"],
        by_id["R0-bare"],
        by_id["G100"],
    )
    if original["metrics"] != g100["metrics"]:
        raise RuntimeError("G100 does not reproduce R0-original metrics")
    if original["benefit_preservation"] != g100["benefit_preservation"]:
        raise RuntimeError("G100 does not reproduce R0-original benefit evidence")
    if float(bare["maximum_residual_ratio"]) != 0.0:
        raise RuntimeError("R0-bare did not produce a zero residual")
    if not bool(by_id["L1"]["benefit_preservation"]["passed"]):
        raise RuntimeError("L1 did not pass the Stage8B benefit gate")

    stage8c = _json(paths["stage8c_summary"])
    if str(stage8c["selected_first_candidate"]) != "L1":
        raise RuntimeError("Stage8C did not select L1 as the first candidate")
    if "L1" not in {str(value) for value in stage8c["eligible_candidate_ids"]}:
        raise RuntimeError("L1 is not Stage8C eligible")
    return {
        "stage8b_summary_sha256": sha256_file(paths["stage8b_summary"]),
        "stage8c_summary_sha256": sha256_file(paths["stage8c_summary"]),
        "g100_exact_control_metrics_equal": True,
        "g100_benefit_evidence_equal": True,
        "r0_bare_zero_residual": True,
        "l1_stage8b_benefit_gate": True,
        "l1_stage8c_eligible": True,
        "l1_stage8c_selected_first": True,
    }


def _validate_parent_d0(
    *,
    paths: Mapping[str, Path],
    settings_9a: Mapping[str, Any],
    settings_9b: Mapping[str, Any],
) -> dict[str, Any]:
    tasks = _task_ids(settings_9a)
    parent_config_sha = sha256_file(paths["parent_config"])
    parent_manifest = _json(paths["parent_manifest"])
    summary = _json(paths["parent_d0_summary"])
    audit = _json(paths["audit_index"])
    expected_success = sorted(str(value) for value in audit["first37"]["successes"]["D0"])
    if int(summary["success_count"]) != 8 or sorted(summary["success_ids"]) != expected_success:
        raise RuntimeError("Immutable D0 summary differs from the Git-safe audit index")
    if int(summary["task_count"]) != 37:
        raise RuntimeError("Immutable D0 task count differs")

    task_hashes: dict[str, str] = {}
    for task_id in tasks:
        path = (
            paths["parent_root"]
            / "first37/conditions/D0/task_results"
            / f"{task_id}.json"
        )
        row = _json(path)
        checks = {
            "format": row.get("format") == PARENT_RESULT_FORMAT,
            "status": row.get("status") == "complete",
            "task_id": str(row.get("task_id")) == task_id,
            "condition": str(row.get("condition")) == "D0",
            "seed": int(row.get("global_seed")) == GLOBAL_SEED,
            "config": str(row.get("config_sha256")) == parent_config_sha,
            "manifest": str(row.get("condition_manifest_sha256"))
            == str(parent_manifest["manifest_sha256"]),
            "same_world": all(
                bool(step["same_world_execution"]) for step in row["steps"]
            ),
            "same_namespace": all(
                bool(step["same_python_namespace"]) for step in row["steps"]
            ),
            "no_reader": all(
                not bool(step["reader_audit"]["active"]) for step in row["steps"]
            ),
            "no_query": all(
                step["field"]["query_status"] == "not_computed_bare_condition"
                for step in row["steps"]
            ),
            "no_retrieval": not bool(row["runtime_memory_retrieval"])
            and not bool(row["runtime_per_memory_scoring"]),
            "no_raw_memory": not bool(row["student_prompt_contains_raw_memory"]),
            "success_source": row["success_source"] == "evaluation.success",
        }
        if not all(checks.values()):
            raise RuntimeError(f"Immutable D0 task validation failed: {task_id} {checks}")
        task_hashes[task_id] = sha256_file(path)

    return {
        "reused": True,
        "task_count": 37,
        "success_count": 8,
        "success_ids": expected_success,
        "task_result_sha256": task_hashes,
        "summary_sha256": sha256_file(paths["parent_d0_summary"]),
        "condition_manifest_sha256": sha256_file(paths["parent_manifest"]),
        "condition_manifest_canonical_sha256": str(
            parent_manifest["manifest_sha256"]
        ),
        "parent_config_sha256": parent_config_sha,
        "same_first37_harness_function": (
            "scripts.run_rcmf_joint_full_bank_first37_9a._run_task"
        ),
        "same_appworld_settings": dict(settings_9a["appworld"]),
        "same_world_initialization_seed": GLOBAL_SEED,
        "same_evaluator_success_source": "evaluation.success",
        "exact_prompt_harness_evaluator_identity": True,
        "identical_world_initialization": True,
        "no_query_or_reader_hook": True,
        "immutable_hashes": validate_immutable_inputs(settings_9b)["hashes"],
        "stage8_equivalence": _stage8_equivalence(paths),
    }


def build_first37_manifest(
    *,
    paths: Mapping[str, Path],
    settings_9a: Mapping[str, Any],
    settings_9b: Mapping[str, Any],
) -> dict[str, Any]:
    tasks = _task_ids(settings_9a)
    candidate = _candidate()
    if candidate.critical_diagnostic_only:
        raise RuntimeError("Diagnostic-only candidate cannot enter first37")
    d0 = _validate_parent_d0(
        paths=paths, settings_9a=settings_9a, settings_9b=settings_9b
    )
    rows = [
        {
            "condition": condition,
            "condition_name": PAIR_CONDITIONS[condition],
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.as_dict(),
            "task_id": task_id,
            "memory_count": 499,
            "runtime_memory_retrieval": False,
            "runtime_per_memory_scoring": False,
            "student_prompt_contains_raw_memory": False,
            "fresh_identical_world": True,
        }
        for condition in PAIR_CONDITIONS
        for task_id in tasks
    ]
    payload = {
        "format": MANIFEST_FORMAT,
        "run_uuid": str(settings_9b["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "candidate_pair_index": 1,
        "maximum_candidate_pairs": int(
            settings_9b["candidates"]["maximum_first37_candidate_pairs"]
        ),
        "candidate": candidate.as_dict(),
        "task_ids": tasks,
        "task_count": len(tasks),
        "conditions": list(PAIR_CONDITIONS),
        "logical_condition_count": len(rows),
        "new_task_execution_count": len(rows),
        "immutable_d0_logical_references": len(tasks),
        "sequential_condition_order": ["D1", "D2"],
        "concurrent_candidate_pairs": False,
        "candidate_frozen_from_stage8c_before_first37_outcomes": True,
        "candidate_definition_changes_after_first37": False,
        "d0_reuse_evidence": d0,
        "deployment_field_sha256": str(
            settings_9b["immutable_exp031a"]["deployment_field_sha256"]
        ),
        "checkpoint_sha256": str(
            settings_9b["immutable_exp031a"]["checkpoint_sha256"]
        ),
        "complete_field_memory_count": 499,
        "exact_harness": {
            "appworld": "0.1.0",
            "max_steps": 50,
            "max_api_calls_per_interaction": 100,
            "prompt_profile": "full_demo",
            "max_context_turns": 40,
            "max_new_tokens": 512,
            "temperature": 0.0,
            "top_p": 1.0,
            "do_sample": False,
            "enable_thinking": False,
            "success_source": "evaluation.success",
        },
        "rows": rows,
        "frozen_before_generation": True,
        "first37_outcomes_used": False,
    }
    if len(rows) != 74 or len(
        {(row["condition"], row["task_id"]) for row in rows}
    ) != 74:
        raise RuntimeError("L1 first37 manifest accounting differs")
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _runtime_preflight(
    *,
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    settings_9b: Mapping[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    summaries = {
        condition: _json(paths[f"parent_{condition.lower()}_summary"])
        for condition in ("D1", "D2")
    }
    measured = {
        condition: float(row["total_wall_seconds"]) / 3600.0
        for condition, row in summaries.items()
    }
    expected = sum(measured.values())
    conservative = expected * float(
        settings_9b["first37"]["runtime_conservative_multiplier"]
    )
    threshold = float(settings_9b["runtime"]["single_batch_review_threshold_hours"])
    report = {
        "format": PREFLIGHT_FORMAT,
        "purpose": (
            "sequential L1 correct-versus-key-payload-shuffle complete first37 "
            "benefit-preservation audit"
        ),
        "run_uuid": str(settings_9b["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "source_commit": str(settings_9b["source_head"]),
        "execution_commit": None,
        "config_sha256": sha256_file(config_path),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "data_identity": {
            "task_count": 37,
            "memory_count": 499,
            "checkpoint_sha256": str(
                settings_9b["immutable_exp031a"]["checkpoint_sha256"]
            ),
            "deployment_field_sha256": str(
                settings_9b["immutable_exp031a"]["deployment_field_sha256"]
            ),
        },
        "candidate": manifest["candidate"],
        "hardware": (
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else "unavailable"
        ),
        "condition_sequence": ["D1", "D2"],
        "new_task_execution_count": 74,
        "immutable_d0_references": 37,
        "parent_measured_h100_hours": measured,
        "expected_h100_hours": expected,
        "conservative_h100_hours": conservative,
        "review_threshold_hours": threshold,
        "automatic_launch_allowed": conservative <= threshold,
        "estimated_artifact_bytes": sum(
            _tree_bytes(
                paths["parent_root"] / f"first37/conditions/{condition}"
            )
            for condition in ("D1", "D2")
        ),
        "checkpoint_restart_plan": {
            "append_only_attempts": True,
            "heartbeat": True,
            "atomic_task_results": True,
            "atomic_per_step_rows": True,
            "atomic_per_step_tensors": True,
            "hash_valid_complete_tasks_reused": True,
            "incomplete_task_restarts_in_fresh_world": True,
            "conditions_run_sequentially": True,
            "second_candidate_not_launched": True,
        },
        "d0_reuse_gate_passed": True,
        "no_retraining": True,
        "no_runtime_retrieval": True,
        "no_hard_gate": True,
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError(f"L1 first37 pair requires explicit runtime review: {report}")
    return report


def _candidate_hook(candidate: CalibrationCandidate):
    return lambda *, model, reader, slots: CalibratedFieldReaderHooks(
        model=model,
        reader=reader,
        slots=slots,
        layer_scales=candidate.layer_scales,
    )


def _validate_candidate_row(
    row: Mapping[str, Any],
    *,
    condition: str,
    candidate: CalibrationCandidate,
) -> None:
    checks = {
        "format": row.get("format") == RESULT_FORMAT,
        "candidate_id": str(row.get("candidate_id")) == candidate.candidate_id,
        "candidate": row.get("candidate") == candidate.as_dict(),
        "condition_name": str(row.get("condition_name"))
        == PAIR_CONDITIONS[condition],
        "pair_index": int(row.get("candidate_pair_index", -1)) == 1,
        "first37_outcomes_used": not bool(row.get("first37_outcomes_used")),
        "no_retrieval": not bool(row.get("runtime_memory_retrieval"))
        and not bool(row.get("runtime_per_memory_scoring")),
        "no_raw_memory": not bool(row.get("student_prompt_contains_raw_memory")),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Candidate task row validation failed: {checks}")


def _family_status(
    success_ids: set[str], settings_9b: Mapping[str, Any]
) -> dict[str, Any]:
    families = {
        name: [str(value) for value in values]
        for name, values in settings_9b["first37"]["gain_families"].items()
    }
    return {
        name: {
            "task_ids": values,
            "successful_task_ids": sorted(set(values) & success_ids),
            "represented": bool(set(values) & success_ids),
        }
        for name, values in families.items()
    }


def scientific_decision(
    *,
    d0_success: set[str],
    original_d1_success: set[str],
    correct_success: set[str],
    shuffled_success: set[str],
    settings_9b: Mapping[str, Any],
) -> dict[str, Any]:
    gains = {str(value) for value in settings_9b["critical_states"]["gains"]}
    retained = {str(value) for value in settings_9b["critical_states"]["retained"]}
    losses = {str(value) for value in settings_9b["critical_states"]["losses"]}
    preserved_gains = gains & correct_success
    preserved_retained = retained & correct_success
    recovered_losses = losses & correct_success
    family = _family_status(correct_success, settings_9b)
    new_net = correct_success - d0_success - gains
    new_net_families = {value.rsplit("_", 1)[0] for value in new_net}
    equivalent_new_net = len(new_net) >= 2 and len(new_net_families) >= 2
    gates = {
        "correct_at_least_10": len(correct_success) >= 10,
        "correct_at_least_2_over_d0": len(correct_success) - len(d0_success) >= 2,
        "correct_at_least_2_over_shuffle": (
            len(correct_success) - len(shuffled_success) >= 2
        ),
        "preserve_at_least_5_of_6_gains": len(preserved_gains) >= 5,
        "all_three_gain_families": all(
            bool(row["represented"]) for row in family.values()
        ),
        "both_retained_successes": preserved_retained == retained,
        "recover_two_losses_or_equivalent_new_net": (
            len(recovered_losses) >= 2 or equivalent_new_net
        ),
        "no_scientific_shortcut": True,
    }
    stop_reasons = {
        "correct_not_above_shuffle": len(correct_success) <= len(shuffled_success),
        "two_or_more_original_gains_lost": len(gains - correct_success) >= 2,
        "retained_success_lost": preserved_retained != retained,
        "constant_time_contract_violated": False,
    }
    if all(gates.values()):
        decision = "PROCEED_preliminary_benefit_preserving_calibration"
    elif any(stop_reasons.values()):
        decision = "STOP_ROUTE"
    else:
        decision = "INCONCLUSIVE"
    margins = {
        "success_over_10": len(correct_success) - 10,
        "success_over_d0_plus_2": len(correct_success) - len(d0_success) - 2,
        "success_over_shuffle_plus_2": (
            len(correct_success) - len(shuffled_success) - 2
        ),
        "preserved_gains_over_5": len(preserved_gains) - 5,
    }
    return {
        "scientific_decision": decision,
        "gates": gates,
        "stop_reasons": stop_reasons,
        "family_preservation": family,
        "preserved_original_gains": sorted(preserved_gains),
        "lost_original_gains": sorted(gains - correct_success),
        "preserved_retained_successes": sorted(preserved_retained),
        "lost_retained_successes": sorted(retained - correct_success),
        "recovered_original_d1_losses": sorted(recovered_losses),
        "equivalent_new_net_gains": sorted(new_net),
        "equivalent_new_net_gain_families": sorted(new_net_families),
        "one_task_sensitivity_margins": margins,
        "one_task_change_can_flip_primary_count_gate": any(
            value == 0 for value in margins.values()
        ),
        "original_d1_success_count": len(original_d1_success),
    }


def _finalize(
    *,
    paths: Mapping[str, Path],
    settings_9a: Mapping[str, Any],
    settings_9b: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    tasks = _task_ids(settings_9a)
    parent_rows = {
        condition: {
            task: _json(
                paths["parent_root"]
                / f"first37/conditions/{condition}/task_results/{task}.json"
            )
            for task in tasks
        }
        for condition in ("D0", "D1", "D2")
    }
    rows = {
        condition: {
            task: _json(_task_output(paths, condition, task, False))
            for task in tasks
        }
        for condition in PAIR_CONDITIONS
    }
    candidate = _candidate()
    for condition in PAIR_CONDITIONS:
        for row in rows[condition].values():
            _validate_candidate_row(row, condition=condition, candidate=candidate)

    success = {
        "original_D0": {
            task for task, row in parent_rows["D0"].items() if bool(row["success"])
        },
        "original_D1": {
            task for task, row in parent_rows["D1"].items() if bool(row["success"])
        },
        "original_D2": {
            task for task, row in parent_rows["D2"].items() if bool(row["success"])
        },
        "L1_correct": {
            task for task, row in rows["D1"].items() if bool(row["success"])
        },
        "L1_shuffle": {
            task for task, row in rows["D2"].items() if bool(row["success"])
        },
    }
    decision = scientific_decision(
        d0_success=success["original_D0"],
        original_d1_success=success["original_D1"],
        correct_success=success["L1_correct"],
        shuffled_success=success["L1_shuffle"],
        settings_9b=settings_9b,
    )
    per_task = []
    for task in tasks:
        per_task.append(
            {
                "task_id": task,
                "success": {
                    "original_D0": bool(parent_rows["D0"][task]["success"]),
                    "original_D1": bool(parent_rows["D1"][task]["success"]),
                    "original_D2": bool(parent_rows["D2"][task]["success"]),
                    "L1_correct": bool(rows["D1"][task]["success"]),
                    "L1_shuffle": bool(rows["D2"][task]["success"]),
                },
                "step_count": {
                    "original_D0": int(parent_rows["D0"][task]["step_count"]),
                    "original_D1": int(parent_rows["D1"][task]["step_count"]),
                    "original_D2": int(parent_rows["D2"][task]["step_count"]),
                    "L1_correct": int(rows["D1"][task]["step_count"]),
                    "L1_shuffle": int(rows["D2"][task]["step_count"]),
                },
                "execution_exceptions": {
                    "L1_correct": int(
                        rows["D1"][task]["counts"].get("execution_exception", 0)
                    ),
                    "L1_shuffle": int(
                        rows["D2"][task]["counts"].get("execution_exception", 0)
                    ),
                },
                "terminal_error": {
                    "L1_correct": rows["D1"][task]["terminal_error"],
                    "L1_shuffle": rows["D2"][task]["terminal_error"],
                },
            }
        )

    summaries = {
        condition: _json(_condition_root(paths, condition, False) / "summary.json")
        for condition in PAIR_CONDITIONS
    }
    counts = {name: len(values) for name, values in success.items()}
    payload = {
        "format": FINAL_FORMAT,
        "run_uuid": str(settings_9b["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "candidate_pair_index": 1,
        "candidate": candidate.as_dict(),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "success_count": counts,
        "success_ids": {name: sorted(values) for name, values in success.items()},
        "L1_correct_minus_D0": counts["L1_correct"] - counts["original_D0"],
        "L1_correct_minus_shuffle": counts["L1_correct"] - counts["L1_shuffle"],
        "retained_from_D0": sorted(
            success["original_D0"] & success["L1_correct"]
        ),
        "gained_over_D0": sorted(
            success["L1_correct"] - success["original_D0"]
        ),
        "lost_from_D0": sorted(
            success["original_D0"] - success["L1_correct"]
        ),
        "newly_lost_vs_original_D1": sorted(
            success["original_D1"] - success["L1_correct"]
        ),
        "newly_gained_vs_original_D1": sorted(
            success["L1_correct"] - success["original_D1"]
        ),
        "condition_summaries": summaries,
        "per_task": per_task,
        "mechanical_exp031a_label": classify_first37(
            counts["original_D0"], counts["L1_correct"], counts["L1_shuffle"]
        ),
        **decision,
        "single_seed_development_result": True,
        "no_statistical_generalization_claim": True,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "hard_memory_gate": False,
        "second_candidate_pair_run": False,
    }
    payload["summary_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["final"], payload)
    atomic_write_text(
        paths["final"].with_suffix(".md"),
        "\n".join(
            [
                "# EXP-031B L1 first37",
                "",
                f"- correct: {counts['L1_correct']}/37",
                f"- shuffled: {counts['L1_shuffle']}/37",
                f"- locked D0: {counts['original_D0']}/37",
                f"- scientific decision: {payload['scientific_decision']}",
                f"- mechanical label: {payload['mechanical_exp031a_label']['decision_branch']}",
                "",
            ]
        ),
    )
    return payload


def _ledger(
    *,
    args: argparse.Namespace,
    settings_9b: Mapping[str, Any],
    phase: str,
    hashes: Mapping[str, str],
) -> AttemptLedger:
    return AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings_9b["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=phase,
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=dict(hashes),
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(
            settings_9b["runtime"]["heartbeat_interval_seconds"]
        ),
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings_9a = cfg.raw["stage_c_9a"]
    settings_9b = cfg.raw["stage_c_9b"]
    paths = _paths(args.artifact_dir, settings_9b)
    if int(settings_9b["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-031B seed differs")
    if os.name != "nt" and not os.path.ismount(
        str(settings_9b["persistent_root"])
    ):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    immutable = validate_immutable_inputs(settings_9b)
    hashes = {
        "config": sha256_file(args.config),
        "checkpoint": immutable["hashes"]["checkpoint"],
        "deployment": immutable["hashes"]["deployment_field"],
        "attempt_ledger": immutable["hashes"]["attempt_ledger"],
        "audit_index": immutable["hashes"]["audit_index"],
        "stage8b_summary": sha256_file(paths["stage8b_summary"]),
        "stage8c_summary": sha256_file(paths["stage8c_summary"]),
    }

    if args.phase == "preflight":
        manifest = build_first37_manifest(
            paths=paths, settings_9a=settings_9a, settings_9b=settings_9b
        )
        report = _runtime_preflight(
            paths=paths,
            manifest=manifest,
            settings_9b=settings_9b,
            config_path=args.config,
        )
        report["execution_commit"] = args.lambda_head
        with _ledger(
            args=args,
            settings_9b=settings_9b,
            phase="stage_8d_first37_l1_preflight",
            hashes=hashes,
        ) as attempt:
            _write_or_validate_json(paths["manifest"], manifest)
            _write_or_validate_json(paths["preflight"], report)
            attempt.progress(
                status="stage_8d_first37_l1_preflight_complete",
                latest_validated_checkpoint=str(paths["preflight"]),
                result=report,
            )
        print(json.dumps(report, sort_keys=True))
        return

    if not paths["manifest"].exists() or not paths["preflight"].exists():
        raise RuntimeError("Frozen Stage8D manifest/preflight is missing")
    manifest, preflight = _json(paths["manifest"]), _json(paths["preflight"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("Stage8D launch was not authorized")
    hashes["manifest"] = sha256_file(paths["manifest"])

    if args.phase == "finalize":
        for condition in PAIR_CONDITIONS:
            if not (_condition_root(paths, condition, False) / "summary.json").exists():
                raise RuntimeError(f"Cannot finalize before {condition} completes")
        with _ledger(
            args=args,
            settings_9b=settings_9b,
            phase="stage_8d_first37_l1_finalize",
            hashes=hashes,
        ) as attempt:
            result = _finalize(
                paths=paths,
                settings_9a=settings_9a,
                settings_9b=settings_9b,
                manifest=manifest,
            )
            attempt.progress(
                status="stage_8d_first37_l1_finalize_complete",
                latest_validated_checkpoint=str(paths["final"]),
                result=result,
            )
        print(json.dumps(result, sort_keys=True))
        return

    if args.condition is None:
        raise ValueError("--condition is required for smoke/run")
    if args.condition == "D2" and args.phase == "run":
        d1_summary = _condition_root(paths, "D1", False) / "summary.json"
        if not d1_summary.exists():
            raise RuntimeError("Matched shuffle cannot start before L1 correct completes")

    candidate = _candidate()
    tasks = _task_ids(settings_9a)
    selected_tasks = tasks[:1] if args.phase == "smoke" else tasks
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Stage8D loaded trainable Qwen")
    runtime = CompleteFieldRuntime(
        settings=settings_9a,
        backend=backend,
        deployment_path=paths["deployment"],
        instant_add_path=paths["instant_add"],
    )
    hashes["query_encoder"] = runtime.query_encoder.identity_sha256
    smoke = args.phase == "smoke"
    hook = _candidate_hook(candidate)
    extra = {
        "condition_name": PAIR_CONDITIONS[args.condition],
        "candidate_id": candidate.candidate_id,
        "candidate": candidate.as_dict(),
        "candidate_pair_index": 1,
        "stage8c_selection_sha256": hashes["stage8c_summary"],
        "first37_outcomes_used": False,
        "candidate_definition_changed_after_first37": False,
    }
    with _ledger(
        args=args,
        settings_9b=settings_9b,
        phase=f"stage_8d_first37_l1_{args.phase}_{args.condition.lower()}",
        hashes=hashes,
    ) as attempt:
        rows, resumed = [], 0
        for task_id in selected_tasks:
            row, reused = _run_task(
                task_id=task_id,
                condition=args.condition,
                settings=settings_9a,
                backend=backend,
                runtime=runtime,
                paths=paths,
                manifest=manifest,
                config_sha256=sha256_file(args.config),
                attempt_id=args.attempt_id,
                smoke=smoke,
                hook_factory=hook,
                result_version=RESULT_FORMAT,
                extra_result_fields=extra,
            )
            _validate_candidate_row(
                row, condition=args.condition, candidate=candidate
            )
            rows.append(row)
            resumed += int(reused)
            attempt.progress(
                status=f"stage_8d_first37_l1_{args.phase}_{args.condition.lower()}",
                completed_tasks=len(rows),
                total_tasks=len(selected_tasks),
                resumed_tasks=resumed,
                latest_validated_checkpoint=str(
                    _task_output(paths, args.condition, task_id, smoke)
                ),
            )
            print(
                f"{args.condition} task={task_id} success={row['success']} "
                f"steps={row['step_count']} reused={reused}",
                flush=True,
            )
        summary = summarize_condition(rows, args.condition)
        summary.update(
            {
                "format": SUMMARY_FORMAT,
                "condition_name": PAIR_CONDITIONS[args.condition],
                "candidate_id": candidate.candidate_id,
                "candidate": candidate.as_dict(),
                "run_uuid": str(settings_9b["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "non_scientific_smoke": smoke,
                "manifest_sha256": str(manifest["manifest_sha256"]),
                "deployment_field_sha256": hashes["deployment"],
                "checkpoint_sha256": hashes["checkpoint"],
                "query_encoder_sha256": runtime.query_encoder.identity_sha256,
                "new_task_count": len(rows) - resumed,
                "resumed_task_count": resumed,
                "first37_outcomes_used_for_candidate_definition": False,
            }
        )
        if smoke:
            summary["passed_infrastructure"] = (
                len(rows) == 1 and all(row["status"] == "complete" for row in rows)
            )
        summary_path = _condition_root(paths, args.condition, smoke) / "summary.json"
        atomic_write_json(summary_path, summary)
        attempt.progress(
            status=f"stage_8d_first37_l1_{args.phase}_{args.condition.lower()}_complete",
            latest_validated_checkpoint=str(summary_path),
            result=summary,
        )
    assert_frozen_without_gradients(backend.model)
    assert_frozen_without_gradients(runtime.reader)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
