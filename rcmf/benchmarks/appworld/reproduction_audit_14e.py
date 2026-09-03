from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.utils.serialization import atomic_write_json, read_jsonl


TARGET_STATE_ID = "appworld:trace:afc0fce_1:step:13:line:69"
CV_METRIC_NAMES = (
    "mean_ndcg4",
    "fold_ndcg4_std",
    "mean_pairwise_accuracy",
    "mean_state_shuffle_drop",
    "mean_transition_shuffle_drop",
)


def _json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _label_cells(path: str | Path) -> Iterable[tuple[str, str, str]]:
    for row in read_jsonl(path):
        yield (
            str(row["state_example_id"]),
            str(row["transition_id"]),
            str(row["cell"]),
        )


def compare_label_cells(
    fresh_path: str | Path,
    historical_path: str | Path,
    *,
    expected_count: int = 310433,
) -> dict[str, Any]:
    fresh_cells: dict[tuple[str, str], str] = {}
    fresh_order: list[tuple[str, str]] = []
    for state_id, transition_id, cell in _label_cells(fresh_path):
        key = (state_id, transition_id)
        if key in fresh_cells:
            raise ValueError(f"Duplicate fresh label pair: {key!r}")
        fresh_cells[key] = cell
        fresh_order.append(key)

    historical_keys: set[tuple[str, str]] = set()
    historical_count = 0
    moved = 0
    missing_in_fresh = 0
    order_mismatch = 0
    for index, (state_id, transition_id, cell) in enumerate(
        _label_cells(historical_path)
    ):
        key = (state_id, transition_id)
        if key in historical_keys:
            raise ValueError(f"Duplicate historical label pair: {key!r}")
        historical_keys.add(key)
        historical_count += 1
        if index >= len(fresh_order) or fresh_order[index] != key:
            order_mismatch += 1
        fresh_cell = fresh_cells.get(key)
        if fresh_cell is None:
            missing_in_fresh += 1
        elif fresh_cell != cell:
            moved += 1

    fresh_only = len(set(fresh_cells) - historical_keys)
    passed = (
        len(fresh_cells) == expected_count
        and historical_count == expected_count
        and missing_in_fresh == 0
        and fresh_only == 0
        and moved == 0
    )
    return {
        "row_count": len(fresh_cells),
        "historical_row_count": historical_count,
        "pair_order_mismatch_count": order_mismatch,
        "missing_in_fresh_count": missing_in_fresh,
        "fresh_only_count": fresh_only,
        "moved_cell_count": moved,
        "passed": passed,
    }


def _folds(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "fold": int(row["fold"]),
            "heldout_tasks": list(map(str, row["heldout_tasks"])),
            "heldout_parents": list(map(str, row["heldout_parents"])),
        }
        for row in rows
    ]


def _candidate_definitions(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row["candidate"]) for row in report["candidates"]]


