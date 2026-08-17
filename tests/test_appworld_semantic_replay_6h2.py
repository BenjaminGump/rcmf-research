from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from rcmf.training.appworld_legacy_replay_6h1 import (
    normalize_observation_locked as normalize_v1_original,
)
from rcmf.training.appworld_semantic_replay_6h2 import (
    ALLOWED_TEMPORAL_CLAIMS,
    ALLOWED_TOKEN_FIELDS,
    SEMANTIC_NORMALIZATION_VERSION,
    compare_identity_layers,
    compare_observations_semantic,
    decode_jwt_strict,
    identity_hashes,
    normalize_observation_locked,
    parse_full_demo_query,
    summarize_semantic_replays,
)
from scripts.run_appworld_semantic_replay_6h2 import (
    _full_decision,
    _repeat_equivalence,
    _sentinel_decision,
)
from scripts.prepare_appworld_semantic_replay_6h2 import (
    _legacy_history_observation_count,
)


def _segment(value: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def _jwt(
    payload: dict,
    *,
    header: dict | None = None,
    signature: str = "c2lnbmF0dXJl",
) -> str:
    header = header or {"alg": "HS256", "typ": "JWT"}
    return f"{_segment(header)}.{_segment(payload)}.{signature}"


def _observation(
    *,
    sub: str = "spotify+user@example.com",
    exp: int = 100,
    token_type: str = "Bearer",
    extra_payload: dict | None = None,
    extra_response: dict | None = None,
    signature: str = "c2lnbmF0dXJl",
) -> str:
    payload = {"sub": sub, "exp": exp, **(extra_payload or {})}
    response = {
        "access_token": _jwt(payload, signature=signature),
        "token_type": token_type,
        **(extra_response or {}),
    }
    return json.dumps(response)


def test_semantic_v2_first_preserves_locked_v1_behavior() -> None:
    values = [
        "Output:\n```\n{'b': 2, 'a': 1}\n```",
        '{"a": 1, "b": 2}',
        "plain text  \nnext",
    ]
    assert [normalize_observation_locked(value) for value in values] == [
        normalize_v1_original(value) for value in values
    ]


def test_exp_only_difference_and_consequent_signature_are_semantically_equal() -> None:
    expected = _observation(exp=100, signature="ZXhwZWN0ZWQ")
    actual = _observation(exp=291, signature="YWN0dWFs")
    report = compare_observations_semantic(expected, actual)
    assert not report["raw_match"]
    assert not report["v1_match"]
    assert report["semantic_v2_match"]
    assert report["jwt_reports"][0]["differing_claims"] == ["exp"]
    assert report["jwt_reports"][0]["temporal_claim_deltas"] == {"exp": 191.0}
    assert report["allowed_token_fields"] == ["access_token"]
    assert report["allowed_temporal_claims"] == ["exp"]


@pytest.mark.parametrize(
    ("expected", "actual", "reason"),
    [
        (_observation(), _observation(sub="spotify+other@example.com"), "subject"),
        (
            _observation(),
            _observation(sub="gmail+user@example.com"),
            "app",
        ),
        (
            _observation(extra_payload={"scopes": ["read"]}),
            _observation(extra_payload={"scopes": ["write"]}),
            "permissions",
        ),
        (_observation(token_type="Bearer"), _observation(token_type="Basic"), "token type"),
        (
            _observation(extra_response={"status": "ok"}),
            _observation(extra_response={"status": "error"}),
            "non-token field",
        ),
        (
            _observation(extra_payload={"jti": "one"}),
            _observation(extra_payload={"jti": "two"}),
            "unapproved dynamic claim",
        ),
    ],
)
def test_semantic_v2_does_not_hide_stable_or_non_token_changes(
    expected: str, actual: str, reason: str
) -> None:
    del reason
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]


def test_semantic_v2_rejects_changed_header_algorithm_and_type() -> None:
    expected = json.dumps(
        {
            "access_token": _jwt(
                {"sub": "spotify+user", "exp": 100},
                header={"alg": "HS256", "typ": "JWT"},
            )
        }
    )
    actual = json.dumps(
        {
            "access_token": _jwt(
                {"sub": "spotify+user", "exp": 200},
                header={"alg": "none", "typ": "OTHER"},
            )
        }
    )
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]
    assert not report["jwt_reports"][0]["header_match"]


def test_semantic_v2_rejects_malformed_token() -> None:
    expected = _observation()
    actual = json.dumps({"access_token": "not-a-jwt", "token_type": "Bearer"})
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]
    assert not report["jwt_reports"][0]["valid_jwt_pair"]


