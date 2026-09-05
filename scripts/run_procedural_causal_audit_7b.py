from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash, identity_hashes
from rcmf.training.datasets import (
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.procedural_causal_audit_6h import (
    evaluate_generated_action,
    messages_with_signature_card,
    signature_only_card,
)
from rcmf.training.procedural_causal_audit_7b import (
    LIVE_BRIDGE_PROTOCOL_VERSION,
    LIVE_GENERATION_RESULT_VERSION,
    LiveBridgeClient,
    build_live_appworld_messages,
    condition_checkpoint_name,
    validate_condition_checkpoint,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import (
    messages_with_transition_memory,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    read_jsonl,
    sha256_file,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _attempt_ids(path: Path) -> set[str]:
    return {str(row["attempt_id"]) for row in read_jsonl(path)} if path.exists() else set()


def _examples_by_state(examples: Sequence[Any]) -> dict[str, Any]:
    output = {}
    for index, example in enumerate(examples):
        state_id = state_example_id(index, example)
        if state_id in output:
            raise ValueError(f"Duplicate decision state: {state_id}")
        output[state_id] = example
    return output


def _records_by_task(records: Sequence[Any]) -> dict[str, Any]:
    output = {str(record.task_id): record for record in records}
    if len(output) != len(records):
        raise ValueError("Successful-trajectory records contain duplicate task IDs")
    return output


def _state_contract(example: Any, record: Any) -> dict[str, Any]:
    _, query, parsed_history = _parse_appworld_state_text(str(example.state_text))
    step_id = int(example.step_id)
    raw_steps = list(record.raw_trajectory["steps"])
    if len(parsed_history) != step_id - 1:
        raise ValueError("Canonical decision history length differs from target step")
    history = []
    for position, (_, parsed_response, parsed_observation) in enumerate(parsed_history, start=1):
        source = raw_steps[position - 1]
        parsed_code, _ = extract_code_and_fix_content(parsed_response)
        source_code, _ = extract_code_and_fix_content(str(source["response"]))
        if parsed_code.strip() != source_code.strip():
            raise ValueError(f"History action differs at step {position}")
        history.append(
            {
                "step_id": position,
                "code": source_code,
                "expected_observation": str(source["observation"]),
                "historical_state_observation": parsed_observation,
            }
        )
    target = raw_steps[step_id - 1]
    target_code, _ = extract_code_and_fix_content(str(target["response"]))
    example_target_code, _ = extract_code_and_fix_content(str(example.target_text))
    if target_code.strip() != example_target_code.strip():
        raise ValueError("Decision target differs from successful trajectory target")
    return {
        "query": query,
        "history_steps": history,
        "target_action": str(target["response"]),
        "target_code": target_code,
        "target_observation": str(target["observation"]),
        "target_action_sha256": hashlib.sha256(str(target["response"]).encode()).hexdigest(),
        "target_observation_sha256": hashlib.sha256(
            str(target["observation"]).encode()
        ).hexdigest(),
    }


def _prepare_message(
    *,
    condition: Mapping[str, Any],
    contract: Mapping[str, Any],
    settings: Mapping[str, Any],
    semantic_path: Path,
    bridge_attempt: str,
) -> dict[str, Any]:
    legacy = settings["legacy"]
    replay = settings["replay"]
    experiment_name = (
        "exp025b_live_"
        + hashlib.sha256((f"{condition['condition_key']}::{bridge_attempt}").encode()).hexdigest()[
            :24
        ]
    )
    return {
        "format": LIVE_BRIDGE_PROTOCOL_VERSION,
        "op": "prepare",
        "condition_key": str(condition["condition_key"]),
        "state_example_id": str(condition["state_example_id"]),
        "task_id": str(condition["state_task_id"]),
        "history_steps": list(contract["history_steps"]),
        "history_steps_sha256": canonical_hash(contract["history_steps"]),
        "expected_identity_field_sha256": identity_hashes(str(contract["query"])),
        "legacy_python": str(legacy["executable"]),
        "appworld_root": str(legacy["appworld_root"]),
        "semantic_module_path": str(semantic_path.resolve()),
        "semantic_module_sha256": sha256_file(semantic_path),
        "normalization_version": "appworld_observation_semantic_normalization_7b_v1",
        "experiment_name": experiment_name,
        "random_seed": int(replay["random_seed"]),
        "max_interactions": int(replay["max_interactions"]),
        "max_api_calls_per_interaction": int(replay["max_api_calls_per_interaction"]),
    }


def _messages_for_condition(
    *,
    condition: Mapping[str, Any],
    example: Any,
    actual_observations: Sequence[str],
    transitions: Mapping[str, Mapping[str, Any]],
    signatures: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
) -> list[dict[str, str]]:
    messages = build_live_appworld_messages(
        example, actual_observations, prompt_profile=prompt_profile
    )
    kind = str(condition["prompt_kind"])
    transition_id = condition.get("transition_id")
    if kind == "raw_transition":
        return messages_with_transition_memory(
            messages, transitions[str(transition_id)], prompt_profile
        )
    if kind == "signature_card":
        return messages_with_signature_card(
            messages,
            signature_only_card(signatures[str(transition_id)]),
            prompt_profile,
        )
    if kind != "bare":
        raise ValueError(f"Unknown prompt kind: {kind}")
    return messages


def _namespace_variables(contract: Mapping[str, Any]) -> list[str]:
    names = []
    for step in contract["history_steps"]:
        try:
            tree = ast.parse(str(step["code"]))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name) and child.id not in names:
                        names.append(child.id)
    return names


def _load_raw_utility(path: Path) -> dict[tuple[str, str], float]:
    if not path.exists():
        return {}
    output = {}
    for row in read_jsonl(path):
        if bool(row.get("valid_for_loss")) and row.get("text_utility") is not None:
            output[(str(row["state_example_id"]), str(row["transition_id"]))] = float(
                row["text_utility"]
            )
    return output


def _run_condition(
    *,
    condition: Mapping[str, Any],
    output_path: Path,
    stderr_path: Path,
    attempt_id: str,
    ordinal: int,
    settings: Mapping[str, Any],
    config_sha256: str,
    corpus_lineage_sha256: str,
    condition_manifest: Mapping[str, Any],
    example: Any,
    record: Any,
    transitions: Mapping[str, Mapping[str, Any]],
    signatures: Mapping[str, Mapping[str, Any]],
    raw_utility: Mapping[tuple[str, str], float],
    backend: Any,
    semantic_path: Path,
    bridge_script: Path,
    runtime_provenance: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    model_name = str(settings["causal_audit"]["generation"]["model_name"])
    if output_path.exists():
        row = _json(output_path)
        validate_condition_checkpoint(
            row,
            condition=condition,
            condition_manifest_sha256=str(condition_manifest["manifest_sha256"]),
            config_sha256=config_sha256,
            corpus_lineage_sha256=corpus_lineage_sha256,
            model_name=model_name,
        )
        if runtime_provenance is not None and row.get(
            "paired_causal_runtime"
        ) != dict(runtime_provenance):
            raise ValueError("Condition checkpoint runtime provenance differs")
        return row, True
    started = time.perf_counter()
    contract = _state_contract(example, record)
    bridge_attempt = f"{attempt_id}-{ordinal:04d}-{time.time_ns()}"
    prepare = _prepare_message(
        condition=condition,
        contract=contract,
        settings=settings,
        semantic_path=semantic_path,
        bridge_attempt=bridge_attempt,
    )
    legacy = settings["legacy"]
    replay = settings["replay"]
    client = LiveBridgeClient(
        executable=Path(str(legacy["executable"])),
        bridge_script=bridge_script,
        appworld_root=Path(str(legacy["appworld_root"])),
        stderr_path=stderr_path,
        timeout_seconds=float(replay["subprocess_timeout_seconds"]),
    )
    try:
        ready = client.prepare(prepare)
        messages = _messages_for_condition(
            condition=condition,
            example=example,
            actual_observations=list(ready["actual_observations"]),
            transitions=transitions,
            signatures=signatures,
            prompt_profile=str(settings["causal_audit"]["generation"]["prompt_profile"]),
        )
        rendered = backend.render_messages(messages, add_generation_prompt=True)
        prompt_tokens = len(
            backend.tokenizer(rendered, add_special_tokens=True, truncation=False)["input_ids"]
        )
        generation = settings["causal_audit"]["generation"]
        remaining = int(generation["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Live prompt is over context for {condition['condition_key']}")
        max_new_tokens = min(int(generation["max_new_tokens"]), remaining)
        generation_started = time.perf_counter()
        output = backend.generate(
            messages=messages,
            max_new_tokens=max_new_tokens,
            temperature=float(generation["temperature"]),
            top_p=float(generation["top_p"]),
        )
        generation_elapsed_seconds = time.perf_counter() - generation_started
        code, fixed_response = extract_code_and_fix_content(output.text)
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
        output.text,
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
    transition_id = condition.get("transition_id")
    utility = (
        raw_utility.get((str(condition["state_example_id"]), str(transition_id)))
        if transition_id is not None
        else None
    )
    row = {
        "format": LIVE_GENERATION_RESULT_VERSION,
        "status": "complete",
        "condition_key": str(condition["condition_key"]),
        "condition_name": str(condition["condition_name"]),
        "prompt_kind": str(condition["prompt_kind"]),
        "state_example_id": str(condition["state_example_id"]),
        "state_task_id": str(condition["state_task_id"]),
        "state_step_id": int(condition["state_step_id"]),
        "audit_stratum": str(condition["audit_stratum"]),
        "transition_id": transition_id,
        "transition_parent_id": condition.get("transition_parent_id"),
        "signature_class_id": condition.get("signature_class_id"),
        "signature_sha256": condition.get("signature_sha256"),
        "signature_class_size": condition.get("signature_class_size"),
        "procedural_tier": condition.get("procedural_tier"),
        "api_documentation_action": condition.get("api_documentation_action"),
        "raw_nll_text_utility": utility,
        "raw_nll_source": "identity_reconciled_transition_teacher_7b_v1"
        if utility is not None
        else None,
        "raw_model_response": output.text,
        "fixed_model_response": fixed_response,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "normalized_observation": str(executed["locked_normalized_observation"]),
        "metrics": metrics,
        "target_action_sha256": contract["target_action_sha256"],
        "target_observation_sha256": contract["target_observation_sha256"],
        "live_worker": {
            "complete": bool(executed["complete"]),
            "same_world_execution": bool(executed["same_world_execution"]),
            "same_python_namespace": bool(executed["same_python_namespace"]),
            "task_identity_checks": ready["task_identity_checks"],
            "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
            "actual_replay_observations": ready["actual_observations"],
            "prepared_state_fingerprint": ready["prepared_state_fingerprint"],
            "pre_execution_state_fingerprint": executed["state_before"],
            "post_execution_state_fingerprint": executed["state_after"],
            "execution_exception": executed["execution_exception"],
            "task_completed": bool(executed["task_completed"]),
            "target_semantic_comparison": executed["target_semantic_comparison"],
            "bridge_stderr_path": str(stderr_path),
        },
        "model_name": model_name,
        "model_dtype": str(generation["dtype"]),
        "temperature": float(generation["temperature"]),
        "top_p": float(generation["top_p"]),
        "do_sample": False,
        "enable_thinking": False,
        "prompt_tokens": prompt_tokens,
        "requested_max_new_tokens": int(generation["max_new_tokens"]),
        "effective_max_new_tokens": max_new_tokens,
        "completion_tokens": int(output.usage["completion_tokens"]),
        "prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "condition_manifest_sha256": str(condition_manifest["manifest_sha256"]),
        "config_sha256": config_sha256,
        "corpus_lineage_sha256": corpus_lineage_sha256,
        "generation_elapsed_seconds": generation_elapsed_seconds,
        "backend_reported_generation_elapsed_ms": float(output.ttft_ms),
        "condition_elapsed_seconds": time.perf_counter() - started,
    }
    if runtime_provenance is not None:
        row["paired_causal_runtime"] = dict(runtime_provenance)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, row)
    validate_condition_checkpoint(
        row,
        condition=condition,
        condition_manifest_sha256=str(condition_manifest["manifest_sha256"]),
        config_sha256=config_sha256,
        corpus_lineage_sha256=corpus_lineage_sha256,
        model_name=model_name,
    )
    if runtime_provenance is not None and row.get(
        "paired_causal_runtime"
    ) != dict(runtime_provenance):
        raise ValueError("Condition checkpoint runtime provenance differs")
    return row, False


def _run_namespace_probe(
    *,
    conditions: Sequence[Mapping[str, Any]],
    examples: Mapping[str, Any],
    records: Mapping[str, Any],
    settings: Mapping[str, Any],
    semantic_path: Path,
    bridge_script: Path,
    output_path: Path,
    attempt_id: str,
) -> dict[str, Any]:
    for condition in conditions:
        example = examples[str(condition["state_example_id"])]
        contract = _state_contract(example, records[str(condition["state_task_id"])])
        variables = _namespace_variables(contract)
        if not variables:
            continue
        probe_condition = {**dict(condition), "condition_key": "smoke-namespace-probe"}
        prepare = _prepare_message(
            condition=probe_condition,
            contract=contract,
            settings=settings,
            semantic_path=semantic_path,
            bridge_attempt=f"{attempt_id}-namespace-{time.time_ns()}",
        )
        client = LiveBridgeClient(
            executable=Path(str(settings["legacy"]["executable"])),
            bridge_script=bridge_script,
            appworld_root=Path(str(settings["legacy"]["appworld_root"])),
            stderr_path=output_path.with_suffix(".stderr.log"),
            timeout_seconds=float(settings["replay"]["subprocess_timeout_seconds"]),
        )
        try:
            ready = client.prepare(prepare)
            variable = variables[0]
            executed = client.execute(
                condition_key="smoke-namespace-probe",
                ready_nonce=str(ready["ready_nonce"]),
                code=f"print({variable})",
            )
        except BaseException:
            client.terminate()
            raise
        payload = {
            "format": "identity_reconciled_live_namespace_probe_7b_v1",
            "state_example_id": str(condition["state_example_id"]),
            "variable": variable,
            "execution_exception": executed["execution_exception"],
            "same_python_namespace": bool(executed["same_python_namespace"]),
            "passed": executed["execution_exception"] is None,
        }
        atomic_write_json(output_path, payload)
        return payload
    raise RuntimeError("No smoke state contains a replay-created Python variable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
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
    parser.add_argument("--tmux-session", default="exp025b")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7b"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    clean_data = Path(str(settings["reconciled_corpus_dir"]))
    clean_audit = args.artifact_dir / "clean_procedural_audit"
    cache_root = Path(str(settings["cache_rebuild"]["output_root"]))
    paths = {
        "decisions": clean_data / "decision_examples.jsonl",
        "memories": clean_data / "memory_records.jsonl",
        "transitions": cache_root / "transition_preflight/transition_manifest.jsonl",
        "signatures": clean_audit / "clean_transition_signature_manifest.jsonl",
        "conditions": clean_audit / "clean_condition_manifest.json",
        "preflight": clean_audit / "clean_causal_audit_preflight_summary.json",
        "cache_validation": cache_root / "postrun_validation.json",
        "raw_utility": cache_root / "transition_teacher/teacher_cache.jsonl",
        "semantic_module": Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        "bridge_script": Path("scripts/appworld_live_one_step_bridge_7b.py"),
    }
    required = [
        "decisions",
        "memories",
        "transitions",
        "signatures",
        "conditions",
        "preflight",
        "cache_validation",
        "semantic_module",
        "bridge_script",
    ]
    for name in required:
        if not paths[name].exists():
            raise FileNotFoundError(f"Required causal-audit input missing: {name}={paths[name]}")
    if not bool(_json(paths["cache_validation"])["passed"]):
        raise RuntimeError("Clean-cache validation did not pass")
    preflight = _json(paths["preflight"])
    if (
        bool(preflight["runtime_projection"]["requires_explicit_runtime_approval"])
        and not args.approved_over_threshold
    ):
        raise RuntimeError("Projected generation exceeds review threshold")
    if args.phase == "formal":
        smoke_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        if not smoke_path.exists() or not bool(_json(smoke_path)["passed"]):
            raise RuntimeError("Full lifecycle smoke has not passed")

    config_sha256 = sha256_file(args.config)
    data_hashes = {
        name: sha256_file(path) for name, path in paths.items() if path.exists() and path.is_file()
    }
    condition_manifest = _json(paths["conditions"])
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    records = _records_by_task(load_memory_records(paths["memories"]))
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    signatures = {str(row["transition_id"]): row for row in _rows(paths["signatures"])}
    raw_utility = _load_raw_utility(paths["raw_utility"])
    ordered = sorted(
        condition_manifest["conditions"],
        key=lambda row: (
            str(row["state_task_id"]),
            int(row["state_step_id"]),
            str(row["condition_name"]),
        ),
    )
    if args.phase == "smoke":
        state_ids = []
        for row in ordered:
            if str(row["state_example_id"]) not in state_ids:
                state_ids.append(str(row["state_example_id"]))
            if len(state_ids) == 2:
                break
        selected = [
            row
            for row in ordered
            if str(row["state_example_id"]) in state_ids
            and str(row["condition_name"]) in {"C0_bare", "C1_raw_oracle"}
        ]
        if len(selected) != 4:
            raise RuntimeError("Smoke could not select two bare/raw condition pairs")
        output_dir = args.artifact_dir / "lifecycle_smoke/condition_outputs"
    else:
        selected = ordered
        output_dir = args.artifact_dir / "condition_outputs"
    generation = settings["causal_audit"]["generation"]
    backend = HFQwenBackend(
        model_name=str(generation["model_name"]),
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=bool(generation["enable_thinking"]),
        load_model=True,
    )
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Frozen-Qwen contract failed")
    corpus_lineage = str(settings["expected_corpus_lineage_sha256"])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"live_one_step_{args.phase}",
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
        if args.phase == "smoke":
            probe = _run_namespace_probe(
                conditions=selected,
                examples=examples,
                records=records,
                settings=settings,
                semantic_path=paths["semantic_module"],
                bridge_script=paths["bridge_script"],
                output_path=args.artifact_dir / "lifecycle_smoke/namespace_probe.json",
                attempt_id=args.attempt_id,
            )
            interrupt = selected[0]
            contract = _state_contract(
                examples[str(interrupt["state_example_id"])],
                records[str(interrupt["state_task_id"])],
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
                    condition=interrupt,
                    contract=contract,
                    settings=settings,
                    semantic_path=paths["semantic_module"],
                    bridge_attempt=f"{args.attempt_id}-simulated-interrupt",
                )
            )
            client.terminate()
            interrupted_output = output_dir / condition_checkpoint_name(
                str(interrupt["condition_key"])
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
        resumed = 0
        started = time.perf_counter()
        for position, condition in enumerate(selected, start=1):
            output_path = output_dir / condition_checkpoint_name(str(condition["condition_key"]))
            row, reused = _run_condition(
                condition=condition,
                output_path=output_path,
                stderr_path=(
                    args.artifact_dir
                    / f"worker_logs/{args.phase}/{condition_checkpoint_name(str(condition['condition_key']))}.stderr.log"
                ),
                attempt_id=args.attempt_id,
                ordinal=position,
                settings=settings,
                config_sha256=config_sha256,
                corpus_lineage_sha256=corpus_lineage,
                condition_manifest=condition_manifest,
                example=examples[str(condition["state_example_id"])],
                record=records[str(condition["state_task_id"])],
                transitions=transitions,
                signatures=signatures,
                raw_utility=raw_utility,
                backend=backend,
                semantic_path=paths["semantic_module"],
                bridge_script=paths["bridge_script"],
            )
            rows.append(row)
            resumed += int(reused)
            elapsed = time.perf_counter() - started
            newly = position - resumed
            seconds_per_new = elapsed / max(newly, 1)
            attempt.progress(
                status=f"{args.phase}_condition_generation",
                completed_conditions=position,
                total_conditions=len(selected),
                resumed_conditions=resumed,
                newly_completed_conditions=newly,
                estimated_remaining_seconds=seconds_per_new * (len(selected) - position),
                latest_validated_checkpoint=str(output_path),
            )
            print(
                json.dumps(
                    {
                        "completed": position,
                        "total": len(selected),
                        "condition": condition["condition_name"],
                        "state": condition["state_example_id"],
                        "resumed": reused,
                        "estimated_remaining_seconds": seconds_per_new * (len(selected) - position),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        summary = {
            "format": f"identity_reconciled_one_step_{args.phase}_summary_7b_v1",
            "phase": args.phase,
            "condition_count": len(rows),
            "unique_condition_count": len({row["condition_key"] for row in rows}),
            "resumed_condition_count": resumed,
            "new_condition_count": len(rows) - resumed,
            "same_world_count": sum(
                bool(row["live_worker"]["same_world_execution"]) for row in rows
            ),
            "same_namespace_count": sum(
                bool(row["live_worker"]["same_python_namespace"]) for row in rows
            ),
            "history_replay_pass_count": sum(
                bool(row["live_worker"]["history_semantic_v3_match"]) for row in rows
            ),
            "execution_exception_count": sum(
                row["live_worker"]["execution_exception"] is not None for row in rows
            ),
            "raw_nll_utility_available_count": sum(
                row["raw_nll_text_utility"] is not None for row in rows
            ),
            "qwen_generation_seconds": sum(
                float(row["generation_elapsed_seconds"]) for row in rows
            ),
            "qwen_generation_h100_hours": sum(
                float(row["generation_elapsed_seconds"]) for row in rows
            )
            / 3600.0,
            "mean_backend_reported_generation_elapsed_ms": sum(
                float(row["backend_reported_generation_elapsed_ms"]) for row in rows
            )
            / len(rows),
            "elapsed_seconds": time.perf_counter() - started,
        }
        if args.phase == "smoke":
            summary["namespace_probe"] = probe
            summary["simulated_interruption"] = interruption
            summary["scientific_metrics_included"] = False
            summary["passed"] = bool(
                len(rows) == 4
                and summary["unique_condition_count"] == 4
                and summary["same_world_count"] == 4
                and summary["same_namespace_count"] == 4
                and summary["history_replay_pass_count"] == 4
                and probe["passed"]
                and interruption["ready_before_termination"]
                and interruption["atomic_output_absent_after_termination"]
            )
            summary_path = args.artifact_dir / "lifecycle_smoke/smoke_summary.json"
        else:
            summary["all_complete"] = bool(
                len(rows) == int(condition_manifest["condition_count"])
                and summary["unique_condition_count"] == int(condition_manifest["condition_count"])
            )
            summary["passed"] = bool(
                summary["all_complete"]
                and summary["same_world_count"] == len(rows)
                and summary["same_namespace_count"] == len(rows)
                and summary["history_replay_pass_count"] == len(rows)
            )
            summary_path = args.artifact_dir / "generation_summary.json"
        atomic_write_json(summary_path, summary)
        if not summary["passed"]:
            raise RuntimeError("clean_corpus_behavioral_audit_infrastructure_invalid")
        attempt.progress(status="completed", latest_validated_checkpoint=str(summary_path))
        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
