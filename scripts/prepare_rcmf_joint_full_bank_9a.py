from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    KEY_DIM,
    SLOT_COUNT,
    FrozenSelectorDecomposition,
    deterministic_payload_permutation,
    tensor_sha256,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file, sha256_text


PREPARATION_VERSION = "rcmf_joint_full_bank_preparation_9a_v1"
SOURCE_CACHE_VERSION = "rcmf_complete_transition_eight_view_cache_9a_v1"
PROVENANCE_VERSION = "rcmf_complete_transition_provenance_9a_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(dict(row), sort_keys=True, ensure_ascii=True) + "\n"
        for row in rows
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _atomic_torch_save(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        torch.save(dict(payload), temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in _rows(path)
        if row.get("attempt_id") is not None
    }


def _task_id(row: Mapping[str, Any]) -> str:
    value = row.get("task_id")
    if value:
        return str(value)
    metadata = row.get("metadata", {})
    if isinstance(metadata, Mapping) and metadata.get("task_id"):
        return str(metadata["task_id"])
    episode = str(row.get("episode_id", ""))
    if episode:
        return episode.rsplit(":", 1)[-1]
    raise KeyError("Decision row has no task identity")


def _split_ids(payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    return (
        [str(value) for value in payload["train_task_ids"]],
        [str(value) for value in payload["validation_task_ids"]],
    )


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_a = Path(str(settings["parent_exp028a"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    return {
        "replay": parent_b / "replay_validated_corpus_manifest.json",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signatures": parent_b
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "state_cache": parent_c
        / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "cache_summary": parent_c
        / "representation_cache/multiview/clean_multiview_cache_summary.json",
        "selector_ensemble": parent_c / "selector/ensemble_scores.pt",
        "selector_root": parent_c / "selector",
        "outcomes": parent_a / "paired_causal/paired_outcomes.json",
        "teacher_cache": parent_a
        / "structured_compiler/policy_teacher_cache.pt",
        "task_split": Path(str(settings["task_split_manifest"])),
        "corpus_summary": corpus / "summary.json",
        "corpus_validation": corpus / "structural_validation.json",
        "decisions": corpus / "decision_examples.jsonl",
        "source_cache": artifact_dir / "data/rcmf_source_cache.pt",
        "provenance": artifact_dir / "data/memory_provenance.jsonl",
        "source_audit": artifact_dir / "data/source_representation_audit.json",
        "selector_audit": artifact_dir / "data/selector_decomposition_audit.json",
        "shuffle_manifest": artifact_dir / "data/key_payload_shuffle_manifest.json",
        "data_manifest": artifact_dir / "data/full_bank_data_manifest.json",
        "runtime_counts": artifact_dir / "runtime/static_counts.json",
        "run_manifest": artifact_dir / "run_manifest.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"EXP-031A immutable input missing: {missing}")


def _section_contract(
    transition: Mapping[str, Any],
    cache_row: Mapping[str, Any],
    vectors: torch.Tensor,
    *,
    lineage: str,
) -> dict[str, Any]:
    sections = (
        (
            "source_task_goal",
            "source_task_goal_sha256",
            "source_task_goal_tokens",
        ),
        (
            "canonical_pre_action_state",
            "canonical_pre_action_state_sha256",
            "canonical_pre_action_state_tokens",
        ),
        ("complete_action", "complete_action_sha256", "complete_action_tokens"),
        (
            "complete_post_action_observation",
            "complete_post_action_observation_sha256",
            "complete_post_action_observation_tokens",
        ),
    )
    section_rows = []
    for index, (text_name, hash_name, token_name) in enumerate(sections):
        text = str(transition[text_name])
        expected_hash = str(transition[hash_name])
        actual_hash = sha256_text(text)
        if actual_hash != expected_hash:
            raise ValueError(
                f"Section hash differs for {transition['transition_id']}:{text_name}"
            )
        mean_vector = vectors[2 * index]
        final_vector = vectors[2 * index + 1]
        section_rows.append(
            {
                "section": text_name,
                "text_sha256": actual_hash,
                "source_token_count": int(transition[token_name]),
                "complete_section_encoded": True,
                "chunking": {
                    "required": False,
                    "chunk_count": 1,
                    "aggregation": "single_complete_span",
                    "boundaries": ["complete_section"],
                },
                "pooling": {
                    "mean_representation_sha256": tensor_sha256(mean_vector),
                    "final_representation_sha256": tensor_sha256(final_vector),
                },
            }
        )
    return {
        "format": PROVENANCE_VERSION,
        "transition_id": str(transition["transition_id"]),
        "parent_memory_id": str(transition["parent_memory_id"]),
        "parent_task_id": str(transition["parent_task_id"]),
        "parent_episode_ids": list(transition["parent_episode_ids"]),
        "parent_replay_ids": list(transition["parent_replay_ids"]),
        "parent_lineage_ids": list(transition["parent_lineage_ids"]),
        "parent_trajectory_sha256": str(transition["parent_trajectory_sha256"]),
        "transition_content_sha256": str(transition["transition_content_sha256"]),
        "teacher_section_sha256": str(transition["teacher_section_sha256"]),
        "complete_render_token_count": int(cache_row["token_count"]),
        "representation_sha256": tensor_sha256(vectors),
        "structural_lineage_sha256": lineage,
        "source_cache_provenance": str(cache_row["provenance"]),
        "truncated": bool(cache_row["truncated"]),
        "token_subsampling": False,
        "full_transition_global_used": False,
        "sections": section_rows,
    }


def _signature_map(payload: Mapping[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in payload["classes"]:
        signature_class_id = str(row["signature_class_id"])
        for transition_id_value in row["member_transition_ids"]:
            transition_id = str(transition_id_value)
            if transition_id in output:
                raise ValueError(
                    f"Transition belongs to duplicate signature classes: {transition_id}"
                )
            output[transition_id] = signature_class_id
    return output


def _permutation_rows(
    transitions: Sequence[Mapping[str, Any]],
    signatures: Mapping[str, str],
    task_ids: set[str],
) -> dict[str, Any]:
    selected = []
    for row in transitions:
        if str(row["parent_task_id"]) not in task_ids:
            continue
        transition_id = str(row["transition_id"])
        selected.append(
            {
                "transition_id": transition_id,
                "parent_task_id": str(row["parent_task_id"]),
                "parent_memory_id": str(row["parent_memory_id"]),
                "signature_class_id": signatures[transition_id],
            }
        )
    mapping = deterministic_payload_permutation(selected)
    rows = []
    for source, target in enumerate(mapping):
        left = selected[source]
        right = selected[target]
        rows.append(
            {
                "key_transition_id": left["transition_id"],
                "payload_transition_id": right["transition_id"],
                "fixed_point": source == target,
                "different_parent_task": left["parent_task_id"]
                != right["parent_task_id"],
                "different_signature_class": left["signature_class_id"]
                != right["signature_class_id"],
                "outcomes_used": False,
            }
        )
    return {
        "memory_count": len(rows),
        "fixed_point_count": sum(bool(row["fixed_point"]) for row in rows),
        "same_task_count": sum(
            not bool(row["different_parent_task"]) for row in rows
        ),
        "same_signature_count": sum(
            not bool(row["different_signature_class"]) for row in rows
        ),
        "rows": rows,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp031a")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9a"]
    persistent_root = Path(str(settings["persistent_root"]))
    if os.name != "nt" and not os.path.ismount(persistent_root):
        raise RuntimeError("Persistent filesystem is not mounted")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")

    paths = _paths(settings, args.artifact_dir)
    required = (
        "replay",
        "transitions",
        "signatures",
        "state_cache",
        "transition_cache",
        "cache_summary",
        "selector_ensemble",
        "outcomes",
        "teacher_cache",
        "task_split",
        "corpus_summary",
        "corpus_validation",
        "decisions",
    )
    _require(paths, required)
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    started = time.perf_counter()

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="full_bank_source_and_address_preparation",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        replay = _json(paths["replay"])
        corpus = _json(paths["corpus_summary"])
        validation = _json(paths["corpus_validation"])
        expected = settings["expected"]
        lineage = str(expected["structural_lineage_sha256"])
        checks = {
            "replay_validated": bool(replay["replay_validated"]),
            "replay_lineage": str(replay["lineage_sha256"])
            == str(expected["replay_lineage_sha256"]),
            "structural_lineage": str(corpus["lineage_sha256"]) == lineage,
            "structural_validation": bool(validation["passed"]),
            "actions_observations_unchanged": bool(
                replay["actions_and_observations_unchanged"]
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(f"Clean lineage gate failed: {checks}")

        transitions = _rows(paths["transitions"])
        transition_by_id = {
            str(row["transition_id"]): row for row in transitions
        }
        if len(transition_by_id) != len(transitions):
            raise ValueError("Transition manifest contains duplicate IDs")
        decisions = _rows(paths["decisions"])
        split = _json(paths["task_split"])
        train_tasks, heldout_tasks = _split_ids(split)
        train_task_set, heldout_task_set = set(train_tasks), set(heldout_tasks)
        if train_task_set & heldout_task_set:
            raise ValueError("29/8 task split overlaps")

        transition_cache = torch.load(
            paths["transition_cache"], map_location="cpu", weights_only=False
        )
        state_cache = torch.load(
            paths["state_cache"], map_location="cpu", weights_only=False
        )
        expected_view_names = [
            "source_task_goal",
            "pre_action_state",
            "complete_action",
            "post_action_observation",
            "full_transition_global",
        ]
        if list(transition_cache["view_names"]) != expected_view_names:
            raise ValueError("Transition view order differs from the clean contract")
        if list(transition_cache["pooling_rules"]) != [
            "token_mean",
            "final_token",
        ]:
            raise ValueError("Transition pooling order differs")
        if str(transition_cache["corpus_lineage_sha256"]) != lineage:
            raise ValueError("Transition cache lineage differs")
        if str(state_cache["corpus_lineage_sha256"]) != lineage:
            raise ValueError("State cache lineage differs")
        if any(bool(row["truncated"]) for row in transition_cache["rows"]):
            raise RuntimeError("Transition cache includes truncation")
        if any(bool(row["truncated"]) for row in state_cache["rows"]):
            raise RuntimeError("State cache includes truncation")

        transition_ids = [str(value) for value in transition_cache["ordered_ids"]]
        if set(transition_ids) != set(transition_by_id):
            raise ValueError("Transition cache and clean manifest identities differ")
        all_transition_values = transition_cache["representations"][
            str(settings["memory"]["source_layer"])
        ].to(torch.float32)
        memory_views = all_transition_values[:, :SLOT_COUNT].contiguous()
        if tuple(memory_views.shape) != (len(transitions), 8, 4096):
            raise ValueError("Eight-view source tensor has the wrong shape")

        provenance_rows = []
        for position, transition_id in enumerate(transition_ids):
            provenance_rows.append(
                _section_contract(
                    transition_by_id[transition_id],
                    transition_cache["rows"][position],
                    memory_views[position],
                    lineage=lineage,
                )
            )
        if any(row["truncated"] for row in provenance_rows):
            raise RuntimeError("RCMF memory provenance contains truncation")
        _atomic_jsonl(paths["provenance"], provenance_rows)

        checkpoint_paths = sorted(
            paths["selector_root"].glob("seed_*/field_selector.pt")
        )
        checkpoints = [
            torch.load(path, map_location="cpu", weights_only=False)
            for path in checkpoint_paths
        ]
        ensemble = torch.load(
            paths["selector_ensemble"], map_location="cpu", weights_only=False
        )
        decomposition = FrozenSelectorDecomposition.from_checkpoints(
            checkpoints, ensemble["train_calibration"]
        )
        if decomposition.key_dim != KEY_DIM:
            raise ValueError(
                f"Selector decomposition yielded {decomposition.key_dim}, expected 960"
            )
        state_values = state_cache["representations"]["final_layer"].to(torch.float32)
        transition_values = all_transition_values
        state_query = decomposition.query(state_values)
        memory_key = decomposition.key(transition_values)
        direct = decomposition.direct_scores(state_values, transition_values)
        decomposed = state_query @ memory_key.T + decomposition.intercept
        stored = ensemble["scores"].to(torch.float32)
        errors = {
            "direct_vs_decomposed_max_abs": float(
                (direct - decomposed).abs().max()
            ),
            "direct_vs_stored_max_abs": float((direct - stored).abs().max()),
            "decomposed_vs_stored_max_abs": float(
                (decomposed - stored).abs().max()
            ),
        }
        tolerance = float(settings["selector"]["equality_atol"])
        if max(errors.values()) > tolerance:
            raise RuntimeError(f"Exact selector decomposition failed: {errors}")

        _atomic_torch_save(
            {
                "format": SOURCE_CACHE_VERSION,
                "global_seed": GLOBAL_SEED,
                "structural_lineage_sha256": lineage,
                "replay_lineage_sha256": str(replay["lineage_sha256"]),
                "source_layer": "final_layer",
                "view_names": expected_view_names[:4],
                "pooling_rules": ["token_mean", "final_token"],
                "ordered_transition_ids": transition_ids,
                "memory_views": memory_views,
                "memory_view_sha256": tensor_sha256(memory_views),
                "memory_keys": memory_key,
                "memory_key_sha256": tensor_sha256(memory_key),
                "ordered_state_ids": [
                    str(value) for value in state_cache["ordered_ids"]
                ],
                "state_queries": state_query,
                "state_query_sha256": tensor_sha256(state_query),
                "selector_intercept": decomposition.intercept,
                "mu_i": 0.0,
                "full_transition_global_excluded": True,
                "truncated": False,
            },
            paths["source_cache"],
        )

        signatures = _signature_map(_json(paths["signatures"]))
        if set(signatures) != set(transition_by_id):
            raise ValueError(
                "Signature equivalence map does not cover clean transitions"
            )
        train_permutation = _permutation_rows(
            transitions, signatures, train_task_set
        )
        full_permutation = _permutation_rows(
            transitions, signatures, train_task_set | heldout_task_set
        )
        if train_permutation["fixed_point_count"] or full_permutation["fixed_point_count"]:
            raise RuntimeError("Key-payload shuffle has a fixed point")
        shuffle_manifest = {
            "format": "rcmf_full_bank_key_payload_shuffle_9a_v1",
            "global_seed": GLOBAL_SEED,
            "selection_uses_outcomes": False,
            "model_training_bank": train_permutation,
            "complete_deployment_bank": full_permutation,
        }
        atomic_write_json(paths["shuffle_manifest"], shuffle_manifest)

        outcomes = _json(paths["outcomes"])
        outcome_rows = list(outcomes["rows"])
        outcome_ids = {str(row["state_example_id"]) for row in outcome_rows}
        state_ids = {str(value) for value in state_cache["ordered_ids"]}
        if not outcome_ids <= state_ids:
            raise ValueError("Paired policy outcomes contain unknown state IDs")
        train_outcomes = [
            row for row in outcome_rows if row["model_split"] == "model_train"
        ]
        heldout_outcomes = [
            row
            for row in outcome_rows
            if row["model_split"] == "heldout_train_validation"
        ]
        teacher = torch.load(
            paths["teacher_cache"], map_location="cpu", weights_only=False
        )
        if set(teacher["ordered_state_ids"]) != outcome_ids:
            raise ValueError("Policy teacher cache and paired outcome IDs differ")

        parent_counts = Counter(
            str(row["parent_memory_id"]) for row in transitions
        )
        rho = {
            str(row["transition_id"]): 1.0
            / parent_counts[str(row["parent_memory_id"])]
            for row in transitions
        }
        train_memories = [
            row
            for row in transitions
            if str(row["parent_task_id"]) in train_task_set
        ]
        heldout_memories = [
            row
            for row in transitions
            if str(row["parent_task_id"]) in heldout_task_set
        ]
        train_queries = [
            row for row in decisions if _task_id(row) in train_task_set
        ]
        heldout_queries = [
            row for row in decisions if _task_id(row) in heldout_task_set
        ]
        labels_train = Counter(str(row["label"]) for row in train_outcomes)
        labels_heldout = Counter(str(row["label"]) for row in heldout_outcomes)
        count_checks = {
            "train_tasks": len(train_tasks) == int(expected["train_task_count"]),
            "heldout_tasks": len(heldout_tasks)
            == int(expected["heldout_task_count"]),
            "train_memories": len(train_memories)
            == int(expected["complete_train_memory_count"]),
            "heldout_memories": len(heldout_memories)
            == int(expected["heldout_memory_count"]),
            "scoreable_train": len(train_outcomes)
            == int(expected["scoreable_train_state_count"]),
            "scoreable_heldout": len(heldout_outcomes)
            == int(expected["scoreable_heldout_state_count"]),
        }
        if not all(count_checks.values()):
            raise RuntimeError(f"Prepared data counts differ: {count_checks}")

        data_manifest = {
            "format": "rcmf_joint_full_bank_data_manifest_9a_v1",
            "global_seed": GLOBAL_SEED,
            "train_task_ids": train_tasks,
            "heldout_task_ids": heldout_tasks,
            "counts": {
                "model_training_memories": len(train_memories),
                "heldout_memories": len(heldout_memories),
                "model_training_query_states_all": len(train_queries),
                "heldout_query_states_all": len(heldout_queries),
                "model_training_query_states_scoreable": len(train_outcomes),
                "heldout_query_states_scoreable": len(heldout_outcomes),
                "model_training_labels": dict(sorted(labels_train.items())),
                "heldout_labels": dict(sorted(labels_heldout.items())),
            },
            "rho_by_transition_id": rho,
            "same_task_exclusion": "subtract_precompiled_task_accumulator",
            "source_cache": str(paths["source_cache"]),
            "source_cache_sha256": sha256_file(paths["source_cache"]),
            "memory_provenance": str(paths["provenance"]),
            "memory_provenance_sha256": sha256_file(paths["provenance"]),
            "shuffle_manifest": str(paths["shuffle_manifest"]),
            "shuffle_manifest_sha256": sha256_file(paths["shuffle_manifest"]),
            "clean_source_hashes": source_hashes,
        }
        atomic_write_json(paths["data_manifest"], data_manifest)

        source_audit = {
            "format": "rcmf_source_representation_audit_9a_v1",
            "decision_branch": "rcmf_source_representation_valid",
            "checks": checks,
            "count_checks": count_checks,
            "memory_count": len(provenance_rows),
            "view_shape": list(memory_views.shape),
            "view_names": expected_view_names[:4],
            "pooling_rules": ["token_mean", "final_token"],
            "all_complete_sections": True,
            "all_chunks_encoded": True,
            "arbitrary_truncation_count": 0,
            "token_subsampling_count": 0,
            "full_transition_global_used": False,
            "maximum_complete_render_tokens": max(
                int(row["complete_render_token_count"])
                for row in provenance_rows
            ),
            "source_cache_sha256": sha256_file(paths["source_cache"]),
        }
        atomic_write_json(paths["source_audit"], source_audit)

        selector_audit = {
            "format": "rcmf_selector_exact_decomposition_audit_9a_v1",
            "decision_branch": "rcmf_address_field_contract_valid",
            "selector_seed_count": len(checkpoints),
            "selector_checkpoint_paths": [str(path) for path in checkpoint_paths],
            "selector_checkpoint_sha256": [
                sha256_file(path) for path in checkpoint_paths
            ],
            "selector_ensemble_sha256": sha256_file(paths["selector_ensemble"]),
            "state_count": int(state_query.shape[0]),
            "memory_count": int(memory_key.shape[0]),
            "query_shape": list(state_query.shape),
            "key_shape": list(memory_key.shape),
            "global_intercept": decomposition.intercept,
            "global_intercept_is_memory_specific": False,
            "mu_i": 0.0,
            "errors": errors,
            "tolerance": tolerance,
            "passed": max(errors.values()) <= tolerance,
            "deployment_score_matrix_used": False,
            "deployment_top_k_used": False,
        }
        atomic_write_json(paths["selector_audit"], selector_audit)

        units_per_epoch = len(train_outcomes) + 2 * labels_train["POSITIVE"]
        runtime_counts = {
            "format": "rcmf_joint_full_bank_static_runtime_counts_9a_v1",
            "train_tasks": len(train_tasks),
            "heldout_tasks": len(heldout_tasks),
            "model_training_memories": len(train_memories),
            "heldout_memories": len(heldout_memories),
            "scoreable_train_states": len(train_outcomes),
            "scoreable_heldout_states": len(heldout_outcomes),
            "labels_train": dict(sorted(labels_train.items())),
            "labels_heldout": dict(sorted(labels_heldout.items())),
            "training_units_per_epoch": units_per_epoch,
            "locked_epoch_count": 2,
            "maximum_training_backwards": units_per_epoch * 2,
            "teacher_forced_heldout_forwards": len(heldout_outcomes) * 4 * 2,
            "heldout_live_conditions": len(heldout_outcomes) * 4 * 2,
            "conditional_first37_conditions": 37 * 3,
            "per_memory_compilations_total": len(transitions),
            "field_A_shape": [KEY_DIM, SLOT_COUNT, 256],
            "field_A_float32_bytes": KEY_DIM * SLOT_COUNT * 256 * 4,
            "field_B_float32_bytes": SLOT_COUNT * 256 * 4,
        }
        atomic_write_json(paths["runtime_counts"], runtime_counts)

        manifest = {
            "format": "rcmf_joint_full_bank_run_manifest_9a_v1",
            "run_uuid": str(settings["run_uuid"]),
            "global_seed": GLOBAL_SEED,
            "branch": str(settings["working_branch"]),
            "starting_head": str(settings["starting_head"]),
            "preparation_head": args.lambda_head,
            "config": str(args.config),
            "config_sha256": sha256_file(args.config),
            "source_hashes": source_hashes,
            "outputs": {
                name: {"path": str(paths[name]), "sha256": sha256_file(paths[name])}
                for name in (
                    "source_cache",
                    "provenance",
                    "source_audit",
                    "selector_audit",
                    "shuffle_manifest",
                    "data_manifest",
                    "runtime_counts",
                )
            },
            "all_scientific_forwards_require_complete_field": True,
            "runtime_retrieval_allowed": False,
            "exp030a_scientific_artifacts_used": False,
            "elapsed_seconds": time.perf_counter() - started,
        }
        atomic_write_json(paths["run_manifest"], manifest)
        attempt.progress(
            status="source_and_address_contract_validated",
            completed_memories=len(transitions),
            completed_states=len(outcome_rows),
            latest_validated_checkpoint=str(paths["run_manifest"]),
        )
        print(json.dumps(manifest, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
