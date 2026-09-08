from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from rcmf.pipeline.portable_v2.checkpoint_policy import CHECKPOINT_POLICY_NAME
from rcmf.pipeline.portable_v2.dag import PortablePhase, PortableRunMode, PortableRunPolicy


@dataclass(frozen=True)
class PortablePipelineConfig:
    schema_version: str
    adapter_factory: str
    dataset_profile: str
    run_root_template: str
    policy: PortableRunPolicy

    @classmethod
    def load(cls, path: str | Path) -> "PortablePipelineConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("portable pipeline config must be a mapping")
        runtime = payload.get("runtime")
        if not isinstance(runtime, Mapping):
            raise ValueError("portable pipeline config is missing runtime policy")
        checkpoint_policy = str(runtime.get("checkpoint_policy", ""))
        if checkpoint_policy != CHECKPOINT_POLICY_NAME:
            raise ValueError(
                f"portable-v2 requires checkpoint_policy={CHECKPOINT_POLICY_NAME}"
            )
        continuation = runtime.get("continuation_from")
        policy = PortableRunPolicy(
            run_mode=PortableRunMode(runtime.get("run_mode", "full")),
            prompt_profile=str(runtime.get("prompt_profile", "")),
            training_epochs=int(runtime.get("training_epochs", 0)),
            global_seed=int(runtime.get("global_seed", 0)),
            continuation_from=(
                next(phase for phase in PortablePhase if phase.value == continuation)
                if continuation
                else None
            ),
        )
        policy.validate()
        result = cls(
            schema_version=str(payload.get("schema_version", "")),
            adapter_factory=str(payload.get("adapter_factory", "")),
            dataset_profile=str(payload.get("dataset_profile", "")),
            run_root_template=str(payload.get("run_root_template", "")),
            policy=policy,
        )
        if result.schema_version != "rcmf_portable_pipeline_config_v2":
            raise ValueError("portable pipeline config schema differs")
        if not result.adapter_factory or ":" not in result.adapter_factory:
            raise ValueError("adapter_factory must be an explicit module:factory reference")
        if not result.dataset_profile or not result.run_root_template:
            raise ValueError("dataset_profile and run_root_template are required")
        return result
