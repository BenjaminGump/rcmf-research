from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from rcmf.pipeline.portable_v2.adapter import (
    required_capabilities_for_phases,
    validate_adapter_capabilities,
)
from rcmf.pipeline.portable_v2.checkpoint_policy import CHECKPOINT_POLICY_NAME
from rcmf.pipeline.portable_v2.dag import (
    PortablePhase,
    PortableRunMode,
    PortableRunPolicy,
    execution_phases_for_policy,
    phases_for_policy,
)
from rcmf.pipeline.portable_v2.executor import load_executor_factory
from rcmf.pipeline.portable_v2.loader import load_adapter
from rcmf.utils.serialization import sha256_file


PIPELINE_CONFIG_VERSION = "rcmf_portable_pipeline_config_v2_1"
DATASET_PROFILE_VERSION = "rcmf_dataset_profile_v2_1"

REQUIRED_SAFETY = {
    "authorization_required": True,
    "no_metric_checkpoint_selection": True,
    "no_checkpoint_fallback": True,
    "model_frozen": True,
    "selector_frozen_after_training": True,
    "runtime_retrieval": False,
    "raw_memory_in_query_prompt": False,
}


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _factory_ref(value: Any, name: str) -> str:
    result = _required_text(value, name)
    if result.count(":") != 1 or any(not part for part in result.split(":")):
        raise ValueError(f"{name} must be an explicit module:factory reference")
    return result


