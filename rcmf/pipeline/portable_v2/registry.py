from __future__ import annotations

from collections.abc import Callable

from rcmf.pipeline.portable_v2.adapter import ReproducibleBenchmarkAdapterV2


AdapterFactory = Callable[..., ReproducibleBenchmarkAdapterV2]


class AdapterRegistry:
    """Explicit adapter registry with no benchmark-specific fallback."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, adapter_id: str, factory: AdapterFactory) -> None:
        if not adapter_id or ":" not in adapter_id:
            raise ValueError("adapter_id must be a versioned namespace such as dataset:v1")
        if adapter_id in self._factories:
            raise ValueError(f"adapter already registered: {adapter_id}")
        self._factories[adapter_id] = factory

    def resolve(self, adapter_id: str, **kwargs: object) -> ReproducibleBenchmarkAdapterV2:
        try:
            factory = self._factories[adapter_id]
        except KeyError as error:
            raise KeyError(f"no adapter registered for {adapter_id!r}; fallback is prohibited") from error
        return factory(**kwargs)

    def registered_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