def audit_static_contract(
    config: Mapping[str, Any], fresh_root: Path, output_root: Path
) -> dict[str, Any]:
    contract = config["pipeline"]["reproduction_contract"]
    references = contract["audit_references"]
    fresh_split_path = fresh_root / "preflight/shared/parent_split.json"
    fresh_labels_path = fresh_root / "preflight/shared/labels.jsonl"
    fresh_folds_path = fresh_root / "preflight/shared/cv_folds_and_sampling.json"
    resolved_3d_path = fresh_root / "resolved_configs/arm_3d.yaml"
    resolved_1d_path = fresh_root / "resolved_configs/arm_1d.yaml"
    required_fresh = (
        fresh_split_path,
        fresh_labels_path,
        fresh_folds_path,
        resolved_3d_path,
        resolved_1d_path,
    )
    if any(not path.is_file() for path in required_fresh):
        raise FileNotFoundError("Fresh contract must be sealed before audit")

    fresh_split_identity = file_identity(fresh_split_path)
    fresh_split = _json(fresh_split_path)
    fresh_folds = _json(fresh_folds_path)

    historical_split_path = Path(str(references["parent_split"]))
    historical_labels_path = Path(str(references["labels"]))
    historical_cv_path = Path(str(references["cv_report"]))
    historical_split = _json(historical_split_path)
    historical_cv = _json(historical_cv_path)
    labels = compare_label_cells(fresh_labels_path, historical_labels_path)

    from rcmf.config import load_config

    resolved_3d = load_config(resolved_3d_path).raw
    resolved_1d = load_config(resolved_1d_path).raw
    panel_3d = resolved_3d["stage_c_7hr"]["panel"]
    panel_1d = resolved_1d["stage_c_7hr"]["panel"]
    panel_values_3d = {
        key: int(panel_3d[key])
        for key in ("initial_state_count", "maximum_state_count", "minimum_per_label")
    }
    panel_values_1d = {
        key: int(panel_1d[key])
        for key in ("initial_state_count", "maximum_state_count", "minimum_per_label")
    }
    expected_panel = {
        key: int(value) for key, value in contract["causal_panel"].items()
    }
    fresh_fold_memberships = _folds(fresh_folds["folds"])
    historical_fold_memberships = _folds(historical_cv["folds"])
    configured_candidates = [dict(row) for row in config["pipeline"]["selector"]["candidates"]]
    historical_candidates = _candidate_definitions(historical_cv)
    checks = {
        "fresh_split_sha_matches_target": fresh_split_identity["sha256"]
        == str(contract["expected_parent_split_sha256"]),
        "fresh_split_payload_matches_historical": fresh_split == historical_split,
        "labels_match_310433_of_310433": bool(labels["passed"]),
        "fold_memberships_match": fresh_fold_memberships
        == historical_fold_memberships,
        "candidate_definitions_match": configured_candidates
        == historical_candidates,
        "cv_seed_matches": int(config["pipeline"]["selector"]["cv_seed"])
        == 25071,
        "final_member_seeds_match": list(
            map(int, config["pipeline"]["final_selector_member_seeds"])
        )
        == [25071, 25072, 25073],
        "panel_3d_is_256_499_40": panel_values_3d == expected_panel,
        "panel_1d_is_256_499_40": panel_values_1d == expected_panel,
        "panel_not_derived_from_366_98": panel_values_3d["initial_state_count"]
        != int(config["pipeline"]["expected"]["downstream_train_states"])
        + int(config["pipeline"]["expected"]["downstream_heldout_states"]),
    }
    result = {
        "format": "exp037a_r3_static_reproduction_contract_audit_14e_v1",
        "fresh_sealed_before_historical_reference_read": True,
        "historical_artifacts_used_as_scientific_inputs": False,
        "fresh_parent_split": fresh_split_identity,
        "historical_parent_split": file_identity(historical_split_path),
        "fresh_labels": file_identity(fresh_labels_path),
        "historical_labels": file_identity(historical_labels_path),
        "fresh_folds": fresh_fold_memberships,
        "historical_folds": historical_fold_memberships,
        "label_comparison": labels,
        "candidate_definitions": configured_candidates,
        "panel_3d": panel_values_3d,
        "panel_1d": panel_values_1d,
        "checks": checks,
        "passed": all(checks.values()),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_root / "static_contract_audit.json", result)
    if not result["passed"]:
        raise RuntimeError(f"Static reproduction-contract gate failed: {checks}")
    return result


def audit_representation_identities(
    config: Mapping[str, Any], fresh_root: Path, output_root: Path
) -> dict[str, Any]:
    summary_path = (
        fresh_root
        / "arms/3d/representation_cache/multiview/clean_multiview_cache_summary.json"
    )
    summary = _json(summary_path)
    actual = {
        "state_final": summary["state"]["aggregate"]["tensor_sha256"]["final_layer"],
        "state_mean_final_four": summary["state"]["aggregate"]["tensor_sha256"]
        ["mean_final_four_layers"],
        "transition_final": summary["transition"]["aggregate"]["tensor_sha256"]
        ["final_layer"],
        "transition_mean_final_four": summary["transition"]["aggregate"]
        ["tensor_sha256"]["mean_final_four_layers"],
    }
    expected = dict(
        config["pipeline"]["reproduction_contract"]["representation_sha256"]
    )
    checks = {key: str(actual[key]) == str(expected[key]) for key in expected}
    result = {
        "format": "exp037a_r3_representation_identity_audit_14e_v1",
        "summary": file_identity(summary_path),
        "actual": actual,
        "expected": expected,
        "checks": checks,
        "passed": all(checks.values()),
        "historical_tensor_loaded": False,
    }
    atomic_write_json(output_root / "representation_identity_audit.json", result)
    if not result["passed"]:
        raise RuntimeError(f"Representation identity gate failed: {checks}")
    return result


