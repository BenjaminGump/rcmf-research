from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import torch

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
from scripts.prepare_rcmf_one_demo_dev_11a import (
    _condition_manifest,
    _prompt_manifest,
    _ready_subscript_keys,
)
from scripts.run_rcmf_one_demo_dev_11a import CONDITION_ORDER, run_determinism
from scripts.analyze_rcmf_one_demo_dev_11a import (
    exact_mcnemar,
    leave_one_task_out,
    paired_bootstrap_ci,
)
import scripts.run_raw_memory_first37_7f as raw_first37
import scripts.export_rcmf_one_demo_dev_audit_11a as audit_export
import scripts.export_rcmf_joint_full_bank_audit_9a as base_audit_export


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

def test_prompt_manifest_records_complete_boundaries_and_retained_roles() -> None:
    settings = {
        "prompt": {
            "original_full_prompt_sha256": (
                "dd74c379c97031a062ba79b2b82d3992ec3b38870792f53d86821544f994c4c3"
            ),
            "original_structured_messages_sha256": (
                "f9a6937120b7da883c60e9b5e9187290bf71d3d68b0182640487b705f4cb3734"
            ),
            "one_demo_prompt_sha256": (
                "a0a8d3b2e10f167dba5dcab5ad62fa8f6737629b813d2d0e27af4872bef9e27b"
            ),
            "one_demo_structured_messages_sha256": (
                "90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe"
            ),
            "original_message_count": 74,
            "one_demo_message_count": 20,
        }
    }
    manifest = _prompt_manifest(settings)
    assert manifest["checks"]["full_demo_non_regression"]
    assert manifest["complete_demo_message_ranges"]["demo_1"]["start"] == 0
    assert manifest["complete_demo_message_ranges"]["demo_3"]["end"] == 72
    assert len(manifest["retained_initial_messages"]) == 20
    assert manifest["frozen_before_dev_generation"]


def test_condition_manifest_accounts_for_complete_dev_without_shortcuts() -> None:
    tasks = [f"dev_{index}" for index in range(57)]
    settings = {
        "run_uuid": "run",
        "dev": {
            "condition_order": list(CONDITION_ORDER),
            "expected_condition_count": 171,
        },
    }
    immutable = {
        "hashes": {
            "deployment_field": "d" * 64,
            "selector_ensemble": "s" * 64,
        }
    }
    manifest = _condition_manifest(tasks, settings, immutable)
    assert manifest["logical_condition_count"] == 171
    assert len({(row["condition"], row["task_id"]) for row in manifest["rows"]}) == 171
    assert all(not row["runtime_memory_retrieval"] for row in manifest["rows"])
    assert all(not row["runtime_per_memory_scoring"] for row in manifest["rows"])
    assert all(not row["student_prompt_contains_raw_memory"] for row in manifest["rows"])
    assert [row["memory_count"] for row in manifest["rows"][:57]] == [0] * 57
    assert all(row["memory_count"] == 499 for row in manifest["rows"][57:])


def test_dev_model_renderer_uses_no_ground_truth_or_allowed_apps() -> None:
    assert _ready_subscript_keys() == ["instruction", "ready_nonce", "supervisor"]

def test_paired_analysis_is_exact_and_seeded() -> None:
    left = [True, True, False, True]
    right = [True, False, True, False]
    mcnemar = exact_mcnemar(left, right)
    assert mcnemar == {
        "left_only": 2,
        "right_only": 1,
        "discordant": 3,
        "two_sided_exact_p": 1.0,
    }
    first = paired_bootstrap_ci(left, right, replicates=200)
    second = paired_bootstrap_ci(left, right, replicates=200)
    assert first == second
    assert first["observed"] == 0.25
    sensitivity = leave_one_task_out(left, right)
    assert len(sensitivity["per_omission"]) == 4


