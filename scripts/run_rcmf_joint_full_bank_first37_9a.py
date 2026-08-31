from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.benchmarks.appworld.data import extract_code_and_fix_content
from rcmf.benchmarks.appworld.prompt import (
    appworld_renderer_metadata,
    build_appworld_messages,
    build_task_message,
)
from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    FieldReaderHooks,
    FrozenSelectorDecomposition,
    assert_frozen_without_gradients,
    read_compiled_field,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    sha256_text,
)
from scripts.run_cross_attention_reader_8b import _attention_context
from scripts.run_raw_memory_first37_7f import (
    FullAgentBridge,
    FrozenDeploymentSelector,
    PROTOCOL_VERSION,
)
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _attempt_ids,
    _build_backend,
    _build_components,
)

RESULT_FORMAT = "rcmf_joint_full_bank_first37_task_9a_v1"
SUMMARY_FORMAT = "rcmf_joint_full_bank_first37_condition_summary_9a_v1"
MANIFEST_FORMAT = "rcmf_joint_full_bank_first37_manifest_9a_v1"
FINAL_FORMAT = "rcmf_joint_full_bank_first37_final_summary_9a_v1"
CONDITIONS = {
    "D0": "bare_zero_field",
    "D1": "correct_complete_field",
    "D2": "key_payload_shuffled_complete_field",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preflight", "smoke", "run", "finalize"), required=True
    )
    parser.add_argument("--condition", choices=sorted(CONDITIONS))
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031a_first37")
    return parser.parse_args()


def _paths(artifact_dir: Path) -> dict[str, Path]:
    root = artifact_dir / "first37"
    return {
        "root": root,
        "preflight": root / "runtime_preflight_v2.json",
        "preflight_v1": root / "runtime_preflight.json",
        "manifest": root / "condition_manifest.json",
        "static_assets": root / "raw_audit/static_prompt_assets.json",
        "deployment": artifact_dir / "deployment_field/complete_37_task_field.pt",
        "instant_add": artifact_dir / "deployment_field/instant_add_report.json",
        "selection": artifact_dir
        / "heldout_validation/live_full_field/checkpoint_selection.json",
        "source_cache": artifact_dir / "data/rcmf_source_cache.pt",
        "data_manifest": artifact_dir / "data/full_bank_data_manifest.json",
        "shuffle": artifact_dir / "data/key_payload_shuffle_manifest.json",
        "final": root / "final_summary.json",
    }


def _condition_root(paths: Mapping[str, Path], condition: str, smoke: bool) -> Path:
    return paths["root"] / ("smoke_v2" if smoke else "conditions") / condition


def _task_ids(settings: Mapping[str, Any]) -> list[str]:
    payload = load_config(Path(str(settings["first37"]["task_manifest_config"])))
    values = [str(v) for v in payload.raw["stage_c_7f"]["first37"]["task_ids"]]
    expected = int(settings["first37"]["expected_task_count"])
    if len(values) != expected or len(set(values)) != expected:
        raise ValueError("Locked first37 task manifest differs")
    return values


