from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.cross_attention_validation_8b import (
    classify_live_reader,
    select_reader_checkpoint,
    summarize_live_controls,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.procedural_causal_audit_7b import (
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    sha256_text,
)
from scripts.run_cross_attention_reader_8b import (
    _generate,
    _json,
    _load_slot_bank,
    _load_source,
    _paths,
    _reader,
    _require,
)
from scripts.run_procedural_causal_audit_7b import (
    _examples_by_state,
    _prepare_message,
    _records_by_task,
    _state_contract,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


RESULT_FORMAT = "cross_attention_reader_live_result_8b_v1"
MANIFEST_FORMAT = "cross_attention_reader_live_manifest_8b_v1"
CONTROLS = (
    "X0_no_memory",
    "X1_correct_memory",
    "X2_transition_shuffle",
    "X3_state_shuffle",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("manifest", "validate", "select"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp030a_reader_live")
    return parser.parse_args()


def _live_paths(base: Mapping[str, Path]) -> dict[str, Path]:
    root = base["phase2_root"] / "heldout_live"
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "condition_outputs": root / "condition_outputs",
        "worker_logs": root / "worker_logs",
        "summary": root / "validation_summary.json",
        "selection": root / "checkpoint_selection.json",
        "report": root / "checkpoint_selection.md",
    }


def _condition_key(epoch: int, source_state: str, control: str) -> str:
    digest = sha256_text(f"25101:exp030a-live:{epoch}:{source_state}:{control}")[:24]
    return f"exp030a-reader-e{epoch:02d}-{digest}"


def _manifest(
    *, settings: Mapping[str, Any], source: Mapping[str, Any], path: Path
) -> dict[str, Any]:
    if path.exists():
        payload = _json(path)
        if payload.get("format") != MANIFEST_FORMAT:
            raise ValueError("Existing live condition manifest format differs")
        return payload
    maximum = int(settings["curriculum"]["phase2_max_epochs"])
    conditions = []
    heldout = sorted(
        (
            (state_id, row)
            for state_id, row in source["outcomes"].items()
            if str(row["model_split"]) == "heldout_train_validation"
        ),
        key=lambda item: (str(item[1]["state_task_id"]), int(item[1]["state_step_id"])),
    )
    if len(heldout) != 98:
        raise ValueError("EXP-030A live manifest requires 98 heldout train states")
    for epoch in range(1, maximum + 1):
        for state_id, outcome in heldout:
            mismatch = source["mismatches"][state_id]
            mismatch_state = str(mismatch["state_mismatch_state_example_id"])
            mismatch_outcome = source["outcomes"][mismatch_state]
            definitions = (
                (
                    "X0_no_memory",
                    state_id,
                    str(outcome["state_task_id"]),
                    int(outcome["state_step_id"]),
                    None,
                    False,
                ),
                (
                    "X1_correct_memory",
                    state_id,
                    str(outcome["state_task_id"]),
                    int(outcome["state_step_id"]),
                    str(outcome["selected_transition_id"]),
                    True,
                ),
                (
                    "X2_transition_shuffle",
                    state_id,
                    str(outcome["state_task_id"]),
                    int(outcome["state_step_id"]),
                    str(mismatch["transition_mismatch_transition_id"]),
                    True,
                ),
                (
                    "X3_state_shuffle",
                    mismatch_state,
                    str(mismatch_outcome["state_task_id"]),
                    int(mismatch_outcome["state_step_id"]),
                    str(outcome["selected_transition_id"]),
                    True,
                ),
            )
            for control, query_state, query_task, query_step, transition_id, executable in definitions:
                conditions.append(
                    {
                        "epoch": epoch,
                        "condition_key": _condition_key(epoch, state_id, control),
                        "control": control,
                        "source_state_id": state_id,
                        "source_task_id": str(outcome["state_task_id"]),
                        "source_step_id": int(outcome["state_step_id"]),
                        "query_state_id": query_state,
                        "query_task_id": query_task,
                        "query_step_id": query_step,
                        "transition_id": transition_id,
                        "executable": executable,
                        "student_prompt_contains_raw_memory": False,
                    }
                )
    payload = {
        "format": MANIFEST_FORMAT,
        "global_seed": GLOBAL_SEED,
        "epoch_count": maximum,
        "heldout_state_count": len(heldout),
        "logical_condition_count": len(conditions),
        "executable_condition_count": sum(row["executable"] for row in conditions),
        "reused_bare_condition_count": sum(not row["executable"] for row in conditions),
        "controls": list(CONTROLS),
        "selection_split": "eight_heldout_train_tasks_only",
        "test_normal_outcomes_used": False,
        "conditions": conditions,
    }
    if (
        payload["logical_condition_count"] != 1568
        or payload["executable_condition_count"] != 1176
        or payload["reused_bare_condition_count"] != 392
    ):
        raise ValueError("EXP-030A live condition accounting differs")
    payload["manifest_sha256"] = canonical_sha256(payload)
    atomic_write_json(path, payload)
    return payload


def _reused_bare_row(
    *, condition: Mapping[str, Any], outcome: Mapping[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT,
        "status": "complete",
        **dict(condition),
        "metrics": dict(outcome["bare_metrics"]),
        "reused_from_exp028a": True,
        "source_condition_key": str(outcome["bare_condition_key"]),
        "same_world_execution": bool(outcome["same_world_pairing"]),
        "same_python_namespace": True,
        "history_semantic_v3_match": True,
        "execution_exception": None,
        "condition_manifest_sha256": manifest_sha256,
    }


def _run_condition(
    *,
    condition: Mapping[str, Any],
    output_path: Path,
    stderr_path: Path,
    attempt_id: str,
    ordinal: int,
    checkpoint: Path,
    reader: Any,
    slots: Mapping[str, torch.Tensor],
    manifest_sha256: str,
    replay: Mapping[str, Any],
    settings: Mapping[str, Any],
    examples: Mapping[str, Any],
    records: Mapping[str, Any],
    backend: Any,
    semantic_path: Path,
    bridge_script: Path,
) -> tuple[dict[str, Any], bool]:
    checkpoint_sha256 = sha256_file(checkpoint)
    if output_path.exists():
        row = _json(output_path)
        checks = {
            "format": row.get("format") == RESULT_FORMAT,
            "condition": str(row.get("condition_key")) == str(condition["condition_key"]),
            "manifest": str(row.get("condition_manifest_sha256")) == manifest_sha256,
            "checkpoint": str(row.get("checkpoint_sha256")) == checkpoint_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing cross-attention live row differs: {checks}")
        return row, True
    query_state = str(condition["query_state_id"])
    query_task = str(condition["query_task_id"])
    example = examples[query_state]
    contract = _state_contract(example, records[query_task])
    bridge_condition = {
        "condition_key": str(condition["condition_key"]),
        "state_example_id": query_state,
        "state_task_id": query_task,
    }
    prepare = _prepare_message(
        condition=bridge_condition,
        contract=contract,
        settings={"legacy": replay["legacy"], "replay": replay["replay"]},
        semantic_path=semantic_path,
        bridge_attempt=f"{attempt_id}-{ordinal:05d}-{time.time_ns()}",
    )
    client = LiveBridgeClient(
        executable=Path(str(replay["legacy"]["executable"])),
        bridge_script=bridge_script,
        appworld_root=Path(str(replay["legacy"]["appworld_root"])),
        stderr_path=stderr_path,
        timeout_seconds=float(replay["replay"]["subprocess_timeout_seconds"]),
    )
    started = time.perf_counter()
    try:
        ready = client.prepare(prepare)
        messages = build_live_appworld_messages(
            example,
            list(ready["actual_observations"]),
            prompt_profile=str(settings["appworld"]["prompt_profile"]),
        )
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        prompt_tokens = int(tokenized.attention_mask.sum().item())
        remaining = int(settings["appworld"]["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Live cross-attention prompt is over context: {query_state}")
        generation_started = time.perf_counter()
        token_ids, text, hook = _generate(
            backend=backend,
            reader=reader,
            messages=messages,
            slots=slots[str(condition["transition_id"])],
            max_new_tokens=min(int(settings["appworld"]["max_new_tokens"]), remaining),
        )
        generation_seconds = time.perf_counter() - generation_started
        code, fixed = extract_code_and_fix_content(text)
        executed = client.execute(
            condition_key=str(condition["condition_key"]),
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(contract["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise
    metrics = evaluate_generated_action(
        text,
        code,
        str(contract["target_action"]),
        str(executed["raw_observation"]),
        str(contract["target_observation"]),
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
        metrics["exception_category"] = str(
            executed["execution_exception"].get("type", "exception")
        ).lower()
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        **dict(condition),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": checkpoint_sha256,
        "condition_manifest_sha256": manifest_sha256,
        "raw_model_response": text,
        "generated_token_ids": token_ids,
        "fixed_model_response": fixed,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "metrics": metrics,
        "target_action_sha256": contract["target_action_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "prompt_sha256": sha256_text(
            backend.render_messages(messages, add_generation_prompt=True)
        ),
        "prompt_tokens": prompt_tokens,
        "generation_tokens": len(token_ids),
        "generation_elapsed_seconds": generation_seconds,
        "elapsed_seconds": time.perf_counter() - started,
        "reader_hook": hook,
        "same_world_execution": bool(executed["same_world_execution"]),
        "same_python_namespace": bool(executed["same_python_namespace"]),
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
        "execution_exception": executed["execution_exception"],
        "student_prompt_contains_raw_memory": False,
        "memory_slots_in_self_attention_kv": False,
    }
    atomic_write_json(output_path, row)
    return row, False


def _validate(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    replay: Mapping[str, Any],
    paths: Mapping[str, Path],
    live: Mapping[str, Path],
    source: Mapping[str, Any],
    manifest: Mapping[str, Any],
    attempt: AttemptLedger,
    attempt_id: str,
) -> dict[str, Any]:
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Qwen must remain frozen")
    slot_bank = _load_slot_bank(paths["memory_index"])
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    records = _records_by_task(load_memory_records(corpus / "memory_records.jsonl"))
    semantic_path = Path(str(settings["appworld"]["semantic_module"]))
    bridge_script = Path(str(settings["appworld"]["one_step_bridge_script"]))
    reports = []
    conditions_by_epoch: dict[int, list[Mapping[str, Any]]] = {}
    for condition in manifest["conditions"]:
        conditions_by_epoch.setdefault(int(condition["epoch"]), []).append(condition)
    total = int(manifest["executable_condition_count"])
    completed = 0
    resumed = 0
    for epoch in sorted(conditions_by_epoch):
        checkpoint = paths["phase2_root"] / f"checkpoints/model_epoch_{epoch:02d}.pt"
        payload = torch.load(checkpoint, map_location=backend.device, weights_only=False)
        reader = _reader(settings, backend.device)
        reader.load_state_dict(payload["reader_state_dict"])
        reader.eval()
        rows = []
        for condition in conditions_by_epoch[epoch]:
            source_state = str(condition["source_state_id"])
            if not bool(condition["executable"]):
                rows.append(
                    _reused_bare_row(
                        condition=condition,
                        outcome=source["outcomes"][source_state],
                        manifest_sha256=str(manifest["manifest_sha256"]),
                    )
                )
                continue
            key = str(condition["condition_key"])
            output_path = live["condition_outputs"] / condition_checkpoint_name(key)
            row, was_reused = _run_condition(
                condition=condition,
                output_path=output_path,
                stderr_path=live["worker_logs"] / f"{condition_checkpoint_name(key)}.stderr.log",
                attempt_id=attempt_id,
                ordinal=completed + 1,
                checkpoint=checkpoint,
                reader=reader,
                slots=slot_bank,
                manifest_sha256=str(manifest["manifest_sha256"]),
                replay=replay,
                settings=settings,
                examples=examples,
                records=records,
                backend=backend,
                semantic_path=semantic_path,
                bridge_script=bridge_script,
            )
            rows.append(row)
            completed += 1
            resumed += int(was_reused)
            if completed % 10 == 0:
                attempt.progress(
                    status=f"reader_live_epoch_{epoch}",
                    completed_conditions=completed,
                    total_conditions=total,
                    latest_validated_checkpoint=str(output_path),
                )
        summary = summarize_live_controls(rows)
        policy_report = _json(paths["policy_eval_root"] / f"epoch_{epoch:02d}.json")
        report = {
            "format": "cross_attention_reader_live_checkpoint_validation_8b_v1",
            "epoch": epoch,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": sha256_file(checkpoint),
            "state_count": 98,
            "logical_condition_count": len(rows),
            "new_condition_count": sum(bool(row.get("executable")) for row in rows),
            "reused_bare_count": sum(not bool(row.get("executable")) for row in rows),
            "live_summary": summary,
            "classification": classify_live_reader(summary),
            "policy_evaluation": {
                "positive_raw_teacher_policy_kl": policy_report["evaluation"][
                    "positive_raw_teacher_policy_kl"
                ]
            },
            "stable_generation": all(
                bool(row["same_world_execution"])
                and bool(row["same_python_namespace"])
                and bool(row["history_semantic_v3_match"])
                for row in rows
            ),
            "test_normal_outcomes_used": False,
        }
        report_path = live["root"] / f"epoch_{epoch:02d}/validation_report.json"
        atomic_write_json(report_path, report)
        reports.append(report)
        attempt.progress(
            status=f"reader_live_epoch_{epoch}_complete",
            latest_validated_checkpoint=str(report_path),
            classification=report["classification"],
            live_summary=summary,
        )
        print(
            f"reader live epoch={epoch} classification={report['classification']}",
            flush=True,
        )
    output = {
        "format": "cross_attention_reader_live_validation_summary_8b_v1",
        "global_seed": GLOBAL_SEED,
        "checkpoint_count": len(reports),
        "new_condition_count": completed - resumed,
        "resumed_condition_count": resumed,
        "reports": reports,
        "test_normal_outcomes_used": False,
        "passed": len(reports) == int(settings["curriculum"]["phase2_max_epochs"]),
    }
    atomic_write_json(live["summary"], output)
    return output


def _select(live: Mapping[str, Path], settings: Mapping[str, Any]) -> dict[str, Any]:
    summary = _json(live["summary"])
    selected = select_reader_checkpoint(summary["reports"])
    classification = "CLEAR_FAILURE" if selected is None else str(selected["classification"])
    report = {
        "format": "cross_attention_reader_checkpoint_selection_8b_v1",
        "global_seed": GLOBAL_SEED,
        "candidates": summary["reports"],
        "selected": selected,
        "classification": classification,
        "heldout_train_only_selection": True,
        "test_normal_outcomes_used": False,
        "decision_branch": (
            "published_cross_attention_reader_failed_on_appworld"
            if selected is None
            else "published_cross_attention_reader_validated_for_field"
        ),
        "run_reversible_field": selected is not None,
    }
    atomic_write_json(live["selection"], report)
    lines = [
        "# EXP-030A selected-single-memory reader validation",
        "",
        *[
            f"- epoch {row['epoch']}: `{row['classification']}`; policy KL `{row['policy_evaluation']['positive_raw_teacher_policy_kl']}`"
            for row in summary["reports"]
        ],
        f"- selected classification: `{classification}`",
        f"- decision branch: `{report['decision_branch']}`",
        f"- reversible field authorized: `{str(report['run_reversible_field']).lower()}`",
        "- checkpoint selection used heldout train tasks only",
        "- test_normal outcomes used: `false`",
        "",
    ]
    atomic_write_text(live["report"], "\n".join(lines))
    return report


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_8b"]
    replay = replay_cfg.raw["stage_c_7b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    seed_everything(GLOBAL_SEED)
    paths = _paths(settings, args.artifact_dir)
    live = _live_paths(paths)
    required = (
        "preflight",
        "memory_index",
        "mismatches",
        "task_split",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
        "implementation",
        "phase1_posttrain",
        "phase2_summary",
        "policy_eval_summary",
    )
    _require(paths, required)
    source = _load_source(paths)
    manifest = _manifest(settings=settings, source=source, path=live["manifest"])
    if args.phase == "manifest":
        print(json.dumps(manifest, sort_keys=True))
        return
    if args.phase == "select":
        result = _select(live, settings)
        print(json.dumps(result, sort_keys=True))
        return
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    data_hashes["condition_manifest"] = sha256_file(live["manifest"])
    data_hashes["replay_config"] = sha256_file(args.replay_config)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="reader_heldout_live_validation",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        result = _validate(
            cfg=cfg,
            settings=settings,
            replay=replay,
            paths=paths,
            live=live,
            source=source,
            manifest=manifest,
            attempt=attempt,
            attempt_id=args.attempt_id,
        )
        attempt.progress(
            status="reader_heldout_live_validation_complete",
            latest_validated_checkpoint=str(live["summary"]),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
