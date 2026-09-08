from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from rcmf.pipeline.contracts import StageSpec


PORTABLE_CORE_VERSION = "rcmf_portable_canonical_v2"


class PortableRunMode(str, Enum):
    FULL = "full"
    SEALED_UPSTREAM_CONTINUATION = "sealed_upstream_continuation"


class PortablePhase(str, Enum):
    PROVENANCE = "P00_environment_and_data_provenance"
    SUCCESSFUL_CORPUS = "P01_successful_trajectory_corpus"
    MEMORY_LEDGER = "P02_memory_transition_ledger"
    REPRESENTATIONS = "P03_state_and_transition_representations"
    SELECTOR_SUPERVISION = "P04_addressing_selector_supervision"
    PAIRED_OUTCOMES = "P05_paired_causal_outcomes"
    TRAINING_UNITS = "P06_policy_teacher_and_training_units"
    TRAINING = "P07_writer_reader_training"
    EPOCH_DIAGNOSTICS = "P08_per_epoch_diagnostics"
    TERMINAL_CHECKPOINT = "P09_terminal_checkpoint_validation"
    DEPLOYMENT_FIELD = "P10_deployment_field"
    OFFICIAL_EVALUATION = "P11_official_evaluation_and_reporting"


@dataclass(frozen=True)
class PortableRunPolicy:
    run_mode: PortableRunMode
    prompt_profile: str
    training_epochs: int
    global_seed: int
    continuation_from: PortablePhase | None = None

    def validate(self) -> None:
        if not self.prompt_profile:
            raise ValueError("prompt_profile must be explicit")
        if self.training_epochs <= 0 or self.global_seed <= 0:
            raise ValueError("training_epochs and global_seed must be positive")
        if self.run_mode == PortableRunMode.FULL and self.continuation_from is not None:
            raise ValueError("full runs cannot specify a continuation boundary")
        if (
            self.run_mode == PortableRunMode.SEALED_UPSTREAM_CONTINUATION
            and self.continuation_from is None
        ):
            raise ValueError("continuations require a semantic phase boundary")


def build_portable_v2_stage_graph(policy: PortableRunPolicy) -> tuple[StageSpec, ...]:
    policy.validate()
    phases = list(PortablePhase)
    if policy.run_mode == PortableRunMode.SEALED_UPSTREAM_CONTINUATION:
        assert policy.continuation_from is not None
        start = phases.index(policy.continuation_from) + 1
        phases = phases[start:]
        if not phases:
            raise ValueError("continuation boundary leaves no phases to execute")
        import_stage = StageSpec(
            stage_id="P00C_sealed_upstream_boundary_validation",
            arm="portable",
            command=("{python}", "scripts/run_rcmf_portable_v2.py", "--phase", "boundary"),
            validator="portable_v2_manifest",
            expected_outputs=("stage_manifest.json",),
        )
        rows = [import_stage]
        previous = import_stage.stage_id
    else:
        rows = []
        previous = None
    for phase in phases:
        rows.append(
            StageSpec(
                stage_id=phase.value,
                arm="portable",
                dependencies=(previous,) if previous else (),
                command=(
                    "{python}",
                    "scripts/run_rcmf_portable_v2.py",
                    "--phase",
                    phase.name.lower(),
                ),
                validator="portable_v2_manifest",
                scientific=phase
                in {
                    PortablePhase.PAIRED_OUTCOMES,
                    PortablePhase.TRAINING,
                    PortablePhase.EPOCH_DIAGNOSTICS,
                    PortablePhase.OFFICIAL_EVALUATION,
                },
                uses_gpu=phase
                in {
                    PortablePhase.REPRESENTATIONS,
                    PortablePhase.PAIRED_OUTCOMES,
                    PortablePhase.TRAINING_UNITS,
                    PortablePhase.TRAINING,
                    PortablePhase.EPOCH_DIAGNOSTICS,
                    PortablePhase.OFFICIAL_EVALUATION,
                },
                expected_outputs=("stage_manifest.json",),
            )
        )
        previous = phase.value
    return tuple(rows)


def portable_stage_graph_manifest(policy: PortableRunPolicy) -> dict[str, Any]:
    stages = build_portable_v2_stage_graph(policy)
    return {
        "format": "rcmf_portable_stage_graph_v2",
        "core_version": PORTABLE_CORE_VERSION,
        "run_policy": {
            "run_mode": policy.run_mode.value,
            "prompt_profile": policy.prompt_profile,
            "training_epochs": policy.training_epochs,
            "global_seed": policy.global_seed,
            "continuation_from": (
                policy.continuation_from.value if policy.continuation_from else None
            ),
            "checkpoint_policy": "terminal_completed_epoch",
        },
        "stages": [stage.as_dict() for stage in stages],
    }
