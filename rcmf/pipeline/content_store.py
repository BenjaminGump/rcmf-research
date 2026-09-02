from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rcmf.pipeline.manifests import canonical_json_bytes, content_sha256
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, ensure_dir, sha256_file


class ContentAddressedStore:
    def __init__(self, root: str | Path) -> None:
        self.root = ensure_dir(root)

    def _path(self, digest: str, suffix: str) -> Path:
        return self.root / digest[:2] / f"{digest}{suffix}"

    def put_json(self, value: Any) -> dict[str, Any]:
        digest = content_sha256(value)
        path = self._path(digest, ".json")
        if not path.exists():
            atomic_write_json(path, value)
        return {"sha256": digest, "path": str(path), "size_bytes": path.stat().st_size}

    def put_text(self, value: str) -> dict[str, Any]:
        import hashlib

        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        path = self._path(digest, ".txt")
        if not path.exists():
            atomic_write_text(path, value)
        return {"sha256": digest, "path": str(path), "size_bytes": path.stat().st_size}

    def put_file(self, source: str | Path) -> dict[str, Any]:
        source = Path(source)
        digest = sha256_file(source)
        path = self._path(digest, source.suffix or ".bin")
        ensure_dir(path.parent)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".tmp")
            with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
                for block in iter(lambda: source_handle.read(1024 * 1024), b""):
                    target_handle.write(block)
            if sha256_file(temporary) != digest:
                temporary.unlink(missing_ok=True)
                raise IOError(f"Content-addressed copy validation failed for {source}")
            os.replace(temporary, path)
        return {"sha256": digest, "path": str(path), "size_bytes": path.stat().st_size}

    def validate(self, identity: dict[str, Any]) -> Path:
        path = Path(str(identity["path"]))
        if not path.exists() or sha256_file(path) != str(identity["sha256"]):
            raise ValueError(f"Content object differs: {identity}")
        return path

