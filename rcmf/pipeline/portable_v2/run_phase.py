from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path
from typing import Any, Mapping

from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import PortablePhase, execution_phases_for_policy
from rcmf.pipeline.portable_v2.executor import (
    PortableExecutionIdentity,
    PortableDependencyOwner,
    PortableDependencyReference,
    PortablePhaseContext,
    execute_and_validate_phase,
    load_executor_factory,
    validate_executor_instance,
)


def _reference(reference: str) -> Any:
    if reference.count(":") != 1:
        raise ValueError("runtime binding must be module:attribute")
    module, attribute = reference.split(":", 1)
    value = getattr(import_module(module), attribute, None)
    if not callable(value):
        raise TypeError(f"runtime binding is not callable: {reference}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--phase", required=True)
    args = parser.parse_args()
    config = PortablePipelineConfig.load(args.config)
    phase = next(
        (row for row in PortablePhase if row.name.lower() == args.phase or row.value == args.phase),
        None,
    )
    if phase is None or phase not in execution_phases_for_policy(config.policy):
        raise ValueError(f"phase is outside the configured semantic DAG: {args.phase}")
    payload = json.loads(args.context.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("portable phase context must be a mapping")
    identity = PortableExecutionIdentity(
        source_commit=str(payload["source_commit"]),
        run_uuid=str(payload["run_uuid"]),
        run_root=Path(str(payload["run_root"])).resolve(strict=False),
        pipeline_config_sha256=str(payload["pipeline_config_sha256"]),
        dataset_profile_sha256=str(payload["dataset_profile_sha256"]),
        adapter_identity=str(payload["adapter_identity"]),
    )
    if identity.pipeline_config_sha256 != config.sha256:
        raise ValueError("runtime context config SHA differs")
    if identity.dataset_profile_sha256 != config.dataset_profile.sha256:
        raise ValueError("runtime context dataset-profile SHA differs")
    if identity.adapter_identity != config.adapter_factory:
        raise ValueError("runtime context adapter identity differs")
    kwargs_factory = _reference(str(payload["executor_kwargs_factory"]))
    kwargs = kwargs_factory(config=config, payload=payload)
    if not isinstance(kwargs, Mapping):
        raise TypeError("executor kwargs factory did not return a mapping")
    factory = load_executor_factory(
        config.phase_executor_factory,
        adapter_factory=config.adapter_factory,
        required_phases=execution_phases_for_policy(config.policy),
    )
    executor = factory(**kwargs)
    validate_executor_instance(
        executor,
        required_phases=execution_phases_for_policy(config.policy),
    )
    dependency_manifests = []
    for value in payload.get("dependency_manifests", ()):
        if isinstance(value, str):
            dependency_manifests.append(PortableDependencyReference.same_run(value))
            continue
        if not isinstance(value, Mapping):
            raise TypeError("dependency manifest reference must be a path or mapping")
        owner = PortableDependencyOwner(value.get("owner"))
        if owner == PortableDependencyOwner.SAME_RUN:
            dependency_manifests.append(
                PortableDependencyReference.same_run(str(value.get("path", "")))
            )
        else:
            dependency_manifests.append(
                PortableDependencyReference.sealed_upstream(
                    str(value.get("path", "")),
                    closure_path=str(value.get("closure_path", "")),
                )
            )
    context = PortablePhaseContext(
        identity=identity,
        phase=phase,
        dependency_manifests=tuple(dependency_manifests),
        input_artifacts={
            key: Path(value) for key, value in payload.get("input_artifacts", {}).items()
        },
        output_root=Path(str(payload["output_root"])).resolve(strict=False),
        policy=dict(payload.get("policy", {})),
    )
    print(json.dumps(execute_and_validate_phase(executor, context), sort_keys=True))


if __name__ == "__main__":
    main()
