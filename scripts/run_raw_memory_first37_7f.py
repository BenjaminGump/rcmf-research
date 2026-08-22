from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import threading
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.config import load_config
from rcmf.training.deep_residual_amortization_7f import (
    GLOBAL_SEED,
    aggregate_and_select_class,
)
from rcmf.training.multiview_representations_6c import (
    STATE_VIEW_NAMES,
    _ordered_message_content_spans,
    frozen_qwen_span_readouts,
    tokenize_and_validate_char_spans,
)
from rcmf.training.signature_balanced_field_7c import SignatureBalancedFieldSelector
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


PROTOCOL_VERSION = "appworld_full_agent_bridge_7f_v1"
RESULT_VERSION = "raw_memory_first37_task_result_7f_v2"
PHASE_A_DIRECTORY = "phase_a_first37_v2"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_deep_residual_amortization_7f.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp027a_phase_a")
    parser.add_argument("--task-limit", type=int)
    return parser.parse_args()


class FullAgentBridge:
    def __init__(
        self,
        *,
        executable: Path,
        script: Path,
        appworld_root: Path,
        stderr_path: Path,
        timeout_seconds: float,
    ) -> None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = stderr_path.open("w", encoding="utf-8")
        environment = dict(os.environ)
        environment["APPWORLD_ROOT"] = str(appworld_root)
        self.process = subprocess.Popen(
            [str(executable), str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
        )
        self.timeout_seconds = float(timeout_seconds)

    def _send(self, payload: Mapping[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("Full-agent bridge stdin is unavailable")
        self.process.stdin.write(json.dumps(dict(payload), sort_keys=True) + "\n")
        self.process.stdin.flush()

    def _receive(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise RuntimeError("Full-agent bridge stdout is unavailable")
        values: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                values.put(self.process.stdout.readline())
            except BaseException as error:  # noqa: BLE001
                values.put(error)

        threading.Thread(target=read, daemon=True).start()
        try:
            value = values.get(timeout=self.timeout_seconds)
        except queue.Empty as error:
            self.terminate()
            raise TimeoutError("Timed out waiting for full-agent bridge") from error
        if isinstance(value, BaseException):
            raise RuntimeError("Full-agent bridge read failed") from value
        if not value:
            raise RuntimeError(
                f"Full-agent bridge closed unexpectedly; exit={self.process.poll()}"
            )
        response = json.loads(value)
        if response.get("op") == "fatal":
            try:
                self.process.wait(timeout=min(self.timeout_seconds, 30.0))
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)
            if not self._stderr.closed:
                self._stderr.close()
            raise RuntimeError(f"Full-agent bridge fatal response: {response['fatal']}")
        return response

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._send(payload)
        response = self._receive()
        if response.get("format") != PROTOCOL_VERSION or not bool(response.get("ready")):
            raise RuntimeError(f"Full-agent bridge did not become ready: {response}")
        return response

    def execute(self, *, nonce: str, step_id: int, code: str) -> dict[str, Any]:
        self._send(
            {
                "format": PROTOCOL_VERSION,
                "op": "execute",
                "ready_nonce": nonce,
                "step_id": int(step_id),
                "code": code,
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            }
        )
        response = self._receive()
        if response.get("op") != "executed" or int(response["step_id"]) != int(step_id):
            raise RuntimeError(f"Full-agent bridge execution differs: {response}")
        return response

    def finish(self, *, nonce: str) -> dict[str, Any]:
        self._send(
            {"format": PROTOCOL_VERSION, "op": "finish", "ready_nonce": nonce}
        )
        response = self._receive()
        if response.get("op") != "finished":
            raise RuntimeError(f"Full-agent bridge finalizer differs: {response}")
        self.close()
        return response

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        code = self.process.wait(timeout=self.timeout_seconds)
        self._stderr.close()
        if code != 0:
            raise RuntimeError(f"Full-agent bridge exited with status {code}")

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if not self._stderr.closed:
            self._stderr.close()

    def __enter__(self) -> "FullAgentBridge":
        return self

    def __exit__(self, *_: Any) -> None:
        if self.process.poll() is None:
            self.terminate()


class FrozenDeploymentSelector:
    def __init__(self, *, settings: Mapping[str, Any], backend: Any) -> None:
        self.settings = settings
        self.backend = backend
        parent_c = Path(str(settings["parent_exp025c"]))
        parent_b = Path(str(settings["parent_exp025b"]))
        self.ensemble_path = parent_c / "selector/ensemble_scores.pt"
        if sha256_file(self.ensemble_path) != str(settings["expected_selector_sha256"]):
            raise ValueError("Frozen deployment selector hash differs")
        ensemble = torch.load(self.ensemble_path, map_location="cpu", weights_only=False)
        self.ordered_transition_ids = [str(value) for value in ensemble["ordered_transition_ids"]]
        self.calibration = list(ensemble["train_calibration"])
        selector = settings["selector"]
        self.models = []
        for row in ensemble["seed_checkpoints"]:
            checkpoint_path = Path(str(row["checkpoint"]))
            if sha256_file(checkpoint_path) != str(row["checkpoint_sha256"]):
                raise ValueError("Frozen selector seed checkpoint hash differs")
            model = SignatureBalancedFieldSelector(
                state_views=int(selector["state_views"]),
                transition_views=int(selector["transition_views"]),
                input_dim=int(selector["input_dim"]),
                projection_dim=int(selector["projection_dim"]),
                interaction_rank=int(selector["interaction_rank"]),
            ).to(backend.device)
            payload = torch.load(checkpoint_path, map_location=backend.device, weights_only=False)
            model.load_state_dict(payload["model_state_dict"])
            model.eval()
            for parameter in model.parameters():
                parameter.requires_grad_(False)
            self.models.append(model)
        transition_cache = torch.load(
            parent_c / "representation_cache/multiview/transition_multiview.pt",
            map_location="cpu",
            weights_only=False,
        )
        if [str(value) for value in transition_cache["ordered_ids"]] != self.ordered_transition_ids:
            raise ValueError("Transition representation order differs from selector order")
        transition_values = transition_cache["representations"]["final_layer"].to(
            backend.device, dtype=torch.float32
        )
        with torch.no_grad():
            self.transition_factors = [
                model.transition_factors(transition_values) for model in self.models
            ]
        transition_path = (
            parent_b / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
        )
        transitions = [dict(row) for row in read_jsonl(transition_path)]
        self.transitions = {str(row["transition_id"]): row for row in transitions}
        if set(self.transitions) != set(self.ordered_transition_ids):
            raise ValueError("Clean transition ledger differs from selector ledger")
        class_manifest = _json(
            parent_b / "clean_procedural_audit/clean_signature_equivalence_manifest.json"
        )
        self.classes = {
            str(row["signature_class_id"]): row for row in class_manifest["classes"]
        }
        self.class_by_transition = {}
        for class_id, row in self.classes.items():
            for transition_id in row["member_transition_ids"]:
                if str(transition_id) in self.class_by_transition:
                    raise ValueError("A transition belongs to multiple signature classes")
                self.class_by_transition[str(transition_id)] = class_id
        self.transition_class_ids = [
            self.class_by_transition[value] for value in self.ordered_transition_ids
        ]

    def _state_values(self, messages: Sequence[Mapping[str, str]]) -> torch.Tensor:
        rendered = self.backend.tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=True
        )
        message_spans = _ordered_message_content_spans(rendered, messages)
        initial_count = len(message_spans) - len(
            [row for row in reversed(message_spans) if row["role"] in {"user", "assistant"}]
        )
        # Full-demo messages are fixed; the current task begins at the last user message
        # that is followed only by current-task assistant/user turns.
        configured_initial = len(messages) - 1
        for index, row in enumerate(message_spans):
            if index > 0 and row["role"] == "user" and str(row["message_index"]) == str(index):
                configured_initial = index
        # The exact initial count is stable and exposed by the frozen renderer metadata.
        from rcmf.benchmarks.appworld.prompt import appworld_renderer_metadata

        initial_count = int(appworld_renderer_metadata("full_demo")["initial_message_count"])
        current = message_spans[initial_count:]
        if not current or current[0]["role"] != "user":
            raise ValueError("Live current-task messages do not start with the query")
        latest_user = next(row for row in reversed(current) if row["role"] == "user")
        encoded = self.backend.tokenizer(
            rendered,
            add_special_tokens=False,
            truncation=False,
            return_offsets_mapping=True,
        )
        offsets = encoded["offset_mapping"]
        nonempty = [(int(start), int(end)) for start, end in offsets if int(end) > int(start)]
        spans = {
            "full_prompt_global": (0, len(rendered)),
            "current_task_goal": (int(current[0]["char_start"]), int(current[0]["char_end"])),
            "current_task_history": (int(current[0]["char_start"]), int(current[-1]["char_end"])),
            "latest_user_output": (int(latest_user["char_start"]), int(latest_user["char_end"])),
            "generation_boundary": nonempty[-1],
        }
        input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
            self.backend.tokenizer, rendered, spans
        )
        readouts = frozen_qwen_span_readouts(
            model=self.backend.model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            span_rows=span_rows,
            device=self.backend.device,
        )["final_layer"]
        return torch.cat([readouts[name] for name in STATE_VIEW_NAMES], dim=0).unsqueeze(0).to(
            self.backend.device
        )

    @torch.no_grad()
    def scores(self, messages: Sequence[Mapping[str, str]]) -> list[float]:
        state = self._state_values(messages)
        values = []
        for model, transition, calibration in zip(
            self.models, self.transition_factors, self.calibration, strict=True
        ):
            state_factor = model.state_factors(state)
            score = torch.einsum(
                "bvr,vwr,twr->bt", state_factor, model.tensor_core, transition
            ) / (
                model.state_views * model.transition_views * model.interaction_rank
            ) ** 0.5
            values.append(
                (score[0] - float(calibration["train_mean"]))
                / float(calibration["train_std"])
            )
        return torch.stack(values).mean(dim=0).cpu().tolist()

    def select(
        self, messages: Sequence[Mapping[str, str]], *, prompt_profile: str
    ) -> dict[str, Any]:
        scores = self.scores(messages)
        selected = aggregate_and_select_class(
            scores,
            self.transition_class_ids,
            legal_transition_ids=self.ordered_transition_ids,
            ordered_transition_ids=self.ordered_transition_ids,
        )
        class_row = self.classes[str(selected["selected_class_id"])]
        canonical = str(class_row["canonical_transition_id"])
        member_ids = [str(value) for value in class_row["member_transition_ids"]]
        median = statistics.median(
            int(self.transitions[value]["teacher_section_tokens"]) for value in member_ids
        )
        ordered_members = [canonical] + sorted(
            (value for value in member_ids if value != canonical),
            key=lambda value: (
                abs(int(self.transitions[value]["teacher_section_tokens"]) - median),
                hashlib.sha256(value.encode("utf-8")).hexdigest(),
            ),
        )
        attempts = []
        for transition_id in ordered_members:
            raw_messages = messages_with_transition_memory(
                messages, self.transitions[transition_id], prompt_profile
            )
            tokenized = self.backend.tokenize_messages(raw_messages, add_generation_prompt=True)
            tokens = int(tokenized.attention_mask.sum().item())
            attempts.append({"transition_id": transition_id, "prompt_tokens": tokens})
            if tokens <= int(self.settings["selector"]["context_limit"]):
                return {
                    **selected,
                    "transition_id": transition_id,
                    "canonical_transition_id": canonical,
                    "same_class_substitution": transition_id != canonical,
                    "prompt_tokens": tokens,
                    "attempts": attempts,
                    "messages": raw_messages,
                }
        raise RuntimeError(
            "selected_signature_class_has_no_context_feasible_raw_member:"
            f"{selected['selected_class_id']}:{attempts}"
        )


def _task_output_path(root: Path, task_id: str) -> Path:
    return root / PHASE_A_DIRECTORY / "task_results" / f"{task_id}.json"


def _run_task(
    *,
    task_id: str,
    settings: Mapping[str, Any],
    backend: Any,
    selector: FrozenDeploymentSelector,
    artifact_dir: Path,
    config_sha256: str,
    attempt_id: str,
) -> dict[str, Any]:
    app = settings["appworld"]
    task_root = artifact_dir / PHASE_A_DIRECTORY
    output = _task_output_path(artifact_dir, task_id)
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == RESULT_VERSION,
            "task": str(row.get("task_id")) == task_id,
            "config": str(row.get("config_sha256")) == config_sha256,
            "selector": str(row.get("selector_sha256"))
            == str(settings["expected_selector_sha256"]),
            "complete": row.get("status") == "complete",
        }
        if not all(checks.values()):
            raise ValueError(f"Invalid existing first37 task row: {task_id}: {checks}")
        return row
    restart = len(list((task_root / "worker_logs").glob(f"{task_id}.*.stderr.log")))
    experiment_name = (
        f"exp027a_raw_first37_{attempt_id}_{task_id}_restart{restart:02d}"
    )
    worker_log = task_root / "worker_logs" / f"{task_id}.{restart:02d}.stderr.log"
    started = time.perf_counter()
    steps = []
    trajectory = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
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
            bare = backend.tokenize_messages(messages, add_generation_prompt=True)
            bare_tokens = int(bare.attention_mask.sum().item())
            selection = selector.select(messages, prompt_profile=str(app["prompt_profile"]))
            generated = backend.generate(
                messages=selection.pop("messages"),
                max_new_tokens=int(app["max_new_tokens"]),
                temperature=float(app["temperature"]),
                top_p=float(app["top_p"]),
            )
            code, fixed = extract_code_and_fix_content(generated.text)
            executed = bridge.execute(nonce=str(ready["ready_nonce"]), step_id=step_id, code=code)
            observation = str(executed["raw_observation"])
            trajectory.append({"response": fixed, "observation": observation})
            for key in total_usage:
                total_usage[key] += int(generated.usage.get(key, 0))
            if not code.strip():
                counts["invalid_code"] += 1
            if executed["execution_exception"] is not None or "Syntax error" in observation:
                counts["execution_exception"] += 1
            lower = observation.lower()
            if "api" in lower and any(value in lower for value in ("not found", "does not exist", "invalid api")):
                counts["wrong_api_heuristic"] += 1
            if "complete_task" in code and not bool(executed["task_completed"]):
                counts["premature_complete"] += 1
            steps.append(
                {
                    "step_id": step_id,
                    "selection": selection,
                    "bare_prompt_tokens": bare_tokens,
                    "raw_prompt_overhead_tokens": int(selection["prompt_tokens"]) - bare_tokens,
                    "usage": generated.usage,
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
        "format": RESULT_VERSION,
        "status": "complete",
        "task_id": task_id,
        "experiment_name": experiment_name,
        "global_seed": GLOBAL_SEED,
        "config_sha256": config_sha256,
        "selector_sha256": str(settings["expected_selector_sha256"]),
        "task_identity": ready,
        "steps": steps,
        "step_count": len(steps),
        "usage": total_usage,
        "counts": dict(counts),
        "success": bool(final["success"]),
        "task_completed": bool(final["task_completed"]),
        "evaluation": final["evaluation"],
        "wall_seconds": time.perf_counter() - started,
        "worker_log": str(worker_log),
    }
    atomic_write_json(output, row)
    return row


def _baseline(settings: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    root = Path(str(settings["baseline_first37_dir"]))
    rows = {}
    for task_id in settings["first37"]["task_ids"]:
        path = root / f"{task_id}.json"
        row = _json(path)
        if str(row["task_id"]) != str(task_id):
            raise ValueError("Bare baseline task identity differs")
        rows[str(task_id)] = row
    if sum(bool(row["success"]) for row in rows.values()) != int(
        settings["first37"]["expected_bare_success"]
    ):
        raise ValueError("Locked first37 bare success count differs")
    return rows


def _summary(rows: Sequence[Mapping[str, Any]], baseline: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    success = {str(row["task_id"]) for row in rows if bool(row["success"])}
    bare = {task_id for task_id, row in baseline.items() if bool(row["success"])}
    count = len(success)
    band = "STRONG" if count >= 12 else "COMPETITIVE" if count >= 9 else "CLEARLY_WEAK"
    payload = {
        "format": "raw_memory_first37_summary_7f_v1",
        "task_count": len(rows),
        "success_count": count,
        "success_ids": sorted(success),
        "bare_success_count": len(bare),
        "bare_success_ids": sorted(bare),
        "retained_success_ids": sorted(success & bare),
        "gained_success_ids": sorted(success - bare),
        "lost_success_ids": sorted(bare - success),
        "interpretation_band": band,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"]["prompt_tokens"]) for row in rows),
        "total_generated_tokens": sum(int(row["usage"]["completion_tokens"]) for row in rows),
        "mean_raw_prompt_overhead_tokens": statistics.fmean(
            float(step["raw_prompt_overhead_tokens"])
            for row in rows
            for step in row["steps"]
        ),
        "diagnostic_counts": dict(
            sum((Counter(row["counts"]) for row in rows), Counter())
        ),
    }
    return payload


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7f"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-027A requires GLOBAL_SEED=25101")
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    config_sha256 = sha256_file(args.config)
    baseline = _baseline(settings)
    backend = _build_backend(cfg)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Phase A loaded trainable Qwen parameters")
    selector = FrozenDeploymentSelector(settings=settings, backend=backend)
    task_ids = list(settings["first37"]["task_ids"])
    if args.task_limit is not None:
        task_ids = task_ids[: int(args.task_limit)]
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="phase_a_raw_memory_first37",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes={
            "selector": str(settings["expected_selector_sha256"]),
            "baseline": hashlib.sha256(
                json.dumps(baseline, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        rows = []
        for task_id in task_ids:
            row = _run_task(
                task_id=str(task_id),
                settings=settings,
                backend=backend,
                selector=selector,
                artifact_dir=args.artifact_dir,
                config_sha256=config_sha256,
                attempt_id=args.attempt_id,
            )
            rows.append(row)
            attempt.progress(
                status="phase_a_raw_memory_first37",
                completed_tasks=len(rows),
                total_tasks=len(task_ids),
                latest_validated_checkpoint=str(_task_output_path(args.artifact_dir, str(task_id))),
            )
            print(
                f"phase_a task={task_id} success={row['success']} steps={row['step_count']}",
                flush=True,
            )
        summary = _summary(rows, baseline)
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "selector_sha256": str(settings["expected_selector_sha256"]),
                "config_sha256": config_sha256,
            }
        )
        path = args.artifact_dir / PHASE_A_DIRECTORY / "summary.json"
        atomic_write_json(path, summary)
        atomic_write_text(
            args.artifact_dir / PHASE_A_DIRECTORY / "report.md",
            "\n".join(
                [
                    "# EXP-027A Phase A raw-memory upper bound",
                    "",
                    f"- result: `{summary['success_count']}/37`",
                    f"- paired bare: `{summary['bare_success_count']}/37`",
                    f"- band: `{summary['interpretation_band']}`",
                    f"- gained/lost: `{len(summary['gained_success_ids'])}/{len(summary['lost_success_ids'])}`",
                    f"- steps: `{summary['total_steps']}`",
                    f"- wall hours: `{summary['total_wall_seconds'] / 3600.0:.4f}`",
                    "",
                ]
            ),
        )


if __name__ == "__main__":
    main()
