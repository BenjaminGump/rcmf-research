from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.portable_v2.dag import PortablePhase
from rcmf.utils.serialization import atomic_write_json, sha256_file


EXECUTOR_PROTOCOL_VERSION = "rcmf_portable_phase_executor_v2_1"
PHASE_MANIFEST_VERSION = "rcmf_portable_phase_manifest_v2_1"
DEPENDENCY_CLOSURE_VERSION = "rcmf_portable_sealed_upstream_closure_v2_1"


class PortableExecutionError(RuntimeError):
    pass


class PortableDependencyOwner(str, Enum):
    SAME_RUN = "SAME_RUN"
    SEALED_UPSTREAM = "SEALED_UPSTREAM"


@dataclass(frozen=True)
class PortableExecutionIdentity:
    source_commit: str
    run_uuid: str
    run_root: Path
    pipeline_config_sha256: str
    dataset_profile_sha256: str
    adapter_identity: str

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise PortableExecutionError("source commit must be a full SHA")
        if not self.run_uuid or not self.adapter_identity:
            raise PortableExecutionError("run UUID and adapter identity are required")
        for name in ("pipeline_config_sha256", "dataset_profile_sha256"):
            if len(str(getattr(self, name))) != 64:
                raise PortableExecutionError(f"{name} must be a SHA256")
        if self.run_root != self.run_root.expanduser().resolve(strict=False):
            raise PortableExecutionError("run root must be canonical")

    def as_dict(self) -> dict[str, str]:
        return {
            "source_commit": self.source_commit,
            "run_uuid": self.run_uuid,
            "run_root": str(self.run_root),
            "pipeline_config_sha256": self.pipeline_config_sha256,
            "dataset_profile_sha256": self.dataset_profile_sha256,
            "adapter_identity": self.adapter_identity,
        }


@dataclass(frozen=True)
class PortableDependencyReference:
    path: Path
    owner: PortableDependencyOwner = PortableDependencyOwner.SAME_RUN
    closure_path: Path | None = None

    @classmethod
    def same_run(cls, path: str | Path) -> "PortableDependencyReference":
        return cls(Path(path), PortableDependencyOwner.SAME_RUN)

    @classmethod
    def sealed_upstream(
        cls, path: str | Path, *, closure_path: str | Path
    ) -> "PortableDependencyReference":
        return cls(
            Path(path),
            PortableDependencyOwner.SEALED_UPSTREAM,
            Path(closure_path),
        )


@dataclass(frozen=True)
class PortablePhaseContext:
    identity: PortableExecutionIdentity
    phase: PortablePhase
    dependency_manifests: tuple[Path | PortableDependencyReference, ...]
    input_artifacts: Mapping[str, Path]
    output_root: Path
    policy: Mapping[str, Any]


@dataclass(frozen=True)
class PortablePhaseWork:
    operations: tuple[Mapping[str, Any], ...]
    output_artifacts: Mapping[str, Path]
    metadata: Mapping[str, Any]


@runtime_checkable
class PortablePhaseExecutorV2(Protocol):
    protocol_version: str

    def bound_phase_ids(self) -> frozenset[str]: ...

    def execute_phase(self, context: PortablePhaseContext) -> PortablePhaseWork: ...


@dataclass(frozen=True)
class PortableExecutorBinding:
    protocol_version: str
    adapter_factory: str
    supported_phases: tuple[str, ...]

    def validate(self, *, adapter_factory: str, required_phases: Sequence[PortablePhase]) -> None:
        if self.protocol_version != EXECUTOR_PROTOCOL_VERSION:
            raise PortableExecutionError("executor protocol version differs")
        if self.adapter_factory != adapter_factory:
            raise PortableExecutionError("executor belongs to a different adapter factory")
        supported = set(self.supported_phases)
        missing = [phase.value for phase in required_phases if phase.value not in supported]
        if missing:
            raise PortableExecutionError(f"executor lacks required phases: {missing}")


def bind_executor_factory(factory: Any, binding: PortableExecutorBinding) -> Any:
    setattr(factory, "portable_executor_binding", binding)
    return factory


def load_executor_factory(
    reference: str,
    *,
    adapter_factory: str,
    required_phases: Sequence[PortablePhase],
) -> Any:
    if reference.count(":") != 1:
        raise PortableExecutionError("phase executor factory must be module:attribute")
    module_name, attribute = reference.split(":", 1)
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise PortableExecutionError(f"phase executor factory is not callable: {reference}")
    binding = getattr(factory, "portable_executor_binding", None)
    if not isinstance(binding, PortableExecutorBinding):
        raise PortableExecutionError("phase executor factory has no typed binding")
    binding.validate(adapter_factory=adapter_factory, required_phases=required_phases)
    return factory


