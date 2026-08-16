from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from rcmf.training.procedural_causal_audit_6h import (
    build_condition_manifest,
    build_signature_equivalence_manifest,
    classify_audit_states,
    evaluate_generated_action,
    messages_with_signature_card,
    normalize_observation,
    paired_task_bootstrap,
    signature_only_card,
    validate_audit_label_coverage,
)
from scripts.run_procedural_causal_audit_6h import (
    _execute_history,
    _state_contract,
)


def _action_signature(
    signature_hash: str,
    *,
    app: str = "spotify",
    api: str = "show_playlist",
    coarse: str = "read_query",
    api_doc: bool = False,
) -> dict:
    return {
        "signature_sha256": signature_hash,
        "coarse_action_type": coarse,
        "primary_app": app,
        "primary_api": f"{app}.{api}",
        "ordered_api_sequence": [f"{app}.{api}"],
        "keyword_argument_names": ["playlist_id"],
        "argument_value_source_roles": ["prior_api_result"],
        "control_flow_constructs": [],
        "pagination_loop_pattern": False,
        "assignment_dataflow_pattern": ["v1<-api_result"],
        "api_documentation_action": api_doc,
        "calls": [
            {
                "app": app,
                "api": api,
                "keyword_names": ["playlist_id"],
                "keyword_roles": {"playlist_id": "prior_api_result"},
                "positional_roles": [],
                "assigned_to": "v1",
            }
        ],
    }


def _stage() -> dict:
    return {
        "docs_known": True,
        "credentials_obtained": True,
        "authenticated": True,
        "authentication_token_present": True,
        "object_ids_available": True,
        "available_id_keys": ["playlist_id"],
        "collection_loaded": True,
        "pagination_state": False,
        "completion_ready": True,
        "latest_observation_category": "mapping",
        "latest_observation_schema_keys": ["playlist_id"],
    }


def _observation() -> dict:
    return {
        "category": "mapping",
        "schema_keys": ["playlist_id"],
        "id_keys": ["playlist_id"],
        "has_access_token_key": False,
        "is_error": False,
        "is_empty": False,
    }


def _signature_row(
    transition_id: str,
    signature_hash: str,
    parent: str,
    *,
    app: str = "spotify",
    api: str = "show_playlist",
    coarse: str = "read_query",
    api_doc: bool = False,
) -> dict:
    return {
        "transition_id": transition_id,
        "parent_id": parent,
        "parent_task_id": f"task-{parent}",
        "action_signature": _action_signature(
            signature_hash,
            app=app,
            api=api,
            coarse=coarse,
            api_doc=api_doc,
        ),
        "pre_action_stage_signature": _stage(),
        "post_action_observation_signature": _observation(),
    }


def _transition(
    transition_id: str, parent: str, tokens: int
) -> dict:
    return {
        "transition_id": transition_id,
        "parent_memory_id": parent,
        "parent_task_id": f"task-{parent}",
        "teacher_section_tokens": tokens,
    }


def _label(
    state_id: str,
    transition_id: str,
    signature_hash: str,
    parent: str,
    *,
    tier: int,
    exact: bool = True,
    same_app: bool = True,
    same_coarse: bool = True,
    api_doc: bool = False,
    split: str = "train",
) -> dict:
    return {
        "state_example_id": state_id,
        "state_task_id": "validation-task",
        "transition_id": transition_id,
        "transition_parent_id": parent,
        "transition_parent_task_id": f"task-{parent}",
        "transition_signature_sha256": signature_hash,
        "transition_split": split,
        "scoreable_under_context": True,
        "procedural_tier": tier,
        "exact_api_sequence": exact,
        "canonical_action_schema_match": tier == 4,
        "state_stage_compatible": tier >= 3,
        "argument_control_compatible": tier >= 3,
        "state_stage_conflict_count": 0 if tier >= 3 else 4,
        "same_primary_app": same_app,
        "same_coarse_action_type": same_coarse,
        "query_coarse_action_type": "read_query",
        "query_primary_app": "spotify",
        "transition_primary_app": "spotify" if same_app else "gmail",
        "transition_api_documentation_action": api_doc,
        "P2_canonical_schema_compatibility": 1.0 if tier == 4 else 0.5,
        "P3_state_stage_compatibility": 1.0 if tier >= 3 else 0.5,
    }


