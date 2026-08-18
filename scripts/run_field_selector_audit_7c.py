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
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    condition_checkpoint_name,
    validate_condition_checkpoint,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
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
from rcmf.training.datasets import load_decision_examples, load_memory_records


def _attempt_ids(path: Path) -> set[str]:
    return (
        {str(row["attempt_id"]) for row in read_jsonl(path)}
        if path.exists()
        else set()
    )


def _runtime_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "legacy": dict(settings["legacy"]),
        "replay": dict(settings["replay"]),
        "causal_audit": {"generation": dict(settings["generation"])},
    }


def _selector_metadata(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selector_summary_sha256": sha256_file(Path(summary["summary_path"])),
        "ensemble_sha256": str(summary["ensemble"]["sha256"]),
        "seed_checkpoint_sha256": [
            str(row["checkpoint_sha256"]) for row in summary["seed_reports"]
        ],
    }


def _validate_result(
    row: Mapping[str, Any],
    *,
    condition: Mapping[str, Any],
    manifest_sha256: str,
    config_sha256: str,
    corpus_lineage_sha256: str,
    model_name: str,
) -> None:
    validate_condition_checkpoint(
        row,
        condition=condition,
        condition_manifest_sha256=manifest_sha256,
        config_sha256=config_sha256,
        corpus_lineage_sha256=corpus_lineage_sha256,
        model_name=model_name,
    )


