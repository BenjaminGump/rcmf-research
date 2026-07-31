from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


TargetType = Literal["code", "tool_call", "answer", "action"]
MemoryStatus = Literal["active", "superseded", "deleted"]


@dataclass
class MemoryRecord:
    memory_id: str
    benchmark: str
    episode_id: str
    task_id: str
    raw_trajectory: dict[str, Any]
    experience_text: str
    outcome: float
    success: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "MemoryRecord":
        return cls(
            memory_id=str(values["memory_id"]),
            benchmark=str(values["benchmark"]),
            episode_id=str(values["episode_id"]),
            task_id=str(values["task_id"]),
            raw_trajectory=dict(values.get("raw_trajectory", {})),
            experience_text=str(values.get("experience_text", "")),
            outcome=float(values.get("outcome", 0.0)),
            success=bool(values.get("success", False)),
            metadata=dict(values.get("metadata", {})),
        )


@dataclass
class DecisionExample:
    benchmark: str
    episode_id: str
    step_id: int
    state_text: str
    target_text: str
    target_type: TargetType
    candidate_memory_ids: list[str] | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "DecisionExample":
        return cls(
            benchmark=str(values["benchmark"]),
            episode_id=str(values["episode_id"]),
            step_id=int(values["step_id"]),
            state_text=str(values.get("state_text", "")),
            target_text=str(values.get("target_text", "")),
            target_type=values.get("target_type", "code"),
            candidate_memory_ids=values.get("candidate_memory_ids"),
            metadata=dict(values.get("metadata", {})),
        )


@dataclass
class AgentStep:
    state_text: str
    action_text: str
    observation_text: str
    done: bool
    reward: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkResult:
    task_id: str
    success: bool
    score: float
    steps: int
    prompt_tokens: int
    generated_tokens: int
    ttft_ms: float
    wall_time_s: float
    extra_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "BenchmarkResult":
        return cls(
            task_id=str(values["task_id"]),
            success=bool(values["success"]),
            score=float(values.get("score", 0.0)),
            steps=int(values.get("steps", 0)),
            prompt_tokens=int(values.get("prompt_tokens", 0)),
            generated_tokens=int(values.get("generated_tokens", 0)),
            ttft_ms=float(values.get("ttft_ms", 0.0)),
            wall_time_s=float(values.get("wall_time_s", 0.0)),
            extra_metrics=dict(values.get("extra_metrics", {})),
        )

