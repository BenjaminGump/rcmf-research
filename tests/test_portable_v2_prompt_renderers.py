from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rcmf.benchmarks.alfworld.prompt_profile import render_react_messages as render_alfworld
from rcmf.benchmarks.webshop.prompt_profile import render_react_messages as render_webshop
from scripts.extract_react_prompt_assets import extract_notebook_string


ROOT = Path(__file__).resolve().parents[1]


def test_alfworld_renderer_uses_exact_task_family_examples() -> None:
    profile_root = ROOT / "assets/prompts/alfworld/react_task_type_two_demo_v1"
    messages = render_alfworld(
        profile_root=profile_root,
        gamefile="pick_heat_then_place_in_recep-Kettle-Stove",
        current_trajectory="You are in a kitchen.\n> ",
    )
    examples = json.loads((profile_root / "alfworld_3prompts.json").read_text(encoding="utf-8"))
    content = messages[0]["content"]
    assert messages[0]["role"] == "user"
    assert examples["react_heat_1"] in content
    assert examples["react_heat_0"] in content
    assert content.index(examples["react_heat_1"]) < content.index(examples["react_heat_0"])
    assert content.endswith("You are in a kitchen.\n> ")


def test_alfworld_renderer_rejects_unknown_or_ambiguous_family() -> None:
    profile_root = ROOT / "assets/prompts/alfworld/react_task_type_two_demo_v1"
    with pytest.raises(ValueError, match="ambiguous or unknown"):
        render_alfworld(
            profile_root=profile_root,
            gamefile="unknown-game",
            current_trajectory="state",
        )


def test_webshop_renderer_preserves_pinned_prompt_and_action_grammar() -> None:
    profile_root = ROOT / "assets/prompts/webshop/react_official_one_demo_v1"
    source = (profile_root / "prompt1.txt").read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "58e4164eb648db4f8f437ee8a7b3ce064d3661d929e4f0fe71155ea6b062c56a"
    )
    messages = render_webshop(
        profile_root=profile_root,
        instruction_and_observation="Instruction: buy a blue mug\n[Search]",
    )
    content = messages[0]["content"]
    assert content.startswith(source.decode("utf-8"))
    for delimiter in ("search[", "think[", "click["):
        assert delimiter in content
    assert messages == render_webshop(
        profile_root=profile_root,
        instruction_and_observation="Instruction: buy a blue mug\n[Search]",
    )


def test_notebook_prompt_extraction_uses_one_static_assignment(tmp_path: Path) -> None:
    notebook = tmp_path / "fixture.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["prompt1 = 'Action: search[x]\\nAction: click[y]\\n'"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert extract_notebook_string(notebook, "prompt1") == (
        "Action: search[x]\nAction: click[y]\n"
    )
    with pytest.raises(ValueError, match="found 0"):
        extract_notebook_string(notebook, "missing")