def build_first37_manifest(
    *, task_ids: Sequence[str], deployment_sha256: str, memory_count: int
) -> dict[str, Any]:
    rows = [
        {
            "condition": condition,
            "condition_name": CONDITIONS[condition],
            "task_id": task_id,
            "memory_count": 0 if condition == "D0" else memory_count,
            "runtime_memory_retrieval": False,
            "runtime_per_memory_scoring": False,
            "student_prompt_contains_raw_memory": False,
            "field_artifact_sha256": None
            if condition == "D0"
            else deployment_sha256,
        }
        for condition in CONDITIONS
        for task_id in task_ids
    ]
    payload = {
        "format": MANIFEST_FORMAT,
        "global_seed": GLOBAL_SEED,
        "task_count": len(task_ids),
        "logical_condition_count": len(rows),
        "conditions": list(CONDITIONS),
        "deployment_field_sha256": deployment_sha256,
        "complete_field_memory_count": memory_count,
        "exact_matched_harness": {
            "appworld": "0.1.0",
            "max_steps": 50,
            "max_api_calls_per_interaction": 100,
            "prompt_profile": "full_demo",
            "max_context_turns": 40,
            "max_new_tokens": 512,
            "temperature": 0.0,
            "top_p": 1.0,
            "do_sample": False,
            "enable_thinking": False,
            "success_source": "evaluation.success",
        },
        "rows": rows,
        "frozen_before_generation": True,
        "test_normal_outcomes_used": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _write_or_validate_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if _json(path) != dict(payload):
            raise ValueError(f"Existing immutable JSON differs: {path}")
    else:
        atomic_write_json(path, dict(payload))


class LiveFieldQueryEncoder:
    """Exact frozen q(s) path; no memory representations or scores are loaded."""

    def __init__(self, *, settings: Mapping[str, Any], backend: Any) -> None:
        self.backend = backend
        self.prompt_profile = str(settings["appworld"]["prompt_profile"])
        parent = Path(str(settings["parent_exp025c"]))
        ensemble_path = parent / "selector/ensemble_scores.pt"
        expected = str(settings["expected"]["selector_ensemble_sha256"])
        if sha256_file(ensemble_path) != expected:
            raise ValueError("Frozen selector ensemble SHA differs")
        ensemble = torch.load(ensemble_path, map_location="cpu", weights_only=False)
        checkpoints, hashes = [], []
        for row in ensemble["seed_checkpoints"]:
            path = Path(str(row["checkpoint"]))
            actual = sha256_file(path)
            if actual != str(row["checkpoint_sha256"]):
                raise ValueError("Frozen selector checkpoint SHA differs")
            checkpoints.append(torch.load(path, map_location="cpu", weights_only=False))
            hashes.append(actual)
        self.decomposition = FrozenSelectorDecomposition.from_checkpoints(
            checkpoints, ensemble["train_calibration"]
        ).to(backend.device)
        self.decomposition.eval()
        for parameter in self.decomposition.parameters():
            parameter.requires_grad_(False)
        if self.decomposition.key_dim != 960:
            raise ValueError("Live selector query dimension differs")
        self.identity = {
            "format": "rcmf_live_field_query_encoder_identity_9a_v1",
            "ensemble_sha256": expected,
            "checkpoint_sha256": hashes,
            "calibration": list(ensemble["train_calibration"]),
            "key_dim": 960,
            "memory_bank_loaded": False,
            "runtime_score_matrix_called": False,
            "runtime_class_aggregation_called": False,
        }
        self.identity_sha256 = canonical_sha256(self.identity)

    def _state_values(self, messages: Sequence[Mapping[str, str]]) -> Tensor:
        return FrozenDeploymentSelector._state_values(self, messages)

    @torch.no_grad()
    def query(self, messages: Sequence[Mapping[str, str]]) -> tuple[Tensor, Tensor]:
        views = self._state_values(messages)
        query = self.decomposition.query(views)[0]
        if tuple(views.shape) != (1, 10, 4096) or tuple(query.shape) != (960,):
            raise ValueError("Live state/query shape differs")
        return views, query


class CompleteFieldRuntime:
    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        backend: Any,
        deployment_path: Path,
        instant_add_path: Path,
    ) -> None:
        self.backend = backend
        self.deployment_sha256 = sha256_file(deployment_path)
        instant = _json(instant_add_path)
        if self.deployment_sha256 != str(instant["deployment_field_sha256"]):
            raise ValueError("Deployment field SHA differs from instant-add record")
        payload = torch.load(deployment_path, map_location="cpu", weights_only=False)
        self.memory_count = int(payload["memory_count"])
        if self.memory_count != int(settings["first37"]["complete_field_memory_count"]):
            raise ValueError("Complete deployment field memory count differs")
        self.A = payload["A"].to(backend.device, torch.float32)
        self.B = payload["B"].to(backend.device, torch.float32)
        self.shuffled_A = payload["shuffled_A"].to(backend.device, torch.float32)
        self.shuffled_B = payload["shuffled_B"].to(backend.device, torch.float32)
        if tuple(self.A.shape) != (960, 8, 256) or tuple(self.B.shape) != (8, 256):
            raise ValueError("Deployment field shape differs")
        _, self.reader = _build_components(backend.device)
        self.reader.load_state_dict(payload["reader_state_dict"])
        self.reader.eval()
        for parameter in self.reader.parameters():
            parameter.requires_grad_(False)
        self.query_encoder = LiveFieldQueryEncoder(settings=settings, backend=backend)

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        if condition not in {"D1", "D2"}:
            raise ValueError("Complete field runtime only serves D1/D2")
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        A, B = (
            (self.A, self.B)
            if condition == "D1"
            else (self.shuffled_A, self.shuffled_B)
        )
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        return slots, {
            "state_views": views,
            "query": query,
            "query_seconds": query_seconds,
            "field_read_seconds": time.perf_counter() - started,
            "field_control": "correct"
            if condition == "D1"
            else "key_payload_shuffle",
        }


def _tensor_sha256(value: Tensor) -> str:
    work = value.detach().to(device="cpu").contiguous()
    return hashlib.sha256(work.view(torch.uint8).numpy().tobytes()).hexdigest()


def _compact_tensor(value: Tensor, edge: int = 8) -> dict[str, Any]:
    flat = value.detach().to(device="cpu", dtype=torch.float32).flatten()
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "sha256": _tensor_sha256(value),
        "norm": float(flat.norm()),
        "mean": float(flat.mean()),
        "std": float(flat.std(unbiased=False)),
        "minimum": float(flat.min()),
        "maximum": float(flat.max()),
        "first_values": [float(v) for v in flat[:edge]],
        "last_values": [float(v) for v in flat[-edge:]],
    }


