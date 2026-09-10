from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence


PROFILE_NAME = "react_task_type_two_demo_v1"
PROMPT_ASSET_SHA256 = "a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a"
PROMPT_ASSET_GIT_BLOB = "0e7c204818e9aa3128d266da5512d1ec3d9ea13e"


def load_profile(profile_root: str | Path) -> tuple[dict[str, object], dict[str, str]]:
    root = Path(profile_root)
    profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
    source = root / str(profile["source_asset"])
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROMPT_ASSET_SHA256:
        raise ValueError("pinned ALFWorld prompt asset SHA-256 mismatch")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if blob != PROMPT_ASSET_GIT_BLOB:
        raise ValueError("pinned ALFWorld prompt asset Git blob mismatch")
    examples = json.loads(raw.decode("utf-8"))
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
        # Exact ReAct notebook behavior: examples already end in one newline.
        demonstrations = "".join(examples[key] for key in keys)
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


def normalize_notebook_reset_observation(observation: str) -> str:
    """Apply the exact reset-only transformation in ReAct's alfworld.ipynb."""

    return "\n".join(str(observation).split("\n\n")[1:])


def render_react_trajectory(
    initial_observation: str,
    history: Sequence[Mapping[str, str]],
) -> str:
    """Render one live ReAct transcript from exact action/observation history."""

    rendered = normalize_notebook_reset_observation(initial_observation) + "\n>"
    for row in history:
        rendered += " " + str(row["action"]) + "\n" + str(row["observation"]) + "\n>"
    return rendered
