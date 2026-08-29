"""Freeze the EXP-033A one-demo prompt, official dev manifest, and leakage audits."""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from prompt import AGENT_SYSTEM_PROMPT_TEMPLATE_AW
from rcmf.benchmarks.appworld.prompt import (
    FULL_DEMO_FIRST_ONLY_PROFILE,
    appworld_renderer_metadata,
    full_demo_sections,
    get_initial_messages,
    get_system_prompt,
)
from rcmf.config import load_config
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text
from scripts.run_rcmf_joint_full_bank_first37_9a import _attempt_ids, _run_task


GLOBAL_SEED = 25101
RUN_MANIFEST_FORMAT = "rcmf_one_demo_dev_run_manifest_11a_v1"
PROMPT_MANIFEST_FORMAT = "rcmf_one_demo_prompt_manifest_11a_v1"
DEV_MANIFEST_FORMAT = "rcmf_official_dev_manifest_11a_v1"
CONDITION_MANIFEST_FORMAT = "rcmf_one_demo_dev_condition_manifest_11a_v1"
LEAKAGE_AUDIT_FORMAT = "rcmf_one_demo_dev_leakage_audit_11a_v1"
CONDITIONS = {
    "D0": "one_demo_bare_zero_memory",
    "D1": "one_demo_correct_499_memory_field",
    "D2": "one_demo_key_payload_shuffle_499_memory_field",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_dev_11a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp033a_prepare")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ordered_sha256(values: Sequence[str]) -> str:
    return sha256_text(json.dumps(list(values), ensure_ascii=False, separators=(",", ":")))


def _message_sha256(messages: Sequence[Mapping[str, str]]) -> str:
    payload = "\n".join(f"{row['role']}:{row['content']}" for row in messages)
    return sha256_text(payload)


def _legacy_inventory(settings_9a: Mapping[str, Any], demo_instruction: str) -> dict[str, Any]:
    app = settings_9a["appworld"]
    code = r'''
import json
import os
from pathlib import Path
from appworld import load_task_ids

root = Path(os.environ["APPWORLD_ROOT"])
datasets = {}
for name in ("train", "dev", "test_normal", "test_challenge"):
    try:
        datasets[name] = list(load_task_ids(dataset_name=name))
    except Exception as error:
        datasets[name] = {"error": type(error).__name__}
needle = os.environ["RCMF_DEMO_INSTRUCTION"]
matches = []
for path in sorted((root / "data/tasks").glob("*/specs.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("instruction", "")) == needle:
        matches.append({
            "task_id": path.parent.name,
            "instruction": str(payload.get("instruction", "")),
            "specs_path": str(path),
            "specs_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            "spec_keys": sorted(payload),
        })
print(json.dumps({"datasets": datasets, "demo_matches": matches}, sort_keys=True))
'''
    env = dict(os.environ)
    env["APPWORLD_ROOT"] = str(app["legacy_root"])
    env["RCMF_DEMO_INSTRUCTION"] = demo_instruction
    completed = subprocess.run(
        [str(app["legacy_python"]), "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError("Legacy AppWorld inventory emitted unexpected stdout")
    return json.loads(lines[0])


def _ready_subscript_keys() -> list[str]:
    tree = ast.parse(inspect.getsource(_run_task))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "ready":
            continue
        value = node.slice
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            keys.add(value.value)
    return sorted(keys)


def _immutable_paths(settings: Mapping[str, Any]) -> dict[str, Path]:
    immutable = settings["immutable_exp031a"]
    return {
        key: Path(str(immutable[key]))
        for key in (
            "checkpoint",
            "deployment_field",
            "instant_add_report",
            "data_manifest",
            "memory_provenance",
            "shuffle_manifest",
            "selector_ensemble",
        )
    }


def _validate_immutable(
    settings: Mapping[str, Any], settings_9a: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    immutable = settings["immutable_exp031a"]
    paths = _immutable_paths(settings)
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Immutable EXP-031A inputs missing: {missing}")
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    expected = {
        "checkpoint": str(immutable["checkpoint_sha256"]),
        "deployment_field": str(immutable["deployment_field_sha256"]),
        "selector_ensemble": str(immutable["selector_ensemble_sha256"]),
    }
    checks = {name: hashes[name] == value for name, value in expected.items()}
    if not all(checks.values()):
        raise ValueError(f"Immutable EXP-031A identity differs: {checks}")

    checkpoint = torch.load(paths["checkpoint"], map_location="cpu", weights_only=False)
    field = torch.load(paths["deployment_field"], map_location="cpu", weights_only=False)
    instant = _json(paths["instant_add_report"])
    data_manifest = _json(paths["data_manifest"])
    provenance = _jsonl(paths["memory_provenance"])
    shuffle = _json(paths["shuffle_manifest"])
    selector = torch.load(paths["selector_ensemble"], map_location="cpu", weights_only=False)
    field_checks = {
        "memory_count": int(field["memory_count"]) == int(immutable["memory_count"]) == 499,
        "A_shape": tuple(field["A"].shape) == (960, 8, 256),
        "B_shape": tuple(field["B"].shape) == (8, 256),
        "shuffled_A_shape": tuple(field["shuffled_A"].shape) == (960, 8, 256),
        "shuffled_B_shape": tuple(field["shuffled_B"].shape) == (8, 256),
        "checkpoint_in_field": str(field["checkpoint_sha256"]) == hashes["checkpoint"],
        "checkpoint_in_instant_add": str(instant["selected_checkpoint_sha256"])
        == hashes["checkpoint"],
        "deployment_in_instant_add": str(instant["deployment_field_sha256"])
        == hashes["deployment_field"],
        "provenance_count": len(provenance) == 499,
        "provenance_hash": str(data_manifest["memory_provenance_sha256"])
        == hashes["memory_provenance"],
        "shuffle_hash": str(data_manifest["shuffle_manifest_sha256"])
        == hashes["shuffle_manifest"],
        "shuffle_count": int(shuffle["memory_count"]) == 499,
        "shuffle_no_fixed_points": int(shuffle["fixed_point_count"]) == 0,
        "selector_hash": hashes["selector_ensemble"]
        == str(settings_9a["expected"]["selector_ensemble_sha256"]),
        "selector_three_seeds": len(selector["seed_checkpoints"]) == 3,
        "checkpoint_reader_present": "reader_state_dict" in checkpoint,
    }
    if not all(field_checks.values()):
        raise ValueError(f"Frozen field/ledger identity differs: {field_checks}")
    return {
        "paths": {name: str(path) for name, path in paths.items()},
        "hashes": hashes,
        "checks": {**checks, **field_checks},
        "reader_sha256": str(checkpoint["reader_sha256"]),
        "memory_parent_task_count": len(
            {str(row["parent_task_id"]) for row in provenance}
        ),
        "memory_parent_task_ids": sorted(
            {str(row["parent_task_id"]) for row in provenance}
        ),
    }, provenance


def _prompt_manifest(settings: Mapping[str, Any]) -> dict[str, Any]:
    expected = settings["prompt"]
    full = AGENT_SYSTEM_PROMPT_TEMPLATE_AW
    sections = full_demo_sections(full)
    full_messages = get_initial_messages("full_demo")
    one_messages = get_initial_messages(FULL_DEMO_FIRST_ONLY_PROFILE)
    section_hashes = {
        key: sha256_text(str(sections[key]))
        for key in (
            "demo_1_with_instruction_prefix",
            "demo_2",
            "demo_3",
            "trailing_key_instructions",
        )
    }
    checks = {
        "full_prompt_sha": sha256_text(full) == str(expected["original_full_prompt_sha256"]),
        "full_message_count": len(full_messages) == int(expected["original_message_count"]),
        "full_message_sha": _message_sha256(full_messages)
        == str(expected["original_structured_messages_sha256"]),
        "one_prompt_sha": sha256_text(get_system_prompt(FULL_DEMO_FIRST_ONLY_PROFILE))
        == str(expected["one_demo_prompt_sha256"]),
        "one_message_count": len(one_messages) == int(expected["one_demo_message_count"]),
        "one_message_sha": _message_sha256(one_messages)
        == str(expected["one_demo_structured_messages_sha256"]),
        "exact_raw_construction": get_system_prompt(FULL_DEMO_FIRST_ONLY_PROFILE)
        == str(sections["demo_1_with_instruction_prefix"])
        + str(sections["trailing_key_instructions"]),
        "full_demo_non_regression": get_system_prompt("full_demo") == full,
    }
    if not all(checks.values()):
        raise ValueError(f"One-demo prompt identity differs: {checks}")
    roles = [
        {
            "index": index,
            "role": str(row["role"]),
            "content_sha256": sha256_text(str(row["content"])),
            "content_bytes": len(str(row["content"]).encode("utf-8")),
        }
        for index, row in enumerate(one_messages)
    ]
    payload = {
        "format": PROMPT_MANIFEST_FORMAT,
        "profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "renderer_metadata": appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE),
        "original_full_prompt_sha256": sha256_text(full),
        "original_structured_message_count": len(full_messages),
        "original_structured_messages_sha256": _message_sha256(full_messages),
        "separator_offsets": list(sections["separator_offsets"]),
        "complete_demo_message_ranges": {
            "demo_1": {"start": 0, "end": 18, "boundary_message_is_clipped_at_separator": True},
            "demo_2": {"start": 18, "end": 40, "boundary_messages_are_clipped_at_separators": True},
            "demo_3": {"start": 40, "end": 72, "boundary_messages_are_clipped_at_separators": True},
            "trailing_key_instructions": {"start": 73, "end": 73},
        },
        "section_sha256": section_hashes,
        "retained_demo_sha256": section_hashes["demo_1_with_instruction_prefix"],
        "removed_demo_2_sha256": section_hashes["demo_2"],
        "removed_demo_3_sha256": section_hashes["demo_3"],
        "one_demo_raw_prompt_sha256": sha256_text(
            get_system_prompt(FULL_DEMO_FIRST_ONLY_PROFILE)
        ),
        "one_demo_initial_message_count": len(one_messages),
        "one_demo_initial_messages_sha256": _message_sha256(one_messages),
        "system_content_role": "user",
        "system_content_sha256": sha256_text(str(one_messages[0]["content"])),
        "retained_initial_messages": roles,
        "checks": checks,
        "frozen_before_dev_generation": True,
        "outcomes_used": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _condition_manifest(
    task_ids: Sequence[str], settings: Mapping[str, Any], immutable: Mapping[str, Any]
) -> dict[str, Any]:
    rows = [
        {
            "condition": condition,
            "condition_name": CONDITIONS[condition],
            "task_id": task_id,
            "memory_count": 0 if condition == "D0" else 499,
            "field_control": "zero"
            if condition == "D0"
            else "correct"
            if condition == "D1"
            else "key_payload_shuffle",
            "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
            "runtime_memory_retrieval": False,
            "runtime_per_memory_scoring": False,
            "student_prompt_contains_raw_memory": False,
        }
        for condition in settings["dev"]["condition_order"]
        for task_id in task_ids
    ]
    expected = int(settings["dev"]["expected_condition_count"])
    if len(rows) != expected or len({(row["condition"], row["task_id"]) for row in rows}) != expected:
        raise ValueError("EXP-033A logical condition accounting differs")
    payload = {
        "format": CONDITION_MANIFEST_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "task_ids": list(task_ids),
        "task_count": len(task_ids),
        "conditions": list(settings["dev"]["condition_order"]),
        "logical_condition_count": len(rows),
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "deployment_field_sha256": immutable["hashes"]["deployment_field"],
        "selector_ensemble_sha256": immutable["hashes"]["selector_ensemble"],
        "rows": rows,
        "frozen_before_generation": True,
        "dev_outcomes_used": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _write_or_validate(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if _json(path) != dict(payload):
            raise ValueError(f"Existing immutable manifest differs: {path}")
        return
    atomic_write_json(path, dict(payload))


def _ledger(
    args: argparse.Namespace, settings: Mapping[str, Any], hashes: Mapping[str, str]
) -> AttemptLedger:
    return AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="exp033a_prepare",
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
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_11a"]
    settings_9a = cfg.raw["stage_c_9a"]
    if not (args.local_head == args.github_head == args.lambda_head):
        raise ValueError("Local/GitHub/Lambda HEADs differ")
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-033A requires seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    immutable, provenance = _validate_immutable(settings, settings_9a)
    prompt_manifest = _prompt_manifest(settings)
    legacy = _legacy_inventory(
        settings_9a, str(settings["prompt"]["retained_demo_instruction"])
    )
    dev_ids = [str(value) for value in legacy["datasets"]["dev"]]
    train_ids = [str(value) for value in legacy["datasets"]["train"]]
    if len(dev_ids) != int(settings["dev"]["expected_task_count"]):
        raise ValueError("Official AppWorld dev task count differs")
    if len(dev_ids) != len(set(dev_ids)):
        raise ValueError("Official AppWorld dev list contains duplicates")
    demo_matches = list(legacy["demo_matches"])
    if len(demo_matches) != 1:
        raise RuntimeError(f"Retained demo source identity is not unique: {demo_matches}")
    demo_task_id = str(demo_matches[0]["task_id"])
    split_membership = sorted(
        name
        for name, values in legacy["datasets"].items()
        if isinstance(values, list) and demo_task_id in values
    )
    memory_parent_ids = {str(row["parent_task_id"]) for row in provenance}
    demo_overlap = demo_task_id in set(dev_ids)
    memory_overlap = sorted(memory_parent_ids & set(dev_ids))
    if demo_overlap or memory_overlap:
        raise RuntimeError(
            f"Dev leakage detected: demo_overlap={demo_overlap}, memory_overlap={memory_overlap}"
        )

    ready_keys = _ready_subscript_keys()
    forbidden = {
        "required_apps",
        "required_apis",
        "ground_truth",
        "solution",
        "answer",
        "evaluation_code",
        "compiled_solution",
        "ground_truth_api_calls",
        "difficulty",
        "allowed_apps",
    }
    leaked_ready_keys = sorted(set(ready_keys) & forbidden)
    if leaked_ready_keys:
        raise RuntimeError(f"Forbidden bridge values enter model rendering: {leaked_ready_keys}")
    leakage_audit = {
        "format": LEAKAGE_AUDIT_FORMAT,
        "legacy_python": str(settings_9a["appworld"]["legacy_python"]),
        "legacy_root": str(settings_9a["appworld"]["legacy_root"]),
        "dev_ground_truth_model_input_leak_count": 0,
        "model_visible_ready_keys": ready_keys,
        "forbidden_ready_keys_used": leaked_ready_keys,
        "bridge_ready_allowed_apps_ignored_by_model_runner": "allowed_apps" not in ready_keys,
        "authoritative_evaluation_occurs_after_generation": True,
        "ground_truth_paths_not_read_by_model_runner": True,
        "target_action_or_solution_not_used": True,
        "demo_task_id": demo_task_id,
        "demo_task_split_membership": split_membership,
        "demo_exact_instruction_match_count": len(demo_matches),
        "demo_specs_sha256": str(demo_matches[0]["specs_sha256"]),
        "demo_overlaps_dev": demo_overlap,
        "memory_parent_overlaps_dev": memory_overlap,
        "memory_parent_task_count": len(memory_parent_ids),
        "passed": not leaked_ready_keys and not demo_overlap and not memory_overlap,
    }
    if not leakage_audit["passed"]:
        raise RuntimeError("EXP-033A leakage audit failed")

    dev_manifest = {
        "format": DEV_MANIFEST_FORMAT,
        "dataset_name": "dev",
        "source": "appworld.load_task_ids(dataset_name='dev')",
        "legacy_python": str(settings_9a["appworld"]["legacy_python"]),
        "legacy_root": str(settings_9a["appworld"]["legacy_root"]),
        "task_ids": dev_ids,
        "task_count": len(dev_ids),
        "ordered_task_ids_sha256": _ordered_sha256(dev_ids),
        "ordered_task_ids_hash_encoding": "compact UTF-8 JSON array",
        "no_subset": True,
        "outcomes_inspected": False,
        "demo_task_id": demo_task_id,
        "demo_not_dev": not demo_overlap,
        "memory_parent_overlap_count": len(memory_overlap),
    }
    dev_manifest["manifest_sha256"] = canonical_sha256(dev_manifest)
    condition_manifest = _condition_manifest(dev_ids, settings, immutable)
    smoke_manifest = {
        "format": "rcmf_one_demo_train_smoke_manifest_11a_v1",
        "dataset_name": "train",
        "task_ids": train_ids[: int(settings["smoke"]["task_count"])],
        "task_count": int(settings["smoke"]["task_count"]),
        "conditions": list(CONDITIONS),
        "non_scientific": True,
        "outcomes_cannot_modify_science": True,
    }
    smoke_manifest["manifest_sha256"] = canonical_sha256(smoke_manifest)

    run_manifest = {
        "format": RUN_MANIFEST_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "starting_head": str(settings["source_head"]),
        "preparation_head": args.local_head,
        "working_branch": str(settings["working_branch"]),
        "exp031a_source_head": str(settings["exp031a_source_head"]),
        "config": str(args.config),
        "config_sha256": sha256_file(args.config),
        "immutable_exp031a": immutable,
        "prompt_manifest_sha256": prompt_manifest["manifest_sha256"],
        "dev_manifest_sha256": dev_manifest["manifest_sha256"],
        "condition_manifest_sha256": condition_manifest["manifest_sha256"],
        "leakage_audit": leakage_audit,
        "optimizer_steps": 0,
        "backward_passes": 0,
        "qwen_trainable_parameters": 0,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "raw_memory_prompt": False,
        "test_normal_or_challenge_used": False,
    }
    run_manifest["manifest_sha256"] = canonical_sha256(run_manifest)

    hashes = {
        "config": sha256_file(args.config),
        **immutable["hashes"],
        "prompt_manifest": str(prompt_manifest["manifest_sha256"]),
        "dev_manifest": str(dev_manifest["manifest_sha256"]),
        "condition_manifest": str(condition_manifest["manifest_sha256"]),
    }
    with _ledger(args, settings, hashes) as attempt:
        _write_or_validate(args.artifact_dir / "run_manifest.json", run_manifest)
        _write_or_validate(args.artifact_dir / "prompt_manifest.json", prompt_manifest)
        _write_or_validate(args.artifact_dir / "dev_manifest.json", dev_manifest)
        _write_or_validate(args.artifact_dir / "condition_manifest.json", condition_manifest)
        _write_or_validate(args.artifact_dir / "smoke_manifest.json", smoke_manifest)
        _write_or_validate(args.artifact_dir / "dev_leakage_audit.json", leakage_audit)
        attempt.progress(
            status="exp033a_prepare_complete",
            latest_validated_checkpoint=str(args.artifact_dir / "condition_manifest.json"),
            result={
                "dev_task_count": len(dev_ids),
                "logical_condition_count": len(condition_manifest["rows"]),
                "dev_task_list_sha256": dev_manifest["ordered_task_ids_sha256"],
                "demo_task_id": demo_task_id,
                "dev_ground_truth_model_input_leak_count": 0,
            },
        )
    print(json.dumps(run_manifest, sort_keys=True))


if __name__ == "__main__":
    main()