def test_semantic_v2_does_not_ignore_arbitrary_timestamp() -> None:
    expected = json.dumps({"timestamp": 100, "value": "same"})
    actual = json.dumps({"timestamp": 200, "value": "same"})
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]
    assert report["jwt_field_count"] == 0


def test_semantic_v2_requires_allowed_token_field_name() -> None:
    expected = json.dumps({"opaque": _jwt({"sub": "x+y", "exp": 100})})
    actual = json.dumps({"opaque": _jwt({"sub": "x+y", "exp": 200})})
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]
    assert report["jwt_field_count"] == 0


def test_semantic_v2_handles_nested_json_and_list_token_fields() -> None:
    expected = json.dumps(
        {"items": [{"auth": {"access_token": _jwt({"sub": "phone+u", "exp": 10})}}]}
    )
    actual = json.dumps(
        {"items": [{"auth": {"access_token": _jwt({"sub": "phone+u", "exp": 20})}}]}
    )
    report = compare_observations_semantic(expected, actual)
    assert report["semantic_v2_match"]
    assert report["jwt_reports"][0]["path"] == "$.items[0].auth.access_token"


def test_semantic_v2_requires_same_temporal_claim_presence() -> None:
    expected = json.dumps({"access_token": _jwt({"sub": "phone+u", "exp": 10})})
    actual = json.dumps({"access_token": _jwt({"sub": "phone+u"})})
    report = compare_observations_semantic(expected, actual)
    assert not report["semantic_v2_match"]
    assert not report["jwt_reports"][0]["temporal_claim_presence_match"]


def test_strict_jwt_requires_exact_three_decodable_segments() -> None:
    with pytest.raises(ValueError, match="three"):
        decode_jwt_strict("one.two")
    with pytest.raises(ValueError, match="decodable"):
        decode_jwt_strict("bad.bad.c2ln")


def _full_query(
    *,
    first: str = "Ada",
    last: str = "Lovelace",
    email: str = "ada@example.com",
    phone: str = "123",
    instruction: str = "Do the task.",
) -> str:
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {first} {last}. My personal email is {email} and phone number is "
        f"{phone}.\nTask: {instruction}"
    )


def test_identity_parser_and_layer_comparison_are_field_exact_and_redacted() -> None:
    query = _full_query()
    parsed = parse_full_demo_query(query)
    assert parsed["instruction"] == "Do the task."
    hashes = identity_hashes(query)
    assert set(hashes) == {"instruction", "first_name", "last_name", "email", "phone_number"}
    result = compare_identity_layers(
        {"decision": query, "trajectory": query, "contract": query},
        official_fields=parsed,
    )
    assert result["identity_match"]
    assert "ada@example.com" not in json.dumps(result)


def test_identity_comparison_detects_supervisor_only_mismatch() -> None:
    query = _full_query()
    official = parse_full_demo_query(_full_query(first="Grace", last="Hopper", email="g@example.com", phone="999"))
    result = compare_identity_layers({"decision": query, "raw": query}, official_fields=official)
    assert not result["identity_match"]
    assert result["mismatched_fields"] == ["email", "first_name", "last_name", "phone_number"]
    assert result["field_matches"]["instruction"]


def _semantic_row(state_id: str, *, passed: bool = True) -> dict:
    history = {
        "step_id": 1,
        "is_target": False,
        "raw_match": False,
        "v1_match": False,
        "semantic_v2_match": passed,
        "semantic_comparison": {
            "jwt_reports": [
                {
                    "semantic_match": passed,
                    "differing_claims": ["exp"],
                    "non_temporal_differing_claims": [],
                    "header_match": True,
                    "stable_claims_match": True,
                }
            ],
            "non_token_difference_count": 0,
        },
    }
    target = {
        **history,
        "step_id": 2,
        "is_target": True,
        "raw_match": True,
        "v1_match": True,
        "semantic_comparison": {"jwt_reports": [], "non_token_difference_count": 0},
    }
    return {
        "state_example_id": state_id,
        "task_id": "task",
        "initial_task_identity_match": True,
        "steps": [history, target],
        "complete_history_raw_match": False,
        "complete_history_v1_match": False,
        "complete_history_semantic_match": passed,
        "first_semantic_divergence_step": None if passed else 1,
        "fatal_exception": None,
        "passed": passed,
    }


