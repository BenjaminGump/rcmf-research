from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.config import load_config
from rcmf.injection.base import build_position_ids
from rcmf.training.deep_residual_amortization_7f import differentiable_layer_ratio_projection
from rcmf.training.deep_residual_carrier_7e import capture_original_layer_states
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_appworld_structured_compiler_7hr import _paths as _compiler_paths
from scripts.run_appworld_structured_compiler_validation_7hr import _load_models
from scripts.run_appworld_structured_gated_first37_7hr import StructuredRuntime, _json
from scripts.run_deep_residual_carrier_7e import _generate_residual
from scripts.run_deep_residual_amortized_one_step_7f import (
    LIVE_PROJECTION_MAXIMUM_RATIO,
)
from scripts.run_raw_memory_first37_7f import FullAgentBridge, PROTOCOL_VERSION
from scripts.run_state_conditioned_program_direct_7dg import _load_representations
from scripts.run_state_conditioned_program_fast_7df import _build_backend


GLOBAL_SEED = 25101
LAYER_INDICES = (7, 14, 21, 28)
TOKEN_COUNT = 4
RESULT_FORMAT = "appworld_structured_compiled_task_result_7hr_v1"
SUMMARY_FORMAT = "appworld_structured_compiled_first37_summary_7hr_v1"
PHASE_DIRECTORY = "gated_compiled_first37"


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
    parser.add_argument("--resume-checkpoint", default="structured_one_step_positive")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_compiled_first37")
    return parser.parse_args()


class CompiledStructuredRuntime(StructuredRuntime):
    def __init__(
        self,
        *,
        cfg: Any,
        settings: Mapping[str, Any],
        backend: Any,
        artifact_dir: Path,
    ) -> None:
        super().__init__(
            cfg=cfg, settings=settings, backend=backend, artifact_dir=artifact_dir
        )
        paths = _compiler_paths(settings, artifact_dir)
        selection_path = paths["root"] / "checkpoint_selection.json"
        selection = _json(selection_path)
        if not bool(selection["passed"]):
            raise RuntimeError("No eligible structured compiler checkpoint")
        self.selected_updates = int(selection["selected"]["updates_per_pair"])
        self.parent, self.composer, self.decoder, self.representations, gate = _load_models(
            cfg=cfg,
            settings=settings,
            paths=paths,
            updates=self.selected_updates,
            device=backend.device,
        )
        if sha256_file(paths["gate"]) != sha256_file(
            artifact_dir / "gate/memory_use_gate.pt"
        ):
            raise ValueError("Compiled runtime gate differs from gated-raw runtime")
        self.gate_feature_mean = gate["standardizer_mean"].to(backend.device)
        self.gate_feature_std = gate["standardizer_std"].to(backend.device)
        self.transition_position = self.representations["transition_position"]
        self.checkpoint_path = Path(str(selection["selected"]["checkpoint"]))
        self.checkpoint_sha256 = sha256_file(self.checkpoint_path)
        self.selection_sha256 = sha256_file(selection_path)

    @torch.no_grad()
    def decide_compiled(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        task_message: str,
        trajectory: Sequence[Mapping[str, str]],
        step_id: int,
        prompt_profile: str,
    ) -> dict[str, Any]:
        decision = super().decide(
            messages=messages,
            task_message=task_message,
            trajectory=trajectory,
            step_id=step_id,
            prompt_profile=prompt_profile,
            include_internal=True,
        )
        state = decision.pop("_state_tensor")
        decision["prompt_messages"] = list(messages)
        if not bool(decision["gate_on"]):
            delta = torch.zeros(
                len(LAYER_INDICES),
                TOKEN_COUNT,
                int(self.representations["transition_values"].shape[-1]),
                device=self.backend.device,
            )
        else:
            transition_id = str(decision["selected_transition_id"])
            transition = self.representations["transition_values"][
                self.transition_position[transition_id]
            ].unsqueeze(0).to(self.backend.device)
            feature = torch.tensor(
                [decision["feature_values"]],
                dtype=torch.float32,
                device=self.backend.device,
            )
            normalized = (feature - self.gate_feature_mean) / self.gate_feature_std
            gate_probability = torch.tensor(
                [float(decision["probabilities"]["POSITIVE"])],
                dtype=torch.float32,
                device=self.backend.device,
            )
            latent = self.composer(
                normalized, self.parent(state, transition), gate_probability
            )
            delta = self.decoder(latent)[0]
        decision["_delta"] = delta
        decision["compiled_checkpoint_sha256"] = self.checkpoint_sha256
        decision["student_prompt_contains_raw_transition"] = False
        return decision


