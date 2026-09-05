from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


KNOWN_PROMPT_PROFILES = {"full_demo", "full_demo_first_only"}

PAIRED_GENERATION_FIELD_OWNERSHIP = {
    "model_name": "shared_required_equal",
    "context_limit": "shared_required_equal",
    "temperature": "shared_required_equal",
    "top_p": "shared_required_equal",
    "do_sample": "shared_required_equal",
    "enable_thinking": "shared_required_equal",
    "prompt_profile": "arm_resolved_authoritative_override",
    "max_new_tokens": "legacy_paired_causal_contract",
    "dtype": "legacy_paired_causal_contract",
    "device_map": "legacy_paired_causal_contract",
}


def canonical_payload_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return str(left) == str(right)


def resolve_effective_paired_causal_runtime(
    *,
    replay_config: Mapping[str, Any],
    arm_config: Mapping[str, Any],
    arm_id: str,
    arm_config_path: str,
    arm_config_sha256: str,
    replay_config_path: str,
    replay_config_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if arm_id not in {"3d", "1d"}:
        raise ValueError(f"Unknown paired-causal arm: {arm_id}")
    replay_settings = copy.deepcopy(replay_config["stage_c_7b"])
    legacy_generation = copy.deepcopy(
        replay_settings["causal_audit"]["generation"]
    )
    arm_settings = arm_config["stage_c_7hr"]
    arm_appworld = arm_settings["appworld"]
    prompt_profile = str(arm_appworld.get("prompt_profile", ""))
    if prompt_profile not in KNOWN_PROMPT_PROFILES:
        raise ValueError(
            f"Missing or unknown arm-resolved prompt profile: {prompt_profile!r}"
        )

    profile_sources = {
        "benchmark.prompt_profile": arm_config.get("benchmark", {}).get(
            "prompt_profile"
        ),
        "stage_c_7c.generation.prompt_profile": arm_config.get(
            "stage_c_7c", {}
        )
        .get("generation", {})
        .get("prompt_profile"),
        "stage_c_7hr.appworld.prompt_profile": prompt_profile,
        "stage_c_9a.appworld.prompt_profile": arm_config.get("stage_c_9a", {})
        .get("appworld", {})
        .get("prompt_profile"),
        "stage_c_11b.prompt_profile": arm_config.get("stage_c_11b", {}).get(
            "prompt_profile"
        ),
    }
    profile_mismatches = {
        key: value for key, value in profile_sources.items() if value != prompt_profile
    }
    if profile_mismatches:
        raise ValueError(
            f"Resolved arm prompt-profile sources disagree: {profile_mismatches}"
        )

    do_sample = arm_appworld.get("do_sample")
    if do_sample is None:
        do_sample = arm_config.get("stage_c_9a", {}).get("appworld", {}).get(
            "do_sample"
        )

    shared_fields = {
        "model_name": arm_settings["expected_model_name"],
        "context_limit": arm_appworld["context_limit"],
        "temperature": arm_appworld["temperature"],
        "top_p": arm_appworld["top_p"],
        "do_sample": do_sample,
        "enable_thinking": arm_appworld["enable_thinking"],
    }
    shared_checks = {
        key: {
            "legacy_value": legacy_generation.get(key),
            "arm_value": value,
            "equal": _equal(legacy_generation.get(key), value),
        }
        for key, value in shared_fields.items()
    }
    failed = [key for key, row in shared_checks.items() if not row["equal"]]
    if failed:
        raise ValueError(
            f"Shared paired-causal generation settings disagree: {failed}"
        )

    effective_generation = copy.deepcopy(legacy_generation)
    effective_generation["prompt_profile"] = prompt_profile
    replay_settings["causal_audit"]["generation"] = effective_generation
    changed_fields = sorted(
        key
        for key in set(legacy_generation) | set(effective_generation)
        if legacy_generation.get(key) != effective_generation.get(key)
    )
    expected_changed = [] if arm_id == "3d" else ["prompt_profile"]
    if changed_fields != expected_changed:
        raise ValueError(
            "Effective paired-causal config changed outside the arm prompt "
            f"contract: {changed_fields}"
        )

    provenance = {
        "format": "exp037a_effective_paired_causal_runtime_14k_v1",
        "arm_id": arm_id,
        "arm_resolved_prompt_profile": prompt_profile,
        "legacy_replay_prompt_profile": str(
            legacy_generation["prompt_profile"]
        ),
        "effective_runtime_prompt_profile": str(
            effective_generation["prompt_profile"]
        ),
        "arm_config_path": arm_config_path,
        "arm_config_sha256": arm_config_sha256,
        "replay_config_path": replay_config_path,
        "replay_config_sha256": replay_config_sha256,
        "field_ownership": dict(PAIRED_GENERATION_FIELD_OWNERSHIP),
        "profile_sources": profile_sources,
        "shared_field_checks": shared_checks,
        "changed_execution_fields": changed_fields,
        "legacy_causal_generation_config_sha256": canonical_payload_sha256(
            legacy_generation
        ),
        "effective_causal_generation_config_sha256": canonical_payload_sha256(
            effective_generation
        ),
        "effective_replay_settings_sha256": canonical_payload_sha256(
            replay_settings
        ),
        "three_demo_effective_generation_diff": (
            len(changed_fields) if arm_id == "3d" else None
        ),
    }
    return replay_settings, provenance
