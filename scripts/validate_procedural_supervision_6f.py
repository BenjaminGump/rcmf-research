from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.training.procedural_supervision_6f import summarize_label_coverage
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.prepare_procedural_supervision_6f import (
    _signature_credential_leakage_paths,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ledger_checks(rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["attempt_id"])].append(row)
    return {
        "attempt_ids_unique_by_event": all(
            len({str(row["event"]) for row in values}) == len(values)
            for values in grouped.values()
        ),
        "attempts_have_one_start": all(
            sum(row.get("event") == "start" for row in values) == 1
            for values in grouped.values()
        ),
        "attempts_have_one_end": all(
            sum(row.get("event") == "end" for row in values) == 1
            for values in grouped.values()
        ),
        "one_run_uuid": len({str(row["run_uuid"]) for row in rows}) == 1,
        "no_scientific_parameter_change": not any(
            bool(row.get("scientific_parameter_changed")) for row in rows
        ),
    }


def _coverage_details(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    all_states: dict[str, Mapping[str, Any]] = {}
    all_transitions: dict[str, Mapping[str, Any]] = {}
    for cell in "ABCD":
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        selected = [row for row in rows if str(row["cell"]) == cell]
        for row in selected:
            grouped[str(row["state_example_id"])].append(row)
            all_states.setdefault(str(row["state_example_id"]), row)
            all_transitions.setdefault(str(row["transition_id"]), row)
        missing = []
        task_coverage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"states": 0, "states_with_tier3_or_4": 0}
        )
        for state_id, state_rows in sorted(grouped.items()):
            first = state_rows[0]
            maximum = max(int(row["procedural_tier"]) for row in state_rows)
            task = str(first["state_task_id"])
            task_coverage[task]["states"] += 1
            if maximum >= 3:
                task_coverage[task]["states_with_tier3_or_4"] += 1
            else:
                missing.append(
                    {
                        "state_example_id": state_id,
                        "task_id": task,
                        "query_primary_app": str(first["query_primary_app"]),
                        "query_primary_api": str(first["query_primary_api"]),
                        "query_coarse_action_type": str(
                            first["query_coarse_action_type"]
                        ),
                        "maximum_tier": maximum,
                        "has_exact_api_sequence": any(
                            bool(row["exact_api_sequence"]) for row in state_rows
                        ),
                    }
                )
        unique_states = [values[0] for values in grouped.values()]
        cells[cell] = {
            "states_without_tier3_or_4": missing,
            "states_without_tier3_or_4_count": len(missing),
            "query_primary_app_state_counts": dict(
                sorted(Counter(str(row["query_primary_app"]) for row in unique_states).items())
            ),
            "query_primary_api_state_counts": dict(
                sorted(Counter(str(row["query_primary_api"]) for row in unique_states).items())
            ),
            "query_action_type_state_counts": dict(
                sorted(
                    Counter(
                        str(row["query_coarse_action_type"])
                        for row in unique_states
                    ).items()
                )
            ),
            "task_coverage": dict(sorted(task_coverage.items())),
        }
    cells["panel"] = {
        "transition_primary_app_counts": dict(
            sorted(
                Counter(
                    str(row["transition_primary_app"])
                    for row in all_transitions.values()
                ).items()
            )
        ),
        "transition_primary_api_counts": dict(
            sorted(
                Counter(
                    str(row["transition_primary_api"])
                    for row in all_transitions.values()
                ).items()
            )
        ),
        "transition_action_type_counts": dict(
            sorted(
                Counter(
                    str(row["transition_coarse_action_type"])
                    for row in all_transitions.values()
                ).items()
            )
        ),
    }
    return cells


