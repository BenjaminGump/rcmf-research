from __future__ import annotations

import base64
import json

import pytest

from rcmf.training.appworld_replay_clean_rebuild_7b import (
    ROOT_JWT_SCHEMA_SENTINEL_IDS,
    SEMANTIC_NORMALIZATION_VERSION,
    analyze_login_action,
    authenticated_calls_using_login_result,
    compare_observations_semantic_v3,
    semantic_replay_gate_v3,
)
from scripts.run_replay_clean_rebuild_7b import (
    CHECKPOINT_VERSION,
    _checkpoint_index,
)
from scripts.freeze_replay_validated_contract_7b import (
    REPLAY_VALIDATED_CORPUS_VERSION,
    _attempt_lifecycle,
)


def _segment(value: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def _jwt(
    *,
    app: str = "spotify",
    username: str = "user@example.com",
    exp: int = 100,
    header: dict | None = None,
    extra: dict | None = None,
    signature: str = "c2lnbmF0dXJl",
) -> str:
    payload = {"sub": f"{app}+{username}", "exp": exp, **(extra or {})}
    jwt_header = header or {"alg": "HS256", "typ": "JWT"}
    return f"{_segment(jwt_header)}.{_segment(payload)}.{signature}"


def _compare(expected: str, actual: str, **overrides: object) -> dict:
    arguments = {
        "action_code": "login_result = apis.spotify.login(username='u', password='p')",
        "expected_validator_accepted": True,
        "actual_validator_accepted": True,
        "subsequent_authenticated_action_count": 1,
        "subsequent_authenticated_actions_accepted": True,
        **overrides,
    }
    return compare_observations_semantic_v3(expected, actual, **arguments)


def test_root_login_exp_only_difference_is_semantically_equal() -> None:
    report = _compare(
        _jwt(exp=100, signature="ZXhwZWN0ZWQ"),
        _jwt(exp=291, signature="YWN0dWFs"),
    )
    assert report["format"] == SEMANTIC_NORMALIZATION_VERSION
    assert not report["semantic_v2_match"]
    assert report["semantic_v3_match"]
    assert report["root_jwt_extension_applied"]
    assert report["root_jwt_report"]["differing_claims"] == ["exp"]
    assert report["root_jwt_report"]["temporal_claim_deltas"] == {"exp": 191.0}


@pytest.mark.parametrize(
    "actual",
    [
        _jwt(username="other@example.com", exp=291),
        _jwt(app="gmail", exp=291),
        _jwt(extra={"roles": ["admin"]}, exp=291),
        _jwt(header={"alg": "HS512", "typ": "JWT"}, exp=291),
        _jwt(header={"alg": "HS256", "typ": "OTHER"}, exp=291),
    ],
)
def test_root_rule_rejects_changed_stable_identity_permissions_or_header(
    actual: str,
) -> None:
    report = _compare(_jwt(exp=100), actual)
    assert not report["semantic_v3_match"]
    assert not report["root_jwt_extension_applied"]


def test_root_rule_rejects_changed_non_token_response_content() -> None:
    actual = json.dumps({"access_token": _jwt(exp=291), "status": "extra"})
    assert not _compare(_jwt(exp=100), actual)["semantic_v3_match"]


@pytest.mark.parametrize(
    ("actual", "overrides"),
    [
        ("not-a-jwt", {}),
        ("291", {}),
        (_jwt(exp=291), {"action_code": "value = apis.spotify.show_profile()"}),
        (_jwt(exp=291), {"actual_validator_accepted": False}),
        (
            _jwt(exp=291),
            {"subsequent_authenticated_actions_accepted": False},
        ),
    ],
)
def test_root_rule_rejects_malformed_nonlogin_unvalidated_or_unaccepted_cases(
    actual: str, overrides: dict
) -> None:
    assert not _compare(_jwt(exp=100), actual, **overrides)["semantic_v3_match"]


def test_root_rule_does_not_ignore_arbitrary_root_timestamp() -> None:
    assert not _compare("100", "291")["semantic_v3_match"]


def test_named_access_token_semantics_remain_locked_v2_behavior() -> None:
    expected = json.dumps({"access_token": _jwt(exp=100)})
    actual = json.dumps({"access_token": _jwt(exp=291)})
    report = _compare(
        expected,
        actual,
        action_code="value = apis.spotify.show_profile(access_token=token)",
    )
    assert report["semantic_v2_match"]
    assert report["semantic_v3_match"]
    assert not report["root_jwt_extension_applied"]


def test_login_ast_requires_exactly_one_appworld_login_call() -> None:
    context = analyze_login_action(
        "login_result = apis.spotify.login(username='u', password='p')"
    )
    assert context.login_call_count == 1
    assert context.app_name == "spotify"
    assert context.assigned_names == ("login_result",)
    duplicate = analyze_login_action(
        "a = apis.spotify.login(username='u', password='p')\n"
        "b = apis.spotify.login(username='u', password='p')"
    )
    assert duplicate.login_call_count == 2
    assert duplicate.app_name is None


def test_authenticated_call_must_reference_the_login_result_as_access_token() -> None:
    reports = authenticated_calls_using_login_result(
        "page = apis.spotify.show_album_library(access_token=login_result, page_index=0)",
        app_name="spotify",
        assigned_names=["login_result"],
    )
    assert reports == [
        {
            "app": "spotify",
            "api": "show_album_library",
            "referenced_login_result_names": ["login_result"],
        }
    ]
    assert authenticated_calls_using_login_result(
        "page = apis.spotify.show_album_library(access_token=other, page_index=0)",
        app_name="spotify",
        assigned_names=["login_result"],
    ) == []


def test_schema_extension_sentinel_is_exactly_the_three_preregistered_states() -> None:
    assert ROOT_JWT_SCHEMA_SENTINEL_IDS == (
        "appworld:trace:82e2fac_3:step:6:line:16",
        "appworld:trace:82e2fac_3:step:7:line:17",
        "appworld:trace:82e2fac_3:step:10:line:20",
    )


def test_replay_gate_requires_every_count_in_both_repeats() -> None:
    summary = {
        "state_count": 45,
        "task_count": 9,
        "identity_match_count": 45,
        "complete_history_v3_match_count": 45,
        "prior_observation_count": 372,
        "prior_v3_match_count": 372,
        "target_observation_count": 45,
        "target_v3_match_count": 45,
        "complete_v3_replay_count": 45,
        "exception_count": 0,
        "non_temporal_root_jwt_mismatch_count": 0,
    }
    assert semantic_replay_gate_v3(
        [summary, summary],
        expected_states=45,
        expected_tasks=9,
        expected_prior_observations=372,
    )["passed"]
    failed = {**summary, "prior_v3_match_count": 371}
    assert not semantic_replay_gate_v3(
        [summary, failed],
        expected_states=45,
        expected_tasks=9,
        expected_prior_observations=372,
    )["passed"]


def test_replay_checkpoint_is_the_only_resume_authority(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    assert _checkpoint_index(path) == {"format": CHECKPOINT_VERSION, "rows": {}}
    payload = {
        "format": CHECKPOINT_VERSION,
        "rows": {
            "sentinel:repeat_1:state": {
                "state_example_id": "state",
                "result_sha256": "abc",
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _checkpoint_index(path) == payload


def test_replay_phase_sources_do_not_import_qwen_or_training_backends() -> None:
    from pathlib import Path

    for path in (
        Path("rcmf/training/appworld_replay_clean_rebuild_7b.py"),
        Path("scripts/appworld_semantic_replay_bridge_7b.py"),
        Path("scripts/prepare_replay_clean_rebuild_7b.py"),
        Path("scripts/run_replay_clean_rebuild_7b.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "HFQwenBackend" not in source
        assert "from_pretrained" not in source
        assert "optimizer" not in source.lower()


def test_replay_validated_contract_has_new_immutable_version() -> None:
    assert REPLAY_VALIDATED_CORPUS_VERSION == (
        "appworld_identity_reconciled_replay_validated_v1"
    )


def test_contract_freeze_rejects_open_or_failed_attempts() -> None:
    assert _attempt_lifecycle(
        [
            {"attempt_id": "a", "event": "start"},
            {"attempt_id": "a", "event": "end", "exit_code": 0},
        ]
    )["passed"]
    assert not _attempt_lifecycle(
        [{"attempt_id": "a", "event": "start"}]
    )["passed"]
    assert not _attempt_lifecycle(
        [
            {"attempt_id": "a", "event": "start"},
            {"attempt_id": "a", "event": "end", "exit_code": 1},
        ]
    )["passed"]
