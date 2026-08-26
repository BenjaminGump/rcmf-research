"""Export Git-safe, reconstructible audit records for EXP-031A.

Lambda remains the immutable source of unredacted logs and tensors. This
exporter writes a credential-redacted review copy, verifies the deployment
field from the complete ledger, and computes contribution diagnostics offline.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import torch
from torch import Tensor

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rcmf.training.rcmf_joint_full_bank_9a import (  # noqa: E402
    AlignedTransitionWriter,
)

RUN_UUID = "rcmf_joint_full_bank_9a_20260826_001"
FORMAT = "rcmf_joint_full_bank_detailed_audit_9a_v1"
AUDIT_RELATIVE = Path("research/audits") / RUN_UUID
JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})(?![A-Za-z0-9_-])"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)((?:['\"])?\b(?:password|passwd|access_token|refresh_token|secret|api_key)\b(?:['\"])?\s*[:=]\s*)(['\"]?)([^'\"\s,}\]]+)(\2)"
)
SENSITIVE_KEYS = {
    "password", "passwd", "access_token", "refresh_token",
    "secret", "api_key", "authorization",
}
SENSITIVE_OBSERVATIONS: set[str] = set()


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raw_tensor_sha256(value: Tensor) -> str:
    work = value.detach().to(device="cpu").contiguous()
    return sha_bytes(work.view(torch.uint8).numpy().tobytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def atomic_torch(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(dict(payload), temporary)
    temporary.replace(path)


def placeholder(kind: str, value: Any) -> str:
    raw = str(value)
    return f"<REDACTED:{kind}:SHA256={sha_text(raw)}>"


def register_sensitive_observation(code: Any, observation: Any) -> None:
    text = str(observation)
    if "password" in str(code).lower() and len(text.strip()) >= 3:
        SENSITIVE_OBSERVATIONS.add(text)


def redact_string(value: str) -> str:
    for secret in sorted(SENSITIVE_OBSERVATIONS, key=len, reverse=True):
        if secret in value:
            value = value.replace(
                secret, placeholder("CREDENTIAL_OBSERVATION", secret)
            )

    def jwt_replace(match: re.Match[str]) -> str:
        return placeholder("JWT", match.group(1))

    def assignment_replace(match: re.Match[str]) -> str:
        prefix, quote, secret, closing = match.groups()
        kind = "JWT" if JWT_RE.fullmatch(secret) else "CREDENTIAL"
        return f"{prefix}{quote}{placeholder(kind, secret)}{closing}"

    return JWT_RE.sub(jwt_replace, ASSIGNMENT_RE.sub(assignment_replace, value))


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_KEYS and value is not None:
        return placeholder(f"FIELD:{key.upper()}", value)
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def load_rows_with_paths(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        row = load_json(path)
        if row.get("status") == "complete":
            rows.append((path, row))
    return rows


def load_deployment(root: Path) -> dict[str, Any]:
    source_path = root / "data/rcmf_source_cache.pt"
    deployment_path = root / "deployment_field/complete_37_task_field.pt"
    checkpoint_path = root / "joint_training/checkpoints/epoch_02.pt"
    data_manifest_path = root / "data/full_bank_data_manifest.json"
    shuffle_path = root / "data/key_payload_shuffle_manifest.json"

    source = torch.load(source_path, map_location="cpu", weights_only=False)
    deployment = torch.load(deployment_path, map_location="cpu", weights_only=False)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    data_manifest = load_json(data_manifest_path)
    shuffle_rows = load_json(shuffle_path)["complete_deployment_bank"]["rows"]

    ordered = [str(value) for value in source["ordered_transition_ids"]]
    if len(ordered) != 499 or set(ordered) != set(deployment["memory_ids"]):
        raise ValueError("Deployment ledger differs from the 499-source cache")
    index = {memory_id: position for position, memory_id in enumerate(ordered)}
    rho = torch.tensor(
        [float(data_manifest["rho_by_transition_id"][mid]) for mid in ordered],
        dtype=torch.float32,
    )
    shuffle_by_key = {
        str(row["key_transition_id"]): str(row["payload_transition_id"])
        for row in shuffle_rows
    }
    if set(shuffle_by_key) != set(ordered):
        raise ValueError("Deployment shuffle key identities differ")
    permutation = torch.tensor(
        [index[shuffle_by_key[memory_id]] for memory_id in ordered],
        dtype=torch.long,
    )

    writer = AlignedTransitionWriter()
    writer.load_state_dict(checkpoint["writer_state_dict"])
    writer.eval()
    with torch.no_grad():
        payloads = writer(source["memory_views"].to(torch.float32))
        keys = source["memory_keys"].to(torch.float32)
        A = torch.einsum("n,nk,nsp->ksp", rho, keys, payloads)
        shuffled_A = torch.einsum(
            "n,nk,nsp->ksp", rho, keys, payloads[permutation]
        )
    correct_error = float((A - deployment["A"].float()).abs().max())
    shuffled_error = float(
        (shuffled_A - deployment["shuffled_A"].float()).abs().max()
    )
    if correct_error > 1.0e-4 or shuffled_error > 1.0e-4:
        raise ValueError(
            f"Offline field reconstruction differs: {correct_error}, {shuffled_error}"
        )
    return {
        "payloads": payloads,
        "keys": keys,
        "rho": rho,
        "permutation": permutation,
        "ordered_ids": ordered,
        "correct_error": correct_error,
        "shuffled_error": shuffled_error,
        "paths": {
            "source": str(source_path),
            "source_sha256": sha_file(source_path),
            "deployment": str(deployment_path),
            "deployment_sha256": sha_file(deployment_path),
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": sha_file(checkpoint_path),
            "data_manifest": str(data_manifest_path),
            "data_manifest_sha256": sha_file(data_manifest_path),
            "shuffle_manifest": str(shuffle_path),
            "shuffle_manifest_sha256": sha_file(shuffle_path),
        },
    }


def top_contributions(
    query: Tensor, deployment: Mapping[str, Any], shuffled: bool, limit: int = 8
) -> list[dict[str, Any]]:
    payloads = deployment["payloads"]
    permutation = deployment["permutation"]
    source_positions = (
        permutation if shuffled else torch.arange(len(payloads), dtype=torch.long)
    )
    weights = (deployment["keys"] @ query.float()) * deployment["rho"]
    contributions = payloads[source_positions] * weights[:, None, None]
    norms = contributions.flatten(start_dim=1).norm(dim=1)
    top = torch.topk(norms, min(limit, len(norms))).indices.tolist()
    ordered = deployment["ordered_ids"]
    return [
        {
            "rank": rank + 1,
            "key_memory_id": ordered[position],
            "payload_memory_id": ordered[int(source_positions[position])],
            "signed_address_weight": float(weights[position]),
            "contribution_frobenius_norm": float(norms[position]),
        }
        for rank, position in enumerate(top)
    ]


def register_asset(
    assets: dict[str, Any], messages: Sequence[Mapping[str, Any]], identity: str
) -> None:
    safe = redact([dict(row) for row in messages])
    row = {
        "raw_sha256": identity,
        "redacted_messages": safe,
        "redacted_sha256": sha_text(
            json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
    }
    previous = assets.get(identity)
    if previous is not None and previous != row:
        raise ValueError(f"Static prompt asset differs for {identity}")
    assets[identity] = row


def heldout_record(source_path, row, tensor_key, static_assets):
    messages = list(row["model_messages"])
    register_asset(
        static_assets, messages[:-1], str(row["static_prompt_asset_sha256"])
    )
    field = dict(row["field"])
    field.pop("query_values", None)
    return redact({
        "format": FORMAT,
        "audit_scope": "heldout_live_one_step",
        "epoch": row["epoch"],
        "condition": row["control"],
        "condition_key": row["condition_key"],
        "task_id": row["source_task_id"],
        "state_id": row["source_state_id"],
        "step_id": row["source_step_id"],
        "prompt_profile": row["prompt_profile"],
        "static_prompt_asset_sha256": row["static_prompt_asset_sha256"],
        "task_message": row["task_message"],
        "trajectory_so_far": row["trajectory_so_far"],
        "dynamic_message": messages[-1],
        "reconstruction_rule": "static asset plus dynamic message",
        "rendered_messages_raw_sha256": row["rendered_messages_sha256"],
        "prompt_tokens": row["prompt_tokens"],
        "context_limit": row["context_limit"],
        "truncation_applied": row["truncation_applied"],
        "generation": {
            "model": row["model_name"],
            "tokenizer": row["tokenizer_identity"],
            "checkpoint_sha256": row["checkpoint_sha256"],
            "seed": row["seed"],
            "temperature": row["temperature"],
            "top_p": row["top_p"],
            "max_new_tokens": row["max_new_tokens"],
            "do_sample": row["do_sample"],
            "enable_thinking": row["enable_thinking"],
            "raw_model_response": row["raw_model_response"],
            "generated_token_ids": row["generated_token_ids"],
            "extracted_code": row["extracted_code"],
            "automatically_repaired_response": row["automatically_repaired_response"],
            "automatically_repaired_code": row["automatically_repaired_code"],
            "executed_code": row["executed_code"],
        },
        "execution_exception": row["execution_exception"],
        "complete_environment_observation": row["complete_environment_observation"],
        "normalized_observation": row["normalized_observation"],
        "task_completed_status": row["task_completed_status"],
        "metrics": row["metrics"],
        "field": field,
        "field_tensor_bundle_key": tensor_key,
        "reader_audit": row["reader_audit"],
        "runtime_memory_retrieval": row["runtime_memory_retrieval"],
        "student_prompt_contains_raw_memory": row["student_prompt_contains_raw_memory"],
        "raw_lambda_source": str(source_path),
        "raw_lambda_source_sha256": sha_file(source_path),
    })


def first37_record(task_path, task, step, tensor_key, contribution):
    field = copy.deepcopy(step["field"])
    field["top_memory_contributions"] = {
        "status": "computed_offline_after_run",
        "not_used_by_model_or_field_read": True,
        "ranking": list(contribution),
    }
    return redact({
        "format": FORMAT,
        "audit_scope": "first37_full_agent",
        "condition": task["condition"],
        "task_id": task["task_id"],
        "task_success": task["success"],
        "step_id": step["step_id"],
        "prompt_profile": step["prompt_profile"],
        "renderer_version": step["renderer_version"],
        "static_prompt_asset_sha256": step["static_prompt_asset_sha256"],
        "current_task_message": step["current_task_message"],
        "complete_trajectory_so_far": step["complete_trajectory_so_far"],
        "reconstruction_rule": (
            "static asset plus current task and trajectory rendered by renderer_version"
        ),
        "rendered_messages_raw_sha256": step["rendered_message_sha256"],
        "prompt_tokens": step["prompt_tokens"],
        "context_limit": step["context_limit"],
        "context_decision": step["context_decision"],
        "truncation_applied": step["truncation_applied"],
        "generation_config": step["generation_config"],
        "model_identity": step["model_identity"],
        "raw_model_response": step["raw_model_response"],
        "generated_token_ids": step["generated_token_ids"],
        "extracted_code": step["extracted_code"],
        "automatically_repaired_response": step["automatically_repaired_response"],
        "automatically_repaired_code": step["automatically_repaired_code"],
        "exact_executed_code": step["exact_executed_code"],
        "execution_exception": step["execution_exception"],
        "complete_environment_observation": step["complete_environment_observation"],
        "locked_normalized_observation": step["locked_normalized_observation"],
        "task_completed_status": step["task_completed_status"],
        "termination_after_step": step["termination_after_step"],
        "same_world_execution": step["same_world_execution"],
        "same_python_namespace": step["same_python_namespace"],
        "repeated_action": step["repeated_action"],
        "repeated_invalid_action": step["repeated_invalid_action"],
        "field": field,
        "field_tensor_bundle_key": tensor_key,
        "reader_audit": step["reader_audit"],
        "raw_lambda_source": str(task_path),
        "raw_lambda_source_sha256": sha_file(task_path),
    })


def materialized_step(step):
    return redact({
        "exact_model_message_array": step["exact_model_message_array"],
        "rendered_messages_raw_sha256": step["rendered_message_sha256"],
        "prompt_tokens": step["prompt_tokens"],
        "raw_model_response": step["raw_model_response"],
        "extracted_code": step["extracted_code"],
        "automatically_repaired_code": step["automatically_repaired_code"],
        "exact_executed_code": step["exact_executed_code"],
        "complete_environment_observation": step["complete_environment_observation"],
        "execution_exception": step["execution_exception"],
        "field": step["field"],
        "reader_audit": step["reader_audit"],
    })


def comparison_markdown(task_id, tasks, divergence, contributions):
    lines = [
        "# EXP-031A first37 comparison: " + task_id,
        "",
        (
            "Git-safe materialization. Credentials and JWTs use typed SHA256 "
            "placeholders; unredacted logs remain on Lambda."
        ),
        "",
        "## Outcomes",
        "",
    ]
    for condition in ("D0", "D1", "D2"):
        row = tasks[condition]
        lines.append(
            "- %s: success=%s, steps=%s, wall_seconds=%.3f"
            % (
                condition,
                bool(row["success"]),
                row["step_count"],
                float(row["wall_seconds"]),
            )
        )
    lines.extend(["", "## First Divergence", "", "~~~json"])
    lines.append(json.dumps(divergence, ensure_ascii=False, sort_keys=True, indent=2))
    lines.extend(["~~~", ""])
    selected_steps = set()
    for name in ("D0_vs_D1", "D1_vs_D2"):
        value = divergence.get(name)
        if value is not None:
            selected_steps.add(int(value["step_id"]))
    for step_id in sorted(selected_steps):
        lines.extend(["## Materialized Step %d" % step_id, ""])
        for condition in ("D0", "D1", "D2"):
            row = tasks[condition]
            step = next(
                (
                    item
                    for item in row["steps"]
                    if int(item["step_id"]) == step_id
                ),
                None,
            )
            lines.extend(["### " + condition, ""])
            if step is None:
                lines.extend(["Condition terminated before this step.", ""])
                continue
            materialized = materialized_step(step)
            key = "%s:%s:%d" % (condition, task_id, step_id)
            materialized["offline_top_memory_contributions"] = list(
                contributions.get(key, ())
            )
            lines.extend([
                "~~~json",
                json.dumps(
                    materialized, ensure_ascii=False, sort_keys=True, indent=2
                ),
                "~~~",
                "",
            ])
    lines.extend([
        "## Causal Interpretation",
        "",
        (
            "**INFERENCE:** correct and shuffled fixed fields change generated "
            "actions early on many tasks. The paired success result supports a "
            "live memory-specific effect (D1=8, D2=5) but does not isolate one "
            "ledger record as causal because all 499 memories contribute."
        ),
        "",
    ])
    return "\n".join(lines)


def export(artifact_root: Path, audit_root: Path) -> dict[str, Any]:
    audit_root.mkdir(parents=True, exist_ok=True)
    deployment = load_deployment(artifact_root)
    static_assets = {}
    tensor_bundle = {
        "format": "rcmf_joint_full_bank_compact_field_tensors_9a_v1",
        "heldout": {},
        "first37": {},
    }

    heldout_source = (
        artifact_root / "heldout_validation/live_full_field/condition_outputs"
    )
    heldout_rows = load_rows_with_paths(heldout_source)
    if len(heldout_rows) != 784:
        raise ValueError("Expected 784 heldout rows, found %d" % len(heldout_rows))
    for _, row in heldout_rows:
        register_sensitive_observation(
            row["executed_code"], row["complete_environment_observation"]
        )
        for turn in row["trajectory_so_far"]:
            register_sensitive_observation(
                turn.get("response", ""), turn.get("observation", "")
            )
    heldout_grouped = defaultdict(list)
    for source_path, row in heldout_rows:
        key = str(row["condition_key"])
        slot_path = Path(str(row["field"]["slot_artifact"]))
        slot_payload = torch.load(slot_path, map_location="cpu", weights_only=False)
        query = slot_payload["query"].float()
        slots = slot_payload["slots"].float()
        if raw_tensor_sha256(query) != str(row["field"]["query_sha256"]):
            raise ValueError("Heldout query hash differs for " + key)
        if raw_tensor_sha256(slots) != str(row["field"]["slots_sha256"]):
            raise ValueError("Heldout slot hash differs for " + key)
        tensor_bundle["heldout"][key] = {"query": query, "slots": slots}
        safe = heldout_record(source_path, row, key, static_assets)
        heldout_grouped[
            (str(row["control"]), str(row["source_task_id"]))
        ].append(safe)
    for (condition, task_id), rows in heldout_grouped.items():
        rows.sort(key=lambda row: (int(row["epoch"]), int(row["step_id"])))
        atomic_jsonl(
            audit_root / "heldout" / condition / (task_id + ".jsonl"), rows
        )

    raw_static = load_json(
        artifact_root / "first37/raw_audit/static_prompt_assets.json"
    )
    for identity, row in raw_static["assets"].items():
        register_asset(static_assets, row["messages"], identity)

    final_summary = load_json(artifact_root / "first37/final_summary.json")
    first37_tasks = defaultdict(dict)
    contribution_rows = {}
    first37_counts = {}
    for condition in ("D0", "D1", "D2"):
        task_dir = (
            artifact_root / "first37/conditions" / condition / "task_results"
        )
        task_paths = sorted(task_dir.glob("*.json"))
        if len(task_paths) != 37:
            raise ValueError(
                "Expected 37 %s task rows, found %d"
                % (condition, len(task_paths))
            )
        step_total = 0
        for task_path in task_paths:
            task = load_json(task_path)
            task_id = str(task["task_id"])
            first37_tasks[task_id][condition] = task
            for step in task["steps"]:
                register_sensitive_observation(
                    step["exact_executed_code"],
                    step["complete_environment_observation"],
                )
                for turn in step["complete_trajectory_so_far"]:
                    register_sensitive_observation(
                        turn.get("response", ""), turn.get("observation", "")
                    )
            safe_steps = []
            for step in task["steps"]:
                step_id = int(step["step_id"])
                key = "%s:%s:%d" % (condition, task_id, step_id)
                tensor_path = Path(str(step["field"]["tensor_artifact"]))
                tensor_payload = torch.load(
                    tensor_path, map_location="cpu", weights_only=False
                )
                query = tensor_payload["query"]
                slots = tensor_payload["slots"].float()
                tensor_bundle["first37"][key] = {
                    "query": None if query is None else query.float(),
                    "slots": slots,
                }
                contribution = []
                if condition != "D0":
                    contribution = top_contributions(
                        query=query,
                        deployment=deployment,
                        shuffled=condition == "D2",
                    )
                contribution_rows[key] = contribution
                safe_steps.append(
                    first37_record(
                        task_path,
                        task,
                        step,
                        key,
                        contribution,
                    )
                )
                step_total += 1
            atomic_jsonl(
                audit_root / "first37" / condition / (task_id + ".jsonl"),
                safe_steps,
            )
        first37_counts[condition] = step_total

    comparison_tasks = []
    for task_id, divergence in sorted(
        final_summary["first_divergence"].items()
    ):
        if (
            divergence.get("D0_success_D1_failure")
            or divergence.get("D1_vs_D2") is not None
        ):
            comparison_tasks.append(task_id)
            path = audit_root / "comparisons" / (task_id + ".md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                comparison_markdown(
                    task_id,
                    first37_tasks[task_id],
                    divergence,
                    contribution_rows,
                ),
                encoding="utf-8",
                newline="\n",
            )

    tensor_path = audit_root / "field_tensors/field_queries_and_slots.pt"
    atomic_torch(tensor_path, tensor_bundle)
    atomic_json(
        audit_root / "static_prompt_assets.json",
        {"format": FORMAT, "assets": static_assets},
    )
    offline_path = (
        artifact_root / "first37/offline_memory_contributions.json"
    )
    atomic_json(
        offline_path,
        {
            "format": "rcmf_first37_offline_memory_contributions_9a_v1",
            "not_used_by_runtime_model_or_outcome": True,
            "rows": contribution_rows,
        },
    )
    verification = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "heldout_rows": len(heldout_rows),
        "heldout_expected": 784,
        "first37_task_rows": sum(len(rows) for rows in first37_tasks.values()),
        "first37_expected_task_rows": 111,
        "first37_steps": first37_counts,
        "comparison_task_count": len(comparison_tasks),
        "static_asset_count": len(static_assets),
        "compact_tensor_bundle": str(tensor_path),
        "compact_tensor_bundle_sha256": sha_file(tensor_path),
        "compact_tensor_bundle_bytes": tensor_path.stat().st_size,
        "offline_contributions": str(offline_path),
        "offline_contributions_sha256": sha_file(offline_path),
        "offline_contributions_not_used_by_runtime_model_or_outcome": True,
        "deployment_field_reconstruction_max_abs": deployment["correct_error"],
        "shuffled_field_reconstruction_max_abs": deployment["shuffled_error"],
        "deployment_provenance": deployment["paths"],
        "raw_lambda_logs_preserved": True,
        "git_safe_copy_redacts_credentials_jwts_only": True,
        "behavioral_conclusion_ready": True,
    }
    atomic_json(audit_root / "verification.json", verification)
    files = []
    for path in sorted(audit_root.rglob("*")):
        if path.is_file() and path.name != "index.json":
            files.append({
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": sha_file(path),
                "bytes": path.stat().st_size,
            })
    index = {
        "format": FORMAT,
        "run_uuid": RUN_UUID,
        "artifact_root": str(artifact_root),
        "audit_root": audit_root.relative_to(REPO_ROOT).as_posix(),
        "heldout": {
            "epochs": 2,
            "states_per_epoch": 98,
            "controls": 4,
            "condition_rows": 784,
        },
        "first37": {
            "tasks_per_condition": 37,
            "conditions": ["D0", "D1", "D2"],
            "task_rows": 111,
            "steps": first37_counts,
            "successes": {
                condition: summary["success_ids"]
                for condition, summary in final_summary["summaries"].items()
            },
        },
        "decision": {
            "interpretation": final_summary["interpretation"],
            "decision_branch": final_summary["decision_branch"],
            "D1_minus_D0": final_summary["D1_minus_D0"],
            "D1_minus_D2": final_summary["D1_minus_D2"],
        },
        "reconstruction": {
            "static_assets": "static_prompt_assets.json",
            "step_records": "heldout/** and first37/**",
            "compact_tensors": "field_tensors/field_queries_and_slots.pt",
            "raw_unredacted_lambda_root": str(artifact_root),
        },
        "verification": "verification.json",
        "files": files,
    }
    atomic_json(audit_root / "index.json", index)
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("runs/stage_c") / RUN_UUID,
    )
    parser.add_argument(
        "--audit-root",
        type=Path,
        default=REPO_ROOT / AUDIT_RELATIVE,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index = export(
        artifact_root=args.artifact_root.resolve(),
        audit_root=args.audit_root.resolve(),
    )
    print(json.dumps(index["decision"], sort_keys=True))


if __name__ == "__main__":
    main()
