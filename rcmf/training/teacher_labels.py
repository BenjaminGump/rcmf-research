from __future__ import annotations

from dataclasses import dataclass

from rcmf.schemas import DecisionExample, MemoryRecord


@dataclass
class UtilityLabel:
    memory_id: str
    episode_id: str
    step_id: int
    utility: float
    source: str


def cheap_utility_label(record: MemoryRecord, example: DecisionExample) -> UtilityLabel:
    """Rule label based on task/API overlap.

    This is intentionally cheap and deterministic. Higher-quality teacher CE
    and paired rollout labels should be generated offline and stored.
    """

    score = 0.0
    source_bits: list[str] = []
    if record.task_id == example.metadata.get("task_id"):
        score += 0.25
        source_bits.append("same_task")
    record_apps = set(record.metadata.get("apps", []))
    example_apps = set(example.metadata.get("apps", []))
    if record_apps and example_apps and record_apps.intersection(example_apps):
        score += 0.5
        source_bits.append("shared_app")
    record_tools = set(record.metadata.get("tools", []))
    example_tools = set(example.metadata.get("tools", []))
    if record_tools and example_tools and record_tools.intersection(example_tools):
        score += 0.75
        source_bits.append("shared_tool")
    if record.success:
        score += 0.1
    return UtilityLabel(
        memory_id=record.memory_id,
        episode_id=example.episode_id,
        step_id=example.step_id,
        utility=min(score, 1.0),
        source="+".join(source_bits) or "weak_default",
    )

