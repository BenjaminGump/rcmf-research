from __future__ import annotations

import random
from statistics import mean

from rcmf.schemas import BenchmarkResult


def success_rate(results: list[BenchmarkResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result.success) / len(results)


def average_score(results: list[BenchmarkResult]) -> float:
    if not results:
        return 0.0
    return mean(result.score for result in results)


def bootstrap_success_ci(
    results: list[BenchmarkResult],
    samples: int = 1000,
    seed: int = 1,
    alpha: float = 0.05,
) -> tuple[float, float]:
    if not results:
        return (0.0, 0.0)
    rng = random.Random(seed)
    rates = []
    for _ in range(samples):
        draw = [rng.choice(results) for _ in results]
        rates.append(success_rate(draw))
    rates.sort()
    lo = rates[int((alpha / 2) * samples)]
    hi = rates[min(samples - 1, int((1 - alpha / 2) * samples))]
    return lo, hi


def summarize_results(results: list[BenchmarkResult]) -> dict[str, float]:
    if not results:
        return {"n": 0, "success_rate": 0.0, "average_score": 0.0}
    ci_low, ci_high = bootstrap_success_ci(results)
    return {
        "n": float(len(results)),
        "success_rate": success_rate(results),
        "success_ci_low": ci_low,
        "success_ci_high": ci_high,
        "average_score": average_score(results),
        "avg_steps": mean(result.steps for result in results),
        "avg_prompt_tokens": mean(result.prompt_tokens for result in results),
        "avg_generated_tokens": mean(result.generated_tokens for result in results),
        "avg_wall_time_s": mean(result.wall_time_s for result in results),
    }

