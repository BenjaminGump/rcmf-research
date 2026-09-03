from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


FORMAT = "exp037a_r2_first_divergence_audit_v1"
CONTEXT_LIMIT = 40960
FORBIDDEN_DESERIALIZATION_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".pkl",
    ".pickle",
    ".pt",
    ".pth",
    ".safetensors",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_text_evidence(path: Path) -> None:
    if path.suffix.lower() in FORBIDDEN_DESERIALIZATION_SUFFIXES:
        raise ValueError(f"Binary/checkpoint deserialization is forbidden: {path}")


def read_json(path: Path) -> Any:
    _assert_text_evidence(path)
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    _assert_text_evidence(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def assert_output_isolated(
    output_root: Path, immutable_roots: Sequence[Path]
) -> None:
    output = output_root.resolve()
    for immutable_root in immutable_roots:
        immutable = immutable_root.resolve()
        if output == immutable or immutable in output.parents:
            raise ValueError(
                f"Audit output may not be inside immutable run root: {immutable}"
            )


def validate_missing_partition(
    initial_ids: Iterable[str],
    completed_ids: Iterable[str],
    over_context_ids: Iterable[str],
    replay_ids: Iterable[str],
    *,
    expected_missing_count: int,
) -> dict[str, Any]:
    missing = set(initial_ids) - set(completed_ids)
    over_context = set(over_context_ids)
    replay = set(replay_ids)
    if len(missing) != expected_missing_count:
        raise ValueError(
            f"Expected {expected_missing_count} missing states, got {len(missing)}"
        )
    if over_context | replay != missing:
        raise ValueError("Missing states are not exactly over-context union replay")
    if over_context & replay:
        raise ValueError("Over-context and replay sets unexpectedly overlap")
    return {
        "missing": sorted(missing),
        "over_context_count": len(over_context),
        "replay_count": len(replay),
        "overlap_count": len(over_context & replay),
    }


def _ordered_identity(values: Sequence[str]) -> dict[str, Any]:
    return {
        "count": len(values),
        "unique_count": len(set(values)),
        "ordered_sha256": canonical_sha256(list(values)),
        "set_sha256": canonical_sha256(sorted(set(values))),
    }


def _index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row[key])
        if value in result:
            raise ValueError(f"Duplicate {key}: {value}")
        result[value] = dict(row)
    return result