def _generate_compiled(
    *, backend: Any, messages: Sequence[Mapping[str, str]], delta: torch.Tensor, max_new_tokens: int
) -> tuple[Any, dict[str, Any]]:
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    user_indices = [int(value) for value in tokenized.metadata["last_user_token_indices"]]
    if len(user_indices) < TOKEN_COUNT:
        raise RuntimeError("Compiled first37 prompt has fewer than four user tokens")
    selected = torch.tensor(
        [user_indices[-TOKEN_COUNT:]], device=backend.device, dtype=torch.long
    )
    original = capture_original_layer_states(
        model=backend.model,
        input_ids=tokenized.input_ids,
        attention_mask=tokenized.attention_mask.to(torch.long),
        selected_token_indices=selected,
        layer_indices=list(LAYER_INDICES),
        position_ids=build_position_ids(tokenized.attention_mask.to(torch.long)),
    ).to(backend.device)
    projected, projection = differentiable_layer_ratio_projection(
        delta.unsqueeze(0),
        original,
        maximum_ratio=LIVE_PROJECTION_MAXIMUM_RATIO,
    )
    output, hook = _generate_residual(
        backend=backend,
        messages=messages,
        delta=projected[0],
        layer_indices=list(LAYER_INDICES),
        max_new_tokens=max_new_tokens,
    )
    if max(float(value) for value in hook["layer_ratios"]) > 1.0001:
        raise RuntimeError("Compiled first37 residual exceeded the locked ratio")
    return output, {
        "selected_token_indices": hook["selected_token_indices"][0],
        "layer_ratios": hook["layer_ratios"],
        "global_ratio": hook["global_ratio"],
        "raw_layer_ratio": projection["raw_layer_ratio"][0].cpu().tolist(),
        "runtime_projection_maximum_ratio": LIVE_PROJECTION_MAXIMUM_RATIO,
    }


def _task_output(root: Path, task_id: str) -> Path:
    return root / PHASE_DIRECTORY / "task_results" / f"{task_id}.json"


