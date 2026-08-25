from __future__ import annotations

from rcmf.training.cross_attention_memory_8b import (
    render_observation_excluded_transition,
)


def test_transition_renderer_excludes_post_action_observation() -> None:
    rendered = render_observation_excluded_transition(
        {
            "source_task_goal": "goal",
            "canonical_pre_action_state": "state",
            "complete_action": "action",
            "complete_post_action_observation": "forbidden outcome",
        }
    )
    assert "goal" in rendered and "state" in rendered and "action" in rendered
    assert "forbidden outcome" not in rendered
