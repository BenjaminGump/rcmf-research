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
from rcmf.training.deep_residual_amortization_7f import aggregate_and_select_class
from rcmf.training.procedural_supervision_6f import (
    _stage_compatibility,
    state_stage_signature,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.training.ungated_structured_e2e_7h2 import (
    GLOBAL_SEED,
    freeze_transition_shuffle,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.prepare_appworld_structured_rescue_7hr import _memory_flags
from scripts.run_appworld_structured_compiled_first37_7hr import (
    CompiledStructuredRuntime,
    _generate_compiled,
)
from scripts.run_appworld_structured_gated_first37_7hr import _live_state_text
from scripts.run_raw_memory_first37_7f import FullAgentBridge, PROTOCOL_VERSION
from scripts.run_state_conditioned_program_fast_7df import _build_backend


RESULT_FORMAT = "ungated_structured_compiler_task_result_7h2_v1"
SUMMARY_FORMAT = "ungated_structured_compiler_first37_summary_7h2_v1"
CONDITIONS = {
    "U1": "ungated_correct_first37",
    "U2": "ungated_transition_shuffle_first37",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_ungated_structured_e2e_7h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--parent-artifact-dir", type=Path, required=True)
    parser.add_argument("--condition", choices=sorted(CONDITIONS), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028b_ungated_first37")
    parser.add_argument("--task-limit", type=int)
    return parser.parse_args()


class UngatedStructuredRuntime(CompiledStructuredRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.transition_shuffle = freeze_transition_shuffle(
            self.selector.ordered_transition_ids,
            self.selector.class_by_transition,
        )

    @torch.no_grad()
    def _feature_for_transition(
        self,
        *,
        state: torch.Tensor,
        transition_id: str,
        class_scores: Sequence[float],
        messages: Sequence[Mapping[str, str]],
        task_message: str,
        trajectory: Sequence[Mapping[str, str]],
        step_id: int,
        prompt_profile: str,
        bare_tokens: int,
    ) -> tuple[list[float], int]:
        transition = self.selector.transitions[transition_id]
        class_id = self.selector.class_by_transition[transition_id]
        signature = self.signatures[transition_id]
        action = signature["action_signature"]
        current_stage = state_stage_signature(_live_state_text(task_message, trajectory))
        stage = _stage_compatibility(
            current_stage, signature["pre_action_stage_signature"]
        )
        raw_messages = messages_with_transition_memory(
            messages, transition, prompt_profile
        )
        raw = self.backend.tokenize_messages(raw_messages, add_generation_prompt=True)
        raw_tokens = int(raw.attention_mask.sum().item())
        source = {
            "state_step_index": int(step_id),
            "history_turn_count": len(trajectory),
            "prompt_tokens": bare_tokens,
            "context_headroom": int(self.settings["appworld"]["context_limit"])
            - bare_tokens,
            "context_limit": int(self.settings["appworld"]["context_limit"]),
            "intent_distributions": self._intent_distributions(state),
            "selector_class_scores": list(map(float, class_scores)),
            "memory_apps": [str(value) for value in transition["apps"]],
            "memory_apis": [str(value) for value in transition["api_names"]],
            "memory_action_type": str(action["coarse_action_type"]),
            "memory_control_flow": [
                str(value) for value in action["control_flow_constructs"]
            ],
            "memory_flags": _memory_flags(action),
            "memory_class_size": int(self.selector.classes[class_id]["class_size"]),
            "memory_token_length": int(transition["teacher_section_tokens"]),
            "memory_parent_step": int(transition["step_index"]),
            "memory_api_call_count": len(action["ordered_api_sequence"]),
            "projected_prompt_overhead": raw_tokens - bare_tokens,
            "stage_compatibility": stage,
        }
        from rcmf.training.appworld_structured_rescue_7hr import build_feature_vector

        values, names = build_feature_vector(self.schema, source)
        if names != list(self.schema.names):
            raise RuntimeError("Ungated structured feature order differs")
        return values, raw_tokens

    @torch.no_grad()
    def decide_ungated(
        self,
        *,
        condition: str,
        messages: Sequence[Mapping[str, str]],
        task_message: str,
        trajectory: Sequence[Mapping[str, str]],
        step_id: int,
        prompt_profile: str,
    ) -> dict[str, Any]:
        state = self.selector._state_values(messages)
        scores = self.selector.scores_from_state(state)
        selected_class = aggregate_and_select_class(
            scores,
            self.selector.transition_class_ids,
            legal_transition_ids=self.selector.ordered_transition_ids,
            ordered_transition_ids=self.selector.ordered_transition_ids,
        )
        class_id = str(selected_class["selected_class_id"])
        canonical = str(self.selector.classes[class_id]["canonical_transition_id"])
        scoreable_fallback = False
        try:
            exemplar = self.selector.select_from_scores(
                messages, scores, prompt_profile=prompt_profile
            )
            correct_transition_id = str(exemplar["transition_id"])
            scoreable_fallback = bool(exemplar["same_class_substitution"])
            selector_raw_over_context = False
        except RuntimeError as error:
            if not str(error).startswith(
                "selected_signature_class_has_no_context_feasible_raw_member"
            ):
                raise
            correct_transition_id = canonical
            selector_raw_over_context = True
        shuffled_transition_id = self.transition_shuffle[correct_transition_id]
        program_transition_id = (
            correct_transition_id if condition == "U1" else shuffled_transition_id
        )
        tokenized = self.backend.tokenize_messages(messages, add_generation_prompt=True)
        bare_tokens = int(tokenized.attention_mask.sum().item())
        class_scores = sorted(
            map(float, selected_class["class_scores"].values()), reverse=True
        )
        correct_feature, correct_raw_tokens = self._feature_for_transition(
            state=state,
            transition_id=correct_transition_id,
            class_scores=class_scores,
            messages=messages,
            task_message=task_message,
            trajectory=trajectory,
            step_id=step_id,
            prompt_profile=prompt_profile,
            bare_tokens=bare_tokens,
        )
        program_feature, program_raw_tokens = self._feature_for_transition(
            state=state,
            transition_id=program_transition_id,
            class_scores=class_scores,
            messages=messages,
            task_message=task_message,
            trajectory=trajectory,
            step_id=step_id,
            prompt_profile=prompt_profile,
            bare_tokens=bare_tokens,
        )
        correct_tensor = torch.tensor(
            [correct_feature], dtype=torch.float32, device=self.backend.device
        )
        logits = self.gate((correct_tensor - self.gate_mean) / self.gate_std)
        probabilities_tensor = F.softmax(logits / self.gate_temperature, dim=-1)[0]
        probabilities = {
            label: float(probabilities_tensor[position].cpu())
            for label, position in self.label_position.items()
        }
        transition = self.representations["transition_values"][
            self.transition_position[program_transition_id]
        ].unsqueeze(0).to(self.backend.device)
        feature = torch.tensor(
            [program_feature], dtype=torch.float32, device=self.backend.device
        )
        normalized = (feature - self.gate_feature_mean) / self.gate_feature_std
        forced_multiplier = torch.ones(1, dtype=torch.float32, device=self.backend.device)
        latent = self.composer(
            normalized, self.parent(state, transition), forced_multiplier
        )
        delta = self.decoder(latent)[0]
        layer_norms = [float(value.norm().cpu()) for value in delta]
        return {
            "condition": condition,
            "prompt_messages": list(messages),
            "student_prompt_contains_raw_transition": False,
            "compiler_forced_multiplier": 1.0,
            "gate_probabilities_diagnostic_only": probabilities,
            "gate_would_activate": bool(
                probabilities["POSITIVE"] >= self.threshold
                and probabilities["HARMFUL"] <= self.maximum_harmful
            ),
            "selected_transition_id": correct_transition_id,
            "selected_class_id": class_id,
            "canonical_transition_id": canonical,
            "same_class_scoreable_substitution": scoreable_fallback,
            "selector_raw_class_over_context": selector_raw_over_context,
            "shuffled_transition_id": shuffled_transition_id,
            "shuffled_class_id": self.selector.class_by_transition[
                shuffled_transition_id
            ],
            "program_transition_id": program_transition_id,
            "transition_class_differs": self.selector.class_by_transition[
                shuffled_transition_id
            ]
            != class_id,
            "selector_score": float(selected_class["class_score"]),
            "selector_margin": class_scores[0] - class_scores[1],
            "base_prompt_tokens": bare_tokens,
            "correct_raw_prompt_tokens_diagnostic": correct_raw_tokens,
            "program_raw_prompt_tokens_diagnostic": program_raw_tokens,
            "correct_feature_sha256": hashlib.sha256(
                json.dumps(correct_feature, separators=(",", ":")).encode()
            ).hexdigest(),
            "program_feature_sha256": hashlib.sha256(
                json.dumps(program_feature, separators=(",", ":")).encode()
            ).hexdigest(),
            "program_layer_norms": layer_norms,
            "program_global_norm": float(delta.norm().cpu()),
            "compiled_checkpoint_sha256": self.checkpoint_sha256,
            "_delta": delta,
        }


def _task_output(root: Path, condition: str, task_id: str) -> Path:
    return root / CONDITIONS[condition] / "task_results" / f"{task_id}.json"


def _run_task(
    *,
    task_id: str,
    condition: str,
    settings: Mapping[str, Any],
    backend: Any,
    runtime: UngatedStructuredRuntime,
    artifact_dir: Path,
    config_sha256: str,
    attempt_id: str,
    shuffle_manifest_sha256: str,
) -> dict[str, Any]:
    output = _task_output(artifact_dir, condition, task_id)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == RESULT_FORMAT,
            "task": str(row.get("task_id")) == task_id,
            "condition": str(row.get("condition")) == condition,
            "config": str(row.get("config_sha256")) == config_sha256,
            "checkpoint": str(row.get("compiler_checkpoint_sha256"))
            == runtime.checkpoint_sha256,
            "shuffle": str(row.get("transition_shuffle_manifest_sha256"))
            == shuffle_manifest_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing ungated task row differs: {checks}")
        return row
    app = settings["appworld"]
    phase = CONDITIONS[condition]
    restart = len(
        list((artifact_dir / phase / "worker_logs").glob(f"{task_id}.*.stderr.log"))
    )
    experiment_name = f"exp028b_{condition.lower()}_{attempt_id}_{task_id}_r{restart:02d}"
    worker_log = artifact_dir / phase / "worker_logs" / f"{task_id}.{restart:02d}.stderr.log"
    started = time.perf_counter()
    trajectory: list[dict[str, str]] = []
    steps = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    counts = Counter()
    previous_invalid_code: str | None = None
    terminal_error: dict[str, Any] | None = None
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
        for step_id in range(1, int(app["max_steps"]) + 1):
            messages = build_appworld_messages(
                task_message=task_message,
                trajectory_so_far=trajectory,
                prompt_profile=str(app["prompt_profile"]),
                max_context_turns=int(app["max_context_turns"]),
            )
            try:
                decision = runtime.decide_ungated(
                    condition=condition,
                    messages=messages,
                    task_message=task_message,
                    trajectory=trajectory,
                    step_id=step_id,
                    prompt_profile=str(app["prompt_profile"]),
                )
            except ValueError as error:
                if not (
                    str(error).startswith("Span source has ")
                    and "exceeding context 40960; no truncation is allowed" in str(error)
                ):
                    raise
                terminal_error = {
                    "category": "locked_context_overflow_no_truncation",
                    "step_id": step_id,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                    "scientific_outcome": "task_failure",
                    "prompt_or_selector_contract_changed": False,
                }
                counts["locked_context_overflow"] += 1
                break
            delta = decision.pop("_delta")
            prompt_messages = decision.pop("prompt_messages")
            tokenized = backend.tokenize_messages(
                prompt_messages, add_generation_prompt=True
            )
            prompt_tokens = int(tokenized.attention_mask.sum().item())
            remaining = int(app["context_limit"]) - prompt_tokens
            if remaining <= 0:
                raise RuntimeError("Ungated compiled bare prompt is over context")
            generated, hook = _generate_compiled(
                backend=backend,
                messages=prompt_messages,
                delta=delta,
                max_new_tokens=min(int(app["max_new_tokens"]), remaining),
            )
            code, fixed = extract_code_and_fix_content(generated.text)
            executed = bridge.execute(
                nonce=str(ready["ready_nonce"]), step_id=step_id, code=code
            )
            observation = str(executed["raw_observation"])
            trajectory.append({"response": fixed, "observation": observation})
            for key in usage:
                usage[key] += int(generated.usage.get(key, 0))
            exception = executed["execution_exception"] is not None
            counts["execution_exception"] += int(exception)
            counts["gate_would_activate"] += int(decision["gate_would_activate"])
            counts["selector_raw_class_over_context"] += int(
                decision["selector_raw_class_over_context"]
            )
            completion_call = "apis.supervisor.complete_task" in code
            counts["completion_action"] += int(completion_call)
            counts["premature_completion"] += int(
                completion_call and not bool(executed["task_completed"])
            )
            repeated_invalid = exception and previous_invalid_code == code
            counts["repeated_invalid_code"] += int(repeated_invalid)
            previous_invalid_code = code if exception else None
            steps.append(
                {
                    "step_id": step_id,
                    "condition": condition,
                    "compiler": decision,
                    "hook": hook,
                    "usage": generated.usage,
                    "raw_model_response": generated.text,
                    "fixed_model_response": fixed,
                    "extracted_code": code,
                    "execution": executed,
                    "repeated_invalid_code": repeated_invalid,
                }
            )
            if bool(executed["task_completed"]):
                break
        final = bridge.finish(nonce=str(ready["ready_nonce"]))
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        "condition": condition,
        "task_id": task_id,
        "experiment_name": experiment_name,
        "global_seed": GLOBAL_SEED,
        "config_sha256": config_sha256,
        "selector_sha256": str(settings["expected_selector_sha256"]),
        "gate_checkpoint_sha256": sha256_file(
            Path(str(settings["parent_exp028a"])) / "gate/memory_use_gate.pt"
        ),
        "compiler_checkpoint_sha256": runtime.checkpoint_sha256,
        "compiler_selection_sha256": runtime.selection_sha256,
        "transition_shuffle_manifest_sha256": shuffle_manifest_sha256,
        "student_prompt_contains_raw_transition": False,
        "steps": steps,
        "step_count": len(steps),
        "usage": usage,
        "counts": dict(counts),
        "terminal_error": terminal_error,
        "success": bool(final["success"]),
        "success_source": "evaluation.success",
        "evaluation": final["evaluation"],
        "wall_seconds": time.perf_counter() - started,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, row)
    return row


def _summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    success = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    steps = sum(int(row["step_count"]) for row in rows)
    counts = Counter()
    for row in rows:
        counts.update(row["counts"])
    return {
        "format": SUMMARY_FORMAT,
        "condition": condition,
        "task_count": len(rows),
        "success_count": len(success),
        "success_ids": success,
        "total_steps": steps,
        "counts": dict(counts),
        "total_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "total_generated_tokens": sum(
            int(row["usage"]["completion_tokens"]) for row in rows
        ),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "single_seed_descriptive_not_statistical": True,
        "student_prompt_contains_raw_transition": False,
        "passed_infrastructure": len(rows) == 37,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = {**cfg.raw["stage_c_7hr"], **cfg.raw["stage_c_7h2"]}
    settings["appworld"] = dict(cfg.raw["stage_c_7hr"]["appworld"])
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028B requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    runtime_preflight = _json(args.artifact_dir / "preflight/runtime_preflight.json")
    if not bool(runtime_preflight["automatic_launch_allowed"]):
        raise RuntimeError("EXP-028B runtime preflight did not authorize generation")
    task_ids = list(cfg.raw["stage_c_7f"]["first37"]["task_ids"])
    if args.task_limit is not None:
        task_ids = task_ids[: args.task_limit]
    config_sha256 = sha256_file(args.config)
    hashes = {
        "config": config_sha256,
        "parent_gate": sha256_file(
            args.parent_artifact_dir / "gate/memory_use_gate.pt"
        ),
        "parent_compiler_selection": sha256_file(
            args.parent_artifact_dir
            / "structured_compiler/checkpoint_selection.json"
        ),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"{args.condition.lower()}_ungated_structured_first37",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="gate_distribution_audit_complete",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        backend = _build_backend(cfg)
        if any(parameter.requires_grad for parameter in backend.model.parameters()):
            raise RuntimeError("Ungated first37 loaded trainable Qwen")
        runtime = UngatedStructuredRuntime(
            cfg=cfg,
            settings=settings,
            backend=backend,
            artifact_dir=args.parent_artifact_dir,
        )
        shuffle_manifest = {
            "format": "ungated_live_transition_shuffle_manifest_7h2_v1",
            "global_seed": GLOBAL_SEED,
            "mapping": runtime.transition_shuffle,
            "mapping_count": len(runtime.transition_shuffle),
            "different_transition_count": sum(
                source != target
                for source, target in runtime.transition_shuffle.items()
            ),
            "different_signature_class_count": sum(
                runtime.selector.class_by_transition[source]
                != runtime.selector.class_by_transition[target]
                for source, target in runtime.transition_shuffle.items()
            ),
            "outcomes_used": False,
            "frozen_before_generation": True,
        }
        shuffle_path = args.artifact_dir / "preflight/transition_shuffle_manifest.json"
        if shuffle_path.exists():
            existing = _json(shuffle_path)
            if existing != shuffle_manifest:
                raise ValueError("Frozen U2 transition mapping differs on resume")
        else:
            atomic_write_json(shuffle_path, shuffle_manifest)
        shuffle_sha256 = sha256_file(shuffle_path)
        rows = []
        for task_id in task_ids:
            row = _run_task(
                task_id=str(task_id),
                condition=args.condition,
                settings=settings,
                backend=backend,
                runtime=runtime,
                artifact_dir=args.artifact_dir,
                config_sha256=config_sha256,
                attempt_id=args.attempt_id,
                shuffle_manifest_sha256=shuffle_sha256,
            )
            rows.append(row)
            attempt.progress(
                status=f"{args.condition.lower()}_first37",
                completed_tasks=len(rows),
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(
                    _task_output(args.artifact_dir, args.condition, str(task_id))
                ),
            )
            print(
                f"{args.condition} task={task_id} success={row['success']} steps={row['step_count']}",
                flush=True,
            )
        summary = _summary(rows, args.condition)
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "config_sha256": config_sha256,
                "gate_checkpoint_sha256": hashes["parent_gate"],
                "compiler_checkpoint_sha256": runtime.checkpoint_sha256,
                "selector_sha256": str(settings["expected_selector_sha256"]),
                "transition_shuffle_manifest_sha256": shuffle_sha256,
            }
        )
        root = args.artifact_dir / CONDITIONS[args.condition]
        atomic_write_json(root / "summary.json", summary)
        atomic_write_text(
            root / "report.md",
            "\n".join(
                [
                    f"# EXP-028B {args.condition} ungated compiled first37",
                    "",
                    f"- success: `{summary['success_count']}/37`",
                    f"- total steps: `{summary['total_steps']}`",
                    f"- execution exceptions: `{summary['counts'].get('execution_exception', 0)}`",
                    f"- forced compiler multiplier: `1.0`",
                    "- task-level differences are single-seed diagnostics, not statistical claims",
                    "",
                ]
            ),
        )
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
