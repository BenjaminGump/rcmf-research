from __future__ import annotations

import argparse
import ast
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import rms_norm
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.prepare_rcmf_benefit_preserving_calibration_9b import (
    validate_immutable_inputs,
)
from scripts.export_rcmf_joint_full_bank_audit_9a import (
    placeholder as audit_placeholder,
    redact_string as exp031a_redact_string,
)


AUDIT_VERSION = "rcmf_benefit_preserving_gain_loss_audit_9b_v1"
JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})(?![A-Za-z0-9_-])")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:password|access_token|token)\s*[:=]\s*['\"](?!<REDACTED)[^'\"]{3,}['\"]"
)

AUDIT_SENSITIVE_KEYS = {
    "password", "passwd", "access_token", "refresh_token", "token",
    "secret", "api_key", "authorization",
}


FINDINGS: dict[str, dict[str, str]] = {
    "0d01c76_1": {
        "d0": "Created the evaluator-required exact title/content note set.",
        "d1": "Completed after a second import/search loop, but the exact title/content set differed.",
        "effect": "harm",
    },
    "0d01c76_2": {
        "d0": "Imported the complete exact title/content note set and completed.",
        "d1": "Entered a repeated file-existence probe and exhausted the interaction budget without import completion.",
        "effect": "harm",
    },
    "0d01c76_3": {
        "d0": "Attempted the import but failed the exact title/content map invariant.",
        "d1": "Recovered authentication and preserved filename-to-title and file-content-to-note-content mappings for the complete set.",
        "effect": "benefit",
    },
    "29a7b7e_3": {
        "d0": "Moved all source files to the exact destination map while preserving file contents.",
        "d1": "Executed a different move transformation and failed the evaluator's exact path/content map.",
        "effect": "harm",
    },
    "325d6ec_1": {
        "d0": "Ended at the exact target cursor position.",
        "d1": "Its direction/stopping sequence did not end at the evaluator target cursor.",
        "effect": "harm",
    },
    "325d6ec_2": {
        "d0": "Drifted into playlist search and play-music behavior rather than advancing the live queue cursor to the downloaded-song target.",
        "d1": "Compared the live queue cursor against downloaded-song membership and stopped at the exact target cursor.",
        "effect": "benefit",
    },
    "325d6ec_3": {
        "d0": "Advanced once and stopped without preserving the required updated-state membership loop.",
        "d1": "Maintained the queue-position, liked-membership, next-action, updated-state, stop-predicate loop to the exact target cursor.",
        "effect": "benefit",
    },
    "634f342_1": {
        "d0": "Spent the remaining budget on repeated song search and never completed the exact-set transaction.",
        "d1": "Enumerated source playlists, removed every required occurrence, created the destination, populated the exact song set, and verified it.",
        "effect": "benefit",
    },
    "634f342_2": {
        "d0": "Drifted through repeated destination creation/search and failed the exact source-absence/destination-set invariant.",
        "d1": "Completed the required removals and exact destination population, then verified the resulting playlists.",
        "effect": "benefit",
    },
    "634f342_3": {
        "d0": "Performed a partial migration but left a final playlist/song-set mismatch.",
        "d1": "Completed an evaluator-exact source-removal and destination-set transaction.",
        "effect": "benefit",
    },
    "8749218_1": {
        "d0": "Reset the queue to the exact recommendation set, shuffled it, and played it.",
        "d1": "Used an extra recommendation retrieval path and produced a queue-set mismatch despite reset/shuffle/play calls.",
        "effect": "harm",
    },
    "8749218_2": {
        "d0": "Passed the exact recommendation-set, shuffled-queue, and playing-state invariant.",
        "d1": "Preserved the same exact final-state invariant under the correct field.",
        "effect": "retained",
    },
    "8749218_3": {
        "d0": "Passed the exact recommendation-set, shuffled-queue, and playing-state invariant.",
        "d1": "Preserved the same exact final-state invariant under the correct field.",
        "effect": "retained",
    },
    "d6ac34d_2": {
        "d0": "Created the exact habit-log title, header, schema, values, pin state, and tags.",
        "d1": "Created a note, but its exact evaluator-normalized habit-log content differed.",
        "effect": "harm",
    },
}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_manifest_hash(root: Path) -> tuple[str, int, int]:
    rows = []
    byte_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        rows.append((relative, size, sha256_file(path)))
        byte_count += size
    return canonical_hash(rows), len(rows), byte_count


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in load_jsonl(path)
        if row.get("attempt_id") is not None
    }


