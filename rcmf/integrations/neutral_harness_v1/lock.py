from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from rcmf.pipeline.manifests import content_sha256


LOCK_FORMAT = "rcmf_neutral_harness_v1_lock_v1"
FINAL_HARNESS_SOURCE_SHA = "827ed6f394804834e93444c9bb02c435e9e238a3"
FINAL_HARNESS_RECORDS_SHA = "3c197d15c80c85ad478fc4c502a7b3e9f6aad7cf"
RCMF_INTEGRATION_IMPLEMENTATION_SHA = "ca93ed71a747c5c1ba0cac3d2659636ca936f092"
HARNESS_PROTOCOL_VERSION = "agent_memory_harness_v1_rc1"

FINAL_SCHEMA_IDENTITIES = {
    "benchmark.lock.schema.json": (
        "eef634d20e64056bcb8c3cecd3706af550ad672ae94308aabb6f48a7fea3ecba"
    ),
    "method.lock.schema.json": (
        "d53db62e63ca3ebb53bbd2852aef6c3c75c3c25af682ba5af04242426953f2a3"
    ),
    "method_state_manifest.schema.json": (
        "aa609ee0fbd9765c68a8bba8a351e2611105d7a4b517d27ed6e109a666e269df"
    ),
    "result_manifest.schema.json": (
        "42cff7d50035523eb6cdb8b4941a65b05c629d5dac13a3eae70d3b11a9cbec46"
    ),
    "run_manifest.schema.json": (
        "ea11e5a711f4d01804fb1f8d1800283681c2d6dd9c636558494c377a5fb0af5b"
    ),
    "service_identity.schema.json": (
        "71664915a2d5a9f341778f0dc74b70c72d0c90153887fcfd6c6aea25cd339958"
    ),
    "task_result.schema.json": (
        "86bff4ee627ceb9fa76403a9462de059d85a6fdac3d70ff62b574c88ace12553"
    ),
}

_EXPECTED_TOP_LEVEL_FIELDS = {
    "compatibility_naming_debt",
    "dependency_direction",
    "final_archive_ref",
    "final_executable_source_sha",
    "final_records",
    "final_tag",
    "format",
    "harness_imports_rcmf",
    "harness_repository",
    "last_verified_utc",
    "protocol_identity",
    "rcmf_integration_review_records_sha",
    "rcmf_integration_source_sha",
    "rcmf_plugin_identity",
    "release_branch",
    "release_manifest",
    "schema_hash_basis",
    "schema_identities",
}


class NeutralHarnessV1LockError(ValueError):
    """The RCMF integration does not match the frozen Neutral Harness V1 release."""


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise NeutralHarnessV1LockError(
            f"{label} fields differ (missing={missing}, extra={extra})"
        )


def _require_sha(value: Any, length: int, label: str) -> str:
    text = str(value)
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", text):
        raise NeutralHarnessV1LockError(f"{label} must be a lowercase {length}-hex digest")
    return text