@dataclass(frozen=True)
class DatasetProfile:
    path: Path
    sha256: str
    benchmark: str
    adapter_identity: str
    dataset_version: str
    environment_version: str
    prompt_profiles: Mapping[str, str]
    ownership: Mapping[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "DatasetProfile":
        source = Path(path).expanduser().resolve(strict=True)
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("dataset profile must be a mapping")
        if payload.get("schema_version") != DATASET_PROFILE_VERSION:
            raise ValueError("dataset profile schema differs")
        allowed = {
            "schema_version",
            "benchmark",
            "adapter_identity",
            "dataset_version",
            "environment_version",
            "action_semantics",
            "reward_semantics",
            "prompt_profiles",
            "trajectory_source",
            "splits",
            "ownership",
            "status",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown dataset profile fields: {sorted(unknown)}")
        prompts = payload.get("prompt_profiles")
        if not isinstance(prompts, Mapping) or not prompts:
            raise ValueError("dataset prompt profiles must bind names to asset SHA256 values")
        for name, digest in prompts.items():
            if not str(name).strip() or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
                raise ValueError("dataset prompt profile identity is incomplete")
        ownership = payload.get("ownership")
        if not isinstance(ownership, Mapping) or not ownership:
            raise ValueError("dataset ownership must be explicit")
        required_text = ("benchmark", "adapter_identity", "dataset_version", "environment_version")
        if any(not isinstance(payload.get(name), str) or not payload[name].strip() for name in required_text):
            raise ValueError("dataset identity fields must be non-empty strings")
        return cls(
            path=source,
            sha256=sha256_file(source),
            benchmark=str(payload["benchmark"]),
            adapter_identity=str(payload["adapter_identity"]),
            dataset_version=str(payload["dataset_version"]),
            environment_version=str(payload["environment_version"]),
            prompt_profiles={str(key): str(value) for key, value in prompts.items()},
            ownership=dict(ownership),
        )


@dataclass(frozen=True)
class PortablePipelineConfig:
    path: Path
    sha256: str
    schema_version: str
    adapter_factory: str
    adapter_factory_kwargs: Mapping[str, Any]
    phase_executor_factory: str
    dataset_profile_path: Path
    dataset_profile: DatasetProfile
    run_root_template: str
    policy: PortableRunPolicy
    safety: Mapping[str, bool]

    @classmethod
    def load(cls, path: str | Path) -> "PortablePipelineConfig":
        source = Path(path).expanduser().resolve(strict=True)
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("portable pipeline config must be a mapping")
        allowed = {
            "schema_version",
            "adapter_factory",
            "adapter_factory_kwargs",
            "phase_executor_factory",
            "dataset_profile",
            "run_root_template",
            "runtime",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown portable pipeline fields: {sorted(unknown)}")
        if payload.get("schema_version") != PIPELINE_CONFIG_VERSION:
            raise ValueError("portable pipeline config schema differs")
        runtime = payload.get("runtime")
        if not isinstance(runtime, Mapping):
            raise ValueError("portable pipeline config is missing runtime policy")
        runtime_allowed = {
            "run_mode",
            "continuation_from",
            "prompt_profile",
            "global_seed",
            "training_epochs",
            "checkpoint_policy",
            *REQUIRED_SAFETY,
        }
        runtime_unknown = set(runtime) - runtime_allowed
        if runtime_unknown:
            raise ValueError(f"unknown runtime safety fields: {sorted(runtime_unknown)}")
        if runtime.get("checkpoint_policy") != CHECKPOINT_POLICY_NAME:
            raise ValueError(f"portable-v2.1 requires checkpoint_policy={CHECKPOINT_POLICY_NAME}")
        for name, required in REQUIRED_SAFETY.items():
            if name not in runtime or runtime[name] is not required:
                raise ValueError(f"runtime safety field {name} must be {required}")
        continuation = runtime.get("continuation_from")
        policy = PortableRunPolicy(
            run_mode=PortableRunMode(runtime.get("run_mode", "full")),
            prompt_profile=_required_text(runtime.get("prompt_profile"), "prompt_profile"),
            training_epochs=int(runtime.get("training_epochs", 0)),
            global_seed=int(runtime.get("global_seed", 0)),
            continuation_from=(
                next((phase for phase in PortablePhase if phase.value == continuation), None)
                if continuation
                else None
            ),
        )
        if continuation and policy.continuation_from is None:
            raise ValueError(f"unknown continuation phase: {continuation}")
        policy.validate()
        adapter_factory = _factory_ref(payload.get("adapter_factory"), "adapter_factory")
        executor_factory = _factory_ref(payload.get("phase_executor_factory"), "phase_executor_factory")
        kwargs = payload.get("adapter_factory_kwargs", {})
        if not isinstance(kwargs, Mapping):
            raise TypeError("adapter_factory_kwargs must be a mapping")
        dataset_value = _required_text(payload.get("dataset_profile"), "dataset_profile")
        dataset_path = Path(os.path.expandvars(dataset_value)).expanduser()
        if not dataset_path.is_absolute():
            dataset_path = (source.parent.parent.parent / dataset_path).resolve(strict=False)
        profile = DatasetProfile.load(dataset_path)
        run_root = _required_text(payload.get("run_root_template"), "run_root_template")
        if re.findall(r"\$\{[^}]+\}", os.path.expandvars(run_root)):
            raise ValueError("run-root environment expansion is unresolved")
        if "{run_uuid}" not in run_root or "{benchmark}" not in run_root:
            raise ValueError("run-root template must bind benchmark and run_uuid")
        result = cls(
            path=source,
            sha256=sha256_file(source),
            schema_version=PIPELINE_CONFIG_VERSION,
            adapter_factory=adapter_factory,
            adapter_factory_kwargs=dict(kwargs),
            phase_executor_factory=executor_factory,
            dataset_profile_path=dataset_path,
            dataset_profile=profile,
            run_root_template=run_root,
            policy=policy,
            safety=dict(REQUIRED_SAFETY),
        )
        result.validate_bindings()
        return result

    def validate_bindings(self) -> dict[str, Any]:
        phases = execution_phases_for_policy(self.policy)
        load_executor_factory(
            self.phase_executor_factory,
            adapter_factory=self.adapter_factory,
            required_phases=phases,
        )
        adapter = load_adapter(self.adapter_factory, **self.adapter_factory_kwargs)
        identity = adapter.identity()
        identity.validate()
        if identity.benchmark_name != self.dataset_profile.benchmark:
            raise ValueError("adapter benchmark differs from dataset profile")
        if identity.data_version != self.dataset_profile.dataset_version:
            raise ValueError("adapter data version differs from dataset profile")
        if identity.environment_version != self.dataset_profile.environment_version:
            raise ValueError("adapter environment version differs from dataset profile")
        if self.dataset_profile.adapter_identity != self.adapter_factory:
            raise ValueError("dataset profile adapter identity differs from config")
        profiles = adapter.prompt_profiles()
        if self.policy.prompt_profile not in profiles:
            raise ValueError("requested prompt profile is unavailable from adapter")
        expected_prompt_sha = self.dataset_profile.prompt_profiles.get(self.policy.prompt_profile)
        if profiles[self.policy.prompt_profile].asset_manifest_sha256 != expected_prompt_sha:
            raise ValueError("adapter prompt asset identity differs from dataset profile")
        capabilities = required_capabilities_for_phases(
            phase.value for phase in phases_for_policy(self.policy)
        )
        validation = validate_adapter_capabilities(adapter, capabilities)
        return {
            "config_sha256": self.sha256,
            "dataset_profile_sha256": self.dataset_profile.sha256,
            "adapter_identity": self.adapter_factory,
            "phase_executor_factory": self.phase_executor_factory,
            "phases": [phase.value for phase in phases],
            "capabilities": validation,
            "passed": True,
        }

    def resolve_run_root(self, *, run_uuid: str) -> Path:
        rendered = os.path.expandvars(self.run_root_template).format(
            benchmark=self.dataset_profile.benchmark,
            run_uuid=_required_text(run_uuid, "run_uuid"),
        )
        if re.search(r"\$\{[^}]+\}|\{[^}]+\}", rendered):
            raise ValueError("run-root template remains unresolved")
        return Path(rendered).expanduser().resolve(strict=False)
