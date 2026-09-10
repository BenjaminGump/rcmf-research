from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from rcmf.pipeline.portable_v2.dag import PortablePhase
from rcmf.pipeline.portable_v2.executor import (
    EXECUTOR_PROTOCOL_VERSION,
    PortableExecutorBinding,
    PortableExecutionError,
    PortablePhaseContext,
    PortablePhaseWork,
    bind_executor_factory,
)
from rcmf.utils.serialization import atomic_write_json, sha256_file


ALFWORLD_ADAPTER_FACTORY = (
    "rcmf.benchmarks.alfworld.portable_adapter_v2:create_alfworld_portable_adapter_v2_1"
)

REQUIRED_EVIDENCE_BY_PHASE = {
    PortablePhase.PROVENANCE.value: frozenset(
        {"environment_and_data_manifest", "benchmark_execution_lock"}
    ),
    PortablePhase.SUCCESSFUL_CORPUS.value: frozenset({"successful_corpus_manifest"}),
    PortablePhase.MEMORY_LEDGER.value: frozenset({"memory_ledger_manifest"}),
    PortablePhase.REPRESENTATIONS.value: frozenset({"representation_diagnostic"}),
    PortablePhase.SELECTOR_SUPERVISION.value: frozenset(
        {"addressing_supervision_diagnostic"}
    ),
    PortablePhase.PAIRED_OUTCOMES.value: frozenset({"paired_outcome_evidence"}),
    PortablePhase.TRAINING_UNITS.value: frozenset({"training_units_manifest"}),
    PortablePhase.TRAINING.value: frozenset({"training_summary"}),
    PortablePhase.EPOCH_DIAGNOSTICS.value: frozenset({"epoch_diagnostics"}),
    PortablePhase.TERMINAL_CHECKPOINT.value: frozenset({"checkpoint_validation"}),
    PortablePhase.DEPLOYMENT_FIELD.value: frozenset({"deployment_field_validation"}),
    PortablePhase.OFFICIAL_EVALUATION.value: frozenset({"official_evaluation_summary"}),
}


class ALFWorldPortablePhaseExecutorV2_1:
    """ALFWorld-owned binding of real phase handlers to the Portable V2.1 DAG."""

    protocol_version = EXECUTOR_PROTOCOL_VERSION

    def __init__(
        self,
        phase_handlers: Mapping[str, Callable[[PortablePhaseContext], PortablePhaseWork]],
    ) -> None:
        self._handlers = dict(phase_handlers)

    def bound_phase_ids(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def execute_phase(self, context: PortablePhaseContext) -> PortablePhaseWork:
        try:
            handler = self._handlers[context.phase.value]
        except KeyError as exc:
            raise PortableExecutionError(
                f"no executable ALFWorld handler is bound for {context.phase.value}"
            ) from exc
        result = handler(context)
        if not isinstance(result, PortablePhaseWork):
            raise PortableExecutionError("ALFWorld phase handler returned an invalid work record")
        return result


def evidence_phase_handlers() -> Mapping[str, Callable[[PortablePhaseContext], PortablePhaseWork]]:
    """Bind every phase to strict evidence validation and a sealed phase record."""

    def handler(context: PortablePhaseContext) -> PortablePhaseWork:
        if not context.input_artifacts:
            raise PortableExecutionError(
                f"ALFWorld phase {context.phase.value} requires real input evidence"
            )
        required = REQUIRED_EVIDENCE_BY_PHASE.get(context.phase.value)
        if required is None:
            raise PortableExecutionError(
                f"ALFWorld phase {context.phase.value} has no evidence contract"
            )
        missing = sorted(required.difference(context.input_artifacts))
        if missing:
            raise PortableExecutionError(
                f"ALFWorld phase {context.phase.value} is missing required evidence: {missing}"
            )
        inputs = []
        for logical_name, path in sorted(context.input_artifacts.items()):
            source = Path(path).resolve(strict=True)
            if not source.is_file():
                raise PortableExecutionError(f"ALFWorld phase input is not a file: {source}")
            inputs.append(
                {
                    "logical_name": logical_name,
                    "path": str(source),
                    "bytes": source.stat().st_size,
                    "sha256": sha256_file(source),
                }
            )
        context.output_root.mkdir(parents=True, exist_ok=True)
        output = context.output_root / "phase_record.json"
        record = {
            "format": "alfworld_portable_phase_record_v1",
            "phase_id": context.phase.value,
            "source_commit": context.identity.source_commit,
            "run_uuid": context.identity.run_uuid,
            "inputs": inputs,
            "policy": dict(context.policy),
            "passed": True,
        }
        atomic_write_json(output, record)
        return PortablePhaseWork(
            operations=(
                {
                    "operation": "validate_and_bind_alfworld_phase_evidence",
                    "phase_id": context.phase.value,
                    "input_count": len(inputs),
                },
            ),
            output_artifacts={"phase_record": output},
            metadata={
                "benchmark": "alfworld",
                "real_evidence_bound": True,
                "input_count": len(inputs),
                "required_evidence": sorted(required),
            },
        )

    return {phase.value: handler for phase in PortablePhase}


def create_alfworld_portable_executor_v2_1(
    *,
    phase_handlers: Mapping[str, Callable[[PortablePhaseContext], PortablePhaseWork]],
    **_: Any,
) -> ALFWorldPortablePhaseExecutorV2_1:
    return ALFWorldPortablePhaseExecutorV2_1(phase_handlers)


bind_executor_factory(
    create_alfworld_portable_executor_v2_1,
    PortableExecutorBinding(
        protocol_version=EXECUTOR_PROTOCOL_VERSION,
        adapter_factory=ALFWORLD_ADAPTER_FACTORY,
        supported_phases=tuple(phase.value for phase in PortablePhase),
    ),
)
