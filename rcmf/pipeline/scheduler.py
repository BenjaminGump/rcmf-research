from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from rcmf.pipeline.contracts import PipelineContract, StageSpec
from rcmf.pipeline.resume import (
    AppendOnlyAttemptLedger,
    StageStateStore,
    exclusive_lock,
    heartbeat_payload,
    utc_now,
)
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.utils.serialization import atomic_write_json, ensure_dir


StageRunner = Callable[[StageSpec, Sequence[str], Path, Mapping[str, str]], int]
StageValidator = Callable[[StageSpec, Path, str], Mapping[str, Any]]


@dataclass
class SchedulerResult:
    status: str
    completed: list[str]
    skipped: list[str]
    failed_stage: str | None
    transitions: list[dict[str, Any]]


def subprocess_stage_runner(
    stage: StageSpec,
    command: Sequence[str],
    stage_dir: Path,
    environment: Mapping[str, str],
) -> int:
    del stage
    log_root = ensure_dir(stage_dir / "logs")
    with (log_root / "stdout.log").open("ab") as stdout, (
        log_root / "stderr.log"
    ).open("ab") as stderr:
        process = subprocess.Popen(
            list(command),
            stdout=stdout,
            stderr=stderr,
            cwd=Path.cwd(),
            env=dict(environment),
            start_new_session=os.name != "nt",
        )
        atomic_write_json(
            stage_dir / "process.json",
            {"pid": process.pid, "command": list(command), "started_utc": utc_now()},
        )
        deadline = float(environment.get("RCMF_PIPELINE_HARD_DEADLINE_EPOCH", "inf"))
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                atomic_write_json(
                    stage_dir / "hard_cap_stop.json",
                    {
                        "format": "rcmf_pipeline_hard_cap_stop_14b_v1",
                        "pid": process.pid,
                        "command": list(command),
                        "deadline_epoch": deadline,
                        "stopped_utc": utc_now(),
                    },
                )
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                return 124
            try:
                return process.wait(timeout=min(30.0, remaining))
            except subprocess.TimeoutExpired:
                continue


def default_stage_validator(
    stage: StageSpec, stage_dir: Path, source_commit: str
) -> Mapping[str, Any]:
    del stage
    return validate_stage_completion(stage_dir, source_commit)


