from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config, save_resolved_config
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.memory_use_target_6e import canonical_two_axis_cell, stable_key
from rcmf.training.procedural_supervision_6f import (
    PROCEDURAL_LABEL_VERSION,
    PROCEDURE_SIGNATURE_VERSION,
    canonical_procedure_signature,
    observation_signature,
    procedural_compatibility,
    state_stage_signature,
    summarize_label_coverage,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _assert_count(name: str, actual: int, expected: int) -> None:
    if int(actual) != int(expected):
        raise ValueError(f"{name} differs: {actual} != {expected}")


def _task_id(example: Any) -> str:
    return str(example.metadata.get("task_id") or str(example.episode_id).split(":")[-1])


def _source_step_map(records: Sequence[Any]) -> dict[tuple[str, int], dict[str, str]]:
    output: dict[tuple[str, int], dict[str, str]] = {}
    for record in records:
        for raw in record.raw_trajectory.get("steps", []):
            key = (str(record.task_id), int(raw["step_id"]))
            if key in output:
                raise ValueError(f"Duplicate source trajectory step: {key}")
            output[key] = {
                "response": str(raw.get("response", "")).strip(),
                "observation": str(raw.get("observation", "")).strip(),
            }
    return output


def _credential_leakage_paths(value: Any, path: str = "root") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            findings.extend(_credential_leakage_paths(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            findings.extend(_credential_leakage_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
            findings.append(f"{path}:email")
        if re.search(r"\b\d{7,}\b", value):
            findings.append(f"{path}:long_number")
    return findings


def _hard_pair_coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for cell in "ABCD":
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            if str(row["cell"]) == cell:
                grouped[str(row["state_example_id"])].append(row)
        pair_count = 0
        state_count = 0
        for values in grouped.values():
            count = 0
            by_intent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for row in values:
                by_intent[str(row["transition_coarse_action_type"])].append(row)
            for intent_rows in by_intent.values():
                for left in range(len(intent_rows)):
                    for right in range(left + 1, len(intent_rows)):
                        if abs(int(intent_rows[left]["procedural_tier"]) - int(intent_rows[right]["procedural_tier"])) >= 1:
                            count += 1
            pair_count += count
            state_count += count > 0
        result[cell] = {
            "pair_count": pair_count,
            "state_count": state_count,
            "state_coverage": state_count / len(grouped) if grouped else 0.0,
        }
    return result


def _step_bucket(step_id: int, step_count: int) -> str:
    ratio = (int(step_id) - 1) / max(1, int(step_count) - 1)
    if ratio <= 1 / 3:
        return "early"
    if ratio <= 2 / 3:
        return "middle"
    return "later"


def _select_behavior_queries(
    query_rows: Sequence[Mapping[str, Any]],
    all_query_signatures: Mapping[str, Mapping[str, Any]],
    *,
    validation_tasks: set[str],
    count_per_task: int,
    seed: int,
) -> dict[str, Any]:
    existing = {
        str(row["state_example_id"])
        for row in query_rows
        if str(row["split"]) == "validation"
    }
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state_id, row in all_query_signatures.items():
        if str(row["task_id"]) in validation_tasks:
            by_task[str(row["task_id"])].append(dict(row))
    selected: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for task in sorted(validation_tasks):
        candidates = by_task[task]
        fixed = sorted(
            (row for row in candidates if str(row["state_example_id"]) in existing),
            key=lambda row: (int(row["step_id"]), str(row["state_example_id"])),
        )
        chosen = list(fixed)
        remaining = [row for row in candidates if row not in chosen]
        while remaining and len(chosen) < int(count_per_task):
            bucket_counts = Counter(str(row["step_bucket"]) for row in chosen)
            type_counts = Counter(str(row["target_signature"]["coarse_action_type"]) for row in chosen)
            picked = min(
                remaining,
                key=lambda row: (
                    bucket_counts[str(row["step_bucket"])],
                    type_counts[str(row["target_signature"]["coarse_action_type"])],
                    stable_key(seed, "behavior-query", task, row["state_example_id"]),
                ),
            )
            chosen.append(picked)
            remaining.remove(picked)
        for row in chosen[:count_per_task]:
            selected.append(
                {
                    "state_example_id": row["state_example_id"],
                    "task_id": task,
                    "step_id": row["step_id"],
                    "step_count": row["step_count"],
                    "step_bucket": row["step_bucket"],
                    "coarse_action_type": row["target_signature"]["coarse_action_type"],
                    "target_signature_sha256": row["target_signature"]["signature_sha256"],
                    "state_sha256": row["state_sha256"],
                    "is_exp020_query": row["state_example_id"] in existing,
                }
            )
        coverage[task] = {
            "available": len(candidates),
            "selected": min(len(chosen), count_per_task),
            "exp020_fixed": len(fixed),
            "shortage": max(0, count_per_task - len(chosen)),
        }
    selected.sort(key=lambda row: (row["task_id"], row["step_id"], row["state_example_id"]))
    return {
        "format": "one_step_query_manifest_6f_v1",
        "seed": seed,
        "count_per_task": count_per_task,
        "query_count": len(selected),
        "task_count": len(validation_tasks),
        "exp020_subset_count": sum(bool(row["is_exp020_query"]) for row in selected),
        "coverage": coverage,
        "rows": selected,
        "manifest_sha256": sha256_text("\n".join(row["state_example_id"] for row in selected)),
    }


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-022 Procedural Signature And Label Coverage",
        "",
        f"- status: `{summary['status']}`",
        f"- query signatures: `{summary['signatures']['query_count']}`",
        f"- transition signatures: `{summary['signatures']['transition_count']}`",
        f"- pair labels: `{summary['labels']['row_count']}`",
        f"- heldout train-bank Tier-3/4 coverage: `{summary['gate']['heldout_train_bank_coverage']:.6f}`",
        f"- coverage gate passed: `{summary['gate']['passed']}`",
        f"- one-step query manifest: `{summary['behavior_queries']['query_count']}`",
        "",
        "## Parser Coverage",
        "",
        f"- query: `{summary['signatures']['query_parse_status']}`",
        f"- transition: `{summary['signatures']['transition_parse_status']}`",
        f"- raw credential leakage rows: `{summary['signatures']['raw_credential_leakage_rows']}`",
        "",
        "## Cell Coverage",
        "",
    ]
    for cell in "ABCD":
        row = summary["labels"]["coverage"]["cells"][cell]
        lines.append(
            f"- {cell}: tiers `{row['tier_counts']}`, Tier-3/4 states "
            f"`{row['states_with_tier3_or_4']}/{row['state_count']}` "
            f"(`{row['tier3_or_4_state_coverage']:.6f}`)."
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare EXP-022 procedural labels")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp022-preflight")
    parser.add_argument("--parent-attempt-id", default=None)
    parser.add_argument("--resume-checkpoint", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6f"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp021 = Path(settings["exp021_artifact"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
        "query_manifest": exp020 / "expanded_query_manifest.json",
        "pair_rows": exp020 / "two_axis_pair_rows.jsonl",
        "exp020_final": exp020 / "final_summary.json",
        "transition_panel": exp017 / "transition_panel.jsonl",
        "exp017_validation": exp017 / "postrun_validation.json",
        "exp021_validation": exp021 / "postrun_validation.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "canonical_procedure_signatures",
            "fixed_procedural_tiers",
            "coverage_gate_before_models",
            "deterministic_one_step_query_manifest",
        ],
    )
    save_resolved_config(cfg, args.artifact_dir / "resolved_config.yaml")
    atomic_write_json(args.artifact_dir / "stage_c_6f_settings.json", settings)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="procedural_signature_label_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["replay"]["heartbeat_interval_seconds"]),
    ) as attempt:
        expected = settings["expected"]
        examples = load_decision_examples(paths["decision_examples"])
        records = load_memory_records(paths["memory_records"])
        query_manifest = _load_json(paths["query_manifest"])
        pairs = _rows(paths["pair_rows"])
        transitions = _rows(paths["transition_panel"])
        exp017_validation = _load_json(paths["exp017_validation"])
        exp021_validation = _load_json(paths["exp021_validation"])
        if not exp017_validation.get("passed") or not exp021_validation.get("passed"):
            raise ValueError("Immutable EXP-017/021 validation is not passed")
        _assert_count("decision examples", len(examples), expected["decision_examples"])
        _assert_count("query states", query_manifest["query_count"], expected["query_states"])
        _assert_count("train query states", query_manifest["train_query_count"], expected["train_query_states"])
        _assert_count("validation query states", query_manifest["validation_query_count"], expected["validation_query_states"])
        _assert_count("transition panel", len(transitions), expected["transitions"])
        _assert_count("scoreable pair rows", len(pairs), expected["scoreable_rows"])
        cell_counts = Counter(canonical_two_axis_cell(row["cell"]) for row in pairs)
        for cell in "ABCD":
            _assert_count(f"cell {cell}", cell_counts[cell], expected[f"cell_{cell.lower()}"])
        if len({str(row["pair_id"]) for row in pairs}) != len(pairs):
            raise ValueError("Duplicate immutable pair IDs")

        source_steps = _source_step_map(records)
        query_rows_by_id = {str(row["state_example_id"]): row for row in query_manifest["query_rows"]}
        train_tasks = {str(row["task_id"]) for row in query_manifest["query_rows"] if row["split"] == "train"}
        validation_tasks = {str(row["task_id"]) for row in query_manifest["query_rows"] if row["split"] == "validation"}
        _assert_count("train query tasks", len(train_tasks), expected["train_query_tasks"])
        _assert_count("validation query tasks", len(validation_tasks), expected["validation_query_tasks"])

        all_query_signatures: dict[str, dict[str, Any]] = {}
        signature_rows: list[dict[str, Any]] = []
        for index, example in enumerate(examples):
            state_id = state_example_id(index, example)
            task = _task_id(example)
            key = (task, int(example.step_id))
            if key not in source_steps:
                raise ValueError(f"Missing source step for query: {key}")
            if source_steps[key]["response"] != str(example.target_text).strip():
                raise ValueError(f"Target differs from source trajectory step: {key}")
            target_signature = canonical_procedure_signature(
                example.target_text, context_text=example.state_text
            )
            stage_signature = state_stage_signature(example.state_text)
            successor_signature = observation_signature(source_steps[key]["observation"])
            step_count = len(next(record for record in records if record.task_id == task).raw_trajectory["steps"])
            row = {
                "kind": "query",
                "state_example_id": state_id,
                "example_index": index,
                "task_id": task,
                "split": "train" if task in train_tasks else "validation" if task in validation_tasks else "unassigned",
                "step_id": int(example.step_id),
                "step_count": step_count,
                "step_bucket": _step_bucket(int(example.step_id), step_count),
                "state_sha256": sha256_text(example.state_text),
                "target_sha256": sha256_text(example.target_text),
                "target_signature": target_signature,
                "state_stage_signature": stage_signature,
                "oracle_successor_observation_signature": successor_signature,
                "target_matches_source_trajectory": True,
            }
            all_query_signatures[state_id] = row
            signature_rows.append(row)

        transition_by_id: dict[str, dict[str, Any]] = {}
        for transition in transitions:
            transition_id = str(transition["transition_id"])
            action = canonical_procedure_signature(
                str(transition["complete_action"]),
                context_text=str(transition["canonical_pre_action_state"]),
            )
            stage = state_stage_signature(str(transition["canonical_pre_action_state"]))
            observation = observation_signature(str(transition["complete_post_action_observation"]))
            row = {
                "kind": "transition",
                "transition_id": transition_id,
                "parent_id": str(transition["parent_memory_id"]),
                "parent_task_id": str(transition["parent_task_id"]),
                "step_index": int(transition["step_index"]),
                "action_sha256": str(transition["complete_action_sha256"]),
                "pre_state_sha256": str(transition["canonical_pre_action_state_sha256"]),
                "observation_sha256": str(transition["complete_post_action_observation_sha256"]),
                "transition_content_sha256": str(transition["transition_content_sha256"]),
                "action_signature": action,
                "pre_action_stage_signature": stage,
                "post_action_observation_signature": observation,
            }
            transition_by_id[transition_id] = row
            signature_rows.append(row)
        leakage_paths = [
            {"kind": row["kind"], "id": str(row.get("state_example_id") or row.get("transition_id")), "paths": paths}
            for row in signature_rows
            if (paths := _credential_leakage_paths(row))
        ]
        if leakage_paths:
            atomic_write_json(
                args.artifact_dir / "credential_leakage_diagnostics.json",
                {"count": len(leakage_paths), "rows": leakage_paths},
            )
            raise ValueError(
                "Canonical procedural signature leaked raw email/phone-like values; "
                f"diagnostic paths={leakage_paths[:3]}"
            )
        write_jsonl(args.artifact_dir / "procedural_signatures.jsonl", signature_rows)

        label_rows: list[dict[str, Any]] = []
        for pair in pairs:
            state_id = str(pair["state_example_id"])
            transition_id = str(pair["transition_id"])
            query = all_query_signatures[state_id]
            transition = transition_by_id[transition_id]
            compatibility = procedural_compatibility(
                query["target_signature"],
                query["state_stage_signature"],
                transition["action_signature"],
                transition["pre_action_stage_signature"],
                transition["post_action_observation_signature"],
            )
            label_rows.append(
                {
                    "format": PROCEDURAL_LABEL_VERSION,
                    "pair_id": str(pair["pair_id"]),
                    "cell": canonical_two_axis_cell(pair["cell"]),
                    "state_example_id": state_id,
                    "state_task_id": str(pair["state_task_id"]),
                    "state_split": str(pair["state_split"]),
                    "transition_id": transition_id,
                    "transition_parent_id": str(pair["transition_parent_id"]),
                    "transition_parent_task_id": str(pair["transition_parent_task_id"]),
                    "transition_split": str(pair["transition_split"]),
                    "procedural_tier": int(compatibility["tier"]),
                    "query_primary_app": query["target_signature"]["primary_app"],
                    "query_primary_api": query["target_signature"]["primary_api"],
                    "query_coarse_action_type": query["target_signature"]["coarse_action_type"],
                    "transition_primary_app": transition["action_signature"]["primary_app"],
                    "transition_primary_api": transition["action_signature"]["primary_api"],
                    "transition_coarse_action_type": transition["action_signature"]["coarse_action_type"],
                    "query_signature_sha256": query["target_signature"]["signature_sha256"],
                    "query_stage_sha256": query["state_stage_signature"]["signature_sha256"],
                    "transition_signature_sha256": transition["action_signature"]["signature_sha256"],
                    "transition_stage_sha256": transition["pre_action_stage_signature"]["signature_sha256"],
                    "transition_observation_sha256": transition["post_action_observation_signature"]["signature_sha256"],
                    "text_utility": float(pair["text_utility"]),
                    **compatibility,
                }
            )
        if len({row["pair_id"] for row in label_rows}) != len(label_rows):
            raise ValueError("Duplicate procedural pair labels")
        write_jsonl(args.artifact_dir / "procedural_label_rows.jsonl", label_rows)
        coverage = summarize_label_coverage(label_rows)
        hard_pairs = _hard_pair_coverage(label_rows)
        behavior_queries = _select_behavior_queries(
            query_manifest["query_rows"],
            all_query_signatures,
            validation_tasks=validation_tasks,
            count_per_task=int(settings["replay"]["audit_states_per_heldout_task"]),
            seed=int(settings["seed"]),
        )
        atomic_write_json(args.artifact_dir / "one_step_query_manifest.json", behavior_queries)
        b_coverage = float(coverage["cells"]["B"]["tier3_or_4_state_coverage"])
        gate_passed = b_coverage >= float(settings["labels"]["heldout_high_tier_coverage_gate"])
        signature_summary = {
            "format": PROCEDURE_SIGNATURE_VERSION,
            "query_count": len(all_query_signatures),
            "panel_query_count": len(query_rows_by_id),
            "transition_count": len(transition_by_id),
            "query_parse_status": dict(Counter(row["target_signature"]["parse_status"] for row in all_query_signatures.values())),
            "transition_parse_status": dict(Counter(row["action_signature"]["parse_status"] for row in transition_by_id.values())),
            "query_action_types": dict(Counter(row["target_signature"]["coarse_action_type"] for row in all_query_signatures.values())),
            "transition_action_types": dict(Counter(row["action_signature"]["coarse_action_type"] for row in transition_by_id.values())),
            "raw_credential_leakage_rows": 0,
            "target_source_mismatch_count": 0,
            "signature_rows_sha256": sha256_file(args.artifact_dir / "procedural_signatures.jsonl"),
        }
        summary = {
            "format": "procedural_supervision_preflight_6f_v1",
            "status": "ready_for_model_training" if gate_passed else "transition_panel_procedural_coverage_insufficient",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "immutable_contract": {
                "queries": query_manifest["query_count"],
                "train_queries": query_manifest["train_query_count"],
                "heldout_queries": query_manifest["validation_query_count"],
                "transitions": len(transitions),
                "legal_rows": expected["legal_rows"],
                "scoreable_rows": len(pairs),
                "over_context_rows": expected["over_context_rows"],
                "cell_counts": dict(cell_counts),
            },
            "signatures": signature_summary,
            "labels": {
                "row_count": len(label_rows),
                "coverage": coverage,
                "hard_same_intent_pairs": hard_pairs,
                "rows_sha256": sha256_file(args.artifact_dir / "procedural_label_rows.jsonl"),
            },
            "behavior_queries": behavior_queries,
            "gate": {
                "heldout_train_bank_coverage": b_coverage,
                "threshold": float(settings["labels"]["heldout_high_tier_coverage_gate"]),
                "passed": gate_passed,
            },
            "runtime_seconds": time.perf_counter() - started,
            "hard_scope": {
                "qwen_forward_calls": 0,
                "appworld_instances": 0,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
                "production_field_training": False,
                "appworld_generation_or_full_evaluation": False,
                "stage_c2": False,
                "end_to_end_rcmf": False,
                "demo_changed": False,
                "injection_changed": False,
                "v4_tag_changed": False,
            },
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "procedural_signature_report.md", _report(summary))
        atomic_write_text(args.artifact_dir / "procedural_label_coverage_report.md", _report(summary))
        attempt.checkpoint("preflight_summary.json")
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