def tensor_summary(value: Tensor) -> dict[str, Any]:
    work = value.detach().float().cpu()
    return {
        "shape": list(work.shape),
        "minimum": float(work.min()),
        "mean": float(work.mean()),
        "maximum": float(work.max()),
        "rms": float(work.square().mean().sqrt()),
        "norm": float(work.norm()),
    }


def numeric_summary(values: Sequence[float]) -> dict[str, float | int]:
    work = torch.tensor(list(values), dtype=torch.float32)
    if not len(work):
        return {"count": 0}
    return {
        "count": len(work),
        "minimum": float(work.min()),
        "mean": float(work.mean()),
        "maximum": float(work.max()),
        "standard_deviation": float(work.std(unbiased=False)),
    }


def first_text_divergence(d0: Sequence[Mapping[str, Any]], d1: Sequence[Mapping[str, Any]]) -> int | None:
    for index in range(max(len(d0), len(d1))):
        if index >= len(d0) or index >= len(d1):
            return index + 1
        if d0[index]["exact_executed_code"] != d1[index]["exact_executed_code"]:
            return index + 1
    return None


def dominant_sign_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sequence = []
    for row in rows:
        ranking = row["field"]["top_memory_contributions"].get("ranking", [])
        if not ranking:
            continue
        weight = float(ranking[0]["signed_address_weight"])
        sequence.append({"step": int(row["step_id"]), "weight": weight, "sign": 1 if weight > 0 else -1 if weight < 0 else 0})
    nonzero = [row["sign"] for row in sequence if row["sign"]]
    changes = sum(left != right for left, right in zip(nonzero, nonzero[1:]))
    return {
        "sequence": sequence,
        "sign_change_count": changes,
        "has_negative": any(value < 0 for value in nonzero),
        "has_positive": any(value > 0 for value in nonzero),
    }


