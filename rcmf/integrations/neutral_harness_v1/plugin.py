from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from rcmf.integrations.neutral_harness_v1.protocol_fixture import (
    HARNESS_PROTOCOL_VERSION,
    MethodCapability,
)


@dataclass(frozen=True)
class RCMFHarnessIdentity:
    source_commit: str
    method_config_sha256: str
    terminal_checkpoint_sha256: str
    deployment_field_sha256: str

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise ValueError("RCMF harness source commit must be a full SHA")
        for name in (
            "method_config_sha256",
            "terminal_checkpoint_sha256",
            "deployment_field_sha256",
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(f"RCMF harness {name} must be a SHA256")


class RCMFMethodBridge(Protocol):
    def prepare_method(self, owned_inputs: Mapping[str, Any]) -> None: ...

    def prepare_training(self, owned_inputs: Mapping[str, Any]) -> None: ...

    def train_or_load(self) -> Mapping[str, Any]: ...

    def bind_reader(self, model: Any, context: Mapping[str, Any], *, field_sha256: str) -> Any: ...

    def begin_evaluation_run(self, run_context: Mapping[str, Any]) -> None: ...

    def reset_episode(self, episode_context: Mapping[str, Any]) -> None: ...

    def end_episode(self, episode_context: Mapping[str, Any]) -> None: ...

    def finalize_run(self) -> None: ...

    def audit_manifest(self) -> Mapping[str, Any]: ...


class RCMFNeutralHarnessPlugin:
    """Thin RC1 lifecycle binding; RCMF mathematics stays in the injected bridge."""

    method_id = "rcmf"

    def __init__(
        self,
        bridge: RCMFMethodBridge,
        identity: RCMFHarnessIdentity,
        *,
        capability_enum: Any = MethodCapability,
    ) -> None:
        identity.validate()
        self._bridge = bridge
        self._identity = identity
        self._capabilities = frozenset(
            {
                capability_enum.PREPARE_TRAINING,
                capability_enum.MODEL_FORWARD_HOOK,
                capability_enum.OFFLINE_MEMORY_COMPILE,
            }
        )
        self._loaded = False

    def method_identity(self) -> Mapping[str, Any]:
        return {
            "method_id": self.method_id,
            "protocol_version": HARNESS_PROTOCOL_VERSION,
            "source_commit": self._identity.source_commit,
            "method_config_sha256": self._identity.method_config_sha256,
        }

    def required_capabilities(self) -> frozenset[Any]:
        return self._capabilities

    def prepare_method(self, owned_inputs: Mapping[str, Any]) -> None:
        self._bridge.prepare_method(owned_inputs)

    def prepare_training(self, owned_inputs: Mapping[str, Any]) -> None:
        self._bridge.prepare_training(owned_inputs)

    def train_or_load(self) -> None:
        loaded = self._bridge.train_or_load()
        expected = {
            "terminal_checkpoint_sha256": self._identity.terminal_checkpoint_sha256,
            "deployment_field_sha256": self._identity.deployment_field_sha256,
        }
        for key, value in expected.items():
            if loaded.get(key) != value:
                raise ValueError(f"RCMF loaded state differs at {key}")
        self._loaded = True

    def wrap_or_prepare_model_forward(self, model: Any, context: Mapping[str, Any]) -> Any:
        if not self._loaded:
            raise RuntimeError("RCMF state must be loaded before reader binding")
        return self._bridge.bind_reader(
            model,
            context,
            field_sha256=self._identity.deployment_field_sha256,
        )

    def begin_evaluation_run(self, run_context: Mapping[str, Any]) -> None:
        self._bridge.begin_evaluation_run(run_context)

    def begin_episode(self, episode_context: Mapping[str, Any]) -> None:
        self._bridge.reset_episode(episode_context)

    def end_episode(self, episode_context: Mapping[str, Any]) -> None:
        self._bridge.end_episode(episode_context)

    def finalize_run(self) -> None:
        self._bridge.finalize_run()

    def audit_manifest(self) -> Mapping[str, Any]:
        audit = dict(self._bridge.audit_manifest())
        audit.update(
            {
                "method_id": self.method_id,
                "protocol_version": HARNESS_PROTOCOL_VERSION,
                "source_commit": self._identity.source_commit,
                "method_config_sha256": self._identity.method_config_sha256,
                "terminal_checkpoint_sha256": self._identity.terminal_checkpoint_sha256,
                "deployment_field_sha256": self._identity.deployment_field_sha256,
                "runtime_retrieval": False,
                "raw_memory_prompt": False,
            }
        )
        return audit
