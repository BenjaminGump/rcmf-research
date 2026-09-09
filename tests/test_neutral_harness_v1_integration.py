from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from rcmf.integrations.neutral_harness_v1 import (
    NeutralHarnessBindingError,
    RCMFHarnessIdentity,
    RCMFNeutralHarnessPlugin,
    portable_dataset_semantic_evidence,
    validate_neutral_harness_benchmark_lock,
)
from rcmf.pipeline.portable_v2.adapter import (
    ADAPTER_PROTOCOL_VERSION,
    BenchmarkIdentity,
    TrajectorySource,
)
from rcmf.pipeline.portable_v2.schemas import ProvenanceClass, TaskRecord


class Capability(str, Enum):
    PREPARE_TRAINING = "prepare_training"
    MODEL_FORWARD_HOOK = "model_forward_hook"
    OFFLINE_MEMORY_COMPILE = "offline_memory_compile"


class SemanticFixtureAdapter:
    def __init__(
        self,
        *,
        benchmark: str,
        action_semantics: str,
        reward_semantics: str,
        train_split: str,
        evaluation_split: str,
        provenance: ProvenanceClass,
    ) -> None:
        self.benchmark = benchmark
        self.action_semantics = action_semantics
        self.reward_semantics = reward_semantics
        self.train_split = train_split
        self.evaluation_split = evaluation_split
        self.provenance = provenance

    def identity(self) -> BenchmarkIdentity:
        return BenchmarkIdentity(
            benchmark_name=self.benchmark,
            benchmark_version="fixture-v1",
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            environment_version="fixture-env-v1",
            data_version="fixture-data-v1",
            deterministic=True,
            action_semantics=self.action_semantics,
            reward_semantics=self.reward_semantics,
        )

    def list_tasks(self) -> Mapping[str, tuple[TaskRecord, ...]]:
        return {
            split: (
                TaskRecord(
                    self.benchmark,
                    "fixture-data-v1",
                    split,
                    f"{split}-task",
                    "goal",
                    (f"{self.benchmark}:{split}",),
                    {"fixture": True},
                ),
            )
            for split in (self.train_split, self.evaluation_split)
        }

    def trajectory_sources(self) -> tuple[TrajectorySource, ...]:
        return (
            TrajectorySource(
                "fixture-source",
                self.provenance,
                {"fixture": True},
                (self.train_split,),
            ),
        )


def _binding(
    benchmark: str,
    action: str,
    reward: str,
    train_split: str,
    evaluation_split: str,
    provenance: ProvenanceClass,
) -> tuple[Any, SemanticFixtureAdapter, dict[str, Any]]:
    adapter = SemanticFixtureAdapter(
        benchmark=benchmark,
        action_semantics=action,
        reward_semantics=reward,
        train_split=train_split,
        evaluation_split=evaluation_split,
        provenance=provenance,
    )
    profile = SimpleNamespace(
        sha256="a" * 64,
        splits={"training": train_split, "official_evaluation": evaluation_split},
        trajectory_source={"provenance": provenance.value, "policy": "fixture"},
    )
    config = SimpleNamespace(dataset_profile=profile, adapter_factory=f"fixtures:{benchmark}")
    expected = portable_dataset_semantic_evidence(config, adapter)
    lock = {
        "schema_version": "agent_memory_benchmark_lock_v1_rc1",
        "harness_protocol_version": "agent_memory_harness_v1_rc1",
        **{key: value for key, value in expected.items() if key != "documents"},
    }
    return config, adapter, lock


