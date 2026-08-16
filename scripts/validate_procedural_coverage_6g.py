from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.training.procedural_coverage_6g import (
    candidate_space_summary,
    context_preflight_summary,
    select_decision_branch,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _immutable_input_paths(settings: Mapping[str, Any]) -> dict[str, Path]:
    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp018 = Path(settings["exp018_artifact"])
    exp019 = Path(settings["exp019_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp021 = Path(settings["exp021_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    return {
        "decision_examples": source / "decision_examples.jsonl",
        "transition_manifest": exp017 / "transition_manifest.jsonl",
        "transition_panel": exp017 / "transition_panel.jsonl",
        "exp017_validation": exp017 / "postrun_validation.json",
        "parent_split": exp018 / "transition_parent_split_manifest.json",
        "exp019_multiview_report": exp019
        / "parts_c_d/multiview_cache_report.json",
        "expanded_query_manifest": exp020 / "expanded_query_manifest.json",
        "old_pair_preflight": exp020 / "pair_preflight.jsonl",
        "exp020_final": exp020 / "final_summary.json",
        "exp020_model_summary": exp020 / "model_summary.json",
        "exp020_validation": exp020 / "postrun_validation.json",
        "exp021_validation": exp021 / "postrun_validation.json",
        "exp022_signatures": exp022 / "procedural_signatures.jsonl",
        "exp022_labels": exp022 / "procedural_label_rows.jsonl",
        "exp022_one_step": exp022 / "one_step_query_manifest.json",
        "exp022_summary": exp022 / "final_exp022_summary.json",
        "exp022_validation": exp022 / "postrun_validation.json",
    }


def _source_hashes_match(
    paths: Mapping[str, Path], hashes: Mapping[str, str]
) -> bool:
    return set(paths) == set(hashes) and all(
        path.exists() and sha256_file(path) == hashes[name]
        for name, path in paths.items()
    )


def _attempt_checks(rows: Sequence[Mapping[str, Any]], run_uuid: str) -> dict[str, bool]:
    starts = Counter(
        str(row["attempt_id"]) for row in rows if str(row.get("event")) == "start"
    )
    ends = Counter(
        str(row["attempt_id"]) for row in rows if str(row.get("event")) == "end"
    )
    end_rows = [row for row in rows if str(row.get("event")) == "end"]
    return {
        "ledger_nonempty": bool(rows),
        "single_run_uuid": {str(row.get("run_uuid")) for row in rows} == {run_uuid},
        "every_attempt_has_one_start": bool(starts)
        and all(value == 1 for value in starts.values()),
        "every_attempt_has_one_end": starts == ends,
        "no_scientific_parameter_change": not any(
            bool(row.get("scientific_parameter_changed")) for row in rows
        ),
        "all_end_events_record_exit_code": all("exit_code" in row for row in end_rows),
        "latest_attempt_succeeded": bool(end_rows)
        and int(end_rows[-1].get("exit_code", 1)) == 0,
    }


def _report(result: Mapping[str, Any]) -> str:
    checks = result["checks"]
    return "\n".join(
        [
            "# EXP-023 Independent Post-Run Validation",
            "",
            f"- passed: `{result['passed']}`",
            f"- checks: `{sum(bool(value) for value in checks.values())}/{len(checks)}`",
            f"- branch: `{result['recomputed']['decision_branch']}`",
            f"- Cartesian / illegal / legal: `{result['recomputed']['cartesian_pairs']}` / "
            f"`{result['recomputed']['illegal_pairs']}` / `{result['recomputed']['legal_pairs']}`",
            f"- scoreable / over-context: `{result['recomputed']['scoreable_pairs']}` / "
            f"`{result['recomputed']['over_context_pairs']}`",
            f"- B / E scoreable high-tier coverage: "
            f"`{result['recomputed']['B_scoreable_coverage']:.6f}` / "
            f"`{result['recomputed']['E_scoreable_coverage']:.6f}`",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate EXP-023 artifacts")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.artifact_dir
    required = [
        "run_manifest.json",
        "resolved_config.yaml",
        "stage_c_6g_settings.json",
        "attempts.jsonl",
        "full_transition_signature_manifest.jsonl",
        "full_transition_signature_validation.json",
        "signature_equivalence_groups.json",
        "full_pair_preflight.jsonl",
        "full_illegal_pairs.jsonl",
        "full_procedural_label_rows.jsonl",
        "one_step_condition_preflight.json",
        "final_exp023_summary.json",
    ]
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise FileNotFoundError(f"Required EXP-023 artifacts missing: {missing}")
    summary = _load_json(root / "final_exp023_summary.json")
    settings = _load_json(root / "stage_c_6g_settings.json")
    run_manifest = _load_json(root / "run_manifest.json")
    signatures = _load_rows(root / "full_transition_signature_manifest.jsonl")
    preflight = _load_rows(root / "full_pair_preflight.jsonl")
    illegal = _load_rows(root / "full_illegal_pairs.jsonl")
    labels = _load_rows(root / "full_procedural_label_rows.jsonl")
    attempts = _load_rows(root / "attempts.jsonl")
    signature_validation = _load_json(
        root / "full_transition_signature_validation.json"
    )
    one_step = _load_json(root / "one_step_condition_preflight.json")
    expected = settings["expected"]
    query_manifest_path = Path(settings["exp020_artifact"]) / "expanded_query_manifest.json"
    query_manifest = _load_json(query_manifest_path)
    state_task_by_id = {
        str(row["state_example_id"]): str(row["task_id"])
        for row in query_manifest["query_rows"]
    }
    train_state_ids = [
        str(row["state_example_id"])
        for row in query_manifest["query_rows"]
        if str(row["split"]) == "train"
    ]
    heldout_state_ids = [
        str(row["state_example_id"])
        for row in query_manifest["query_rows"]
        if str(row["split"]) == "validation"
    ]
    label_by_pair = {str(row["pair_id"]): row for row in labels}
    preflight_by_pair = {str(row["pair_id"]): row for row in preflight}
    context = context_preflight_summary(preflight, label_rows=labels)
    b_rows = [
        row
        for row in labels
        if str(row["cell"]) == "B" and bool(row["scoreable_under_context"])
    ]
    e_rows = [
        row
        for row in labels
        if str(row["cell"]) in {"B", "D"}
        and bool(row["scoreable_under_context"])
    ]
    b = candidate_space_summary(
        b_rows,
        state_ids=heldout_state_ids,
        state_task_by_id=state_task_by_id,
    )
    e = candidate_space_summary(
        e_rows,
        state_ids=heldout_state_ids,
        state_task_by_id=state_task_by_id,
    )
    branch = select_decision_branch(
        b_coverage=float(b["tier3_or_4_state_coverage"]),
        b_diverse_coverage=float(b["diverse_tier3_or_4_state_coverage"]),
        e_coverage=float(e["tier3_or_4_state_coverage"]),
        threshold=float(settings["heldout_high_tier_coverage_gate"]),
    )
    cartesian = int(expected["query_states"]) * int(expected["transitions"])
    cell_counts = Counter(str(row["cell"]) for row in labels)
    summary_cells = summary["counts"]["cell_counts"]
    attempts_ok = _attempt_checks(attempts, str(settings["run_uuid"]))
    source_hashes_unchanged = _source_hashes_match(
        _immutable_input_paths(settings), run_manifest["data_manifest_hashes"]
    )
    prohibited_files = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pt", ".pth", ".safetensors"}
    ]
    checks = {
        "signature_count": len(signatures) == int(expected["transitions"]),
        "signature_ids_unique": len(
            {str(row["transition_id"]) for row in signatures}
        )
        == len(signatures),
        "all_actions_ast_parsed": signature_validation["fallback_count"] == 0
        and signature_validation["parse_status"] == {"ast": len(signatures)},
        "credential_redaction": signature_validation["credential_leakage_count"]
        == 0,
        "old_panel_reproduced": signature_validation[
            "old_panel_overlap_mismatch_count"
        ]
        == 0
        and signature_validation["old_panel_overlap_count"]
        == int(expected["old_panel_transitions"]),
        "cartesian_partition": len(preflight) + len(illegal) == cartesian,
        "pair_ids_unique": len(preflight_by_pair) == len(preflight)
        and len(label_by_pair) == len(labels),
        "label_preflight_identity": set(label_by_pair) == set(preflight_by_pair),
        "no_truncation": context["truncated_pair_count"] == 0,
        "scoreable_over_context_partition": context["scoreable_pair_count"]
        + context["over_context_pair_count"]
        == len(preflight),
        "cell_partition": sum(cell_counts.values()) == len(labels)
        and all(
            cell_counts[cell] == int(summary_cells[cell]["legal"])
            for cell in "ABCD"
        ),
        "summary_counts_reproduced": summary["counts"]["cartesian_pairs"]
        == cartesian
        and summary["counts"]["illegal_pairs"] == len(illegal)
        and summary["counts"]["legal_pairs"] == len(preflight)
        and summary["counts"]["scoreable_pairs"] == context["scoreable_pair_count"]
        and summary["counts"]["over_context_pairs"]
        == context["over_context_pair_count"],
        "decision_reproduced": branch == summary["decision"]["branch"],
        "one_step_query_count": one_step["conditions"]["query_state_count"]
        == int(expected["one_step_query_states"]),
        "hard_scope_zero_execution": all(
            int(summary["hard_scope"][key]) == 0
            for key in (
                "model_training_count",
                "qwen_forward_count",
                "qwen_generation_count",
                "appworld_instance_count",
                "action_execution_count",
                "truncated_pair_count",
            )
        ),
        "no_model_artifacts": not prohibited_files,
        "source_hashes_unchanged": source_hashes_unchanged,
        **{f"attempt_{key}": value for key, value in attempts_ok.items()},
    }
    result = {
        "format": "procedural_coverage_postrun_validation_6g_v1",
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, value in checks.items() if not value],
        "recomputed": {
            "cartesian_pairs": cartesian,
            "illegal_pairs": len(illegal),
            "legal_pairs": len(preflight),
            "scoreable_pairs": context["scoreable_pair_count"],
            "over_context_pairs": context["over_context_pair_count"],
            "cell_counts": dict(cell_counts),
            "B_scoreable_coverage": b["tier3_or_4_state_coverage"],
            "B_scoreable_diverse_coverage": b[
                "diverse_tier3_or_4_state_coverage"
            ],
            "E_scoreable_coverage": e["tier3_or_4_state_coverage"],
            "decision_branch": branch,
        },
        "artifact_hashes": {
            name: sha256_file(root / name)
            for name in required
            if (root / name).is_file()
        },
        "prohibited_model_artifacts": prohibited_files,
    }
    atomic_write_json(root / "postrun_validation.json", result)
    atomic_write_text(root / "postrun_validation.md", _report(result))
    if not result["passed"]:
        raise SystemExit(f"EXP-023 validation failed: {result['failed_checks']}")


if __name__ == "__main__":
    main()
