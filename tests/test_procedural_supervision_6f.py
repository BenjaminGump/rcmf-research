from __future__ import annotations

import json
from pathlib import Path

from scripts.prepare_procedural_supervision_6f import _credential_leakage_paths

from rcmf.training.procedural_supervision_6f import (
    canonical_procedure_signature,
    observation_signature,
    procedural_compatibility,
    state_stage_signature,
)


def test_ast_parses_multiple_calls_and_order() -> None:
    signature = canonical_procedure_signature(
        """```python
docs = apis.api_docs.show_api_doc(app_name='spotify', api_name='login')
result = apis.spotify.login(username=user_email, password=pwd)
```"""
    )
    assert signature["parse_status"] == "ast"
    assert signature["ordered_api_sequence"] == [
        "api_docs.show_api_doc", "spotify.login"
    ]
    assert signature["primary_app"] == "api_docs"


def test_loop_pagination_and_conditionals() -> None:
    signature = canonical_procedure_signature(
        """page_index = 0
while True:
    rows = apis.spotify.search_tracks(access_token=token, query=query, page_index=page_index)
    if not rows:
        break
    page_index += 1
"""
    )
    assert signature["pagination_loop_pattern"] is True
    assert "while" in signature["control_flow_constructs"]
    assert "if" in signature["control_flow_constructs"]
    assert signature["conditional_pattern"] is True


def test_assignment_and_variable_reuse_are_normalized() -> None:
    first = canonical_procedure_signature(
        "token_result = apis.spotify.login(username=email, password=password)\n"
        "items = apis.spotify.search_tracks(access_token=token_result['access_token'], query='x')"
    )
    second = canonical_procedure_signature(
        "a = apis.spotify.login(username=u, password=p)\n"
        "b = apis.spotify.search_tracks(access_token=a['access_token'], query='x')"
    )
    assert first["assignment_dataflow_pattern"] == second["assignment_dataflow_pattern"]
    assert "prior_api_result" in first["argument_value_source_roles"]


def test_keyword_literals_are_redacted_to_roles() -> None:
    secret = "sk-secret-value"
    signature = canonical_procedure_signature(
        f"apis.spotify.login(username='person@example.com', password='{secret}')",
        context_text="person@example.com",
    )
    serialized = json.dumps(signature, sort_keys=True)
    assert secret not in serialized
    assert "person@example.com" not in serialized
    assert signature["calls"][0]["keyword_roles"]["username"] == "user_or_task_text"


def test_complete_task_and_message_action() -> None:
    complete = canonical_procedure_signature("apis.supervisor.complete_task(answer=result)")
    message = canonical_procedure_signature("apis.phone.send_message(phone_number=number, message=text)")
    assert complete["completion_action"] is True
    assert complete["coarse_action_type"] == "completion"
    assert message["message_send_action"] is True
    assert message["coarse_action_type"] == "message_send"


def test_malformed_code_uses_non_dropping_fallback() -> None:
    signature = canonical_procedure_signature("apis.spotify.search_tracks(query='x'")
    assert signature["parse_status"] == "regex_fallback"
    assert signature["ordered_api_sequence"] == ["spotify.search_tracks"]
    assert signature["syntax_error_category"]


def test_signature_hash_is_stable() -> None:
    source = "apis.api_docs.show_api_doc(app_name='spotify', api_name='login')"
    assert canonical_procedure_signature(source)["signature_sha256"] == canonical_procedure_signature(source)["signature_sha256"]


def test_observation_schema_has_no_values() -> None:
    signature = observation_signature("{'access_token': 'secret', 'playlist_id': '123'}")
    serialized = json.dumps(signature)
    assert "secret" not in serialized and "123" not in serialized
    assert signature["has_access_token_key"] is True
    assert signature["id_keys"] == ["playlist_id"]


def test_state_stage_uses_only_trace_history() -> None:
    state = """[SYSTEM PROMPT]
mention access_token and password in instructions
[QUERY]
Task: inspect playlists
[TRACE SO FAR]
Step 1 - Response:
```python
creds = apis.supervisor.show_account_passwords()
```
Step 1 - Observation:
[{'account_name': 'spotify', 'password': 'redacted'}]
Step 2 - Response:
```python
login = apis.spotify.login(username=email, password=pwd)
```
Step 2 - Observation:
{'access_token': 'redacted'}
"""
    signature = state_stage_signature(state)
    assert signature["history_step_count"] == 2
    assert signature["credentials_obtained"] is True
    assert signature["authenticated"] is True
    assert signature["authentication_token_present"] is True
    assert signature["future_target_action_accessed"] is False


def test_procedural_tier_exact_schema_and_stage() -> None:
    action = canonical_procedure_signature(
        "rows = apis.spotify.search_tracks(access_token=token, query=query)"
    )
    stage = state_stage_signature("[QUERY]\nTask: x\n")
    observation = observation_signature("[{'track_id': 'redacted'}]")
    result = procedural_compatibility(action, stage, action, stage, observation)
    assert result["tier"] == 4
    assert result["P1_exact_api_compatibility"] == 1.0
    assert result["P2_canonical_schema_compatibility"] == 1.0


def test_preflight_uses_canonical_state_identity_argument_order() -> None:
    source = Path("scripts/prepare_procedural_supervision_6f.py").read_text(
        encoding="utf-8"
    )
    assert "state_example_id(index, example)" in source
    assert "state_example_id(example, index)" not in source


def test_credential_leakage_diagnostic_reports_paths_without_values() -> None:
    findings = _credential_leakage_paths(
        {"safe": "phone_number", "nested": {"value": "person@example.com"}}
    )
    assert findings == ["root.nested.value:email"]
    assert "person@example.com" not in json.dumps(findings)
