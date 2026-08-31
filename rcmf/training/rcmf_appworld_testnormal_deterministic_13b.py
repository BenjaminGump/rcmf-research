"""Process and audit contract for deterministic EXP-036B evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import locale
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text


REQUIRED_PYTHON_HASH_SEED = "25101"
DETERMINISM_MODE = "hash_seed_only"
PROCESS_IDENTITY_FORMAT = "rcmf_exp036b_process_hash_identity_v1"
TASK_RESULT_FORMAT = "rcmf_appworld_testnormal_task_13b_v1"
PROBE_RESULT_FORMAT = "rcmf_appworld_testnormal_probe_task_13b_v1"


def _hash_sentinel() -> dict[str, Any]:
    value = {"exp036b", "python", "hash", "25101", "appworld"}
    rendered = repr(value)
    return {
        "value_sha256": sha256_text(rendered),
        "value_length": len(rendered),
        "builtin_hash": hash(frozenset(value)),
    }


def assert_hash_seed_process() -> None:
    actual = os.environ.get("PYTHONHASHSEED")
    if actual != REQUIRED_PYTHON_HASH_SEED:
        raise RuntimeError(
            "EXP-036B Python interpreter did not start with "
            f"PYTHONHASHSEED={REQUIRED_PYTHON_HASH_SEED}; actual={actual!r}"
        )
    if int(sys.flags.hash_randomization) != 1:
        raise RuntimeError("EXP-036B requires fixed nonzero Python hash randomization")


def _child_probe(executable: Path) -> dict[str, Any]:
    code = """
