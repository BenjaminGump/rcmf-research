from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    condition_checkpoint_name,
)
from rcmf.training.selector_behavioral_missing_7cr import validate_result_keys
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_field_selector_audit_7c import (
    _materialize_reuse,
    _runtime_settings,
    _selector_metadata,
    _validate_result,
)
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _json,
    _load_raw_utility,
    _prepare_message,
    _records_by_task,
    _rows,
    _run_condition,
    _run_namespace_probe,
    _state_contract,
)


def _attempt_ids(path: Path) -> set[str]:
    return (
        {str(row["attempt_id"]) for row in read_jsonl(path)}
        if path.exists()
        else set()
    )


def _smoke_conditions(
    conditions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_state: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in conditions:
        by_state.setdefault(str(row["state_example_id"]), {})[
            str(row["condition_name"])
        ] = row
    wanted_first = {"F1_strict_b_field_raw", "F3_deployment_e_field_raw"}
    wanted_second = {
        "F4_deployment_e_field_signature",
        "F5_predicted_intent_raw",
    }
    first_state = next(
        state for state, values in sorted(by_state.items()) if wanted_first <= set(values)
    )
    second_state = next(
        state
        for state, values in sorted(by_state.items())
        if state != first_state and wanted_second <= set(values)
    )
    selected = []
    for state_id, names in (
        (first_state, wanted_first),
        (second_state, wanted_second),
    ):
        for name in sorted(names):
            copy = dict(by_state[state_id][name])
            copy["condition_key"] = "smoke-" + str(copy["condition_key"])
            copy["result_source"] = {
                "kind": "execute",
                "condition_key": copy["condition_key"],
                "condition_name": copy["condition_name"],
            }
            selected.append(copy)
    if len(selected) != 4 or {row["condition_name"] for row in selected} != (
        wanted_first | wanted_second
    ):
        raise RuntimeError("Could not construct the fixed four-condition smoke")
    if any(not bool(row["valid_for_generation"]) for row in selected):
        raise RuntimeError("Lifecycle smoke selected a missing condition")
    return selected


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
    parser.add_argument("--phase", choices=("smoke", "formal"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--approved-over-threshold", action="store_true")
    parser.add_argument("--tmux-session", default="exp025cr")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7cr"]
    if os.name != "nt" and not os.path.ismount(Path(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    preflight = _json(args.artifact_dir / "selector_audit_preflight.json")
    if (
        bool(preflight["runtime_projection"]["requires_explicit_runtime_approval"])
        and not args.approved_over_threshold
    ):
        raise RuntimeError("Projected generation exceeds the 12-H100-hour review threshold")
    if args.phase == "formal":
        smoke_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        if not smoke_path.exists() or not bool(_json(smoke_path)["passed"]):
            raise RuntimeError("EXP-025C-R lifecycle smoke has not passed")

    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    clean_audit = parent_b / "clean_procedural_audit"
    clean_cache = parent_b / "clean_cache_rebuild"
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    paths = {
        "manifest": args.artifact_dir / "selector_condition_manifest.json",
        "selector_summary": parent_c / "selector/selector_summary.json",
        "selector_ensemble": parent_c / "selector/ensemble_scores.pt",
        "decisions": corpus / "decision_examples.jsonl",
        "memories": corpus / "memory_records.jsonl",
        "transitions": clean_cache / "transition_preflight/transition_manifest.jsonl",
        "signatures": clean_audit / "clean_transition_signature_manifest.jsonl",
        "raw_utility": clean_cache / "transition_teacher/teacher_cache.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
        "old_manifest": clean_audit / "clean_condition_manifest.json",
        "old_config": Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    }
    for name, path in paths.items():
        if name == "raw_utility" and not path.exists():
            continue
        if not path.exists():
            raise FileNotFoundError(f"Field audit input missing: {name}={path}")
    manifest = _json(paths["manifest"])
    if int(manifest["logical_slot_count"]) != 225:
        raise ValueError("Logical selector manifest does not contain 225 slots")
    selector_summary = _json(paths["selector_summary"])
    selector_summary["summary_path"] = str(paths["selector_summary"])
    selector_meta = _selector_metadata(selector_summary)
    if selector_meta["ensemble_sha256"] != str(
        settings["expected_selector_ensemble_sha256"]
    ):
        raise ValueError("Frozen selector ensemble hash differs")
    runtime_settings = _runtime_settings(settings)
    config_sha256 = sha256_file(args.config)
    corpus_lineage = str(settings["expected_structural_lineage_sha256"])
    model_name = str(settings["generation"]["model_name"])
    data_hashes = {
        name: sha256_file(path) for name, path in paths.items() if path.exists()
    }
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    transitions = {
        str(row["transition_id"]): row for row in _rows(paths["transitions"])
    }
    signatures = {
        str(row["transition_id"]): row for row in _rows(paths["signatures"])
    }
    raw_utility = _load_raw_utility(paths["raw_utility"])
    logical = sorted(
        manifest["conditions"],
        key=lambda row: (
            str(row["state_task_id"]),
            int(row["state_step_id"]),
            str(row["condition_name"]),
        ),
    )
    conditions = [row for row in logical if bool(row["valid_for_generation"])]
    missing = [row for row in logical if not bool(row["valid_for_generation"])]
    if len(conditions) != 224 or len(missing) != 1:
        raise ValueError("Executable/missing selector condition count differs")
    selected = _smoke_conditions(conditions) if args.phase == "smoke" else conditions
    output_dir = (
        args.artifact_dir / "lifecycle_smoke/condition_outputs"
        if args.phase == "smoke"
        else args.artifact_dir / "selector_condition_outputs"
    )
    missing_output = args.artifact_dir / "selector_condition_outputs" / condition_checkpoint_name(
        str(missing[0]["condition_key"])
    )
    if missing_output.exists():
        raise RuntimeError("A result exists for the missing F5 logical slot")

    generation = settings["generation"]
    backend = HFQwenBackend(
        model_name=model_name,
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=bool(generation["enable_thinking"]),
        load_model=True,
    )
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Frozen-Qwen contract failed")
    old_manifest = _json(paths["old_manifest"])
    old_condition_by_key = {
        str(row["condition_key"]): row for row in old_manifest["conditions"]
    }
    old_config_sha256 = sha256_file(paths["old_config"])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"missing_control_selector_one_step_{args.phase}",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        smoke_probe = None
        interruption = None
        if args.phase == "smoke":
            smoke_probe = _run_namespace_probe(
                conditions=selected,
                examples=examples,
                records=records,
                settings=runtime_settings,
                semantic_path=paths["semantic_module"],
                bridge_script=paths["bridge_script"],
                output_path=args.artifact_dir
                / "lifecycle_smoke/namespace_probe.json",
                attempt_id=args.attempt_id,
            )
            interrupted = selected[0]
            contract = _state_contract(
                examples[str(interrupted["state_example_id"])],
                records[str(interrupted["state_task_id"])],
            )
            client = LiveBridgeClient(
                executable=Path(str(settings["legacy"]["executable"])),
                bridge_script=paths["bridge_script"],
                appworld_root=Path(str(settings["legacy"]["appworld_root"])),
                stderr_path=args.artifact_dir
                / "lifecycle_smoke/simulated_interruption.stderr.log",
                timeout_seconds=float(
                    settings["replay"]["subprocess_timeout_seconds"]
                ),
            )
            ready = client.prepare(
                _prepare_message(
                    condition=interrupted,
                    contract=contract,
                    settings=runtime_settings,
                    semantic_path=paths["semantic_module"],
                    bridge_attempt=f"{args.attempt_id}-simulated-interrupt",
                )
            )
            client.terminate()
            interrupted_output = output_dir / condition_checkpoint_name(
                str(interrupted["condition_key"])
            )
            interruption = {
                "ready_before_termination": bool(ready["ready"]),
                "atomic_output_absent_after_termination": not interrupted_output.exists(),
            }
            atomic_write_json(
                args.artifact_dir / "lifecycle_smoke/simulated_interruption.json",
                interruption,
            )

        rows = []
        source_rows: dict[str, dict[str, Any]] = {}
        counts = {
            "execute": 0,
            "exp025b": 0,
            "exp025cr_alias": 0,
            "resumed": 0,
        }
        started = time.perf_counter()
        for position, condition in enumerate(selected, start=1):
            output_path = output_dir / condition_checkpoint_name(
                str(condition["condition_key"])
            )
            source_kind = (
                "execute"
                if args.phase == "smoke"
                else str(condition["result_source"]["kind"])
            )
            if output_path.exists():
                row = _json(output_path)
                _validate_result(
                    row,
                    condition=condition,
                    manifest_sha256=str(manifest["manifest_sha256"]),
                    config_sha256=config_sha256,
                    corpus_lineage_sha256=corpus_lineage,
                    model_name=model_name,
                )
                counts["resumed"] += 1
            elif source_kind == "execute":
                row, _ = _run_condition(
                    condition=condition,
                    output_path=output_path,
                    stderr_path=args.artifact_dir
                    / f"worker_logs/{args.phase}/{condition_checkpoint_name(str(condition['condition_key']))}.stderr.log",
                    attempt_id=args.attempt_id,
                    ordinal=position,
                    settings=runtime_settings,
                    config_sha256=config_sha256,
                    corpus_lineage_sha256=corpus_lineage,
                    condition_manifest=manifest,
                    example=examples[str(condition["state_example_id"])],
                    record=records[str(condition["state_task_id"])],
                    transitions=transitions,
                    signatures=signatures,
                    raw_utility=raw_utility,
                    backend=backend,
                    semantic_path=paths["semantic_module"],
                    bridge_script=paths["bridge_script"],
                )
                row["selector"] = dict(selector_meta)
                row["result_reuse"] = {
                    "source_kind": "execute",
                    "source_condition_key": str(condition["condition_key"]),
                    "semantic_prompt_key": str(condition["semantic_prompt_key"]),
                    "qwen_generation_reused": False,
                    "appworld_execution_reused": False,
                }
                atomic_write_json(output_path, row)
                counts["execute"] += 1
            elif source_kind == "exp025b":
                source_key = str(condition["result_source"]["condition_key"])
                old_condition = old_condition_by_key[source_key]
                old_path = parent_b / "condition_outputs" / condition_checkpoint_name(
                    source_key
                )
                source = _json(old_path)
                _validate_result(
                    source,
                    condition=old_condition,
                    manifest_sha256=str(old_manifest["manifest_sha256"]),
                    config_sha256=old_config_sha256,
                    corpus_lineage_sha256=corpus_lineage,
                    model_name=model_name,
                )
                row = _materialize_reuse(
                    condition=condition,
                    source_row=source,
                    source_kind=source_kind,
                    source_condition_key=source_key,
                    output_path=output_path,
                    manifest=manifest,
                    config_sha256=config_sha256,
                    corpus_lineage_sha256=corpus_lineage,
                    model_name=model_name,
                    selector_metadata=selector_meta,
                )
                counts["exp025b"] += 1
            elif source_kind == "exp025cr_alias":
                source_key = str(condition["result_source"]["condition_key"])
                source = source_rows.get(source_key)
                if source is None:
                    source_path = output_dir / condition_checkpoint_name(source_key)
                    if not source_path.exists():
                        raise RuntimeError(
                            f"EXP-025C-R alias source is unavailable: {source_key}"
                        )
                    source = _json(source_path)
                row = _materialize_reuse(
                    condition=condition,
                    source_row=source,
                    source_kind=source_kind,
                    source_condition_key=source_key,
                    output_path=output_path,
                    manifest=manifest,
                    config_sha256=config_sha256,
                    corpus_lineage_sha256=corpus_lineage,
                    model_name=model_name,
                    selector_metadata=selector_meta,
                )
                counts["exp025cr_alias"] += 1
            else:
                raise ValueError(f"Unknown result source: {source_kind}")

            metadata_changed = False
            for field in (
                "condition_status",
                "valid_for_generation",
                "valid_for_pairwise_comparison",
                "missing_reason",
            ):
                expected = condition.get(field)
                if row.get(field) != expected:
                    row[field] = expected
                    metadata_changed = True
            if row.get("selector") is None:
                row["selector"] = dict(selector_meta)
                metadata_changed = True
            elif row["selector"] != selector_meta:
                raise ValueError(
                    f"Selector provenance differs for {condition['condition_key']}"
                )
            if row.get("result_reuse") is None:
                row["result_reuse"] = {
                    "source_kind": source_kind,
                    "source_condition_key": str(
                        condition["result_source"]["condition_key"]
                        if args.phase == "formal"
                        else condition["condition_key"]
                    ),
                    "semantic_prompt_key": str(condition["semantic_prompt_key"]),
                    "qwen_generation_reused": source_kind != "execute",
                    "appworld_execution_reused": source_kind != "execute",
                }
                metadata_changed = True
            if metadata_changed:
                atomic_write_json(output_path, row)
            source_rows[str(condition["condition_key"])] = row
            rows.append(row)
            attempt.progress(
                status=f"selector_audit_{args.phase}",
                completed_conditions=position,
                total_conditions=len(selected),
                latest_validated_checkpoint=str(output_path),
            )
            print(
                json.dumps(
                    {
                        "completed": position,
                        "total": len(selected),
                        "condition": condition["condition_name"],
                        "source_kind": source_kind,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        summary = {
            "format": f"missing_control_selector_{args.phase}_summary_7cr_v1",
            "phase": args.phase,
            "logical_slot_count": int(manifest["logical_slot_count"]),
            "executable_slot_count": int(manifest["executable_slot_count"]),
            "missing_slot_count": int(manifest["missing_slot_count"]),
            "completed_condition_count": len(rows),
            "unique_condition_count": len({row["condition_key"] for row in rows}),
            "result_source_counts": counts,
            "same_world_count": sum(
                bool(row["live_worker"]["same_world_execution"]) for row in rows
            ),
            "same_namespace_count": sum(
                bool(row["live_worker"]["same_python_namespace"]) for row in rows
            ),
            "history_replay_pass_count": sum(
                bool(row["live_worker"]["history_semantic_v3_match"])
                for row in rows
            ),
            "execution_exception_count": sum(
                row["live_worker"]["execution_exception"] is not None for row in rows
            ),
            "new_qwen_generation_count": counts["execute"],
            "new_appworld_execution_count": counts["execute"],
            "qwen_generation_seconds": sum(
                float(row["generation_elapsed_seconds"])
                for row in rows
                if not bool(row["result_reuse"]["qwen_generation_reused"])
            ),
            "elapsed_seconds": time.perf_counter() - started,
            "missing_result_absent": not missing_output.exists(),
        }
        if args.phase == "smoke":
            summary["namespace_probe"] = smoke_probe
            summary["simulated_interruption"] = interruption
            summary["scientific_metrics_included"] = False
            summary["covered_conditions"] = sorted(
                {str(row["condition_name"]) for row in rows}
            )
            summary["passed"] = bool(
                len(rows) == 4
                and summary["same_world_count"] == 4
                and summary["same_namespace_count"] == 4
                and summary["history_replay_pass_count"] == 4
                and summary["missing_result_absent"]
                and smoke_probe["passed"]
                and interruption["ready_before_termination"]
                and interruption["atomic_output_absent_after_termination"]
            )
            summary_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        else:
            key_validation = validate_result_keys(
                logical, [str(row["condition_key"]) for row in rows]
            )
            summary["result_key_validation"] = key_validation
            summary["passed"] = bool(
                len(rows) == 224
                and summary["unique_condition_count"] == 224
                and summary["same_world_count"] == 224
                and summary["same_namespace_count"] == 224
                and summary["history_replay_pass_count"] == 224
                and summary["missing_result_absent"]
                and key_validation["missing_result_count"] == 0
            )
            summary_path = args.artifact_dir / "selector_generation_summary.json"
        atomic_write_json(summary_path, summary)
        if not summary["passed"]:
            raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
