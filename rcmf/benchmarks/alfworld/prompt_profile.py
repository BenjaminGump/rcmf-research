from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence


PROFILE_NAME = "react_task_type_two_demo_v1"


def load_profile(profile_root: str | Path) -> tuple[dict[str, object], dict[str, str]]:
    root = Path(profile_root)
    profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
    examples = json.loads((root / str(profile["source_asset"])).read_text(encoding="utf-8"))
    if profile.get("profile") != PROFILE_NAME:
        raise ValueError("unexpected ALFWorld prompt profile")
    return profile, examples


def task_family_from_gamefile(gamefile: str, mapping: Mapping[str, str]) -> str:
    matches = [source for source in mapping if source in gamefile]
    if len(matches) != 1:
        raise ValueError(f"ALFWorld task family is ambiguous or unknown: {gamefile!r}")
    return matches[0]


def render_react_messages(
    *,
    profile_root: str | Path,
    gamefile: str,
    current_trajectory: str,
) -> Sequence[Mapping[str, str]]:
    profile, examples = load_profile(profile_root)
    mapping = {str(key): str(value) for key, value in profile["task_family_prefixes"].items()}
    family = task_family_from_gamefile(gamefile, mapping)
    prefix = mapping[family]
    suffixes = tuple(str(value) for value in profile["example_suffix_order"])
    keys = tuple(f"react_{prefix}_{suffix}" for suffix in suffixes)
    try:
        demonstrations = "\n".join(examples[key] for key in keys)
    except KeyError as error:
        raise KeyError(f"missing pinned ALFWorld ReAct example: {error.args[0]}") from error
    if not current_trajectory:
        raise ValueError("current ALFWorld trajectory must be non-empty")
    content = (
        str(profile["header"])
        + demonstrations
        + str(profile["trailer"])
        + current_trajectory
    )
    return ({"role": "user", "content": content},)

