from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from rcmf.integrations.neutral_harness_v1.lock import (
    FINAL_HARNESS_RECORDS_SHA,
    FINAL_HARNESS_SOURCE_SHA,
    NeutralHarnessV1LockError,
    load_neutral_harness_v1_lock,
    validate_neutral_harness_v1_release_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "configs" / "harness" / "neutral_harness_v1.lock.json"


def _lock() -> dict:
    return load_neutral_harness_v1_lock(LOCK_PATH)


def _release(lock: dict) -> dict:
    return {
        "format": "neutral_harness_v1_release_manifest_v1",
        "final_executable_source_sha": lock["final_executable_source_sha"],
        "final_archive_ref": lock["final_archive_ref"],
        "final_tag": lock["final_tag"],
        "release_branch": lock["release_branch"],
        "final_records_location": lock["final_records"]["location"],
        "method_protocol_identity": lock["protocol_identity"],
        "schema_identities": deepcopy(lock["schema_identities"]),
        "compatibility_naming_debt": lock["compatibility_naming_debt"],
    }


def _validate(lock: dict, release: dict | None = None, schemas: dict | None = None) -> dict:
    return validate_neutral_harness_v1_release_evidence(
        lock,
        source_head=FINAL_HARNESS_SOURCE_SHA,
        records_head=FINAL_HARNESS_RECORDS_SHA,
        release_manifest=release or _release(lock),
        release_manifest_sha256=lock["release_manifest"]["git_blob_sha256"],
        schema_sha256s=schemas or deepcopy(lock["schema_identities"]),
    )


def test_final_neutral_harness_lock_and_release_evidence_pass() -> None:
    result = _validate(_lock())
    assert result["passed"]
    assert result["release_evidence_passed"]
    assert result["harness_source_sha"] == FINAL_HARNESS_SOURCE_SHA


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("final_executable_source_sha", "0" * 40),
        ("protocol_identity", "other-protocol"),
        ("dependency_direction", "neutral_harness_imports_rcmf"),
        ("harness_imports_rcmf", True),
    ),
)
def test_final_neutral_harness_lock_rejects_identity_mismatch(field: str, value: object) -> None:
    lock = _lock()
    lock[field] = value
    with pytest.raises(NeutralHarnessV1LockError):
        _validate(lock)


def test_final_neutral_harness_lock_rejects_source_checkout_mismatch() -> None:
    lock = _lock()
    with pytest.raises(NeutralHarnessV1LockError, match="source checkout"):
        validate_neutral_harness_v1_release_evidence(
            lock,
            source_head="0" * 40,
            records_head=FINAL_HARNESS_RECORDS_SHA,
            release_manifest=_release(lock),
            release_manifest_sha256=lock["release_manifest"]["git_blob_sha256"],
            schema_sha256s=lock["schema_identities"],
        )


def test_final_neutral_harness_lock_rejects_schema_file_mismatch() -> None:
    lock = _lock()
    schemas = deepcopy(lock["schema_identities"])
    schemas["task_result.schema.json"] = "0" * 64
    with pytest.raises(NeutralHarnessV1LockError, match="schema file hashes"):
        _validate(lock, schemas=schemas)


def test_final_neutral_harness_lock_rejects_release_manifest_mismatch() -> None:
    lock = _lock()
    release = _release(lock)
    release["final_tag"] = "floating-tag"
    with pytest.raises(NeutralHarnessV1LockError, match="final_tag"):
        _validate(lock, release=release)


def test_final_neutral_harness_lock_has_no_dataset_benchmark_identity() -> None:
    lock = _lock()
    serialized = json.dumps(lock, sort_keys=True)
    assert "alfworld" not in serialized.lower()
    assert "webshop" not in serialized.lower()
    assert "benchmark_lock_sha256" not in lock