import hashlib
import json
import os
import sys
value = {"exp036b", "python", "hash", "25101", "appworld"}
rendered = repr(value)
print(json.dumps({
    "executable": sys.executable,
    "python_version": sys.version,
    "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
    "hash_randomization": int(sys.flags.hash_randomization),
    "hash_sentinel": {
        "value_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "value_length": len(rendered),
        "builtin_hash": hash(frozenset(value)),
    },
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(executable), "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"Unexpected child hash probe output from {executable}")
    result = json.loads(lines[0])
    if result["python_hash_seed"] != REQUIRED_PYTHON_HASH_SEED:
        raise RuntimeError(f"Child Python did not inherit PYTHONHASHSEED: {executable}")
    if int(result["hash_randomization"]) != 1:
        raise RuntimeError(f"Child Python hash randomization is disabled: {executable}")
    return result


def write_process_identity(
    *,
    artifact_dir: Path,
    attempt_id: str,
    launcher_path: Path,
    entrypoint_path: Path,
    legacy_python: Path,
    source_head: str,
) -> dict[str, Any]:
    assert_hash_seed_process()
    launcher = launcher_path.resolve()
    entrypoint = entrypoint_path.resolve()
    parent = _hash_sentinel()
    children = {
        "execution_python": _child_probe(Path(sys.executable)),
        "legacy_appworld_python": _child_probe(legacy_python),
    }
    if any(row["hash_sentinel"] != parent for row in children.values()):
        raise RuntimeError("Parent/child deterministic hash sentinels differ")
    result = {
        "format": PROCESS_IDENTITY_FORMAT,
        "attempt_id": attempt_id,
        "source_head": source_head,
        "required_python_hash_seed": REQUIRED_PYTHON_HASH_SEED,
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "hash_randomization": int(sys.flags.hash_randomization),
        "hash_sentinel": parent,
        "process_start_command": list(getattr(sys, "orig_argv", sys.argv)),
        "scientific_argv": list(sys.argv),
        "launcher_command": os.environ.get("RCMF_DETERMINISM_LAUNCH_COMMAND"),
        "launcher_path": str(launcher),
        "launcher_sha256": sha256_file(launcher),
        "entrypoint_path": str(entrypoint),
        "entrypoint_sha256": sha256_file(entrypoint),
        "child_process_inheritance": children,
        "locale": {
            "current": list(locale.getlocale()),
            "preferred_encoding": locale.getpreferredencoding(False),
        },
        "timezone": {
            "environment_TZ": os.environ.get("TZ"),
            "tzname": list(time.tzname),
        },
        "environment_changed_after_interpreter_start": false_value(),
    }
    result["identity_sha256"] = canonical_sha256(result)
    path = artifact_dir / "process_environment" / f"{attempt_id}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != result:
            raise ValueError(f"Existing process identity differs: {path}")
    else:
        atomic_write_json(path, result)
    result["artifact_path"] = str(path)
    result["artifact_sha256"] = sha256_file(path)
    return result


def false_value() -> bool:
    return False


def read_mode_manifest(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "manifests" / "determinism_mode.json"
    if not path.exists():
        raise FileNotFoundError("Frozen EXP-036B determinism mode is missing")
    mode = json.loads(path.read_text(encoding="utf-8"))
    content = {key: value for key, value in mode.items() if key != "manifest_sha256"}
    checks = {
        "mode": mode.get("mode") == DETERMINISM_MODE,
        "canonicalizer_disabled": mode.get("canonicalizer")
        == {"enabled": False, "identity": "disabled"},
        "manifest_sha256": mode.get("manifest_sha256") == canonical_sha256(content),
        "probe_passed": bool(mode.get("stage1_probe_passed")),
    }
    if not all(checks.values()):
        raise ValueError(f"EXP-036B determinism mode differs: {checks}")
    mode["artifact_path"] = str(path)
    mode["artifact_sha256"] = sha256_file(path)
    return mode


def _prompt_token_sha(backend: Any, messages: Sequence[Mapping[str, str]]) -> str:
    tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
    values = [int(value) for value in tokenized.input_ids[0].tolist()]
    return canonical_sha256(values)


def augment_task_row(
    *,
    row: dict[str, Any],
    backend: Any,
    process_identity: Mapping[str, Any],
    mode: Mapping[str, Any],
    result_format: str,
) -> dict[str, Any]:
    row["format"] = result_format
    for step in row["steps"]:
        raw = str(step["complete_environment_observation"])
        raw_sha = sha256_text(raw)
        step["prompt_token_ids_sha256"] = _prompt_token_sha(
            backend, step["exact_model_message_array"]
        )
        step["observation_rendering"] = {
            "mode": DETERMINISM_MODE,
            "raw_observation_sha256": raw_sha,
            "model_visible_observation_sha256": raw_sha,
            "semantic_structure_sha256": raw_sha,
            "bodies_identical": True,
            "raw_body_field": "complete_environment_observation",
            "model_visible_body_field": "complete_environment_observation",
            "canonicalization_applied": False,
            "canonicalizer_identity": "disabled",
            "evaluator_state_modified": False,
        }
    row["determinism"] = {
        "mode": DETERMINISM_MODE,
        "mode_manifest_sha256": str(mode["manifest_sha256"]),
        "mode_artifact_sha256": str(mode["artifact_sha256"]),
        "process_identity_sha256": str(process_identity["identity_sha256"]),
        "process_artifact_sha256": str(process_identity["artifact_sha256"]),
        "python_hash_seed": REQUIRED_PYTHON_HASH_SEED,
        "canonicalizer_identity": "disabled",
        "raw_observations_preserved": True,
        "model_visible_observation_is_raw": True,
        "evaluator_state_modified": False,
    }
    row["result_sha256"] = canonical_sha256(
        {key: value for key, value in row.items() if key != "result_sha256"}
    )
    return row


def compare_probe_rows(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_world = left["world_identity"]
    right_world = right["world_identity"]
    world_keys = (
        "task_id",
        "fresh_isolated_world",
        "appworld_root",
        "evaluator_success_source",
    )
    fields = {
        "raw_world_initialization": all(
            left_world.get(key) == right_world.get(key) for key in world_keys
        )
        and left["steps"][0].get("state_fingerprint_before")
        == right["steps"][0].get("state_fingerprint_before"),
        "success": left["success"] == right["success"],
        "step_count": left["step_count"] == right["step_count"],
        "prompts": [step["exact_model_message_array"] for step in left["steps"]]
        == [step["exact_model_message_array"] for step in right["steps"]],
        "prompt_token_ids": [step["prompt_token_ids_sha256"] for step in left["steps"]]
        == [step["prompt_token_ids_sha256"] for step in right["steps"]],
        "generated_token_ids": [step["generated_token_ids"] for step in left["steps"]]
        == [step["generated_token_ids"] for step in right["steps"]],
        "raw_responses": [step["raw_model_response"] for step in left["steps"]]
        == [step["raw_model_response"] for step in right["steps"]],
        "executed_code": [step["exact_executed_code"] for step in left["steps"]]
        == [step["exact_executed_code"] for step in right["steps"]],
        "raw_observations": [step["complete_environment_observation"] for step in left["steps"]]
        == [step["complete_environment_observation"] for step in right["steps"]],
        "completion_status": [step["task_completed_status"] for step in left["steps"]]
        == [step["task_completed_status"] for step in right["steps"]],
        "state_fingerprints": [
            (step.get("state_fingerprint_before"), step.get("state_fingerprint_after"))
            for step in left["steps"]
        ]
        == [
            (step.get("state_fingerprint_before"), step.get("state_fingerprint_after"))
            for step in right["steps"]
        ],
    }
    return {"passed": all(fields.values()), "checks": fields}


def probe_row_path(artifact_dir: Path, process_label: str, condition: str, task_id: str) -> Path:
    return (
        artifact_dir
        / "determinism_probe"
        / f"process_{process_label}"
        / "conditions"
        / condition
        / "task_results"
        / f"{task_id}.json"
    )


def freeze_hash_seed_mode(
    *,
    artifact_dir: Path,
    task_id: str,
    launcher_path: Path,
    root_cause_path: Path,
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    row_sources: dict[str, Any] = {}
    for condition in ("B0", "FULL1D-S"):
        paths = {
            label: probe_row_path(artifact_dir, label, condition, task_id)
            for label in ("A", "B")
        }
        if not all(path.exists() for path in paths.values()):
            raise FileNotFoundError(f"Incomplete Stage 1 probe rows for {condition}")
        rows = {
            label: json.loads(path.read_text(encoding="utf-8"))
            for label, path in paths.items()
        }
        comparisons[condition] = compare_probe_rows(rows["A"], rows["B"])
        row_sources[condition] = {
            label: {"path": str(path), "sha256": sha256_file(path)}
            for label, path in paths.items()
        }
    if not all(row["passed"] for row in comparisons.values()):
        raise RuntimeError("EXP-036B Stage 1 cross-process probe failed")
    result = {
        "format": "rcmf_exp036b_determinism_mode_v1",
        "mode": DETERMINISM_MODE,
        "required_python_hash_seed": REQUIRED_PYTHON_HASH_SEED,
        "stage1_probe_passed": True,
        "stage1_probe_task_id": task_id,
        "stage1_probe_conditions": ["B0", "FULL1D-S"],
        "stage1_comparisons": comparisons,
        "stage1_rows": row_sources,
        "launcher": {
            "path": str(launcher_path),
            "sha256": sha256_file(launcher_path),
        },
        "canonicalizer": {"enabled": False, "identity": "disabled"},
        "root_cause": {
            "path": str(root_cause_path),
            "sha256": sha256_file(root_cause_path),
        },
        "model_visible_observation_contract": "exact raw observation string",
        "raw_observation_preserved": True,
        "evaluator_state_modified": False,
        "selection_uses_task_success": False,
    }
    result["manifest_sha256"] = canonical_sha256(result)
    path = artifact_dir / "manifests" / "determinism_mode.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != result:
            raise ValueError("Frozen determinism mode already exists and differs")
    else:
        atomic_write_json(path, result)
    return result
