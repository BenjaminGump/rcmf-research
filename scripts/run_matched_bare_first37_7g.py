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
import transformers

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.config import load_config
from rcmf.training.memory_specific_deep_amortization_7g import GLOBAL_SEED
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_raw_memory_first37_7f import FullAgentBridge, PROTOCOL_VERSION
from scripts.run_state_conditioned_program_fast_7df import _build_backend


RESULT_FORMAT = "matched_harness_bare_first37_task_7g_v1"
PHASE_DIRECTORY = "phase_a_matched_bare_first37"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_memory_specific_deep_amortization_7g.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027b_matched_bare")
    parser.add_argument("--task-limit", type=int)
    return parser.parse_args()


def _output_path(root: Path, task_id: str) -> Path:
    return root / PHASE_DIRECTORY / "task_results" / f"{task_id}.json"


def _environment_identity(backend: Any) -> dict[str, Any]:
    tokenizer_kwargs = dict(getattr(backend.tokenizer, "init_kwargs", {}) or {})
    return {
        "torch_version": str(torch.__version__),
        "transformers_version": str(transformers.__version__),
        "model_name": str(backend.model_name),
        "model_config_commit_hash": getattr(backend.model.config, "_commit_hash", None),
        "tokenizer_name_or_path": str(
            getattr(backend.tokenizer, "name_or_path", "")
        ),
        "tokenizer_commit_hash": tokenizer_kwargs.get("_commit_hash"),
        "tokenizer_revision": tokenizer_kwargs.get("revision"),
        "qwen_frozen": not any(
            parameter.requires_grad for parameter in backend.model.parameters()
        ),
    }


def _validate_row(
    row: Mapping[str, Any], *, task_id: str, config_sha256: str
) -> None:
    checks = {
        "format": str(row.get("format")) == RESULT_FORMAT,
        "status": str(row.get("status")) == "complete",
        "task": str(row.get("task_id")) == task_id,
        "config": str(row.get("config_sha256")) == config_sha256,
        "seed": int(row.get("global_seed", -1)) == GLOBAL_SEED,
        "selector_disabled": not bool(row.get("selector_invoked", True)),
        "memory_disabled": not bool(row.get("memory_inserted", True)),
        "success_source": str(row.get("success_source")) == "evaluation.success",
    }
    if not all(checks.values()):
        raise ValueError(f"Matched-bare task row identity differs: {task_id}: {checks}")