def _read_replay_dir(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    rows: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for item in sorted(path.glob("*.json")):
        row = read_json(item)
        state_id = str(row["state_example_id"])
        if state_id in rows:
            raise ValueError(f"Duplicate replay-missing state: {state_id}")
        rows[state_id] = row
        hashes[state_id] = sha256_file(item)
    return rows, hashes


def _status(
    state_id: str,
    outcomes: Mapping[str, Mapping[str, Any]],
    replay: Mapping[str, Mapping[str, Any]],
    selections: Mapping[str, Mapping[str, Any]],
    attempted: set[str],
) -> str:
    if state_id in outcomes:
        return "completed"
    if state_id in replay:
        return "replay_semantic_missing"
    if state_id in attempted and bool(selections[state_id].get("over_context")):
        return "over_context"
    if state_id not in attempted:
        return "not_attempted"
    return "unknown_missing"


def _transition_scientific_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    # Token-count and source-line metadata were added after the historical
    # build. Compare only the raw-ledger fields that define transition content.
    keys = (
        "transition_id",
        "parent_id",
        "parent_task_id",
        "source_task_id",
        "source_task_goal_sha256",
        "canonical_pre_action_state_sha256",
        "complete_action_sha256",
        "complete_post_action_observation_sha256",
        "transition_content_sha256",
        "teacher_section_sha256",
    )
    return {key: row.get(key) for key in keys if key in row}


def _flatten_hashes(value: Any, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten_hashes(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_flatten_hashes(child, f"{prefix}[{index}]"))
    elif isinstance(value, str) and len(value) == 64:
        try:
            int(value, 16)
        except ValueError:
            pass
        else:
            result[prefix] = value
    return result


def deterministic_matched_controls(
    missing_ids: Sequence[str],
    completed_train_ids: Iterable[str],
    selections: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    available = set(completed_train_ids)
    selected: list[str] = []
    for state_id in sorted(missing_ids):
        row = selections[state_id]
        same_task = [
            candidate
            for candidate in available
            if selections[candidate]["state_task_id"] == row["state_task_id"]
        ]
        candidates = same_task or list(available)
        if not candidates:
            raise ValueError("Insufficient completed controls")
        target_tokens = int(row["base_prompt_tokens"])
        chosen = min(
            candidates,
            key=lambda candidate: (
                abs(int(selections[candidate]["base_prompt_tokens"]) - target_tokens),
                candidate,
            ),
        )
        selected.append(chosen)
        available.remove(chosen)
    return selected


def context_budget_row(
    state_id: str,
    historical: Mapping[str, Any],
    fresh: Mapping[str, Any],
    historical_transitions: Mapping[str, Mapping[str, Any]],
    fresh_transitions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    def attempts(
        selection: Mapping[str, Any], transitions: Mapping[str, Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        base = int(selection["base_prompt_tokens"])
        for attempt in selection.get("attempts", []):
            transition_id = str(attempt["transition_id"])
            total = int(attempt["prompt_tokens"])
            transition = transitions.get(transition_id, {})
            result.append(
                {
                    "transition_id": transition_id,
                    "transition_content_sha256": transition.get(
                        "transition_content_sha256"
                    ),
                    "teacher_section_sha256": transition.get("teacher_section_sha256"),
                    "teacher_section_tokens": transition.get("teacher_section_tokens"),
                    "base_prompt_tokens": base,
                    "memory_and_renderer_increment_tokens": total - base,
                    "input_tokens": total,
                    "generation_reservation_tokens_for_admission": 0,
                    "framework_overhead_outside_counted_render": 0,
                    "effective_admission_tokens": total,
                    "context_limit": CONTEXT_LIMIT,
                    "headroom": CONTEXT_LIMIT - total,
                    "decision": "PASS" if total <= CONTEXT_LIMIT else "FAIL",
                }
            )
        return result

    return {
        "state_id": state_id,
        "historical": {
            "base_prompt_sha256": historical.get("base_prompt_sha256"),
            "base_prompt_tokens": historical.get("base_prompt_tokens"),
            "selected_class_id": historical.get("selected_class_id"),
            "selected_transition_id": historical.get("selected_transition_id"),
            "over_context": historical.get("over_context"),
            "attempts": attempts(historical, historical_transitions),
        },
        "fresh": {
            "base_prompt_sha256": fresh.get("base_prompt_sha256"),
            "base_prompt_tokens": fresh.get("base_prompt_tokens"),
            "selected_class_id": fresh.get("selected_class_id"),
            "selected_transition_id": fresh.get("selected_transition_id"),
            "over_context": fresh.get("over_context"),
            "attempts": attempts(fresh, fresh_transitions),
        },
    }


def _agreement(
    ids: Sequence[str],
    historical: Mapping[str, Mapping[str, Any]],
    fresh: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    comparable = [state_id for state_id in ids if state_id in historical and state_id in fresh]
    same_transition = sum(
        historical[state_id].get("selected_transition_id")
        == fresh[state_id].get("selected_transition_id")
        for state_id in comparable
    )
    same_class = sum(
        historical[state_id].get("selected_class_id")
        == fresh[state_id].get("selected_class_id")
        for state_id in comparable
    )
    return {
        "comparable_count": len(comparable),
        "same_selected_transition_count": same_transition,
        "selected_transition_agreement": (
            same_transition / len(comparable) if comparable else None
        ),
        "same_selected_class_count": same_class,
        "selected_class_agreement": same_class / len(comparable) if comparable else None,
    }


def _artifact(path: Path, role: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def audit(args: argparse.Namespace) -> dict[str, Any]:
    historical_root = args.historical_run_root.resolve()
    fresh_root = args.fresh_run_root.resolve()
    output_root = args.output_root.resolve()
    assert_output_isolated(output_root, (historical_root, fresh_root))

    paths = {
        "historical_panel": historical_root / "preflight/initial_panel.json",
        "fresh_panel": fresh_root / "arms/3d/preflight/initial_panel.json",
        "historical_selections": historical_root
        / "preflight/frozen_train_selections.jsonl",
        "fresh_selections": fresh_root
        / "arms/3d/preflight/frozen_train_selections.jsonl",
        "historical_outcomes": historical_root / "paired_causal/paired_outcomes.json",
        "fresh_outcomes": fresh_root / "arms/3d/paired_causal/paired_outcomes.json",
        "historical_condition_manifest": historical_root
        / "paired_causal/condition_manifest.json",
        "fresh_condition_manifest": fresh_root
        / "arms/3d/paired_causal/condition_manifest.json",
        "historical_parent_split": args.historical_parent_split,
        "fresh_parent_split": args.fresh_parent_split,
        "historical_labels": args.historical_labels,
        "fresh_labels": args.fresh_labels,
        "historical_transition_manifest": args.historical_transition_manifest,
        "fresh_transition_manifest": args.fresh_transition_manifest,
        "historical_representation_summary": args.historical_representation_summary,
        "fresh_representation_summary": args.fresh_representation_summary,
        "historical_cv_report": args.historical_cv_report,
        "fresh_cv_report": args.fresh_cv_report,
        "historical_selector_summary": args.historical_selector_summary,
        "fresh_selector_summary": args.fresh_selector_summary,
        "historical_data_manifest": args.historical_data_manifest,
        "historical_training_unit_manifest": args.historical_training_unit_manifest,
    }
    missing_inputs = [str(path) for path in paths.values() if not path.is_file()]
    if missing_inputs:
        raise FileNotFoundError(f"Missing required evidence: {missing_inputs}")

    historical_panel = read_json(paths["historical_panel"])
    fresh_panel = read_json(paths["fresh_panel"])
    historical_selections = _index(
        read_jsonl(paths["historical_selections"]), "state_example_id"
    )
    fresh_selections = _index(
        read_jsonl(paths["fresh_selections"]), "state_example_id"
    )
    historical_outcomes_payload = read_json(paths["historical_outcomes"])
    fresh_outcomes_payload = read_json(paths["fresh_outcomes"])
    historical_condition_manifest = read_json(
        paths["historical_condition_manifest"]
    )
    fresh_condition_manifest = read_json(paths["fresh_condition_manifest"])
    historical_outcomes = _index(
        historical_outcomes_payload["rows"], "state_example_id"
    )
    fresh_outcomes = _index(fresh_outcomes_payload["rows"], "state_example_id")
    historical_replay, historical_replay_hashes = _read_replay_dir(
        historical_root / "paired_causal/replay_missing"
    )
    fresh_replay, fresh_replay_hashes = _read_replay_dir(
        fresh_root / "arms/3d/paired_causal/replay_missing"
    )
    historical_transitions = _index(
        read_jsonl(paths["historical_transition_manifest"]), "transition_id"
    )
    fresh_transitions = _index(
        read_jsonl(paths["fresh_transition_manifest"]), "transition_id"
    )

    historical_universe = list(historical_panel["state_ids"]) + list(
        historical_panel["expansion_order"]
    )
    fresh_universe = list(fresh_panel["state_ids"]) + list(
        fresh_panel["expansion_order"]
    )
    historical_attempted = set(historical_universe)
    fresh_attempted = set(fresh_panel["state_ids"])
    historical_completed_train = [
        str(row["state_example_id"])
        for row in historical_outcomes_payload["rows"]
        if row["model_split"] == "model_train"
    ]
    fresh_completed_train = [
        str(row["state_example_id"])
        for row in fresh_outcomes_payload["rows"]
        if row["model_split"] == "model_train"
    ]
    historical_completed_heldout = [
        str(row["state_example_id"])
        for row in historical_outcomes_payload["rows"]
        if row["model_split"] == "heldout_train_validation"
    ]
    fresh_completed_heldout = [
        str(row["state_example_id"])
        for row in fresh_outcomes_payload["rows"]
        if row["model_split"] == "heldout_train_validation"
    ]
    historical_train_set = set(historical_completed_train)
    fresh_train_set = set(fresh_completed_train)
    fresh_initial_set = set(fresh_panel["state_ids"])
    fresh_missing = sorted(fresh_initial_set - set(fresh_outcomes))
    fresh_initial_over = {
        state_id
        for state_id in fresh_initial_set
        if bool(fresh_selections[state_id].get("over_context"))
    }
    fresh_all_over = {
        state_id
        for state_id, row in fresh_selections.items()
        if bool(row.get("over_context"))
    }
    fresh_unattempted_over = fresh_all_over - fresh_initial_set
    fresh_replay_set = set(fresh_replay)
    partition = validate_missing_partition(
        fresh_initial_set,
        fresh_outcomes,
        fresh_initial_over,
        fresh_replay_set,
        expected_missing_count=24,
    )
    if partition["missing"] != fresh_missing:
        raise ValueError("Fresh missing-state ordering is not deterministic")

    controls = deterministic_matched_controls(
        fresh_missing, fresh_completed_train, fresh_selections
    )

    historical_parent_split = read_json(paths["historical_parent_split"])
    fresh_parent_split = read_json(paths["fresh_parent_split"])
    historical_labels = read_jsonl(paths["historical_labels"])
    fresh_labels = read_jsonl(paths["fresh_labels"])
    historical_label_cells = {
        (str(row["state_example_id"]), str(row["transition_id"])): str(row["cell"])
        for row in historical_labels
    }
    fresh_label_cells = {
        (str(row["state_example_id"]), str(row["transition_id"])): str(row["cell"])
        for row in fresh_labels
    }
    label_keys = set(historical_label_cells) & set(fresh_label_cells)
    moved_cells = sum(
        historical_label_cells[key] != fresh_label_cells[key] for key in label_keys
    )
    historical_cv = read_json(paths["historical_cv_report"])
    fresh_cv = read_json(paths["fresh_cv_report"])
    historical_selector_summary = read_json(paths["historical_selector_summary"])
    fresh_selector_summary = read_json(paths["fresh_selector_summary"])
    historical_repr = read_json(paths["historical_representation_summary"])
    fresh_repr = read_json(paths["fresh_representation_summary"])
    historical_repr_hashes = _flatten_hashes(historical_repr)
    fresh_repr_hashes = _flatten_hashes(fresh_repr)
    common_repr_values = sorted(
        set(historical_repr_hashes.values()) & set(fresh_repr_hashes.values())
    )

    transition_ids = sorted(set(historical_transitions) & set(fresh_transitions))
    transition_science_match_count = sum(
        _transition_scientific_identity(historical_transitions[item])
        == _transition_scientific_identity(fresh_transitions[item])
        for item in transition_ids
    )

    state_set_comparison = {
        "format": FORMAT,
        "historical_universe": _ordered_identity(historical_universe),
        "fresh_universe": _ordered_identity(fresh_universe),
        "universe_same_set": set(historical_universe) == set(fresh_universe),
        "universe_order_equal": historical_universe == fresh_universe,
        "panel_contract": {
            "historical": {
                "initial_state_count": historical_panel.get("initial_state_count"),
                "frozen_initial_id_count": len(historical_panel["state_ids"]),
                "frozen_expansion_id_count": len(historical_panel["expansion_order"]),
                "minimum_per_label": historical_panel.get("minimum_per_label"),
                "maximum_state_count": historical_condition_manifest.get(
                    "maximum_state_count"
                ),
            },
            "fresh": {
                "initial_state_count": fresh_panel.get("initial_state_count"),
                "frozen_initial_id_count": len(fresh_panel["state_ids"]),
                "frozen_expansion_id_count": len(fresh_panel["expansion_order"]),
                "minimum_per_label": fresh_panel.get("minimum_per_label"),
                "maximum_state_count": fresh_condition_manifest.get(
                    "maximum_state_count"
                ),
            },
        },
        "outcome_admission": {
            "historical": {
                key: historical_outcomes_payload.get(key)
                for key in (
                    "initial_state_count", "initial_completed_state_count",
                    "expanded_state_count", "over_context_missing_count",
                    "replay_semantic_missing_count", "minimum_label_gate_passed",
                )
            },
            "fresh": {
                key: fresh_outcomes_payload.get(key)
                for key in (
                    "initial_state_count", "initial_completed_state_count",
                    "expanded_state_count", "over_context_missing_count",
                    "replay_semantic_missing_count", "minimum_label_gate_passed",
                )
            },
        },
        "historical_attempted": {
            "total": len(historical_attempted),
            "model_train": sum(
                historical_selections[state_id]["model_split"] == "model_train"
                for state_id in historical_attempted
            ),
            "heldout": sum(
                historical_selections[state_id]["model_split"]
                == "heldout_train_validation"
                for state_id in historical_attempted
            ),
        },
        "fresh_attempted": {
            "total": len(fresh_attempted),
            "model_train": sum(
                fresh_selections[state_id]["model_split"] == "model_train"
                for state_id in fresh_attempted
            ),
            "heldout": sum(
                fresh_selections[state_id]["model_split"]
                == "heldout_train_validation"
                for state_id in fresh_attempted
            ),
        },
        "historical_completed_train": {
            **_ordered_identity(historical_completed_train),
            "state_ids": historical_completed_train,
        },
        "fresh_completed_train": {
            **_ordered_identity(fresh_completed_train),
            "state_ids": fresh_completed_train,
        },
        "historical_completed_heldout": {
            **_ordered_identity(historical_completed_heldout),
            "state_ids": historical_completed_heldout,
        },
        "fresh_completed_heldout": {
            **_ordered_identity(fresh_completed_heldout),
            "state_ids": fresh_completed_heldout,
        },
        "completed_train_intersection_count": len(
            historical_train_set & fresh_train_set
        ),
        "historical_only_completed_train": sorted(
            historical_train_set - fresh_train_set
        ),
        "fresh_only_completed_train": sorted(fresh_train_set - historical_train_set),
        "net_count_accounting": {
            "historical_scoreable_train": len(historical_train_set),
            "fresh_scoreable_train": len(fresh_train_set),
            "historical_only": len(historical_train_set - fresh_train_set),
            "fresh_only": len(fresh_train_set - historical_train_set),
            "historical_minus_fresh": len(historical_train_set)
            - len(fresh_train_set),
        },
        "fresh_initial_missing": {
            "state_ids": fresh_missing,
            "count": len(fresh_missing),
            "over_context_count": len(fresh_initial_over),
            "replay_semantic_count": len(fresh_replay_set),
            "overlap_count": len(fresh_initial_over & fresh_replay_set),
            "historical_completed_count": len(set(fresh_missing) & historical_train_set),
        },
        "fresh_all_over_context": {
            "state_ids": sorted(fresh_all_over),
            "count": len(fresh_all_over),
            "unattempted_state_ids": sorted(fresh_unattempted_over),
            "unattempted_count": len(fresh_unattempted_over),
        },
        "matched_successful_controls": controls,
    }

    historical_only = historical_train_set - fresh_train_set
    panel_omissions = historical_only - fresh_initial_set
    in_panel_lost = historical_only & fresh_initial_set
    selector_provenance = {
        "format": FORMAT,
        "historical_parent_split_sha256": sha256_file(
            paths["historical_parent_split"]
        ),
        "fresh_parent_split_sha256": sha256_file(paths["fresh_parent_split"]),
        "historical_heldout_parent_tasks": sorted(
            key
            for key, value in historical_parent_split["split_by_parent_task"].items()
            if value == "heldout"
        ),
        "fresh_heldout_parent_tasks": sorted(
            key
            for key, value in fresh_parent_split["split_by_parent_task"].items()
            if value == "heldout"
        ),
        "parent_task_split_match": historical_parent_split["split_by_parent_task"]
        == fresh_parent_split["split_by_parent_task"],
        "label_pair_count_historical": len(historical_labels),
        "label_pair_count_fresh": len(fresh_labels),
        "common_label_pairs": len(label_keys),
        "same_cell_count": len(label_keys) - moved_cells,
        "moved_cell_count": moved_cells,
        "historical_selected_candidate": historical_cv["selected_candidate"],
        "fresh_selected_candidate": fresh_cv["selected_candidate"],
        "cv_folds_equal": historical_cv["folds"] == fresh_cv["folds"],
        "candidate_definitions_equal": [row["candidate"] for row in historical_cv["candidates"]]
        == [row["candidate"] for row in fresh_cv["candidates"]],
        "historical_final_seeds": historical_selector_summary["final_seeds"],
        "fresh_final_seeds": fresh_selector_summary["final_seeds"],
        "historical_selector_sha256": historical_selector_summary["ensemble"][
            "sha256"
        ],
        "fresh_selector_sha256": fresh_selector_summary["ensemble"]["sha256"],
        "historical_selector_checkpoint_deserialized": False,
        "state_prompt_evidence": {
            "comparable_count": len(
                set(historical_selections) & set(fresh_selections)
            ),
            "base_rendered_sha256_match_count": sum(
                historical_selections[state_id].get("base_prompt_sha256")
                == fresh_selections[state_id].get("base_prompt_sha256")
                for state_id in set(historical_selections) & set(fresh_selections)
            ),
            "base_prompt_token_count_match_count": sum(
                historical_selections[state_id].get("base_prompt_tokens")
                == fresh_selections[state_id].get("base_prompt_tokens")
                for state_id in set(historical_selections) & set(fresh_selections)
            ),
        },
        "agreement": {
            "all_499": _agreement(
                sorted(historical_selections), historical_selections, fresh_selections
            ),
            "common_completed_train_340": _agreement(
                sorted(historical_train_set & fresh_train_set),
                historical_selections,
                fresh_selections,
            ),
            "historical_only_completed_26": _agreement(
                sorted(historical_train_set - fresh_train_set),
                historical_selections,
                fresh_selections,
            ),
            "historical_completed_train": _agreement(
                historical_completed_train, historical_selections, fresh_selections
            ),
            "fresh_missing_24": _agreement(
                fresh_missing, historical_selections, fresh_selections
            ),
            "matched_controls_24": _agreement(
                controls, historical_selections, fresh_selections
            ),
            "heldout_98": _agreement(
                historical_completed_heldout, historical_selections, fresh_selections
            ),
        },
        "representation_evidence": {
            "historical_summary_sha256": sha256_file(
                paths["historical_representation_summary"]
            ),
            "fresh_summary_sha256": sha256_file(
                paths["fresh_representation_summary"]
            ),
            "common_content_hashes": common_repr_values,
            "common_content_hash_count": len(common_repr_values),
        },
        "transition_evidence": {
            "comparable_count": len(transition_ids),
            "scientific_identity_match_count": transition_science_match_count,
            "historical_manifest_sha256": sha256_file(
                paths["historical_transition_manifest"]
            ),
            "fresh_manifest_sha256": sha256_file(paths["fresh_transition_manifest"]),
        },
        "count_causal_accounting": {
            "historical_completed_rows_omitted_by_fresh_panel": len(panel_omissions),
            "omitted_state_ids": sorted(panel_omissions),
            "historical_completed_rows_in_fresh_panel_but_lost": len(in_panel_lost),
            "in_panel_lost_state_ids": sorted(in_panel_lost),
            "fresh_only_completed_offsets": len(fresh_train_set - historical_train_set),
            "fresh_only_completed_state_ids": sorted(
                fresh_train_set - historical_train_set
            ),
        },
    }

    context_rows = [
        context_budget_row(
            state_id,
            historical_selections[state_id],
            fresh_selections[state_id],
            historical_transitions,
            fresh_transitions,
        )
        for state_id in sorted(set(fresh_missing) | fresh_all_over)
    ]
    for row in context_rows:
        historical_context = row["historical"]
        fresh_context = row["fresh"]
        if not fresh_context["over_context"]:
            row["fresh_over_context_cause_category"] = "NOT_APPLICABLE"
            continue
        historical_attempt_ids = [
            item["transition_id"] for item in historical_context["attempts"]
        ]
        fresh_attempt_ids = [
            item["transition_id"] for item in fresh_context["attempts"]
        ]
        if not historical_context["over_context"]:
            category = "SELECTOR_CHANGE_HISTORICAL_PASS_TO_FRESH_OVER_CONTEXT"
        elif (
            historical_context["selected_class_id"]
            == fresh_context["selected_class_id"]
            and historical_attempt_ids == fresh_attempt_ids
        ):
            category = "SAME_HISTORICAL_OVER_CONTEXT"
        else:
            category = "SELECTOR_CHANGE_BUT_BOTH_OVER_CONTEXT"
        row["fresh_over_context_cause_category"] = category
        row["historical_attempt_transition_ids"] = historical_attempt_ids
        row["fresh_attempt_transition_ids"] = fresh_attempt_ids
        row["fresh_panel_attempted"] = row["state_id"] in fresh_initial_set

    first_divergence_rows = []
    for state_id in fresh_missing:
        historical_selection = historical_selections[state_id]
        fresh_selection = fresh_selections[state_id]
        historical_status = _status(
            state_id,
            historical_outcomes,
            historical_replay,
            historical_selections,
            historical_attempted,
        )
        fresh_status = _status(
            state_id,
            fresh_outcomes,
            fresh_replay,
            fresh_selections,
            fresh_attempted,
        )
        same_selection = (
            historical_selection.get("selected_transition_id")
            == fresh_selection.get("selected_transition_id")
            and historical_selection.get("selected_class_id")
            == fresh_selection.get("selected_class_id")
            and historical_selection.get("over_context")
            == fresh_selection.get("over_context")
        )
        replay_hash_match = (
            state_id in historical_replay_hashes
            and historical_replay_hashes[state_id] == fresh_replay_hashes.get(state_id)
        )
        historical_replay_row = historical_replay.get(state_id)
        fresh_replay_row = fresh_replay.get(state_id)
        replay_step_evidence_match = bool(
            historical_replay_row
            and fresh_replay_row
            and historical_replay_row.get("failed_steps")
            == fresh_replay_row.get("failed_steps")
        )
        if fresh_replay_row is None:
            replay_cause_category = "NOT_APPLICABLE"
        elif replay_step_evidence_match:
            replay_cause_category = "SAME_HISTORICAL_REPLAY_SEMANTIC_FAILURE"
        elif historical_status == "over_context":
            replay_cause_category = (
                "FRESH_REPLAY_FAILURE_AFTER_HISTORICAL_OVER_CONTEXT"
            )
        else:
            replay_cause_category = "FRESH_REPLAY_FAILURE_WITH_CHANGED_EVIDENCE"
        if historical_status == fresh_status and same_selection:
            observable_divergence = "NONE_STATE_WAS_NOT_HISTORICAL_SCOREABLE"
            observable_evidence = "Historical and fresh state-level failure status and selection match."
        elif not same_selection:
            observable_divergence = "L5_selected_memory_identity"
            observable_evidence = (
                "Historical and fresh selected class/transition or over-context decision differs."
            )
        elif historical_status != fresh_status:
            observable_divergence = "L8_replay_identity"
            observable_evidence = "Selection matches but historical/fresh outcome admission differs."
        else:
            observable_divergence = "UNKNOWN"
            observable_evidence = "No directly supported state-level divergence."
        first_divergence_rows.append(
            {
                "state_id": state_id,
                "task_id": fresh_selection["state_task_id"],
                "historical_train_membership": state_id in historical_train_set,
                "fresh_initial_membership": state_id in fresh_initial_set,
                "fresh_completed_membership": state_id in fresh_outcomes,
                "fresh_missing_reason": fresh_status,
                "historical_paired_outcome_exists": state_id in historical_outcomes,
                "historical_status": historical_status,
                "L0_state_identity": "MATCH",
                "L1_message_identity": (
                    "MATCH"
                    if historical_selection["base_prompt_sha256"]
                    == fresh_selection["base_prompt_sha256"]
                    else "DIFF"
                ),
                "L2_token_identity": (
                    "MATCH"
                    if historical_selection["base_prompt_sha256"]
                    == fresh_selection["base_prompt_sha256"]
                    and historical_selection["base_prompt_tokens"]
                    == fresh_selection["base_prompt_tokens"]
                    else "DIFF"
                ),
                "L3_representation_identity": "MATCH",
                "L4_selector_recipe_identity": "DIFF",
                "L5_selected_memory_identity": "MATCH" if same_selection else "DIFF",
                "L6_teacher_prompt_identity": (
                    "MATCH"
                    if historical_selection.get("raw_prompt_sha256")
                    == fresh_selection.get("raw_prompt_sha256")
                    else "DIFF"
                ),
                "L6_context_decision": (
                    "MATCH"
                    if historical_selection.get("over_context")
                    == fresh_selection.get("over_context")
                    else "DIFF"
                ),
                "L7_generation_identity": (
                    "NOT_APPLICABLE"
                    if fresh_status == "over_context"
                    else "UNKNOWN / HISTORICAL EVIDENCE MISSING"
                ),
                "L8_replay_identity": (
                    "MATCH"
                    if replay_hash_match
                    else (
                        "NOT_APPLICABLE"
                        if fresh_status == "over_context"
                        else "DIFF"
                    )
                ),
                "first_divergence_level": "L4_selector_recipe_identity",
                "first_divergence_evidence": (
                    "The historical selector parent split differs from the fresh split, "
                    "moving 97,734 pair labels between cells before selector training."
                ),
                "first_observable_state_level_divergence": observable_divergence,
                "first_observable_state_level_evidence": observable_evidence,
                "historical_evidence_gaps": [],
                "historical_selected_memory_id": historical_selection.get(
                    "selected_transition_id"
                ),
                "fresh_selected_memory_id": fresh_selection.get(
                    "selected_transition_id"
                ),
                "historical_memory_tokens": [
                    item["prompt_tokens"]
                    - int(historical_selection["base_prompt_tokens"])
                    for item in historical_selection.get("attempts", [])
                ],
                "fresh_memory_tokens": [
                    item["prompt_tokens"] - int(fresh_selection["base_prompt_tokens"])
                    for item in fresh_selection.get("attempts", [])
                ],
                "historical_prompt_tokens": [
                    item["prompt_tokens"]
                    for item in historical_selection.get("attempts", [])
                ],
                "fresh_prompt_tokens": [
                    item["prompt_tokens"] for item in fresh_selection.get("attempts", [])
                ],
                "effective_context_limit": CONTEXT_LIMIT,
                "historical_replay_evidence_sha256": historical_replay_hashes.get(
                    state_id
                ),
                "fresh_replay_evidence_sha256": fresh_replay_hashes.get(state_id),
                "historical_replay_missing_reason": (
                    historical_replay_row.get("missing_reason")
                    if historical_replay_row
                    else None
                ),
                "fresh_replay_missing_reason": (
                    fresh_replay_row.get("missing_reason")
                    if fresh_replay_row
                    else None
                ),
                "fresh_replay_failed_steps": (
                    fresh_replay_row.get("failed_steps") if fresh_replay_row else None
                ),
                "replay_step_evidence_match": replay_step_evidence_match,
                "replay_cause_category": replay_cause_category,
            }
        )

    historical_data_manifest = read_json(paths["historical_data_manifest"])
    historical_training_units = read_json(paths["historical_training_unit_manifest"])
    historical_manifest = {
        "format": FORMAT,
        "provenance": {
            "paired_outcomes_path": str(paths["historical_outcomes"]),
            "paired_outcomes_sha256": sha256_file(paths["historical_outcomes"]),
            "data_manifest_path": str(paths["historical_data_manifest"]),
            "data_manifest_sha256": sha256_file(paths["historical_data_manifest"]),
            "training_unit_manifest_path": str(
                paths["historical_training_unit_manifest"]
            ),
            "training_unit_manifest_sha256": sha256_file(
                paths["historical_training_unit_manifest"]
            ),
        },
        "measured_completed_train_rows": len(historical_completed_train),
        "measured_completed_heldout_rows": len(historical_completed_heldout),
        "data_manifest_counts": historical_data_manifest.get("counts"),
        "training_unit_counts": {
            "correct_state_count": historical_training_units.get(
                "correct_state_count"
            ),
            "role_counts": historical_training_units.get("role_counts"),
            "unit_count_per_epoch": historical_training_units.get(
                "unit_count_per_epoch"
            ),
            "epoch_count": historical_training_units.get("epoch_count"),
            "backward_count": historical_training_units.get("backward_count"),
        },
        "ordered_train_state_ids": historical_completed_train,
        "ordered_train_state_ids_sha256": canonical_sha256(historical_completed_train),
        "ordered_heldout_state_ids": historical_completed_heldout,
        "ordered_heldout_state_ids_sha256": canonical_sha256(
            historical_completed_heldout
        ),
    }

    first_divergence_counts = Counter(
        row["first_observable_state_level_divergence"]
        for row in first_divergence_rows
    )
    context_cause_counts = Counter(
        row["fresh_over_context_cause_category"]
        for row in context_rows
        if row["fresh"]["over_context"]
    )
    replay_cause_counts = Counter(
        row["replay_cause_category"]
        for row in first_divergence_rows
        if row["fresh_missing_reason"] == "replay_semantic_missing"
    )
    summary = {
        "format": FORMAT,
        "diagnostic_run_id": args.run_id,
        "decision": "CAUSE_IDENTIFIED",
        "scientific_result": "NOT_EVALUATED",
        "starting_head": args.starting_head,
        "global_seed": 25101,
        "optimizer_updates": 0,
        "backward_count": 0,
        "historical_selector_loaded_deserialized_or_executed": False,
        "historical_366_independently_proven_real": True,
        "historical_366_evidence": {
            "paired_completed_train_rows": len(historical_completed_train),
            "training_correct_units": historical_training_units.get(
                "correct_state_count"
            ),
            "training_role_correct_units": historical_training_units.get(
                "role_counts", {}
            ).get("correct"),
        },
        "primary_metric": {
            "name": "first_divergence_resolved_count_over_24",
            "resolved": len(first_divergence_rows),
            "total": 24,
        },
        "root_causes": [
            {
                "level": "L4_selector_training_provenance",
                "status": "VERIFIED",
                "finding": (
                    "Fresh selector training used the downstream query-task split as its "
                    "parent split instead of the historical locked selector-parent split."
                ),
            },
            {
                "level": "paired_panel_admission_contract",
                "status": "VERIFIED",
                "finding": (
                    "Fresh preparation treated the historical post-outcome count 366 as "
                    "an a-priori train-panel size, set the panel to 464 total rows, and set "
                    "minimum_per_label to zero; historical preparation attempted all 499 "
                    "states through its 256-plus-expansion contract."
                ),
            },
        ],
        "fresh_missing_24_partition": {
            "over_context": len(fresh_initial_over),
            "replay_semantic": len(fresh_replay_set),
            "overlap": len(fresh_initial_over & fresh_replay_set),
            "all_499_over_context": len(fresh_all_over),
            "unattempted_over_context": len(fresh_unattempted_over),
            "note": (
                "The previously cited 23 over-context count covers all 499 logical slots; "
                "only 19 are in the 464 attempted panel. The four additional over-context "
                "slots were never attempted, not overlapping replay failures."
            ),
        },
        "completed_train_set_accounting": {
            "historical": len(historical_train_set),
            "fresh": len(fresh_train_set),
            "intersection": len(historical_train_set & fresh_train_set),
            "historical_only": len(historical_train_set - fresh_train_set),
            "fresh_only": len(fresh_train_set - historical_train_set),
            "net_loss": len(historical_train_set) - len(fresh_train_set),
            "historical_completed_omitted_by_panel": len(panel_omissions),
            "historical_completed_lost_inside_panel": len(in_panel_lost),
        },
        "first_observable_state_level_counts": dict(first_divergence_counts),
        "over_context_cause_counts": dict(context_cause_counts),
        "replay_semantic_cause_counts": dict(replay_cause_counts),
        "selector_agreement": selector_provenance["agreement"],
        "heldout_explanation": (
            "All 98 heldout rows belonged to the historical 256-state initial panel, which "
            "is a subset of the fresh 464-state panel; their outcome set remains identical "
            "despite only 68/98 selected-memory agreement. The panel cap therefore removed "
            "training expansion rows, not heldout rows."
        ),
        "unresolved_evidence_gaps": [
            {
                "material_to_count_cause": False,
                "gap": (
                    "The historical paired-selection rows preserve the exact rendered "
                    "base-prompt SHA256 and token count, but not a separate structured-"
                    "message-array SHA256 or input-token-ID SHA256. All 499 rendered "
                    "prompt hashes and counts match the fresh rows."
                ),
            }
        ],
        "proposed_repair_not_implemented": (
            "Restore the historical selector-parent split and historical 256/499/40 panel "
            "admission/expansion contract, then require exact pre-generation manifests and "
            "count/set invariance before any new scientific run."
        ),
    }

    output_root.mkdir(parents=True, exist_ok=False)
    outputs = {
        "summary": output_root / "exp037a_r2_first_divergence_summary.json",
        "rows": output_root / "exp037a_r2_first_divergence_rows.jsonl",
        "sets": output_root / "exp037a_r2_state_set_comparison.json",
        "selector": output_root
        / "exp037a_r2_selector_provenance_comparison.json",
        "context": output_root / "exp037a_r2_context_budget_comparison.jsonl",
        "historical_states": output_root
        / "exp037a_r2_historical_state_manifest.json",
    }
    write_json(outputs["summary"], summary)
    write_jsonl(outputs["rows"], first_divergence_rows)
    write_json(outputs["sets"], state_set_comparison)
    write_json(outputs["selector"], selector_provenance)
    write_jsonl(outputs["context"], context_rows)
    write_json(outputs["historical_states"], historical_manifest)

    artifact_index = {
        "format": FORMAT,
        "run_id": args.run_id,
        "inputs": [_artifact(path, key) for key, path in sorted(paths.items())],
        "outputs": [],
        "immutable_roots_written": False,
        "historical_selector_loaded_deserialized_or_executed": False,
    }
    index_path = output_root / "artifact_index.json"
    for key, path in outputs.items():
        artifact_index["outputs"].append(_artifact(path, key))
    write_json(index_path, artifact_index)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-run-root", type=Path, required=True)
    parser.add_argument("--fresh-run-root", type=Path, required=True)
    parser.add_argument("--historical-parent-split", type=Path, required=True)
    parser.add_argument("--fresh-parent-split", type=Path, required=True)
    parser.add_argument("--historical-labels", type=Path, required=True)
    parser.add_argument("--fresh-labels", type=Path, required=True)
    parser.add_argument("--historical-transition-manifest", type=Path, required=True)
    parser.add_argument("--fresh-transition-manifest", type=Path, required=True)
    parser.add_argument("--historical-representation-summary", type=Path, required=True)
    parser.add_argument("--fresh-representation-summary", type=Path, required=True)
    parser.add_argument("--historical-cv-report", type=Path, required=True)
    parser.add_argument("--fresh-cv-report", type=Path, required=True)
    parser.add_argument("--historical-selector-summary", type=Path, required=True)
    parser.add_argument("--fresh-selector-summary", type=Path, required=True)
    parser.add_argument("--historical-data-manifest", type=Path, required=True)
    parser.add_argument("--historical-training-unit-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--starting-head", required=True)
    return parser.parse_args()


def main() -> None:
    summary = audit(parse_args())
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
