from __future__ import annotations

from scripts.prepare_cross_attention_field_8b_v2 import decision_task_id


def test_decision_task_id_uses_metadata_schema() -> None:
    assert decision_task_id({"metadata": {"task_id": "task_2"}}) == "task_2"


def test_decision_task_id_falls_back_to_episode_suffix() -> None:
    assert decision_task_id({"episode_id": "appworld:trace:task_3"}) == "task_3"
