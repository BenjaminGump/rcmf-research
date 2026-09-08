from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence


PROFILE_NAME = "react_official_one_demo_v1"


def render_react_messages(
    *,
    profile_root: str | Path,
    instruction_and_observation: str,
) -> Sequence[Mapping[str, str]]:
    root = Path(profile_root)
    profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
    if profile.get("profile") != PROFILE_NAME:
        raise ValueError("unexpected WebShop prompt profile")
    if not instruction_and_observation:
        raise ValueError("WebShop instruction/observation must be non-empty")
    source = (root / str(profile["source_asset"])).read_text(encoding="utf-8")
    return (
        {
            "role": "user",
            "content": source + "\n" + instruction_and_observation,
        },
    )

