from __future__ import annotations

from importlib import import_module
from typing import Any

from rcmf.pipeline.portable_v2.adapter import ReproducibleBenchmarkAdapterV2


def load_adapter_factory(reference: str) -> Any:
    if reference.count(":") != 1:
        raise ValueError("adapter factory must be an exact module:attribute reference")
    module_name, attribute = reference.split(":", 1)
    if not module_name or not attribute:
        raise ValueError("adapter factory reference is incomplete")
    module = import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise TypeError(f"adapter factory is not callable: {reference}")
    return factory


def load_adapter(reference: str, **kwargs: Any) -> ReproducibleBenchmarkAdapterV2:
    adapter = load_adapter_factory(reference)(**kwargs)
    if not isinstance(adapter, ReproducibleBenchmarkAdapterV2):
        raise TypeError(f"factory {reference} did not return a portable-v2 adapter")
    return adapter
