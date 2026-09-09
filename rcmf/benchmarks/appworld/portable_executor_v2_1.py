from __future__ import annotations

from collections.abc import Callable, Mapping
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


APPWORLD_ADAPTER_FACTORY = (
    "rcmf.benchmarks.appworld.portable_adapter_v2:create_appworld_portable_adapter_v2_1"
)


class AppWorldLegacyCompatibilityExecutorV2_1:
    """Adapter-owned bridge from portable phases to bounded validated AppWorld work."""

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
                f"no executable AppWorld handler is bound for {context.phase.value}"
            ) from exc
        result = handler(context)
        if not isinstance(result, PortablePhaseWork):
            raise PortableExecutionError("AppWorld phase handler returned an invalid work record")
        return result


def create_appworld_portable_executor_v2_1(
    *,
    phase_handlers: Mapping[str, Callable[[PortablePhaseContext], PortablePhaseWork]],
    **_: Any,
) -> AppWorldLegacyCompatibilityExecutorV2_1:
    return AppWorldLegacyCompatibilityExecutorV2_1(phase_handlers)


bind_executor_factory(
    create_appworld_portable_executor_v2_1,
    PortableExecutorBinding(
        protocol_version=EXECUTOR_PROTOCOL_VERSION,
        adapter_factory=APPWORLD_ADAPTER_FACTORY,
        supported_phases=tuple(phase.value for phase in PortablePhase),
    ),
)