def test_signature_equivalence_uses_median_exemplar_and_parent_alternate() -> None:
    signatures = [
        _signature_row("t1", "sig", "p1"),
        _signature_row("t2", "sig", "p1"),
        _signature_row("t3", "sig", "p2"),
    ]
    transitions = [
        _transition("t1", "p1", 10),
        _transition("t2", "p1", 20),
        _transition("t3", "p2", 40),
    ]
    manifest = build_signature_equivalence_manifest(transitions, signatures)
    row = manifest["classes"][0]
    assert manifest["transition_count"] == 3
    assert manifest["signature_class_count"] == 1
    assert row["canonical_transition_id"] == "t2"
    assert row["alternate_transition_id"] == "t3"
    assert row["inverse_frequency_weight"] == 1 / 3


def test_canonical_tie_uses_transition_id_sha256() -> None:
    ids = ["a", "b"]
    expected = min(ids, key=lambda value: hashlib.sha256(value.encode()).hexdigest())
    manifest = build_signature_equivalence_manifest(
        [_transition(value, f"p-{value}", 10) for value in ids],
        [_signature_row(value, "sig", f"p-{value}") for value in ids],
    )
    assert manifest["classes"][0]["canonical_transition_id"] == expected


def test_signature_card_contains_only_normalized_metadata() -> None:
    row = _signature_row("t1", "sig", "p1")
    row["source_task_goal"] = "RAW SECRET GOAL"
    row["complete_action"] = "password='secret'"
    row["complete_post_action_observation"] = "token=secret"
    card = signature_only_card(row)
    assert "RAW SECRET GOAL" not in card
    assert "password='secret'" not in card
    assert "token=secret" not in card
    assert "spotify.show_playlist" in card
    assert card == signature_only_card(row)


def test_signature_card_uses_locked_current_state_delimiters(monkeypatch) -> None:
    monkeypatch.setattr(
        "rcmf.training.procedural_causal_audit_6h.appworld_renderer_metadata",
        lambda profile: {"initial_message_count": 1},
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current state"},
    ]
    rendered = messages_with_signature_card(messages, "card", "full_demo")
    assert rendered[1]["content"] == (
        "card\n\n[CURRENT APPWORLD STATE START]\ncurrent state\n"
        "[CURRENT APPWORLD STATE END]"
    )
    assert messages[1]["content"] == "current state"


def test_strata_and_condition_manifest_preserve_canonical_controls() -> None:
    transitions = [
        _transition("t1", "p1", 10),
        _transition("t2", "p2", 11),
        _transition("t3", "p3", 12),
    ]
    signatures = [
        _signature_row("t1", "s1", "p1"),
        _signature_row("t2", "s2", "p2"),
        _signature_row("t3", "s3", "p3", app="gmail", api="send_email", coarse="message_send"),
    ]
    equivalence = build_signature_equivalence_manifest(transitions, signatures)
    labels = [
        _label("state", "t1", "s1", "p1", tier=4),
        _label("state", "t2", "s2", "p2", tier=3),
        _label(
            "state",
            "t3",
            "s3",
            "p3",
            tier=0,
            exact=False,
            same_app=False,
            same_coarse=False,
        ),
    ]
    audit = [{"state_example_id": "state", "task_id": "task", "step_id": 2}]
    strata = classify_audit_states(audit, labels)
    assert strata["rows"][0]["stratum"] == "A"
    conditions = build_condition_manifest(strata, labels, equivalence)
    by_name = {row["condition_name"]: row for row in conditions["conditions"]}
    assert by_name["C1_raw_oracle"]["transition_id"] == "t1"
    assert by_name["C2_signature_only"]["transition_id"] == "t1"
    assert by_name["C5_unrelated"]["transition_id"] == "t3"
    assert conditions["condition_counts"]["C0_bare"] == 1