@pytest.mark.parametrize(
    ("benchmark", "action", "reward", "train", "evaluation", "provenance"),
    (
        (
            "appworld",
            "python_code",
            "official_binary_success",
            "train",
            "dev",
            ProvenanceClass.OFFICIAL_MODEL_OR_IL,
        ),
        (
            "alfworld",
            "text_command",
            "binary_success",
            "train",
            "eval_out_of_distribution",
            ProvenanceClass.OFFICIAL_EXPERT,
        ),
        (
            "webshop",
            "search_click",
            "continuous_optional_binary",
            "train",
            "test",
            ProvenanceClass.OFFICIAL_HUMAN,
        ),
    ),
)
def test_dataset_semantic_identity_closes_appworld_alfworld_and_webshop_like_fixtures(
    benchmark: str,
    action: str,
    reward: str,
    train: str,
    evaluation: str,
    provenance: ProvenanceClass,
) -> None:
    config, adapter, lock = _binding(benchmark, action, reward, train, evaluation, provenance)
    assert validate_neutral_harness_benchmark_lock(
        lock,
        config=config,
        adapter=adapter,
    )["passed"]
    for field in (
        "dataset_profile_sha256",
        "adapter_identity",
        "split_identity_sha256",
        "trajectory_source_identity_sha256",
        "action_semantics_sha256",
        "reward_semantics_sha256",
    ):
        changed = dict(lock)
        changed[field] = "0" * 64 if field.endswith("sha256") else "other:adapter"
        with pytest.raises(NeutralHarnessBindingError, match=field):
            validate_neutral_harness_benchmark_lock(
                changed,
                config=config,
                adapter=adapter,
            )


@dataclass
class Bridge:
    checkpoint_sha: str
    field_sha: str

    def __post_init__(self) -> None:
        self.events: list[str] = []

    def prepare_method(self, owned_inputs: Mapping[str, Any]) -> None:
        self.events.append("prepare_method")

    def prepare_training(self, owned_inputs: Mapping[str, Any]) -> None:
        self.events.append("prepare_training")

    def train_or_load(self) -> Mapping[str, Any]:
        self.events.append("train_or_load")
        return {
            "terminal_checkpoint_sha256": self.checkpoint_sha,
            "deployment_field_sha256": self.field_sha,
        }

    def bind_reader(self, model: Any, context: Mapping[str, Any], *, field_sha256: str) -> Any:
        self.events.append("bind_reader")
        return {"model": model, "field_sha256": field_sha256}

    def begin_evaluation_run(self, run_context: Mapping[str, Any]) -> None:
        self.events.append("begin_run")

    def reset_episode(self, episode_context: Mapping[str, Any]) -> None:
        self.events.append("reset_episode")

    def end_episode(self, episode_context: Mapping[str, Any]) -> None:
        self.events.append("end_episode")

    def finalize_run(self) -> None:
        self.events.append("finalize")

    def audit_manifest(self) -> Mapping[str, Any]:
        return {"bridge_events": list(self.events)}


def test_rcmf_plugin_binds_checkpoint_field_reader_and_episode_reset() -> None:
    identity = RCMFHarnessIdentity("a" * 40, "b" * 64, "c" * 64, "d" * 64)
    bridge = Bridge(identity.terminal_checkpoint_sha256, identity.deployment_field_sha256)
    plugin = RCMFNeutralHarnessPlugin(
        bridge,
        identity,
        capability_enum=Capability,
    )
    plugin.prepare_method({"task_manifest": "locked"})
    plugin.prepare_training({"trajectory_manifest": "locked"})
    plugin.train_or_load()
    wrapped = plugin.wrap_or_prepare_model_forward("frozen-model", {"step": 0})
    plugin.begin_evaluation_run({"run_uuid": "fixture"})
    plugin.begin_episode({"episode_id": "one"})
    plugin.end_episode({"episode_id": "one"})
    plugin.begin_episode({"episode_id": "two"})
    plugin.end_episode({"episode_id": "two"})
    plugin.finalize_run()
    audit = plugin.audit_manifest()
    assert wrapped["field_sha256"] == "d" * 64
    assert bridge.events.count("reset_episode") == 2
    assert audit["runtime_retrieval"] is False
    assert audit["raw_memory_prompt"] is False
    assert {item.value for item in plugin.required_capabilities()} == {
        "prepare_training",
        "model_forward_hook",
        "offline_memory_compile",
    }


def test_rcmf_plugin_rejects_checkpoint_or_field_identity_mismatch() -> None:
    identity = RCMFHarnessIdentity("a" * 40, "b" * 64, "c" * 64, "d" * 64)
    bridge = Bridge("e" * 64, identity.deployment_field_sha256)
    plugin = RCMFNeutralHarnessPlugin(bridge, identity, capability_enum=Capability)
    with pytest.raises(ValueError, match="terminal_checkpoint_sha256"):
        plugin.train_or_load()
