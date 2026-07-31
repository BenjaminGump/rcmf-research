from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WriteDecisionType = Literal["ADD", "REPLACE", "DELETE", "UNCERTAIN"]


@dataclass
class WriteDecision:
    decision: WriteDecisionType
    target_memory_id: str | None = None
    reason: str = ""


class ExplicitIdWriteController:
    """Core v1 write controller for known-memory-id edits."""

    def decide(
        self,
        requested_action: WriteDecisionType,
        target_memory_id: str | None = None,
    ) -> WriteDecision:
        if requested_action == "ADD":
            return WriteDecision("ADD", reason="explicit add")
        if requested_action in {"REPLACE", "DELETE"}:
            if not target_memory_id:
                return WriteDecision("UNCERTAIN", reason="target memory_id is required")
            return WriteDecision(requested_action, target_memory_id, f"explicit {requested_action.lower()}")
        return WriteDecision("UNCERTAIN", reason="unsupported or ambiguous write request")


class CandidateSearchWriteController(ExplicitIdWriteController):
    """Optional write-time extension placeholder.

    This search is never used during normal task inference. It exists only for
    experiments labelled RCMF+WriteController.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def decide_from_text(self, new_experience_text: str) -> WriteDecision:
        if not self.enabled:
            return WriteDecision("ADD", reason="candidate search disabled")
        return WriteDecision("UNCERTAIN", reason="relation classifier is not configured")