def test_audit_label_coverage_rejects_exp020_only_subset() -> None:
    audit = [
        {"state_example_id": "covered"},
        {"state_example_id": "one-step-only"},
    ]
    labels = [
        {
            "state_example_id": "covered",
            "scoreable_under_context": True,
        }
    ]
    try:
        validate_audit_label_coverage(
            audit, labels, expected_scoreable_count=1
        )
    except ValueError as exc:
        assert "one-step-only" in str(exc)
    else:
        raise AssertionError("Missing one-step labels were silently accepted")


def test_observation_normalization_and_action_metrics() -> None:
    left = "Output:\n```\n{'b': 2, 'a': 1}\n```"
    right = '{"a": 1, "b": 2}'
    assert normalize_observation(left) == normalize_observation(right)
    target = "```python\nprint(apis.spotify.show_playlist(playlist_id='x'))\n```"
    metrics = evaluate_generated_action(
        target,
        "print(apis.spotify.show_playlist(playlist_id='x'))",
        target,
        "{'id': 1}",
        '{"id": 1}',
    )
    assert metrics["valid_python"]
    assert metrics["exact_primary_app_api_match"]
    assert metrics["canonical_procedural_signature_match"]
    assert metrics["exact_successor_match"]


def test_task_grouped_bootstrap_pairs_conditions() -> None:
    rows = []
    for state, task, left, right in (
        ("s1", "t1", 1, 0),
        ("s2", "t1", 1, 0),
        ("s3", "t2", 0, 0),
    ):
        rows.extend(
            [
                {
                    "state_example_id": state,
                    "state_task_id": task,
                    "condition_name": "left",
                    "metrics": {"score": left},
                },
                {
                    "state_example_id": state,
                    "state_task_id": task,
                    "condition_name": "right",
                    "metrics": {"score": right},
                },
            ]
        )
    result = paired_task_bootstrap(
        rows,
        left_condition="left",
        right_condition="right",
        metric="score",
        samples=100,
        seed=1,
    )
    assert result["paired_state_count"] == 3
    assert result["task_count"] == 2
    assert result["difference"] == 2 / 3


def test_replay_contract_and_history_execution_use_pair_state() -> None:
    response1 = "```python\nx = 1\nprint(x)\n```"
    response2 = "```python\nprint(x + 1)\n```"
    state_text = (
        "[QUERY]\nTask\n[TRACE SO FAR]\n"
        "Step 1 - Response:\n"
        f"{response1}\n"
        "Step 1 - Observation:\n1"
    )
    example = SimpleNamespace(state_text=state_text, target_text=response2)
    record = SimpleNamespace(
        raw_trajectory={
            "steps": [
                {"response": response1, "observation": "1"},
                {"response": response2, "observation": "2"},
            ]
        }
    )
    query = {"state_example_id": "state", "task_id": "task", "step_id": 2}
    contract = _state_contract(query=query, example=example, record=record)

    class FakeWorld:
        def execute(self, code: str) -> str:
            assert "x = 1" in code
            return "1"

    checks = _execute_history(FakeWorld(), contract)
    assert checks == [
        {
            "step_id": 1,
            "action_sha256": hashlib.sha256(
                "x = 1\nprint(x)".encode("utf-8")
            ).hexdigest(),
            "expected_observation_sha256": hashlib.sha256(
                "1".encode("utf-8")
            ).hexdigest(),
            "actual_observation_sha256": hashlib.sha256(
                "1".encode("utf-8")
            ).hexdigest(),
            "observation_match": True,
        }
    ]


def test_prepare_scope_contains_no_qwen_forward_or_appworld(tmp_path: Path) -> None:
    source = Path("scripts/prepare_procedural_causal_audit_6h.py").read_text(
        encoding="utf-8"
    )
    assert "AppWorld(" not in source
    assert ".generate(" not in source
    assert "forward_train" not in source