def _model_identity(backend: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
    config = backend.model.config
    tokenizer = backend.tokenizer
    init = getattr(tokenizer, "init_kwargs", {})
    return {
        "model_name": str(settings["expected"]["model_name"]),
        "model_config_commit_hash": getattr(config, "_commit_hash", None),
        "model_config_sha256": sha256_text(config.to_json_string()),
        "tokenizer_name_or_path": str(getattr(tokenizer, "name_or_path", "")),
        "tokenizer_commit_hash": init.get("_commit_hash"),
        "tokenizer_revision": init.get("revision"),
        "torch_version": torch.__version__,
        "transformers_version": __import__("transformers").__version__,
    }


def _register_static_asset(
    path: Path,
    messages: Sequence[Mapping[str, str]],
    *,
    prompt_profile: str = "full_demo",
) -> str:
    count = int(appworld_renderer_metadata(prompt_profile)["initial_message_count"])
    static = [dict(row) for row in messages[:count]]
    identity = sha256_text(
        json.dumps(static, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    payload = (
        _json(path)
        if path.exists()
        else {"format": "rcmf_static_prompt_assets_9a_v1", "assets": {}}
    )
    row = {
        "sha256": identity,
        "prompt_profile": prompt_profile,
        "renderer_metadata": appworld_renderer_metadata(prompt_profile),
        "messages": static,
    }
    if identity in payload["assets"] and payload["assets"][identity] != row:
        raise ValueError("Static prompt asset hash collision")
    if identity not in payload["assets"]:
        payload["assets"][identity] = row
        atomic_write_json(path, payload)
    return identity


def _generate(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    max_new_tokens: int,
    reader: Any | None,
    slots: Tensor | None,
    hook_factory: Any = None,
) -> tuple[list[int], str, dict[str, Any]]:
    tokenized = backend.tokenize_messages(list(messages), add_generation_prompt=True)
    prompt_length = int(tokenized.input_ids.shape[1])
    hooks = None
    if reader is not None and slots is not None:
        hooks = (
            FieldReaderHooks(model=backend.model, reader=reader, slots=slots)
            if hook_factory is None
            else hook_factory(model=backend.model, reader=reader, slots=slots)
        )
    started = time.perf_counter()
    with torch.no_grad(), torch.autocast(
        device_type="cuda",
        dtype=torch.bfloat16,
        enabled=backend.device.type == "cuda",
    ), _attention_context(backend.device):
        if hooks is None:
            output = backend.model.generate(
                input_ids=tokenized.input_ids,
                attention_mask=tokenized.attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=backend.tokenizer.eos_token_id,
                eos_token_id=backend.tokenizer.eos_token_id,
            )
            reader_audit = {
                "active": False,
                "bare_generation_has_no_reader_hooks": True,
            }
        else:
            with hooks:
                output = backend.model.generate(
                    input_ids=tokenized.input_ids,
                    attention_mask=tokenized.attention_mask,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                    pad_token_id=backend.tokenizer.eos_token_id,
                    eos_token_id=backend.tokenizer.eos_token_id,
                )
            reader_audit = {"active": True, **hooks.audit.as_dict()}
    if backend.device.type == "cuda":
        torch.cuda.synchronize()
    ids = [int(v) for v in output[0, prompt_length:].tolist()]
    return ids, backend.tokenizer.decode(ids, skip_special_tokens=True), {
        "reader": reader_audit,
        "generation_seconds": time.perf_counter() - started,
        "prompt_tokens": prompt_length,
        "completion_tokens": len(ids),
        "total_tokens": prompt_length + len(ids),
    }


def _task_output(
    paths: Mapping[str, Path], condition: str, task_id: str, smoke: bool
) -> Path:
    return _condition_root(paths, condition, smoke) / "task_results" / f"{task_id}.json"


def _run_task(
    *,
    task_id: str,
    condition: str,
    settings: Mapping[str, Any],
    backend: Any,
    runtime: CompleteFieldRuntime | None,
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    config_sha256: str,
    attempt_id: str,
    smoke: bool,
    hook_factory: Any = None,
    result_version: str = RESULT_FORMAT,
    extra_result_fields: Mapping[str, Any] | None = None,
    bare_condition: bool | None = None,
    condition_name: str | None = None,
    memory_count: int | None = None,
    field_artifact_path: Path | None = None,
    field_provenance_path: Path | None = None,
    max_steps_override: int | None = None,
    experiment_prefix: str = "exp031a",
    field_control_condition: str | None = None,
    collect_resource_metrics: bool = False,
) -> tuple[dict[str, Any], bool]:
    output = _task_output(paths, condition, task_id, smoke)
    is_bare = condition == "D0" if bare_condition is None else bool(bare_condition)
    resolved_memory_count = (
        0 if is_bare else 499 if memory_count is None else int(memory_count)
    )
    active_field_path = (
        paths["deployment"] if field_artifact_path is None else field_artifact_path
    )
    deployment_sha = sha256_file(active_field_path)
    display_name = CONDITIONS[condition] if condition_name is None else condition_name
    provenance_path = (
        paths["instant_add"]
        if field_provenance_path is None
        else field_provenance_path
    )
    if output.exists():
        row = _json(output)
        checks = {
            "format": row.get("format") == result_version,
            "task": str(row.get("task_id")) == task_id,
            "condition": str(row.get("condition")) == condition,
            "config": str(row.get("config_sha256")) == config_sha256,
            "manifest": str(row.get("condition_manifest_sha256"))
            == str(manifest["manifest_sha256"]),
            "deployment": str(row.get("deployment_field_sha256")) == deployment_sha,
            "complete": row.get("status") == "complete",
            "smoke": bool(row.get("non_scientific_smoke")) == smoke,
        }
        if not all(checks.values()):
            raise ValueError(f"Existing first37 task row differs: {checks}")
        return row, True

    app = settings["appworld"]
    root = _condition_root(paths, condition, smoke)
    restart = len(list((root / "worker_logs").glob(f"{task_id}.*.stderr.log")))
    experiment_name = (
        f"{experiment_prefix}_{'smoke_' if smoke else ''}{condition.lower()}_"
        f"{attempt_id}_{task_id}_r{restart:02d}"
    )
    worker_log = root / "worker_logs" / f"{task_id}.{restart:02d}.stderr.log"
    started = time.perf_counter()
    trajectory: list[dict[str, str]] = []
    steps: list[dict[str, Any]] = []
    usage, counts = Counter(), Counter()
    previous_code: str | None = None
    terminal_error: dict[str, Any] | None = None
    model_identity = _model_identity(backend, settings)
    max_steps = (
        int(max_steps_override)
        if max_steps_override is not None
        else 2 if smoke else int(app["max_steps"])
    )
    gpu_memory_start: dict[str, int] | None = None
    if collect_resource_metrics and backend.device.type == "cuda":
        torch.cuda.synchronize(backend.device)
        torch.cuda.reset_peak_memory_stats(backend.device)
        gpu_memory_start = {
            "allocated_bytes": int(torch.cuda.memory_allocated(backend.device)),
            "reserved_bytes": int(torch.cuda.memory_reserved(backend.device)),
        }

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
                "max_interactions": max_steps,
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
        for step_id in range(1, max_steps + 1):
            trajectory_before = copy.deepcopy(trajectory)
            messages = build_appworld_messages(
                task_message=task_message,
                trajectory_so_far=trajectory,
                prompt_profile=str(app["prompt_profile"]),
                max_context_turns=int(app["max_context_turns"]),
            )
            static_sha = _register_static_asset(
                paths["static_assets"],
                messages,
                prompt_profile=str(app["prompt_profile"]),
            )
            rendered = backend.tokenizer.apply_chat_template(
                list(messages), tokenize=False, add_generation_prompt=True
            )
            tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
            prompt_tokens = int(tokenized.attention_mask.sum().item())
            remaining = int(app["context_limit"]) - prompt_tokens
            if remaining <= 0:
                terminal_error = {
                    "category": "locked_context_overflow_no_truncation",
                    "step_id": step_id,
                    "prompt_tokens": prompt_tokens,
                    "context_limit": int(app["context_limit"]),
                    "scientific_outcome": "task_failure",
                }
                counts["context_overflow"] += 1
                break

            if is_bare:
                views, query = None, None
                slots = torch.zeros(8, 256, device=backend.device)
                field_info = {                    "query_status": "not_computed_bare_condition",
                    "field_control": "zero",
                    "query_seconds": 0.0,
                    "field_read_seconds": 0.0,
                }
                reader = None
            else:
                if runtime is None:
                    raise RuntimeError("Non-bare conditions require a field runtime")
                runtime_condition = (
                    condition
                    if field_control_condition is None
                    else field_control_condition
                )
                slots, field_info = runtime.read(messages, runtime_condition)
                views = field_info.pop("state_views")
                query = field_info.pop("query")
                reader = runtime.reader

            tensor_path = (
                root
                / "step_tensors"
                / task_id
                / f"restart_{restart:02d}_step_{step_id:02d}.pt"
            )
            _atomic_torch_save(
                {
                    "format": "rcmf_first37_step_field_tensor_9a_v1",
                    "condition": condition,
                    "task_id": task_id,
                    "step_id": step_id,
                    "state_views": None if views is None else views.detach().cpu(),
                    "query": None if query is None else query.detach().cpu(),
                    "slots": slots.detach().cpu(),
                    "deployment_field_sha256": deployment_sha,
                    "memory_count": resolved_memory_count,
                },
                tensor_path,
            )
            slot_info = {
                **field_info,
                "state_views": None if views is None else _compact_tensor(views),
                "query": None if query is None else _compact_tensor(query),
                "slots": _compact_tensor(slots),
                "tensor_artifact": str(tensor_path),
                "tensor_artifact_sha256": sha256_file(tensor_path),
                "deployment_field": None
                if is_bare
                else str(active_field_path),
                "deployment_field_sha256": None
                if is_bare
                else deployment_sha,
                "complete_bank_memory_count": resolved_memory_count,
                "runtime_memory_retrieval": False,
                "runtime_per_memory_scoring": False,
                "top_memory_contributions": {
                    "status": "pending_offline_post_run_audit",
                    "not_used_by_model_or_field_read": True,
                },
                "instant_add_report": (
                    str(paths["instant_add"])
                    if field_provenance_path is None
                    else None
                ),
                "instant_add_report_sha256": (
                    sha256_file(paths["instant_add"])
                    if field_provenance_path is None
                    else None
                ),
                "field_provenance": str(provenance_path),
                "field_provenance_sha256": sha256_file(provenance_path),
            }
            ids, raw_response, generation = _generate(
                backend=backend,
                messages=messages,
                max_new_tokens=min(int(app["max_new_tokens"]), remaining),
                reader=reader,
                slots=slots if reader is not None else None,
                hook_factory=hook_factory,
            )
            code, fixed = extract_code_and_fix_content(raw_response)
            execution_started = time.perf_counter()
            executed = bridge.execute(
                nonce=str(ready["ready_nonce"]), step_id=step_id, code=code
            )
            execution_seconds = time.perf_counter() - execution_started
            observation = str(executed["raw_observation"])
            trajectory.append({"response": fixed, "observation": observation})
            usage.update(
                {
                    "prompt_tokens": int(generation["prompt_tokens"]),
                    "completion_tokens": int(generation["completion_tokens"]),
                    "total_tokens": int(generation["total_tokens"]),
                }
            )
            exception = executed["execution_exception"] is not None
            repeated_action = previous_code == code
            repeated_invalid = bool(exception and repeated_action)
            counts["execution_exception"] += int(exception)
            counts["completion_action"] += int("apis.supervisor.complete_task" in code)
            counts["premature_completion"] += int(
                "apis.supervisor.complete_task" in code
                and not bool(executed["task_completed"])
            )
            counts["repeated_action"] += int(repeated_action)
            counts["repeated_invalid_action"] += int(repeated_invalid)
            previous_code = code
            step_row = {
                "format": "rcmf_detailed_generation_interaction_step_9a_v1",
                "condition": condition,
                "task_id": task_id,
                "step_id": step_id,
                "prompt_profile": str(app["prompt_profile"]),
                "renderer_version": appworld_renderer_metadata(
                    str(app["prompt_profile"])
                ),
                "static_prompt_asset_sha256": static_sha,
                "current_task_message": task_message,
                "complete_trajectory_so_far": trajectory_before,
                "exact_model_message_array": [dict(row) for row in messages],
                "rendered_message_sha256": sha256_text(rendered),
                "prompt_tokens": prompt_tokens,
                "context_limit": int(app["context_limit"]),
                "truncation_applied": False,
                "context_decision": "full_prompt_no_truncation",
                "model_identity": model_identity,
                "generation_config": {
                    "seed": GLOBAL_SEED,
                    "temperature": float(app["temperature"]),
                    "top_p": float(app["top_p"]),
                    "do_sample": bool(app["do_sample"]),
                    "enable_thinking": bool(app["enable_thinking"]),
                    "max_new_tokens": min(int(app["max_new_tokens"]), remaining),
                },
                "raw_model_response": raw_response,
                "generated_token_ids": ids,
                "extracted_code": code,
                "automatically_repaired_response": fixed,
                "automatically_repaired_code": code,
                "exact_executed_code": code,
                "execution_exception": executed["execution_exception"],
                "complete_environment_observation": observation,
                "locked_normalized_observation": executed.get(
                    "locked_normalized_observation"
                ),
                "task_completed_status": bool(executed["task_completed"]),
                "same_world_execution": bool(executed["same_world_execution"]),
                "same_python_namespace": bool(executed["same_python_namespace"]),
                "state_fingerprint_before": executed.get("state_before"),
                "state_fingerprint_after": executed.get("state_after"),
                "field": slot_info,
                "reader_audit": generation["reader"],
                "usage": {
                    "prompt_tokens": generation["prompt_tokens"],
                    "completion_tokens": generation["completion_tokens"],
                    "total_tokens": generation["total_tokens"],
                },
                "generation_seconds": generation["generation_seconds"],
                "repeated_action": repeated_action,
                "repeated_invalid_action": repeated_invalid,                "termination_after_step": "task_completed"
                if bool(executed["task_completed"])
                else "continue",
            }
            if collect_resource_metrics:
                step_row["environment_execution_seconds"] = execution_seconds
            atomic_write_json(
                root
                / "raw_steps"
                / task_id
                / f"restart_{restart:02d}_step_{step_id:02d}.json",
                step_row,
            )
            steps.append(step_row)
            if bool(executed["task_completed"]):
                break
        evaluator_started = time.perf_counter()
        final = bridge.finish(nonce=str(ready["ready_nonce"]))
        evaluator_seconds = time.perf_counter() - evaluator_started

    resource_metrics: dict[str, Any] | None = None
    if collect_resource_metrics:
        if backend.device.type == "cuda":
            torch.cuda.synchronize(backend.device)
            resource_metrics = {
                "device": str(backend.device),
                "initial_allocated_bytes": int(gpu_memory_start["allocated_bytes"]),
                "initial_reserved_bytes": int(gpu_memory_start["reserved_bytes"]),
                "peak_allocated_bytes": int(
                    torch.cuda.max_memory_allocated(backend.device)
                ),
                "peak_reserved_bytes": int(
                    torch.cuda.max_memory_reserved(backend.device)
                ),
                "final_allocated_bytes": int(torch.cuda.memory_allocated(backend.device)),
                "final_reserved_bytes": int(torch.cuda.memory_reserved(backend.device)),
                "evaluator_seconds": evaluator_seconds,
            }
        else:
            resource_metrics = {
                "device": str(backend.device),
                "initial_allocated_bytes": 0,
                "initial_reserved_bytes": 0,
                "peak_allocated_bytes": 0,
                "peak_reserved_bytes": 0,
                "final_allocated_bytes": 0,
                "final_reserved_bytes": 0,
                "evaluator_seconds": evaluator_seconds,
            }

    row = {
        "format": result_version,
        "status": "complete",
        "non_scientific_smoke": smoke,
        "condition": condition,
        "condition_name": display_name,
        "task_id": task_id,
        "experiment_name": experiment_name,
        "global_seed": GLOBAL_SEED,
        "config_sha256": config_sha256,
        "condition_manifest_sha256": str(manifest["manifest_sha256"]),
        "deployment_field_sha256": deployment_sha,
        "complete_bank_memory_count": resolved_memory_count,
        "student_prompt_contains_raw_memory": False,
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
        "query_encoder_sha256": None
        if runtime is None
        else runtime.query_encoder.identity_sha256,
        "model_identity": model_identity,
        "steps": steps,
        "step_count": len(steps),
        "usage": dict(usage),
        "counts": dict(counts),
        "terminal_error": terminal_error,
        "success": bool(final["success"]),
        "success_source": "evaluation.success",
        "evaluation": final["evaluation"],
        "wall_seconds": time.perf_counter() - started,
        "raw_audit_complete": True,
        **dict(extra_result_fields or {}),
    }
    if resource_metrics is not None:
        row["resource_metrics"] = resource_metrics
    atomic_write_json(output, row)
    return row, False


def summarize_condition(
    rows: Sequence[Mapping[str, Any]], condition: str
) -> dict[str, Any]:
    success = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    counts, delta_norms, query_seconds, read_seconds = Counter(), [], [], []
    for row in rows:
        counts.update(row["counts"])
        for step in row["steps"]:
            query_seconds.append(float(step["field"]["query_seconds"]))
            read_seconds.append(float(step["field"]["field_read_seconds"]))
            for values in step["reader_audit"].get("delta_norms", {}).values():
                delta_norms.extend(float(value) for value in values)
    return {
        "format": SUMMARY_FORMAT,
        "condition": condition,
        "condition_name": CONDITIONS[condition],
        "task_count": len(rows),
        "success_count": len(success),
        "success_ids": success,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_prompt_tokens": sum(
            int(row["usage"].get("prompt_tokens", 0)) for row in rows
        ),
        "total_generated_tokens": sum(
            int(row["usage"].get("completion_tokens", 0)) for row in rows
        ),
        "counts": dict(counts),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "mean_query_seconds": statistics.fmean(query_seconds),
        "mean_field_read_seconds": statistics.fmean(read_seconds),
        "mean_reader_delta_norm": statistics.fmean(delta_norms)
        if delta_norms
        else 0.0,
        "student_prompt_contains_raw_memory": False,
        "runtime_memory_retrieval": False,
        "runtime_per_memory_scoring": False,
        "single_seed_descriptive_not_statistical": True,
        "passed_infrastructure": len(rows) == 37
        and all(row["status"] == "complete" for row in rows),
    }


def _first_divergence(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any] | None:
    left_steps, right_steps = list(left["steps"]), list(right["steps"])
    for index in range(max(len(left_steps), len(right_steps))):
        if index >= len(left_steps) or index >= len(right_steps):
            return {
                "step_id": index + 1,
                "reason": "trajectory_length",
                "left_steps": len(left_steps),
                "right_steps": len(right_steps),
            }
        lrow, rrow = left_steps[index], right_steps[index]
        differing = [
            key
            for key in (
                "raw_model_response",
                "exact_executed_code",
                "complete_environment_observation",
                "task_completed_status",
            )
            if lrow.get(key) != rrow.get(key)
        ]
        if differing:
            return {"step_id": index + 1, "reason": "step_content", "fields": differing}
    if bool(left["success"]) != bool(right["success"]):
        return {"step_id": None, "reason": "evaluation_success"}
    return None


def classify_first37(d0: int, d1: int, d2: int) -> dict[str, Any]:
    if d1 > d0 and d1 >= d2 + 2:
        return {
            "interpretation": "PRELIMINARY_POSITIVE",
            "decision_branch": "rcmf_full_field_preliminary_positive",
        }
    if d1 > d2 and d1 <= d0 - 2:
        return {
            "interpretation": "PARTIAL",
            "decision_branch": "rcmf_field_partial_live_signal",
        }
    if d1 > d2:
        return {
            "interpretation": "LIVE_MEMORY_SPECIFIC_SIGNAL",
            "decision_branch": "rcmf_full_field_live_memory_specific_signal",
        }
    return {
        "interpretation": "CLEAR_FAILURE",
        "decision_branch": "rcmf_field_not_live_memory_specific",
    }


def _finalize(paths: Mapping[str, Path], task_ids: Sequence[str]) -> dict[str, Any]:
    summaries = {
        c: _json(_condition_root(paths, c, False) / "summary.json")
        for c in CONDITIONS
    }
    tasks = {
        c: {task: _json(_task_output(paths, c, task, False)) for task in task_ids}
        for c in CONDITIONS
    }
    divergences = {
        task: {
            "D0_vs_D1": _first_divergence(tasks["D0"][task], tasks["D1"][task]),
            "D1_vs_D2": _first_divergence(tasks["D1"][task], tasks["D2"][task]),
            "D0_success_D1_failure": bool(tasks["D0"][task]["success"])
            and not bool(tasks["D1"][task]["success"]),
        }
        for task in task_ids
    }
    counts = {c: int(row["success_count"]) for c, row in summaries.items()}
    payload = {
        "format": FINAL_FORMAT,
        "global_seed": GLOBAL_SEED,
        "summaries": summaries,
        "success_count": counts,
        "D1_minus_D0": counts["D1"] - counts["D0"],
        "D1_minus_D2": counts["D1"] - counts["D2"],
        "D2_minus_D0": counts["D2"] - counts["D0"],
        "first_divergence": divergences,
        **classify_first37(counts["D0"], counts["D1"], counts["D2"]),
        "single_seed_development_diagnostic": True,
        "behavioral_conclusion_status": "pending_committed_detailed_audit",
    }
    atomic_write_json(paths["final"], payload)
    return payload


def _preflight(    settings: Mapping[str, Any], paths: Mapping[str, Path], config_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    for name in (
        "deployment",
        "instant_add",
        "selection",
        "source_cache",
        "data_manifest",
        "shuffle",
    ):
        if not paths[name].exists():
            raise FileNotFoundError(paths[name])
    instant = _json(paths["instant_add"])
    if not bool(instant["passed"]):
        raise RuntimeError("Instant-add gate did not pass")
    deployment_sha = sha256_file(paths["deployment"])
    if deployment_sha != str(instant["deployment_field_sha256"]):
        raise ValueError("Deployment SHA differs")
    deployment = torch.load(paths["deployment"], map_location="cpu", weights_only=False)
    memory_count = int(deployment["memory_count"])
    tasks = _task_ids(settings)
    manifest = build_first37_manifest(
        task_ids=tasks,
        deployment_sha256=deployment_sha,
        memory_count=memory_count,
    )
    if int(manifest["logical_condition_count"]) != int(
        settings["first37"]["expected_condition_count"]
    ):
        raise ValueError("First37 condition accounting differs")
    bare = float(settings["first37"]["measured_bare_h100_hours"])
    expected = bare * (
        1 + 2 * float(settings["first37"]["correct_field_multiplier_expected"])
    )
    conservative = bare * (
        1 + 2 * float(settings["first37"]["correct_field_multiplier_conservative"])
    )
    threshold = float(settings["runtime"]["review_threshold_h100_hours"])
    report = {
        "format": "rcmf_joint_full_bank_first37_runtime_preflight_9a_v1",
        "global_seed": GLOBAL_SEED,
        "task_ids": tasks,
        "task_count": len(tasks),
        "condition_count": int(manifest["logical_condition_count"]),
        "D0_generation_count": 37,
        "D1_generation_count": 37,
        "D2_generation_count": 37,
        "complete_field_memory_count": memory_count,
        "field_shape": {
            "A": list(deployment["A"].shape),
            "B": list(deployment["B"].shape),
        },
        "field_bytes": int(
            deployment["A"].numel() * deployment["A"].element_size()
            + deployment["B"].numel() * deployment["B"].element_size()
        ),
        "expected_h100_hours": expected,
        "conservative_h100_hours": conservative,
        "review_threshold_h100_hours": threshold,
        "automatic_launch_allowed": expected <= threshold
        and conservative <= threshold,
        "hardware": torch.cuda.get_device_name(0)
        if torch.cuda.is_available()
        else "unavailable",
        "expected_hardware": str(settings["runtime"]["hardware_required"]),
        "expected_git_safe_audit_bytes": int(
            settings["first37"]["expected_git_safe_audit_bytes"]
        ),
        "expected_lambda_raw_audit_bytes": int(
            settings["first37"]["expected_lambda_raw_audit_bytes"]
        ),
        "checkpoint_restart_plan": {
            "atomic_task_results": True,
            "atomic_per_step_raw_rows": True,
            "atomic_per_step_field_tensors": True,
            "append_only_attempts": True,
            "resume_reuses_only_hash_valid_complete_tasks": True,
            "incomplete_tasks_restart_in_fresh_world": True,
        },
        "D0_exact_bare_no_query_or_hook": True,
        "D1_D2_fixed_field_only": True,
        "runtime_memory_scan_or_retrieval": False,
        "config_sha256": sha256_file(config_path),
        "deployment_field_sha256": deployment_sha,
        "instant_add_report_sha256": sha256_file(paths["instant_add"]),
        "supersedes_preflight_due_to_verified_selector_sha_typo": (
            str(paths["preflight_v1"]) if paths["preflight_v1"].exists() else None
        ),
    }
    if not report["automatic_launch_allowed"]:
        raise RuntimeError(f"First37 runtime requires review: {report}")
    return manifest, report


def _ledger(
    *,
    args: argparse.Namespace,
    settings: Mapping[str, Any],
    phase: str,
    hashes: Mapping[str, str],
) -> AttemptLedger:
    return AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=phase,
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=dict(hashes),
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9a"]
    paths = _paths(args.artifact_dir)
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-031A requires seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    tasks = _task_ids(settings)
    hashes = {
        "config": sha256_file(args.config),
        "deployment": sha256_file(paths["deployment"]),
        "instant_add": sha256_file(paths["instant_add"]),
        "selection": sha256_file(paths["selection"]),
    }

    if args.phase == "preflight":
        manifest, report = _preflight(settings, paths, args.config)
        with _ledger(
            args=args,
            settings=settings,
            phase="joint_full_bank_first37_preflight",
            hashes=hashes,
        ) as attempt:
            _write_or_validate_json(paths["manifest"], manifest)
            _write_or_validate_json(paths["preflight"], report)
            attempt.progress(
                status="first37_preflight_complete",
                latest_validated_checkpoint=str(paths["preflight"]),
                result=report,
            )
        print(json.dumps(report, sort_keys=True))
        return

    if not paths["preflight"].exists() or not paths["manifest"].exists():
        raise RuntimeError("First37 preflight/manifest is missing")
    preflight, manifest = _json(paths["preflight"]), _json(paths["manifest"])
    if not bool(preflight["automatic_launch_allowed"]):
        raise RuntimeError("First37 automatic launch was not authorized")
    hashes["manifest"] = sha256_file(paths["manifest"])

    if args.phase == "finalize":
        with _ledger(
            args=args,
            settings=settings,
            phase="joint_full_bank_first37_finalize",
            hashes=hashes,
        ) as attempt:
            result = _finalize(paths, tasks)
            attempt.progress(
                status="first37_finalize_complete",
                latest_validated_checkpoint=str(paths["final"]),
                result=result,
            )
        print(json.dumps(result, sort_keys=True))
        return

    if args.condition is None:
        raise ValueError("--condition is required for smoke/run")
    smoke = args.phase == "smoke"
    selected_tasks = tasks[:1] if smoke else tasks
    backend = _build_backend(cfg)
    if hasattr(backend.model, "gradient_checkpointing_disable"):
        backend.model.gradient_checkpointing_disable()
    backend.model.config.use_cache = True
    backend.model.eval()
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("First37 loaded trainable Qwen")
    runtime = (
        None
        if args.condition == "D0"
        else CompleteFieldRuntime(
            settings=settings,
            backend=backend,
            deployment_path=paths["deployment"],
            instant_add_path=paths["instant_add"],
        )
    )
    if runtime is not None:
        hashes["query_encoder"] = runtime.query_encoder.identity_sha256

    with _ledger(
        args=args,
        settings=settings,
        phase=f"joint_full_bank_first37_{args.phase}_{args.condition.lower()}",
        hashes=hashes,
    ) as attempt:
        rows, resumed = [], 0
        for task in selected_tasks:
            row, reused = _run_task(
                task_id=task,
                condition=args.condition,
                settings=settings,
                backend=backend,
                runtime=runtime,
                paths=paths,
                manifest=manifest,
                config_sha256=sha256_file(args.config),
                attempt_id=args.attempt_id,
                smoke=smoke,
            )
            rows.append(row)
            resumed += int(reused)
            attempt.progress(
                status=f"first37_{args.phase}_{args.condition.lower()}",
                completed_tasks=len(rows),
                total_tasks=len(selected_tasks),
                resumed_tasks=resumed,
                latest_validated_checkpoint=str(
                    _task_output(paths, args.condition, task, smoke)
                ),
            )
            print(
                f"{args.condition} task={task} success={row['success']} "
                f"steps={row['step_count']} reused={reused}",
                flush=True,
            )
        summary = summarize_condition(rows, args.condition)
        if smoke:
            summary["passed_infrastructure"] = (
                len(rows) == 1 and all(row["status"] == "complete" for row in rows)
            )
        summary.update(
            {
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "non_scientific_smoke": smoke,
                "condition_manifest_sha256": str(manifest["manifest_sha256"]),
                "deployment_field_sha256": sha256_file(paths["deployment"]),
                "query_encoder_sha256": None
                if runtime is None
                else runtime.query_encoder.identity_sha256,
                "new_task_count": len(rows) - resumed,
                "resumed_task_count": resumed,
            }
        )
        summary_path = _condition_root(paths, args.condition, smoke) / "summary.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(
            summary_path.with_suffix(".md"),
            "\n".join(
                [
                    f"# EXP-031A {args.condition} first37",
                    "",
                    f"- success: {summary['success_count']}/{len(rows)}",
                    f"- steps: {summary['total_steps']}",
                    f"- runtime retrieval: {summary['runtime_memory_retrieval']}",
                    f"- non-scientific smoke: {smoke}",
                    "",
                ]
            ),
        )
        attempt.progress(
            status=f"first37_{args.phase}_{args.condition.lower()}_complete",
            latest_validated_checkpoint=str(summary_path),
            result=summary,
        )
    assert_frozen_without_gradients(backend.model)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
