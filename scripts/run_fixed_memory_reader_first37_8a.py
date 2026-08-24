from __future__ import annotations

import argparse
from collections import Counter
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
from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.config import load_config
from rcmf.training.deep_residual_amortization_7f import aggregate_and_select_class
from rcmf.training.fixed_memory_reader_8a import GLOBAL_SEED, FixedMemoryReader
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.ungated_structured_e2e_7h2 import freeze_transition_shuffle
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_deep_residual_compiler_7f import _build_model
from scripts.run_fixed_memory_reader_8a import _generate_reader
from scripts.run_raw_memory_first37_7f import (
    FullAgentBridge,
    FrozenDeploymentSelector,
    PROTOCOL_VERSION,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


RESULT_FORMAT = "fixed_memory_reader_first37_task_result_8a_v1"
SUMMARY_FORMAT = "fixed_memory_reader_first37_summary_8a_v1"
CONDITIONS = ("D1_correct_reader", "D2_transition_shuffle_reader")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_fixed_memory_reader_8a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--condition", choices=CONDITIONS, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp029a_first37")
    parser.add_argument("--task-limit", type=int)
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_g = Path(str(settings["parent_exp027b"]))
    root = artifact_dir / "first37"
    return {
        "selection": artifact_dir / "reader/checkpoint_selection.json",
        "parent_training": parent_g / "compiler/pairmlp/training_summary.json",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "matched_bare": parent_g / "phase_a_matched_bare_first37/summary.json",
        "root": root,
        "analysis": root / "analysis.json",
        "report": root / "report.md",
    }


def _task_output(root: Path, condition: str, task_id: str) -> Path:
    return root / condition / "task_results" / f"{task_id}.json"


class FixedReaderRuntime:
    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        paths: Mapping[str, Path],
        backend: Any,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.selector = FrozenDeploymentSelector(settings=settings, backend=backend)
        selection = _json(paths["selection"])
        if str(selection["classification"]) != "STRONG":
            raise RuntimeError("First37 requires a STRONG heldout train classification")
        selected = selection["selected"]
        checkpoint_path = Path(str(selected["checkpoint"]))
        if sha256_file(checkpoint_path) != str(selected["checkpoint_sha256"]):
            raise ValueError("Selected fixed-reader checkpoint hash differs")
        payload = torch.load(
            checkpoint_path, map_location=backend.device, weights_only=False
        )
        transition_payload = torch.load(
            paths["transition_cache"], map_location="cpu", weights_only=False
        )
        self.transition_position = {
            str(value): index
            for index, value in enumerate(transition_payload["ordered_ids"])
        }
        self.transition_values = transition_payload["representations"][
            "final_layer"
        ].to(torch.float32)
        self.model = _build_model(
            kind="pairmlp",
            settings=settings,
            view_names=list(transition_payload["view_names"]),
            device=backend.device,
        )
        self.model.load_state_dict(payload["model_state_dict"])
        self.model.eval()
        self.reader = FixedMemoryReader(
            model_dim=int(settings["compiler"]["representation_dim"]),
            latent_dim=int(settings["reader"]["latent_dim"]),
            bottleneck=int(settings["reader"]["bottleneck_dim"]),
            layer_count=len(settings["reader"]["selected_layer_indices"]),
        ).to(backend.device)
        self.reader.load_state_dict(payload["reader_state_dict"])
        self.reader.eval()
        self.checkpoint_path = checkpoint_path
        self.checkpoint_sha256 = sha256_file(checkpoint_path)
        self.transition_shuffle = freeze_transition_shuffle(
            self.selector.ordered_transition_ids,
            self.selector.class_by_transition,
        )

    @torch.no_grad()
    def decide(
        self,
        messages: Sequence[Mapping[str, str]],
        condition: str,
    ) -> dict[str, Any]:
        state = self.selector._state_values(messages)
        scores = self.selector.scores_from_state(state)
        selected = aggregate_and_select_class(
            scores,
            self.selector.transition_class_ids,
            legal_transition_ids=self.selector.ordered_transition_ids,
            ordered_transition_ids=self.selector.ordered_transition_ids,
        )
        class_id = str(selected["selected_class_id"])
        correct_transition = str(
            self.selector.classes[class_id]["canonical_transition_id"]
        )
        program_transition = (
            correct_transition
            if condition == "D1_correct_reader"
            else self.transition_shuffle[correct_transition]
        )
        transition = self.transition_values[
            self.transition_position[program_transition]
        ].unsqueeze(0).to(self.backend.device)
        latent = self.model(state, transition)
        return {
            "selected_class_id": class_id,
            "selected_transition_id": correct_transition,
            "program_transition_id": program_transition,
            "program_signature_class_id": self.selector.class_by_transition[
                program_transition
            ],
            "transition_signature_differs": self.selector.class_by_transition[
                program_transition
            ]
            != class_id,
            "selector_score": float(selected["class_score"]),
            "latent_norm": float(latent.norm().cpu()),
            "_latent": latent,
        }