def _materialize_reuse(
    *,
    condition: Mapping[str, Any],
    source_row: Mapping[str, Any],
    source_kind: str,
    source_condition_key: str,
    output_path: Path,
    manifest: Mapping[str, Any],
    config_sha256: str,
    corpus_lineage_sha256: str,
    model_name: str,
    selector_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if output_path.exists():
        row = _json(output_path)
        _validate_result(
            row,
            condition=condition,
            manifest_sha256=str(manifest["manifest_sha256"]),
            config_sha256=config_sha256,
            corpus_lineage_sha256=corpus_lineage_sha256,
            model_name=model_name,
        )
        return row
    row = dict(source_row)
    for field in (
        "condition_key",
        "condition_name",
        "prompt_kind",
        "state_example_id",
        "state_task_id",
        "state_step_id",
        "audit_stratum",
        "transition_id",
        "transition_parent_id",
        "signature_class_id",
        "signature_sha256",
        "signature_class_size",
        "procedural_tier",
        "api_documentation_action",
    ):
        row[field] = condition.get(field)
    row["condition_manifest_sha256"] = str(manifest["manifest_sha256"])
    row["config_sha256"] = config_sha256
    row["corpus_lineage_sha256"] = corpus_lineage_sha256
    row["selector"] = dict(selector_metadata)
    row["result_reuse"] = {
        "source_kind": source_kind,
        "source_condition_key": source_condition_key,
        "source_prompt_sha256": str(source_row["prompt_sha256"]),
        "semantic_prompt_key": str(condition["semantic_prompt_key"]),
        "qwen_generation_reused": True,
        "appworld_execution_reused": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, row)
    _validate_result(
        row,
        condition=condition,
        manifest_sha256=str(manifest["manifest_sha256"]),
        config_sha256=config_sha256,
        corpus_lineage_sha256=corpus_lineage_sha256,
        model_name=model_name,
    )
    return row


def _smoke_conditions(conditions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    states = []
    for row in conditions:
        state_id = str(row["state_example_id"])
        if state_id not in states:
            states.append(state_id)
        if len(states) == 2:
            break
    selected = []
    wanted = {
        states[0]: {"F1_strict_b_field_raw", "F3_deployment_e_field_raw"},
        states[1]: {"F4_deployment_e_field_signature"},
    }
    for row in conditions:
        if str(row["condition_name"]) in wanted.get(
            str(row["state_example_id"]), set()
        ):
            copy = dict(row)
            copy["condition_key"] = "smoke-" + str(row["condition_key"])
            copy["result_source"] = {
                "kind": "execute",
                "condition_key": copy["condition_key"],
                "condition_name": copy["condition_name"],
            }
            selected.append(copy)
    if len(selected) != 3:
        raise RuntimeError("Could not construct the fixed two-state lifecycle smoke")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_signature_balanced_field_7c.yaml"),
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
    preflight = _json(args.artifact_dir / "selector_audit_preflight.json")
    if (
        bool(preflight["runtime_projection"]["requires_explicit_runtime_approval"])
        and not args.approved_over_threshold
    ):
        raise RuntimeError("Projected generation exceeds the 12-H100-hour review threshold")
    if args.phase == "formal":
        smoke_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        if not smoke_path.exists() or not bool(_json(smoke_path)["passed"]):
            raise RuntimeError("EXP-025C lifecycle smoke has not passed")
    parent = Path(str(settings["parent_exp025b"]))
    clean_audit = parent / "clean_procedural_audit"
    clean_cache = parent / "clean_cache_rebuild"
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    paths = {
        "manifest": args.artifact_dir / "selector_condition_manifest.json",
        "selector_summary": args.artifact_dir / "selector/selector_summary.json",
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
    selector_summary = _json(paths["selector_summary"])
    selector_summary["summary_path"] = str(paths["selector_summary"])
    selector_meta = _selector_metadata(selector_summary)
    runtime_settings = _runtime_settings(settings)
    config_sha256 = sha256_file(args.config)
    corpus_lineage = str(settings["expected_structural_lineage_sha256"])
    model_name = str(settings["generation"]["model_name"])
    data_hashes = {name: sha256_file(path) for name, path in paths.items() if path.exists()}
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    signatures = {str(row["transition_id"]): row for row in _rows(paths["signatures"])}
    raw_utility = _load_raw_utility(paths["raw_utility"])
    conditions = sorted(
        manifest["conditions"],
        key=lambda row: (
            str(row["state_task_id"]),
            int(row["state_step_id"]),
            str(row["condition_name"]),
        ),
    )
    selected = _smoke_conditions(conditions) if args.phase == "smoke" else conditions
    output_dir = (
        args.artifact_dir / "lifecycle_smoke/condition_outputs"
        if args.phase == "smoke"
        else args.artifact_dir / "selector_condition_outputs"
    )
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
        phase=f"deployable_selector_one_step_{args.phase}",
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
                output_path=args.artifact_dir / "lifecycle_smoke/namespace_probe.json",
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
                stderr_path=args.artifact_dir / "lifecycle_smoke/simulated_interruption.stderr.log",
                timeout_seconds=float(settings["replay"]["subprocess_timeout_seconds"]),
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
        counts = {"execute": 0, "exp025b": 0, "exp025c_alias": 0, "resumed": 0}
        started = time.perf_counter()
        for position, condition in enumerate(selected, start=1):
            output_path = output_dir / condition_checkpoint_name(
                str(condition["condition_key"])
            )
            if args.phase == "smoke":
                source_kind = "execute"
            else:
                source_kind = str(condition["result_source"]["kind"])
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
                old_path = parent / "condition_outputs" / condition_checkpoint_name(source_key)
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
            else:
                source_key = str(condition["result_source"]["condition_key"])
                source = source_rows.get(source_key)
                if source is None:
                    source_path = output_dir / condition_checkpoint_name(source_key)
                    if not source_path.exists():
                        raise RuntimeError(f"EXP-025C alias source is unavailable: {source_key}")
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
                counts["exp025c_alias"] += 1
            metadata_changed = False
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
            "format": f"signature_balanced_selector_{args.phase}_summary_7c_v1",
            "phase": args.phase,
            "condition_count": len(rows),
            "unique_condition_count": len({row["condition_key"] for row in rows}),
            "result_source_counts": counts,
            "same_world_count": sum(bool(row["live_worker"]["same_world_execution"]) for row in rows),
            "same_namespace_count": sum(bool(row["live_worker"]["same_python_namespace"]) for row in rows),
            "history_replay_pass_count": sum(bool(row["live_worker"]["history_semantic_v3_match"]) for row in rows),
            "execution_exception_count": sum(row["live_worker"]["execution_exception"] is not None for row in rows),
            "new_qwen_generation_count": counts["execute"],
            "qwen_generation_seconds": sum(
                float(row["generation_elapsed_seconds"])
                for row in rows
                if not bool(row["result_reuse"]["qwen_generation_reused"])
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        if args.phase == "smoke":
            summary["namespace_probe"] = smoke_probe
            summary["simulated_interruption"] = interruption
            summary["scientific_metrics_included"] = False
            summary["passed"] = bool(
                len(rows) == 3
                and summary["same_world_count"] == 3
                and summary["same_namespace_count"] == 3
                and summary["history_replay_pass_count"] == 3
                and smoke_probe["passed"]
                and interruption["ready_before_termination"]
                and interruption["atomic_output_absent_after_termination"]
            )
            summary_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        else:
            summary["passed"] = bool(
                len(rows) == int(manifest["condition_count"])
                and summary["unique_condition_count"] == int(manifest["condition_count"])
                and summary["same_world_count"] == len(rows)
                and summary["same_namespace_count"] == len(rows)
                and summary["history_replay_pass_count"] == len(rows)
            )
            summary_path = args.artifact_dir / "selector_generation_summary.json"
        atomic_write_json(summary_path, summary)
        if not summary["passed"]:
            raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