def _lf_sha256(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NeutralHarnessV1LockError(f"cannot resolve Harness checkout at {root}") from exc
    return result.stdout.strip()


def load_neutral_harness_v1_lock(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NeutralHarnessV1LockError(f"cannot load Neutral Harness V1 lock: {source}") from exc
    if not isinstance(payload, dict):
        raise NeutralHarnessV1LockError("Neutral Harness V1 lock must be a JSON object")
    validate_neutral_harness_v1_lock(payload)
    return payload


def validate_neutral_harness_v1_lock(lock: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_fields(lock, _EXPECTED_TOP_LEVEL_FIELDS, "Neutral Harness V1 lock")
    exact = {
        "format": LOCK_FORMAT,
        "harness_repository": "BenjaminGump/agent-memory-eval-harness",
        "final_executable_source_sha": FINAL_HARNESS_SOURCE_SHA,
        "final_tag": "harness-v1.0.0",
        "final_archive_ref": "archive/harness-v1-source-827ed6f",
        "release_branch": "release/harness-v1",
        "protocol_identity": HARNESS_PROTOCOL_VERSION,
        "schema_hash_basis": "git_blob_bytes_lf",
        "rcmf_integration_source_sha": RCMF_INTEGRATION_IMPLEMENTATION_SHA,
        "rcmf_integration_review_records_sha": (
            "543de32a91e20796ca6441b65b3a9e41f271c412"
        ),
        "dependency_direction": "rcmf_to_neutral_harness_protocol",
        "compatibility_naming_debt": "COMPATIBILITY_NAMING_DEBT_NON_SEMANTIC",
        "harness_imports_rcmf": False,
    }
    for field, expected in exact.items():
        if lock.get(field) != expected:
            raise NeutralHarnessV1LockError(f"Neutral Harness V1 lock differs at {field}")

    records = lock.get("final_records")
    if not isinstance(records, Mapping):
        raise NeutralHarnessV1LockError("final_records must be an object")
    _require_exact_fields(records, {"location", "sha"}, "final_records")
    if records.get("location") != "refs/heads/release/harness-v1":
        raise NeutralHarnessV1LockError("Neutral Harness V1 lock differs at final records location")
    if records.get("sha") != FINAL_HARNESS_RECORDS_SHA:
        raise NeutralHarnessV1LockError("Neutral Harness V1 lock differs at final records SHA")

    release = lock.get("release_manifest")
    if not isinstance(release, Mapping):
        raise NeutralHarnessV1LockError("release_manifest must be an object")
    _require_exact_fields(release, {"path", "git_blob_sha256"}, "release_manifest")
    if release.get("path") != "docs/HARNESS_V1_RELEASE.json":
        raise NeutralHarnessV1LockError("Neutral Harness V1 release manifest path differs")
    _require_sha(release.get("git_blob_sha256"), 64, "release manifest SHA256")

    plugin = lock.get("rcmf_plugin_identity")
    if not isinstance(plugin, Mapping):
        raise NeutralHarnessV1LockError("rcmf_plugin_identity must be an object")
    expected_plugin = {
        "module": "rcmf.integrations.neutral_harness_v1.plugin",
        "class": "RCMFNeutralHarnessPlugin",
        "method_id": "rcmf",
        "protocol_identity": HARNESS_PROTOCOL_VERSION,
    }
    _require_exact_fields(plugin, set(expected_plugin), "rcmf_plugin_identity")
    if dict(plugin) != expected_plugin:
        raise NeutralHarnessV1LockError("RCMF plugin identity differs")

    schemas = lock.get("schema_identities")
    if not isinstance(schemas, Mapping) or dict(schemas) != FINAL_SCHEMA_IDENTITIES:
        raise NeutralHarnessV1LockError("Neutral Harness V1 schema identities differ")

    verified = lock.get("last_verified_utc")
    if not isinstance(verified, str) or not verified.endswith("Z"):
        raise NeutralHarnessV1LockError("last_verified_utc must be an explicit UTC timestamp")
    try:
        datetime.fromisoformat(verified.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise NeutralHarnessV1LockError("last_verified_utc is invalid") from exc

    return {
        "passed": True,
        "lock_sha256": content_sha256(dict(lock)),
        "harness_source_sha": FINAL_HARNESS_SOURCE_SHA,
        "harness_records_sha": FINAL_HARNESS_RECORDS_SHA,
        "rcmf_integration_source_sha": RCMF_INTEGRATION_IMPLEMENTATION_SHA,
    }


def validate_neutral_harness_v1_release_evidence(
    lock: Mapping[str, Any],
    *,
    source_head: str,
    records_head: str,
    release_manifest: Mapping[str, Any],
    release_manifest_sha256: str,
    schema_sha256s: Mapping[str, str],
) -> dict[str, Any]:
    summary = validate_neutral_harness_v1_lock(lock)
    if source_head != lock["final_executable_source_sha"]:
        raise NeutralHarnessV1LockError("Harness source checkout differs from the frozen source")
    if records_head != lock["final_records"]["sha"]:
        raise NeutralHarnessV1LockError("Harness records checkout differs from the frozen records")
    if release_manifest_sha256 != lock["release_manifest"]["git_blob_sha256"]:
        raise NeutralHarnessV1LockError("Harness release manifest content hash differs")
    release_bindings = {
        "format": "neutral_harness_v1_release_manifest_v1",
        "final_executable_source_sha": lock["final_executable_source_sha"],
        "final_archive_ref": lock["final_archive_ref"],
        "final_tag": lock["final_tag"],
        "release_branch": lock["release_branch"],
        "final_records_location": lock["final_records"]["location"],
        "method_protocol_identity": lock["protocol_identity"],
        "schema_identities": lock["schema_identities"],
        "compatibility_naming_debt": lock["compatibility_naming_debt"],
    }
    for field, expected in release_bindings.items():
        if release_manifest.get(field) != expected:
            raise NeutralHarnessV1LockError(f"Harness release manifest differs at {field}")
    if dict(schema_sha256s) != dict(lock["schema_identities"]):
        raise NeutralHarnessV1LockError("Harness source schema file hashes differ")
    return {**summary, "release_evidence_passed": True}


def validate_neutral_harness_v1_checkouts(
    lock: Mapping[str, Any],
    *,
    source_root: str | Path,
    records_root: str | Path,
) -> dict[str, Any]:
    source = Path(source_root)
    records = Path(records_root)
    release_path = records / str(lock["release_manifest"]["path"])
    try:
        release_manifest = json.loads(release_path.read_text(encoding="utf-8"))
        schemas = {
            name: _lf_sha256(source / "schemas" / name)
            for name in lock["schema_identities"]
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise NeutralHarnessV1LockError("cannot read frozen Harness release evidence") from exc
    return validate_neutral_harness_v1_release_evidence(
        lock,
        source_head=_git_head(source),
        records_head=_git_head(records),
        release_manifest=release_manifest,
        release_manifest_sha256=_lf_sha256(release_path),
        schema_sha256s=schemas,
    )