def _run_task(
    *,
    task_id: str,
    condition: str,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    backend: Any,
    runtime: FixedReaderRuntime,
    artifact_dir: Path,
    config_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    output = _task_output(paths["root"], condition, task_id)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == RESULT_FORMAT,
            "task": str(row.get("task_id")) == task_id,
            "condition": str(row.get("condition")) == condition,
            "config": str(row.get("config_sha256")) == config_sha256,
            "checkpoint": str(row.get("checkpoint_sha256"))
            == runtime.checkpoint_sha256,
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Existing reader first37 row differs: {checks}")
        return row
    app = settings["appworld"]
    restart = len(
        list(
            (paths["root"] / condition / "worker_logs").glob(
                f"{task_id}.*.stderr.log"
            )
        )
    )
    worker_log = (
        paths["root"]
        / condition
        / "worker_logs"
        / f"{task_id}.{restart:02d}.stderr.log"
    )
    started = time.perf_counter()
    trajectory: list[dict[str, str]] = []
    steps = []
    counts = Counter()
    usage = Counter()
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
                "experiment_name": f"exp029a_{condition}_{attempt_id}_{task_id}_r{restart:02d}",
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
            prompt = backend.tokenize_messages(messages, add_generation_prompt=True)
            prompt_tokens = int(prompt.attention_mask.sum())
            remaining = int(app["context_limit"]) - prompt_tokens
            if remaining <= 0:
                terminal_error = "locked_context_overflow_no_truncation"
                counts["context_overflow"] += 1
                break
            decision = runtime.decide(messages, condition)
            latent = decision.pop("_latent")
            generated, hook = _generate_reader(
                backend=backend,
                messages=messages,
                reader=runtime.reader,
                latent=latent,
                max_new_tokens=min(int(app["max_new_tokens"]), remaining),
                maximum_ratio=float(settings["reader"]["ratio_budget_per_layer"]),
            )
            code, fixed = extract_code_and_fix_content(generated.text)
            executed = bridge.execute(
                nonce=str(ready["ready_nonce"]), step_id=step_id, code=code
            )
            observation = str(executed["raw_observation"])
            trajectory.append({"response": fixed, "observation": observation})
            usage.update(generated.usage)
            counts["execution_exception"] += int(
                executed["execution_exception"] is not None
            )
            counts["completion_action"] += int(
                "apis.supervisor.complete_task" in code
            )
            steps.append(
                {
                    "step_id": step_id,
                    "decision": decision,
                    "reader_hook": hook,
                    "raw_model_response": generated.text,
                    "fixed_model_response": fixed,
                    "extracted_code": code,
                    "raw_observation": observation,
                    "execution_exception": executed["execution_exception"],
                    "task_completed": bool(executed["task_completed"]),
                }
            )
            if bool(executed["task_completed"]):
                break
        final = bridge.finish(nonce=str(ready["ready_nonce"]))
    success = bool(final["evaluation"]["success"])
    row = {
        "format": RESULT_FORMAT,
        "status": "complete",
        "global_seed": GLOBAL_SEED,
        "task_id": task_id,
        "condition": condition,
        "success": success,
        "success_source": "evaluation.success",
        "evaluation": final["evaluation"],
        "step_count": len(steps),
        "steps": steps,
        "counts": dict(sorted(counts.items())),
        "usage": dict(usage),
        "terminal_error": terminal_error,
        "checkpoint_sha256": runtime.checkpoint_sha256,
        "config_sha256": config_sha256,
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_write_json(output, row)
    return row


def _summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "condition": condition,
        "task_count": len(rows),
        "success_count": sum(bool(row["success"]) for row in rows),
        "success_ids": sorted(
            str(row["task_id"]) for row in rows if bool(row["success"])
        ),
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_prompt_tokens": sum(
            int(row["usage"].get("prompt_tokens", 0)) for row in rows
        ),
        "total_generated_tokens": sum(
            int(row["usage"].get("completion_tokens", 0)) for row in rows
        ),
        "total_wall_seconds": sum(float(row["elapsed_seconds"]) for row in rows),
        "execution_exception_count": sum(
            int(row["counts"].get("execution_exception", 0)) for row in rows
        ),
        "context_overflow_count": sum(
            int(row["counts"].get("context_overflow", 0)) for row in rows
        ),
        "single_seed_development_diagnostic": True,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8a"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-029A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = ("selection", "parent_training", "transition_cache", "matched_bare")
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing reader first37 input: {missing}")
    selection = _json(paths["selection"])
    if not bool(selection["run_first37"]):
        raise RuntimeError("Heldout train reader gate did not authorize first37")
    task_ids = [
        str(value)
        for value in cfg.raw["stage_c_7f"]["first37"]["task_ids"]
    ]
    if args.task_limit is not None:
        task_ids = task_ids[: int(args.task_limit)]
    backend = _build_backend(cfg)
    runtime = FixedReaderRuntime(
        settings=settings, paths=paths, backend=backend
    )
    config_sha256 = sha256_file(args.config)
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"first37_{args.condition}",
        command=[str(value) for value in sys.argv],
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
        rows = []
        for ordinal, task_id in enumerate(task_ids, start=1):
            rows.append(
                _run_task(
                    task_id=task_id,
                    condition=args.condition,
                    settings=settings,
                    paths=paths,
                    backend=backend,
                    runtime=runtime,
                    artifact_dir=args.artifact_dir,
                    config_sha256=config_sha256,
                    attempt_id=args.attempt_id,
                )
            )
            attempt.progress(
                status=f"first37_{args.condition}",
                completed_tasks=ordinal,
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(
                    _task_output(paths["root"], args.condition, task_id)
                ),
            )
            print(f"{args.condition} first37 {ordinal}/{len(task_ids)}", flush=True)
        summary = _summary(rows, args.condition)
        atomic_write_json(paths["root"] / args.condition / "summary.json", summary)
        attempt.progress(
            status=f"first37_{args.condition}_complete",
            latest_validated_checkpoint=str(
                paths["root"] / args.condition / "summary.json"
            ),
            result=summary,
        )
    if args.task_limit is None:
        summaries = {}
        for condition in CONDITIONS:
            path = paths["root"] / condition / "summary.json"
            if path.exists():
                summaries[condition] = _json(path)
        if len(summaries) == 2:
            bare = _json(paths["matched_bare"])
            d1 = int(summaries["D1_correct_reader"]["success_count"])
            d2 = int(summaries["D2_transition_shuffle_reader"]["success_count"])
            d0 = int(bare["success_count"])
            live_specific = d1 > d2 and d1 >= d0 - 1
            preliminary = d1 >= 9 and d1 >= d2 + 2
            analysis = {
                "format": "fixed_memory_reader_first37_analysis_8a_v1",
                "global_seed": GLOBAL_SEED,
                "D0_matched_bare": d0,
                "D1_correct_reader": d1,
                "D2_transition_shuffle_reader": d2,
                "D1_minus_D0": d1 - d0,
                "D1_minus_D2": d1 - d2,
                "live_memory_specific_signal": live_specific,
                "preliminary_positive": preliminary,
                "single_seed_development_diagnostic": True,
                "decision_branch": (
                    "fixed_memory_reader_validated"
                    if d1 > d2
                    else "reader_one_step_not_live_specific"
                ),
            }
            analysis["analysis_sha256"] = canonical_sha256(analysis)
            atomic_write_json(paths["analysis"], analysis)
            atomic_write_text(
                paths["report"],
                "\n".join(
                    [
                        "# EXP-029A fixed-reader first37 development audit",
                        "",
                        f"- D0 matched bare: `{d0}/37`",
                        f"- D1 correct reader: `{d1}/37`",
                        f"- D2 transition shuffle: `{d2}/37`",
                        f"- live memory-specific signal: `{str(live_specific).lower()}`",
                        f"- preliminary positive: `{str(preliminary).lower()}`",
                        f"- decision branch: `{analysis['decision_branch']}`",
                        "- single seed; exposed development tasks; not a final-test claim",
                        "",
                    ]
                ),
            )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