def _run_task(
    *,
    task_id: str,
    settings: Mapping[str, Any],
    backend: Any,
    artifact_dir: Path,
    config_sha256: str,
    attempt_id: str,
) -> tuple[dict[str, Any], bool]:
    output = _output_path(artifact_dir, task_id)
    if output.exists():
        row = _json(output)
        _validate_row(row, task_id=task_id, config_sha256=config_sha256)
        return row, True

    app = settings["appworld"]
    task_root = artifact_dir / PHASE_DIRECTORY
    restart = len(list((task_root / "worker_logs").glob(f"{task_id}.*.stderr.log")))
    experiment_name = f"exp027b_bare_{attempt_id}_{task_id}_restart{restart:02d}"
    worker_log = task_root / "worker_logs" / f"{task_id}.{restart:02d}.stderr.log"
    started = time.perf_counter()
    steps = []
    trajectory = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    counts: Counter[str] = Counter()
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
            tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
            prompt_tokens = int(tokenized.attention_mask.sum().item())
            generated = backend.generate(
                messages=messages,
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
            for key in total_usage:
                total_usage[key] += int(generated.usage.get(key, 0))
            if not code.strip():
                counts["invalid_code"] += 1
            if executed["execution_exception"] is not None or "Syntax error" in observation:
                counts["execution_exception"] += 1
            lower = observation.lower()
            if "api" in lower and any(
                value in lower for value in ("not found", "does not exist", "invalid api")
            ):
                counts["wrong_api_heuristic"] += 1
            if "complete_task" in code and not bool(executed["task_completed"]):
                counts["premature_complete"] += 1
            steps.append(
                {
                    "step_id": step_id,
                    "prompt_tokens": prompt_tokens,
                    "usage": dict(generated.usage),
                    "raw_model_response": generated.text,
                    "extracted_code": code,
                    "fixed_model_response": fixed,
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
        "selector_invoked": False,
        "memory_inserted": False,
        "repeated_invalid_action_early_stop": False,
        "task_identity": ready,
        "steps": steps,
        "step_count": len(steps),
        "usage": total_usage,
        "counts": dict(counts),
        "success": bool(final["success"]),
        "success_source": "evaluation.success",
        "task_completed": bool(final["task_completed"]),
        "evaluation": final["evaluation"],
        "wall_seconds": time.perf_counter() - started,
        "worker_log": str(worker_log),
    }
    atomic_write_json(output, row)
    return row, False


def _summary(
    rows: Sequence[Mapping[str, Any]],
    raw_summary: Mapping[str, Any],
    environment: Mapping[str, Any],
) -> dict[str, Any]:
    bare = {str(row["task_id"]) for row in rows if bool(row["success"])}
    raw = {str(value) for value in raw_summary["success_ids"]}
    return {
        "format": "matched_harness_bare_first37_summary_7g_v1",
        "global_seed": GLOBAL_SEED,
        "task_count": len(rows),
        "success_count": len(bare),
        "success_ids": sorted(bare),
        "raw_memory_success_count": len(raw),
        "raw_memory_success_ids": sorted(raw),
        "raw_memory_retained_success_ids": sorted(raw & bare),
        "raw_memory_gained_success_ids": sorted(raw - bare),
        "raw_memory_lost_success_ids": sorted(bare - raw),
        "historical_bare_success_count_secondary_reference": 10,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "total_generated_tokens": sum(
            int(row["usage"]["completion_tokens"]) for row in rows
        ),
        "mean_steps": statistics.fmean(float(row["step_count"]) for row in rows),
        "diagnostic_counts": dict(
            sum((Counter(row["counts"]) for row in rows), Counter())
        ),
        "task_outcomes": [
            {
                "task_id": str(row["task_id"]),
                "success": bool(row["success"]),
                "steps": int(row["step_count"]),
                "wall_seconds": float(row["wall_seconds"]),
                "prompt_tokens": int(row["usage"]["prompt_tokens"]),
                "generated_tokens": int(row["usage"]["completion_tokens"]),
            }
            for row in rows
        ],
        "environment_identity": dict(environment),
        "primary_comparator": "matched_harness_bare",
        "selector_invoked": False,
        "memory_inserted": False,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7g"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-027B requires seed 25101")
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    raw_path = Path(str(settings["first37"]["raw_memory_summary"]))
    raw_summary = _json(raw_path)
    if int(raw_summary["success_count"]) != int(settings["first37"]["raw_memory_success"]):
        raise ValueError("Immutable EXP-027A raw-memory result differs")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    config_sha256 = sha256_file(args.config)
    task_ids = [str(value) for value in settings["first37"]["task_ids"]]
    if args.task_limit is not None:
        task_ids = task_ids[: int(args.task_limit)]
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="phase_a_matched_harness_bare",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes={"raw_memory_summary": sha256_file(raw_path)},
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        backend = _build_backend(cfg)
        environment = _environment_identity(backend)
        rows = []
        reused = 0
        for task_id in task_ids:
            row, was_reused = _run_task(
                task_id=task_id,
                settings=settings,
                backend=backend,
                artifact_dir=args.artifact_dir,
                config_sha256=config_sha256,
                attempt_id=args.attempt_id,
            )
            rows.append(row)
            reused += int(was_reused)
            attempt.progress(
                status="phase_a_matched_harness_bare",
                completed_tasks=len(rows),
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(_output_path(args.artifact_dir, task_id)),
            )
            print(
                f"matched bare task={task_id} success={row['success']} "
                f"steps={row['step_count']}",
                flush=True,
            )
        summary = _summary(rows, raw_summary, environment)
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "config_sha256": config_sha256,
                "raw_memory_summary_sha256": sha256_file(raw_path),
                "resumed_task_count": reused,
                "new_task_count": len(rows) - reused,
            }
        )
        root = args.artifact_dir / PHASE_DIRECTORY
        atomic_write_json(root / "summary.json", summary)
        atomic_write_text(
            root / "report.md",
            "\n".join(
                [
                    "# EXP-027B matched-harness bare first37",
                    "",
                    f"- matched bare: `{summary['success_count']}/37`",
                    f"- raw memory: `{summary['raw_memory_success_count']}/37`",
                    f"- raw retained/gained/lost: `{len(summary['raw_memory_retained_success_ids'])}/"
                    f"{len(summary['raw_memory_gained_success_ids'])}/"
                    f"{len(summary['raw_memory_lost_success_ids'])}`",
                    f"- total steps: `{summary['total_steps']}`",
                    f"- wall hours: `{summary['total_wall_seconds'] / 3600.0:.4f}`",
                    "- authoritative success: `evaluation.success`",
                    "- selector/memory: `disabled/disabled`",
                    "- historical 10/37 bare is secondary only",
                    "",
                ]
            ),
        )


if __name__ == "__main__":
    main()
