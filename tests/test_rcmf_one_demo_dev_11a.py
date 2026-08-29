from __future__ import annotations

import hashlib
import json

from prompt import AGENT_SYSTEM_PROMPT_TEMPLATE_AW

from rcmf.benchmarks.appworld.prompt import (
    FIRST_ONLY_PROMPT_RENDERER_VERSION,
    FULL_DEMO_FIRST_ONLY_PROFILE,
    PROMPT_RENDERER_VERSION,
    appworld_renderer_metadata,
    build_appworld_messages,
    build_task_message,
    full_demo_sections,
    get_initial_messages,
    get_system_prompt,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import _register_static_asset


def _messages_sha(messages: list[dict[str, str]]) -> str:
    payload = "\n".join(
        f"{message['role']}:{message['content']}" for message in messages
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_full_demo_profile_is_byte_for_byte_unchanged() -> None:
    messages = get_initial_messages("full_demo")
    assert get_system_prompt("full_demo") == AGENT_SYSTEM_PROMPT_TEMPLATE_AW
    assert len(messages) == 74
    assert _messages_sha(messages) == (
        "f9a6937120b7da883c60e9b5e9187290bf71d3d68b0182640487b705f4cb3734"
    )
    metadata = appworld_renderer_metadata("full_demo")
    assert metadata["renderer_version"] == PROMPT_RENDERER_VERSION
    assert metadata["initial_message_count"] == 74
    assert metadata["initial_messages_sha256"] == _messages_sha(messages)


def test_first_only_profile_uses_complete_raw_demo_boundaries() -> None:
    sections = full_demo_sections(AGENT_SYSTEM_PROMPT_TEMPLATE_AW)
    assert sections["separator_offsets"] == [8953, 15852, 27991]
    expected_hashes = {
        "demo_1_with_instruction_prefix": (
            "32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713"
        ),
        "demo_2": "0a34647714de22cffebd072a933bf3341511cd4145cc8432582715d7d743f52e",
        "demo_3": "6c4afd304257d0cc57d135180ba9a0050ae46043397c15837b46efcd764d82d6",
        "trailing_key_instructions": (
            "d414132b5da4e69c6e0bb4e01befc6595a4c5d26c7b4de1575daa8d8bbeb8720"
        ),
    }
    for key, expected in expected_hashes.items():
        assert hashlib.sha256(sections[key].encode("utf-8")).hexdigest() == expected
    expected = (
        sections["demo_1_with_instruction_prefix"]
        + sections["trailing_key_instructions"]
    )
    assert sections["first_only_prompt"] == expected
    assert get_system_prompt(FULL_DEMO_FIRST_ONLY_PROFILE) == expected
    assert (
        hashlib.sha256(expected.encode("utf-8")).hexdigest()
        == "a0a8d3b2e10f167dba5dcab5ad62fa8f6737629b813d2d0e27af4872bef9e27b"
    )


def test_first_only_structured_messages_retain_demo_one_and_key_instructions() -> None:
    messages = get_initial_messages(FULL_DEMO_FIRST_ONLY_PROFILE)
    assert len(messages) == 20
    assert _messages_sha(messages) == (
        "90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe"
    )
    assert "How many playlists do I have in Spotify?" in messages[0]["content"]
    assert "most-liked song" not in json.dumps(messages)
    assert "Christopher's text message" not in json.dumps(messages)
    assert messages[-1]["content"].startswith("**Key instructions**")
    metadata = appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)
    assert metadata["renderer_version"] == FIRST_ONLY_PROMPT_RENDERER_VERSION
    assert metadata["initial_message_count"] == 20
    assert metadata["initial_messages_sha256"] == _messages_sha(messages)


def test_first_only_current_task_rendering_matches_full_demo() -> None:
    supervisor = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "phone_number": "123",
    }
    full = build_task_message("Do the task.", supervisor, profile="full_demo")
    first_only = build_task_message(
        "Do the task.", supervisor, profile=FULL_DEMO_FIRST_ONLY_PROFILE
    )
    assert first_only == full
    assert build_appworld_messages(
        full, prompt_profile="full_demo"
    ) == [*get_initial_messages("full_demo"), {"role": "user", "content": full}]
    assert build_appworld_messages(
        first_only, prompt_profile=FULL_DEMO_FIRST_ONLY_PROFILE
    ) == [
        *get_initial_messages(FULL_DEMO_FIRST_ONLY_PROFILE),
        {"role": "user", "content": first_only},
    ]


def test_static_asset_registration_uses_requested_profile(tmp_path) -> None:
    task = {"role": "user", "content": "current task"}
    messages = [*get_initial_messages(FULL_DEMO_FIRST_ONLY_PROFILE), task]
    path = tmp_path / "assets.json"
    identity = _register_static_asset(
        path, messages, prompt_profile=FULL_DEMO_FIRST_ONLY_PROFILE
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    row = payload["assets"][identity]
    assert row["prompt_profile"] == FULL_DEMO_FIRST_ONLY_PROFILE
    assert row["renderer_metadata"]["initial_message_count"] == 20
    assert row["messages"] == messages[:20]