class EventDrivenScheduler:
    def __init__(
        self,
        contract: PipelineContract,
        run_root: str | Path,
        *,
        python_executable: str,
        config_path: str | Path,
        runner: StageRunner = subprocess_stage_runner,
        validator: StageValidator = default_stage_validator,
        heartbeat_interval_seconds: float = 240.0,
        transition_target_seconds: float = 60.0,
    ) -> None:
        contract.validate()
        self.contract = contract
        self.run_root = ensure_dir(run_root)
        self.store = StageStateStore(self.run_root)
        self.ledger = AppendOnlyAttemptLedger(self.run_root / "attempts.jsonl")
        self.python_executable = python_executable
        self.config_path = str(config_path)
        self.runner = runner
        self.validator = validator
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.transition_target_seconds = transition_target_seconds
        self._stop_heartbeat = threading.Event()
        self._current_stage: str | None = None

    def _close_interrupted_attempts(self) -> None:
        for attempt_id in self.ledger.open_attempt_ids():
            self.ledger.close(
                attempt_id,
                "interrupted",
                {
                    "reason": "parent_or_stage_process_ended_before_attempt_closure",
                    "resume_policy": "validate_atomic_completion_then_rerun_first_incomplete_stage",
                },
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.is_set():
            atomic_write_json(
                self.run_root / "heartbeat.json",
                heartbeat_payload(self.contract.run_uuid, self._current_stage, os.getpid()),
            )
            self._stop_heartbeat.wait(self.heartbeat_interval_seconds)

    def _completion_valid(self, stage: StageSpec) -> bool:
        completion = self.store.load_completion(stage.stage_id)
        if not completion or not completion.get("passed"):
            return False
        result = self.validator(stage, self.store.stage_dir(stage.stage_id), self.contract.source_commit)
        return bool(result.get("passed"))

    def _gate_allows_one_demo(self) -> bool:
        completion = self.store.load_completion("D22_three_demo_reproduction_gate")
        if not completion:
            return False
        gate_path = self.store.stage_dir("D22_three_demo_reproduction_gate") / "gate.json"
        if not gate_path.exists():
            return False
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        return bool(gate.get("continue_to_one_demo", False))

    def _is_eligible(self, stage: StageSpec) -> bool:
        if stage.conditional_on and not self._gate_allows_one_demo():
            return False
        return all(self._completion_valid(self.contract.stage_map()[dep]) for dep in stage.dependencies)

    def _resolved_command(self, stage: StageSpec) -> list[str]:
        values = [part.format(python=self.python_executable) for part in stage.command]
        return [
            *values,
            "--config",
            self.config_path,
            "--run-root",
            str(self.run_root),
            "--source-commit",
            self.contract.source_commit,
        ]

    def run(self) -> SchedulerResult:
        authorization = self.run_root / "runtime_authorization.json"
        if not authorization.exists():
            raise PermissionError("Formal scheduler requires runtime_authorization.json")
        auth = json.loads(authorization.read_text(encoding="utf-8"))
        if not auth.get("authorized") or float(auth.get("hard_cap_hours", 0)) != self.contract.hard_cap_hours:
            raise PermissionError("Runtime authorization does not match the pipeline contract")
        if auth.get("source_commit") and str(auth["source_commit"]) != self.contract.source_commit:
            raise PermissionError("Runtime authorization source commit differs")
        if float(auth.get("recommended_hard_cap_hours", 0.0)) > self.contract.hard_cap_hours:
            raise PermissionError("Recommended hard cap exceeds the approved ceiling")
        started_utc = str(auth.get("run_started_utc") or utc_now())
        started_timestamp = datetime.fromisoformat(
            started_utc.replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
        completed: list[str] = []
        skipped: list[str] = []
        transitions: list[dict[str, Any]] = []
        failed_stage: str | None = None
        heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat.start()
        try:
            with exclusive_lock(
                self.run_root / "scheduler.lock",
                {"pid": os.getpid(), "run_uuid": self.contract.run_uuid, "utc": utc_now()},
            ):
                self._close_interrupted_attempts()
                for stage in self.contract.stages:
                    elapsed_hours = (
                        datetime.now(timezone.utc).timestamp() - started_timestamp
                    ) / 3600.0
                    if elapsed_hours >= self.contract.hard_cap_hours:
                        failed_stage = stage.stage_id
                        break
                    if self._completion_valid(stage):
                        completed.append(stage.stage_id)
                        continue
                    if stage.conditional_on and not self._gate_allows_one_demo():
                        skipped.append(stage.stage_id)
                        continue
                    if not self._is_eligible(stage):
                        failed_stage = stage.stage_id
                        break
                    maximum_attempts = int(
                        self.contract.metadata.get(
                            "maximum_recoverable_attempts_per_stage", 3
                        )
                    )
                    retry_delay = float(
                        self.contract.metadata.get(
                            "recoverable_retry_delay_seconds", 60.0
                        )
                    )
                    passed = False
                    exit_code = 65
                    for retry_ordinal in range(1, maximum_attempts + 1):
                        self._current_stage = stage.stage_id
                        attempt_id = (
                            f"{stage.stage_id}-{int(time.time() * 1_000_000)}"
                        )
                        stage_start = time.monotonic()
                        stage_start_utc = utc_now()
                        command = self._resolved_command(stage)
                        self.ledger.open(
                            attempt_id,
                            {
                                "run_uuid": self.contract.run_uuid,
                                "stage_id": stage.stage_id,
                                "source_commit": self.contract.source_commit,
                                "command": command,
                                "retry_ordinal": retry_ordinal,
                                "maximum_recoverable_attempts": maximum_attempts,
                            },
                        )
                        environment = dict(os.environ)
                        environment["PYTHONHASHSEED"] = str(
                            self.contract.global_seed
                        )
                        environment["RCMF_PIPELINE_ATTEMPT_ID"] = attempt_id
                        environment["RCMF_PIPELINE_HARD_DEADLINE_EPOCH"] = str(
                            started_timestamp + self.contract.hard_cap_hours * 3600.0
                        )
                        exit_code = self.runner(
                            stage,
                            command,
                            self.store.stage_dir(stage.stage_id),
                            environment,
                        )
                        stage_end_utc = utc_now()
                        validator_start = time.monotonic()
                        validator_start_utc = utc_now()
                        validation = self.validator(
                            stage,
                            self.store.stage_dir(stage.stage_id),
                            self.contract.source_commit,
                        )
                        validator_end = time.monotonic()
                        validator_end_utc = utc_now()
                        passed = exit_code == 0 and bool(validation.get("passed"))
                        recoverable = (
                            exit_code == 75
                            and retry_ordinal < maximum_attempts
                        )
                        self.store.write_completion(
                            stage.stage_id,
                            {
                                "passed": passed,
                                "exit_code": exit_code,
                                "recoverable": recoverable,
                                "source_commit": self.contract.source_commit,
                                "attempt_id": attempt_id,
                                "retry_ordinal": retry_ordinal,
                                "validator": dict(validation),
                            },
                        )
                        self.ledger.close(
                            attempt_id,
                            "complete" if passed else "failed",
                            {
                                "exit_code": exit_code,
                                "recoverable": recoverable,
                                "validator": dict(validation),
                            },
                        )
                        transition_seconds = max(
                            0.0, time.monotonic() - validator_end
                        )
                        transitions.append(
                            {
                                "stage_id": stage.stage_id,
                                "attempt_id": attempt_id,
                                "retry_ordinal": retry_ordinal,
                                "stage_start_utc": stage_start_utc,
                                "stage_end_utc": stage_end_utc,
                                "stage_wall_seconds": validator_start - stage_start,
                                "validator_start_utc": validator_start_utc,
                                "validator_end_utc": validator_end_utc,
                                "validator_seconds": validator_end - validator_start,
                                "next_stage_start_utc": utc_now(),
                                "stage_to_next_transition_seconds": transition_seconds,
                                "transition_delay_reason": (
                                    "recoverable_infrastructure_retry"
                                    if recoverable
                                    else "none"
                                ),
                                "target_met": transition_seconds
                                <= self.transition_target_seconds,
                            }
                        )
                        if passed or not recoverable:
                            break
                        time.sleep(retry_delay)
                    if not passed:
                        failed_stage = stage.stage_id
                        break
                    completed.append(stage.stage_id)
                    self.store.write_scheduler_state(
                        {
                            "status": "running",
                            "current_stage": stage.stage_id,
                            "completed": completed,
                            "skipped": skipped,
                            "transitions": transitions,
                        }
                    )
        finally:
            self._stop_heartbeat.set()
            heartbeat.join(timeout=2)
            self._current_stage = None
        status = "failed" if failed_stage else "complete"
        result = SchedulerResult(status, completed, skipped, failed_stage, transitions)
        self.store.write_scheduler_state(
            {
                "status": status,
                "completed": completed,
                "skipped": skipped,
                "failed_stage": failed_stage,
                "transitions": transitions,
            }
        )
        return result


def terminate_process_tree(pid: int) -> None:
    """Best-effort helper for explicit operator shutdown; never called by watchdog."""
    os.kill(pid, signal.SIGTERM)