def test_determinism_repetitions_use_distinct_world_prefixes(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "smoke_manifest.json").write_text(
        json.dumps({"task_ids": ["train_task_1"]}), encoding="utf-8"
    )
    prefixes: list[str] = []

    def fake_run_rows(**kwargs):
        prefixes.append(kwargs["experiment_prefix"])
        return ([{"task_id": "train_task_1", "wall_seconds": 0.0}], 0)

    monkeypatch.setattr(
        "scripts.run_rcmf_one_demo_dev_11a._run_rows", fake_run_rows
    )
    monkeypatch.setattr(
        "scripts.run_rcmf_one_demo_dev_11a._scope_paths",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "scripts.run_rcmf_one_demo_dev_11a.deterministic_task_match",
        lambda _left, _right: {"passed": True},
    )
    result = run_determinism(
        SimpleNamespace(artifact_dir=tmp_path),
        cfg=object(),
        settings={"smoke": {"max_steps": 2}},
        attempt=object(),
    )
    assert result["passed"] is True
    assert prefixes == [
        "exp033a_repeat_a",
        "exp033a_repeat_a",
        "exp033a_repeat_a",
        "exp033a_repeat_b",
        "exp033a_repeat_b",
        "exp033a_repeat_b",
    ]


def test_audit_registers_all_sensitive_observations_before_redaction(
    tmp_path
) -> None:
    secret = "late-registered-sensitive-observation"
    for task_id, executed_code in (
        ("task_early", "print('status')"),
        ("task_late", "password = 'typed-at-runtime'"),
    ):
        for condition in audit_export.CONDITIONS:
            path = (
                tmp_path
                / "dev"
                / "conditions"
                / condition
                / "task_results"
                / f"{task_id}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "exact_executed_code": executed_code,
                                "complete_environment_observation": secret,
                                "complete_trajectory_so_far": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
    base_audit_export.SENSITIVE_OBSERVATIONS.clear()
    tasks = audit_export._load_and_register_tasks(
        tmp_path, ["task_early", "task_late"]
    )
    redacted = base_audit_export.redact(tasks["task_early"]["D0"])
    assert secret not in json.dumps(redacted)
    assert "<REDACTED:CREDENTIAL_OBSERVATION:SHA256=" in json.dumps(redacted)
    base_audit_export.SENSITIVE_OBSERVATIONS.clear()


def test_frozen_state_extractor_uses_explicit_prompt_profile(monkeypatch) -> None:
    class Tokenizer:
        def apply_chat_template(self, *_args, **_kwargs):
            return "rendered"

        def __call__(self, *_args, **_kwargs):
            return {"offset_mapping": [(0, 1)]}

    captured = []

    def fake_tokenize(_tokenizer, _rendered, spans):
        captured.append(spans)
        return torch.ones(1, 1, dtype=torch.long), torch.ones(1, 1), spans

    monkeypatch.setattr(
        raw_first37,
        "tokenize_and_validate_char_spans",
        fake_tokenize,
    )
    monkeypatch.setattr(
        raw_first37,
        "frozen_qwen_span_readouts",
        lambda **_kwargs: {
            "final_layer": {
                name: torch.zeros(2, 4096) for name in raw_first37.STATE_VIEW_NAMES
            }
        },
    )

    for profile, initial_count in (
        ("full_demo", 74),
        (FULL_DEMO_FIRST_ONLY_PROFILE, 20),
    ):
        message_spans = [
            {
                "role": "assistant" if index % 2 else "user",
                "message_index": index,
                "char_start": index * 10,
                "char_end": index * 10 + 5,
            }
            for index in range(initial_count)
        ]
        message_spans.extend(
            [
                {
                    "role": "user",
                    "message_index": initial_count,
                    "char_start": initial_count * 10,
                    "char_end": initial_count * 10 + 5,
                },
                {
                    "role": "assistant",
                    "message_index": initial_count + 1,
                    "char_start": initial_count * 10 + 10,
                    "char_end": initial_count * 10 + 15,
                },
                {
                    "role": "user",
                    "message_index": initial_count + 2,
                    "char_start": initial_count * 10 + 20,
                    "char_end": initial_count * 10 + 25,
                },
            ]
        )
        monkeypatch.setattr(
            raw_first37,
            "_ordered_message_content_spans",
            lambda _rendered, _messages, spans=message_spans: spans,
        )
        owner = SimpleNamespace(
            prompt_profile=profile,
            backend=SimpleNamespace(
                tokenizer=Tokenizer(), model=object(), device=torch.device("cpu")
            ),
        )
        output = raw_first37.FrozenDeploymentSelector._state_values(
            owner, [{} for _ in message_spans]
        )
        assert output.shape == (1, 10, 4096)
        assert captured[-1]["current_task_goal"] == (
            initial_count * 10,
            initial_count * 10 + 5,
        )
