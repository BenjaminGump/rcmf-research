from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from rcmf.utils.serialization import atomic_write_json, sha256_file, to_jsonable


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

