from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def render_observation_excluded_transition(transition: Mapping[str, Any]) -> str:
    """Render the authoritative raw ledger without the post-action outcome."""
    required = ("source_task_goal", "canonical_pre_action_state", "complete_action")
    missing = [name for name in required if name not in transition]
    if missing:
        raise KeyError(f"Transition is missing observation-excluded views: {missing}")
    return "\n".join(
        (
            "### External transition memory",
            "Source task goal:",
            str(transition["source_task_goal"]).strip(),
            "",
            "Pre-action state:",
            str(transition["canonical_pre_action_state"]).strip(),
            "",
            "Complete action:",
            str(transition["complete_action"]).strip(),
            "### End external transition memory",
        )
    )
