from __future__ import annotations

from typing import Any, Mapping

from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.adapter import ReproducibleBenchmarkAdapterV2
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig


HARNESS_PROTOCOL_VERSION = "agent_memory_harness_v1_rc1"


class NeutralHarnessBindingError(ValueError):
    pass


def portable_dataset_semantic_evidence(
    config: PortablePipelineConfig,
    adapter: ReproducibleBenchmarkAdapterV2,
) -> dict[str, Any]:
    """Derive benchmark-lock semantics from adapter records, never its name."""
    identity = adapter.identity()
    identity.validate()
    profile = config.dataset_profile
    tasks = adapter.list_tasks()
    sources = tuple(adapter.trajectory_sources())
    split_document = {
        "format": "rcmf_neutral_harness_split_identity_v1",
        "profile_splits": dict(sorted(profile.splits.items())),
        "task_ids_by_split": {
            str(split): [row.task_id for row in rows] for split, rows in sorted(tasks.items())
        },
    }
    source_document = {
        "format": "rcmf_neutral_harness_trajectory_source_identity_v1",
        "profile_trajectory_source": dict(sorted(profile.trajectory_source.items())),
        "adapter_trajectory_sources": [
            {
                "source_id": source.source_id,
                "provenance": source.provenance.value,
                "source_identity": dict(source.source_identity),
                "training_splits": list(source.training_splits),
            }
            for source in sources
        ],
    }
    action_document = {
        "format": "rcmf_neutral_harness_action_semantics_v1",
        "action_semantics": identity.action_semantics,
    }
    reward_document = {
        "format": "rcmf_neutral_harness_reward_semantics_v1",
        "reward_semantics": identity.reward_semantics,
    }
    return {
        "benchmark_id": identity.benchmark_name,
        "dataset_profile_sha256": profile.sha256,
        "adapter_identity": config.adapter_factory,
        "split_identity_sha256": content_sha256(split_document),
        "trajectory_source_identity_sha256": content_sha256(source_document),
        "action_semantics_sha256": content_sha256(action_document),
        "reward_semantics_sha256": content_sha256(reward_document),
        "documents": {
            "split_identity": split_document,
            "trajectory_source_identity": source_document,
            "action_semantics": action_document,
            "reward_semantics": reward_document,
        },
    }


def validate_neutral_harness_benchmark_lock(
    benchmark_lock: Mapping[str, Any],
    *,
    config: PortablePipelineConfig,
    adapter: ReproducibleBenchmarkAdapterV2,
) -> dict[str, Any]:
    if benchmark_lock.get("schema_version") != "agent_memory_benchmark_lock_v1_rc1":
        raise NeutralHarnessBindingError("neutral-harness benchmark lock schema differs")
    if benchmark_lock.get("harness_protocol_version") != HARNESS_PROTOCOL_VERSION:
        raise NeutralHarnessBindingError("neutral-harness protocol version differs")
    expected = portable_dataset_semantic_evidence(config, adapter)
    for field in (
        "benchmark_id",
        "dataset_profile_sha256",
        "adapter_identity",
        "split_identity_sha256",
        "trajectory_source_identity_sha256",
        "action_semantics_sha256",
        "reward_semantics_sha256",
    ):
        if benchmark_lock.get(field) != expected[field]:
            raise NeutralHarnessBindingError(f"neutral-harness benchmark lock differs at {field}")
    return {
        "harness_protocol_version": HARNESS_PROTOCOL_VERSION,
        "semantic_identity": {key: expected[key] for key in expected if key != "documents"},
        "passed": True,
    }
