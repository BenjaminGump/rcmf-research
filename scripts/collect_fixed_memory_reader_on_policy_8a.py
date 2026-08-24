from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
import torch.nn.functional as F

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.config import load_config
from rcmf.training.appworld_semantic_replay_6h2 import canonical_hash, identity_hashes
from rcmf.training.appworld_structured_rescue_7hr import classify_paired_outcome
from rcmf.training.datasets import load_memory_records
from rcmf.training.fixed_memory_reader_8a import GLOBAL_SEED, stratified_live_steps
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.procedural_causal_audit_6h import evaluate_generated_action
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_procedural_causal_audit_7b import LiveBridgeClient
from scripts.run_raw_memory_first37_7f import (
    FullAgentBridge,
    FrozenDeploymentSelector,
    PROTOCOL_VERSION,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


COLLECTION_FORMAT = "fixed_memory_reader_on_policy_collection_8a_v1"
STATE_FORMAT = "fixed_memory_reader_on_policy_state_8a_v1"
PAIR_RESULT_FORMAT = "fixed_memory_reader_paired_condition_8a_v1"
PAIR_SUMMARY_FORMAT = "fixed_memory_reader_paired_outcomes_8a_v1"
LIVE_PROTOCOL_VERSION = "appworld_live_one_step_bridge_7b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_fixed_memory_reader_8a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("collect", "paired"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp029a_collect")
    parser.add_argument("--task-limit", type=int)
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "on_policy"
    return {
        "preflight": artifact_dir / "runtime_preflight.json",
        "memories": corpus / "memory_records.jsonl",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "task_results": root / "task_results",
        "task_tensors": root / "task_tensors",
        "collection_manifest": root / "frozen_state_manifest.json",
        "state_tensors": root / "state_representations.pt",
        "collection_report": root / "collection_report.md",
        "condition_results": artifact_dir / "paired_outcomes/condition_results",
        "condition_logs": artifact_dir / "paired_outcomes/worker_logs",
        "paired_summary": artifact_dir / "paired_outcomes/summary.json",
        "paired_rows": artifact_dir / "paired_outcomes/rows.jsonl",
        "paired_report": artifact_dir / "paired_outcomes/report.md",
        "semantic_module": Path(str(settings["appworld"]["semantic_module"])),
        "one_step_bridge": Path(str(settings["appworld"]["one_step_bridge_script"])),
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-029A collection input: {missing}")


def _task_result_path(paths: Mapping[str, Path], task_id: str) -> Path:
    return paths["task_results"] / f"{task_id}.json"


def _task_tensor_path(paths: Mapping[str, Path], task_id: str) -> Path:
    return paths["task_tensors"] / f"{task_id}.pt"


def _condition_path(
    paths: Mapping[str, Path], state_id: str, condition: str
) -> Path:
    key = sha256_text(f"{state_id}::{condition}")
    return paths["condition_results"] / f"{key}.json"


def _policy_distribution(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    generated_ids: Sequence[int],
    top_k: int,
) -> dict[str, Any]:
    tokenized = backend.tokenize_messages(
        list(messages), add_generation_prompt=True
    )
    prompt_ids = tokenized.input_ids
    target = torch.tensor(
        [list(map(int, generated_ids))], dtype=torch.long, device=backend.device
    )
    if target.numel() == 0:
        raise RuntimeError("Policy teacher generated no tokens")
    full_ids = torch.cat((prompt_ids, target), dim=1)
    labels = torch.full_like(full_ids, -100)
    labels[:, prompt_ids.shape[1] :] = target
    with torch.no_grad():
        scored = backend.forward_train(
            input_ids=full_ids,
            attention_mask=torch.ones_like(full_ids),
            labels=labels,
        )
        logits = scored.logits.to(torch.float32)
    if int(logits.shape[0]) != int(target.shape[1]):
        raise ValueError("Policy-teacher logits and generated IDs are misaligned")
    k = min(int(top_k), int(logits.shape[-1]))
    top_logits, top_ids = torch.topk(logits, k=k, dim=-1)
    log_normalizer = torch.logsumexp(logits.to(torch.float64), dim=-1)
    top_logprobs = top_logits.to(torch.float64) - log_normalizer.unsqueeze(1)
    probabilities = top_logprobs.exp()
    target_logprobs = F.log_softmax(logits.to(torch.float64), dim=-1)[
        torch.arange(int(target.shape[1]), device=backend.device), target[0]
    ]
    positions = []
    for index in range(int(target.shape[1])):
        positions.append(
            {
                "position": index,
                "teacher_token_id": int(target[0, index]),
                "teacher_token_logprob": float(target_logprobs[index].cpu()),
                "top_token_ids": [
                    int(value) for value in top_ids[index].cpu().tolist()
                ],
                "top_logits": [float(value) for value in top_logits[index].cpu()],
                "top_logprobs": [
                    float(value) for value in top_logprobs[index].cpu()
                ],
                "other_probability": max(
                    0.0, 1.0 - float(probabilities[index].sum().cpu())
                ),
            }
        )
    rendered = str(tokenized.metadata["text"])
    return {
        "format": "fixed_memory_reader_sparse_policy_teacher_8a_v1",
        "generated_token_ids": [int(value) for value in generated_ids],
        "generated_token_count": len(generated_ids),
        "positions": positions,
        "prompt_sha256": sha256_text(rendered),
        "prompt_tokens": int(prompt_ids.shape[1]),
        "last_user_token_indices": [
            int(value)
            for value in tokenized.metadata.get("last_user_token_indices", [])
        ],
        "top_k": k,
    }


def _state_id(task_id: str, step_id: int, history: Sequence[Mapping[str, Any]]) -> str:
    fingerprint = canonical_hash(
        [
            {
                "step_id": int(row["step_id"]),
                "code_sha256": sha256_text(str(row["code"])),
                "observation_sha256": sha256_text(str(row["observation"])),
            }
            for row in history
        ]
    )[:16]
    return f"appworld:onpolicy:{task_id}:step:{int(step_id)}:{fingerprint}"


def _validate_collection_task(
    row: Mapping[str, Any], *, task_id: str, config_sha256: str
) -> None:
    checks = {
        "format": row.get("format") == COLLECTION_FORMAT,
        "task_id": str(row.get("task_id")) == task_id,
        "config": str(row.get("config_sha256")) == config_sha256,
        "seed": int(row.get("global_seed", -1)) == GLOBAL_SEED,
        "complete": row.get("status") == "complete",
    }
    if not all(checks.values()):
        raise ValueError(f"Existing on-policy task row differs: {task_id}: {checks}")


def _run_collection_task(
    *,
    task_id: str,
    model_split: str,
    record: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    backend: Any,
    selector: FrozenDeploymentSelector,
    config_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    output = _task_result_path(paths, task_id)
    tensor_output = _task_tensor_path(paths, task_id)
    if output.exists() and tensor_output.exists():
        row = _json(output)
        _validate_collection_task(row, task_id=task_id, config_sha256=config_sha256)
        return row
    app = settings["appworld"]
    restart = len(list(paths["task_results"].glob(f"{task_id}.restart.*.json")))
    worker_log = paths["task_results"] / f"{task_id}.restart.{restart:02d}.stderr.log"
    experiment_name = f"exp029a_collect_{attempt_id}_{task_id}_r{restart:02d}"
    started = time.perf_counter()
    trajectory: list[dict[str, str]] = []
    history: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    candidate_tensors: dict[str, torch.Tensor] = {}
    skipped = Counter()
    usage = Counter()
    clean_steps = list(record.raw_trajectory["steps"])
    prior_exception = False
    with FullAgentBridge(
        executable=Path(str(app["legacy_python"])),
        script=Path(str(app["full_bridge_script"])),
        appworld_root=Path(str(app["legacy_root"])),
        stderr_path=worker_log,
        timeout_seconds=float(app["worker_timeout_seconds"]),
    ) as bridge:
        ready = bridge.prepare(
            {
                "format": PROTOCOL_VERSION,
                "op": "prepare",
                "legacy_python": str(app["legacy_python"]),
                "appworld_root": str(app["legacy_root"]),
                "task_id": task_id,
                "experiment_name": experiment_name,
                "random_seed": GLOBAL_SEED,
                "max_interactions": int(app["max_steps"]),
                "max_api_calls_per_interaction": int(
                    app["max_api_calls_per_interaction"]
                ),
            }
        )
        task_message = build_task_message(
            str(ready["instruction"]),
            dict(ready["supervisor"]),
            profile=str(app["prompt_profile"]),
        )
        terminal_error = None
        for step_id in range(1, int(app["max_steps"]) + 1):
            messages = build_appworld_messages(
                task_message=task_message,
                trajectory_so_far=trajectory,
                prompt_profile=str(app["prompt_profile"]),
                max_context_turns=int(app["max_context_turns"]),
            )
            bare = backend.tokenize_messages(messages, add_generation_prompt=True)
            prompt_tokens = int(bare.attention_mask.sum().item())
            if prompt_tokens >= int(app["context_limit"]):
                terminal_error = "bare_prompt_over_locked_context"
                break
            if step_id <= len(clean_steps) and not prior_exception:
                try:
                    selection, state_tensor, scores = selector.select_with_state(
                        messages, prompt_profile=str(app["prompt_profile"])
                    )
                    state_id = _state_id(task_id, step_id, history)
                    target = clean_steps[step_id - 1]
                    target_code, _ = extract_code_and_fix_content(
                        str(target["response"])
                    )
                    candidates.append(
                        {
                            "format": STATE_FORMAT,
                            "state_id": state_id,
                            "task_id": task_id,
                            "step_id": step_id,
                            "model_split": model_split,
                            "task_message": task_message,
                            "history": [dict(value) for value in history],
                            "selected_transition_id": str(selection["transition_id"]),
                            "selected_signature_class_id": str(
                                selection["selected_class_id"]
                            ),
                            "selector_class_score": float(selection["class_score"]),
                            "selector_scores_sha256": canonical_sha256(
                                [float(value) for value in scores]
                            ),
                            "selector_raw_prompt_tokens": int(
                                selection["prompt_tokens"]
                            ),
                            "bare_prompt_tokens": prompt_tokens,
                            "target_action": str(target["response"]),
                            "target_code": target_code,
                            "target_observation": str(target["observation"]),
                            "target_action_sha256": sha256_text(
                                str(target["response"])
                            ),
                            "target_observation_sha256": sha256_text(
                                str(target["observation"])
                            ),
                            "target_alignment": "clean_successful_trajectory_step_ordinal",
                            "outcome_used_for_selection": False,
                        }
                    )
                    candidate_tensors[state_id] = state_tensor[0].detach().cpu()
                except RuntimeError as error:
                    if str(error).startswith(
                        "selected_signature_class_has_no_context_feasible_raw_member"
                    ):
                        skipped["selected_raw_class_over_context"] += 1
                    else:
                        raise
            elif step_id > len(clean_steps):
                skipped["no_clean_target_at_live_step"] += 1
            elif prior_exception:
                skipped["prior_history_exception"] += 1

            generated = backend.generate(
                messages=messages,
                max_new_tokens=min(
                    int(app["max_new_tokens"]),
                    int(app["context_limit"]) - prompt_tokens,
                ),
                temperature=float(app["temperature"]),
                top_p=float(app["top_p"]),
            )
            code, fixed = extract_code_and_fix_content(generated.text)
            executed = bridge.execute(
                nonce=str(ready["ready_nonce"]), step_id=step_id, code=code
            )
            observation = str(executed["raw_observation"])
            exception = executed["execution_exception"]
            history.append(
                {
                    "step_id": step_id,
                    "response": fixed,
                    "code": code,
                    "observation": observation,
                    "execution_exception": exception,
                }
            )
            trajectory.append({"response": fixed, "observation": observation})
            usage.update(
                {
                    key: int(generated.usage.get(key, 0))
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            )
            prior_exception = prior_exception or exception is not None
            if bool(executed["task_completed"]):
                break
        final = bridge.finish(nonce=str(ready["ready_nonce"]))

    maximum = int(settings["collection"]["maximum_live_states_per_task"])
    selected_steps = set(
        stratified_live_steps(
            [int(row["step_id"]) for row in candidates],
            task_id=task_id,
            maximum=maximum,
        )
    )
    selected_rows = [
        row for row in candidates if int(row["step_id"]) in selected_steps
    ]
    selected_ids = [str(row["state_id"]) for row in selected_rows]
    tensor_payload = {
        "format": "fixed_memory_reader_task_state_tensors_8a_v1",
        "task_id": task_id,
        "ordered_state_ids": selected_ids,
        "values": torch.stack([candidate_tensors[value] for value in selected_ids])
        if selected_ids
        else torch.empty((0, 10, 4096), dtype=torch.float32),
    }
    atomic_torch_save(tensor_payload, tensor_output)
    row = {
        "format": COLLECTION_FORMAT,
        "status": "complete",
        "global_seed": GLOBAL_SEED,
        "task_id": task_id,
        "model_split": model_split,
        "task_message": task_message,
        "task_message_sha256": sha256_text(task_message),
        "live_step_count": len(history),
        "clean_target_step_count": len(clean_steps),
        "candidate_count_before_stratification": len(candidates),
        "selected_state_count": len(selected_rows),
        "selected_states": selected_rows,
        "selected_state_ids": selected_ids,
        "skipped": dict(sorted(skipped.items())),
        "history_execution_exception_count": sum(
            value["execution_exception"] is not None for value in history
        ),
        "evaluation": final.get("evaluation"),
        "usage": dict(usage),
        "terminal_error": terminal_error,
        "state_tensor_path": str(tensor_output),
        "state_tensor_sha256": sha256_file(tensor_output),
        "config_sha256": config_sha256,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output, row)
    _validate_collection_task(row, task_id=task_id, config_sha256=config_sha256)
    return row


def _collect(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    args: argparse.Namespace,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    preflight = _json(paths["preflight"])
    if not bool(preflight["passed"]):
        raise RuntimeError("Runtime preflight did not authorize collection")
    task_split = preflight["task_split"]
    train_ids = [str(value) for value in task_split["model_train_task_ids"]]
    validation_ids = [
        str(value) for value in task_split["heldout_train_validation_task_ids"]
    ]
    tasks = [(value, "model_train") for value in train_ids] + [
        (value, "heldout_train_validation") for value in validation_ids
    ]
    if args.task_limit is not None:
        tasks = tasks[: int(args.task_limit)]
    records = {str(row.task_id): row for row in load_memory_records(paths["memories"])}
    missing = [task_id for task_id, _ in tasks if task_id not in records]
    if missing:
        raise KeyError(f"Train tasks missing from clean corpus: {missing}")
    backend = _build_backend(cfg)
    selector = FrozenDeploymentSelector(settings=settings, backend=backend)
    config_sha256 = sha256_file(args.config)
    outputs = []
    for ordinal, (task_id, split) in enumerate(tasks, start=1):
        row = _run_collection_task(
            task_id=task_id,
            model_split=split,
            record=records[task_id],
            settings=settings,
            paths=paths,
            backend=backend,
            selector=selector,
            config_sha256=config_sha256,
            attempt_id=args.attempt_id,
        )
        outputs.append(row)
        attempt.progress(
            status="on_policy_collection",
            completed_tasks=ordinal,
            total_tasks=len(tasks),
            collected_states=sum(int(value["selected_state_count"]) for value in outputs),
            latest_validated_checkpoint=str(_task_result_path(paths, task_id)),
        )
        print(f"on-policy collection {ordinal}/{len(tasks)}", flush=True)
    if args.task_limit is not None:
        return {"partial": True, "completed_task_count": len(outputs)}

    states = [dict(state) for row in outputs for state in row["selected_states"]]
    states.sort(key=lambda row: (str(row["task_id"]), int(row["step_id"])))
    state_ids = [str(row["state_id"]) for row in states]
    if len(state_ids) != len(set(state_ids)):
        raise ValueError("On-policy collection contains duplicate state IDs")
    tensor_by_id = {}
    for row in outputs:
        payload = torch.load(
            Path(str(row["state_tensor_path"])), map_location="cpu", weights_only=False
        )
        if sha256_file(Path(str(row["state_tensor_path"]))) != str(
            row["state_tensor_sha256"]
        ):
            raise ValueError("Task state tensor changed after atomic write")
        for index, state_id in enumerate(payload["ordered_state_ids"]):
            tensor_by_id[str(state_id)] = payload["values"][index]
    atomic_torch_save(
        {
            "format": "fixed_memory_reader_state_representations_8a_v1",
            "ordered_state_ids": state_ids,
            "values": torch.stack([tensor_by_id[value] for value in state_ids]),
            "view_count": 10,
            "representation_dim": 4096,
        },
        paths["state_tensors"],
    )
    manifest = {
        "format": "fixed_memory_reader_frozen_on_policy_manifest_8a_v1",
        "global_seed": GLOBAL_SEED,
        "state_count": len(states),
        "task_count": len({str(row["task_id"]) for row in states}),
        "model_train_state_count": sum(
            str(row["model_split"]) == "model_train" for row in states
        ),
        "heldout_train_validation_state_count": sum(
            str(row["model_split"]) == "heldout_train_validation" for row in states
        ),
        "states": states,
        "state_representations_sha256": sha256_file(paths["state_tensors"]),
        "selection_frozen_before_paired_outcomes": True,
        "test_normal_state_count": 0,
        "target_alignment": "clean_successful_trajectory_step_ordinal",
        "config_sha256": config_sha256,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_json(paths["collection_manifest"], manifest)
    summary = {
        "task_count": len(outputs),
        "state_count": len(states),
        "model_train_state_count": manifest["model_train_state_count"],
        "heldout_train_validation_state_count": manifest[
            "heldout_train_validation_state_count"
        ],
        "candidate_count_before_stratification": sum(
            int(row["candidate_count_before_stratification"]) for row in outputs
        ),
        "history_execution_exception_count": sum(
            int(row["history_execution_exception_count"]) for row in outputs
        ),
        "wall_seconds": sum(float(row["elapsed_seconds"]) for row in outputs),
        "manifest_sha256": sha256_file(paths["collection_manifest"]),
        "passed": len(outputs) == 37 and len(states) > 0,
    }
    atomic_write_text(
        paths["collection_report"],
        "\n".join(
            [
                "# EXP-029A on-policy train-state collection",
                "",
                f"- tasks: `{summary['task_count']}`",
                f"- frozen states: `{summary['state_count']}`",
                f"- model-train states: `{summary['model_train_state_count']}`",
                f"- heldout train-validation states: `{summary['heldout_train_validation_state_count']}`",
                "- test_normal states: `0`",
                "- paired outcomes used to select states: `false`",
                "- target alignment: same ordinal in the immutable clean successful trajectory",
                f"- manifest SHA256: `{summary['manifest_sha256']}`",
                "",
            ]
        ),
    )
    return summary


def _prepare_on_policy(
    *,
    state: Mapping[str, Any],
    condition: str,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt_id: str,
) -> dict[str, Any]:
    history_steps = [
        {
            "step_id": int(row["step_id"]),
            "code": str(row["code"]),
            "expected_observation": str(row["observation"]),
        }
        for row in state["history"]
    ]
    key = f"{state['state_id']}::{condition}"
    experiment_name = "exp029a_pair_" + sha256_text(
        f"{key}:{attempt_id}:{time.time_ns()}"
    )[:24]
    app = settings["appworld"]
    return {
        "format": LIVE_PROTOCOL_VERSION,
        "op": "prepare",
        "condition_key": key,
        "state_example_id": str(state["state_id"]),
        "task_id": str(state["task_id"]),
        "history_steps": history_steps,
        "history_steps_sha256": canonical_hash(history_steps),
        "expected_identity_field_sha256": identity_hashes(
            str(state["task_message"])
        ),
        "legacy_python": str(app["legacy_python"]),
        "appworld_root": str(app["legacy_root"]),
        "semantic_module_path": str(paths["semantic_module"].resolve()),
        "semantic_module_sha256": sha256_file(paths["semantic_module"]),
        "normalization_version": "appworld_observation_semantic_normalization_7b_v1",
        "experiment_name": experiment_name,
        "random_seed": GLOBAL_SEED,
        "max_interactions": int(app["max_steps"]),
        "max_api_calls_per_interaction": int(app["max_api_calls_per_interaction"]),
    }


def _paired_messages(
    *,
    state: Mapping[str, Any],
    actual_observations: Sequence[str],
    prompt_profile: str,
) -> list[dict[str, str]]:
    if len(actual_observations) != len(state["history"]):
        raise ValueError("Replay observation count differs from on-policy history")
    trajectory = [
        {
            "response": str(source["response"]),
            "observation": str(observation),
        }
        for source, observation in zip(
            state["history"], actual_observations, strict=True
        )
    ]
    return build_appworld_messages(
        task_message=str(state["task_message"]),
        trajectory_so_far=trajectory,
        prompt_profile=prompt_profile,
        max_context_turns=40,
    )


def _run_paired_condition(
    *,
    state: Mapping[str, Any],
    condition: str,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    transitions: Mapping[str, Mapping[str, Any]],
    backend: Any,
    config_sha256: str,
    manifest_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    output = _condition_path(paths, str(state["state_id"]), condition)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == PAIR_RESULT_FORMAT,
            "state": str(row.get("state_id")) == str(state["state_id"]),
            "condition": str(row.get("condition")) == condition,
            "config": str(row.get("config_sha256")) == config_sha256,
            "manifest": str(row.get("collection_manifest_sha256"))
            == manifest_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing paired outcome differs: {checks}")
        return row
    app = settings["appworld"]
    key = f"{state['state_id']}::{condition}"
    stderr = paths["condition_logs"] / f"{sha256_text(key)}.stderr.log"
    client = LiveBridgeClient(
        executable=Path(str(app["legacy_python"])),
        bridge_script=paths["one_step_bridge"],
        appworld_root=Path(str(app["legacy_root"])),
        stderr_path=stderr,
        timeout_seconds=float(app["worker_timeout_seconds"]),
    )
    started = time.perf_counter()
    try:
        ready = client.prepare(
            _prepare_on_policy(
                state=state,
                condition=condition,
                settings=settings,
                paths=paths,
                attempt_id=attempt_id,
            )
        )
        messages = _paired_messages(
            state=state,
            actual_observations=list(ready["actual_observations"]),
            prompt_profile=str(app["prompt_profile"]),
        )
        if condition == "T1_selected_raw":
            messages = messages_with_transition_memory(
                messages,
                transitions[str(state["selected_transition_id"])],
                str(app["prompt_profile"]),
            )
        elif condition != "T0_bare":
            raise ValueError(f"Unknown paired condition: {condition}")
        tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
        prompt_tokens = int(tokenized.attention_mask.sum().item())
        remaining = int(app["context_limit"]) - prompt_tokens
        if remaining <= 0:
            raise RuntimeError(f"Paired condition exceeds locked context: {key}")
        generated = backend.generate(
            messages=messages,
            max_new_tokens=min(int(app["max_new_tokens"]), remaining),
            temperature=float(app["temperature"]),
            top_p=float(app["top_p"]),
        )
        code, fixed = extract_code_and_fix_content(generated.text)
        executed = client.execute(
            condition_key=key,
            ready_nonce=str(ready["ready_nonce"]),
            code=code,
            expected_target_observation=str(state["target_observation"]),
        )
    except BaseException:
        client.terminate()
        raise
    metrics = evaluate_generated_action(
        generated.text,
        code,
        str(state["target_action"]),
        str(executed["raw_observation"]),
        str(state["target_observation"]),
    )
    if executed["execution_exception"] is not None:
        metrics["execution_success"] = False
        metrics["exception_category"] = str(
            executed["execution_exception"].get("type", "exception")
        ).lower()
    metrics["semantic_successor_match"] = bool(executed["target_semantic_match"])
    teacher = _policy_distribution(
        backend=backend,
        messages=messages,
        generated_ids=generated.token_ids,
        top_k=int(settings["training"]["top_k"]),
    )
    row = {
        "format": PAIR_RESULT_FORMAT,
        "status": "complete",
        "state_id": str(state["state_id"]),
        "task_id": str(state["task_id"]),
        "step_id": int(state["step_id"]),
        "model_split": str(state["model_split"]),
        "condition": condition,
        "selected_transition_id": str(state["selected_transition_id"]),
        "selected_signature_class_id": str(
            state["selected_signature_class_id"]
        ),
        "raw_model_response": generated.text,
        "fixed_model_response": fixed,
        "extracted_code": code,
        "execution_output": str(executed["raw_observation"]),
        "metrics": metrics,
        "policy_teacher": teacher,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": int(generated.usage["completion_tokens"]),
        "prompt_sha256": teacher["prompt_sha256"],
        "history_semantic_v3_match": bool(ready["history_semantic_v3_match"]),
        "actual_replay_observations": [
            str(value) for value in ready["actual_observations"]
        ],
        "task_identity_checks": ready["task_identity_checks"],
        "same_world_execution": bool(executed["same_world_execution"]),
        "same_python_namespace": bool(executed["same_python_namespace"]),
        "execution_exception": executed["execution_exception"],
        "target_action_sha256": str(state["target_action_sha256"]),
        "target_observation_sha256": str(state["target_observation_sha256"]),
        "collection_manifest_sha256": manifest_sha256,
        "config_sha256": config_sha256,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output, row)
    return row


def _paired(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    args: argparse.Namespace,
    attempt: AttemptLedger,
) -> dict[str, Any]:
    manifest = _json(paths["collection_manifest"])
    if not bool(manifest["selection_frozen_before_paired_outcomes"]):
        raise RuntimeError("On-policy state manifest was not prospectively frozen")
    states = list(manifest["states"])
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    backend = _build_backend(cfg)
    config_sha256 = sha256_file(args.config)
    manifest_sha256 = sha256_file(paths["collection_manifest"])
    paired_rows = []
    conditions = ("T0_bare", "T1_selected_raw")
    for ordinal, state in enumerate(states, start=1):
        values = {
            condition: _run_paired_condition(
                state=state,
                condition=condition,
                settings=settings,
                paths=paths,
                transitions=transitions,
                backend=backend,
                config_sha256=config_sha256,
                manifest_sha256=manifest_sha256,
                attempt_id=args.attempt_id,
            )
            for condition in conditions
        }
        bare_metrics = values["T0_bare"]["metrics"]
        raw_metrics = values["T1_selected_raw"]["metrics"]
        label = classify_paired_outcome(
            {
                "semantic_successor_match": bare_metrics[
                    "semantic_successor_match"
                ],
                "action_signature_match": bare_metrics[
                    "canonical_procedural_signature_match"
                ],
                "execution_success": bare_metrics["execution_success"],
            },
            {
                "semantic_successor_match": raw_metrics[
                    "semantic_successor_match"
                ],
                "action_signature_match": raw_metrics[
                    "canonical_procedural_signature_match"
                ],
                "execution_success": raw_metrics["execution_success"],
            },
        )
        paired_rows.append(
            {
                "format": "fixed_memory_reader_paired_state_outcome_8a_v1",
                "state_id": str(state["state_id"]),
                "task_id": str(state["task_id"]),
                "step_id": int(state["step_id"]),
                "model_split": str(state["model_split"]),
                "selected_transition_id": str(state["selected_transition_id"]),
                "selected_signature_class_id": str(
                    state["selected_signature_class_id"]
                ),
                "label": label["label"],
                "label_details": label,
                "bare_result_path": str(
                    _condition_path(paths, str(state["state_id"]), "T0_bare")
                ),
                "raw_result_path": str(
                    _condition_path(
                        paths, str(state["state_id"]), "T1_selected_raw"
                    )
                ),
                "bare_metrics": bare_metrics,
                "raw_metrics": raw_metrics,
            }
        )
        attempt.progress(
            status="on_policy_paired_outcomes",
            completed_states=ordinal,
            total_states=len(states),
            completed_conditions=2 * ordinal,
            latest_validated_checkpoint=str(
                _condition_path(
                    paths, str(state["state_id"]), "T1_selected_raw"
                )
            ),
        )
        if ordinal % 10 == 0 or ordinal == len(states):
            print(f"paired outcomes {ordinal}/{len(states)}", flush=True)
    write_jsonl(paths["paired_rows"], paired_rows)
    labels = Counter(str(row["label"]) for row in paired_rows)
    train_labels = Counter(
        str(row["label"])
        for row in paired_rows
        if str(row["model_split"]) == "model_train"
    )
    validation_labels = Counter(
        str(row["label"])
        for row in paired_rows
        if str(row["model_split"]) == "heldout_train_validation"
    )
    minimum_positive = int(
        settings["collection"]["minimum_model_train_positive_states"]
    )
    summary = {
        "format": PAIR_SUMMARY_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "state_count": len(paired_rows),
        "paired_condition_count": 2 * len(paired_rows),
        "label_counts": dict(sorted(labels.items())),
        "model_train_label_counts": dict(sorted(train_labels.items())),
        "heldout_train_validation_label_counts": dict(
            sorted(validation_labels.items())
        ),
        "model_train_positive_minimum": minimum_positive,
        "augmentation_required": train_labels["POSITIVE"] < minimum_positive,
        "augmentation_needed_count": max(
            0, minimum_positive - train_labels["POSITIVE"]
        ),
        "collection_manifest_sha256": manifest_sha256,
        "paired_rows_sha256": sha256_file(paths["paired_rows"]),
        "test_normal_outcome_count": 0,
        "target_alignment": "clean_successful_trajectory_step_ordinal",
        "wall_seconds": sum(
            float(_json(Path(row["bare_result_path"]))["elapsed_seconds"])
            + float(_json(Path(row["raw_result_path"]))["elapsed_seconds"])
            for row in paired_rows
        ),
        "passed": len(paired_rows) == int(manifest["state_count"]),
    }
    atomic_write_json(paths["paired_summary"], summary)
    atomic_write_text(
        paths["paired_report"],
        "\n".join(
            [
                "# EXP-029A on-policy paired outcomes",
                "",
                f"- states: `{summary['state_count']}`",
                f"- paired T0/T1 conditions: `{summary['paired_condition_count']}`",
                f"- all labels: `{json.dumps(summary['label_counts'], sort_keys=True)}`",
                f"- model-train labels: `{json.dumps(summary['model_train_label_counts'], sort_keys=True)}`",
                f"- heldout validation labels: `{json.dumps(summary['heldout_train_validation_label_counts'], sort_keys=True)}`",
                f"- train-only expert augmentation required: `{str(summary['augmentation_required']).lower()}`",
                "- test_normal outcomes used: `0`",
                "- selection frozen before outcomes: `true`",
                "",
            ]
        ),
    )
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8a"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-029A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "memories",
        "selector",
        "transitions",
        "semantic_module",
        "one_step_bridge",
    )
    if args.phase == "paired":
        required = (*required, "collection_manifest", "state_tensors")
    _require(paths, required)
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"on_policy_{args.phase}",
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
        result = (
            _collect(
                cfg=cfg,
                settings=settings,
                paths=paths,
                args=args,
                attempt=attempt,
            )
            if args.phase == "collect"
            else _paired(
                cfg=cfg,
                settings=settings,
                paths=paths,
                args=args,
                attempt=attempt,
            )
        )
        attempt.progress(
            status=f"on_policy_{args.phase}_complete",
            latest_validated_checkpoint=str(
                paths["collection_manifest"]
                if args.phase == "collect"
                else paths["paired_summary"]
            ),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