def evaluator_symbols(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        value: ast.AST = node.func
        parts = []
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        if parts:
            calls.add(".".join(reversed(parts)))
    return {"sha256": sha256_file(path), "calls": sorted(calls)}


def evaluator_invariant(task_id: str, task_root: Path) -> dict[str, Any]:
    directory = task_root / task_id / "ground_truth"
    private = load_json(directory / "private_data.json")
    base = {
        "evaluator": evaluator_symbols(directory / "evaluation.py"),
        "private_data_sha256": sha256_file(directory / "private_data.json"),
        "private_data_canonical_sha256": canonical_hash(private),
    }
    if task_id.startswith("0d01c76_"):
        mapping = private["note_title_to_content"]
        base.update(invariant="changed Simple Note records equal the complete title-to-content map and no unrelated model changes occur", expected_record_count=len(mapping), expected_map_sha256=canonical_hash(mapping))
    elif task_id == "29a7b7e_3":
        mapping = private["meeting_start_to_end_file_path"]
        base.update(invariant="the complete start-path to end-path mapping is realized with file contents preserved and no unrelated model changes", expected_file_count=len(mapping), expected_map_sha256=canonical_hash(mapping))
    elif task_id.startswith("325d6ec_"):
        base.update(invariant="Spotify cursor_position equals the exact target song cursor and no unrelated model/field changes occur", target_song_id_sha256=canonical_hash(private["target_song_id"]))
    elif task_id.startswith("634f342_"):
        mapping = private["to_archive_playlist_id_to_song_ids"]
        songs = private["to_archive_song_ids"]
        base.update(invariant="all listed source occurrences are absent and the new destination playlist contains exactly the required unique song set", source_playlist_count=len(mapping), source_occurrence_count=sum(len(v) for v in mapping.values()), expected_unique_song_count=len(songs), expected_set_sha256=canonical_hash(sorted(songs)))
    elif task_id.startswith("8749218_"):
        songs = private["recommendation_song_ids"]
        base.update(invariant="the queue contains exactly all recommendation IDs, is shuffled relative to canonical order, and playback is active", expected_recommendation_count=len(songs), expected_set_sha256=canonical_hash(sorted(songs)))
    elif task_id == "d6ac34d_2":
        expected = private["expected_data"]
        base.update(invariant="one exact normalized habit-log note matches title, header, ten Boolean fields, pin state, and tags", expected_field_count=len(expected), expected_data_sha256=canonical_hash(expected), meditation_expected=bool(expected["practiced_meditation"]), expected_pinned=bool(private["expected_pinned"]), expected_tag_count=len(private["expected_tags"]), expected_title_sha256=canonical_hash(private["expected_title"]), expected_header_sha256=canonical_hash(private["expected_header"]))
    else:
        raise ValueError(task_id)
    return base


def reader_summary(reader: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "delta_norms": {str(layer): numeric_summary(values) for layer, values in reader["delta_norms"].items()},
        "attention_entropy": {str(layer): numeric_summary(values) for layer, values in reader["attention_entropy"].items()},
        "attention_row_sum_error": reader["attention_row_sum_error"],
        "calls": reader["calls"],
    }


def safe_memory(memory_id: str, provenance: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    p = provenance[memory_id]
    row = ledger[memory_id]
    return {
        "transition_id": memory_id,
        "parent_task_id": row["parent_task_id"],
        "step_index": row["step_index"],
        "apps": row["apps"],
        "api_names": row["api_names"],
        "action_type": row["action_type"],
        "completion_related": row["completion_related"],
        "complete_action_sha256": row["complete_action_sha256"],
        "source_task_goal_sha256": row["source_task_goal_sha256"],
        "transition_content_sha256": row["transition_content_sha256"],
        "provenance_representation_sha256": p["representation_sha256"],
        "human_readable_summary": f"task {row['parent_task_id']} step {row['step_index']}: {row['action_type']} via {', '.join(row['api_names']) or 'Python-only action'}",
    }


def git_safe_redact(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in AUDIT_SENSITIVE_KEYS and value is not None:
        return audit_placeholder(f"FIELD:{key.upper()}", value)
    if isinstance(value, str):
        return exp031a_redact_string(value)
    if isinstance(value, Mapping):
        return {str(name): git_safe_redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [git_safe_redact(item) for item in value]
    return value


def _serialized_secret_matches(value: Any) -> list[dict[str, Any]]:
    text = json.dumps(value, ensure_ascii=False)
    matches = [
        {
            "kind": "jwt",
            "match_sha256": hashlib.sha256(match.group(1).encode("utf-8")).hexdigest(),
            "value_length": len(match.group(1)),
        }
        for match in JWT_RE.finditer(text)
    ]
    matches.extend(
        {
            "kind": "credential_assignment",
            "match_sha256": hashlib.sha256(match.group(0).encode("utf-8")).hexdigest(),
            "value_length": len(match.group(0)),
        }
        for match in SECRET_ASSIGNMENT_RE.finditer(text)
    )
    return matches


def git_safe_findings(value: Any, path: str = "$") -> list[dict[str, Any]]:
    matches = _serialized_secret_matches(value)
    if not matches:
        return []
    if isinstance(value, Mapping):
        for name, item in value.items():
            child_path = f"{path}/{name}"
            findings = git_safe_findings(item, child_path)
            if findings:
                return findings
            pair_matches = _serialized_secret_matches({str(name): item})
            if pair_matches:
                return [{"path": child_path, **row} for row in pair_matches]
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            findings = git_safe_findings(item, f"{path}/{index}")
            if findings:
                return findings
    return [{"path": path, **row} for row in matches]


def git_safe_check(value: Any) -> None:
    findings = git_safe_findings(value)
    if not findings:
        return
    finding = findings[0]
    if finding["kind"] == "jwt":
        raise ValueError(
            "Git-safe audit contains an unredacted JWT at "
            f"{finding['path']} (sha256={finding['match_sha256']})"
        )
    raise ValueError(
        "Git-safe audit contains an unredacted credential assignment at "
        f"{finding['path']} (sha256={finding['match_sha256']})"
    )


def markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-031B Gain/Loss Mechanism Audit",
        "",
        f"- Run UUID: `{payload['run_uuid']}`",
        f"- Source EXP-031A: `{payload['source_exp031a_head']}`",
        f"- Audited tasks: `{payload['task_count']}`",
        "- Candidate outcomes inspected: `false`",
        "- Runtime retrieval used: `false`",
        "",
        "## Verified Task Mechanisms",
        "",
        "| Task | Group | D0/D1/D2 | Critical D1 step | Mechanism |",
        "|---|---:|---:|---:|---|",
    ]
    for task in payload["tasks"]:
        outcomes = "/".join("pass" if task["outcomes"][condition]["success"] else "fail" for condition in ("D0", "D1", "D2"))
        lines.append(f"| `{task['task_id']}` | {task['group']} | {outcomes} | {task['critical_state']['step_id']} | {task['mechanism']} |")
    lines.extend(["", "## Hypothesis Audit", ""])
    for name, row in payload["hypothesis_audit"].items():
        lines.append(f"- **{name}: {row['status']}** {row['finding']}")
    lines.extend([
        "",
        "## Evidence Boundaries",
        "",
        "**VERIFIED:** Outcomes, emitted redacted code/observations, evaluator hashes and invariants, field magnitudes, reader statistics, and signed contribution sequences come from immutable EXP-031A rows and official AppWorld 0.1.0 task snapshots.",
        "",
        "**INFERENCE:** Mechanism labels identify the earliest trajectory decision that best explains the final-state difference; whole-bank interventions prevent attribution to one memory record.",
        "",
        "**UNVERIFIED:** No candidate calibration has been evaluated, and no top-contribution row is claimed individually causal.",
        "",
        "## Exact Replays",
        "",
        "Fourteen replay cases are locked in the machine-readable JSON. Each points to the immutable raw Lambda row, exact renderer/prompt hashes, replay-prefix hashes, task snapshot hash, field query/slot tensor, and fresh-world reconstruction rule. Git-safe prompt components are redacted; exact raw material remains on Lambda.",
        "",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/benchmark/stage_c_rcmf_benefit_preserving_calibration_9b.yaml"))
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--output-json", type=Path, default=Path("research/analysis/exp031b_gain_loss_audit.json"))
    parser.add_argument("--output-markdown", type=Path, default=Path("research/analysis/EXP_031B_GAIN_LOSS_AUDIT.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_config(args.config).raw["stage_c_9b"]
    persistent = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise RuntimeError("Local, GitHub, and Lambda HEADs differ")
    immutable = validate_immutable_inputs(settings)
    audit = settings["gain_loss_audit"]
    project = Path(str(settings["persistent_root"])) / "project"
    audit_root = Path(str(audit["source_audit_root"]))
    task_root = Path(str(audit["task_spec_root"]))
    deployment_path = project / str(settings["immutable_exp031a"]["deployment_field"])
    deployment = torch.load(deployment_path, map_location="cpu", weights_only=False)
    tensor_bundle_path = Path(str(audit["field_tensor_bundle"]))
    tensor_bundle = torch.load(tensor_bundle_path, map_location="cpu", weights_only=False)
    provenance_path = project / str(audit["memory_provenance"])
    ledger_path = project / str(audit["transition_ledger"])
    provenance = {row["transition_id"]: row for row in load_jsonl(provenance_path)}
    ledger = {row["transition_id"]: row for row in load_jsonl(ledger_path)}
    expected_tasks = set(settings["critical_states"]["gains"] + settings["critical_states"]["retained"] + settings["critical_states"]["losses"])
    if set(audit["critical_steps"]) != expected_tasks or set(FINDINGS) != expected_tasks:
        raise ValueError("Critical audit task identities differ")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="gain_loss_mechanism_audit_and_critical_replay_materialization",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session="none_cpu_audit",
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={"deployment_field": sha256_file(deployment_path), "source_audit_index": sha256_file(audit_root / "index.json"), "memory_provenance": sha256_file(provenance_path), "transition_ledger": sha256_file(ledger_path)},
        parent_attempt_id="exp031b-immutable-preflight-001",
        resume_checkpoint="none",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        tasks = []
        replay_rows = []
        for task_id in sorted(expected_tasks):
            locked = audit["critical_steps"][task_id]
            rows_by_condition = {condition: load_jsonl(audit_root / "first37" / condition / f"{task_id}.jsonl") for condition in ("D0", "D1", "D2")}
            critical = rows_by_condition["D1"][int(locked["d1_critical_step"]) - 1]
            if int(critical["step_id"]) != int(locked["d1_critical_step"]):
                raise ValueError(f"Critical step index mismatch: {task_id}")
            raw_path = Path(critical["raw_lambda_source"])
            if sha256_file(raw_path) != critical["raw_lambda_source_sha256"]:
                raise ValueError(f"Raw task result differs: {task_id}")
            raw = load_json(raw_path)
            raw_step = raw["steps"][int(critical["step_id"]) - 1]
            if raw_step["rendered_message_sha256"] != critical["rendered_messages_raw_sha256"]:
                raise ValueError(f"Raw prompt hash differs: {task_id}")
            bundle_key = critical["field_tensor_bundle_key"]
            tensors = tensor_bundle["first37"][bundle_key]
            query = tensors["query"].float()
            raw_field = deployment["B"].float() + torch.einsum("k,ksp->sp", query, deployment["A"].float())
            normalized = rms_norm(raw_field)
            slot_error = float((normalized - tensors["slots"].float()).abs().max())
            if slot_error > 1.0e-4:
                raise ValueError(f"Stored slots differ from field reconstruction: {task_id} {slot_error}")
            task_manifest_sha, task_file_count, task_bytes = file_manifest_hash(task_root / task_id)
            prefix_steps = raw["steps"][: int(critical["step_id"]) - 1]
            contributions = []
            for contribution in critical["field"]["top_memory_contributions"]["ranking"]:
                item = dict(contribution)
                item["key_memory"] = safe_memory(str(item["key_memory_id"]), provenance, ledger)
                item["payload_memory"] = safe_memory(str(item["payload_memory_id"]), provenance, ledger)
                contributions.append(item)
            invariant = evaluator_invariant(task_id, task_root)
            outcomes = {}
            for condition, rows in rows_by_condition.items():
                source = Path(rows[0]["raw_lambda_source"])
                raw_condition = load_json(source)
                outcomes[condition] = {"success": bool(rows[0]["task_success"]), "step_count": len(rows), "raw_result_sha256": sha256_file(source), "num_tests": raw_condition["evaluation"].get("num_tests")}
            d0_behavior = rows_by_condition["D0"][int(locked["d0_behavior_step"]) - 1]
            d1_behavior = rows_by_condition["D1"][int(locked["d1_behavior_step"]) - 1]
            replay = {
                "format": AUDIT_VERSION,
                "task_id": task_id,
                "condition": "D1_correct_complete_field",
                "step_id": int(critical["step_id"]),
                "raw_lambda_source": str(raw_path),
                "raw_lambda_source_sha256": critical["raw_lambda_source_sha256"],
                "raw_step_index_zero_based": int(critical["step_id"]) - 1,
                "prompt": {"renderer_version": critical["renderer_version"], "prompt_profile": critical["prompt_profile"], "rendered_messages_raw_sha256": critical["rendered_messages_raw_sha256"], "static_prompt_asset_sha256": critical["static_prompt_asset_sha256"], "current_task_message_raw_sha256": canonical_hash(critical["current_task_message"]), "current_task_message_git_safe": git_safe_redact(critical["current_task_message"]), "complete_trajectory_so_far_raw_sha256": canonical_hash(critical["complete_trajectory_so_far"]), "complete_trajectory_so_far_git_safe": git_safe_redact(critical["complete_trajectory_so_far"]), "prompt_tokens": critical["prompt_tokens"], "context_limit": critical["context_limit"], "truncation_applied": critical["truncation_applied"]},
                "world": {"task_snapshot_manifest_sha256": task_manifest_sha, "task_snapshot_file_count": task_file_count, "task_snapshot_bytes": task_bytes, "fresh_isolated_world_required": True, "same_python_namespace_required": True, "prefix_action_count": len(prefix_steps), "raw_prefix_action_sequence_sha256": canonical_hash([row["exact_executed_code"] for row in prefix_steps]), "raw_prefix_observation_sequence_sha256": canonical_hash([row["complete_environment_observation"] for row in prefix_steps]), "reconstruction": "initialize official AppWorld 0.1.0 task, execute the exact raw prefix actions in order in one live Python namespace, verify each raw observation hash, then generate the critical action"},
                "field": {"deployment_field_sha256": critical["field"]["deployment_field_sha256"], "field_tensor_bundle": str(tensor_bundle_path), "field_tensor_bundle_sha256": sha256_file(tensor_bundle_path), "field_tensor_bundle_key": bundle_key, "query_sha256": critical["field"]["query"]["sha256"], "slots_sha256": critical["field"]["slots"]["sha256"], "normalized_slot_reconstruction_max_abs_error": slot_error},
                "model_identity": critical["model_identity"],
                "generation_config": critical["generation_config"],
                "same_world_execution": critical["same_world_execution"],
                "same_python_namespace": critical["same_python_namespace"],
            }
            replay_rows.append(replay)
            task_payload = {
                "task_id": task_id,
                "group": str(locked["group"]),
                "mechanism": str(locked["mechanism"]),
                "requirement_raw_sha256": canonical_hash(critical["current_task_message"]),
                "requirement_git_safe": git_safe_redact(critical["current_task_message"]),
                "outcomes": outcomes,
                "first_text_divergence_step": first_text_divergence(rows_by_condition["D0"], rows_by_condition["D1"]),
                "first_behavioral_divergence": {"d0_step": int(locked["d0_behavior_step"]), "d1_step": int(locked["d1_behavior_step"]), "d0_exact_generated_code_raw_sha256": canonical_hash(d0_behavior["exact_executed_code"]), "d0_exact_generated_code_git_safe": git_safe_redact(d0_behavior["exact_executed_code"]), "d0_exact_observation_raw_sha256": canonical_hash(d0_behavior["complete_environment_observation"]), "d0_exact_observation_git_safe": git_safe_redact(d0_behavior["complete_environment_observation"]), "d1_exact_generated_code_raw_sha256": canonical_hash(d1_behavior["exact_executed_code"]), "d1_exact_generated_code_git_safe": git_safe_redact(d1_behavior["exact_executed_code"]), "d1_exact_observation_raw_sha256": canonical_hash(d1_behavior["complete_environment_observation"]), "d1_exact_observation_git_safe": git_safe_redact(d1_behavior["complete_environment_observation"])},
                "critical_state": {"step_id": int(critical["step_id"]), "exact_generated_code_raw_sha256": canonical_hash(critical["exact_executed_code"]), "exact_generated_code_git_safe": git_safe_redact(critical["exact_executed_code"]), "exact_observation_raw_sha256": canonical_hash(critical["complete_environment_observation"]), "exact_observation_git_safe": git_safe_redact(critical["complete_environment_observation"]), "raw_pre_rms_field": tensor_summary(raw_field), "query": tensor_summary(query), "normalized_slot_reconstruction_max_abs_error": slot_error, "reader": reader_summary(critical["reader_audit"]), "top_memory_contributions": contributions},
                "dominant_contribution_signs": dominant_sign_audit(rows_by_condition["D1"]),
                "final_state_invariant": invariant,
                "verified_findings": FINDINGS[task_id],
                "inference": "The locked critical step is causally diagnostic because it is the earliest or final decisive operation in the mechanism category that separates the observed final-state outcomes; it does not identify one ledger memory as causal.",
                "unverified": "Individual offline top-contribution records are descriptive and were never runtime inputs.",
                "replay_case": replay,
            }
            tasks.append(task_payload)
        mixed_sign_tasks = [row["task_id"] for row in tasks if row["dominant_contribution_signs"]["has_negative"] and row["dominant_contribution_signs"]["has_positive"]]
        payload = {
            "format": AUDIT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_uuid": settings["run_uuid"],
            "source_exp031a_head": settings["source_head"],
            "record_head": args.lambda_head,
            "global_seed": settings["global_seed"],
            "task_count": len(tasks),
            "outcomes_used_for_candidate_selection": False,
            "candidate_results_inspected": False,
            "runtime_memory_retrieval": False,
            "source_hashes": {"config": sha256_file(args.config), "audit_index": sha256_file(audit_root / "index.json"), "deployment_field": sha256_file(deployment_path), "field_tensor_bundle": sha256_file(tensor_bundle_path), "memory_provenance": sha256_file(provenance_path), "transition_ledger": sha256_file(ledger_path)},
            "immutable_exp031a": immutable,
            "tasks": tasks,
            "hypothesis_audit": {
                "A_cross_app_mapping": {"status": "SUPPORTED_WITH_ATTRIBUTION_LIMIT", "finding": "0d01c76_3 D1 passed the complete exact note map while D0 did not; credential/data-type separation is consistent with the trace but cannot be isolated from the whole-bank intervention."},
                "B_spotify_state_machine": {"status": "SUPPORTED", "finding": "Both 325d6ec gains pass only when the live queue cursor, membership predicate, direction action, updated state, and stop predicate remain coherent."},
                "C_exact_set_transaction": {"status": "SUPPORTED", "finding": "All three 634f342 D1 runs pass evaluator-exact source-absence and destination-set invariants; their D0 controls fail through search drift, duplicate/retry behavior, or incomplete final sets."},
                "D_harm_taxonomy": {"status": "SUPPORTED", "finding": "The six losses cover procedural drift, direction/stopping failure, argument/path construction, exact-set mismatch, and schema/content preservation failure."},
                "E_signed_contribution_ambiguity": {"status": "SUPPORTED", "finding": f"Mixed dominant contribution signs occur in {len(mixed_sign_tasks)}/14 audited D1 trajectories ({', '.join(mixed_sign_tasks)}); sign alone is therefore not a harm gate."},
            },
            "critical_replay_count": len(replay_rows),
            "critical_replay_raw_manifest_sha256": canonical_hash(replay_rows),
        }
        replay_rows = git_safe_redact(replay_rows)
        payload = git_safe_redact(payload)
        payload["critical_replay_manifest_sha256"] = canonical_hash(replay_rows)
        git_safe_check(payload)
        git_safe_check(replay_rows)
        atomic_write_json(args.output_json, payload)
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(markdown(payload), encoding="utf-8")
        replay_path = args.artifact_dir / "critical_replays" / "manifest.json"
        atomic_write_json(replay_path, {"format": AUDIT_VERSION, "rows": replay_rows, "manifest_sha256": canonical_hash(replay_rows)})
        attempt.progress(phase="complete", task_count=len(tasks), critical_replay_count=len(replay_rows), critical_replay_manifest_sha256=canonical_hash(replay_rows))
        print(json.dumps({"task_count": len(tasks), "critical_replay_count": len(replay_rows), "critical_replay_manifest_sha256": canonical_hash(replay_rows), "mixed_sign_task_count": len(mixed_sign_tasks)}, sort_keys=True))


if __name__ == "__main__":
    main()