def _report(result: Mapping[str, Any]) -> str:
    gate = result["gate"]
    coverage = result["coverage_details"]
    missing = coverage["B"]["states_without_tier3_or_4"]
    lines = [
        "# EXP-022 Procedural Supervision Validation",
        "",
        f"- passed: `{result['passed']}`",
        f"- decision branch: `{result['decision_branch']}`",
        f"- checks: `{sum(result['checks'].values())}/{len(result['checks'])}`",
        f"- scoreable labels: `{result['counts']['label_rows']}`",
        f"- signatures: `{result['counts']['query_signatures']}` query / "
        f"`{result['counts']['transition_signatures']}` transition",
        f"- held-out Tier-3/4 coverage: `{gate['observed']:.6f}` "
        f"(`{gate['states_with_tier3_or_4']}/{gate['state_count']}`), threshold "
        f"`{gate['threshold']:.2f}`",
        "",
        "## Tier Counts",
        "",
        "| Cell | Tier 0 | Tier 1 | Tier 2 | Tier 3 | Tier 4 | High-tier states |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in "ABCD":
        item = result["label_coverage"]["cells"][cell]
        tiers = item["tier_counts"]
        lines.append(
            f"| {cell} | {tiers.get('0', tiers.get(0, 0))} | "
            f"{tiers.get('1', tiers.get(1, 0))} | "
            f"{tiers.get('2', tiers.get(2, 0))} | "
            f"{tiers.get('3', tiers.get(3, 0))} | "
            f"{tiers.get('4', tiers.get(4, 0))} | "
            f"{item['states_with_tier3_or_4']}/{item['state_count']} |"
        )
    lines.extend(["", "## Held-out Coverage Gaps", ""])
    lines.extend(
        f"- `{row['state_example_id']}` ({row['query_primary_app']}."
        f"{row['query_primary_api']}, {row['query_coarse_action_type']}): "
        f"maximum tier {row['maximum_tier']}"
        for row in missing
    )
    lines.extend(
        [
            "",
            "## Stop Decision",
            "",
            "The preregistered 70% coverage gate failed, so field-model training, "
            "AppWorld replay preflight, and one-step generation were not run. No "
            "query or transition was substituted and the fixed 148-transition "
            "panel was not expanded.",
            "",
            "## Errors",
            "",
            *([f"- {value}" for value in result["errors"]] or ["- none"]),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate stopped EXP-022 preflight")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifact_dir
    required = [
        "run_manifest.json",
        "stage_c_6f_settings.json",
        "preflight_summary.json",
        "procedural_signatures.jsonl",
        "procedural_label_rows.jsonl",
        "one_step_query_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
    ]
    errors = [f"missing:{name}" for name in required if not (root / name).exists()]
    checks: dict[str, bool] = {"required_artifacts": not errors}
    result: dict[str, Any] = {
        "format": "procedural_supervision_postrun_validation_6f_v1",
        "passed": False,
        "decision_branch": "transition_panel_procedural_coverage_insufficient",
        "checks": checks,
        "errors": errors,
    }
    if not errors:
        summary = _load_json(root / "preflight_summary.json")
        signatures = list(read_jsonl(root / "procedural_signatures.jsonl"))
        labels = list(read_jsonl(root / "procedural_label_rows.jsonl"))
        behavior = _load_json(root / "one_step_query_manifest.json")
        attempts = list(read_jsonl(root / "attempts.jsonl"))
        kind_counts = Counter(str(row["kind"]) for row in signatures)
        cell_counts = Counter(str(row["cell"]) for row in labels)
        recomputed_coverage = summarize_label_coverage(labels)
        details = _coverage_details(labels)
        b_coverage = recomputed_coverage["cells"]["B"]
        expected_cells = {"A": 8205, "B": 2051, "C": 2296, "D": 576}
        checks.update(
            {
                "immutable_contract": summary["immutable_contract"]
                == {
                    "queries": 92,
                    "train_queries": 74,
                    "heldout_queries": 18,
                    "transitions": 148,
                    "legal_rows": 13320,
                    "scoreable_rows": 13128,
                    "over_context_rows": 192,
                    "cell_counts": expected_cells,
                },
                "signature_counts": kind_counts
                == {"query": 638, "transition": 148},
                "signature_ids_unique": len(
                    {
                        (
                            str(row["kind"]),
                            str(
                                row.get("state_example_id")
                                or row.get("transition_id")
                            ),
                        )
                        for row in signatures
                    }
                )
                == len(signatures),
                "signature_hash_preserved": sha256_file(
                    root / "procedural_signatures.jsonl"
                )
                == summary["signatures"]["signature_rows_sha256"],
                "all_actions_parsed": all(
                    (
                        row["target_signature"]["parse_status"]
                        if row["kind"] == "query"
                        else row["action_signature"]["parse_status"]
                    )
                    in {"ast", "regex_fallback"}
                    for row in signatures
                ),
                "no_signature_credentials": not any(
                    _signature_credential_leakage_paths(row) for row in signatures
                ),
                "label_count_and_cells": len(labels) == 13128
                and dict(cell_counts) == expected_cells,
                "pair_ids_unique": len({str(row["pair_id"]) for row in labels})
                == len(labels),
                "tiers_in_range": all(
                    int(row["procedural_tier"]) in {0, 1, 2, 3, 4}
                    for row in labels
                ),
                "label_hash_preserved": sha256_file(
                    root / "procedural_label_rows.jsonl"
                )
                == summary["labels"]["rows_sha256"],
                "coverage_gate_arithmetic": (
                    b_coverage["state_count"] == 18
                    and b_coverage["states_with_tier3_or_4"] == 12
                    and abs(b_coverage["tier3_or_4_state_coverage"] - 2.0 / 3.0)
                    < 1.0e-12
                ),
                "registered_stop_branch": summary["status"]
                == "transition_panel_procedural_coverage_insufficient"
                and not bool(summary["gate"]["passed"]),
                "behavior_manifest_is_preselected_only": behavior["query_count"]
                == 45
                and behavior["exp020_subset_count"] == 18
                and len({str(row["state_example_id"]) for row in behavior["rows"]})
                == 45
                and set(Counter(str(row["task_id"]) for row in behavior["rows"]).values())
                == {5},
                "no_model_or_replay_artifacts": not any(
                    (root / name).exists()
                    for name in (
                        "model_summary.json",
                        "replay_preflight_summary.json",
                        "one_step_behavioral_audit.json",
                    )
                ),
                **_attempt_ledger_checks(attempts),
            }
        )
        errors.extend(name for name, passed in checks.items() if not passed)
        result.update(
            {
                "passed": not errors,
                "checks": checks,
                "errors": errors,
                "counts": {
                    "label_rows": len(labels),
                    "query_signatures": kind_counts["query"],
                    "transition_signatures": kind_counts["transition"],
                    "behavior_manifest_states_not_executed": behavior["query_count"],
                    "attempt_rows": len(attempts),
                    "attempts": len({str(row["attempt_id"]) for row in attempts}),
                },
                "gate": {
                    "observed": b_coverage["tier3_or_4_state_coverage"],
                    "threshold": summary["gate"]["threshold"],
                    "passed": False,
                    "states_with_tier3_or_4": b_coverage[
                        "states_with_tier3_or_4"
                    ],
                    "state_count": b_coverage["state_count"],
                },
                "label_coverage": recomputed_coverage,
                "coverage_details": details,
                "hard_same_intent_pairs": summary["labels"][
                    "hard_same_intent_pairs"
                ],
                "execution": {
                    "qwen_forward_calls": 0,
                    "model_training_runs": 0,
                    "appworld_instances": 0,
                    "one_step_generations": 0,
                    "h100_hours": 0.0,
                    "stop_before_model_training": True,
                    "stop_before_replay_preflight": True,
                },
                "artifact_bytes_before_validation_outputs": sum(
                    path.stat().st_size for path in root.rglob("*") if path.is_file()
                ),
            }
        )
    output_path = root / "postrun_validation.json"
    atomic_write_json(output_path, result)
    atomic_write_text(root / "postrun_validation.md", _report(result))
    atomic_write_json(root / "final_exp022_summary.json", result)
    atomic_write_text(root / "final_exp022_report.md", _report(result))
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
