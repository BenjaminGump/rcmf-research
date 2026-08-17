from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any

from rcmf.benchmarks.appworld.prompt import build_appworld_messages
from rcmf.training.datasets import _parse_appworld_state_text
from rcmf.training.procedural_supervision_6f import stable_hash


CLEAN_CONDITION_MANIFEST_VERSION = "identity_reconciled_causal_condition_manifest_7b_v1"
LIVE_BRIDGE_PROTOCOL_VERSION = "appworld_live_one_step_bridge_7b_v1"
LIVE_GENERATION_RESULT_VERSION = "identity_reconciled_one_step_generation_7b_v1"


class LiveBridgeClient:
    def __init__(
        self,
        *,
        executable: Path,
        bridge_script: Path,
        appworld_root: Path,
        stderr_path: Path,
        timeout_seconds: float,
    ) -> None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr = stderr_path.open("w", encoding="utf-8")
        environment = dict(os.environ)
        environment["APPWORLD_ROOT"] = str(appworld_root)
        self.process = subprocess.Popen(
            [str(executable), str(bridge_script)],
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
            raise RuntimeError("Live bridge stdin is unavailable")
        self.process.stdin.write(
            json.dumps(dict(payload), sort_keys=True, ensure_ascii=False) + "\n"
        )
        self.process.stdin.flush()

    def _receive(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise RuntimeError("Live bridge stdout is unavailable")
        values: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                values.put(self.process.stdout.readline())
            except BaseException as error:  # noqa: BLE001 - crosses thread boundary
                values.put(error)

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            value = values.get(timeout=self.timeout_seconds)
        except queue.Empty as error:
            self.terminate()
            raise TimeoutError("Timed out waiting for live bridge response") from error
        if isinstance(value, BaseException):
            raise RuntimeError("Live bridge response read failed") from value
        if not value:
            code = self.process.poll()
            raise RuntimeError(f"Live bridge closed before response; exit={code}")
        response = json.loads(value)
        if response.get("op") == "fatal":
            raise RuntimeError(f"Live bridge fatal response: {response['fatal']}")
        return response

    def prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._send(payload)
        response = self._receive()
        if response.get("format") != LIVE_BRIDGE_PROTOCOL_VERSION:
            raise RuntimeError("Unexpected live bridge response version")
        if response.get("op") != "ready" or not bool(response.get("ready")):
            raise RuntimeError(f"Live bridge did not become ready: {response}")
        return response

    def execute(
        self,
        *,
        condition_key: str,
        ready_nonce: str,
        code: str,
        expected_target_observation: str | None = None,
    ) -> dict[str, Any]:
        self._send(
            {
                "format": LIVE_BRIDGE_PROTOCOL_VERSION,
                "op": "execute",
                "condition_key": condition_key,
                "ready_nonce": ready_nonce,
                "code": code,
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "expected_target_observation": expected_target_observation,
            }
        )
        response = self._receive()
        if response.get("op") != "executed" or not bool(response.get("complete")):
            raise RuntimeError(f"Live bridge did not execute action: {response}")
        self.close()
        return response

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            self.terminate()
            raise TimeoutError("Live bridge did not exit after execution") from error
        finally:
            self._stderr.close()
        if code != 0:
            raise RuntimeError(f"Live bridge exited with status {code}")

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

    def __enter__(self) -> "LiveBridgeClient":
        return self

    def __exit__(self, *_: Any) -> None:
        if self.process.poll() is None:
            self.terminate()


def build_live_appworld_messages(
    example: Any,
    actual_observations: Sequence[str],
    *,
    prompt_profile: str,
) -> list[dict[str, str]]:
    """Render the canonical prompt with observations from the retained live world."""

    _, query, historical_steps = _parse_appworld_state_text(str(example.state_text))
    if len(historical_steps) != len(actual_observations):
        raise ValueError(
            "Live observation count differs from the canonical state history: "
            f"{len(actual_observations)} != {len(historical_steps)}"
        )
    trajectory = []
    for (_, response, _), observation in zip(historical_steps, actual_observations, strict=True):
        trajectory.append({"response": str(response), "observation": str(observation)})
    return build_appworld_messages(
        task_message=query,
        trajectory_so_far=trajectory,
        prompt_profile=prompt_profile,
        target_type=str(example.target_type),
        system_prompt=str(example.metadata.get("system_prompt", "") or ""),
    )


def condition_checkpoint_name(condition_key: str) -> str:
    digest = hashlib.sha256(str(condition_key).encode("utf-8")).hexdigest()
    return f"{digest}.json"


def validate_condition_checkpoint(
    row: Mapping[str, Any],
    *,
    condition: Mapping[str, Any],
    condition_manifest_sha256: str,
    config_sha256: str,
    corpus_lineage_sha256: str,
    model_name: str,
) -> None:
    checks = {
        "format": row.get("format") == LIVE_GENERATION_RESULT_VERSION,
        "status": row.get("status") == "complete",
        "condition_key": str(row.get("condition_key")) == str(condition["condition_key"]),
        "condition_name": str(row.get("condition_name")) == str(condition["condition_name"]),
        "state": str(row.get("state_example_id")) == str(condition["state_example_id"]),
        "manifest": str(row.get("condition_manifest_sha256")) == str(condition_manifest_sha256),
        "config": str(row.get("config_sha256")) == str(config_sha256),
        "lineage": str(row.get("corpus_lineage_sha256")) == str(corpus_lineage_sha256),
        "model": str(row.get("model_name")) == str(model_name),
        "worker_complete": bool(row.get("live_worker", {}).get("complete")),
        "same_world": bool(row.get("live_worker", {}).get("same_world_execution")),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(f"Invalid condition checkpoint: {failed}")


def compare_condition_manifests(
    old_manifest: Mapping[str, Any],
    clean_manifest: Mapping[str, Any],
    *,
    old_transition_semantics: Mapping[str, tuple[str, int]],
    clean_transition_semantics: Mapping[str, tuple[str, int]],
) -> dict[str, Any]:
    """Classify clean-manifest changes without consulting model outcomes."""

    def keyed(manifest: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
        rows = {}
        for row in manifest["conditions"]:
            key = (str(row["state_example_id"]), str(row["condition_name"]))
            if key in rows:
                raise ValueError(f"Duplicate semantic condition: {key}")
            rows[key] = row
        return rows

    old = keyed(old_manifest)
    clean = keyed(clean_manifest)
    classifications: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for key in sorted(set(old) | set(clean)):
        previous = old.get(key)
        current = clean.get(key)
        if previous is None:
            category = "condition_added"
            reason = "condition became available after identity reconciliation"
        elif current is None:
            category = "condition_removed"
            reason = "condition is no longer available after identity reconciliation"
        else:
            old_id = previous.get("transition_id")
            clean_id = current.get("transition_id")
            old_semantic = old_transition_semantics.get(str(old_id)) if old_id is not None else None
            clean_semantic = (
                clean_transition_semantics.get(str(clean_id)) if clean_id is not None else None
            )
            if old_id == clean_id:
                category = "same_semantic_condition"
                reason = "condition and transition identity are unchanged"
            elif old_semantic is not None and old_semantic == clean_semantic:
                category = "id_only_change"
                reason = "same parent task and transition step under a reconciled ID"
            elif str(previous["condition_name"]) in {
                "C1_raw_oracle",
                "C2_signature_only",
                "C6_alternate_same_signature",
            }:
                category = "canonical_exemplar_change"
                reason = "clean signature-class tie breaking selected a different semantic exemplar"
            else:
                category = "semantic_selection_change"
                reason = (
                    "clean labels or deterministic controls selected different transition content"
                )
        counts[category] += 1
        classifications.append(
            {
                "state_example_id": key[0],
                "condition_name": key[1],
                "classification": category,
                "reason": reason,
                "old_condition_key": previous.get("condition_key") if previous else None,
                "clean_condition_key": current.get("condition_key") if current else None,
                "old_transition_id": previous.get("transition_id") if previous else None,
                "clean_transition_id": current.get("transition_id") if current else None,
                "old_transition_semantic": old_semantic if previous else None,
                "clean_transition_semantic": clean_semantic if current else None,
            }
        )
    payload = {
        "format": "identity_reconciled_condition_manifest_comparison_7b_v1",
        "old_condition_count": len(old),
        "clean_condition_count": len(clean),
        "classification_counts": dict(sorted(counts.items())),
        "rows": classifications,
    }
    payload["comparison_sha256"] = stable_hash(payload)
    return payload


def generation_runtime_projection(
    condition_count: int,
    state_count: int,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = settings["runtime"]
    scenarios = {}
    for name in ("best", "expected", "conservative"):
        generation_seconds = float(condition_count) * float(
            runtime["generation_seconds_per_condition"][name]
        )
        replay_seconds = float(condition_count) * float(
            runtime["replay_seconds_per_condition"][name]
        )
        smoke_seconds = 4.0 * (
            float(runtime["generation_seconds_per_condition"][name])
            + float(runtime["replay_seconds_per_condition"][name])
        )
        validation_seconds = float(state_count) * float(
            runtime["validation_seconds_per_state"][name]
        )
        scenarios[name] = {
            "qwen_generation_seconds": generation_seconds,
            "h100_hours": generation_seconds / 3600.0,
            "appworld_replay_execution_seconds": replay_seconds,
            "smoke_seconds": smoke_seconds,
            "final_validation_report_seconds": validation_seconds,
            "wall_seconds": generation_seconds
            + replay_seconds
            + smoke_seconds
            + validation_seconds,
        }
    projected_bytes = int(runtime["artifact_bytes_per_condition"]) * int(condition_count)
    threshold = float(runtime["review_threshold_h100_hours"])
    return {
        "format": "identity_reconciled_one_step_runtime_projection_7b_v1",
        "state_count": int(state_count),
        "condition_count": int(condition_count),
        "qwen_generation_count": int(condition_count),
        "appworld_reconstruction_count": int(condition_count),
        "appworld_execution_count": int(condition_count),
        "scenarios": scenarios,
        "projected_artifact_bytes": projected_bytes,
        "projected_artifact_gib": projected_bytes / (1024**3),
        "review_threshold_h100_hours": threshold,
        "requires_explicit_runtime_approval": scenarios["expected"]["h100_hours"] > threshold,
        "resume_plan": {
            "unit": "one immutable condition key",
            "worker": "fresh AppWorld 0.1.0 subprocess per condition",
            "checkpoint": "one atomic JSON result per completed condition",
            "skip": "hash-validate completed result before skipping",
            "interrupted_condition": "discard incomplete worker and retry with a new experiment name",
            "ledger": "append-only attempts with parent/resume identity",
            "heartbeat_seconds": 240,
        },
    }
