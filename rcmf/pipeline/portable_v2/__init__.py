"""Portable canonical RCMF pipeline contracts for prospective dataset ports."""

from rcmf.pipeline.portable_v2.adapter import (
    AdapterCapability,
    BenchmarkIdentity,
    PromptProfile,
    ReproducibleBenchmarkAdapterV2,
    TrajectorySource,
    probe_adapter_capabilities,
    validate_adapter_capabilities,
)
from rcmf.pipeline.portable_v2.checkpoint_policy import (
    CheckpointIdentityError,
    TerminalCheckpointPolicy,
)
from rcmf.pipeline.portable_v2.dag import (
    PortableRunMode,
    PortableRunPolicy,
    build_portable_v2_stage_graph,
)
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    EvaluationResult,
    ProvenanceClass,
    ReplayStatus,
    TaskRecord,
    TrajectoryRecord,
    TrajectoryStep,
    TransitionRecord,
)

__all__ = [
    "AdapterCapability",
    "BenchmarkIdentity",
    "CheckpointIdentityError",
    "DecisionStateRecord",
    "EvaluationResult",
    "PortableRunMode",
    "PortableRunPolicy",
    "PromptProfile",
    "ProvenanceClass",
    "ReplayStatus",
    "ReproducibleBenchmarkAdapterV2",
    "TaskRecord",
    "TerminalCheckpointPolicy",
    "TrajectoryRecord",
    "TrajectorySource",
    "TrajectoryStep",
    "TransitionRecord",
    "build_portable_v2_stage_graph",
    "probe_adapter_capabilities",
    "validate_adapter_capabilities",
]