def _run_task(
    *,
    task_id: str,
    settings: Mapping[str, Any],
    backend: Any,
    runtime: CompiledStructuredRuntime,
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
            "checkpoint": str(row.get("compiler_checkpoint_sha256"))
            == runtime.checkpoint_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing compiled task row differs: {checks}")
        return row
    app = settings["appworld"]
    restart = len(
        list(
            (artifact_dir / PHASE_DIRECTORY / "worker_logs").glob(
                f"{task_id}.*.stderr.log"
            )
        )
    )
    experiment_name = f"exp028a_compiled_{attempt_id}_{task_id}_r{restart:02d}"
    worker_log = artifact_dir / PHASE_DIRECTORY / "worker_logs" / f"{task_id}.{restart:02d}.stderr.log"
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
                "max_api_calls_per_interaction": int(app["max_api_calls_per_interaction"]),
            }
        )
        task_message = build_task_message(
            str(ready["instruction"]), dict(ready["supervisor"]), profile=str(app["prompt_profile"])
        )
        for step_id in range(1, int(app["max_steps"]) + 1):
            messages = build_appworld_messages(
                task_message=task_message,
                trajectory_so_far=trajectory,
                prompt_profile=str(app["prompt_profile"]),
                max_context_turns=int(app["max_context_turns"]),
            )
            decision = runtime.decide_compiled(
                messages=messages,
                task_message=task_message,
                trajectory=trajectory,
                step_id=step_id,
                prompt_profile=str(app["prompt_profile"]),
            )
            delta = decision.pop("_delta")
            prompt_messages = decision.pop("prompt_messages")
            tokenized = backend.tokenize_messages(prompt_messages, add_generation_prompt=True)
            prompt_tokens = int(tokenized.attention_mask.sum().item())
            remaining = int(settings["appworld"]["context_limit"]) - prompt_tokens
            if remaining <= 0:
                raise RuntimeError("Compiled first37 bare prompt is over context")
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
                    "hook": hook,
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
        "gate_checkpoint_sha256": sha256_file(artifact_dir / "gate/memory_use_gate.pt"),
        "compiler_checkpoint_sha256": runtime.checkpoint_sha256,
        "compiler_selection_sha256": runtime.selection_sha256,
        "student_prompt_contains_raw_transition": False,
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
    gated_raw: Mapping[str, Any],
) -> dict[str, Any]:
    success = {str(row["task_id"]) for row in rows if bool(row["success"])}
    bare = set(map(str, matched_bare["success_ids"]))
    raw = set(map(str, gated_raw["success_ids"]))
    gate_on = sum(int(row["counts"].get("gate_on", 0)) for row in rows)
    steps = sum(int(row["step_count"]) for row in rows)
    count = len(success)
    if count >= 9:
        interpretation = "PRELIMINARY_POSITIVE"
        branch = "appworld_structured_compiled_memory_preliminary_positive"
    elif count >= 7:
        interpretation = "COMPETITIVE"
        branch = "appworld_structured_compiler_competitive"
    else:
        interpretation = "CLEARLY_WEAK"
        branch = "structured_compiler_not_end_to_end_retained"
    return {
        "format": SUMMARY_FORMAT,
        "task_count": len(rows),
        "success_count": count,
        "success_ids": sorted(success),
        "matched_bare_success_count": len(bare),
        "gated_raw_success_count": len(raw),
        "retained_vs_bare": sorted(success & bare),
        "gained_vs_bare": sorted(success - bare),
        "lost_vs_bare": sorted(bare - success),
        "retained_vs_gated_raw": sorted(success & raw),
        "gained_vs_gated_raw": sorted(success - raw),
        "lost_vs_gated_raw": sorted(raw - success),
        "single_seed_descriptive_not_statistical": True,
        "gate_on_count": gate_on,
        "gate_off_count": steps - gate_on,
        "activation_rate": gate_on / max(1, steps),
        "total_steps": steps,
        "total_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "total_generated_tokens": sum(int(row["usage"]["completion_tokens"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "interpretation": interpretation,
        "decision_branch": branch,
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
    analysis = _json(args.artifact_dir / "locked_one_step/analysis.json")
    if analysis["classification"]["classification"] not in {
        "STRONG_POSITIVE",
        "PARTIAL_POSITIVE",
    }:
        raise RuntimeError("Structured compiler one-step result did not authorize first37")
    matched_bare = _json(Path(str(settings["first37"]["matched_bare_summary"])))
    gated_raw_path = args.artifact_dir / "gated_raw_first37/summary.json"
    gated_raw = _json(gated_raw_path)
    if int(matched_bare["success_count"]) != 8:
        raise ValueError("Locked matched-bare first37 differs")
    task_ids = list(cfg.raw["stage_c_7f"]["first37"]["task_ids"])
    config_sha256 = sha256_file(args.config)
    hashes = {
        "gate": sha256_file(args.artifact_dir / "gate/memory_use_gate.pt"),
        "selector": str(settings["expected_selector_sha256"]),
        "matched_bare": sha256_file(Path(str(settings["first37"]["matched_bare_summary"]))),
        "gated_raw": sha256_file(gated_raw_path),
        "one_step": sha256_file(args.artifact_dir / "locked_one_step/analysis.json"),
    }
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="gated_compiled_first37",
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
            raise RuntimeError("Compiled first37 loaded trainable Qwen")
        runtime = CompiledStructuredRuntime(
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
                status="gated_compiled_first37",
                completed_tasks=len(rows),
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(_task_output(args.artifact_dir, str(task_id))),
            )
            print(
                f"compiled task={task_id} success={row['success']} steps={row['step_count']}",
                flush=True,
            )
        summary = _summary(rows, matched_bare, gated_raw)
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "config_sha256": config_sha256,
                "gate_checkpoint_sha256": hashes["gate"],
                "selector_sha256": hashes["selector"],
                "compiler_checkpoint_sha256": runtime.checkpoint_sha256,
            }
        )
        root = args.artifact_dir / PHASE_DIRECTORY
        atomic_write_json(root / "summary.json", summary)
        atomic_write_text(
            root / "report.md",
            "\n".join(
                [
                    "# EXP-028A gated compiled first37",
                    "",
                    f"- compiled: `{summary['success_count']}/37`",
                    f"- matched bare: `{summary['matched_bare_success_count']}/37`",
                    f"- gated raw: `{summary['gated_raw_success_count']}/37`",
                    f"- gate activation: `{summary['gate_on_count']}/{summary['total_steps']}`",
                    f"- interpretation: `{summary['interpretation']}`",
                    f"- branch: `{summary['decision_branch']}`",
                    "- task-level differences are single-seed diagnostics, not statistical claims",
                    "",
                ]
            ),
        )
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
