from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from rcmf.utils.serialization import atomic_write_json, sha256_file, to_jsonable


STAGE_IDENTITY_KEYS = (
    "source_commit",
    "run_uuid",
    "run_root",
    "pipeline_config_sha256",
    "contract_sha256",
    "stage_id",
    "attempt_id",
)


def stage_identity_payload(
    *,
    source_commit: str,
    run_uuid: str,
    run_root: str | Path,
    pipeline_config_sha256: str,
    contract_sha256: str,
    stage_id: str,
    attempt_id: str,
    require_complete: bool,
) -> dict[str, str]:
    payload = {
        "source_commit": str(source_commit),
        "run_uuid": str(run_uuid),
        "run_root": str(Path(run_root).expanduser().resolve(strict=False)),
        "pipeline_config_sha256": str(pipeline_config_sha256),
        "contract_sha256": str(contract_sha256),
        "stage_id": str(stage_id),
        "attempt_id": str(attempt_id),
    }
    missing = [key for key in STAGE_IDENTITY_KEYS if not payload[key]]
    if require_complete and missing:
        raise PermissionError(
            f"Formal stage identity is incomplete: {missing}"
        )
    return payload


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        to_jsonable(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_identity(path: str | Path, root: str | Path | None = None) -> dict[str, Any]:
    source = Path(path)
    relative = source.resolve()
    if root is not None:
        relative = source.resolve().relative_to(Path(root).resolve())
    return {
        "path": str(relative).replace("\\", "/"),
        "size_bytes": source.stat().st_size,
        "sha256": sha256_file(source),
    }


def file_manifest(paths: Iterable[str | Path], root: str | Path) -> dict[str, Any]:
    rows = [file_identity(path, root) for path in sorted(map(Path, paths), key=lambda p: str(p))]
    payload = {"format": "content_addressed_file_manifest_v1", "files": rows}
    payload["manifest_sha256"] = content_sha256(payload)
    return payload


def write_content_manifest(path: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body.pop("manifest_sha256", None)
    body["manifest_sha256"] = content_sha256(body)
    atomic_write_json(path, body)
    return body


def validate_content_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    recorded = payload.pop("manifest_sha256", None)
    actual = content_sha256(payload)
    if recorded != actual:
        raise ValueError(f"Manifest hash differs for {source}: {recorded} != {actual}")
    payload["manifest_sha256"] = recorded
    return payload
