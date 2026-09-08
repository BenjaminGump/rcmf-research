from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from rcmf.utils.serialization import sha256_file


class ArtifactOwner(str, Enum):
    CURRENT_RUN = "CURRENT_RUN"
    SEALED_UPSTREAM = "SEALED_UPSTREAM"
    SHARED_IMMUTABLE_EXTERNAL = "SHARED_IMMUTABLE_EXTERNAL"


@dataclass(frozen=True)
class ArtifactReference:
    logical_name: str
    path: str
    sha256: str
    owner: ArtifactOwner
    producer_manifest_sha256: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactReference":
        return cls(
            logical_name=str(value.get("logical_name", "")),
            path=str(value.get("path", "")),
            sha256=str(value.get("sha256", "")),
            owner=ArtifactOwner(value.get("owner")),
            producer_manifest_sha256=str(value.get("producer_manifest_sha256", "")),
        )


class ArtifactResolver:
    """Resolve only explicitly owned artifacts; directory-search fallback is forbidden."""

    def __init__(self, manifest: Mapping[str, Mapping[str, Any]]) -> None:
        self._manifest = {
            str(name): ArtifactReference.from_mapping(value)
            for name, value in manifest.items()
        }

    def resolve(self, logical_name: str, *, expected_owner: ArtifactOwner) -> Path:
        try:
            reference = self._manifest[logical_name]
        except KeyError as error:
            raise KeyError(f"artifact ownership is not declared: {logical_name}") from error
        if reference.logical_name != logical_name:
            raise ValueError("artifact logical name differs from its manifest key")
        if reference.owner != expected_owner:
            raise ValueError(
                f"artifact {logical_name} is owned by {reference.owner.value}, "
                f"not {expected_owner.value}"
            )
        if len(reference.producer_manifest_sha256) != 64:
            raise ValueError("artifact producer-manifest identity is incomplete")
        path = Path(reference.path).expanduser().resolve(strict=False)
        if not path.is_file() or sha256_file(path) != reference.sha256:
            raise ValueError(f"artifact content identity differs: {logical_name}")
        return path
