from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rcmf.pipeline.manifests import canonical_json_bytes
from rcmf.utils.serialization import sha256_file


@dataclass(frozen=True)
class PromptAssetManifest:
    profile: str
    upstream_repository: str
    upstream_commit: str
    upstream_path: str
    upstream_blob: str
    upstream_file_sha256: str
    extracted_text_sha256: str
    local_path: str
    local_sha256: str
    license: str
    extraction_method: str
    content_changed: bool
    status: str

    @classmethod
    def load(cls, path: str | Path) -> "PromptAssetManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        row = cls(**{key: payload[key] for key in cls.__dataclass_fields__})
        row.validate(Path(path).parent)
        return row

    def validate(self, manifest_dir: Path) -> None:
        for name in (
            "profile",
            "upstream_repository",
            "upstream_commit",
            "upstream_path",
            "upstream_blob",
            "upstream_file_sha256",
            "extracted_text_sha256",
            "local_path",
            "local_sha256",
            "license",
            "extraction_method",
            "status",
        ):
            if not str(getattr(self, name)):
                raise ValueError(f"prompt manifest field {name} is empty")
        if len(self.upstream_commit) != 40:
            raise ValueError("prompt upstream commit must be a full Git SHA")
        for digest in (self.upstream_file_sha256, self.extracted_text_sha256, self.local_sha256):
            if len(digest) != 64:
                raise ValueError("prompt SHA256 identity is incomplete")
        local = (manifest_dir / self.local_path).resolve(strict=False)
        if not local.is_file() or sha256_file(local) != self.local_sha256:
            raise ValueError(f"prompt local asset differs: {local}")


def message_array_sha256(messages: Sequence[Mapping[str, str]]) -> str:
    return hashlib.sha256(canonical_json_bytes(list(messages))).hexdigest()
