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
from rcmf.training.signature_balanced_field_7c import (
    condition_semantic_key,
    select_scoreable_class_exemplar,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.prepare_procedural_causal_audit_6h import _prompt_preflight


MANIFEST_FORMAT = "signature_balanced_selector_condition_manifest_7c_v1"


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


def _selected_classes(path: Path) -> dict[str, str]:
    return {
        str(row["state_example_id"]): str(row["top1_class_id"])
        for row in _rows(path)
    }


def _condition_row(
    *,
    state: Mapping[str, Any],
    condition_name: str,
    prompt_kind: str,
    selected_class_id: str,
    rows: Sequence[Mapping[str, Any]],
    class_row: Mapping[str, Any],
    transitions_by_id: Mapping[str, Mapping[str, Any]],
    selector_source: str,
    transition_split: str | None,
) -> dict[str, Any]:
    candidate_rows = [
        row
        for row in rows
        if transition_split is None
        or str(row["transition_split"]) == transition_split
    ]
    exemplar = select_scoreable_class_exemplar(
        class_row=class_row,
        legal_rows=candidate_rows,
        transitions_by_id=transitions_by_id,
    )
    transition_id = str(exemplar["transition_id"])
    label = next(
        row
        for row in candidate_rows
        if str(row["transition_id"]) == transition_id
    )
    identity = {
        "format": MANIFEST_FORMAT,
        "state_example_id": str(state["state_example_id"]),
        "condition_name": condition_name,
        "prompt_kind": prompt_kind,
        "transition_id": transition_id,
        "selected_class_id": selected_class_id,
    }
    output = {
        "format": MANIFEST_FORMAT,
        "condition_key": stable_hash(identity),
        "state_example_id": str(state["state_example_id"]),
        "state_task_id": str(state["task_id"]),
        "state_step_id": int(state["step_id"]),
        "audit_stratum": str(state["stratum"]),
        "condition_name": condition_name,
        "prompt_kind": prompt_kind,
        "transition_id": transition_id,
        "transition_parent_id": str(label["transition_parent_id"]),
        "transition_parent_task_id": str(label["transition_parent_task_id"]),
        "transition_split": str(label["transition_split"]),
        "signature_class_id": selected_class_id,
        "signature_sha256": str(class_row["signature_sha256"]),
        "signature_class_size": int(class_row["class_size"]),
        "procedural_tier": int(label["procedural_tier"]),
        "exact_api_sequence": bool(label["exact_api_sequence"]),
        "state_stage_compatible": bool(label["state_stage_compatible"]),
        "api_documentation_action": bool(
            label["transition_api_documentation_action"]
        ),
        "selector_source": selector_source,
        **exemplar,
    }
    output["semantic_prompt_key"] = condition_semantic_key(output)
    return output


def _reuse_contract(
    conditions: Sequence[dict[str, Any]],
    old_conditions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    old_by_semantic: dict[str, Mapping[str, Any]] = {}
    for row in old_conditions:
        old_by_semantic.setdefault(condition_semantic_key(row), row)
    first_new_by_semantic: dict[str, dict[str, Any]] = {}
    old_count = 0
    alias_count = 0
    execute_count = 0
    for row in conditions:
        semantic = str(row["semantic_prompt_key"])
        old = old_by_semantic.get(semantic)
        if old is not None:
            row["result_source"] = {
                "kind": "exp025b",
                "condition_key": str(old["condition_key"]),
                "condition_name": str(old["condition_name"]),
            }
            old_count += 1
        elif semantic in first_new_by_semantic:
            source = first_new_by_semantic[semantic]
            row["result_source"] = {
                "kind": "exp025c_alias",
                "condition_key": str(source["condition_key"]),
                "condition_name": str(source["condition_name"]),
            }
            alias_count += 1
        else:
            row["result_source"] = {
                "kind": "execute",
                "condition_key": str(row["condition_key"]),
                "condition_name": str(row["condition_name"]),
            }
            first_new_by_semantic[semantic] = row
            execute_count += 1
    return {
        "condition_count": len(conditions),
        "reused_exp025b_count": old_count,
        "intra_exp025c_alias_count": alias_count,
        "new_unique_execution_count": execute_count,
        "unique_semantic_prompt_count": len(
            {str(row["semantic_prompt_key"]) for row in conditions}
        ),
    }


def _runtime_projection(
    *,
    conditions: int,
    executions: int,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    generation = settings["generation"]
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        generation_seconds = executions * float(
            generation["seconds_per_new_condition"][name]
        )
        replay_seconds = executions * float(
            generation["replay_seconds_per_condition"][name]
        )
        scenarios[name] = {
            "qwen_generation_seconds": generation_seconds,
            "appworld_replay_execution_seconds": replay_seconds,
            "h100_hours": generation_seconds / 3600.0,
            "wall_hours": (generation_seconds + replay_seconds) / 3600.0,
        }
    threshold = float(generation["review_threshold_h100_hours"])
    return {
        "condition_count": conditions,
        "qwen_generation_count": executions,
        "appworld_reconstruction_execution_count": executions,
        "scenarios": scenarios,
        "projected_artifact_bytes": executions
        * int(generation["artifact_bytes_per_condition"]),
        "review_threshold_h100_hours": threshold,
        "requires_explicit_runtime_approval": scenarios["expected"][
            "h100_hours"
        ]
        > threshold,
        "resume_plan": (
            "one atomic row per logical condition; semantic aliases materialize "
            "only after the source row validates; completed keys are hash-validated"
        ),
    }


def _report(summary: Mapping[str, Any]) -> str:
    reuse = summary["reuse"]
    runtime = summary["runtime_projection"]
    return "\n".join(
        [
            "# EXP-025C Deployable-Selection Audit Preflight",
            "",
            "## VERIFIED",
            "",
            f"- audit states: `{summary['state_count']}`",
            f"- logical F1-F5 conditions: `{summary['condition_count']}`",
            f"- EXP-025B results reused: `{reuse['reused_exp025b_count']}`",
            f"- intra-EXP-025C aliases: `{reuse['intra_exp025c_alias_count']}`",
            f"- new Qwen generations/AppWorld executions: "
            f"`{reuse['new_unique_execution_count']}`",
            f"- scoreable-exemplar substitutions: "
            f"`{summary['scoreable_substitution_count']}`",
            "",
            "## Runtime",
            "",
            f"- best/expected/conservative H100 hours: "
            f"`{runtime['scenarios']['best']['h100_hours']:.4f}` / "
            f"`{runtime['scenarios']['expected']['h100_hours']:.4f}` / "
            f"`{runtime['scenarios']['conservative']['h100_hours']:.4f}`",
            f"- expected wall hours: "
            f"`{runtime['scenarios']['expected']['wall_hours']:.4f}`",
            f"- projected artifact bytes: `{runtime['projected_artifact_bytes']}`",
            f"- exceeds 12-H100-hour review threshold: "
            f"`{runtime['requires_explicit_runtime_approval']}`",
            "",
            "All selector choices and result-reuse identities were frozen before "
            "Qwen generation or AppWorld execution.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_signature_balanced_field_7c.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--tmux-session", default="exp025c")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7c"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    parent = Path(str(settings["parent_exp025b"]))
    selector_root = args.artifact_dir / "selector"
    clean_audit = parent / "clean_procedural_audit"
    clean_cache = parent / "clean_cache_rebuild"
    paths = {
        "selector_summary": selector_root / "selector_summary.json",
        "strict_scores": selector_root / "evaluation/B_correct_state_metrics.jsonl",
        "deployment_scores": selector_root / "evaluation/E_correct_state_metrics.jsonl",
        "intent_scores": selector_root / "evaluation/E_predicted_intent_state_metrics.jsonl",
        "strata": clean_audit / "clean_audit_state_strata.json",
        "one_step_labels": clean_audit / "clean_one_step_procedural_labels.jsonl",
        "classes": clean_audit / "clean_signature_equivalence_manifest.json",
        "transitions": clean_cache / "transition_preflight/transition_manifest.jsonl",
        "signatures": clean_audit / "clean_transition_signature_manifest.jsonl",
        "old_conditions": clean_audit / "clean_condition_manifest.json",
        "old_generation_summary": parent / "generation_summary.json",
        "clean_decisions": Path(str(settings["reconciled_corpus_dir"]))
        / "decision_examples.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Audit preflight input missing: {name}={path}")
    selector_summary = _json(paths["selector_summary"])
    if not bool(selector_summary["gates"]["deployment_e"]["passed"]):
        raise RuntimeError("Deployment-E selector gate did not pass")
    old_generation = _json(paths["old_generation_summary"])
    if not bool(old_generation["passed"]) or int(old_generation["condition_count"]) != 323:
        raise RuntimeError("EXP-025B formal condition outputs are incomplete")
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="selector_condition_manifest_and_runtime_preflight",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        strict = _selected_classes(paths["strict_scores"])
        deployment = _selected_classes(paths["deployment_scores"])
        intent = _selected_classes(paths["intent_scores"])
        states = {
            str(row["state_example_id"]): row
            for row in _json(paths["strata"])["rows"]
        }
        if not (set(states) <= set(strict) and set(states) <= set(deployment) and set(states) <= set(intent)):
            raise ValueError("Selector evaluation does not cover all 45 audit states")
        labels_by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _rows(paths["one_step_labels"]):
            if bool(row["scoreable_under_context"]):
                labels_by_state[str(row["state_example_id"])].append(row)
        class_rows = _json(paths["classes"])["classes"]
        classes = {str(row["signature_class_id"]): row for row in class_rows}
        transitions = _rows(paths["transitions"])
        transitions_by_id = {str(row["transition_id"]): row for row in transitions}
        conditions = []
        definitions = (
            ("F1_strict_b_field_raw", "raw_transition", strict, "field_strict_b", "train"),
            ("F2_strict_b_field_signature", "signature_card", strict, "field_strict_b", "train"),
            ("F3_deployment_e_field_raw", "raw_transition", deployment, "field_deployment_e", None),
            ("F4_deployment_e_field_signature", "signature_card", deployment, "field_deployment_e", None),
            ("F5_predicted_intent_raw", "raw_transition", intent, "predicted_intent", None),
        )
        for state_id in sorted(states):
            for name, prompt_kind, selections, source, transition_split in definitions:
                class_id = selections[state_id]
                conditions.append(
                    _condition_row(
                        state=states[state_id],
                        condition_name=name,
                        prompt_kind=prompt_kind,
                        selected_class_id=class_id,
                        rows=labels_by_state[state_id],
                        class_row=classes[class_id],
                        transitions_by_id=transitions_by_id,
                        selector_source=source,
                        transition_split=transition_split,
                    )
                )
        if len(conditions) != 45 * 5 or len({row["condition_key"] for row in conditions}) != len(conditions):
            raise ValueError("F1-F5 condition count or identity differs")
        reuse = _reuse_contract(
            conditions, _json(paths["old_conditions"])["conditions"]
        )
        manifest = {
            "format": MANIFEST_FORMAT,
            "run_uuid": str(settings["run_uuid"]),
            "structural_corpus_lineage_sha256": str(
                settings["expected_structural_lineage_sha256"]
            ),
            "replay_validated_contract_lineage_sha256": str(
                settings["expected_replay_lineage_sha256"]
            ),
            "state_count": 45,
            "task_count": 9,
            "condition_count": len(conditions),
            "condition_name_counts": dict(
                sorted(Counter(row["condition_name"] for row in conditions).items())
            ),
            "reuse": reuse,
            "selector_summary_sha256": source_hashes["selector_summary"],
            "selector_ensemble_sha256": str(selector_summary["ensemble"]["sha256"]),
            "conditions": conditions,
        }
        manifest["manifest_sha256"] = stable_hash(manifest)
        manifest_path = args.artifact_dir / "selector_condition_manifest.json"
        atomic_write_json(manifest_path, manifest)
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
            conditions=conditions,
            transitions_by_id=transitions_by_id,
            signatures_by_id={
                str(row["transition_id"]): row for row in _rows(paths["signatures"])
            },
            prompt_profile=cfg.benchmark.prompt_profile,
            context_limit=int(settings["generation"]["context_limit"]),
            requested_new_tokens=int(settings["generation"]["max_new_tokens"]),
        )
        if int(prompt_summary["truncated_count"]) != 0:
            raise RuntimeError("Selector audit preflight attempted truncation")
        _atomic_jsonl(args.artifact_dir / "selector_condition_prompt_preflight.jsonl", prompt_rows)
        runtime = _runtime_projection(
            conditions=len(conditions),
            executions=int(reuse["new_unique_execution_count"]),
            settings=settings,
        )
        summary = {
            "format": "signature_balanced_selector_audit_preflight_7c_v1",
            "status": "completed",
            "state_count": 45,
            "task_count": 9,
            "condition_count": len(conditions),
            "reuse": reuse,
            "scoreable_substitution_count": sum(
                bool(row["scoreable_substitution"]) for row in conditions
            ),
            "prompt_preflight": prompt_summary,
            "runtime_projection": runtime,
            "manifest_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "selections_locked_before_generation": True,
        }
        summary_path = args.artifact_dir / "selector_audit_preflight.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(args.artifact_dir / "selector_audit_preflight.md", _report(summary))
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
