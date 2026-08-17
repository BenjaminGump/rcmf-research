from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_6h import (
    build_condition_manifest,
    build_signature_equivalence_manifest,
    classify_audit_states,
    validate_audit_label_coverage,
)
from rcmf.training.procedural_causal_audit_7b import (
    CLEAN_CONDITION_MANIFEST_VERSION,
    compare_condition_manifests,
    generation_runtime_projection,
)
from rcmf.training.procedural_supervision_6f import (
    canonical_procedure_signature,
    observation_signature,
    stable_hash,
    state_stage_signature,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.prepare_procedural_causal_audit_6h import _prompt_preflight
from scripts.prepare_procedural_coverage_6g import (
    _build_label_rows,
    _full_transition_signatures,
    _resumable_preflight,
)
from scripts.prepare_procedural_supervision_6f import (
    _signature_credential_leakage_paths,
    _source_step_map,
    _step_bucket,
    _task_id,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _attempt_ids(path: Path) -> set[str]:
    return {str(row["attempt_id"]) for row in read_jsonl(path)} if path.exists() else set()


def _query_signatures(
    examples: Sequence[Any], records: Sequence[Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    source_steps = _source_step_map(records)
    records_by_task = {str(record.task_id): record for record in records}
    rows = []
    keyed = {}
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        task_id = _task_id(example)
        source = source_steps.get((task_id, int(example.step_id)))
        if source is None:
            raise ValueError(f"Missing source trajectory step: {task_id}/{example.step_id}")
        if source["response"] != str(example.target_text).strip():
            raise ValueError(f"Decision target differs from source step: {state_id}")
        step_count = len(records_by_task[task_id].raw_trajectory["steps"])
        row = {
            "format": "identity_reconciled_query_procedure_signature_7b_v1",
            "kind": "query",
            "state_example_id": state_id,
            "example_index": index,
            "task_id": task_id,
            "step_id": int(example.step_id),
            "step_count": step_count,
            "step_bucket": _step_bucket(int(example.step_id), step_count),
            "state_sha256": sha256_text(str(example.state_text)),
            "target_sha256": sha256_text(str(example.target_text)),
            "target_signature": canonical_procedure_signature(
                str(example.target_text), context_text=str(example.state_text)
            ),
            "state_stage_signature": state_stage_signature(str(example.state_text)),
            "oracle_successor_observation_signature": observation_signature(source["observation"]),
            "target_matches_source_trajectory": True,
        }
        if state_id in keyed:
            raise ValueError(f"Duplicate decision identity: {state_id}")
        keyed[state_id] = row
        rows.append(row)
    return rows, keyed


def _clean_audit_rows(
    old_manifest: Mapping[str, Any],
    query_signatures: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for original in old_manifest["rows"]:
        state_id = str(original["state_example_id"])
        current = query_signatures.get(state_id)
        if current is None:
            raise ValueError(f"Immutable audit state is absent from clean corpus: {state_id}")
        output.append(
            {
                **dict(original),
                "task_id": str(current["task_id"]),
                "step_id": int(current["step_id"]),
                "step_count": int(current["step_count"]),
                "step_bucket": str(current["step_bucket"]),
                "coarse_action_type": str(current["target_signature"]["coarse_action_type"]),
                "target_signature_sha256": str(current["target_signature"]["signature_sha256"]),
                "state_sha256": str(current["state_sha256"]),
                "corpus_identity_reconciled": True,
            }
        )
    if [row["state_example_id"] for row in output] != [
        row["state_example_id"] for row in old_manifest["rows"]
    ]:
        raise AssertionError("Immutable audit-state order changed")
    return output


def _parent_split(
    old_labels: Sequence[Mapping[str, Any]],
    clean_transitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_task: dict[str, str] = {}
    for row in old_labels:
        task = str(row["transition_parent_task_id"])
        split = str(row["transition_split"])
        prior = by_task.setdefault(task, split)
        if prior != split:
            raise ValueError(f"Old parent task has inconsistent split: {task}")
    by_parent = {}
    for transition in clean_transitions:
        task = str(transition["parent_task_id"])
        if task not in by_task:
            raise ValueError(f"Clean transition parent task has no locked split: {task}")
        parent = str(transition["parent_memory_id"])
        prior = by_parent.setdefault(parent, by_task[task])
        if prior != by_task[task]:
            raise ValueError(f"Clean parent has inconsistent split: {parent}")
    if Counter(by_parent.values()) != Counter({"train": 29, "heldout": 8}):
        raise ValueError(f"Parent split changed: {Counter(by_parent.values())}")
    return {
        "format": "identity_reconciled_locked_parent_split_7b_v1",
        "split_by_parent": by_parent,
        "split_by_parent_task": by_task,
        "train_parent_count": 29,
        "heldout_parent_count": 8,
    }


def _markdown(summary: Mapping[str, Any]) -> str:
    runtime = summary["runtime_projection"]
    return "\n".join(
        [
            "# EXP-025B Clean Procedural Causal-Audit Preflight",
            "",
            "## VERIFIED",
            "",
            f"- reconciled states: `{summary['audit_state_count']}` across `{summary['audit_task_count']}` tasks",
            f"- clean transitions: `{summary['transition_count']}`",
            f"- clean signature classes: `{summary['signature_class_count']}`",
            f"- legal/scoreable pairs: `{summary['legal_pair_count']}` / `{summary['scoreable_pair_count']}`",
            f"- frozen conditions: `{summary['condition_count']}`",
            f"- context-limited conditions: `{summary['prompt_preflight']['context_limited_completion_count']}`",
            f"- no truncation: `{summary['prompt_preflight']['truncated_count'] == 0}`",
            "",
            "## Runtime",
            "",
            f"- best H100 hours: `{runtime['scenarios']['best']['h100_hours']:.3f}`",
            f"- expected H100 hours: `{runtime['scenarios']['expected']['h100_hours']:.3f}`",
            f"- conservative H100 hours: `{runtime['scenarios']['conservative']['h100_hours']:.3f}`",
            f"- expected wall hours: `{runtime['scenarios']['expected']['wall_seconds'] / 3600.0:.3f}`",
            f"- projected artifact GiB: `{runtime['projected_artifact_gib']:.3f}`",
            f"- exceeds 12-H100-hour review threshold: `{runtime['requires_explicit_runtime_approval']}`",
            "",
            "All C0-C8 conditions were selected before generation and without Qwen outputs, "
            "AppWorld outcomes, raw-NLL utility, or historical model scores.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--tmux-session", default="exp025b")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7b"]
    audit = settings["causal_audit"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    output = args.artifact_dir / "clean_procedural_audit"
    output.mkdir(parents=True, exist_ok=True)
    clean_data = Path(str(settings["reconciled_corpus_dir"]))
    clean_transition_path = (
        Path(str(settings["cache_rebuild"]["output_root"]))
        / "transition_preflight/transition_manifest.jsonl"
    )
    paths = {
        "clean_decisions": clean_data / "decision_examples.jsonl",
        "clean_memories": clean_data / "memory_records.jsonl",
        "clean_transitions": clean_transition_path,
        "replay_validated_contract": args.artifact_dir / "replay_validated_corpus_manifest.json",
        "old_audit_queries": Path(str(audit["exp022_artifact"])) / "one_step_query_manifest.json",
        "old_signatures": Path(str(audit["exp023_artifact"]))
        / "full_transition_signature_manifest.jsonl",
        "old_labels": Path(str(audit["exp023_artifact"])) / "full_procedural_label_rows.jsonl",
        "old_conditions": Path(str(audit["exp024a_artifact"])) / "condition_manifest.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Required clean/audit input missing: {name}={path}")
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="clean_procedural_condition_manifest_and_runtime_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        examples = load_decision_examples(paths["clean_decisions"])
        records = load_memory_records(paths["clean_memories"])
        transitions = _rows(paths["clean_transitions"])
        old_signatures = _rows(paths["old_signatures"])
        old_labels = _rows(paths["old_labels"])
        old_audit = _json(paths["old_audit_queries"])
        old_conditions = _json(paths["old_conditions"])
        if len(examples) != 638 or len(records) != 46 or len(transitions) != 499:
            raise ValueError("Clean structural counts differ from 638/46/499")

        query_rows, query_by_id = _query_signatures(examples, records)
        audit_rows = _clean_audit_rows(old_audit, query_by_id)
        if len(audit_rows) != 45 or len({row["task_id"] for row in audit_rows}) != 9:
            raise ValueError("Clean audit state/task counts differ from 45/9")
        clean_audit_manifest = {
            **{key: value for key, value in old_audit.items() if key != "rows"},
            "format": "identity_reconciled_one_step_query_manifest_7b_v1",
            "rows": audit_rows,
            "query_count": 45,
            "task_count": 9,
            "selection_unchanged_from_exp022": True,
            "manifest_sha256": sha256_text(
                "\n".join(str(row["state_example_id"]) for row in audit_rows)
            ),
        }
        atomic_write_json(output / "clean_one_step_query_manifest.json", clean_audit_manifest)
        _atomic_jsonl(output / "clean_query_procedural_signatures.jsonl", query_rows)

        transition_signatures, signature_validation = _full_transition_signatures(
            transitions, old_signature_rows=old_signatures
        )
        if signature_validation["credential_leakage_count"]:
            raise RuntimeError("Clean transition signatures contain credential leakage")
        leakage = [
            {
                "transition_id": row["transition_id"],
                "paths": _signature_credential_leakage_paths(row),
            }
            for row in transition_signatures
            if _signature_credential_leakage_paths(row)
        ]
        if leakage:
            raise RuntimeError(f"Clean signature metadata leaked credentials: {leakage[:3]}")
        _atomic_jsonl(output / "clean_transition_signature_manifest.jsonl", transition_signatures)
        atomic_write_json(
            output / "clean_transition_signature_validation.json", signature_validation
        )
        equivalence = build_signature_equivalence_manifest(transitions, transition_signatures)
        if int(equivalence["signature_class_count"]) != 150:
            raise ValueError("Clean procedural signature count differs from 150")
        atomic_write_json(output / "clean_signature_equivalence_manifest.json", equivalence)

        parent_split = _parent_split(old_labels, transitions)
        atomic_write_json(output / "clean_parent_split_manifest.json", parent_split)
        example_indices = {
            state_example_id(index, example): index for index, example in enumerate(examples)
        }
        preflight_queries = [
            {
                **row,
                "example_index": example_indices[str(row["state_example_id"])],
                "split": "validation",
            }
            for row in audit_rows
        ]
        tokenizer = AutoTokenizer.from_pretrained(cfg.model.name, trust_remote_code=True)
        if tokenizer.pad_token_id is None and tokenizer.eos_token is not None:
            tokenizer.pad_token = tokenizer.eos_token
        preflight, illegal, resume = _resumable_preflight(
            tokenizer=tokenizer,
            examples=examples,
            query_rows=preflight_queries,
            transitions=transitions,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(audit["generation"]["context_limit"]),
            checkpoint_dir=output / "checkpoints/condition_pair_preflight",
            transition_manifest_sha256=hashes["clean_transitions"],
            attempt=attempt,
            scope="clean_one_step_45_query",
        )
        if len(preflight) + len(illegal) != 45 * 499:
            raise ValueError("Clean one-step legal/illegal rows do not cover Cartesian product")
        _atomic_jsonl(output / "clean_one_step_pair_preflight.jsonl", preflight)
        _atomic_jsonl(output / "clean_one_step_illegal_pairs.jsonl", illegal)
        transition_by_id = {str(row["transition_id"]): row for row in transition_signatures}
        labels = _build_label_rows(
            preflight_rows=preflight,
            query_signatures=query_by_id,
            transition_signatures=transition_by_id,
            parent_split=parent_split,
        )
        _atomic_jsonl(output / "clean_one_step_procedural_labels.jsonl", labels)
        coverage = validate_audit_label_coverage(
            audit_rows,
            labels,
            expected_scoreable_count=sum(not bool(row["over_context"]) for row in preflight),
        )
        atomic_write_json(output / "clean_one_step_label_validation.json", coverage)
        scoreable = [row for row in labels if bool(row["scoreable_under_context"])]
        strata = classify_audit_states(audit_rows, scoreable)
        if int(strata["primary_non_documentation_high_tier_state_count"]) < 18:
            raise RuntimeError("Clean non-documentation high-tier state gate failed")
        if int(strata["primary_non_documentation_high_tier_task_count"]) < 6:
            raise RuntimeError("Clean non-documentation high-tier task gate failed")
        atomic_write_json(output / "clean_audit_state_strata.json", strata)
        conditions = build_condition_manifest(strata, labels, equivalence)
        conditions["format"] = CLEAN_CONDITION_MANIFEST_VERSION
        conditions["corpus_lineage_sha256"] = str(settings["expected_corpus_lineage_sha256"])
        conditions.pop("manifest_sha256", None)
        conditions["manifest_sha256"] = stable_hash(conditions)
        atomic_write_json(output / "clean_condition_manifest.json", conditions)

        old_semantics = {
            str(row["transition_id"]): (
                str(row["parent_task_id"]),
                int(row["step_index"]),
            )
            for row in old_signatures
        }
        clean_semantics = {
            str(row["transition_id"]): (
                str(row["parent_task_id"]),
                int(row["step_index"]),
            )
            for row in transition_signatures
        }
        comparison = compare_condition_manifests(
            old_conditions,
            conditions,
            old_transition_semantics=old_semantics,
            clean_transition_semantics=clean_semantics,
        )
        atomic_write_json(output / "clean_condition_manifest_comparison.json", comparison)
        prompt_rows, prompt_summary = _prompt_preflight(
            tokenizer=tokenizer,
            examples_by_state={
                state_example_id(index, example): example for index, example in enumerate(examples)
            },
            conditions=conditions["conditions"],
            transitions_by_id={str(row["transition_id"]): row for row in transitions},
            signatures_by_id=transition_by_id,
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(audit["generation"]["context_limit"]),
            requested_new_tokens=int(audit["generation"]["max_new_tokens"]),
        )
        _atomic_jsonl(output / "clean_condition_prompt_preflight.jsonl", prompt_rows)
        runtime = generation_runtime_projection(len(conditions["conditions"]), 45, audit)
        summary = {
            "format": "identity_reconciled_causal_audit_preflight_7b_v1",
            "status": (
                "paused_projected_runtime_requires_review"
                if runtime["requires_explicit_runtime_approval"]
                else "ready_for_lifecycle_smoke"
            ),
            "source_commit": args.lambda_head,
            "corpus_lineage_sha256": settings["expected_corpus_lineage_sha256"],
            "audit_state_count": 45,
            "audit_task_count": 9,
            "transition_count": 499,
            "signature_class_count": equivalence["signature_class_count"],
            "legal_pair_count": len(preflight),
            "illegal_pair_count": len(illegal),
            "scoreable_pair_count": sum(not bool(row["over_context"]) for row in preflight),
            "over_context_pair_count": sum(bool(row["over_context"]) for row in preflight),
            "condition_count": len(conditions["conditions"]),
            "condition_counts": conditions["condition_counts"],
            "prompt_preflight": prompt_summary,
            "runtime_projection": runtime,
            "resume": resume,
            "condition_comparison": {
                "old_count": comparison["old_condition_count"],
                "clean_count": comparison["clean_condition_count"],
                "classification_counts": comparison["classification_counts"],
            },
            "elapsed_seconds": time.perf_counter() - started,
            "hard_scope": {
                "qwen_forward_calls": 0,
                "appworld_instances": 0,
                "conditions_selected_before_generation": True,
                "raw_nll_used_for_selection": False,
                "historical_model_scores_used_for_selection": False,
                "no_truncation": prompt_summary["truncated_count"] == 0,
            },
        }
        atomic_write_json(output / "clean_causal_audit_preflight_summary.json", summary)
        atomic_write_text(output / "clean_causal_audit_preflight_report.md", _markdown(summary))
        attempt.progress(
            status=summary["status"],
            completed_conditions=len(conditions["conditions"]),
            latest_validated_checkpoint=str(output / "clean_causal_audit_preflight_summary.json"),
        )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
