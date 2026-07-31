from __future__ import annotations

from rcmf.benchmarks.appworld.adapter import AppWorldAdapter
from rcmf.benchmarks.appworld.data import extract_code_and_fix_content, render_appworld_experience
from rcmf.config import load_config


def test_extract_code_and_fix_partial_block() -> None:
    code, fixed = extract_code_and_fix_content("Code:\n```python\nprint(1)")
    assert code == "print(1)"
    assert fixed.endswith("```")


def test_render_experience_contains_required_sections() -> None:
    text = render_appworld_experience(
        {
            "task_instruction": "Do the thing",
            "steps": [{"observation": "obs", "action": "act"}],
            "success": True,
            "reward": 1.0,
        }
    )
    for section in ["[BENCHMARK]", "[TASK]", "[STEPS]", "[OUTCOME]"]:
        assert section in text


def test_adapter_render_state_no_appworld_import_needed() -> None:
    cfg = load_config("configs/base.yaml")
    adapter = AppWorldAdapter(cfg)
    state_text = adapter.render_state(type("Env", (), {"task": type("Task", (), {"instruction": "x"})()})(), [])
    assert "[TASK]" in state_text
    assert "x" in state_text


def test_appworld_target_extraction_prefers_code() -> None:
    cfg = load_config("configs/base.yaml")
    adapter = AppWorldAdapter(cfg)

    target, target_type = adapter._extract_target(
        {"ground_truth": {"compiled_solution_code": "print(1)", "answer": "fallback"}}
    )

    assert target == "print(1)"
    assert target_type == "code"


def test_appworld_target_extraction_does_not_use_final_answer_as_action() -> None:
    cfg = load_config("configs/base.yaml")
    adapter = AppWorldAdapter(cfg)

    target, target_type = adapter._extract_target(
        {"ground_truth": {"solution_code": "", "answer": "A Love That Never Was"}}
    )

    assert target == ""
    assert target_type == "code"
