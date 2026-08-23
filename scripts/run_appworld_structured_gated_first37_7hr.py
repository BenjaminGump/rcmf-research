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
from rcmf.training.appworld_structured_rescue_7hr import (
    FeatureSchema,
    GLOBAL_SEED,
    LABELS,
    MemoryUseGate,
    build_feature_vector,
)
from rcmf.training.procedural_supervision_6f import (
    _stage_compatibility,
    state_stage_signature,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.prepare_appworld_structured_rescue_7hr import _memory_flags
from scripts.run_action_intent_probe_6d import ActionIntentProbe
from scripts.run_raw_memory_first37_7f import (
    FullAgentBridge,
    FrozenDeploymentSelector,
    PROTOCOL_VERSION,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


RESULT_FORMAT = "appworld_structured_gated_raw_task_result_7hr_v1"
SUMMARY_FORMAT = "appworld_structured_gated_raw_first37_summary_7hr_v1"
PHASE_DIRECTORY = "gated_raw_first37"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_structured_rescue_7hr.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="gate_passed")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_gated_raw")
    return parser.parse_args()


def _live_state_text(
    task_message: str, trajectory: Sequence[Mapping[str, str]]
) -> str:
    lines = ["[QUERY]", task_message.strip(), "", "[TRACE SO FAR]"]
    for index, step in enumerate(trajectory, start=1):
        lines.extend(
            [
                f"Step {index} - Response:",
                str(step["response"]).strip(),
                f"Step {index} - Observation:",
                str(step["observation"]).strip(),
            ]
        )
    return "\n".join(lines).strip() + "\n"


class StructuredRuntime:
    def __init__(
        self,
        *,
        cfg: Any,
        settings: Mapping[str, Any],
        backend: Any,
        artifact_dir: Path,
    ) -> None:
        parent_c = Path(str(settings["parent_exp025c"]))
        parent_b = Path(str(settings["parent_exp025b"]))
        selector_settings = dict(cfg.raw["stage_c_7f"])
        selector_settings.update(
            {
                "parent_exp025b": str(parent_b),
                "parent_exp025c": str(parent_c),
                "expected_selector_sha256": str(settings["expected_selector_sha256"]),
            }
        )
        self.selector = FrozenDeploymentSelector(
            settings=selector_settings, backend=backend
        )
        self.backend = backend
        self.settings = settings
        schema = _json(artifact_dir / "preflight/structured_feature_schema.json")
        self.schema = FeatureSchema(
            app_vocabulary=tuple(schema["app_vocabulary"]),
            api_vocabulary=tuple(schema["api_vocabulary"]),
            action_vocabulary=tuple(schema["action_vocabulary"]),
            control_vocabulary=tuple(schema["control_vocabulary"]),
            version=str(schema["version"]),
        )
        checkpoint_path = parent_c / "clean_intent_probe/action_intent_probe.pt"
        intent_summary = _json(parent_c / "clean_intent_probe/clean_intent_summary.json")
        checkpoint = torch.load(
            checkpoint_path, map_location=backend.device, weights_only=False
        )
        self.intent_vocabularies = {
            key: list(value) for key, value in checkpoint["vocabularies"].items()
        }
        self.intent_temperatures = {
            key: float(value)
            for key, value in intent_summary["calibration"]["temperatures"].items()
        }
        self.intent_mean = checkpoint["normalization"]["mean"].to(backend.device)
        self.intent_std = checkpoint["normalization"]["std"].to(backend.device)
        self.intent = ActionIntentProbe(
            int(self.intent_mean.numel()),
            256,
            {key: len(value) for key, value in self.intent_vocabularies.items()},
        ).to(backend.device)
        self.intent.load_state_dict(checkpoint["model_state_dict"])
        self.intent.eval()
        for parameter in self.intent.parameters():
            parameter.requires_grad_(False)
        gate_path = artifact_dir / "gate/memory_use_gate.pt"
        gate = torch.load(gate_path, map_location=backend.device, weights_only=False)
        if str(gate["feature_schema_sha256"]) != sha256_file(
            artifact_dir / "preflight/structured_feature_schema.json"
        ):
            raise ValueError("Gate and structured feature schemas differ")
        self.gate = MemoryUseGate(
            len(gate["feature_names"]), int(settings["gate"]["hidden_dim"])
        ).to(backend.device)
        self.gate.load_state_dict(gate["model_state_dict"])
        self.gate.eval()
        for parameter in self.gate.parameters():
            parameter.requires_grad_(False)
        self.gate_mean = gate["standardizer_mean"].to(backend.device)
        self.gate_std = gate["standardizer_std"].to(backend.device)
        self.gate_temperature = float(gate["temperature"])
        self.threshold = float(gate["activation_threshold"])
        self.maximum_harmful = float(gate["maximum_harmful_probability"])
        self.label_position = {name: index for index, name in enumerate(gate["labels"])}
        signature_path = (
            parent_b / "clean_procedural_audit/clean_transition_signature_manifest.jsonl"
        )
        from rcmf.utils.serialization import read_jsonl

        self.signatures = {
            str(row["transition_id"]): dict(row) for row in read_jsonl(signature_path)
        }

    @torch.no_grad()
    def _intent_distributions(self, state: torch.Tensor) -> dict[str, dict[str, float]]:
        normalized = (state.flatten(1) - self.intent_mean) / self.intent_std
        logits = self.intent(normalized)
        output = {}
        for name, values in logits.items():
            probabilities = F.softmax(
                values[0] / self.intent_temperatures[name], dim=-1
            )
            output[name] = {
                value: float(probabilities[index].cpu())
                for index, value in enumerate(self.intent_vocabularies[name])
            }
        return output

    @torch.no_grad()
    def decide(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        task_message: str,
        trajectory: Sequence[Mapping[str, str]],
        step_id: int,
        prompt_profile: str,
    ) -> dict[str, Any]:
        state = self.selector._state_values(messages)
        scores = self.selector.scores_from_state(state)
        bare = self.backend.tokenize_messages(messages, add_generation_prompt=True)
        bare_tokens = int(bare.attention_mask.sum().item())
        try:
            selection = self.selector.select_from_scores(
                messages, scores, prompt_profile=prompt_profile
            )
        except RuntimeError as error:
            if not str(error).startswith(
                "selected_signature_class_has_no_context_feasible_raw_member"
            ):
                raise
            return {
                "gate_on": False,
                "gate_status": "selected_class_over_context",
                "prompt_messages": list(messages),
                "base_prompt_tokens": bare_tokens,
                "raw_prompt_tokens": None,
                "feature_values": None,
                "probabilities": None,
            }
        transition_id = str(selection["transition_id"])
        transition = self.selector.transitions[transition_id]
        class_id = str(selection["selected_class_id"])
        signature = self.signatures[transition_id]
        action = signature["action_signature"]
        current_stage = state_stage_signature(
            _live_state_text(task_message, trajectory)
        )
        stage = _stage_compatibility(
            current_stage, signature["pre_action_stage_signature"]
        )
        class_scores = sorted(
            (float(value) for value in selection["class_scores"].values()),
            reverse=True,
        )
        source = {
            "state_step_index": int(step_id),
            "history_turn_count": len(trajectory),
            "prompt_tokens": bare_tokens,
            "context_headroom": int(self.settings["appworld"]["context_limit"])
            - bare_tokens,
            "context_limit": int(self.settings["appworld"]["context_limit"]),
            "intent_distributions": self._intent_distributions(state),
            "selector_class_scores": class_scores,
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
            "projected_prompt_overhead": int(selection["prompt_tokens"])
            - bare_tokens,
            "stage_compatibility": stage,
        }
        values, names = build_feature_vector(self.schema, source)
        if names != list(self.schema.names):
            raise RuntimeError("Live structured feature order differs")
        feature = torch.tensor([values], dtype=torch.float32, device=self.backend.device)
        logits = self.gate((feature - self.gate_mean) / self.gate_std)
        probability = F.softmax(logits / self.gate_temperature, dim=-1)[0]
        probabilities = {
            label: float(probability[index].cpu())
            for label, index in self.label_position.items()
        }
        gate_on = (
            probabilities["POSITIVE"] >= self.threshold
            and probabilities["HARMFUL"] <= self.maximum_harmful
        )
        return {
            "gate_on": bool(gate_on),
            "gate_status": "on" if gate_on else "off",
            "prompt_messages": selection["messages"] if gate_on else list(messages),
            "base_prompt_tokens": bare_tokens,
            "raw_prompt_tokens": int(selection["prompt_tokens"]),
            "feature_values": values,
            "feature_sha256": hashlib.sha256(
                json.dumps(values, separators=(",", ":")).encode()
            ).hexdigest(),
            "probabilities": probabilities,
            "selected_transition_id": transition_id,
            "selected_class_id": class_id,
            "selector_score": float(selection["class_score"]),
            "selector_margin": class_scores[0] - class_scores[1],
            "same_class_substitution": bool(selection["same_class_substitution"]),
        }


def _task_output(root: Path, task_id: str) -> Path:
    return root / PHASE_DIRECTORY / "task_results" / f"{task_id}.json"


def _run_task(
    *,
    task_id: str,
    settings: Mapping[str, Any],
    backend: Any,
    runtime: StructuredRuntime,
    artifact_dir: Path,
    config_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    output = _task_output(artifact_dir, task_id)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == RESULT_FORMAT,
            "task": str(row.get("task_id")) == task_id,
            "config": str(row.get("config_sha256")) == config_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing gated-raw task row differs: {checks}")
        return row
    app = settings["appworld"]
    restart = len(
        list(
            (artifact_dir / PHASE_DIRECTORY / "worker_logs").glob(
                f"{task_id}.*.stderr.log"
            )
        )
    )
    experiment_name = f"exp028a_gated_raw_{attempt_id}_{task_id}_r{restart:02d}"
    worker_log = (
        artifact_dir
        / PHASE_DIRECTORY
        / "worker_logs"
        / f"{task_id}.{restart:02d}.stderr.log"
    )
    started = time.perf_counter()
    trajectory: list[dict[str, str]] = []
    steps = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    counts = Counter()
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
            decision = runtime.decide(
                messages=messages,
                task_message=task_message,
                trajectory=trajectory,
                step_id=step_id,
                prompt_profile=str(app["prompt_profile"]),
            )
            generated = backend.generate(
                messages=decision.pop("prompt_messages"),
                max_new_tokens=int(app["max_new_tokens"]),
                temperature=float(app["temperature"]),
                top_p=float(app["top_p"]),
            )
            code, fixed = extract_code_and_fix_content(generated.text)
            executed = bridge.execute(
                nonce=str(ready["ready_nonce"]), step_id=step_id, code=code
            )
            observation = str(executed["raw_observation"])
            trajectory.append({"response": fixed, "observation": observation})
            for key in usage:
                usage[key] += int(generated.usage.get(key, 0))
            counts["gate_on"] += int(decision["gate_on"])
            counts["gate_off"] += int(not decision["gate_on"])
            counts["over_context_off"] += int(
                decision["gate_status"] == "selected_class_over_context"
            )
            counts["execution_exception"] += int(
                executed["execution_exception"] is not None
            )
            steps.append(
                {
                    "step_id": step_id,
                    "gate": decision,
                    "usage": generated.usage,
                    "raw_model_response": generated.text,
                    "fixed_model_response": fixed,
                    "extracted_code": code,
                    "execution": executed,
                }
            )
            if bool(executed["task_completed"]):
                break
        final = bridge.finish(nonce=str(ready["ready_nonce"]))
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        "task_id": task_id,
        "experiment_name": experiment_name,
        "global_seed": GLOBAL_SEED,
        "config_sha256": config_sha256,
        "selector_sha256": str(settings["expected_selector_sha256"]),
        "gate_checkpoint_sha256": sha256_file(
            artifact_dir / "gate/memory_use_gate.pt"
        ),
        "steps": steps,
        "step_count": len(steps),
        "usage": usage,
        "counts": dict(counts),
        "success": bool(final["success"]),
        "success_source": "evaluation.success",
        "evaluation": final["evaluation"],
        "wall_seconds": time.perf_counter() - started,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, row)
    return row


def _summary(
    rows: Sequence[Mapping[str, Any]],
    matched_bare: Mapping[str, Any],
    always_raw: Mapping[str, Any],
) -> dict[str, Any]:
    success = {str(row["task_id"]) for row in rows if bool(row["success"])}
    bare = set(map(str, matched_bare["success_ids"]))
    raw = set(map(str, always_raw["success_ids"]))
    gate_on = sum(int(row["counts"].get("gate_on", 0)) for row in rows)
    steps = sum(int(row["step_count"]) for row in rows)
    return {
        "format": SUMMARY_FORMAT,
        "task_count": len(rows),
        "success_count": len(success),
        "success_ids": sorted(success),
        "matched_bare_success_count": len(bare),
        "always_on_raw_success_count": len(raw),
        "retained_vs_bare": sorted(success & bare),
        "gained_vs_bare": sorted(success - bare),
        "lost_vs_bare": sorted(bare - success),
        "retained_vs_always_raw": sorted(success & raw),
        "gained_vs_always_raw": sorted(success - raw),
        "lost_vs_always_raw": sorted(raw - success),
        "single_seed_descriptive_not_causal": True,
        "gate_on_count": gate_on,
        "gate_off_count": steps - gate_on,
        "activation_rate": gate_on / max(1, steps),
        "total_steps": steps,
        "total_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "total_generated_tokens": sum(
            int(row["usage"]["completion_tokens"]) for row in rows
        ),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "passed_infrastructure": len(rows) == 37,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7hr"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    gate_report = _json(args.artifact_dir / "gate/gate_report.json")
    if not bool(gate_report["passed"]):
        raise RuntimeError("Train-side causal gate did not pass")
    matched_bare = _json(Path(str(settings["first37"]["matched_bare_summary"])))
    always_raw = _json(Path(str(settings["first37"]["always_on_raw_summary"])))
    if int(matched_bare["success_count"]) != 8 or int(always_raw["success_count"]) != 5:
        raise ValueError("Locked first37 references differ")
    task_ids = list(cfg.raw["stage_c_7f"]["first37"]["task_ids"])
    config_sha256 = sha256_file(args.config)
    hashes = {
        "gate": sha256_file(args.artifact_dir / "gate/memory_use_gate.pt"),
        "selector": str(settings["expected_selector_sha256"]),
        "matched_bare": sha256_file(Path(str(settings["first37"]["matched_bare_summary"]))),
        "always_raw": sha256_file(Path(str(settings["first37"]["always_on_raw_summary"]))),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="gated_raw_first37_diagnostic",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        backend = _build_backend(cfg)
        if any(parameter.requires_grad for parameter in backend.model.parameters()):
            raise RuntimeError("Gated raw diagnostic loaded trainable Qwen")
        runtime = StructuredRuntime(
            cfg=cfg, settings=settings, backend=backend, artifact_dir=args.artifact_dir
        )
        rows = []
        for task_id in task_ids:
            row = _run_task(
                task_id=str(task_id),
                settings=settings,
                backend=backend,
                runtime=runtime,
                artifact_dir=args.artifact_dir,
                config_sha256=config_sha256,
                attempt_id=args.attempt_id,
            )
            rows.append(row)
            attempt.progress(
                status="gated_raw_first37",
                completed_tasks=len(rows),
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(_task_output(args.artifact_dir, str(task_id))),
            )
            print(
                f"gated raw task={task_id} success={row['success']} steps={row['step_count']}",
                flush=True,
            )
        summary = _summary(rows, matched_bare, always_raw)
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "config_sha256": config_sha256,
                "gate_checkpoint_sha256": hashes["gate"],
                "selector_sha256": hashes["selector"],
            }
        )
        root = args.artifact_dir / PHASE_DIRECTORY
        atomic_write_json(root / "summary.json", summary)
        atomic_write_text(
            root / "report.md",
            "\n".join(
                [
                    "# EXP-028A gated raw first37 diagnostic",
                    "",
                    f"- gated raw: `{summary['success_count']}/37`",
                    f"- matched bare: `{summary['matched_bare_success_count']}/37`",
                    f"- always-on raw: `{summary['always_on_raw_success_count']}/37`",
                    f"- gate activation: `{summary['gate_on_count']}/{summary['total_steps']}`",
                    f"- activation rate: `{summary['activation_rate']:.6f}`",
                    "- gained/lost IDs are single-seed descriptive diagnostics, not causal evidence",
                    "",
                ]
            ),
        )
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
