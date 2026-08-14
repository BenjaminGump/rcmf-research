from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import CELL_NAMES
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_artifact(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    required = (
        "run_manifest.json",
        "attempts.jsonl",
        "heartbeat.json",
        "exp017_reuse_validation.json",
        "transition_parent_split_manifest.json",
        "two_axis_split_manifest.json",
        "two_axis_pair_rows.jsonl",
        "field_algebra_validation.json",
        "representation_cache/query_state_representations.pt",
        "representation_cache/transition_representations.pt",
        "representation_cache_report.json",
        "cheap_gate/grouped_cv_manifest.json",
        "cheap_gate/cv_results.json",
        "cheap_gate/model_results.json",
        "cheap_gate/cheap_interaction_report.json",
        "parts_a_d_summary.json",
        "parts_a_d_report.md",
    )
    missing = [value for value in required if not (root / value).exists()]
    if missing:
        return {"passed": False, "errors": [f"missing:{value}" for value in missing]}

    run_manifest = _load_json(root / "run_manifest.json")
    heartbeat = _load_json(root / "heartbeat.json")
    exp017 = _load_json(root / "exp017_reuse_validation.json")
    parent_split = _load_json(root / "transition_parent_split_manifest.json")
    two_axis = _load_json(root / "two_axis_split_manifest.json")
    field = _load_json(root / "field_algebra_validation.json")
    representation_report = _load_json(root / "representation_cache_report.json")
    cv_manifest = _load_json(root / "cheap_gate/grouped_cv_manifest.json")
    cheap = _load_json(root / "cheap_gate/cheap_interaction_report.json")
    summary = _load_json(root / "parts_a_d_summary.json")
    pair_rows = list(read_jsonl(root / "two_axis_pair_rows.jsonl"))
    attempts = list(read_jsonl(root / "attempts.jsonl"))

    if not exp017.get("passed"):
        errors.append("exp017_reuse_validation_not_passed")
    if len(pair_rows) != 4579:
        errors.append(f"pair_row_count:{len(pair_rows)}")
    pair_ids = [str(row["pair_id"]) for row in pair_rows]
    if len(pair_ids) != len(set(pair_ids)):
        errors.append("duplicate_pair_ids")
    if any(not bool(row.get("valid_for_loss")) for row in pair_rows):
        errors.append("invalid_pair_in_behavioral_rows")
    if any(bool(row.get("truncated")) for row in pair_rows):
        errors.append("truncated_pair_in_behavioral_rows")
    cell_counts = Counter(str(row["cell"]) for row in pair_rows)
    if set(cell_counts) != set(CELL_NAMES):
        errors.append(f"cell_names:{sorted(cell_counts)}")
    for cell in CELL_NAMES:
        expected_count = int(two_axis["cells"][cell]["pair_count"])
        if cell_counts[cell] != expected_count:
            errors.append(f"cell_count:{cell}:{cell_counts[cell]}:{expected_count}")
    if int(parent_split.get("train_parent_count", -1)) != 29:
        errors.append("train_parent_count")
    if int(parent_split.get("heldout_parent_count", -1)) != 8:
        errors.append("heldout_parent_count")
    if set(parent_split["train_parent_ids"]).intersection(
        parent_split["heldout_parent_ids"]
    ):
        errors.append("parent_split_overlap")

    state_cache = torch.load(
        root / "representation_cache/query_state_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    transition_cache = torch.load(
        root / "representation_cache/transition_representations.pt",
        map_location="cpu",
        weights_only=False,
    )
    if tuple(state_cache["representations"].shape) != (32, 4096):
        errors.append(f"state_representation_shape:{tuple(state_cache['representations'].shape)}")
    if tuple(transition_cache["representations"].shape) != (148, 4096):
        errors.append(
            f"transition_representation_shape:{tuple(transition_cache['representations'].shape)}"
        )
    state_hash = tensor_state_sha256(
        {"representations": state_cache["representations"]}
    )
    transition_hash = tensor_state_sha256(
        {"representations": transition_cache["representations"]}
    )
    if state_hash != state_cache.get("representation_tensor_sha256"):
        errors.append("state_representation_tensor_hash")
    if transition_hash != transition_cache.get("representation_tensor_sha256"):
        errors.append("transition_representation_tensor_hash")
    if any(bool(row.get("future_target_action_used")) for row in state_cache["rows"]):
        errors.append("query_representation_contains_future_target")
    if any(bool(row.get("truncated")) for row in transition_cache["rows"]):
        errors.append("transition_representation_truncated")
    if not representation_report.get("qwen_frozen"):
        errors.append("representation_qwen_not_frozen")

    for fold in cv_manifest["folds"]:
        train_ids = set(fold["train_pair_ids"])
        validation_ids = set(fold["validation_pair_ids"])
        if train_ids.intersection(validation_ids):
            errors.append(f"cv_pair_overlap:{fold['fold']}")
        by_id = {str(row["pair_id"]): row for row in pair_rows}
        train_tasks = {str(by_id[value]["state_task_id"]) for value in train_ids}
        validation_tasks = {
            str(by_id[value]["state_task_id"]) for value in validation_ids
        }
        train_parents = {
            str(by_id[value]["transition_parent_id"]) for value in train_ids
        }
        validation_parents = {
            str(by_id[value]["transition_parent_id"]) for value in validation_ids
        }
        if train_tasks.intersection(validation_tasks):
            errors.append(f"cv_task_leakage:{fold['fold']}")
        if train_parents.intersection(validation_parents):
            errors.append(f"cv_parent_leakage:{fold['fold']}")
    if cheap.get("selection_labels_used") != "train_state__train_transition":
        errors.append("cheap_gate_selection_cell")
    if cheap.get("heldout_labels_used_for_selection") is not False:
        errors.append("heldout_labels_used_for_selection")
    if not field.get("passed"):
        errors.append("field_algebra_not_passed")

    for kind, model in cheap["models"].items():
        checkpoint = Path(model["checkpoint"])
        if not checkpoint.exists():
            errors.append(f"missing_final_checkpoint:{kind}")
        elif sha256_file(checkpoint) != model["checkpoint_sha256"]:
            errors.append(f"final_checkpoint_hash:{kind}")
    starts = Counter(
        str(row["attempt_id"]) for row in attempts if row.get("event") == "start"
    )
    ends = Counter(
        str(row["attempt_id"]) for row in attempts if row.get("event") == "end"
    )
    if starts != ends or any(value != 1 for value in starts.values()):
        errors.append(f"attempt_ledger_pairing:{dict(starts)}:{dict(ends)}")
    if heartbeat.get("status") != "completed":
        errors.append(f"heartbeat_status:{heartbeat.get('status')}")
    if summary.get("run_uuid") != run_manifest.get("run_uuid"):
        errors.append("run_uuid_mismatch")
    if summary.get("decision_branch") != cheap["gate"].get("branch"):
        errors.append("decision_branch_mismatch")
    hard_scope = summary.get("hard_scope") or {}
    forbidden_true = (
        "qwen_behavioral_backpropagation_run",
        "selector_trained",
        "production_full_bank_constructed",
        "appworld_generation_or_evaluation_run",
        "stage_c2_started",
        "end_to_end_rcmf_started",
        "v4_tag_created",
    )
    for key in forbidden_true:
        if hard_scope.get(key) is not False:
            errors.append(f"hard_scope:{key}")

    return {
        "format": "state_conditioned_transition_parts_a_d_validation_6b_v1",
        "passed": not errors,
        "errors": errors,
        "run_uuid": run_manifest.get("run_uuid"),
        "attempt_count": len(starts),
        "pair_count": len(pair_rows),
        "cell_counts": dict(cell_counts),
        "state_representation_sha256": state_hash,
        "transition_representation_sha256": transition_hash,
        "decision_branch": summary.get("decision_branch"),
        "gate": cheap.get("gate"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate EXP-018 Parts A-D artifacts.")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_artifact(args.artifact_dir)
    atomic_write_json(args.artifact_dir / "parts_a_d_postrun_validation.json", report)
    atomic_write_text(
        args.artifact_dir / "parts_a_d_postrun_validation.md",
        "# EXP-018 Parts A-D Validation\n\n"
        f"- passed: `{report['passed']}`\n"
        f"- errors: `{len(report['errors'])}`\n"
        f"- decision branch: `{report.get('decision_branch')}`\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
