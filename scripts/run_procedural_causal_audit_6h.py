from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.procedural_causal_audit_6h import (
    GENERATION_RESULT_VERSION,
    evaluate_generated_action,
    messages_with_signature_card,
    normalize_observation,
    normalized_observation_hash,
    signature_only_card,
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
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found: {path}")
    return rows


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _example_by_state(examples: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for index, example in enumerate(examples):
        identity = state_example_id(index, example)
        if identity in output:
            raise ValueError(f"Duplicate decision example: {identity}")
        output[identity] = example
    return output


def _records_by_task(records: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for record in records:
        task_id = str(record.task_id)
        if task_id in output:
            raise ValueError(f"Duplicate successful trajectory task: {task_id}")
        output[task_id] = record
    return output


def _state_contract(
    *,
    query: Mapping[str, Any],
    example: Any,
    record: Any,
) -> dict[str, Any]:
    step_id = int(query["step_id"])
    raw_steps = list(record.raw_trajectory["steps"])
    if step_id < 1 or step_id > len(raw_steps):
        raise ValueError(
            f"State step {step_id} outside trajectory with {len(raw_steps)} steps"
        )
    target_step = raw_steps[step_id - 1]
    target_code, _ = extract_code_and_fix_content(str(example.target_text))
    record_target_code, _ = extract_code_and_fix_content(
        str(target_step["response"])
    )
    if target_code.strip() != record_target_code.strip():
        raise ValueError(
            f"Decision target differs from successful trajectory: "
            f"{query['state_example_id']}"
        )
    _, _, parsed_history = _parse_appworld_state_text(example.state_text)
    if len(parsed_history) != step_id - 1:
        raise ValueError(
            f"State history length differs for {query['state_example_id']}: "
            f"{len(parsed_history)} != {step_id - 1}"
        )
    for position, (_, response, observation) in enumerate(parsed_history):
        raw = raw_steps[position]
        raw_code, _ = extract_code_and_fix_content(str(raw["response"]))
        parsed_code, _ = extract_code_and_fix_content(response)
        if raw_code.strip() != parsed_code.strip():
            raise ValueError(
                f"History action differs at {query['state_example_id']} step "
                f"{position + 1}"
            )
        if normalize_observation(observation) != normalize_observation(
            str(raw["observation"])
        ):
            raise ValueError(
                f"History observation differs at {query['state_example_id']} step "
                f"{position + 1}"
            )
    identity = {
        "state_example_id": str(query["state_example_id"]),
        "task_id": str(query["task_id"]),
        "step_id": step_id,
        "history_action_hashes": [
            hashlib.sha256(str(step["response"]).encode("utf-8")).hexdigest()
            for step in raw_steps[: step_id - 1]
        ],
        "history_observation_hashes": [
            normalized_observation_hash(str(step["observation"]))
            for step in raw_steps[: step_id - 1]
        ],
        "target_action_sha256": hashlib.sha256(
            str(target_step["response"]).encode("utf-8")
        ).hexdigest(),
        "target_observation_sha256": normalized_observation_hash(
            str(target_step["observation"])
        ),
    }
    return {
        **identity,
        "history_steps": raw_steps[: step_id - 1],
        "target_action": str(target_step["response"]),
        "target_code": record_target_code,
        "target_observation": str(target_step["observation"]),
        "environment_reconstruction_sha256": hashlib.sha256(
            json.dumps(
                identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
    }


def _execute_history(world: Any, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for position, step in enumerate(contract["history_steps"], start=1):
        code, _ = extract_code_and_fix_content(str(step["response"]))
        if not code.strip():
            raise ValueError(
                f"Recorded history has no executable code at step {position}"
            )
        actual = str(world.execute(code))
        expected = str(step["observation"])
        checks.append(
            {
                "step_id": position,
                "action_sha256": hashlib.sha256(
                    code.encode("utf-8")
                ).hexdigest(),
                "expected_observation_sha256": normalized_observation_hash(
                    expected
                ),
                "actual_observation_sha256": normalized_observation_hash(actual),
                "observation_match": normalize_observation(actual)
                == normalize_observation(expected),
            }
        )
    return checks


def _world(task_id: str, experiment_name: str, settings: Mapping[str, Any]) -> Any:
    from appworld import AppWorld

    replay = settings["replay"]
    return AppWorld(
        task_id=task_id,
        experiment_name=experiment_name,
        load_ground_truth=False,
        random_seed=int(replay["random_seed"]),
        max_interactions=int(replay["max_interactions"]),
        max_api_calls_per_interaction=int(
            replay["max_api_calls_per_interaction"]
        ),
    )


def _validate_replay_output(
    path: Path,
    *,
    state_id: str,
    config_sha256: str,
    reconstruction_sha256: str,
) -> dict[str, Any]:
    row = _load_json(path)
    checks = {
        "state": str(row.get("state_example_id")) == state_id,
        "config": row.get("config_sha256") == config_sha256,
        "reconstruction": row.get("environment_reconstruction_sha256")
        == reconstruction_sha256,
        "passed": bool(row.get("passed")),
    }
    if not all(checks.values()):
        raise ValueError(
            f"Invalid replay checkpoint {path}: "
            f"{[key for key, value in checks.items() if not value]}"
        )
    return row


def _run_replay(
    *,
    artifact_dir: Path,
    settings: Mapping[str, Any],
    config_sha256: str,
    audit_rows: Sequence[Mapping[str, Any]],
    examples_by_state: Mapping[str, Any],
    records_by_task: Mapping[str, Any],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    output_dir = artifact_dir / "replay" / "states"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    resumed = 0
    computed = 0
    started = time.perf_counter()
    for position, query in enumerate(audit_rows, start=1):
        state_id = str(query["state_example_id"])
        task_id = str(query["task_id"])
        contract = _state_contract(
            query=query,
            example=examples_by_state[state_id],
            record=records_by_task[task_id],
        )
        output_path = output_dir / f"{hashlib.sha256(state_id.encode()).hexdigest()}.json"
        if output_path.exists():
            row = _validate_replay_output(
                output_path,
                state_id=state_id,
                config_sha256=config_sha256,
                reconstruction_sha256=str(
                    contract["environment_reconstruction_sha256"]
                ),
            )
            resumed += 1
        else:
            state_started = time.perf_counter()
            experiment_name = (
                f"exp024a_replay_{hashlib.sha256(state_id.encode()).hexdigest()[:16]}"
            )
            with _world(task_id, experiment_name, settings) as world:
                history_checks = _execute_history(world, contract)
                actual_target_observation = str(
                    world.execute(str(contract["target_code"]))
                )
                target_match = normalize_observation(
                    actual_target_observation
                ) == normalize_observation(str(contract["target_observation"]))
            row = {
                "format": "appworld_one_step_replay_validation_6h_v1",
                "state_example_id": state_id,
                "task_id": task_id,
                "step_id": int(query["step_id"]),
                "history_step_count": len(contract["history_steps"]),
                "history_checks": history_checks,
                "history_match": all(
                    bool(value["observation_match"])
                    for value in history_checks
                ),
                "target_action_sha256": contract["target_action_sha256"],
                "expected_target_observation_sha256": contract[
                    "target_observation_sha256"
                ],
                "actual_target_observation_sha256": normalized_observation_hash(
                    actual_target_observation
                ),
                "target_observation_match": target_match,
                "passed": all(
                    bool(value["observation_match"])
                    for value in history_checks
                )
                and target_match,
                "environment_isolation": "fresh_AppWorld_instance_per_state",
                "environment_reconstruction_sha256": contract[
                    "environment_reconstruction_sha256"
                ],
                "normalization_version": settings["replay"][
                    "observation_normalization"
                ],
                "config_sha256": config_sha256,
                "elapsed_seconds": time.perf_counter() - state_started,
            }
            atomic_write_json(output_path, row)
            computed += 1
        rows.append(row)
        attempt.progress(
            phase="exact_appworld_replay",
            completed_states=position,
            total_states=len(audit_rows),
            resumed_states=resumed,
            newly_replayed_states=computed,
            failed_states=sum(not bool(value["passed"]) for value in rows),
            elapsed_seconds=time.perf_counter() - started,
            latest_validated_checkpoint=str(output_path),
        )
    summary = {
        "format": "appworld_one_step_replay_summary_6h_v1",
        "state_count": len(rows),
        "passed_state_count": sum(bool(row["passed"]) for row in rows),
        "failed_state_count": sum(not bool(row["passed"]) for row in rows),
        "resumed_state_count": resumed,
        "newly_replayed_state_count": computed,
        "all_states_passed": all(bool(row["passed"]) for row in rows),
        "elapsed_seconds": time.perf_counter() - started,
        "state_output_hashes": {
            str(row["state_example_id"]): sha256_file(
                output_dir
                / f"{hashlib.sha256(str(row['state_example_id']).encode()).hexdigest()}.json"
            )
            for row in rows
        },
    }
    atomic_write_json(artifact_dir / "replay" / "replay_summary.json", summary)
    if not summary["all_states_passed"]:
        raise RuntimeError("appworld_one_step_replay_invalid")
    return summary


def _messages_for_condition(
    *,
    condition: Mapping[str, Any],
    example: Any,
    transitions_by_id: Mapping[str, Mapping[str, Any]],
    signatures_by_id: Mapping[str, Mapping[str, Any]],
    prompt_profile: str,
) -> list[dict[str, str]]:
    messages = _appworld_messages_from_example(example, prompt_profile)
    kind = str(condition["prompt_kind"])
    transition_id = condition.get("transition_id")
    if kind == "raw_transition":
        return messages_with_transition_memory(
            messages, transitions_by_id[str(transition_id)], prompt_profile
        )
    if kind == "signature_card":
        return messages_with_signature_card(
            messages,
            signature_only_card(signatures_by_id[str(transition_id)]),
            prompt_profile,
        )
    if kind != "bare":
        raise ValueError(f"Unknown condition prompt kind: {kind}")
    return messages


def _validate_generation_output(
    path: Path,
    *,
    condition: Mapping[str, Any],
    config_sha256: str,
    condition_manifest_sha256: str,
    prompt_sha256: str,
    model_name: str,
) -> dict[str, Any]:
    row = _load_json(path)
    checks = {
        "format": row.get("format") == GENERATION_RESULT_VERSION,
        "condition_key": row.get("condition_key")
        == condition["condition_key"],
        "config": row.get("config_sha256") == config_sha256,
        "manifest": row.get("condition_manifest_sha256")
        == condition_manifest_sha256,
        "prompt": row.get("prompt_sha256") == prompt_sha256,
        "model": row.get("model_name") == model_name,
        "complete": row.get("status") == "complete",
    }
    if not all(checks.values()):
        raise ValueError(
            f"Invalid generation checkpoint {path}: "
            f"{[key for key, value in checks.items() if not value]}"
        )
    return row


def _run_generation(
    *,
    artifact_dir: Path,
    settings: Mapping[str, Any],
    config_sha256: str,
    condition_manifest: Mapping[str, Any],
    prompt_preflight: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    examples_by_state: Mapping[str, Any],
    records_by_task: Mapping[str, Any],
    transitions_by_id: Mapping[str, Mapping[str, Any]],
    signatures_by_id: Mapping[str, Mapping[str, Any]],
    raw_utility_by_pair: Mapping[tuple[str, str], float],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    replay_summary = _load_json(artifact_dir / "replay" / "replay_summary.json")
    if not bool(replay_summary.get("all_states_passed")):
        raise RuntimeError("Exact AppWorld replay validation has not passed")
    query_by_id = {
        str(row["state_example_id"]): row for row in audit_rows
    }
    preflight_by_key = {
        str(row["condition_key"]): row for row in prompt_preflight
    }
    conditions = list(condition_manifest["conditions"])
    if set(preflight_by_key) != {
        str(row["condition_key"]) for row in conditions
    }:
        raise ValueError("Prompt preflight and condition manifest differ")
    output_dir = artifact_dir / "condition_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    generation = settings["generation"]
    backend = HFQwenBackend(
        model_name=str(generation["model_name"]),
        dtype=str(generation["dtype"]),
        device_map=generation.get("device_map"),
        freeze_backbone=True,
        enable_thinking=bool(generation["enable_thinking"]),
        load_model=True,
    )
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Frozen-Qwen contract failed")

    resumed = 0
    computed = 0
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    ordered = sorted(
        conditions,
        key=lambda row: (
            str(row["state_task_id"]),
            int(row["state_step_id"]),
            str(row["condition_name"]),
        ),
    )
    for position, condition in enumerate(ordered, start=1):
        condition_key = str(condition["condition_key"])
        state_id = str(condition["state_example_id"])
        task_id = str(condition["state_task_id"])
        query = query_by_id[state_id]
        preflight = preflight_by_key[condition_key]
        output_path = output_dir / f"{condition_key}.json"
        if output_path.exists():
            row = _validate_generation_output(
                output_path,
                condition=condition,
                config_sha256=config_sha256,
                condition_manifest_sha256=str(
                    condition_manifest["manifest_sha256"]
                ),
                prompt_sha256=str(preflight["prompt_sha256"]),
                model_name=str(generation["model_name"]),
            )
            resumed += 1
        else:
            condition_started = time.perf_counter()
            contract = _state_contract(
                query=query,
                example=examples_by_state[state_id],
                record=records_by_task[task_id],
            )
            messages = _messages_for_condition(
                condition=condition,
                example=examples_by_state[state_id],
                transitions_by_id=transitions_by_id,
                signatures_by_id=signatures_by_id,
                prompt_profile=str(generation["prompt_profile"]),
            )
            rendered = backend.render_messages(messages, add_generation_prompt=True)
            prompt_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            if prompt_hash != preflight["prompt_sha256"]:
                raise ValueError(
                    f"Prompt hash differs for condition {condition_key}"
                )
            experiment_name = f"exp024a_condition_{condition_key[:16]}"
            with _world(task_id, experiment_name, settings) as world:
                history_checks = _execute_history(world, contract)
                if not all(
                    bool(value["observation_match"])
                    for value in history_checks
                ):
                    raise RuntimeError(
                        f"Replay drift before condition {condition_key}"
                    )
                output = backend.generate(
                    messages=messages,
                    max_new_tokens=int(preflight["effective_max_new_tokens"]),
                    temperature=float(generation["temperature"]),
                    top_p=float(generation["top_p"]),
                )
                code, fixed_response = extract_code_and_fix_content(output.text)
                observation = str(world.execute(code))
            metrics = evaluate_generated_action(
                output.text,
                code,
                str(contract["target_action"]),
                observation,
                str(contract["target_observation"]),
            )
            transition_id = condition.get("transition_id")
            raw_utility = (
                raw_utility_by_pair.get((state_id, str(transition_id)))
                if transition_id is not None
                else None
            )
            row = {
                "format": GENERATION_RESULT_VERSION,
                "status": "complete",
                "condition_key": condition_key,
                "condition_name": condition["condition_name"],
                "prompt_kind": condition["prompt_kind"],
                "state_example_id": state_id,
                "state_task_id": task_id,
                "state_step_id": int(condition["state_step_id"]),
                "audit_stratum": condition["audit_stratum"],
                "transition_id": transition_id,
                "transition_parent_id": condition.get("transition_parent_id"),
                "signature_class_id": condition.get("signature_class_id"),
                "signature_sha256": condition.get("signature_sha256"),
                "signature_class_size": condition.get("signature_class_size"),
                "procedural_tier": condition.get("procedural_tier"),
                "api_documentation_action": condition.get(
                    "api_documentation_action"
                ),
                "raw_nll_text_utility": raw_utility,
                "raw_model_response": output.text,
                "fixed_model_response": fixed_response,
                "extracted_code": code,
                "execution_output": observation,
                "normalized_observation": normalize_observation(observation),
                "metrics": metrics,
                "target_action_sha256": contract["target_action_sha256"],
                "target_observation_sha256": contract[
                    "target_observation_sha256"
                ],
                "environment_reconstruction_sha256": contract[
                    "environment_reconstruction_sha256"
                ],
                "history_replay_match": True,
                "model_name": str(generation["model_name"]),
                "model_dtype": str(generation["dtype"]),
                "temperature": float(generation["temperature"]),
                "top_p": float(generation["top_p"]),
                "do_sample": False,
                "enable_thinking": False,
                "requested_max_new_tokens": int(
                    preflight["requested_max_new_tokens"]
                ),
                "effective_max_new_tokens": int(
                    preflight["effective_max_new_tokens"]
                ),
                "prompt_tokens": int(preflight["prompt_tokens"]),
                "completion_tokens": int(output.usage["completion_tokens"]),
                "prompt_sha256": prompt_hash,
                "condition_manifest_sha256": condition_manifest[
                    "manifest_sha256"
                ],
                "config_sha256": config_sha256,
                "generation_elapsed_ms": float(output.ttft_ms),
                "condition_elapsed_seconds": time.perf_counter()
                - condition_started,
            }
            atomic_write_json(output_path, row)
            computed += 1
        rows.append(row)
        gpu = {}
        try:
            import torch

            if torch.cuda.is_available():
                gpu = {
                    "gpu_memory_allocated_bytes": int(
                        torch.cuda.memory_allocated()
                    ),
                    "gpu_memory_reserved_bytes": int(
                        torch.cuda.memory_reserved()
                    ),
                }
        except Exception:
            gpu = {}
        elapsed = time.perf_counter() - started
        rate = elapsed / max(computed, 1)
        attempt.progress(
            phase="deterministic_qwen_one_step_generation",
            completed_conditions=position,
            total_conditions=len(ordered),
            resumed_conditions=resumed,
            newly_generated_conditions=computed,
            elapsed_seconds=elapsed,
            estimated_remaining_seconds=rate * (len(ordered) - position),
            latest_validated_checkpoint=str(output_path),
            **gpu,
        )
        print(
            json.dumps(
                {
                    "completed": position,
                    "total": len(ordered),
                    "state": state_id,
                    "condition": condition["condition_name"],
                    "elapsed_seconds": elapsed,
                    "estimated_remaining_seconds": rate
                    * (len(ordered) - position),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    summary = {
        "format": "procedural_causal_generation_summary_6h_v1",
        "condition_count": len(rows),
        "unique_condition_key_count": len(
            {str(row["condition_key"]) for row in rows}
        ),
        "resumed_condition_count": resumed,
        "newly_generated_condition_count": computed,
        "elapsed_seconds": time.perf_counter() - started,
        "total_completion_tokens": sum(
            int(row["completion_tokens"]) for row in rows
        ),
        "raw_nll_utility_available_count": sum(
            row.get("raw_nll_text_utility") is not None for row in rows
        ),
        "all_complete": len(rows) == len(ordered),
    }
    if summary["unique_condition_key_count"] != len(ordered):
        raise ValueError("Generated condition outputs contain duplicate keys")
    atomic_write_json(artifact_dir / "generation_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_procedural_causal_audit_6h.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("replay", "generate"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024a")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    parser.add_argument("--approved-over-threshold", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    config_sha256 = sha256_file(args.config)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
        "transition_manifest": exp017 / "transition_manifest.jsonl",
        "teacher_cache": exp020 / "teacher_cache.jsonl",
        "one_step_query_manifest": exp022 / "one_step_query_manifest.json",
        "condition_manifest": args.artifact_dir / "condition_manifest.json",
        "prompt_preflight": args.artifact_dir
        / "condition_prompt_preflight.jsonl",
        "signature_manifest": args.artifact_dir
        / "signature_equivalence_manifest.json",
        "preflight_summary": args.artifact_dir / "preflight_summary.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Required input missing: {name}={path}")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    preflight_summary = _load_json(paths["preflight_summary"])
    if (
        args.phase == "generate"
        and bool(
            preflight_summary["runtime_projection"][
                "requires_explicit_runtime_approval"
            ]
        )
        and not args.approved_over_threshold
    ):
        raise RuntimeError(
            "Projected runtime exceeds the review threshold; explicit approval required"
        )

    examples = load_decision_examples(paths["decision_examples"])
    records = load_memory_records(paths["memory_records"])
    examples_by_state = _example_by_state(examples)
    records_by_task = _records_by_task(records)
    audit_rows = list(_load_json(paths["one_step_query_manifest"])["rows"])
    condition_manifest = _load_json(paths["condition_manifest"])
    prompt_preflight = _load_rows(paths["prompt_preflight"])
    transitions = _load_rows(paths["transition_manifest"])
    transitions_by_id = {
        str(row["transition_id"]): row for row in transitions
    }
    signature_source = Path(settings["exp023_artifact"]) / (
        "full_transition_signature_manifest.jsonl"
    )
    signatures_by_id = {
        str(row["transition_id"]): row for row in _load_rows(signature_source)
    }
    teacher_rows = _load_rows(paths["teacher_cache"])
    raw_utility_by_pair = {
        (str(row["state_example_id"]), str(row["transition_id"])): float(
            row["text_utility"]
        )
        for row in teacher_rows
        if bool(row["valid_for_loss"])
    }

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=(
            "exact_appworld_one_step_replay"
            if args.phase == "replay"
            else "deterministic_qwen_generation_and_execution"
        ),
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
        if args.phase == "replay":
            summary = _run_replay(
                artifact_dir=args.artifact_dir,
                settings=settings,
                config_sha256=config_sha256,
                audit_rows=audit_rows,
                examples_by_state=examples_by_state,
                records_by_task=records_by_task,
                attempt=attempt,
            )
        else:
            summary = _run_generation(
                artifact_dir=args.artifact_dir,
                settings=settings,
                config_sha256=config_sha256,
                condition_manifest=condition_manifest,
                prompt_preflight=prompt_preflight,
                audit_rows=audit_rows,
                examples_by_state=examples_by_state,
                records_by_task=records_by_task,
                transitions_by_id=transitions_by_id,
                signatures_by_id=signatures_by_id,
                raw_utility_by_pair=raw_utility_by_pair,
                attempt=attempt,
            )
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
