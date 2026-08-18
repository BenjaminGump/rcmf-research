from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.procedural_supervision_6f import stable_hash
from rcmf.training.selector_behavioral_missing_7cr import (
    MISSING_POLICY_VERSION,
    mark_executable,
    mark_over_context_missing,
    validate_logical_manifest,
)
from rcmf.training.signature_balanced_field_7c import condition_semantic_key
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
    validate_or_record_run_manifest_config_supersession,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.prepare_field_selector_audit_7c import (
    _condition_row as _parent_condition_row,
    _json,
    _rows,
    _runtime_projection,
    _selected_classes,
)
from scripts.prepare_procedural_causal_audit_6h import _prompt_preflight


MANIFEST_FORMAT = "missing_control_selector_condition_manifest_7cr_v1"


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _attempt_ids(path: Path) -> set[str]:
    return (
        {str(row["attempt_id"]) for row in read_jsonl(path)}
        if path.exists()
        else set()
    )


def _condition_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": MANIFEST_FORMAT,
        "state_example_id": str(row["state_example_id"]),
        "condition_name": str(row["condition_name"]),
        "prompt_kind": str(row["prompt_kind"]),
        "transition_id": str(row["transition_id"]),
        "selected_class_id": str(row["signature_class_id"]),
    }


def _rekey(row: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["format"] = MANIFEST_FORMAT
    output["condition_key"] = stable_hash(_condition_identity(output))
    output["semantic_prompt_key"] = condition_semantic_key(output)
    return output


def _missing_condition_row(
    *,
    state: Mapping[str, Any],
    class_row: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    selector_source: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    policy = settings["missing_policy"]
    member_ids = {str(value) for value in class_row["member_transition_ids"]}
    if len(member_ids) != 1 or int(class_row["class_size"]) != 1:
        raise ValueError("Approved missing policy applies only to the singleton class")
    candidates = [
        row for row in rows if str(row["transition_id"]) in member_ids
    ]
    if len(candidates) != 1:
        raise ValueError("Missing selected class must have one legal state row")
    label = candidates[0]
    if bool(label.get("scoreable_under_context", True)):
        raise ValueError("Approved missing class unexpectedly became scoreable")
    transition_id = str(label["transition_id"])
    if transition_id != str(policy["transition_id"]):
        raise ValueError("Frozen missing transition identity changed")
    output = {
        "format": MANIFEST_FORMAT,
        "state_example_id": str(state["state_example_id"]),
        "state_task_id": str(state["task_id"]),
        "state_step_id": int(state["step_id"]),
        "audit_stratum": str(state["stratum"]),
        "condition_name": str(policy["condition_name"]),
        "prompt_kind": "raw_transition",
        "transition_id": transition_id,
        "transition_parent_id": str(label["transition_parent_id"]),
        "transition_parent_task_id": str(label["transition_parent_task_id"]),
        "transition_split": str(label["transition_split"]),
        "signature_class_id": str(class_row["signature_class_id"]),
        "selected_class_id": str(class_row["signature_class_id"]),
        "signature_sha256": str(class_row["signature_sha256"]),
        "signature_class_size": int(class_row["class_size"]),
        "procedural_tier": int(label["procedural_tier"]),
        "exact_api_sequence": bool(label["exact_api_sequence"]),
        "state_stage_compatible": bool(label["state_stage_compatible"]),
        "api_documentation_action": bool(
            label["transition_api_documentation_action"]
        ),
        "selector_source": selector_source,
        "canonical_transition_id": str(class_row["canonical_transition_id"]),
        "scoreable_substitution": False,
        "selection_rule": "frozen_selected_singleton_class_no_scoreable_member",
    }
    output = _rekey(output)
    return mark_over_context_missing(
        output,
        prompt_tokens=int(policy["prompt_tokens"]),
        context_limit=int(policy["context_limit"]),
    )


def _reuse_contract(
    conditions: Sequence[dict[str, Any]],
    old_conditions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    old_by_semantic: dict[str, Mapping[str, Any]] = {}
    for row in old_conditions:
        old_by_semantic.setdefault(condition_semantic_key(row), row)
    first_new_by_semantic: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for row in conditions:
        semantic = str(row["semantic_prompt_key"])
        old = old_by_semantic.get(semantic)
        if old is not None:
            row["result_source"] = {
                "kind": "exp025b",
                "condition_key": str(old["condition_key"]),
                "condition_name": str(old["condition_name"]),
            }
            counts["reused_exp025b_count"] += 1
        elif semantic in first_new_by_semantic:
            source = first_new_by_semantic[semantic]
            row["result_source"] = {
                "kind": "exp025cr_alias",
                "condition_key": str(source["condition_key"]),
                "condition_name": str(source["condition_name"]),
            }
            counts["intra_exp025cr_alias_count"] += 1
        else:
            row["result_source"] = {
                "kind": "execute",
                "condition_key": str(row["condition_key"]),
                "condition_name": str(row["condition_name"]),
            }
            first_new_by_semantic[semantic] = row
            counts["new_unique_execution_count"] += 1
    return {
        "executable_condition_count": len(conditions),
        "reused_exp025b_count": counts["reused_exp025b_count"],
        "intra_exp025cr_alias_count": counts["intra_exp025cr_alias_count"],
        "new_unique_execution_count": counts["new_unique_execution_count"],
        "unique_semantic_prompt_count": len(
            {str(row["semantic_prompt_key"]) for row in conditions}
        ),
    }


def _validate_selector_artifacts(
    selector_summary: Mapping[str, Any],
    *,
    parent_exp025c: Path,
    expected_ensemble_sha256: str,
) -> dict[str, Any]:
    ensemble_path = parent_exp025c / "selector/ensemble_scores.pt"
    ensemble_sha = sha256_file(ensemble_path)
    if ensemble_sha != expected_ensemble_sha256:
        raise ValueError("Frozen selector ensemble SHA256 differs")
    if str(selector_summary["ensemble"]["sha256"]) != ensemble_sha:
        raise ValueError("Selector summary and ensemble SHA256 differ")
    seed_rows = selector_summary["seed_reports"]
    if len(seed_rows) != 3:
        raise ValueError("Frozen selector must contain exactly three seeds")
    validated = []
    for row in seed_rows:
        checkpoint = Path(str(row["checkpoint"]))
        score_path = Path(str(row["score_path"]))
        checkpoint_sha = sha256_file(checkpoint)
        score_sha = sha256_file(score_path)
        if checkpoint_sha != str(row["checkpoint_sha256"]):
            raise ValueError("Frozen seed checkpoint SHA256 differs")
        if score_sha != str(row["score_sha256"]):
            raise ValueError("Frozen seed score SHA256 differs")
        validated.append(
            {
                "seed": int(row["seed"]),
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": checkpoint_sha,
                "score_path": str(score_path),
                "score_sha256": score_sha,
            }
        )
    return {
        "ensemble_path": str(ensemble_path),
        "ensemble_sha256": ensemble_sha,
        "seed_checkpoints": validated,
        "calibration": selector_summary["ensemble"]["train_calibration"],
        "strict_b_gate_passed": bool(selector_summary["gates"]["strict_b"]["passed"]),
        "deployment_e_gate_passed": bool(
            selector_summary["gates"]["deployment_e"]["passed"]
        ),
        "heldout_parent_d_gate_passed": bool(
            selector_summary["gates"]["heldout_parent_d"]["passed"]
        ),
    }


def _report(summary: Mapping[str, Any]) -> str:
    reuse = summary["reuse"]
    runtime = summary["runtime_projection"]
    missing = summary["missing_record"]
    return "\n".join(
        [
            "# EXP-025C-R Missing-Control-Aware Preflight",
            "",
            f"- logical/executable/missing slots: "
            f"`{summary['logical_slot_count']}/{summary['executable_slot_count']}/"
            f"{summary['missing_slot_count']}`",
            f"- missing state/condition: `{missing['state_example_id']}` / "
            f"`{missing['condition_name']}`",
            f"- missing prompt/context: `{missing['prompt_tokens']}` / "
            f"`{missing['context_limit']}` tokens",
            f"- EXP-025B outputs reused: `{reuse['reused_exp025b_count']}`",
            f"- in-run semantic aliases: `{reuse['intra_exp025cr_alias_count']}`",
            f"- new Qwen generations/AppWorld executions: "
            f"`{reuse['new_unique_execution_count']}`",
            f"- best/expected/conservative H100 hours: "
            f"`{runtime['scenarios']['best']['h100_hours']:.4f}` / "
            f"`{runtime['scenarios']['expected']['h100_hours']:.4f}` / "
            f"`{runtime['scenarios']['conservative']['h100_hours']:.4f}`",
            f"- expected wall hours: "
            f"`{runtime['scenarios']['expected']['wall_hours']:.4f}`",
            f"- projected artifact bytes: `{runtime['projected_artifact_bytes']}`",
            f"- exceeds review threshold: "
            f"`{runtime['requires_explicit_runtime_approval']}`",
            "",
            "The frozen selected class is retained for the missing F5 slot. No "
            "fallback, truncation, reranking, recalibration, or imputation is used.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_signature_balanced_field_7cr.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025cr")
    parser.add_argument("--supersede-config-sha256")
    parser.add_argument("--config-supersession-reason")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7cr"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    selector_root = parent_c / "selector"
    clean_audit = parent_b / "clean_procedural_audit"
    clean_cache = parent_b / "clean_cache_rebuild"
    paths = {
        "selector_summary": selector_root / "selector_summary.json",
        "selector_ensemble": selector_root / "ensemble_scores.pt",
        "strict_scores": selector_root / "evaluation/B_correct_state_metrics.jsonl",
        "deployment_scores": selector_root
        / "evaluation/E_correct_state_metrics.jsonl",
        "intent_scores": selector_root
        / "evaluation/E_predicted_intent_state_metrics.jsonl",
        "strata": clean_audit / "clean_audit_state_strata.json",
        "one_step_labels": clean_audit / "clean_one_step_procedural_labels.jsonl",
        "classes": clean_audit / "clean_signature_equivalence_manifest.json",
        "transitions": clean_cache / "transition_preflight/transition_manifest.jsonl",
        "signatures": clean_audit / "clean_transition_signature_manifest.jsonl",
        "old_conditions": clean_audit / "clean_condition_manifest.json",
        "old_generation_summary": parent_b / "generation_summary.json",
        "clean_decisions": Path(str(settings["reconciled_corpus_dir"]))
        / "decision_examples.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Audit preflight input missing: {name}={path}")
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    command_scope = ["missing_policy", "preflight", "smoke", "formal", "analysis"]

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="missing_policy_manifest_and_runtime_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.supersede_config_sha256:
            validate_or_record_run_manifest_config_supersession(
                args.artifact_dir / "run_manifest.json",
                run_uuid=str(settings["run_uuid"]),
                previous_config_sha256=args.supersede_config_sha256,
                replacement_config_sha256=config_hash,
                data_manifest_hashes=source_hashes,
                source_commit=args.lambda_head,
                command_scope=command_scope,
                parent_attempt_id=args.parent_attempt_id,
                reason=str(args.config_supersession_reason or ""),
            )
        else:
            initialize_or_validate_run_manifest(
                args.artifact_dir / "run_manifest.json",
                run_uuid=str(settings["run_uuid"]),
                config_sha256=config_hash,
                data_manifest_hashes=source_hashes,
                source_commit=args.lambda_head,
                command_scope=command_scope,
            )
        selector_summary = _json(paths["selector_summary"])
        selector_validation = _validate_selector_artifacts(
            selector_summary,
            parent_exp025c=parent_c,
            expected_ensemble_sha256=str(
                settings["expected_selector_ensemble_sha256"]
            ),
        )
        if not all(
            selector_validation[name]
            for name in (
                "strict_b_gate_passed",
                "deployment_e_gate_passed",
                "heldout_parent_d_gate_passed",
            )
        ):
            raise RuntimeError("Frozen selector gates are not all passed")
        old_generation = _json(paths["old_generation_summary"])
        if not bool(old_generation["passed"]) or int(
            old_generation["condition_count"]
        ) != int(settings["expected"]["parent_conditions"]):
            raise RuntimeError("EXP-025B formal condition outputs are incomplete")
        strict = _selected_classes(paths["strict_scores"])
        deployment = _selected_classes(paths["deployment_scores"])
        intent = _selected_classes(paths["intent_scores"])
        states = {
            str(row["state_example_id"]): row
            for row in _json(paths["strata"])["rows"]
        }
        if not (
            set(states) <= set(strict)
            and set(states) <= set(deployment)
            and set(states) <= set(intent)
        ):
            raise ValueError("Frozen selector predictions do not cover all audit states")
        all_labels_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
        scoreable_labels_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _rows(paths["one_step_labels"]):
            state_id = str(row["state_example_id"])
            all_labels_by_state[state_id].append(row)
            if bool(row["scoreable_under_context"]):
                scoreable_labels_by_state[state_id].append(row)
        class_rows = _json(paths["classes"])["classes"]
        classes = {str(row["signature_class_id"]): row for row in class_rows}
        transitions = _rows(paths["transitions"])
        transitions_by_id = {str(row["transition_id"]): row for row in transitions}
        definitions = (
            ("F1_strict_b_field_raw", "raw_transition", strict, "field_strict_b", "train"),
            ("F2_strict_b_field_signature", "signature_card", strict, "field_strict_b", "train"),
            ("F3_deployment_e_field_raw", "raw_transition", deployment, "field_deployment_e", None),
            ("F4_deployment_e_field_signature", "signature_card", deployment, "field_deployment_e", None),
            ("F5_predicted_intent_raw", "raw_transition", intent, "predicted_intent", None),
        )
        conditions: list[dict[str, Any]] = []
        policy = settings["missing_policy"]
        for state_id in sorted(states):
            for name, prompt_kind, selections, source, split in definitions:
                class_id = selections[state_id]
                if (
                    state_id == str(policy["state_example_id"])
                    and name == str(policy["condition_name"])
                ):
                    if class_id != str(policy["signature_class_id"]):
                        raise ValueError("Frozen missing selected class changed")
                    condition = _missing_condition_row(
                        state=states[state_id],
                        class_row=classes[class_id],
                        rows=all_labels_by_state[state_id],
                        selector_source=source,
                        settings=settings,
                    )
                else:
                    condition = _parent_condition_row(
                        state=states[state_id],
                        condition_name=name,
                        prompt_kind=prompt_kind,
                        selected_class_id=class_id,
                        rows=scoreable_labels_by_state[state_id],
                        class_row=classes[class_id],
                        transitions_by_id=transitions_by_id,
                        selector_source=source,
                        transition_split=split,
                    )
                    condition = mark_executable(_rekey(condition))
                conditions.append(condition)

        accounting = validate_logical_manifest(
            conditions,
            expected_state_count=int(settings["expected"]["audit_states"]),
            expected_missing_state_id=str(policy["state_example_id"]),
            expected_missing_condition=str(policy["condition_name"]),
            expected_missing_class_id=str(policy["signature_class_id"]),
            expected_prompt_tokens=int(policy["prompt_tokens"]),
            expected_context_limit=int(policy["context_limit"]),
        )
        executable = [row for row in conditions if bool(row["valid_for_generation"])]
        missing = [row for row in conditions if not bool(row["valid_for_generation"])]
        reuse = _reuse_contract(
            executable, _json(paths["old_conditions"])["conditions"]
        )
        manifest = {
            "format": MANIFEST_FORMAT,
            "missing_policy_version": MISSING_POLICY_VERSION,
            "run_uuid": str(settings["run_uuid"]),
            "structural_corpus_lineage_sha256": str(
                settings["expected_structural_lineage_sha256"]
            ),
            "replay_validated_contract_lineage_sha256": str(
                settings["expected_replay_lineage_sha256"]
            ),
            "state_count": int(settings["expected"]["audit_states"]),
            "task_count": int(settings["expected"]["audit_tasks"]),
            "logical_slot_count": len(conditions),
            "executable_slot_count": len(executable),
            "missing_slot_count": len(missing),
            "condition_name_counts": dict(
                sorted(Counter(row["condition_name"] for row in conditions).items())
            ),
            "accounting": accounting,
            "reuse": reuse,
            "selector_validation": selector_validation,
            "selector_summary_sha256": source_hashes["selector_summary"],
            "selector_ensemble_sha256": selector_validation["ensemble_sha256"],
            "conditions": conditions,
        }
        manifest["manifest_sha256"] = stable_hash(manifest)
        manifest_path = args.artifact_dir / "selector_condition_manifest.json"
        atomic_write_json(manifest_path, manifest)
        atomic_write_json(args.artifact_dir / "missing_f5_record.json", missing[0])

        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["generation"]["model_name"]),
            use_fast=True,
            local_files_only=True,
        )
        from rcmf.training.datasets import load_decision_examples
        from rcmf.training.transition_memory_6a import state_example_id

        examples = load_decision_examples(paths["clean_decisions"])
        prompt_rows, prompt_summary = _prompt_preflight(
            tokenizer=tokenizer,
            examples_by_state={
                state_example_id(index, example): example
                for index, example in enumerate(examples)
            },
            conditions=executable,
            transitions_by_id=transitions_by_id,
            signatures_by_id={
                str(row["transition_id"]): row for row in _rows(paths["signatures"])
            },
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["generation"]["context_limit"]),
            requested_new_tokens=int(settings["generation"]["max_new_tokens"]),
        )
        if len(prompt_rows) != len(executable):
            raise RuntimeError("Executable context preflight count differs")
        if int(prompt_summary["truncated_count"]) != 0:
            raise RuntimeError("Selector audit preflight attempted truncation")
        if any(
            int(row["prompt_tokens"]) > int(settings["generation"]["context_limit"])
            for row in prompt_rows
        ):
            raise RuntimeError("Executable selector condition exceeds context")
        _atomic_jsonl(
            args.artifact_dir / "selector_condition_prompt_preflight.jsonl",
            prompt_rows,
        )
        runtime = _runtime_projection(
            conditions=len(executable),
            executions=int(reuse["new_unique_execution_count"]),
            settings=settings,
        )
        summary = {
            "format": "missing_control_selector_audit_preflight_7cr_v1",
            "status": "completed",
            **accounting,
            "reuse": reuse,
            "selector_validation": selector_validation,
            "scoreable_substitution_count": sum(
                bool(row["scoreable_substitution"]) for row in executable
            ),
            "cross_class_fallback_count": 0,
            "changed_selection_count": 0,
            "truncation_count": 0,
            "over_context_executable_count": 0,
            "prompt_preflight": prompt_summary,
            "runtime_projection": runtime,
            "missing_record": missing[0],
            "manifest_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "selections_locked_before_generation": True,
        }
        summary_path = args.artifact_dir / "selector_audit_preflight.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(
            args.artifact_dir / "selector_audit_preflight.md", _report(summary)
        )
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