def validate_executor_instance(
    executor: PortablePhaseExecutorV2,
    *,
    required_phases: Sequence[PortablePhase],
) -> dict[str, Any]:
    if not isinstance(executor, PortablePhaseExecutorV2):
        raise PortableExecutionError("factory did not return PortablePhaseExecutorV2")
    if executor.protocol_version != EXECUTOR_PROTOCOL_VERSION:
        raise PortableExecutionError("executor instance protocol version differs")
    try:
        bound = frozenset(executor.bound_phase_ids())
    except Exception as exc:
        raise PortableExecutionError("executor handler registry could not be inspected") from exc
    invalid = sorted(value for value in bound if not isinstance(value, str) or not value)
    if invalid:
        raise PortableExecutionError("executor handler registry contains invalid phase IDs")
    unknown = sorted(bound - {phase.value for phase in PortablePhase})
    if unknown:
        raise PortableExecutionError(
            f"executor handler registry contains unknown phases: {unknown}"
        )
    missing = [phase.value for phase in required_phases if phase.value not in bound]
    if missing:
        raise PortableExecutionError(f"executor instance lacks required handlers: {missing}")
    return {"bound_phase_ids": sorted(bound), "passed": True}


def _validated_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PortableExecutionError(f"dependency manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload.get("manifest_sha256")
    body = dict(payload)
    body.pop("manifest_sha256", None)
    if recorded != content_sha256(body):
        raise PortableExecutionError(f"dependency manifest hash differs: {path}")
    if not payload.get("passed"):
        raise PortableExecutionError(f"dependency manifest did not pass: {path}")
    return payload


def _coerce_dependency_reference(
    value: Path | PortableDependencyReference,
) -> PortableDependencyReference:
    if isinstance(value, PortableDependencyReference):
        return value
    return PortableDependencyReference.same_run(value)


def _require_manifest_identity(
    payload: Mapping[str, Any],
    expected: Mapping[str, str],
    *,
    owner: str,
) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise PortableExecutionError(f"{owner} identity differs at {key}")


def _validated_closure(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PortableExecutionError(f"sealed-upstream closure is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload.get("closure_sha256")
    body = dict(payload)
    body.pop("closure_sha256", None)
    if recorded != content_sha256(body):
        raise PortableExecutionError("sealed-upstream closure hash differs")
    if payload.get("format") != DEPENDENCY_CLOSURE_VERSION or not payload.get("passed"):
        raise PortableExecutionError("sealed-upstream closure is not strict-valid")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping):
        raise PortableExecutionError("sealed-upstream closure identity is missing")
    PortableExecutionIdentity(
        source_commit=str(identity.get("source_commit", "")),
        run_uuid=str(identity.get("run_uuid", "")),
        run_root=Path(str(identity.get("run_root", ""))).expanduser().resolve(strict=False),
        pipeline_config_sha256=str(identity.get("pipeline_config_sha256", "")),
        dataset_profile_sha256=str(identity.get("dataset_profile_sha256", "")),
        adapter_identity=str(identity.get("adapter_identity", "")),
    ).validate()
    manifests = payload.get("dependency_manifest_sha256s")
    if (
        not isinstance(manifests, list)
        or not manifests
        or any(not isinstance(value, str) or len(value) != 64 for value in manifests)
    ):
        raise PortableExecutionError("sealed-upstream closure manifest list is invalid")
    return payload


def _validated_dependency(
    value: Path | PortableDependencyReference,
    *,
    expected: PortableExecutionIdentity,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    reference = _coerce_dependency_reference(value)
    path = reference.path.expanduser().resolve(strict=True)
    payload = _validated_manifest(path)
    if reference.owner == PortableDependencyOwner.SAME_RUN:
        if reference.closure_path is not None:
            raise PortableExecutionError("same-run dependency cannot carry an upstream closure")
        _require_manifest_identity(
            payload,
            expected.as_dict(),
            owner="same-run dependency manifest",
        )
        return path, payload, {"owner": reference.owner.value}
    if reference.owner != PortableDependencyOwner.SEALED_UPSTREAM:
        raise PortableExecutionError("dependency owner is unsupported")
    if reference.closure_path is None:
        raise PortableExecutionError("sealed-upstream dependency requires an explicit closure")
    closure_path = reference.closure_path.expanduser().resolve(strict=True)
    closure = _validated_closure(closure_path)
    if payload["manifest_sha256"] not in closure["dependency_manifest_sha256s"]:
        raise PortableExecutionError("dependency manifest is absent from upstream closure")
    _require_manifest_identity(
        payload,
        {str(key): str(value) for key, value in closure["identity"].items()},
        owner="sealed-upstream dependency manifest",
    )
    return (
        path,
        payload,
        {
            "owner": reference.owner.value,
            "closure": {
                **file_identity(closure_path),
                "closure_sha256": closure["closure_sha256"],
            },
        },
    )


def execute_and_validate_phase(
    executor: PortablePhaseExecutorV2,
    context: PortablePhaseContext,
) -> dict[str, Any]:
    """Execute real phase work and seal one strict content-addressed manifest."""
    context.identity.validate()
    output_root = context.output_root.expanduser().resolve(strict=False)
    try:
        output_root.relative_to(context.identity.run_root)
    except ValueError as exc:
        raise PortableExecutionError("phase output root escaped its run root") from exc
    validate_executor_instance(executor, required_phases=(context.phase,))
    dependencies = [
        _validated_dependency(path, expected=context.identity)
        for path in context.dependency_manifests
    ]
    input_rows = []
    for logical_name, path in sorted(context.input_artifacts.items()):
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise PortableExecutionError(f"input artifact is not a file: {resolved}")
        input_rows.append({"logical_name": logical_name, **file_identity(resolved)})
    work = executor.execute_phase(context)
    if not work.operations:
        raise PortableExecutionError("phase executor reported no bounded work")
    outputs = []
    for logical_name, path in sorted(work.output_artifacts.items()):
        resolved = Path(path).expanduser().resolve(strict=True)
        try:
            resolved.relative_to(context.identity.run_root)
        except ValueError as exc:
            raise PortableExecutionError("phase output escaped its run root") from exc
        outputs.append({"logical_name": logical_name, **file_identity(resolved)})
    if not outputs:
        raise PortableExecutionError("phase executor produced no artifacts")
    manifest = {
        "format": PHASE_MANIFEST_VERSION,
        "executor_protocol_version": EXECUTOR_PROTOCOL_VERSION,
        "source_commit": context.identity.source_commit,
        "run_uuid": context.identity.run_uuid,
        "run_root": str(context.identity.run_root),
        "pipeline_config_sha256": context.identity.pipeline_config_sha256,
        "dataset_profile_sha256": context.identity.dataset_profile_sha256,
        "adapter_identity": context.identity.adapter_identity,
        "phase_id": context.phase.value,
        "dependency_manifests": [
            {
                **file_identity(path),
                "manifest_sha256": row["manifest_sha256"],
                **ownership,
            }
            for path, row, ownership in dependencies
        ],
        "dependency_manifest_sha256s": [row["manifest_sha256"] for _, row, _ in dependencies],
        "input_artifacts": input_rows,
        "output_artifacts": outputs,
        "operations": list(work.operations),
        "metadata": dict(work.metadata),
        "passed": True,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    path = context.output_root / "stage_manifest.json"
    atomic_write_json(path, manifest)
    validate_phase_manifest(path, expected=context.identity, phase=context.phase)
    return manifest


def validate_phase_manifest(
    path: str | Path,
    *,
    expected: PortableExecutionIdentity,
    phase: PortablePhase,
) -> dict[str, Any]:
    payload = _validated_manifest(Path(path))
    expected.validate()
    expected_fields = {
        "source_commit": expected.source_commit,
        "run_uuid": expected.run_uuid,
        "run_root": str(expected.run_root),
        "pipeline_config_sha256": expected.pipeline_config_sha256,
        "dataset_profile_sha256": expected.dataset_profile_sha256,
        "adapter_identity": expected.adapter_identity,
        "phase_id": phase.value,
    }
    for key, value in expected_fields.items():
        if payload.get(key) != value:
            raise PortableExecutionError(f"phase manifest identity differs at {key}")
    if payload.get("format") != PHASE_MANIFEST_VERSION:
        raise PortableExecutionError("phase manifest format differs")
    dependency_rows = payload.get("dependency_manifests", ())
    if [row.get("manifest_sha256") for row in dependency_rows] != payload.get(
        "dependency_manifest_sha256s", ()
    ):
        raise PortableExecutionError("dependency manifest hash lists differ")
    for category in ("dependency_manifests", "input_artifacts", "output_artifacts"):
        for row in payload.get(category, ()):
            artifact = Path(str(row.get("path", "")))
            if not artifact.is_file() or sha256_file(artifact) != row.get("sha256"):
                raise PortableExecutionError(f"{category} hash differs: {artifact}")
    for row in dependency_rows:
        owner = PortableDependencyOwner(row.get("owner"))
        reference = PortableDependencyReference(
            path=Path(str(row.get("path", ""))),
            owner=owner,
            closure_path=(
                Path(str(row["closure"].get("path", "")))
                if isinstance(row.get("closure"), Mapping)
                else None
            ),
        )
        _, dependency, ownership = _validated_dependency(reference, expected=expected)
        if dependency.get("manifest_sha256") != row.get("manifest_sha256"):
            raise PortableExecutionError("dependency manifest identity differs")
        if owner == PortableDependencyOwner.SEALED_UPSTREAM:
            if ownership.get("closure", {}).get("closure_sha256") != row.get("closure", {}).get(
                "closure_sha256"
            ):
                raise PortableExecutionError("sealed-upstream closure identity differs")
    return payload
