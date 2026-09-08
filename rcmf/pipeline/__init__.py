"""Reusable, benchmark-neutral orchestration for reproducible RCMF runs."""

from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.portable_v2 import (
    ReproducibleBenchmarkAdapterV2,
    TerminalCheckpointPolicy,
    build_portable_v2_stage_graph,
)

__all__ = [
    "ArmContract",
    "PipelineContract",
    "StageSpec",
    "build_exp037a_stage_graph",
    "ReproducibleBenchmarkAdapterV2",
    "TerminalCheckpointPolicy",
    "build_portable_v2_stage_graph",
]