def _compact_candidate_metrics(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in report["candidates"]:
        output.append(
            {
                "candidate": dict(row["candidate"]),
                **{name: float(row[name]) for name in CV_METRIC_NAMES},
            }
        )
    return output


def simulate_historical_adaptive_expansion(
    panel: Mapping[str, Any], outcomes: Mapping[str, Any], minimum: int
) -> dict[str, Any]:
    initial = list(map(str, panel["state_ids"]))
    expansion = list(map(str, panel["expansion_order"]))
    labels = {
        str(row["state_example_id"]): str(row["label"])
        for row in outcomes["rows"]
    }
    counts = {"POSITIVE": 0, "NEUTRAL": 0, "HARMFUL": 0}
    attempted = []
    for index, state_id in enumerate(initial + expansion):
        if index >= len(initial) and all(
            counts[label] >= int(minimum) for label in counts
        ):
            break
        attempted.append(state_id)
        label = labels.get(state_id)
        if label is not None:
            counts[label] += 1
    return {
        "format": "exp037a_r3_historical_panel_simulation_14e_v1",
        "audit_only": True,
        "historical_outcomes_used_as_fresh_inputs": False,
        "initial_state_count": len(initial),
        "expansion_candidate_count": len(expansion),
        "logical_state_count": len(initial) + len(expansion),
        "attempted_state_count": len(attempted),
        "completed_label_counts": counts,
        "minimum_per_label": int(minimum),
        "quota_met": all(counts[label] >= int(minimum) for label in counts),
        "all_logical_states_attempted": len(attempted) == len(initial) + len(expansion),
        "attempted_state_ids_sha256": content_sha256(attempted),
    }


def audit_selector_and_context(
    config: Mapping[str, Any], fresh_root: Path, output_root: Path
) -> dict[str, Any]:
    contract = config["pipeline"]["reproduction_contract"]
    references = contract["audit_references"]
    fresh_cv_path = fresh_root / "arms/3d/selector/a_only_cv/a_only_cv_report.json"
    fresh_summary_path = fresh_root / "arms/3d/selector/selector_summary.json"
    fresh_selections_path = (
        fresh_root / "arms/3d/preflight/frozen_train_selections.jsonl"
    )
    if any(
        not path.is_file()
        for path in (fresh_cv_path, fresh_summary_path, fresh_selections_path)
    ):
        raise FileNotFoundError("Fresh selector outputs must be sealed before comparison")
    fresh_cv = _json(fresh_cv_path)
    fresh_summary = _json(fresh_summary_path)
    historical_panel_path = Path(str(references["causal_panel"]))
    historical_outcomes_path = Path(str(references["paired_outcomes"]))
    fresh_rows = {
        str(row["state_example_id"]): row
        for row in read_jsonl(fresh_selections_path)
    }

    historical_cv_path = Path(str(references["cv_report"]))
    historical_summary_path = Path(str(references["selector_summary"]))
    historical_selections_path = Path(str(references["selected_memories"]))
    historical_cv = _json(historical_cv_path)
    historical_summary = _json(historical_summary_path)
    historical_panel = _json(historical_panel_path)
    historical_outcomes = _json(historical_outcomes_path)
    historical_expansion = simulate_historical_adaptive_expansion(
        historical_panel, historical_outcomes, minimum=40
    )
    historical_rows = {
        str(row["state_example_id"]): row
        for row in read_jsonl(historical_selections_path)
    }
    state_ids = sorted(set(fresh_rows) & set(historical_rows))
    mismatches = []
    context_rows = []
    selected_match = 0
    class_match = 0
    context_match = 0
    for state_id in state_ids:
        fresh = fresh_rows[state_id]
        historical = historical_rows[state_id]
        same_selected = str(fresh["selected_transition_id"]) == str(
            historical["selected_transition_id"]
        )
        same_class = str(fresh["selected_class_id"]) == str(
            historical["selected_class_id"]
        )
        selected_match += int(same_selected)
        class_match += int(same_class)
        context_fields = (
            "base_prompt_sha256",
            "base_prompt_tokens",
            "raw_prompt_sha256",
            "raw_prompt_tokens",
            "over_context",
            "scoreable",
        )
        same_context = all(fresh.get(key) == historical.get(key) for key in context_fields)
        context_match += int(same_context)
        row = {
            "state_example_id": state_id,
            "state_task_id": str(fresh["state_task_id"]),
            "fresh_selected_memory_id": str(fresh["selected_transition_id"]),
            "historical_selected_memory_id": str(
                historical["selected_transition_id"]
            ),
            "selected_memory_match": same_selected,
            "selected_class_match": same_class,
            "base_prompt_sha256": str(fresh["base_prompt_sha256"]),
            "base_prompt_tokens": int(fresh["base_prompt_tokens"]),
            "memory_increment_tokens": int(fresh["raw_prompt_tokens"])
            - int(fresh["base_prompt_tokens"]),
            "total_prompt_tokens": int(fresh["raw_prompt_tokens"]),
            "context_limit": 40960,
            "context_decision": (
                "OVER_CONTEXT" if bool(fresh["over_context"]) else "PASS"
            ),
            "historical_context_match": same_context,
            "fresh_class_score": float(fresh["class_score"]),
            "fresh_class_margin": float(fresh["class_margin"]),
            "historical_class_score": float(historical["class_score"]),
            "historical_class_margin": float(historical["class_margin"]),
        }
        context_rows.append(row)
        if not same_selected or not same_class or not same_context:
            mismatches.append(row)
    target = next(row for row in context_rows if row["state_example_id"] == TARGET_STATE_ID)
    fresh_winner = str(fresh_cv["selected_candidate"]["name"])
    historical_winner = str(historical_cv["selected_candidate"]["name"])
    fresh_seeds = list(map(int, fresh_summary["final_seeds"]))
    checks = {
        "state_coverage_499": len(state_ids) == 499,
        "fresh_winner_matches_contract": fresh_winner
        == str(contract["expected_selector_winner"]),
        "historical_panel_expanded_through_499": historical_expansion[
            "all_logical_states_attempted"],
        "fresh_winner_matches_historical": fresh_winner == historical_winner,
        "final_member_seeds_match": fresh_seeds == [25071, 25072, 25073],
        "selected_memory_agreement_499": selected_match == 499,
        "selected_class_agreement_499": class_match == 499,
        "context_agreement_499": context_match == 499,
        "target_context_is_historical_pass": target["context_decision"] == "PASS"
        and target["historical_context_match"],
    }
    result = {
        "format": "exp037a_r3_selector_context_audit_14e_v1",
        "fresh_outputs_sealed_before_historical_reference_read": True,
        "historical_selector_checkpoint_loaded_deserialized_or_executed": False,
        "historical_selected_memories_used_as_fresh_inputs": False,
        "fresh_cv_report": file_identity(fresh_cv_path),
        "fresh_selector_summary": file_identity(fresh_summary_path),
        "historical_causal_panel": file_identity(historical_panel_path),
        "historical_paired_outcomes": file_identity(historical_outcomes_path),
        "historical_adaptive_expansion_simulation": historical_expansion,
        "fresh_selections": file_identity(fresh_selections_path),
        "historical_cv_report": file_identity(historical_cv_path),
        "historical_selector_summary": file_identity(historical_summary_path),
        "historical_selections": file_identity(historical_selections_path),
        "fresh_candidate_metrics": _compact_candidate_metrics(fresh_cv),
        "historical_candidate_metrics": _compact_candidate_metrics(historical_cv),
        "fresh_winner": fresh_winner,
        "historical_winner": historical_winner,
        "fresh_final_member_seeds": fresh_seeds,
        "selected_memory_agreement": {"count": selected_match, "total": 499},
        "selected_class_agreement": {"count": class_match, "total": 499},
        "context_agreement": {"count": context_match, "total": 499},
        "target_state": target,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "context_rows_sha256": content_sha256(context_rows),
        "checks": checks,
        "passed": all(checks.values()),
    }
    atomic_write_json(output_root / "selector_context_audit.json", result)
    with (output_root / "context_budget_rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in context_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    if not result["passed"]:
        raise RuntimeError(f"Selector/context reproduction gate failed: {checks}")
    return result
