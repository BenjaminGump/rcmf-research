from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class LatencyMeasurement:
    name: str
    seconds: float
    iterations: int


def measure_latency(name: str, fn: Callable[[], object], iterations: int = 10) -> LatencyMeasurement:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    return LatencyMeasurement(
        name=name,
        seconds=(time.perf_counter() - start) / iterations,
        iterations=iterations,
    )

