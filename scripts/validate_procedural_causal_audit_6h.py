from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.procedural_causal_audit_6h import GENERATION_RESULT_VERSION
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_procedural_causal_audit_6h.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    base_required = (
        "run_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
        "signature_equivalence_manifest.json",
        "audit_state_strata.json",
        "condition_manifest.json",
        "condition_prompt_preflight.jsonl",
        "preflight_summary.json",
        "replay/replay_summary.json",
        "final_exp024a_summary.json",
        "final_exp024a_report.md",
    )
    checks: dict[str, bool] = {
        f"required:{name}": (args.artifact_dir / name).exists()
        for name in base_required
    }
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise FileNotFoundError(f"Missing post-run artifacts: {failed}")

    replay = _load_json(args.artifact_dir / "replay" / "replay_summary.json")
    final = _load_json(args.artifact_dir / "final_exp024a_summary.json")
    replay_failure_stop = (
        final["decision"]["decision_branch"]
        == "appworld_one_step_replay_invalid"
    )
    branch_required = (
        ("replay_failure_diagnostics.json", "replay_failure_report.md")
        if replay_failure_stop
        else (
            "generation_summary.json",
            "one_step_metrics.json",
            "causal_comparisons.json",
            "same_signature_consistency.json",
            "raw_nll_behavior_relationship.json",
        )
    )
    checks.update(
        {
            f"required:{name}": (args.artifact_dir / name).exists()
            for name in branch_required
        }
    )
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise FileNotFoundError(f"Missing branch artifacts: {failed}")

    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    equivalence = _load_json(
        args.artifact_dir / "signature_equivalence_manifest.json"
    )
    strata = _load_json(args.artifact_dir / "audit_state_strata.json")
    conditions = _load_json(args.artifact_dir / "condition_manifest.json")
    preflight_rows = list(
        read_jsonl(args.artifact_dir / "condition_prompt_preflight.jsonl")
    )
    preflight = _load_json(args.artifact_dir / "preflight_summary.json")
    generation = (
        None
        if replay_failure_stop
        else _load_json(args.artifact_dir / "generation_summary.json")
    )
    attempts = list(read_jsonl(args.artifact_dir / "attempts.jsonl"))
    expected = settings["expected"]

    checks.update(
        {
            "run_uuid": run_manifest["run_uuid"] == settings["run_uuid"],
            "source_head": run_manifest["initial_source_commit"]
            == args.expected_head,
            "config_hash": run_manifest["config_sha256"]
            == sha256_file(args.config),
            "transition_count": equivalence["transition_count"]
            == expected["transitions"],
            "signature_class_count": equivalence["signature_class_count"]
            == expected["unique_signatures"],
            "duplicate_transition_count": equivalence[
                "duplicate_transition_count"
            ]
            == expected["duplicate_transitions"],
            "api_documentation_transition_count": equivalence[
                "api_documentation_transition_count"
            ]
            == expected["api_documentation_transitions"],
            "class_member_partition": sum(
                int(row["class_size"]) for row in equivalence["classes"]
            )
            == expected["transitions"],
            "class_unique_members": len(
                {
                    transition_id
                    for row in equivalence["classes"]
                    for transition_id in row["member_transition_ids"]
                }
            )
            == expected["transitions"],
            "audit_state_count": strata["state_count"]
            == expected["one_step_audit_states"],
            "primary_state_gate": strata[
                "primary_non_documentation_high_tier_state_count"
            ]
            >= settings["selection"]["minimum_primary_states"],
            "primary_task_gate": strata[
                "primary_non_documentation_high_tier_task_count"
            ]
            >= settings["selection"]["minimum_primary_tasks"],
            "condition_unique_keys": len(
                {row["condition_key"] for row in conditions["conditions"]}
            )
            == conditions["condition_count"],
            "preflight_condition_identity": {
                row["condition_key"] for row in preflight_rows
            }
            == {
                row["condition_key"] for row in conditions["conditions"]
            },
            "no_prompt_truncation": all(
                not bool(row["truncated"]) for row in preflight_rows
            ),
            "preflight_no_qwen_forward": preflight["gpu_forward_count"] == 0,
            "preflight_no_appworld": preflight["appworld_instance_count"] == 0,
            "replay_all_states": replay["state_count"]
            == expected["one_step_audit_states"],
            "field_training_blocked": bool(
                final["decision"]["field_training_remains_blocked"]
            ),
        }
    )

    if replay_failure_stop:
        diagnostic = _load_json(
            args.artifact_dir / "replay_failure_diagnostics.json"
        )
        checks.update(
            {
                "replay_gate_failed": not bool(replay["all_states_passed"]),
                "replay_failure_present": replay["failed_state_count"] > 0,
                "diagnostic_state_count": diagnostic["replay_diagnostics"][
                    "state_count"
                ]
                == expected["one_step_audit_states"],
                "diagnostic_failure_count": diagnostic["replay_diagnostics"][
                    "failed_state_count"
                ]
                == replay["failed_state_count"],
                "diagnostic_branch": diagnostic["decision_branch"]
                == "appworld_one_step_replay_invalid",
                "appworld_provenance_recorded": bool(
                    diagnostic["appworld_provenance"]["installed"][
                        "package_version"
                    ]
                )
                and diagnostic["appworld_provenance"]["source"]["task_count"]
                == 9,
                "version_comparison_recorded": isinstance(
                    diagnostic["appworld_provenance"][
                        "installed_matches_source_versions"
                    ],
                    bool,
                ),
                "no_generation_summary": not (
                    args.artifact_dir / "generation_summary.json"
                ).exists(),
                "final_zero_generations": final[
                    "actual_qwen_generation_count"
                ]
                == 0,
                "final_zero_h100": float(final["actual_h100_hours"]) == 0.0,
                "final_planned_condition_count": final[
                    "planned_condition_count"
                ]
                == conditions["condition_count"],
                "final_actual_condition_count": final["actual_condition_count"]
                == 0,
            }
        )
    else:
        assert generation is not None
        checks.update(
            {
                "replay_all_passed": bool(replay["all_states_passed"]),
                "generation_complete": bool(generation["all_complete"]),
                "generation_count": generation["condition_count"]
                == conditions["condition_count"],
                "generation_unique": generation["unique_condition_key_count"]
                == conditions["condition_count"],
                "final_condition_count": final["condition_count"]
                == conditions["condition_count"],
                "final_replay_passed": bool(
                    final["replay"]["all_states_passed"]
                ),
            }
        )

    class_by_id = {
        row["signature_class_id"]: row for row in equivalence["classes"]
    }
    for row in conditions["conditions"]:
        if row["condition_name"] == "C1_raw_oracle":
            checks[f"canonical_oracle:{row['condition_key']}"] = (
                row["transition_id"]
                == class_by_id[row["signature_class_id"]][
                    "canonical_transition_id"
                ]
            )
        if row["condition_name"] == "C6_alternate_same_signature":
            checks[f"fixed_alternate:{row['condition_key']}"] = (
                row["transition_id"]
                == class_by_id[row["signature_class_id"]][
                    "alternate_transition_id"
                ]
            )

    output_paths = sorted((args.artifact_dir / "condition_outputs").glob("*.json"))
    output_rows = [_load_json(path) for path in output_paths]
    if replay_failure_stop:
        checks["no_condition_outputs"] = not output_rows
    else:
        checks["one_output_per_condition"] = len(output_rows) == conditions[
            "condition_count"
        ]
        checks["output_keys_exact"] = {
            row["condition_key"] for row in output_rows
        } == {row["condition_key"] for row in conditions["conditions"]}
        checks["output_format"] = all(
            row["format"] == GENERATION_RESULT_VERSION for row in output_rows
        )
        checks["frozen_qwen_identity"] = all(
            row["model_name"] == settings["generation"]["model_name"]
            and not bool(row["do_sample"])
            and not bool(row["enable_thinking"])
            and float(row["temperature"]) == 0.0
            for row in output_rows
        )
        checks["fresh_environment_hashes"] = all(
            bool(row["environment_reconstruction_sha256"])
            and bool(row["history_replay_match"])
            for row in output_rows
        )

    start_counts = Counter(
        row["attempt_id"] for row in attempts if row["event"] == "start"
    )
    end_counts = Counter(
        row["attempt_id"] for row in attempts if row["event"] == "end"
    )
    checks["attempt_start_end_pairing"] = start_counts == end_counts and all(
        value == 1 for value in start_counts.values()
    )
    end_rows = [row for row in attempts if row["event"] == "end"]
    checks["attempt_exit_codes_recorded"] = all(
        isinstance(row.get("exit_code"), int) for row in end_rows
    )
    if replay_failure_stop:
        checks["replay_gate_failure_in_ledger"] = any(
            "appworld_one_step_replay_invalid" in str(row.get("stop_reason"))
            for row in end_rows
        )
        checks["finalization_attempt_succeeded"] = any(
            row["phase"] == "replay_failure_diagnosis_and_finalization"
            and int(row["exit_code"]) == 0
            for row in end_rows
        )
    else:
        checks["analysis_attempt_succeeded"] = any(
            row["phase"] == "causal_metrics_and_scientific_gate"
            and int(row["exit_code"]) == 0
            for row in end_rows
        )
    checks["attempt_no_scientific_change"] = all(
        not bool(row["scientific_parameter_changed"]) for row in attempts
    )

    passed = all(checks.values())
    payload = {
        "format": "procedural_causal_postrun_validation_6h_v1",
        "passed": passed,
        "check_count": len(checks),
        "passed_count": sum(checks.values()),
        "failed_checks": [name for name, value in checks.items() if not value],
        "checks": checks,
        "attempt_count": len(start_counts),
        "condition_count": len(output_rows),
        "decision_branch": final["decision"]["decision_branch"],
    }
    atomic_write_json(args.artifact_dir / "postrun_validation.json", payload)
    atomic_write_text(
        args.artifact_dir / "postrun_validation.md",
        "# EXP-024A Post-run Validation\n\n"
        f"Passed: {passed}\n\n"
        f"Checks: {payload['passed_count']}/{payload['check_count']}\n\n"
        f"Failed: {payload['failed_checks']}\n",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