def test_semantic_summary_separates_raw_v1_and_v2() -> None:
    summary = summarize_semantic_replays([_semantic_row("state")])
    assert summary["prior_raw_match_count"] == 0
    assert summary["prior_v1_match_count"] == 0
    assert summary["prior_semantic_match_count"] == 1
    assert summary["temporal_only_jwt_count"] == 1
    assert summary["non_temporal_jwt_mismatch_count"] == 0


def test_repeated_sentinel_gate_requires_both_complete_repeats() -> None:
    summary = {
        "state_count": 13,
        "identity_match_count": 13,
        "complete_history_semantic_match_count": 13,
        "prior_observation_count": 102,
        "prior_semantic_match_count": 102,
        "target_semantic_match_count": 13,
        "complete_semantic_replay_count": 13,
        "exception_count": 0,
        "non_temporal_jwt_mismatch_count": 0,
        "non_token_mismatch_count": 0,
    }
    decision = _sentinel_decision(
        [summary, summary],
        [{"semantic_repeat_match": True} for _ in range(13)],
    )
    assert decision["full_replay_allowed"]
    broken = dict(summary, prior_semantic_match_count=101)
    assert not _sentinel_decision([summary, broken], [{"semantic_repeat_match": True}])["full_replay_allowed"]


def test_full_gate_is_exactly_45_and_372() -> None:
    summary = {
        "state_count": 45,
        "identity_match_count": 45,
        "complete_history_semantic_match_count": 45,
        "prior_observation_count": 372,
        "prior_semantic_match_count": 372,
        "target_semantic_match_count": 45,
        "complete_semantic_replay_count": 45,
        "exception_count": 0,
        "non_temporal_jwt_mismatch_count": 0,
        "non_token_mismatch_count": 0,
    }
    assert _full_decision(summary)["semantic_replay_validated"]
    assert not _full_decision(dict(summary, complete_semantic_replay_count=44))[
        "semantic_replay_validated"
    ]


def test_repeat_equivalence_requires_state_and_semantic_step_hashes() -> None:
    base = {
        "state_example_id": "state",
        "initial_task_files": {"sha": "task"},
        "initial_state_fingerprint": {"sha": "initial"},
        "final_state_fingerprint": {"sha": "final"},
        "steps": [
            {
                "step_id": 1,
                "semantic_comparison": {
                    "actual_semantic_sha256": "semantic",
                    "non_token_differences": [],
                },
                "state_before": {"sha": "initial"},
                "state_after": {"sha": "final"},
                "exception": None,
            }
        ],
    }
    assert _repeat_equivalence(base, json.loads(json.dumps(base)))["semantic_repeat_match"]
    changed = json.loads(json.dumps(base))
    changed["steps"][0]["state_after"] = {"sha": "changed"}
    assert not _repeat_equivalence(base, changed)["semantic_repeat_match"]


def test_bridges_are_qwen_free_and_scope_locked() -> None:
    semantic_bridge = Path("scripts/appworld_semantic_replay_bridge_6h2.py").read_text(
        encoding="utf-8"
    )
    identity_bridge = Path("scripts/appworld_identity_probe_bridge_6h2.py").read_text(
        encoding="utf-8"
    )
    for source in (semantic_bridge, identity_bridge):
        assert "transformers" not in source
        assert "Qwen" not in source
        assert "world.execute" not in identity_bridge
        assert "from appworld import AppWorld" in source
        assert "APPWORLD_ROOT" in source
    assert "world.execute" in semantic_bridge
    assert "expected_python.parent.parent" in semantic_bridge


def test_config_locks_capsule_semantics_and_no_generation() -> None:
    text = Path("configs/benchmark/stage_c_appworld_semantic_replay_6h2.yaml").read_text(
        encoding="utf-8"
    )
    assert "appworld-0.1.0-replay-py311-click817" in text
    assert "appworld_observation_semantic_normalization_6h2_v1" in text
    assert "allowed_temporal_claims:\n      - exp" in text
    assert "no_qwen_import_forward_or_generation" in text
    assert "no_memory_condition_execution" in text
    assert ALLOWED_TOKEN_FIELDS == frozenset({"access_token"})
    assert ALLOWED_TEMPORAL_CLAIMS == frozenset({"exp"})
    assert SEMANTIC_NORMALIZATION_VERSION.endswith("6h2_v1")


def test_preflight_reads_immutable_exp024r_history_count_schema() -> None:
    assert _legacy_history_observation_count({"history_observation_count": 102}) == 102
    with pytest.raises(KeyError, match="history_observation_count"):
        _legacy_history_observation_count({"prior_observation_count": 102})
