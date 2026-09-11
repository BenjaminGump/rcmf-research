"""Fail-closed reader for the Harness-owned ALFWorld Track R execution lock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    GENERATION_IDENTITY_SHA256,
    MODEL_REVISION,
    TRACK_R_ID,
)
from rcmf.benchmarks.alfworld.runtime_agent import (
    FROZEN_ATTENTION_IMPLEMENTATION,
    FROZEN_EINOPS_VERSION,
    FROZEN_FLASH_ATTN_VERSION,
    sha256_file,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    TRACK_R_TASK_IDS_SHA256,
    canonical_sha256,
)


TRACK_R_ROLE = "UPSTREAM_PROTOCOL_REFERENCE"
TRACK_R_TASK_COUNT = 134
GENERATION_IDENTITY_V1_SHA256 = (
    "6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9"
)
TRACK_R_LOCK_IDENTITY_SHA256 = (
    "5cb89b2d602162044daf8bd14c03b822f405086b09bf77de8c06c2b09ae9a72f"
)
TRACK_R_LOCK_FILE_SHA256 = (
    "f89d3bb2fe59eff40082a8fa3d3749d3be8ed2db0fe5e9bb6bf8b322e17f4361"
)
V1_LOCK_IDENTITY_SHA256 = (
    "055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de"
)
ACTION_EXTRACTION_V2 = (
    "first decoded line; strip whitespace; remove at most one leading > transcript "
    "marker plus following whitespace; reject empty; preserve remainder"
)
TRACK_R_EVALUATION_ORDER_SHA256 = (
    "c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8"
)
CHAT_TEMPLATE_SHA256 = (
    "a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8"
)


class ALFWorldExecutionLockError(ValueError):
    pass


def lock_identity(payload: Mapping[str, Any]) -> str:
    identity_payload = dict(payload)
    identity_payload.pop("lock_identity_sha256", None)
    return canonical_sha256(identity_payload)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def load_execution_lock(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve(strict=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ALFWorldExecutionLockError("ALFWorld execution lock must be an object")
    schema_version = payload.get("schema_version")
    if schema_version == "alfworld_track_r_execution_lock_v1":
        expected_status = "FROZEN_FOR_MATCHED_BARE_AND_RCMF_EXECUTION"
        expected_generation = GENERATION_IDENTITY_V1_SHA256
    elif schema_version == "alfworld_track_r_execution_lock_v2":
        expected_status = "FROZEN_FOR_MATCHED_BARE_AND_RCMF_CORRECTIVE_EXECUTION"
        expected_generation = GENERATION_IDENTITY_SHA256
    else:
        raise ALFWorldExecutionLockError("ALFWorld execution lock schema version differs")
    if payload.get("status") != expected_status or payload.get("final_benchmark_lock") is not True:
        raise ALFWorldExecutionLockError("ALFWorld execution lock status differs")
    track = payload.get("track") or {}
    if (
        track.get("track_id") != TRACK_R_ID
        or track.get("role") != TRACK_R_ROLE
        or track.get("expected_task_count") != TRACK_R_TASK_COUNT
        or track.get("task_ids_sha256") != TRACK_R_TASK_IDS_SHA256
    ):
        raise ALFWorldExecutionLockError("ALFWorld Track R identity differs")
    model = payload.get("model_context") or {}
    generation = payload.get("generation") or {}
    if (
        model.get("model_revision") != MODEL_REVISION
        or model.get("tokenizer_revision") != MODEL_REVISION
        or model.get("chat_template_sha256") != CHAT_TEMPLATE_SHA256
        or generation.get("identity_sha256") != expected_generation
        or generation.get("environment_action_cap") != 49
        or generation.get("max_new_tokens") != 512
        or generation.get("do_sample") is not False
        or generation.get("stopping_criteria") != []
    ):
        raise ALFWorldExecutionLockError("ALFWorld model/generation identity differs")
    if schema_version == "alfworld_track_r_execution_lock_v2":
        supersedes = payload.get("supersedes") or {}
        if (
            generation.get("action_extraction") != ACTION_EXTRACTION_V2
            or generation.get("previous_identity_sha256") != GENERATION_IDENTITY_V1_SHA256
            or generation.get("think_detection_after_normalization") is not True
            or supersedes.get("v1_lock_identity_sha256") != V1_LOCK_IDENTITY_SHA256
        ):
            raise ALFWorldExecutionLockError("ALFWorld action-boundary identity differs")
    runtime = payload.get("runtime_execution") or {}
    if (
        runtime.get("deterministic_evaluation_order_sha256")
        != TRACK_R_EVALUATION_ORDER_SHA256
        or runtime.get("attention_implementation")
        != FROZEN_ATTENTION_IMPLEMENTATION
        or runtime.get("flash_attn_version") != FROZEN_FLASH_ATTN_VERSION
        or runtime.get("einops_version") != FROZEN_EINOPS_VERSION
        or runtime.get("microbatch_max_size") != 16
        or runtime.get("left_padding") != "exact attention mask; no truncation"
        or not _is_sha256(runtime.get("flash_attn_installation_manifest_sha256"))
    ):
        raise ALFWorldExecutionLockError("ALFWorld runtime execution identity differs")
    actual_identity = lock_identity(payload)
    if payload.get("lock_identity_sha256") != actual_identity:
        raise ALFWorldExecutionLockError("ALFWorld execution lock content identity differs")
    if schema_version == "alfworld_track_r_execution_lock_v2" and (
        actual_identity != TRACK_R_LOCK_IDENTITY_SHA256
        or sha256_file(source) != TRACK_R_LOCK_FILE_SHA256
    ):
        raise ALFWorldExecutionLockError("ALFWorld v2 lock portable identity differs")
    return {
        "path": str(source),
        "file_sha256": sha256_file(source),
        "lock_identity_sha256": actual_identity,
        "payload": payload,
    }
